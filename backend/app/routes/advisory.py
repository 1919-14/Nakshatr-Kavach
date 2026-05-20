# backend/app/routes/advisory.py
"""NAKSHATRA-KAVACH Layer 6 advisory REST endpoints."""
from __future__ import annotations

import logging
import json
import os
import time
from datetime import datetime
from typing import Any, Tuple
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, make_response, request, stream_with_context
import requests

from app.services.advisory_generator import (
    ADVISORY_GENERATOR,
    VALID_ADVISORY_TYPES,
    append_advisory_history,
    clean_internal_metadata,
    generate_advisory_pdf,
    generate_sms_text,
    generate_whatsapp_summary,
    get_advisory_history,
    get_advisory_status,
    get_latest_advisory,
    get_trigger_state_snapshot,
    save_advisory_to_db,
    update_latest_advisory,
)
from app.utils.constants import ADVISORY_MIN_INTERVAL_MINUTES, GROQ_MODEL

logger = logging.getLogger(__name__)
advisory_bp = Blueprint("advisory", __name__, url_prefix="/api/advisory")

_last_manual_generation_time: float = 0.0


def _with_advisory_headers(response: Response, advisory: dict, status_code: int = 200) -> Response:
    """Attach advisory metadata headers used by dashboard and integrations."""
    response.status_code = status_code
    response.headers["X-Advisory-ID"] = str(advisory.get("advisory_id", "N/A"))
    response.headers["X-Advisory-Source"] = str(advisory.get("advisory_source", "RULE_BASED"))
    response.headers["X-Advisory-Urgency"] = str(advisory.get("advisory_urgency", "ROUTINE"))
    response.headers["X-Generated-At"] = str(advisory.get("generated_at_utc", ""))
    return response


def _json_error(message: str, status_code: int, advisory: dict | None = None) -> Response:
    """Return a JSON error response with advisory context headers when possible."""
    latest = advisory or get_latest_advisory()
    response = jsonify({"error": message})
    return _with_advisory_headers(response, latest, status_code=status_code)


def _upstream_inputs() -> Tuple[dict, dict, dict, dict]:
    """Read current Layer 1, 3, 4, and 5 outputs without mutating upstream state.
    
    When replay mode is active, reads from the latest cached replay frame
    instead of real-time sources so SHAP/chatbot/advisory stay consistent.
    """
    # Check if replay is active and use replay data if available
    try:
        from app.services.replay_engine import PipelineInjector, REPLAY_CONTROLLER
        if PipelineInjector.REPLAY_MODE_ACTIVE:
            status = REPLAY_CONTROLLER.get_status()
            current_frame = status.get("current_frame", 0)
            cached = REPLAY_CONTROLLER.get_cached_frame(current_frame)
            if cached:
                return (
                    cached.get("kp_forecast", {}),
                    cached.get("satellite_risks", {}),
                    cached.get("grid_risks", {}),
                    cached.get("solar", {}),
                )
    except Exception:
        pass  # Fall through to live data

    try:
        from app.services.ingestion_service import get_snapshot
        solar_data = get_snapshot()
    except Exception as exc:
        logger.error("Advisory upstream solar snapshot unavailable: %s", exc)
        solar_data = {}

    try:
        from app.services.kp_predictor import get_latest_kp_forecast
        kp_forecast = get_latest_kp_forecast()
    except Exception as exc:
        logger.error("Advisory upstream Kp forecast unavailable: %s", exc)
        kp_forecast = {}

    try:
        from app.services.satellite_scorer import get_latest_satellite_risks
        satellite_risks = get_latest_satellite_risks()
    except Exception as exc:
        logger.error("Advisory upstream satellite risks unavailable: %s", exc)
        satellite_risks = {}

    try:
        from app.services.grid_risk_engine import get_latest_grid_risks
        grid_risks = get_latest_grid_risks()
    except Exception as exc:
        logger.error("Advisory upstream grid risks unavailable: %s", exc)
        grid_risks = {}

    return kp_forecast or {}, satellite_risks or {}, grid_risks or {}, solar_data or {}


def _safe_num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_satellite_risks(satellite_risks: dict) -> dict:
    """Keep the LLM context complete enough for reasoning without sending 158 full objects."""
    tier1 = list((satellite_risks.get("tier1") or {}).values())
    tier2 = satellite_risks.get("tier2") or []
    tier3 = satellite_risks.get("tier3") or []
    if isinstance(tier2, dict):
        tier2 = list(tier2.values())
    if isinstance(tier3, dict):
        tier3 = list(tier3.values())
    all_sats = [*tier1, *tier2, *tier3]

    def score(sat: dict) -> float:
        return _safe_num((sat.get("risk_scores") or {}).get("composite_final", sat.get("composite_risk", 0)))

    def slim(sat: dict) -> dict:
        scores = sat.get("risk_scores") or {}
        return {
            "name": sat.get("display_name") or sat.get("name"),
            "tier": sat.get("tier"),
            "orbit_type": sat.get("orbit_type"),
            "altitude_km": sat.get("altitude_km"),
            "risk_level": sat.get("risk_level"),
            "composite_risk": score(sat),
            "drag_risk": scores.get("drag_risk"),
            "charging_risk": scores.get("charging_risk"),
            "radiation_risk": scores.get("radiation_risk"),
            "recommended_action": sat.get("recommended_action"),
            "safe_mode": sat.get("safe_mode_countdown"),
        }

    ranked = sorted(all_sats, key=score, reverse=True)
    return {
        "fleet_monitored": satellite_risks.get("fleet_monitored", len(all_sats)),
        "critical_count": satellite_risks.get("fleet_critical_count", satellite_risks.get("critical_count", 0)),
        "high_count": satellite_risks.get("fleet_high_count", satellite_risks.get("high_count", 0)),
        "storm_class_used": satellite_risks.get("storm_class_used"),
        "kp_used": satellite_risks.get("kp_used"),
        "fleet_summary": satellite_risks.get("fleet_summary", {}),
        "top_risk_satellites": [slim(sat) for sat in ranked[:12]],
    }


def _compact_grid_risks(grid_risks: dict) -> dict:
    corridors = grid_risks.get("corridors") or []
    if isinstance(corridors, dict):
        corridors = list(corridors.values())

    def risk(corridor: dict) -> float:
        return _safe_num(corridor.get("saturation_risk", corridor.get("risk_percent", 0)))

    def slim(corridor: dict) -> dict:
        return {
            "name": corridor.get("corridor_name") or corridor.get("name"),
            "voltage_kv": corridor.get("voltage_kv"),
            "gic_amps": corridor.get("gic_amps"),
            "risk_percent": risk(corridor),
            "states": corridor.get("states_affected") or corridor.get("states"),
            "operator_action": (corridor.get("load_reduction") or {}).get("action") or corridor.get("action"),
        }

    ranked = sorted(corridors, key=risk, reverse=True)
    return {
        "national_summary": grid_risks.get("national_summary", {}),
        "top_corridors": [slim(item) for item in ranked[:8]],
    }


def _compact_shap(shap: dict | None, frontend_context: dict | None = None) -> dict:
    frontend_shap = (frontend_context or {}).get("shap") or []
    if frontend_shap and not shap:
        return {"features": frontend_shap[:10], "source": "frontend_store"}
    shap = shap or {}
    features = shap.get("top_features") or shap.get("features") or shap.get("all_features") or frontend_shap
    return {
        "method": shap.get("method", f"TreeSHAP {shap.get('horizon', '6hr')}"),
        "horizon": shap.get("horizon", "6hr"),
        "predicted_kp": shap.get("predicted_kp"),
        "base_value": shap.get("base_value"),
        "dominant_driver": shap.get("dominant_driver"),
        "features": features[:10] if isinstance(features, list) else [],
    }


def _latest_shap() -> dict:
    """Read current SHAP from the forecast cache or compute it when possible."""
    try:
        from app.services.kp_predictor import get_latest_kp_forecast
        cached = get_latest_kp_forecast()
        if cached.get("shap"):
            return cached["shap"]
    except Exception:
        logger.debug("No cached SHAP in latest forecast", exc_info=True)

    try:
        from app.services.feature_engineering import get_latest_features
        from app.services.kp_predictor import model_loader
        import numpy as np

        horizon = "6hr"
        if not model_loader.shap_explainers.get(horizon):
            return {}
        features = get_latest_features() or {}
        xgb_raw = features.get("xgb_vector_raw")
        xgb_scaled = features.get("xgb_vector_scaled")
        if not isinstance(xgb_raw, np.ndarray):
            xgb_raw = np.array(xgb_raw, dtype=np.float64)
        if not isinstance(xgb_scaled, np.ndarray):
            xgb_scaled = np.array(xgb_scaled, dtype=np.float64)
        return model_loader.shap_analyzer.compute_shap(
            horizon,
            xgb_raw.reshape(1, -1),
            xgb_scaled.reshape(1, -1),
        )
    except Exception:
        logger.debug("Could not compute SHAP context for Groq", exc_info=True)
        return {}


def _build_llm_context(frontend_context: dict | None = None, shap_payload: dict | None = None) -> dict:
    kp_forecast, satellite_risks, grid_risks, solar_data = _upstream_inputs()
    latest_advisory = get_latest_advisory()
    shap = shap_payload or _latest_shap()
    frontend_context = frontend_context or {}
    return {
        "solar": solar_data,
        "kp_forecast": kp_forecast,
        "shap": _compact_shap(shap, frontend_context),
        "satellites": _compact_satellite_risks(satellite_risks),
        "grid": _compact_grid_risks(grid_risks),
        "advisory": {
            "id": latest_advisory.get("advisory_id"),
            "source": latest_advisory.get("advisory_source"),
            "urgency": latest_advisory.get("advisory_urgency"),
            "content": latest_advisory.get("content", {}),
        },
        "frontend_context": frontend_context,
    }


def _groq_chat_client():
    from groq import Groq
    from app.config import GROQ_CHAT_API_KEY
    if not GROQ_CHAT_API_KEY:
        raise RuntimeError("Groq chat client is not configured. Set GROQ_API_KEY1 in backend/.env.")
    return Groq(api_key=GROQ_CHAT_API_KEY)


def _llm_context_text(context: dict) -> str:
    return json.dumps(context, ensure_ascii=False, default=str)[:14000]


@advisory_bp.route("/latest", methods=["GET"])
def latest_advisory() -> Response:
    """Return the latest cached advisory from memory."""
    advisory = get_latest_advisory()
    return _with_advisory_headers(jsonify(advisory), advisory)


@advisory_bp.route("/generate", methods=["POST"])
def generate_advisory_now() -> Response:
    """Generate a fresh manual advisory with Groq-first and rule-based fallback."""
    global _last_manual_generation_time
    now = time.time()
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force"))
    elapsed = now - _last_manual_generation_time
    minimum_seconds = ADVISORY_MIN_INTERVAL_MINUTES * 60
    if not force and _last_manual_generation_time and elapsed < minimum_seconds:
        retry_after = int(minimum_seconds - elapsed)
        latest = get_latest_advisory()
        response = _with_advisory_headers(jsonify({
            "error": f"Rate limited. Retry in {retry_after}s.",
            "retry_after_seconds": retry_after,
            "latest_advisory": latest,
        }), latest, status_code=429)
        response.headers["Retry-After"] = str(retry_after)
        return response

    trigger_type = str(payload.get("trigger_type", "MANUAL_REFRESH"))
    if trigger_type not in VALID_ADVISORY_TYPES:
        trigger_type = "MANUAL_REFRESH"

    kp_forecast, satellite_risks, grid_risks, solar_data = _upstream_inputs()
    try:
        advisory = ADVISORY_GENERATOR.generate(
            trigger_type=trigger_type,
            kp_forecast=kp_forecast,
            satellite_risks=satellite_risks,
            grid_risks=grid_risks,
            solar_data=solar_data,
        )
        update_latest_advisory(advisory)
        append_advisory_history(advisory)
        save_advisory_to_db(advisory)
        _last_manual_generation_time = now
        return _with_advisory_headers(jsonify(advisory), advisory)
    except Exception as exc:
        logger.error("Manual advisory generation failed: %s", exc, exc_info=True)
        return _json_error(str(exc), 500)


@advisory_bp.route("/history", methods=["GET"])
def advisory_history() -> Response:
    """Return the bounded in-memory advisory history."""
    latest = get_latest_advisory()
    return _with_advisory_headers(jsonify({"count": len(get_advisory_history()), "advisories": get_advisory_history()}), latest)


@advisory_bp.route("/export/pdf", methods=["GET"])
def export_pdf() -> Response:
    """Return the latest advisory as a downloadable NDMA-style PDF."""
    advisory = get_latest_advisory()
    try:
        pdf_bytes = generate_advisory_pdf(advisory)
    except Exception as exc:
        logger.error("PDF export failed: %s", exc, exc_info=True)
        return _json_error(str(exc), 500, advisory)

    response = make_response(pdf_bytes)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename=nakshatra_advisory_{timestamp}.pdf"
    return _with_advisory_headers(response, advisory)


@advisory_bp.route("/export/whatsapp", methods=["GET"])
def export_whatsapp() -> Response:
    """Return WhatsApp and SMS summaries for operator distribution."""
    advisory = get_latest_advisory()
    payload = {
        "whatsapp_text": generate_whatsapp_summary(advisory),
        "sms_text": generate_sms_text(advisory),
    }
    return _with_advisory_headers(jsonify(payload), advisory)


@advisory_bp.route("/export/sms", methods=["GET", "POST"])
def export_sms() -> Response:
    """Return an SMS-ready advisory and optionally submit through SMSGate."""
    advisory = get_latest_advisory()
    sms_text = generate_sms_text(advisory)
    recipients = [
        item.strip()
        for item in os.environ.get("SMSGATE_RECIPIENTS", "").split(",")
        if item.strip()
    ]
    payload = {
        "sms_text": sms_text,
        "recipients": recipients,
        "provider": "SMSGate",
        "dry_run": True,
        "send_status": "not_requested",
        "sms_uri": f"sms:{recipients[0] if recipients else ''}?body={quote(sms_text)}",
    }

    should_send = request.method == "POST" or request.args.get("send", "").lower() in {"1", "true", "yes"}
    if should_send:
        username = os.environ.get("SMSGATE_USERNAME", "")
        password = os.environ.get("SMSGATE_PASSWORD", "")
        device_id = os.environ.get("SMSGATE_DEVICE_ID", "")
        if not (username and password and recipients):
            payload.update({
                "send_status": "not_configured",
                "message": "Set SMSGATE_USERNAME, SMSGATE_PASSWORD and SMSGATE_RECIPIENTS to send through SMSGate.",
            })
        else:
            body = {
                "textMessage": {"text": sms_text},
                "phoneNumbers": recipients,
                "simNumber": int(os.environ.get("SMSGATE_SIM_NUMBER", "1")),
                "ttl": int(os.environ.get("SMSGATE_TTL_SECONDS", "3600")),
                "priority": int(os.environ.get("SMSGATE_PRIORITY", "100")),
            }
            if device_id:
                body["deviceId"] = device_id
            try:
                response = requests.post(
                    "https://api.sms-gate.app/3rdparty/v1/messages",
                    params={"skipPhoneValidation": "true", "deviceActiveWithin": os.environ.get("SMSGATE_DEVICE_ACTIVE_HOURS", "12")},
                    json=body,
                    auth=(username, password),
                    timeout=20,
                )
                payload["dry_run"] = False
                payload["send_status"] = "submitted" if response.ok else "failed"
                payload["provider_status_code"] = response.status_code
                payload["provider_response"] = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:500]
            except Exception as exc:
                payload["send_status"] = "failed"
                payload["error"] = str(exc)
    return _with_advisory_headers(jsonify(payload), advisory)


@advisory_bp.route("/export/json", methods=["GET"])
def export_json() -> Response:
    """Return the latest advisory stripped of internal underscore metadata."""
    advisory = get_latest_advisory()
    clean = clean_internal_metadata(advisory)
    return _with_advisory_headers(jsonify(clean), advisory)


@advisory_bp.route("/status", methods=["GET"])
def advisory_status() -> Response:
    """Return advisory subsystem health without failing when Groq is absent."""
    advisory = get_latest_advisory()
    return _with_advisory_headers(jsonify(get_advisory_status(ADVISORY_GENERATOR)), advisory)


@advisory_bp.route("/chat/stream", methods=["POST"])
def chat_stream() -> Response:
    """Stream a Groq-powered natural-language answer over SSE."""
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("message", "")).strip()
    language = str(payload.get("language", "en")).lower()
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    if not question:
        return jsonify({"error": "message is required"}), 400

    try:
        client = _groq_chat_client()
    except Exception as exc:
        return jsonify({"error": str(exc), "groq_available": False}), 503

    context = _build_llm_context(payload.get("context") if isinstance(payload.get("context"), dict) else {})
    context_text = _llm_context_text(context)
    language_rule = (
        "Answer primarily in Hindi using Devanagari script, but keep satellite, Kp, SHAP and grid technical terms readable."
        if language.startswith("hi")
        else "Answer in concise natural English unless the user asks for Hindi."
    )
    compact_history = [
        {"role": item.get("role"), "content": str(item.get("text") or item.get("content") or "")[:1200]}
        for item in history[-8:]
        if item.get("role") in {"user", "assistant"}
    ]
    system_prompt = f"""
You are NAKSHATRA-KAVACH's Groq LLaMA 3.3 mission assistant.
Use only the supplied live/replay context. Explain Kp, SHAP feature drivers,
satellite risk, India grid risk, replay/live data quality, and advisory actions.
Do not invent values. If data quality is STALE or replay, say so clearly.
{language_rule}
Be calm, operational, specific, and conversational. Keep answers short unless asked for detail.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Current NAKSHATRA context JSON:\n{context_text}"},
        *compact_history,
        {"role": "user", "content": question},
    ]

    def generate():
        yield f"event: meta\ndata: {json.dumps({'model': GROQ_MODEL, 'source': 'GROQ', 'language': language})}\n\n"
        try:
            stream = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0.25,
                max_tokens=900,
                stream=True,
            )
            for chunk in stream:
                choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                delta = getattr(choice, "delta", None)
                token = getattr(delta, "content", None) if delta is not None else None
                if token:
                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            logger.error("Groq chat stream failed: %s", exc, exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@advisory_bp.route("/explain/shap", methods=["POST"])
def explain_shap() -> Response:
    """Generate Groq natural-language SHAP explainability for dashboard display."""
    payload = request.get_json(silent=True) or {}
    language = str(payload.get("language", "en")).lower()
    try:
        client = _groq_chat_client()
    except Exception as exc:
        return jsonify({"error": str(exc), "groq_available": False}), 503

    context = _build_llm_context(
        payload.get("context") if isinstance(payload.get("context"), dict) else {},
        payload.get("shap") if isinstance(payload.get("shap"), dict) else None,
    )
    context_text = _llm_context_text(context)
    system_prompt = """
You explain TreeSHAP output for Indian space-weather operators.
Return ONLY valid JSON with keys:
english_summary, hindi_summary, operator_takeaway, driver_notes.
driver_notes must be an array of 3 short strings.
Use the supplied context only. Do not invent feature values or Kp values.
Hindi must be Devanagari. No markdown.
"""
    user_prompt = (
        f"Language preference: {language}\n"
        f"Explain the Kp forecast SHAP drivers from this context:\n{context_text}"
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        parsed["source"] = "GROQ"
        parsed["model"] = GROQ_MODEL
        return jsonify(parsed)
    except Exception as exc:
        logger.error("Groq SHAP explanation failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc), "source": "GROQ", "model": GROQ_MODEL}), 502


@advisory_bp.route("/triggers", methods=["GET"])
def advisory_triggers() -> Response:
    """Return a non-mutating snapshot of current advisory trigger state."""
    kp_forecast, satellite_risks, grid_risks, solar_data = _upstream_inputs()
    advisory = get_latest_advisory()
    payload = get_trigger_state_snapshot(kp_forecast, satellite_risks, grid_risks, solar_data)
    return _with_advisory_headers(jsonify(payload), advisory)
