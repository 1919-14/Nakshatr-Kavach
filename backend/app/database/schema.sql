-- backend/app/database/schema.sql
-- NAKSHATRA-KAVACH Layer 1 — MySQL Schema
-- All tables for solar wind readings, CME events, NOAA alerts, and ingestion log.
-- Run via db.init_db() on first startup. Uses CREATE TABLE IF NOT EXISTS for idempotency.

-- ─────────────────────────────────────────────────────────────────
-- TABLE: solar_wind_readings
-- One row per 1-minute poll cycle. Core Layer 1 output.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS solar_wind_readings (
    id                       INT AUTO_INCREMENT PRIMARY KEY,

    -- Source timestamps
    timestamp_utc            VARCHAR(32) NOT NULL,          -- ISO 8601, from DSCOVR/ACE
    ingested_at              VARCHAR(32) NOT NULL,          -- datetime.utcnow() ISO 8601

    -- Interplanetary Magnetic Field (nT) — from NOAA SWPC Source 1
    bx_gsm                   DOUBLE,
    by_gsm                   DOUBLE,
    bz_gsm                   DOUBLE,                        -- CRITICAL — null triggers PARTIAL
    bt_total                 DOUBLE,

    -- Solar Wind Plasma — from NOAA SWPC Source 1
    sw_speed_kmps            DOUBLE,
    proton_density_ccm       DOUBLE,
    proton_temp_kelvin       DOUBLE,

    -- Kp Index
    kp_estimated_from_sw     DOUBLE,
    kp_current               DOUBLE,
    kp_status                VARCHAR(32),

    -- X-Ray Flux — from GOES (Source 3)
    xray_flux_wm2            DOUBLE,
    xray_class               VARCHAR(16),
    xray_severity_numeric    INT,

    -- CME Data — from NASA DONKI (Source 4)
    cme_earth_directed       TINYINT(1) DEFAULT 0,
    cme_speed_kmps           DOUBLE,
    cme_arrival_minutes      DOUBLE,
    cme_arrival_time_utc     VARCHAR(32),

    -- Computed Physical Fields
    transit_warning_minutes  DOUBLE,
    epsilon_coupling         DOUBLE,
    dynamic_pressure_npa     DOUBLE,

    -- Official Alerts — from NOAA Alerts (Source 5)
    official_alert_class     VARCHAR(8),

    -- Quality & Derived Flags
    data_quality_flag        VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN',
    bz_southward_flag        TINYINT(1) DEFAULT 0,
    storm_onset_risk         VARCHAR(16) DEFAULT 'UNKNOWN',
    source_dscovr_active     TINYINT(1) DEFAULT 1,
    interpolated             TINYINT(1) DEFAULT 0,

    UNIQUE KEY uq_timestamp (timestamp_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_sw_timestamp ON solar_wind_readings(timestamp_utc DESC);
CREATE INDEX idx_sw_quality   ON solar_wind_readings(data_quality_flag);
CREATE INDEX idx_sw_ingested  ON solar_wind_readings(ingested_at DESC);


-- ─────────────────────────────────────────────────────────────────
-- TABLE: cme_events
-- Coronal Mass Ejection events from NASA DONKI, Earth-directed only.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cme_events (
    id                              INT AUTO_INCREMENT PRIMARY KEY,
    detected_at_utc                 VARCHAR(32) NOT NULL,
    cme_launch_time_utc             VARCHAR(32),
    speed_kmps                      DOUBLE,
    half_angle_deg                  DOUBLE,
    latitude_deg                    DOUBLE,
    longitude_deg                   DOUBLE,
    is_earth_directed               TINYINT(1) DEFAULT 0,
    estimated_arrival_utc           VARCHAR(32),
    estimated_duration_hr           DOUBLE,
    arrival_minutes_from_detection  DOUBLE,
    catalog_source                  VARCHAR(64),
    donki_link                      TEXT,
    active                          TINYINT(1) DEFAULT 1,
    created_at                      DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_cme_arrival ON cme_events(estimated_arrival_utc);
CREATE INDEX idx_cme_active  ON cme_events(active, is_earth_directed);


-- ─────────────────────────────────────────────────────────────────
-- TABLE: noaa_alerts
-- Official geomagnetic storm alerts from NOAA SWPC.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS noaa_alerts (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    product_id         VARCHAR(128) NOT NULL,
    issue_datetime_utc VARCHAR(32),
    alert_code         VARCHAR(16),
    storm_class        VARCHAR(8),
    full_message       TEXT,
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_alert_issued ON noaa_alerts(issue_datetime_utc DESC);
CREATE INDEX idx_alert_class  ON noaa_alerts(storm_class);


-- ─────────────────────────────────────────────────────────────────
-- TABLE: ingestion_log
-- Audit trail of every API poll attempt.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_log (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    source_name         VARCHAR(64) NOT NULL,
    poll_timestamp_utc  VARCHAR(32) NOT NULL,
    success             TINYINT(1) NOT NULL,
    records_ingested    INT DEFAULT 0,
    error_message       TEXT,
    response_time_ms    DOUBLE,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_log_source  ON ingestion_log(source_name, poll_timestamp_utc DESC);
CREATE INDEX idx_log_success ON ingestion_log(success);


-- ─────────────────────────────────────────────────────────────────
-- TABLE: kp_forecast_history
-- Layer 3 Kp prediction snapshots for accuracy tracking and history API.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kp_forecast_history (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    computed_at_utc         VARCHAR(32) NOT NULL,
    kp_current              DOUBLE,
    storm_class_current     VARCHAR(8),
    kp_forecast_3hr         DOUBLE,
    kp_forecast_6hr         DOUBLE,
    kp_forecast_12hr        DOUBLE,
    kp_forecast_24hr        DOUBLE,
    uncertainty_3hr         DOUBLE,
    uncertainty_6hr         DOUBLE,
    uncertainty_12hr        DOUBLE,
    uncertainty_24hr        DOUBLE,
    peak_storm_class        VARCHAR(8),
    storm_probability_12hr  DOUBLE,
    prediction_confidence   VARCHAR(16),
    inference_time_ms       DOUBLE,
    data_quality_used       VARCHAR(16),
    shap_top_feature        VARCHAR(64),
    shap_top_value          DOUBLE,
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_kp_hist_time ON kp_forecast_history(computed_at_utc DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- LAYER 4: Satellite Vulnerability Scoring
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS satellite_risk_history (
    id                  INTEGER PRIMARY KEY AUTO_INCREMENT,
    computed_at_utc     TEXT NOT NULL,
    satellite_name      TEXT NOT NULL,
    kp_used             DOUBLE,
    drag_risk           DOUBLE,
    charging_risk       DOUBLE,
    radiation_risk      DOUBLE,
    composite_final     DOUBLE,
    risk_level          VARCHAR(16),
    safe_mode_required  INTEGER,
    safe_mode_minutes   DOUBLE,
    recommended_action  TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_sat_risk_name_time
    ON satellite_risk_history(satellite_name(64), computed_at_utc(32) DESC);
CREATE INDEX idx_sat_risk_level
    ON satellite_risk_history(risk_level, computed_at_utc(32) DESC);

CREATE TABLE IF NOT EXISTS satellite_events (
    id                  INTEGER PRIMARY KEY AUTO_INCREMENT,
    event_timestamp_utc TEXT NOT NULL,
    satellite_name      TEXT NOT NULL,
    event_type          VARCHAR(32) NOT NULL,
    previous_risk_level VARCHAR(16),
    new_risk_level      VARCHAR(16),
    kp_at_event         DOUBLE,
    event_description   TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_sat_events_name
    ON satellite_events(satellite_name(64), event_timestamp_utc(32) DESC);

