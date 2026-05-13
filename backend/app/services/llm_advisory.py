"""Layer 6: Groq LLaMA advisory + Hindi + rule fallback."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are NAKSHATRA-KAVACH, an ISRO-style space weather mission advisory engine.
Output STRICT JSON only with keys: threat_assessment (English string), threat_assessment_hi (Hindi string — same meaning),
satellite_bullets (array of English strings), grid_bullets (array of English strings),
timeline (array of strings with IST-relative guidance), recovery_estimate (English), recovery_estimate_hi (Hindi).
Use only facts from the provided context JSON. Formal, precise, time-aware. No markdown outside JSON."""


def _rule_based(ctx: Dict[str, Any]) -> Dict[str, Any]:
    kp = float(ctx.get("kp_now", 3))
    sc = ctx.get("storm_class", "QUIET")
    top_sat = (ctx.get("satellites") or [{}])[0]
    top_grid = (ctx.get("grid") or [{}])[0]
    sat_n = top_sat.get("name", "Tier-1 satellite")
    g_n = top_grid.get("name", "EHV corridor")
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": "RULE_BASED",
        "storm_class": sc,
        "kp": kp,
        "sections": [
            {"title": "THREAT ASSESSMENT", "content": f"Current Kp {kp:.1f} ({sc}). Monitor coupling per IMF context in payload."},
            {"title": "SATELLITE OPERATIONS", "content": f"Highest composite risk: {sat_n} — follow safe-mode policy if composite ≥ 60."},
            {"title": "INDIA GRID ASSESSMENT", "content": f"Top corridor: {g_n} — follow POSOCO thermal/GIC watch protocols."},
            {"title": "RECOMMENDED ACTIONS", "content": "Validate telemetry cadence; confirm backup links; brief operators in English and Hindi."},
        ],
        "hindi_summary": f"वर्तमान Kp {kp:.1f} ({sc})। {sat_n} पर उच्च निगरानी बनाए रखें। {g_n} पर GIC जोखिम की समीक्षा करें।",
        "multilingual": {"en_summary": f"Kp {kp:.1f} ({sc}). Prioritize {sat_n} and grid corridor {g_n}.", "hi_summary": f"Kp {kp:.1f} — {sat_n} और {g_n} के लिए सावधानी।"},
    }


def generate_advisory(ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not GROQ_API_KEY:
        return _rule_based(ctx)

    user_msg = json.dumps(ctx, default=str)[:12000]
    try:
        client = Groq(api_key=GROQ_API_KEY)
        chat = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.2,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = (chat.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM did not return JSON; using rules")
            return _rule_based(ctx)
        sections = [
            {"title": "THREAT ASSESSMENT", "content": data.get("threat_assessment", "")},
            {"title": "THREAT (हिंदी)", "content": data.get("threat_assessment_hi", "")},
            {"title": "SATELLITE OPERATIONS", "content": "\n".join(data.get("satellite_bullets") or [])},
            {"title": "INDIA GRID ASSESSMENT", "content": "\n".join(data.get("grid_bullets") or [])},
            {"title": "TIMELINE (IST-oriented)", "content": "\n".join(data.get("timeline") or [])},
            {"title": "RECOVERY", "content": (data.get("recovery_estimate") or "") + "\n\n" + (data.get("recovery_estimate_hi") or "")},
        ]
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "source": "LLM_GROQ",
            "storm_class": ctx.get("storm_class", "QUIET"),
            "kp": ctx.get("kp_now"),
            "sections": [s for s in sections if s.get("content")],
            "hindi_summary": data.get("threat_assessment_hi") or data.get("recovery_estimate_hi", ""),
            "multilingual": {
                "en_summary": data.get("threat_assessment", ""),
                "hi_summary": data.get("threat_assessment_hi", ""),
            },
        }
    except Exception as e:
        logger.warning("Groq advisory failed: %s", e)
        return _rule_based(ctx)
