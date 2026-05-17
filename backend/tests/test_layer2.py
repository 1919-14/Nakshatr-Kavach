# backend/tests/test_layer2.py
"""
NAKSHATRA-KAVACH Layer 2 pytest suite.

Tests the feature ordering, physics calculations, rolling window behavior,
sequence construction, imputation, metadata, and latest-feature thread safety.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest

from app.services.feature_engineering import (
    FEATURE_NAMES,
    SEQUENCE_FEATURE_NAMES,
    EpsilonCalculator,
    RollingWindowCalculator,
    build_feature_metadata,
    compute_lstm_sequence,
    compute_xgb_vector,
    get_latest_features,
    load_scalers,
    update_latest_features,
    xray_peak_flux_log,
)


def _mock_snapshot(
    bz: float | None = -10.0,
    by: float | None = 0.0,
    bt: float | None = 12.0,
    speed: float | None = 500.0,
    density: float | None = 5.0,
    kp: float | None = 3.0,
    cme_earth_directed: bool = False,
) -> Dict[str, Any]:
    """Build a Layer 1 snapshot-shaped dictionary for Layer 2 tests."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "last_updated_utc": now.isoformat().replace("+00:00", "Z"),
        "data_age_seconds": 30,
        "data_quality": "GOOD",
        "solar_wind": {
            "timestamp_utc": now.isoformat().replace("+00:00", "Z"),
            "bx_gsm": 1.0,
            "by_gsm": by,
            "bz_gsm": bz,
            "bt_total": bt,
            "sw_speed_kmps": speed,
            "proton_density_ccm": density,
            "proton_temp_kelvin": 100000.0,
        },
        "kp": {"kp_current": kp, "kp_status": "OFFICIAL"},
        "xray": {"xray_flux_wm2": 1e-7, "xray_severity_numeric": 2},
        "cme": {
            "earth_directed": cme_earth_directed,
            "cme_speed_kmps": 1200.0 if cme_earth_directed else None,
            "arrival_minutes_from_now": 240.0 if cme_earth_directed else None,
        },
        "computed": {"epsilon_coupling": None, "dynamic_pressure_npa": None},
    }


def _mock_history(minutes: int = 180, bz_value: float = -10.0) -> pd.DataFrame:
    """Create one-minute mock solar-wind history ending at the current minute."""
    end = pd.Timestamp.utcnow().floor("min")
    index = pd.date_range(end=end, periods=minutes, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "bz_gsm": np.full(minutes, bz_value, dtype=float),
            "by_gsm": np.zeros(minutes, dtype=float),
            "bx_gsm": np.ones(minutes, dtype=float),
            "bt_total": np.full(minutes, 12.0, dtype=float),
            "sw_speed_kmps": np.full(minutes, 500.0, dtype=float),
            "proton_density_ccm": np.full(minutes, 5.0, dtype=float),
            "proton_temp_kelvin": np.full(minutes, 100000.0, dtype=float),
            "dynamic_pressure_npa": np.full(minutes, 1.0, dtype=float),
            "xray_flux_wm2": np.full(minutes, 1e-7, dtype=float),
            "xray_severity_numeric": np.full(minutes, 2.0, dtype=float),
            "kp_current": np.full(minutes, 3.0, dtype=float),
            "kp_status": ["OFFICIAL"] * minutes,
            "data_quality_flag": ["GOOD"] * minutes,
        },
        index=index,
    )


def test_feature_vector_length() -> None:
    """compute_xgb_vector returns exactly 45 float64 finite values."""
    vector = compute_xgb_vector(_mock_snapshot(), _mock_history(360))
    assert vector.shape == (45,)
    assert vector.dtype == np.float64
    assert np.isfinite(vector).all()


def test_feature_names_match_vector_length() -> None:
    """Feature name constants match the Layer 3 contract."""
    assert len(FEATURE_NAMES) == 45
    assert len(SEQUENCE_FEATURE_NAMES) == 15


def test_epsilon_calculation_physics() -> None:
    """Pure southward IMF produces physically large epsilon coupling."""
    epsilon_scaled = EpsilonCalculator.compute_scaled(700.0, 20.0, 0.0, -20.0)
    expected = 700.0 * 400.0 * (7 * 6371.0) ** 2 / 1e13
    assert math.isclose(epsilon_scaled, expected, rel_tol=1e-9)
    assert 50.0 < epsilon_scaled < 200.0


def test_epsilon_zero_when_northward() -> None:
    """Pure northward IMF produces zero coupling."""
    epsilon_scaled = EpsilonCalculator.compute_scaled(700.0, 20.0, 0.0, 20.0)
    assert epsilon_scaled == 0.0


def test_rolling_window_30min() -> None:
    """Rolling mean over 30 one-minute rows equals the constant Bz value."""
    df = _mock_history(minutes=30, bz_value=-10.0)
    rolling = RollingWindowCalculator(df)
    assert rolling.mean("bz_gsm", 30) == -10.0


def test_rolling_window_insufficient_data() -> None:
    """A 60-minute window with only five readings returns None."""
    df = _mock_history(minutes=5, bz_value=-10.0)
    rolling = RollingWindowCalculator(df)
    assert rolling.mean("bz_gsm", 60) is None


def test_consecutive_southward_count() -> None:
    """Consecutive southward Bz count walks backward from the final reading."""
    index = pd.date_range(end=pd.Timestamp.utcnow().floor("min"), periods=11, freq="1min", tz="UTC")
    df = _mock_history(minutes=11, bz_value=-15.0)
    df.index = index
    df.iloc[0, df.columns.get_loc("bz_gsm")] = 5.0
    assert RollingWindowCalculator(df).consecutive_below("bz_gsm", -5.0) == 10.0

    all_south = _mock_history(minutes=30, bz_value=-10.0)
    assert RollingWindowCalculator(all_south).consecutive_below("bz_gsm", -5.0) == 30.0


def test_lstm_sequence_shape() -> None:
    """A full 24-hour one-minute history returns a (1, 24, 15) float32 tensor."""
    sequence = compute_lstm_sequence(_mock_history(minutes=24 * 60))
    assert sequence.shape == (1, 24, 15)
    assert sequence.dtype == np.float32


def test_lstm_sequence_padding() -> None:
    """Ten hourly bins are left-padded to the required 24-hour tensor length."""
    start = pd.Timestamp("2024-05-01T00:00:00Z")
    index = pd.date_range(start=start, periods=10 * 60, freq="1min", tz="UTC")
    df = _mock_history(minutes=10 * 60)
    df.index = index
    sequence = compute_lstm_sequence(df)[0]
    assert sequence.shape == (24, 15)
    np.testing.assert_allclose(sequence[:14], np.repeat(sequence[[14]], 14, axis=0))


def test_scaler_not_fitted_raises_error(tmp_path) -> None:
    """Missing scaler files raise a clear RuntimeError."""
    with pytest.raises(RuntimeError, match="scaler file"):
        load_scalers(tmp_path / "missing_xgb.pkl", tmp_path / "missing_lstm.pkl")


def test_imputation_when_bz_null() -> None:
    """Null current Bz forces all Bz statistics to conservative zero values."""
    vector = compute_xgb_vector(_mock_snapshot(bz=None), _mock_history(360, bz_value=-20.0))
    assert np.all(vector[:9] == 0.0)
    assert np.isfinite(vector).all()


def test_cme_features_no_cme() -> None:
    """No active Earth-directed CME yields the no-CME feature defaults."""
    vector = compute_xgb_vector(_mock_snapshot(cme_earth_directed=False), _mock_history(360))
    assert vector[32] == 0.0
    assert vector[33] == 0.0
    assert vector[34] == 48.0
    assert vector[35] == 0.0


def test_xray_log_transform() -> None:
    """X-ray flux uses log10(max_flux) + 9 and missing values become zero."""
    assert xray_peak_flux_log(1e-4) == 5.0
    assert xray_peak_flux_log(1e-7) == 2.0
    assert xray_peak_flux_log(None) == 0.0


def test_bz_southward_onset_flag() -> None:
    """Southward onset fires only when current Bz crosses below -5 nT."""
    base = _mock_history(minutes=30, bz_value=2.0)
    vector = compute_xgb_vector(_mock_snapshot(bz=-8.0), base)
    assert vector[27] == 1.0

    southward_base = _mock_history(minutes=30, bz_value=-6.0)
    vector = compute_xgb_vector(_mock_snapshot(bz=-8.0), southward_base)
    assert vector[27] == 0.0

    quiet_base = _mock_history(minutes=30, bz_value=5.0)
    vector = compute_xgb_vector(_mock_snapshot(bz=3.0), quiet_base)
    assert vector[27] == 0.0


def test_feature_metadata_structure() -> None:
    """Feature metadata contains all required top-level and per-feature keys."""
    raw = compute_xgb_vector(_mock_snapshot(), _mock_history(360))
    scaled = np.zeros(45, dtype=np.float64)
    metadata = build_feature_metadata(raw, scaled, "GOOD", False, "2024-05-10T14:32:00Z")
    assert {"computed_at_utc", "features", "summary", "data_quality"}.issubset(metadata.keys())
    assert len(metadata["features"]) == 45
    for feature in metadata["features"]:
        assert {"index", "name", "value", "unit", "risk_interpretation"}.issubset(feature.keys())


def test_thread_safety_latest_features() -> None:
    """Concurrent latest-feature reads and writes do not corrupt the singleton."""
    errors: list[str] = []
    deadline = time.time() + 1.0

    def reader() -> None:
        while time.time() < deadline:
            try:
                result = get_latest_features()
                assert isinstance(result, dict)
                assert "xgb_vector_raw" in result
            except Exception as exc:
                errors.append(str(exc))

    def writer() -> None:
        payload = {
            "xgb_vector_scaled": np.zeros(45, dtype=np.float64),
            "lstm_sequence_scaled": np.zeros((1, 24, 15), dtype=np.float32),
            "xgb_vector_raw": np.zeros(45, dtype=np.float64),
            "lstm_sequence_raw": np.zeros((1, 24, 15), dtype=np.float32),
            "feature_metadata": build_feature_metadata(np.zeros(45), np.zeros(45)),
            "data_quality": "GOOD",
            "stale_data": False,
            "computed_at_utc": "2024-05-10T14:32:00Z",
        }
        while time.time() < deadline:
            try:
                update_latest_features(payload)
            except Exception as exc:
                errors.append(str(exc))

    threads = [threading.Thread(target=reader if i % 2 == 0 else writer) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert not errors
