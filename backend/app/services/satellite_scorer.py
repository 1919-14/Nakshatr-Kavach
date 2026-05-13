"""Layer 4: ISRO satellite risk scoring (physics-weighted heuristics)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from app.services.physics import storm_class_from_kp

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "isro_satellites.json"


def _kp_scale(kp: float) -> float:
    return max(0.0, min(1.0, (kp - 2.0) / 6.0))


def score_satellites(kp_now: float, kp_max_horizon: float) -> List[Dict[str, Any]]:
    kp_eff = max(kp_now, kp_max_horizon * 0.85)
    s = _kp_scale(kp_eff)
    raw = _CATALOG_PATH.read_text(encoding="utf-8") if _CATALOG_PATH.exists() else "[]"
    cats = json.loads(raw)
    out: List[Dict[str, Any]] = []
    for sat in cats:
        orbit = (sat.get("type") or "LEO").upper()
        alt = float(sat.get("altitude_km") or 600)
        crit = float(sat.get("criticality", 1.0))

        drag = 0.0
        charge = 0.0
        rad = 40.0 + 35.0 * s
        if orbit in ("LEO", "SSO"):
            h = max(200.0, 900.0 - alt)
            drag = min(100.0, 25.0 + 55.0 * s * (600.0 / max(alt, 300.0)) + h * 0.02)
            charge = 5.0 * s
        elif orbit in ("GEO", "IGSO", "MEO"):
            drag = 2.0 * s
            charge = min(100.0, 30.0 + 60.0 * s)
            rad = 35.0 + 30.0 * s
        else:
            drag = 10.0 * s
            charge = 25.0 * s

        if orbit in ("LEO", "SSO"):
            w = (0.55, 0.15, 0.30)
        elif orbit in ("GEO", "IGSO"):
            w = (0.1, 0.55, 0.35)
        else:
            w = (0.35, 0.25, 0.40)
        comp = crit * (w[0] * drag + w[1] * charge + w[2] * rad)
        comp = float(min(100.0, max(0.0, comp)))

        if comp >= 80:
            lvl = "CRITICAL"
        elif comp >= 60:
            lvl = "HIGH"
        elif comp >= 40:
            lvl = "MODERATE"
        else:
            lvl = "LOW"

        safe_m = int(max(0.0, 90.0 - comp)) if comp >= 55 else None
        action = (
            f"Initiate safe mode in {safe_m} minutes — {storm_class_from_kp(kp_eff)} conditions"
            if safe_m is not None
            else "Maintain enhanced monitoring; no immediate safe mode"
        )

        out.append(
            {
                "id": sat["id"],
                "name": sat["name"],
                "shortName": sat.get("shortName", sat["name"][:4]),
                "type": orbit,
                "altitude": int(alt),
                "inclination": float(sat.get("inclination_deg", 0)),
                "mission": sat.get("mission", ""),
                "drag_risk": int(round(drag)),
                "charging_risk": int(round(charge)),
                "radiation_risk": int(round(rad)),
                "composite_risk": int(round(comp)),
                "risk_level": lvl,
                "action": action,
                "safe_mode_minutes": safe_m,
                "orbit_color": sat.get("orbit_color", "#00D4FF"),
            }
        )
    out.sort(key=lambda x: -x["composite_risk"])
    return out
