# backend/app/services/replay_engine.py
"""
NAKSHATRA-KAVACH Layer 7: Historical Storm Replay Engine.

This module replays cached storm datasets through the same Layer 2-6 pipeline
used by live telemetry. It is thread-safe by design: only one replay can run at
a time, live scheduler cycles are paused while replay mode is active, and the
Layer 1 snapshot is always restored in finally blocks.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from app.utils.constants import (
    REPLAY_ACTIVE_ADVISORY_FRAME_INTERVAL,
    REPLAY_ADVISORY_FRAME_INTERVAL,
    REPLAY_FRAME_CACHE_SIZE,
    REPLAY_PAUSE_TIMEOUT_SECONDS,
    RESAMPLE_LIMIT_HOURS,
    STORM_CATALOG_PATH,
    STORM_DATA_DIR,
    SYNTHETIC_STORM_MAIN_BZ,
    SYNTHETIC_STORM_QUIET_KP,
    VALIDATION_SAMPLE_STEP,
    WS_EVENT_REPLAY_COMPLETED,
    WS_EVENT_REPLAY_FRAME,
    WS_EVENT_REPLAY_FRAME_READY,
    WS_EVENT_REPLAY_KEY_MOMENT,
    WS_EVENT_REPLAY_STATE_CHANGE,
    WS_EVENT_REPLAY_VALIDATION_UPDATE,
)

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
STORM_DATA_PATH = BACKEND_ROOT / STORM_DATA_DIR
STORM_CATALOG_FILE = BACKEND_ROOT / STORM_CATALOG_PATH

STORM_COLUMNS: List[str] = [
    "timestamp_utc",
    "bz_gsm",
    "by_gsm",
    "bx_gsm",
    "bt_total",
    "sw_speed_kmps",
    "proton_density_ccm",
    "proton_temp_kelvin",
    "kp_current",
    "kp_estimated_from_sw",
    "xray_flux_wm2",
    "xray_class",
    "xray_severity_numeric",
    "cme_earth_directed",
    "cme_speed_kmps",
    "cme_arrival_minutes",
    "dynamic_pressure_npa",
    "data_quality_flag",
    "bz_southward_flag",
    "storm_onset_risk",
    "data_type",
]

RAW_ALLOWED_FIELDS = {
    "timestamp_utc",
    "bz_gsm",
    "by_gsm",
    "bt_total",
    "sw_speed_kmps",
    "proton_density_ccm",
    "kp_current",
    "xray_class",
    "xray_flux_wm2",
}

VALIDATION_JOBS: Dict[str, "FullValidationJob"] = {}
VALIDATION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ReplayValidation")
SEEK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ReplaySeek")


class ReplayState(Enum):
    """Replay lifecycle states exposed to the dashboard."""

    IDLE = "IDLE"
    LOADING = "LOADING"
    READY = "READY"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class ReplaySpeed(Enum):
    """Supported replay speed multipliers."""

    REALTIME = 1
    FAST = 60
    ULTRAFAST = 3600

    @classmethod
    def from_value(cls, value: Any) -> "ReplaySpeed":
        """Return a ReplaySpeed from an int-like API value."""
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            numeric = cls.FAST.value
        for speed in cls:
            if speed.value == numeric:
                return speed
        raise ValueError("speed must be one of 1, 60, 3600")


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a scalar to float while treating NaN/None as default."""
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert a scalar to int while treating NaN/None as default."""
    try:
        if value is None or pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _utc_iso(dt: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_safe(value: Any) -> Any:
    """Recursively convert numpy/pandas objects to JSON-safe Python types."""
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        return None if math.isnan(f) else f
    if isinstance(value, pd.Timestamp):
        return _utc_iso(value.to_pydatetime())
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _max_consecutive_true(values: Iterable[bool]) -> int:
    """Return the longest consecutive True run in a boolean iterable."""
    best = 0
    current = 0
    for value in values:
        if bool(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def kp_to_storm_class(kp: float) -> str:
    """Map Kp to NOAA geomagnetic storm class."""
    if kp >= 9.0:
        return "G5"
    if kp >= 8.0:
        return "G4"
    if kp >= 7.0:
        return "G3"
    if kp >= 6.0:
        return "G2"
    if kp >= 5.0:
        return "G1"
    return "QUIET"


def kp_to_color(kp: float) -> str:
    """Map Kp to dashboard color."""
    if kp >= 9.0:
        return "#9C27B0"
    if kp >= 8.0:
        return "#F44336"
    if kp >= 7.0:
        return "#FF9800"
    if kp >= 6.0:
        return "#CDDC39"
    if kp >= 5.0:
        return "#4CAF50"
    return "#607D8B"


def classify_storm_onset_risk(bz_gsm: Any) -> str:
    """Classify storm onset risk from Bz."""
    bz = _safe_float(bz_gsm, 0.0)
    if bz >= -5.0:
        return "LOW"
    if bz >= -10.0:
        return "MODERATE"
    if bz >= -20.0:
        return "HIGH"
    return "CRITICAL"


def load_storm_catalog() -> Dict[str, Any]:
    """Load the replay storm catalog from disk."""
    if not STORM_CATALOG_FILE.exists():
        raise FileNotFoundError(f"Storm catalog not found: {STORM_CATALOG_FILE}")
    with STORM_CATALOG_FILE.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    storms = catalog.get("storms")
    if not isinstance(storms, list) or not storms:
        raise ValueError("storm_catalog.json must contain a non-empty storms array")
    return catalog


def get_storm_metadata(storm_id: str) -> Dict[str, Any]:
    """Return one catalog metadata object by storm id."""
    catalog = load_storm_catalog()
    for storm in catalog["storms"]:
        if storm.get("storm_id") == storm_id:
            return copy.deepcopy(storm)
    raise ValueError(f"Storm ID not found: {storm_id}")


def validate_storm_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and clean a storm dataframe.

    Rules:
    - timestamp_utc must exist and parse for every row.
    - bz_gsm, bt_total, sw_speed_kmps must be numeric and can have at most
      five consecutive NaNs before forward/backward fill.
    - kp_current must be numeric and within [0, 9].
    - rows are returned sorted ascending by timestamp.
    """
    if df is None or df.empty:
        raise ValueError("Storm dataframe is empty")

    cleaned = df.copy()
    required = ["timestamp_utc", "bz_gsm", "bt_total", "sw_speed_kmps", "kp_current"]
    for column in required:
        if column not in cleaned.columns:
            raise ValueError(f"Missing required column: {column}")

    timestamps = pd.to_datetime(cleaned["timestamp_utc"], utc=True, errors="coerce")
    if timestamps.isna().any():
        bad_count = int(timestamps.isna().sum())
        raise ValueError(f"Invalid timestamp_utc values: {bad_count} rows")
    cleaned["_timestamp"] = timestamps
    cleaned = cleaned.sort_values("_timestamp").reset_index(drop=True)

    for column in ["bz_gsm", "bt_total", "sw_speed_kmps"]:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
        max_run = _max_consecutive_true(cleaned[column].isna().tolist())
        if max_run > 5:
            raise ValueError(f"{column} has more than 5 consecutive NaN values")
        cleaned[column] = cleaned[column].ffill().bfill()
        if cleaned[column].isna().any():
            raise ValueError(f"{column} could not be filled")

    cleaned["kp_current"] = pd.to_numeric(cleaned["kp_current"], errors="coerce")
    if cleaned["kp_current"].isna().any():
        raise ValueError("kp_current contains non-numeric or missing values")
    invalid_kp = ~cleaned["kp_current"].between(0.0, 9.0)
    if invalid_kp.any():
        bad = cleaned.loc[invalid_kp, "kp_current"].iloc[0]
        raise ValueError(f"kp_current outside range [0, 9]: {bad}")

    optional_defaults: Dict[str, Any] = {
        "by_gsm": 0.0,
        "bx_gsm": 0.0,
        "proton_density_ccm": 5.0,
        "proton_temp_kelvin": 100000.0,
        "kp_estimated_from_sw": cleaned["kp_current"],
        "xray_flux_wm2": 1e-8,
        "xray_class": "A1.0",
        "xray_severity_numeric": 1,
        "cme_earth_directed": 0,
        "cme_speed_kmps": np.nan,
        "cme_arrival_minutes": np.nan,
        "dynamic_pressure_npa": np.nan,
        "data_quality_flag": "GOOD",
        "bz_southward_flag": np.nan,
        "storm_onset_risk": np.nan,
        "data_type": "SYNTHETIC",
    }
    for column, default in optional_defaults.items():
        if column not in cleaned.columns:
            cleaned[column] = default

    numeric_optional = [
        "by_gsm",
        "bx_gsm",
        "proton_density_ccm",
        "proton_temp_kelvin",
        "kp_estimated_from_sw",
        "xray_flux_wm2",
        "xray_severity_numeric",
        "cme_earth_directed",
        "cme_speed_kmps",
        "cme_arrival_minutes",
        "dynamic_pressure_npa",
    ]
    for column in numeric_optional:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    pressure_missing = cleaned["dynamic_pressure_npa"].isna()
    if pressure_missing.any():
        density = cleaned.loc[pressure_missing, "proton_density_ccm"].fillna(5.0)
        speed = cleaned.loc[pressure_missing, "sw_speed_kmps"].fillna(450.0)
        cleaned.loc[pressure_missing, "dynamic_pressure_npa"] = (
            1.6726e-27 * 1e6 * density * (speed * 1000.0) ** 2 * 1e9
        )

    cleaned["bz_southward_flag"] = (cleaned["bz_gsm"] < -5.0).astype(int)
    cleaned["storm_onset_risk"] = cleaned["bz_gsm"].apply(classify_storm_onset_risk)
    cleaned["data_quality_flag"] = cleaned["data_quality_flag"].fillna("GOOD").astype(str)
    cleaned["xray_class"] = cleaned["xray_class"].fillna("A1.0").astype(str)
    cleaned["data_type"] = cleaned["data_type"].fillna("SYNTHETIC").astype(str)
    cleaned["timestamp_utc"] = cleaned["_timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cleaned = cleaned.drop(columns=["_timestamp"])

    ordered = [column for column in STORM_COLUMNS if column in cleaned.columns]
    extras = [column for column in cleaned.columns if column not in ordered]
    return cleaned[ordered + extras].reset_index(drop=True)


def resample_to_5min(df: pd.DataFrame) -> pd.DataFrame:
    """
    Upsample hourly storm data to five-minute resolution.

    Continuous fields are linearly interpolated. Kp and event fields are
    forward-filled, and derived binary/risk fields are recomputed from Bz.
    """
    cleaned = validate_storm_dataframe(df)
    indexed = cleaned.copy()
    indexed["_timestamp"] = pd.to_datetime(indexed["timestamp_utc"], utc=True)
    indexed = indexed.set_index("_timestamp").sort_index()

    numeric_continuous = [
        "bz_gsm",
        "by_gsm",
        "bx_gsm",
        "bt_total",
        "sw_speed_kmps",
        "proton_density_ccm",
        "proton_temp_kelvin",
        "kp_estimated_from_sw",
        "xray_flux_wm2",
        "dynamic_pressure_npa",
    ]
    forward_fill = [
        "kp_current",
        "xray_severity_numeric",
        "cme_earth_directed",
        "cme_speed_kmps",
        "cme_arrival_minutes",
    ]
    text_columns = ["xray_class", "data_quality_flag", "data_type"]

    out = pd.DataFrame(index=pd.date_range(indexed.index.min(), indexed.index.max(), freq="5min", tz="UTC"))
    for column in numeric_continuous:
        series = pd.to_numeric(indexed[column], errors="coerce")
        out[column] = series.resample("5min").interpolate(
            method="time",
            limit=RESAMPLE_LIMIT_HOURS * 12,
            limit_direction="forward",
        )
    for column in forward_fill:
        out[column] = indexed[column].resample("5min").ffill().bfill()
    for column in text_columns:
        out[column] = indexed[column].resample("5min").ffill().bfill()

    out["timestamp_utc"] = out.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["bz_southward_flag"] = (out["bz_gsm"] < -5.0).astype(int)
    out["storm_onset_risk"] = out["bz_gsm"].apply(classify_storm_onset_risk)
    return validate_storm_dataframe(out.reset_index(drop=True))


def _storm_phase_profile(duration_hours: int, resolution_minutes: int) -> Tuple[np.ndarray, np.ndarray]:
    """Return frame indices and normalized storm time."""
    total_frames = max(1, int(duration_hours * 60 / resolution_minutes))
    idx = np.arange(total_frames, dtype=float)
    t = idx / max(1.0, total_frames - 1.0)
    return idx, t


def generate_synthetic_storm(
    storm_class: str,
    duration_hours: int,
    kp_peak: float,
    start_timestamp_utc: str = "2024-05-10T00:00:00Z",
    resolution_minutes: int = 1,
) -> pd.DataFrame:
    """
    Generate a deterministic, physically plausible four-phase storm profile.

    The output is labeled with data_type=SYNTHETIC so it is never mistaken for
    verified historical data.
    """
    _, t = _storm_phase_profile(duration_hours, resolution_minutes)
    total_frames = len(t)
    kp = np.zeros(total_frames, dtype=float)
    bz = np.zeros(total_frames, dtype=float)
    speed = np.zeros(total_frames, dtype=float)
    density = np.zeros(total_frames, dtype=float)
    bt = np.zeros(total_frames, dtype=float)

    p1 = t < 0.10
    p2 = (t >= 0.10) & (t < 0.15)
    p3 = (t >= 0.15) & (t < 0.60)
    p4 = t >= 0.60

    # Phase 1: quiet.
    q = np.linspace(0.0, 1.0, max(1, int(p1.sum())))
    kp[p1] = SYNTHETIC_STORM_QUIET_KP + 0.6 * np.sin(q * math.pi)
    bz[p1] = 4.0 + 1.0 * np.sin(q * 2.0 * math.pi)
    speed[p1] = 400.0 + 20.0 * np.sin(q * math.pi)
    density[p1] = 4.0 + 0.8 * np.cos(q * 2.0 * math.pi)

    # Phase 2: sudden commencement.
    q = np.linspace(0.0, 1.0, max(1, int(p2.sum())))
    kp[p2] = np.linspace(3.0, 5.0, len(q))
    bz[p2] = np.linspace(5.0, -15.0, len(q)) + 1.2 * np.sin(q * math.pi)
    speed[p2] = np.linspace(420.0, 820.0, len(q))
    density[p2] = np.linspace(5.0, 15.0, len(q))

    # Phase 3: main phase.
    q = np.linspace(0.0, 1.0, max(1, int(p3.sum())))
    kp[p3] = np.linspace(5.0, kp_peak, len(q))
    bz[p3] = -20.0 + 10.0 * np.sin(q * 8.0 * math.pi) - 4.0 * np.sin(q * math.pi)
    bz[p3] = np.clip(bz[p3], -30.0, -10.0)
    if storm_class == "G1":
        bz[p3] = np.clip(bz[p3] * 0.45, -14.0, -5.0)
    speed[p3] = 800.0 + 80.0 * np.sin(q * 4.0 * math.pi)
    density[p3] = 10.0 + 5.0 * np.sin(q * 6.0 * math.pi)

    # Phase 4: recovery.
    q = np.linspace(0.0, 1.0, max(1, int(p4.sum())))
    decay = np.exp(-4.0 * q)
    kp[p4] = 2.0 + (kp_peak - 2.0) * decay
    bz[p4] = np.linspace(-15.0, 2.0, len(q)) + 1.5 * np.sin(q * 4.0 * math.pi) * (1.0 - q)
    speed[p4] = 420.0 + (760.0 - 420.0) * decay
    density[p4] = 5.0 + 6.0 * decay

    kp = np.clip(kp, 0.0, 9.0)
    by = 0.35 * np.abs(bz) * np.sin(t * 10.0 * math.pi)
    bx = 0.25 * np.abs(bz) * np.cos(t * 6.0 * math.pi)
    bt = np.maximum(np.sqrt(bx**2 + by**2 + bz**2), np.abs(bz) + 1.5)
    temp = 90000.0 + (speed - 400.0) * 220.0
    pressure = 1.6726e-27 * 1e6 * density * (speed * 1000.0) ** 2 * 1e9

    start = pd.Timestamp(start_timestamp_utc)
    timestamps = [
        _utc_iso((start + pd.Timedelta(minutes=i * resolution_minutes)).to_pydatetime())
        for i in range(total_frames)
    ]
    xray_flux = np.full(total_frames, 1e-8)
    xray_flux[p2 | p3] = 2e-5 if storm_class != "G1" else 4e-6
    xray_class = ["M2.0" if flux >= 1e-5 else "C4.0" if flux >= 1e-6 else "A1.0" for flux in xray_flux]
    xray_sev = [4 if cls.startswith("M") else 3 if cls.startswith("C") else 1 for cls in xray_class]

    cme_directed = (p2 | p3).astype(int)
    cme_speed = np.where(cme_directed == 1, np.maximum(speed + 300.0, 900.0), np.nan)
    cme_arrival = np.where(cme_directed == 1, np.maximum(0.0, 180.0 - t * duration_hours * 60.0), np.nan)

    df = pd.DataFrame(
        {
            "timestamp_utc": timestamps,
            "bz_gsm": np.round(bz, 3),
            "by_gsm": np.round(by, 3),
            "bx_gsm": np.round(bx, 3),
            "bt_total": np.round(bt, 3),
            "sw_speed_kmps": np.round(speed, 3),
            "proton_density_ccm": np.round(np.clip(density, 1.0, 30.0), 3),
            "proton_temp_kelvin": np.round(temp, 1),
            "kp_current": np.round(kp, 3),
            "kp_estimated_from_sw": np.round(kp + 0.15 * np.sin(t * 5.0 * math.pi), 3),
            "xray_flux_wm2": xray_flux,
            "xray_class": xray_class,
            "xray_severity_numeric": xray_sev,
            "cme_earth_directed": cme_directed,
            "cme_speed_kmps": np.round(cme_speed, 3),
            "cme_arrival_minutes": np.round(cme_arrival, 3),
            "dynamic_pressure_npa": np.round(pressure, 3),
            "data_quality_flag": "GOOD",
            "bz_southward_flag": (bz < -5.0).astype(int),
            "storm_onset_risk": [classify_storm_onset_risk(v) for v in bz],
            "data_type": "SYNTHETIC",
        }
    )
    return validate_storm_dataframe(df)


def _load_storm_dataframe_from_metadata(metadata: Mapping[str, Any]) -> pd.DataFrame:
    """Load, validate, and optionally resample one storm dataframe."""
    csv_path = STORM_DATA_PATH / str(metadata["data_file"])
    if csv_path.exists():
        df = pd.read_csv(csv_path)
    else:
        logger.warning("Storm CSV missing: %s; generating synthetic storm", csv_path)
        df = generate_synthetic_storm(
            str(metadata.get("storm_class", "G5")),
            int(metadata.get("duration_hours", 72)),
            float(metadata.get("kp_peak", 9.0)),
            str(metadata.get("start_timestamp_utc", "2024-05-10T00:00:00Z")),
            int(metadata.get("resolution_minutes", 1)),
        )
    df = validate_storm_dataframe(df)
    if int(metadata.get("resolution_minutes", 1)) == 60:
        df = resample_to_5min(df)
    return df


def load_storm_dataframe(storm_id: str) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """Load metadata and dataframe for an API endpoint or controller."""
    metadata = get_storm_metadata(storm_id)
    df = _load_storm_dataframe_from_metadata(metadata)
    effective = 5 if int(metadata.get("resolution_minutes", 1)) == 60 else int(metadata.get("resolution_minutes", 1))
    metadata["_effective_resolution_minutes"] = effective
    return metadata, df


def _get_history_window(
    full_df: pd.DataFrame,
    current_frame: int,
    window_hours: int = 24,
    resolution_minutes: int = 1,
) -> pd.DataFrame:
    """Return a rolling history window based on actual frame resolution."""
    frames_per_hour = 60.0 / max(float(resolution_minutes), 1.0)
    window_frames = int(window_hours * frames_per_hour)
    start_frame = max(0, int(current_frame) - window_frames)
    return full_df.iloc[start_frame : int(current_frame) + 1].copy()


def should_generate_advisory_for_frame(
    frame_index: int,
    snapshot: dict,
    kp_forecast: dict,
    replay_speed: ReplaySpeed = ReplaySpeed.FAST,
) -> bool:
    """
    Decide whether this replay frame may generate a Layer 6 advisory.

    Fast replay modes are throttled to one advisory every 50 frames. Realtime
    replay keeps the older transition/every-20-frame cadence for operator demos.
    """
    if (
        PipelineInjector.REPLAY_MODE_ACTIVE
        and replay_speed != ReplaySpeed.REALTIME
    ):
        return frame_index % REPLAY_ACTIVE_ADVISORY_FRAME_INTERVAL == 0
    kp = _safe_float(snapshot.get("kp", {}).get("kp_current"), 0.0)
    if round(kp, 1) in {4.9, 5.0, 5.1, 5.9, 6.0, 6.1, 6.9, 7.0, 7.1, 7.9, 8.0, 8.1, 9.0}:
        return True
    return frame_index % REPLAY_ADVISORY_FRAME_INTERVAL == 0


class PipelineInjector:
    """
    Bridge from historical storm rows to the live Layer 2-6 pipeline.

    The class-level replay flag is checked by the scheduler before live work.
    """

    REPLAY_MODE_ACTIVE = False
    _replay_lock = threading.Lock()

    @classmethod
    def activate_replay_mode(cls) -> None:
        """Set replay mode so live scheduler jobs skip their cycles."""
        with cls._replay_lock:
            cls.REPLAY_MODE_ACTIVE = True
        logger.info("Replay mode ACTIVATED - live scheduler paused")

    @classmethod
    def restore_live_mode(cls) -> None:
        """Clear replay mode so live scheduler jobs may resume."""
        with cls._replay_lock:
            cls.REPLAY_MODE_ACTIVE = False
        logger.info("Replay mode DEACTIVATED - live scheduler resumed")

    def build_snapshot_from_row(self, row: pd.Series | Mapping[str, Any]) -> dict:
        """Build a Layer 1 LATEST_SNAPSHOT-compatible dict from a storm row."""
        bz = _safe_float(row.get("bz_gsm"), 0.0)
        by = _safe_float(row.get("by_gsm"), 0.0)
        bx = _safe_float(row.get("bx_gsm"), 0.0)
        bt = _safe_float(row.get("bt_total"), max(abs(bz), 5.0))
        speed = _safe_float(row.get("sw_speed_kmps"), 450.0)
        density = _safe_float(row.get("proton_density_ccm"), 5.0)
        temp = _safe_float(row.get("proton_temp_kelvin"), 100000.0)
        kp = _safe_float(row.get("kp_current"), 0.0)
        xflux = _safe_float(row.get("xray_flux_wm2"), 1e-8)
        xsev = _safe_int(row.get("xray_severity_numeric"), 1)
        cme_directed = _safe_int(row.get("cme_earth_directed"), 0)
        cme_speed = None if pd.isna(row.get("cme_speed_kmps")) else _safe_float(row.get("cme_speed_kmps"), 0.0)
        cme_arrival = None if pd.isna(row.get("cme_arrival_minutes")) else _safe_float(row.get("cme_arrival_minutes"), 0.0)

        theta = math.atan2(by, bz if bz != 0 else 1e-9)
        epsilon = speed * (bt**2) * (math.sin(theta / 2.0) ** 4) * ((7.0 * 6371.0) ** 2)
        dynamic_pressure = 1.6726e-27 * 1e6 * density * (speed * 1000.0) ** 2 * 1e9
        transit = 1_500_000.0 / speed / 60.0 if speed > 0.0 else 60.0
        storm_class = kp_to_storm_class(kp)
        timestamp = str(row.get("timestamp_utc"))
        data_type = str(row.get("data_type", "SYNTHETIC"))

        if xflux >= 1e-4:
            xray_class = f"X{xflux / 1e-4:.1f}"
        elif xflux >= 1e-5:
            xray_class = f"M{xflux / 1e-5:.1f}"
        elif xflux >= 1e-6:
            xray_class = f"C{xflux / 1e-6:.1f}"
        elif xflux >= 1e-7:
            xray_class = f"B{xflux / 1e-7:.1f}"
        else:
            xray_class = f"A{xflux / 1e-8:.1f}"

        return {
            "last_updated_utc": timestamp,
            "data_age_seconds": 0,
            "data_quality": str(row.get("data_quality_flag", "GOOD")),
            "is_historical": True,
            "replay_data_type": data_type,
            "solar_wind": {
                "timestamp_utc": timestamp,
                "bx_gsm": bx,
                "by_gsm": by,
                "bz_gsm": bz,
                "bt_total": bt,
                "sw_speed_kmps": speed,
                "sw_speed": speed,
                "proton_density_ccm": density,
                "proton_density": density,
                "proton_temp_kelvin": temp,
                "bz_southward_flag": 1 if bz < -5.0 else 0,
                "storm_onset_risk": classify_storm_onset_risk(bz),
                "source_dscovr_active": 1,
            },
            "kp": {
                "kp_current": kp,
                "kp_index": int(min(9, max(0, round(kp)))),
                "kp_status": "HISTORICAL",
                "storm_class": storm_class,
                "kp_timestamp_utc": timestamp,
            },
            "xray": {
                "xray_flux_wm2": xflux,
                "xray_flux": xflux,
                "xray_class": xray_class,
                "xray_severity_numeric": xsev,
                "xray_timestamp_utc": timestamp,
            },
            "cme": {
                "earth_directed": bool(cme_directed),
                "cme_speed_kmps": cme_speed,
                "arrival_minutes_from_now": cme_arrival,
                "active_cme_count": 1 if cme_directed else 0,
            },
            "alert": {
                "latest_official_class": storm_class if kp >= 5.0 else None,
                "latest_alert_code": f"ALTK0{int(round(kp))}" if kp >= 5.0 else None,
                "alert_issued_utc": timestamp,
                "active_watch": kp >= 5.0,
            },
            "computed": {
                "transit_warning_minutes": transit,
                "epsilon_coupling": epsilon,
                "dynamic_pressure_npa": dynamic_pressure,
                "storm_imminent": kp >= 5.0 or bool(cme_directed and cme_arrival is not None and cme_arrival < 120.0),
                "recommended_action_level": (
                    "ACT_NOW" if kp >= 7.0 else "PREPARE" if kp >= 5.0 else "WATCH" if kp >= 3.0 else "MONITOR"
                ),
            },
        }

    def run_pipeline_on_snapshot(
        self,
        historical_snapshot: dict,
        frame_index: int,
        full_storm_df: pd.DataFrame,
        resolution_minutes: int = 1,
        replay_speed: ReplaySpeed = ReplaySpeed.FAST,
    ) -> dict:
        """Run Layers 2-6 on one historical snapshot and return JSON-safe output."""
        output: Dict[str, Any] = {"solar": historical_snapshot}

        from app.services.ingestion_service import _restore_snapshot, _temporarily_set_snapshot

        _temporarily_set_snapshot(historical_snapshot)
        try:
            try:
                history_window = _get_history_window(full_storm_df, frame_index, 24, resolution_minutes)
                from app.services.feature_engineering import compute_features_realtime

                features = compute_features_realtime(snapshot=historical_snapshot, history_df=history_window)
                output["features"] = {
                    "kp_current": historical_snapshot["kp"]["kp_current"],
                    "bz_current": historical_snapshot["solar_wind"]["bz_gsm"],
                }
                if not features:
                    raise RuntimeError("Layer 2 returned no features")

                from app.services.kp_predictor import run_inference_cycle

                kp_forecast = run_inference_cycle(features)
                output["kp_forecast"] = kp_forecast
            except Exception as exc:
                logger.warning("Replay frame %d Layer 2/3 fallback: %s", frame_index, exc)
                kp_forecast = _fallback_forecast(historical_snapshot)
                output["kp_forecast"] = kp_forecast

            try:
                from app.services.satellite_scorer import run_satellite_scoring

                output["satellite_risks"] = run_satellite_scoring(kp_forecast, historical_snapshot)
            except Exception as exc:
                logger.warning("Replay frame %d Layer 4 fallback: %s", frame_index, exc)
                output["satellite_risks"] = _fallback_satellite_risks()

            try:
                from app.services.grid_risk_engine import run_grid_risk_scoring

                output["grid_risks"] = run_grid_risk_scoring(kp_forecast, historical_snapshot)
            except Exception as exc:
                logger.warning("Replay frame %d Layer 5 fallback: %s", frame_index, exc)
                output["grid_risks"] = _fallback_grid_risks()

            actual_kp_now = _safe_float(historical_snapshot.get("kp", {}).get("kp_current"), 0.0)
            predicted_kp_3hr = _safe_float(kp_forecast.get("forecast", {}).get("3hr", {}).get("kp"), actual_kp_now)
            actual_class = kp_to_storm_class(actual_kp_now)
            predicted_class = str(kp_forecast.get("summary", {}).get("peak_storm_class", kp_to_storm_class(predicted_kp_3hr)))
            validation = {
                "timestamp_utc": historical_snapshot.get("last_updated_utc"),
                "actual_kp_now": actual_kp_now,
                "predicted_kp_3hr": predicted_kp_3hr,
                "prediction_error_3hr": round(abs(predicted_kp_3hr - actual_kp_now), 3),
                "actual_storm_class": actual_class,
                "predicted_storm_class": predicted_class,
                "class_match": actual_class == predicted_class,
            }
            output["validation"] = validation
            VALIDATION_ENGINE.record_frame(
                str(validation["timestamp_utc"]),
                predicted_kp_3hr,
                actual_kp_now,
                predicted_class,
                actual_class,
            )

            if should_generate_advisory_for_frame(frame_index, historical_snapshot, kp_forecast, replay_speed):
                try:
                    from app.services.advisory_generator import ADVISORY_GENERATOR, GroqAdvisoryError

                    try:
                        output["advisory"] = ADVISORY_GENERATOR.generate(
                            trigger_type="STORM_UPDATE",
                            kp_forecast=kp_forecast,
                            satellite_risks=output["satellite_risks"],
                            grid_risks=output["grid_risks"],
                            solar_data=historical_snapshot,
                        )
                    except GroqAdvisoryError:
                        pass
                except ImportError:
                    logger.debug("Advisory generator unavailable during replay", exc_info=True)
                except Exception as exc:
                    logger.warning("Replay advisory skipped at frame %d: %s", frame_index, exc)
            return _json_safe(output)
        finally:
            _restore_snapshot()


class ValidationEngine:
    """Accumulates replay prediction-vs-actual metrics for charts."""

    def __init__(self) -> None:
        self.predictions: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def record_frame(
        self,
        timestamp_utc: str,
        predicted_kp_3hr: float,
        actual_kp: float,
        predicted_class: str,
        actual_class: str,
    ) -> None:
        """Record one frame's validation comparison."""
        with self.lock:
            self.predictions.append(
                {
                    "timestamp_utc": timestamp_utc,
                    "predicted_kp": round(predicted_kp_3hr, 2),
                    "actual_kp": round(actual_kp, 2),
                    "error": round(abs(predicted_kp_3hr - actual_kp), 2),
                    "predicted_class": predicted_class,
                    "actual_class": actual_class,
                    "class_correct": predicted_class == actual_class,
                }
            )

    def get_metrics(self) -> dict:
        """Compute running validation metrics."""
        with self.lock:
            if not self.predictions:
                return {"total_frames": 0, "predictions": []}
            errors = [float(p["error"]) for p in self.predictions]
            class_hits = [p for p in self.predictions if p["class_correct"]]
            g3_actual = [p for p in self.predictions if p["actual_class"] in {"G3", "G4", "G5"}]
            g3_predicted = [p for p in g3_actual if p["predicted_class"] in {"G3", "G4", "G5"}]
            return {
                "rmse": round((sum(e**2 for e in errors) / len(errors)) ** 0.5, 3),
                "mae": round(sum(errors) / len(errors), 3),
                "max_error": round(max(errors), 2),
                "class_accuracy": round(len(class_hits) / len(self.predictions), 3),
                "storm_detection_rate": round(len(g3_predicted) / len(g3_actual), 3) if g3_actual else None,
                "total_frames": len(self.predictions),
                "predictions": copy.deepcopy(self.predictions[-100:]),
            }

    def reset(self) -> None:
        """Clear accumulated validation state."""
        with self.lock:
            self.predictions = []


class ReplayController:
    """
    Thread-safe controller for storm replay playback and frame computation.

    State transitions are guarded by ``self.lock``. Frame processing happens
    outside the lock, but cache insert/access uses the lock so seek and playback
    can share the LRU safely.
    """

    def __init__(self, socketio_instance: Any = None, pipeline_injector: Optional[PipelineInjector] = None) -> None:
        self.socketio = socketio_instance
        self.injector = pipeline_injector or PipelineInjector()
        self.state = ReplayState.IDLE
        self.lock = threading.RLock()

        self.storm_metadata: Optional[Dict[str, Any]] = None
        self.storm_df: Optional[pd.DataFrame] = None
        self.total_frames = 0
        self.current_frame = 0

        self.speed = ReplaySpeed.FAST
        self.play_thread: Optional[threading.Thread] = None
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self._pause_timeout_thread: Optional[threading.Thread] = None
        self._pause_timeout_cancel = threading.Event()

        self.frame_cache: "OrderedDict[int, dict]" = OrderedDict()
        self.cache_size_limit = REPLAY_FRAME_CACHE_SIZE
        self.frames_emitted = 0
        self.replay_start_real_time: Optional[float] = None
        self.replay_start_storm_time: Optional[str] = None

    def _emit(self, event: str, payload: dict) -> None:
        """Emit a socket event without letting SocketIO failures stop replay."""
        try:
            socketio = self.socketio
            if socketio is None:
                from app import socketio as app_socketio

                socketio = app_socketio
            socketio.emit(event, _json_safe(payload))
        except Exception as exc:
            logger.debug("Replay socket emit skipped for %s: %s", event, exc)

    def _set_state(self, new_state: ReplayState) -> None:
        """Set state and emit a state-change event."""
        with self.lock:
            previous = self.state
            self.state = new_state
            payload = {
                "previous_state": previous.value,
                "new_state": new_state.value,
                "storm_id": self.storm_metadata.get("storm_id") if self.storm_metadata else None,
                "frame": self.current_frame,
            }
        if previous != new_state:
            self._emit(WS_EVENT_REPLAY_STATE_CHANGE, payload)

    def _effective_resolution_minutes(self) -> int:
        """Return current dataframe frame spacing in minutes."""
        if not self.storm_metadata:
            return 1
        return int(self.storm_metadata.get("_effective_resolution_minutes") or self.storm_metadata.get("resolution_minutes", 1) or 1)

    def load_storm(self, storm_id: str) -> dict:
        """Load one storm into memory and prepare it for playback."""
        with self.lock:
            if self.state == ReplayState.PLAYING:
                return {"success": False, "error": "Cannot load while replay is playing"}
            previous_state = self.state
        self._set_state(ReplayState.LOADING)

        try:
            metadata, df = load_storm_dataframe(storm_id)
            with self.lock:
                self.storm_metadata = metadata
                self.storm_df = df
                self.total_frames = len(df)
                self.current_frame = 0
                self.frame_cache.clear()
                self.frames_emitted = 0
            VALIDATION_ENGINE.reset()
            self._set_state(ReplayState.READY)
            logger.info("Storm loaded: %s frames=%d", storm_id, len(df))
            return {
                "success": True,
                "storm_id": storm_id,
                "storm_name": metadata["name"],
                "total_frames": len(df),
                "duration_hours": metadata["duration_hours"],
                "data_type": metadata.get("data_type", "SYNTHETIC"),
                "state": ReplayState.READY.value,
            }
        except Exception as exc:
            with self.lock:
                self.state = previous_state if previous_state == ReplayState.IDLE else ReplayState.ERROR
            logger.error("Storm load failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def play(self, speed: ReplaySpeed = ReplaySpeed.FAST) -> dict:
        """Start playback from READY or restart from COMPLETED."""
        with self.lock:
            if self.state == ReplayState.PAUSED:
                return self.resume(speed)
            if self.state == ReplayState.COMPLETED:
                self.current_frame = 0
            if self.state not in {ReplayState.READY, ReplayState.COMPLETED}:
                return {"success": False, "error": f"Cannot play in state: {self.state.value}"}
            if self.storm_df is None or self.storm_metadata is None:
                return {"success": False, "error": "No storm loaded"}
            self.speed = speed
            self.stop_event.clear()
            self.pause_event.clear()
            self.replay_start_real_time = time.time()
            self.replay_start_storm_time = self._frame_timestamp(self.current_frame)
        self._set_state(ReplayState.PLAYING)

        self.play_thread = threading.Thread(target=self._replay_loop, name="NakshatraReplayThread", daemon=True)
        self.play_thread.start()
        return {
            "success": True,
            "state": ReplayState.PLAYING.value,
            "speed": speed.value,
            "from_frame": self.current_frame,
            "total": self.total_frames,
        }

    def pause(self) -> dict:
        """Pause playback and start an auto-stop timer."""
        with self.lock:
            if self.state != ReplayState.PLAYING:
                return {"success": False, "error": "Not playing"}
        self._set_state(ReplayState.PAUSED)
        self.pause_event.set()
        self._start_pause_timeout()
        return {"success": True, "state": ReplayState.PAUSED.value, "frame": self.current_frame, "timestamp": self._frame_timestamp(self.current_frame)}

    def resume(self, speed: Optional[ReplaySpeed] = None) -> dict:
        """Resume paused playback."""
        with self.lock:
            if self.state != ReplayState.PAUSED:
                return {"success": False, "error": "Not paused"}
            if speed is not None:
                self.speed = speed
        self._cancel_pause_timeout()
        self.pause_event.clear()
        self._set_state(ReplayState.PLAYING)
        return {"success": True, "state": ReplayState.PLAYING.value, "speed": self.speed.value}

    def stop(self) -> dict:
        """Stop replay, restore live mode, and return to IDLE."""
        self.stop_event.set()
        self.pause_event.clear()
        self._cancel_pause_timeout()
        if self.play_thread and self.play_thread.is_alive() and threading.current_thread() is not self.play_thread:
            self.play_thread.join(timeout=5.0)
        self.injector.restore_live_mode()
        with self.lock:
            self.storm_df = None
            self.storm_metadata = None
            self.total_frames = 0
            self.current_frame = 0
            self.frame_cache.clear()
            self.frames_emitted = 0
        self._set_state(ReplayState.IDLE)
        return {"success": True, "state": ReplayState.IDLE.value}

    def seek(self, frame_index: int) -> dict:
        """Seek asynchronously to a frame while READY or PAUSED."""
        with self.lock:
            if self.state not in {ReplayState.PAUSED, ReplayState.READY}:
                return {"success": False, "error": "Can only seek when PAUSED or READY"}
            if frame_index < 0 or frame_index >= self.total_frames:
                return {"success": False, "error": f"Frame {frame_index} out of range [0, {self.total_frames})"}
            self.current_frame = int(frame_index)
        SEEK_EXECUTOR.submit(self._compute_seek_frame, int(frame_index))
        return {"success": True, "frame": int(frame_index), "status": "COMPUTING"}

    def set_speed(self, speed: ReplaySpeed) -> dict:
        """Change replay speed."""
        with self.lock:
            self.speed = speed
        return {"success": True, "speed": speed.value}

    def get_status(self) -> dict:
        """Return current replay status."""
        with self.lock:
            total = self.total_frames
            progress = round(self.current_frame / total * 100.0, 1) if total else 0.0
            return {
                "state": self.state.value,
                "storm_id": self.storm_metadata.get("storm_id") if self.storm_metadata else None,
                "storm_name": self.storm_metadata.get("name") if self.storm_metadata else None,
                "data_type": self.storm_metadata.get("data_type") if self.storm_metadata else None,
                "current_frame": self.current_frame,
                "total_frames": total,
                "progress_pct": progress,
                "current_timestamp": self._frame_timestamp(self.current_frame),
                "speed": self.speed.value,
                "frames_emitted": self.frames_emitted,
                "cache_size": len(self.frame_cache),
                "key_moments": self.storm_metadata.get("key_moments", []) if self.storm_metadata else [],
            }

    def get_cached_frame(self, frame_index: int) -> Optional[dict]:
        """Return one cached full frame and refresh LRU access order."""
        with self.lock:
            if frame_index not in self.frame_cache:
                return None
            self.frame_cache.move_to_end(frame_index)
            return copy.deepcopy(self.frame_cache[frame_index])

    def _start_pause_timeout(self) -> None:
        """Start a 30-minute auto-stop guard for paused replay."""
        self._cancel_pause_timeout()
        self._pause_timeout_cancel.clear()
        self._pause_timeout_thread = threading.Thread(
            target=self._pause_timeout_worker,
            name="ReplayPauseTimeout",
            daemon=True,
        )
        self._pause_timeout_thread.start()

    def _cancel_pause_timeout(self) -> None:
        """Cancel the pause auto-stop guard."""
        self._pause_timeout_cancel.set()
        if (
            self._pause_timeout_thread
            and self._pause_timeout_thread.is_alive()
            and threading.current_thread() is not self._pause_timeout_thread
        ):
            self._pause_timeout_thread.join(timeout=0.2)

    def _pause_timeout_worker(self) -> None:
        """Stop replay if it remains paused for too long."""
        if self._pause_timeout_cancel.wait(REPLAY_PAUSE_TIMEOUT_SECONDS):
            return
        with self.lock:
            still_paused = self.state == ReplayState.PAUSED and self.pause_event.is_set()
        if still_paused:
            logger.warning("Replay paused for >30 min — auto-stopping to restore live pipeline.")
            self.stop()

    def _replay_loop(self) -> None:
        """Background playback loop."""
        storm_id = self.storm_metadata.get("storm_id") if self.storm_metadata else "unknown"
        self.injector.activate_replay_mode()
        try:
            resolution = self._effective_resolution_minutes()
            while True:
                with self.lock:
                    frame_index = self.current_frame
                    total_frames = self.total_frames
                    speed = self.speed
                if self.stop_event.is_set() or frame_index >= total_frames:
                    break
                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue
                try:
                    self._process_and_emit_frame(frame_index)
                except Exception as exc:
                    logger.error("Replay frame %d failed: %s", frame_index, exc, exc_info=True)
                with self.lock:
                    self.current_frame += 1
                    self.frames_emitted += 1
                    speed = self.speed
                sleep_seconds = (resolution * 60.0) / float(speed.value)
                if sleep_seconds > 0.001:
                    time.sleep(sleep_seconds)
        finally:
            self.injector.restore_live_mode()
            self._cancel_pause_timeout()
            if not self.stop_event.is_set():
                self._set_state(ReplayState.COMPLETED)
                self._emit(
                    WS_EVENT_REPLAY_COMPLETED,
                    {
                        "storm_id": storm_id,
                        "frames_total": self.frames_emitted,
                        "validation_summary": VALIDATION_ENGINE.get_metrics(),
                    },
                )

    def _compute_seek_frame(self, frame_index: int) -> None:
        """Compute a seek frame in the background and notify clients."""
        activated_here = False
        if not PipelineInjector.REPLAY_MODE_ACTIVE:
            self.injector.activate_replay_mode()
            activated_here = True
        try:
            output = self._get_or_compute_frame(frame_index)
            summary = self._build_frame_summary(frame_index, output)
            self._emit(WS_EVENT_REPLAY_FRAME_READY, {"frame_index": frame_index, "status": "READY", "summary": summary})
        except Exception as exc:
            logger.error("Seek frame %d failed: %s", frame_index, exc, exc_info=True)
            self._emit(WS_EVENT_REPLAY_FRAME_READY, {"frame_index": frame_index, "status": "ERROR", "error": str(exc)})
        finally:
            if activated_here:
                self.injector.restore_live_mode()

    def _get_or_compute_frame(self, frame_index: int) -> dict:
        """Return full frame output from LRU cache or compute it."""
        with self.lock:
            if frame_index in self.frame_cache:
                self.frame_cache.move_to_end(frame_index)
                return copy.deepcopy(self.frame_cache[frame_index])
            storm_df = self.storm_df
            metadata = copy.deepcopy(self.storm_metadata)
            speed = self.speed
        if storm_df is None or metadata is None:
            raise RuntimeError("No storm loaded")
        row = storm_df.iloc[frame_index]
        snapshot = self.injector.build_snapshot_from_row(row)
        output = self.injector.run_pipeline_on_snapshot(
            snapshot,
            frame_index,
            storm_df,
            resolution_minutes=int(metadata.get("_effective_resolution_minutes", metadata.get("resolution_minutes", 1))),
            replay_speed=speed,
        )
        with self.lock:
            self.frame_cache[frame_index] = copy.deepcopy(output)
            self.frame_cache.move_to_end(frame_index)
            while len(self.frame_cache) > self.cache_size_limit:
                self.frame_cache.popitem(last=False)
        return output

    def _process_and_emit_frame(self, frame_index: int) -> None:
        """Process one frame, cache full output, and emit compact summary."""
        output = self._get_or_compute_frame(frame_index)
        summary = self._build_frame_summary(frame_index, output)
        self._emit(WS_EVENT_REPLAY_FRAME, summary)
        if summary.get("key_moment"):
            self._emit(
                WS_EVENT_REPLAY_KEY_MOMENT,
                {
                    "moment": summary["key_moment"],
                    "frame": frame_index,
                    "kp": summary["kp_current"],
                    "storm_class": summary["storm_class"],
                },
            )
        if frame_index % VALIDATION_SAMPLE_STEP == 0:
            self._emit(WS_EVENT_REPLAY_VALIDATION_UPDATE, VALIDATION_ENGINE.get_metrics())

    def _build_frame_summary(self, frame_index: int, output: dict) -> dict:
        """Build the compact replay_frame payload."""
        kp_forecast = output.get("kp_forecast", {})
        sat_risks = output.get("satellite_risks", {})
        grid_risks = output.get("grid_risks", {})
        current = kp_forecast.get("current", {})
        timestamp = self._frame_timestamp(frame_index)
        total = self.total_frames or 1
        key_moment = self._find_key_moment(frame_index)
        return {
            "frame_index": frame_index,
            "total_frames": self.total_frames,
            "progress_pct": round(frame_index / total * 100.0, 1),
            "storm_timestamp": timestamp,
            "speed": self.speed.value,
            "kp_current": _safe_float(current.get("kp"), 0.0),
            "storm_class": str(current.get("storm_class", "QUIET")),
            "satellite_critical_count": int(sat_risks.get("critical_count", 0)),
            "grid_critical_count": int(grid_risks.get("national_summary", {}).get("critical_corridors_count", 0)),
            "advisory": output.get("advisory"),
            "validation": output.get("validation"),
            "is_historical": True,
            "replay_data_type": self.storm_metadata.get("data_type", "SYNTHETIC") if self.storm_metadata else "SYNTHETIC",
            "key_moment": key_moment,
        }

    def _frame_timestamp(self, frame_index: int) -> Optional[str]:
        """Return timestamp for a frame index."""
        if self.storm_df is None or frame_index < 0 or frame_index >= len(self.storm_df):
            return None
        return str(self.storm_df.iloc[frame_index]["timestamp_utc"])

    def _find_key_moment(self, frame_index: int) -> Optional[dict]:
        """Return the key moment near a frame, if any."""
        if not self.storm_metadata:
            return None
        resolution = self._effective_resolution_minutes()
        for moment in self.storm_metadata.get("key_moments", []):
            moment_frame = int(float(moment.get("offset_hours", 0)) * 60.0 / resolution)
            if abs(frame_index - moment_frame) <= 2:
                return copy.deepcopy(moment)
        return None


class FullValidationJob:
    """Run complete storm validation in a managed thread pool."""

    def __init__(self, storm_id: str) -> None:
        self.storm_id = storm_id
        self.job_id = f"VAL-{storm_id}-{int(time.time())}"
        self.state = "PENDING"
        self.progress = 0.0
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.future: Optional[Future[Any]] = None

    def start(self) -> str:
        """Submit the job to the validation executor."""
        self.state = "COMPUTING"
        self.future = VALIDATION_EXECUTOR.submit(self._run)
        return self.job_id

    def _run(self) -> None:
        """Execute validation and persist results in a finally block."""
        partial_results: List[dict] = []
        metadata: Dict[str, Any] = {"name": self.storm_id}
        try:
            metadata, df = load_storm_dataframe(self.storm_id)
            step = max(1, VALIDATION_SAMPLE_STEP)
            injector = PipelineInjector()
            total = len(df)
            resolution = int(metadata.get("_effective_resolution_minutes", metadata.get("resolution_minutes", 1)))
            for frame_index in range(0, total, step):
                row = df.iloc[frame_index]
                snapshot = injector.build_snapshot_from_row(row)
                history = _get_history_window(df, frame_index, 24, resolution)
                from app.services.feature_engineering import compute_features_realtime
                from app.services.kp_predictor import run_inference_cycle

                features = compute_features_realtime(snapshot=snapshot, history_df=history)
                forecast = run_inference_cycle(features) if features else _fallback_forecast(snapshot)
                actual_kp = _safe_float(row.get("kp_current"), 0.0)
                f3 = forecast.get("forecast", {}).get("3hr", {})
                f6 = forecast.get("forecast", {}).get("6hr", {})
                f12 = forecast.get("forecast", {}).get("12hr", {})
                partial_results.append(
                    {
                        "timestamp": str(row["timestamp_utc"]),
                        "frame": frame_index,
                        "actual_kp": actual_kp,
                        "predicted_3hr": _safe_float(f3.get("kp"), actual_kp),
                        "predicted_6hr": _safe_float(f6.get("kp"), actual_kp),
                        "predicted_12hr": _safe_float(f12.get("kp"), actual_kp),
                        "uncertainty_3hr": _safe_float(f3.get("uncertainty"), 0.0),
                        "ci_lower_3hr": _safe_float(f3.get("ci_lower_90"), actual_kp),
                        "ci_upper_3hr": _safe_float(f3.get("ci_upper_90"), actual_kp),
                        "actual_class": kp_to_storm_class(actual_kp),
                        "predicted_class": str(forecast.get("summary", {}).get("peak_storm_class", "QUIET")),
                    }
                )
                partial_results[-1]["class_match"] = partial_results[-1]["actual_class"] == partial_results[-1]["predicted_class"]
                self.progress = round(frame_index / max(total, 1) * 100.0, 1)

            self.result = _build_validation_result(self.storm_id, metadata, partial_results)
            self.progress = 100.0
            self.state = "COMPLETE"
        except Exception as exc:
            self.state = "ERROR"
            self.error = str(exc)
            if partial_results:
                self.result = _build_validation_result(self.storm_id, metadata, partial_results)
                self.result["error"] = str(exc)
            logger.error("Full validation failed: %s", exc, exc_info=True)
        finally:
            self._persist_result()

    def _persist_result(self) -> None:
        """Persist final or partial validation output to MySQL if available."""
        result = self.result or {
            "storm_id": self.storm_id,
            "storm_name": self.storm_id,
            "total_samples": 0,
            "rmse_3hr": None,
            "mae_3hr": None,
            "class_accuracy": None,
            "data_points": [],
            "error": self.error,
        }
        try:
            from app.database.db import get_db

            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO validation_results (
                            storm_id, computed_at_utc, total_samples, rmse_3hr,
                            mae_3hr, class_accuracy, full_result_json
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            self.storm_id,
                            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            result.get("total_samples", 0),
                            result.get("rmse_3hr"),
                            result.get("mae_3hr"),
                            result.get("class_accuracy"),
                            json.dumps(_json_safe(result)),
                        ),
                    )
        except Exception as exc:
            logger.debug("Validation result persistence skipped: %s", exc)

    def status(self) -> dict:
        """Return API-safe job status."""
        return {
            "job_id": self.job_id,
            "storm_id": self.storm_id,
            "state": self.state,
            "progress_pct": self.progress,
            "result": self.result if self.state == "COMPLETE" else None,
            "error": self.error,
        }


def _build_validation_result(storm_id: str, metadata: Mapping[str, Any], points: List[dict]) -> dict:
    """Build aggregate validation metrics from data points."""
    if not points:
        return {
            "storm_id": storm_id,
            "storm_name": metadata.get("name", storm_id),
            "total_samples": 0,
            "rmse_3hr": None,
            "mae_3hr": None,
            "class_accuracy": None,
            "data_points": [],
            "summary_text": "No validation samples were computed.",
        }
    errors = [abs(_safe_float(p["predicted_3hr"]) - _safe_float(p["actual_kp"])) for p in points]
    class_hits = [p for p in points if p.get("class_match")]
    rmse = round((sum(e**2 for e in errors) / len(errors)) ** 0.5, 3)
    mae = round(sum(errors) / len(errors), 3)
    acc = round(len(class_hits) / len(points), 3)
    return {
        "storm_id": storm_id,
        "storm_name": metadata.get("name", storm_id),
        "total_samples": len(points),
        "rmse_3hr": rmse,
        "mae_3hr": mae,
        "class_accuracy": acc,
        "data_points": points,
        "summary_text": (
            f"NAKSHATRA-KAVACH achieved RMSE of {rmse} Kp units on "
            f"{metadata.get('name', storm_id)}. Storm class matched in {round(acc * 100, 0):.0f}% of samples."
        ),
    }


def get_cached_validation_result(storm_id: str) -> Optional[dict]:
    """Return latest persisted validation result for a storm, if available."""
    try:
        from app.database.db import get_db

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT full_result_json FROM validation_results WHERE storm_id=%s ORDER BY computed_at_utc DESC, id DESC LIMIT 1",
                    (storm_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        raw = row.get("full_result_json") if isinstance(row, dict) else row[0]
        return json.loads(raw)
    except Exception as exc:
        logger.debug("Cached validation lookup skipped: %s", exc)
        return None


def build_replay_timeline(storm_df: pd.DataFrame, storm_metadata: dict) -> dict:
    """Build timeline data for the replay scrubber."""
    total_frames = len(storm_df)
    resolution = int(storm_metadata.get("_effective_resolution_minutes", storm_metadata.get("resolution_minutes", 1)))
    sample_step = max(1, total_frames // 300)
    kp_profile = []
    for i in range(0, total_frames, sample_step):
        row = storm_df.iloc[i]
        kp = _safe_float(row.get("kp_current"), 0.0)
        kp_profile.append(
            {
                "frame": i,
                "kp": round(kp, 1),
                "bz": round(_safe_float(row.get("bz_gsm"), 0.0), 1),
                "timestamp": str(row["timestamp_utc"]),
                "color": kp_to_color(kp),
            }
        )

    markers = []
    for moment in storm_metadata.get("key_moments", []):
        frame = int(float(moment.get("offset_hours", 0)) * 60.0 / resolution)
        if 0 <= frame < total_frames:
            markers.append(
                {
                    "frame": frame,
                    "pct": round(frame / max(total_frames, 1) * 100.0, 1),
                    "event": moment.get("event", ""),
                    "kp_at_moment": round(_safe_float(storm_df.iloc[frame].get("kp_current"), 0.0), 1),
                }
            )

    transitions = []
    previous = "QUIET"
    for i in range(0, total_frames, sample_step):
        kp = _safe_float(storm_df.iloc[i].get("kp_current"), 0.0)
        current = kp_to_storm_class(kp)
        if current != previous:
            transitions.append(
                {
                    "frame": i,
                    "pct": round(i / max(total_frames, 1) * 100.0, 1),
                    "from": previous,
                    "to": current,
                    "timestamp": str(storm_df.iloc[i]["timestamp_utc"]),
                }
            )
            previous = current

    total_minutes = total_frames * resolution
    return {
        "storm_id": storm_metadata["storm_id"],
        "storm_name": storm_metadata["name"],
        "total_frames": total_frames,
        "duration_str": f"{total_minutes // 60} hours {total_minutes % 60} minutes",
        "resolution_minutes": resolution,
        "kp_profile": kp_profile,
        "moment_markers": markers,
        "transitions": transitions,
        "kp_peak": storm_metadata["kp_peak"],
        "display_color": storm_metadata["display_color"],
        "data_type": storm_metadata.get("data_type", "SYNTHETIC"),
    }


def _fallback_forecast(snapshot: Mapping[str, Any]) -> dict:
    """Build a minimal Layer 3-shaped forecast when model inference fails."""
    kp = _safe_float(snapshot.get("kp", {}).get("kp_current"), 0.0)
    storm_class = kp_to_storm_class(kp)
    forecast = {}
    for horizon in ("3hr", "6hr", "12hr", "24hr"):
        forecast[horizon] = {
            "kp": round(kp, 2),
            "uncertainty": 0.75,
            "ci_lower_90": round(max(0.0, kp - 1.2), 2),
            "ci_upper_90": round(min(9.0, kp + 1.2), 2),
            "storm_class": storm_class,
            "p_storm_g1": 1.0 if kp >= 5.0 else 0.0,
            "p_storm_g2": 1.0 if kp >= 6.0 else 0.0,
            "p_storm_g3": 1.0 if kp >= 7.0 else 0.0,
            "p_storm_g4": 1.0 if kp >= 8.0 else 0.0,
            "p_storm_g5": 1.0 if kp >= 9.0 else 0.0,
        }
    return {
        "computed_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_quality_used": snapshot.get("data_quality", "GOOD"),
        "prediction_confidence": "MODERATE",
        "current": {"kp": kp, "storm_class": storm_class, "storm_class_color": kp_to_color(kp), "storm_class_numeric": 0},
        "forecast": forecast,
        "summary": {
            "peak_storm_class": storm_class,
            "peak_storm_class_color": kp_to_color(kp),
            "peak_kp_24hr": kp,
            "peak_horizon": "3hr",
            "storm_probability_12hr": 1.0 if kp >= 5.0 else 0.0,
            "storm_onset_detected": kp >= 5.0,
            "storm_imminent": kp >= 5.0,
            "recommended_action_level": snapshot.get("computed", {}).get("recommended_action_level", "MONITOR"),
            "transit_warning_minutes": snapshot.get("computed", {}).get("transit_warning_minutes", 60.0),
            "cme_active": snapshot.get("cme", {}).get("earth_directed", False),
            "dominant_driver": "Historical Kp fallback",
        },
        "shap": None,
    }


def _fallback_satellite_risks() -> dict:
    """Return an empty but schema-compatible Layer 4 result."""
    return {"critical_count": 0, "high_count": 0, "tier1": {}, "tier2": [], "fleet_summary": {}}


def _fallback_grid_risks() -> dict:
    """Return an empty but schema-compatible Layer 5 result."""
    return {
        "corridors": [],
        "map_data": [],
        "national_summary": {
            "critical_corridors_count": 0,
            "high_corridors_count": 0,
            "max_gic_amps": 0.0,
            "max_gic_corridor": "N/A",
        },
    }


VALIDATION_ENGINE = ValidationEngine()
REPLAY_CONTROLLER = ReplayController()
