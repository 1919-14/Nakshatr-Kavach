"""Assemble L1–L5 snapshot for API and WebSocket."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from app.services.data_ingestion import get_ingestion_service
from app.services.grid_risk_engine import score_grid
from app.services.kp_predictor import get_kp_predictor
from app.services.physics import snapshot_to_api_row
from app.services.satellite_scorer import score_satellites


def build_dashboard_payload() -> Dict[str, Any]:
    ing = get_ingestion_service()
    snap = ing.get_latest_snapshot()
    df = ing.get_historical_data(72)
    if df.empty and snap:
        df = ing.get_latest_dataframe()
    kp = get_kp_predictor()
    kp_pred = kp.predict(df if not df.empty else pd.DataFrame())
    k_now = float(snap.get("kp_current") or kp_pred.get("current_kp") or 2.0)
    fc = kp_pred.get("forecast") or {}
    k_max = max(
        k_now,
        float(fc.get("kp_12hr", {}).get("value") or k_now),
        float(fc.get("kp_24hr", {}).get("value") or k_now),
    )
    sats = score_satellites(k_now, k_max)
    grid = score_grid(k_now, float(fc.get("kp_12hr", {}).get("value") or k_now))
    shap = kp.shap_explain(df if not df.empty else pd.DataFrame())
    solar = snapshot_to_api_row(snap) if snap else {}
    return {
        "solar_wind": solar,
        "kp_forecast": kp_pred,
        "satellites": sats,
        "grid": grid,
        "shap": shap,
    }


def build_advisory_context() -> Dict[str, Any]:
    p = build_dashboard_payload()
    high_sats = [s for s in (p.get("satellites") or []) if float(s.get("composite_risk") or 0) >= 60]
    high_grid = [g for g in (p.get("grid") or []) if float(g.get("risk_percent") or 0) >= 70]
    return {
        "kp_now": p["kp_forecast"].get("current_kp"),
        "storm_class": p["kp_forecast"].get("storm_class"),
        "forecast": p["kp_forecast"].get("forecast"),
        "storm_probability": p["kp_forecast"].get("storm_probability"),
        "peak_arrival_minutes": p["kp_forecast"].get("peak_arrival_minutes"),
        "warning_minutes": (p.get("solar_wind") or {}).get("warning_minutes"),
        "solar": p["solar_wind"],
        "satellites": p["satellites"][:8],
        "grid": p["grid"][:6],
        "top_satellites_over_threshold": high_sats[:5],
        "top_corridors_over_threshold": high_grid[:5],
        "shap_top": (p["shap"].get("features") or [])[:6],
    }
