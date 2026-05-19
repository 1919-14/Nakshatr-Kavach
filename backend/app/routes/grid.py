# backend/app/routes/grid.py
"""NAKSHATRA-KAVACH Layer 5: India grid GIC risk REST endpoints."""
from __future__ import annotations

import logging
from typing import Tuple

from flask import Blueprint, Response, jsonify, request

from app.utils.constants import GIC_MODEL_ACCURACY_NOTE, SATURATION_THRESHOLDS

logger = logging.getLogger(__name__)
grid_bp = Blueprint("grid", __name__, url_prefix="/api/grid")
OptionalResponse = Response | None


def _ensure_grid_db() -> Tuple[bool, Any]:
    """Load the grid database if needed and return availability status."""
    try:
        from app.services.grid_risk_engine import grid_db

        if not grid_db.is_loaded:
            grid_db.load()
        return True, grid_db
    except Exception as exc:
        logger.error("Grid database unavailable: %s", exc)
        return False, None


def _latest_or_503() -> Tuple[dict, OptionalResponse]:
    """Return latest risks or a 503 JSON response when unavailable."""
    ok, _ = _ensure_grid_db()
    if not ok:
        return {}, _with_standard_headers(
            jsonify({"status": "unavailable", "message": "Grid database not loaded"}),
            {},
            status_code=503,
        )
    from app.services.grid_risk_engine import get_latest_grid_risks

    risks = get_latest_grid_risks()
    if not risks:
        return {}, _with_standard_headers(
            jsonify({"status": "unavailable", "message": "No grid scoring data yet"}),
            {},
            status_code=503,
        )
    return risks, None


def _with_standard_headers(response: Response, risks: dict, status_code: int = 200) -> Response:
    """Attach Layer 5 freshness and data-quality headers to a response."""
    response.status_code = status_code
    response.headers["X-Computed-At"] = str(risks.get("computed_at_utc", "N/A"))
    response.headers["X-Data-Quality"] = str(risks.get("data_quality_used", "UNKNOWN"))
    return response


@grid_bp.route("/risk", methods=["GET"])
def get_grid_risk() -> Response:
    """Return the full LATEST_GRID_RISKS object."""
    risks, error = _latest_or_503()
    if error:
        return error
    resp = _with_standard_headers(jsonify(risks), risks)
    ns = risks.get("national_summary", {})
    resp.headers["X-Critical-Corridors"] = str(ns.get("critical_corridors_count", 0))
    resp.headers["X-Storm-Class-Used"] = str(risks.get("storm_class_used", "UNKNOWN"))
    return resp


@grid_bp.route("/risk/<path:corridor_id>", methods=["GET"])
def get_corridor_risk(corridor_id: str) -> Response:
    """Return risk object for one corridor by ID."""
    risks, error = _latest_or_503()
    if error:
        return error
    for corridor in risks.get("corridors", []):
        if corridor.get("corridor_id") == corridor_id:
            return _with_standard_headers(jsonify(corridor), risks)
    return _with_standard_headers(
        jsonify({"error": f"Corridor '{corridor_id}' not found"}),
        risks,
        status_code=404,
    )


@grid_bp.route("/critical", methods=["GET"])
def get_critical_corridors() -> Response:
    """Return HIGH and CRITICAL corridors sorted by saturation risk."""
    risks, error = _latest_or_503()
    if error:
        return error
    critical = [
        c for c in risks.get("corridors", [])
        if c.get("risk_level") in ("CRITICAL", "HIGH")
    ]
    critical.sort(key=lambda c: c.get("saturation_risk", 0.0), reverse=True)
    return _with_standard_headers(
        jsonify({
            "count": len(critical),
            "corridors": critical,
            "computed_at_utc": risks.get("computed_at_utc"),
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
        }),
        risks,
    )


@grid_bp.route("/map", methods=["GET"])
def get_grid_map() -> Response:
    """Return Leaflet-ready corridor map data plus national summary."""
    risks, error = _latest_or_503()
    if error:
        return error
    return _with_standard_headers(
        jsonify({
            "map_data": risks.get("map_data", []),
            "national_summary": risks.get("national_summary", {}),
            "computed_at_utc": risks.get("computed_at_utc"),
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
        }),
        risks,
    )


@grid_bp.route("/summary", methods=["GET"])
def get_grid_summary() -> Response:
    """Return national grid impact summary only."""
    risks, error = _latest_or_503()
    if error:
        return error
    return _with_standard_headers(jsonify(risks.get("national_summary", {})), risks)


@grid_bp.route("/gic-forecast/<path:corridor_id>", methods=["GET"])
def get_gic_forecast(corridor_id: str) -> Response:
    """Return GIC amplitude forecast and transformer thresholds for one corridor."""
    risks, error = _latest_or_503()
    if error:
        return error
    for corridor in risks.get("corridors", []):
        if corridor.get("corridor_id") == corridor_id:
            payload = {
                "corridor_id": corridor_id,
                "corridor_name": corridor.get("corridor_name"),
                "labels": ["Now", "3hr", "6hr", "12hr", "24hr"],
                "values": [
                    round(corridor.get("gic_now_amps", 0.0), 1),
                    round(corridor.get("gic_by_horizon", {}).get("3hr", 0.0), 1),
                    round(corridor.get("gic_by_horizon", {}).get("6hr", 0.0), 1),
                    round(corridor.get("gic_by_horizon", {}).get("12hr", 0.0), 1),
                    round(corridor.get("gic_by_horizon", {}).get("24hr", 0.0), 1),
                ],
                "thresholds": corridor.get("saturation_thresholds", {}),
                "threshold_minor": corridor.get("saturation_thresholds", {}).get("minor"),
                "threshold_critical": corridor.get("saturation_thresholds", {}).get("critical"),
                "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            }
            return _with_standard_headers(jsonify(payload), risks)
    return _with_standard_headers(
        jsonify({"error": f"Corridor '{corridor_id}' not found"}),
        risks,
        status_code=404,
    )


@grid_bp.route("/historical-context", methods=["GET"])
def get_historical_context() -> Response:
    """Return a historical GIC incident comparison for a Kp/storm class pair."""
    ok, _ = _ensure_grid_db()
    if not ok:
        return _with_standard_headers(
            jsonify({"status": "unavailable", "message": "Grid database not loaded"}),
            {},
            status_code=503,
        )
    from app.services.grid_risk_engine import HistoricalContextEngine, get_latest_grid_risks

    kp = request.args.get("kp", type=float)
    storm_class = request.args.get("storm_class", "UNKNOWN")
    latest = get_latest_grid_risks()
    if kp is None:
        kp = float(latest.get("kp_peak_used", 0.0)) if latest else 0.0
    context = HistoricalContextEngine.get_historical_context(kp, storm_class)
    return _with_standard_headers(
        jsonify({
            "kp": kp,
            "storm_class": storm_class,
            "historical_context": context,
        }),
        latest,
    )


@grid_bp.route("/catalog", methods=["GET"])
def get_grid_catalog() -> Response:
    """Return static corridor database entries without risk scores."""
    ok, db = _ensure_grid_db()
    if not ok:
        return _with_standard_headers(
            jsonify({"status": "unavailable", "message": "Grid database not loaded"}),
            {},
            status_code=503,
        )
    from app.services.grid_risk_engine import get_latest_grid_risks

    latest = get_latest_grid_risks()
    corridors = db.get_all()
    for corridor in corridors:
        corridor["saturation_thresholds"] = SATURATION_THRESHOLDS[corridor["transformer_type"]]
    return _with_standard_headers(
        jsonify({
            "count": len(corridors),
            "corridors": corridors,
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
        }),
        latest,
    )
