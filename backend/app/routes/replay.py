# backend/app/routes/replay.py
"""NAKSHATRA-KAVACH Layer 7 replay REST endpoints."""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from flask import Blueprint, Response, jsonify, request

from app.services.replay_engine import (
    RAW_ALLOWED_FIELDS,
    VALIDATION_JOBS,
    FullValidationJob,
    ReplaySpeed,
    ReplayState,
    build_replay_timeline,
    get_cached_validation_result,
    load_storm_catalog,
    load_storm_dataframe,
    REPLAY_CONTROLLER,
    STORM_DATA_PATH,
    VALIDATION_ENGINE,
)

logger = logging.getLogger(__name__)
replay_bp = Blueprint("replay", __name__, url_prefix="/api/replay")

_TIMELINE_CACHE: Dict[str, dict] = {}


@replay_bp.after_request
def _add_replay_headers(response: Response) -> Response:
    """Attach replay state to every endpoint response."""
    response.headers["X-Replay-State"] = REPLAY_CONTROLLER.get_status().get("state", "UNKNOWN")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _data_dir_available() -> bool:
    """Return whether the storm cache directory exists."""
    return STORM_DATA_PATH.exists() and STORM_DATA_PATH.is_dir()


def _dir_or_503() -> Response | None:
    """Return a standard 503 when the storm cache is unavailable."""
    if _data_dir_available():
        return None
    return jsonify({"error": f"Storm data directory not found: {STORM_DATA_PATH}"}), 503


def _json_error(message: str, status_code: int) -> Tuple[Response, int]:
    """Build a JSON error response."""
    return jsonify({"success": False, "error": message}), status_code


@replay_bp.route("/catalog", methods=["GET"])
def catalog() -> Tuple[Response, int]:
    """Return all configured replay storms."""
    missing = _dir_or_503()
    if missing:
        return missing
    try:
        return jsonify(load_storm_catalog()), 200
    except Exception as exc:
        logger.error("Replay catalog failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@replay_bp.route("/load", methods=["POST"])
def load() -> Tuple[Response, int]:
    """Load a storm into the replay controller."""
    missing = _dir_or_503()
    if missing:
        return missing
    payload = request.get_json(silent=True) or {}
    storm_id = str(payload.get("storm_id", "")).strip()
    if not storm_id:
        return _json_error("storm_id is required", 400)
    if REPLAY_CONTROLLER.get_status().get("state") == ReplayState.PLAYING.value:
        return _json_error("Cannot load while replay is playing", 409)
    result = REPLAY_CONTROLLER.load_storm(storm_id)
    status_code = 200 if result.get("success") else 404
    return jsonify(result), status_code


@replay_bp.route("/status", methods=["GET"])
def status() -> Tuple[Response, int]:
    """Return current replay status."""
    return jsonify(REPLAY_CONTROLLER.get_status()), 200


@replay_bp.route("/play", methods=["POST"])
def play() -> Tuple[Response, int]:
    """Start playback from the current frame."""
    payload = request.get_json(silent=True) or {}
    try:
        speed = ReplaySpeed.from_value(payload.get("speed", ReplaySpeed.FAST.value))
    except ValueError as exc:
        return _json_error(str(exc), 400)
    result = REPLAY_CONTROLLER.play(speed)
    return jsonify(result), 200 if result.get("success") else 400


@replay_bp.route("/pause", methods=["POST"])
def pause() -> Tuple[Response, int]:
    """Pause active playback."""
    result = REPLAY_CONTROLLER.pause()
    return jsonify(result), 200 if result.get("success") else 400


@replay_bp.route("/resume", methods=["POST"])
def resume() -> Tuple[Response, int]:
    """Resume paused playback."""
    payload = request.get_json(silent=True) or {}
    speed = None
    if payload.get("speed") is not None:
        try:
            speed = ReplaySpeed.from_value(payload.get("speed"))
        except ValueError as exc:
            return _json_error(str(exc), 400)
    result = REPLAY_CONTROLLER.resume(speed)
    return jsonify(result), 200 if result.get("success") else 400


@replay_bp.route("/stop", methods=["POST"])
def stop() -> Tuple[Response, int]:
    """Stop replay and restore live mode."""
    return jsonify(REPLAY_CONTROLLER.stop()), 200


@replay_bp.route("/seek", methods=["POST"])
def seek() -> Tuple[Response, int]:
    """Seek asynchronously to a frame or progress percentage."""
    payload = request.get_json(silent=True) or {}
    status_obj = REPLAY_CONTROLLER.get_status()
    total = int(status_obj.get("total_frames") or 0)
    if total <= 0:
        return _json_error("No storm loaded", 400)
    if "progress_pct" in payload:
        try:
            pct = max(0.0, min(100.0, float(payload["progress_pct"])))
            frame = int(round((pct / 100.0) * max(total - 1, 0)))
        except (TypeError, ValueError):
            return _json_error("Invalid progress_pct", 400)
    else:
        try:
            frame = int(payload.get("frame"))
        except (TypeError, ValueError):
            return _json_error("frame or progress_pct is required", 400)
    result = REPLAY_CONTROLLER.seek(frame)
    return jsonify(result), 202 if result.get("success") else 400


@replay_bp.route("/speed", methods=["POST"])
def speed() -> Tuple[Response, int]:
    """Change replay speed."""
    payload = request.get_json(silent=True) or {}
    try:
        replay_speed = ReplaySpeed.from_value(payload.get("speed"))
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify(REPLAY_CONTROLLER.set_speed(replay_speed)), 200


@replay_bp.route("/timeline/<storm_id>", methods=["GET"])
def timeline(storm_id: str) -> Tuple[Response, int]:
    """Return scrubber timeline data for a storm."""
    missing = _dir_or_503()
    if missing:
        return missing
    try:
        if storm_id not in _TIMELINE_CACHE:
            metadata, df = load_storm_dataframe(storm_id)
            _TIMELINE_CACHE[storm_id] = build_replay_timeline(df, metadata)
        return jsonify(_TIMELINE_CACHE[storm_id]), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error("Timeline failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@replay_bp.route("/validation", methods=["GET"])
def validation() -> Tuple[Response, int]:
    """Return running replay validation metrics."""
    return jsonify(VALIDATION_ENGINE.get_metrics()), 200


@replay_bp.route("/validation/full/<storm_id>", methods=["GET"])
def validation_full(storm_id: str) -> Tuple[Response, int]:
    """Start or return cached full-storm validation."""
    cached = get_cached_validation_result(storm_id)
    if cached:
        return jsonify({"cached": True, "result": cached}), 200
    job = FullValidationJob(storm_id)
    VALIDATION_JOBS[job.job_id] = job
    job.start()
    return jsonify({"job_id": job.job_id, "state": job.state, "progress_pct": job.progress}), 202


@replay_bp.route("/validation/status/<job_id>", methods=["GET"])
def validation_status(job_id: str) -> Tuple[Response, int]:
    """Return status of a full validation job."""
    job = VALIDATION_JOBS.get(job_id)
    if not job:
        return jsonify({"error": f"Job not found: {job_id}"}), 404
    return jsonify(job.status()), 200


@replay_bp.route("/frame/<int:frame_index>", methods=["GET"])
def frame_detail(frame_index: int) -> Tuple[Response, int]:
    """Return full cached pipeline output for one replay frame."""
    frame = REPLAY_CONTROLLER.get_cached_frame(frame_index)
    if frame is None:
        return jsonify({"error": "Frame not cached", "frame": frame_index}), 404
    return jsonify({"frame_index": frame_index, "output": frame}), 200


@replay_bp.route("/data/<storm_id>/raw", methods=["GET"])
def raw_data(storm_id: str) -> Tuple[Response, int]:
    """Return paginated raw storm data with validated field selection."""
    missing = _dir_or_503()
    if missing:
        return missing
    try:
        _, df = load_storm_dataframe(storm_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    total = len(df)
    explicit_end = "end_frame" in request.args
    try:
        start_frame = int(request.args.get("start_frame", 0))
        end_frame = int(request.args.get("end_frame", min(1000, total)))
    except (TypeError, ValueError):
        return jsonify({"error": "start_frame and end_frame must be integers"}), 400

    if start_frame < 0:
        return jsonify({"error": "start_frame must be >= 0"}), 400
    if end_frame > total and explicit_end:
        return jsonify({"error": f"end_frame {end_frame} exceeds total_frames {total}"}), 400
    end_frame = min(end_frame, total)
    if end_frame < start_frame:
        return jsonify({"error": "end_frame must be >= start_frame"}), 400
    if end_frame - start_frame > 5000:
        return jsonify({"error": "Maximum 5000 rows per request"}), 400

    fields_arg = request.args.get("fields")
    if fields_arg:
        fields = [field.strip() for field in fields_arg.split(",") if field.strip()]
    else:
        fields = list(RAW_ALLOWED_FIELDS)
    invalid = [field for field in fields if field not in RAW_ALLOWED_FIELDS]
    if invalid:
        return jsonify({"error": f"Invalid fields: {', '.join(invalid)}", "allowed_fields": sorted(RAW_ALLOWED_FIELDS)}), 400

    records = df.iloc[start_frame:end_frame][fields].to_dict(orient="records")
    return jsonify(
        {
            "storm_id": storm_id,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "total_frames": total,
            "fields": fields,
            "records": records,
        }
    ), 200

