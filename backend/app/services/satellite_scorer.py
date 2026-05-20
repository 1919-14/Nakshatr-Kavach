# backend/app/services/satellite_scorer.py
"""
NAKSHATRA-KAVACH Layer 4: Satellite Vulnerability Scoring Engine.
Maps Kp forecasts to per-satellite, per-mechanism risk scores.
"""
from __future__ import annotations
import json, logging, math, threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from app.utils.constants import (
    RISK_LEVEL_THRESHOLDS, RISK_LEVEL_NUMERIC, RISK_LEVEL_COLORS,
    ORBIT_RISK_WEIGHTS, SHIELDING_FACTORS, XRAY_SEU_FACTORS,
    DRAG_NORMALIZATION_FACTOR, CHARGING_EXPONENT,
    SAA_AMPLIFICATION_BASE, SAFE_MODE_BUFFER_MINUTES, HORIZON_MINUTES,
    EARTH_RADIUS_KM, EARTH_RADIUS_3JS,
    NAVIC_DEGRADATION_KP, NAVIC_IMPAIRED_KP, NAVIC_AFFECTED_USERS_MILLION,
    WS_EVENT_SATELLITE_RISK_CHANGE,
)
from app.utils.formatters import utcnow_iso

# ── Thread-safe singleton ────────────────────────────────────────────────
_RISKS_LOCK = threading.RLock()
LATEST_SATELLITE_RISKS: Dict[str, Any] = {}


def update_latest_satellite_risks(risks: dict) -> None:
    global LATEST_SATELLITE_RISKS
    with _RISKS_LOCK:
        LATEST_SATELLITE_RISKS = risks


def get_latest_satellite_risks() -> dict:
    with _RISKS_LOCK:
        return LATEST_SATELLITE_RISKS.copy()


# ═════════════════════════════════════════════════════════════════════════
# Satellite Database (singleton)
# ═════════════════════════════════════════════════════════════════════════

class SatelliteDatabase:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._loaded = False
            return cls._instance

    def load(self) -> None:
        path = Path(__file__).resolve().parent.parent / "data" / "isro_satellites.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.tier1: List[dict] = data.get("tier1", [])
        self.tier2: List[dict] = data.get("tier2", [])
        self.tier3: List[dict] = data.get("tier3", [])
        self.metadata: dict = data.get("metadata", {})
        self._tier1_map = {s["name"]: s for s in self.tier1}
        self._tier2_map = {s["name"]: s for s in self.tier2}
        self._loaded = True
        logger.info("Satellite DB: T1=%d T2=%d T3=%d", len(self.tier1), len(self.tier2), len(self.tier3))

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_tier1(self, name: str) -> Optional[dict]:
        return self._tier1_map.get(name)

    def get_all_tier1(self) -> List[dict]:
        return self.tier1

    def get_all_tier2(self) -> List[dict]:
        return self.tier2

    def fleet_count(self) -> int:
        return len(self.tier1) + len(self.tier2) + len(self.tier3)


sat_db = SatelliteDatabase()


# ═════════════════════════════════════════════════════════════════════════
# Risk Calculators — Three Kill Mechanisms
# ═════════════════════════════════════════════════════════════════════════

def _clamp(v: float) -> float:
    return min(100.0, max(0.0, v))


def calculate_drag_risk(satellite: dict, kp_peak: float) -> float:
    """Atmospheric drag risk using Jacchia-77 simplified density model. LEO only."""
    if not satellite.get("drag_applicable", False):
        return 0.0
    alt = satellite["altitude_km"]
    storm_mult = 10 ** (kp_peak / 4.5)
    if alt < 400:   alt_scale = 0.5
    elif alt < 500: alt_scale = 0.7
    elif alt < 600: alt_scale = 1.0
    elif alt < 800: alt_scale = 1.4
    else:           alt_scale = 2.5
    raw = storm_mult / alt_scale
    return _clamp((raw / DRAG_NORMALIZATION_FACTOR) * 100.0)


def calculate_charging_risk(satellite: dict, kp_peak: float) -> float:
    """Surface electrostatic charging from outer radiation belt electrons. GEO only."""
    if not satellite.get("charging_applicable", False):
        return 0.0
    intensity = (kp_peak / 9.0) ** CHARGING_EXPONENT
    shield = SHIELDING_FACTORS.get(satellite.get("shielding_level", "MEDIUM"), 1.0)
    eclipse = 1.0 + satellite.get("eclipse_fraction", 0.025) * 2.0
    return _clamp(intensity * shield * eclipse * 100.0)


def calculate_radiation_risk(satellite: dict, kp_peak: float, solar_data: dict) -> float:
    """Radiation Single Event Upset risk from SEPs and SAA. All orbits."""
    if not satellite.get("seu_applicable", False):
        return 0.0
    xray_sev = solar_data.get("xray_severity_numeric", 1)
    xray_f = XRAY_SEU_FACTORS.get(xray_sev, 1.0)
    if satellite.get("orbit_type") == "L1_HALO":
        base = (kp_peak / 9.0) * 50.0
        xray_c = xray_f * 15.0
    else:
        base = (kp_peak / 9.0) * 60.0
        xray_c = xray_f * 15.0
    saa = 0.0
    if satellite.get("saa_applicable", False):
        saa = min(25.0, (1.0 + (kp_peak / 9.0) * SAA_AMPLIFICATION_BASE) * 4.0)
    direct = 20.0 if satellite.get("direct_radiation_exposure", False) else 0.0
    return _clamp(base + xray_c + saa + direct)


def calculate_clock_anomaly(satellite: dict, charging: float, kp_peak: float) -> dict:
    """NavIC atomic clock frequency drift under surface charging stress."""
    if "clock_anomaly" not in satellite.get("secondary_risk_mechanism", ""):
        return {"clock_anomaly_risk": 0.0, "nav_error_meters": 0.0, "nav_system_status": "N/A"}
    risk = _clamp(charging * 0.4)
    err = round(satellite.get("navig_error_during_storm_m", 15.0) * (kp_peak / 9.0), 1)
    if kp_peak >= NAVIC_IMPAIRED_KP:     status = "IMPAIRED"
    elif kp_peak >= NAVIC_DEGRADATION_KP: status = "DEGRADED"
    else:                                  status = "NOMINAL"
    return {"clock_anomaly_risk": round(risk, 1), "nav_error_meters": err,
            "nav_system_status": status, "affected_users_million": NAVIC_AFFECTED_USERS_MILLION,
            "nav_degradation_onset_kp": NAVIC_DEGRADATION_KP,
            "recommendation_users": "Alert navigation-dependent services to switch to backup positioning"}


def calculate_composite(satellite: dict, drag: float, charging: float, radiation: float) -> float:
    """Weighted composite score with criticality multiplier."""
    orbit = satellite.get("orbit_type", "LEO")
    w = ORBIT_RISK_WEIGHTS.get(orbit, ORBIT_RISK_WEIGHTS["LEO"])
    raw = _clamp(w["drag"] * drag + w["charging"] * charging + w["seu"] * radiation)
    return _clamp(raw * satellite.get("criticality_multiplier", 1.0))


def classify_risk_level(composite: float) -> str:
    if composite >= 80: return "CRITICAL"
    elif composite >= 60: return "HIGH"
    elif composite >= 40: return "MODERATE"
    elif composite >= 20: return "LOW"
    return "MINIMAL"


def compute_risk_at_horizon(sat: dict, kp: float, solar: dict) -> float:
    d = calculate_drag_risk(sat, kp)
    c = calculate_charging_risk(sat, kp)
    r = calculate_radiation_risk(sat, kp, solar)
    return calculate_composite(sat, d, c, r)


# ═════════════════════════════════════════════════════════════════════════
# Safe Mode Countdown
# ═════════════════════════════════════════════════════════════════════════

def compute_safe_mode_countdown(sat: dict, risk_obj: dict, kp_fc: dict) -> dict:
    if sat.get("name") == "ADITYA-L1":
        return {"safe_mode_required": False, "countdown_display": "N/A", "countdown_urgency": "ENHANCED_OBS"}
    if risk_obj.get("risk_level") not in ("HIGH", "CRITICAL"):
        return {"safe_mode_required": False, "countdown_display": "--:--", "countdown_urgency": "NONE"}
    summary = kp_fc.get("summary", {})
    if summary.get("cme_active"):
        mins = summary.get("transit_warning_minutes", 360)
        atype = "CME_ARRIVAL"
    else:
        mins = risk_obj.get("safe_mode_minutes") or 360
        atype = "KP_FORECAST"
    deadline = max(0, mins - SAFE_MODE_BUFFER_MINUTES)
    if deadline <= 10:   urg = "CRITICAL_IMMEDIATE"
    elif deadline <= 30: urg = "URGENT"
    elif deadline <= 60: urg = "ELEVATED"
    else:                urg = "ADVISORY"
    h, m = int(deadline // 60), int(deadline % 60)
    return {"safe_mode_required": True, "safe_mode_deadline_minutes": deadline,
            "countdown_display": f"{h:02d}:{m:02d}", "countdown_urgency": urg,
            "arrival_type": atype, "storm_arrival_minutes": mins,
            "action_text": sat.get("safe_mode_action", "Initiate safe mode"),
            "should_pulse_animation": deadline <= 30}


# ═════════════════════════════════════════════════════════════════════════
# Orbit Visualisation
# ═════════════════════════════════════════════════════════════════════════

def compute_orbit_params(sat: dict) -> dict:
    scale = EARTH_RADIUS_3JS / EARTH_RADIUS_KM
    r_km = EARTH_RADIUS_KM + sat["altitude_km"]
    period = sat.get("orbital_period_min", 1436.0)
    return {"satellite_name": sat["name"], "orbit_radius_3js": round(r_km * scale, 4),
            "orbit_radius_km": r_km, "inclination_rad": round(math.radians(sat.get("inclination_deg", 0)), 4),
            "angular_velocity": round(2 * math.pi / period, 6),
            "start_angle_rad": round(math.radians(sat.get("longitude_deg", 0)), 4),
            "orbit_color_hex": sat.get("color_hex", "#00D4FF"),
            "orbit_type": sat.get("orbit_type", "LEO"),
            "is_geostationary": sat.get("orbit_type") == "GEO",
            "trail_length": 20 if sat.get("orbit_type") == "LEO" else 5,
            "point_size": 0.04, "label": sat.get("display_name", sat["name"]),
            "dashboard_priority": sat.get("dashboard_priority", 99)}


def compute_all_orbit_params() -> List[dict]:
    if not sat_db.is_loaded:
        sat_db.load()
    out = [compute_orbit_params(s) for s in sat_db.get_all_tier1() + sat_db.get_all_tier2()]
    return sorted(out, key=lambda p: p["dashboard_priority"])


def _catalog_only_satellite(sat: dict, tier: int) -> dict:
    name = sat.get("name", "UNKNOWN")
    return {
        "name": name,
        "display_name": sat.get("display_name", name),
        "tier": sat.get("tier", tier),
        "orbit_type": sat.get("orbit_type", "CATALOG"),
        "altitude_km": sat.get("altitude_km"),
        "inclination_deg": sat.get("inclination_deg"),
        "mission": sat.get("mission", "Catalogued ISRO/India fleet asset"),
        "tlenorad_id": sat.get("tlenorad_id"),
        "norad_id": sat.get("norad_id"),
        "risk_scores": {"drag_risk": 0.0, "charging_risk": 0.0,
                        "radiation_risk": 0.0, "clock_anomaly_risk": 0.0,
                        "composite_raw": 0.0, "composite_final": 0.0,
                        "criticality_multiplier": 1.0},
        "risk_level": "MINIMAL",
        "risk_level_numeric": RISK_LEVEL_NUMERIC.get("MINIMAL", 0),
        "risk_color_hex": RISK_LEVEL_COLORS.get("MINIMAL", "#9E9E9E"),
        "recommended_action": "Monitor catalog asset during storm escalation",
        "urgency": "NONE",
        "primary_threat": "catalog_only",
    }


# ═════════════════════════════════════════════════════════════════════════
# Full Scoring Pipeline
# ═════════════════════════════════════════════════════════════════════════

def _extract_kp_peak(kp_fc: dict) -> float:
    fc = kp_fc.get("forecast", {})
    return max((fc.get(h, {}).get("kp", 0) for h in ("3hr", "6hr", "12hr")), default=0)


def _extract_solar(snapshot: dict) -> dict:
    sw = snapshot.get("solar_wind", {})
    return {"xray_severity_numeric": sw.get("xray_severity_numeric", 1),
            "sw_speed_kmps": sw.get("sw_speed_kmps", 400)}


def score_one(sat: dict, kp_fc: dict, solar: dict) -> dict:
    """Full risk object for one satellite."""
    kp_peak = _extract_kp_peak(kp_fc)
    fc = kp_fc.get("forecast", {})
    drag = calculate_drag_risk(sat, kp_peak)
    charging = calculate_charging_risk(sat, kp_peak)
    radiation = calculate_radiation_risk(sat, kp_peak, solar)
    composite = calculate_composite(sat, drag, charging, radiation)
    level = classify_risk_level(composite)
    clock = calculate_clock_anomaly(sat, charging, kp_peak)
    is_aditya = sat.get("name") == "ADITYA-L1"
    if is_aditya and kp_peak > 6:
        action = sat.get("special_action", sat.get("safe_mode_action", ""))
        urgency = "ENHANCED_OBS"
    elif level == "CRITICAL":
        action, urgency = sat.get("critical_action", "Safe mode now"), "IMMEDIATE"
    elif level == "HIGH":
        action, urgency = sat.get("safe_mode_action", "Prepare safe mode"), "IN_30_MINUTES"
    elif level == "MODERATE":
        action, urgency = sat.get("warning_action", "Elevate monitoring"), "WATCH"
    else:
        action, urgency = "Routine monitoring — no action required", "NONE"

    threats = {"atmospheric_drag": drag, "surface_charging": charging, "radiation_seu": radiation}
    primary = max(threats, key=threats.get) if max(threats.values()) > 0 else "none"

    sm_min = None
    at_risk = {}
    for h in ("3hr", "6hr", "12hr", "24hr"):
        kp_h = fc.get(h, {}).get("kp", 0)
        r_h = compute_risk_at_horizon(sat, kp_h, solar)
        at_risk[f"kp_{h}"] = round(kp_h, 1)
        at_risk[f"risk_{h}"] = round(r_h, 1)
        if sm_min is None and r_h >= 80:
            sm_min = HORIZON_MINUTES[h] - 10

    raw_comp = _clamp(
        ORBIT_RISK_WEIGHTS.get(sat.get("orbit_type", "LEO"), ORBIT_RISK_WEIGHTS["LEO"])["drag"] * drag +
        ORBIT_RISK_WEIGHTS.get(sat.get("orbit_type", "LEO"), ORBIT_RISK_WEIGHTS["LEO"])["charging"] * charging +
        ORBIT_RISK_WEIGHTS.get(sat.get("orbit_type", "LEO"), ORBIT_RISK_WEIGHTS["LEO"])["seu"] * radiation)

    obj = {
        "name": sat["name"], "display_name": sat.get("display_name", sat["name"]),
        "tier": sat.get("tier", 1), "orbit_type": sat.get("orbit_type"),
        "altitude_km": sat["altitude_km"], "mission": sat.get("mission", ""),
        "criticality": sat.get("criticality", "MODERATE"),
        "risk_scores": {"drag_risk": round(drag, 1), "charging_risk": round(charging, 1),
                        "radiation_risk": round(radiation, 1), "clock_anomaly_risk": clock["clock_anomaly_risk"],
                        "composite_raw": round(raw_comp, 1), "composite_final": round(composite, 1),
                        "criticality_multiplier": sat.get("criticality_multiplier", 1.0)},
        "risk_level": level, "risk_level_numeric": RISK_LEVEL_NUMERIC.get(level, 0),
        "risk_color_hex": RISK_LEVEL_COLORS.get(level, "#9E9E9E"),
        "safe_mode_minutes": sm_min, "recommended_action": action, "urgency": urgency,
        "primary_threat": primary, "at_risk_horizon": at_risk,
        "national_dependencies": sat.get("national_dependencies", []),
        "orbit_params": compute_orbit_params(sat),
    }
    if clock["nav_system_status"] != "N/A":
        obj.update(clock)
    obj["safe_mode_countdown"] = compute_safe_mode_countdown(sat, obj, kp_fc)
    return obj


def classify_orbit_type(alt: float) -> str:
    if alt < 2000: return "LEO"
    elif alt < 20000: return "MEO"
    elif alt < 37000: return "GEO"
    return "L1_HALO"


def run_satellite_scoring(kp_fc: dict, snapshot: dict) -> dict:
    """Run full fleet scoring pipeline. Returns LATEST_SATELLITE_RISKS structure."""
    if not sat_db.is_loaded:
        sat_db.load()
    solar = _extract_solar(snapshot)
    kp_peak = _extract_kp_peak(kp_fc)
    storm = kp_fc.get("summary", {}).get("peak_storm_class", "QUIET")
    now = utcnow_iso()

    t1 = {}
    for s in sat_db.get_all_tier1():
        t1[s["name"]] = score_one(s, kp_fc, solar)

    t2 = []
    for s in sat_db.get_all_tier2():
        r = score_one({**{"safe_mode_action": "Standard storm protocol", "warning_action": "Elevate monitoring",
                          "critical_action": "Initiate safe mode", "direct_radiation_exposure": False}, **s}, kp_fc, solar)
        t2.append({"name": r["name"], "composite_final": r["risk_scores"]["composite_final"],
                    "risk_level": r["risk_level"], "risk_color_hex": r["risk_color_hex"],
                    "orbit_type": r["orbit_type"], "altitude_km": r["altitude_km"],
                    "inclination_deg": s.get("inclination_deg"), "mission": s.get("mission", ""),
                    "tlenorad_id": s.get("tlenorad_id"), "norad_id": s.get("norad_id"),
                    "primary_threat": r["primary_threat"]})

    t3 = [_catalog_only_satellite(s, 3) for s in sat_db.tier3]

    crit = sum(1 for r in t1.values() if r["risk_level"] == "CRITICAL")
    high = sum(1 for r in t1.values() if r["risk_level"] == "HIGH")
    tier2_crit = sum(1 for r in t2 if r["risk_level"] == "CRITICAL")
    tier2_high = sum(1 for r in t2 if r["risk_level"] == "HIGH")
    scored_all = list(t1.values()) + [{"name": r["name"], "risk_scores": {"composite_final": r["composite_final"]}} for r in t2]
    best = max(scored_all, key=lambda r: r["risk_scores"]["composite_final"]) if scored_all else {}
    def_risk = sum(1 for r in t1.values() if r.get("criticality") == "DEFENSE_CRITICAL" and r["risk_level"] in ("HIGH", "CRITICAL"))
    nav = t1.get("NavIC-IRNSS", {})
    val = sum(s.get("asset_value_crore", 0) for s in sat_db.get_all_tier1()
              if t1.get(s["name"], {}).get("risk_level") in ("HIGH", "CRITICAL"))

    return {"computed_at_utc": now, "kp_used": round(kp_peak, 1), "storm_class_used": storm,
            "total_at_risk": crit + high + tier2_crit + tier2_high,
            "critical_count": crit, "high_count": high,
            "tier2_critical_count": tier2_crit, "tier2_high_count": tier2_high,
            "fleet_critical_count": crit + tier2_crit, "fleet_high_count": high + tier2_high,
            "fleet_monitored": sat_db.fleet_count(), "tier1": t1, "tier2": t2, "tier3": t3,
            "tier3_count": len(sat_db.tier3),
            "fleet_summary": {"highest_risk_satellite": best.get("name", "N/A"),
                              "highest_risk_score": best.get("risk_scores", {}).get("composite_final", 0),
                              "defense_satellites_at_risk": def_risk,
                              "navigation_system_status": nav.get("nav_system_status", "NOMINAL"),
                              "nav_error_meters": nav.get("nav_error_meters", 0),
                              "total_economic_value_at_risk_crore": val}}


# ═════════════════════════════════════════════════════════════════════════
# DB persistence + WebSocket alerts
# ═════════════════════════════════════════════════════════════════════════

def save_satellite_risks_to_db(risks: dict) -> None:
    try:
        from app.database.db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                ts, kp = risks["computed_at_utc"], risks["kp_used"]
                for name, r in risks.get("tier1", {}).items():
                    sc, sm = r["risk_scores"], r["safe_mode_countdown"]
                    cur.execute(
                        "INSERT INTO satellite_risk_history (computed_at_utc,satellite_name,kp_used,"
                        "drag_risk,charging_risk,radiation_risk,composite_final,risk_level,"
                        "safe_mode_required,safe_mode_minutes,recommended_action) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (ts, name, kp, sc["drag_risk"], sc["charging_risk"], sc["radiation_risk"],
                         sc["composite_final"], r["risk_level"], 1 if sm.get("safe_mode_required") else 0,
                         sm.get("safe_mode_deadline_minutes"), r["recommended_action"][:500]))
    except Exception as e:
        logger.error("Satellite risks DB save failed: %s", e)


def check_and_emit_satellite_alerts(new: dict, prev: dict) -> None:
    if not prev: return
    try:
        from app import socketio
    except Exception:
        return
    for name, nr in new.get("tier1", {}).items():
        pr = prev.get("tier1", {}).get(name, {})
        pl, nl = pr.get("risk_level", "MINIMAL"), nr.get("risk_level", "MINIMAL")
        if nl != pl:
            socketio.emit(WS_EVENT_SATELLITE_RISK_CHANGE, {
                "satellite_name": name, "previous_level": pl, "new_level": nl,
                "composite_risk": nr["risk_scores"]["composite_final"],
                "primary_threat": nr["primary_threat"],
                "recommended_action": nr["recommended_action"],
                "timestamp_utc": new["computed_at_utc"]})
            logger.warning("THRESHOLD CROSSED: %s %s → %s composite=%.0f Kp_used=%.1f",
                           name, pl, nl, nr["risk_scores"]["composite_final"], new["kp_used"])
