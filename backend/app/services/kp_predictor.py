"""Layer 3: Hybrid Kp forecast (XGBoost + sequence surrogate/LSTM) + SHAP on XGB."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.services.feature_engineering import build_sequence_tensor, build_tabular_feature_vector
from app.services.physics import storm_class_from_kp

logger = logging.getLogger(__name__)

MODEL_DIRS = [
    Path(__file__).resolve().parent.parent / "models",
    Path(__file__).resolve().parent.parent / "ml_models",
]
XGB_CANDIDATES = ["xgb_kp_model.pkl", "xgb_kp_multi.joblib"]
LSTM_CANDIDATES = ["lstm_kp_model.h5", "lstm_kp.keras"]

try:
    MC_DROPOUT_PASSES = int(os.environ.get("MC_DROPOUT_PASSES", "32"))
except (TypeError, ValueError):
    MC_DROPOUT_PASSES = 32

ROW7_NAMES = ["kp0", "bz", "v", "bt", "n", "south", "p_dyn"]


def _fusion_weight_xgb(hours: float) -> float:
    if hours <= 3:
        return 0.7
    if hours >= 24:
        return 0.2
    t = (hours - 3) / (24 - 3)
    return 0.7 + t * (0.2 - 0.7)


def _clip_kp(x: float) -> float:
    return float(max(0.0, min(9.0, x)))


def features_row7(df: pd.DataFrame) -> np.ndarray:
    """Physics-style 7-vector for XGB + SHAP (stable schema)."""
    if df is None or df.empty:
        return np.array([[2.0, 0.0, 400.0, 5.0, 5.0, 0.0, 1.0]], dtype=np.float32)
    last = df.sort_values("timestamp").iloc[-1]
    kp0 = float(last.get("kp_current") or 2.0)
    bz = float(last.get("bz_gsm") or 0.0)
    v = float(last.get("sw_speed_kmps") or 400.0)
    bt = float(last.get("bt_total") or 5.0)
    n = float(last.get("proton_density_ccm") or 5.0)
    south = max(0.0, -bz / 20.0)
    press = 0.5 * n * (v / 1000.0) ** 2
    return np.array([[kp0, bz, v, bt, n, south, press]], dtype=np.float32)


class KpPredictor:
    def __init__(self):
        for d in MODEL_DIRS:
            d.mkdir(parents=True, exist_ok=True)
        self._xgb = None
        self._xgb_bootstrap = False
        self._load_or_init_xgb()
        self._lstm = None
        self._load_lstm()

    def _first_existing(self, names: List[str]) -> Optional[Path]:
        for d in MODEL_DIRS:
            for n in names:
                p = d / n
                if p.exists():
                    return p
        return None

    def _load_or_init_xgb(self):
        import joblib
        import xgboost as xgb

        xgb_path = self._first_existing(XGB_CANDIDATES)
        if xgb_path is not None:
            self._xgb = joblib.load(xgb_path)
            logger.info("Loaded XGBoost bundle from %s", xgb_path)
            return
        self._xgb = self._bootstrap_xgb(xgb)
        target = MODEL_DIRS[0] / XGB_CANDIDATES[1]
        joblib.dump(self._xgb, target)
        self._xgb_bootstrap = True
        logger.info("Saved bootstrap XGBoost to %s", target)

    def _load_lstm(self):
        lstm_path = self._first_existing(LSTM_CANDIDATES)
        if lstm_path is None:
            return
        try:
            from tensorflow import keras

            self._lstm = keras.models.load_model(str(lstm_path))
            logger.info("Loaded LSTM from %s", lstm_path)
        except Exception as e:
            logger.warning("LSTM not loaded: %s", e)

    def _bootstrap_xgb(self, xgb_mod) -> Any:
        rng = np.random.default_rng(42)
        n = 800
        kp0 = rng.uniform(0, 6, n)
        bz = rng.normal(0, 6, n)
        v = rng.uniform(300, 700, n)
        bt = rng.uniform(3, 20, n)
        n_p = rng.uniform(2, 20, n)
        south = np.clip(-bz / 20.0, 0, 1.5)
        press = 0.5 * n_p * (v / 1000.0) ** 2
        y3 = kp0 + 0.45 * south + 0.02 * press + rng.normal(0, 0.35, n)
        y6 = kp0 + 0.65 * south + 0.03 * press + rng.normal(0, 0.45, n)
        y12 = kp0 + 0.8 * south + 0.035 * press + rng.normal(0, 0.55, n)
        y24 = kp0 + 0.9 * south + 0.04 * press + rng.normal(0, 0.65, n)
        X = np.column_stack([kp0, bz, v, bt, n_p, south, press]).astype(np.float32)
        y = np.column_stack([y3, y6, y12, y24])
        from sklearn.multioutput import MultiOutputRegressor

        base = xgb_mod.XGBRegressor(
            n_estimators=140,
            max_depth=5,
            learning_rate=0.07,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="reg:squarederror",
        )
        m = MultiOutputRegressor(base)
        m.fit(X, y)
        return m

    def _xgb_horizons(self, df: pd.DataFrame) -> np.ndarray:
        X = features_row7(df)
        pred = self._xgb.predict(X)
        pred = np.clip(np.asarray(pred, dtype=np.float32), 0, 9)
        if pred.ndim == 1:
            pred = pred.reshape(1, -1)
        return pred

    def _lstm_predict(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        if self._lstm is None:
            return None
        seq = build_sequence_tensor(df, 24)
        try:
            out = self._lstm.predict(seq, verbose=0)
            o = np.asarray(out, dtype=np.float32).reshape(1, -1)
            return np.clip(o, 0, 9)
        except Exception as e:
            logger.warning("LSTM predict failed: %s", e)
            return None

    def _lstm_mc_dropout(self, df: pd.DataFrame, passes: int = MC_DROPOUT_PASSES) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        if self._lstm is None:
            return None
        seq = build_sequence_tensor(df, 24)
        try:
            preds: List[np.ndarray] = []
            for _ in range(max(1, int(passes))):
                p = self._lstm(seq, training=True)
                preds.append(np.asarray(p, dtype=np.float32).reshape(1, -1))
            stack = np.vstack(preds)
            mean = np.clip(np.mean(stack, axis=0, keepdims=True), 0, 9)
            std = np.std(stack, axis=0, keepdims=True)
            return mean, std
        except Exception as e:
            logger.warning("LSTM MC-dropout failed: %s", e)
            return None

    def _sequence_surrogate(self, df: pd.DataFrame) -> np.ndarray:
        d = df.sort_values("timestamp") if df is not None and not df.empty else pd.DataFrame()
        if d.empty or len(d) < 3:
            return np.array([[2.0, 2.2, 2.4, 2.5]], dtype=np.float32)
        kp = pd.to_numeric(d["kp_current"], errors="coerce").dropna()
        bz = pd.to_numeric(d["bz_gsm"], errors="coerce").dropna()
        base = float(kp.iloc[-1])
        slope = float(np.mean(np.diff(kp.tail(12)))) if len(kp) > 2 else 0.0
        south = max(0.0, -float(bz.iloc[-1]) / 15.0)
        h3 = base + 3 * slope + 0.4 * south
        h6 = base + 6 * slope + 0.55 * south
        h12 = base + 8 * slope + 0.65 * south
        h24 = base + 10 * slope + 0.7 * south
        return np.array([[_clip_kp(h3), _clip_kp(h6), _clip_kp(h12), _clip_kp(h24)]], dtype=np.float32)

    def predict(self, df: pd.DataFrame) -> Dict[str, Any]:
        xgb_raw = self._xgb_horizons(df)
        lstm_unc = None
        mc = self._lstm_mc_dropout(df)
        if mc is not None:
            lstm_raw, lstm_unc = mc
        else:
            lstm_raw = self._lstm_predict(df)
        if lstm_raw is None:
            lstm_raw = self._sequence_surrogate(df)
        if lstm_raw.shape[1] < xgb_raw.shape[1]:
            pad = np.repeat(lstm_raw[:, -1:], xgb_raw.shape[1] - lstm_raw.shape[1], axis=1)
            lstm_raw = np.hstack([lstm_raw, pad])
        elif lstm_raw.shape[1] > xgb_raw.shape[1]:
            lstm_raw = lstm_raw[:, : xgb_raw.shape[1]]

        horizons = [3, 6, 12, 24]
        fused = []
        unc = []
        for i, h in enumerate(horizons):
            w = _fusion_weight_xgb(h)
            xv = float(xgb_raw[0, min(i, xgb_raw.shape[1] - 1)])
            lv = float(lstm_raw[0, min(i, lstm_raw.shape[1] - 1)])
            fused.append(_clip_kp(w * xv + (1 - w) * lv))
            base_unc = float(0.25 + (1 - w) * 0.45 + abs(xv - lv) * 0.15)
            if lstm_unc is not None:
                lu = float(lstm_unc[0, min(i, lstm_unc.shape[1] - 1)])
                base_unc = max(base_unc, 0.2 + lu * 0.8)
            unc.append(base_unc)

        kp_now = float(df["kp_current"].iloc[-1]) if df is not None and not df.empty else 2.0
        v_last = float(df["sw_speed_kmps"].iloc[-1]) if df is not None and not df.empty else 400.0
        storm_p = float(min(0.99, max(0.01, (fused[2] - 4) / 5.0)))
        model_status = "OPERATIONAL"
        if self._lstm is None and self._xgb_bootstrap:
            model_status = "DEGRADED_HEURISTIC"
        elif self._lstm is None:
            model_status = "DEGRADED_NO_LSTM"
        elif self._xgb_bootstrap:
            model_status = "DEGRADED_BOOTSTRAP_XGB"

        return {
            "current_kp": kp_now,
            "storm_class": storm_class_from_kp(fused[2]),
            "storm_probability": storm_p,
            "peak_arrival_minutes": int(1_500_000 / max(v_last, 250) / 60),
            "model_notes": "hybrid_xgb_sequence_or_lstm",
            "model_status": model_status,
            "forecast": {
                "kp_3hr": {"value": fused[0], "uncertainty": unc[0]},
                "kp_6hr": {"value": fused[1], "uncertainty": unc[1]},
                "kp_12hr": {"value": fused[2], "uncertainty": unc[2]},
                "kp_24hr": {"value": fused[3], "uncertainty": unc[3]},
            },
        }

    def shap_explain(self, df: pd.DataFrame) -> Dict[str, Any]:
        X = features_row7(df)
        try:
            import shap

            est = self._xgb.estimators_[1] if hasattr(self._xgb, "estimators_") else self._xgb
            explainer = shap.TreeExplainer(est)
            sv = explainer.shap_values(X)
            if isinstance(sv, list):
                contrib = np.array(sv[0])[0]
            else:
                arr = np.array(sv)
                contrib = arr[0] if arr.ndim == 2 else arr.flatten()[:7]
            order = np.argsort(-np.abs(contrib))[:10]
            feats = [
                {
                    "feature": ROW7_NAMES[i],
                    "shap_value": float(contrib[i]),
                    "physics_note": _physics_note(ROW7_NAMES[i]),
                }
                for i in order
            ]
            return {"target_horizon_hours": 6, "features": feats, "method": "TreeSHAP_XGBoost"}
        except Exception as e:
            logger.warning("SHAP: %s", e)
            _, names = build_tabular_feature_vector(df)
            flat = build_tabular_feature_vector(df)[0].flatten()
            idx = np.argsort(-np.abs(flat))[: min(10, len(flat))]
            feats = [
                {
                    "feature": names[i] if i < len(names) else f"f{i}",
                    "shap_value": float(flat[i]) * 0.02,
                    "physics_note": "fallback sensitivity",
                }
                for i in idx
            ]
            return {"target_horizon_hours": 6, "features": feats, "method": "tabular_proxy"}


def _physics_note(name: str) -> str:
    return {
        "kp0": "Current geomagnetic activity baseline",
        "bz": "Southward IMF enhances magnetospheric coupling",
        "v": "Solar wind speed sets L1–Earth transit (warning window)",
        "bt": "Total IMF magnitude scales epsilon energy input",
        "n": "Proton density raises dynamic pressure",
        "south": "Southward Bz fraction proxy drives ring current injection",
        "p_dyn": "Ram pressure compresses magnetosphere",
    }.get(name, "Driver")


_kp_predictor: Optional[KpPredictor] = None


def get_kp_predictor() -> KpPredictor:
    global _kp_predictor
    if _kp_predictor is None:
        _kp_predictor = KpPredictor()
    return _kp_predictor
