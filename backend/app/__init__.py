# backend/app/__init__.py
"""
NAKSHATRA-KAVACH — Layer 1: Flask Application Factory
Creates and configures the Flask app, SocketIO, CORS, blueprints, DB, and scheduler.
"""

import logging
import os

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Module-level SocketIO instance (imported by scheduler and ingestion_service)
socketio = SocketIO()


def create_app(config_object=None) -> Flask:
    """
    Flask application factory.

    Args:
        config_object: Config class or None (auto-detects from FLASK_ENV).

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)

    # ── Load configuration ──────────────────────────────────────
    if config_object is None:
        from config import get_config
        config_object = get_config()

    app.config.from_object(config_object)
    config_object.init_app(app)

    # ── Database ────────────────────────────────────────────────
    from app.database.db import init_db, set_db_path
    set_db_path(app.config["DB_PATH"])
    init_db()

    # ── CORS ────────────────────────────────────────────────────
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
    )

    # ── SocketIO ────────────────────────────────────────────────
    socketio.init_app(
        app,
        cors_allowed_origins=app.config["SOCKETIO_CORS_ALLOWED_ORIGINS"],
        async_mode=app.config["SOCKETIO_ASYNC_MODE"],
        logger=False,
        engineio_logger=False,
    )

    # ── Register SocketIO event handlers ────────────────────────
    from socketio_events import register_socketio_events
    register_socketio_events(socketio)

    # ── Register REST blueprints ─────────────────────────────────────
    from app.routes.solar import solar_bp
    app.register_blueprint(solar_bp)
    from app.routes.features import features_bp
    app.register_blueprint(features_bp)
    from app.routes.kp_forecast import kp_bp
    app.register_blueprint(kp_bp)
    from app.routes.satellites import satellites_bp
    app.register_blueprint(satellites_bp)
    from app.routes.grid import grid_bp
    app.register_blueprint(grid_bp)
    from app.routes.advisory import advisory_bp
    app.register_blueprint(advisory_bp)
    from app.routes.replay import replay_bp
    app.register_blueprint(replay_bp)

    # ── Load Layer 2 Scalers ───────────────────────────────────────
    try:
        from app.services.feature_engineering import load_scalers
        load_scalers()
    except Exception as exc:
        logger.warning("Layer 2 scalers loading failed: %s", exc)

    # ── Load Layer 3 prediction models ─────────────────────────────
    try:
        from app.services.kp_predictor import model_loader
        model_loader.load_all()
    except Exception as exc:
        logger.warning("Layer 3 model loading failed (degraded mode): %s", exc)

    # ── Load Layer 4 satellite database ────────────────────────────
    try:
        from app.services.satellite_scorer import sat_db
        sat_db.load()
    except Exception as exc:
        logger.warning("Layer 4 satellite DB loading failed: %s", exc)

    # ── Load Layer 5 India grid corridor database ───────────────────────────
    try:
        from app.services.grid_risk_engine import grid_db
        grid_db.load()
    except Exception as exc:
        logger.warning("Layer 5 grid DB loading failed: %s", exc)

    # ── Start background scheduler ───────────────────────────────
    # Guard: only start in non-testing environments
    if not app.config.get("TESTING", False):
        from scheduler import init_scheduler
        init_scheduler(app)

    logger.info(
        "NAKSHATRA-KAVACH app created | env=%s | db=%s",
        os.getenv("FLASK_ENV", "development"),
        app.config.get("DB_PATH"),
    )

    return app
