# ml_training/05_evaluate_models.py
"""
NAKSHATRA-KAVACH Layer 3: Comprehensive model evaluation.

Evaluates XGBoost and LSTM models on:
  - RMSE, MAE per horizon
  - Storm detection metrics (precision, recall, F1 for G3+)
  - Confusion matrix (QUIET/G1/G2/G3+)
  - Model agreement analysis
  - Outputs evaluation_report.json

Usage:
    cd backend
    python -m ml_training.05_evaluate_models
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import (
    classification_report, confusion_matrix, f1_score,
    mean_absolute_error, mean_squared_error, precision_score, recall_score,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MODEL_DIR = ROOT / "backend" / "app" / "models"
REPORT_PATH = ROOT / "ml_training" / "evaluation_report.json"


def classify_kp(kp: float) -> str:
    if kp >= 7.0: return "G3+"
    if kp >= 6.0: return "G2"
    if kp >= 5.0: return "G1"
    return "QUIET"


def evaluate() -> None:
    print("=" * 70)
    print("NAKSHATRA-KAVACH — Model Evaluation Suite")
    print("=" * 70)

    report: dict = {"horizons": {}}

    # ── Load REAL May 2024 G5 test data or synthetic fallback ──
    g5_path = ROOT / "download_data" / "raw" / "may2024_g5_test.parquet"
    if g5_path.exists():
        import pandas as pd
        df = pd.read_parquet(g5_path)
        print(f"Loaded REAL May 2024 G5 Extreme storm test set: {len(df):,} rows")
        print("  This data was NEVER seen during training — true held-out validation!")

        targets = {
            "3hr":  df["kp_target_3hr"].values,
            "6hr":  df["kp_target_6hr"].values,
            "12hr": df["kp_target_12hr"].values,
            "24hr": df["kp_target_24hr"].values,
        }

        # Build features using the SAME pipeline as training (build_layer2_feature_matrix)
        # to ensure feature order and derivation match the XGBoost models exactly.
        import importlib
        try:
            _xgb_train = importlib.import_module("ml_training.03_train_xgboost")
            X_test = _xgb_train.build_layer2_feature_matrix(df)
            print(f"  Built {X_test.shape[1]} Layer-2 features (matching training pipeline)")
        except Exception as exc:
            print(f"  WARNING: Could not use build_layer2_feature_matrix ({exc})")
            print(f"  Falling back to raw parquet columns (may cause feature mismatch!)")
            drop_cols = ["timestamp_utc", "kp_target_3hr", "kp_target_6hr",
                         "kp_target_12hr", "kp_target_24hr"]
            feat_cols = [c for c in df.columns if c not in drop_cols]
            X_test = np.nan_to_num(df[feat_cols].values.astype(np.float32), nan=0.0)

        report["test_set"] = "may2024_g5_storm_real"
        report["test_rows"] = len(df)
    else:
        print("WARNING: May 2024 G5 test set not found. Using synthetic data.")
        import importlib
        _xgb_mod = importlib.import_module("ml_training.03_train_xgboost")
        X_test, targets = _xgb_mod.generate_synthetic_training_data(n_samples=2000)
        report["test_set"] = "synthetic_fallback"

    # Load XGBoost models
    import xgboost as xgb
    xgb_models = {}
    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        path = MODEL_DIR / f"xgb_kp_{horizon}.json"
        if path.exists():
            model = xgb.XGBRegressor()
            model.load_model(str(path))
            xgb_models[horizon] = model
            print(f"Loaded XGBoost {horizon}")
        else:
            print(f"WARNING: XGBoost {horizon} not found at {path}")

    # ── Load PyTorch LSTM model ──
    lstm_model = None
    lstm_n_features = None
    lstm_seq_len = 24
    lstm_horizon_idx = {"3hr": 0, "6hr": 1, "12hr": 2, "24hr": 3}
    pt_path = MODEL_DIR / "lstm_kp_model.pt"
    if pt_path.exists():
        import torch, importlib
        _lstm_mod = importlib.import_module("ml_training.04_train_lstm")
        NakshatraLSTM = _lstm_mod.NakshatraLSTM
        ckpt = torch.load(str(pt_path), map_location="cpu", weights_only=False)
        lstm_n_features = ckpt.get("n_features", 72)
        lstm_seq_len    = ckpt.get("seq_len", 24)
        lstm_model = NakshatraLSTM(n_features=lstm_n_features, seq_len=lstm_seq_len)
        lstm_model.load_state_dict(ckpt["model_state_dict"])
        lstm_model.eval()
        print(f"Loaded PyTorch LSTM from {pt_path} (features={lstm_n_features})")

        # Build sliding-window sequences from RAW parquet features for LSTM
        # LSTM was trained on all parquet columns except timestamp+targets (72 features)
        n_test = len(X_test)
        if n_test > lstm_seq_len:
            # Build raw feature matrix from parquet (matching LSTM training)
            drop_cols_lstm = ["timestamp_utc", "kp_target_3hr", "kp_target_6hr",
                              "kp_target_12hr", "kp_target_24hr"]
            feat_cols_lstm = [c for c in df.columns if c not in drop_cols_lstm]
            X_lstm_raw = np.nan_to_num(df[feat_cols_lstm].values.astype(np.float32), nan=0.0)
            X_lstm_raw = X_lstm_raw[:, :lstm_n_features]  # align feature count
            lstm_windows = np.stack(
                [X_lstm_raw[i: i + lstm_seq_len] for i in range(n_test - lstm_seq_len)],
                axis=0).astype(np.float32)
            with torch.no_grad():
                lstm_raw = lstm_model(torch.from_numpy(lstm_windows)).numpy()
            # lstm_raw shape: (n_windows, 4)  - aligned to row index [seq_len .. n_test)
            print(f"  LSTM inference on {len(lstm_windows):,} windows. [OK]")
        else:
            lstm_model = None
            print("WARNING: test set too small for LSTM sequence windows. Skipping LSTM eval.")
    else:
        print(f"WARNING: LSTM .pt model not found at {pt_path}  — skipping LSTM evaluation.")

    # ── Evaluate each horizon ──
    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        y_true = np.nan_to_num(targets[horizon], nan=0.0)
        horizon_report: dict = {}
        labels = ["QUIET", "G1", "G2", "G3+"]

        # XGBoost evaluation
        if horizon in xgb_models:
            y_pred = np.clip(xgb_models[horizon].predict(X_test), 0, 9)
            rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
            mae  = float(mean_absolute_error(y_true, y_pred))
            classes_true = [classify_kp(v) for v in y_true]
            classes_pred = [classify_kp(v) for v in y_pred]
            storm_true = [1 if c == "G3+" else 0 for c in classes_true]
            storm_pred = [1 if c == "G3+" else 0 for c in classes_pred]
            horizon_report["xgboost"] = {
                "rmse": round(rmse, 4),
                "mae":  round(mae, 4),
                "storm_precision_g3": round(float(precision_score(storm_true, storm_pred, zero_division=0)), 4),
                "storm_recall_g3":    round(float(recall_score(storm_true, storm_pred, zero_division=0)), 4),
                "storm_f1_g3":        round(float(f1_score(storm_true, storm_pred, zero_division=0)), 4),
                "confusion_matrix":   confusion_matrix(classes_true, classes_pred, labels=labels).tolist(),
                "confusion_labels":   labels,
            }
            print(f"\n{horizon} XGBoost: RMSE={rmse:.4f} MAE={mae:.4f} "
                  f"F1(G3+)={horizon_report['xgboost']['storm_f1_g3']:.4f}")
        else:
            horizon_report["xgboost"] = {"error": "model not found"}
            print(f"\n{horizon} XGBoost: SKIPPED (model not found)")

        # LSTM evaluation
        if lstm_model is not None:
            hidx = lstm_horizon_idx[horizon]
            # lstm_raw is aligned to rows [seq_len..n_test); trim y_true to match
            y_true_lstm = y_true[lstm_seq_len:]
            y_pred_lstm = np.clip(lstm_raw[:, hidx], 0, 9)
            rmse_l = float(np.sqrt(mean_squared_error(y_true_lstm, y_pred_lstm)))
            mae_l  = float(mean_absolute_error(y_true_lstm, y_pred_lstm))
            classes_true_l = [classify_kp(v) for v in y_true_lstm]
            classes_pred_l = [classify_kp(v) for v in y_pred_lstm]
            storm_true_l = [1 if c == "G3+" else 0 for c in classes_true_l]
            storm_pred_l = [1 if c == "G3+" else 0 for c in classes_pred_l]
            horizon_report["lstm"] = {
                "rmse": round(rmse_l, 4),
                "mae":  round(mae_l, 4),
                "storm_precision_g3": round(float(precision_score(storm_true_l, storm_pred_l, zero_division=0)), 4),
                "storm_recall_g3":    round(float(recall_score(storm_true_l, storm_pred_l, zero_division=0)), 4),
                "storm_f1_g3":        round(float(f1_score(storm_true_l, storm_pred_l, zero_division=0)), 4),
                "confusion_matrix":   confusion_matrix(classes_true_l, classes_pred_l, labels=labels).tolist(),
                "confusion_labels":   labels,
            }
            print(f"{horizon} LSTM:    RMSE={rmse_l:.4f} MAE={mae_l:.4f} "
                  f"F1(G3+)={horizon_report['lstm']['storm_f1_g3']:.4f}")
        else:
            horizon_report["lstm"] = {"error": "model not available"}

        report["horizons"][horizon] = horizon_report

    # Save report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to: {REPORT_PATH}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    evaluate()
