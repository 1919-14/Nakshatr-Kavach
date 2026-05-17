# backend/app/services/kp_utils.py
"""
NAKSHATRA-KAVACH Layer 3: Kp classification, storm utilities, and SHAP analysis.

Pure functions with no model dependencies — imported by kp_predictor.py
and the REST routes. Never calls external APIs or modifies upstream data.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np

from app.utils.constants import (
    KP_STORM_THRESHOLDS,
    N_MC_SAMPLES,
    STORM_COLORS,
    UNCERTAINTY_MULTIPLIERS,
    XGB_LSTM_WEIGHTS,
)

logger = logging.getLogger(__name__)

# ── Storm classification ────────────────────────────────────────────────────

def classify_kp_to_storm(kp: Optional[float]) -> str:
    """Map continuous Kp value to NOAA storm class string."""
    if kp is None or (isinstance(kp, float) and math.isnan(kp)):
        return "UNKNOWN"
    kp = float(kp)
    if kp >= 9.0: return "G5"
    if kp >= 8.0: return "G4"
    if kp >= 7.0: return "G3"
    if kp >= 6.0: return "G2"
    if kp >= 5.0: return "G1"
    return "QUIET"


def storm_class_to_color(storm_class: str) -> str:
    """Map storm class to dashboard hex color."""
    return STORM_COLORS.get(storm_class, "#9E9E9E")


def storm_class_to_numeric(storm_class: str) -> int:
    """Map storm class to integer 0-5 for plotting."""
    return {"QUIET": 0, "G1": 1, "G2": 2, "G3": 3, "G4": 4, "G5": 5}.get(storm_class, 0)


def compute_peak_storm_class(fused_predictions: Dict[str, Any]) -> str:
    """Find the worst storm class across all forecast horizons."""
    order = ["QUIET", "G1", "G2", "G3", "G4", "G5"]
    classes = [fused_predictions[h]["storm_class"] for h in ["3hr", "6hr", "12hr", "24hr"]]
    return max(classes, key=lambda c: order.index(c) if c in order else -1)


def compute_overall_storm_probability(fused_predictions: Dict[str, Any]) -> float:
    """Probability of any storm (≥G1) within 12 hours — headline dashboard number."""
    p_values = [
        fused_predictions["3hr"]["p_storm_g1"],
        fused_predictions["6hr"]["p_storm_g1"],
        fused_predictions["12hr"]["p_storm_g1"],
    ]
    return round(max(p_values), 3)


def compute_prediction_confidence(fused_predictions: Dict[str, Any], data_quality: str) -> str:
    """Overall confidence level for the entire prediction."""
    avg_std = float(np.mean([fused_predictions[h]["uncertainty"] for h in ["3hr", "6hr", "12hr", "24hr"]]))
    avg_agreement = float(np.mean([fused_predictions[h]["model_agreement"] for h in ["3hr", "6hr", "12hr", "24hr"]]))
    if data_quality == "STALE":
        return "LOW"
    if avg_std > 1.5 or avg_agreement < 0.5:
        return "LOW"
    if avg_std > 0.8 or avg_agreement < 0.75:
        return "MODERATE"
    return "HIGH"


def compute_recommended_action(fused: Dict[str, Any]) -> str:
    """Determine recommended action level from fused predictions."""
    peak_kp = max(fused[h]["kp"] for h in fused)
    p_g3_12hr = fused["12hr"]["p_storm_g3"]
    if peak_kp >= 8 or p_g3_12hr > 0.7:
        return "ACT_NOW"
    if peak_kp >= 6 or p_g3_12hr > 0.3:
        return "PREPARE"
    if peak_kp >= 5:
        return "WATCH"
    return "MONITOR"


def detect_storm_onset(current_kp: float, fused_3hr_kp: float, feature_metadata: Dict[str, Any]) -> bool:
    """Detect if a storm is beginning RIGHT NOW."""
    bz_onset = feature_metadata.get("summary", {}).get("storm_onset_detected", False)
    kp_rising = fused_3hr_kp > current_kp + 1.0
    crossing_g1 = current_kp < 5.0 and fused_3hr_kp >= 5.0
    return bool(bz_onset and (kp_rising or crossing_g1))


# ── MC Dropout uncertainty ──────────────────────────────────────────────────

def predict_with_uncertainty(lstm_model: Any, sequence: np.ndarray, n_samples: int = N_MC_SAMPLES) -> Dict[str, Any]:
    """
    Run Monte Carlo Dropout inference on the LSTM model.
    Keeps dropout active (training=True) to produce a distribution of predictions.
    """
    sequence_batch = np.repeat(sequence, n_samples, axis=0)  # (N, 24, 15)
    predictions = lstm_model(sequence_batch, training=True).numpy()  # (N, 4)
    predictions = np.clip(predictions, 0.0, 9.0)

    results: Dict[str, Any] = {}
    for i, (name, hours) in enumerate(zip(["3hr", "6hr", "12hr", "24hr"], [3, 6, 12, 24])):
        samples = predictions[:, i]
        results[f"kp_{name}"] = {
            "mean": round(float(np.mean(samples)), 2),
            "std": round(float(np.std(samples)), 2),
            "p5": round(float(np.percentile(samples, 5)), 2),
            "p95": round(float(np.percentile(samples, 95)), 2),
            "storm_class": classify_kp_to_storm(float(np.mean(samples))),
            "p_storm_g1": round(float(np.mean(samples >= 5.0)), 3),
            "p_storm_g2": round(float(np.mean(samples >= 6.0)), 3),
            "p_storm_g3": round(float(np.mean(samples >= 7.0)), 3),
            "p_storm_g4": round(float(np.mean(samples >= 8.0)), 3),
            "p_storm_g5": round(float(np.mean(samples >= 9.0)), 3),
            "mc_samples": samples.tolist(),
            "forecast_horizon_hours": hours,
        }
    return results


# ── Hybrid fusion engine ────────────────────────────────────────────────────

def fuse_predictions(xgb_preds: Dict[str, float], lstm_preds: Dict[str, Any], data_quality: str) -> Dict[str, Any]:
    """
    Combine XGBoost and LSTM predictions using horizon-aware weighting.
    Applies quality-based confidence adjustment when data is stale.
    """
    fused: Dict[str, Any] = {}
    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        w_xgb = XGB_LSTM_WEIGHTS[horizon]["xgb"]
        w_lstm = XGB_LSTM_WEIGHTS[horizon]["lstm"]
        xgb_kp = xgb_preds[horizon]
        lstm_kp = lstm_preds[f"kp_{horizon}"]["mean"]
        lstm_std = lstm_preds[f"kp_{horizon}"]["std"]

        fused_kp = float(np.clip(w_xgb * xgb_kp + w_lstm * lstm_kp, 0.0, 9.0))

        uncertainty_multiplier = UNCERTAINTY_MULTIPLIERS.get(data_quality, 1.5)
        adjusted_std = lstm_std * uncertainty_multiplier

        xgb_lstm_disagreement = abs(xgb_kp - lstm_kp)
        if xgb_lstm_disagreement > 2.0:
            adjusted_std *= 1.5

        adjusted_p5 = float(np.clip(fused_kp - 1.645 * adjusted_std, 0.0, 9.0))
        adjusted_p95 = float(np.clip(fused_kp + 1.645 * adjusted_std, 0.0, 9.0))

        fused[horizon] = {
            "kp": round(fused_kp, 2),
            "uncertainty": round(adjusted_std, 2),
            "ci_lower_90": round(adjusted_p5, 2),
            "ci_upper_90": round(adjusted_p95, 2),
            "storm_class": classify_kp_to_storm(fused_kp),
            "storm_class_color": storm_class_to_color(classify_kp_to_storm(fused_kp)),
            "xgb_component": round(xgb_kp, 2),
            "lstm_component": round(lstm_kp, 2),
            "model_agreement": round(1.0 - min(xgb_lstm_disagreement / 9.0, 1.0), 2),
            "p_storm_g1": lstm_preds[f"kp_{horizon}"]["p_storm_g1"],
            "p_storm_g2": lstm_preds[f"kp_{horizon}"]["p_storm_g2"],
            "p_storm_g3": lstm_preds[f"kp_{horizon}"]["p_storm_g3"],
            "p_storm_g4": lstm_preds[f"kp_{horizon}"]["p_storm_g4"],
            "p_storm_g5": lstm_preds[f"kp_{horizon}"]["p_storm_g5"],
            "mc_samples": lstm_preds[f"kp_{horizon}"]["mc_samples"],
            "forecast_horizon_hours": int(horizon.replace("hr", "")),
        }
    return fused


# ── SHAP explainability ─────────────────────────────────────────────────────

_SHAP_EXPLANATIONS: Dict[str, str] = {
    "bz_current": "Southward Bz of {val:.1f} nT is the primary storm driver",
    "bz_mean_1hr": "Sustained southward Bz (avg {val:.1f} nT over 1hr) is driving the prediction",
    "epsilon_current": "High solar wind-magnetosphere coupling (epsilon={val:.1f}) indicates strong energy transfer",
    "epsilon_mean_1hr": "High epsilon coupling (avg {val:.1f} over 1hr) indicates sustained energy transfer",
    "sw_speed_mean_1hr": "Elevated solar wind speed ({val:.0f} km/s) is amplifying storm conditions",
    "cme_is_imminent": "Earth-directed CME arrival within 6 hours is the dominant risk factor",
    "kp_current": "Already-elevated Kp of {val:.1f} indicates the magnetosphere is disturbed",
    "consecutive_southward_minutes": "Bz has been southward for {val:.0f} consecutive minutes",
    "dynamic_pressure_mean_1hr": "High dynamic pressure ({val:.1f} nPa) is compressing the magnetosphere",
    "bz_min_1hr": "Deep southward Bz excursion ({val:.1f} nT) in the last hour is driving coupling",
}


class ShapAnalyzer:
    """Manages SHAP explainability for XGBoost Kp predictions."""

    def __init__(self) -> None:
        self.explainers: Dict[str, Any] = {}
        self.feature_names: List[str] = []

    def compute_shap(self, horizon: str, xgb_vector_raw: np.ndarray, xgb_vector_scaled: np.ndarray) -> Dict[str, Any]:
        """Compute SHAP values for one horizon and return structured output."""
        explainer = self.explainers.get(horizon)
        if explainer is None:
            return {"horizon": horizon, "base_value": 0.0, "predicted_kp": 0.0,
                    "top_features": [], "all_features": [], "storm_drivers": [],
                    "storm_dampeners": [], "dominant_driver": "SHAP unavailable for this horizon"}

        shap_values = explainer.shap_values(xgb_vector_scaled)
        shap_vals = shap_values[0] if len(shap_values.shape) > 1 else shap_values

        raw_row = xgb_vector_raw[0] if xgb_vector_raw.ndim > 1 else xgb_vector_raw
        features: List[Dict[str, Any]] = []
        for i, (name, shap_val) in enumerate(zip(self.feature_names, shap_vals)):
            raw_val = float(raw_row[i]) if i < len(raw_row) else 0.0
            features.append({
                "rank": 0,
                "feature_index": i,
                "feature_name": name,
                "raw_value": round(raw_val, 4),
                "shap_value": round(float(shap_val), 4),
                "abs_shap": round(abs(float(shap_val)), 4),
                "direction": "positive" if shap_val > 0 else "negative",
                "impact": "RAISES_KP" if shap_val > 0 else "LOWERS_KP",
            })

        features.sort(key=lambda x: x["abs_shap"], reverse=True)
        for rank, feat in enumerate(features):
            feat["rank"] = rank + 1

        base_value = float(explainer.expected_value)
        return {
            "horizon": horizon,
            "base_value": round(base_value, 2),
            "predicted_kp": round(base_value + sum(shap_vals), 2),
            "top_features": features[:10],
            "all_features": features,
            "storm_drivers": [f for f in features if f["direction"] == "positive"][:5],
            "storm_dampeners": [f for f in features if f["direction"] == "negative"][:5],
            "dominant_driver": self.get_dominant_driver({"top_features": features[:10]}),
        }

    def get_dominant_driver(self, shap_output: Dict[str, Any]) -> str:
        """Returns a one-sentence explanation of the top SHAP driver."""
        top_feats = shap_output.get("top_features", [])
        if not top_feats:
            return "No dominant driver identified"
        top = top_feats[0]
        name = top["feature_name"]
        val = top["raw_value"]
        template = _SHAP_EXPLANATIONS.get(name)
        if template:
            return template.format(val=val)
        return f"{name.replace('_', ' ')} (value={val:.2f}) is the primary prediction driver"


# ── Degraded mode fallback ──────────────────────────────────────────────────

def degraded_kp_estimate(feature_metadata: Dict[str, Any]) -> Dict[str, float]:
    """
    Simple rule-based Kp estimate when ML models are not loaded.
    Uses Bz and current Kp to produce rough estimates.
    """
    features = feature_metadata.get("features", [])
    bz_val = 0.0
    kp_val = 1.0
    for f in features:
        if f.get("name") == "bz_current":
            bz_val = float(f.get("value") or 0.0)
        elif f.get("name") == "kp_current":
            kp_val = float(f.get("value") or 1.0)

    base = min(9.0, max(0.0, abs(bz_val) * 0.35 + kp_val * 0.3))
    return {
        "3hr": round(min(9.0, max(0.0, base * 1.0)), 2),
        "6hr": round(min(9.0, max(0.0, base * 0.95)), 2),
        "12hr": round(min(9.0, max(0.0, base * 0.85)), 2),
        "24hr": round(min(9.0, max(0.0, base * 0.70)), 2),
    }
