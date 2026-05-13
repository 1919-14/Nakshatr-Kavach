"""Physical helpers: L1 warning window, epsilon coupling, dynamic pressure, storm class."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple


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
