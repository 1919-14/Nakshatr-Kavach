# backend/app/routes/satellites.py
"""NAKSHATRA-KAVACH Layer 4: Satellite vulnerability REST endpoints."""
from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait
from flask import Blueprint, jsonify, request
import requests

logger = logging.getLogger(__name__)
satellites_bp = Blueprint("satellites", __name__, url_prefix="/api/satellites")
_TLE_CACHE: dict = {"expires": 0.0, "payload": None}


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


@satellites_bp.route("/tle", methods=["GET"])
def get_live_tles():
    """Fetch current public TLEs from CelesTrak for catalogued NORAD IDs."""
    from app.services.satellite_scorer import sat_db
    if not sat_db.is_loaded:
        sat_db.load()

    now = time.time()
    if _TLE_CACHE["payload"] and now < _TLE_CACHE["expires"]:
        return jsonify(_TLE_CACHE["payload"])

    catalog = sat_db.get_all_tier1() + sat_db.get_all_tier2() + sat_db.tier3
    by_norad = {}
    for sat in catalog:
        norad = sat.get("norad_id") or sat.get("tlenorad_id")
        if norad:
            by_norad[str(norad)] = sat

    if request.args.get("live", "").lower() not in {"1", "true", "yes"}:
        return jsonify({
            "source": "CelesTrak GP",
            "count": 0,
            "requested": len(by_norad),
            "satellites": [],
            "errors": [],
            "cache_seconds": 0,
            "live_refresh_skipped": True,
            "message": "Pass live=true to refresh public TLEs; dashboard uses local TLE cache plus catalog fallback by default.",
        })

    def fetch_one(norad: str, sat: dict) -> tuple[dict | None, dict | None]:
        try:
            response = requests.get(
                "https://celestrak.org/NORAD/elements/gp.php",
                params={"CATNR": norad, "FORMAT": "TLE"},
                headers={"User-Agent": "Nakshatra-Kavach/1.0 hackathon demo"},
                timeout=(2, 4),
            )
            response.raise_for_status()
            lines = [line.strip() for line in response.text.splitlines() if line.strip()]
            tle1 = next((line for line in lines if line.startswith("1 ")), None)
            tle2 = next((line for line in lines if line.startswith("2 ")), None)
            if not tle1 or not tle2:
                return None, {"norad_id": norad, "name": sat.get("name"), "error": "No TLE lines returned"}
            return {
                "name": sat.get("name"),
                "display_name": sat.get("display_name", sat.get("name")),
                "norad_id": norad,
                "tle1": tle1,
                "tle2": tle2,
                "epoch": tle1[18:32].strip() if len(tle1) >= 32 else None,
                "source": "CelesTrak GP",
            }, None
        except Exception as exc:
            return None, {"norad_id": norad, "name": sat.get("name"), "error": str(exc)}

    satellites = []
    errors = []
    executor = ThreadPoolExecutor(max_workers=min(6, max(1, len(by_norad))))
    futures = [executor.submit(fetch_one, norad, sat) for norad, sat in by_norad.items()]
    done, pending = wait(futures, timeout=12)
    for future in done:
        try:
            sat_payload, err_payload = future.result()
        except Exception as exc:
            sat_payload, err_payload = None, {"norad_id": None, "name": None, "error": str(exc)}
        if sat_payload:
            satellites.append(sat_payload)
        if err_payload:
            errors.append(err_payload)
    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    if pending:
        errors.append({"error": f"TLE fetch timed out for {len(pending)} satellite(s)"})

    payload = {
        "source": "CelesTrak GP",
        "count": len(satellites),
        "requested": len(by_norad),
        "satellites": satellites,
        "errors": errors[:10],
        "cache_seconds": 6 * 60 * 60,
    }
    _TLE_CACHE["payload"] = payload
    _TLE_CACHE["expires"] = now + payload["cache_seconds"]
    return jsonify(payload)


@satellites_bp.route("/navic", methods=["GET"])
@satellites_bp.route("/navig", methods=["GET"])
def get_navic_status():
    """NavIC constellation-specific status including scintillation metrics."""
    from app.services.satellite_scorer import get_latest_satellite_risks
    from app.services.ingestion_service import get_snapshot
    risks = get_latest_satellite_risks()
    if not risks:
        return jsonify({"nav_system_status": "UNKNOWN", "message": "No data"}), 503
    
    snapshot = get_snapshot()
    scint = snapshot.get("scintillation", {})
    
    navic = risks.get("tier1", {}).get("NavIC-IRNSS", {})
    return jsonify({
        "nav_system_status": navic.get("nav_system_status", "NOMINAL"),
        "nav_error_meters": max(navic.get("nav_error_meters", 0.0), scint.get("positioning_error_m", 0.0)),
        "clock_anomaly_risk": navic.get("risk_scores", {}).get("clock_anomaly_risk", 0),
        "affected_users_million": navic.get("affected_users_million", 500),
        "risk_level": navic.get("risk_level", "MINIMAL"),
        "composite_risk": navic.get("risk_scores", {}).get("composite_final", 0),
        "kp_used": risks.get("kp_used", 0),
        "computed_at_utc": risks.get("computed_at_utc"),
        "scintillation": {
            "s4_index": scint.get("s4_index", 0.05),
            "scintillation_class": scint.get("scintillation_class", "NONE"),
            "positioning_error_m": scint.get("positioning_error_m", 3.0),
            "navic_status": scint.get("navic_status", "NOMINAL"),
            "diurnal_phase": scint.get("diurnal_phase", "DAY"),
            "eia_active": scint.get("eia_active", False),
            "clock_error_ns": scint.get("clock_error_ns", 0.0),
        }
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
