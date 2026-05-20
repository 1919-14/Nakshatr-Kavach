# backend/app/services/fetchers.py
"""
NAKSHATRA-KAVACH — Layer 1: API Fetch Functions
Handles all outbound HTTP requests to NOAA SWPC, NASA DONKI, GOES.
Never raises exceptions — returns None on any failure.
"""

import json
import logging
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
) -> Optional[Any]:
    """
    Perform an HTTP GET with exponential-ish retry logic.
    Returns parsed JSON on success, None after all retries fail.

    Args:
        url:         Full endpoint URL.
        source_name: Human-readable source label for logging/audit.
        params:      Optional query parameters dict.
        max_retries: Number of attempts (default 3).
        timeout:     Per-request timeout in seconds.

    Returns:
        Parsed JSON (list or dict) or None.
    """
    start_ms = time.monotonic() * 1000

    for attempt in range(max_retries):
        delay = RETRY_DELAYS_S[attempt] if attempt < len(RETRY_DELAYS_S) else 15
        if delay > 0:
            logger.debug("Retry %d for %s — sleeping %ds", attempt + 1, source_name, delay)
            time.sleep(delay)

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
            logger.warning("API timeout: %s (attempt %d)", source_name, attempt + 1)
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
        error_message=f"All {max_retries} retries failed",
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
