"""
Layer 1: Real-time data ingestion — NOAA JSON, validation, MySQL/SQLite, snapshot.
Downstream layers must not call external HTTP; only this module does.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

from app.db import SolarWindData, get_engine, get_session_factory, init_db
from app.services.physics import hemi_plasma_proxy, storm_class_from_kp, xray_class_from_flux, warning_minutes_from_speed

logger = logging.getLogger(__name__)

# External data URLs
RTSW_MAG = "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json"
PLANETARY_KP_1M = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
GOES_XRAY = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"
DONKI_CME = "https://api.nasa.gov/DONKI/CME"
DONKI_FLR = "https://api.nasa.gov/DONKI/FLR"
ALERTS_FEED = "https://services.swpc.noaa.gov/json/alert.xml.json"

# Constants
PHYSICAL_RANGES = {
    "bz_gsm": (-100, 100),
    "by_gsm": (-100, 100),
    "bx_gsm": (-100, 100),
    "bt_total": (0, 100),
    "sw_speed_kmps": (200, 1200),
    "proton_density_ccm": (0, 100),
    "proton_temp_K": (1e4, 1e8),
    "xray_flux_Wm2": (1e-10, 1e-2),
    "kp_current": (0, 9),
}

UNIFIED_SCHEMA_COLS = [
    "timestamp", "source", "bz_gsm", "by_gsm", "bx_gsm", "bt_total",
    "sw_speed_kmps", "proton_density_ccm", "proton_temp_K",
    "xray_flux_Wm2", "kp_current", "xray_class",
    "cme_id", "cme_speed_kmps", "cme_earth_directed",
    "flare_id", "flare_class", "flare_earth_directed",
    "alerts", "quality_flag", "is_interpolated",
]

INTERPOLATION_THRESHOLD_MINUTES = 15


class DataIngestionService:
    def __init__(self):
        init_db()
        self.engine = get_engine()
        self.Session = get_session_factory()
        self._latest_snapshot: Dict[str, Any] = {}
        self._scheduler = None
        self._nasa_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
        self._alert_cache: List[Dict[str, Any]] = []
        self._flare_cache: List[Dict[str, Any]] = []

    def _fetch_dscovr_mag(self) -> Dict[str, Any]:
        """Fetch latest DSCOVR magnetic field data with robust fallback."""
        try:
            r = requests.get(RTSW_MAG, timeout=25)
            r.raise_for_status()
            rows: List[Dict] = r.json()
            dsc = [x for x in rows if str(x.get("source", "")).upper() == "DSCOVR"]
            if not dsc:
                dsc = rows[:5]
            dsc.sort(key=lambda x: str(x.get("time_tag", "")), reverse=True)
            row = dsc[0]
            return {
                "bz_gsm": _f(row.get("bz_gsm")),
                "by_gsm": _f(row.get("by_gsm")),
                "bx_gsm": _f(row.get("bx_gsm")),
                "bt_total": _f(row.get("bt")),
                "time_tag": row.get("time_tag"),
            }
        except Exception as e:
            logger.warning("DSCOVR fetch failed: %s, returning partial fallback", e)
            return {
                "bz_gsm": None,
                "by_gsm": None,
                "bx_gsm": None,
                "bt_total": None,
                "time_tag": None,
            }

    def _fetch_kp(self) -> Dict[str, Any]:
        """Fetch latest planetary K-index."""
        try:
            r = requests.get(PLANETARY_KP_1M, timeout=25)
            r.raise_for_status()
            rows = r.json()
            if not rows:
                return {"kp_current": None, "kp_time_tag": None}
            last = rows[-1]
            kp = last.get("estimated_kp") or last.get("kp_index") or last.get("Kp")
            return {
                "kp_current": float(kp) if kp is not None else None,
                "kp_time_tag": last.get("time_tag"),
            }
        except Exception as e:
            logger.warning("Kp fetch failed: %s", e)
            return {"kp_current": None, "kp_time_tag": None}

    def _fetch_xray(self) -> Dict[str, Any]:
        """Fetch latest GOES X-ray flux."""
        try:
            r = requests.get(GOES_XRAY, timeout=25)
            r.raise_for_status()
            rows = r.json()
            if not rows:
                return {"xray_flux_Wm2": None}
            last = rows[-1]
            flux = last.get("flux") or last.get("flux1") or last.get("flux2")
            return {"xray_flux_Wm2": float(flux) if flux is not None else None}
        except Exception as e:
            logger.warning("X-ray fetch failed: %s", e)
            return {"xray_flux_Wm2": None}

    def _fetch_alerts(self) -> List[Dict[str, Any]]:
        """Fetch active NOAA space weather alerts."""
        try:
            r = requests.get(ALERTS_FEED, timeout=20)
            r.raise_for_status()
            data = r.json()
            alerts = data.get("alerts", [])
            recent = [
                a for a in alerts
                if _parse_alert_time(a.get("issue_datetime", ""))
                >= datetime.utcnow() - timedelta(hours=6)
            ]
            return recent
        except Exception as e:
            logger.warning("Alerts fetch failed: %s", e)
            return self._alert_cache[-5:] if self._alert_cache else []

    def _fetch_solar_flares(self) -> List[Dict[str, Any]]:
        """Fetch recent DONKI solar flare events."""
        try:
            start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
            end = datetime.utcnow().strftime("%Y-%m-%d")
            url = f"{DONKI_FLR}?startDate={start}&endDate={end}&api_key={self._nasa_key}"
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                return self._flare_cache[-3:] if self._flare_cache else []
            events = r.json() if isinstance(r.json(), list) else []
            self._flare_cache.extend(events[-5:])
            return events[-5:] if events else []
        except Exception as e:
            logger.warning("Solar flare fetch failed: %s", e)
            return self._flare_cache[-3:] if self._flare_cache else []

    def _donki_cme_summary(self) -> Dict[str, Any]:
        return _donki_cme_summary()

    def validate_reading(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Validate each field against physical ranges, returning dict of field->issue."""
        issues: Dict[str, str] = {}
        for field, (lo, hi) in PHYSICAL_RANGES.items():
            val = data.get(field)
            if val is None:
                issues[field] = "missing"
            elif not (lo <= float(val) <= hi):
                issues[field] = f"out_of_range[{lo},{hi}]"
        return issues

    def interpolate_gaps(self, df: pd.DataFrame, threshold_minutes: int = INTERPOLATION_THRESHOLD_MINUTES) -> pd.DataFrame:
        """Fill small gaps in time-series via forward/backward linear interpolation."""
        if df.empty:
            return df
        df = df.sort_values("timestamp").copy()
        numeric_cols = [c for c in PHYSICAL_RANGES if c in df.columns]
        df.set_index("timestamp", inplace=True)
        min_interval = pd.Timedelta(minutes=1)
        expected = pd.date_range(df.index.min(), df.index.max(), freq=min_interval, tz=df.index.tz)
        df = df.reindex(expected)
        df[numeric_cols] = df[numeric_cols].interpolate(method="time", limit=int(threshold_minutes))
        df[numeric_cols] = df[numeric_cols].ffill(limit=int(threshold_minutes)).bfill(limit=int(threshold_minutes))
        df["is_interpolated"] = df.get("is_interpolated", False)
        df["is_interpolated"] = df["is_interpolated"].fillna(False)
        df["is_interpolated"] = df["is_interpolated"] | df[numeric_cols].isna().any(axis=1)
        df.reset_index(names="timestamp", inplace=True)
        return df

    def compute_quality_flag(self, data: Dict[str, Any]) -> str:
        """Compute data quality: GOOD / DEGRADED / BAD."""
        critical = ["bz_gsm", "sw_speed_kmps", "kp_current"]
        optional = ["by_gsm", "bx_gsm", "bt_total", "proton_density_ccm", "proton_temp_K", "xray_flux_Wm2"]
        if any(data.get(f) is None for f in critical):
            return "BAD"
        issues = self.validate_reading(data)
        if any(f in issues for f in critical):
            return "BAD"
        if any(data.get(f) is None for f in optional):
            return "DEGRADED"
        return "GOOD"

    def fetch_noaa_bundle(self) -> Dict[str, Any]:
        """Fetch all NOAA sources, merge with hemi_plasma_proxy, compute quality."""
        mag = self._fetch_dscovr_mag()
        kp = self._fetch_kp()
        xr = self._fetch_xray()
        cme_event = self._donki_cme_summary()
        flare_events = self._fetch_solar_flares()
        alerts = self._fetch_alerts()

        kp_val = kp.get("kp_current")
        bz = mag.get("bz_gsm")
        bt = mag.get("bt_total")

        if kp_val is None:
            kp_val = 2.0
        if bz is None:
            bz = 0.0
        if bt is None:
            bt = 5.0

        v, n, t = hemi_plasma_proxy(kp_val, bz, bt)

        cme = cme_event or {}
        flare = flare_events[-1] if flare_events else {}

        merged: Dict[str, Any] = {
            "bz_gsm": mag.get("bz_gsm"),
            "by_gsm": mag.get("by_gsm"),
            "bx_gsm": mag.get("bx_gsm"),
            "bt_total": mag.get("bt_total"),
            "sw_speed_kmps": v,
            "proton_density_ccm": n,
            "proton_temp_K": t,
            "xray_flux_Wm2": xr.get("xray_flux_Wm2"),
            "kp_current": kp_val,
            "xray_class": xray_class_from_flux(xr.get("xray_flux_Wm2")),
            "cme_id": cme.get("cme_id"),
            "cme_speed_kmps": cme.get("cme_speed_kmps"),
            "cme_earth_directed": cme.get("cme_earth_directed", False),
            "flare_id": flare.get("flrID"),
            "flare_class": flare.get("classType"),
            "flare_earth_directed": bool("earth" in str(flare.get("note", "")).lower()),
            "alerts": alerts[-10:] if alerts else [],
            "timestamp": datetime.now(timezone.utc),
            "source": "NOAA_SWPC_JSON",
        }

        merged["quality_flag"] = self.compute_quality_flag(merged)
        merged["warning_minutes"] = warning_minutes_from_speed(merged.get("sw_speed_kmps"))
        return merged

    def _persist_reading(self, reading: Dict[str, Any]) -> SolarWindData:
        """Write a single reading to the database."""
        rec = SolarWindData(
            timestamp=reading["timestamp"],
            source=reading["source"],
            bz_gsm=reading.get("bz_gsm"),
            by_gsm=reading.get("by_gsm"),
            bx_gsm=reading.get("bx_gsm"),
            bt_total=reading.get("bt_total"),
            sw_speed_kmps=reading.get("sw_speed_kmps"),
            proton_density_ccm=reading.get("proton_density_ccm"),
            proton_temp_K=reading.get("proton_temp_K"),
            xray_flux_Wm2=reading.get("xray_flux_Wm2"),
            kp_current=reading.get("kp_current"),
            quality_flag=reading.get("quality_flag", "UNKNOWN"),
            is_interpolated=reading.get("is_interpolated", False),
        )
        session = self.Session()
        try:
            session.add(rec)
            session.commit()
            session.refresh(rec)
            return rec
        finally:
            session.close()

    def _push_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Push latest snapshot to WebSocket dashboard."""
        try:
            from app.realtime import push_dashboard_snapshot
            push_dashboard_snapshot()
        except Exception as e:
            logger.debug("Snapshot push skipped: %s", e)

    def get_warning_minutes(self, sw_speed: Optional[float] = None) -> float:
        """Return L1 warning time in minutes for current or given solar wind speed."""
        if sw_speed is None:
            snap = self.get_latest_snapshot()
            sw_speed = snap.get("sw_speed_kmps")
        return warning_minutes_from_speed(sw_speed)

    def ingest_data(self) -> Dict[str, Any]:
        """Main ingestion cycle: fetch → validate → interpolate → store → snapshot → push."""
        logger.info("Ingestion cycle start")
        try:
            merged = self.fetch_noaa_bundle()
        except Exception as e:
            logger.error("NOAA bundle failed, simulated fallback: %s", e)
            merged = _simulated_row()
            merged["timestamp"] = datetime.now(timezone.utc)
            merged["source"] = "SIMULATED"
            merged["quality_flag"] = self.compute_quality_flag(merged)
            merged.setdefault("is_interpolated", False)

        issues = self.validate_reading(merged)
        if issues:
            logger.warning("Validation issues: %s", issues)

        self._latest_snapshot = merged.copy()
        self._persist_reading(merged)
        self._push_snapshot(merged)
        logger.info("Ingestion cycle complete")
        return self._latest_snapshot

    def get_latest_snapshot(self) -> Dict[str, Any]:
        return dict(self._latest_snapshot) if self._latest_snapshot else {}

    def get_latest_dataframe(self) -> pd.DataFrame:
        snap = self.get_latest_snapshot()
        if not snap:
            return pd.DataFrame()
        row = {k: snap.get(k) for k in [
            "timestamp", "bz_gsm", "by_gsm", "bx_gsm", "bt_total",
            "sw_speed_kmps", "proton_density_ccm", "proton_temp_K",
            "xray_flux_Wm2", "kp_current",
        ]}
        row["data_quality_flag"] = snap.get("quality_flag", "UNKNOWN")
        return pd.DataFrame([row])

    def get_historical_data(self, hours: int = 48) -> pd.DataFrame:
        session = self.Session()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            q = (
                session.query(SolarWindData)
                .filter(SolarWindData.timestamp >= cutoff)
                .order_by(SolarWindData.timestamp.asc())
                .all()
            )
            df = pd.DataFrame(
                [
                    {
                        "timestamp": r.timestamp,
                        "bz_gsm": r.bz_gsm,
                        "by_gsm": r.by_gsm,
                        "bx_gsm": r.bx_gsm,
                        "bt_total": r.bt_total,
                        "sw_speed_kmps": r.sw_speed_kmps,
                        "proton_density_ccm": r.proton_density_ccm,
                        "proton_temp_K": r.proton_temp_K,
                        "xray_flux_Wm2": r.xray_flux_Wm2,
                        "kp_current": r.kp_current,
                        "quality_flag": r.quality_flag,
                        "is_interpolated": r.is_interpolated,
                    }
                    for r in q
                ]
            )
            if df.empty:
                return df
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            df = df.dropna(subset=["timestamp"])
            df = self.interpolate_gaps(df)
            if "quality_flag" in df.columns:
                df["data_quality_flag"] = df["quality_flag"]
            else:
                df["data_quality_flag"] = "UNKNOWN"
            return df
        finally:
            session.close()

    def start_scheduler(self, interval_seconds: int = 60):
        from apscheduler.schedulers.background import BackgroundScheduler

        if self._scheduler is not None:
            return
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self.ingest_data,
            "interval",
            seconds=interval_seconds,
            id="data_ingestion",
            name="NAKSHATRA L1 Ingestion",
            misfire_grace_time=30,
            coalesce=True,
        )
        self._scheduler.start()
        self.ingest_data()

    def stop_scheduler(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def trigger_now(self) -> Dict[str, Any]:
        """Manually trigger an immediate ingestion cycle."""
        return self.ingest_data()


def _donki_cme_summary() -> Dict[str, Any]:
    """Fetch DONKI CME summary (last 7 days, Earth-directed only)."""
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    try:
        start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        end = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"{DONKI_CME}?startDate={start}&endDate={end}&api_key={key}"
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return {}
        events = r.json() if isinstance(r.json(), list) else []
        earth = [e for e in events if e.get("isEarthGB") or "earth" in str(e.get("note", "")).lower()]
        pick = earth[-1] if earth else (events[-1] if events else None)
        if not pick:
            return {}
        return {
            "cme_id": pick.get("activityID"),
            "cme_speed_kmps": _f(pick.get("speed")),
            "cme_earth_directed": bool(pick.get("isEarthGB")),
        }
    except Exception as e:
        logger.warning("DONKI CME fetch failed: %s", e)
        return {}


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_alert_time(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.utcnow() - timedelta(days=30)


def _simulated_row() -> Dict[str, Any]:
    rng = np.random.default_rng()
    kp = float(rng.uniform(1.5, 4.5))
    bz = float(rng.uniform(-6, 4))
    bt = float(rng.uniform(4, 14))
    by = float(rng.uniform(-4, 4))
    v, n, t = hemi_plasma_proxy(kp, bz, bt)
    return {
        "bz_gsm": bz,
        "by_gsm": by,
        "bx_gsm": float(rng.uniform(-8, 8)),
        "bt_total": bt,
        "sw_speed_kmps": v,
        "proton_density_ccm": n,
        "proton_temp_K": t,
        "xray_flux_Wm2": float(10 ** rng.uniform(-7.5, -6.5)),
        "kp_current": kp,
        "xray_class": xray_class_from_flux(1e-7),
        "cme_id": None,
        "cme_speed_kmps": None,
        "cme_earth_directed": False,
        "flare_id": None,
        "flare_class": None,
        "flare_earth_directed": False,
        "alerts": [],
    }


_ingestion_service: Optional[DataIngestionService] = None


def get_ingestion_service() -> DataIngestionService:
    global _ingestion_service
    if _ingestion_service is None:
        _ingestion_service = DataIngestionService()
    return _ingestion_service
