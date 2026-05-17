# backend/app/services/storm_alert.py
"""
NAKSHATRA-KAVACH Layer 3: Storm class change detection and WebSocket alert emission.

Tracks the previous storm class in memory and emits a 'storm_alert' SocketIO
event whenever the peak storm class changes — enabling real-time dashboard
overlays and operator notifications.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from app.utils.constants import WS_EVENT_STORM_ALERT

logger = logging.getLogger(__name__)

_alert_lock = threading.Lock()
_previous_storm_class: str = "QUIET"
_previous_action_level: str = "MONITOR"


def check_and_emit_storm_alert(forecast: Dict[str, Any]) -> None:
    """
    Compare the new forecast's peak storm class against the previous one.
    If it has changed (especially escalated), emit a 'storm_alert' WebSocket
    event to all connected dashboard clients.

    Args:
        forecast: Complete LATEST_KP_FORECAST dict from run_inference_cycle().
    """
    global _previous_storm_class, _previous_action_level

    summary = forecast.get("summary", {})
    new_class = summary.get("peak_storm_class", "QUIET")
    new_action = summary.get("recommended_action_level", "MONITOR")

    with _alert_lock:
        old_class = _previous_storm_class
        old_action = _previous_action_level

        class_changed = new_class != old_class
        action_escalated = _action_order(new_action) > _action_order(old_action)

        if class_changed or action_escalated:
            _previous_storm_class = new_class
            _previous_action_level = new_action

            alert_payload = {
                "type": "storm_class_change" if class_changed else "action_escalation",
                "previous_storm_class": old_class,
                "new_storm_class": new_class,
                "previous_action_level": old_action,
                "new_action_level": new_action,
                "peak_kp_24hr": summary.get("peak_kp_24hr", 0.0),
                "storm_probability_12hr": summary.get("storm_probability_12hr", 0.0),
                "storm_imminent": summary.get("storm_imminent", False),
                "storm_onset_detected": summary.get("storm_onset_detected", False),
                "dominant_driver": summary.get("dominant_driver", "Unknown"),
                "computed_at_utc": forecast.get("computed_at_utc"),
                "prediction_confidence": forecast.get("prediction_confidence", "LOW"),
                "escalation": _class_order(new_class) > _class_order(old_class),
            }

            _emit_alert(alert_payload)

            if _class_order(new_class) > _class_order(old_class):
                logger.warning(
                    "STORM ESCALATION: %s → %s | Peak Kp=%.1f | P(storm)=%.0f%% | Action=%s",
                    old_class, new_class,
                    summary.get("peak_kp_24hr", 0),
                    summary.get("storm_probability_12hr", 0) * 100,
                    new_action,
                )
            elif _class_order(new_class) < _class_order(old_class):
                logger.info(
                    "Storm de-escalation: %s → %s | Peak Kp=%.1f",
                    old_class, new_class, summary.get("peak_kp_24hr", 0),
                )
        else:
            _previous_storm_class = new_class
            _previous_action_level = new_action


def _emit_alert(payload: Dict[str, Any]) -> None:
    """Emit the storm_alert event via SocketIO. Fails silently if not initialised."""
    try:
        from app import socketio
        socketio.emit(WS_EVENT_STORM_ALERT, payload)
        logger.info("Emitted storm_alert event: %s → %s",
                     payload.get("previous_storm_class"), payload.get("new_storm_class"))
    except Exception as exc:
        logger.debug("Could not emit storm_alert (socketio not ready): %s", exc)


def _class_order(storm_class: str) -> int:
    """Numeric ordering of storm classes for comparison."""
    return {"QUIET": 0, "G1": 1, "G2": 2, "G3": 3, "G4": 4, "G5": 5}.get(storm_class, -1)


def _action_order(action: str) -> int:
    """Numeric ordering of action levels for comparison."""
    return {"MONITOR": 0, "WATCH": 1, "PREPARE": 2, "ACT_NOW": 3}.get(action, -1)


def get_current_alert_state() -> Dict[str, Any]:
    """Return the current alert tracking state for diagnostics."""
    with _alert_lock:
        return {
            "current_storm_class": _previous_storm_class,
            "current_action_level": _previous_action_level,
        }
