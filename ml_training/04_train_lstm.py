# ml_training/04_train_lstm.py
"""
NAKSHATRA-KAVACH Layer 3: Train the Bidirectional LSTM Kp prediction model.

Multi-output architecture: input (24, 15) → output (4,) for 3/6/12/24hr horizons.
Uses storm-weighted Huber loss to prioritise accuracy during active storms.
Saves best checkpoint to backend/app/models/lstm_kp_model.keras.

Usage:
    cd backend
    python -m ml_training.04_train_lstm
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MODEL_DIR = ROOT / "backend" / "app" / "models"
LOG_DIR = ROOT / "ml_training" / "logs"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def generate_synthetic_sequences(n_samples: int = 5000, seq_len: int = 24, n_features: int = 15):
    """Generate synthetic LSTM training data with physics-informed patterns."""
    rng = np.random.default_rng(42)

    X = np.zeros((n_samples, seq_len, n_features), dtype=np.float32)
    y = np.zeros((n_samples, 4), dtype=np.float32)

    for i in range(n_samples):
        # Base solar wind state
        bz_base = rng.normal(0, 8)
        speed_base = rng.uniform(300, 800)
        bt_base = abs(bz_base) + rng.uniform(1, 5)
        kp_base = np.clip(0.3 * abs(bz_base) + 0.01 * speed_base, 0, 9)

        for t in range(seq_len):
            drift = rng.normal(0, 0.3)
            bz_t = bz_base + drift * t * 0.1
            X[i, t, 0] = bz_t                                    # bz_gsm
            X[i, t, 1] = abs(bz_t) + rng.uniform(1, 3)          # bt_total
            X[i, t, 2] = speed_base + rng.normal(0, 15)         # sw_speed
            X[i, t, 3] = rng.uniform(1, 20)                     # density
            X[i, t, 4] = max(0, -bz_t) * speed_base / 1e4       # epsilon
            X[i, t, 5] = 0.5 * X[i, t, 3] * (X[i, t, 2] / 1000) ** 2  # pressure
            X[i, t, 6] = 1.0 if bz_t < -5 else 0.0             # southward flag
            X[i, t, 7] = rng.choice([0, 1, 2, 3, 4, 5])        # xray
            X[i, t, 8] = kp_base + rng.normal(0, 0.3)           # kp
            X[i, t, 9] = rng.normal(0, 0.5)                     # bz_rate
            X[i, t, 10] = rng.normal(0, 0.3)                    # bt_rate
            X[i, t, 11] = rng.normal(0, 2)                      # speed_rate
            X[i, t, 12] = rng.uniform(-1, 1)                    # clock_sin
            X[i, t, 13] = rng.uniform(-1, 1)                    # clock_cos
            X[i, t, 14] = max(0, min(1, -bz_t / 20))           # southward_frac

        # Targets: Kp at 3/6/12/24hr horizons
        storm_driver = max(0, -bz_base) * 0.4 + speed_base * 0.005
        y[i, 0] = np.clip(kp_base + storm_driver * 0.3 + rng.normal(0, 0.3), 0, 9)
        y[i, 1] = np.clip(kp_base + storm_driver * 0.5 + rng.normal(0, 0.5), 0, 9)
        y[i, 2] = np.clip(kp_base + storm_driver * 0.4 + rng.normal(0, 0.7), 0, 9)
        y[i, 3] = np.clip(kp_base + storm_driver * 0.3 + rng.normal(0, 0.9), 0, 9)

    return X, y


def train_and_save() -> None:
    """Train the LSTM model and save the best checkpoint."""
    import tensorflow as tf

    # Import storm_weighted_huber from kp_predictor
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.kp_predictor import storm_weighted_huber, build_lstm_model

    print("=" * 70)
    print("NAKSHATRA-KAVACH — LSTM Kp Model Training")
    print("=" * 70)

    X, y = generate_synthetic_sequences(n_samples=8000)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    print(f"Train: {X_train.shape}, Val: {X_val.shape}")

    model = build_lstm_model(dropout_rate=0.25)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3, clipnorm=1.0),
        loss=storm_weighted_huber,
        metrics=["mae", tf.keras.metrics.RootMeanSquaredError(name="rmse")],
    )
    model.summary()

    save_path = str(MODEL_DIR / "lstm_kp_model.keras")
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=20, restore_best_weights=True, verbose=1),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=8, min_lr=1e-6, verbose=1),
        tf.keras.callbacks.ModelCheckpoint(
            filepath=save_path, monitor="val_loss", save_best_only=True, verbose=1),
        tf.keras.callbacks.TensorBoard(
            log_dir=str(LOG_DIR / f"lstm_{datetime.now().strftime('%Y%m%d_%H%M')}"),
            histogram_freq=1),
    ]

    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=200, batch_size=64,
        callbacks=callbacks, verbose=1,
    )

    # Evaluate
    y_pred = np.clip(model.predict(X_val), 0, 9)
    for i, horizon in enumerate(["3hr", "6hr", "12hr", "24hr"]):
        rmse = float(np.sqrt(np.mean((y_val[:, i] - y_pred[:, i]) ** 2)))
        print(f"  {horizon} RMSE: {rmse:.4f}")

    print(f"\nModel saved to: {save_path}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    train_and_save()
