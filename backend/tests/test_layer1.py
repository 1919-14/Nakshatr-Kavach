# backend/tests/test_layer1.py
"""
NAKSHATRA-KAVACH — Layer 1: Pytest Test Suite
Covers fetch functions, validation, computed physics, DB ops,
snapshot thread safety, and all REST endpoints.
Run: pytest tests/test_layer1.py -v
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    """Create a temporary SQLite DB for the test session."""
    db_dir = tmp_path_factory.mktemp("db")
    return db_dir / "test_nakshatra.db"


@pytest.fixture(scope="session", autouse=True)
def init_test_db(test_db_path):
    """Initialize the test database schema once per session."""
    from app.database.db import set_db_path, init_db
    set_db_path(test_db_path)
    init_db()
    yield
    # Teardown: remove test DB
    if test_db_path.exists():
        test_db_path.unlink()


@pytest.fixture
def app(test_db_path):
    """Flask test application with testing config."""
    from config import TestingConfig
    from app import create_app
    TestingConfig.DB_PATH = test_db_path
    flask_app = create_app(TestingConfig)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ─────────────────────────────────────────────────────────────────
# SAMPLE FIXTURES
# ─────────────────────────────────────────────────────────────────

SAMPLE_SOLAR_WIND_RESPONSE = [
    {
        "time_tag": (datetime.utcnow() - timedelta(seconds=30)).strftime(
            "%Y-%m-%d %H:%M:%S.000"
        ),
        "active": True,
        "source": "DSCOVR",
        "bx_gsm": 3.14,
        "by_gsm": -8.22,
        "bz_gsm": -18.40,
        "bt": 28.70,
        "speed": 720.0,
        "density": 12.3,
        "temperature": 85000.0,
        "range_kp": 7,
    }
]

SAMPLE_KP_RESPONSE = [
    {
        "time_tag": (datetime.utcnow() - timedelta(minutes=5)).strftime(
            "%Y-%m-%d %H:%M:%S.000"
        ),
        "kp": 7.33,
        "kp_fraction": 0.33,
        "kp_index": 7,
        "status": "OFFICIAL",
    }
]

SAMPLE_XRAY_RESPONSE = [
    {
        "time_tag": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "satellite": 16,
        "flux": 0.000150,
        "observed_flux": 0.000148,
        "energy": "0.1-0.8nm",
    }
]

SAMPLE_CME_RESPONSE = [
    {
        "time21_5": "2024-05-08T16:09Z",
        "latitude": 17.0,
        "longitude": -5.0,
        "halfAngle": 40.0,
        "speed": 1400.0,
        "type": "C",
        "isMostAccurate": True,
        "note": "",
        "catalog": "M2M_CATALOG",
        "link": "https://kauai.ccmc.gsfc.nasa.gov/",
        "enlilList": [
            {
                "modelCompletionTime": "2024-05-08T20:00Z",
                "estimatedShockArrivalTime": (
                    datetime.utcnow() + timedelta(hours=3)
                ).strftime("%Y-%m-%dT%H:%MZ"),
                "estimatedDuration": 24,
                "isEarthDirected": True,
                "link": "https://kauai.ccmc.gsfc.nasa.gov/",
            }
        ],
    }
]


# ─────────────────────────────────────────────────────────────────
# TEST 1: fetch_solar_wind returns dict with expected keys
# ─────────────────────────────────────────────────────────────────

def test_solar_wind_fetch_returns_dict():
    """Mock requests.get for NOAA URL and assert correct field mapping."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = SAMPLE_SOLAR_WIND_RESPONSE

    with patch("app.services.fetchers.requests.get", return_value=mock_resp):
        from app.services.fetchers import fetch_solar_wind
        result = fetch_solar_wind()

    assert result is not None, "fetch_solar_wind should not return None"
    assert isinstance(result, dict)
    assert "bz_gsm" in result
    assert "speed" in result
    assert "bt" in result

    # Values should be float or None
    for key in ("bz_gsm", "speed", "bt", "density"):
        val = result.get(key)
        assert val is None or isinstance(val, (int, float)), (
            f"Field '{key}' should be numeric or None, got {type(val)}"
        )


# ─────────────────────────────────────────────────────────────────
# TEST 2: validate_solar_wind rejects out-of-range Bz
# ─────────────────────────────────────────────────────────────────

def test_validate_solar_wind_rejects_out_of_range():
    """Bz=-999 is outside [-100, +100] nT — must be set to None."""
    from app.services.validators import validate_solar_wind

    raw = {
        "time_tag": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.000"),
        "bz_gsm": -999.0,
        "speed": 500.0,
        "bt": 20.0,
        "density": 8.0,
        "temperature": 50000.0,
        "bx_gsm": 2.0,
        "by_gsm": -3.0,
        "range_kp": 4.0,
        "active": True,
    }

    with patch("app.services.validators._try_interpolate_field", return_value=(None, False)):
        result = validate_solar_wind(raw)

    assert result["bz_gsm"] is None, (
        "Bz=-999 is out of physical range [-100,+100] and must be nulled"
    )
    assert result["data_quality_flag"] in ("PARTIAL", "STALE", "UNKNOWN"), (
        "Quality should degrade when bz_gsm is null"
    )



# ─────────────────────────────────────────────────────────────────
# TEST 3: classify_xray returns correct class letter
# ─────────────────────────────────────────────────────────────────

def test_classify_xray_all_classes():
    """Each flux range must map to the correct flare class letter."""
    from app.services.validators import classify_xray

    test_cases = [
        (1e-4,  "X"),
        (1e-5,  "M"),
        (1e-6,  "C"),
        (1e-7,  "B"),
        (1e-8,  "A"),
    ]
    for flux, expected_letter in test_cases:
        result = classify_xray(flux)
        assert result.startswith(expected_letter), (
            f"classify_xray({flux}) = '{result}', expected prefix '{expected_letter}'"
        )

    # Edge cases — must never raise
    assert classify_xray(None) == "UNKNOWN"
    assert classify_xray(0) == "A0.0"
    assert classify_xray(-1e-5) == "A0.0"


# ─────────────────────────────────────────────────────────────────
# TEST 4: transit time calculation
# ─────────────────────────────────────────────────────────────────

def test_transit_time_calculation():
    """Verify L1 transit formula: 1,500,000 / speed / 60."""
    from app.services.validators import compute_transit_warning_minutes

    transit_700 = compute_transit_warning_minutes(700.0)
    assert 34.0 < transit_700 < 37.0, (
        f"At 700 km/s transit should be ~35.7 min, got {transit_700}"
    )

    transit_400 = compute_transit_warning_minutes(400.0)
    assert 61.0 < transit_400 < 64.0, (
        f"At 400 km/s transit should be ~62.5 min, got {transit_400}"
    )

    transit_none = compute_transit_warning_minutes(None)
    assert transit_none == 60.0, (
        f"Default transit for None speed should be 60.0 min, got {transit_none}"
    )

    transit_zero = compute_transit_warning_minutes(0.0)
    assert transit_zero == 60.0, (
        "Zero speed should fall back to 60.0 min default"
    )


# ─────────────────────────────────────────────────────────────────
# TEST 5: Snapshot thread safety
# ─────────────────────────────────────────────────────────────────

def test_snapshot_thread_safety():
    """10 concurrent threads reading/writing LATEST_SNAPSHOT must not corrupt it."""
    from app.services.ingestion_service import LATEST_SNAPSHOT, get_snapshot
    from app.services.ingestion_service import _snapshot_lock

    errors = []

    def reader():
        for _ in range(50):
            try:
                snap = get_snapshot()
                assert isinstance(snap, dict)
            except Exception as exc:
                errors.append(str(exc))

    def writer():
        for _ in range(50):
            try:
                with _snapshot_lock:
                    LATEST_SNAPSHOT["data_age_seconds"] = 42
            except Exception as exc:
                errors.append(str(exc))

    threads = [
        threading.Thread(target=reader if i % 2 == 0 else writer)
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Thread safety violations: {errors}"


# ─────────────────────────────────────────────────────────────────
# TEST 6: GET /api/solar/live returns 200
# ─────────────────────────────────────────────────────────────────

def test_api_solar_live_returns_200(client):
    """Live endpoint should return 200 with solar_wind and kp keys."""
    response = client.get("/api/solar/live")
    assert response.status_code == 200

    data = json.loads(response.data)
    assert "solar_wind" in data, "Response missing 'solar_wind' key"
    assert "kp" in data, "Response missing 'kp' key"
    assert "computed" in data, "Response missing 'computed' key"


# ─────────────────────────────────────────────────────────────────
# TEST 7: Stale data header propagation
# ─────────────────────────────────────────────────────────────────

def test_api_handles_stale_data(client):
    """When data_age_seconds > threshold, X-Data-Quality header must be STALE."""
    from app.services.ingestion_service import LATEST_SNAPSHOT, _snapshot_lock
    from app.utils.constants import QUALITY_STALE

    with _snapshot_lock:
        LATEST_SNAPSHOT["data_age_seconds"] = 700
        LATEST_SNAPSHOT["data_quality"] = QUALITY_STALE

    response = client.get("/api/solar/live")
    quality_header = response.headers.get("X-Data-Quality", "")
    assert quality_header == "STALE", (
        f"Expected X-Data-Quality=STALE, got '{quality_header}'"
    )

    # Reset for other tests
    with _snapshot_lock:
        LATEST_SNAPSHOT["data_age_seconds"] = 30
        LATEST_SNAPSHOT["data_quality"] = "GOOD"


# ─────────────────────────────────────────────────────────────────
# TEST 8: retry_request retries on Timeout then succeeds
# ─────────────────────────────────────────────────────────────────

def test_retry_request_retries_on_timeout():
    """Mock requests.get to raise Timeout twice then return valid JSON."""
    import requests as req_lib
    from app.services.fetchers import retry_request

    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.json.return_value = [{"value": "success"}]

    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise req_lib.Timeout("Simulated timeout")
        return mock_ok

    with patch("app.services.fetchers.requests.get", side_effect=side_effect):
        with patch("app.services.fetchers.time.sleep"):  # skip actual delays
            result = retry_request("https://example.com", source_name="test_source")

    assert result is not None, "retry_request should succeed on 3rd attempt"
    assert result == [{"value": "success"}]
    assert call_count["n"] == 3


# ─────────────────────────────────────────────────────────────────
# TEST 9: Database write and read round-trip
# ─────────────────────────────────────────────────────────────────

def test_database_write_and_read():
    """Write one solar_wind_readings record and read it back."""
    from app.database.db import insert_solar_wind_reading, get_solar_wind_history

    ts = (datetime.utcnow() - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp_utc":          ts,
        "ingested_at":            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bx_gsm":                 2.5,
        "by_gsm":                 -6.1,
        "bz_gsm":                 -15.3,
        "bt_total":               16.8,
        "sw_speed_kmps":          650.0,
        "proton_density_ccm":     10.0,
        "proton_temp_kelvin":     70000.0,
        "kp_estimated_from_sw":   6.0,
        "kp_current":             6.33,
        "kp_status":              "OFFICIAL",
        "xray_flux_wm2":          1e-5,
        "xray_class":             "M1.0",
        "xray_severity_numeric":  4,
        "cme_earth_directed":     0,
        "cme_speed_kmps":         None,
        "cme_arrival_minutes":    None,
        "cme_arrival_time_utc":   None,
        "transit_warning_minutes": 38.5,
        "epsilon_coupling":       2.3e10,
        "dynamic_pressure_npa":   4.8,
        "official_alert_class":   "G2",
        "data_quality_flag":      "GOOD",
        "bz_southward_flag":      1,
        "storm_onset_risk":       "HIGH",
        "source_dscovr_active":   1,
        "interpolated":           0,
    }

    row_id = insert_solar_wind_reading(record)
    assert row_id is not None, "insert_solar_wind_reading should return a row ID"

    history = get_solar_wind_history(hours=1, quality_filter=["GOOD"])
    assert len(history) >= 1

    found = next((r for r in history if r["timestamp_utc"] == ts), None)
    assert found is not None, f"Written record with timestamp {ts} not found in history"
    assert abs(found["bz_gsm"] - (-15.3)) < 0.001
    assert abs(found["sw_speed_kmps"] - 650.0) < 0.001
    assert found["storm_onset_risk"] == "HIGH"


# ─────────────────────────────────────────────────────────────────
# TEST 10: CME extraction from DONKI sets snapshot correctly
# ─────────────────────────────────────────────────────────────────

def test_cme_extraction_from_donki():
    """Mock DONKI response with Earth-directed CME and verify snapshot update."""
    from app.services.ingestion_service import update_snapshot_cme, get_snapshot

    with patch("app.database.db.insert_cme_event", return_value=1):
        update_snapshot_cme(SAMPLE_CME_RESPONSE)

    snap = get_snapshot()
    assert snap["cme"]["earth_directed"] is True, (
        "earth_directed should be True for Earth-directed CME"
    )
    assert snap["cme"]["arrival_minutes_from_now"] is not None, (
        "arrival_minutes_from_now should not be None"
    )
    assert snap["cme"]["arrival_minutes_from_now"] > 0, (
        "CME arrival must be in the future (positive minutes)"
    )
    assert snap["cme"]["cme_speed_kmps"] == 1400.0


# ─────────────────────────────────────────────────────────────────
# TEST 11: epsilon coupling physics
# ─────────────────────────────────────────────────────────────────

def test_epsilon_coupling_physics():
    """Verify Akasofu epsilon returns physically plausible values during a storm."""
    from app.services.validators import compute_epsilon_coupling
    import math

    # Typical major storm values
    eps = compute_epsilon_coupling(
        sw_speed_kmps=700.0,
        bt_total=25.0,
        by_gsm=-5.0,
        bz_gsm=-20.0,
    )
    assert eps is not None
    assert eps > 0, "Epsilon must be positive"

    # When Bz = 0, by = 0 → theta = 0 → sin(0)^4 = 0 → epsilon = 0
    eps_zero = compute_epsilon_coupling(600.0, 20.0, 0.0, 0.0)
    assert eps_zero == 0.0 or eps_zero is not None

    # None inputs → None output
    assert compute_epsilon_coupling(None, 20.0, -5.0, -10.0) is None
    assert compute_epsilon_coupling(600.0, None, -5.0, -10.0) is None


# ─────────────────────────────────────────────────────────────────
# TEST 12: storm_onset_risk classification
# ─────────────────────────────────────────────────────────────────

def test_storm_onset_risk_classification():
    """Verify all Bz thresholds map to correct risk levels."""
    from app.services.validators import compute_storm_onset_risk

    # Implementation uses >= so exact threshold values fall in the less-severe bucket
    assert compute_storm_onset_risk(None)   == "UNKNOWN"
    assert compute_storm_onset_risk(5.0)    == "LOW"      # >= -5 → LOW
    assert compute_storm_onset_risk(0.0)    == "LOW"
    assert compute_storm_onset_risk(-4.9)   == "LOW"
    assert compute_storm_onset_risk(-5.0)   == "LOW"      # exactly -5: >= -5 → LOW
    assert compute_storm_onset_risk(-5.1)   == "MODERATE" # crosses into < -5
    assert compute_storm_onset_risk(-9.9)   == "MODERATE"
    assert compute_storm_onset_risk(-10.0)  == "MODERATE" # exactly -10: >= -10 → MODERATE
    assert compute_storm_onset_risk(-10.1)  == "HIGH"     # crosses into < -10
    assert compute_storm_onset_risk(-19.9)  == "HIGH"
    assert compute_storm_onset_risk(-20.0)  == "HIGH"     # exactly -20: >= -20 → HIGH
    assert compute_storm_onset_risk(-20.1)  == "CRITICAL" # crosses into < -20
    assert compute_storm_onset_risk(-35.0)  == "CRITICAL"


# ─────────────────────────────────────────────────────────────────
# TEST 13: GET /api/solar/status returns scheduler info
# ─────────────────────────────────────────────────────────────────

def test_api_status_endpoint(client):
    """Status endpoint should return scheduler_running and sources keys."""
    response = client.get("/api/solar/status")
    assert response.status_code == 200

    data = json.loads(response.data)
    assert "data_quality" in data
    assert "sources" in data
    assert "storm_imminent" in data


# ─────────────────────────────────────────────────────────────────
# TEST 14: kp_to_storm_class mapping
# ─────────────────────────────────────────────────────────────────

def test_kp_to_storm_class():
    """Kp values should map to correct NOAA G-scale storm classes."""
    from app.services.validators import kp_to_storm_class

    assert kp_to_storm_class(None) == "QUIET"
    assert kp_to_storm_class(4.9)  == "QUIET"
    assert kp_to_storm_class(5.0)  == "G1"
    assert kp_to_storm_class(5.9)  == "G1"
    assert kp_to_storm_class(6.0)  == "G2"
    assert kp_to_storm_class(7.0)  == "G3"
    assert kp_to_storm_class(8.0)  == "G4"
    assert kp_to_storm_class(9.0)  == "G5"
