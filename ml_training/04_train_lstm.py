# ml_training/04_train_lstm.py
"""
NAKSHATRA-KAVACH Layer 3: Train the Bidirectional LSTM Kp prediction model.

Uses PyTorch (native CUDA on Windows) for GPU acceleration on RTX 4050.
Multi-output architecture: input (24, n_features) → output (4,) for 3/6/12/24hr horizons.
Uses storm-weighted Huber loss to prioritise accuracy during active storms.
Saves best checkpoint to backend/app/models/lstm_kp_model.pt

Usage:
    python -m ml_training.04_train_lstm
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MODEL_DIR = ROOT / "backend" / "app" / "models"
LOG_DIR = ROOT / "ml_training" / "logs"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ── PyTorch Bidirectional LSTM Model ────────────────────────────────────────

class NakshatraLSTM(nn.Module):
    """
    Deep Bidirectional LSTM for multi-horizon Kp forecasting.
    Matches the TensorFlow architecture in kp_predictor.py but in PyTorch.
    Architecture: BiLSTM(128) → LSTM(64) → LSTM(32) → Dense(64) → Dense(32) → Output(4)
    """
    def __init__(self, n_features: int = 72, seq_len: int = 24,
                 n_outputs: int = 4, dropout: float = 0.25):
        super().__init__()
        self.bilstm = nn.LSTM(n_features, 128, batch_first=True, bidirectional=True)
        self.drop1 = nn.Dropout(dropout)
        self.bn1 = nn.BatchNorm1d(128 * 2)

        self.lstm2 = nn.LSTM(128 * 2, 64, batch_first=True)
        self.drop2 = nn.Dropout(dropout)
        self.bn2 = nn.BatchNorm1d(64)

        self.lstm3 = nn.LSTM(64, 32, batch_first=True)
        self.drop3 = nn.Dropout(dropout)

        self.dense1 = nn.Linear(32, 64)
        self.relu1 = nn.ReLU()
        self.drop4 = nn.Dropout(dropout * 0.5)
        self.dense2 = nn.Linear(64, 32)
        self.relu2 = nn.ReLU()
        self.output = nn.Linear(32, n_outputs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, n_features)
        out, _ = self.bilstm(x)          # (batch, seq_len, 256)
        out = self.drop1(out)
        out = self.bn1(out.permute(0, 2, 1)).permute(0, 2, 1)

        out, _ = self.lstm2(out)          # (batch, seq_len, 64)
        out = self.drop2(out)
        out = self.bn2(out.permute(0, 2, 1)).permute(0, 2, 1)

        out, _ = self.lstm3(out)          # (batch, seq_len, 32)
        out = out[:, -1, :]               # take last timestep → (batch, 32)
        out = self.drop3(out)

        out = self.relu1(self.dense1(out))
        out = self.drop4(out)
        out = self.relu2(self.dense2(out))
        out = torch.sigmoid(self.output(out)) * 9.0  # constrain Kp to [0, 9]
        return out


# ── Storm-Weighted Huber Loss (PyTorch) ──────────────────────────────────────

def storm_weighted_huber(pred: torch.Tensor, target: torch.Tensor,
                          delta: float = 1.0) -> torch.Tensor:
    """
    Penalises errors during active G3+ storms (Kp≥7) by 5× and
    G1+ storms (Kp≥5) by 3× — identical physics to TF version.
    """
    weights = torch.where(target >= 7.0,
                          torch.full_like(target, 5.0),
                          torch.where(target >= 5.0,
                                      torch.full_like(target, 3.0),
                                      torch.ones_like(target)))
    error = target - pred
    abs_err = torch.abs(error)
    quad = torch.clamp(abs_err, max=delta)
    lin = abs_err - quad
    base = 0.5 * quad ** 2 + delta * lin
    return (weights * base).mean()


# ── Data Loading ─────────────────────────────────────────────────────────────

def create_sequences_from_parquet(parquet_path: Path, seq_len: int = 24,
                                   max_rows: int = 500_000):
    """
    Sample max_rows rows evenly from the parquet via pyarrow (fast, low RAM),
    then build sliding-window sequences for LSTM: (N, seq_len, n_features).
    """
    import pyarrow.parquet as pq
    import pandas as pd

    print(f"Loading dataset (sampled): {parquet_path}")
    pf = pq.ParquetFile(parquet_path)
    total_rows = pf.metadata.num_rows
    n_groups = pf.metadata.num_row_groups
    print(f"  {total_rows:,} rows in file — reading {max_rows:,} across {n_groups} row-groups.")

    target_per_group = max(1, max_rows // n_groups)
    rows_collected = []
    for i in range(n_groups):
        batch = pf.read_row_group(i).to_pandas()
        stride = max(1, len(batch) // target_per_group)
        rows_collected.append(batch.iloc[::stride].head(target_per_group))
        if sum(len(r) for r in rows_collected) >= max_rows:
            break

    df = pd.concat(rows_collected, ignore_index=True).head(max_rows)
    print(f"  Sampled {len(df):,} rows of real NASA OMNI + GFZ Kp data.")

    drop_cols = ["timestamp_utc", "kp_target_3hr", "kp_target_6hr",
                 "kp_target_12hr", "kp_target_24hr"]
    feat_cols = [c for c in df.columns if c not in drop_cols]
    n_features = len(feat_cols)
    print(f"  Using {n_features} physical features.")

    features = np.nan_to_num(df[feat_cols].values.astype(np.float32), nan=0.0)
    targets  = np.nan_to_num(df[["kp_target_3hr", "kp_target_6hr",
                                  "kp_target_12hr", "kp_target_24hr"]].values.astype(np.float32), nan=0.0)

    # Build sliding-window sequences (step=1 gives max richness within max_rows)
    max_seqs = 150_000
    step = max(1, (len(features) - seq_len) // max_seqs)
    X_list, y_list = [], []
    for i in range(0, len(features) - seq_len, step):
        X_list.append(features[i: i + seq_len])
        y_list.append(targets[i + seq_len - 1])
        if len(X_list) >= max_seqs:
            break

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)
    print(f"  Created {len(X):,} sequences of shape ({seq_len}, {n_features}).")
    return X, y, n_features


def generate_synthetic_sequences(n_samples: int = 10000, seq_len: int = 24,
                                   n_features: int = 15) -> tuple:
    """Synthetic fallback if parquet not found."""
    rng = np.random.default_rng(42)
    X = np.zeros((n_samples, seq_len, n_features), dtype=np.float32)
    y = np.zeros((n_samples, 4), dtype=np.float32)
    for i in range(n_samples):
        bz = rng.normal(0, 8)
        speed = rng.uniform(300, 800)
        kp_base = np.clip(0.3 * abs(bz) + 0.01 * speed, 0, 9)
        for t in range(seq_len):
            bz_t = bz + rng.normal(0, 0.3) * t * 0.1
            X[i, t, 0] = bz_t
            X[i, t, 1] = abs(bz_t) + rng.uniform(1, 3)
            X[i, t, 2] = speed + rng.normal(0, 15)
            if n_features > 3:
                X[i, t, 3:] = rng.normal(0, 1, n_features - 3)
        storm_driver = max(0, -bz) * 0.4 + speed * 0.005
        y[i, 0] = np.clip(kp_base + storm_driver * 0.3 + rng.normal(0, 0.3), 0, 9)
        y[i, 1] = np.clip(kp_base + storm_driver * 0.5 + rng.normal(0, 0.5), 0, 9)
        y[i, 2] = np.clip(kp_base + storm_driver * 0.4 + rng.normal(0, 0.7), 0, 9)
        y[i, 3] = np.clip(kp_base + storm_driver * 0.3 + rng.normal(0, 0.9), 0, 9)
    return X, y, n_features


# ── Training Loop ─────────────────────────────────────────────────────────────

def train_and_save() -> None:
    print("=" * 70)
    print("NAKSHATRA-KAVACH — LSTM Kp Model Training (PyTorch)")
    print("=" * 70)

    # ── GPU detection ──
    if torch.cuda.is_available():
        device = torch.device("cuda")
        gpu_name = torch.cuda.get_device_name(0)
        print(f"GPU detected: {gpu_name}  ✓ Training on CUDA!")
        batch_size = 512   # large batch for RTX 4050 Tensor Cores
        # Enable TF32 for speed on Ampere GPUs
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
    else:
        device = torch.device("cpu")
        print("No CUDA GPU found — training on CPU (multi-threaded).")
        batch_size = 128

    print(f"Device: {device}  |  Batch size: {batch_size}")

    # ── Load data ──
    parquet_path = ROOT / "download_data" / "raw" / "training_lstm.parquet"
    if parquet_path.exists():
        X, y, n_features = create_sequences_from_parquet(parquet_path, seq_len=24)
    else:
        print("Warning: Real dataset not found. Using synthetic fallback.")
        X, y, n_features = generate_synthetic_sequences(n_samples=10000)

    split = int(len(X) * 0.85)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    print(f"Train: {X_train.shape}  |  Val: {X_val.shape}")

    # Build PyTorch DataLoaders
    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds   = TensorDataset(torch.from_numpy(X_val),   torch.from_numpy(y_val))
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                          pin_memory=(device.type == "cuda"), num_workers=0)
    val_dl   = DataLoader(val_ds,   batch_size=batch_size * 2, shuffle=False,
                          pin_memory=(device.type == "cuda"), num_workers=0)

    # ── Build model ──
    model = NakshatraLSTM(n_features=n_features, seq_len=24, dropout=0.25).to(device)
    param_count = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Model parameters: {param_count:,}")

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=8, min_lr=1e-6, verbose=True)

    # ── Training loop ──
    save_path = MODEL_DIR / "lstm_kp_model.pt"
    best_val_loss = float("inf")
    patience_counter = 0
    PATIENCE = 20
    EPOCHS = 200

    print(f"\nTraining for up to {EPOCHS} epochs (early stop patience={PATIENCE})...")
    print("-" * 70)

    for epoch in range(1, EPOCHS + 1):
        t0 = time.time()
        model.train()
        train_losses = []
        for xb, yb in train_dl:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = storm_weighted_huber(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(loss.item())

        # Validation
        model.eval()
        val_losses, val_preds, val_trues = [], [], []
        with torch.no_grad():
            for xb, yb in val_dl:
                xb, yb = xb.to(device), yb.to(device)
                pred = model(xb)
                val_losses.append(storm_weighted_huber(pred, yb).item())
                val_preds.append(pred.cpu().numpy())
                val_trues.append(yb.cpu().numpy())

        train_loss = np.mean(train_losses)
        val_loss   = np.mean(val_losses)
        elapsed    = time.time() - t0

        # Compute RMSE for 3hr horizon
        vp = np.clip(np.concatenate(val_preds), 0, 9)
        vt = np.concatenate(val_trues)
        rmse_3hr = float(np.sqrt(np.mean((vt[:, 0] - vp[:, 0]) ** 2)))

        print(f"Epoch {epoch:3d}/{EPOCHS} | "
              f"loss={train_loss:.4f} val_loss={val_loss:.4f} "
              f"rmse_3hr={rmse_3hr:.4f} | {elapsed:.1f}s")

        scheduler.step(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_loss": best_val_loss,
                "n_features": n_features,
                "seq_len": 24,
                "n_outputs": 4,
            }, save_path)
            print(f"  ✓ Best model saved  (val_loss={best_val_loss:.4f})")
        else:
            patience_counter += 1
            if patience_counter >= PATIENCE:
                print(f"\nEarly stopping at epoch {epoch} (no improvement for {PATIENCE} epochs).")
                break

    # ── Final evaluation ──
    print("\n" + "=" * 70)
    print("Final evaluation on validation set:")
    vp = np.clip(np.concatenate(val_preds), 0, 9)
    vt = np.concatenate(val_trues)
    for i, horizon in enumerate(["3hr", "6hr", "12hr", "24hr"]):
        rmse = float(np.sqrt(np.mean((vt[:, i] - vp[:, i]) ** 2)))
        print(f"  {horizon} RMSE: {rmse:.4f}")

    print(f"\nModel saved to: {save_path}")
    print(f"Device used:    {device}")
    print("=" * 70)


if __name__ == "__main__":
    train_and_save()
