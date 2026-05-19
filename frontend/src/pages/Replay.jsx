import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { io } from "socket.io-client";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { motion } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { getStormClass } from "../utils/stormClassifier";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";
const SPEEDS = [
  { label: "1x", value: 1 },
  { label: "60x", value: 60 },
  { label: "3600x", value: 3600 },
];

export default function Replay() {
  const {
    replayState,
    replaySpeed,
    replayProgress,
    replayCurrentFrame,
    replayTotalFrames,
    setReplayMode,
    setReplayStatus,
    setReplayFrame,
    setReplaySpeed,
  } = useStormStore();

  const [catalog, setCatalog] = useState([]);
  const [selectedStormId, setSelectedStormId] = useState("");
  const [timeline, setTimeline] = useState(null);
  const [localStatus, setLocalStatus] = useState({ state: "IDLE" });
  const [loading, setLoading] = useState(false);
  const [computingSeek, setComputingSeek] = useState(false);
  const [error, setError] = useState("");
  const [validationPoints, setValidationPoints] = useState([]);
  const [validationMetrics, setValidationMetrics] = useState({});
  const scrubberRef = useRef(null);

  const selectedStorm = useMemo(
    () => catalog.find((storm) => storm.storm_id === selectedStormId) || null,
    [catalog, selectedStormId]
  );

  const currentFrame = replayCurrentFrame || localStatus.current_frame || 0;
  const totalFrames = replayTotalFrames || timeline?.total_frames || localStatus.total_frames || 0;
  const progress = replayProgress || localStatus.progress_pct || 0;
  const currentKp = validationPoints.length
    ? validationPoints[validationPoints.length - 1].actual
    : selectedStorm?.thumbnail_kp ?? 0;
  const stormClass = getStormClass(currentKp);

  const timelineGradient = useMemo(() => {
    const profile = timeline?.kp_profile || [];
    if (!profile.length) return "linear-gradient(90deg,#4CAF50,#CDDC39,#F44336,#9C27B0)";
    if (profile.length === 1) return profile[0].color;
    return `linear-gradient(90deg, ${profile
      .map((point, index) => `${point.color} ${Math.round((index / (profile.length - 1)) * 100)}%`)
      .join(", ")})`;
  }, [timeline]);

  const fetchStatus = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API_BASE}/api/replay/status`, { timeout: 10000 });
      setLocalStatus(data);
      setReplayStatus(data);
    } catch {
      // The page remains usable while the backend is starting.
    }
  }, [setReplayStatus]);

  useEffect(() => {
    let active = true;
    async function loadCatalog() {
      setLoading(true);
      setError("");
      try {
        const { data } = await axios.get(`${API_BASE}/api/replay/catalog`, { timeout: 20000 });
        if (!active) return;
        const storms = data?.storms || [];
        setCatalog(storms);
        if (storms.length) setSelectedStormId(storms.find((storm) => storm.storm_id === "2024_may_g5")?.storm_id || storms[0].storm_id);
      } catch (err) {
        if (active) setError(err.response?.data?.error || `${err.message || "Replay catalog unavailable"} - backend is unreachable at ${API_BASE}`);
      } finally {
        if (active) setLoading(false);
      }
    }
    loadCatalog();
    fetchStatus();
    const statusTimer = setInterval(fetchStatus, 500);
    return () => {
      active = false;
      clearInterval(statusTimer);
    };
  }, [fetchStatus]);

  useEffect(() => {
    const socket = io(API_BASE, { transports: ["websocket", "polling"] });
    const recordValidation = (frame) => {
      if (!frame?.validation) return;
      const validation = frame.validation;
      const point = {
        frame: frame.frame_index,
        label: frame.storm_timestamp?.slice(5, 16)?.replace("T", " ") || String(frame.frame_index),
        predicted: Number(validation.predicted_kp_3hr ?? 0),
        actual: Number(validation.actual_kp_now ?? frame.kp_current ?? 0),
      };
      setValidationPoints((points) => [...points.slice(-179), point]);
    };

    socket.on("replay_frame", (frame) => {
      setComputingSeek(false);
      setReplayFrame(frame);
      setLocalStatus((status) => ({
        ...status,
        state: "PLAYING",
        current_frame: frame.frame_index,
        total_frames: frame.total_frames,
        progress_pct: frame.progress_pct,
        speed: frame.speed,
      }));
      recordValidation(frame);
    });
    socket.on("replay_frame_ready", (payload) => {
      setComputingSeek(false);
      if (payload?.status === "READY" && payload.summary) {
        setReplayFrame(payload.summary);
        recordValidation(payload.summary);
      } else if (payload?.status === "ERROR") {
        setError(payload.error || "Seek frame computation failed");
      }
    });
    socket.on("replay_validation_update", setValidationMetrics);
    socket.on("replay_completed", (payload) => {
      setLocalStatus((status) => ({ ...status, state: "COMPLETED" }));
      setReplayStatus({ state: "COMPLETED", ...payload });
      if (payload?.validation_summary) setValidationMetrics(payload.validation_summary);
    });
    socket.on("replay_state_change", (payload) => {
      setReplayStatus({
        state: payload?.new_state,
        frame: payload?.frame,
        storm_name: payload?.storm_name,
      });
    });
    return () => socket.disconnect();
  }, [setReplayFrame, setReplayStatus]);

  const loadStorm = useCallback(async (stormId) => {
    if (!stormId) return;
    setLoading(true);
    setComputingSeek(false);
    setError("");
    setValidationPoints([]);
    setValidationMetrics({});
    try {
      const loadResponse = await axios.post(`${API_BASE}/api/replay/load`, { storm_id: stormId }, { timeout: 30000 });
      const timelineResponse = await axios.get(`${API_BASE}/api/replay/timeline/${stormId}`, { timeout: 30000 });
      setTimeline(timelineResponse.data);
      setLocalStatus(loadResponse.data);
      setReplayStatus(loadResponse.data);
      setReplayMode(true);
    } catch (err) {
      setError(err.response?.data?.error || `${err.message || "Storm load failed"} - check that the backend replay API is running at ${API_BASE}`);
    } finally {
      setLoading(false);
    }
  }, [setReplayMode, setReplayStatus]);

  useEffect(() => {
    if (selectedStormId) loadStorm(selectedStormId);
  }, [selectedStormId, loadStorm]);

  const play = async () => {
    setError("");
    const { data } = await axios.post(`${API_BASE}/api/replay/play`, { speed: replaySpeed }, { timeout: 15000 });
    setLocalStatus(data);
    setReplayStatus({ ...data, storm_name: selectedStorm?.name });
  };

  const pause = async () => {
    setError("");
    const { data } = await axios.post(`${API_BASE}/api/replay/pause`, {}, { timeout: 15000 });
    setLocalStatus((status) => ({ ...status, ...data }));
    setReplayStatus(data);
  };

  const resume = async () => {
    setError("");
    const { data } = await axios.post(`${API_BASE}/api/replay/resume`, { speed: replaySpeed }, { timeout: 15000 });
    setLocalStatus((status) => ({ ...status, ...data }));
    setReplayStatus(data);
  };

  const stop = async () => {
    setError("");
    const { data } = await axios.post(`${API_BASE}/api/replay/stop`, {}, { timeout: 15000 });
    setLocalStatus(data);
    setReplayStatus(data);
    setReplayMode(false);
    setComputingSeek(false);
  };

  const changeSpeed = async (value) => {
    const speedValue = Number(value);
    setReplaySpeed(speedValue);
    setError("");
    try {
      const { data } = await axios.post(`${API_BASE}/api/replay/speed`, { speed: speedValue }, { timeout: 10000 });
      setReplayStatus(data);
    } catch (err) {
      setError(err.response?.data?.error || err.message || "Speed change failed");
    }
  };

  const seekToProgress = async (event) => {
    if (!scrubberRef.current || !timeline) return;
    const rect = scrubberRef.current.getBoundingClientRect();
    const pct = Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100));
    setError("");
    setComputingSeek(true);
    try {
      if ((localStatus.state || replayState) === "PLAYING") {
        await axios.post(`${API_BASE}/api/replay/pause`, {}, { timeout: 15000 });
      }
      const { data } = await axios.post(`${API_BASE}/api/replay/seek`, { progress_pct: pct }, { timeout: 15000 });
      setLocalStatus((status) => ({
        ...status,
        state: "PAUSED",
        current_frame: data.frame,
        progress_pct: pct,
      }));
      setReplayStatus({ state: "PAUSED", frame: data.frame, progress_pct: pct });
    } catch (err) {
      setComputingSeek(false);
      setError(err.response?.data?.error || err.message || "Seek failed");
    }
  };

  return (
    <div style={pageStyle}>
      <style>{`@keyframes replaySpin { to { transform: rotate(360deg); } }`}</style>
      <div style={bannerStyle}>
        <div>
          <div style={eyebrowStyle}>LAYER 7 HISTORICAL STORM REPLAY</div>
          <h1 style={titleStyle}>Replay Engine</h1>
        </div>
        <div style={badgeStyle}>
          REPLAY {selectedStorm?.data_type || "DEMO"} DATA
        </div>
      </div>

      {error && <div style={errorStyle}>{error}</div>}

      <section style={topSectionStyle}>
        <label style={labelStyle}>
          Storm
          <select
            value={selectedStormId}
            onChange={(event) => setSelectedStormId(event.target.value)}
            style={selectStyle}
            disabled={loading}
          >
            {catalog.map((storm) => (
              <option key={storm.storm_id} value={storm.storm_id}>
                {storm.short_name || storm.name}
              </option>
            ))}
          </select>
        </label>

        <div style={statusGridStyle}>
          <StatusTile label="State" value={loading ? "LOADING" : localStatus.state || replayState} color="#FF9800" />
          <StatusTile label="Frame" value={`${currentFrame} / ${totalFrames || "-"}`} color="#00D4FF" />
          <StatusTile label="Progress" value={`${Number(progress || 0).toFixed(1)}%`} color="#4CAF50" />
          <StatusTile label="Storm" value={selectedStorm?.storm_class || "-"} color={selectedStorm?.display_color || "#9C27B0"} />
        </div>
      </section>

      <section style={middleGridStyle}>
        <div style={panelStyle}>
          <div style={panelHeaderStyle}>
            <div>
              <div style={panelTitleStyle}>Timeline Scrubber</div>
              <div style={panelSubStyle}>{timeline?.duration_str || "Load a storm to initialize timeline"}</div>
            </div>
            <div style={{ color: stormClass.color, fontFamily: "Orbitron,sans-serif", fontWeight: 800 }}>
              Kp {Number(currentKp || 0).toFixed(1)}
            </div>
          </div>

          <div ref={scrubberRef} onClick={seekToProgress} style={scrubberStyle}>
            <div style={{ ...scrubberGradientStyle, background: timelineGradient }} />
            {(timeline?.moment_markers || []).map((marker) => (
              <div key={`${marker.frame}-${marker.event}`} style={{ ...markerStyle, left: `${marker.pct}%` }}>
                <div style={flagStyle} />
                <div style={markerTooltipStyle}>{marker.event}</div>
              </div>
            ))}
            <motion.div
              animate={{ left: `${Math.min(100, Math.max(0, progress || 0))}%` }}
              transition={{ type: "spring", stiffness: 140, damping: 18 }}
              style={playheadStyle}
            />
          </div>

          <div style={timelineFooterStyle}>
            <span>{timeline?.storm_name || selectedStorm?.name || "No storm loaded"}</span>
            <span>{localStatus.current_timestamp || selectedStorm?.dates || ""}</span>
          </div>

          <div style={controlRowStyle}>
            <button onClick={play} disabled={loading || localStatus.state === "PLAYING"} style={primaryButtonStyle}>
              Play
            </button>
            <button onClick={pause} disabled={localStatus.state !== "PLAYING"} style={buttonStyle}>
              Pause
            </button>
            <button onClick={resume} disabled={localStatus.state !== "PAUSED"} style={buttonStyle}>
              Resume
            </button>
            <button onClick={stop} style={dangerButtonStyle}>
              Stop
            </button>
          </div>

          {computingSeek && (
            <div style={spinnerRowStyle}>
              <span style={spinnerStyle} />
              Computing selected frame...
            </div>
          )}
        </div>

        <div style={panelStyle}>
          <div style={panelHeaderStyle}>
            <div>
              <div style={panelTitleStyle}>Validation Metrics</div>
              <div style={panelSubStyle}>Predicted Kp vs actual historical Kp</div>
            </div>
            <div style={metricPillStyle}>
              RMSE {validationMetrics?.rmse ?? validationMetrics?.rmse_3hr ?? "-"}
            </div>
          </div>

          <div style={{ height: 280 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={validationPoints}>
                <CartesianGrid stroke="rgba(255,255,255,0.08)" />
                <XAxis dataKey="label" tick={{ fill: "#78909C", fontSize: 10 }} minTickGap={28} />
                <YAxis yAxisId="left" domain={[0, 9]} tick={{ fill: "#78909C", fontSize: 10 }} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 9]} tick={{ fill: "#78909C", fontSize: 10 }} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#E8F4FD" }} />
                <Legend />
                <Line yAxisId="left" type="monotone" dataKey="predicted" name="Predicted Kp" stroke="#42A5F5" strokeWidth={2} dot={false} />
                <Line yAxisId="right" type="monotone" dataKey="actual" name="Actual Kp" stroke="#FF9800" strokeWidth={2} dot={false} strokeDasharray="5 4" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div style={metricsRowStyle}>
            <StatusTile label="MAE" value={validationMetrics?.mae ?? validationMetrics?.mae_3hr ?? "-"} color="#42A5F5" compact />
            <StatusTile label="Class Accuracy" value={formatPct(validationMetrics?.class_accuracy)} color="#4CAF50" compact />
            <StatusTile label="Storm Detection" value={formatPct(validationMetrics?.storm_detection_rate)} color="#FF9800" compact />
          </div>
        </div>
      </section>

      <section style={bottomSectionStyle}>
        <div>
          <div style={panelTitleStyle}>Simulation Controls</div>
          <div style={panelSubStyle}>
            Speed changes apply immediately while playback is active. Seeking computes the target frame asynchronously.
          </div>
        </div>
        <div style={speedGroupStyle}>
          {SPEEDS.map((item) => (
            <button
              key={item.value}
              onClick={() => changeSpeed(item.value)}
              style={Number(replaySpeed) === item.value ? activeSpeedStyle : speedButtonStyle}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function StatusTile({ label, value, color, compact = false }) {
  return (
    <div style={{ ...tileStyle, padding: compact ? "9px 10px" : "12px 14px", borderColor: `${color}44` }}>
      <div style={tileLabelStyle}>{label}</div>
      <div style={{ ...tileValueStyle, color }}>{value}</div>
    </div>
  );
}

function formatPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const pct = Number(value) <= 1 ? Number(value) * 100 : Number(value);
  return `${pct.toFixed(1)}%`;
}

const pageStyle = {
  minHeight: "100vh",
  background: "var(--color-bg-primary)",
  color: "#E8F4FD",
  padding: "84px 22px 26px",
  fontFamily: "Space Grotesk,sans-serif",
};

const bannerStyle = {
  display: "flex",
  justifyContent: "space-between",
  gap: 18,
  alignItems: "center",
  marginBottom: 18,
  padding: "16px 18px",
  borderRadius: 10,
  background: "linear-gradient(90deg, rgba(255,111,0,0.24), rgba(2,8,23,0.72))",
  border: "1px solid rgba(255,152,0,0.38)",
};

const eyebrowStyle = {
  fontFamily: "Orbitron,sans-serif",
  fontSize: 10,
  letterSpacing: "0.12em",
  color: "#FFB74D",
  marginBottom: 3,
};

const titleStyle = {
  margin: 0,
  fontFamily: "Orbitron,sans-serif",
  fontSize: 24,
  letterSpacing: "0.02em",
};

const badgeStyle = {
  padding: "7px 12px",
  borderRadius: 999,
  background: "rgba(255,152,0,0.18)",
  border: "1px solid rgba(255,152,0,0.55)",
  color: "#FFB74D",
  fontFamily: "Orbitron,sans-serif",
  fontSize: 11,
  fontWeight: 800,
};

const errorStyle = {
  marginBottom: 14,
  padding: "10px 12px",
  borderRadius: 8,
  background: "rgba(244,67,54,0.14)",
  border: "1px solid rgba(244,67,54,0.4)",
  color: "#FFCDD2",
};

const topSectionStyle = {
  display: "grid",
  gridTemplateColumns: "minmax(260px, 360px) 1fr",
  gap: 14,
  marginBottom: 14,
};

const labelStyle = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  color: "#90A4AE",
  fontFamily: "Orbitron,sans-serif",
  fontSize: 10,
  letterSpacing: "0.1em",
};

const selectStyle = {
  minHeight: 43,
  borderRadius: 8,
  background: "rgba(255,255,255,0.06)",
  color: "#E8F4FD",
  border: "1px solid rgba(0,212,255,0.18)",
  padding: "0 12px",
  fontFamily: "Space Grotesk,sans-serif",
  fontSize: 14,
};

const statusGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(4,minmax(0,1fr))",
  gap: 10,
};

const tileStyle = {
  background: "var(--color-bg-card)",
  border: "1px solid rgba(0,212,255,0.12)",
  borderRadius: 8,
};

const tileLabelStyle = {
  fontFamily: "Orbitron,sans-serif",
  fontSize: 9,
  color: "#607D8B",
  letterSpacing: "0.1em",
  marginBottom: 4,
};

const tileValueStyle = {
  fontFamily: "Orbitron,sans-serif",
  fontSize: 15,
  fontWeight: 800,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const middleGridStyle = {
  display: "grid",
  gridTemplateColumns: "minmax(0,1fr) minmax(360px,0.82fr)",
  gap: 14,
  marginBottom: 14,
};

const panelStyle = {
  background: "var(--color-bg-card)",
  border: "1px solid rgba(0,212,255,0.14)",
  borderRadius: 10,
  padding: 16,
  minHeight: 390,
};

const panelHeaderStyle = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 12,
  marginBottom: 18,
};

const panelTitleStyle = {
  fontFamily: "Orbitron,sans-serif",
  fontSize: 13,
  fontWeight: 800,
  color: "#00D4FF",
  letterSpacing: "0.08em",
};

const panelSubStyle = {
  fontSize: 12,
  color: "#78909C",
  marginTop: 4,
};

const scrubberStyle = {
  position: "relative",
  height: 86,
  marginTop: 10,
  cursor: "pointer",
};

const scrubberGradientStyle = {
  position: "absolute",
  left: 0,
  right: 0,
  top: 36,
  height: 14,
  borderRadius: 999,
  boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.14)",
};

const markerStyle = {
  position: "absolute",
  top: 14,
  transform: "translateX(-50%)",
};

const flagStyle = {
  width: 0,
  height: 0,
  borderLeft: "5px solid transparent",
  borderRight: "5px solid transparent",
  borderTop: "12px solid #FFB74D",
  filter: "drop-shadow(0 0 5px rgba(255,183,77,0.55))",
};

const markerTooltipStyle = {
  position: "absolute",
  top: -6,
  left: 12,
  width: 160,
  color: "#B0BEC5",
  fontSize: 10,
  lineHeight: 1.35,
  pointerEvents: "none",
};

const playheadStyle = {
  position: "absolute",
  top: 28,
  width: 4,
  height: 30,
  borderRadius: 6,
  transform: "translateX(-50%)",
  background: "#FFFFFF",
  boxShadow: "0 0 14px rgba(255,255,255,0.85)",
};

const timelineFooterStyle = {
  display: "flex",
  justifyContent: "space-between",
  color: "#78909C",
  fontSize: 11,
  fontFamily: "JetBrains Mono,monospace",
  marginTop: 4,
};

const controlRowStyle = {
  display: "flex",
  gap: 9,
  flexWrap: "wrap",
  marginTop: 18,
};

const buttonBase = {
  minHeight: 38,
  borderRadius: 8,
  padding: "0 16px",
  fontFamily: "Orbitron,sans-serif",
  fontSize: 11,
  fontWeight: 800,
  cursor: "pointer",
};

const buttonStyle = {
  ...buttonBase,
  background: "rgba(255,255,255,0.06)",
  color: "#B0BEC5",
  border: "1px solid rgba(255,255,255,0.12)",
};

const primaryButtonStyle = {
  ...buttonBase,
  background: "rgba(0,212,255,0.17)",
  color: "#00D4FF",
  border: "1px solid rgba(0,212,255,0.45)",
};

const dangerButtonStyle = {
  ...buttonBase,
  background: "rgba(244,67,54,0.14)",
  color: "#FF8A80",
  border: "1px solid rgba(244,67,54,0.38)",
};

const spinnerRowStyle = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 12,
  color: "#FFB74D",
  fontSize: 12,
};

const spinnerStyle = {
  width: 14,
  height: 14,
  borderRadius: "50%",
  border: "2px solid rgba(255,183,77,0.25)",
  borderTopColor: "#FFB74D",
  animation: "replaySpin 1s linear infinite",
};

const tooltipStyle = {
  background: "rgba(2,8,23,0.96)",
  border: "1px solid rgba(0,212,255,0.22)",
  borderRadius: 8,
  color: "#E8F4FD",
};

const metricPillStyle = {
  padding: "5px 10px",
  borderRadius: 999,
  background: "rgba(66,165,245,0.14)",
  color: "#42A5F5",
  border: "1px solid rgba(66,165,245,0.35)",
  fontFamily: "Orbitron,sans-serif",
  fontSize: 10,
  fontWeight: 800,
};

const metricsRowStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(3,minmax(0,1fr))",
  gap: 9,
  marginTop: 12,
};

const bottomSectionStyle = {
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  alignItems: "center",
  background: "var(--color-bg-card)",
  border: "1px solid rgba(0,212,255,0.14)",
  borderRadius: 10,
  padding: 16,
};

const speedGroupStyle = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const speedButtonStyle = {
  ...buttonBase,
  background: "rgba(255,255,255,0.05)",
  color: "#90A4AE",
  border: "1px solid rgba(255,255,255,0.12)",
};

const activeSpeedStyle = {
  ...buttonBase,
  background: "rgba(255,152,0,0.18)",
  color: "#FFB74D",
  border: "1px solid rgba(255,152,0,0.5)",
};
