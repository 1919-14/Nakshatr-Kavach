# backend/app/routes/satellites.py
"""NAKSHATRA-KAVACH Layer 4: Satellite vulnerability REST endpoints."""
from __future__ import annotations
import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
satellites_bp = Blueprint("satellites", __name__, url_prefix="/api/satellites")


@satellites_bp.route("/risk", methods=["GET"])
def get_all_risks():
    """Full LATEST_SATELLITE_RISKS dict."""
    from app.services.satellite_scorer import get_latest_satellite_risks
    risks = get_latest_satellite_risks()
    if not risks:
        return jsonify({"status": "unavailable", "message": "No scoring data yet"}), 503
    resp = jsonify(risks)
    resp.headers["X-Critical-Count"] = str(risks.get("critical_count", 0))
    resp.headers["X-Storm-Class-Used"] = risks.get("storm_class_used", "UNKNOWN")
    return resp


@satellites_bp.route("/risk/<path:name>", methods=["GET"])
def get_satellite_risk(name: str):
    """Risk object for one satellite by name."""
    from app.services.satellite_scorer import get_latest_satellite_risks
    risks = get_latest_satellite_risks()
    if not risks:
        return jsonify({"error": "No scoring data yet"}), 503
    sat = risks.get("tier1", {}).get(name)
    if not sat:
        for t2 in risks.get("tier2", []):
            if t2.get("name") == name:
                sat = t2
                break
    if not sat:
        return jsonify({"error": f"Satellite '{name}' not found"}), 404
    return jsonify(sat)


@satellites_bp.route("/critical", methods=["GET"])
def get_critical_satellites():
    """Only satellites at CRITICAL risk level, sorted by composite descending."""
    from app.services.satellite_scorer import get_latest_satellite_risks
    risks = get_latest_satellite_risks()
    if not risks:
        return jsonify({"status": "unavailable"}), 503
    critical = [r for r in risks.get("tier1", {}).values() if r.get("risk_level") == "CRITICAL"]
    critical.sort(key=lambda r: r["risk_scores"]["composite_final"], reverse=True)
    return jsonify({"count": len(critical), "satellites": critical,
                    "kp_used": risks.get("kp_used"), "computed_at_utc": risks.get("computed_at_utc")})


@satellites_bp.route("/orbits", methods=["GET"])
def get_orbit_params():
    """Orbit visualisation params for Three.js Earth Globe."""
    from app.services.satellite_scorer import compute_all_orbit_params
    return jsonify({"orbits": compute_all_orbit_params()})


@satellites_bp.route("/catalog", methods=["GET"])
def get_catalog():
    """Full satellite database entries (no risk scores)."""
    from app.services.satellite_scorer import sat_db
    if not sat_db.is_loaded:
        sat_db.load()
    tier_filter = request.args.get("tier", "1,2")
    tiers = [int(t.strip()) for t in tier_filter.split(",") if t.strip().isdigit()]
    result = {}
    if 1 in tiers:
        result["tier1"] = sat_db.get_all_tier1()
    if 2 in tiers:
        result["tier2"] = sat_db.get_all_tier2()
    if 3 in tiers:
        result["tier3"] = sat_db.tier3
    result["fleet_count"] = sat_db.fleet_count()
    return jsonify(result)


@satellites_bp.route("/navic", methods=["GET"])
def get_navic_status():
    """NavIC constellation-specific status."""
    from app.services.satellite_scorer import get_latest_satellite_risks
    risks = get_latest_satellite_risks()
    if not risks:
        return jsonify({"nav_system_status": "UNKNOWN", "message": "No data"}), 503
    navic = risks.get("tier1", {}).get("NavIC-IRNSS", {})
    return jsonify({
        "nav_system_status": navic.get("nav_system_status", "NOMINAL"),
        "nav_error_meters": navic.get("nav_error_meters", 0),
        "clock_anomaly_risk": navic.get("risk_scores", {}).get("clock_anomaly_risk", 0),
        "affected_users_million": navic.get("affected_users_million", 500),
        "risk_level": navic.get("risk_level", "MINIMAL"),
        "composite_risk": navic.get("risk_scores", {}).get("composite_final", 0),
        "kp_used": risks.get("kp_used", 0),
        "computed_at_utc": risks.get("computed_at_utc"),
    })


@satellites_bp.route("/fleet-summary", methods=["GET"])
def get_fleet_summary():
    """Fleet summary stats for dashboard header bar."""
    from app.services.satellite_scorer import get_latest_satellite_risks
    risks = get_latest_satellite_risks()
    if not risks:
        return jsonify({"status": "unavailable"}), 503
    return jsonify({
        "fleet_summary": risks.get("fleet_summary", {}),
        "total_at_risk": risks.get("total_at_risk", 0),
        "critical_count": risks.get("critical_count", 0),
        "high_count": risks.get("high_count", 0),
        "fleet_monitored": risks.get("fleet_monitored", 0),
        "kp_used": risks.get("kp_used", 0),
        "storm_class_used": risks.get("storm_class_used", "QUIET"),
        "computed_at_utc": risks.get("computed_at_utc"),
    })
