# backend/app/services/advisory_generator.py
"""NAKSHATRA-KAVACH Layer 6: mission-control advisory generation."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    # Layer 6 is often imported directly by tests, scripts, and route smoke checks.
    # Loading the backend .env here keeps Groq configuration available even when
    # the Flask app factory has not run yet.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except Exception:  # pragma: no cover - missing dotenv should not break fallback mode
    pass

try:
    import groq
except ImportError:  # pragma: no cover - exercised in environments without optional deps
    groq = None

from app.utils.constants import (
    ADVISORY_CME_MIN_INTERVAL_MINUTES,
    ADVISORY_HISTORY_MAX_LENGTH,
    ADVISORY_ID_PREFIX,
    ADVISORY_MIN_INTERVAL_MINUTES,
    ADVISORY_RECOVERY_INTERVAL_MINUTES,
    ADVISORY_STORM_INTERVAL_MINUTES,
    ADVISORY_VALIDITY_MINUTES,
    GROQ_MAX_RETRIES,
    GROQ_MAX_TOKENS,
    GROQ_MODEL,
    GROQ_RETRY_DELAYS,
    GROQ_TEMPERATURE,
    GROQ_TIMEOUT_SECONDS,
    IST_TIMEZONE,
    MAX_CONSECUTIVE_GROQ_FAILURES,
    WS_EVENT_ADVISORY_SYSTEM_FAILURE,
)
from app.utils.formatters import utcnow_iso

logger = logging.getLogger(__name__)

if groq is not None:
    GroqRateLimitError = groq.RateLimitError
    GroqAPIConnectionError = groq.APIConnectionError
    GroqAPIStatusError = groq.APIStatusError
else:
    class _GroqUnavailableError(Exception):
        """Placeholder exception used when the optional Groq package is absent."""

    GroqRateLimitError = _GroqUnavailableError
    GroqAPIConnectionError = _GroqUnavailableError
    GroqAPIStatusError = _GroqUnavailableError


SYSTEM_PROMPT = """
You are NAKSHATRA-KAVACH, an AI-powered Space Weather Impact Intelligence
System developed to protect ISRO's satellite fleet and India's national
power grid from geomagnetic storms.

Your role is ADVISORY GENERATION - producing precise, operationally
actionable mission advisories for:
  - ISRO ISTRAC (Indian Space Research Organisation - Telemetry,
    Tracking and Command Network) satellite operators
  - POWERGRID / POSOCO / NLDC power grid operations teams
  - Ministry of Earth Sciences emergency management officers

COMMUNICATION STYLE:
  - Formal, precise, time-indexed
  - Use ISRO/NDMA operational terminology
  - Every recommendation includes a specific time window (T-minus format)
  - Use Indian time (IST), Indian satellite names, Indian grid terminology
  - Numbers are specific: "68 Amps" not "high current"
  - Risk levels use ISRO-standard terms: NOMINAL, ELEVATED, DEGRADED, CRITICAL

ADVISORY STRUCTURE (follow this EXACTLY, no deviation):
  You MUST produce the advisory in the following JSON structure.
  Return ONLY valid JSON. No markdown. No preamble. No explanation.
  No backticks. Just the raw JSON object.

  {
    "advisory_title": "<concise title, max 12 words>",
    "threat_assessment": "<2-3 sentences: storm class, Kp trajectory, arrival time, confidence>",
    "satellite_operations": [
      {
        "satellite": "<ISRO official satellite name>",
        "risk_level": "<CRITICAL|HIGH|MODERATE>",
        "action": "<specific operational action>",
        "deadline": "<e.g., Within 25 minutes | T-35min | Immediately>",
        "consequence_if_ignored": "<what happens if action not taken>"
      }
    ],
    "grid_operations": [
      {
        "corridor": "<corridor short name>",
        "voltage_kv": <number>,
        "gic_amps": <number>,
        "action": "<specific load reduction or monitoring action>",
        "deadline": "<specific time>",
        "states_affected": ["<state1>", "<state2>"]
      }
    ],
    "timeline": [
      {"time": "T-<X>min", "event": "<action or event>"},
      {"time": "T+0", "event": "Storm impact / Peak GIC"},
      {"time": "T+<X>hr", "event": "<recovery or follow-up action>"}
    ],
    "recovery_estimate": "<when conditions expected to return to normal>",
    "navigation_alert": "<NavIC/GPS status and user advisory, or null>",
    "hindi_summary": "<4-5 sentence operational summary in Hindi>",
    "priority_action": "<single most important action in one sentence>",
    "advisory_classification": "<STORM_ESCALATION|SATELLITE_CRITICAL|GRID_CRITICAL|STORM_ONSET|CME_IMMINENT|STORM_UPDATE|STORM_RECOVERY|MANUAL_REFRESH>"
  }

MANDATORY RULES:
  1. satellite_operations: include ONLY satellites at HIGH or CRITICAL risk.
     Do NOT mention satellites at MODERATE or LOW risk in the advisory.
  2. grid_operations: include ONLY corridors at HIGH or CRITICAL risk.
  3. timeline: minimum 4 entries, maximum 8 entries.
     Always include T+0 (storm impact) and at least one recovery entry.
  4. All time references use IST. Add "IST" suffix to all clock times.
     T-minus format for countdowns: "T-38min", "T+2hr"
  5. hindi_summary: Write in Devanagari script. Use 80-120 words.
     Cover current Kp/forecast, satellite action, grid action, and data confidence.
  6. recovery_estimate: Be specific - "4-8 hours from storm peak" not "soon".
  7. priority_action: The ONE thing an operator must do RIGHT NOW.
     Must be a concrete action, not a general statement.
  8. NEVER fabricate data. Use only the values provided in the context.
     If a value is not in the context, do not invent it.
  9. NEVER include disclaimers, caveats, or "consult a professional" language.
     This IS the professional advisory system. Be authoritative.
 10. NEVER include markdown formatting (no **, no ##, no bullet points).
     Pure JSON only. The frontend handles all formatting.

TONE CALIBRATION BY URGENCY:
  ROUTINE:  Informational, measured, no alarm language.
  MODERATE: Elevated but controlled, specific recommendations.
  HIGH:     Direct, action-oriented, clear consequences stated.
  CRITICAL: Maximum urgency, imperative language, every second counts.
"""

VALID_ADVISORY_TYPES = {
    "STORM_ESCALATION",
    "SATELLITE_CRITICAL",
    "GRID_CRITICAL",
    "STORM_ONSET",
    "CME_IMMINENT",
    "STORM_UPDATE",
    "STORM_RECOVERY",
    "MANUAL_REFRESH",
}
STORM_CLASS_ORDER = ["QUIET", "G1", "G2", "G3", "G4", "G5"]


class GroqAdvisoryError(Exception):
    """Raised when Groq API advisory generation fails after retries."""


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Return a float while tolerating missing or malformed upstream values."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_round(value: Any, ndigits: int = 1, default: float = 0.0) -> float:
    """Round a numeric value after safe coercion."""
    return round(_safe_float(value, default), ndigits)


def _nested(data: dict, path: List[str], default: Any = None) -> Any:
    """Read a nested dict path without raising KeyError."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _now_ist() -> datetime:
    """Return timezone-aware current time in India Standard Time."""
    return datetime.now(ZoneInfo(IST_TIMEZONE))


def _now_ist_str() -> str:
    """Return current IST timestamp for operator-facing fields."""
    return _now_ist().strftime("%Y-%m-%d %H:%M:%S IST")


def _utc_from_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse the UTC advisory timestamp used by Layer 6 objects."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def determine_advisory_urgency(storm_ctx: dict, sat_ctx: dict, grid_ctx: dict) -> str:
    """Determine overall urgency level for advisory tone calibration."""
    cme_minutes = storm_ctx.get("cme_arrival_minutes")
    cme_imminent = (
        storm_ctx.get("cme_active")
        and cme_minutes is not None
        and _safe_float(cme_minutes, 9999.0) < 30.0
    )
    if (
        storm_ctx.get("peak_class") in ["G4", "G5"]
        or sat_ctx.get("critical_count", 0) >= 2
        or grid_ctx.get("critical_count", 0) >= 1
        or cme_imminent
    ):
        return "CRITICAL"
    if (
        storm_ctx.get("peak_class") == "G3"
        or sat_ctx.get("critical_count", 0) >= 1
        or sat_ctx.get("high_count", 0) >= 3
        or grid_ctx.get("high_count", 0) >= 2
    ):
        return "HIGH"
    if (
        storm_ctx.get("peak_class") in ["G1", "G2"]
        or sat_ctx.get("high_count", 0) >= 1
        or grid_ctx.get("high_count", 0) >= 1
    ):
        return "MODERATE"
    return "ROUTINE"


def build_advisory_context(
    trigger_type: str,
    kp_forecast: dict,
    satellite_risks: dict,
    grid_risks: dict,
    solar_data: dict,
) -> dict:
    """Assemble the compact structured briefing used by Groq and fallback rules."""
    forecast = kp_forecast.get("forecast", {})
    summary = kp_forecast.get("summary", {})
    solar_wind = solar_data.get("solar_wind", {})
    xray = solar_data.get("xray", {})
    cme = solar_data.get("cme", {})
    alert = solar_data.get("alert", {})

    storm_ctx = {
        "current_kp": _safe_round(_nested(kp_forecast, ["current", "kp"], 0.0), 1),
        "current_class": _nested(kp_forecast, ["current", "storm_class"], "QUIET"),
        "kp_3hr": _safe_round(_nested(forecast, ["3hr", "kp"], 0.0), 1),
        "kp_6hr": _safe_round(_nested(forecast, ["6hr", "kp"], 0.0), 1),
        "kp_12hr": _safe_round(_nested(forecast, ["12hr", "kp"], 0.0), 1),
        "kp_24hr": _safe_round(_nested(forecast, ["24hr", "kp"], 0.0), 1),
        "uncertainty_3hr": _safe_round(_nested(forecast, ["3hr", "uncertainty"], 0.0), 1),
        "peak_class": summary.get("peak_storm_class", "QUIET"),
        "storm_probability": round(_safe_float(summary.get("storm_probability_12hr"), 0.0), 2),
        "transit_minutes": round(_safe_float(summary.get("transit_warning_minutes"), 60.0), 0),
        "cme_active": bool(summary.get("cme_active", cme.get("earth_directed", False))),
        "cme_arrival_minutes": cme.get("arrival_minutes_from_now"),
        "bz_current": _safe_round(solar_wind.get("bz_gsm"), 1),
        "sw_speed": round(_safe_float(solar_wind.get("sw_speed_kmps"), 0.0), 0),
        "xray_class": xray.get("xray_class", solar_data.get("xray_class", "Unknown")),
        "official_noaa_class": alert.get("latest_official_class"),
        "dominant_driver": summary.get(
            "dominant_driver",
            _nested(kp_forecast, ["shap", "dominant_driver"], ""),
        ),
        "prediction_confidence": kp_forecast.get("prediction_confidence", "LOW"),
    }

    at_risk_sats: List[dict] = []
    for sat_name, sat_data in satellite_risks.get("tier1", {}).items():
        risk_level = sat_data.get("risk_level", "MINIMAL")
        if risk_level in ["HIGH", "CRITICAL"]:
            at_risk_sats.append({
                "name": sat_name,
                "risk_level": risk_level,
                "composite": round(_safe_float(_nested(sat_data, ["risk_scores", "composite_final"], 0.0)), 0),
                "primary_threat": sat_data.get("primary_threat", "unknown"),
                "action": sat_data.get("recommended_action", ""),
                "urgency": sat_data.get("urgency", "WATCH"),
                "safe_mode_min": _nested(sat_data, ["safe_mode_countdown", "safe_mode_deadline_minutes"]),
                "criticality": sat_data.get("criticality", "HIGH"),
            })
    at_risk_sats.sort(key=lambda x: x["composite"], reverse=True)

    fleet_summary = satellite_risks.get("fleet_summary", {})
    sat_ctx = {
        "critical_count": satellite_risks.get("critical_count", 0),
        "high_count": satellite_risks.get("high_count", 0),
        "at_risk_satellites": at_risk_sats[:8],
        "defense_sats_at_risk": fleet_summary.get("defense_satellites_at_risk", 0),
        "navig_status": fleet_summary.get("navigation_system_status", "NOMINAL"),
        "nav_error_meters": fleet_summary.get("nav_error_meters", 0),
        "economic_value_at_risk_crore": fleet_summary.get("total_economic_value_at_risk_crore", 0),
    }

    at_risk_corridors: List[dict] = []
    for corridor in grid_risks.get("corridors", []):
        if corridor.get("risk_level") in ["HIGH", "CRITICAL"]:
            at_risk_corridors.append({
                "name": corridor.get("short_name", corridor.get("corridor_name", "Unknown corridor")),
                "voltage_kv": corridor.get("voltage_kv", 0),
                "risk_level": corridor.get("risk_level"),
                "gic_amps": round(_safe_float(corridor.get("gic_amps"), 0.0), 0),
                "saturation_pct": round(_safe_float(corridor.get("saturation_risk"), 0.0), 0),
                "load_reduction": _nested(corridor, ["load_reduction", "reduction_percent"], 0),
                "action": _nested(corridor, ["load_reduction", "action"], ""),
                "urgency": _nested(corridor, ["load_reduction", "urgency"], "WATCH"),
                "states": corridor.get("states_affected", []),
                "population_M": corridor.get("population_served_million", 0),
                "damage_time_min": corridor.get("thermal_damage_time_minutes"),
                "spare_available": _nested(corridor, ["economic_impact", "spare_transformer_available"], False),
            })
    at_risk_corridors.sort(key=lambda x: x["gic_amps"], reverse=True)

    ns = grid_risks.get("national_summary", {})
    grid_ctx = {
        "critical_count": ns.get("critical_corridors_count", 0),
        "high_count": ns.get("high_corridors_count", 0),
        "at_risk_corridors": at_risk_corridors[:5],
        "population_at_risk_M": ns.get("population_at_risk_million", 0),
        "economic_impact_crore": ns.get("total_economic_impact_crore", 0),
        "cascade_risk": ns.get("cascade_failure_risk", "LOW"),
        "nldc_alert_required": ns.get("nldc_alert_required", False),
        "max_gic_amps": ns.get("max_gic_amps", 0),
        "max_gic_corridor": ns.get("max_gic_corridor", ""),
        "grid_stability_index": ns.get("grid_stability_index", 100),
    }

    timing_ctx = {
        "current_time_ist": _now_ist().strftime("%Y-%m-%d %H:%M IST"),
        "trigger_type": trigger_type,
        "advisory_urgency": determine_advisory_urgency(storm_ctx, sat_ctx, grid_ctx),
    }

    return {
        "storm": storm_ctx,
        "satellites": sat_ctx,
        "grid": grid_ctx,
        "timing": timing_ctx,
    }


def build_user_message(context: dict, trigger_type: str, advisory_urgency: str) -> str:
    """Build the concise operator briefing injected into the Groq user message."""
    storm = context["storm"]
    sats = context["satellites"]
    grid = context["grid"]
    timing = context["timing"]

    sat_lines = []
    for sat in sats["at_risk_satellites"]:
        deadline = (
            f"Safe mode in {sat['safe_mode_min']} min"
            if sat.get("safe_mode_min") is not None
            else "Monitor closely"
        )
        sat_lines.append(
            f"  - {sat['name']}: {sat['risk_level']} ({sat['composite']:.0f}%) | "
            f"Threat: {sat['primary_threat']} | {deadline} | Action: {sat['action']}"
        )

    corridor_lines = []
    for corridor in grid["at_risk_corridors"]:
        damage = (
            f"Thermal damage in {float(corridor['damage_time_min']):.0f}min"
            if corridor.get("damage_time_min") is not None
            else "No immediate thermal risk"
        )
        corridor_lines.append(
            f"  - {corridor['name']} {corridor['voltage_kv']}kV: {corridor['risk_level']} | "
            f"GIC={corridor['gic_amps']:.0f}A | Load reduction: {corridor['load_reduction']}% | "
            f"{damage} | States: {', '.join(corridor['states'])}"
        )

    cme_str = "No active Earth-directed CME"
    if storm["cme_active"] and storm.get("cme_arrival_minutes") is not None:
        cme_str = f"Earth-directed CME - arrival in {float(storm['cme_arrival_minutes']):.0f} minutes"

    navig_str = ""
    if sats["navig_status"] != "NOMINAL":
        navig_str = (
            f"\nNavIC Navigation: {sats['navig_status']} - "
            f"Estimated positioning error: {float(sats['nav_error_meters']):.0f}m"
        )

    sat_block = "\n".join(sat_lines) if sat_lines else "  None above threshold"
    corridor_block = "\n".join(corridor_lines) if corridor_lines else "  None above threshold"

    return f"""NAKSHATRA-KAVACH ADVISORY BRIEF
Generated: {timing['current_time_ist']}
Trigger: {trigger_type} | Urgency: {advisory_urgency}

SPACE WEATHER STATUS:
  Current Kp: {storm['current_kp']} ({storm['current_class']})
  Forecast: Kp {storm['kp_3hr']} @ 3hr | {storm['kp_6hr']} @ 6hr | {storm['kp_12hr']} @ 12hr | {storm['kp_24hr']} @ 24hr
  Peak predicted class: {storm['peak_class']}
  Storm probability (12hr): {storm['storm_probability'] * 100:.0f}%
  Prediction confidence: {storm['prediction_confidence']}
  Warning window: {storm['transit_minutes']:.0f} minutes (L1 transit time)
  {cme_str}
  Bz current: {storm['bz_current']} nT | SW Speed: {storm['sw_speed']:.0f} km/s
  X-ray class: {storm['xray_class']}
  NOAA official alert: {storm['official_noaa_class'] or 'None issued'}
  Primary driver: {storm['dominant_driver']}

SATELLITE RISK SUMMARY:
  Critical: {sats['critical_count']} satellites | High: {sats['high_count']} satellites
  Defense satellites at risk: {sats['defense_sats_at_risk']}
  NavIC status: {sats['navig_status']}{navig_str}
  Asset value at risk: INR {float(sats['economic_value_at_risk_crore']):,.0f} crore

AT-RISK SATELLITES:
{sat_block}

POWER GRID STATUS:
  Critical corridors: {grid['critical_count']} | High risk: {grid['high_count']}
  Population at risk: {float(grid['population_at_risk_M']):.1f} million
  Economic impact: INR {float(grid['economic_impact_crore']):,.0f} crore
  Max GIC: {float(grid['max_gic_amps']):.0f}A at {grid['max_gic_corridor']}
  Cascade failure risk: {grid['cascade_risk']}
  NLDC alert required: {'YES' if grid['nldc_alert_required'] else 'NO'}

AT-RISK TRANSMISSION CORRIDORS:
{corridor_block}

Generate the mission advisory JSON now. Follow the exact structure specified in your instructions.
Advisory urgency level: {advisory_urgency}."""


class PromptQAChecker:
    """Validate LLM advisory quality before accepting it for operations."""

    def validate(self, advisory_content: dict, context: dict) -> Tuple[bool, List[str]]:
        """Return whether advisory content satisfies anti-hallucination checks."""
        issues: List[str] = []
        storm = context.get("storm", {})
        sats = context.get("satellites", {})

        at_risk_names = {s["name"] for s in sats.get("at_risk_satellites", [])}
        for sat_op in advisory_content.get("satellite_operations", []):
            if sat_op.get("satellite") not in at_risk_names:
                issues.append(f"Hallucinated satellite: {sat_op.get('satellite')} - not in at-risk list")

        hindi = advisory_content.get("hindi_summary", "")
        if not any("\u0900" <= char <= "\u097F" for char in hindi):
            issues.append("Hindi summary missing Devanagari characters")

        priority = advisory_content.get("priority_action", "")
        if len(priority.strip()) < 10:
            issues.append("Priority action too short or empty")

        timeline = advisory_content.get("timeline", [])
        if not any("T+0" in entry.get("time", "") for entry in timeline if isinstance(entry, dict)):
            issues.append("Timeline missing T+0 (storm impact) entry")

        classification = advisory_content.get("advisory_classification")
        if classification not in VALID_ADVISORY_TYPES:
            issues.append(f"Invalid advisory_classification: {classification}")

        threat = advisory_content.get("threat_assessment", "")
        kp_mentions = re.findall(r"Kp[=\s:]*([\d.]+)", threat)
        actual_kp = _safe_float(storm.get("kp_6hr"), _safe_float(storm.get("current_kp"), 0.0))
        for kp_mention in kp_mentions:
            mentioned_kp = _safe_float(kp_mention, actual_kp)
            if abs(mentioned_kp - actual_kp) > 3.0:
                issues.append(f"Hallucinated Kp value: {mentioned_kp} (actual forecast: {actual_kp})")

        if issues:
            logger.warning("Advisory QA failed (%d issues): %s", len(issues), "; ".join(issues))
        return len(issues) == 0, issues


class GroqAdvisoryClient:
    """Manage Groq LLM calls, JSON parsing, retries, usage, and QA validation."""

    def __init__(self, api_key: str):
        """Create a Groq client with usage counters."""
        if groq is None:
            raise GroqAdvisoryError("groq package is not installed")
        try:
            try:
                import httpx

                self.client = groq.Groq(
                    api_key=api_key,
                    http_client=httpx.Client(timeout=GROQ_TIMEOUT_SECONDS),
                )
            except ImportError:
                self.client = groq.Groq(api_key=api_key)
        except Exception as exc:
            raise GroqAdvisoryError(f"Groq client initialization failed: {exc}") from exc
        self.request_count = 0
        self.total_tokens_used = 0
        self.last_request_timestamp: Optional[float] = None
        self.rate_limit_tokens_per_minute = 14400

    def generate_advisory(
        self,
        system_prompt: str,
        user_message: str,
        advisory_urgency: str,
        context: dict,
    ) -> dict:
        """Call Groq once for this trigger event and return validated JSON."""
        last_error: Optional[BaseException] = None

        for attempt in range(GROQ_MAX_RETRIES):
            try:
                start_time = time.time()
                temperature = {
                    "CRITICAL": 0.1,
                    "HIGH": 0.15,
                    "MODERATE": 0.2,
                    "ROUTINE": 0.2,
                }.get(advisory_urgency, GROQ_TEMPERATURE)

                response = self.client.chat.completions.create(
                    model=GROQ_MODEL,
                    max_tokens=GROQ_MAX_TOKENS,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    response_format={"type": "json_object"},
                )

                elapsed_ms = (time.time() - start_time) * 1000
                raw_content = response.choices[0].message.content
                usage = getattr(response, "usage", None)
                tokens_used = int(getattr(usage, "total_tokens", 0) or 0)

                self.request_count += 1
                self.total_tokens_used += tokens_used
                self.last_request_timestamp = time.time()

                logger.info(
                    "Groq API call: attempt=%d elapsed=%.0fms tokens=%d model=%s",
                    attempt + 1,
                    elapsed_ms,
                    tokens_used,
                    GROQ_MODEL,
                )

                advisory_dict = self._parse_and_validate(raw_content, context)
                advisory_dict["_groq_metadata"] = {
                    "tokens_used": tokens_used,
                    "elapsed_ms": round(elapsed_ms, 0),
                    "model": GROQ_MODEL,
                    "temperature": temperature,
                    "attempt_number": attempt + 1,
                }
                return advisory_dict

            except GroqRateLimitError as exc:
                logger.warning("Groq rate limit hit: %s", exc)
                last_error = exc
                if attempt < GROQ_MAX_RETRIES - 1:
                    time.sleep(GROQ_RETRY_DELAYS[attempt] * 3)
            except GroqAPIConnectionError as exc:
                logger.error("Groq connection error: %s", exc)
                last_error = exc
                if attempt < GROQ_MAX_RETRIES - 1:
                    time.sleep(GROQ_RETRY_DELAYS[attempt])
            except GroqAPIStatusError as exc:
                logger.error("Groq API status error: %s", exc)
                last_error = exc
                status_code = getattr(exc, "status_code", None)
                if status_code in [500, 502, 503] and attempt < GROQ_MAX_RETRIES - 1:
                    time.sleep(GROQ_RETRY_DELAYS[attempt])
                else:
                    break
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error("Groq JSON/QA validation error: %s", exc)
                last_error = exc
                if attempt < GROQ_MAX_RETRIES - 1:
                    time.sleep(GROQ_RETRY_DELAYS[attempt])
            except Exception as exc:
                logger.error("Unexpected Groq advisory failure: %s", exc)
                last_error = exc
                break

        raise GroqAdvisoryError(f"Groq API failed after {GROQ_MAX_RETRIES} attempts: {last_error}")

    def _parse_and_validate(self, raw_content: str, context: Optional[dict] = None) -> dict:
        """Parse Groq JSON, enforce required fields, and run prompt QA when context exists."""
        content = (raw_content or "").strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        advisory = json.loads(content)
        required_fields = [
            "advisory_title",
            "threat_assessment",
            "satellite_operations",
            "grid_operations",
            "timeline",
            "recovery_estimate",
            "navigation_alert",
            "hindi_summary",
            "priority_action",
            "advisory_classification",
        ]
        missing = [field for field in required_fields if field not in advisory]
        if missing:
            raise ValueError(f"Advisory missing required fields: {missing}")

        if not isinstance(advisory.get("satellite_operations"), list):
            advisory["satellite_operations"] = []
        if not isinstance(advisory.get("grid_operations"), list):
            advisory["grid_operations"] = []
        if not isinstance(advisory.get("timeline"), list):
            advisory["timeline"] = []
        if not advisory["timeline"]:
            advisory["timeline"] = [{"time": "T+0", "event": "Storm peak intensity"}]

        if context is not None:
            ok, issues = PromptQAChecker().validate(advisory, context)
            if not ok:
                raise ValueError(f"Advisory QA failed: {issues}")
        return advisory


class RuleBasedAdvisoryGenerator:
    """Template generator used when Groq is absent, degraded, or fails QA."""

    STORM_TEMPLATES = {
        "G5": {
            "title": "EXTREME G5 Geomagnetic Storm - Maximum Alert",
            "threat": (
                "An extreme G5 geomagnetic storm is in progress or imminent. "
                "This is the maximum NOAA geomagnetic storm classification. "
                "Critical satellites and EHV corridors face severe damage risk."
            ),
            "priority": "CRITICAL: Initiate safe mode on all HIGH and CRITICAL GEO satellites immediately.",
        },
        "G4": {
            "title": "SEVERE G4 Geomagnetic Storm - High Alert",
            "threat": (
                "A severe G4 geomagnetic storm is predicted. GEO satellites face "
                "significant surface charging risk and northern India EHV corridors "
                "face moderate to severe GIC exposure."
            ),
            "priority": "HIGH: Prepare satellite safe modes and EHV load reduction now.",
        },
        "G3": {
            "title": "STRONG G3 Geomagnetic Storm - Elevated Alert",
            "threat": (
                "A strong G3 geomagnetic storm is predicted. GEO satellites face "
                "noticeable surface charging and major transmission corridors may "
                "experience significant GIC flows."
            ),
            "priority": "ELEVATED: Initiate storm monitoring and protective measures.",
        },
        "G2": {
            "title": "MODERATE G2 Geomagnetic Storm - Watch",
            "threat": "A moderate G2 geomagnetic storm is predicted with minor satellite effects expected.",
            "priority": "WATCH: Monitor satellite housekeeping telemetry.",
        },
        "G1": {
            "title": "MINOR G1 Geomagnetic Storm - Routine Alert",
            "threat": "A minor G1 geomagnetic storm is predicted with negligible India grid impact expected.",
            "priority": "MONITOR: Continue routine monitoring.",
        },
        "QUIET": {
            "title": "Space Weather - No Significant Storm Activity",
            "threat": "Current space weather conditions are quiet. No storm threat is indicated.",
            "priority": "NOMINAL: No immediate operational action required.",
        },
        "STORM_RECOVERY": {
            "title": "Geomagnetic Storm Subsiding - Recovery Phase",
            "threat": (
                "The geomagnetic storm is subsiding and Kp values are declining. "
                "Recommend controlled restoration after confirming stable telemetry."
            ),
            "priority": "RECOVERY: Begin controlled restoration of normal operations.",
        },
    }
    SATELLITE_ACTION_TEMPLATES = {
        "CRITICAL": "Initiate immediate safe mode. Suspend non-essential payload operations. Alert mission operations team.",
        "HIGH": "Prepare safe mode procedures. Increase housekeeping telemetry monitoring to 5-minute intervals.",
        "MODERATE": "Elevate monitoring. Verify storm protection procedures are accessible.",
    }
    GRID_ACTION_TEMPLATES = {
        "CRITICAL": "EMERGENCY: Reduce transformer loading by 50% immediately. Alert NLDC and activate relay monitoring.",
        "HIGH": "Reduce loading by 35%. Alert regional load dispatch centre and monitor transformer temperature continuously.",
        "MODERATE": "Reduce loading by 15% as precaution and monitor reactive power absorption.",
    }
    HINDI_TEMPLATES = {
        "G5": "अत्यंत भीषण G5 भू-चुंबकीय तूफान। महत्वपूर्ण उपग्रहों को तत्काल सुरक्षित मोड में डालें और विद्युत ग्रिड संचालकों को अभी सूचित करें।",
        "G4": "गंभीर G4 भू-चुंबकीय तूफान की चेतावनी। इसरो उपग्रहों और बिजली ग्रिड पर तत्काल सुरक्षात्मक कार्रवाई आवश्यक है।",
        "G3": "तीव्र G3 भू-चुंबकीय तूफान। उपग्रह सुरक्षा प्रक्रियाएं और ग्रिड निगरानी तत्काल शुरू करें।",
        "G2": "मध्यम G2 भू-चुंबकीय तूफान। उपग्रह टेलीमेट्री की निगरानी बढ़ाएं।",
        "G1": "मामूली G1 भू-चुंबकीय तूफान। सामान्य निगरानी जारी रखें।",
        "QUIET": "अंतरिक्ष मौसम सामान्य है। कोई तत्काल कार्रवाई आवश्यक नहीं।",
        "STORM_RECOVERY": "भू-चुंबकीय तूफान कम हो रहा है। स्थिरता की पुष्टि के बाद सामान्य परिचालन धीरे-धीरे बहाल करें।",
    }

    def generate(self, context: dict, trigger_type: str, advisory_urgency: str) -> dict:
        """Generate a schema-compatible advisory from deterministic templates."""
        storm = context["storm"]
        sats = context["satellites"]
        grid = context["grid"]
        peak_class = storm.get("peak_class", "QUIET")
        template_key = "STORM_RECOVERY" if trigger_type == "STORM_RECOVERY" else peak_class
        template = self.STORM_TEMPLATES.get(template_key, self.STORM_TEMPLATES["QUIET"])

        sat_ops = []
        for sat in sats.get("at_risk_satellites", []):
            deadline = (
                f"Within {sat['safe_mode_min']} minutes"
                if sat.get("safe_mode_min") is not None
                else "As soon as possible"
            )
            sat_ops.append({
                "satellite": sat["name"],
                "risk_level": sat["risk_level"],
                "action": self.SATELLITE_ACTION_TEMPLATES.get(sat["risk_level"], self.SATELLITE_ACTION_TEMPLATES["MODERATE"]),
                "deadline": deadline,
                "consequence_if_ignored": "Potential surface charging damage, drag-induced orbit error, or SEU-induced anomaly",
            })

        grid_ops = []
        for corridor in grid.get("at_risk_corridors", []):
            grid_ops.append({
                "corridor": corridor["name"],
                "voltage_kv": corridor["voltage_kv"],
                "gic_amps": corridor["gic_amps"],
                "action": self.GRID_ACTION_TEMPLATES.get(corridor["risk_level"], self.GRID_ACTION_TEMPLATES["MODERATE"]),
                "deadline": "Within 20 minutes",
                "states_affected": corridor["states"],
            })

        transit = _safe_float(storm.get("transit_minutes"), 60.0)
        timeline = [
            {"time": f"T-{transit:.0f}min", "event": "Current time - storm warning window open"},
            {"time": "T-30min", "event": "Complete satellite safe mode decisions"},
            {"time": "T-20min", "event": "Complete EHV load reduction actions"},
            {"time": "T+0", "event": f"Storm impact - peak {peak_class} conditions"},
            {"time": "T+2hr", "event": "Assess satellite and grid status"},
            {"time": "T+6hr", "event": "Begin controlled restoration if Kp trend is declining"},
        ]

        return {
            "advisory_title": template["title"],
            "threat_assessment": (
                f"{template['threat']} Current Kp: {storm['current_kp']} "
                f"({storm['current_class']}). Predicted peak: {peak_class}. "
                f"Storm probability (12hr): {storm['storm_probability'] * 100:.0f}%."
            ),
            "satellite_operations": sat_ops,
            "grid_operations": grid_ops,
            "timeline": timeline,
            "recovery_estimate": "Conditions expected to improve within 6-12 hours of storm peak. Monitor Kp forecast continuously.",
            "navigation_alert": (
                f"NavIC status: {sats['navig_status']}. Expected positioning error: {float(sats['nav_error_meters']):.0f}m"
                if sats.get("navig_status") != "NOMINAL"
                else None
            ),
            "hindi_summary": self.HINDI_TEMPLATES.get(template_key, self.HINDI_TEMPLATES["QUIET"]),
            "priority_action": template["priority"],
            "advisory_classification": trigger_type,
            "_rule_based": True,
            "_advisory_urgency": advisory_urgency,
        }


def _context_snapshot(kp_forecast: dict, satellite_risks: dict, grid_risks: dict) -> dict:
    """Build the compact context snapshot embedded in each advisory object."""
    ns = grid_risks.get("national_summary", {})
    summary = kp_forecast.get("summary", {})
    return {
        "kp_at_generation": _safe_float(_nested(kp_forecast, ["current", "kp"], 0.0), 0.0),
        "storm_class": summary.get("peak_storm_class", _nested(kp_forecast, ["current", "storm_class"], "QUIET")),
        "satellites_critical": satellite_risks.get("critical_count", 0),
        "satellites_high": satellite_risks.get("high_count", 0),
        "corridors_critical": ns.get("critical_corridors_count", 0),
        "corridors_high": ns.get("high_corridors_count", 0),
        "transit_minutes": summary.get("transit_warning_minutes", 60.0),
        "cme_active": bool(summary.get("cme_active", False)),
    }


def _derive_sections(advisory: dict) -> List[dict]:
    """Create the legacy frontend section list from canonical advisory content."""
    content = advisory.get("content", advisory)
    sat_lines = [
        f"- {op.get('satellite')}: {op.get('action')} Deadline: {op.get('deadline')}. Consequence: {op.get('consequence_if_ignored')}"
        for op in content.get("satellite_operations", [])
    ]
    grid_lines = [
        f"- {op.get('corridor')} {op.get('voltage_kv')}kV: {op.get('gic_amps')}A. {op.get('action')} Deadline: {op.get('deadline')}"
        for op in content.get("grid_operations", [])
    ]
    timeline_lines = [
        f"{entry.get('time')}: {entry.get('event')}"
        for entry in content.get("timeline", [])
        if isinstance(entry, dict)
    ]
    return [
        {"title": "THREAT ASSESSMENT", "content": content.get("threat_assessment", "")},
        {"title": "SATELLITE OPERATIONS", "content": "\n".join(sat_lines) if sat_lines else "No HIGH or CRITICAL satellite actions."},
        {"title": "INDIA GRID ASSESSMENT", "content": "\n".join(grid_lines) if grid_lines else "No HIGH or CRITICAL grid corridor actions."},
        {"title": "TIMELINE AND RECOVERY", "content": "\n".join(timeline_lines + [content.get("recovery_estimate", "")])},
    ]


def add_frontend_compatibility(advisory: dict) -> dict:
    """Attach current dashboard-compatible fields beside the canonical contract."""
    enriched = copy.deepcopy(advisory)
    content = enriched.get("content", {})
    snapshot = enriched.get("context_snapshot", {})
    enriched["generated_at"] = enriched.get("generated_at_utc")
    enriched["source"] = "AI_GENERATED" if enriched.get("advisory_source") == "LLM_GROQ" else "RULE_BASED"
    enriched["storm_class"] = snapshot.get("storm_class", "QUIET")
    enriched["kp"] = snapshot.get("kp_at_generation", 0.0)
    enriched["sections"] = _derive_sections(enriched)
    enriched["hindi_summary"] = content.get("hindi_summary")
    return enriched


def generate_whatsapp_summary(advisory: dict) -> str:
    """Generate a WhatsApp-ready summary capped at 300 characters."""
    content = advisory["content"]
    snapshot = advisory["context_snapshot"]
    priority = content.get("priority_action", "")
    priority_short = priority[:120] if len(priority) > 120 else priority
    summary = (
        "NAKSHATRA-KAVACH ALERT\n"
        f"Storm: {snapshot.get('storm_class')} | Kp: {snapshot.get('kp_at_generation')}\n"
        f"Critical Sats: {snapshot.get('satellites_critical')}\n"
        f"ACTION: {priority_short}\n"
        "- ISRO Space Weather Advisory"
    )
    return summary[:300]


def generate_sms_text(advisory: dict) -> str:
    """Generate a 160-character SMS version of the advisory."""
    content = advisory["content"]
    storm_class = advisory["context_snapshot"].get("storm_class", "QUIET")
    priority = content.get("priority_action", "")
    return f"NAKSHATRA-KAVACH: {storm_class} storm. {priority[:100]} -ISRO"[:160]


class AdvisoryGenerator:
    """Orchestrate context construction, Groq generation, fallback, and packaging."""

    def __init__(self):
        """Initialize Groq if configured; otherwise enter deterministic fallback mode."""
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.groq_client: Optional[GroqAdvisoryClient] = None
        if api_key:
            try:
                self.groq_client = GroqAdvisoryClient(api_key)
            except Exception as exc:
                logger.warning("Groq client unavailable - using fallback: %s", exc)
        else:
            logger.warning("GROQ_API_KEY not set - using rule-based fallback")
        self.rule_based = RuleBasedAdvisoryGenerator()
        self.fallback_mode = self.groq_client is None
        self.consecutive_failures = 0
        self.max_consecutive_failures = MAX_CONSECUTIVE_GROQ_FAILURES

    @property
    def groq_api_available(self) -> bool:
        """Return true when Groq can be attempted for new advisories."""
        return self.groq_client is not None and not self.fallback_mode

    def generate(
        self,
        trigger_type: str,
        kp_forecast: dict,
        satellite_risks: dict,
        grid_risks: dict,
        solar_data: dict,
    ) -> dict:
        """Generate a full Layer 6 advisory object via Groq or fallback."""
        context = build_advisory_context(trigger_type, kp_forecast, satellite_risks, grid_risks, solar_data)
        advisory_urgency = context["timing"]["advisory_urgency"]
        advisory_content: Optional[dict] = None
        advisory_source = "RULE_BASED"
        generation_start = time.time()

        if (
            self.groq_client is not None
            and not self.fallback_mode
            and self.consecutive_failures < self.max_consecutive_failures
        ):
            try:
                user_message = build_user_message(context, trigger_type, advisory_urgency)
                advisory_content = self.groq_client.generate_advisory(
                    SYSTEM_PROMPT,
                    user_message,
                    advisory_urgency,
                    context,
                )
                advisory_source = "LLM_GROQ"
                self.consecutive_failures = 0
                logger.info("Advisory generated via Groq LLM")
            except GroqAdvisoryError as exc:
                self.consecutive_failures += 1
                logger.error(
                    "Groq advisory failed (%d/%d): %s",
                    self.consecutive_failures,
                    self.max_consecutive_failures,
                    exc,
                )
                if self.consecutive_failures >= self.max_consecutive_failures:
                    self.fallback_mode = True
                    logger.warning("Groq API unreliable - switching to rule-based fallback")

        if advisory_content is None:
            try:
                advisory_content = self.rule_based.generate(context, trigger_type, advisory_urgency)
                advisory_source = "RULE_BASED"
                logger.info("Advisory generated via rule-based fallback")
            except Exception as exc:
                logger.critical("Rule-based advisory generation failed: %s", exc, exc_info=True)
                try:
                    from app import socketio

                    socketio.emit(WS_EVENT_ADVISORY_SYSTEM_FAILURE, {"error": str(exc), "trigger_type": trigger_type})
                except Exception:
                    logger.debug("Could not emit advisory_system_failure", exc_info=True)
                raise

        storm_class = kp_forecast.get("summary", {}).get("peak_storm_class", "QUIET")
        timestamp_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        advisory_id = f"{ADVISORY_ID_PREFIX}-{timestamp_str}-{storm_class}"
        generated_at_utc = utcnow_iso()

        advisory = {
            "advisory_id": advisory_id,
            "generated_at_utc": generated_at_utc,
            "generated_at_ist": _now_ist_str(),
            "advisory_source": advisory_source,
            "trigger_type": trigger_type,
            "advisory_urgency": advisory_urgency,
            "content": advisory_content,
            "context_snapshot": _context_snapshot(kp_forecast, satellite_risks, grid_risks),
            "distribution": {
                "pdf_generated": False,
                "whatsapp_summary": None,
                "sms_text": None,
                "email_sent": False,
            },
            "expires_at_utc": (
                datetime.utcnow() + timedelta(minutes=ADVISORY_VALIDITY_MINUTES)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        advisory["distribution"]["whatsapp_summary"] = generate_whatsapp_summary(advisory)
        advisory["distribution"]["sms_text"] = generate_sms_text(advisory)
        generation_elapsed = (time.time() - generation_start) * 1000
        logger.info(
            "Advisory complete: ID=%s Source=%s Urgency=%s Time=%.0fms",
            advisory_id,
            advisory_source,
            advisory_urgency,
            generation_elapsed,
        )
        return add_frontend_compatibility(advisory)


_advisory_lock = threading.RLock()
_trigger_state_lock = threading.RLock()
LATEST_ADVISORY: Optional[dict] = None
ADVISORY_HISTORY = deque(maxlen=ADVISORY_HISTORY_MAX_LENGTH)

last_advisory_timestamp_utc: Optional[datetime] = None
last_advisory_type: Optional[str] = None
previous_storm_class: str = "QUIET"
previous_satellite_risk_levels: Dict[str, str] = {}
previous_corridor_risk_levels: Dict[str, str] = {}
previous_cme_arrival_minutes: Optional[float] = None
previous_storm_onset_detected: bool = False


def _storm_class_index(storm_class: str) -> int:
    """Return comparable severity index for a storm class."""
    return STORM_CLASS_ORDER.index(storm_class) if storm_class in STORM_CLASS_ORDER else 0


def _minutes_since_last_advisory(now: Optional[datetime] = None) -> float:
    """Return minutes since the last accepted advisory."""
    current = now or datetime.now(timezone.utc)
    if last_advisory_timestamp_utc is None:
        return 9999.0
    last = last_advisory_timestamp_utc
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0.0, (current - last).total_seconds() / 60.0)


def _was_onset_detected_before() -> bool:
    """Return whether storm onset was already recorded in trigger state."""
    return previous_storm_onset_detected


def _update_previous_state(kp_forecast: dict, satellite_risks: dict, grid_risks: dict, solar_data: dict) -> None:
    """Update trigger comparison state after an advisory-generating event."""
    global previous_storm_class, previous_satellite_risk_levels
    global previous_corridor_risk_levels, previous_cme_arrival_minutes
    global previous_storm_onset_detected

    previous_storm_class = _nested(kp_forecast, ["current", "storm_class"], "QUIET")
    previous_satellite_risk_levels = {
        name: sat.get("risk_level", "MINIMAL")
        for name, sat in satellite_risks.get("tier1", {}).items()
    }
    previous_corridor_risk_levels = {
        corridor.get("corridor_id"): corridor.get("risk_level", "MINIMAL")
        for corridor in grid_risks.get("corridors", [])
        if corridor.get("corridor_id")
    }
    previous_cme_arrival_minutes = solar_data.get("cme", {}).get("arrival_minutes_from_now")
    previous_storm_onset_detected = bool(kp_forecast.get("summary", {}).get("storm_onset_detected", False))


def check_advisory_triggers(kp_forecast: dict, satellite_risks: dict, grid_risks: dict, solar_data: dict) -> Optional[dict]:
    """Check event-driven Layer 6 advisory triggers and update state on fire."""
    now = datetime.now(timezone.utc)
    with _trigger_state_lock:
        minutes_since_last = _minutes_since_last_advisory(now)
        current_class = _nested(kp_forecast, ["current", "storm_class"], "QUIET")
        cme = solar_data.get("cme", {})
        cme_arrival = cme.get("arrival_minutes_from_now")

        if (
            cme.get("earth_directed")
            and cme_arrival is not None
            and _safe_float(cme_arrival, 9999.0) < 60.0
            and (previous_cme_arrival_minutes is None or _safe_float(previous_cme_arrival_minutes, 9999.0) >= 60.0)
            and minutes_since_last >= ADVISORY_CME_MIN_INTERVAL_MINUTES
        ):
            _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
            return {"trigger_type": "CME_IMMINENT", "priority": "CRITICAL"}

        if minutes_since_last < ADVISORY_MIN_INTERVAL_MINUTES:
            return None

        if _storm_class_index(current_class) > _storm_class_index(previous_storm_class):
            _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
            return {"trigger_type": "STORM_ESCALATION", "priority": "CRITICAL"}

        if kp_forecast.get("summary", {}).get("storm_onset_detected") and not _was_onset_detected_before():
            _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
            return {"trigger_type": "STORM_ONSET", "priority": "HIGH"}

        for sat_name, sat in satellite_risks.get("tier1", {}).items():
            new_level = sat.get("risk_level", "MINIMAL")
            prev_level = previous_satellite_risk_levels.get(sat_name, "MINIMAL")
            new_composite = _safe_float(_nested(sat, ["risk_scores", "composite_final"], 0.0), 0.0)
            prev_critical = prev_level == "CRITICAL"
            if new_level == "CRITICAL" and not prev_critical and new_composite >= 80.0:
                _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
                return {"trigger_type": "SATELLITE_CRITICAL", "priority": "HIGH", "satellite": sat_name}

        for corridor in grid_risks.get("corridors", []):
            corridor_id = corridor.get("corridor_id")
            new_level = corridor.get("risk_level", "MINIMAL")
            prev_level = previous_corridor_risk_levels.get(corridor_id, "MINIMAL")
            if new_level == "CRITICAL" and prev_level != "CRITICAL":
                _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
                return {"trigger_type": "GRID_CRITICAL", "priority": "HIGH", "corridor": corridor_id}

        if current_class in ["G3", "G4", "G5"] and minutes_since_last >= ADVISORY_STORM_INTERVAL_MINUTES:
            _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
            return {"trigger_type": "STORM_UPDATE", "priority": "MODERATE"}

        if (
            current_class in ["QUIET", "G1"]
            and previous_storm_class in ["G3", "G4", "G5"]
            and minutes_since_last >= ADVISORY_RECOVERY_INTERVAL_MINUTES
        ):
            _update_previous_state(kp_forecast, satellite_risks, grid_risks, solar_data)
            return {"trigger_type": "STORM_RECOVERY", "priority": "LOW"}

        return None


def update_latest_advisory(advisory: dict) -> None:
    """Atomically replace the latest advisory and update dedupe state."""
    global LATEST_ADVISORY, last_advisory_timestamp_utc, last_advisory_type
    with _advisory_lock:
        LATEST_ADVISORY = copy.deepcopy(advisory)
        last_advisory_type = advisory.get("trigger_type")
        parsed = _utc_from_iso(advisory.get("generated_at_utc"))
        if parsed is not None:
            last_advisory_timestamp_utc = parsed


def _build_default_advisory() -> dict:
    """Return a non-error nominal advisory before any generated advisory exists."""
    generated_at = utcnow_iso()
    content = {
        "advisory_title": "Space Weather Advisory Standby",
        "threat_assessment": "No Layer 6 advisory has been generated in this runtime. Current advisory state is standby.",
        "satellite_operations": [],
        "grid_operations": [],
        "timeline": [
            {"time": "T+0", "event": "Awaiting trigger-driven advisory generation"},
            {"time": "T+30min", "event": "Continue monitoring upstream space weather intelligence"},
        ],
        "recovery_estimate": "No active storm recovery estimate available.",
        "navigation_alert": None,
        "hindi_summary": "वर्तमान में कोई सक्रिय सलाह जारी नहीं है। निगरानी जारी रखें।",
        "priority_action": "Continue monitoring upstream Layer 3, 4, and 5 intelligence.",
        "advisory_classification": "MANUAL_REFRESH",
        "_rule_based": True,
    }
    advisory = {
        "advisory_id": f"{ADVISORY_ID_PREFIX}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-QUIET",
        "generated_at_utc": generated_at,
        "generated_at_ist": _now_ist_str(),
        "advisory_source": "RULE_BASED",
        "trigger_type": "MANUAL_REFRESH",
        "advisory_urgency": "ROUTINE",
        "content": content,
        "context_snapshot": {
            "kp_at_generation": 0.0,
            "storm_class": "QUIET",
            "satellites_critical": 0,
            "satellites_high": 0,
            "corridors_critical": 0,
            "corridors_high": 0,
            "transit_minutes": 60.0,
            "cme_active": False,
        },
        "distribution": {"pdf_generated": False, "whatsapp_summary": None, "sms_text": None, "email_sent": False},
        "expires_at_utc": (datetime.utcnow() + timedelta(minutes=ADVISORY_VALIDITY_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    advisory["distribution"]["whatsapp_summary"] = generate_whatsapp_summary(advisory)
    advisory["distribution"]["sms_text"] = generate_sms_text(advisory)
    return add_frontend_compatibility(advisory)


def get_latest_advisory() -> dict:
    """Thread-safe read of the latest advisory or standby default."""
    with _advisory_lock:
        if LATEST_ADVISORY is None:
            return _build_default_advisory()
        return copy.deepcopy(LATEST_ADVISORY)


def append_advisory_history(advisory: dict) -> None:
    """Prepend an advisory to the bounded in-memory dashboard history."""
    with _advisory_lock:
        ADVISORY_HISTORY.appendleft(copy.deepcopy(advisory))


def get_advisory_history() -> List[dict]:
    """Return a copy of the in-memory advisory history."""
    with _advisory_lock:
        return [copy.deepcopy(item) for item in ADVISORY_HISTORY]


def save_advisory_to_db(advisory: dict) -> None:
    """Persist a generated advisory to MySQL for post-storm audit."""
    try:
        from app.database.db import get_db

        content = advisory.get("content", {})
        metadata = content.get("_groq_metadata", {})
        snapshot = advisory.get("context_snapshot", {})
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO advisory_history (
                        advisory_id, generated_at_utc, advisory_source, trigger_type,
                        advisory_urgency, storm_class, kp_at_generation,
                        satellites_critical, satellites_high, corridors_critical,
                        corridors_high, advisory_title, priority_action,
                        groq_tokens_used, groq_elapsed_ms, rule_based,
                        full_advisory_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE full_advisory_json = VALUES(full_advisory_json)""",
                    (
                        advisory.get("advisory_id"),
                        advisory.get("generated_at_utc"),
                        advisory.get("advisory_source"),
                        advisory.get("trigger_type"),
                        advisory.get("advisory_urgency"),
                        snapshot.get("storm_class"),
                        snapshot.get("kp_at_generation"),
                        snapshot.get("satellites_critical"),
                        snapshot.get("satellites_high"),
                        snapshot.get("corridors_critical"),
                        snapshot.get("corridors_high"),
                        content.get("advisory_title"),
                        content.get("priority_action"),
                        metadata.get("tokens_used"),
                        metadata.get("elapsed_ms"),
                        1 if content.get("_rule_based") else 0,
                        json.dumps(advisory, ensure_ascii=False, default=str),
                    ),
                )
    except Exception as exc:
        logger.error("Failed to save advisory to DB: %s", exc)


def save_trigger_log_to_db(
    trigger_type: str,
    trigger_condition: str,
    previous_value: Optional[str],
    new_value: Optional[str],
    advisory_generated: bool,
    suppression_reason: Optional[str] = None,
) -> None:
    """Persist one trigger-check audit row without interrupting operations."""
    try:
        from app.database.db import get_db

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO advisory_trigger_log (
                        logged_at_utc, trigger_type, trigger_condition,
                        previous_value, new_value, advisory_generated,
                        suppression_reason
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        utcnow_iso(),
                        trigger_type,
                        trigger_condition,
                        previous_value,
                        new_value,
                        1 if advisory_generated else 0,
                        suppression_reason,
                    ),
                )
    except Exception as exc:
        logger.debug("Trigger log DB save skipped: %s", exc)


def clean_internal_metadata(value: Any) -> Any:
    """Recursively remove internal underscore-prefixed metadata for JSON export."""
    if isinstance(value, dict):
        return {
            key: clean_internal_metadata(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [clean_internal_metadata(item) for item in value]
    return value


def generate_advisory_pdf(advisory: dict) -> bytes:
    """Generate an NDMA-style PDF advisory report and return raw bytes."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("reportlab is required for PDF export") from exc

    import io

    content = advisory.get("content", {})
    snapshot = advisory.get("context_snapshot", {})
    distribution = advisory.get("distribution", {})
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("NAKSHATRA-KAVACH SPACE WEATHER ADVISORY", styles["Title"]),
        Spacer(1, 6 * mm),
        Paragraph(f"Advisory ID: {advisory.get('advisory_id')}", styles["Normal"]),
        Paragraph(f"Generated: {advisory.get('generated_at_ist')}", styles["Normal"]),
        Paragraph(f"Source: {advisory.get('advisory_source', 'UNKNOWN')} | Urgency: {advisory.get('advisory_urgency', 'ROUTINE')}", styles["Normal"]),
        Spacer(1, 5 * mm),
        Paragraph(content.get("advisory_title", "Mission Advisory"), styles["Heading2"]),
        Paragraph(content.get("threat_assessment", ""), styles["BodyText"]),
        Spacer(1, 4 * mm),
        Paragraph("Executive Situation Snapshot", styles["Heading3"]),
        _pdf_table([
            ["Metric", "Value"],
            ["Storm class", snapshot.get("storm_class", "UNKNOWN")],
            ["Kp at generation", snapshot.get("kp_at_generation", "N/A")],
            ["Critical satellites", snapshot.get("satellites_critical", 0)],
            ["High-risk satellites", snapshot.get("satellites_high", 0)],
            ["Critical grid corridors", snapshot.get("grid_critical", 0)],
            ["High-risk grid corridors", snapshot.get("grid_high", 0)],
            ["Data quality", snapshot.get("data_quality", "UNKNOWN")],
        ], colors.darkblue),
        Spacer(1, 4 * mm),
        Paragraph("Operator Intent", styles["Heading3"]),
        Paragraph(content.get("priority_action", "Continue monitoring and follow standard escalation thresholds."), styles["BodyText"]),
        Spacer(1, 4 * mm),
    ]

    sat_rows = [["Satellite", "Risk", "Action", "Deadline"]]
    for item in content.get("satellite_operations", []):
        sat_rows.append([item.get("satellite"), item.get("risk_level"), item.get("action"), item.get("deadline")])
    if len(sat_rows) > 1:
        story.extend([Paragraph("Satellite Operations", styles["Heading3"]), _pdf_table(sat_rows, colors.orange), Spacer(1, 4 * mm)])
    else:
        story.extend([Paragraph("Satellite Operations", styles["Heading3"]), Paragraph("No HIGH or CRITICAL satellite actions were generated for this cycle.", styles["BodyText"]), Spacer(1, 4 * mm)])

    grid_rows = [["Corridor", "kV", "GIC A", "Action"]]
    for item in content.get("grid_operations", []):
        grid_rows.append([item.get("corridor"), item.get("voltage_kv"), item.get("gic_amps"), item.get("action")])
    if len(grid_rows) > 1:
        story.extend([Paragraph("Grid Operations", styles["Heading3"]), _pdf_table(grid_rows, colors.red), Spacer(1, 4 * mm)])
    else:
        story.extend([Paragraph("Grid Operations", styles["Heading3"]), Paragraph("No HIGH or CRITICAL grid corridor actions were generated for this cycle.", styles["BodyText"]), Spacer(1, 4 * mm)])

    timeline_rows = [["Time", "Event"]]
    for item in content.get("timeline", []):
        if isinstance(item, dict):
            timeline_rows.append([item.get("time"), item.get("event")])
    story.extend([
        Paragraph("Timeline", styles["Heading3"]),
        _pdf_table(timeline_rows, colors.lightblue),
        Spacer(1, 4 * mm),
        Paragraph("Hindi Summary", styles["Heading3"]),
        Paragraph(content.get("hindi_summary", ""), styles["BodyText"]),
        Spacer(1, 3 * mm),
        Paragraph("Distribution", styles["Heading3"]),
        Paragraph(f"SMS summary: {distribution.get('sms_text') or generate_sms_text(advisory)}", styles["BodyText"]),
        Spacer(1, 3 * mm),
        Paragraph("Model and Data Provenance", styles["Heading3"]),
        Paragraph(
            "Forecasts are generated from the backend Kp model ensemble. SHAP values, when available, describe the XGBoost branch feature contribution for operator explainability. Solar-wind freshness and quality flags should be checked before operational use.",
            styles["BodyText"],
        ),
    ])
    doc.build(story)
    return buffer.getvalue()


def _pdf_table(rows: List[List[Any]], header_color: Any) -> Any:
    """Build a ReportLab table with consistent NDMA-style styling."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    return table


def get_advisory_status(generator: Optional[AdvisoryGenerator] = None) -> dict:
    """Return health and usage status for the Layer 6 subsystem."""
    gen = generator or ADVISORY_GENERATOR
    latest = get_latest_advisory()
    now = datetime.now(timezone.utc)
    count_24hr = 0
    for item in get_advisory_history():
        generated = _utc_from_iso(item.get("generated_at_utc"))
        if generated and (now - generated).total_seconds() <= 86400:
            count_24hr += 1
    next_eligible = None
    if last_advisory_timestamp_utc is not None:
        last = last_advisory_timestamp_utc
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        next_eligible = (last + timedelta(minutes=ADVISORY_MIN_INTERVAL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "groq_api_available": gen.groq_api_available,
        "last_advisory_utc": latest.get("generated_at_utc"),
        "last_advisory_ist": latest.get("generated_at_ist"),
        "last_advisory_type": latest.get("trigger_type"),
        "last_advisory_source": latest.get("advisory_source"),
        "advisory_count_24hr": count_24hr,
        "groq_tokens_used_today": gen.groq_client.total_tokens_used if gen.groq_client else 0,
        "groq_rate_limit_per_min": gen.groq_client.rate_limit_tokens_per_minute if gen.groq_client else 14400,
        "fallback_mode_active": gen.fallback_mode,
        "next_eligible_generation": next_eligible,
    }


def get_trigger_state_snapshot(kp_forecast: dict, satellite_risks: dict, grid_risks: dict, solar_data: dict) -> dict:
    """Return current trigger state without mutating dedupe state."""
    current_class = _nested(kp_forecast, ["current", "storm_class"], "QUIET")
    critical_now = sum(1 for sat in satellite_risks.get("tier1", {}).values() if sat.get("risk_level") == "CRITICAL")
    critical_prev = sum(1 for level in previous_satellite_risk_levels.values() if level == "CRITICAL")
    sat_crossed = critical_now > critical_prev
    grid_critical_now = sum(1 for item in grid_risks.get("corridors", []) if item.get("risk_level") == "CRITICAL")
    grid_critical_prev = sum(1 for level in previous_corridor_risk_levels.values() if level == "CRITICAL")
    minutes_ago = _minutes_since_last_advisory()
    active: List[str] = []
    if _storm_class_index(current_class) > _storm_class_index(previous_storm_class):
        active.append("STORM_ESCALATION")
    if sat_crossed:
        active.append("SATELLITE_CRITICAL")
    if grid_critical_now > grid_critical_prev:
        active.append("GRID_CRITICAL")
    if kp_forecast.get("summary", {}).get("storm_onset_detected") and not previous_storm_onset_detected:
        active.append("STORM_ONSET")
    cme_arrival = solar_data.get("cme", {}).get("arrival_minutes_from_now")
    if solar_data.get("cme", {}).get("earth_directed") and cme_arrival is not None and _safe_float(cme_arrival, 9999.0) < 60.0:
        active.append("CME_IMMINENT")
    return {
        "previous_storm_class": previous_storm_class,
        "current_storm_class": current_class,
        "storm_class_changed": current_class != previous_storm_class,
        "critical_satellites_now": critical_now,
        "critical_satellites_previous": critical_prev,
        "satellite_threshold_crossed": sat_crossed,
        "critical_corridors_now": grid_critical_now,
        "critical_corridors_previous": grid_critical_prev,
        "grid_threshold_crossed": grid_critical_now > grid_critical_prev,
        "last_advisory_minutes_ago": round(minutes_ago, 1),
        "next_trigger_eligible_in_min": max(0.0, round(ADVISORY_MIN_INTERVAL_MINUTES - minutes_ago, 1)),
        "active_triggers": active,
    }


ADVISORY_GENERATOR = AdvisoryGenerator()
