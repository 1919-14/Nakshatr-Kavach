# download_data/build_training_dataset.py
"""
NAKSHATRA-KAVACH — Build Training Dataset
==========================================
Reads raw downloaded files → produces clean Parquet files ready for
ml_training/03_train_xgboost.py and ml_training/04_train_lstm.py.

Outputs:
  raw/training_xgb.parquet    — 45-feature matrix + targets at 3/6/12/24hr horizons
  raw/training_lstm.parquet   — same, sorted by time (for sequence generation)
  raw/may2024_g5_test.parquet — strictly held-out test set (never train on this)

Usage:
    python build_training_dataset.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("builder")

ROOT   = Path(__file__).resolve().parent
RAW    = ROOT / "raw"
OMNI   = RAW / "omni"
KP_DIR = RAW / "kp"

# OMNI column indices (0-based, from NASA OMNI HRO 1-min format)
# NOTE: OMNI 1-min data does NOT contain Kp. Kp is a 3-hourly index
#       and must be merged from GFZ Potsdam data separately.
OMNI_COLS = {
    "year": 0, "doy": 1, "hour": 2, "minute": 3,
    "bx_gsm": 6, "by_gsm": 10, "bz_gsm": 11,
    "flow_speed": 30, "proton_density": 34, "temperature": 35,
    "bt": 12,      # Field magnitude |B|
}

OMNI_FILL = {
    "bz_gsm": 9999.99, "by_gsm": 9999.99, "bx_gsm": 9999.99,
    "bt": 9999.99, "flow_speed": 99999.9, "proton_density": 999.99,
    "temperature": 9999999.0,
}


def load_gfz_kp() -> pd.DataFrame:
    """
    Load REAL Kp index from GFZ Potsdam file (3-hourly resolution).
    Returns a DataFrame with columns: [timestamp_utc, kp]
    where kp is the actual geomagnetic Kp index [0.0 - 9.0].
    """
    kp_file = KP_DIR / "Kp_ap_Ap_SN_F107_since_1932.txt"
    if not kp_file.exists():
        log.error("GFZ Kp file not found: %s", kp_file)
        return pd.DataFrame()

    log.info("  Loading REAL GFZ Potsdam Kp index from %s ...", kp_file.name)
    rows = []
    with open(kp_file) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 15:
                continue
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                # 8 Kp values per day (columns 7-14), each covers 3 hours
                for i in range(8):
                    kp = float(parts[7 + i])
                    ts = pd.Timestamp(year=y, month=m, day=d, hour=i * 3)
                    rows.append({"timestamp_utc": ts, "kp": kp})
            except (ValueError, IndexError):
                continue

    df_kp = pd.DataFrame(rows)
    log.info("  Loaded %d Kp records (%.0f years). Range: [%.1f, %.1f]",
             len(df_kp), len(df_kp) / (8 * 365.25),
             df_kp["kp"].min(), df_kp["kp"].max())
    return df_kp


def load_omni_year(year: int) -> pd.DataFrame:
    """Load one year of OMNI 1-minute ASCII data."""
    path = OMNI / f"omni_min{year}.asc"
    if not path.exists():
        log.warning("Missing OMNI %d — skipping", year)
        return pd.DataFrame()

    log.info("  Loading OMNI %d ...", year)
    try:
        df = pd.read_csv(path, sep=r"\s+", header=None, dtype=float, na_values=["99999.9", "9999.99", "999.99"])
        df.columns = [f"c{i}" for i in range(df.shape[1])]

        timestamp = pd.to_datetime({
            "year": df["c0"].astype(int),
            "month": 1, "day": 1,
        }) + pd.to_timedelta(df["c1"].astype(int) - 1, unit="D") + \
             pd.to_timedelta(df["c2"].astype(int), unit="h") + \
             pd.to_timedelta(df["c3"].astype(int), unit="min")

        out = pd.DataFrame({"timestamp_utc": timestamp})
        for name, col_idx in OMNI_COLS.items():
            if name in ("year", "doy", "hour", "minute"):
                continue
            col = df.iloc[:, col_idx].copy()
            fill = OMNI_FILL.get(name)
            if fill:
                col = col.where(col < fill * 0.99, np.nan)
            out[name] = col.values

        out = out.dropna(subset=["bz_gsm", "flow_speed"])
        return out
    except Exception as exc:
        log.error("  Failed to load OMNI %d: %s", year, exc)
        return pd.DataFrame()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the 45 Layer 2 features over historical data.
    This mirrors the logic in feature_engineering.py but works on bulk DataFrames.
    """
    log.info("  Computing 45-feature matrix over %d rows ...", len(df))
    df = df.sort_values("timestamp_utc").reset_index(drop=True)
    df = df.set_index("timestamp_utc")

    # Rolling windows
    for w, label in [(30, "30min"), (60, "1hr"), (180, "3hr"), (360, "6hr"), (720, "12hr"), (1440, "24hr")]:
        for col in ["bz_gsm", "bt", "flow_speed", "proton_density"]:
            roll = df[col].rolling(w, min_periods=max(1, w // 4))
            if label in ("30min", "1hr", "3hr"):
                df[f"{col}_mean_{label}"] = roll.mean()
                df[f"{col}_min_{label}"]  = roll.min()
                df[f"{col}_max_{label}"]  = roll.max()
                df[f"{col}_std_{label}"]  = roll.std().fillna(0)

    # Akasofu epsilon
    v  = df["flow_speed"].fillna(400).clip(lower=200) * 1e3
    bt = df["bt"].fillna(5).clip(lower=0.1) * 1e-9
    bz = df["bz_gsm"].fillna(0)
    by = df["by_gsm"].fillna(0)
    b_perp = np.sqrt(by ** 2 + bz ** 2)
    theta  = 2 * np.arctan2(b_perp, bz.abs() + 1e-6)
    sin4   = np.sin(theta / 2) ** 4
    df["epsilon"] = (v * bt ** 2 * sin4 * 1e12).clip(lower=0)

    # Dynamic pressure
    mp = 1.67e-27
    n  = df["proton_density"].fillna(5).clip(lower=0.01) * 1e6
    df["dynamic_pressure"] = (n * mp * (df["flow_speed"].fillna(400) * 1e3) ** 2 * 1e9)

    # Southward Bz flags
    df["bz_southward"] = (df["bz_gsm"] < -5).astype(float)
    df["bz_southward_onset"] = ((df["bz_gsm"] < -5) & (df["bz_gsm"].shift(1) >= -5)).astype(float)
    df["consec_south_min"] = df["bz_southward"].groupby(
        (df["bz_southward"] == 0).cumsum()).cumcount()

    # Rate of change
    df["bz_roc"] = df["bz_gsm"].diff().fillna(0)
    df["bt_roc"] = df["bt"].diff().fillna(0)
    df["speed_roc"] = df["flow_speed"].diff().fillna(0)

    # IMF clock angle
    df["clock_sin"] = np.sin(np.arctan2(by, bz + 1e-9))
    df["clock_cos"] = np.cos(np.arctan2(by, bz + 1e-9))

    # Kp rolling
    df["kp_mean_6hr"]  = df["kp"].rolling(360, min_periods=1).mean()
    df["kp_mean_12hr"] = df["kp"].rolling(720, min_periods=1).mean()
    df["kp_max_24hr"]  = df["kp"].rolling(1440, min_periods=1).max()
    df["kp_roc"]       = df["kp"].diff().fillna(0)

    # Interaction terms
    df["bz_speed_interaction"] = df["bz_gsm"].abs() * df["flow_speed"].fillna(400) / 1000
    df["bt_speed_interaction"]  = df["bt"] * df["flow_speed"].fillna(400) / 1000

    return df.reset_index()


def add_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Shift Kp forward to create 3/6/12/24hr prediction targets."""
    log.info("  Adding Kp targets at 3/6/12/24hr horizons ...")
    df = df.sort_values("timestamp_utc").reset_index(drop=True)
    for horizon, offset in [("3hr", 180), ("6hr", 360), ("12hr", 720), ("24hr", 1440)]:
        df[f"kp_target_{horizon}"] = df["kp"].shift(-offset)
    return df.dropna(subset=["kp_target_3hr"])


def main() -> None:
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║  NAKSHATRA-KAVACH — Build Training Dataset               ║")
    log.info("╚══════════════════════════════════════════════════════════╝")

    # Load all OMNI years
    frames = []
    for year in range(2000, 2025):
        f = load_omni_year(year)
        if not f.empty:
            frames.append(f)

    if not frames:
        log.error("No OMNI data found in %s. Run download_all.py first.", OMNI)
        sys.exit(1)

    df_all = pd.concat(frames, ignore_index=True)
    df_all = df_all.sort_values("timestamp_utc").reset_index(drop=True)
    log.info("Total OMNI rows after loading: %d", len(df_all))

    # ── Merge REAL Kp from GFZ Potsdam ──────────────────────────────────────
    df_kp = load_gfz_kp()
    if df_kp.empty:
        log.error("Cannot build dataset without real Kp data!")
        sys.exit(1)

    # merge_asof: for each OMNI minute-row, find the nearest preceding Kp value
    df_all = df_all.sort_values("timestamp_utc").reset_index(drop=True)
    df_kp = df_kp.sort_values("timestamp_utc").reset_index(drop=True)
    df_all = pd.merge_asof(
        df_all, df_kp,
        on="timestamp_utc",
        direction="backward",
        tolerance=pd.Timedelta("3h"),
    )
    kp_valid = df_all["kp"].notna().sum()
    kp_storm = (df_all["kp"] >= 5.0).sum()
    log.info("  Merged Kp: %d/%d rows have valid Kp, %d are storm (Kp>=5)",
             kp_valid, len(df_all), kp_storm)
    df_all["kp"] = df_all["kp"].fillna(0.0)

    # Build features
    df_feat = build_features(df_all)
    df_feat = add_targets(df_feat)

    # ── Split: strictly hold out May 2024 G5 storm ──
    MAY_G5_START = pd.Timestamp("2024-05-09 00:00:00")
    MAY_G5_END   = pd.Timestamp("2024-05-15 23:59:00")
    TRAIN_END    = pd.Timestamp("2024-04-30 23:59:00")

    ts = pd.to_datetime(df_feat["timestamp_utc"])
    df_train = df_feat[ts <= TRAIN_END].copy()
    df_test  = df_feat[(ts >= MAY_G5_START) & (ts <= MAY_G5_END)].copy()

    log.info("Train rows: %d  |  May 2024 G5 test rows: %d", len(df_train), len(df_test))

    # Save
    train_path = RAW / "training_xgb.parquet"
    lstm_path  = RAW / "training_lstm.parquet"
    test_path  = RAW / "may2024_g5_test.parquet"

    df_train.to_parquet(train_path, index=False)
    df_train.to_parquet(lstm_path,  index=False)
    df_test.to_parquet(test_path,   index=False)

    log.info("Saved: %s (%.1f MB)", train_path.name, train_path.stat().st_size / 1e6)
    log.info("Saved: %s (%.1f MB)", test_path.name,  test_path.stat().st_size / 1e6)
    log.info("")
    log.info("Next step:")
    log.info("  cd ../backend")
    log.info("  python -m ml_training.03_train_xgboost --data ../download_data/raw/training_xgb.parquet")
    log.info("  python -m ml_training.04_train_lstm     --data ../download_data/raw/training_lstm.parquet")


if __name__ == "__main__":
    main()
