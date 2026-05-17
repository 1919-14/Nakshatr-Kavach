#!/usr/bin/env python3
# download_data/download_all.py
"""
NAKSHATRA-KAVACH — Dataset Downloader
=======================================
Downloads ALL training data required for the XGBoost and LSTM Kp prediction models.

Datasets:
  1. NASA OMNI 1-minute solar wind data (2000-2024) — ~12 million rows — PRIMARY
  2. GFZ Potsdam definitive Kp index (2000-2024)   — ground truth labels
  3. NOAA GOES X-ray flux monthly CSVs (2010-2024) — flare data
  4. NASA DONKI CME catalog (2010-2024)             — CME events (JSON → CSV)

Usage (first time):
    cd download_data
    python -m venv venv
    venv\\Scripts\\activate          (Windows)
    pip install -r requirements.txt
    python download_all.py

Re-run is safe — existing files are skipped automatically.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

import requests
from tqdm import tqdm

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("downloader")

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
RAW.mkdir(parents=True, exist_ok=True)

OMNI_DIR  = RAW / "omni"
KP_DIR    = RAW / "kp"
GOES_DIR  = RAW / "goes_xray"
DONKI_DIR = RAW / "donki_cme"

for d in [OMNI_DIR, KP_DIR, GOES_DIR, DONKI_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "NAKSHATRA-KAVACH-Downloader/1.0"

CHUNK_SIZE = 1 << 16   # 64 KB


def _download(url: str, dest: Path, desc: str = "", retries: int = 5) -> bool:
    """Download url → dest with progress bar. Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        log.info("  SKIP (exists): %s", dest.name)
        return True

    for attempt in range(1, retries + 1):
        try:
            with SESSION.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                with open(dest, "wb") as f, tqdm(
                    total=total, unit="B", unit_scale=True,
                    desc=desc or dest.name, leave=False,
                ) as bar:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        f.write(chunk)
                        bar.update(len(chunk))
            log.info("  OK: %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
            return True
        except Exception as exc:
            log.warning("  Attempt %d/%d failed: %s", attempt, retries, exc)
            if dest.exists():
                dest.unlink()
            if attempt < retries:
                time.sleep(5 * attempt)
    log.error("  FAILED after %d attempts: %s", retries, url)
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 1. NASA OMNI — 1-minute merged solar wind (CDAWeb bulk download)
#    Provides: Bx/By/Bz/Bt/speed/density/temperature/Kp estimates
#    URL pattern: https://cdaweb.gsfc.nasa.gov/pub/data/omni/high_res_omni/
# ═══════════════════════════════════════════════════════════════════════════════

OMNI_BASE = "https://cdaweb.gsfc.nasa.gov/pub/data/omni/high_res_omni/"

def download_omni(start_year: int = 2000, end_year: int = 2024) -> None:
    """Download yearly OMNI 1-minute ASCII files."""
    log.info("=" * 60)
    log.info("DATASET 1: NASA OMNI 1-min Solar Wind (%d–%d)", start_year, end_year)
    log.info("=" * 60)

    ok = failed = skipped = 0
    for year in range(start_year, end_year + 1):
        fname = f"omni_min{year}.asc"
        url   = OMNI_BASE + fname
        dest  = OMNI_DIR / fname
        if dest.exists() and dest.stat().st_size > 1000:
            log.info("  SKIP: %s", fname)
            skipped += 1
            continue
        result = _download(url, dest, desc=f"OMNI {year}")
        if result:
            ok += 1
        else:
            failed += 1

    log.info("OMNI: %d downloaded, %d skipped, %d failed", ok, skipped, failed)
    _write_omni_readme()


def _write_omni_readme() -> None:
    readme = OMNI_DIR / "COLUMNS.txt"
    if readme.exists():
        return
    readme.write_text(
        "OMNI 1-minute column order (space-separated):\n"
        "Year  DOY  Hour  Minute\n"
        "Col 07: BX_GSE (nT)\n"
        "Col 08: BY_GSE (nT)\n"
        "Col 09: BZ_GSE (nT)\n"
        "Col 10: BY_GSM (nT)\n"
        "Col 11: BZ_GSM (nT)  ← PRIMARY\n"
        "Col 12: sigma_Bx\n"
        "Col 13: sigma_By\n"
        "Col 14: sigma_Bz\n"
        "Col 15: Flow pressure (nPa)\n"
        "Col 16: E field (mV/m)\n"
        "Col 17: Plasma beta\n"
        "Col 18: Alfven Mach number\n"
        "Col 19: Kp*10 (integer)\n"
        "Col 20: R (sunspot)\n"
        "Col 21: Dst index\n"
        "Col 22: AE index\n"
        "Col 23: Proton flux >1MeV\n"
        "Col 24: Proton flux >2MeV\n"
        "Col 25: Proton flux >4MeV\n"
        "Col 26: Proton flux >10MeV\n"
        "Col 27: Proton flux >30MeV\n"
        "Col 28: Proton flux >60MeV\n"
        "Col 29: Flag\n"
        "Col 30: AP index\n"
        "Col 31: Flow speed (km/s)  ← PRIMARY\n"
        "Col 32: Vx GSE\n"
        "Col 33: Vy GSE\n"
        "Col 34: Vz GSE\n"
        "Col 35: Proton density (cc)  ← PRIMARY\n"
        "Col 36: Temperature (K)  ← PRIMARY\n"
        "Fill values: 9999, 99999, 999999 etc.\n"
        "\nSee: https://omniweb.gsfc.nasa.gov/html/omni_min_data.html\n"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GFZ Potsdam — Definitive Kp index (ground truth labels)
#    Single file covers 1932–present, updated monthly.
# ═══════════════════════════════════════════════════════════════════════════════

KP_URL  = "https://kp.gfz-potsdam.de/app/files/Kp_ap_Ap_SN_F107_since_1932.txt"
KP_FILE = KP_DIR / "Kp_ap_Ap_SN_F107_since_1932.txt"

# Fallback: NOAA 3-hour Kp JSON (last 30 days only — for live reference)
KP_NOAA_URL  = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
KP_NOAA_FILE = KP_DIR / "kp_1min_noaa_latest.json"


def download_kp() -> None:
    log.info("=" * 60)
    log.info("DATASET 2: GFZ Potsdam Kp Index (1932–present)")
    log.info("=" * 60)
    _download(KP_URL, KP_FILE, desc="Kp definitive")
    # Also grab NOAA 1-min Kp for recent data reference
    _download(KP_NOAA_URL, KP_NOAA_FILE, desc="Kp NOAA 1-min")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. NOAA GOES X-ray flux (NCEI archive)
#    Monthly CSV files per satellite. GOES-16 from 2017 onwards.
# ═══════════════════════════════════════════════════════════════════════════════

def download_goes_xray(start_year: int = 2010, end_year: int = 2024) -> None:
    """Download GOES X-ray flux monthly CSVs from NOAA NCEI."""
    log.info("=" * 60)
    log.info("DATASET 3: NOAA GOES X-ray Flux (%d–%d)", start_year, end_year)
    log.info("=" * 60)

    ok = failed = skipped = 0
    for year in range(start_year, end_year + 1):
        # GOES-16 from 2017, GOES-15 before
        sat = "goes16" if year >= 2017 else "goes15"
        for month in range(1, 13):
            if year == end_year and month > datetime.utcnow().month:
                break
            ym = f"{year}{month:02d}"
            fname = f"{sat}_xrs_1m_{ym}01_{ym}{_days_in_month(year, month):02d}.csv"
            dest = GOES_DIR / fname
            if dest.exists() and dest.stat().st_size > 0:
                skipped += 1
                continue

            # NCEI GOES archive URL pattern
            url = (
                f"https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites"
                f"/goes/{sat}/l2/data/xrsf-l2-avg1m_science"
                f"/{year}/{month:02d}/{fname}"
            )
            if _download(url, dest, desc=f"GOES {ym}"):
                ok += 1
            else:
                # Try alternate simple filename format
                alt_fname = f"g{sat[-2:]}_xrs_1m_{ym}.csv"
                alt_dest = GOES_DIR / alt_fname
                alt_url = (
                    f"https://www.ngdc.noaa.gov/stp/satellite/goes-r/goesrful/full"
                    f"/{sat}/xrs/csv/{year}/{alt_fname}"
                )
                if _download(alt_url, alt_dest, desc=f"GOES {ym} alt"):
                    ok += 1
                else:
                    failed += 1

    log.info("GOES: %d downloaded, %d skipped, %d failed", ok, skipped, failed)


def _days_in_month(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NASA DONKI — CME catalog (JSON API)
#    Fetches all Earth-directed CME events in yearly batches.
# ═══════════════════════════════════════════════════════════════════════════════

DONKI_BASE = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get"


def download_donki(start_year: int = 2010, end_year: int = 2024) -> None:
    """Download NASA DONKI CME catalog in yearly chunks."""
    log.info("=" * 60)
    log.info("DATASET 4: NASA DONKI CME Catalog (%d–%d)", start_year, end_year)
    log.info("=" * 60)

    all_cmes: List[dict] = []
    for year in range(start_year, end_year + 1):
        fname = f"donki_cme_{year}.json"
        dest = DONKI_DIR / fname
        if dest.exists() and dest.stat().st_size > 10:
            log.info("  SKIP: %s", fname)
            with open(dest) as f:
                all_cmes.extend(json.load(f) or [])
            continue

        params = {
            "startDate": f"{year}-01-01",
            "endDate": f"{year}-12-31",
        }
        url = f"{DONKI_BASE}/CMEAnalysis?" + urlencode(params)
        log.info("  Fetching DONKI %d ...", year)
        for attempt in range(1, 4):
            try:
                r = SESSION.get(url, timeout=60)
                r.raise_for_status()
                data = r.json() or []
                with open(dest, "w") as f:
                    json.dump(data, f, indent=2)
                all_cmes.extend(data)
                log.info("  OK: %s (%d events)", fname, len(data))
                break
            except Exception as exc:
                log.warning("  Attempt %d failed: %s", attempt, exc)
                time.sleep(5 * attempt)

    # Merge into one CSV for easy loading
    if all_cmes:
        _save_donki_csv(all_cmes)


def _save_donki_csv(events: List[dict]) -> None:
    import pandas as pd
    rows = []
    for e in events:
        rows.append({
            "time_21_5":        e.get("time21_5"),
            "latitude":         e.get("latitude"),
            "longitude":        e.get("longitude"),
            "half_angle":       e.get("halfAngle"),
            "speed":            e.get("speed"),
            "type":             e.get("type"),
            "is_most_accurate": e.get("isMostAccordantSource"),
            "note":             e.get("note", "")[:200],
        })
    df = pd.DataFrame(rows)
    out = DONKI_DIR / "donki_cme_all.csv"
    df.to_csv(out, index=False)
    log.info("  Merged DONKI CSV: %s (%d rows)", out.name, len(df))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. NOAA SWPC — Real-time + archive Kp 1-minute for 2020-2024
#    Supplements GFZ with higher-cadence recent data
# ═══════════════════════════════════════════════════════════════════════════════

def download_noaa_kp_archive() -> None:
    """Download NOAA 3-hourly Kp archive text files (2000–present)."""
    log.info("=" * 60)
    log.info("DATASET 5: NOAA SWPC Kp 3-hourly archive")
    log.info("=" * 60)

    base = "https://www.swpc.noaa.gov/pub/indices/old_indices/"
    ok = failed = skipped = 0
    for year in range(2000, datetime.utcnow().year + 1):
        fname = f"{year}_DGD.txt"
        dest = KP_DIR / fname
        if dest.exists() and dest.stat().st_size > 100:
            skipped += 1
            continue
        url = base + fname
        if _download(url, dest, desc=f"NOAA Kp {year}"):
            ok += 1
        else:
            failed += 1
    log.info("NOAA Kp: %d downloaded, %d skipped, %d failed", ok, failed, skipped)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="NAKSHATRA-KAVACH dataset downloader")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year",   type=int, default=2024)
    parser.add_argument("--quick",  action="store_true",
                        help="Download 2010-2023 only (~7M rows, faster)")
    parser.add_argument("--omni-only",  action="store_true")
    parser.add_argument("--kp-only",    action="store_true")
    parser.add_argument("--goes-only",  action="store_true")
    parser.add_argument("--donki-only", action="store_true")
    args = parser.parse_args()

    if args.quick:
        args.start_year = 2010
        args.end_year   = 2023

    only_flags = [args.omni_only, args.kp_only, args.goes_only, args.donki_only]
    run_all = not any(only_flags)

    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║  NAKSHATRA-KAVACH — Dataset Downloader                  ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.info("Output directory: %s", RAW)
    log.info("Year range: %d – %d", args.start_year, args.end_year)
    if args.quick:
        log.info("Mode: QUICK (~7M rows, 2010–2023)")
    else:
        log.info("Mode: FULL  (~12M rows, 2000–2024)")
    log.info("")

    t0 = time.time()

    if run_all or args.omni_only:
        download_omni(args.start_year, args.end_year)

    if run_all or args.kp_only:
        download_kp()
        download_noaa_kp_archive()

    if run_all or args.goes_only:
        goes_start = max(args.start_year, 2010)
        download_goes_xray(goes_start, args.end_year)

    if run_all or args.donki_only:
        donki_start = max(args.start_year, 2010)
        download_donki(donki_start, args.end_year)

    elapsed = time.time() - t0
    log.info("")
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║  DOWNLOAD COMPLETE in %.0f seconds                       ║", elapsed)
    log.info("╚══════════════════════════════════════════════════════════╝")
    _print_summary()


def _print_summary() -> None:
    log.info("Files downloaded:")
    total_bytes = 0
    for folder in [OMNI_DIR, KP_DIR, GOES_DIR, DONKI_DIR]:
        files = list(folder.glob("*"))
        size = sum(f.stat().st_size for f in files if f.is_file())
        total_bytes += size
        log.info("  %-20s  %3d files  %6.1f MB", folder.name, len(files), size / 1e6)
    log.info("  ─────────────────────────────────────────────────────")
    log.info("  TOTAL                         %6.1f MB", total_bytes / 1e6)
    log.info("")
    log.info("Next step:")
    log.info("  python build_training_dataset.py")


if __name__ == "__main__":
    main()
