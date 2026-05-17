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
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error

# Ensure backend/ is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

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

    X, targets = generate_synthetic_training_data(n_samples=10000)
    tscv = TimeSeriesSplit(n_splits=5, gap=48)

    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        print(f"\n{'─' * 50}")
        print(f"Training {horizon} model...")
        y = targets[horizon]

        params = {**BASE_PARAMS, **HORIZON_OVERRIDES[horizon]}
        model = xgb.XGBRegressor(**params)

        # Use last fold for final eval
        train_idx, val_idx = list(tscv.split(X))[-1]
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        model.fit(
            X_train, y_train,
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

        # Save SHAP explainer
        try:
            import shap
            explainer = shap.TreeExplainer(model)
            shap_path = str(MODEL_DIR / f"shap_xgb_{horizon}.pkl")
            joblib.dump(explainer, shap_path)
            print(f"  SHAP:  {shap_path}")
        except Exception as e:
            print(f"  SHAP save failed: {e}")

    print(f"\n{'=' * 70}")
    print("All XGBoost models trained and saved.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    train_and_save()
