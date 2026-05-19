# backend/tests/test_layer5.py
"""NAKSHATRA-KAVACH Layer 5: India Power Grid GIC Risk Engine tests."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.grid_risk_engine import (
    GICCalculator,
    HistoricalContextEngine,
    check_and_emit_grid_alerts,
    compute_national_summary,
    get_latest_grid_risks,
    grid_db,
    run_grid_risk_scoring,
    update_latest_grid_risks,
)
from app.utils.constants import (
    GIC_MODEL_ACCURACY_NOTE,
    GIC_OPERATIONAL_CALIBRATION_FACTOR,
    SATURATION_THRESHOLDS,
)


@pytest.fixture(scope="module", autouse=True)
def load_grid_db() -> None:
    """Load the grid database once for Layer 5 tests."""
    grid_db.load()


@pytest.fixture()
def calculator() -> GICCalculator:
    """Return a fresh GIC calculator."""
    return GICCalculator()


def _corridor(corridor_id: str) -> dict:
    item = grid_db.get_by_id(corridor_id)
    assert item is not None
    return item


def _mock_kp(kp: float) -> dict:
    return {
        "computed_at_utc": "2026-05-18T00:00:00Z",
        "data_quality_used": "GOOD",
        "current": {"kp": kp},
        "forecast": {
            "3hr": {"kp": kp},
            "6hr": {"kp": kp},
            "12hr": {"kp": kp},
            "24hr": {"kp": kp},
        },
        "summary": {
            "peak_storm_class": "G5" if kp >= 9 else "G4" if kp >= 8 else "QUIET",
            "transit_warning_minutes": 360,
            "storm_imminent": kp >= 5,
        },
    }


def _mock_solar() -> dict:
    return {
        "data_quality": "GOOD",
        "solar_wind": {"bz_gsm": -12.0, "sw_speed_kmps": 700.0},
        "computed": {"dynamic_pressure_npa": 4.2},
    }


def test_geoelectric_field_scales_with_kp(calculator: GICCalculator) -> None:
    corridor = {
        "midpoint": {"lat": 60.0},
        "ground_type": "AVERAGE",
        "angle_from_north_deg": 0.0,
        "eej_applicable": False,
    }
    e5 = calculator.compute_geoelectric_field(corridor, 5.0)
    e9 = calculator.compute_geoelectric_field(corridor, 9.0)
    assert e5["E_geo_mV_per_km"] == pytest.approx(10.0)
    assert e5["f_kp"] == pytest.approx(1.0)
    assert e9["E_geo_mV_per_km"] > 30.0
    assert e9["f_kp"] > e5["f_kp"] * 3.0


def test_geoelectric_field_latitude_scaling(calculator: GICCalculator) -> None:
    assert calculator.latitude_scaling(60.0, 5.0) == pytest.approx(1.0)
    assert calculator.latitude_scaling(27.0, 5.0) == pytest.approx(0.6975, abs=0.02)
    assert calculator.latitude_scaling(13.0, 5.0) == pytest.approx(0.386, abs=0.02)
    assert calculator.latitude_scaling(27.0, 5.0) > calculator.latitude_scaling(13.0, 5.0)


def test_orientation_factor_ns_vs_ew(calculator: GICCalculator) -> None:
    assert calculator.orientation_factor(0.0) == pytest.approx(1.0)
    assert calculator.orientation_factor(90.0) == pytest.approx(0.0, abs=1e-12)
    assert 0.9 < calculator.orientation_factor(22.0) < 1.0


def test_bina_gwalior_max_coupling(calculator: GICCalculator) -> None:
    corridors = grid_db.get_all()
    bina = _corridor("WR_NR_765_02")
    assert calculator.orientation_factor(bina["angle_from_north_deg"]) > 0.99
    highest = max(corridors, key=lambda c: calculator.orientation_factor(c["angle_from_north_deg"]))
    assert highest["id"] == "WR_NR_765_02"


def test_agra_lucknow_low_coupling(calculator: GICCalculator) -> None:
    agra = _corridor("NR_765_02")
    assert calculator.orientation_factor(agra["angle_from_north_deg"]) < 0.1
    risk = calculator.calculate_corridor_risk(agra, _mock_kp(9.0), _mock_solar())
    assert risk["gic_amps"] < 20.0
    assert risk["risk_level"] == "MINIMAL"


def test_raw_gic_amps_formula(calculator: GICCalculator) -> None:
    raw = calculator.compute_raw_gic_amps(20.0, 400.0, 15.0)
    assert raw == pytest.approx(0.533, abs=0.001)


def test_raw_formula_and_calibrated_scoring_are_separate(calculator: GICCalculator) -> None:
    bina = _corridor("WR_NR_765_02")
    raw = calculator.compute_gic_components(bina, 8.0, apply_calibration=False)
    calibrated = calculator.compute_gic_components(bina, 8.0, apply_calibration=True)
    assert calibrated["gic_amps"] == pytest.approx(
        raw["gic_amps"] * GIC_OPERATIONAL_CALIBRATION_FACTOR,
        rel=1e-6,
    )


def test_gic_realistic_storm_values(calculator: GICCalculator) -> None:
    bina = _corridor("WR_NR_765_02")
    gic = calculator.compute_gic_for_kp(bina, 8.0, _mock_solar(), apply_calibration=True)
    assert 30.0 < gic < 150.0


def test_saturation_risk_and_levels(calculator: GICCalculator) -> None:
    safe = calculator.compute_saturation_risk(5.0, "765kV_auto")
    assert safe["saturation_level"] == "SAFE"
    assert safe["saturation_risk"] == pytest.approx(5.0)
    critical = calculator.compute_saturation_risk(
        SATURATION_THRESHOLDS["765kV_auto"]["critical"],
        "765kV_auto",
    )
    assert critical["saturation_risk"] == pytest.approx(100.0)
    assert critical["saturation_level"] == "CRITICAL"


def test_ground_type_resistive_increases_field(calculator: GICCalculator) -> None:
    corridor = _corridor("WR_NR_765_02")
    average = calculator.compute_geoelectric_field({**corridor, "ground_type": "AVERAGE"}, 8.0)
    resistive = calculator.compute_geoelectric_field({**corridor, "ground_type": "RESISTIVE"}, 8.0)
    assert resistive["E_geo_mV_per_km"] > average["E_geo_mV_per_km"]
    assert calculator.compute_gic_for_kp({**corridor, "ground_type": "RESISTIVE"}, 8.0) > calculator.compute_gic_for_kp(corridor, 8.0)


def test_eej_increases_southern_risk(calculator: GICCalculator) -> None:
    kolar = _corridor("SR_400_01")
    with_eej = calculator.compute_gic_for_kp(kolar, 8.0)
    without_eej = calculator.compute_gic_for_kp({**kolar, "eej_applicable": False}, 8.0)
    assert with_eej > without_eej


def test_load_reduction_levels(calculator: GICCalculator) -> None:
    assert calculator.compute_load_reduction(5.0, "765kV_auto", 68)["reduction_percent"] == 0
    severe = calculator.compute_load_reduction(80.0, "765kV_auto", 68)
    assert severe["reduction_percent"] >= 35
    assert severe["urgency"] == "URGENT"
    critical = calculator.compute_load_reduction(100.0, "765kV_auto", 68)
    assert critical["reduction_percent"] == 50
    assert critical["urgency"] == "CRITICAL_IMMEDIATE"


def test_thermal_damage_time_unfloored(calculator: GICCalculator) -> None:
    critical = SATURATION_THRESHOLDS["765kV_auto"]["critical"]
    assert calculator.compute_thermal_timeline(critical, "765kV_auto") == pytest.approx(1.5)
    assert calculator.compute_thermal_timeline(critical * 2, "765kV_auto") == pytest.approx(0.75)
    assert calculator.compute_thermal_timeline(20.0, "765kV_auto") is None


def test_economic_impact_expected_value(calculator: GICCalculator) -> None:
    corridor = _corridor("WR_NR_765_02")
    no_spare = calculator.compute_economic_impact(corridor, 80.0, "SEVERE")
    with_spare = calculator.compute_economic_impact(
        {**corridor, "spare_transformer_available": True},
        80.0,
        "SEVERE",
    )
    assert no_spare["economic_multiplier"] == 2.5
    assert no_spare["expected_replacement_cost_crore"] == pytest.approx(144.0)
    assert no_spare["total_economic_impact_crore"] > no_spare["expected_replacement_cost_crore"]
    assert with_spare["outage_months_if_damaged"] == 0.5
    assert with_spare["total_economic_impact_crore"] < no_spare["total_economic_impact_crore"]


def test_national_summary_cascade_and_dedup() -> None:
    risks = []
    for i in range(3):
        risks.append({
            "risk_level": "CRITICAL",
            "population_served_million": 10.0,
            "economic_impact": {"total_economic_impact_crore": 100.0},
            "gic_amps": 100.0 + i,
            "corridor_name": f"C{i}",
        })
    summary = compute_national_summary(risks, _mock_kp(9.0))
    assert summary["cascade_failure_risk"] == "HIGH"
    assert summary["nldc_alert_required"] is True
    assert summary["population_at_risk_million"] == pytest.approx(21.0)
    quiet = compute_national_summary(
        [{
            "risk_level": "MINIMAL",
            "population_served_million": 10.0,
            "economic_impact": {"total_economic_impact_crore": 0.0},
            "gic_amps": 0.0,
            "corridor_name": "quiet",
        }],
        _mock_kp(0.0),
    )
    assert quiet["cascade_failure_risk"] == "LOW"
    assert quiet["nldc_alert_required"] is False


def test_grid_database_loads() -> None:
    corridors = grid_db.get_all()
    assert len(corridors) == 10
    required = {
        "id", "name", "short_name", "voltage_kv", "transformer_type",
        "length_km", "start_point", "end_point", "midpoint",
        "angle_from_north_deg", "ground_type", "resistance_per_km_ohm",
        "grounding_resistance_ohm", "population_served_million",
        "polyline_coords",
    }
    for corridor in corridors:
        assert required.issubset(corridor.keys())


def test_all_corridors_risk_scores_bounded() -> None:
    result = run_grid_risk_scoring(_mock_kp(9.0), _mock_solar())
    assert len(result["corridors"]) == 10
    for corridor in result["corridors"]:
        assert 0.0 <= corridor["saturation_risk"] <= 100.0
        assert corridor["gic_amps"] >= 0.0
        assert corridor["model_accuracy_note"] == GIC_MODEL_ACCURACY_NOTE


def test_all_corridors_risk_scores_nonnegative_at_quiet() -> None:
    result = run_grid_risk_scoring(_mock_kp(0.0), _mock_solar())
    for corridor in result["corridors"]:
        assert corridor["saturation_risk"] == pytest.approx(0.0)
        assert corridor["gic_amps"] == pytest.approx(0.0)
        assert corridor["risk_level"] == "MINIMAL"


def test_historical_context_returns_relevant() -> None:
    context = HistoricalContextEngine.get_historical_context(9.0, "G5")
    assert context is not None
    assert "Quebec" in context["comparison_text"]
    assert "6 million" in context["comparison_text"]
    assert HistoricalContextEngine.get_historical_context(2.0, "QUIET") is None


def test_map_data_structure() -> None:
    result = run_grid_risk_scoring(_mock_kp(8.0), _mock_solar())
    map_data = result["map_data"][0]
    for key in ("corridor_id", "polyline_coords", "popup", "markers", "gic_forecast"):
        assert key in map_data
    assert isinstance(map_data["polyline_coords"][0], list)
    assert "gic_amps" in map_data["popup"]
    assert map_data["popup"]["model_accuracy_note"] == GIC_MODEL_ACCURACY_NOTE
    assert len(map_data["gic_forecast"]["values"]) == 5


def test_thread_safety_grid_risks() -> None:
    errors: list[str] = []
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            try:
                update_latest_grid_risks({"i": i, "corridors": [], "national_summary": {}})
                i += 1
            except Exception as exc:
                errors.append(str(exc))

    def reader() -> None:
        while not stop.is_set():
            try:
                assert isinstance(get_latest_grid_risks(), dict)
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


def test_risk_level_change_detection() -> None:
    previous = {"corridors": [{"corridor_id": "NR_400_01", "risk_level": "LOW"}]}
    new = {
        "computed_at_utc": "2026-05-18T00:00:00Z",
        "kp_peak_used": 9.0,
        "corridors": [{
            "corridor_id": "NR_400_01",
            "corridor_name": "Bikaner-Moga 400kV",
            "short_name": "Bikaner-Moga",
            "risk_level": "CRITICAL",
            "gic_amps": 125.0,
            "thermal_damage_time_minutes": 1.2,
            "load_reduction": {
                "reduction_percent": 50,
                "action": "EMERGENCY: Reduce loading",
                "urgency": "CRITICAL_IMMEDIATE",
            },
            "economic_impact": {"total_economic_impact_crore": 500.0},
        }],
    }
    with patch("app.socketio") as mock_socketio:
        check_and_emit_grid_alerts(new, previous)
        emitted_events = [call.args[0] for call in mock_socketio.emit.call_args_list]
    assert "grid_risk_change" in emitted_events
    assert "nldc_alert" in emitted_events


def test_mysql_schema_for_grid_tables() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "app" / "database" / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS grid_risk_history" in schema
    assert "CREATE TABLE IF NOT EXISTS grid_events" in schema
    assert "INT AUTO_INCREMENT PRIMARY KEY" in schema
    assert "VARCHAR(64) NOT NULL" in schema
    assert "DOUBLE" in schema
    assert "sqlite" not in schema.lower()


def test_topology_json_is_valid() -> None:
    topology_path = Path(__file__).resolve().parents[1] / "app" / "data" / "india_grid_topology.json"
    data = json.loads(topology_path.read_text(encoding="utf-8"))
    assert len(data) == 10
    assert {c["id"] for c in data} >= {"WR_NR_765_01", "WR_NR_765_02", "SR_400_01"}
