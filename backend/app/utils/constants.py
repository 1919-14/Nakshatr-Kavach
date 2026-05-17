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
    "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json"
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

# -----------------------------------------------------------------------------
# LAYER 2 FEATURE ENGINEERING CONSTANTS
# -----------------------------------------------------------------------------

BZ_DANGER_THRESHOLD: float = -5.0
BZ_SEVERE_THRESHOLD: float = -10.0
BZ_EXTREME_THRESHOLD: float = -20.0
EPSILON_SCALE_FACTOR: float = 1e10
L0_KM: float = 7 * 6371.0
SEQUENCE_LENGTH: int = 24
N_SEQUENCE_FEATURES: int = 15
N_XGB_FEATURES: int = 45
SCALER_XGB_PATH: str = "app/models/xgb_scaler.pkl"
SCALER_LSTM_PATH: str = "app/models/lstm_scaler.pkl"
FEATURE_IMPUTATION_VALUES: dict = {
    "bz": 0.0,
    "bt": 5.0,
    "speed": 450.0,
    "density": 5.0,
    "epsilon": 0.0,
    "xray": 0.0,
    "kp": 1.0,
    "cme_arrival": 48.0,
}

# -----------------------------------------------------------------------------
# LAYER 3 KP PREDICTION ENGINE CONSTANTS
# -----------------------------------------------------------------------------

# Hybrid fusion weights: XGBoost dominates short-term, LSTM dominates long-term
XGB_LSTM_WEIGHTS: dict = {
    "3hr":  {"xgb": 0.70, "lstm": 0.30},
    "6hr":  {"xgb": 0.55, "lstm": 0.45},
    "12hr": {"xgb": 0.30, "lstm": 0.70},
    "24hr": {"xgb": 0.15, "lstm": 0.85},
}

# Monte Carlo Dropout sample count for uncertainty quantification
N_MC_SAMPLES: int = 100

# NOAA Kp storm classification thresholds
KP_STORM_THRESHOLDS: dict = {
    "G1": 5.0,
    "G2": 6.0,
    "G3": 7.0,
    "G4": 8.0,
    "G5": 9.0,
}

# Storm class dashboard colours
STORM_COLORS: dict = {
    "G5":      "#9C27B0",
    "G4":      "#F44336",
    "G3":      "#FF9800",
    "G2":      "#CDDC39",
    "G1":      "#4CAF50",
    "QUIET":   "#607D8B",
    "UNKNOWN": "#9E9E9E",
}

# Model file paths (relative to backend/)
XGB_MODEL_PATHS: dict = {
    "3hr":  "app/models/xgb_kp_3hr.json",
    "6hr":  "app/models/xgb_kp_6hr.json",
    "12hr": "app/models/xgb_kp_12hr.json",
    "24hr": "app/models/xgb_kp_24hr.json",
}
LSTM_MODEL_PATH: str = "app/models/lstm_kp_model.keras"
SHAP_EXPLAINER_PATHS: dict = {
    "3hr":  "app/models/shap_xgb_3hr.pkl",
    "6hr":  "app/models/shap_xgb_6hr.pkl",
    "12hr": "app/models/shap_xgb_12hr.pkl",
    "24hr": "app/models/shap_xgb_24hr.pkl",
}

# Uncertainty multipliers based on data quality
UNCERTAINTY_MULTIPLIERS: dict = {
    "GOOD":    1.0,
    "PARTIAL": 1.3,
    "STALE":   1.8,
    "UNKNOWN": 2.0,
}

# XGBoost horizon-specific hyperparameters
XGB_HORIZON_PARAMS: dict = {
    "3hr":  {"n_estimators": 500, "max_depth": 6, "reg_alpha": 0.1, "reg_lambda": 1.0},
    "6hr":  {"n_estimators": 600, "max_depth": 6, "reg_alpha": 0.1, "reg_lambda": 1.5},
    "12hr": {"n_estimators": 700, "max_depth": 6, "reg_alpha": 0.3, "reg_lambda": 2.0},
    "24hr": {"n_estimators": 800, "max_depth": 5, "reg_alpha": 0.1, "reg_lambda": 3.0},
}

# Kp forecast history retention (days)
RETENTION_KP_FORECAST_DAYS: int = 30

# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — Satellite Vulnerability Scoring Engine
# ═══════════════════════════════════════════════════════════════════════════════

# Risk level thresholds (composite score → risk level)
RISK_LEVEL_THRESHOLDS: dict = {
    "CRITICAL": 80.0,
    "HIGH":     60.0,
    "MODERATE": 40.0,
    "LOW":      20.0,
    "MINIMAL":   0.0,
}

# Risk level → numeric (for sorting/comparison)
RISK_LEVEL_NUMERIC: dict = {
    "CRITICAL": 4,
    "HIGH":     3,
    "MODERATE": 2,
    "LOW":      1,
    "MINIMAL":  0,
}

# Risk level → hex colour for dashboard display
RISK_LEVEL_COLORS: dict = {
    "CRITICAL": "#D50000",
    "HIGH":     "#FF6D00",
    "MODERATE": "#FFD600",
    "LOW":      "#00C853",
    "MINIMAL":  "#607D8B",
}

# Orbit type classification by altitude (km)
ORBIT_TYPE_DEFINITIONS: dict = {
    "LEO":      {"min_alt": 0,      "max_alt": 2000},
    "MEO":      {"min_alt": 2000,   "max_alt": 20000},
    "GEO":      {"min_alt": 35286,  "max_alt": 36286},
    "IGSO":     {"min_alt": 35286,  "max_alt": 36286},
    "L1_HALO":  {"min_alt": 1000000, "max_alt": 2000000},
}

# Criticality multipliers — scale composite risk for mission-critical assets
CRITICALITY_MULTIPLIERS: dict = {
    "NATIONAL_CRITICAL": 1.5,
    "DEFENSE_CRITICAL":  1.6,
    "HIGH":              1.3,
    "MODERATE":          1.0,
    "SCIENCE":           1.0,
}

# Shielding factors — surface charging risk scaling
SHIELDING_FACTORS: dict = {
    "HIGH":   0.6,
    "MEDIUM": 1.0,
    "LOW":    1.4,
}

# Composite risk weights by orbit type
ORBIT_RISK_WEIGHTS: dict = {
    "GEO":      {"drag": 0.00, "charging": 0.70, "seu": 0.30},
    "IGSO":     {"drag": 0.00, "charging": 0.70, "seu": 0.30},
    "GEO_IGSO": {"drag": 0.00, "charging": 0.70, "seu": 0.30},
    "LEO":      {"drag": 0.55, "charging": 0.00, "seu": 0.45},
    "MEO":      {"drag": 0.20, "charging": 0.40, "seu": 0.40},
    "L1_HALO":  {"drag": 0.00, "charging": 0.00, "seu": 1.00},
}

# Altitude scale factors for drag risk normalisation
ALTITUDE_DRAG_SCALES: dict = {
    400:  0.5,   # < 400 km: extremely high drag risk
    500:  0.7,   # 400–500 km
    600:  1.0,   # 500–600 km (reference altitude)
    800:  1.4,   # 600–800 km
    9999: 2.5,   # > 800 km: minimal drag
}

# X-ray severity → SEU enhancement factor
XRAY_SEU_FACTORS: dict = {
    0: 1.0,   # No data / below detection
    1: 1.0,   # A-class
    2: 1.1,   # B-class
    3: 1.3,   # C-class
    4: 1.8,   # M-class
    5: 3.0,   # X-class
}

# Physics constants for Jacchia-77 simplified atmosphere model
JACCHIA_SCALE_HEIGHT_KM: float = 70.0
LEO_REFERENCE_DENSITY_KG_M3: float = 5.0e-13   # ρ at 500 km quiet
DRAG_NORMALIZATION_FACTOR: float = 20.0          # normaliser for risk 0–100
CHARGING_EXPONENT: float = 1.8                   # non-linear electron energisation

# SAA (South Atlantic Anomaly) amplification
SAA_AMPLIFICATION_BASE: float = 4.0

# Safe mode operational buffer
SAFE_MODE_BUFFER_MINUTES: int = 20
SAFE_MODE_EXECUTION_MINUTES: int = 15
SAFE_MODE_AUTHORIZATION_MINUTES: int = 5

# Horizon label → minutes mapping
HORIZON_MINUTES: dict = {
    "3hr": 180, "6hr": 360, "12hr": 720, "24hr": 1440,
}

# WebSocket event for satellite risk changes
WS_EVENT_SATELLITE_RISK_CHANGE: str = "satellite_risk_change"

# Satellite risk history retention
RETENTION_SATELLITE_RISK_DAYS: int = 30
RETENTION_SATELLITE_EVENTS_DAYS: int = 90

# Earth constants for orbit visualisation
EARTH_RADIUS_KM: float = 6371.0
EARTH_RADIUS_3JS: float = 2.0

# Satellite data file path
ISRO_SATELLITES_JSON_PATH: str = "app/data/isro_satellites.json"

# NavIC degradation thresholds
NAVIC_DEGRADATION_KP: float = 5.0
NAVIC_IMPAIRED_KP: float = 7.0
NAVIC_AFFECTED_USERS_MILLION: int = 500
