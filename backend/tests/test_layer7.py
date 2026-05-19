# backend/tests/test_layer7.py
"""
NAKSHATRA-KAVACH Layer 7 replay engine tests.

These tests focus on replay safety guarantees: deep snapshot restore, LRU frame
cache behavior, validation failures, resolution-aware history windows, async
seek semantics, raw-data pagination, pause timeout, and validation persistence.
"""

from __future__ import annotations

import copy
import json
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

import pandas as pd
import pytest
from flask import Flask

import app.routes.replay as replay_routes
import app.services.ingestion_service as ingestion
import app.services.replay_engine as replay_engine
from app.services.replay_engine import (
    FullValidationJob,
    PipelineInjector,
    ReplayController,
    ReplaySpeed,
    ReplayState,
    ValidationEngine,
    _get_history_window,
    generate_synthetic_storm,
    load_storm_catalog,
    should_generate_advisory_for_frame,
    validate_storm_dataframe,
)


class DummySocket:
    """Small Socket.IO test double that records emitted events."""

    def __init__(self) -> None:
        self.events: List[tuple[str, dict]] = []

    def emit(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def _tiny_df(rows: int = 10) -> pd.DataFrame:
    """Build a valid storm dataframe with one-minute cadence."""
    return pd.DataFrame(
        {
            "timestamp_utc": pd.date_range("2024-05-10", periods=rows, freq="min", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bz_gsm": [-2.0] * rows,
            "bt_total": [6.0] * rows,
            "sw_speed_kmps": [430.0] * rows,
            "kp_current": [2.0] * rows,
        }
    )


def test_storm_catalog_loads_with_data_type() -> None:
    catalog = load_storm_catalog()
    storm_ids = {storm["storm_id"] for storm in catalog["storms"]}

    assert storm_ids == {"2024_may_g5", "1989_quebec", "2003_halloween", "2022_starlink"}
    assert all(storm.get("data_type") == "SYNTHETIC" for storm in catalog["storms"])


def test_ingestion_snapshot_backup_restore_uses_deepcopy() -> None:
    original_live = copy.deepcopy(ingestion.LATEST_SNAPSHOT)
    seeded_snapshot = {
        "last_updated_utc": "live",
        "solar_wind": {"bz_gsm": 3.0, "nested": {"source": "live"}},
        "kp": {"kp_current": 2.0},
        "xray": {},
        "cme": {},
        "alert": {},
        "computed": {},
    }
    replay_snapshot = copy.deepcopy(seeded_snapshot)
    replay_snapshot["solar_wind"]["bz_gsm"] = -18.0

    try:
        with ingestion._snapshot_lock:
            ingestion.LATEST_SNAPSHOT = copy.deepcopy(seeded_snapshot)
            ingestion._SNAPSHOT_BACKUP = None

        ingestion._temporarily_set_snapshot(replay_snapshot)
        replay_snapshot["solar_wind"]["nested"]["source"] = "mutated-outside"
        with ingestion._snapshot_lock:
            ingestion.LATEST_SNAPSHOT["solar_wind"]["nested"]["source"] = "mutated-inside"

        ingestion._restore_snapshot()

        with ingestion._snapshot_lock:
            assert ingestion.LATEST_SNAPSHOT == seeded_snapshot
            assert ingestion.LATEST_SNAPSHOT["solar_wind"]["nested"]["source"] == "live"
            assert ingestion._SNAPSHOT_BACKUP is None
    finally:
        with ingestion._snapshot_lock:
            ingestion.LATEST_SNAPSHOT = original_live
            ingestion._SNAPSHOT_BACKUP = None


def test_validate_storm_dataframe_enforces_timestamp_numeric_and_kp_rules() -> None:
    invalid_timestamp = _tiny_df()
    invalid_timestamp.loc[0, "timestamp_utc"] = "not-a-time"
    with pytest.raises(ValueError, match="Invalid timestamp_utc"):
        validate_storm_dataframe(invalid_timestamp)

    invalid_kp = _tiny_df()
    invalid_kp.loc[1, "kp_current"] = 10.0
    with pytest.raises(ValueError, match="outside range"):
        validate_storm_dataframe(invalid_kp)

    too_many_nans = _tiny_df()
    too_many_nans.loc[1:6, "bz_gsm"] = None
    with pytest.raises(ValueError, match="bz_gsm"):
        validate_storm_dataframe(too_many_nans)


def test_get_history_window_uses_actual_resolution() -> None:
    df = _tiny_df(500)
    window = _get_history_window(df, current_frame=400, window_hours=24, resolution_minutes=5)

    assert len(window) == 289
    assert window.index[0] == 112
    assert window.index[-1] == 400


def test_synthetic_storm_generation_four_phase_profile() -> None:
    df = generate_synthetic_storm("G5", duration_hours=2, kp_peak=9.0)

    assert len(df) == 120
    assert set(df["data_type"]) == {"SYNTHETIC"}
    assert df.iloc[:10]["bz_gsm"].min() > 0
    assert df["sw_speed_kmps"].max() >= 800
    assert df["kp_current"].max() >= 8.5
    assert df["bz_gsm"].min() < -15


def test_replay_controller_lru_cache_tracks_access_and_evicts() -> None:
    class TinyInjector:
        def build_snapshot_from_row(self, row: pd.Series) -> dict:
            return {"row": int(row.name)}

        def run_pipeline_on_snapshot(
            self,
            historical_snapshot: dict,
            frame_index: int,
            full_storm_df: pd.DataFrame,
            resolution_minutes: int = 1,
            replay_speed: ReplaySpeed = ReplaySpeed.FAST,
        ) -> dict:
            return {"frame": frame_index, "snapshot": historical_snapshot}

        def restore_live_mode(self) -> None:
            return None

    controller = ReplayController(socketio_instance=DummySocket(), pipeline_injector=TinyInjector())
    controller.storm_df = validate_storm_dataframe(_tiny_df(3))
    controller.storm_metadata = {"storm_id": "tiny", "name": "Tiny", "resolution_minutes": 1, "data_type": "SYNTHETIC"}
    controller.total_frames = 3
    controller.cache_size_limit = 2

    controller._get_or_compute_frame(0)
    controller._get_or_compute_frame(1)
    controller._get_or_compute_frame(0)
    controller._get_or_compute_frame(2)

    assert list(controller.frame_cache.keys()) == [0, 2]
    assert 1 not in controller.frame_cache


def test_should_generate_advisory_replay_throttle() -> None:
    snapshot = {"kp": {"kp_current": 2.0}}
    forecast: Dict[str, Any] = {}
    old_flag = PipelineInjector.REPLAY_MODE_ACTIVE
    try:
      PipelineInjector.REPLAY_MODE_ACTIVE = True
      assert should_generate_advisory_for_frame(20, snapshot, forecast, ReplaySpeed.FAST) is False
      assert should_generate_advisory_for_frame(50, snapshot, forecast, ReplaySpeed.FAST) is True
      assert should_generate_advisory_for_frame(20, snapshot, forecast, ReplaySpeed.REALTIME) is True
    finally:
      PipelineInjector.REPLAY_MODE_ACTIVE = old_flag


def test_validation_engine_accumulates_and_resets() -> None:
    engine = ValidationEngine()
    for i in range(5):
        engine.record_frame(f"t{i}", predicted_kp_3hr=5.0 + i, actual_kp=5.5 + i, predicted_class="G1", actual_class="G1")

    metrics = engine.get_metrics()
    assert metrics["total_frames"] == 5
    assert 0 <= metrics["class_accuracy"] <= 1
    assert metrics["rmse"] > 0

    engine.reset()
    assert engine.predictions == []


def test_pause_timeout_auto_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = ReplayController(socketio_instance=DummySocket(), pipeline_injector=PipelineInjector())
    called: List[bool] = []

    monkeypatch.setattr(replay_engine, "REPLAY_PAUSE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(controller, "stop", lambda: called.append(True) or {"success": True})
    controller.state = ReplayState.PAUSED
    controller.pause_event.set()

    controller._start_pause_timeout()
    time.sleep(0.2)

    assert called == [True]


def test_full_validation_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        def execute(self, sql: str, params: tuple[Any, ...]) -> None:
            captured["sql"] = sql
            captured["params"] = params

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

    @contextmanager
    def fake_get_db() -> Iterator[Connection]:
        yield Connection()

    import app.database.db as db_module

    monkeypatch.setattr(db_module, "get_db", fake_get_db)
    job = FullValidationJob("2024_may_g5")
    job.result = {
        "storm_id": "2024_may_g5",
        "storm_name": "May 2024",
        "total_samples": 2,
        "rmse_3hr": 0.5,
        "mae_3hr": 0.4,
        "class_accuracy": 1.0,
        "data_points": [],
    }

    job._persist_result()

    assert "INSERT INTO validation_results" in captured["sql"]
    assert captured["params"][0] == "2024_may_g5"
    assert json.loads(captured["params"][-1])["total_samples"] == 2


@pytest.fixture()
def replay_client() -> Any:
    app = Flask(__name__)
    app.register_blueprint(replay_routes.replay_bp)
    return app.test_client()


def test_api_catalog_endpoint(replay_client: Any) -> None:
    response = replay_client.get("/api/replay/catalog")

    assert response.status_code == 200
    assert len(response.get_json()["storms"]) == 4
    assert "X-Replay-State" in response.headers


def test_api_raw_data_pagination_validation(replay_client: Any) -> None:
    invalid_field = replay_client.get("/api/replay/data/2024_may_g5/raw?fields=timestamp_utc,bad_field")
    invalid_range = replay_client.get("/api/replay/data/2024_may_g5/raw?start_frame=-1")
    too_many_rows = replay_client.get("/api/replay/data/2024_may_g5/raw?start_frame=0&end_frame=5001")
    ok = replay_client.get("/api/replay/data/2024_may_g5/raw?start_frame=0&end_frame=5&fields=timestamp_utc,kp_current")

    assert invalid_field.status_code == 400
    assert invalid_range.status_code == 400
    assert too_many_rows.status_code == 400
    assert ok.status_code == 200
    payload = ok.get_json()
    assert payload["fields"] == ["timestamp_utc", "kp_current"]
    assert len(payload["records"]) == 5


def test_api_seek_returns_202_async(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeController:
        def get_status(self) -> dict:
            return {"state": "READY", "total_frames": 100}

        def seek(self, frame_index: int) -> dict:
            return {"success": True, "frame": frame_index, "status": "COMPUTING"}

    monkeypatch.setattr(replay_routes, "REPLAY_CONTROLLER", FakeController())
    app = Flask(__name__)
    app.register_blueprint(replay_routes.replay_bp)
    client = app.test_client()

    response = client.post("/api/replay/seek", json={"progress_pct": 50.0})

    assert response.status_code == 202
    assert response.get_json()["status"] == "COMPUTING"
