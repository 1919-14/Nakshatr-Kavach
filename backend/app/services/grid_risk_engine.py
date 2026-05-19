# backend/app/services/grid_risk_engine.py
"""
NAKSHATRA-KAVACH Layer 5: India Power Grid GIC Risk Engine.

This module converts Layer 3 Kp forecasts and Layer 1 solar-wind snapshot
metadata into deterministic, physics-based screening estimates of
Geomagnetically Induced Current (GIC) risk for monitored Indian EHV corridors.
It performs no external API calls and no ML inference.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.constants import (
    CASCADE_THRESHOLD_CRITICAL,
    CASCADE_THRESHOLD_HIGH,
    DAMAGE_TIME_BASE_SECONDS,
    ECONOMIC_MULTIPLIER_NO_SPARE,
    EEJ_BASE_FACTOR,
    E_REFERENCE_MV_PER_KM,
    G5_LAT_AMPLIFICATION,
    GEO_FIELD_KP_EXPONENT,
    GEO_FIELD_LAT_DENOMINATOR,
    GIC_MODEL_ACCURACY_NOTE,
    GIC_MODEL_NAME,
    GIC_OPERATIONAL_CALIBRATION_FACTOR,
    GRID_RISK_LEVEL_COLORS,
    GRID_RISK_LEVEL_NUMERIC,
    GROUND_H_FACTORS,
    HISTORICAL_GIC_INCIDENTS,
    POPULATION_DEDUPLICATION,
    POPULATION_GIC_GDP_CRORE_PER_MILLION,
    SATURATION_THRESHOLDS,
    WS_EVENT_GRID_RISK_CHANGE,
    WS_EVENT_NLDC_ALERT,
)
from app.utils.formatters import utcnow_iso

logger = logging.getLogger(__name__)

GRID_TOPOLOGY_PATH = Path(__file__).resolve().parent.parent / "data" / "india_grid_topology.json"

_GRID_RISKS_LOCK = threading.RLock()
LATEST_GRID_RISKS: Dict[str, Any] = {}


def update_latest_grid_risks(risks: dict) -> None:
    """Atomically replace the latest Layer 5 grid risk object."""
    global LATEST_GRID_RISKS
    with _GRID_RISKS_LOCK:
        LATEST_GRID_RISKS = copy.deepcopy(risks)


def get_latest_grid_risks() -> dict:
    """Return a thread-safe deep copy of the latest Layer 5 grid risks."""
    with _GRID_RISKS_LOCK:
        return copy.deepcopy(LATEST_GRID_RISKS)


class GridDatabase:
    """Thread-safe singleton loader for the India EHV corridor topology JSON."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls) -> "GridDatabase":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._loaded = False
                cls._instance.corridors = []
                cls._instance._by_id = {}
            return cls._instance

    @property
    def is_loaded(self) -> bool:
        """Whether the topology JSON has been successfully loaded."""
        return bool(self._loaded)

    def load(self) -> List[dict]:
        """Load and cache the authoritative corridor database from JSON."""
        with open(GRID_TOPOLOGY_PATH, "r", encoding="utf-8") as f:
            corridors = json.load(f)
        if not isinstance(corridors, list) or not corridors:
            raise ValueError("India grid topology must be a non-empty list")
        self.corridors = corridors
        self._by_id = {c["id"]: c for c in corridors}
        self._loaded = True
        logger.info("Grid DB loaded: corridors=%d", len(corridors))
        return self.get_all()

    def get_all(self) -> List[dict]:
        """Return all cached corridor records."""
        if not self._loaded:
            self.load()
        return copy.deepcopy(self.corridors)

    def get_by_id(self, corridor_id: str) -> Optional[dict]:
        """Return one corridor by ID, or None if absent."""
        if not self._loaded:
            self.load()
        corridor = self._by_id.get(corridor_id)
        return copy.deepcopy(corridor) if corridor else None


grid_db = GridDatabase()


class GICCalculator:
    """Physics and operational risk calculator for one transmission corridor."""

    @staticmethod
    def storm_scaling(kp: float) -> float:
        """
        Compute storm intensity scaling.

        Formula:
            f(Kp) = (Kp / 5.0) ** 2.2

        Kp=5 is the reference level, so f(5)=1.0. The exponent captures the
        super-linear growth of geoelectric fields during stronger storms.
        """
        kp_clamped = max(0.0, float(kp))
        return (kp_clamped / 5.0) ** GEO_FIELD_KP_EXPONENT

    @staticmethod
    def latitude_scaling(latitude_deg: float, kp_peak: float) -> float:
        """
        Compute geomagnetic latitude scaling for surface electric fields.

        Formula:
            g(lat) = max(0.15, 1 - (lat - 60)^2 / 3600)
            if Kp >= 9: g *= 1.5

        The auroral electrojet is strongest near 60 degrees latitude. During
        G5 storms, the disturbance expands equatorward, increasing Indian risk.
        """
        g_lat = max(
            0.15,
            1.0 - ((float(latitude_deg) - 60.0) ** 2) / GEO_FIELD_LAT_DENOMINATOR,
        )
        if float(kp_peak) >= 9.0:
            g_lat *= G5_LAT_AMPLIFICATION
        return g_lat

    @staticmethod
    def orientation_factor(angle_from_north_deg: float) -> float:
        """
        Compute corridor coupling to the storm-time northward electric field.

        Formula:
            orientation_factor = abs(cos(angle_from_north))

        A N-S corridor has factor 1.0 and an E-W corridor has factor 0.0, because
        the northward geoelectric field projects along N-S lines and not E-W ones.
        """
        return abs(math.cos(math.radians(float(angle_from_north_deg))))

    def compute_geoelectric_field(self, corridor: dict, kp_peak: float) -> Dict[str, float]:
        """
        Estimate surface geoelectric field with a plane-wave approximation.

        Formula:
            E_geo = E_ref * f(Kp) * g(latitude) * h(ground_type) * EEJ

        The Kp term controls storm intensity, latitude captures auroral-zone
        distance, ground type captures crustal conductivity, and the EEJ factor
        applies only to low-latitude corridors close to India's magnetic equator.
        """
        lat_mid = float(corridor["midpoint"]["lat"])
        ground_type = str(corridor.get("ground_type", "UNKNOWN")).upper()
        f_kp = self.storm_scaling(kp_peak)
        g_latitude = self.latitude_scaling(lat_mid, kp_peak)
        h_ground = float(GROUND_H_FACTORS.get(ground_type, GROUND_H_FACTORS["UNKNOWN"]))
        eej_factor = 1.0
        if bool(corridor.get("eej_applicable", False)) and lat_mid < 15.0:
            eej_factor = 1.0 + EEJ_BASE_FACTOR * (float(kp_peak) / 9.0)

        e_geo = E_REFERENCE_MV_PER_KM * f_kp * g_latitude * h_ground * eej_factor
        return {
            "E_geo_mV_per_km": e_geo,
            "f_kp": f_kp,
            "g_latitude": g_latitude,
            "h_ground": h_ground,
            "eej_factor": eej_factor,
            "orientation_factor": self.orientation_factor(corridor["angle_from_north_deg"]),
        }

    @staticmethod
    def compute_resistance(corridor: dict) -> Dict[str, float]:
        """
        Compute DC resistance of the line and transformer grounding path.

        Formula:
            R_line = resistance_per_km * length_km
            R_total = R_line + 2 * grounding_resistance

        GIC is quasi-DC, so the relevant impedance is the conductor DC
        resistance plus the grounding resistance at both transformer terminals.
        """
        length_km = float(corridor["length_km"])
        r_line = float(corridor["resistance_per_km_ohm"]) * length_km
        r_grounding = 2.0 * float(corridor["grounding_resistance_ohm"])
        return {
            "R_line_ohm": r_line,
            "R_grounding_ohm": r_grounding,
            "R_total_ohm": r_line + r_grounding,
        }

    @staticmethod
    def compute_raw_gic_amps(
        e_geo_mV_per_km: float,
        length_northward_km: float,
        r_total_ohm: float,
    ) -> float:
        """
        Compute uncalibrated GIC from the simplified line-integral formula.

        Formula:
            I_raw = (E_geo_mV_per_km * 0.001 * L_northward) / R_total

        The 0.001 converts mV/km to V/km. This raw formula remains separately
        callable for unit tests and physics sanity checks.
        """
        if r_total_ohm <= 0:
            raise ValueError("R_total must be positive for GIC calculation")
        return max(0.0, (float(e_geo_mV_per_km) * 0.001 * float(length_northward_km)) / float(r_total_ohm))

    def compute_gic_components(
        self,
        corridor: dict,
        kp: float,
        apply_calibration: bool = True,
    ) -> Dict[str, Any]:
        """
        Compute GIC and all intermediate corridor coupling terms.

        Formula:
            L_northward = length * abs(cos(angle_from_north))
            I_raw = (E_geo * 0.001 * L_northward) / R_total
            I_operational = I_raw * calibration_factor

        The calibration factor is applied only to operational screening output;
        the raw formula remains available through compute_raw_gic_amps().
        """
        geofield = self.compute_geoelectric_field(corridor, kp)
        resistance = self.compute_resistance(corridor)
        length_km = float(corridor["length_km"])
        orient = geofield["orientation_factor"]
        length_northward = length_km * orient
        raw_gic = self.compute_raw_gic_amps(
            geofield["E_geo_mV_per_km"],
            length_northward,
            resistance["R_total_ohm"],
        )
        calibration = GIC_OPERATIONAL_CALIBRATION_FACTOR if apply_calibration else 1.0
        gic_amps = raw_gic * calibration

        if apply_calibration and float(corridor["angle_from_north_deg"]) > 75.0:
            gic_ns = self.compute_raw_gic_amps(
                geofield["E_geo_mV_per_km"],
                length_km,
                resistance["R_total_ohm"],
            ) * calibration
            if gic_ns > 0.0:
                assert gic_amps < gic_ns * 0.1, "E-W corridor GIC coupling guard failed"

        return {
            **geofield,
            **resistance,
            "length_northward_km": length_northward,
            "raw_gic_amps": raw_gic,
            "calibration_factor": calibration,
            "gic_amps": gic_amps,
            "model": GIC_MODEL_NAME,
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
        }

    def compute_gic_for_kp(
        self,
        corridor: dict,
        kp: float,
        solar_data: Optional[dict] = None,
        apply_calibration: bool = True,
    ) -> float:
        """
        Compute GIC amplitude at a single Kp horizon.

        Formula:
            I(Kp_h) = calibrated simplified plane-wave GIC at Kp_h

        Each horizon recomputes the full non-linear Kp relation rather than
        scaling linearly from the peak GIC.
        """
        _ = solar_data
        return float(self.compute_gic_components(corridor, kp, apply_calibration)["gic_amps"])

    def compute_saturation_risk(self, gic_amps: float, transformer_type: str) -> Dict[str, Any]:
        """
        Convert GIC amperes to transformer saturation risk.

        Formula:
            saturation_risk = min(100, GIC / critical_threshold * 100)

        Transformer thresholds are type-specific because 765kV autotransformers
        saturate at lower quasi-DC current than conventional power transformers.
        """
        thresholds = SATURATION_THRESHOLDS[transformer_type]
        gic = max(0.0, float(gic_amps))
        risk = min(100.0, (gic / float(thresholds["critical"])) * 100.0)
        if gic < thresholds["safe"]:
            level = "SAFE"
        elif gic < thresholds["minor"]:
            level = "MINOR"
        elif gic < thresholds["moderate"]:
            level = "MODERATE"
        elif gic < thresholds["critical"]:
            level = "SEVERE"
        else:
            level = "CRITICAL"
        return {
            "saturation_risk": risk,
            "saturation_level": level,
            "saturation_thresholds": copy.deepcopy(thresholds),
        }

    def compute_thermal_timeline(self, gic_amps: float, transformer_type: str) -> Optional[float]:
        """
        Estimate time to transformer thermal damage at severe/critical GIC.

        Critical formula:
            t_minutes = 90s * (critical_threshold / GIC) / 60

        No minimum floor is applied: at higher GIC, thermal damage can occur
        faster than at the critical threshold. Below severe, damage is not
        expected from this screening model and None is returned.
        """
        thresholds = SATURATION_THRESHOLDS[transformer_type]
        gic = float(gic_amps)
        if gic >= thresholds["critical"]:
            return (DAMAGE_TIME_BASE_SECONDS * (float(thresholds["critical"]) / gic)) / 60.0
        if gic >= thresholds["severe"]:
            return max(30.0, 300.0 * (float(thresholds["severe"]) / gic))
        return None

    @staticmethod
    def classify_risk_level(saturation_risk: float) -> str:
        """
        Classify operational corridor risk from saturation percentage.

        Formula:
            >=75 CRITICAL, >=55 HIGH, >=35 MODERATE, >=15 LOW, else MINIMAL

        This is the dashboard/operator risk level, distinct from the
        transformer saturation band.
        """
        risk = float(saturation_risk)
        if risk >= 75.0:
            return "CRITICAL"
        if risk >= 55.0:
            return "HIGH"
        if risk >= 35.0:
            return "MODERATE"
        if risk >= 15.0:
            return "LOW"
        return "MINIMAL"

    def compute_load_reduction(
        self,
        gic_amps: float,
        transformer_type: str,
        current_loading_percent: float,
    ) -> dict:
        """
        Recommend load reduction based on GIC saturation thresholds.

        Load reduction does not reduce GIC itself; it lowers transformer thermal
        and reactive-power stress so the equipment has more operating headroom.
        """
        thresholds = SATURATION_THRESHOLDS[transformer_type]
        gic = float(gic_amps)
        if gic < thresholds["minor"]:
            return {"reduction_percent": 0, "action": "No load reduction required", "urgency": "NONE"}
        if gic < thresholds["moderate"]:
            return {"reduction_percent": 10, "action": "Reduce loading by 10% as precaution", "urgency": "ADVISORY"}
        if gic < thresholds["severe"]:
            return {"reduction_percent": 20, "action": "Reduce loading by 20% - activate monitoring", "urgency": "ELEVATED"}
        if gic < thresholds["critical"]:
            return {"reduction_percent": 35, "action": "Reduce loading by 35% - alert transformer team", "urgency": "URGENT"}
        max_safe_loading = max(0.0, float(current_loading_percent) - 50.0)
        return {
            "reduction_percent": 50,
            "action": (
                f"EMERGENCY: Reduce to {max_safe_loading:.0f}% loading. "
                "Prepare transformer isolation if GIC persists."
            ),
            "urgency": "CRITICAL_IMMEDIATE",
        }

    def compute_economic_impact(self, corridor: dict, saturation_risk: float, saturation_level: str) -> dict:
        """
        Compute expected economic impact from transformer damage probability.

        Formula:
            expected_replacement = P(damage) * replacement_cost
            gdp_loss = P(damage) * population_million * 8 Cr/month * outage_months

        This is an expected-value estimate, not a worst-case sum.
        """
        base_cost = float(corridor["transformer_replacement_cost_crore"])
        spare_available = bool(corridor["spare_transformer_available"])
        replacement_months = float(corridor["transformer_replacement_months"])
        economic_multiplier = 1.0 if spare_available else ECONOMIC_MULTIPLIER_NO_SPARE
        outage_months = 0.5 if spare_available else replacement_months
        damage_probability = float(saturation_risk) / 100.0
        if saturation_level in ("SAFE", "MINOR"):
            damage_probability = 0.0
        expected_replacement = base_cost * damage_probability
        gdp_loss = (
            float(corridor["population_served_million"])
            * POPULATION_GIC_GDP_CRORE_PER_MILLION
            * outage_months
            * damage_probability
        )
        total = expected_replacement + gdp_loss
        return {
            "transformer_replacement_cost_crore": round(base_cost, 1),
            "transformer_damage_probability": round(damage_probability, 3),
            "expected_replacement_cost_crore": round(expected_replacement, 1),
            "gdp_loss_crore": round(gdp_loss, 1),
            "total_economic_impact_crore": round(total, 1),
            "economic_multiplier": economic_multiplier,
            "spare_transformer_available": spare_available,
            "outage_months_if_damaged": outage_months,
            "replacement_months": replacement_months,
            "worst_case_replacement_cost_crore": round(base_cost * economic_multiplier, 1),
        }

    def compute_gic_by_horizon(self, corridor: dict, kp_forecast: dict, solar_data: dict) -> Dict[str, float]:
        """
        Compute GIC independently at each forecast horizon.

        Formula:
            I_h = I(Kp_h) using the full E_geo(Kp_h) relation

        GIC-Kp coupling is non-linear, so horizon values are recomputed instead
        of linearly scaled from the peak.
        """
        out: Dict[str, float] = {}
        for horizon in ("3hr", "6hr", "12hr", "24hr"):
            kp_h = _extract_horizon_kp(kp_forecast, horizon)
            out[horizon] = self.compute_gic_for_kp(corridor, kp_h, solar_data, apply_calibration=True)
        return out

    def build_corridor_map_data(self, corridor: dict, risk_object: dict) -> dict:
        """Build Leaflet-ready map data for one corridor risk object."""
        risk_level = risk_object["risk_level"]
        risk_color = GRID_RISK_LEVEL_COLORS.get(risk_level, GRID_RISK_LEVEL_COLORS["MINIMAL"])
        stroke_weight = {
            "CRITICAL": 5,
            "HIGH": 4,
            "MODERATE": 3,
            "LOW": 2,
            "MINIMAL": 1,
        }.get(risk_level, 1)
        thresholds = risk_object["saturation_thresholds"]
        return {
            "corridor_id": corridor["id"],
            "corridor_name": corridor["name"],
            "short_name": corridor["short_name"],
            "polyline_coords": corridor["polyline_coords"],
            "risk_level": risk_level,
            "risk_color": risk_color,
            "stroke_weight": stroke_weight,
            "stroke_opacity": 0.9 if risk_level in ("CRITICAL", "HIGH") else 0.6,
            "use_animated_dash": risk_level in ("CRITICAL", "HIGH"),
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            "popup": {
                "title": corridor["name"],
                "voltage_kv": corridor["voltage_kv"],
                "length_km": corridor["length_km"],
                "orientation": corridor["orientation_description"],
                "states": corridor["states_affected"],
                "gic_amps": round(risk_object["gic_amps"], 1),
                "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
                "saturation_level": risk_object["saturation_level"],
                "saturation_risk_pct": round(risk_object["saturation_risk"], 1),
                "population_million": corridor["population_served_million"],
                "economic_impact_crore": risk_object["economic_impact"]["total_economic_impact_crore"],
                "load_reduction": risk_object["load_reduction"]["reduction_percent"],
                "action": risk_object["load_reduction"]["action"],
                "urgency": risk_object["load_reduction"]["urgency"],
                "damage_time_min": risk_object.get("thermal_damage_time_minutes"),
                "spare_available": corridor["spare_transformer_available"],
                "replacement_months": corridor["transformer_replacement_months"],
                "historical_context": risk_object.get("historical_context"),
            },
            "markers": [
                {
                    "lat": corridor["start_point"]["lat"],
                    "lon": corridor["start_point"]["lon"],
                    "name": corridor["start_point"]["name"],
                    "type": "substation",
                    "color": risk_color,
                },
                {
                    "lat": corridor["end_point"]["lat"],
                    "lon": corridor["end_point"]["lon"],
                    "name": corridor["end_point"]["name"],
                    "type": "substation",
                    "color": risk_color,
                },
            ],
            "gic_forecast": {
                "labels": ["Now", "3hr", "6hr", "12hr", "24hr"],
                "values": [
                    round(risk_object["gic_now_amps"], 1),
                    round(risk_object["gic_by_horizon"]["3hr"], 1),
                    round(risk_object["gic_by_horizon"]["6hr"], 1),
                    round(risk_object["gic_by_horizon"]["12hr"], 1),
                    round(risk_object["gic_by_horizon"]["24hr"], 1),
                ],
                "threshold_minor": thresholds["minor"],
                "threshold_critical": thresholds["critical"],
                "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            },
        }

    def calculate_corridor_risk(self, corridor: dict, kp_forecast: dict, solar_data: dict) -> dict:
        """
        Calculate complete Layer 5 risk output for one corridor.

        Formula chain:
            Kp peak -> E_geo -> GIC amps -> saturation -> economics/actions

        All corridor geometry and electrical parameters are read from the JSON
        database, so operators can update topology without code changes.
        """
        kp_now = _extract_current_kp(kp_forecast)
        kp_peak = max(
            _extract_horizon_kp(kp_forecast, "3hr"),
            _extract_horizon_kp(kp_forecast, "6hr"),
            _extract_horizon_kp(kp_forecast, "12hr"),
        )
        components = self.compute_gic_components(corridor, kp_peak, apply_calibration=True)
        gic_now = self.compute_gic_for_kp(corridor, kp_now, solar_data, apply_calibration=True)
        gic_by_horizon = self.compute_gic_by_horizon(corridor, kp_forecast, solar_data)
        saturation = self.compute_saturation_risk(components["gic_amps"], corridor["transformer_type"])
        risk_level = self.classify_risk_level(saturation["saturation_risk"])
        thermal_time = self.compute_thermal_timeline(components["gic_amps"], corridor["transformer_type"])
        load_reduction = self.compute_load_reduction(
            components["gic_amps"],
            corridor["transformer_type"],
            corridor["typical_loading_percent"],
        )
        economic = self.compute_economic_impact(
            corridor,
            saturation["saturation_risk"],
            saturation["saturation_level"],
        )
        historical = None
        if risk_level in ("HIGH", "CRITICAL"):
            historical = HistoricalContextEngine.get_historical_context(
                kp_peak,
                kp_forecast.get("summary", {}).get("peak_storm_class", "UNKNOWN"),
            )

        risk_object = {
            "corridor_id": corridor["id"],
            "corridor_name": corridor["name"],
            "short_name": corridor["short_name"],
            "voltage_kv": corridor["voltage_kv"],
            "length_km": corridor["length_km"],
            "states_affected": corridor["states_affected"],
            "geoelectric_field": {
                "E_geo_mV_per_km": round(components["E_geo_mV_per_km"], 3),
                "f_kp": round(components["f_kp"], 4),
                "g_latitude": round(components["g_latitude"], 4),
                "h_ground": round(components["h_ground"], 3),
                "eej_factor": round(components["eej_factor"], 4),
                "orientation_factor": round(components["orientation_factor"], 4),
            },
            "gic_amps": round(components["gic_amps"], 3),
            "raw_gic_amps": round(components["raw_gic_amps"], 5),
            "gic_now_amps": round(gic_now, 3),
            "gic_by_horizon": {h: round(v, 3) for h, v in gic_by_horizon.items()},
            "model": GIC_MODEL_NAME,
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            "calibration_factor": components["calibration_factor"],
            "saturation_level": saturation["saturation_level"],
            "saturation_risk": round(saturation["saturation_risk"], 3),
            "risk_level": risk_level,
            "risk_level_numeric": GRID_RISK_LEVEL_NUMERIC.get(risk_level, 0),
            "risk_color_hex": GRID_RISK_LEVEL_COLORS.get(risk_level, GRID_RISK_LEVEL_COLORS["MINIMAL"]),
            "thermal_damage_time_minutes": round(thermal_time, 3) if thermal_time is not None else None,
            "load_reduction": load_reduction,
            "economic_impact": economic,
            "population_served_million": corridor["population_served_million"],
            "population_at_risk_million": corridor["population_served_million"] if risk_level in ("HIGH", "CRITICAL") else 0.0,
            "major_cities_affected": corridor["major_cities_affected"],
            "saturation_thresholds": saturation["saturation_thresholds"],
            "historical_context": historical,
        }
        risk_object["map_data"] = self.build_corridor_map_data(corridor, risk_object)
        return risk_object


class HistoricalContextEngine:
    """Matches current storm risk against known historical GIC incidents."""

    @staticmethod
    def get_historical_context(kp_peak: float, storm_class: str) -> Optional[dict]:
        """Return the closest historical comparison at comparable Kp severity."""
        if float(kp_peak) < 5.0:
            return None
        relevant = [
            incident for incident in HISTORICAL_GIC_INCIDENTS
            if float(incident["kp_peak"]) >= float(kp_peak) * 0.8
        ]
        if not relevant:
            return None
        closest = min(relevant, key=lambda i: abs(float(i["kp_peak"]) - float(kp_peak)))
        return {
            "incident": copy.deepcopy(closest),
            "comparison_text": (
                f"The predicted Kp of {float(kp_peak):.1f} ({storm_class}) is comparable "
                f"to the {closest['name']} on {closest['date']} "
                f"(Kp={float(closest['kp_peak']):.0f}, {closest['storm_class']}). "
                f"In that event: {closest['damage']}"
            ),
        }


def compute_national_summary(corridor_risks: List[dict], kp_forecast: dict) -> dict:
    """Aggregate corridor risks into a national grid impact summary."""
    if not corridor_risks:
        return {
            "critical_corridors_count": 0,
            "high_corridors_count": 0,
            "moderate_corridors_count": 0,
            "total_corridors_monitored": 0,
            "population_at_risk_million": 0.0,
            "total_economic_impact_crore": 0.0,
            "max_gic_amps": 0.0,
            "max_gic_corridor": None,
            "grid_stability_index": 100.0,
            "cascade_failure_risk": "LOW",
            "system_actions": [],
            "nldc_alert_required": False,
            "storm_class_used": kp_forecast.get("summary", {}).get("peak_storm_class", "UNKNOWN"),
            "kp_used_for_calculation": 0.0,
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
        }

    critical = [c for c in corridor_risks if c["risk_level"] == "CRITICAL"]
    high = [c for c in corridor_risks if c["risk_level"] == "HIGH"]
    moderate = [c for c in corridor_risks if c["risk_level"] == "MODERATE"]
    raw_population = sum(c["population_served_million"] for c in critical + high)
    population_at_risk = round(raw_population * POPULATION_DEDUPLICATION, 1)
    total_economic_impact = sum(
        c["economic_impact"]["total_economic_impact_crore"] for c in critical + high
    )
    max_gic_corridor = max(corridor_risks, key=lambda c: c["gic_amps"])
    total_corridors = len(corridor_risks)
    at_risk_fraction = len(critical) + 0.5 * len(high)
    grid_stability_index = max(0.0, 100.0 * (1.0 - at_risk_fraction / total_corridors))

    cascade_risk = "LOW"
    if len(critical) >= CASCADE_THRESHOLD_CRITICAL or len(critical) + len(high) >= CASCADE_THRESHOLD_HIGH:
        cascade_risk = "HIGH"
    elif len(critical) >= 1 or len(high) >= 3:
        cascade_risk = "MODERATE"

    system_actions: List[str] = []
    if critical:
        system_actions.append(
            "CRITICAL: Contact NLDC (National Load Dispatch Centre) immediately - "
            "activate space weather emergency protocol"
        )
        system_actions.append(
            "Alert all RLDC (Regional Load Dispatch Centres) - initiate EHV "
            "transformer monitoring at 15-minute intervals"
        )
    if high:
        system_actions.append(
            "Verify Dissolved Gas Analysis (DGA) monitoring is active on all at-risk transformers"
        )
        system_actions.append("Pre-position maintenance crews at critical substations")
    if population_at_risk > 5.0:
        system_actions.append(
            f"Alert State Electricity Regulatory Commissions in at-risk states - "
            f"{population_at_risk:.0f}M people at risk"
        )

    kp_peak = max(
        _extract_horizon_kp(kp_forecast, "3hr"),
        _extract_horizon_kp(kp_forecast, "6hr"),
        _extract_horizon_kp(kp_forecast, "12hr"),
    )
    return {
        "critical_corridors_count": len(critical),
        "high_corridors_count": len(high),
        "moderate_corridors_count": len(moderate),
        "total_corridors_monitored": total_corridors,
        "population_at_risk_million": population_at_risk,
        "total_economic_impact_crore": round(total_economic_impact, 0),
        "max_gic_amps": round(max_gic_corridor["gic_amps"], 1),
        "max_gic_corridor": max_gic_corridor["corridor_name"],
        "grid_stability_index": round(grid_stability_index, 1),
        "cascade_failure_risk": cascade_risk,
        "system_actions": system_actions,
        "nldc_alert_required": len(critical) > 0,
        "storm_class_used": kp_forecast.get("summary", {}).get("peak_storm_class", "UNKNOWN"),
        "kp_used_for_calculation": round(kp_peak, 2),
        "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
    }


def run_grid_risk_scoring(kp_forecast: dict, solar_data: dict) -> dict:
    """Run the complete Layer 5 scoring pipeline for all monitored corridors."""
    if not grid_db.is_loaded:
        grid_db.load()
    calculator = GICCalculator()
    corridors = grid_db.get_all()
    risks = [calculator.calculate_corridor_risk(c, kp_forecast, solar_data) for c in corridors]
    risks.sort(key=lambda c: (c["risk_level_numeric"], c["saturation_risk"]), reverse=True)
    summary = compute_national_summary(risks, kp_forecast)
    computed_at = utcnow_iso()
    kp_peak = summary["kp_used_for_calculation"]
    result = {
        "computed_at_utc": computed_at,
        "kp_peak_used": kp_peak,
        "storm_class_used": kp_forecast.get("summary", {}).get("peak_storm_class", "UNKNOWN"),
        "data_quality_used": solar_data.get("data_quality", kp_forecast.get("data_quality_used", "UNKNOWN")),
        "model": GIC_MODEL_NAME,
        "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
        "calibration_factor": GIC_OPERATIONAL_CALIBRATION_FACTOR,
        "corridors": risks,
        "national_summary": summary,
        "map_data": [c["map_data"] for c in risks],
    }
    return result


def save_grid_risks_to_db(grid_risks: dict) -> None:
    """Persist one Layer 5 scoring run into MySQL grid_risk_history."""
    try:
        from app.database.db import get_db

        with get_db() as conn:
            with conn.cursor() as cur:
                for risk in grid_risks.get("corridors", []):
                    cur.execute(
                        """INSERT INTO grid_risk_history (
                            computed_at_utc, corridor_id, corridor_name, kp_used,
                            e_geo_mV_per_km, gic_amps, saturation_level,
                            saturation_risk, risk_level, load_reduction_percent,
                            economic_impact_crore, transformer_damage_prob
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            grid_risks.get("computed_at_utc"),
                            risk.get("corridor_id"),
                            risk.get("corridor_name"),
                            grid_risks.get("kp_peak_used"),
                            risk.get("geoelectric_field", {}).get("E_geo_mV_per_km"),
                            risk.get("gic_amps"),
                            risk.get("saturation_level"),
                            risk.get("saturation_risk"),
                            risk.get("risk_level"),
                            risk.get("load_reduction", {}).get("reduction_percent"),
                            risk.get("economic_impact", {}).get("total_economic_impact_crore"),
                            risk.get("economic_impact", {}).get("transformer_damage_probability"),
                        ),
                    )
    except Exception as exc:
        logger.error("Grid risks DB save failed: %s", exc)


def check_and_emit_grid_alerts(new_risks: dict, previous_risks: dict) -> None:
    """Detect corridor risk level changes and emit WebSocket events."""
    if not previous_risks:
        return
    try:
        from app import socketio
    except Exception:
        socketio = None

    new_corridors = {c["corridor_id"]: c for c in new_risks.get("corridors", [])}
    prev_corridors = {c["corridor_id"]: c for c in previous_risks.get("corridors", [])}

    for corridor_id, new_c in new_corridors.items():
        prev_c = prev_corridors.get(corridor_id, {})
        prev_level = prev_c.get("risk_level", "MINIMAL")
        new_level = new_c.get("risk_level", "MINIMAL")
        if new_level == prev_level:
            continue

        payload = {
            "corridor_id": corridor_id,
            "corridor_name": new_c["corridor_name"],
            "previous_level": prev_level,
            "new_level": new_level,
            "gic_amps": new_c["gic_amps"],
            "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            "load_reduction": new_c["load_reduction"]["reduction_percent"],
            "action": new_c["load_reduction"]["action"],
            "urgency": new_c["load_reduction"]["urgency"],
            "economic_impact": new_c["economic_impact"]["total_economic_impact_crore"],
            "timestamp_utc": new_risks["computed_at_utc"],
        }
        if socketio is not None:
            socketio.emit(WS_EVENT_GRID_RISK_CHANGE, payload)

        logger.warning(
            "GRID THRESHOLD: %s | %s → %s | GIC=%.1fA | Load_reduction=%d%% | Impact=₹%.0fCr",
            new_c["short_name"],
            prev_level,
            new_level,
            new_c["gic_amps"],
            new_c["load_reduction"]["reduction_percent"],
            new_c["economic_impact"]["total_economic_impact_crore"],
        )
        _save_grid_event_to_db(
            new_risks,
            new_c,
            "RISK_LEVEL_CHANGE",
            prev_level,
            new_level,
            f"{new_c['short_name']} risk changed from {prev_level} to {new_level}",
        )

        if new_c["load_reduction"]["reduction_percent"] > 0:
            _save_grid_event_to_db(
                new_risks,
                new_c,
                "LOAD_REDUCTION_ALERT",
                prev_level,
                new_level,
                new_c["load_reduction"]["action"],
            )

        if new_level == "CRITICAL" and prev_level != "CRITICAL":
            nldc_payload = {
                "corridor_name": new_c["corridor_name"],
                "gic_amps": new_c["gic_amps"],
                "thermal_damage_time_minutes": new_c.get("thermal_damage_time_minutes"),
                "immediate_action": new_c["load_reduction"]["action"],
                "economic_impact_crore": new_c["economic_impact"]["total_economic_impact_crore"],
                "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            }
            if socketio is not None:
                socketio.emit(WS_EVENT_NLDC_ALERT, nldc_payload)
            _save_grid_event_to_db(
                new_risks,
                new_c,
                "NLDC_ALERT_TRIGGERED",
                prev_level,
                new_level,
                "NLDC alert triggered for critical GIC threshold crossing",
            )


def _save_grid_event_to_db(
    risks: dict,
    corridor: dict,
    event_type: str,
    previous_level: str,
    new_level: str,
    description: str,
) -> None:
    """Persist one grid event to MySQL, best-effort only."""
    try:
        from app.database.db import get_db

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO grid_events (
                        event_timestamp_utc, corridor_id, corridor_name, event_type,
                        previous_risk_level, new_risk_level, gic_amps_at_event,
                        kp_at_event, event_description
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        risks.get("computed_at_utc"),
                        corridor.get("corridor_id"),
                        corridor.get("corridor_name"),
                        event_type,
                        previous_level,
                        new_level,
                        corridor.get("gic_amps"),
                        risks.get("kp_peak_used"),
                        description,
                    ),
                )
    except Exception as exc:
        logger.debug("Grid event DB save skipped: %s", exc)


def score_grid(kp_now: float, kp_horizon: float) -> List[Dict[str, Any]]:
    """
    Backward-compatible wrapper for the older dashboard pipeline.

    New code should call run_grid_risk_scoring() or /api/grid/map. This wrapper
    converts the full Layer 5 contract into the legacy list shape.
    """
    forecast = {
        "current": {"kp": float(kp_now)},
        "forecast": {
            "3hr": {"kp": float(kp_now)},
            "6hr": {"kp": float(kp_horizon)},
            "12hr": {"kp": float(kp_horizon)},
            "24hr": {"kp": float(kp_horizon)},
        },
        "summary": {"peak_storm_class": _storm_class_from_kp(max(float(kp_now), float(kp_horizon)))},
        "data_quality_used": "UNKNOWN",
    }
    risks = run_grid_risk_scoring(forecast, {"data_quality": "UNKNOWN"})
    legacy: List[Dict[str, Any]] = []
    for risk in risks["corridors"]:
        legacy.append(
            {
                "id": risk["corridor_id"],
                "name": risk["corridor_name"],
                "states": " -> ".join(risk["states_affected"]),
                "voltage": f"{risk['voltage_kv']}kV",
                "coords": risk["map_data"]["polyline_coords"],
                "gic_amps": int(round(risk["gic_amps"])),
                "risk_percent": int(round(risk["saturation_risk"])),
                "impact_crore": int(round(risk["economic_impact"]["total_economic_impact_crore"])),
                "population_millions": round(risk["population_served_million"], 2),
                "action": risk["load_reduction"]["action"],
                "model_accuracy_note": GIC_MODEL_ACCURACY_NOTE,
            }
        )
    return legacy


def _extract_horizon_kp(kp_forecast: dict, horizon: str) -> float:
    """Safely extract a Kp forecast horizon value."""
    try:
        return max(0.0, float(kp_forecast.get("forecast", {}).get(horizon, {}).get("kp", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _extract_current_kp(kp_forecast: dict) -> float:
    """Safely extract current Kp from a Layer 3 forecast object."""
    try:
        return max(0.0, float(kp_forecast.get("current", {}).get("kp", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _storm_class_from_kp(kp: float) -> str:
    """Classify Kp to a NOAA-style storm class for legacy wrapper output."""
    if kp >= 9.0:
        return "G5"
    if kp >= 8.0:
        return "G4"
    if kp >= 7.0:
        return "G3"
    if kp >= 6.0:
        return "G2"
    if kp >= 5.0:
        return "G1"
    return "QUIET"
