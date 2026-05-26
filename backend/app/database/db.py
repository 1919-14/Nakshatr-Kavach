# backend/app/database/db.py
"""
NAKSHATRA-KAVACH — Layer 1: MySQL Database Manager
Handles connection lifecycle, schema initialization, and data retention cleanup.
Replaces the previous SQLite implementation. Public API is identical so all
callers (ingestion_service, feature_engineering, routes) require zero changes.

MySQL driver: PyMySQL (pure-Python, no C extensions required).
Connection settings are read from environment variables at module load time.
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# MySQL CONNECTION PARAMETERS — read from environment / config
# ─────────────────────────────────────────────────────────────────

_DB_HOST: str = os.getenv("MYSQL_HOST", "localhost")
_DB_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
_DB_NAME: str = os.getenv("MYSQL_DATABASE", "nakshatra_kavach")
_DB_USER: str = os.getenv("MYSQL_USER", "root")
_DB_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
_DB_CHARSET: str = "utf8mb4"

# Resolve schema SQL path relative to this file
_SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def set_db_path(path: Any) -> None:
    """
    Compatibility shim — SQLite used a file path; MySQL uses env vars.
    This function is intentionally a no-op so the app factory continues
    to work without modification.

    Args:
        path: Ignored. MySQL connection is configured via environment variables.
    """
    logger.debug(
        "set_db_path() called (MySQL mode) — connection params: %s@%s:%s/%s",
        _DB_USER, _DB_HOST, _DB_PORT, _DB_NAME,
    )


def _build_connection() -> pymysql.Connection:
    """
    Open and return a new PyMySQL connection.
    DictCursor is set so rows are returned as dicts (mirrors sqlite3.Row behaviour).

    Returns:
        pymysql.Connection ready for use.

    Raises:
        pymysql.Error: If the connection cannot be established.
    """
    conn = pymysql.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        database=_DB_NAME,
        user=_DB_USER,
        password=_DB_PASSWORD,
        charset=_DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )
    return conn


def get_connection() -> pymysql.Connection:
    """
    Create and return a new MySQL connection.
    Caller is responsible for closing or using as a context manager.

    Returns:
        pymysql.Connection configured for thread-safe use.
    """
    return _build_connection()


@contextmanager
def get_db() -> Generator[pymysql.Connection, None, None]:
    """
    Context-manager that yields a DB connection and commits/rollbacks on exit.
    Designed for use in Flask request contexts and scheduler jobs.

    Yields:
        pymysql.Connection — open and ready.

    Example:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """
    conn = _build_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialize the MySQL database by executing schema.sql.
    Safe to call multiple times — schema uses CREATE TABLE IF NOT EXISTS.

    The schema SQL is split on semicolons and executed statement-by-statement
    because MySQL does not support executescript(). Empty statements from
    trailing semicolons and comments-only blocks are skipped.

    After executing the base schema, dynamic ALTER TABLE migrations are run
    to add enhancement columns to existing tables without breaking live
    deployments. Each migration is wrapped in try/except to tolerate
    columns that already exist (MySQL error 1060).
    """
    if not _SCHEMA_FILE.exists():
        logger.critical("Schema file not found: %s", _SCHEMA_FILE)
        raise FileNotFoundError(f"Schema file missing: {_SCHEMA_FILE}")

    schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")

    # Split on ";" and execute non-empty statements
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]

    with get_db() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                # Skip comment-only blocks
                non_comment = "\n".join(
                    line for line in stmt.splitlines()
                    if not line.strip().startswith("--")
                ).strip()
                if non_comment:
                    try:
                        cur.execute(stmt)
                    except pymysql.Error as exc:
                        # MySQL raises error 1061 (Duplicate key name) or OperationalError
                        # on CREATE INDEX when it already exists — treat as harmless
                        if hasattr(exc, "args") and len(exc.args) > 0 and exc.args[0] in (1060, 1061):
                            logger.debug("Index/table element already exists (skipped): %s", exc)
                        elif "Duplicate key name" in str(exc):
                            logger.debug("Index already exists (skipped): %s", exc)
                        else:
                            raise

    # ── Dynamic migrations: add new columns to existing tables safely ──
    _run_dynamic_migrations()

    logger.info(
        "MySQL database initialized successfully — %s@%s:%s/%s",
        _DB_USER, _DB_HOST, _DB_PORT, _DB_NAME,
    )


def _run_dynamic_migrations() -> None:
    """
    Run ALTER TABLE migrations for enhancement columns.
    Each statement is individually wrapped so a pre-existing column
    (MySQL error 1060) does not abort the entire migration batch.
    """
    migration_stmts: List[Tuple[str, str]] = [
        # (description, SQL)
        (
            "Add dst_current to solar_wind_readings",
            "ALTER TABLE solar_wind_readings ADD COLUMN dst_current DOUBLE DEFAULT NULL",
        ),
        (
            "Add dst_classification to solar_wind_readings",
            "ALTER TABLE solar_wind_readings ADD COLUMN dst_classification VARCHAR(16) DEFAULT 'QUIET'",
        ),
        (
            "Add sep_alert_active to solar_wind_readings",
            "ALTER TABLE solar_wind_readings ADD COLUMN sep_alert_active TINYINT(1) DEFAULT 0",
        ),
        (
            "Add sep_class to solar_wind_readings",
            "ALTER TABLE solar_wind_readings ADD COLUMN sep_class VARCHAR(8) DEFAULT NULL",
        ),
        (
            "Add s4_scintillation_index to solar_wind_readings",
            "ALTER TABLE solar_wind_readings ADD COLUMN s4_scintillation_index DOUBLE DEFAULT NULL",
        ),
    ]
    with get_db() as conn:
        with conn.cursor() as cur:
            for description, stmt in migration_stmts:
                try:
                    cur.execute(stmt)
                    logger.info("Migration applied: %s", description)
                except pymysql.Error as exc:
                    if hasattr(exc, "args") and exc.args[0] == 1060:
                        logger.debug("Column already exists (migration skipped): %s", description)
                    else:
                        logger.warning("Migration failed for '%s': %s", description, exc)


# ─────────────────────────────────────────────────────────────────
# HELPER — named-placeholder (%s) conversion
# ─────────────────────────────────────────────────────────────────

def _named_to_positional(sql: str, record: Dict[str, Any]) -> Tuple[str, list]:
    """
    Convert SQLite-style :name placeholders to PyMySQL %s placeholders,
    returning the converted SQL and an ordered list of values.

    Args:
        sql:    SQL string with :key placeholders.
        record: Dict mapping key → value.

    Returns:
        (converted_sql, ordered_values)
    """
    import re
    keys = re.findall(r":(\w+)", sql)
    converted = re.sub(r":(\w+)", "%s", sql)
    values = [record.get(k) for k in keys]
    return converted, values


# ─────────────────────────────────────────────────────────────────
# DATA MANIPULATION FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def cleanup_old_data() -> Dict[str, int]:
    """
    Delete records older than the configured retention periods.
    Called daily by APScheduler Job 4.

    Returns:
        Dict mapping table name → number of rows deleted.
    """
    from app.utils.constants import (
        RETENTION_CME_EVENTS_DAYS,
        RETENTION_GRID_EVENTS_DAYS,
        RETENTION_GRID_RISK_DAYS,
        RETENTION_INGESTION_LOG_DAYS,
        RETENTION_NOAA_ALERTS_DAYS,
        RETENTION_SOLAR_WIND_DAYS,
        RETENTION_ADVISORY_HISTORY_DAYS,
        RETENTION_ADVISORY_TRIGGER_LOG_DAYS,
    )

    now_utc = datetime.utcnow()
    deleted_counts: Dict[str, int] = {}

    cutoffs: List[Tuple[str, str, int]] = [
        ("solar_wind_readings", "ingested_at",   RETENTION_SOLAR_WIND_DAYS),
        ("cme_events",          "created_at",    RETENTION_CME_EVENTS_DAYS),
        ("noaa_alerts",         "created_at",    RETENTION_NOAA_ALERTS_DAYS),
        ("ingestion_log",       "created_at",    RETENTION_INGESTION_LOG_DAYS),
        ("grid_risk_history",   "created_at",    RETENTION_GRID_RISK_DAYS),
        ("grid_events",         "created_at",    RETENTION_GRID_EVENTS_DAYS),
        ("advisory_history",    "created_at",    RETENTION_ADVISORY_HISTORY_DAYS),
        ("advisory_trigger_log","created_at",    RETENTION_ADVISORY_TRIGGER_LOG_DAYS),
    ]

    with get_db() as conn:
        with conn.cursor() as cur:
            for table, ts_col, days in cutoffs:
                cutoff_dt = now_utc - timedelta(days=days)
                cutoff_str = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
                cur.execute(
                    f"DELETE FROM {table} WHERE {ts_col} < %s",  # noqa: S608
                    (cutoff_str,),
                )
                deleted_counts[table] = cur.rowcount
                logger.info(
                    "Cleanup: removed %d rows from %s older than %d days",
                    cur.rowcount, table, days,
                )

    return deleted_counts


def insert_solar_wind_reading(record: Dict[str, Any]) -> Optional[int]:
    """
    Insert one validated solar wind reading into solar_wind_readings.
    Uses parameterized query. Returns new row ID or None on failure.

    Args:
        record: Dict matching the solar_wind_readings column names.

    Returns:
        New row ID (int) on success, None on failure.
    """
    sql_template = """
        INSERT INTO solar_wind_readings (
            timestamp_utc, ingested_at,
            bx_gsm, by_gsm, bz_gsm, bt_total,
            sw_speed_kmps, proton_density_ccm, proton_temp_kelvin,
            kp_estimated_from_sw, kp_current, kp_status,
            xray_flux_wm2, xray_class, xray_severity_numeric,
            cme_earth_directed, cme_speed_kmps, cme_arrival_minutes, cme_arrival_time_utc,
            transit_warning_minutes, epsilon_coupling, dynamic_pressure_npa,
            official_alert_class,
            data_quality_flag, bz_southward_flag, storm_onset_risk,
            source_dscovr_active, interpolated
        ) VALUES (
            :timestamp_utc, :ingested_at,
            :bx_gsm, :by_gsm, :bz_gsm, :bt_total,
            :sw_speed_kmps, :proton_density_ccm, :proton_temp_kelvin,
            :kp_estimated_from_sw, :kp_current, :kp_status,
            :xray_flux_wm2, :xray_class, :xray_severity_numeric,
            :cme_earth_directed, :cme_speed_kmps, :cme_arrival_minutes, :cme_arrival_time_utc,
            :transit_warning_minutes, :epsilon_coupling, :dynamic_pressure_npa,
            :official_alert_class,
            :data_quality_flag, :bz_southward_flag, :storm_onset_risk,
            :source_dscovr_active, :interpolated
        )
    """
    sql, values = _named_to_positional(sql_template, record)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                return cur.lastrowid
    except pymysql.err.IntegrityError as exc:
        logger.debug("Duplicate solar wind record skipped: %s", exc)
        return None
    except pymysql.Error as exc:
        logger.error("DB write failed for solar_wind_readings: %s", exc)
        return None


def timestamp_exists_in_sw(timestamp_utc: str) -> bool:
    """
    Check whether a reading with the given UTC timestamp already exists.
    Used for duplicate detection before inserting.

    Args:
        timestamp_utc: ISO 8601 UTC timestamp string.

    Returns:
        True if the record already exists, False otherwise.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM solar_wind_readings WHERE timestamp_utc = %s LIMIT 1",
                    (timestamp_utc,),
                )
                return cur.fetchone() is not None
    except pymysql.Error as exc:
        logger.error("DB read error in duplicate check: %s", exc)
        return False


def get_recent_field_values(field: str, limit: int = 15) -> List[Optional[float]]:
    """
    Retrieve the most recent non-null values for a specific field.
    Used by the interpolation logic in validate_solar_wind().

    Args:
        field: Column name in solar_wind_readings (validated against whitelist).
        limit: Maximum number of records to return.

    Returns:
        List of float values (most recent first), or empty list.
    """
    allowed_fields = {
        "bx_gsm", "by_gsm", "bz_gsm", "bt_total",
        "sw_speed_kmps", "proton_density_ccm", "proton_temp_kelvin",
        "kp_estimated_from_sw", "kp_current",
        "xray_flux_wm2", "epsilon_coupling", "dynamic_pressure_npa",
    }
    if field not in allowed_fields:
        logger.warning("get_recent_field_values: rejected unknown field '%s'", field)
        return []

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {field} FROM solar_wind_readings "  # noqa: S608
                    f"WHERE {field} IS NOT NULL "
                    f"ORDER BY timestamp_utc DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
                return [row[field] for row in rows]
    except pymysql.Error as exc:
        logger.error("DB read error in get_recent_field_values(%s): %s", field, exc)
        return []


def insert_cme_event(record: Dict[str, Any]) -> Optional[int]:
    """
    Insert a new CME event record. Returns new row ID or None on failure.

    Args:
        record: Dict matching cme_events column schema.

    Returns:
        New row ID or None.
    """
    sql_template = """
        INSERT INTO cme_events (
            detected_at_utc, cme_launch_time_utc, speed_kmps,
            half_angle_deg, latitude_deg, longitude_deg,
            is_earth_directed, estimated_arrival_utc, estimated_duration_hr,
            arrival_minutes_from_detection, catalog_source, donki_link, active
        ) VALUES (
            :detected_at_utc, :cme_launch_time_utc, :speed_kmps,
            :half_angle_deg, :latitude_deg, :longitude_deg,
            :is_earth_directed, :estimated_arrival_utc, :estimated_duration_hr,
            :arrival_minutes_from_detection, :catalog_source, :donki_link, :active
        )
    """
    sql, values = _named_to_positional(sql_template, record)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                return cur.lastrowid
    except pymysql.Error as exc:
        logger.error("DB write failed for cme_events: %s", exc)
        return None


def insert_noaa_alert(record: Dict[str, Any]) -> Optional[int]:
    """
    Insert a NOAA alert. Silently skips duplicates (product_id is UNIQUE).

    Args:
        record: Dict matching noaa_alerts column schema.

    Returns:
        New row ID, or None if duplicate or failure.
    """
    sql_template = """
        INSERT IGNORE INTO noaa_alerts (
            product_id, issue_datetime_utc, alert_code, storm_class, full_message
        ) VALUES (
            :product_id, :issue_datetime_utc, :alert_code, :storm_class, :full_message
        )
    """
    sql, values = _named_to_positional(sql_template, record)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                if cur.rowcount == 0:
                    logger.debug("Duplicate NOAA alert ignored: %s", record.get("product_id"))
                    return None
                return cur.lastrowid
    except pymysql.Error as exc:
        logger.error("DB write failed for noaa_alerts: %s", exc)
        return None


def log_ingestion_attempt(
    source_name: str,
    success: bool,
    records_ingested: int = 0,
    error_message: Optional[str] = None,
    response_time_ms: Optional[float] = None,
) -> None:
    """
    Write one row to ingestion_log for audit and monitoring purposes.

    Args:
        source_name:       Human-readable source identifier (e.g. "noaa_swpc").
        success:           True if the poll succeeded.
        records_ingested:  Number of records successfully stored.
        error_message:     Error string (or None on success).
        response_time_ms:  HTTP round-trip time in milliseconds.
    """
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = """
        INSERT INTO ingestion_log (
            source_name, poll_timestamp_utc, success,
            records_ingested, error_message, response_time_ms
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        source_name,
                        now_str,
                        1 if success else 0,
                        records_ingested,
                        error_message,
                        response_time_ms,
                    ),
                )
    except pymysql.Error as exc:
        # Never raise — the audit log must never crash the pipeline
        logger.warning("Failed to write ingestion_log entry: %s", exc)


def get_solar_wind_history(
    hours: int = 24,
    quality_filter: Optional[List[str]] = None,
    limit: int = 10_000,
) -> List[Dict[str, Any]]:
    """
    Retrieve historical solar wind readings for the REST history endpoint
    and for Layer 2 feature engineering.

    Args:
        hours:          How far back to query (max 168 = 7 days).
        quality_filter: List of quality flags to include (e.g. ["GOOD","PARTIAL"]).
        limit:          Maximum number of rows to return.

    Returns:
        List of dicts (one per record), newest first.
    """
    cutoff_dt = datetime.utcnow() - timedelta(hours=min(hours, 168))
    cutoff_str = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    base_sql = "SELECT * FROM solar_wind_readings WHERE ingested_at >= %s "
    params: list = [cutoff_str]

    if quality_filter:
        placeholders = ", ".join(["%s"] * len(quality_filter))
        base_sql += f"AND data_quality_flag IN ({placeholders}) "
        params.extend(quality_filter)

    base_sql += "ORDER BY timestamp_utc DESC LIMIT %s"
    params.append(min(limit, 10_000))

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(base_sql, params)
                return cur.fetchall()
    except pymysql.Error as exc:
        logger.error("DB read error in get_solar_wind_history: %s", exc)
        return []


def get_recent_cme_events(days: int = 7) -> List[Dict[str, Any]]:
    """
    Retrieve active CME events from the last N days.

    Args:
        days: Look-back window.

    Returns:
        List of dicts ordered by estimated arrival ascending.
    """
    cutoff_str = (datetime.utcnow() - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM cme_events WHERE detected_at_utc >= %s "
                    "ORDER BY estimated_arrival_utc ASC",
                    (cutoff_str,),
                )
                return cur.fetchall()
    except pymysql.Error as exc:
        logger.error("DB read error in get_recent_cme_events: %s", exc)
        return []


def get_recent_alerts(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Retrieve the most recent NOAA alerts.

    Args:
        limit: Maximum number of alerts to return.

    Returns:
        List of dicts, newest first.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM noaa_alerts ORDER BY issue_datetime_utc DESC LIMIT %s",
                    (limit,),
                )
                return cur.fetchall()
    except pymysql.Error as exc:
        logger.error("DB read error in get_recent_alerts: %s", exc)
        return []


def get_last_ingestion_status() -> Dict[str, Any]:
    """
    Return the most recent successful poll timestamp per source.
    Used by the /api/solar/status endpoint.

    Returns:
        Dict mapping source_name → {"status": "OK"/"FAIL", "last_success_utc": str}.
    """
    known_sources = ["noaa_swpc", "noaa_kp", "nasa_donki", "goes_xray", "noaa_alerts"]
    result: Dict[str, Any] = {}

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                for source in known_sources:
                    cur.execute(
                        "SELECT poll_timestamp_utc, success FROM ingestion_log "
                        "WHERE source_name = %s ORDER BY poll_timestamp_utc DESC LIMIT 1",
                        (source,),
                    )
                    row = cur.fetchone()
                    if row:
                        result[source] = {
                            "status": "OK" if row["success"] else "FAIL",
                            "last_success_utc": row["poll_timestamp_utc"],
                        }
                    else:
                        result[source] = {"status": "UNKNOWN", "last_success_utc": None}
    except pymysql.Error as exc:
        logger.error("DB read error in get_last_ingestion_status: %s", exc)

    return result


# ─────────────────────────────────────────────────────────────────
# DST INDEX — INSERT + QUERY
# ─────────────────────────────────────────────────────────────────

def insert_dst_reading(record: Dict[str, Any]) -> Optional[int]:
    """
    Insert one Dst index reading into dst_history.
    Silently skips duplicates (timestamp_utc is UNIQUE).

    Args:
        record: Dict with keys: timestamp_utc, ingested_at, dst_nt,
                dst_classification, source_url, data_quality.

    Returns:
        New row ID or None.
    """
    sql = """
        INSERT IGNORE INTO dst_history
            (timestamp_utc, ingested_at, dst_nt, dst_classification, source_url, data_quality)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    record.get("timestamp_utc"),
                    record.get("ingested_at"),
                    record.get("dst_nt"),
                    record.get("dst_classification", "QUIET"),
                    record.get("source_url"),
                    record.get("data_quality", "UNKNOWN"),
                ))
                return cur.lastrowid if cur.rowcount > 0 else None
    except pymysql.Error as exc:
        logger.error("DB write failed for dst_history: %s", exc)
        return None


def get_dst_history(hours: int = 24, limit: int = 1440) -> List[Dict[str, Any]]:
    """
    Retrieve Dst index history for the past N hours.

    Args:
        hours: Look-back window (default 24).
        limit: Maximum rows to return.

    Returns:
        List of dicts, newest first.
    """
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=min(hours, 168))).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM dst_history WHERE timestamp_utc >= %s "
                    "ORDER BY timestamp_utc DESC LIMIT %s",
                    (cutoff, min(limit, 10_000)),
                )
                return cur.fetchall()
    except pymysql.Error as exc:
        logger.error("DB read error in get_dst_history: %s", exc)
        return []


def get_latest_dst() -> Optional[Dict[str, Any]]:
    """Return the single most recent Dst reading, or None."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM dst_history ORDER BY timestamp_utc DESC LIMIT 1"
                )
                return cur.fetchone()
    except pymysql.Error as exc:
        logger.error("DB read error in get_latest_dst: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────
# SEP PROTON FLUX — INSERT + QUERY
# ─────────────────────────────────────────────────────────────────

def insert_sep_reading(record: Dict[str, Any]) -> Optional[int]:
    """
    Insert one SEP proton flux reading into sep_history.

    Args:
        record: Dict with SEP flux fields.

    Returns:
        New row ID or None.
    """
    sql = """
        INSERT IGNORE INTO sep_history
            (timestamp_utc, ingested_at, proton_flux_gt10mev, proton_flux_gt100mev,
             sep_alert_active, sep_class, peak_flux, data_quality, source_satellite)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    record.get("timestamp_utc"),
                    record.get("ingested_at"),
                    record.get("proton_flux_gt10mev"),
                    record.get("proton_flux_gt100mev"),
                    1 if record.get("sep_alert_active") else 0,
                    record.get("sep_class"),
                    record.get("peak_flux"),
                    record.get("data_quality", "UNKNOWN"),
                    record.get("source_satellite"),
                ))
                return cur.lastrowid if cur.rowcount > 0 else None
    except pymysql.Error as exc:
        logger.error("DB write failed for sep_history: %s", exc)
        return None


def get_sep_history(hours: int = 24, limit: int = 288) -> List[Dict[str, Any]]:
    """Retrieve SEP proton flux history for the past N hours, newest first."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=min(hours, 168))).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM sep_history WHERE timestamp_utc >= %s "
                    "ORDER BY timestamp_utc DESC LIMIT %s",
                    (cutoff, min(limit, 5000)),
                )
                return cur.fetchall()
    except pymysql.Error as exc:
        logger.error("DB read error in get_sep_history: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────
# SCINTILLATION — INSERT + QUERY
# ─────────────────────────────────────────────────────────────────

def insert_scintillation_reading(record: Dict[str, Any]) -> Optional[int]:
    """Persist a NavIC ionospheric scintillation calculation result."""
    sql = """
        INSERT INTO scintillation_history
            (timestamp_utc, kp_used, xray_severity, s4_index, scintillation_class,
             positioning_error_m, navic_status, diurnal_phase, magnetic_lat_deg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    record.get("timestamp_utc"),
                    record.get("kp_used"),
                    record.get("xray_severity"),
                    record.get("s4_index"),
                    record.get("scintillation_class"),
                    record.get("positioning_error_m"),
                    record.get("navic_status"),
                    record.get("diurnal_phase"),
                    record.get("magnetic_lat_deg"),
                ))
                return cur.lastrowid
    except pymysql.Error as exc:
        logger.error("DB write failed for scintillation_history: %s", exc)
        return None


def get_scintillation_history(hours: int = 24) -> List[Dict[str, Any]]:
    """Retrieve scintillation history for the past N hours, newest first."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=min(hours, 72))).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM scintillation_history WHERE timestamp_utc >= %s "
                    "ORDER BY timestamp_utc DESC LIMIT 1000",
                    (cutoff,),
                )
                return cur.fetchall()
    except pymysql.Error as exc:
        logger.error("DB read error in get_scintillation_history: %s", exc)
        return []
