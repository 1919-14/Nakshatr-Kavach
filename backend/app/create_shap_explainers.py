"""Generate serialized SHAP explainers for the trained XGBoost Kp models.

This is an offline utility. It loads the existing XGBoost model JSON files from
`backend/app/models/` and writes SHAP TreeExplainer pickles alongside them.

Run from repo root:
  backend\venv\Scripts\python.exe backend\app\create_shap_explainers.py
"""

from __future__ import annotations

from pathlib import Path

import joblib
import shap
import xgboost as xgb


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    model_dir = repo_root / "backend" / "app" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)

    xgb_paths = {
        "3hr": model_dir / "xgb_kp_3hr.json",
        "6hr": model_dir / "xgb_kp_6hr.json",
        "12hr": model_dir / "xgb_kp_12hr.json",
        "24hr": model_dir / "xgb_kp_24hr.json",
    }
    shap_paths = {
        "3hr": model_dir / "shap_xgb_3hr.pkl",
        "6hr": model_dir / "shap_xgb_6hr.pkl",
        "12hr": model_dir / "shap_xgb_12hr.pkl",
        "24hr": model_dir / "shap_xgb_24hr.pkl",
    }

    missing = [str(p) for p in xgb_paths.values() if not p.exists()]
    if missing:
        raise SystemExit("Missing XGBoost model(s):\n- " + "\n- ".join(missing))

    for horizon in ["3hr", "6hr", "12hr", "24hr"]:
        model = xgb.XGBRegressor()
        model.load_model(str(xgb_paths[horizon]))

        explainer = shap.TreeExplainer(model)
        joblib.dump({"horizon": horizon, "explainer": explainer}, str(shap_paths[horizon]))
        print(f"saved {horizon} -> {shap_paths[horizon]}")


if __name__ == "__main__":
    main()
