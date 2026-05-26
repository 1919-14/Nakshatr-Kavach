# ml_training/download_historical_storms.py
"""
Build the Layer 7 historical storm cache.

The script attempts lightweight public-source downloads where stable URLs are
available, then falls back to deterministic synthetic profiles. Synthetic rows
are clearly labeled with data_type=SYNTHETIC in both CSV and catalog metadata.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.replay_engine import generate_synthetic_storm, validate_storm_dataframe

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

STORM_DIR = BACKEND_ROOT / "app" / "data" / "storms"
CATALOG_PATH = STORM_DIR / "storm_catalog.json"


def _catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return the four-storm catalog used by Layer 7."""
    return {
        "storms": [
            {
                "storm_id": "2024_may_g5",
                "name": "May 2024 Extreme Geomagnetic Storm",
                "short_name": "May 2024 G5",
                "dates": "May 10-12, 2024",
                "start_timestamp_utc": "2024-05-10T00:00:00Z",
                "kp_peak": 9.0,
                "storm_class": "G5",
                "duration_hours": 72,
                "data_file": "2024_may_g5.csv",
                "resolution_minutes": 1,
                "data_type": "SYNTHETIC",
                "significance": "First G5 storm since November 2003. Strongest storm in 21 years. Aurora visible in Ladakh, India.",
                "why_primary": "Most recent major storm and a genuine model validation case after the training window.",
                "data_source": "Synthetic fallback profile matching NOAA/SWPC storm morphology until archived data is downloaded.",
                "display_color": "#9C27B0",
                "thumbnail_kp": 9.0,
                "demo_script": "NAKSHATRA-KAVACH predicts G4-G5 conditions before peak; satellites and grid corridors move into critical risk.",
                "key_moments": [
                    {"offset_hours": 0, "event": "Storm quiet - Kp=2.1"},
                    {"offset_hours": 8, "event": "Bz begins turning southward"},
                    {"offset_hours": 14, "event": "First G1 alert - Kp crosses 5"},
                    {"offset_hours": 18, "event": "G3 conditions - satellites at HIGH risk"},
                    {"offset_hours": 22, "event": "G5 PEAK - Kp=9.0, all critical"},
                    {"offset_hours": 30, "event": "Storm subsiding - recovery begins"},
                    {"offset_hours": 48, "event": "Quiet conditions restored"},
                ],
                "validation_available": True,
                "max_satellite_risk_pct": 89,
                "max_gic_amps": 72,
                "total_data_rows": 4320,
            },
            {
                "storm_id": "1989_quebec",
                "name": "Quebec Blackout Geomagnetic Storm",
                "short_name": "Quebec 1989",
                "dates": "March 9-14, 1989",
                "start_timestamp_utc": "1989-03-09T00:00:00Z",
                "kp_peak": 9.0,
                "storm_class": "G5",
                "duration_hours": 120,
                "data_file": "1989_quebec.csv",
                "resolution_minutes": 60,
                "data_type": "SYNTHETIC",
                "significance": "Hydro-Quebec's 735kV system collapsed in 92 seconds; 6 million people lost power for 9 hours.",
                "why_included": "Best demonstration of grid GIC risk and the canonical space-weather power failure.",
                "data_source": "Synthetic fallback profile matching reconstructed OMNI hourly storm morphology.",
                "display_color": "#F44336",
                "thumbnail_kp": 9.0,
                "demo_script": "NAKSHATRA-KAVACH flags long north-south lines at critical GIC risk before collapse.",
                "key_moments": [
                    {"offset_hours": 0, "event": "Solar flare March 9 - CME launched"},
                    {"offset_hours": 84, "event": "CME arrives - storm begins"},
                    {"offset_hours": 88, "event": "Kp reaches 8 - G4 conditions"},
                    {"offset_hours": 90, "event": "Kp=9 G5 PEAK - Quebec grid collapses"},
                    {"offset_hours": 99, "event": "Storm gradually subsides"},
                ],
                "validation_available": False,
                "max_satellite_risk_pct": 91,
                "max_gic_amps": 95,
                "total_data_rows": 120,
            },
            {
                "storm_id": "2003_halloween",
                "name": "Halloween Geomagnetic Storms 2003",
                "short_name": "Halloween 2003",
                "dates": "October 28 - November 4, 2003",
                "start_timestamp_utc": "2003-10-28T00:00:00Z",
                "kp_peak": 9.0,
                "storm_class": "G5",
                "duration_hours": 168,
                "data_file": "2003_halloween.csv",
                "resolution_minutes": 60,
                "data_type": "SYNTHETIC",
                "significance": "Multiple X-class flares, 40+ satellite anomalies, Sweden power outage, and ISS crew radiation sheltering.",
                "why_included": "Shows sustained multi-peak satellite risk over a week-long storm sequence.",
                "data_source": "Synthetic fallback profile matching reconstructed OMNI hourly multi-event morphology.",
                "display_color": "#FF6B35",
                "thumbnail_kp": 9.0,
                "key_moments": [
                    {"offset_hours": 0, "event": "X17.2 flare - CME launched Oct 28"},
                    {"offset_hours": 18, "event": "First CME arrives - G4 storm begins"},
                    {"offset_hours": 22, "event": "G5 PEAK 1 - Kp=9.0"},
                    {"offset_hours": 42, "event": "X10 flare - second CME launched Oct 29"},
                    {"offset_hours": 60, "event": "G5 PEAK 2 - compound storm"},
                    {"offset_hours": 144, "event": "X28+ flare Nov 4 - strongest ever recorded"},
                    {"offset_hours": 162, "event": "Final G5 peak - storm gradually ends"},
                ],
                "validation_available": False,
                "max_satellite_risk_pct": 93,
                "max_gic_amps": 88,
                "total_data_rows": 168,
            },
            {
                "storm_id": "2022_starlink",
                "name": "Starlink Satellite Loss Geomagnetic Storm",
                "short_name": "Starlink 2022",
                "dates": "February 3-4, 2022",
                "start_timestamp_utc": "2022-02-03T00:00:00Z",
                "kp_peak": 5.5,
                "storm_class": "G1",
                "duration_hours": 48,
                "data_file": "2022_starlink.csv",
                "resolution_minutes": 1,
                "data_type": "SYNTHETIC",
                "significance": "A moderate G1 storm destroyed 38-40 Starlink satellites during low-altitude deployment.",
                "why_included": "Demonstrates that low Kp can still be catastrophic for vulnerable LEO satellites.",
                "data_source": "Synthetic fallback profile matching NOAA/SWPC storm morphology until archived data is downloaded.",
                "display_color": "#4CAF50",
                "thumbnail_kp": 5.5,
                "demo_script": "Cartosat-3 shows elevated drag risk; operators are advised to suspend LEO maneuvers.",
                "key_moments": [
                    {"offset_hours": 0, "event": "SpaceX Falcon 9 launch - 49 satellites at 210km"},
                    {"offset_hours": 8, "event": "Bz turns southward - storm building"},
                    {"offset_hours": 16, "event": "G1 conditions - Kp=5.5"},
                    {"offset_hours": 18, "event": "Atmospheric density at 210km increases 50%"},
                    {"offset_hours": 24, "event": "Starlink orbit-raising fails - drag too high"},
                    {"offset_hours": 36, "event": "SpaceX confirms satellite losses beginning"},
                    {"offset_hours": 48, "event": "38-40 satellites deorbited - $80M lost"},
                ],
                "validation_available": True,
                "max_satellite_risk_pct": 58,
                "max_gic_amps": 14,
                "total_data_rows": 2880,
            },
        ]
    }


def _try_download_real_data(storm: Dict[str, Any]) -> Optional[pd.DataFrame]:
    """
    Attempt real-data acquisition for a storm.

    For the May 2024 G5 storm, loads the real OMNI+GFZ-sourced parquet file
    that was built by download_data/build_training_dataset.py — the strictly
    held-out test set (never used in model training).

    All other storms fall back to deterministic synthetic profiles until a
    verified archival downloader is available for those events.

    Returns:
        DataFrame with columns matching the Layer 7 replay schema, or None.
    """
    storm_id = storm["storm_id"]

    # ── May 2024 G5: real OMNI/GFZ data available ──────────────────
    if storm_id == "2024_may_g5":
        parquet_path = REPO_ROOT / "download_data" / "raw" / "may2024_g5_test.parquet"
        if not parquet_path.exists():
            logger.warning(
                "Real May 2024 G5 parquet not found at %s — using synthetic fallback", parquet_path
            )
            return None

        logger.info("Loading REAL May 2024 G5 storm data from %s", parquet_path.name)
        try:
            df = pd.read_parquet(parquet_path)

            # Rename columns to match Layer 7 replay schema
            df = df.rename(columns={
                "bt":              "bt_total",
                "flow_speed":      "sw_speed_kmps",
                "kp":              "kp_current",
                "proton_density":  "proton_density_ccm",
                "temperature":     "proton_temp_kelvin",
            })

            # Ensure datetime parsing and UTC timezone
            df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
            df = df.sort_values("timestamp_utc").reset_index(drop=True)

            # Filter to the storm's defined time window
            start_ts = pd.Timestamp(storm["start_timestamp_utc"], tz="UTC")
            duration_hrs = int(storm["duration_hours"])
            end_ts = start_ts + pd.Timedelta(hours=duration_hrs)

            df_storm = df[
                (df["timestamp_utc"] >= start_ts) & (df["timestamp_utc"] < end_ts)
            ].copy()

            if df_storm.empty:
                logger.warning(
                    "No rows found in parquet between %s and %s — using synthetic fallback",
                    start_ts, end_ts,
                )
                return None

            # Forward/backward fill any gaps from OMNI fill-values
            numeric_cols = df_storm.select_dtypes(include="number").columns.tolist()
            df_storm[numeric_cols] = df_storm[numeric_cols].ffill().bfill()

            # Convert timestamp to ISO string expected by validate_storm_dataframe
            df_storm["timestamp_utc"] = df_storm["timestamp_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            logger.info(
                "Loaded %d REAL rows for May 2024 G5 (window: %s → %s)",
                len(df_storm), start_ts.isoformat(), end_ts.isoformat(),
            )
            return df_storm

        except Exception as exc:
            logger.error("Failed to load real May 2024 G5 data: %s — using synthetic fallback", exc)
            return None

    # ── All other storms: no verified archival download available ───
    logger.info("No verified real-data source for %s — using synthetic fallback", storm_id)
    return None


def _write_storm(storm: Dict[str, Any]) -> None:
    """Download or synthesize a storm CSV and update catalog row metadata."""
    df = _try_download_real_data(storm)
    if df is None:
        df = generate_synthetic_storm(
            storm_class=storm["storm_class"],
            duration_hours=int(storm["duration_hours"]),
            kp_peak=float(storm["kp_peak"]),
            start_timestamp_utc=storm["start_timestamp_utc"],
            resolution_minutes=int(storm["resolution_minutes"]),
        )
        storm["data_type"] = "SYNTHETIC"
        df["data_type"] = "SYNTHETIC"
    else:
        df = validate_storm_dataframe(df)
        storm["data_type"] = "REAL"
        df["data_type"] = "REAL"

    storm["total_data_rows"] = len(df)
    out_path = STORM_DIR / storm["data_file"]
    df.to_csv(out_path, index=False)
    logger.info("Wrote %s rows to %s", len(df), out_path)


def main() -> None:
    """Build all replay storm CSVs and catalog metadata."""
    STORM_DIR.mkdir(parents=True, exist_ok=True)
    catalog = _catalog()
    for storm in catalog["storms"]:
        _write_storm(storm)
    with CATALOG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=2)
    logger.info("Wrote storm catalog: %s", CATALOG_PATH)


if __name__ == "__main__":
    main()
