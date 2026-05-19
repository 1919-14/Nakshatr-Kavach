# backend/socketio_events.py
"""
NAKSHATRA-KAVACH — Layer 1: SocketIO Event Handlers
Defines connect/disconnect handlers and manual emit helpers.
The scheduled push is handled in scheduler.py (_job_emit_snapshot).
"""

import logging

logger = logging.getLogger(__name__)


def register_socketio_events(socketio) -> None:
    """
    Register all SocketIO event handlers on the given SocketIO instance.
    Called once from the Flask app factory after socketio is initialized.

    Args:
        socketio: Flask-SocketIO instance.
    """

    @socketio.on("connect")
    def handle_connect():
        """Send the current snapshot immediately to a newly connected client."""
        logger.info("WebSocket client connected")
        try:
            from app.services.replay_engine import PipelineInjector

            if PipelineInjector.REPLAY_MODE_ACTIVE:
                socketio.emit("replay_state_change", {"new_state": "PLAYING", "previous_state": "UNKNOWN", "frame": None})
                return
        except Exception:
            pass
        from app.services.ingestion_service import get_snapshot
        from app.utils.constants import WS_EVENT_SOLAR_UPDATE
        snapshot = get_snapshot()
        socketio.emit(WS_EVENT_SOLAR_UPDATE, snapshot)

    @socketio.on("disconnect")
    def handle_disconnect(reason=None):
        """Log client disconnection."""
        logger.info("WebSocket client disconnected%s", f" | reason={reason}" if reason else "")

    @socketio.on("request_snapshot")
    def handle_request_snapshot(data=None):
        """
        Allow clients to request an on-demand snapshot push.
        Payload: ignored.
        """
        try:
            from app.services.replay_engine import PipelineInjector

            if PipelineInjector.REPLAY_MODE_ACTIVE:
                socketio.emit("replay_state_change", {"new_state": "PLAYING", "previous_state": "UNKNOWN", "frame": None})
                return
        except Exception:
            pass
        from app.services.ingestion_service import get_snapshot
        from app.utils.constants import WS_EVENT_SOLAR_UPDATE
        logger.debug("Client requested on-demand snapshot")
        snapshot = get_snapshot()
        socketio.emit(WS_EVENT_SOLAR_UPDATE, snapshot)

    @socketio.on_error_default
    def handle_error(exc):
        """Log unhandled SocketIO errors."""
        logger.error("SocketIO unhandled error: %s", exc, exc_info=True)
