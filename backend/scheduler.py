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


def _replay_mode_active() -> bool:
    """Return True when Layer 7 is replaying historical data."""
    try:
        from app.services.replay_engine import PipelineInjector

        return bool(PipelineInjector.REPLAY_MODE_ACTIVE)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# JOB FUNCTIONS — thin wrappers that import from services
# ─────────────────────────────────────────────────────────────────

def _job_solar_wind_and_kp() -> None:
    """
    Job 1: Poll NOAA solar wind and Kp every 60 seconds.
    Fetches, validates, updates snapshot, persists to DB, and triggers Layer 2.
    """
    if _replay_mode_active():
        logger.debug("Scheduler: Replay mode active - skipping live solar/Kp cycle")
        return
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

            # ── Layer 3: Kp Prediction Engine ──
            try:
                from app.services.kp_predictor import (
                    model_loader, run_inference_cycle,
                    update_latest_kp_forecast, save_forecast_to_db,
                )
                if model_loader.are_loaded():
                    import time as _time
                    t0 = _time.perf_counter()
                    forecast = run_inference_cycle(feature_result)
                    elapsed_ms = (_time.perf_counter() - t0) * 1000
                    forecast["inference_time_ms"] = round(elapsed_ms, 1)
                    update_latest_kp_forecast(forecast)
                    save_forecast_to_db(forecast)

                    # Trigger storm alert WebSocket if storm class changed
                    from app.services.storm_alert import check_and_emit_storm_alert
                    check_and_emit_storm_alert(forecast)

                    # ── Layer 4: Satellite Vulnerability Scoring ──
                    try:
                        from app.services.satellite_scorer import (
                            run_satellite_scoring, update_latest_satellite_risks,
                            get_latest_satellite_risks, save_satellite_risks_to_db,
                            check_and_emit_satellite_alerts,
                        )
                        from app.services.ingestion_service import get_snapshot
                        snapshot = get_snapshot()
                        if snapshot:
                            prev_risks = get_latest_satellite_risks()
                            sat_risks = run_satellite_scoring(forecast, snapshot)
                            update_latest_satellite_risks(sat_risks)
                            save_satellite_risks_to_db(sat_risks)
                            check_and_emit_satellite_alerts(sat_risks, prev_risks)
                            logger.info(
                                "Satellite scoring: Critical=%d High=%d Top=%s@%.0f",
                                sat_risks["critical_count"], sat_risks["high_count"],
                                sat_risks["fleet_summary"]["highest_risk_satellite"],
                                sat_risks["fleet_summary"]["highest_risk_score"])

                            # ── Layer 5: India Power Grid GIC Risk Scoring ──
                            try:
                                from app.services.grid_risk_engine import (
                                    check_and_emit_grid_alerts,
                                    get_latest_grid_risks,
                                    run_grid_risk_scoring,
                                    save_grid_risks_to_db,
                                    update_latest_grid_risks,
                                )
                                t_grid = _time.perf_counter()
                                previous_grid_risks = get_latest_grid_risks()
                                grid_risks = run_grid_risk_scoring(forecast, snapshot)
                                grid_elapsed_ms = (_time.perf_counter() - t_grid) * 1000
                                update_latest_grid_risks(grid_risks)
                                save_grid_risks_to_db(grid_risks)
                                check_and_emit_grid_alerts(grid_risks, previous_grid_risks)
                                ns = grid_risks["national_summary"]
                                logger.info(
                                    "Grid scoring: %dms | Critical=%d | High=%d | "
                                    "MaxGIC=%.1fA@%s | Impact=₹%.0fCr | Pop@Risk=%.1fM",
                                    int(grid_elapsed_ms),
                                    ns["critical_corridors_count"],
                                    ns["high_corridors_count"],
                                    ns["max_gic_amps"],
                                    ns["max_gic_corridor"],
                                    ns["total_economic_impact_crore"],
                                    ns["population_at_risk_million"],
                                )

                                # Layer 6: event-driven mission advisory generation.
                                try:
                                    from app.services.advisory_generator import (
                                        ADVISORY_GENERATOR,
                                        append_advisory_history,
                                        check_advisory_triggers,
                                        save_advisory_to_db,
                                        update_latest_advisory,
                                    )
                                    from app.utils.constants import (
                                        WS_EVENT_ADVISORY_UPDATE,
                                        WS_EVENT_NEW_ADVISORY,
                                    )

                                    triggered = check_advisory_triggers(
                                        kp_forecast=forecast,
                                        satellite_risks=sat_risks,
                                        grid_risks=grid_risks,
                                        solar_data=snapshot,
                                    )
                                    if triggered:
                                        trigger_type = triggered["trigger_type"]
                                        logger.info("Advisory trigger fired: %s", trigger_type)
                                        advisory = ADVISORY_GENERATOR.generate(
                                            trigger_type=trigger_type,
                                            kp_forecast=forecast,
                                            satellite_risks=sat_risks,
                                            grid_risks=grid_risks,
                                            solar_data=snapshot,
                                        )
                                        update_latest_advisory(advisory)
                                        append_advisory_history(advisory)
                                        save_advisory_to_db(advisory)
                                        try:
                                            from app import socketio

                                            socketio.emit(WS_EVENT_NEW_ADVISORY, advisory)
                                            socketio.emit(WS_EVENT_ADVISORY_UPDATE, advisory)
                                        except Exception as sio_exc:
                                            logger.debug("Advisory socket emit skipped: %s", sio_exc)
                                        logger.info(
                                            "Advisory generated: ID=%s Source=%s Urgency=%s Tokens=%d",
                                            advisory["advisory_id"],
                                            advisory["advisory_source"],
                                            advisory["advisory_urgency"],
                                            advisory["content"].get("_groq_metadata", {}).get("tokens_used", 0),
                                        )
                                except Exception as l6_exc:
                                    logger.error("Layer 6 advisory generation failed: %s", l6_exc, exc_info=True)
                            except Exception as l5_exc:
                                logger.error("Layer 5 grid scoring failed: %s", l5_exc, exc_info=True)
                    except Exception as l4_exc:
                        logger.error("Layer 4 satellite scoring failed: %s", l4_exc, exc_info=True)

                else:
                    logger.debug("Layer 3 models not yet loaded — skipping inference")
            except Exception as l3_exc:
                logger.error("Layer 3 inference trigger failed: %s", l3_exc, exc_info=True)
    except Exception as exc:
        logger.error("Layer 2 feature engineering trigger failed: %s", exc, exc_info=True)


def _job_xray_and_alerts() -> None:
    """
    Job 2: Poll GOES X-ray flux and NOAA alerts every 5 minutes.
    Updates xray and alert sections of the snapshot.
    """
    if _replay_mode_active():
        logger.debug("Scheduler: Replay mode active - skipping live xray/alerts cycle")
        return
    from app.services.ingestion_service import run_xray_and_alerts_poll
    run_xray_and_alerts_poll()


def _job_cme() -> None:
    """
    Job 3: Poll NASA DONKI CME catalog every 30 minutes.
    Updates CME section of snapshot and persists Earth-directed events to DB.
    """
    if _replay_mode_active():
        logger.debug("Scheduler: Replay mode active - skipping live CME cycle")
        return
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
    if _replay_mode_active():
        logger.debug("Scheduler: Replay mode active - skipping live solar_update emit")
        return
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
