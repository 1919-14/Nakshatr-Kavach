"""
NAKSHATRA-KAVACH HTTP API — L1–L6 integration (MySQL + hybrid Kp + Groq + SHAP).
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, jsonify, request

from app.db import AdvisoryRecord, get_session_factory
from app.services.data_ingestion import get_ingestion_service
from app.services.llm_advisory import generate_advisory
from app.services.pipeline import build_advisory_context, build_dashboard_payload
from app.services.physics import snapshot_to_api_row
from app.services.replay_engine import load_storm

api_bp = Blueprint("api", __name__)


@api_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "NAKSHATRA-KAVACH", "layers": "L1-L6"})


# ── Solar / L1 ──────────────────────────────────────────────────────────────


@api_bp.route("/solar/live", methods=["GET"])
@api_bp.route("/solar-wind", methods=["GET"])
def solar_live():
    ing = get_ingestion_service()
    snap = ing.get_latest_snapshot()
    if not snap:
        return jsonify({"error": "No data yet; run POST /api/v1/trigger or wait for scheduler"}), 503
    return jsonify(snapshot_to_api_row(snap))


@api_bp.route("/v1/latest", methods=["GET"])
def v1_latest():
    ing = get_ingestion_service()
    snap = ing.get_latest_snapshot()
    if not snap:
        return jsonify({"error": "No data"}), 404
    return jsonify(
        {
            "timestamp": snap.get("timestamp"),
            "data": {
                "bz_gsm": snap.get("bz_gsm"),
                "by_gsm": snap.get("by_gsm"),
                "bx_gsm": snap.get("bx_gsm"),
                "bt_total": snap.get("bt_total"),
                "sw_speed_kmps": snap.get("sw_speed_kmps"),
                "proton_density_ccm": snap.get("proton_density_ccm"),
                "proton_temp_K": snap.get("proton_temp_K"),
                "xray_flux_Wm2": snap.get("xray_flux_Wm2"),
                "kp_current": snap.get("kp_current"),
            },
            "quality_flag": snap.get("quality_flag"),
            "source": snap.get("source"),
        }
    )


@api_bp.route("/v1/latest/dataframe", methods=["GET"])
def v1_dataframe():
    ing = get_ingestion_service()
    df = ing.get_latest_dataframe()
    if df.empty:
        return jsonify({"error": "No data"}), 404
    return jsonify(df.to_dict(orient="records"))


@api_bp.route("/v1/history", methods=["GET"])
def v1_history():
    ing = get_ingestion_service()
    hours = request.args.get("hours", 24, type=int)
    limit = request.args.get("limit", 500, type=int)
    df = ing.get_historical_data(hours=hours)
    if limit:
        df = df.tail(limit)
    return jsonify({"count": len(df), "hours": hours, "data": df.to_dict(orient="records")})


@api_bp.route("/v1/trigger", methods=["POST"])
def v1_trigger():
    ing = get_ingestion_service()
    r = ing.ingest_data()
    return jsonify({"status": "success", "timestamp": str(r.get("timestamp")), "quality_flag": r.get("quality_flag")})


@api_bp.route("/v1/stats", methods=["GET"])
def v1_stats():
    ing = get_ingestion_service()
    df = ing.get_historical_data(hours=24)
    if df.empty:
        return jsonify({"records_24h": 0})
    qc = df["quality_flag"].value_counts().to_dict() if "quality_flag" in df.columns else {}
    return jsonify({"records_24h": len(df), "quality_distribution": qc})


# ── Kp + SHAP ───────────────────────────────────────────────────────────────


@api_bp.route("/kp/forecast", methods=["GET"])
@api_bp.route("/kp-forecast", methods=["GET"])
def kp_forecast():
    p = build_dashboard_payload()
    k = p["kp_forecast"]
    return jsonify(
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "current_kp": k.get("current_kp"),
            "storm_class": k.get("storm_class"),
            "forecast": k.get("forecast"),
            "storm_probability": k.get("storm_probability"),
            "peak_arrival_minutes": k.get("peak_arrival_minutes"),
            "model_notes": k.get("model_notes"),
        }
    )


@api_bp.route("/shap/explain", methods=["GET"])
def shap_explain():
    return jsonify(build_dashboard_payload().get("shap") or {})


# ── Satellites + Grid ─────────────────────────────────────────────────────────


@api_bp.route("/satellites/risk", methods=["GET"])
@api_bp.route("/satellite-risk", methods=["GET"])
def satellites_risk():
    return jsonify(build_dashboard_payload().get("satellites") or [])


@api_bp.route("/grid/risk", methods=["GET"])
@api_bp.route("/grid-risk", methods=["GET"])
def grid_risk():
    return jsonify(build_dashboard_payload().get("grid") or [])


# ── Advisory + Groq ──────────────────────────────────────────────────────────


def _latest_advisory_from_db():
    Session = get_session_factory()
    s = Session()
    try:
        row = s.query(AdvisoryRecord).order_by(AdvisoryRecord.generated_at.desc()).first()
        if not row:
            return None
        return json.loads(row.payload_json)
    except Exception:
        return None
    finally:
        s.close()


def _save_advisory(payload: dict):
    Session = get_session_factory()
    s = Session()
    try:
        rec = AdvisoryRecord(
            generated_at=datetime.utcnow(),
            advisory_source=str(payload.get("advisory_source") or payload.get("source", "UNKNOWN")),
            payload_json=json.dumps(payload, default=str),
        )
        s.add(rec)
        s.commit()
    finally:
        s.close()


@api_bp.route("/advisory/latest", methods=["GET"])
@api_bp.route("/advisory", methods=["GET"])
def advisory_latest():
    cached = _latest_advisory_from_db()
    if cached:
        return jsonify(cached)
    ctx = build_advisory_context()
    adv = generate_advisory(ctx)
    _save_advisory(adv)
    return jsonify(adv)


@api_bp.route("/advisory/generate", methods=["POST"])
def advisory_generate():
    ctx = build_advisory_context()
    adv = generate_advisory(ctx)
    _save_advisory(adv)
    return jsonify(adv)


# ── Aditya-L1 stub (public feeds vary) ───────────────────────────────────────


@api_bp.route("/aditya/l1", methods=["GET"])
def aditya_l1():
    ing = get_ingestion_service()
    snap = ing.get_latest_snapshot()
    return jsonify(
        {
            "note": "Proxy: compare DSCOVR L1 solar wind with Aditya-L1 mission context.",
            "dscovr_proxy": {"bz_gsm": snap.get("bz_gsm"), "speed_km_s": snap.get("sw_speed_kmps")},
            "aditya_status": "Use ISRO/ADITYA public portals for ASPEX/SOLEXS level-1 when available.",
        }
    )


# ── Unified dashboard JSON ────────────────────────────────────────────────────


@api_bp.route("/dashboard", methods=["GET"])
def dashboard():
    return jsonify(build_dashboard_payload())


@api_bp.route("/history/<storm_id>", methods=["GET"])
def history_storm(storm_id: str):
    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", None, type=int)
    payload = load_storm(storm_id, offset=offset, limit=limit)
    if payload is None:
        return jsonify({"error": "unknown_storm", "id": storm_id}), 404
    return jsonify(payload)
