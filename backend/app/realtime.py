"""WebSocket push — avoid circular imports by late-binding socketio."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def push_dashboard_snapshot():
    try:
        from app import socketio
        from app.services.pipeline import build_dashboard_payload

        payload = build_dashboard_payload()
        socketio.emit("dashboard_update", payload)
        if payload.get("solar_wind"):
            socketio.emit("solar_wind_update", payload["solar_wind"])
        if payload.get("satellites"):
            socketio.emit("satellite_update", payload["satellites"])
    except Exception as e:
        logger.debug("socket push skipped: %s", e)
