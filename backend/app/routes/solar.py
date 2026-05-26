# backend/app/routes/solar.py
"""
NAKSHATRA-KAVACH — Layer 1: Solar REST API Endpoints
Blueprint exposing 5 endpoints for live data, history, status, CME, and alerts.
All reads from LATEST_SNAPSHOT are O(1) — no DB query for live endpoint.
"""

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, Response

from app.services.ingestion_service import LATEST_SNAPSHOT, get_snapshot
from app.utils.constants import (
    HISTORY_DEFAULT_HOURS,
    HISTORY_MAX_HOURS,
    HISTORY_MAX_RECORDS,
    QUALITY_STALE,
    DATA_AGE_STALE_SECONDS,
)

logger = logging.getLogger(__name__)

solar_bp = Blueprint("solar", __name__, url_prefix="/api/solar")


def _quality_headers(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Build standard quality response headers from snapshot."""
    return {
        "X-Data-Quality": snapshot.get("data_quality", "UNKNOWN"),
        "X-Last-Updated": snapshot.get("last_updated_utc") or "never",
        "Cache-Control":  "no-cache, no-store, must-revalidate",
    }


def _stale_response(snapshot: Dict[str, Any]) -> Optional[Response]:
    """
    Return a 503 JSON error if data is stale beyond the threshold.
    Returns None if data is acceptable.
    """
    age = snapshot.get("data_age_seconds", 0)
    quality = snapshot.get("data_quality", "UNKNOWN")
    if quality == QUALITY_STALE and age > DATA_AGE_STALE_SECONDS:
        resp = jsonify({
            "error": "Data unavailable — all external APIs unreachable",
            "age_seconds": age,
            "last_known_utc": snapshot.get("last_updated_utc"),
        })
        resp.status_code = 503
        for k, v in _quality_headers(snapshot).items():
            resp.headers[k] = v
        return resp
    return None


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/live
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/live", methods=["GET"])
def get_live():
    """
    Return the full LATEST_SNAPSHOT as JSON.
    Memory read only — target response time < 10ms.

    Returns:
        200: Full snapshot dict.
        503: If data is stale and age > 600s.
    """
    snapshot = get_snapshot()
    stale = _stale_response(snapshot)
    if stale and request.args.get("allow_stale", "").lower() not in {"1", "true", "yes"}:
        return stale

    resp = jsonify(snapshot)
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/history
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/history", methods=["GET"])
def get_history():
    """
    Return historical solar wind readings from the DB.

    Query params:
        hours:   int, default 24, max 168
        quality: comma-separated quality flags, default "GOOD,PARTIAL"

    Returns:
        200: JSON array of records, newest first.
        400: Invalid query parameters.
    """
    try:
        hours = int(request.args.get("hours", HISTORY_DEFAULT_HOURS))
        hours = max(1, min(hours, HISTORY_MAX_HOURS))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid 'hours' parameter"}), 400

    quality_param = request.args.get("quality", "GOOD,PARTIAL")
    quality_filter = [q.strip().upper() for q in quality_param.split(",") if q.strip()]

    from app.database.db import get_solar_wind_history
    records = get_solar_wind_history(
        hours=hours,
        quality_filter=quality_filter or None,
        limit=HISTORY_MAX_RECORDS,
    )

    snapshot = get_snapshot()
    resp = jsonify({"count": len(records), "hours": hours, "records": records})
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/status
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/status", methods=["GET"])
def get_status():
    """
    Return system health: scheduler status, source health, data age.

    Returns:
        200: Status dict.
    """
    from app.database.db import get_last_ingestion_status
    from scheduler import scheduler

    snapshot = get_snapshot()
    sources = get_last_ingestion_status()

    payload = {
        "scheduler_running":  scheduler.running,
        "last_poll_utc":      snapshot.get("last_updated_utc"),
        "data_quality":       snapshot.get("data_quality"),
        "data_age_seconds":   snapshot.get("data_age_seconds"),
        "sources": {
            "noaa_swpc":   sources.get("noaa_swpc",   {"status": "UNKNOWN"}),
            "nasa_donki":  sources.get("nasa_donki",  {"status": "UNKNOWN"}),
            "goes_xray":   sources.get("goes_xray",   {"status": "UNKNOWN"}),
            "noaa_alerts": sources.get("noaa_alerts", {"status": "UNKNOWN"}),
        },
        "active_cme":       snapshot["cme"].get("earth_directed", False),
        "storm_imminent":   snapshot["computed"].get("storm_imminent", False),
    }

    resp = jsonify(payload)
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/cme
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/cme", methods=["GET"])
def get_cme():
    """
    Return current active CME details from snapshot plus DB history.

    Returns:
        200: Dict with current CME snapshot and list of recent DB events.
    """
    from app.database.db import get_recent_cme_events
    snapshot = get_snapshot()
    recent_events = get_recent_cme_events(days=7)

    payload = {
        "current": snapshot.get("cme", {}),
        "recent_events": recent_events,
    }
    resp = jsonify(payload)
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/alerts
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/alerts", methods=["GET"])
def get_alerts():
    """
    Return the 10 most recent NOAA official alerts from the DB.

    Returns:
        200: Dict with alert snapshot summary and list of recent alerts.
    """
    from app.database.db import get_recent_alerts
    snapshot = get_snapshot()
    alerts = get_recent_alerts(limit=10)

    payload = {
        "current": snapshot.get("alert", {}),
        "recent_alerts": alerts,
    }
    resp = jsonify(payload)
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/dst
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/dst", methods=["GET"])
def get_dst():
    """
    Return real-time Dst ring-current index from snapshot and DB history.

    Query params:
        hours: int, default 24, max 168 — history look-back window.

    Returns:
        200: Dict with current Dst snapshot and recent history.
    """
    try:
        hours = int(request.args.get("hours", 24))
        hours = max(1, min(hours, 168))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid 'hours' parameter"}), 400

    from app.database.db import get_dst_history, get_latest_dst
    snapshot = get_snapshot()
    current_dst = snapshot.get("dst", {})
    history = get_dst_history(hours=hours, limit=hours * 60)

    payload = {
        "current": current_dst,
        "history": history,
        "hours": hours,
        "count": len(history),
        "description": "Dst (Disturbance Storm Time) ring-current index in nanoTesla."
                        " Negative values indicate geomagnetic disturbance.",
        "thresholds": {
            "QUIET": ">= -20 nT",
            "MINOR": "-20 to -50 nT",
            "MODERATE": "-50 to -100 nT",
            "INTENSE": "-100 to -200 nT",
            "EXTREME": "< -200 nT",
        },
    }
    resp = jsonify(payload)
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200


# ─────────────────────────────────────────────────────────────────
# GET /api/solar/sep
# ─────────────────────────────────────────────────────────────────

@solar_bp.route("/sep", methods=["GET"])
def get_sep():
    """
    Return Solar Energetic Particle (SEP) proton flux from snapshot and DB history.

    Query params:
        hours: int, default 24, max 168 — history look-back window.

    Returns:
        200: Dict with current SEP snapshot, alert status, and recent history.
    """
    try:
        hours = int(request.args.get("hours", 24))
        hours = max(1, min(hours, 168))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid 'hours' parameter"}), 400

    from app.database.db import get_sep_history
    snapshot = get_snapshot()
    current_sep = snapshot.get("sep", {})
    history = get_sep_history(hours=hours)

    payload = {
        "current": current_sep,
        "history": history,
        "hours": hours,
        "count": len(history),
        "alert_active": current_sep.get("sep_alert_active", False),
        "description": "GOES integral proton flux at >10 MeV (Solar Energetic Particles)."
                        " S1 threshold = 10 pfu. Active SEP events increase SEU risk.",
        "noaa_scale": {
            "S1": ">=10 pfu (minor)",
            "S2": ">=100 pfu (moderate)",
            "S3": ">=1000 pfu (strong)",
            "S4": ">=10000 pfu (severe)",
            "S5": ">=100000 pfu (extreme)",
        },
    }
    resp = jsonify(payload)
    for k, v in _quality_headers(snapshot).items():
        resp.headers[k] = v
    return resp, 200
