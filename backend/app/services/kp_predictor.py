# backend/app/services/kp_predictor.py
"""
NAKSHATRA-KAVACH Layer 3: Kp Prediction Engine — XGBoost + LSTM Hybrid.

This module is the CORE INTELLIGENCE of NAKSHATRA-KAVACH. It loads pre-trained
XGBoost and LSTM models, runs hybrid inference with Monte Carlo Dropout
uncertainty quantification, and produces calibrated Kp forecasts at 3/6/12/24
hour horizons with storm class probabilities and SHAP explainability.

Layer 3 CONSUMES from Layer 2 (feature_engineering):
  xgb_vector_scaled, lstm_sequence_scaled, xgb_vector_raw, feature_metadata, data_quality

Layer 3 PRODUCES for Layers 4, 5, 6, 8:
  LATEST_KP_FORECAST dict with point estimates, uncertainty, storm probabilities, SHAP.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from app.services.kp_utils import (
    ShapAnalyzer,
    classify_kp_to_storm,
    compute_overall_storm_probability,
    compute_peak_storm_class,
    compute_prediction_confidence,
    compute_recommended_action,
    degraded_kp_estimate,
    detect_storm_onset,
    fuse_predictions,
    predict_with_uncertainty,
    storm_class_to_color,
    storm_class_to_numeric,
)
from app.utils.constants import (
    LSTM_MODEL_PATH,
    N_MC_SAMPLES,
    SHAP_EXPLAINER_PATHS,
    XGB_LSTM_WEIGHTS,
    XGB_MODEL_PATHS,
)

logger = logging.getLogger(__name__)

# ── Thread-safe forecast state ──────────────────────────────────────────────

_forecast_lock = threading.RLock()

LATEST_KP_FORECAST: Dict[str, Any] = {
    "computed_at_utc": None,
    "data_quality_used": "UNKNOWN",
    "prediction_confidence": "LOW",
    "inference_time_ms": 0.0,
    "current": {"kp": 0.0, "storm_class": "QUIET", "storm_class_color": "#607D8B", "storm_class_numeric": 0},
    "forecast": {},
    "summary": {},
    "shap": None,
    "model_info": {
        "xgb_versions": {h: "v1.0" for h in ["3hr", "6hr", "12hr", "24hr"]},
        "lstm_version": "v1.0",
        "n_mc_samples": N_MC_SAMPLES,
        "fusion_weights": XGB_LSTM_WEIGHTS,
    },
}


def update_latest_kp_forecast(forecast: Dict[str, Any]) -> None:
    """Thread-safe atomic swap of the forecast dict."""
    global LATEST_KP_FORECAST
    new_copy = copy.deepcopy(forecast)
    with _forecast_lock:
        LATEST_KP_FORECAST = new_copy


def get_latest_kp_forecast() -> Dict[str, Any]:
    """Thread-safe read of the latest forecast."""
    with _forecast_lock:
        return copy.deepcopy(LATEST_KP_FORECAST)


# ── Custom loss for LSTM (needed for model loading) ─────────────────────────

def storm_weighted_huber(y_true: Any, y_pred: Any, delta: float = 1.0) -> Any:
    """
    Storm-weighted Huber loss: penalises errors during active storms (Kp≥5)
    much more heavily than quiet-time errors, because a 1-Kp miss at G4 is
    catastrophic for satellite operations while a 1-Kp miss at Kp=2 is irrelevant.
    """
    import tensorflow as tf
    storm_weight = tf.where(
        y_true >= 7.0,
        tf.ones_like(y_true) * 5.0,
        tf.where(
            y_true >= 5.0,
            tf.ones_like(y_true) * 3.0,
            tf.ones_like(y_true) * 1.0,
        ),
    )
    error = y_true - y_pred
    abs_error = tf.abs(error)
    quadratic = tf.minimum(abs_error, delta)
    linear = abs_error - quadratic
    base_loss = 0.5 * quadratic ** 2 + delta * linear
    weighted_loss = storm_weight * base_loss
    return tf.reduce_mean(weighted_loss)


# ── LSTM model builder ──────────────────────────────────────────────────────

def build_lstm_model(sequence_length: int = 24, n_features: int = 15,
                     n_outputs: int = 4, dropout_rate: float = 0.25) -> Any:
    """
    Build the Bidirectional LSTM model for multi-horizon Kp prediction.
    Output uses sigmoid * 9 to constrain predictions to physical Kp range [0, 9].
    """
    import tensorflow as tf
    from tensorflow.keras.layers import (
        LSTM, BatchNormalization, Bidirectional, Dense, Dropout, Input,
    )
    from tensorflow.keras.models import Model
    from tensorflow.keras.regularizers import l2

    inputs = Input(shape=(sequence_length, n_features), name="solar_sequence")
    x = Bidirectional(LSTM(128, return_sequences=True, kernel_regularizer=l2(1e-4)), name="bilstm_1")(inputs)
    x = Dropout(dropout_rate, name="dropout_1")(x)
    x = BatchNormalization(name="bn_1")(x)
    x = LSTM(64, return_sequences=True, kernel_regularizer=l2(1e-4), name="lstm_2")(x)
    x = Dropout(dropout_rate, name="dropout_2")(x)
    x = BatchNormalization(name="bn_2")(x)
    x = LSTM(32, return_sequences=False, kernel_regularizer=l2(1e-4), name="lstm_3")(x)
    x = Dropout(dropout_rate, name="dropout_3")(x)
    x = Dense(64, activation="relu", name="dense_1")(x)
    x = Dropout(dropout_rate * 0.5, name="dropout_4")(x)
    x = Dense(32, activation="relu", name="dense_2")(x)
    outputs = Dense(n_outputs, activation=lambda v: tf.keras.activations.sigmoid(v) * 9.0, name="kp_outputs")(x)
    model = Model(inputs=inputs, outputs=outputs, name="nakshatra_lstm")
    return model


# ── Model Loader ────────────────────────────────────────────────────────────

class ModelLoader:
    """Loads and manages all Layer 3 models at application startup."""

    def __init__(self) -> None:
        self.xgb_models: Dict[str, Any] = {}
        self.shap_explainers: Dict[str, Any] = {}
        self.lstm_model: Any = None
        self.models_loaded: bool = False
        self.degraded_mode: bool = False
        self._shap_analyzer = ShapAnalyzer()

    def load_all(self) -> None:
        """Load all models. Sets degraded_mode=True if models are missing."""
        import joblib
        start = time.time()
        logger.info("Loading NAKSHATRA-KAVACH prediction models...")

        xgb_ok = self._load_xgb_models()
        lstm_ok = self._load_lstm_model()
        self._load_shap_explainers(joblib)
        self._init_shap_analyzer()

        if not xgb_ok and not lstm_ok:
            self.degraded_mode = True
            logger.warning("DEGRADED_MODE: No ML models found. Using rule-based fallback.")
        elif not xgb_ok or not lstm_ok:
            self.degraded_mode = True
            logger.warning("DEGRADED_MODE: Partial model load — some models missing.")
        else:
            self.degraded_mode = False

        self.models_loaded = True
        elapsed = (time.time() - start) * 1000
        logger.info("Model loading complete in %.0fms | degraded=%s", elapsed, self.degraded_mode)

    def _load_xgb_models(self) -> bool:
        try:
            import xgboost as xgb
        except ImportError:
            logger.warning("xgboost not installed — XGBoost models unavailable")
            return False
        loaded = 0
        for horizon in ["3hr", "6hr", "12hr", "24hr"]:
            path = XGB_MODEL_PATHS[horizon]
            if os.path.exists(path):
                model = xgb.XGBRegressor()
                model.load_model(path)
                self.xgb_models[horizon] = model
                loaded += 1
                logger.info("Loaded XGBoost %s model from %s", horizon, path)
            else:
                logger.warning("XGBoost model not found: %s", path)
        return loaded == 4

    def _load_lstm_model(self) -> bool:
        # Support both PyTorch (.pt) and Keras (.keras) model files
        pt_path = str(LSTM_MODEL_PATH).replace(".keras", ".pt")
        load_path = pt_path if os.path.exists(pt_path) else LSTM_MODEL_PATH
        if not os.path.exists(load_path):
            logger.warning("LSTM model not found: %s", load_path)
        try:
            if str(load_path).endswith(".pt"):
                import torch, importlib, sys
                from pathlib import Path
                root_dir = str(Path(__file__).resolve().parents[3])
                if root_dir not in sys.path:
                    sys.path.insert(0, root_dir)
                _lstm_mod = importlib.import_module("ml_training.04_train_lstm")
                NakshatraLSTM = _lstm_mod.NakshatraLSTM
                ckpt = torch.load(load_path, map_location="cpu", weights_only=False)
                n_features = ckpt.get("n_features", 72)
                seq_len    = ckpt.get("seq_len", 24)
                model = NakshatraLSTM(n_features=n_features, seq_len=seq_len)
                model.load_state_dict(ckpt["model_state_dict"])
                model.eval()
                self.lstm_model = model
                self._lstm_backend = "pytorch"
                self._lstm_n_features = n_features
                self._lstm_seq_len = seq_len
                logger.info("PyTorch LSTM loaded from %s (features=%d)", load_path, n_features)
            else:
                import tensorflow as tf
                self.lstm_model = tf.keras.models.load_model(
                    load_path,
                    custom_objects={"storm_weighted_huber": storm_weighted_huber},
                )
                self._lstm_backend = "tensorflow"
                self._lstm_n_features = 72
                self._lstm_seq_len = 24
                dummy = np.zeros((1, 24, self._lstm_n_features), dtype=np.float32)
                _ = self.lstm_model(dummy, training=True)
                logger.info("TF LSTM loaded from %s", load_path)
            return True
        except Exception as e:
            logger.warning("LSTM load failed: %s", e)
            return False

    def _load_shap_explainers(self, joblib: Any) -> None:
        try:
            import shap
        except ImportError:
            logger.warning("shap not installed — explainability unavailable")
            return
            
        for horizon in ["3hr", "6hr", "12hr", "24hr"]:
            if horizon in self.xgb_models:
                try:
                    self.shap_explainers[horizon] = shap.TreeExplainer(self.xgb_models[horizon])
                    logger.info("Initialized SHAP explainer for %s", horizon)
                except Exception as e:
                    logger.warning("SHAP explainer init failed for %s: %s", horizon, e)

    def _init_shap_analyzer(self) -> None:
        from app.services.feature_engineering import FEATURE_NAMES
        self._shap_analyzer.feature_names = list(FEATURE_NAMES)
        self._shap_analyzer.explainers = self.shap_explainers

    @property
    def shap_analyzer(self) -> ShapAnalyzer:
        return self._shap_analyzer

    def are_loaded(self) -> bool:
        return self.models_loaded


# Module-level singleton
model_loader = ModelLoader()


# ── Inference cycle ─────────────────────────────────────────────────────────

def run_inference_cycle(features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full Layer 3 inference: XGB → LSTM MC Dropout → Fusion → SHAP.
    Returns the complete LATEST_KP_FORECAST structure.
    """
    from app.utils.formatters import utcnow_iso

    t_start = time.perf_counter()
    xgb_vector_scaled = features["xgb_vector_scaled"]
    lstm_seq_scaled = features["lstm_sequence_scaled"]
    xgb_vector_raw = features["xgb_vector_raw"]
    feature_metadata = features["feature_metadata"]
    data_quality = features["data_quality"]

    # Ensure numpy arrays
    if not isinstance(xgb_vector_scaled, np.ndarray):
        xgb_vector_scaled = np.array(xgb_vector_scaled, dtype=np.float64)
    if not isinstance(lstm_seq_scaled, np.ndarray):
        lstm_seq_scaled = np.array(lstm_seq_scaled, dtype=np.float32)
    if not isinstance(xgb_vector_raw, np.ndarray):
        xgb_vector_raw = np.array(xgb_vector_raw, dtype=np.float64)

    # Ensure correct shapes
    if lstm_seq_scaled.ndim == 2:
        lstm_seq_scaled = lstm_seq_scaled.reshape(1, lstm_seq_scaled.shape[0], lstm_seq_scaled.shape[1])

    # Extract current Kp from feature metadata
    fm_features = feature_metadata.get("features", [])
    current_kp = 0.0
    for f in fm_features:
        if f.get("name") == "kp_current":
            current_kp = float(f.get("value") or 0.0)
            break

    # ── XGBoost predictions ──
    t_xgb = time.perf_counter()
    if model_loader.xgb_models:
        xgb_preds: Dict[str, float] = {}
        for horizon in ["3hr", "6hr", "12hr", "24hr"]:
            if horizon in model_loader.xgb_models:
                raw_pred = model_loader.xgb_models[horizon].predict(xgb_vector_scaled.reshape(1, -1))[0]
                xgb_preds[horizon] = float(np.clip(raw_pred, 0.0, 9.0))
            else:
                xgb_preds[horizon] = current_kp
    else:
        xgb_preds = degraded_kp_estimate(feature_metadata)
    xgb_ms = (time.perf_counter() - t_xgb) * 1000

    # ── LSTM MC Dropout ──
    t_lstm = time.perf_counter()
    if model_loader.lstm_model is not None:
        backend = getattr(model_loader, "_lstm_backend", "tensorflow")
        if backend == "pytorch":
            lstm_preds = _predict_pytorch_lstm(model_loader.lstm_model, lstm_seq_scaled, n_samples=N_MC_SAMPLES)
        else:
            lstm_preds = predict_with_uncertainty(model_loader.lstm_model, lstm_seq_scaled, n_samples=N_MC_SAMPLES)
    else:
        # Degraded: synthesise LSTM-like output from XGBoost predictions
        lstm_preds = _synthesise_lstm_preds(xgb_preds)
    lstm_ms = (time.perf_counter() - t_lstm) * 1000

    # ── Hybrid fusion ──
    fused = fuse_predictions(xgb_preds, lstm_preds, data_quality)

    # ── SHAP analysis ──
    t_shap = time.perf_counter()
    shap_output = None
    if model_loader.shap_explainers.get("6hr"):
        try:
            shap_output = model_loader.shap_analyzer.compute_shap(
                "6hr", xgb_vector_raw.reshape(1, -1), xgb_vector_scaled.reshape(1, -1))
        except Exception as e:
            logger.warning("SHAP computation failed: %s", e)
    shap_ms = (time.perf_counter() - t_shap) * 1000

    total_ms = (time.perf_counter() - t_start) * 1000

    # ── Assemble forecast ──
    forecast: Dict[str, Any] = {
        "computed_at_utc": utcnow_iso(),
        "data_quality_used": data_quality,
        "prediction_confidence": compute_prediction_confidence(fused, data_quality),
        "inference_time_ms": round(total_ms, 1),
        "current": {
            "kp": current_kp,
            "storm_class": classify_kp_to_storm(current_kp),
            "storm_class_color": storm_class_to_color(classify_kp_to_storm(current_kp)),
            "storm_class_numeric": storm_class_to_numeric(classify_kp_to_storm(current_kp)),
        },
        "forecast": fused,
        "summary": {
            "peak_storm_class": compute_peak_storm_class(fused),
            "peak_storm_class_color": storm_class_to_color(compute_peak_storm_class(fused)),
            "peak_kp_24hr": round(max(fused[h]["kp"] for h in fused), 2),
            "peak_horizon": max(fused, key=lambda h: fused[h]["kp"]),
            "storm_probability_12hr": compute_overall_storm_probability(fused),
            "storm_onset_detected": detect_storm_onset(current_kp, fused["3hr"]["kp"], feature_metadata),
            "storm_imminent": (fused["3hr"]["p_storm_g1"] > 0.7 or fused["6hr"]["p_storm_g3"] > 0.3),
            "recommended_action_level": compute_recommended_action(fused),
            "transit_warning_minutes": feature_metadata.get("summary", {}).get("transit_warning_minutes", 60.0),
            "cme_active": feature_metadata.get("summary", {}).get("cme_active", False),
            "dominant_driver": shap_output.get("dominant_driver", "Analysis unavailable") if shap_output else "Analysis unavailable",
        },
        "shap": shap_output,
        "model_info": {
            "xgb_versions": {h: "v1.0" for h in ["3hr", "6hr", "12hr", "24hr"]},
            "lstm_version": "v1.0",
            "n_mc_samples": N_MC_SAMPLES,
            "fusion_weights": XGB_LSTM_WEIGHTS,
            "degraded_mode": model_loader.degraded_mode,
        },
    }

    logger.info(
        "Inference: XGB=%.0fms LSTM=%.0fms SHAP=%.0fms Total=%.0fms | "
        "Kp3hr=%.1f Kp6hr=%.1f PeakClass=%s Confidence=%s",
        xgb_ms, lstm_ms, shap_ms, total_ms,
        fused["3hr"]["kp"], fused["6hr"]["kp"],
        forecast["summary"]["peak_storm_class"],
        forecast["prediction_confidence"],
    )
    if total_ms > 1000:
        logger.warning("Inference exceeded 1000ms target: %.0fms", total_ms)

    return forecast


def _predict_pytorch_lstm(model: Any, lstm_seq: np.ndarray,
                           n_samples: int = 50) -> Dict[str, Any]:
    """
    PyTorch MC-Dropout inference: runs the model n_samples times with
    dropout enabled to produce uncertainty estimates. Produces the same
    output dict format as predict_with_uncertainty (TensorFlow version).
    """
    import torch
    x = torch.from_numpy(lstm_seq.astype(np.float32))
    if x.ndim == 2:
        x = x.unsqueeze(0)   # (1, seq_len, n_features)
    # MC Dropout — keep dropout active during inference
    model.train()
    all_preds = []
    with torch.no_grad():
        for _ in range(n_samples):
            pred = model(x).cpu().numpy()   # (1, 4)
            all_preds.append(np.clip(pred[0], 0.0, 9.0))
    model.eval()

    all_preds = np.array(all_preds)  # (n_samples, 4)
    results: Dict[str, Any] = {}
    for i, (name, hours) in enumerate(zip(["3hr", "6hr", "12hr", "24hr"], [3, 6, 12, 24])):
        samples = all_preds[:, i]
        from app.services.kp_utils import classify_kp_to_storm as _cls
        results[f"kp_{name}"] = {
            "mean": round(float(np.mean(samples)), 2),
            "std":  round(float(np.std(samples)), 2),
            "p5":   round(float(np.percentile(samples, 5)), 2),
            "p95":  round(float(np.percentile(samples, 95)), 2),
            "storm_class": _cls(float(np.mean(samples))),
            "p_storm_g1": round(float(np.mean(samples >= 5.0)), 3),
            "p_storm_g2": round(float(np.mean(samples >= 6.0)), 3),
            "p_storm_g3": round(float(np.mean(samples >= 7.0)), 3),
            "p_storm_g4": round(float(np.mean(samples >= 8.0)), 3),
            "p_storm_g5": round(float(np.mean(samples >= 9.0)), 3),
            "mc_samples": samples.tolist(),
            "forecast_horizon_hours": hours,
        }
    return results


def _synthesise_lstm_preds(xgb_preds: Dict[str, float]) -> Dict[str, Any]:
    """Create synthetic LSTM-like output when the LSTM model is unavailable."""
    results: Dict[str, Any] = {}
    for name, hours in zip(["3hr", "6hr", "12hr", "24hr"], [3, 6, 12, 24]):
        base = xgb_preds[name]
        noise = np.random.normal(0, 0.3, 50)
        samples = np.clip(base + noise, 0.0, 9.0)
        from app.services.kp_utils import classify_kp_to_storm as _cls
        results[f"kp_{name}"] = {
            "mean": round(float(np.mean(samples)), 2),
            "std": round(float(np.std(samples)), 2),
            "p5": round(float(np.percentile(samples, 5)), 2),
            "p95": round(float(np.percentile(samples, 95)), 2),
            "storm_class": _cls(float(np.mean(samples))),
            "p_storm_g1": round(float(np.mean(samples >= 5.0)), 3),
            "p_storm_g2": round(float(np.mean(samples >= 6.0)), 3),
            "p_storm_g3": round(float(np.mean(samples >= 7.0)), 3),
            "p_storm_g4": round(float(np.mean(samples >= 8.0)), 3),
            "p_storm_g5": round(float(np.mean(samples >= 9.0)), 3),
            "mc_samples": samples.tolist(),
            "forecast_horizon_hours": hours,
        }
    return results


# ── Database persistence ────────────────────────────────────────────────────

def save_forecast_to_db(forecast: Dict[str, Any]) -> None:
    """Persist the forecast to kp_forecast_history table."""
    try:
        from app.database.db import get_db
        fcast = forecast.get("forecast", {})
        summary = forecast.get("summary", {})
        shap_data = forecast.get("shap", {}) or {}
        top_feats = shap_data.get("top_features", [])

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO kp_forecast_history (
                        computed_at_utc, kp_current, storm_class_current,
                        kp_forecast_3hr, kp_forecast_6hr, kp_forecast_12hr, kp_forecast_24hr,
                        uncertainty_3hr, uncertainty_6hr, uncertainty_12hr, uncertainty_24hr,
                        peak_storm_class, storm_probability_12hr, prediction_confidence,
                        inference_time_ms, data_quality_used, shap_top_feature, shap_top_value
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        forecast.get("computed_at_utc"),
                        forecast.get("current", {}).get("kp"),
                        forecast.get("current", {}).get("storm_class"),
                        fcast.get("3hr", {}).get("kp"),
                        fcast.get("6hr", {}).get("kp"),
                        fcast.get("12hr", {}).get("kp"),
                        fcast.get("24hr", {}).get("kp"),
                        fcast.get("3hr", {}).get("uncertainty"),
                        fcast.get("6hr", {}).get("uncertainty"),
                        fcast.get("12hr", {}).get("uncertainty"),
                        fcast.get("24hr", {}).get("uncertainty"),
                        summary.get("peak_storm_class"),
                        summary.get("storm_probability_12hr"),
                        forecast.get("prediction_confidence"),
                        forecast.get("inference_time_ms"),
                        forecast.get("data_quality_used"),
                        top_feats[0]["feature_name"] if top_feats else None,
                        top_feats[0]["shap_value"] if top_feats else None,
                    ),
                )
    except Exception as exc:
        logger.error("Failed to save forecast to DB: %s", exc)
