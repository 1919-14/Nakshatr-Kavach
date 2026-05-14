# backend/app/utils/constants.py
"""
NAKSHATRA-KAVACH — Layer 1: Constants and Configuration
All physical limits, API endpoints, storm thresholds, and retention policies.
"""

# ─────────────────────────────────────────────────────────────────
# API ENDPOINTS
# ─────────────────────────────────────────────────────────────────

NOAA_SOLAR_WIND_URL = (
    "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json"
)
NOAA_KP_INDEX_URL = (
    "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
)
NOAA_XRAY_FLUX_URL = (
    "https://services.swpc.noaa.gov/json/goes/primary/xray-fluxes-7-day.json"
)
NOAA_ALERTS_URL = (
    "https://services.swpc.noaa.gov/products/alerts.json"
)
NASA_DONKI_CME_BASE_URL = (
    "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/CMEAnalysis"
)
ACE_BACKUP_URL = (
    "https://services.swpc.noaa.gov/json/ace/ace_mag_1m.json"
)

# ─────────────────────────────────────────────────────────────────
# PHYSICAL CONSTANTS — L1 TRANSIT
# ─────────────────────────────────────────────────────────────────

# Distance from DSCOVR (L1 Lagrange point) to Earth's magnetosphere (km)
L1_TO_EARTH_KM: float = 1_500_000.0

# Default solar wind speed used when actual speed is unavailable (km/s)
DEFAULT_SW_SPEED_KMPS: float = 450.0

# ─────────────────────────────────────────────────────────────────
# BZ (INTERPLANETARY MAGNETIC FIELD — Z COMPONENT) THRESHOLDS (nT)
# ─────────────────────────────────────────────────────────────────

BZ_STORM_DEVELOPMENT: float = -5.0     # Storm development beginning
BZ_SIGNIFICANT_STORM: float = -10.0    # Significant storm likely
BZ_SEVERE_STORM: float = -20.0         # Severe storm (G3+)
BZ_EXTREME_STORM: float = -30.0        # Extreme storm (G4–G5)

# ─────────────────────────────────────────────────────────────────
# PHYSICAL VALID RANGES FOR SENSOR DATA VALIDATION
# ─────────────────────────────────────────────────────────────────

VALID_RANGES: dict = {
    "bz_gsm":           (-100.0,   100.0),    # nT
    "bt_total":         (0.0,      100.0),    # nT
    "sw_speed_kmps":    (200.0,   3000.0),    # km/s
    "proton_density":   (0.0,      100.0),    # protons/cm³
    "proton_temp":      (1000.0,   10_000_000.0),  # K
    "xray_flux":        (1e-9,     1e-2),     # W/m²
    "kp_index":         (0.0,      9.0),      # dimensionless
}

# ─────────────────────────────────────────────────────────────────
# NOAA ALERT CODES → STORM CLASS MAPPING
# ─────────────────────────────────────────────────────────────────

ALERT_CODE_TO_STORM_CLASS: dict = {
    "ALTK05": "G1",
    "ALTK06": "G2",
    "ALTK07": "G3",
    "ALTK08": "G4",
    "ALTK09": "G5",
    "WATA20": "WATCH",   # Geomagnetic storm Watch (earliest warning)
    "WARNG":  "WARNING", # Warning issued
}

# ─────────────────────────────────────────────────────────────────
# Kp INDEX → NOAA STORM CLASS THRESHOLDS
# ─────────────────────────────────────────────────────────────────

KP_TO_STORM_CLASS: dict = {
    5: "G1",
    6: "G2",
    7: "G3",
    8: "G4",
    9: "G5",
}

KP_STORM_THRESHOLD: float = 5.0  # Kp >= 5 means at least G1

# ─────────────────────────────────────────────────────────────────
# POLL INTERVALS (seconds)
# ─────────────────────────────────────────────────────────────────

POLL_INTERVAL_SOLAR_WIND_S: int = 60
POLL_INTERVAL_KP_S: int = 60
POLL_INTERVAL_XRAY_S: int = 300    # 5 minutes
POLL_INTERVAL_ALERTS_S: int = 300  # 5 minutes
POLL_INTERVAL_CME_S: int = 1800    # 30 minutes
POLL_INTERVAL_CLEANUP_S: int = 86400  # 24 hours

# ─────────────────────────────────────────────────────────────────
# DATA QUALITY THRESHOLDS
# ─────────────────────────────────────────────────────────────────

DATA_AGE_STALE_SECONDS: int = 600        # 10 minutes
DATA_AGE_PARTIAL_SECONDS: int = 300      # 5 minutes
INTERPOLATION_MAX_GAP_SECONDS: int = 900 # 15 minutes
INTERPOLATION_LOOKBACK_RECORDS: int = 15

# ─────────────────────────────────────────────────────────────────
# DATABASE RETENTION POLICIES (days)
# ─────────────────────────────────────────────────────────────────

RETENTION_SOLAR_WIND_DAYS: int = 30
RETENTION_CME_EVENTS_DAYS: int = 90
RETENTION_NOAA_ALERTS_DAYS: int = 30
RETENTION_INGESTION_LOG_DAYS: int = 7

# ─────────────────────────────────────────────────────────────────
# HTTP REQUEST SETTINGS
# ─────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT_S: int = 10
MAX_RETRIES: int = 3
RETRY_DELAYS_S: list = [0, 5, 15]   # Wait before attempt 1, 2, 3

# ─────────────────────────────────────────────────────────────────
# CME DONKI QUERY WINDOW
# ─────────────────────────────────────────────────────────────────

CME_LOOKBACK_DAYS: int = 7
CME_MAX_SPEED_KMPS: float = 5000.0
CME_MIN_SPEED_KMPS: float = 0.0

# ─────────────────────────────────────────────────────────────────
# X-RAY FLUX CLASS NUMERIC ENCODING
# ─────────────────────────────────────────────────────────────────

XRAY_CLASS_NUMERIC: dict = {
    "A": 1,
    "B": 2,
    "C": 3,
    "M": 4,
    "X": 5,
}

# ─────────────────────────────────────────────────────────────────
# STORM ONSET RISK LEVELS
# ─────────────────────────────────────────────────────────────────

RISK_LOW = "LOW"
RISK_MODERATE = "MODERATE"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"
RISK_UNKNOWN = "UNKNOWN"

# ─────────────────────────────────────────────────────────────────
# RECOMMENDED ACTION LEVELS
# ─────────────────────────────────────────────────────────────────

ACTION_MONITOR = "MONITOR"
ACTION_WATCH = "WATCH"
ACTION_PREPARE = "PREPARE"
ACTION_ACT_NOW = "ACT_NOW"

# ─────────────────────────────────────────────────────────────────
# DATA QUALITY FLAGS
# ─────────────────────────────────────────────────────────────────

QUALITY_GOOD = "GOOD"
QUALITY_PARTIAL = "PARTIAL"
QUALITY_STALE = "STALE"
QUALITY_UNKNOWN = "UNKNOWN"

# ─────────────────────────────────────────────────────────────────
# STORM IMMINENCE THRESHOLDS
# ─────────────────────────────────────────────────────────────────

STORM_IMMINENT_BZ_THRESHOLD: float = -10.0
STORM_IMMINENT_SPEED_THRESHOLD: float = 500.0
STORM_IMMINENT_CME_ARRIVAL_MINUTES: float = 120.0
STORM_IMMINENT_KP_THRESHOLD: float = 6.0
ACT_NOW_KP_THRESHOLD: float = 7.0
ACT_NOW_BZ_THRESHOLD: float = -20.0

# ─────────────────────────────────────────────────────────────────
# WEBSOCKET EVENTS
# ─────────────────────────────────────────────────────────────────

WS_EVENT_SOLAR_UPDATE = "solar_update"
WS_EVENT_STORM_ALERT = "storm_alert"
WS_EVENT_DATA_STALE = "data_stale"

# ─────────────────────────────────────────────────────────────────
# HISTORY ENDPOINT LIMITS
# ─────────────────────────────────────────────────────────────────

HISTORY_DEFAULT_HOURS: int = 24
HISTORY_MAX_HOURS: int = 168       # 7 days
HISTORY_MAX_RECORDS: int = 10_000

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s | %(levelname)8s | %(name)s | %(message)s"
LOG_FILE_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB
LOG_FILE_BACKUP_COUNT: int = 5
