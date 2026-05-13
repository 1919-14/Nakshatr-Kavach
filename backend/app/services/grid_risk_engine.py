"""Layer 5: India EHV corridor GIC-style risk (simplified Viljanen–Pirjola proxy)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

_GRID_PATH = Path(__file__).resolve().parent.parent / "data" / "india_grid_topology.json"


def _e_geo_v_per_km(kp: float) -> float:
    return 10.0 * ((max(kp, 0.0) / 5.0) ** 2)


def score_grid(kp_now: float, kp_horizon: float) -> List[Dict[str, Any]]:
    kp_eff = max(kp_now, kp_horizon * 0.9)
    e = _e_geo_v_per_km(kp_eff)
    raw = _GRID_PATH.read_text(encoding="utf-8") if _GRID_PATH.exists() else "[]"
    corridors = json.loads(raw)
    out: List[Dict[str, Any]] = []
    for c in corridors:
        L = float(c.get("length_km", 400))
        theta_deg = float(c.get("angle_from_ns_deg", 35))
        r_line = float(c.get("line_resistance_ohm", 4.0))
        gic = abs(e * L * math.sin(math.radians(theta_deg)) / max(r_line, 0.5))
        risk_pct = min(100.0, 35.0 + gic * 0.65 + kp_eff * 4.0)
        pop = float(c.get("population_millions", 2.0))
        impact = float(c.get("base_impact_crore", 80)) * (risk_pct / 55.0) ** 1.5
        action = "Routine monitoring"
        if gic > 80:
            action = "Reduce corridor loading ~20%; alert POSOCO"
        elif gic > 50:
            action = "Reduce load by ~12%; verify transformer neutrals"
        elif gic > 35:
            action = "Elevated GIC watch — thermal monitoring on key transformers"

        out.append(
            {
                "id": c["id"],
                "name": c["name"],
                "states": c.get("states", ""),
                "voltage": c.get("voltage", "400kV"),
                "coords": c.get("coords", []),
                "gic_amps": int(round(gic)),
                "risk_percent": int(round(risk_pct)),
                "impact_crore": int(round(impact)),
                "population_millions": round(pop * (0.8 + risk_pct / 250.0), 2),
                "action": action,
            }
        )
    out.sort(key=lambda x: -x["risk_percent"])
    return out
