# ml_training/02_feature_engineering.py
"""
Batch Layer 2 feature builder for NAKSHATRA-KAVACH model training.

This script loads historical Layer 1 solar_wind_readings, builds the 45-feature
XGBoost table and 24 x 15 LSTM sequences, constructs Kp targets at 3/6/12/24
hours, fits MinMaxScalers, and writes temporal train/validation/test artifacts.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

try:
    import joblib
except Exception as exc:  # pragma: no cover - import guard for CLI clarity
    raise RuntimeError("joblib is required to persist Layer 2 scalers") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.feature_engineering import (  # noqa: E402
    FEATURE_NAMES,
    SEQUENCE_FEATURE_NAMES,
    compute_lstm_sequence,
    compute_xgb_vector,
    prepare_history_dataframe,
    row_to_snapshot,
)
from app.utils.constants import SCALER_LSTM_PATH, SCALER_XGB_PATH  # noqa: E402


logger = logging.getLogger(__name__)
TARGET_COLUMNS = ["kp_3hr", "kp_6hr", "kp_12hr", "kp_24hr"]
HORIZON_MINUTES = {"kp_3hr": 180, "kp_6hr": 360, "kp_12hr": 720, "kp_24hr": 1440}


def configure_logging() -> None:
    """Configure console logging for the batch feature script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)8s | %(name)s | %(message)s",
    )


def load_historical_readings() -> pd.DataFrame:
    """
    Load the complete solar_wind_readings table from the configured database.

    Returns:
        UTC-indexed DataFrame sorted ascending.
    """
    from app.database.db import get_db

    query = "SELECT * FROM solar_wind_readings ORDER BY timestamp_utc ASC"
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    prepared = prepare_history_dataframe(df)
    logger.info("Loaded %d historical readings", len(prepared))
    return prepared


def construct_targets(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construct future Kp targets for all configured horizons.

    Args:
        df: UTC-indexed historical DataFrame.

    Returns:
        DataFrame with kp_3hr, kp_6hr, kp_12hr, and kp_24hr columns.
    """
    if df.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS, index=df.index)
    minute_grid = df[["kp_current"]].sort_index()
    targets = pd.DataFrame(index=df.index)
    for target_col, minutes in HORIZON_MINUTES.items():
        future_index = df.index + pd.Timedelta(minutes=minutes)
        future_values = minute_grid["kp_current"].reindex(
            future_index,
            method="nearest",
            tolerance=pd.Timedelta(minutes=5),
        )
        targets[target_col] = future_values.to_numpy(dtype=np.float64)
    return targets


def filter_training_rows(df: pd.DataFrame, targets: pd.DataFrame) -> pd.Index:
    """
    Select rows usable for supervised time-series training.

    Args:
        df: Historical feature source rows.
        targets: Future target matrix.

    Returns:
        DatetimeIndex of valid sample timestamps.
    """
    quality = df.get("data_quality_flag", pd.Series("UNKNOWN", index=df.index)).astype(str).str.upper()
    kp_status = df.get("kp_status", pd.Series("", index=df.index)).astype(str).str.upper()
    valid = (quality != "STALE") & (kp_status != "QUICKLOOK")
    valid &= targets[TARGET_COLUMNS].notna().all(axis=1)
    if len(df.index) > 0:
        valid &= df.index >= (df.index.min() + pd.Timedelta(hours=24))
    return df.index[valid]


def build_feature_matrices(df: pd.DataFrame, sample_index: pd.Index) -> Tuple[pd.DataFrame, Dict[str, pd.Series], np.ndarray, pd.DataFrame]:
    """
    Build XGB tabular features, LSTM sequences, and targets for each sample.

    Args:
        df: Historical readings.
        sample_index: Valid sample timestamps.

    Returns:
        X_all DataFrame, target Series dict, sequence array, and target matrix.
    """
    targets = construct_targets(df)
    x_rows: List[np.ndarray] = []
    sequences: List[np.ndarray] = []
    valid_times: List[pd.Timestamp] = []
    for idx, timestamp in enumerate(sample_index):
        if idx and idx % 1000 == 0:
            logger.info("Built %d/%d samples", idx, len(sample_index))
        history_window = df.loc[df.index <= timestamp]
        if len(history_window) < 24 * 60:
            continue
        snapshot = row_to_snapshot(df.loc[timestamp], timestamp)
        x_rows.append(compute_xgb_vector(snapshot, history_window))
        sequences.append(compute_lstm_sequence(history_window)[0])
        valid_times.append(timestamp)

    if not x_rows:
        raise RuntimeError("No valid training samples were generated")

    X_all = pd.DataFrame(np.vstack(x_rows), index=pd.DatetimeIndex(valid_times), columns=FEATURE_NAMES)
    y_all = targets.loc[X_all.index, TARGET_COLUMNS].astype(float)
    y_series = {col: y_all[col].copy() for col in TARGET_COLUMNS}
    sequence_array = np.stack(sequences, axis=0).astype(np.float32)
    return X_all, y_series, sequence_array, y_all


def temporal_split(
    X_all: pd.DataFrame,
    y_all: pd.DataFrame,
    sequences: np.ndarray,
) -> Dict[str, object]:
    """
    Split features and targets into fixed temporal train/validation/test sets.

    Args:
        X_all: Full feature table.
        y_all: Full target matrix.
        sequences: Full sequence tensor with first dimension aligned to X_all.

    Returns:
        Dictionary containing split DataFrames and arrays.
    """
    train_mask = (X_all.index >= "2010-01-01") & (X_all.index <= "2022-12-31 23:59:59")
    val_mask = (X_all.index >= "2023-01-01") & (X_all.index <= "2023-12-31 23:59:59")
    test_mask = (X_all.index >= "2024-01-01") & (X_all.index <= "2024-12-31 23:59:59")
    masks = {"train": train_mask, "val": val_mask, "test": test_mask}
    split: Dict[str, object] = {}
    for name, mask in masks.items():
        positions = np.flatnonzero(np.asarray(mask))
        split[f"X_{name}"] = X_all.loc[mask].copy()
        split[f"y_{name}"] = y_all.loc[mask].copy()
        split[f"seq_{name}"] = sequences[positions]
    return split


def fit_and_save_scalers(X_all: pd.DataFrame, sequences: np.ndarray, models_dir: Path) -> Tuple[MinMaxScaler, MinMaxScaler]:
    """
    Fit and persist the two Layer 2 MinMaxScalers.

    Args:
        X_all: Full tabular feature matrix.
        sequences: Full sequence tensor.
        models_dir: Directory where scaler pickle files are written.

    Returns:
        Tuple of fitted XGB and LSTM scalers.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    scaler_xgb = MinMaxScaler(feature_range=(0, 1))
    scaler_xgb.fit(X_all.to_numpy(dtype=np.float64))
    scaler_lstm = MinMaxScaler(feature_range=(0, 1))
    scaler_lstm.fit(sequences.reshape(-1, len(SEQUENCE_FEATURE_NAMES)))
    joblib.dump(scaler_xgb, models_dir / Path(SCALER_XGB_PATH).name)
    joblib.dump(scaler_lstm, models_dir / Path(SCALER_LSTM_PATH).name)
    logger.info("Saved scalers to %s", models_dir)
    return scaler_xgb, scaler_lstm


def save_split_outputs(split: Dict[str, object], output_dir: Path) -> None:
    """
    Write split feature, target, and sequence artifacts to disk.

    Args:
        split: Temporal split dictionary.
        output_dir: Destination directory.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("train", "val", "test"):
        X = split[f"X_{name}"]
        y = split[f"y_{name}"]
        seq = split[f"seq_{name}"]
        assert isinstance(X, pd.DataFrame)
        assert isinstance(y, pd.DataFrame)
        assert isinstance(seq, np.ndarray)
        X.to_csv(output_dir / f"X_{name}.csv", index_label="timestamp_utc")
        y.to_csv(output_dir / f"y_{name}.csv", index_label="timestamp_utc")
        for target_col in TARGET_COLUMNS:
            y[[target_col]].to_csv(output_dir / f"y_{name}_{target_col}.csv", index_label="timestamp_utc")
        np.save(output_dir / f"lstm_sequences_{name}.npy", seq)
    logger.info("Saved split outputs to %s", output_dir)


def print_feature_statistics(X_all: pd.DataFrame, y_all: pd.DataFrame, split: Dict[str, object]) -> None:
    """
    Print a concise feature statistics report.

    Args:
        X_all: Full feature matrix.
        y_all: Full target matrix.
        split: Temporal split dictionary.
    """
    print("\nLayer 2 Feature Engineering Report")
    print("=" * 40)
    print(f"Samples: {len(X_all):,}")
    print(f"Features: {len(FEATURE_NAMES)}")
    print(f"Sequence features: {len(SEQUENCE_FEATURE_NAMES)}")
    for name in ("train", "val", "test"):
        X = split[f"X_{name}"]
        assert isinstance(X, pd.DataFrame)
        print(f"{name.title():>5}: {len(X):,} rows")
    print("\nTarget Kp summary:")
    print(y_all.describe().round(3).to_string())
    print("\nTop absolute feature correlations with kp_3hr:")
    corr = X_all.join(y_all["kp_3hr"]).corr(numeric_only=True)["kp_3hr"].drop("kp_3hr")
    print(corr.abs().sort_values(ascending=False).head(15).round(3).to_string())
    print("\nMay 2024 storm in test set:", bool(((X_all.index >= "2024-05-10") & (X_all.index <= "2024-05-12 23:59:59")).any()))


def parse_args() -> argparse.Namespace:
    """
    Parse CLI arguments.

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(description="Build Layer 2 training features and scalers.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "ml_training" / "output")
    parser.add_argument("--models-dir", type=Path, default=BACKEND_ROOT / "app" / "models")
    return parser.parse_args()


def main() -> None:
    """Run the full batch Layer 2 feature engineering workflow."""
    configure_logging()
    args = parse_args()
    df = load_historical_readings()
    targets = construct_targets(df)
    sample_index = filter_training_rows(df, targets)
    logger.info("Selected %d valid supervised samples", len(sample_index))
    X_all, y_series, sequences, y_all = build_feature_matrices(df, sample_index)
    split = temporal_split(X_all, y_all, sequences)
    fit_and_save_scalers(X_all, sequences, args.models_dir)
    save_split_outputs(split, args.output_dir)
    print_feature_statistics(X_all, y_all, split)
    logger.info("Built y Series for horizons: %s", ", ".join(y_series.keys()))


if __name__ == "__main__":
    main()
