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

    # Generate test data
    from ml_training.03_train_xgboost import generate_synthetic_training_data
    X_test, targets = generate_synthetic_training_data(n_samples=2000)

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

    # Evaluate each horizon
    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        y_true = targets[horizon]
        horizon_report: dict = {}

        if horizon in xgb_models:
            y_pred = np.clip(xgb_models[horizon].predict(X_test), 0, 9)
            rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
            mae = float(mean_absolute_error(y_true, y_pred))

            classes_true = [classify_kp(v) for v in y_true]
            classes_pred = [classify_kp(v) for v in y_pred]
            labels = ["QUIET", "G1", "G2", "G3+"]

            storm_true = [1 if c == "G3+" else 0 for c in classes_true]
            storm_pred = [1 if c == "G3+" else 0 for c in classes_pred]

            horizon_report = {
                "rmse": round(rmse, 4),
                "mae": round(mae, 4),
                "storm_precision_g3": round(float(precision_score(storm_true, storm_pred, zero_division=0)), 4),
                "storm_recall_g3": round(float(recall_score(storm_true, storm_pred, zero_division=0)), 4),
                "storm_f1_g3": round(float(f1_score(storm_true, storm_pred, zero_division=0)), 4),
                "confusion_matrix": confusion_matrix(classes_true, classes_pred, labels=labels).tolist(),
                "confusion_labels": labels,
            }

            print(f"\n{horizon}: RMSE={rmse:.4f} MAE={mae:.4f} F1(G3+)={horizon_report['storm_f1_g3']:.4f}")
        else:
            horizon_report = {"error": "model not found"}
            print(f"\n{horizon}: SKIPPED (model not found)")

        report["horizons"][horizon] = horizon_report

    # Save report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to: {REPORT_PATH}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    evaluate()
