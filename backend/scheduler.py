# backend/scheduler.py
"""
NAKSHATRA-KAVACH — Layer 1: APScheduler Job Definitions
All 5 background jobs. Imported and started from the Flask app factory.
Uses BackgroundScheduler so Flask API remains non-blocking.
"""

import atexit
import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from app.utils.constants import (
    POLL_INTERVAL_CME_S,
    POLL_INTERVAL_CLEANUP_S,
    POLL_INTERVAL_SOLAR_WIND_S,
    POLL_INTERVAL_XRAY_S,
)

logger = logging.getLogger(__name__)

# Module-level scheduler instance (singleton)
scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce": True,      # if missed, run once not multiple times
        "max_instances": 1,    # never run the same job twice simultaneously
        "misfire_grace_time": 30,
    },
    timezone="UTC",
)


def _on_job_error(event: Any) -> None:
    """Log scheduler job exceptions at ERROR level."""
    logger.error(
        "SCHEDULER_JOB_ERROR | job_id=%s | error=%s",
        event.job_id,
        event.exception,
        exc_info=True,
    )


def _on_job_executed(event: Any) -> None:
    """Log successful scheduler job executions at DEBUG level."""
    logger.debug("Scheduler job executed: %s (runtime=%.2fs)", event.job_id, event.retval or 0)


scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)


# ─────────────────────────────────────────────────────────────────
# JOB FUNCTIONS — thin wrappers that import from services
# ─────────────────────────────────────────────────────────────────

def _job_solar_wind_and_kp() -> None:
    """
    Job 1: Poll NOAA solar wind and Kp every 60 seconds.
    Fetches, validates, updates snapshot, persists to DB, and triggers Layer 2.
    """
    from app.services.ingestion_service import run_solar_wind_and_kp_poll
    run_solar_wind_and_kp_poll()
    try:
        from app.services.feature_engineering import compute_features_realtime, update_latest_features

        feature_result = compute_features_realtime()
        if feature_result:
            update_latest_features(feature_result)
            metadata = feature_result.get("feature_metadata", {})
            features = metadata.get("features", [])
            bz_value = features[0]["value"] if len(features) > 0 else 0.0
            epsilon_value = features[19]["value"] if len(features) > 19 else 0.0
            logger.info(
                "Features computed: Bz=%.1f Eps=%.2f Quality=%s",
                bz_value,
                epsilon_value,
                feature_result.get("data_quality", "UNKNOWN"),
            )
    except Exception as exc:
        logger.error("Layer 2 feature engineering trigger failed: %s", exc, exc_info=True)


def _job_xray_and_alerts() -> None:
    """
    Job 2: Poll GOES X-ray flux and NOAA alerts every 5 minutes.
    Updates xray and alert sections of the snapshot.
    """
    from app.services.ingestion_service import run_xray_and_alerts_poll
    run_xray_and_alerts_poll()


def _job_cme() -> None:
    """
    Job 3: Poll NASA DONKI CME catalog every 30 minutes.
    Updates CME section of snapshot and persists Earth-directed events to DB.
    """
    from app.services.ingestion_service import run_cme_poll
    run_cme_poll()


def _job_cleanup() -> None:
    """
    Job 4: Delete records beyond retention policy once per day.
    Keeps DB lean without manual intervention.
    """
    from app.database.db import cleanup_old_data
    deleted = cleanup_old_data()
    logger.info("Daily cleanup complete — rows deleted: %s", deleted)


def _job_emit_snapshot() -> None:
    """
    Job 5: Push the full LATEST_SNAPSHOT to all connected WebSocket clients.
    Runs every 60 seconds. Also checks for stale data and emits data_stale event.
    """
    from app.services.ingestion_service import LATEST_SNAPSHOT, get_snapshot
    from app.utils.constants import DATA_AGE_STALE_SECONDS, WS_EVENT_DATA_STALE, WS_EVENT_SOLAR_UPDATE
    try:
        from app import socketio
    except Exception:
        return  # socketio not yet initialized

    snapshot = get_snapshot()
    age = snapshot.get("data_age_seconds", 9999)

    # Push full snapshot
    socketio.emit(WS_EVENT_SOLAR_UPDATE, snapshot)

    # Emit stale event if data has gone cold
    if age > DATA_AGE_STALE_SECONDS:
        socketio.emit(
            WS_EVENT_DATA_STALE,
            {
                "age_seconds": age,
                "last_good": snapshot.get("last_updated_utc"),
            },
        )
        logger.warning("DATA_STALE emitted — age=%ds", int(age))


# ─────────────────────────────────────────────────────────────────
# SCHEDULER START — called from app factory
# ─────────────────────────────────────────────────────────────────

def init_scheduler(app: Any) -> None:
    """
    Register all jobs and start the BackgroundScheduler.
    Guards against double-start when Flask debug reloader is active.

    Args:
        app: Flask application instance.
    """
    if app.config.get("SCHEDULER_STARTED"):
        logger.debug("Scheduler already running — skipping re-init")
        return

    # Job 1 — Solar wind + Kp every 60 seconds
    scheduler.add_job(
        func=_job_solar_wind_and_kp,
        trigger="interval",
        seconds=POLL_INTERVAL_SOLAR_WIND_S,
        id="solar_wind_and_kp",
        name="NOAA Solar Wind + Kp Poll",
        replace_existing=True,
    )

    # Job 2 — X-ray + Alerts every 5 minutes
    scheduler.add_job(
        func=_job_xray_and_alerts,
        trigger="interval",
        seconds=POLL_INTERVAL_XRAY_S,
        id="xray_and_alerts",
        name="GOES X-Ray + NOAA Alerts Poll",
        replace_existing=True,
    )

    # Job 3 — CME every 30 minutes
    scheduler.add_job(
        func=_job_cme,
        trigger="interval",
        seconds=POLL_INTERVAL_CME_S,
        id="cme_poll",
        name="NASA DONKI CME Poll",
        replace_existing=True,
    )

    # Job 4 — Daily cleanup
    scheduler.add_job(
        func=_job_cleanup,
        trigger="interval",
        seconds=POLL_INTERVAL_CLEANUP_S,
        id="cleanup_old_data",
        name="DB Retention Cleanup",
        replace_existing=True,
    )

    # Job 5 — WebSocket snapshot push every 60 seconds
    scheduler.add_job(
        func=_job_emit_snapshot,
        trigger="interval",
        seconds=60,
        id="emit_snapshot",
        name="WebSocket Snapshot Emitter",
        replace_existing=True,
    )

    scheduler.start()
    app.config["SCHEDULER_STARTED"] = True

    # Trigger an immediate first poll so the dashboard has data on launch
    _job_solar_wind_and_kp()
    _job_xray_and_alerts()
    _job_cme()

    logger.info("APScheduler started — %d jobs registered", len(scheduler.get_jobs()))

    # Graceful shutdown on process exit
    atexit.register(lambda: scheduler.shutdown(wait=False))
