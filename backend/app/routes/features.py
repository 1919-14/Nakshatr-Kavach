# backend/app/routes/features.py
"""
NAKSHATRA-KAVACH Layer 2 feature API routes.

The endpoints expose the latest feature metadata, raw/scaled vectors, and a
recent feature history for dashboard inspection and Layer 3 debugging.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from flask import Blueprint, Response, jsonify, request

from app.services.feature_engineering import (
    FEATURE_NAMES,
    compute_feature_history,
    get_latest_features,
    scalers_loaded,
)

features_bp = Blueprint("features", __name__, url_prefix="/api/features")


def _status_for_quality(data_quality: str) -> int:
    """
    Return the HTTP status code for a Layer 2 data-quality state.

    Args:
        data_quality: GOOD, PARTIAL, STALE, or UNKNOWN.

    Returns:
        HTTP status code.
    """
    if not scalers_loaded():
        return 503
    if str(data_quality).upper() == "PARTIAL":
        return 206
    return 200


def _quality_headers(data_quality: str, computed_at_utc: str) -> Dict[str, str]:
    """
    Build standard Layer 2 response headers.

    Args:
        data_quality: Current data-quality flag.
        computed_at_utc: Feature computation timestamp.

    Returns:
        Header dictionary.
    """
    return {
        "X-Data-Quality": data_quality,
        "X-Features-Computed-At": computed_at_utc,
        "Cache-Control": "no-cache, no-store, must-revalidate",
    }


def _scaler_unavailable_response() -> Tuple[Response, int]:
    """
    Build a 503 response when fitted scalers are not loaded.

    Returns:
        Flask response tuple.
    """
    payload = {
        "error": "Layer 2 scalers are not loaded",
        "detail": "Call load_scalers() after fitting backend/app/models/xgb_scaler.pkl and backend/app/models/lstm_scaler.pkl.",
    }
    return jsonify(payload), 503


def _attach_headers(response: Response, data_quality: str, computed_at_utc: str) -> Response:
    """
    Attach standard response headers.

    Args:
        response: Flask response object.
        data_quality: Current data-quality flag.
        computed_at_utc: Feature computation timestamp.

    Returns:
        Response with headers attached.
    """
    for key, value in _quality_headers(data_quality, computed_at_utc).items():
        response.headers[key] = value
    return response


@features_bp.route("/current", methods=["GET"])
def get_current_features() -> Tuple[Response, int]:
    """
    Return the latest feature metadata dictionary for SHAP displays.

    Returns:
        200 for normal data, 206 for partial data, or 503 if scalers are absent.
    """
    latest = get_latest_features(json_safe=True)
    data_quality = latest.get("data_quality", "UNKNOWN")
    computed_at = latest.get("computed_at_utc", "never")
    if not scalers_loaded():
        response, status = _scaler_unavailable_response()
        return _attach_headers(response, data_quality, computed_at), status
    payload = latest.get("feature_metadata", {})
    response = jsonify(payload)
    status = _status_for_quality(data_quality)
    return _attach_headers(response, data_quality, computed_at), status


@features_bp.route("/vector", methods=["GET"])
def get_feature_vector() -> Tuple[Response, int]:
    """
    Return raw/scaled XGB vectors and LSTM sequence shape.

    Returns:
        200 for normal data, 206 for partial data, or 503 if scalers are absent.
    """
    latest = get_latest_features(json_safe=True)
    data_quality = latest.get("data_quality", "UNKNOWN")
    computed_at = latest.get("computed_at_utc", "never")
    if not scalers_loaded():
        response, status = _scaler_unavailable_response()
        return _attach_headers(response, data_quality, computed_at), status
    sequence = latest.get("lstm_sequence_scaled", [])
    payload: Dict[str, Any] = {
        "xgb_vector": latest.get("xgb_vector_raw", []),
        "xgb_vector_scaled": latest.get("xgb_vector_scaled", []),
        "feature_names": FEATURE_NAMES,
        "sequence_shape": [
            len(sequence),
            len(sequence[0]) if sequence else 0,
            len(sequence[0][0]) if sequence and sequence[0] else 0,
        ],
        "computed_at_utc": computed_at,
        "data_quality": data_quality,
    }
    response = jsonify(payload)
    status = _status_for_quality(data_quality)
    return _attach_headers(response, data_quality, computed_at), status


@features_bp.route("/history", methods=["GET"])
def get_features_history() -> Tuple[Response, int]:
    """
    Return recent feature vectors over time.

    Query params:
        hours: Lookback window in hours, default 6 and capped at 72.

    Returns:
        200 for normal data, 206 for partial data, or 503 if scalers are absent.
    """
    latest = get_latest_features(json_safe=True)
    data_quality = latest.get("data_quality", "UNKNOWN")
    computed_at = latest.get("computed_at_utc", "never")
    if not scalers_loaded():
        response, status = _scaler_unavailable_response()
        return _attach_headers(response, data_quality, computed_at), status
    try:
        hours = int(request.args.get("hours", 6))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid 'hours' parameter"}), 400
    hours = max(1, min(72, hours))
    records = compute_feature_history(hours=hours)
    response = jsonify({"hours": hours, "count": len(records), "records": records})
    status = _status_for_quality(data_quality)
    return _attach_headers(response, data_quality, computed_at), status
