# backend/tests/test_layer6.py
"""NAKSHATRA-KAVACH Layer 6 advisory generator tests."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pytest
from flask import Flask

# Allow direct execution from the repository root:
#   py backend/tests/test_layer6.py
# Pytest imports get this from backend/conftest.py, but plain Python does not.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

import app.routes.advisory as advisory_routes
import app.services.advisory_generator as ag
from app.services.advisory_generator import (
    AdvisoryGenerator,
    GroqAdvisoryClient,
    GroqAdvisoryError,
    PromptQAChecker,
    RuleBasedAdvisoryGenerator,
    SYSTEM_PROMPT,
    append_advisory_history,
    build_advisory_context,
    build_user_message,
    generate_advisory_pdf,
    generate_sms_text,
    generate_whatsapp_summary,
)


def _mock_kp(kp: float = 7.2, current_class: str = "G3", peak_class: str = "G3") -> dict:
    """Return a complete Layer 3-like Kp forecast for Layer 6 tests."""
    return {
        "computed_at_utc": "2026-05-18T00:00:00Z",
        "data_quality_used": "GOOD",
        "prediction_confidence": "HIGH",
        "current": {"kp": kp, "storm_class": current_class},
        "forecast": {
            "3hr": {"kp": 7.8, "uncertainty": 0.6, "p_storm_g3": 0.72},
            "6hr": {"kp": 7.5, "uncertainty": 0.7, "p_storm_g3": 0.68},
            "12hr": {"kp": 6.4, "uncertainty": 0.9, "p_storm_g3": 0.25},
            "24hr": {"kp": 4.2, "uncertainty": 1.1, "p_storm_g3": 0.05},
        },
        "summary": {
            "peak_storm_class": peak_class,
            "storm_probability_12hr": 0.82,
            "transit_warning_minutes": 38.0,
            "storm_imminent": True,
            "storm_onset_detected": False,
            "cme_active": True,
            "dominant_driver": "southward IMF Bz",
        },
        "shap": {"dominant_driver": "southward IMF Bz"},
    }


def _sat(name: str, level: str, composite: float) -> dict:
    """Return one Layer 4-like satellite risk object."""
    return {
        "risk_level": level,
        "risk_scores": {"composite_final": composite},
        "primary_threat": "surface_charging",
        "recommended_action": "Prepare safe mode and increase telemetry cadence.",
        "urgency": "IN_30_MINUTES",
        "criticality": "HIGH",
        "safe_mode_countdown": {"safe_mode_deadline_minutes": 25 if composite >= 80 else None},
    }


def _mock_sats() -> dict:
    """Return a mixed-risk satellite fleet summary."""
    return {
        "critical_count": 1,
        "high_count": 2,
        "tier1": {
            "LOW-1": _sat("LOW-1", "LOW", 22),
            "LOW-2": _sat("LOW-2", "LOW", 25),
            "LOW-3": _sat("LOW-3", "MINIMAL", 5),
            "HIGH-1": _sat("HIGH-1", "HIGH", 64),
            "HIGH-2": _sat("HIGH-2", "HIGH", 71),
            "CRIT-1": _sat("CRIT-1", "CRITICAL", 88),
        },
        "fleet_summary": {
            "navigation_system_status": "DEGRADED",
            "nav_error_meters": 12,
            "defense_satellites_at_risk": 1,
            "total_economic_value_at_risk_crore": 3200,
        },
    }


def _mock_grid() -> dict:
    """Return a Layer 5-like grid risk output."""
    return {
        "corridors": [
            {
                "corridor_id": "C1",
                "short_name": "Vindhyachal-Agra",
                "corridor_name": "Vindhyachal-Agra 765kV",
                "voltage_kv": 765,
                "risk_level": "HIGH",
                "gic_amps": 68.0,
                "saturation_risk": 72.0,
                "load_reduction": {"reduction_percent": 35, "action": "Reduce loading by 35%", "urgency": "URGENT"},
                "states_affected": ["Madhya Pradesh", "Uttar Pradesh"],
                "population_served_million": 8.5,
                "thermal_damage_time_minutes": 25.0,
                "economic_impact": {"spare_transformer_available": False, "total_economic_impact_crore": 250.0},
            },
            {
                "corridor_id": "C2",
                "short_name": "Quiet Corridor",
                "corridor_name": "Quiet Corridor 400kV",
                "voltage_kv": 400,
                "risk_level": "LOW",
                "gic_amps": 8.0,
                "saturation_risk": 12.0,
                "load_reduction": {"reduction_percent": 0, "action": "Monitor", "urgency": "WATCH"},
                "states_affected": ["Rajasthan"],
                "population_served_million": 1.0,
                "thermal_damage_time_minutes": None,
                "economic_impact": {"spare_transformer_available": True, "total_economic_impact_crore": 0.0},
            },
        ],
        "national_summary": {
            "critical_corridors_count": 0,
            "high_corridors_count": 1,
            "population_at_risk_million": 6.0,
            "total_economic_impact_crore": 250.0,
            "cascade_failure_risk": "MODERATE",
            "nldc_alert_required": False,
            "max_gic_amps": 68.0,
            "max_gic_corridor": "Vindhyachal-Agra 765kV",
            "grid_stability_index": 80.0,
        },
    }


def _mock_solar(arrival: float | None = 45.0, earth_directed: bool = True) -> dict:
    """Return a Layer 1-like solar snapshot."""
    return {
        "solar_wind": {"bz_gsm": -18.4, "sw_speed_kmps": 720.0},
        "xray": {"xray_class": "M1.5"},
        "cme": {"earth_directed": earth_directed, "arrival_minutes_from_now": arrival},
        "alert": {"latest_official_class": "G3"},
        "data_quality": "GOOD",
    }


def _mock_context() -> dict:
    """Return a compact advisory context fixture."""
    return build_advisory_context("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())


def _valid_llm_json() -> str:
    """Return valid Groq JSON for parser tests."""
    return json.dumps({
        "advisory_title": "G3 Storm Advisory",
        "threat_assessment": "Kp 7.5 G3 conditions are expected within the warning window.",
        "satellite_operations": [],
        "grid_operations": [],
        "timeline": [
            {"time": "T-30min", "event": "Complete readiness checks"},
            {"time": "T+0", "event": "Storm impact"},
            {"time": "T+2hr", "event": "Status assessment"},
            {"time": "T+6hr", "event": "Recovery review"},
        ],
        "recovery_estimate": "6-12 hours from storm peak",
        "navigation_alert": None,
        "hindi_summary": "तीव्र भू-चुंबकीय तूफान की चेतावनी। निगरानी बढ़ाएं।",
        "priority_action": "Increase telemetry monitoring immediately.",
        "advisory_classification": "MANUAL_REFRESH",
    })


@pytest.fixture(autouse=True)
def reset_layer6_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset mutable Layer 6 globals between tests."""
    ag.LATEST_ADVISORY = None
    ag.ADVISORY_HISTORY.clear()
    ag.last_advisory_timestamp_utc = None
    ag.last_advisory_type = None
    ag.previous_storm_class = "QUIET"
    ag.previous_satellite_risk_levels = {}
    ag.previous_corridor_risk_levels = {}
    ag.previous_cme_arrival_minutes = None
    ag.previous_storm_onset_detected = False
    advisory_routes._last_manual_generation_time = 0.0
    monkeypatch.setattr(advisory_routes, "save_advisory_to_db", lambda advisory: None)
    monkeypatch.setattr(ag, "save_advisory_to_db", lambda advisory: None)


def test_system_prompt_contains_required_sections() -> None:
    """SYSTEM_PROMPT must preserve required schema and JSON-only instruction."""
    assert "satellite_operations" in SYSTEM_PROMPT
    assert "grid_operations" in SYSTEM_PROMPT
    assert "hindi_summary" in SYSTEM_PROMPT
    assert "priority_action" in SYSTEM_PROMPT
    assert "Return ONLY valid JSON" in SYSTEM_PROMPT


def test_context_builder_filters_low_risk_satellites() -> None:
    """Context builder includes only HIGH and CRITICAL Tier 1 satellites."""
    context = _mock_context()
    sats = context["satellites"]["at_risk_satellites"]
    assert len(sats) == 3
    assert {s["risk_level"] for s in sats} == {"HIGH", "CRITICAL"}


def test_context_builder_token_budget() -> None:
    """Serialized context should remain compact for free-tier Groq calls."""
    json_str = json.dumps(_mock_context(), ensure_ascii=False)
    assert len(json_str) < 6000


def test_user_message_contains_kp_values() -> None:
    """User prompt should carry Kp forecasts and storm class."""
    context = _mock_context()
    message = build_user_message(context, "MANUAL_REFRESH", "HIGH")
    assert "7.8" in message
    assert "G3" in message


def test_rule_based_generates_all_storm_classes() -> None:
    """Rule fallback should generate non-empty advisories for all storm templates."""
    generator = RuleBasedAdvisoryGenerator()
    for storm_class in ["G5", "G4", "G3", "G2", "G1", "QUIET"]:
        context = build_advisory_context("MANUAL_REFRESH", _mock_kp(peak_class=storm_class), _mock_sats(), _mock_grid(), _mock_solar())
        advisory = generator.generate(context, "MANUAL_REFRESH", "HIGH")
        assert advisory["advisory_title"]
        assert advisory["advisory_classification"] == "MANUAL_REFRESH"
        assert len(advisory["hindi_summary"]) > 10
    recovery = generator.generate(_mock_context(), "STORM_RECOVERY", "MODERATE")
    assert recovery["advisory_classification"] == "STORM_RECOVERY"


def test_rule_based_advisory_schema_matches_llm_schema() -> None:
    """Rule-based output must match the top-level content schema."""
    advisory = RuleBasedAdvisoryGenerator().generate(_mock_context(), "MANUAL_REFRESH", "HIGH")
    for key in [
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
    ]:
        assert key in advisory
    assert isinstance(advisory["satellite_operations"], list)
    assert isinstance(advisory["grid_operations"], list)
    assert len(advisory["timeline"]) >= 4


def test_rule_based_hindi_is_devanagari() -> None:
    """Hindi template output must contain Devanagari characters."""
    advisory = RuleBasedAdvisoryGenerator().generate(_mock_context(), "MANUAL_REFRESH", "HIGH")
    assert any("\u0900" <= char <= "\u097F" for char in advisory["hindi_summary"])


def test_groq_client_parses_valid_json() -> None:
    """Groq parser should accept valid JSON without constructing a real client."""
    client = GroqAdvisoryClient.__new__(GroqAdvisoryClient)
    result = client._parse_and_validate(_valid_llm_json())
    assert result["priority_action"]


def test_groq_client_strips_markdown_fences() -> None:
    """Parser tolerates accidental markdown fences around JSON."""
    client = GroqAdvisoryClient.__new__(GroqAdvisoryClient)
    result = client._parse_and_validate(f"```json\n{_valid_llm_json()}\n```")
    assert result["advisory_title"] == "G3 Storm Advisory"


def test_groq_client_raises_on_missing_fields() -> None:
    """Parser raises clear ValueError when required fields are missing."""
    client = GroqAdvisoryClient.__new__(GroqAdvisoryClient)
    with pytest.raises(ValueError, match="missing required fields"):
        client._parse_and_validate(json.dumps({"advisory_title": "bad"}))


def test_prompt_qa_detects_bad_satellite_and_kp() -> None:
    """Prompt QA should catch hallucinated satellite names and Kp values."""
    bad = json.loads(_valid_llm_json())
    bad["satellite_operations"] = [{"satellite": "FAKE-SAT", "risk_level": "HIGH"}]
    bad["threat_assessment"] = "Kp 1.0 conditions expected."
    ok, issues = PromptQAChecker().validate(bad, _mock_context())
    assert ok is False
    assert any("Hallucinated satellite" in issue for issue in issues)


def test_trigger_storm_escalation_fires() -> None:
    """T1 fires on storm-class worsening."""
    ag.previous_storm_class = "G2"
    trigger = ag.check_advisory_triggers(_mock_kp(current_class="G3"), _mock_sats(), _mock_grid(), _mock_solar(earth_directed=False))
    assert trigger and trigger["trigger_type"] == "STORM_ESCALATION"


def test_trigger_no_fire_for_improvement() -> None:
    """Storm-class improvement should not fire escalation."""
    ag.previous_storm_class = "G3"
    trigger = ag.check_advisory_triggers(
        _mock_kp(kp=6.5, current_class="G2", peak_class="G2"),
        {"tier1": {}, "fleet_summary": {}},
        {"corridors": [], "national_summary": {}},
        _mock_solar(earth_directed=False),
    )
    assert trigger is None


def test_trigger_deduplication_10_min() -> None:
    """Hard dedupe suppresses non-CME triggers inside 10 minutes."""
    ag.previous_storm_class = "G2"
    ag.last_advisory_timestamp_utc = datetime.now(timezone.utc) - timedelta(minutes=5)
    trigger = ag.check_advisory_triggers(_mock_kp(current_class="G3"), _mock_sats(), _mock_grid(), _mock_solar(earth_directed=False))
    assert trigger is None


def test_trigger_cme_imminent_overrides_minimum() -> None:
    """T5 uses the 5-minute CME minimum instead of the 10-minute hard minimum."""
    ag.previous_cme_arrival_minutes = 90.0
    ag.last_advisory_timestamp_utc = datetime.now(timezone.utc) - timedelta(minutes=6)
    trigger = ag.check_advisory_triggers(_mock_kp(), _mock_sats(), _mock_grid(), _mock_solar(arrival=45.0))
    assert trigger and trigger["trigger_type"] == "CME_IMMINENT"


def test_trigger_satellite_critical_fires() -> None:
    """T2 fires when a Tier 1 satellite reaches CRITICAL."""
    ag.previous_storm_class = "G3"
    ag.previous_satellite_risk_levels = {"CRIT-1": "HIGH"}
    trigger = ag.check_advisory_triggers(_mock_kp(), _mock_sats(), _mock_grid(), _mock_solar(earth_directed=False))
    assert trigger and trigger["trigger_type"] == "SATELLITE_CRITICAL"


def test_trigger_onset_deduplicates() -> None:
    """T4 uses previous_storm_onset_detected for rising-edge detection."""
    kp = _mock_kp(kp=5.4, current_class="G1", peak_class="G1")
    kp["summary"]["storm_onset_detected"] = True
    ag.previous_storm_class = "G1"
    trigger = ag.check_advisory_triggers(kp, {"tier1": {}, "fleet_summary": {}}, {"corridors": [], "national_summary": {}}, _mock_solar(earth_directed=False))
    assert trigger and trigger["trigger_type"] == "STORM_ONSET"
    assert ag.previous_storm_onset_detected is True
    trigger2 = ag.check_advisory_triggers(kp, {"tier1": {}, "fleet_summary": {}}, {"corridors": [], "national_summary": {}}, _mock_solar(earth_directed=False))
    assert trigger2 is None


def test_advisory_id_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advisory IDs must be sortable and include storm class."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    advisory = AdvisoryGenerator().generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
    assert re.match(r"ADV-\d{8}-\d{6}-G3", advisory["advisory_id"])


def test_advisory_output_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full generator output should satisfy Section H top-level contract."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    advisory = AdvisoryGenerator().generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
    for key in [
        "advisory_id",
        "generated_at_utc",
        "generated_at_ist",
        "advisory_source",
        "trigger_type",
        "advisory_urgency",
        "content",
        "context_snapshot",
        "distribution",
        "expires_at_utc",
    ]:
        assert key in advisory
    assert advisory["distribution"]["whatsapp_summary"] is not None
    assert advisory["sections"]


def test_whatsapp_summary_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """WhatsApp and SMS summaries must respect distribution limits."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    advisory = AdvisoryGenerator().generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
    assert len(generate_whatsapp_summary(advisory)) <= 300
    assert "NAKSHATRA-KAVACH" in generate_whatsapp_summary(advisory)
    assert len(generate_sms_text(advisory)) <= 160


def test_pdf_export_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF export returns bytes when ReportLab is installed."""
    pytest.importorskip("reportlab")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    advisory = AdvisoryGenerator().generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
    pdf = generate_advisory_pdf(advisory)
    assert pdf.startswith(b"%PDF")


def test_advisory_history_bounded_at_20(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-memory advisory history is bounded by deque maxlen."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    generator = AdvisoryGenerator()
    for _ in range(25):
        append_advisory_history(generator.generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar()))
    assert len(ag.ADVISORY_HISTORY) == 20


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create a tiny Flask app with only the advisory blueprint."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(advisory_routes, "ADVISORY_GENERATOR", AdvisoryGenerator())
    monkeypatch.setattr(advisory_routes, "_upstream_inputs", lambda: (_mock_kp(), _mock_sats(), _mock_grid(), _mock_solar()))
    app = Flask(__name__)
    app.register_blueprint(advisory_routes.advisory_bp)
    return app.test_client()


def test_rate_limit_endpoint(client: Any) -> None:
    """Manual generation endpoint returns 429 for immediate repeated requests."""
    first = client.post("/api/advisory/generate", json={"trigger_type": "MANUAL_REFRESH"})
    assert first.status_code == 200
    second = client.post("/api/advisory/generate", json={"trigger_type": "MANUAL_REFRESH"})
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_advisory_latest_endpoint(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Latest endpoint returns advisory JSON and advisory headers."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    advisory = AdvisoryGenerator().generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
    ag.update_latest_advisory(advisory)
    response = client.get("/api/advisory/latest")
    assert response.status_code == 200
    data = response.get_json()
    assert "advisory_id" in data
    assert "content" in data
    assert response.headers.get("X-Advisory-Source")


def test_status_endpoint_reports_fallback_without_groq(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Status endpoint returns 200 and degraded state when Groq is unavailable."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(advisory_routes, "ADVISORY_GENERATOR", AdvisoryGenerator())
    response = client.get("/api/advisory/status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["groq_api_available"] is False
    assert data["fallback_mode_active"] is True


def test_fallback_activates_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three Groq failures switch the generator into persistent fallback mode."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    generator = AdvisoryGenerator()

    class AlwaysFail:
        """Groq client double that always fails."""

        def __init__(self) -> None:
            self.calls = 0
            self.total_tokens_used = 0
            self.rate_limit_tokens_per_minute = 14400

        def generate_advisory(self, *args: Any, **kwargs: Any) -> dict:
            """Raise the operational Groq error used by the orchestrator."""
            self.calls += 1
            raise GroqAdvisoryError("forced failure")

    failing = AlwaysFail()
    generator.groq_client = failing
    generator.fallback_mode = False
    for _ in range(3):
        advisory = generator.generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
        assert advisory["advisory_source"] == "RULE_BASED"
    assert generator.fallback_mode is True
    calls_after = failing.calls
    generator.generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
    assert failing.calls == calls_after


def test_thread_safety_latest_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent latest advisory reads and writes should not raise runtime errors."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    generator = AdvisoryGenerator()
    errors: list[str] = []
    stop = threading.Event()

    def writer() -> None:
        """Repeatedly replace latest advisory."""
        while not stop.is_set():
            try:
                advisory = generator.generate("MANUAL_REFRESH", _mock_kp(), _mock_sats(), _mock_grid(), _mock_solar())
                ag.update_latest_advisory(advisory)
            except Exception as exc:
                errors.append(str(exc))

    def reader() -> None:
        """Repeatedly read latest advisory."""
        while not stop.is_set():
            try:
                assert isinstance(ag.get_latest_advisory(), dict)
            except Exception as exc:
                errors.append(str(exc))

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(20)]
    for thread in threads:
        thread.start()
    time.sleep(1.0)
    stop.set()
    for thread in threads:
        thread.join(timeout=3)
    assert errors == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
