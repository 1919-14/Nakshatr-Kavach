"""
NAKSHATRA-KAVACH — Physics Engine
High-fidelity geophysical models for satellite risk scoring.

Modules:
  - L1 transit warning window
  - Solar wind dynamic pressure (nPa)
  - Akasofu epsilon coupling function
  - Storm class classification from Kp
  - X-ray class from flux
  - Plasma proxy when NOAA plasma JSON is unavailable
  - IGRF-13 simplified dipole geomagnetic field model
  - South Atlantic Anomaly (SAA) intensity model
  - NavIC equatorial ionospheric scintillation model (S4-index based)
  - Dst classification from Dst-index value
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


# ─────────────────────────────────────────────────────────────────
# EXISTING CORE PHYSICS (unchanged signatures)
# ─────────────────────────────────────────────────────────────────

def warning_minutes_from_speed(sw_speed_kmps: Optional[float]) -> float:
    """DSCOVR L1 to Earth ~1.5e6 km; transit time in minutes."""
    v = float(sw_speed_kmps or 400.0)
    v = max(v, 200.0)
    return 1_500_000.0 / v / 60.0


def dynamic_pressure_npa(n_cm3: float, v_km_s: float) -> float:
    """Solar wind dynamic pressure (nPa): P = n_p * m_p * v^2, with proton mass ~1.67e-27."""
    mp = 1.67e-27
    n_si = max(n_cm3, 0.01) * 1e6
    v_si = max(v_km_s, 0.0) * 1000.0
    return float(n_si * mp * v_si * v_si * 1e9)


def akasofu_epsilon_W(
    v_km_s: float,
    bt_nt: float,
    bz_nt: float,
    by_nt: float,
) -> float:
    """
    Akasofu epsilon (approximate, W): v * Bt^2 * sin^4(theta/2) in canonical scaling.
    theta: clock angle in GSM Y-Z plane; sin(theta) ~ sqrt(by^2+bz^2) coupling to Bt.
    """
    v = max(v_km_s, 200.0) * 1000.0
    bt = max(bt_nt, 0.1)
    b_perp = math.sqrt(by_nt * by_nt + bz_nt * bz_nt)
    theta = math.atan2(b_perp, abs(bz_nt) + 1e-6) * 2.0
    s = math.sin(theta / 2.0)
    return float(v * (bt * 1e-9) ** 2 * (s ** 4) * 1e12)


def storm_class_from_kp(kp: float) -> str:
    if kp >= 9:
        return "G5"
    if kp >= 8:
        return "G4"
    if kp >= 7:
        return "G3"
    if kp >= 6:
        return "G2"
    if kp >= 5:
        return "G1"
    return "QUIET"


def xray_class_from_flux(flux_wm2: Optional[float]) -> str:
    if flux_wm2 is None or flux_wm2 <= 0:
        return "A0.0"
    f = float(flux_wm2)
    if f >= 1e-4:
        return "X"
    if f >= 1e-5:
        return "M"
    if f >= 1e-6:
        return "C"
    if f >= 1e-7:
        return "B"
    return "A"


def hemi_plasma_proxy(kp: float, bz: float, bt: float) -> Tuple[float, float, float]:
    """
    When NOAA plasma JSON is unavailable, use Kp- and IMF-informed proxies (order-of-magnitude).
    Returns (speed_km_s, density_cm3, temp_K).
    """
    kp = max(0.0, min(float(kp), 9.0))
    v = 350.0 + 18.0 * kp + 12.0 * max(0.0, -float(bz))
    n = 3.5 + 0.45 * kp + 0.08 * max(0.0, float(bt) - 8.0)
    t = 8e5 + 5e4 * kp * kp + 3e4 * max(0.0, -bz)
    return v, n, t


def snapshot_to_api_row(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Frontend / legacy API shape."""
    kp = float(snap.get("kp_current") or 0.0)
    v = snap.get("sw_speed_kmps")
    wm = warning_minutes_from_speed(v)
    return {
        "timestamp": snap.get("timestamp").isoformat() + "Z"
        if hasattr(snap.get("timestamp"), "isoformat")
        else str(snap.get("timestamp")),
        "bz_gsm": snap.get("bz_gsm"),
        "bt_total": snap.get("bt_total"),
        "sw_speed": v,
        "proton_density": snap.get("proton_density_ccm"),
        "proton_temp": snap.get("proton_temp_K"),
        "xray_flux": snap.get("xray_flux_Wm2"),
        "xray_class": snap.get("xray_class") or xray_class_from_flux(snap.get("xray_flux_Wm2")),
        "kp_current": kp,
        "storm_class": storm_class_from_kp(kp),
        "storm_active": kp >= 5,
        "data_quality": snap.get("quality_flag", "UNKNOWN"),
        "warning_minutes": round(wm, 1),
        "epsilon_proxy_GW": round(
            akasofu_epsilon_W(
                float(v or 400),
                float(snap.get("bt_total") or 5),
                float(snap.get("bz_gsm") or 0),
                float(snap.get("by_gsm") or 0),
            )
            / 1e9,
            4,
        ),
        "dynamic_pressure_npa": round(
            dynamic_pressure_npa(float(snap.get("proton_density_ccm") or 5), float(v or 400)), 3
        ),
    }


# ─────────────────────────────────────────────────────────────────
# PHASE 2 ENHANCEMENT: IGRF-13 Simplified Dipole Geomagnetic Field
# ─────────────────────────────────────────────────────────────────

# IGRF-13 (2020) main dipole coefficients (nT)
_IGRF13_G10: float = -29404.5   # g(1,0) — axial dipole
_IGRF13_G11: float = -1450.9    # g(1,1) — equatorial x
_IGRF13_H11: float = 4652.5     # h(1,1) — equatorial y
_EARTH_RADIUS_KM: float = 6371.2  # IGRF reference radius


def compute_magnetic_field_igrf(
    latitude_deg: float,
    longitude_deg: float,
    altitude_km: float,
    date: Optional[datetime] = None,
) -> Dict[str, float]:
    """
    Compute Earth's magnetic field components using the IGRF-13 centered dipole
    (first-order terms only).  Accurate to ±5% for mid-latitudes; sufficient for
    radiation belt and SAA risk estimates.

    Args:
        latitude_deg:  Geographic latitude in degrees (-90 to +90).
        longitude_deg: Geographic longitude in degrees (-180 to +180).
        altitude_km:   Altitude above Earth's surface in km.
        date:          Reference date (UTC). Defaults to current UTC date.

    Returns:
        Dict with:
            B_total_nT   — Total field magnitude (nT)
            B_north_nT   — Northward component
            B_east_nT    — Eastward component
            B_down_nT    — Downward component (positive downward)
            magnetic_lat — Magnetic latitude (degrees)
            magnetic_lon — Magnetic longitude (degrees)
            inclination  — Field inclination angle (degrees)
            declination  — Magnetic declination (degrees)
    """
    if date is None:
        date = datetime.now(timezone.utc)

    # Secular variation: simple linear interpolation from epoch 2020.0
    year_frac = date.year + (date.timetuple().tm_yday / 365.25)
    dt = year_frac - 2020.0  # years since IGRF-13 epoch

    # IGRF-13 secular variation rates (nT/yr)
    g10 = _IGRF13_G10 + dt * 5.7
    g11 = _IGRF13_G11 + dt * 7.4
    h11 = _IGRF13_H11 + dt * (-25.9)

    lat_rad = math.radians(latitude_deg)
    lon_rad = math.radians(longitude_deg)

    # Geocentric radius ratio
    r = _EARTH_RADIUS_KM + max(altitude_km, 0.0)
    ratio = (_EARTH_RADIUS_KM / r) ** 3

    # Schmidt quasi-normal associated Legendre P(1,0) = cos(colat), P(1,1) = sin(colat)
    colat_rad = math.pi / 2.0 - lat_rad
    cos_c = math.cos(colat_rad)
    sin_c = math.sin(colat_rad)

    # Field components (nT) in geocentric spherical: r, theta, phi
    # Br = -2*(g10*cos_c + g11*cos_lon*sin_c + h11*sin_lon*sin_c) * ratio
    # Btheta = -(−g10*sin_c + g11*cos_lon*cos_c + h11*sin_lon*cos_c) * ratio
    # Bphi = (g11*sin_lon − h11*cos_lon) * sin_c * ratio  (not / sin_c as theta component absorbs)
    cos_lon = math.cos(lon_rad)
    sin_lon = math.sin(lon_rad)

    Br = -2.0 * ratio * (g10 * cos_c + (g11 * cos_lon + h11 * sin_lon) * sin_c)
    Btheta = -ratio * (-g10 * sin_c + (g11 * cos_lon + h11 * sin_lon) * cos_c)
    Bphi = ratio * (g11 * sin_lon - h11 * cos_lon) * sin_c / (sin_c + 1e-10)

    # Convert to north/east/down (geographic)
    B_down = -Br
    B_north = -Btheta
    B_east = Bphi

    B_total = math.sqrt(B_north**2 + B_east**2 + B_down**2)
    B_horiz = math.sqrt(B_north**2 + B_east**2)
    inclination = math.degrees(math.atan2(B_down, B_horiz))
    declination = math.degrees(math.atan2(B_east, B_north))

    # Magnetic latitude (dipole approximation)
    mag_lat = math.degrees(math.atan(0.5 * math.tan(lat_rad)))

    return {
        "B_total_nT": round(B_total, 1),
        "B_north_nT": round(B_north, 1),
        "B_east_nT": round(B_east, 1),
        "B_down_nT": round(B_down, 1),
        "magnetic_lat": round(mag_lat, 2),
        "magnetic_lon": round(longitude_deg, 2),
        "inclination": round(inclination, 2),
        "declination": round(declination, 2),
    }


# ─────────────────────────────────────────────────────────────────
# PHASE 2 ENHANCEMENT: South Atlantic Anomaly (SAA) Intensity Model
# ─────────────────────────────────────────────────────────────────

# SAA core parameters (geographic centre, approximate)
_SAA_CENTER_LAT: float = -25.0    # degrees
_SAA_CENTER_LON: float = -50.0    # degrees W
_SAA_SEMI_LAT: float = 30.0       # semi-axis in latitude (degrees)
_SAA_SEMI_LON: float = 45.0       # semi-axis in longitude (degrees)
# Westward drift of SAA: ~0.3 deg/year from 2000 epoch
_SAA_DRIFT_DEG_PER_YEAR: float = 0.3
_SAA_EPOCH_YEAR: float = 2000.0


def compute_saa_intensity(
    altitude_km: float,
    satellite_lat: float = _SAA_CENTER_LAT,
    satellite_lon: float = _SAA_CENTER_LON,
    date: Optional[datetime] = None,
) -> Dict[str, float]:
    """
    Estimate the South Atlantic Anomaly trapped-radiation intensity at a
    given satellite position.  Uses an elliptical Gaussian overlap model
    anchored to the IGRF field minimum.

    The result is a unitless intensity fraction (0–1) and a normalised
    particle flux amplification factor relative to the background LEO flux.

    Args:
        altitude_km:   Orbital altitude above Earth surface (km).
        satellite_lat: Current satellite latitude (degrees).
        satellite_lon: Current satellite longitude (degrees).
        date:          Reference UTC date for SAA drift correction.

    Returns:
        Dict with:
            saa_overlap_fraction  — 0 (outside) to 1 (centre of SAA)
            flux_amplification    — Multiplicative factor vs background LEO flux
            in_saa_region         — Boolean flag
            saa_center_lat        — Current drift-corrected SAA centre latitude
            saa_center_lon        — Current drift-corrected SAA centre longitude
    """
    if date is None:
        date = datetime.now(timezone.utc)

    # Drift correction
    years_since_epoch = date.year + (date.timetuple().tm_yday / 365.25) - _SAA_EPOCH_YEAR
    saa_lon_corrected = _SAA_CENTER_LON - _SAA_DRIFT_DEG_PER_YEAR * years_since_epoch

    # Normalised elliptical distance from SAA centre
    dlat = (satellite_lat - _SAA_CENTER_LAT) / _SAA_SEMI_LAT
    dlon = (satellite_lon - saa_lon_corrected) / _SAA_SEMI_LON
    dist_sq = dlat**2 + dlon**2

    # Gaussian overlap (exp(-dist^2 / 2))
    saa_overlap = math.exp(-dist_sq / 2.0)

    # Altitude scaling: SAA flux peaks at ~200–400 km and diminishes above 1000 km
    if altitude_km < 200:
        alt_scale = 0.3
    elif altitude_km < 400:
        alt_scale = 0.5 + 0.5 * (altitude_km - 200) / 200.0
    elif altitude_km < 600:
        alt_scale = 1.0
    elif altitude_km < 1000:
        alt_scale = 1.0 + 0.8 * (altitude_km - 600) / 400.0  # peaks in inner belt
    elif altitude_km < 2000:
        alt_scale = max(0.3, 1.8 - (altitude_km - 1000) / 1000.0)
    else:
        alt_scale = 0.1  # MEO/GEO — SAA not relevant

    weighted = min(1.0, saa_overlap * alt_scale)
    flux_amp = 1.0 + 9.0 * weighted    # 1x (outside) to 10x (deep SAA centre at 600 km)

    return {
        "saa_overlap_fraction": round(weighted, 4),
        "flux_amplification": round(flux_amp, 2),
        "in_saa_region": weighted > 0.15,
        "saa_center_lat": round(_SAA_CENTER_LAT, 1),
        "saa_center_lon": round(saa_lon_corrected, 1),
    }


# ─────────────────────────────────────────────────────────────────
# PHASE 2 ENHANCEMENT: NavIC Equatorial Ionospheric Scintillation
# ─────────────────────────────────────────────────────────────────

# NavIC Equatorial Ionisation Anomaly (EIA) magnetic latitude range
_EIA_LAT_MIN: float = 5.0    # degrees magnetic latitude
_EIA_LAT_MAX: float = 20.0   # degrees magnetic latitude
# NavIC ground track nominally spans ~0–25°N magnetic latitude over India

# Diurnal scintillation peak: 20:00–00:00 IST (UTC+5:30 → UTC 14:30–18:30)
_SCINT_PEAK_START_UTC: int = 14   # 14:30 UTC = 20:00 IST
_SCINT_PEAK_END_UTC: int = 20     # 20:00 UTC = 01:30 IST

# S4 thresholds (amplitude scintillation index)
_S4_WEAK: float = 0.3
_S4_MODERATE: float = 0.5
_S4_STRONG: float = 0.7
_S4_EXTREME: float = 1.0

# NavIC L5 positioning error (metres) calibrated to S4 index
_NAVIC_BASE_ERROR_M: float = 3.0          # nominal quiet condition (HDOP ~1)
_NAVIC_ERROR_PER_S4_UNIT: float = 25.0    # empirical: +25m per S4=1.0

# Kp → scintillation enhancement coupling
# Geomagnetic storms drive equatorial plasma bubbles post-midnight
_KP_SCINT_COUPLING: float = 0.06    # S4 increase per Kp unit above quiet baseline
_XRAY_SCINT_COUPLING: float = 0.04  # S4 increase per xray severity unit (M-class → notable)


def compute_navic_scintillation(
    kp_peak: float,
    current_time_utc: Optional[datetime] = None,
    xray_severity: int = 1,
    magnetic_lat_deg: float = 15.0,   # India's geomagnetic latitude centroid
) -> Dict[str, Any]:
    """
    Compute the NavIC L5/S1 ionospheric scintillation S4 index and induced
    positioning error for India's EIA region.

    The S4 index (amplitude scintillation) is the standard deviation of
    signal amplitude normalised by the mean — S4 > 0.6 causes significant
    GNSS cycle slips and positioning errors.

    Physics basis:
      - EIA (Equatorial Ionisation Anomaly) creates plasma density irregularities
        at 5–20° magnetic latitude post-sunset that intensify during storms.
      - Kp ≥ 4 drives additional E×B drift, deepening plasma bubbles.
      - Solar X-ray flares (M/X class) cause sudden ionospheric disturbances (SID)
        with absorption on day-side, scintillation on night-side recovery.
      - Diurnal peak: 20:00–00:30 IST (post-sunset local time, ~14:30–19:00 UTC).

    Args:
        kp_peak:         Peak Kp over the next 3-hour window.
        current_time_utc: Current UTC datetime. Defaults to now.
        xray_severity:   NOAA X-ray severity (1=A, 2=B, 3=C, 4=M, 5=X).
        magnetic_lat_deg: Magnetic latitude of interest (degrees).

    Returns:
        Dict with:
            s4_index             — S4 amplitude scintillation index (0.0–1.5+)
            scintillation_class  — NONE / WEAK / MODERATE / STRONG / EXTREME
            positioning_error_m  — NavIC additional positioning error in metres
            navic_status         — NOMINAL / DEGRADED / IMPAIRED
            diurnal_phase        — DAY / TWILIGHT / NIGHT / PEAK_RISK
            eia_active           — Whether the satellite is in the EIA zone
            clock_error_ns       — Estimated clock group delay error in nanoseconds
            s4_components        — Breakdown of S4 contributions
    """
    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)

    kp = max(0.0, min(float(kp_peak), 9.0))
    xray_sev = max(1, min(int(xray_severity), 5))

    # ── EIA zone check ──
    in_eia = _EIA_LAT_MIN <= abs(magnetic_lat_deg) <= _EIA_LAT_MAX
    eia_factor = 1.0 if in_eia else 0.4    # EIA enhances scintillation

    # ── Diurnal phase factor ──
    utc_hour = current_time_utc.hour + current_time_utc.minute / 60.0
    if _SCINT_PEAK_START_UTC <= utc_hour < _SCINT_PEAK_END_UTC:
        diurnal_factor = 1.6   # post-sunset peak risk in IST
        diurnal_phase = "PEAK_RISK"
    elif 20 <= utc_hour or utc_hour < 2:
        diurnal_factor = 1.2   # late night / early morning
        diurnal_phase = "NIGHT"
    elif 2 <= utc_hour < 7:
        diurnal_factor = 0.5   # pre-dawn — scintillation subsides
        diurnal_phase = "NIGHT"
    elif 7 <= utc_hour < 12:
        diurnal_factor = 0.3   # morning — quiet ionosphere
        diurnal_phase = "DAY"
    else:
        diurnal_factor = 0.4   # afternoon / pre-sunset
        diurnal_phase = "TWILIGHT"

    # ── S4 component contributions ──
    s4_background = 0.05                                      # baseline quiet ionosphere
    s4_kp = _KP_SCINT_COUPLING * max(0.0, kp - 2.0)         # Kp enhancement (above Kp=2 baseline)
    s4_xray = _XRAY_SCINT_COUPLING * max(0.0, xray_sev - 2)  # X-ray enhancement (above B-class)

    s4_raw = (s4_background + s4_kp + s4_xray) * diurnal_factor * eia_factor
    s4_index = min(1.5, round(s4_raw, 3))

    # ── Classification ──
    if s4_index >= _S4_EXTREME:
        scintillation_class = "EXTREME"
        navic_status = "IMPAIRED"
    elif s4_index >= _S4_STRONG:
        scintillation_class = "STRONG"
        navic_status = "IMPAIRED"
    elif s4_index >= _S4_MODERATE:
        scintillation_class = "MODERATE"
        navic_status = "DEGRADED"
    elif s4_index >= _S4_WEAK:
        scintillation_class = "WEAK"
        navic_status = "DEGRADED"
    else:
        scintillation_class = "NONE"
        navic_status = "NOMINAL"

    # Override with Kp-based status for compatibility with existing NavIC logic
    if kp >= 7.0:
        navic_status = "IMPAIRED"
    elif kp >= 5.0 and navic_status == "NOMINAL":
        navic_status = "DEGRADED"

    # ── Positioning error ──
    positioning_error_m = round(
        _NAVIC_BASE_ERROR_M + _NAVIC_ERROR_PER_S4_UNIT * s4_index, 1
    )

    # ── Group delay / clock-equivalent error (nanoseconds) ──
    # TEC perturbation ΔI ≈ 2 TECU per S4=0.1 (empirical approximation for L5)
    clock_error_ns = round(s4_index * 6.0, 2)  # ~6 ns per S4 unit at L5 frequency

    return {
        "s4_index": s4_index,
        "scintillation_class": scintillation_class,
        "positioning_error_m": positioning_error_m,
        "navic_status": navic_status,
        "diurnal_phase": diurnal_phase,
        "eia_active": in_eia,
        "clock_error_ns": clock_error_ns,
        "s4_components": {
            "background": round(s4_background, 3),
            "kp_contribution": round(s4_kp, 3),
            "xray_contribution": round(s4_xray, 3),
            "diurnal_factor": diurnal_factor,
            "eia_factor": eia_factor,
        },
    }


# ─────────────────────────────────────────────────────────────────
# PHASE 2 ENHANCEMENT: Dst Index Classification
# ─────────────────────────────────────────────────────────────────

def classify_dst(dst_nt: Optional[float]) -> str:
    """
    Classify the Dst (Disturbance Storm Time) index into NOAA-aligned storm categories.

    Dst scale (WDC/Kyoto):
        ≥ -20 nT    : QUIET    (no storm)
        -20 to -50  : MINOR    (slight ring-current enhancement)
        -50 to -100 : MODERATE (G1-G2 equivalent)
        -100 to -200: INTENSE  (G3-G4 equivalent)
        < -200 nT   : EXTREME  (G5 equivalent)

    Args:
        dst_nt: Dst index in nanoTesla (negative values = disturbed).

    Returns:
        Classification string.
    """
    if dst_nt is None:
        return "UNKNOWN"
    try:
        d = float(dst_nt)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if d >= -20:
        return "QUIET"
    if d >= -50:
        return "MINOR"
    if d >= -100:
        return "MODERATE"
    if d >= -200:
        return "INTENSE"
    return "EXTREME"


def dst_to_storm_class(dst_nt: Optional[float]) -> str:
    """Map Dst index to approximate NOAA Geomagnetic storm class."""
    if dst_nt is None:
        return "QUIET"
    try:
        d = float(dst_nt)
    except (TypeError, ValueError):
        return "QUIET"
    if d >= -30:
        return "QUIET"
    if d >= -50:
        return "G1"
    if d >= -100:
        return "G2"
    if d >= -150:
        return "G3"
    if d >= -200:
        return "G4"
    return "G5"
