// NAKSHATRA-KAVACH — All Custom Hooks

import { useEffect, useRef, useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { useStormStore } from "../store/useStormStore";
import {
  MOCK_SOLAR_WIND, MOCK_KP_FORECAST, MOCK_SATELLITES,
  MOCK_GRID_CORRIDORS, MOCK_ADVISORY,
} from "../mock/mockData";
import {
  normalizeKpForecast,
  buildKpChartData,
  buildKpChartDataWithHistory,
  normalizeAdvisory,
  normalizeSolarSnapshot,
  normalizeSatelliteRisks,
  normalizeGridRisks,
  normalizeShapExplain,
} from "../utils/apiNormalize";

const USE_MOCK = import.meta.env.VITE_USE_MOCK_DATA === "true";
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

async function ensureSolarLive() {
  try {
    const { data } = await axios.get(`${API_BASE}/api/solar/live?allow_stale=true`, { timeout: 25000 });
    return data;
  } catch (e) {
    if (e.response?.status === 503) {
      const { data } = await axios.get(`${API_BASE}/api/solar/status`, { timeout: 25000 });
      return data;
    }
    const { data } = await axios.get(`${API_BASE}/api/solar/status`, { timeout: 25000 });
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
      const norm = normalizeSolarSnapshot(data);
      setSolarWind(norm);
      const q = norm?.data_quality || norm?.quality;
      setSystemStatus((s) => ({
        ...s,
        noaa: q === "BAD" ? "offline" : ["DEGRADED", "STALE", "PARTIAL", "UNKNOWN"].includes(q) ? "degraded" : "online",
      }));
      return norm;
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
      let history = null;
      try {
        const res = await axios.get(`${API_BASE}/api/kp/history?hours=24`, { timeout: 15000 });
        history = res.data;
      } catch {
        history = null;
      }
      const norm = normalizeKpForecast(data);
      setKpForecast(norm);
      setKpChartData(buildKpChartDataWithHistory(data, history));
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
      const norm = normalizeSatelliteRisks(data);
      setSatellites(norm);
      axios.get(`${API_BASE}/api/satellites/tle`, { timeout: 45000 })
        .then((tleResult) => setSatellites(normalizeSatelliteRisks(data, tleResult.data)))
        .catch((error) => console.warn("Live TLE fetch failed; keeping catalog positions", error));
      return norm;
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
      const norm = normalizeGridRisks(data);
      setGridCorridors(norm);
      return norm;
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
      let { data } = await axios.get(`${API_BASE}/api/advisory/latest`, { timeout: 120000 });
      let norm = normalizeAdvisory(data);

      if (norm?.source === "RULE_BASED") {
        try {
          const { data: status } = await axios.get(`${API_BASE}/api/advisory/status`, { timeout: 30000 });
          if (status?.groq_api_available) {
            const generated = await axios.post(
              `${API_BASE}/api/advisory/generate`,
              { trigger_type: "MANUAL_REFRESH" },
              { timeout: 120000 }
            );
            data = generated.data;
            norm = normalizeAdvisory(data);
          }
        } catch (error) {
          console.warn("Groq advisory auto-generation unavailable; keeping latest advisory", error);
        }
      }

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
      const { data } = await axios.get(`${API_BASE}/api/kp/shap`, { timeout: 60000 });
      const norm = normalizeShapExplain(data);
      setShapExplain(norm);
      return norm;
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
    setReplayMode,
    setReplayStatus,
    setReplayFrame,
  } = useStormStore();
  const socketRef = useRef(null);

  useEffect(() => {
    const hydrateReplayFrame = async (frame) => {
      if (!frame) return;
      setReplayMode(Boolean(frame.is_historical));
      setReplayFrame(frame);
      setSolarWind((current) => ({
        ...(current || {}),
        timestamp: frame.storm_timestamp,
        kp_current: frame.kp_current,
        storm_class: frame.storm_class,
        is_historical: Boolean(frame.is_historical),
        replay_data_type: frame.replay_data_type,
      }));
      if (frame.advisory) setAdvisory(normalizeAdvisory(frame.advisory));

      try {
        const { data } = await axios.get(`${API_BASE}/api/replay/frame/${frame.frame_index}`, { timeout: 30000 });
        const output = data?.output || {};
        if (output.solar) {
          setSolarWind(normalizeSolarSnapshot({
            ...output.solar,
            replay_data_type: frame.replay_data_type,
          }));
        }
        if (output.kp_forecast) {
          setKpForecast(normalizeKpForecast(output.kp_forecast));
          setKpChartData(buildKpChartData(output.kp_forecast));
        }
        if (output.satellite_risks) setSatellites(normalizeSatelliteRisks(output.satellite_risks));
        if (output.grid_risks) setGridCorridors(normalizeGridRisks(output.grid_risks));
        if (output.advisory) setAdvisory(normalizeAdvisory(output.advisory));
        if (output.shap) setShapExplain(normalizeShapExplain(output.shap));
      } catch (error) {
        if (error.response?.status !== 404) {
          console.warn("Replay frame hydration failed", error);
        }
      }
    };

    import("socket.io-client").then(({ io }) => {
      const s = io(API_BASE, { transports: ["websocket", "polling"] });
      socketRef.current = s;
      s.on("solar_wind_update", (payload) => {
        if (!USE_MOCK) setSolarWind(normalizeSolarSnapshot(payload));
      });
      s.on("satellite_update", (payload) => {
        if (!USE_MOCK) setSatellites(normalizeSatelliteRisks(payload));
      });
      s.on("advisory_update", (payload) => {
        if (!USE_MOCK) setAdvisory(normalizeAdvisory(payload));
      });
      s.on("dashboard_update", (payload) => {
        if (USE_MOCK) return;
        if (!payload) return;
        if (payload.solar_wind) setSolarWind(normalizeSolarSnapshot(payload.solar_wind));
        if (payload.satellites) setSatellites(normalizeSatelliteRisks(payload.satellites));
        if (payload.grid) setGridCorridors(normalizeGridRisks(payload.grid));
        if (payload.shap) setShapExplain(normalizeShapExplain(payload.shap));
        if (payload.kp_forecast) {
          setKpForecast(normalizeKpForecast(payload.kp_forecast));
          setKpChartData(buildKpChartData(payload.kp_forecast));
        }
      });
      s.on("replay_frame", hydrateReplayFrame);
      s.on("replay_frame_ready", (payload) => {
        if (payload?.status === "READY" && payload.summary) {
          hydrateReplayFrame(payload.summary);
        }
      });
      s.on("replay_state_change", (payload) => {
        setReplayStatus({
          state: payload?.new_state,
          frame: payload?.frame,
          storm_name: payload?.storm_name,
        });
      });
      s.on("replay_completed", (payload) => {
        setReplayStatus({ state: "COMPLETED", ...payload });
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
    setReplayMode,
    setReplayStatus,
    setReplayFrame,
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
      setTime(new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) + " IST");
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);
  return time;
}
