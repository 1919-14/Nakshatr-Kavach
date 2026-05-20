# backend/app/services/feature_engineering.py
"""
NAKSHATRA-KAVACH Layer 2: physics-informed feature engineering.

This module converts Layer 1 solar-wind snapshots and recent history into the
45 tabular features used by XGBoost and the 24 x 15 hourly sequence used by
the LSTM forecast model. It never mutates Layer 1 state, never calls external
APIs, and never runs model inference.
"""

from __future__ import annotations

import copy
import logging
import math
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from app.utils.constants import (
    BZ_DANGER_THRESHOLD,
    BZ_EXTREME_THRESHOLD,
    EPSILON_SCALE_FACTOR,
    FEATURE_IMPUTATION_VALUES,
    L0_KM,
    N_SEQUENCE_FEATURES,
    N_XGB_FEATURES,
    SCALER_LSTM_PATH,
    SCALER_XGB_PATH,
    SEQUENCE_LENGTH,
)

logger = logging.getLogger(__name__)

ArrayLike = Union[float, int, Sequence[float], np.ndarray, pd.Series]


FEATURE_NAMES: List[str] = [
    "bz_current",
    "bz_mean_30min",
    "bz_mean_1hr",
    "bz_mean_3hr",
    "bz_min_30min",
    "bz_min_1hr",
    "bz_std_1hr",
    "bz_southward_duration_30min",
    "bz_rate_of_change_per_min",
    "bt_mean_30min",
    "bt_mean_1hr",
    "bt_max_1hr",
    "bt_rate_of_change_per_min",
    "sw_speed_mean_1hr",
    "sw_speed_max_1hr",
    "sw_speed_rate_of_change",
    "proton_density_mean_1hr",
    "dynamic_pressure_mean_1hr",
    "dynamic_pressure_max_1hr",
    "epsilon_current",
    "epsilon_mean_30min",
    "epsilon_mean_1hr",
    "epsilon_cumulative_3hr",
    "consecutive_southward_minutes",
    "southward_fraction_30min",
    "southward_fraction_1hr",
    "southward_fraction_3hr",
    "bz_southward_onset_flag",
    "xray_severity_current",
    "xray_severity_max_6hr",
    "time_since_last_M_class_hours",
    "xray_peak_flux_24hr",
    "cme_earth_directed",
    "cme_speed_normalized",
    "cme_arrival_hours",
    "cme_is_imminent",
    "kp_current",
    "kp_mean_6hr",
    "kp_mean_12hr",
    "kp_max_24hr",
    "kp_rate_of_change",
    "bz_speed_interaction",
    "bt_speed_interaction",
    "imf_clock_angle_sin",
    "imf_clock_angle_cos",
]


SEQUENCE_FEATURE_NAMES: List[str] = [
    "bz_gsm",
    "bt_total",
    "sw_speed_kmps",
    "proton_density_ccm",
    "epsilon_scaled",
    "dynamic_pressure_npa",
    "bz_southward_flag",
    "xray_severity",
    "kp_current",
    "bz_rate_1hr",
    "bt_rate_1hr",
    "sw_speed_rate_1hr",
    "imf_clock_sin",
    "imf_clock_cos",
    "southward_fraction_1hr",
]


FEATURE_UNITS: Dict[str, str] = {
    "bz_current": "nT",
    "bz_mean_30min": "nT",
    "bz_mean_1hr": "nT",
    "bz_mean_3hr": "nT",
    "bz_min_30min": "nT",
    "bz_min_1hr": "nT",
    "bz_std_1hr": "nT",
    "bz_southward_duration_30min": "min",
    "bz_rate_of_change_per_min": "nT/min",
    "bt_mean_30min": "nT",
    "bt_mean_1hr": "nT",
    "bt_max_1hr": "nT",
    "bt_rate_of_change_per_min": "nT/min",
    "sw_speed_mean_1hr": "km/s",
    "sw_speed_max_1hr": "km/s",
    "sw_speed_rate_of_change": "km/s/min",
    "proton_density_mean_1hr": "cm^-3",
    "dynamic_pressure_mean_1hr": "nPa",
    "dynamic_pressure_max_1hr": "nPa",
    "epsilon_current": "scaled",
    "epsilon_mean_30min": "scaled",
    "epsilon_mean_1hr": "scaled",
    "epsilon_cumulative_3hr": "scaled",
    "consecutive_southward_minutes": "min",
    "southward_fraction_30min": "fraction",
    "southward_fraction_1hr": "fraction",
    "southward_fraction_3hr": "fraction",
    "bz_southward_onset_flag": "binary",
    "xray_severity_current": "class",
    "xray_severity_max_6hr": "class",
    "time_since_last_M_class_hours": "hours",
    "xray_peak_flux_24hr": "log10 shifted",
    "cme_earth_directed": "binary",
    "cme_speed_normalized": "fraction",
    "cme_arrival_hours": "hours",
    "cme_is_imminent": "binary",
    "kp_current": "Kp",
    "kp_mean_6hr": "Kp",
    "kp_mean_12hr": "Kp",
    "kp_max_24hr": "Kp",
    "kp_rate_of_change": "Kp/hour",
    "bz_speed_interaction": "scaled",
    "bt_speed_interaction": "scaled",
    "imf_clock_angle_sin": "unitless",
    "imf_clock_angle_cos": "unitless",
}


DISPLAY_NAMES: Dict[str, str] = {
    "bz_current": "Bz (Current)",
    "bz_mean_30min": "Bz Mean (30 min)",
    "bz_mean_1hr": "Bz Mean (1 hr)",
    "bz_mean_3hr": "Bz Mean (3 hr)",
    "bz_min_30min": "Bz Minimum (30 min)",
    "bz_min_1hr": "Bz Minimum (1 hr)",
    "bz_std_1hr": "Bz Variability (1 hr)",
    "bz_southward_duration_30min": "Southward Bz Duration",
    "bz_rate_of_change_per_min": "Bz Turning Rate",
    "bt_mean_30min": "Bt Mean (30 min)",
    "bt_mean_1hr": "Bt Mean (1 hr)",
    "bt_max_1hr": "Bt Maximum (1 hr)",
    "bt_rate_of_change_per_min": "Bt Shock Rate",
    "sw_speed_mean_1hr": "Solar Wind Speed Mean",
    "sw_speed_max_1hr": "Solar Wind Speed Maximum",
    "sw_speed_rate_of_change": "Solar Wind Acceleration",
    "proton_density_mean_1hr": "Proton Density Mean",
    "dynamic_pressure_mean_1hr": "Dynamic Pressure Mean",
    "dynamic_pressure_max_1hr": "Dynamic Pressure Maximum",
    "epsilon_current": "Epsilon Coupling (Current)",
    "epsilon_mean_30min": "Epsilon Mean (30 min)",
    "epsilon_mean_1hr": "Epsilon Mean (1 hr)",
    "epsilon_cumulative_3hr": "Epsilon Cumulative (3 hr)",
    "consecutive_southward_minutes": "Current Southward Streak",
    "southward_fraction_30min": "Southward Fraction (30 min)",
    "southward_fraction_1hr": "Southward Fraction (1 hr)",
    "southward_fraction_3hr": "Southward Fraction (3 hr)",
    "bz_southward_onset_flag": "Bz Southward Onset",
    "xray_severity_current": "X-Ray Severity",
    "xray_severity_max_6hr": "X-Ray Severity Max",
    "time_since_last_M_class_hours": "Time Since M-Class Flare",
    "xray_peak_flux_24hr": "X-Ray Peak Flux",
    "cme_earth_directed": "Earth-Directed CME",
    "cme_speed_normalized": "CME Speed",
    "cme_arrival_hours": "CME Arrival",
    "cme_is_imminent": "CME Imminent",
    "kp_current": "Kp (Current)",
    "kp_mean_6hr": "Kp Mean (6 hr)",
    "kp_mean_12hr": "Kp Mean (12 hr)",
    "kp_max_24hr": "Kp Maximum (24 hr)",
    "kp_rate_of_change": "Kp Rate of Change",
    "bz_speed_interaction": "Bz-Speed Interaction",
    "bt_speed_interaction": "Bt-Speed Interaction",
    "imf_clock_angle_sin": "IMF Clock Angle Sine",
    "imf_clock_angle_cos": "IMF Clock Angle Cosine",
}


PHYSICAL_MEANINGS: Dict[str, str] = {
    "bz_current": "Southward IMF component, the primary storm driver",
    "bz_mean_30min": "Recent average magnetic reconnection driver",
    "bz_mean_1hr": "Short-term sustained southward IMF driver",
    "bz_mean_3hr": "Storm main-phase magnetic driving history",
    "bz_min_30min": "Peak recent southward IMF excursion",
    "bz_min_1hr": "Strongest short-term southward IMF excursion",
    "bz_std_1hr": "Bz variability, distinguishing steady driving from oscillation",
    "bz_southward_duration_30min": "Minutes of active southward coupling in the last 30 minutes",
    "bz_rate_of_change_per_min": "Rapid Bz turning rate for storm onset detection",
    "bt_mean_30min": "Recent total IMF magnitude",
    "bt_mean_1hr": "Total magnetic field strength over the short-term forecast window",
    "bt_max_1hr": "Peak magnetic compression during possible shock arrival",
    "bt_rate_of_change_per_min": "Sudden IMF strengthening associated with CME shocks",
    "sw_speed_mean_1hr": "Solar wind flow speed controlling coupling strength",
    "sw_speed_max_1hr": "Peak solar wind speed, a CME shock signature",
    "sw_speed_rate_of_change": "Solar wind acceleration indicating shock-front passage",
    "proton_density_mean_1hr": "Plasma density contributing to magnetopause pressure",
    "dynamic_pressure_mean_1hr": "Average ram pressure compressing Earth's magnetosphere",
    "dynamic_pressure_max_1hr": "Peak pressure impulse capable of sudden storm commencement",
    "epsilon_current": "Current Akasofu energy coupling into the magnetosphere",
    "epsilon_mean_30min": "Recent mean solar-wind to magnetosphere coupling rate",
    "epsilon_mean_1hr": "One-hour average energy coupling, highly predictive of Kp",
    "epsilon_cumulative_3hr": "Integrated storm energy input over the main-phase buildup",
    "consecutive_southward_minutes": "Current uninterrupted southward Bz duration",
    "southward_fraction_30min": "Fraction of recent minutes with active southward coupling",
    "southward_fraction_1hr": "Fraction of the last hour with storm-effective Bz",
    "southward_fraction_3hr": "Persistence of storm-driving IMF over three hours",
    "bz_southward_onset_flag": "Binary detector for the start of southward IMF driving",
    "xray_severity_current": "Current solar flare class as an activity precursor",
    "xray_severity_max_6hr": "Worst recent flare class indicating eruptive solar state",
    "time_since_last_M_class_hours": "Recency of M/X-class flare activity",
    "xray_peak_flux_24hr": "Log-scaled peak flare energy over the last day",
    "cme_earth_directed": "Presence of an Earth-directed CME",
    "cme_speed_normalized": "Normalized CME speed for long-horizon storm risk",
    "cme_arrival_hours": "Time until modeled CME impact at Earth",
    "cme_is_imminent": "Binary flag for CME arrival within six hours",
    "kp_current": "Current global geomagnetic disturbance level",
    "kp_mean_6hr": "Recent magnetospheric disturbance state",
    "kp_mean_12hr": "Half-day storm persistence context",
    "kp_max_24hr": "Peak storm intensity in the last day",
    "kp_rate_of_change": "Geomagnetic storm intensification or recovery rate",
    "bz_speed_interaction": "Nonlinear storm power from southward field and flow speed",
    "bt_speed_interaction": "Magnetic energy flux proxy from IMF strength and speed",
    "imf_clock_angle_sin": "Dawn-dusk IMF direction encoding",
    "imf_clock_angle_cos": "North-south IMF direction encoding",
}


_scaler_lock = threading.RLock()
_latest_lock = threading.RLock()
_scaler_xgb: Optional[Any] = None
_scaler_lstm: Optional[Any] = None
_scalers_loaded: bool = False


class _IdentityScaler:
    """Passthrough scaler used when current models consume raw engineered features."""

    def transform(self, values: Any) -> np.ndarray:
        return np.asarray(values)


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_metadata() -> Dict[str, Any]:
    """Build a zero-valued metadata payload for process startup and fallbacks."""
    raw = np.zeros(N_XGB_FEATURES, dtype=np.float64)
    scaled = np.zeros(N_XGB_FEATURES, dtype=np.float64)
    return build_feature_metadata(
        raw_vector=raw,
        scaled_vector=scaled,
        data_quality="UNKNOWN",
        stale_data=False,
        computed_at_utc=_utcnow_iso(),
    )


LATEST_FEATURES: Dict[str, Any] = {
    "xgb_vector_scaled": np.zeros(N_XGB_FEATURES, dtype=np.float64),
    "lstm_sequence_scaled": np.zeros((1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES), dtype=np.float32),
    "xgb_vector_raw": np.zeros(N_XGB_FEATURES, dtype=np.float64),
    "lstm_sequence_raw": np.zeros((1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES), dtype=np.float32),
    "feature_metadata": {},
    "data_quality": "UNKNOWN",
    "stale_data": False,
    "computed_at_utc": _utcnow_iso(),
}


class EpsilonCalculator:
    """Vectorized Akasofu epsilon coupling calculator for scalars or arrays."""

    @staticmethod
    def compute(
        sw_speed_kmps: ArrayLike,
        bt_total: ArrayLike,
        by_gsm: ArrayLike,
        bz_gsm: ArrayLike,
    ) -> Union[float, np.ndarray]:
        """
        Compute Akasofu epsilon with theta = atan2(By, Bz).

        Args:
            sw_speed_kmps: Solar wind speed in km/s.
            bt_total: Total IMF magnitude in nT.
            by_gsm: IMF By component in GSM coordinates.
            bz_gsm: IMF Bz component in GSM coordinates.

        Returns:
            Epsilon coupling in the same scaled physical convention requested by
            the Layer 2 contract, either as a float or a numpy array.
        """
        scalar_input = np.isscalar(sw_speed_kmps) and np.isscalar(bt_total) and np.isscalar(by_gsm) and np.isscalar(bz_gsm)
        speed = np.asarray(sw_speed_kmps, dtype=np.float64)
        bt = np.asarray(bt_total, dtype=np.float64)
        by = np.asarray(by_gsm, dtype=np.float64)
        bz = np.asarray(bz_gsm, dtype=np.float64)
        valid = np.isfinite(speed) & np.isfinite(bt) & np.isfinite(by) & np.isfinite(bz)
        valid &= (speed > 0.0) & (bt > 0.0)
        theta = np.arctan2(by, bz)
        sin_half_theta_4 = np.sin(theta / 2.0) ** 4
        epsilon = speed * (bt ** 2) * sin_half_theta_4 * (L0_KM ** 2) / 1000.0
        epsilon = np.where(valid, epsilon, 0.0)
        if scalar_input:
            return float(np.asarray(epsilon).item())
        return np.asarray(epsilon, dtype=np.float64)

    @staticmethod
    def compute_scaled(
        sw_speed_kmps: ArrayLike,
        bt_total: ArrayLike,
        by_gsm: ArrayLike,
        bz_gsm: ArrayLike,
    ) -> Union[float, np.ndarray]:
        """
        Compute Akasofu epsilon divided by EPSILON_SCALE_FACTOR.

        Args:
            sw_speed_kmps: Solar wind speed in km/s.
            bt_total: Total IMF magnitude in nT.
            by_gsm: IMF By component in GSM coordinates.
            bz_gsm: IMF Bz component in GSM coordinates.

        Returns:
            Scaled epsilon coupling as a float or numpy array.
        """
        value = EpsilonCalculator.compute(sw_speed_kmps, bt_total, by_gsm, bz_gsm)
        return value / EPSILON_SCALE_FACTOR


def compute_dynamic_pressure_npa(
    proton_density_ccm: ArrayLike,
    sw_speed_kmps: ArrayLike,
) -> Union[float, np.ndarray]:
    """
    Compute solar-wind dynamic pressure in nPa.

    Args:
        proton_density_ccm: Proton density in particles per cubic centimeter.
        sw_speed_kmps: Solar wind speed in kilometers per second.

    Returns:
        Dynamic pressure in nanopascals as a float or numpy array.
    """
    scalar_input = np.isscalar(proton_density_ccm) and np.isscalar(sw_speed_kmps)
    density = np.asarray(proton_density_ccm, dtype=np.float64)
    speed = np.asarray(sw_speed_kmps, dtype=np.float64)
    density = np.where(np.isfinite(density), density, FEATURE_IMPUTATION_VALUES["density"])
    speed = np.where(np.isfinite(speed), speed, FEATURE_IMPUTATION_VALUES["speed"])
    pressure = 0.5 * 1.6726e-27 * (density * 1e6) * ((speed * 1000.0) ** 2) * 1e9
    pressure = np.where(np.isfinite(pressure), pressure, 0.0)
    if scalar_input:
        return float(np.asarray(pressure).item())
    return np.asarray(pressure, dtype=np.float64)


class RollingWindowCalculator:
    """Fast rolling-window statistics over a UTC DatetimeIndex DataFrame."""

    def __init__(self, history_df: pd.DataFrame) -> None:
        """
        Initialize the calculator from historical readings.

        Args:
            history_df: Solar-wind history indexed by timestamp_utc or containing
                a timestamp_utc/timestamp column.
        """
        self.df = prepare_history_dataframe(history_df)
        if not self.df.empty:
            self._log_large_gaps()

    def _log_large_gaps(self) -> None:
        """Log debug information when time gaps greater than five minutes exist."""
        diffs = self.df.index.to_series().diff().dropna()
        gap_count = int((diffs > pd.Timedelta(minutes=5)).sum())
        if gap_count:
            logger.debug("Rolling history contains %d gaps greater than 5 minutes", gap_count)

    def _window_series(self, column: str, window_minutes: int) -> Optional[pd.Series]:
        """
        Return a non-null series for the requested time window.

        Args:
            column: DataFrame column name.
            window_minutes: Window width in minutes.

        Returns:
            Non-null pandas Series, or None when data is unavailable or below the
            50 percent coverage requirement.
        """
        if self.df.empty or column not in self.df.columns:
            return None
        cutoff_time = self.df.index.max() - pd.Timedelta(minutes=window_minutes)
        window = self.df.loc[self.df.index >= cutoff_time, column].dropna()
        if len(window) == 0:
            return None
        if len(window) < window_minutes * 0.5:
            return None
        return window

    def mean(self, column: str, window_minutes: int) -> Optional[float]:
        """Return the rolling mean for a column and window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float(series.mean())

    def std(self, column: str, window_minutes: int) -> Optional[float]:
        """Return the rolling standard deviation for a column and window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float(series.std(ddof=0))

    def min(self, column: str, window_minutes: int) -> Optional[float]:
        """Return the rolling minimum for a column and window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float(series.min())

    def max(self, column: str, window_minutes: int) -> Optional[float]:
        """Return the rolling maximum for a column and window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float(series.max())

    def count_below(self, column: str, threshold: float, window_minutes: int) -> Optional[float]:
        """Return the count of readings below threshold in a rolling window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float((series < threshold).sum())

    def count_above(self, column: str, threshold: float, window_minutes: int) -> Optional[float]:
        """Return the count of readings above threshold in a rolling window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float((series > threshold).sum())

    def cumsum(self, column: str, window_minutes: int) -> Optional[float]:
        """Return the cumulative sum for a column and window."""
        series = self._window_series(column, window_minutes)
        return None if series is None else float(series.sum())

    def consecutive_below(self, column: str, threshold: float) -> float:
        """
        Count consecutive readings below threshold working backward from the end.

        Args:
            column: Column to inspect.
            threshold: Threshold that defines an active streak.

        Returns:
            Consecutive count as a float.
        """
        if self.df.empty or column not in self.df.columns:
            return 0.0
        series = self.df[column].dropna()
        count = 0
        for value in reversed(series.to_numpy(dtype=np.float64)):
            if value < threshold:
                count += 1
            else:
                break
        return float(count)

    def value_n_minutes_ago(self, column: str, n_minutes: int) -> Optional[float]:
        """
        Return the latest value at or before approximately n minutes ago.

        Args:
            column: Column to inspect.
            n_minutes: Offset from the latest timestamp.

        Returns:
            Float value or None when unavailable.
        """
        if self.df.empty or column not in self.df.columns:
            return None
        target_time = self.df.index.max() - pd.Timedelta(minutes=n_minutes)
        series = self.df[column].dropna().sort_index()
        if series.empty:
            return None
            
        eligible = series.loc[series.index <= target_time]
        if eligible.empty:
            deltas = np.abs(series.index - target_time)
            closest_position = int(np.argmin(deltas))
            if deltas[closest_position] <= pd.Timedelta(minutes=5):
                return float(series.iloc[closest_position])
            return None
        return float(eligible.iloc[-1])


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Convert values to float while preserving explicit missingness.

    Args:
        value: Input value.
        default: Value to return when conversion fails.

    Returns:
        Float or default.
    """
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isfinite(result):
        return result
    return default


def _snapshot_value(snapshot: Mapping[str, Any], section: str, key: str, default: Optional[float] = None) -> Optional[float]:
    """
    Read a numeric value from a nested snapshot section.

    Args:
        snapshot: Layer 1 snapshot dictionary.
        section: Top-level snapshot section.
        key: Field name inside the section.
        default: Default for missing or non-finite values.

    Returns:
        Float value or default.
    """
    nested = snapshot.get(section, {})
    if not isinstance(nested, Mapping):
        return default
    return _as_float(nested.get(key), default)


def _quality_from_snapshot(snapshot: Mapping[str, Any]) -> str:
    """
    Extract the Layer 1 data quality flag from a snapshot.

    Args:
        snapshot: Layer 1 snapshot.

    Returns:
        Uppercase quality flag.
    """
    value = snapshot.get("data_quality", "UNKNOWN")
    return str(value or "UNKNOWN").upper()


def _canonical_column(df: pd.DataFrame, canonical: str, aliases: Iterable[str]) -> None:
    """
    Ensure a canonical column exists by copying from the first present alias.

    Args:
        df: DataFrame to mutate.
        canonical: Desired column name.
        aliases: Alternative names.
    """
    if canonical in df.columns:
        return
    for alias in aliases:
        if alias in df.columns:
            df[canonical] = df[alias]
            return
    df[canonical] = np.nan


def prepare_history_dataframe(history: Union[pd.DataFrame, Sequence[Mapping[str, Any]], None]) -> pd.DataFrame:
    """
    Normalize raw DB rows into a UTC-indexed, numeric DataFrame.

    Args:
        history: DataFrame, sequence of row dicts, or None.

    Returns:
        DataFrame sorted ascending by UTC timestamp.
    """
    if history is None:
        return pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))
    df = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame(list(history))
    if df.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))

    if isinstance(df.index, pd.DatetimeIndex):
        index = pd.to_datetime(df.index, utc=True, errors="coerce")
    else:
        timestamp_col = "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"
        if timestamp_col not in df.columns:
            timestamp_col = "ingested_at" if "ingested_at" in df.columns else ""
        if timestamp_col:
            index = pd.to_datetime(df[timestamp_col], utc=True, errors="coerce")
        else:
            index = pd.DatetimeIndex([], tz="UTC")

    df = df.loc[pd.notna(index)].copy()
    if df.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))
    df.index = pd.DatetimeIndex(index[pd.notna(index)])
    df.index.name = "timestamp_utc"
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    _canonical_column(df, "proton_temp_kelvin", ["proton_temp_K", "proton_temp"])
    _canonical_column(df, "xray_flux_wm2", ["xray_flux_Wm2", "xray_flux"])
    _canonical_column(df, "data_quality_flag", ["quality_flag", "data_quality"])
    for col in [
        "bx_gsm",
        "by_gsm",
        "bz_gsm",
        "bt_total",
        "sw_speed_kmps",
        "proton_density_ccm",
        "proton_temp_kelvin",
        "kp_current",
        "xray_flux_wm2",
        "xray_severity_numeric",
        "dynamic_pressure_npa",
        "epsilon_coupling",
        "bz_southward_flag",
        "cme_earth_directed",
        "cme_speed_kmps",
        "cme_arrival_minutes",
    ]:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    missing_pressure = df["dynamic_pressure_npa"].isna()
    if missing_pressure.any():
        df.loc[missing_pressure, "dynamic_pressure_npa"] = compute_dynamic_pressure_npa(
            df.loc[missing_pressure, "proton_density_ccm"].fillna(FEATURE_IMPUTATION_VALUES["density"]).to_numpy(),
            df.loc[missing_pressure, "sw_speed_kmps"].fillna(FEATURE_IMPUTATION_VALUES["speed"]).to_numpy(),
        )

    missing_southward = df["bz_southward_flag"].isna()
    if missing_southward.any():
        df.loc[missing_southward, "bz_southward_flag"] = (
            df.loc[missing_southward, "bz_gsm"] < BZ_DANGER_THRESHOLD
        ).astype(float)

    return df


def append_snapshot_to_history(history_df: pd.DataFrame, snapshot: Mapping[str, Any]) -> pd.DataFrame:
    """
    Append the current snapshot as a final row for real-time calculations.

    Args:
        history_df: Historical readings.
        snapshot: Current Layer 1 snapshot.

    Returns:
        Normalized DataFrame including the latest snapshot row.
    """
    df = prepare_history_dataframe(history_df)
    solar = snapshot.get("solar_wind", {}) if isinstance(snapshot.get("solar_wind", {}), Mapping) else {}
    kp = snapshot.get("kp", {}) if isinstance(snapshot.get("kp", {}), Mapping) else {}
    xray = snapshot.get("xray", {}) if isinstance(snapshot.get("xray", {}), Mapping) else {}
    cme = snapshot.get("cme", {}) if isinstance(snapshot.get("cme", {}), Mapping) else {}
    computed = snapshot.get("computed", {}) if isinstance(snapshot.get("computed", {}), Mapping) else {}
    ts_value = solar.get("timestamp_utc") or snapshot.get("last_updated_utc") or _utcnow_iso()
    ts = pd.to_datetime(ts_value, utc=True, errors="coerce")
    if pd.isna(ts):
        ts = pd.Timestamp.utcnow()
    row = pd.DataFrame(
        [
            {
                "bx_gsm": solar.get("bx_gsm"),
                "by_gsm": solar.get("by_gsm"),
                "bz_gsm": solar.get("bz_gsm"),
                "bt_total": solar.get("bt_total"),
                "sw_speed_kmps": solar.get("sw_speed_kmps"),
                "proton_density_ccm": solar.get("proton_density_ccm"),
                "proton_temp_kelvin": solar.get("proton_temp_kelvin"),
                "kp_current": kp.get("kp_current"),
                "kp_status": kp.get("kp_status"),
                "xray_flux_wm2": xray.get("xray_flux_wm2"),
                "xray_severity_numeric": xray.get("xray_severity_numeric"),
                "cme_earth_directed": 1.0 if cme.get("earth_directed") else 0.0,
                "cme_speed_kmps": cme.get("cme_speed_kmps"),
                "cme_arrival_minutes": cme.get("arrival_minutes_from_now"),
                "epsilon_coupling": computed.get("epsilon_coupling"),
                "dynamic_pressure_npa": computed.get("dynamic_pressure_npa"),
                "data_quality_flag": snapshot.get("data_quality"),
                "bz_southward_flag": 1.0 if _as_float(solar.get("bz_gsm"), 0.0) < BZ_DANGER_THRESHOLD else 0.0,
            }
        ],
        index=pd.DatetimeIndex([ts], name="timestamp_utc"),
    )
    row = row.dropna(axis=1, how="all")
    combined = row if df.empty else pd.concat([df, row], axis=0, sort=False)
    return prepare_history_dataframe(combined)


def apply_group_imputation(value: Optional[float], group: str) -> float:
    """
    Apply feature-group imputation and finite-value enforcement.

    Args:
        value: Candidate value.
        group: Imputation group key.

    Returns:
        Finite float value.
    """
    default = FEATURE_IMPUTATION_VALUES.get(group, 0.0)
    if value is None:
        return float(default)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(result):
        return float(default)
    return result


def _count_fraction(count: Optional[float], denominator: float) -> float:
    """
    Convert a count to a bounded fraction.

    Args:
        count: Count value or None.
        denominator: Positive denominator.

    Returns:
        Fraction in [0, 1].
    """
    if count is None or denominator <= 0:
        return 0.0
    return float(max(0.0, min(1.0, count / denominator)))


def compute_bz_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 1 Bz statistics.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator over recent history.

    Returns:
        Dict of Bz feature names to values.
    """
    bz_current_raw = _snapshot_value(snapshot, "solar_wind", "bz_gsm")
    if bz_current_raw is None:
        return {
            "bz_current": 0.0,
            "bz_mean_30min": 0.0,
            "bz_mean_1hr": 0.0,
            "bz_mean_3hr": 0.0,
            "bz_min_30min": 0.0,
            "bz_min_1hr": 0.0,
            "bz_std_1hr": 0.0,
            "bz_southward_duration_30min": 0.0,
            "bz_rate_of_change_per_min": 0.0,
        }
    bz_current = apply_group_imputation(bz_current_raw, "bz")
    bz_5min_ago = rolling.value_n_minutes_ago("bz_gsm", 5)
    rate = 0.0 if bz_5min_ago is None else (bz_current - bz_5min_ago) / 5.0
    return {
        "bz_current": bz_current,
        "bz_mean_30min": apply_group_imputation(rolling.mean("bz_gsm", 30), "bz"),
        "bz_mean_1hr": apply_group_imputation(rolling.mean("bz_gsm", 60), "bz"),
        "bz_mean_3hr": apply_group_imputation(rolling.mean("bz_gsm", 180), "bz"),
        "bz_min_30min": apply_group_imputation(rolling.min("bz_gsm", 30), "bz"),
        "bz_min_1hr": apply_group_imputation(rolling.min("bz_gsm", 60), "bz"),
        "bz_std_1hr": apply_group_imputation(rolling.std("bz_gsm", 60), "bz"),
        "bz_southward_duration_30min": apply_group_imputation(
            rolling.count_below("bz_gsm", BZ_DANGER_THRESHOLD, 30),
            "bz",
        ),
        "bz_rate_of_change_per_min": apply_group_imputation(rate, "bz"),
    }


def compute_bt_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 2 Bt statistics.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator.

    Returns:
        Dict of Bt feature names to values.
    """
    bt_current = _snapshot_value(snapshot, "solar_wind", "bt_total", FEATURE_IMPUTATION_VALUES["bt"])
    bt_5min_ago = rolling.value_n_minutes_ago("bt_total", 5)
    rate = 0.0 if bt_5min_ago is None else (apply_group_imputation(bt_current, "bt") - bt_5min_ago) / 5.0
    return {
        "bt_mean_30min": apply_group_imputation(rolling.mean("bt_total", 30), "bt"),
        "bt_mean_1hr": apply_group_imputation(rolling.mean("bt_total", 60), "bt"),
        "bt_max_1hr": apply_group_imputation(rolling.max("bt_total", 60), "bt"),
        "bt_rate_of_change_per_min": apply_group_imputation(rate, "bt"),
    }


def compute_solar_wind_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 3 solar wind speed, density, and pressure features.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator.

    Returns:
        Dict of solar-wind feature names to values.
    """
    speed_current = _snapshot_value(snapshot, "solar_wind", "sw_speed_kmps", FEATURE_IMPUTATION_VALUES["speed"])
    speed_30min_ago = rolling.value_n_minutes_ago("sw_speed_kmps", 30)
    speed_rate = 0.0 if speed_30min_ago is None else (apply_group_imputation(speed_current, "speed") - speed_30min_ago) / 30.0
    return {
        "sw_speed_mean_1hr": apply_group_imputation(rolling.mean("sw_speed_kmps", 60), "speed"),
        "sw_speed_max_1hr": apply_group_imputation(rolling.max("sw_speed_kmps", 60), "speed"),
        "sw_speed_rate_of_change": apply_group_imputation(speed_rate, "speed"),
        "proton_density_mean_1hr": apply_group_imputation(rolling.mean("proton_density_ccm", 60), "density"),
        "dynamic_pressure_mean_1hr": apply_group_imputation(rolling.mean("dynamic_pressure_npa", 60), "density"),
        "dynamic_pressure_max_1hr": apply_group_imputation(rolling.max("dynamic_pressure_npa", 60), "density"),
    }


def _history_with_epsilon(history_df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a history DataFrame with per-reading scaled epsilon values.

    Args:
        history_df: Normalized history.

    Returns:
        DataFrame containing epsilon_scaled column.
    """
    df = prepare_history_dataframe(history_df)
    if df.empty:
        df["epsilon_scaled"] = pd.Series(dtype=float)
        return df
    df = df.copy()
    df["epsilon_scaled"] = EpsilonCalculator.compute_scaled(
        df["sw_speed_kmps"].to_numpy(dtype=np.float64),
        df["bt_total"].to_numpy(dtype=np.float64),
        df["by_gsm"].fillna(0.0).to_numpy(dtype=np.float64),
        df["bz_gsm"].to_numpy(dtype=np.float64),
    )
    return df


def compute_epsilon_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 4 Akasofu epsilon coupling features.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator.

    Returns:
        Dict of epsilon feature names to values.
    """
    bz = _snapshot_value(snapshot, "solar_wind", "bz_gsm")
    bt = _snapshot_value(snapshot, "solar_wind", "bt_total")
    speed = _snapshot_value(snapshot, "solar_wind", "sw_speed_kmps")
    by = _snapshot_value(snapshot, "solar_wind", "by_gsm", 0.0)
    epsilon_current = 0.0 if bz is None or bt is None or speed is None else float(EpsilonCalculator.compute_scaled(speed, bt, by or 0.0, bz))
    eps_df = _history_with_epsilon(rolling.df)
    eps_rolling = RollingWindowCalculator(eps_df)
    return {
        "epsilon_current": apply_group_imputation(epsilon_current, "epsilon"),
        "epsilon_mean_30min": apply_group_imputation(eps_rolling.mean("epsilon_scaled", 30), "epsilon"),
        "epsilon_mean_1hr": apply_group_imputation(eps_rolling.mean("epsilon_scaled", 60), "epsilon"),
        "epsilon_cumulative_3hr": apply_group_imputation(eps_rolling.cumsum("epsilon_scaled", 180), "epsilon"),
    }


def compute_southward_duration_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 5 southward Bz streak and fraction features.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator.

    Returns:
        Dict of southward-duration feature names to values.
    """
    bz_current = _snapshot_value(snapshot, "solar_wind", "bz_gsm")
    if bz_current is None:
        return {
            "consecutive_southward_minutes": 0.0,
            "southward_fraction_30min": 0.0,
            "southward_fraction_1hr": 0.0,
            "southward_fraction_3hr": 0.0,
            "bz_southward_onset_flag": 0.0,
        }
    count_30 = rolling.count_below("bz_gsm", BZ_DANGER_THRESHOLD, 30)
    count_60 = rolling.count_below("bz_gsm", BZ_DANGER_THRESHOLD, 60)
    count_180 = rolling.count_below("bz_gsm", BZ_DANGER_THRESHOLD, 180)
    bz_30min_ago = rolling.value_n_minutes_ago("bz_gsm", 30)
    onset = 1.0 if bz_current < BZ_DANGER_THRESHOLD and bz_30min_ago is not None and bz_30min_ago >= BZ_DANGER_THRESHOLD else 0.0
    return {
        "consecutive_southward_minutes": min(360.0, rolling.consecutive_below("bz_gsm", BZ_DANGER_THRESHOLD)),
        "southward_fraction_30min": _count_fraction(count_30, 30.0),
        "southward_fraction_1hr": _count_fraction(count_60, 60.0),
        "southward_fraction_3hr": _count_fraction(count_180, 180.0),
        "bz_southward_onset_flag": onset,
    }


def xray_peak_flux_log(max_flux: Optional[float]) -> float:
    """
    Convert X-ray peak flux to shifted log10 feature space.

    Args:
        max_flux: Peak flux in W/m^2.

    Returns:
        log10(max_flux) + 9, or 0.0 when missing.
    """
    flux = _as_float(max_flux)
    if flux is None or flux <= 0.0:
        return 0.0
    return float(math.log10(flux) + 9.0)


def compute_xray_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 6 X-ray flare features.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator.

    Returns:
        Dict of X-ray feature names to values.
    """
    severity_current = _snapshot_value(snapshot, "xray", "xray_severity_numeric", 1.0)
    max_flux = rolling.max("xray_flux_wm2", 1440)
    df = rolling.df
    time_since_m = 24.0
    if not df.empty and "xray_severity_numeric" in df.columns:
        recent_m = df.loc[df["xray_severity_numeric"] >= 4.0]
        if not recent_m.empty:
            delta = df.index.max() - recent_m.index.max()
            time_since_m = min(24.0, max(0.0, delta.total_seconds() / 3600.0))
    return {
        "xray_severity_current": apply_group_imputation(severity_current, "xray"),
        "xray_severity_max_6hr": apply_group_imputation(rolling.max("xray_severity_numeric", 360), "xray"),
        "time_since_last_M_class_hours": float(time_since_m),
        "xray_peak_flux_24hr": xray_peak_flux_log(max_flux),
    }


def compute_cme_features(snapshot: Mapping[str, Any]) -> Dict[str, float]:
    """
    Compute Group 7 CME features from the snapshot.

    Args:
        snapshot: Layer 1 snapshot.

    Returns:
        Dict of CME feature names to values.
    """
    cme = snapshot.get("cme", {}) if isinstance(snapshot.get("cme", {}), Mapping) else {}
    earth_directed = bool(cme.get("earth_directed", False))
    arrival_minutes = _as_float(cme.get("arrival_minutes_from_now"))
    speed = _as_float(cme.get("cme_speed_kmps"))
    if not earth_directed:
        return {
            "cme_earth_directed": 0.0,
            "cme_speed_normalized": 0.0,
            "cme_arrival_hours": FEATURE_IMPUTATION_VALUES["cme_arrival"],
            "cme_is_imminent": 0.0,
        }
    speed_norm = 0.0 if speed is None else max(0.0, min(1.0, speed / 3000.0))
    arrival_hours = FEATURE_IMPUTATION_VALUES["cme_arrival"] if arrival_minutes is None else max(0.0, min(48.0, arrival_minutes / 60.0))
    return {
        "cme_earth_directed": 1.0,
        "cme_speed_normalized": float(speed_norm),
        "cme_arrival_hours": float(arrival_hours),
        "cme_is_imminent": 1.0 if arrival_minutes is not None and arrival_minutes < 360.0 else 0.0,
    }


def compute_kp_features(snapshot: Mapping[str, Any], rolling: RollingWindowCalculator) -> Dict[str, float]:
    """
    Compute Group 8 Kp history features.

    Args:
        snapshot: Layer 1 snapshot.
        rolling: Rolling-window calculator.

    Returns:
        Dict of Kp feature names to values.
    """
    kp_current = _snapshot_value(snapshot, "kp", "kp_current", 0.0)
    kp_6hr_ago = rolling.value_n_minutes_ago("kp_current", 360)
    kp_rate = 0.0 if kp_6hr_ago is None else (apply_group_imputation(kp_current, "kp") - kp_6hr_ago) / 6.0
    return {
        "kp_current": apply_group_imputation(kp_current, "bz"),
        "kp_mean_6hr": apply_group_imputation(rolling.mean("kp_current", 360), "kp"),
        "kp_mean_12hr": apply_group_imputation(rolling.mean("kp_current", 720), "kp"),
        "kp_max_24hr": apply_group_imputation(rolling.max("kp_current", 1440), "kp"),
        "kp_rate_of_change": apply_group_imputation(kp_rate, "bz"),
    }


def compute_interaction_features(snapshot: Mapping[str, Any], feature_values: Mapping[str, float]) -> Dict[str, float]:
    """
    Compute Group 9 interaction and IMF clock-angle features.

    Args:
        snapshot: Layer 1 snapshot.
        feature_values: Previously computed feature values.

    Returns:
        Dict of interaction feature names to values.
    """
    bz_min_1hr = feature_values.get("bz_min_1hr")
    speed_mean_1hr = feature_values.get("sw_speed_mean_1hr")
    bt_mean_1hr = feature_values.get("bt_mean_1hr")
    by = _snapshot_value(snapshot, "solar_wind", "by_gsm")
    bz = _snapshot_value(snapshot, "solar_wind", "bz_gsm")
    if by is None or bz is None:
        clock_sin = 0.0
        clock_cos = 1.0
    else:
        theta = math.atan2(by, bz)
        clock_sin = math.sin(theta)
        clock_cos = math.cos(theta)
    bz_interaction = 0.0
    if bz_min_1hr is not None and speed_mean_1hr is not None:
        bz_interaction = abs(float(bz_min_1hr)) * float(speed_mean_1hr) / 1000.0
    bt_interaction = 0.0
    if bt_mean_1hr is not None and speed_mean_1hr is not None:
        bt_interaction = float(bt_mean_1hr) * float(speed_mean_1hr) / 1000.0
    return {
        "bz_speed_interaction": apply_group_imputation(bz_interaction, "bz"),
        "bt_speed_interaction": apply_group_imputation(bt_interaction, "bt"),
        "imf_clock_angle_sin": float(clock_sin),
        "imf_clock_angle_cos": float(clock_cos),
    }


def compute_feature_dict(snapshot: Mapping[str, Any], history_df: pd.DataFrame) -> Dict[str, float]:
    """
    Compute all feature groups as a name-value dictionary.

    Args:
        snapshot: Layer 1 snapshot.
        history_df: Historical readings.

    Returns:
        Dict containing all 45 feature values.
    """
    prepared = append_snapshot_to_history(history_df, snapshot)
    rolling = RollingWindowCalculator(prepared)
    values: Dict[str, float] = {}
    values.update(compute_bz_features(snapshot, rolling))
    values.update(compute_bt_features(snapshot, rolling))
    values.update(compute_solar_wind_features(snapshot, rolling))
    values.update(compute_epsilon_features(snapshot, rolling))
    values.update(compute_southward_duration_features(snapshot, rolling))
    values.update(compute_xray_features(snapshot, rolling))
    values.update(compute_cme_features(snapshot))
    values.update(compute_kp_features(snapshot, rolling))
    values.update(compute_interaction_features(snapshot, values))
    return values


def compute_xgb_vector(snapshot: Mapping[str, Any], history_df: Union[pd.DataFrame, Sequence[Mapping[str, Any]], None]) -> np.ndarray:
    """
    Build the 45-element raw tabular feature vector in immutable order.

    Args:
        snapshot: Layer 1 snapshot.
        history_df: Recent historical DB readings.

    Returns:
        Float64 numpy array of shape (45,) with no NaN values.
    """
    try:
        values = compute_feature_dict(snapshot, prepare_history_dataframe(history_df))
        vector = np.array([values.get(name, 0.0) for name in FEATURE_NAMES], dtype=np.float64)
        if not np.isfinite(vector).all() or vector.shape != (N_XGB_FEATURES,):
            raise ValueError("non-finite or incorrectly shaped XGB vector")
        return vector
    except Exception as exc:
        logger.critical("XGB feature vector construction failed: %s", exc, exc_info=True)
        return np.zeros(N_XGB_FEATURES, dtype=np.float64)


def _empty_sequence_row() -> Dict[str, float]:
    """
    Return conservative imputation values for one hourly sequence row.

    Returns:
        Dict of sequence feature names to default values.
    """
    return {
        "bz_gsm": FEATURE_IMPUTATION_VALUES["bz"],
        "bt_total": FEATURE_IMPUTATION_VALUES["bt"],
        "sw_speed_kmps": FEATURE_IMPUTATION_VALUES["speed"],
        "proton_density_ccm": FEATURE_IMPUTATION_VALUES["density"],
        "epsilon_scaled": FEATURE_IMPUTATION_VALUES["epsilon"],
        "dynamic_pressure_npa": float(compute_dynamic_pressure_npa(FEATURE_IMPUTATION_VALUES["density"], FEATURE_IMPUTATION_VALUES["speed"])),
        "bz_southward_flag": 0.0,
        "xray_severity": 1.0,
        "kp_current": FEATURE_IMPUTATION_VALUES["kp"],
        "bz_rate_1hr": 0.0,
        "bt_rate_1hr": 0.0,
        "sw_speed_rate_1hr": 0.0,
        "imf_clock_sin": 0.0,
        "imf_clock_cos": 1.0,
        "southward_fraction_1hr": 0.0,
    }


def compute_lstm_sequence(history_df: Union[pd.DataFrame, Sequence[Mapping[str, Any]], None]) -> np.ndarray:
    """
    Build the raw LSTM sequence tensor with shape (1, 24, 15).

    Args:
        history_df: Last 24 hours of 1-minute DB readings.

    Returns:
        Float32 numpy array of shape (1, 24, 15).
    """
    df = prepare_history_dataframe(history_df)
    if df.empty:
        row = np.array([_empty_sequence_row()[name] for name in SEQUENCE_FEATURE_NAMES], dtype=np.float32)
        return np.tile(row, (SEQUENCE_LENGTH, 1)).reshape(1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES).astype(np.float32)

    cutoff = df.index.max() - pd.Timedelta(hours=24)
    df = df.loc[df.index >= cutoff].copy()
    if df.empty:
        row = np.array([_empty_sequence_row()[name] for name in SEQUENCE_FEATURE_NAMES], dtype=np.float32)
        return np.tile(row, (SEQUENCE_LENGTH, 1)).reshape(1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES).astype(np.float32)

    aggregations: Dict[str, Union[str, Callable[[pd.Series], float]]] = {
        "bz_gsm": "mean",
        "bt_total": "mean",
        "sw_speed_kmps": "mean",
        "proton_density_ccm": "mean",
        "dynamic_pressure_npa": "mean",
        "bz_southward_flag": "sum",
        "xray_severity_numeric": "max",
        "kp_current": "mean",
        "by_gsm": "mean",
    }
    hourly = df.resample("1h").agg(aggregations)
    full_index = pd.date_range(
        start=hourly.index.min(),
        end=hourly.index.max(),
        freq="1h",
        tz="UTC",
    )
    hourly = hourly.reindex(full_index)
    hourly = hourly.ffill(limit=3)

    defaults = _empty_sequence_row()
    fill_map = {
        "bz_gsm": defaults["bz_gsm"],
        "bt_total": defaults["bt_total"],
        "sw_speed_kmps": defaults["sw_speed_kmps"],
        "proton_density_ccm": defaults["proton_density_ccm"],
        "dynamic_pressure_npa": defaults["dynamic_pressure_npa"],
        "bz_southward_flag": 0.0,
        "xray_severity_numeric": defaults["xray_severity"],
        "kp_current": defaults["kp_current"],
        "by_gsm": 0.0,
    }
    hourly = hourly.fillna(fill_map)
    hourly["epsilon_scaled"] = EpsilonCalculator.compute_scaled(
        hourly["sw_speed_kmps"].to_numpy(dtype=np.float64),
        hourly["bt_total"].to_numpy(dtype=np.float64),
        hourly["by_gsm"].to_numpy(dtype=np.float64),
        hourly["bz_gsm"].to_numpy(dtype=np.float64),
    )
    hourly["bz_rate_1hr"] = hourly["bz_gsm"].diff().fillna(0.0)
    hourly["bt_rate_1hr"] = hourly["bt_total"].diff().fillna(0.0)
    hourly["sw_speed_rate_1hr"] = hourly["sw_speed_kmps"].diff().fillna(0.0)
    theta = np.arctan2(hourly["by_gsm"].to_numpy(dtype=np.float64), hourly["bz_gsm"].to_numpy(dtype=np.float64))
    hourly["imf_clock_sin"] = np.sin(theta)
    hourly["imf_clock_cos"] = np.cos(theta)
    hourly["southward_fraction_1hr"] = np.clip(hourly["bz_southward_flag"].to_numpy(dtype=np.float64) / 60.0, 0.0, 1.0)
    hourly["xray_severity"] = hourly["xray_severity_numeric"]

    sequence_df = hourly[SEQUENCE_FEATURE_NAMES].replace([np.inf, -np.inf], np.nan)
    if sequence_df.isna().any().any():
        sequence_df = sequence_df.fillna(defaults)
    sequence = sequence_df.to_numpy(dtype=np.float32)
    if sequence.shape[0] < SEQUENCE_LENGTH:
        pad_rows = np.repeat(sequence[[0], :], SEQUENCE_LENGTH - sequence.shape[0], axis=0)
        sequence = np.vstack([pad_rows, sequence])
    if sequence.shape[0] > SEQUENCE_LENGTH:
        sequence = sequence[-SEQUENCE_LENGTH:, :]
    sequence = np.nan_to_num(sequence, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    return sequence.reshape(1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES)


def _repo_backend_root() -> Path:
    """
    Resolve the backend directory path.

    Returns:
        Absolute path to backend/.
    """
    return Path(__file__).resolve().parents[2]


def _resolve_backend_path(relative_path: str) -> Path:
    """
    Resolve a backend-relative path.

    Args:
        relative_path: Path relative to backend.

    Returns:
        Absolute Path.
    """
    return _repo_backend_root() / relative_path


def load_scalers(
    xgb_path: Optional[Union[str, Path]] = None,
    lstm_path: Optional[Union[str, Path]] = None,
) -> None:
    """
    Load fitted MinMaxScalers into module-level singletons.

    Args:
        xgb_path: Optional explicit path to xgb_scaler.pkl.
        lstm_path: Optional explicit path to lstm_scaler.pkl.

    Raises:
        RuntimeError: If either scaler file is missing or cannot be loaded.
    """
    global _scaler_xgb, _scaler_lstm, _scalers_loaded
    resolved_xgb = Path(xgb_path) if xgb_path is not None else _resolve_backend_path(SCALER_XGB_PATH)
    resolved_lstm = Path(lstm_path) if lstm_path is not None else _resolve_backend_path(SCALER_LSTM_PATH)
    missing = [str(path) for path in (resolved_xgb, resolved_lstm) if not path.exists()]
    if missing:
        message = f"Layer 2 scaler file(s) missing: {', '.join(missing)}"
        if xgb_path is not None or lstm_path is not None:
            _scalers_loaded = False
            logger.critical(message)
            raise RuntimeError(message)
        with _scaler_lock:
            _scaler_xgb = _IdentityScaler()
            _scaler_lstm = _IdentityScaler()
            _scalers_loaded = True
        logger.info("%s; using raw feature passthrough scalers for runtime inference", message)
        return

    try:
        import joblib
    except Exception as exc:
        logger.critical("joblib is required to load Layer 2 scalers: %s", exc)
        raise RuntimeError("Layer 2 scalers cannot load because joblib is unavailable") from exc
    with _scaler_lock:
        _scaler_xgb = joblib.load(resolved_xgb)
        _scaler_lstm = joblib.load(resolved_lstm)
        _scalers_loaded = True
    logger.info("Layer 2 scalers loaded: xgb=%s lstm=%s", resolved_xgb, resolved_lstm)


def scalers_loaded() -> bool:
    """
    Return whether both real-time scalers have been loaded.

    Returns:
        True if loaded, else False.
    """
    with _scaler_lock:
        return bool(_scalers_loaded and _scaler_xgb is not None and _scaler_lstm is not None)


def scale_xgb_vector(raw_vector: np.ndarray) -> np.ndarray:
    """
    Apply the fitted XGB MinMaxScaler.

    Args:
        raw_vector: Raw 45-element vector.

    Returns:
        Scaled 45-element vector clipped to [0, 1].

    Raises:
        RuntimeError: If scalers are not loaded.
    """
    with _scaler_lock:
        if not scalers_loaded():
            raise RuntimeError("Layer 2 XGB scaler has not been loaded")
        scaled = _scaler_xgb.transform(raw_vector.reshape(1, -1))[0]
        if isinstance(_scaler_xgb, _IdentityScaler):
            return np.asarray(scaled, dtype=np.float64)
    return np.clip(np.asarray(scaled, dtype=np.float64), 0.0, 1.0)


def scale_lstm_sequence(raw_sequence: np.ndarray) -> np.ndarray:
    """
    Apply the fitted LSTM MinMaxScaler to a raw 24 x 15 sequence.

    Args:
        raw_sequence: Raw sequence of shape (1, 24, 15) or (24, 15).

    Returns:
        Scaled sequence of shape (1, 24, 15), clipped to [0, 1].

    Raises:
        RuntimeError: If scalers are not loaded.
    """
    sequence_2d = raw_sequence.reshape(SEQUENCE_LENGTH, N_SEQUENCE_FEATURES)
    with _scaler_lock:
        if not scalers_loaded():
            raise RuntimeError("Layer 2 LSTM scaler has not been loaded")
        scaled_2d = _scaler_lstm.transform(sequence_2d)
        if isinstance(_scaler_lstm, _IdentityScaler):
            return np.asarray(scaled_2d, dtype=np.float32).reshape(1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES)
    scaled = np.clip(np.asarray(scaled_2d, dtype=np.float32), 0.0, 1.0)
    return scaled.reshape(1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES)


def _risk_for_bz(value: float) -> str:
    """
    Classify Bz risk.

    Args:
        value: Bz value in nT.

    Returns:
        Risk label.
    """
    if value >= 0:
        return "SAFE"
    if value >= -5:
        return "WATCH"
    if value >= -10:
        return "MODERATE"
    if value >= -20:
        return "HIGH"
    return "CRITICAL"


def _risk_for_kp(value: float) -> str:
    """
    Classify Kp risk.

    Args:
        value: Kp value.

    Returns:
        Risk label.
    """
    if value < 4:
        return "QUIET"
    if value < 5:
        return "ACTIVE"
    if value < 7:
        return "STORM"
    if value < 9:
        return "SEVERE"
    return "EXTREME"


def _risk_for_epsilon(value: float) -> str:
    """
    Classify scaled epsilon risk.

    Args:
        value: Epsilon scaled by 1e10.

    Returns:
        Risk label.
    """
    if value < 1:
        return "LOW"
    if value < 5:
        return "MODERATE"
    if value <= 20:
        return "HIGH"
    return "EXTREME"


def _risk_for_feature(name: str, value: float) -> str:
    """
    Classify risk interpretation for one feature.

    Args:
        name: Feature name.
        value: Feature value.

    Returns:
        Risk label.
    """
    if name.startswith("bz_") or name in {"consecutive_southward_minutes", "southward_fraction_30min", "southward_fraction_1hr", "southward_fraction_3hr"}:
        if "fraction" in name:
            return "HIGH" if value >= 0.75 else "MODERATE" if value >= 0.4 else "LOW"
        if "duration" in name or "consecutive" in name:
            return "HIGH" if value >= 30 else "MODERATE" if value >= 10 else "LOW"
        return _risk_for_bz(value)
    if name.startswith("kp_"):
        return _risk_for_kp(value)
    if name.startswith("epsilon_"):
        return _risk_for_epsilon(value)
    if name.startswith("cme_"):
        return "HIGH" if value > 0 and name in {"cme_earth_directed", "cme_is_imminent"} else "INFO"
    if name.startswith("xray_"):
        return "HIGH" if value >= 4 else "MODERATE" if value >= 3 else "LOW"
    if "dynamic_pressure" in name:
        return "HIGH" if value >= 10 else "MODERATE" if value >= 4 else "LOW"
    return "INFO"


def _threshold_breached(name: str, value: float) -> bool:
    """
    Determine whether a feature has crossed an operational threshold.

    Args:
        name: Feature name.
        value: Feature value.

    Returns:
        True when the feature is operationally notable.
    """
    if name.startswith("bz_") and "std" not in name and "rate" not in name:
        return value < BZ_DANGER_THRESHOLD
    if name.startswith("epsilon_"):
        return value >= 5.0
    if name.startswith("kp_"):
        return value >= 5.0
    if name in {"cme_earth_directed", "cme_is_imminent", "bz_southward_onset_flag"}:
        return value >= 1.0
    if name.startswith("xray_"):
        return value >= 4.0
    if "dynamic_pressure" in name:
        return value >= 10.0
    return False


def _recommended_urgency(values: Mapping[str, float]) -> str:
    """
    Compute Layer 3 urgency from feature values.

    Args:
        values: Feature values keyed by name.

    Returns:
        ROUTINE, ELEVATED, IMMEDIATE, or CRITICAL.
    """
    kp_current = values.get("kp_current", 0.0)
    bz_mean_1hr = values.get("bz_mean_1hr", 0.0)
    bz_min_1hr = values.get("bz_min_1hr", 0.0)
    storm_imminent = values.get("cme_is_imminent", 0.0) >= 1.0
    if kp_current >= 8.0 or bz_min_1hr < -25.0:
        return "CRITICAL"
    if kp_current >= 7.0 or bz_min_1hr < -15.0 or storm_imminent:
        return "IMMEDIATE"
    if 5.0 <= kp_current <= 6.0 or bz_mean_1hr < -5.0:
        return "ELEVATED"
    return "ROUTINE"


def build_feature_metadata(
    raw_vector: np.ndarray,
    scaled_vector: Optional[np.ndarray] = None,
    data_quality: str = "UNKNOWN",
    stale_data: bool = False,
    computed_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the human-readable feature metadata dictionary for SHAP displays.

    Args:
        raw_vector: Raw 45-feature vector.
        scaled_vector: Optional scaled 45-feature vector.
        data_quality: Layer 1 quality flag.
        stale_data: Whether the source data is stale.
        computed_at_utc: Optional ISO timestamp.

    Returns:
        Metadata dictionary containing all feature explanations and summary.
    """
    raw = np.asarray(raw_vector, dtype=np.float64).reshape(N_XGB_FEATURES)
    scaled = np.zeros_like(raw) if scaled_vector is None else np.asarray(scaled_vector, dtype=np.float64).reshape(N_XGB_FEATURES)
    values = {name: float(raw[idx]) for idx, name in enumerate(FEATURE_NAMES)}
    features = []
    for idx, name in enumerate(FEATURE_NAMES):
        value = float(raw[idx])
        features.append(
            {
                "index": idx,
                "name": name,
                "value": value,
                "scaled_value": float(scaled[idx]),
                "unit": FEATURE_UNITS.get(name, "unitless"),
                "display_name": DISPLAY_NAMES.get(name, name.replace("_", " ").title()),
                "physical_meaning": PHYSICAL_MEANINGS.get(name, "Physics-informed space-weather predictor"),
                "risk_interpretation": _risk_for_feature(name, value),
                "threshold_breached": _threshold_breached(name, value),
            }
        )
    epsilon_level = _risk_for_epsilon(values.get("epsilon_current", 0.0))
    summary = {
        "bz_danger": values.get("bz_current", 0.0) < BZ_DANGER_THRESHOLD or values.get("bz_mean_1hr", 0.0) < BZ_DANGER_THRESHOLD,
        "cme_active": values.get("cme_earth_directed", 0.0) >= 1.0,
        "storm_onset_detected": values.get("bz_southward_onset_flag", 0.0) >= 1.0,
        "epsilon_level": epsilon_level,
        "recommended_layer3_urgency": _recommended_urgency(values),
    }
    return {
        "computed_at_utc": computed_at_utc or _utcnow_iso(),
        "data_quality": data_quality,
        "stale_data": bool(stale_data),
        "features": features,
        "summary": summary,
    }


LATEST_FEATURES["feature_metadata"] = _default_metadata()


def _json_safe(value: Any) -> Any:
    """
    Convert numpy-heavy structures to JSON-safe Python structures.

    Args:
        value: Arbitrary value.

    Returns:
        JSON-safe equivalent.
    """
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def get_latest_features(json_safe: bool = False) -> Dict[str, Any]:
    """
    Thread-safe read of the latest feature result.

    Args:
        json_safe: Convert numpy arrays to lists when True.

    Returns:
        Copy of LATEST_FEATURES. This function never raises.
    """
    try:
        with _latest_lock:
            result = copy.deepcopy(LATEST_FEATURES)
        return _json_safe(result) if json_safe else result
    except Exception as exc:
        logger.error("get_latest_features fallback after unexpected error: %s", exc, exc_info=True)
        fallback = {
            "xgb_vector_scaled": np.zeros(N_XGB_FEATURES, dtype=np.float64),
            "lstm_sequence_scaled": np.zeros((1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES), dtype=np.float32),
            "xgb_vector_raw": np.zeros(N_XGB_FEATURES, dtype=np.float64),
            "lstm_sequence_raw": np.zeros((1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES), dtype=np.float32),
            "feature_metadata": _default_metadata(),
            "data_quality": "UNKNOWN",
            "stale_data": False,
            "computed_at_utc": _utcnow_iso(),
        }
        return _json_safe(fallback) if json_safe else fallback


def update_latest_features(feature_result: Mapping[str, Any]) -> None:
    """
    Thread-safe update of the latest feature singleton.

    Args:
        feature_result: Full feature result from compute_features_realtime().
    """
    with _latest_lock:
        LATEST_FEATURES.clear()
        LATEST_FEATURES.update(copy.deepcopy(dict(feature_result)))


def load_recent_history(hours: int = 24) -> pd.DataFrame:
    """
    Load recent Layer 1 history with a single database read.

    Args:
        hours: Lookback in hours.

    Returns:
        Normalized DataFrame sorted ascending.
    """
    try:
        from app.database.db import get_solar_wind_history
        records = get_solar_wind_history(hours=hours, quality_filter=["GOOD", "PARTIAL"], limit=max(1440, hours * 60 + 60))
        return prepare_history_dataframe(records)
    except Exception as exc:
        logger.error("Failed to load solar wind history for feature engineering: %s", exc, exc_info=True)
        return prepare_history_dataframe(None)


def compute_features_realtime(
    snapshot: Optional[Mapping[str, Any]] = None,
    history_df: Union[pd.DataFrame, Sequence[Mapping[str, Any]], None] = None,
) -> Optional[Dict[str, Any]]:
    """
    Orchestrate real-time Layer 2 feature generation.

    Args:
        snapshot: Optional supplied snapshot for tests or manual invocation.
        history_df: Optional supplied recent history; if omitted the DB is read once.

    Returns:
        Feature result dictionary, or None if construction fails completely.
    """
    start = time.perf_counter()
    try:
        if snapshot is None:
            from app.services.ingestion_service import get_snapshot
            snapshot = get_snapshot()
        history = load_recent_history(hours=24) if history_df is None else prepare_history_dataframe(history_df)
        data_quality = _quality_from_snapshot(snapshot)
        stale_data = data_quality == "STALE"
        computed_at = _utcnow_iso()
        raw_vector = compute_xgb_vector(snapshot, history)
        raw_sequence = compute_lstm_sequence(append_snapshot_to_history(history, snapshot))

        # Models are trained on raw features directly; pass raw values to scaled slots
        xgb_scaled = raw_vector.copy()
        lstm_scaled = raw_sequence.copy().reshape(1, SEQUENCE_LENGTH, N_SEQUENCE_FEATURES)

        metadata = build_feature_metadata(raw_vector, xgb_scaled, data_quality, stale_data, computed_at)
        result = {
            "xgb_vector_scaled": xgb_scaled,
            "lstm_sequence_scaled": lstm_scaled.astype(np.float32),
            "xgb_vector_raw": raw_vector,
            "lstm_sequence_raw": raw_sequence.astype(np.float32),
            "feature_metadata": metadata,
            "data_quality": data_quality,
            "stale_data": stale_data,
            "computed_at_utc": computed_at,
        }
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        log_fn = logger.warning if elapsed_ms > 200.0 else logger.info
        log_fn(
            "Feature engineering completed in %.1fms | quality=%s | Bz=%.2f | Eps=%.2f",
            elapsed_ms,
            data_quality,
            float(raw_vector[0]),
            float(raw_vector[19]),
        )
        return result
    except Exception as exc:
        logger.critical("compute_features_realtime failed: %s", exc, exc_info=True)
        return None


def compute_feature_history(hours: int = 6) -> List[Dict[str, Any]]:
    """
    Compute feature vectors across recent history for visualization.

    Args:
        hours: Number of hours to include, capped by the route.

    Returns:
        List of JSON-safe feature rows ordered by time.
    """
    history = load_recent_history(hours=hours)
    if history.empty:
        return []
    rows: List[Dict[str, Any]] = []
    for timestamp, row in history.iterrows():
        snapshot = row_to_snapshot(row, timestamp)
        window = history.loc[history.index <= timestamp]
        vector = compute_xgb_vector(snapshot, window)
        payload = {name: float(vector[idx]) for idx, name in enumerate(FEATURE_NAMES)}
        payload["timestamp_utc"] = timestamp.isoformat().replace("+00:00", "Z")
        payload["data_quality"] = str(row.get("data_quality_flag", "UNKNOWN"))
        rows.append(payload)
    return rows


def row_to_snapshot(row: Union[pd.Series, Mapping[str, Any]], timestamp: Optional[pd.Timestamp] = None) -> Dict[str, Any]:
    """
    Convert a historical DB row into a snapshot-shaped dictionary.

    Args:
        row: Historical row.
        timestamp: Optional timestamp for the row.

    Returns:
        Snapshot-like dictionary suitable for compute_xgb_vector().
    """
    data = dict(row)
    ts = timestamp or pd.to_datetime(data.get("timestamp_utc") or data.get("timestamp"), utc=True, errors="coerce")
    ts_str = _utcnow_iso() if pd.isna(ts) else pd.Timestamp(ts).isoformat().replace("+00:00", "Z")
    return {
        "last_updated_utc": ts_str,
        "data_age_seconds": 0,
        "data_quality": str(data.get("data_quality_flag") or data.get("quality_flag") or "UNKNOWN"),
        "solar_wind": {
            "timestamp_utc": ts_str,
            "bx_gsm": data.get("bx_gsm"),
            "by_gsm": data.get("by_gsm"),
            "bz_gsm": data.get("bz_gsm"),
            "bt_total": data.get("bt_total"),
            "sw_speed_kmps": data.get("sw_speed_kmps"),
            "proton_density_ccm": data.get("proton_density_ccm"),
            "proton_temp_kelvin": data.get("proton_temp_kelvin"),
        },
        "kp": {
            "kp_current": data.get("kp_current"),
            "kp_status": data.get("kp_status"),
        },
        "xray": {
            "xray_flux_wm2": data.get("xray_flux_wm2"),
            "xray_severity_numeric": data.get("xray_severity_numeric"),
        },
        "cme": {
            "earth_directed": bool(data.get("cme_earth_directed") or False),
            "cme_speed_kmps": data.get("cme_speed_kmps"),
            "arrival_minutes_from_now": data.get("cme_arrival_minutes"),
        },
        "computed": {
            "epsilon_coupling": data.get("epsilon_coupling"),
            "dynamic_pressure_npa": data.get("dynamic_pressure_npa"),
        },
    }


__all__ = [
    "FEATURE_NAMES",
    "SEQUENCE_FEATURE_NAMES",
    "LATEST_FEATURES",
    "EpsilonCalculator",
    "RollingWindowCalculator",
    "append_snapshot_to_history",
    "apply_group_imputation",
    "build_feature_metadata",
    "compute_dynamic_pressure_npa",
    "compute_feature_history",
    "compute_features_realtime",
    "compute_lstm_sequence",
    "compute_xgb_vector",
    "get_latest_features",
    "load_recent_history",
    "load_scalers",
    "prepare_history_dataframe",
    "row_to_snapshot",
    "scale_lstm_sequence",
    "scale_xgb_vector",
    "scalers_loaded",
    "update_latest_features",
    "xray_peak_flux_log",
]
