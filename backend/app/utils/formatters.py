# backend/app/utils/formatters.py
"""
NAKSHATRA-KAVACH — Layer 1: Date/Time Formatters and Utilities
All time-related helper functions used across Layer 1.
Rule: All internal computation and storage is UTC. IST is display-only.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

# India Standard Time offset from UTC
IST_OFFSET = timedelta(hours=5, minutes=30)


def utcnow_iso() -> str:
    """
    Return the current UTC datetime as an ISO 8601 string.

    Returns:
        String like "2024-05-10T14:32:00Z"
    """
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert a datetime object to an ISO 8601 UTC string.
    If the datetime is timezone-aware, it is converted to UTC first.

    Args:
        dt: A datetime object (naive assumed UTC, or tz-aware).

    Returns:
        ISO 8601 UTC string or None if dt is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """
    Parse any reasonable timestamp string into a naive UTC datetime.
    Handles NOAA format "2024-05-10 14:32:00.000", ISO 8601 with Z suffix,
    and ISO 8601 with timezone offsets.

    Args:
        ts_str: Timestamp string from any API source.

    Returns:
        Naive UTC datetime object, or None on parse failure.
    """
    if not ts_str:
        return None
    try:
        dt = dateutil_parser.parse(ts_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, OverflowError) as exc:
        logger.warning("Failed to parse timestamp '%s': %s", ts_str, exc)
        return None


def data_age_seconds(timestamp_utc: Optional[str]) -> float:
    """
    Calculate how many seconds ago a UTC timestamp was.

    Args:
        timestamp_utc: ISO 8601 UTC string or None.

    Returns:
        Age in seconds (float). Returns 9999.0 if timestamp is None or unparseable.
    """
    if not timestamp_utc:
        return 9999.0
    dt = parse_utc_timestamp(timestamp_utc)
    if dt is None:
        return 9999.0
    delta = datetime.utcnow() - dt
    return max(delta.total_seconds(), 0.0)


def utc_to_ist_str(timestamp_utc: Optional[str]) -> Optional[str]:
    """
    Convert a UTC ISO 8601 string to IST string for display purposes only.
    NEVER use IST internally. This is for frontend/PDF export labels only.

    Args:
        timestamp_utc: UTC ISO 8601 string.

    Returns:
        IST string like "2024-05-10 20:02:00 IST" or None.
    """
    if not timestamp_utc:
        return None
    dt = parse_utc_timestamp(timestamp_utc)
    if dt is None:
        return None
    ist_dt = dt + IST_OFFSET
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")


def minutes_until(target_utc: Optional[str]) -> Optional[float]:
    """
    Return how many minutes from now until a future UTC timestamp.
    Returns None if the target is in the past or cannot be parsed.

    Args:
        target_utc: Future UTC ISO 8601 string.

    Returns:
        Minutes as float, or None.
    """
    if not target_utc:
        return None
    dt = parse_utc_timestamp(target_utc)
    if dt is None:
        return None
    delta = dt - datetime.utcnow()
    minutes = delta.total_seconds() / 60.0
    return minutes if minutes > 0 else None


def format_countdown(minutes: Optional[float]) -> str:
    """
    Format a minutes value as a MM:SS countdown string for the dashboard.

    Args:
        minutes: Number of minutes (float).

    Returns:
        Formatted string like "35:42" or "--:--" if None.
    """
    if minutes is None or minutes < 0:
        return "--:--"
    total_seconds = int(minutes * 60)
    mm = total_seconds // 60
    ss = total_seconds % 60
    return f"{mm:02d}:{ss:02d}"


def rolling_window_start(hours: int) -> str:
    """
    Return an ISO 8601 UTC string for N hours ago. Used for DONKI date queries.

    Args:
        hours: Number of hours to look back.

    Returns:
        UTC ISO 8601 string suitable for DONKI API date parameters.
    """
    dt = datetime.utcnow() - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%d")


def today_utc_str() -> str:
    """
    Return today's UTC date as YYYY-MM-DD string for API queries.

    Returns:
        Date string like "2024-05-10".
    """
    return datetime.utcnow().strftime("%Y-%m-%d")
