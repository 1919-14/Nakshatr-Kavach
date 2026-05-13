"""Layer 7 replay loader for historical storm frames."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_BASE = Path(__file__).resolve().parent.parent / "data" / "historical"
_ALIASES = {
    "2024_may_g5": "may2024",
    "may2024": "may2024",
    "2024-may-g5": "may2024",
}


def _safe_id(storm_id: str) -> str:
    return "".join(c for c in storm_id if c.isalnum() or c in "_-").lower()


def _resolve_path(storm_id: str) -> Optional[Path]:
    sid = _safe_id(storm_id)
    candidates = [sid, _ALIASES.get(sid)]
    for name in candidates:
        if not name:
            continue
        p = _BASE / f"{name}.json"
        if p.is_file():
            return p
    return None


def load_storm(storm_id: str, offset: int = 0, limit: Optional[int] = None) -> Optional[Dict[str, Any]]:
    path = _resolve_path(storm_id)
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames: List[Dict[str, Any]] = list(payload.get("frames") or [])
    if offset < 0:
        offset = 0
    if limit is not None and limit >= 0:
        sliced = frames[offset: offset + limit]
    else:
        sliced = frames[offset:]
    payload["frames"] = sliced
    payload["frames_total"] = len(frames)
    payload["frames_count"] = len(sliced)
    payload["offset"] = offset
    payload["limit"] = limit
    payload["storm_id"] = payload.get("id") or _safe_id(storm_id)
    return payload

