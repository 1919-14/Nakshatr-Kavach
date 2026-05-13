// NAKSHATRA-KAVACH — All Custom Hooks

import { useEffect, useRef, useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { useStormStore } from "../store/useStormStore";
import {
  MOCK_SOLAR_WIND, MOCK_KP_FORECAST, MOCK_SATELLITES,
  MOCK_GRID_CORRIDORS, MOCK_ADVISORY,
} from "../mock/mockData";
import { normalizeKpForecast, buildKpChartData, normalizeAdvisory } from "../utils/apiNormalize";

const USE_MOCK = import.meta.env.VITE_USE_MOCK_DATA !== "false";
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

async function ensureSolarLive() {
  try {
    const { data } = await axios.get(`${API_BASE}/api/solar/live`, { timeout: 25000 });
    return data;
  } catch (e) {
    if (e.response?.status === 503) {
      await axios.post(`${API_BASE}/api/v1/trigger`, {}, { timeout: 60000 });
      const { data } = await axios.get(`${API_BASE}/api/solar/live`, { timeout: 25000 });
      return data;
    }
    const { data } = await axios.get(`${API_BASE}/api/solar-wind`, { timeout: 25000 });
    return data;
  }
}

// ── useSolarData ──────────────────────────────────────────────────────────────
export function useSolarData() {
  const { setSolarWind, tickMockData, setSystemStatus } = useStormStore();

  useEffect(() => {
    if (!USE_MOCK) return;
    const t = setInterval(tickMockData, 3000);
    return () => clearInterval(t);
  }, [tickMockData]);

  return useQuery({
    queryKey: ["solar-wind"],
    queryFn: async () => {
      if (USE_MOCK) return MOCK_SOLAR_WIND;
      const data = await ensureSolarLive();
      setSolarWind(data);
      const q = data?.data_quality || data?.quality;
      setSystemStatus((s) => ({
        ...s,
        noaa: q === "BAD" ? "offline" : q === "DEGRADED" ? "degraded" : "online",
      }));
      return data;
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

// ── useKpForecast ─────────────────────────────────────────────────────────────
export function useKpForecast() {
  const { setKpForecast, setKpChartData } = useStormStore();
  return useQuery({
    queryKey: ["kp-forecast"],
    queryFn: async () => {
      if (USE_MOCK) {
        setKpForecast(MOCK_KP_FORECAST);
        return MOCK_KP_FORECAST;
      }
      const { data } = await axios.get(`${API_BASE}/api/kp/forecast`, { timeout: 30000 });
      const norm = normalizeKpForecast(data);
      setKpForecast(norm);
      setKpChartData(buildKpChartData(data));
      return norm;
    },
    refetchInterval: 60_000,
  });
}

// ── useSatelliteRisk ──────────────────────────────────────────────────────────
export function useSatelliteRisk() {
  const { setSatellites } = useStormStore();
  return useQuery({
    queryKey: ["satellite-risk"],
    queryFn: async () => {
      if (USE_MOCK) return MOCK_SATELLITES;
      const { data } = await axios.get(`${API_BASE}/api/satellites/risk`, { timeout: 30000 });
      setSatellites(data);
      return data;
    },
    refetchInterval: 60_000,
  });
}

// ── useGridRisk ───────────────────────────────────────────────────────────────
export function useGridRisk() {
  const { setGridCorridors } = useStormStore();
  return useQuery({
    queryKey: ["grid-risk"],
    queryFn: async () => {
      if (USE_MOCK) return MOCK_GRID_CORRIDORS;
      const { data } = await axios.get(`${API_BASE}/api/grid/risk`, { timeout: 30000 });
      setGridCorridors(data);
      return data;
    },
    refetchInterval: 60_000,
  });
}

// ── useAdvisory ───────────────────────────────────────────────────────────────
export function useAdvisory() {
  const { setAdvisory, setSystemStatus } = useStormStore();
  return useQuery({
    queryKey: ["advisory"],
    queryFn: async () => {
      if (USE_MOCK) return MOCK_ADVISORY;
      const { data } = await axios.get(`${API_BASE}/api/advisory/latest`, { timeout: 120000 });
      const norm = normalizeAdvisory(data);
      setAdvisory(norm);
      setSystemStatus((s) => ({
        ...s,
        llm: norm.source === "RULE_BASED" ? "degraded" : "online",
      }));
      return norm;
    },
    refetchInterval: 300_000,
  });
}

// ── useShapExplain ─────────────────────────────────────────────────────────────
export function useShapExplain() {
  const { setShapExplain } = useStormStore();
  return useQuery({
    queryKey: ["shap-explain"],
    queryFn: async () => {
      if (USE_MOCK) {
        const mock = {
          method: "demo",
          features: [
            { feature: "bz_southward_duration_1h", shap_value: 0.42, physics_note: "Southward IMF coupling" },
            { feature: "epsilon_coupling", shap_value: 0.21, physics_note: "Solar wind–IMF energy flux" },
            { feature: "sw_dynamic_pressure", shap_value: 0.11, physics_note: "Ram pressure" },
          ],
        };
        setShapExplain(mock);
        return mock;
      }
      const { data } = await axios.get(`${API_BASE}/api/shap/explain`, { timeout: 60000 });
      setShapExplain(data);
      return data;
    },
    refetchInterval: 120_000,
  });
}

// ── useSocket ─────────────────────────────────────────────────────────────────
export function useSocket() {
  const {
    setSolarWind,
    setSatellites,
    setAdvisory,
    setKpForecast,
    setKpChartData,
    setGridCorridors,
    setShapExplain,
  } = useStormStore();
  const socketRef = useRef(null);

  useEffect(() => {
    if (USE_MOCK) return;
    import("socket.io-client").then(({ io }) => {
      const s = io(API_BASE, { transports: ["websocket", "polling"] });
      socketRef.current = s;
      s.on("solar_wind_update", setSolarWind);
      s.on("satellite_update", setSatellites);
      s.on("advisory_update", setAdvisory);
      s.on("dashboard_update", (payload) => {
        if (!payload) return;
        if (payload.solar_wind) setSolarWind(payload.solar_wind);
        if (payload.satellites) setSatellites(payload.satellites);
        if (payload.grid) setGridCorridors(payload.grid);
        if (payload.shap) setShapExplain(payload.shap);
        if (payload.kp_forecast) {
          setKpForecast(normalizeKpForecast(payload.kp_forecast));
          setKpChartData(buildKpChartData(payload.kp_forecast));
        }
      });
    });
    return () => {
      socketRef.current?.disconnect();
      socketRef.current = null;
    };
  }, [
    setSolarWind,
    setSatellites,
    setAdvisory,
    setKpForecast,
    setKpChartData,
    setGridCorridors,
    setShapExplain,
  ]);

  return socketRef.current;
}

// ── useTypewriter ─────────────────────────────────────────────────────────────
export function useTypewriter(text, speed = 18, enabled = true) {
  const [displayed, setDisplayed]   = useState("");
  const [isDone, setIsDone]         = useState(false);
  const intervalRef                 = useRef(null);
  const indexRef                    = useRef(0);
  const currentTextRef              = useRef(text);

  const start = useCallback((newText) => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    currentTextRef.current = newText;
    indexRef.current = 0;
    setDisplayed("");
    setIsDone(false);

    intervalRef.current = setInterval(() => {
      indexRef.current++;
      const slice = currentTextRef.current.slice(0, indexRef.current);
      setDisplayed(slice);
      if (indexRef.current >= currentTextRef.current.length) {
        clearInterval(intervalRef.current);
        setIsDone(true);
      }
    }, speed);
  }, [speed]);

  useEffect(() => {
    if (!enabled || !text) return;
    const delay = isDone ? 0 : 200;
    const t = setTimeout(() => start(text), delay);
    return () => clearTimeout(t);
  }, [text, enabled, start, isDone]);

  useEffect(() => () => clearInterval(intervalRef.current), []);

  return { displayed, isDone };
}

// ── useAnimatedNumber ─────────────────────────────────────────────────────────
export function useAnimatedNumber(target, duration = 800) {
  const [value, setValue] = useState(target);
  const prevRef           = useRef(target);
  const rafRef            = useRef(null);

  useEffect(() => {
    const start     = prevRef.current;
    const end       = target;
    const startTime = performance.now();

    const animate = (now) => {
      const elapsed  = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased    = 1 - Math.pow(1 - progress, 3);
      setValue(parseFloat((start + (end - start) * eased).toFixed(1)));
      if (progress < 1) rafRef.current = requestAnimationFrame(animate);
      else prevRef.current = end;
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);

  return value;
}

// ── useCountdown (seconds remaining) ─────────────────────────────────────────
export function useCountdown(totalSeconds) {
  const [remaining, setRemaining] = useState(totalSeconds);

  useEffect(() => {
    setRemaining(totalSeconds);
    const t = setInterval(() => {
      setRemaining(r => Math.max(0, r - 1));
    }, 1000);
    return () => clearInterval(t);
  }, [totalSeconds]);

  return remaining;
}

// ── useNowIST ─────────────────────────────────────────────────────────────────
export function useNowIST() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const ist = new Date(now.getTime() + 5.5 * 60 * 60 * 1000);
      setTime(ist.toISOString().slice(11, 19) + " IST");
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);
  return time;
}
