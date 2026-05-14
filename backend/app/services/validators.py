# backend/app/services/validators.py
"""
NAKSHATRA-KAVACH — Layer 1: Validation and Computed Fields
Validates raw API records against physical ranges, handles nulls,
detects staleness, and computes derived quantities.
"""

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.utils.constants import (
    BZ_STORM_DEVELOPMENT,
    BZ_SEVERE_STORM,
    DEFAULT_SW_SPEED_KMPS,
    INTERPOLATION_MAX_GAP_SECONDS,
    INTERPOLATION_LOOKBACK_RECORDS,
    L1_TO_EARTH_KM,
    QUALITY_GOOD,
    QUALITY_PARTIAL,
    QUALITY_STALE,
    QUALITY_UNKNOWN,
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MODERATE,
    RISK_UNKNOWN,
    VALID_RANGES,
    XRAY_CLASS_NUMERIC,
    DATA_AGE_STALE_SECONDS,
)
from app.utils.formatters import data_age_seconds, parse_utc_timestamp, utcnow_iso
from app.database.db import get_recent_field_values

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# X-RAY CLASSIFICATION
# ─────────────────────────────────────────────────────────────────

def classify_xray(flux: Optional[float]) -> str:
    """
    Convert an X-ray flux value (W/m²) to NOAA flare class string.
    Handles None, zero, and extreme values without raising exceptions.

    Args:
        flux: X-ray flux in W/m² (can be None).

    Returns:
        Flare class string like "M1.5", "X2.3", "B3.0", "A0.0", "UNKNOWN".
    """
    if flux is None:
        return "UNKNOWN"
    try:
        flux = float(flux)
    except (TypeError, ValueError):
        return "UNKNOWN"

    if flux <= 0:
        return "A0.0"

    if flux >= 1e-3:
        return "X" + f"{flux / 1e-4:.1f}"
    elif flux >= 1e-4:
        return "X" + f"{flux / 1e-4:.1f}"
    elif flux >= 1e-5:
        return "M" + f"{flux / 1e-5:.1f}"
    elif flux >= 1e-6:
        return "C" + f"{flux / 1e-6:.1f}"
    elif flux >= 1e-7:
        return "B" + f"{flux / 1e-7:.1f}"
    else:
        return "A" + f"{flux / 1e-8:.1f}"


def xray_severity_numeric(xray_class: str) -> int:
    """
    Convert flare class string to integer severity (1–5).

    Args:
        xray_class: String like "M1.5", "X2.3". First character is the class letter.

    Returns:
        Integer 1–5 (A=1, B=2, C=3, M=4, X=5). Returns 1 on unknown.
    """
    if not xray_class or xray_class == "UNKNOWN":
        return 1
    letter = xray_class[0].upper()
    return XRAY_CLASS_NUMERIC.get(letter, 1)


# ─────────────────────────────────────────────────────────────────
# COMPUTED PHYSICAL QUANTITIES
# ─────────────────────────────────────────────────────────────────

def compute_transit_warning_minutes(sw_speed_kmps: Optional[float]) -> float:
    """
    Calculate solar wind transit time from DSCOVR (L1) to Earth's magnetosphere.

    Formula: transit_minutes = 1,500,000 km / speed (km/s) / 60

    Args:
        sw_speed_kmps: Solar wind speed in km/s. If None, uses default.

    Returns:
        Transit time in minutes (float). Default 60.0 if speed unavailable.
    """
    if sw_speed_kmps and sw_speed_kmps > 0:
        return L1_TO_EARTH_KM / sw_speed_kmps / 60.0
    return 60.0  # conservative default


def compute_epsilon_coupling(
    sw_speed_kmps: Optional[float],
    bt_total: Optional[float],
    by_gsm: Optional[float],
    bz_gsm: Optional[float],
) -> Optional[float]:
    """
    Compute the Akasofu epsilon coupling function — best single predictor of
    global geomagnetic activity (energy input from solar wind to magnetosphere).

    Formula: epsilon = v * Bt² * sin⁴(θ/2) × 1e10  (in Gigawatts range)
    where θ = atan2(By, Bz)

    Args:
        sw_speed_kmps: Solar wind speed (km/s).
        bt_total:      Total magnetic field magnitude (nT).
        by_gsm:        Y-component of IMF (nT).
        bz_gsm:        Z-component of IMF (nT).

    Returns:
        Epsilon value (float) or None if inputs are unavailable.
    """
    if sw_speed_kmps is None or bt_total is None:
        return None
    if by_gsm is None or bz_gsm is None:
        return None
    try:
        theta = math.atan2(by_gsm, bz_gsm)
        epsilon = sw_speed_kmps * (bt_total ** 2) * (math.sin(theta / 2) ** 4) * 1e10
        return round(epsilon, 4)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def compute_dynamic_pressure(
    proton_density_ccm: Optional[float],
    sw_speed_kmps: Optional[float],
) -> Optional[float]:
    """
    Compute solar wind dynamic pressure in nanopascals.

    Formula: Pdyn = 1.6726e-27 * 1e6 * n * (v * 1000)² × 1e9

    Args:
        proton_density_ccm: Proton density in cm⁻³.
        sw_speed_kmps:      Solar wind speed in km/s.

    Returns:
        Dynamic pressure in nPa, or None if inputs unavailable.
    """
    if proton_density_ccm is None or sw_speed_kmps is None:
        return None
    try:
        dp = 1.6726e-27 * 1e6 * proton_density_ccm * ((sw_speed_kmps * 1000.0) ** 2)
        return round(dp * 1e9, 6)  # convert to nanopascals
    except (OverflowError, ZeroDivisionError):
        return None


def compute_storm_onset_risk(bz_gsm: Optional[float]) -> str:
    """
    Classify geomagnetic storm onset risk based on Bz component.

    Args:
        bz_gsm: IMF Bz value in nT. Negative = southward = dangerous.

    Returns:
        Risk level string: "LOW", "MODERATE", "HIGH", "CRITICAL", or "UNKNOWN".
    """
    if bz_gsm is None:
        return RISK_UNKNOWN
    if bz_gsm >= -5.0:
        return RISK_LOW
    if bz_gsm >= -10.0:
        return RISK_MODERATE
    if bz_gsm >= -20.0:
        return RISK_HIGH
    return RISK_CRITICAL


def compute_data_quality(
    bz_gsm: Optional[float],
    sw_speed_kmps: Optional[float],
    timestamp_utc: Optional[str],
) -> str:
    """
    Determine the data quality flag for a solar wind reading.

    Rules:
        GOOD    → bz and speed both present AND data age < 5 min
        PARTIAL → some fields missing but core fields present
        STALE   → timestamp older than 10 minutes

    Args:
        bz_gsm:        IMF Bz (nT).
        sw_speed_kmps: Solar wind speed (km/s).
        timestamp_utc: ISO 8601 UTC source timestamp.

    Returns:
        Quality flag string.
    """
    age = data_age_seconds(timestamp_utc)
    if age > DATA_AGE_STALE_SECONDS:
        return QUALITY_STALE
    if bz_gsm is not None and sw_speed_kmps is not None:
        return QUALITY_GOOD
    if bz_gsm is None:
        return QUALITY_PARTIAL
    return QUALITY_PARTIAL


# ─────────────────────────────────────────────────────────────────
# INTERPOLATION
# ─────────────────────────────────────────────────────────────────

def _try_interpolate_field(field: str) -> Tuple[Optional[float], bool]:
    """
    Attempt linear interpolation for a null field using recent DB values.
    Returns the interpolated value and a flag indicating whether interpolation occurred.

    Args:
        field: Column name to interpolate.

    Returns:
        Tuple of (interpolated_value_or_None, was_interpolated).
    """
    recent = get_recent_field_values(field, limit=INTERPOLATION_LOOKBACK_RECORDS)
    if not recent:
        logger.warning("Data gap — no recent values to interpolate for %s", field)
        return None, False

    # Use the last known value as best estimate when gap < 15 min
    last_valid = recent[0]
    logger.debug("Interpolated %s from last valid value: %s", field, last_valid)
    return last_valid, True


# ─────────────────────────────────────────────────────────────────
# SOLAR WIND RECORD VALIDATION
# ─────────────────────────────────────────────────────────────────

def validate_solar_wind(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize a raw solar wind record from NOAA SWPC.
    Applies range checks, null handling, staleness detection, and interpolation.

    Args:
        raw: Raw dict from NOAA SWPC API (last item in the response array).

    Returns:
        Cleaned and normalized dict ready for snapshot update and DB insert.
        Always returns a dict — never raises.
    """
    out: Dict[str, Any] = {}
    interpolated = False

    # ── Timestamp ──
    ts_str = raw.get("time_tag") or raw.get("time_tag_id") or ""
    dt = parse_utc_timestamp(ts_str)
    out["timestamp_utc"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else utcnow_iso()

    age = data_age_seconds(out["timestamp_utc"])
    if age > DATA_AGE_STALE_SECONDS:
        logger.warning(
            "STALE data from NOAA: age=%ds timestamp=%s", int(age), out["timestamp_utc"]
        )

    # ── Field mapping from NOAA schema to internal schema ──
    field_map = {
        "bx_gsm":            ("bx_gsm",      "bz_gsm",       (-100.0, 100.0)),
        "by_gsm":            ("by_gsm",       None,           (-100.0, 100.0)),
        "bz_gsm":            ("bz_gsm",       None,           (-100.0, 100.0)),
        "bt_total":          ("bt",           None,           (0.0, 100.0)),
        "sw_speed_kmps":     ("speed",        None,           (200.0, 3000.0)),
        "proton_density_ccm":("density",      None,           (0.0, 100.0)),
        "proton_temp_kelvin":("temperature",  None,           (1000.0, 10_000_000.0)),
        "kp_estimated_from_sw": ("range_kp", None,           (0.0, 9.0)),
    }

    for out_key, (raw_key, _, valid_range) in field_map.items():
        raw_val = raw.get(raw_key)

        # Range check
        if raw_val is not None:
            try:
                raw_val = float(raw_val)
                lo, hi = valid_range
                if not (lo <= raw_val <= hi):
                    logger.warning(
                        "Range violation: %s=%s out of [%s, %s] — setting null",
                        out_key, raw_val, lo, hi,
                    )
                    raw_val = None
            except (TypeError, ValueError):
                raw_val = None

        # Interpolate if null and gap < threshold
        if raw_val is None:
            interp_val, did_interp = _try_interpolate_field(out_key)
            if did_interp:
                raw_val = interp_val
                interpolated = True

        out[out_key] = raw_val

    # ── Critical field warnings ──
    if out.get("bz_gsm") is None:
        logger.warning(
            "BZ_NULL | source=DSCOVR | age=%ds | last_valid_bz=<interpolation failed>",
            int(age),
        )

    if out.get("sw_speed_kmps") is None:
        logger.warning("sw_speed_kmps is null — using default for transit calc only")

    # ── Source flags ──
    out["source_dscovr_active"] = 1 if raw.get("active", True) else 0

    # ── Computed fields ──
    out["transit_warning_minutes"] = compute_transit_warning_minutes(out.get("sw_speed_kmps"))
    out["epsilon_coupling"] = compute_epsilon_coupling(
        out.get("sw_speed_kmps"), out.get("bt_total"),
        out.get("by_gsm"), out.get("bz_gsm"),
    )
    out["dynamic_pressure_npa"] = compute_dynamic_pressure(
        out.get("proton_density_ccm"), out.get("sw_speed_kmps")
    )
    out["bz_southward_flag"] = (
        1 if (out.get("bz_gsm") is not None and out["bz_gsm"] < BZ_STORM_DEVELOPMENT)
        else 0
    )
    out["storm_onset_risk"] = compute_storm_onset_risk(out.get("bz_gsm"))
    out["data_quality_flag"] = compute_data_quality(
        out.get("bz_gsm"), out.get("sw_speed_kmps"), out["timestamp_utc"]
    )
    out["interpolated"] = 1 if interpolated else 0
    out["ingested_at"] = utcnow_iso()

    # ── Kp / alert fields (filled by separate pollers) ──
    out.setdefault("kp_current", None)
    out.setdefault("kp_status", None)
    out.setdefault("xray_flux_wm2", None)
    out.setdefault("xray_class", None)
    out.setdefault("xray_severity_numeric", None)
    out.setdefault("cme_earth_directed", 0)
    out.setdefault("cme_speed_kmps", None)
    out.setdefault("cme_arrival_minutes", None)
    out.setdefault("cme_arrival_time_utc", None)
    out.setdefault("official_alert_class", None)

    return out


# ─────────────────────────────────────────────────────────────────
# CME RECORD VALIDATION
# ─────────────────────────────────────────────────────────────────

def validate_cme_record(raw_cme: Dict[str, Any]) -> bool:
    """
    Validate a single CME analysis record from NASA DONKI.

    Checks:
        - speed > 0 and speed < 5000 km/s
        - arrival time is in the future
        - isEarthDirected is a boolean

    Args:
        raw_cme: One CME analysis dict from DONKI response.

    Returns:
        True if the record passes all checks, False otherwise.
    """
    from app.utils.constants import CME_MAX_SPEED_KMPS
    from app.utils.formatters import parse_utc_timestamp

    speed = raw_cme.get("speed")
    if speed is None or not isinstance(speed, (int, float)):
        return False
    if not (0 < speed < CME_MAX_SPEED_KMPS):
        logger.warning("CME speed out of range: %s km/s", speed)
        return False

    # Check enlilList for Earth-directed arrival time
    enl_list = raw_cme.get("enlilList") or []
    for enl in enl_list:
        if enl.get("isEarthDirected") is not True:
            continue
        arr_str = enl.get("estimatedShockArrivalTime")
        if arr_str:
            arr_dt = parse_utc_timestamp(arr_str)
            if arr_dt and arr_dt < datetime.utcnow():
                logger.debug("CME arrival already past — skipping: %s", arr_str)
                return False

    return True


# ─────────────────────────────────────────────────────────────────
# STORM CLASS FROM Kp
# ─────────────────────────────────────────────────────────────────

def kp_to_storm_class(kp: Optional[float]) -> str:
    """
    Convert a Kp value to NOAA storm class string.

    Args:
        kp: Kp index value (0–9 scale).

    Returns:
        Storm class: "G1"–"G5" or "QUIET".
    """
    if kp is None:
        return "QUIET"
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
