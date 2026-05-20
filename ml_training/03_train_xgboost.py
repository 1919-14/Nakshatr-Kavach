# ml_training/03_train_xgboost.py
"""
NAKSHATRA-KAVACH Layer 3: Train four XGBoost Kp prediction models.

One model per forecast horizon (3hr, 6hr, 12hr, 24hr).
Uses TimeSeriesSplit with gap=48 to prevent data leakage.
Saves trained models and SHAP TreeExplainers to backend/app/models/.

Usage:
    cd backend
    python -m ml_training.03_train_xgboost
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
import pyarrow.parquet as pq
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error

# Ensure backend/ is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.feature_engineering import FEATURE_NAMES

MODEL_DIR = ROOT / "backend" / "app" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ── XGBoost hyperparameters per horizon ─────────────────────────────────────

BASE_PARAMS = {
    "n_estimators": 500,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 3,
    "gamma": 0.1,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "objective": "reg:squarederror",
    "eval_metric": "rmse",
    "random_state": 42,
    "n_jobs": -1,
    "tree_method": "hist",
}

HORIZON_OVERRIDES = {
    "3hr":  {},
    "6hr":  {"n_estimators": 600, "reg_lambda": 1.5},
    "12hr": {"n_estimators": 700, "reg_alpha": 0.3, "reg_lambda": 2.0},
    "24hr": {"n_estimators": 800, "max_depth": 5, "reg_lambda": 3.0},
}

HORIZON_TARGET_OFFSETS = {"3hr": 180, "6hr": 360, "12hr": 720, "24hr": 1440}
RMSE_TARGETS = {"3hr": 0.8, "6hr": 1.0, "12hr": 1.3, "24hr": 1.6}


_DIRECT_COLUMN_MAP = {
    "bz_current": "bz_gsm",
    "bz_mean_30min": "bz_gsm_mean_30min",
    "bz_mean_1hr": "bz_gsm_mean_1hr",
    "bz_mean_3hr": "bz_gsm_mean_3hr",
    "bz_min_30min": "bz_gsm_min_30min",
    "bz_min_1hr": "bz_gsm_min_1hr",
    "bz_std_1hr": "bz_gsm_std_1hr",
    "bz_rate_of_change_per_min": "bz_roc",
    "bt_mean_30min": "bt_mean_30min",
    "bt_mean_1hr": "bt_mean_1hr",
    "bt_max_1hr": "bt_max_1hr",
    "bt_rate_of_change_per_min": "bt_roc",
    "sw_speed_mean_1hr": "flow_speed_mean_1hr",
    "sw_speed_max_1hr": "flow_speed_max_1hr",
    "sw_speed_rate_of_change": "speed_roc",
    "proton_density_mean_1hr": "proton_density_mean_1hr",
    "epsilon_current": "epsilon",
    "bz_southward_onset_flag": "bz_southward_onset",
    "kp_current": "kp",
    "kp_mean_6hr": "kp_mean_6hr",
    "kp_mean_12hr": "kp_mean_12hr",
    "kp_max_24hr": "kp_max_24hr",
    "kp_rate_of_change": "kp_roc",
    "bz_speed_interaction": "bz_speed_interaction",
    "bt_speed_interaction": "bt_speed_interaction",
    "imf_clock_angle_sin": "clock_sin",
    "imf_clock_angle_cos": "clock_cos",
}


_ZERO_FEATURES = {
    "xray_severity_current",
    "xray_severity_max_6hr",
    "time_since_last_M_class_hours",
    "xray_peak_flux_24hr",
    "cme_earth_directed",
    "cme_speed_normalized",
    "cme_arrival_hours",
    "cme_is_imminent",
}


def _rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    csum = np.cumsum(values, dtype=np.float64)
    out = csum.copy()
    out[window:] = csum[window:] - csum[:-window]
    return out.astype(np.float32)


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    sums = _rolling_sum(values, window=window).astype(np.float64)
    denom = np.minimum(np.arange(1, len(values) + 1, dtype=np.int32), window).astype(np.float64)
    return (sums / denom).astype(np.float32)


def _consecutive_true_count(flags: np.ndarray) -> np.ndarray:
    flags = (np.asarray(flags) > 0).astype(np.int8)
    idx = np.arange(flags.size, dtype=np.int32)
    last_false = np.maximum.accumulate(np.where(flags == 0, idx, -1))
    consec = np.where(flags == 1, idx - last_false, 0)
    return consec.astype(np.float32)


def _load_parquet_tail_columns(parquet_path: Path, columns: list[str], max_rows: int, buffer_rows: int) -> pd.DataFrame:
    pf = pq.ParquetFile(parquet_path)
    needed = max_rows + buffer_rows
    row_groups: list[int] = []
    running = 0
    for rg in range(pf.num_row_groups - 1, -1, -1):
        row_groups.append(rg)
        running += pf.metadata.row_group(rg).num_rows
        if running >= needed:
            break
    row_groups = sorted(row_groups)

    table = pf.read_row_groups(row_groups, columns=columns)
    df = table.to_pandas()
    if len(df) > needed:
        df = df.iloc[-needed:].copy()
    return df


def build_layer2_feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """Build the exact Layer 2 XGB feature vector (45 features) in FEATURE_NAMES order."""
    n = len(df)
    south = df["bz_southward"].to_numpy()
    epsilon = df["epsilon"].to_numpy(dtype=np.float32)
    dynp = df["dynamic_pressure"].to_numpy(dtype=np.float32)

    computed: dict[str, np.ndarray] = {
        "bz_southward_duration_30min": _rolling_sum(south.astype(np.float32), window=30),
        "consecutive_southward_minutes": _consecutive_true_count(south),
        "southward_fraction_30min": _rolling_mean(south.astype(np.float32), window=30),
        "southward_fraction_1hr": _rolling_mean(south.astype(np.float32), window=60),
        "southward_fraction_3hr": _rolling_mean(south.astype(np.float32), window=180),
        "epsilon_mean_30min": _rolling_mean(epsilon, window=30),
        "epsilon_mean_1hr": _rolling_mean(epsilon, window=60),
        "epsilon_cumulative_3hr": _rolling_sum(epsilon, window=180),
        "dynamic_pressure_mean_1hr": _rolling_mean(dynp, window=60),
        # Approximation: keep aligned with runtime schema without costly rolling max.
        "dynamic_pressure_max_1hr": _rolling_mean(dynp, window=60),
    }

    features: list[np.ndarray] = []
    for name in FEATURE_NAMES:
        if name in _ZERO_FEATURES:
            features.append(np.zeros(n, dtype=np.float32))
            continue
        mapped = _DIRECT_COLUMN_MAP.get(name)
        if mapped is not None:
            features.append(df[mapped].to_numpy(dtype=np.float32))
            continue
        if name in computed:
            features.append(computed[name].astype(np.float32))
            continue
        raise KeyError(f"No mapping for feature '{name}'")

    X = np.column_stack(features).astype(np.float32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    if X.shape[1] != len(FEATURE_NAMES):
        raise ValueError(f"Feature matrix has {X.shape[1]} cols, expected {len(FEATURE_NAMES)}")
    return X


def generate_synthetic_training_data(n_samples: int = 8000) -> pd.DataFrame:
    """
    Generate physics-informed synthetic solar wind data for model training.
    This is used when real historical data CSVs are not yet available.
    """
    rng = np.random.default_rng(42)

    bz = rng.normal(0, 8, n_samples)
    bt = np.abs(bz) + rng.uniform(1, 5, n_samples)
    speed = rng.uniform(300, 800, n_samples)
    density = rng.uniform(1, 30, n_samples)
    epsilon = np.maximum(0, -bz) * speed * bt ** 2 / 1e10 + rng.normal(0, 0.1, n_samples)
    kp_base = np.clip(0.3 * np.abs(bz) + 0.01 * speed + 0.5 * epsilon + rng.normal(0, 0.5, n_samples), 0, 9)

    # Build 45 features (matching Layer 2 schema)
    features = np.column_stack([
        bz,  # bz_current
        bz + rng.normal(0, 0.5, n_samples),  # bz_mean_30min
        bz + rng.normal(0, 0.8, n_samples),  # bz_mean_1hr
        bz + rng.normal(0, 1.2, n_samples),  # bz_mean_3hr
        bz - np.abs(rng.normal(0, 2, n_samples)),  # bz_min_30min
        bz - np.abs(rng.normal(0, 3, n_samples)),  # bz_min_1hr
        np.abs(rng.normal(0, 2, n_samples)),  # bz_std_1hr
        rng.uniform(0, 30, n_samples),  # bz_southward_duration_30min
        rng.normal(0, 0.5, n_samples),  # bz_rate_of_change_per_min
        bt + rng.normal(0, 0.5, n_samples),  # bt_mean_30min
        bt + rng.normal(0, 0.8, n_samples),  # bt_mean_1hr
        bt + np.abs(rng.normal(0, 2, n_samples)),  # bt_max_1hr
        rng.normal(0, 0.3, n_samples),  # bt_rate_of_change_per_min
        speed + rng.normal(0, 20, n_samples),  # sw_speed_mean_1hr
        speed + np.abs(rng.normal(0, 30, n_samples)),  # sw_speed_max_1hr
        rng.normal(0, 2, n_samples),  # sw_speed_rate_of_change
        density + rng.normal(0, 2, n_samples),  # proton_density_mean_1hr
        0.5 * density * (speed / 1000) ** 2,  # dynamic_pressure_mean_1hr
        0.5 * density * ((speed + 50) / 1000) ** 2,  # dynamic_pressure_max_1hr
        epsilon,  # epsilon_current
        epsilon + rng.normal(0, 0.1, n_samples),  # epsilon_mean_30min
        epsilon + rng.normal(0, 0.15, n_samples),  # epsilon_mean_1hr
        epsilon * 180 + rng.normal(0, 5, n_samples),  # epsilon_cumulative_3hr
        rng.uniform(0, 180, n_samples),  # consecutive_southward_minutes
        rng.uniform(0, 1, n_samples),  # southward_fraction_30min
        rng.uniform(0, 1, n_samples),  # southward_fraction_1hr
        rng.uniform(0, 1, n_samples),  # southward_fraction_3hr
        (bz < -5).astype(float),  # bz_southward_onset_flag
        rng.choice([0, 1, 2, 3, 4, 5], n_samples),  # xray_severity_current
        rng.choice([0, 1, 2, 3, 4, 5], n_samples),  # xray_severity_max_6hr
        rng.uniform(0, 48, n_samples),  # time_since_last_M_class_hours
        rng.uniform(-9, -4, n_samples),  # xray_peak_flux_24hr
        rng.choice([0, 1], n_samples, p=[0.85, 0.15]),  # cme_earth_directed
        rng.uniform(0, 1, n_samples),  # cme_speed_normalized
        rng.uniform(0, 48, n_samples),  # cme_arrival_hours
        (rng.uniform(0, 48, n_samples) < 6).astype(float),  # cme_is_imminent
        kp_base,  # kp_current
        kp_base + rng.normal(0, 0.3, n_samples),  # kp_mean_6hr
        kp_base + rng.normal(0, 0.5, n_samples),  # kp_mean_12hr
        np.clip(kp_base + np.abs(rng.normal(0, 1, n_samples)), 0, 9),  # kp_max_24hr
        rng.normal(0, 0.3, n_samples),  # kp_rate_of_change
        np.abs(bz) * speed / 1000,  # bz_speed_interaction
        bt * speed / 1000,  # bt_speed_interaction
        rng.uniform(-1, 1, n_samples),  # imf_clock_angle_sin
        rng.uniform(-1, 1, n_samples),  # imf_clock_angle_cos
    ])

    # Target Kp at future horizons (physics-informed decay)
    targets = {}
    for horizon, offset_min in HORIZON_TARGET_OFFSETS.items():
        decay = 1.0 - (offset_min / 1440) * 0.3
        noise_scale = 0.3 + (offset_min / 1440) * 0.8
        target = np.clip(kp_base * decay + 0.4 * np.abs(bz) * decay + rng.normal(0, noise_scale, n_samples), 0, 9)
        targets[horizon] = target

    return features, targets


def train_and_save() -> None:
    """Train all 4 XGBoost models and save them with SHAP explainers."""
    print("=" * 70)
    print("NAKSHATRA-KAVACH — XGBoost Kp Model Training")
    print("=" * 70)

    # ── Load Real or Synthetic Data ──────────────────────────────────────────
    parquet_path = ROOT / "download_data" / "raw" / "training_xgb.parquet"
    if parquet_path.exists():
        print(f"Loading REAL NASA OMNI + GFZ Potsdam dataset: {parquet_path}")

        required_cols = {
            "timestamp_utc",
            "kp_target_3hr",
            "kp_target_6hr",
            "kp_target_12hr",
            "kp_target_24hr",
            # columns used directly or for derived rollups
            "bz_southward",
            "bz_southward_onset",
            "epsilon",
            "dynamic_pressure",
        }
        required_cols.update(_DIRECT_COLUMN_MAP.values())

        # Load ALL rows — storm events are extremely rare (< 0.001% of data),
        # so we must train on the complete dataset to capture them.
        print("  Loading FULL dataset (all rows) — this may take a minute...")
        df = pd.read_parquet(parquet_path, columns=sorted(required_cols))
        print(f"  Loaded {len(df):,} rows for training.")

        # Extract targets
        targets = {
            "3hr": df["kp_target_3hr"].to_numpy(dtype=np.float32),
            "6hr": df["kp_target_6hr"].to_numpy(dtype=np.float32),
            "12hr": df["kp_target_12hr"].to_numpy(dtype=np.float32),
            "24hr": df["kp_target_24hr"].to_numpy(dtype=np.float32),
        }

        # Report storm sample counts for each horizon
        for h in ["3hr", "6hr", "12hr", "24hr"]:
            storm_n = int((targets[h] >= 5.0).sum())
            print(f"  Target {h}: {storm_n:,} storm samples (Kp>=5)")

        print(f"  Using {len(FEATURE_NAMES)} Layer-2 aligned features: {FEATURE_NAMES[:5]} ...")
        X = build_layer2_feature_matrix(df)

        for k in targets:
            targets[k] = np.nan_to_num(targets[k], nan=0.0, posinf=0.0, neginf=0.0)
    else:
        print("WARNING: Real dataset not found. Using synthetic fallback.")
        X, targets = generate_synthetic_training_data(n_samples=10000)

    # ── Detect GPU (RTX 4050 CUDA) ───────────────────────────────────────────
    gpu_available = False
    try:
        dmat = xgb.DMatrix(np.random.randn(10, 2), label=np.random.randn(10))
        xgb.train({"device": "cuda", "tree_method": "hist"}, dmat, num_boost_round=1)
        gpu_available = True
        print("RTX 4050 CUDA detected! XGBoost will train on GPU.")
    except Exception:
        print("No XGBoost CUDA support. Training on CPU (multi-threaded).")

    tscv = TimeSeriesSplit(n_splits=5, gap=48)

    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        print(f"\n{'-' * 50}")
        print(f"Training {horizon} model...")
        y = targets[horizon]

        params = {**BASE_PARAMS, **HORIZON_OVERRIDES[horizon]}
        if gpu_available:
            params["tree_method"] = "hist"
            params["device"] = "cuda"
            print("  [GPU] CUDA accelerated Hist algorithm on RTX 4050.")
        else:
            params["tree_method"] = "hist"
            params["n_jobs"] = -1
            print("  [CPU] Multi-threaded Hist algorithm.")

        model = xgb.XGBRegressor(**params)

        # Use last fold for final eval
        train_idx, val_idx = list(tscv.split(X))[-1]
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        # ── Storm-weighted sampling ──
        # With only ~57 storm rows out of 11M+, the model collapses to
        # predicting near-zero.  Heavily upweight storm periods.
        sample_weights = np.ones(len(y_train), dtype=np.float32)
        sample_weights[y_train >= 7.0] = 50.0   # G3+ storms: 50× weight
        sample_weights[np.logical_and(y_train >= 5.0, y_train < 7.0)] = 20.0  # G1-G2
        sample_weights[np.logical_and(y_train >= 3.0, y_train < 5.0)] = 5.0   # Active
        sample_weights[np.logical_and(y_train >= 1.0, y_train < 3.0)] = 2.0   # Unsettled
        storm_count = int((y_train >= 5.0).sum())
        print(f"  Training on {len(X_train):,} rows, validating on {len(X_val):,} rows.")
        print(f"  Storm samples (Kp>=5): {storm_count} — storm weights applied (50x/20x/5x/2x)")

        model.fit(
            X_train, y_train,
            sample_weight=sample_weights,
            eval_set=[(X_val, y_val)],
            verbose=100,
        )

        y_pred = np.clip(model.predict(X_val), 0, 9)
        rmse = float(np.sqrt(mean_squared_error(y_val, y_pred)))
        target_rmse = RMSE_TARGETS[horizon]
        status = "✓ PASS" if rmse < target_rmse else "✗ FAIL"
        print(f"  {horizon} RMSE: {rmse:.4f} (target < {target_rmse}) {status}")

        # Save model
        model_path = str(MODEL_DIR / f"xgb_kp_{horizon}.json")
        model.save_model(model_path)
        print(f"  Saved: {model_path}")

        # Save SHAP explainer (subsample reference to prevent OOM)
        try:
            import shap
            ref_size = min(200, len(X_train))
            ref_idx = np.random.choice(len(X_train), size=ref_size, replace=False)
            explainer = shap.TreeExplainer(model, data=X_train[ref_idx])
            shap_path = str(MODEL_DIR / f"shap_xgb_{horizon}.pkl")
            joblib.dump(explainer, shap_path)
            print(f"  SHAP:  {shap_path}")
        except Exception as e:
            print(f"  SHAP save skipped: {e}")

    print(f"\n{'=' * 70}")
    print("All XGBoost models trained and saved.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    train_and_save()
