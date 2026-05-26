# backend/app/services/ingestion_service.py
"""
NAKSHATRA-KAVACH — Layer 1: Ingestion Service (Snapshot + Orchestration)
Maintains the in-memory LATEST_SNAPSHOT singleton.
Orchestrates fetch → validate → snapshot update → DB persist pipeline.
This is the contract surface for all downstream layers (Layer 2+).
"""

import copy
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.database.db import (
    insert_cme_event,
    insert_dst_reading,
    insert_noaa_alert,
    insert_scintillation_reading,
    insert_sep_reading,
    insert_solar_wind_reading,
    timestamp_exists_in_sw,
)
from app.services.fetchers import fetch_alerts, fetch_cme, fetch_dst, fetch_kp, fetch_solar_wind, fetch_sep_proton_flux, fetch_xray
from app.services.validators import (
    classify_xray,
    compute_dynamic_pressure,
    compute_epsilon_coupling,
    compute_storm_onset_risk,
    compute_transit_warning_minutes,
    kp_to_storm_class,
    validate_cme_record,
    validate_solar_wind,
    xray_severity_numeric,
)
from app.utils.constants import (
    ACT_NOW_BZ_THRESHOLD,
    ACT_NOW_KP_THRESHOLD,
    ACTION_ACT_NOW,
    ACTION_MONITOR,
    ACTION_PREPARE,
    ACTION_WATCH,
    ALERT_CODE_TO_STORM_CLASS,
    DATA_AGE_STALE_SECONDS,
    QUALITY_STALE,
    QUALITY_UNKNOWN,
    RISK_HIGH,
    RISK_LOW,
    RISK_MODERATE,
    STORM_IMMINENT_BZ_THRESHOLD,
    STORM_IMMINENT_CME_ARRIVAL_MINUTES,
    STORM_IMMINENT_KP_THRESHOLD,
    STORM_IMMINENT_SPEED_THRESHOLD,
)
from app.utils.formatters import data_age_seconds, parse_utc_timestamp, utcnow_iso, minutes_until

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> Optional[float]:
    """Return a numeric value when upstream APIs send numbers as strings."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

# ─────────────────────────────────────────────────────────────────
# THREAD-SAFE LATEST SNAPSHOT
# ─────────────────────────────────────────────────────────────────

_snapshot_lock = threading.RLock()
_SNAPSHOT_BACKUP: Optional[Dict[str, Any]] = None

LATEST_SNAPSHOT: Dict[str, Any] = {
    "last_updated_utc": None,
    "data_age_seconds": 9999,
    "data_quality": QUALITY_UNKNOWN,
    "solar_wind": {
        "timestamp_utc": None,
        "bx_gsm": None,
        "by_gsm": None,
        "bz_gsm": None,
        "bt_total": None,
        "sw_speed_kmps": None,
        "proton_density_ccm": None,
        "proton_temp_kelvin": None,
        "bz_southward_flag": 0,
        "storm_onset_risk": QUALITY_UNKNOWN,
        "source_dscovr_active": 0,
    },
    "kp": {
        "kp_current": None,
        "kp_index": None,
        "kp_status": None,
        "storm_class": "QUIET",
        "kp_timestamp_utc": None,
    },
    "xray": {
        "xray_flux_wm2": None,
        "xray_class": "UNKNOWN",
        "xray_severity_numeric": 1,
        "xray_timestamp_utc": None,
    },
    "cme": {
        "earth_directed": False,
        "cme_speed_kmps": None,
        "arrival_time_utc": None,
        "arrival_minutes_from_now": None,
        "duration_hours": None,
        "active_cme_count": 0,
    },
    "alert": {
        "latest_official_class": None,
        "latest_alert_code": None,
        "alert_issued_utc": None,
        "active_watch": False,
    },
    "computed": {
        "transit_warning_minutes": 60.0,
        "epsilon_coupling": None,
        "dynamic_pressure_npa": None,
        "storm_imminent": False,
        "recommended_action_level": ACTION_MONITOR,
    },
    "dst": {
        "dst_nt": None,
        "dst_classification": "QUIET",
        "dst_timestamp_utc": None,
    },
    "sep": {
        "proton_flux_gt10mev": None,
        "proton_flux_gt100mev": None,
        "sep_alert_active": False,
        "sep_class": None,
        "sep_timestamp_utc": None,
    },
    "scintillation": {
        "s4_index": None,
        "scintillation_class": "NONE",
        "positioning_error_m": None,
        "navic_status": "NOMINAL",
        "diurnal_phase": None,
        "eia_active": False,
        "clock_error_ns": None,
    },
}

# Track previous storm class for change-detection events
_previous_storm_class: str = "QUIET"


def get_snapshot() -> Dict[str, Any]:
    """
    Thread-safe read of the full LATEST_SNAPSHOT.
    Always returns within ~1ms. Returns a shallow copy.

    Returns:
        Copy of the current LATEST_SNAPSHOT dict.
    """
    with _snapshot_lock:
        return dict(LATEST_SNAPSHOT)


def _temporarily_set_snapshot(snapshot: Dict[str, Any]) -> None:
    """
    Atomically swap LATEST_SNAPSHOT for a replay snapshot.

    The replay engine must preserve live data exactly while historical frames
    pass through Layers 2-6. A deep copy is required because the snapshot has
    nested dictionaries and downstream code may mutate nested sections.
    """
    global LATEST_SNAPSHOT, _SNAPSHOT_BACKUP
    with _snapshot_lock:
        if _SNAPSHOT_BACKUP is None:
            _SNAPSHOT_BACKUP = copy.deepcopy(LATEST_SNAPSHOT)
        LATEST_SNAPSHOT = copy.deepcopy(snapshot)


def _restore_snapshot() -> None:
    """
    Restore the pre-replay LATEST_SNAPSHOT atomically.

    This function is intentionally idempotent so callers can use it freely in
    finally blocks even if frame processing failed before the swap completed.
    """
    global LATEST_SNAPSHOT, _SNAPSHOT_BACKUP
    with _snapshot_lock:
        if _SNAPSHOT_BACKUP is not None:
            LATEST_SNAPSHOT = copy.deepcopy(_SNAPSHOT_BACKUP)
            _SNAPSHOT_BACKUP = None


# ─────────────────────────────────────────────────────────────────
# COMPUTED SNAPSHOT FIELDS
# ─────────────────────────────────────────────────────────────────

def _compute_storm_imminent() -> bool:
    """
    Determine whether a storm is imminent based on multi-parameter check.

    Returns:
        True if any storm-imminent condition is met.
    """
    with _snapshot_lock:
        bz = _as_float(LATEST_SNAPSHOT["solar_wind"].get("bz_gsm"))
        speed = _as_float(LATEST_SNAPSHOT["solar_wind"].get("sw_speed_kmps"))
        kp = _as_float(LATEST_SNAPSHOT["kp"].get("kp_current"))
        cme_directed = LATEST_SNAPSHOT["cme"].get("earth_directed", False)
        cme_minutes = LATEST_SNAPSHOT["cme"].get("arrival_minutes_from_now")
        alert_class = LATEST_SNAPSHOT["alert"].get("latest_official_class")

    if bz is not None and speed is not None:
        if bz < STORM_IMMINENT_BZ_THRESHOLD and speed > STORM_IMMINENT_SPEED_THRESHOLD:
            return True
    if cme_directed and cme_minutes is not None:
        if cme_minutes < STORM_IMMINENT_CME_ARRIVAL_MINUTES:
            return True
    if kp is not None and kp >= STORM_IMMINENT_KP_THRESHOLD:
        return True
    if alert_class in ("G3", "G4", "G5"):
        return True
    return False


def _compute_recommended_action() -> str:
    """
    Determine the recommended action level for operators.

    Returns:
        Action level string: MONITOR / WATCH / PREPARE / ACT_NOW.
    """
    with _snapshot_lock:
        risk = LATEST_SNAPSHOT["solar_wind"].get("storm_onset_risk", QUALITY_UNKNOWN)
        kp = _as_float(LATEST_SNAPSHOT["kp"].get("kp_current"))
        bz = _as_float(LATEST_SNAPSHOT["solar_wind"].get("bz_gsm"))
        storm_imminent = LATEST_SNAPSHOT["computed"].get("storm_imminent", False)

    if kp is not None and kp >= ACT_NOW_KP_THRESHOLD:
        return ACTION_ACT_NOW
    if bz is not None and bz < ACT_NOW_BZ_THRESHOLD:
        return ACTION_ACT_NOW
    if storm_imminent or risk == RISK_HIGH:
        return ACTION_PREPARE
    if risk == RISK_MODERATE:
        return ACTION_WATCH
    return ACTION_MONITOR


# ─────────────────────────────────────────────────────────────────
# SNAPSHOT UPDATE FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def update_snapshot_solar_wind(record: Dict[str, Any]) -> None:
    """
    Atomically update the solar_wind and computed sections of LATEST_SNAPSHOT.

    Args:
        record: Validated solar wind dict from validate_solar_wind().
    """
    now_iso = utcnow_iso()
    with _snapshot_lock:
        LATEST_SNAPSHOT["solar_wind"].update({
            "timestamp_utc":        record.get("timestamp_utc"),
            "bx_gsm":               record.get("bx_gsm"),
            "by_gsm":               record.get("by_gsm"),
            "bz_gsm":               record.get("bz_gsm"),
            "bt_total":             record.get("bt_total"),
            "sw_speed_kmps":        record.get("sw_speed_kmps"),
            "proton_density_ccm":   record.get("proton_density_ccm"),
            "proton_temp_kelvin":   record.get("proton_temp_kelvin"),
            "bz_southward_flag":    record.get("bz_southward_flag", 0),
            "storm_onset_risk":     record.get("storm_onset_risk", QUALITY_UNKNOWN),
            "source_dscovr_active": record.get("source_dscovr_active", 0),
            "data_source":           record.get("source"),
            "wind_time_tag":         record.get("wind_time_tag"),
            "mag_time_tag":          record.get("mag_time_tag"),
        })
        LATEST_SNAPSHOT["last_updated_utc"] = now_iso
        LATEST_SNAPSHOT["data_quality"] = record.get("data_quality_flag", QUALITY_UNKNOWN)
        LATEST_SNAPSHOT["data_age_seconds"] = data_age_seconds(record.get("timestamp_utc"))

        # Update computed fields
        LATEST_SNAPSHOT["computed"]["transit_warning_minutes"] = record.get(
            "transit_warning_minutes", 60.0
        )
        LATEST_SNAPSHOT["computed"]["epsilon_coupling"] = record.get("epsilon_coupling")
        LATEST_SNAPSHOT["computed"]["dynamic_pressure_npa"] = record.get("dynamic_pressure_npa")

    # Recompute storm imminent + action (uses lock internally)
    storm_imminent = _compute_storm_imminent()
    action = _compute_recommended_action()
    with _snapshot_lock:
        LATEST_SNAPSHOT["computed"]["storm_imminent"] = storm_imminent
        LATEST_SNAPSHOT["computed"]["recommended_action_level"] = action

    logger.info(
        "SOLAR_WIND_UPDATE | bz=%s | speed=%s | kp=%s | quality=%s",
        record.get("bz_gsm"),
        record.get("sw_speed_kmps"),
        LATEST_SNAPSHOT["kp"].get("kp_current"),
        record.get("data_quality_flag"),
    )


def update_snapshot_kp(raw_kp: Dict[str, Any]) -> None:
    """
    Update the kp section of LATEST_SNAPSHOT from a raw NOAA Kp record.

    Args:
        raw_kp: Dict from NOAA Kp endpoint (last array item).
    """
    kp_val = _as_float(raw_kp.get("kp"))
    kp_index = raw_kp.get("kp_index")
    kp_status = raw_kp.get("status", "UNKNOWN")
    ts = raw_kp.get("time_tag")
    storm_class = kp_to_storm_class(kp_val)

    with _snapshot_lock:
        LATEST_SNAPSHOT["kp"].update({
            "kp_current":      kp_val,
            "kp_index":        kp_index,
            "kp_status":       kp_status,
            "storm_class":     storm_class,
            "kp_timestamp_utc": ts,
        })

    _check_and_emit_storm_class_change(storm_class, kp_val)


def update_snapshot_xray(raw_xray: Dict[str, Any]) -> None:
    """
    Update the xray section of LATEST_SNAPSHOT.

    Args:
        raw_xray: Dict from GOES X-ray endpoint (filtered 0.1-0.8nm entry).
    """
    flux = raw_xray.get("flux")
    xray_cls = classify_xray(flux)
    severity = xray_severity_numeric(xray_cls)
    ts = raw_xray.get("time_tag")

    with _snapshot_lock:
        LATEST_SNAPSHOT["xray"].update({
            "xray_flux_wm2":        flux,
            "xray_class":           xray_cls,
            "xray_severity_numeric": severity,
            "xray_timestamp_utc":   ts,
        })


def update_snapshot_cme(cme_list: List[Dict[str, Any]]) -> None:
    """
    Update the cme section of LATEST_SNAPSHOT from a filtered DONKI CME list.
    Selects the Earth-directed CME with the soonest arrival time.

    Args:
        cme_list: List of filtered CME dicts from fetch_cme().
    """
    earth_directed_cmes = []

    for cme in cme_list:
        if not validate_cme_record(cme):
            continue
        enl_list = cme.get("enlilList") or []
        for enl in enl_list:
            if enl.get("isEarthDirected") is True:
                arr_str = enl.get("estimatedShockArrivalTime")
                arr_dt = parse_utc_timestamp(arr_str)
                if arr_dt and arr_dt > datetime.utcnow():
                    earth_directed_cmes.append({
                        "speed_kmps":        cme.get("speed"),
                        "arrival_dt":        arr_dt,
                        "arrival_time_utc":  arr_str,
                        "duration_hours":    enl.get("estimatedDuration"),
                        "launch_time_utc":   cme.get("time21_5"),
                        "half_angle_deg":    cme.get("halfAngle"),
                        "latitude_deg":      cme.get("latitude"),
                        "longitude_deg":     cme.get("longitude"),
                        "catalog_source":    cme.get("catalog"),
                        "donki_link":        cme.get("link"),
                    })

    if not earth_directed_cmes:
        with _snapshot_lock:
            LATEST_SNAPSHOT["cme"].update({
                "earth_directed":           False,
                "cme_speed_kmps":           None,
                "arrival_time_utc":         None,
                "arrival_minutes_from_now": None,
                "duration_hours":           None,
                "active_cme_count":         0,
            })
        return

    # Use soonest-arriving CME
    earth_directed_cmes.sort(key=lambda x: x["arrival_dt"])
    soonest = earth_directed_cmes[0]
    arrival_minutes = minutes_until(soonest["arrival_time_utc"])

    with _snapshot_lock:
        LATEST_SNAPSHOT["cme"].update({
            "earth_directed":           True,
            "cme_speed_kmps":           soonest["speed_kmps"],
            "arrival_time_utc":         soonest["arrival_time_utc"],
            "arrival_minutes_from_now": arrival_minutes,
            "duration_hours":           soonest["duration_hours"],
            "active_cme_count":         len(earth_directed_cmes),
        })

    logger.info(
        "CME_DETECTED | speed=%s | arrival_min=%s | earth_directed=True",
        soonest["speed_kmps"], arrival_minutes,
    )

    # Persist all Earth-directed CMEs to DB
    now_iso = utcnow_iso()
    for c in earth_directed_cmes:
        arr_minutes = minutes_until(c["arrival_time_utc"])
        insert_cme_event({
            "detected_at_utc":               now_iso,
            "cme_launch_time_utc":           c.get("launch_time_utc"),
            "speed_kmps":                    c.get("speed_kmps"),
            "half_angle_deg":                c.get("half_angle_deg"),
            "latitude_deg":                  c.get("latitude_deg"),
            "longitude_deg":                 c.get("longitude_deg"),
            "is_earth_directed":             1,
            "estimated_arrival_utc":         c.get("arrival_time_utc"),
            "estimated_duration_hr":         c.get("duration_hours"),
            "arrival_minutes_from_detection": arr_minutes,
            "catalog_source":                c.get("catalog_source"),
            "donki_link":                    c.get("donki_link"),
            "active":                        1,
        })


def update_snapshot_alert(alerts: List[Dict[str, Any]]) -> None:
    """
    Parse the NOAA alerts list and update the alert section of LATEST_SNAPSHOT.
    Persists new alerts to the noaa_alerts DB table.

    Args:
        alerts: List of alert dicts from fetch_alerts().
    """
    latest_class = None
    latest_code = None
    latest_ts = None
    active_watch = False

    for alert in reversed(alerts):  # newest first
        message = alert.get("message", "")
        product_id = alert.get("product_id", "")
        issue_dt = alert.get("issue_datetime", "")

        # Detect storm class from message
        detected_class = None
        detected_code = None
        for code, cls in ALERT_CODE_TO_STORM_CLASS.items():
            if code in message:
                detected_code = code
                detected_class = cls
                if code == "WATA20":
                    active_watch = True
                break

        if detected_class and latest_class is None:
            latest_class = detected_class
            latest_code = detected_code
            latest_ts = issue_dt

        # Persist to DB (INSERT OR IGNORE handles duplicates)
        insert_noaa_alert({
            "product_id":        product_id,
            "issue_datetime_utc": issue_dt,
            "alert_code":        detected_code,
            "storm_class":       detected_class,
            "full_message":      message,
        })

    with _snapshot_lock:
        LATEST_SNAPSHOT["alert"].update({
            "latest_official_class": latest_class,
            "latest_alert_code":     latest_code,
            "alert_issued_utc":      latest_ts,
            "active_watch":          active_watch,
        })


# ─────────────────────────────────────────────────────────────────
# STORM CLASS CHANGE DETECTION
# ─────────────────────────────────────────────────────────────────

def _check_and_emit_storm_class_change(new_class: str, kp: Optional[float]) -> None:
    """
    Detect when storm class changes and emit a WebSocket storm_alert event.

    Args:
        new_class: Newly computed storm class string.
        kp:        Current Kp value (for payload).
    """
    global _previous_storm_class

    if new_class == _previous_storm_class:
        return

    logger.info(
        "STORM_CLASS_CHANGE | %s → %s | kp=%s",
        _previous_storm_class, new_class, kp,
    )

    # Emit via socketio — imported lazily to avoid circular import
    try:
        from app import socketio  # noqa: PLC0415
        socketio.emit(
            "storm_alert",
            {
                "new_class":      new_class,
                "previous_class": _previous_storm_class,
                "kp":             kp,
                "timestamp":      utcnow_iso(),
            },
        )
    except Exception as exc:
        logger.warning("Failed to emit storm_alert socket event: %s", exc)

    _previous_storm_class = new_class


# ─────────────────────────────────────────────────────────────────
# DATABASE PERSIST
# ─────────────────────────────────────────────────────────────────

def save_to_database(record: Dict[str, Any]) -> bool:
    """
    Persist a validated solar wind record to solar_wind_readings table.
    Checks for duplicates before inserting.

    Args:
        record: Fully validated and enriched solar wind record.

    Returns:
        True if inserted, False if duplicate or error.
    """
    ts = record.get("timestamp_utc")
    if not ts:
        logger.warning("save_to_database: record has no timestamp_utc — skipping")
        return False

    if timestamp_exists_in_sw(ts):
        logger.debug("Duplicate solar wind record skipped: %s", ts)
        return False

    # Merge current kp/xray/cme/alert values from snapshot into DB record
    with _snapshot_lock:
        record["kp_current"]          = LATEST_SNAPSHOT["kp"].get("kp_current")
        record["kp_status"]           = LATEST_SNAPSHOT["kp"].get("kp_status")
        record["xray_flux_wm2"]       = LATEST_SNAPSHOT["xray"].get("xray_flux_wm2")
        record["xray_class"]          = LATEST_SNAPSHOT["xray"].get("xray_class")
        record["xray_severity_numeric"] = LATEST_SNAPSHOT["xray"].get("xray_severity_numeric")
        record["cme_earth_directed"]  = 1 if LATEST_SNAPSHOT["cme"].get("earth_directed") else 0
        record["cme_speed_kmps"]      = LATEST_SNAPSHOT["cme"].get("cme_speed_kmps")
        record["cme_arrival_minutes"] = LATEST_SNAPSHOT["cme"].get("arrival_minutes_from_now")
        record["cme_arrival_time_utc"] = LATEST_SNAPSHOT["cme"].get("arrival_time_utc")
        record["official_alert_class"] = LATEST_SNAPSHOT["alert"].get("latest_official_class")

    row_id = insert_solar_wind_reading(record)
    return row_id is not None


# ─────────────────────────────────────────────────────────────────
# FULL PIPELINE ORCHESTRATION FUNCTIONS (called by scheduler jobs)
# ─────────────────────────────────────────────────────────────────

def run_solar_wind_and_kp_poll() -> None:
    """
    Job 1: Fetch solar wind + Kp, validate, update snapshot, persist to DB.
    Called every 60 seconds by APScheduler.
    Never raises — all errors are caught and logged.
    """
    try:
        raw_sw = fetch_solar_wind()
        if raw_sw:
            record = validate_solar_wind(raw_sw)
            update_snapshot_solar_wind(record)
            save_to_database(record)
        else:
            logger.warning("Solar wind fetch returned None — keeping cached data")
            with _snapshot_lock:
                ts = LATEST_SNAPSHOT["solar_wind"].get("timestamp_utc")
                age = data_age_seconds(ts)
                LATEST_SNAPSHOT["data_age_seconds"] = age
                if age > DATA_AGE_STALE_SECONDS:
                    LATEST_SNAPSHOT["data_quality"] = QUALITY_STALE
    except Exception as exc:
        logger.error("Unhandled error in run_solar_wind_and_kp_poll: %s", exc, exc_info=True)

    try:
        raw_kp = fetch_kp()
        if raw_kp:
            update_snapshot_kp(raw_kp)
    except Exception as exc:
        logger.error("Unhandled error in Kp poll: %s", exc, exc_info=True)


def run_xray_and_alerts_poll() -> None:
    """
    Job 2: Fetch X-ray flux + NOAA alerts, update snapshot.
    Called every 5 minutes by APScheduler.
    """
    try:
        raw_xray = fetch_xray()
        if raw_xray:
            update_snapshot_xray(raw_xray)
    except Exception as exc:
        logger.error("Unhandled error in X-ray poll: %s", exc, exc_info=True)

    try:
        alerts = fetch_alerts()
        if alerts:
            update_snapshot_alert(alerts)
    except Exception as exc:
        logger.error("Unhandled error in alerts poll: %s", exc, exc_info=True)


def run_cme_poll() -> None:
    """
    Job 3: Fetch and process NASA DONKI CME catalog.
    Called every 30 minutes by APScheduler.
    """
    try:
        cme_list = fetch_cme()
        if cme_list is not None:
            update_snapshot_cme(cme_list)
        else:
            logger.warning("CME fetch returned None — keeping cached CME data")
    except Exception as exc:
        logger.error("Unhandled error in CME poll: %s", exc, exc_info=True)


def run_dst_and_sep_poll() -> None:
    """
    Job 4 (Enhancement): Fetch Dst index + SEP proton flux and update snapshot.
    Also recomputes NavIC ionospheric scintillation from latest physics models.
    Called every 5 minutes by APScheduler.
    Never raises — all errors are caught and logged.
    """
    from app.services.physics import compute_navic_scintillation
    from app.utils.formatters import utcnow_iso
    from datetime import datetime, timezone

    # ── Dst index ──
    try:
        dst_record = fetch_dst()
        if dst_record:
            with _snapshot_lock:
                LATEST_SNAPSHOT["dst"].update({
                    "dst_nt":             dst_record.get("dst_nt"),
                    "dst_classification": dst_record.get("dst_classification", "QUIET"),
                    "dst_timestamp_utc":  dst_record.get("timestamp_utc"),
                })
            insert_dst_reading({
                **dst_record,
                "ingested_at": utcnow_iso(),
            })
            logger.debug("DST_UPDATE | dst=%s | class=%s",
                         dst_record.get("dst_nt"), dst_record.get("dst_classification"))
        else:
            logger.debug("DST fetch returned None — keeping cached Dst data")
    except Exception as exc:
        logger.error("Unhandled error in DST poll: %s", exc, exc_info=True)

    # ── SEP proton flux ──
    try:
        sep_record = fetch_sep_proton_flux()
        if sep_record:
            with _snapshot_lock:
                LATEST_SNAPSHOT["sep"].update({
                    "proton_flux_gt10mev":  sep_record.get("proton_flux_gt10mev"),
                    "proton_flux_gt100mev": sep_record.get("proton_flux_gt100mev"),
                    "sep_alert_active":     sep_record.get("sep_alert_active", False),
                    "sep_class":            sep_record.get("sep_class"),
                    "sep_timestamp_utc":    sep_record.get("timestamp_utc"),
                })
            insert_sep_reading({
                **sep_record,
                "ingested_at": utcnow_iso(),
            })
            if sep_record.get("sep_alert_active"):
                logger.warning("SEP_ALERT | class=%s | flux_gt10=%s pfu",
                               sep_record.get("sep_class"),
                               sep_record.get("proton_flux_gt10mev"))
        else:
            logger.debug("SEP fetch returned None — keeping cached SEP data")
    except Exception as exc:
        logger.error("Unhandled error in SEP poll: %s", exc, exc_info=True)

    # ── NavIC scintillation (recomputed every poll cycle) ──
    try:
        with _snapshot_lock:
            kp = LATEST_SNAPSHOT["kp"].get("kp_current") or 0.0
            xray_sev = LATEST_SNAPSHOT["xray"].get("xray_severity_numeric") or 1

        scint = compute_navic_scintillation(
            kp_peak=float(kp),
            current_time_utc=datetime.now(timezone.utc),
            xray_severity=int(xray_sev),
            magnetic_lat_deg=15.0,  # India's mean magnetic latitude
        )

        with _snapshot_lock:
            LATEST_SNAPSHOT["scintillation"].update({
                "s4_index":          scint["s4_index"],
                "scintillation_class": scint["scintillation_class"],
                "positioning_error_m": scint["positioning_error_m"],
                "navic_status":       scint["navic_status"],
                "diurnal_phase":      scint["diurnal_phase"],
                "eia_active":         scint["eia_active"],
                "clock_error_ns":     scint["clock_error_ns"],
            })

        insert_scintillation_reading({
            "timestamp_utc":     utcnow_iso(),
            "kp_used":           float(kp),
            "xray_severity":     int(xray_sev),
            "s4_index":          scint["s4_index"],
            "scintillation_class": scint["scintillation_class"],
            "positioning_error_m": scint["positioning_error_m"],
            "navic_status":       scint["navic_status"],
            "diurnal_phase":      scint["diurnal_phase"],
            "magnetic_lat_deg":   15.0,
        })

        logger.debug("SCINTILLATION_UPDATE | S4=%.3f | class=%s | error_m=%.1f | phase=%s",
                     scint["s4_index"], scint["scintillation_class"],
                     scint["positioning_error_m"], scint["diurnal_phase"])
    except Exception as exc:
        logger.error("Unhandled error in scintillation computation: %s", exc, exc_info=True)
