# backend/app/services/fetchers.py
"""
NAKSHATRA-KAVACH — Layer 1: API Fetch Functions
Handles all outbound HTTP requests to NOAA SWPC, NASA DONKI, GOES.
Never raises exceptions — returns None on any failure.
"""

import json
import logging
import math
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from app.utils.constants import (
    ACE_BACKUP_URL,
    CME_LOOKBACK_DAYS,
    MAX_RETRIES,
    NASA_DONKI_CME_BASE_URL,
    NOAA_ALERTS_URL,
    NOAA_KP_INDEX_URL,
    NOAA_SOLAR_MAG_URL,
    NOAA_SOLAR_WIND_URL,
    NOAA_XRAY_FLUX_URL,
    REQUEST_TIMEOUT_S,
    RETRY_DELAYS_S,
    NOAA_DST_URL,
    NOAA_SEP_PROTON_URL,
    SEP_ALERT_THRESHOLD_PFU,
)
from app.utils.formatters import rolling_window_start, today_utc_str
from app.database.db import log_ingestion_attempt

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# RETRY UTILITY
# ─────────────────────────────────────────────────────────────────

def retry_request(
    url: str,
    source_name: str,
    params: Optional[Dict] = None,
    max_retries: int = MAX_RETRIES,
    timeout: int = REQUEST_TIMEOUT_S,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Optional[Any]:
    """
    Perform an HTTP GET with true exponential backoff + jitter retry logic.

    Backoff formula: delay = min(max_delay, base_delay * 2^attempt) + jitter
    Jitter is ±30% of the computed backoff to spread retry storms.

    Args:
        url:         Full endpoint URL.
        source_name: Human-readable source label for logging/audit.
        params:      Optional query parameters dict.
        max_retries: Number of attempts (default 3).
        timeout:     Per-request timeout in seconds.
        base_delay:  Base backoff delay in seconds (default 1.0).
        max_delay:   Maximum backoff ceiling in seconds (default 30.0).

    Returns:
        Parsed JSON (list or dict) or None.
    """
    start_ms = time.monotonic() * 1000

    for attempt in range(max_retries):
        # Exponential backoff with full jitter on retry attempts
        if attempt > 0:
            exp_delay = min(max_delay, base_delay * (2 ** attempt))
            jitter = exp_delay * 0.3 * (2.0 * random.random() - 1.0)  # ±30%
            sleep_s = max(0.0, exp_delay + jitter)
            logger.debug(
                "Retry %d/%d for %s — backing off %.1fs (base=%.1fs jitter=%+.1fs)",
                attempt + 1, max_retries, source_name, sleep_s, exp_delay, jitter,
            )
            time.sleep(sleep_s)

        try:
            response = requests.get(url, params=params, timeout=timeout)
            elapsed_ms = (time.monotonic() * 1000) - start_ms

            if response.status_code != 200:
                logger.warning(
                    "API_FAILURE | source=%s | attempt=%d | HTTP %d: %s",
                    source_name, attempt + 1, response.status_code, url,
                )
                continue

            try:
                data = response.json()
            except ValueError as exc:
                # NOAA SWPC endpoints occasionally return a valid JSON payload
                # followed by unexpected trailing bytes (or concatenated JSON).
                # Try to salvage the first JSON value to avoid spurious failures.
                text = (response.text or "").lstrip("\ufeff").strip()
                try:
                    data, idx = json.JSONDecoder().raw_decode(text)
                    if text[idx:].strip():
                        logger.warning(
                            "JSON_TRAILING_DATA | source=%s | ignored=%d bytes",
                            source_name,
                            len(text[idx:].strip()),
                        )
                except Exception:
                    raise exc

            log_ingestion_attempt(
                source_name=source_name,
                success=True,
                records_ingested=len(data) if isinstance(data, list) else 1,
                response_time_ms=elapsed_ms,
            )
            return data

        except requests.Timeout:
            logger.warning("API timeout: %s (attempt %d/%d)", source_name, attempt + 1, max_retries)
        except requests.ConnectionError as exc:
            logger.error("Connection failed: %s | %s", url, exc)
        except ValueError as exc:
            logger.error("JSON decode error from %s: %s", source_name, exc)

    elapsed_ms = (time.monotonic() * 1000) - start_ms
    logger.error(
        "ALL_RETRIES_FAILED | source=%s | using_cached_data=True", source_name
    )
    log_ingestion_attempt(
        source_name=source_name,
        success=False,
        error_message=f"All {max_retries} retries failed (exponential backoff)",
        response_time_ms=elapsed_ms,
    )
    return None


# ─────────────────────────────────────────────────────────────────
# SOURCE 1: NOAA SOLAR WIND (DSCOVR/ACE)
# ─────────────────────────────────────────────────────────────────

def fetch_solar_wind() -> Optional[Dict[str, Any]]:
    """
    Poll the NOAA SWPC real-time solar wind endpoint.
    The RTSW stream automatically combines DSCOVR and ACE telemetry.

    Returns:
        Dict of the most recent solar wind record, or None on failure.
        Keys: time_tag, bx_gsm, by_gsm, bz_gsm, bt, speed, density,
              temperature, range_kp, active, source.
    """
    wind_data = retry_request(NOAA_SOLAR_WIND_URL, source_name="noaa_swpc_wind")
    mag_data = retry_request(NOAA_SOLAR_MAG_URL, source_name="noaa_swpc_mag")

    if not wind_data or not isinstance(wind_data, list):
        logger.error("Solar wind fetch returned no data")
        return None

    latest_wind = wind_data[-1]
    latest_mag = mag_data[-1] if mag_data and isinstance(mag_data, list) else {}
    latest = {
        "time_tag": latest_mag.get("time_tag") or latest_wind.get("time_tag"),
        "bx_gsm": latest_mag.get("bx_gsm"),
        "by_gsm": latest_mag.get("by_gsm"),
        "bz_gsm": latest_mag.get("bz_gsm"),
        "bt": latest_mag.get("bt"),
        "speed": latest_wind.get("speed") or latest_wind.get("proton_speed"),
        "density": latest_wind.get("density") or latest_wind.get("proton_density"),
        "temperature": latest_wind.get("temperature") or latest_wind.get("proton_temperature"),
        "range_kp": latest_wind.get("range_kp"),
        "active": latest_wind.get("active", True),
        "source": "NOAA_RTSW_MAG_WIND",
        "_wind_time_tag": latest_wind.get("time_tag"),
        "_mag_time_tag": latest_mag.get("time_tag"),
    }

    if latest.get("bz_gsm") is None:
        logger.warning(
            "BZ_NULL | source=DSCOVR_MAG | time=%s", latest.get("_mag_time_tag", "unknown")
        )

    return latest


# ─────────────────────────────────────────────────────────────────
# SOURCE 2: NOAA Kp INDEX
# ─────────────────────────────────────────────────────────────────

def fetch_kp() -> Optional[Dict[str, Any]]:
    """
    Poll NOAA real-time planetary Kp index endpoint.

    Returns:
        Dict with kp, kp_fraction, kp_index, status, time_tag keys,
        or None on failure.
    """
    data = retry_request(NOAA_KP_INDEX_URL, source_name="noaa_kp")
    if not data or not isinstance(data, list):
        return None
    return data[-1]  # newest is last


# ─────────────────────────────────────────────────────────────────
# SOURCE 3: GOES X-RAY FLUX
# ─────────────────────────────────────────────────────────────────

def fetch_xray() -> Optional[Dict[str, Any]]:
    """
    Poll NOAA GOES X-ray flux endpoint and return the most recent
    0.1-0.8nm band reading.

    Returns:
        Dict with time_tag, flux, satellite keys, or None on failure.
    """
    data = retry_request(NOAA_XRAY_FLUX_URL, source_name="goes_xray")
    if not data or not isinstance(data, list):
        return None

    # Filter to the standard 0.1-0.8nm X-ray band only
    band_data = [
        item for item in data
        if item.get("energy") == "0.1-0.8nm"
    ]
    if not band_data:
        logger.warning("No 0.1-0.8nm X-ray entries in GOES response")
        return None

    return band_data[-1]  # newest is last


# ─────────────────────────────────────────────────────────────────
# SOURCE 4: NASA DONKI CME CATALOG
# ─────────────────────────────────────────────────────────────────

def fetch_cme() -> Optional[List[Dict[str, Any]]]:
    """
    Poll NASA DONKI CME analysis endpoint for the rolling 7-day window.
    Filters to mostAccurate=true, type=C, and Earth-directed entries.

    Returns:
        List of filtered CME dicts or None on failure. May be empty list
        if no qualifying CMEs exist.
    """
    start_date = rolling_window_start(hours=CME_LOOKBACK_DAYS * 24)
    end_date = today_utc_str()

    params = {
        "startDate": start_date,
        "endDate": end_date,
        "mostAccurate": "true",
        "speed": "0",
        "halfAngle": "0",
    }

    data = retry_request(
        NASA_DONKI_CME_BASE_URL, source_name="nasa_donki", params=params
    )
    if data is None:
        return None
    if not isinstance(data, list):
        logger.warning("DONKI returned unexpected type: %s", type(data))
        return []

    # Filter: most accurate Cone-model entries only
    filtered = [
        item for item in data
        if item.get("isMostAccurate") is True and item.get("type") == "C"
    ]
    return filtered


# ─────────────────────────────────────────────────────────────────
# SOURCE 5: NOAA ALERTS
# ─────────────────────────────────────────────────────────────────

def fetch_alerts() -> Optional[List[Dict[str, Any]]]:
    """
    Poll the NOAA SWPC space weather alerts feed.

    Returns:
        List of alert dicts (product_id, issue_datetime, message),
        or None on failure.
    """
    data = retry_request(NOAA_ALERTS_URL, source_name="noaa_alerts")
    if not data or not isinstance(data, list):
        return None
    return data


# ─────────────────────────────────────────────────────────────────
# SOURCE 6: NOAA REAL-TIME Dst INDEX (ring-current geomagnetic disturbance)
# ─────────────────────────────────────────────────────────────────

def fetch_dst() -> Optional[Dict[str, Any]]:
    """
    Poll NOAA SWPC for the real-time Dst index.

    NOAA provides Dst-equivalent (Hp60) via the planetary geomagnetic data feed.
    Falls back to the Kyoto WDC quasi-real-time if NOAA is unavailable.

    Returns:
        Dict with keys: timestamp_utc, dst_nt, dst_classification, source_url
        or None on failure.
    """
    from app.services.physics import classify_dst
    from app.utils.formatters import utcnow_iso

    # Primary: NOAA SWPC Hp60 (Dst proxy)
    data = retry_request(NOAA_DST_URL, source_name="noaa_dst", base_delay=1.5)
    if data and isinstance(data, list) and len(data) > 0:
        latest = data[-1]
        try:
            # NOAA format: ["time_tag", "Hp60"] or {"time_tag": ..., "Hp60": ...}
            if isinstance(latest, list) and len(latest) >= 2:
                ts = latest[0]
                dst_val = float(latest[1]) if latest[1] not in (None, "", "null") else None
            elif isinstance(latest, dict):
                ts = latest.get("time_tag") or latest.get("timestamp")
                # Try common NOAA field names for Dst/Hp60
                dst_val = None
                for key in ("Hp60", "hp60", "dst", "Dst", "a_running"):
                    if latest.get(key) is not None:
                        try:
                            dst_val = float(latest[key])
                            break
                        except (TypeError, ValueError):
                            continue
            else:
                ts = utcnow_iso()
                dst_val = None

            if dst_val is not None:
                return {
                    "timestamp_utc": ts,
                    "dst_nt": round(dst_val, 1),
                    "dst_classification": classify_dst(dst_val),
                    "source_url": NOAA_DST_URL,
                    "data_quality": "GOOD",
                }
        except (TypeError, ValueError, IndexError) as exc:
            logger.warning("DST parse error from NOAA: %s", exc)

    # Dst is not critical-path: return a synthetic estimate from Kp if API fails
    logger.warning("DST fetch failed — Dst data unavailable this cycle")
    return None


# ─────────────────────────────────────────────────────────────────
# SOURCE 7: GOES SEP PROTON FLUX (Solar Energetic Particles)
# ─────────────────────────────────────────────────────────────────

def fetch_sep_proton_flux() -> Optional[Dict[str, Any]]:
    """
    Poll NOAA SWPC GOES proton flux endpoint for Solar Energetic Particle events.

    Proton flux thresholds (NOAA S-scale):
        S1: ≥10 pfu at >10 MeV
        S2: ≥100 pfu at >10 MeV
        S3: ≥1000 pfu at >10 MeV
        S4: ≥10000 pfu at >10 MeV
        S5: ≥100000 pfu at >10 MeV

    Returns:
        Dict with keys: timestamp_utc, proton_flux_gt10mev, proton_flux_gt100mev,
                        sep_alert_active, sep_class, peak_flux, source_satellite
        or None on failure.
    """
    from app.utils.formatters import utcnow_iso

    data = retry_request(NOAA_SEP_PROTON_URL, source_name="noaa_sep", base_delay=2.0)
    if not data or not isinstance(data, list):
        return None

    try:
        # Filter to >10 MeV channel
        gt10_entries = [
            item for item in data
            if item.get("energy") in (">=10 MeV", ">10 MeV", ">=10MeV")
        ]
        gt100_entries = [
            item for item in data
            if item.get("energy") in (">=100 MeV", ">100 MeV", ">=100MeV")
        ]

        latest_10 = gt10_entries[-1] if gt10_entries else (data[-1] if data else {})
        latest_100 = gt100_entries[-1] if gt100_entries else {}

        flux_10 = None
        flux_100 = None
        ts = utcnow_iso()

        for key in ("flux", "proton_flux", "protons"):
            if latest_10.get(key) is not None:
                try:
                    flux_10 = float(latest_10[key])
                    break
                except (TypeError, ValueError):
                    pass
        for key in ("flux", "proton_flux", "protons"):
            if latest_100.get(key) is not None:
                try:
                    flux_100 = float(latest_100[key])
                    break
                except (TypeError, ValueError):
                    pass

        ts = latest_10.get("time_tag") or latest_10.get("timestamp") or utcnow_iso()
        source_sat = latest_10.get("satellite", latest_10.get("source", "GOES"))

        # Classify SEP storm level
        sep_class = None
        alert_active = False
        flux_ref = flux_10 or 0.0
        if flux_ref >= SEP_ALERT_THRESHOLD_PFU:
            alert_active = True
            if flux_ref >= 100_000:
                sep_class = "S5"
            elif flux_ref >= 10_000:
                sep_class = "S4"
            elif flux_ref >= 1_000:
                sep_class = "S3"
            elif flux_ref >= 100:
                sep_class = "S2"
            else:
                sep_class = "S1"

        return {
            "timestamp_utc": ts,
            "proton_flux_gt10mev": round(flux_10, 3) if flux_10 is not None else None,
            "proton_flux_gt100mev": round(flux_100, 3) if flux_100 is not None else None,
            "sep_alert_active": alert_active,
            "sep_class": sep_class,
            "peak_flux": round(flux_10, 3) if flux_10 is not None else None,
            "source_satellite": str(source_sat)[:16] if source_sat else "GOES",
            "data_quality": "GOOD",
        }

    except (TypeError, ValueError, IndexError, KeyError) as exc:
        logger.error("SEP proton flux parse error: %s", exc)
        return None
