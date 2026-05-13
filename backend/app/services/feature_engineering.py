"""Layer 2: Feature engineering from recent solar-wind history (physics-informed)."""
from __future__ import annotations

import math
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

from app.services.physics import akasofu_epsilon_W, dynamic_pressure_npa

logger = logging.getLogger(__name__)


CORE_SEQUENCE_COLS = [
    "bz_gsm",
    "by_gsm",
    "bt_total",
    "sw_speed_kmps",
    "proton_density_ccm",
    "kp_current",
    "log_xray",
    "epsilon_proxy",
    "p_dyn",
    "sin_clock",
    "cos_clock",
    "south_mask",
    "bz_x_speed",
    "hour_sin",
    "hour_cos",
]

_MODEL_DIRS = [
    Path(__file__).resolve().parent.parent / "models",
    Path(__file__).resolve().parent.parent / "ml_models",
]
_TAB_SCALER = None
_SEQ_SCALER = None


def _load_scaler(name: str):
    for d in _MODEL_DIRS:
        p = d / name
        if p.exists():
            try:
                return joblib.load(p)
            except Exception as e:
                logger.warning("Failed to load scaler %s: %s", p, e)
                return None
    return None


def _get_tab_scaler():
    global _TAB_SCALER
    if _TAB_SCALER is None:
        _TAB_SCALER = _load_scaler("scaler_tabular.joblib")
    return _TAB_SCALER


def _get_seq_scaler():
    global _SEQ_SCALER
    if _SEQ_SCALER is None:
        _SEQ_SCALER = _load_scaler("scaler_seq.joblib")
    return _SEQ_SCALER


def _prep_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    for c in ["bz_gsm", "by_gsm", "bx_gsm", "bt_total", "sw_speed_kmps", "proton_density_ccm", "kp_current", "xray_flux_Wm2", "proton_temp_K"]:
        if c not in d.columns:
            d[c] = np.nan
    d = d.sort_values("timestamp").reset_index(drop=True)
    d["bz_gsm"] = pd.to_numeric(d["bz_gsm"], errors="coerce").interpolate(limit_direction="both")
    d["kp_current"] = pd.to_numeric(d["kp_current"], errors="coerce").interpolate(limit_direction="both")
    d["bt_total"] = pd.to_numeric(d["bt_total"], errors="coerce").ffill().fillna(5.0)
    d["by_gsm"] = pd.to_numeric(d["by_gsm"], errors="coerce").fillna(0.0)
    d["sw_speed_kmps"] = pd.to_numeric(d["sw_speed_kmps"], errors="coerce").fillna(400.0)
    d["proton_density_ccm"] = pd.to_numeric(d["proton_density_ccm"], errors="coerce").fillna(5.0)
    d["xray_flux_Wm2"] = pd.to_numeric(d["xray_flux_Wm2"], errors="coerce").fillna(1e-7)
    d["log_xray"] = np.log10(np.clip(d["xray_flux_Wm2"].values, 1e-12, None))
    v = d["sw_speed_kmps"].values
    bt = d["bt_total"].values
    bz = d["bz_gsm"].values
    by = d["by_gsm"].values
    n = d["proton_density_ccm"].values
    eps = [akasofu_epsilon_W(float(vi), float(bti), float(bzi), float(byi)) for vi, bti, bzi, byi in zip(v, bt, bz, by)]
    d["epsilon_proxy"] = eps
    d["p_dyn"] = [dynamic_pressure_npa(float(ni), float(vi)) for ni, vi in zip(n, v)]
    clock = np.arctan2(by, bz)
    d["sin_clock"] = np.sin(clock)
    d["cos_clock"] = np.cos(clock)
    d["south_mask"] = (bz < 0).astype(float)
    d["bz_x_speed"] = bz * v / 1000.0
    ts = pd.to_datetime(d["timestamp"], utc=True, errors="coerce")
    hour = ts.dt.hour.fillna(0).astype(float)
    d["hour_sin"] = np.sin(2 * math.pi * hour / 24.0)
    d["hour_cos"] = np.cos(2 * math.pi * hour / 24.0)
    return d


def build_sequence_tensor(df: pd.DataFrame, steps: int = 24) -> np.ndarray:
    """Shape (1, steps, len(CORE_SEQUENCE_COLS)) for LSTM path."""
    d = _prep_df(df)
    if d.empty:
        return np.zeros((1, steps, len(CORE_SEQUENCE_COLS)), dtype=np.float32)
    tail = d.tail(steps)[CORE_SEQUENCE_COLS].values.astype(np.float32)
    if tail.shape[0] < steps:
        pad = np.zeros((steps - tail.shape[0], tail.shape[1]), dtype=np.float32)
        tail = np.vstack([pad, tail])
    scaler = _get_seq_scaler()
    if scaler is not None:
        try:
            s = scaler.transform(tail)
            return np.asarray(s, dtype=np.float32).reshape(1, steps, -1)
        except Exception:
            pass
    # per-channel minmax on tail only (local normalization)
    t = tail.copy()
    for j in range(t.shape[1]):
        col = t[:, j]
        lo, hi = np.nanmin(col), np.nanmax(col)
        if hi - lo < 1e-6:
            t[:, j] = 0.0
        else:
            t[:, j] = (col - lo) / (hi - lo)
    return t.reshape(1, steps, -1)


def build_tabular_feature_vector(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """Single row (1, n_features) for XGBoost + SHAP."""
    d = _prep_df(df)
    names: List[str] = []
    feats: List[float] = []
    if d.empty:
        return np.zeros((1, 32), dtype=np.float32), [f"f{i}" for i in range(32)]

    last = d.iloc[-1]
    kp = float(last["kp_current"])
    bz = float(last["bz_gsm"])
    v = float(last["sw_speed_kmps"])
    bt = float(last["bt_total"])
    n = float(last["proton_density_ccm"])

    def add(name: str, val: float):
        names.append(name)
        feats.append(float(val))

    add("kp_now", kp)
    add("bz_now", bz)
    add("bt_now", bt)
    add("v_now", v)
    add("n_now", n)
    add("p_dyn", dynamic_pressure_npa(n, v))
    add("epsilon", akasofu_epsilon_W(v, bt, bz, float(last["by_gsm"])))
    for w in (6, 12, 24, 48):
        sl = d.tail(w)
        add(f"bz_mean_{w}", float(sl["bz_gsm"].mean()))
        add(f"bz_min_{w}", float(sl["bz_gsm"].min()))
        add(f"bz_south_frac_{w}", float((sl["bz_gsm"] < 0).mean()))
        add(f"kp_mean_{w}", float(sl["kp_current"].mean()))
        add(f"kp_max_{w}", float(sl["kp_current"].max()))
    add("bz_dt_1", float(d["bz_gsm"].diff().iloc[-1] or 0.0))
    add("kp_dt_1", float(d["kp_current"].diff().iloc[-1] or 0.0))
    x = np.array(feats, dtype=np.float32).reshape(1, -1)
    scaler = _get_tab_scaler()
    if scaler is not None:
        try:
            x = scaler.transform(x)
            x = np.asarray(x, dtype=np.float32)
        except Exception:
            pass
    return x, names
