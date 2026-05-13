import React, { memo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../../store/useStormStore";
import { getStormClass, getEdgeGlow } from "../../utils/stormClassifier";
import { getRiskColor } from "../../utils/riskColorMapper";

// ── AlertBar (scrolling ticker between navbar and content) ────────────────────
export const AlertBar = memo(() => {
  const { solarWind, satellites, kpForecast } = useStormStore();
  const kp = solarWind?.kp_current ?? 0;

  const highRiskSats = (satellites || []).filter(s => s.composite_risk > 60);
  const show = kp >= 5 || highRiskSats.length > 0;

  if (!show) return null;

  const barColor  = kp >= 7 ? "#F44336" : "#FF9800";
  const arrival   = kpForecast?.peak_arrival_minutes;

  const parts = [
    highRiskSats.length > 0
      ? `🛰 ${highRiskSats.length} SATELLITE${highRiskSats.length > 1 ? "S" : ""} AT HIGH RISK`
      : null,
    highRiskSats.find(s => s.safe_mode_minutes)
      ? `⚠ ${highRiskSats.find(s=>s.safe_mode_minutes).name} SAFE MODE RECOMMENDED`
      : null,
    arrival
      ? `⚡ STORM PEAK IN T-${arrival} MIN`
      : null,
    solarWind?.storm_class
      ? `STORM CLASS ${solarWind.storm_class} ACTIVE — Kp ${kp.toFixed(1)}`
      : null,
    "NAKSHATRA-KAVACH MONITORING ACTIVE — ALL SYSTEMS NOMINAL",
  ].filter(Boolean).join("  ·  ");

  return (
    <AnimatePresence>
      <motion.div
        initial={{ height: 0, opacity: 0 }}
        animate={{ height: 32, opacity: 1 }}
        exit={{   height: 0, opacity: 0 }}
        style={{
          position:   "fixed",
          top:        64, left: 0, right: 0,
          zIndex:     900,
          background: `${barColor}18`,
          borderBottom: `1px solid ${barColor}44`,
          overflow:   "hidden",
          display:    "flex",
          alignItems: "center",
          cursor:     "pointer",
        }}
      >
        {/* Static label */}
        <div style={{
          flexShrink:  0,
          padding:     "0 12px",
          background:  `${barColor}33`,
          height:      "100%",
          display:     "flex",
          alignItems:  "center",
          borderRight: `1px solid ${barColor}44`,
          fontFamily:  "Orbitron, sans-serif",
          fontSize:    10, fontWeight: 700,
          color:       barColor,
          letterSpacing: "0.1em",
        }}>
          LIVE ALERT
        </div>

        {/* Scrolling text */}
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <div className="marquee-inner" style={{
            display:    "inline-block",
            whiteSpace: "nowrap",
            fontFamily: "JetBrains Mono, monospace",
            fontSize:   11, color: barColor,
            letterSpacing: "0.06em",
            padding:    "0 40px",
          }}>
            {parts} &nbsp;&nbsp;&nbsp; {parts}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
});

// ── EdgeGlow (viewport border glow during storm) ──────────────────────────────
export const EdgeGlow = memo(() => {
  const { solarWind } = useStormStore();
  const kp  = solarWind?.kp_current ?? 0;
  const glow = getEdgeGlow(kp);

  if (glow === "none") return null;

  return (
    <motion.div
      initial={{ boxShadow: "none" }}
      animate={{ boxShadow: glow }}
      transition={{ duration: 1.5, ease: "easeInOut" }}
      style={{
        position:   "fixed",
        inset:      0,
        zIndex:     999,
        pointerEvents: "none",
      }}
    />
  );
});

// ── StormAlertOverlay ─────────────────────────────────────────────────────────
export const StormAlertOverlay = memo(() => {
  const { solarWind, kpForecast, alertOverlayVisible, hideAlertOverlay } = useStormStore();
  const kp = solarWind?.kp_current ?? 0;
  const sc = getStormClass(kp);

  // useEffect MUST be before early return — Rules of Hooks
  React.useEffect(() => {
    if (!alertOverlayVisible) return;
    const t = setTimeout(hideAlertOverlay, 8000);
    return () => clearTimeout(t);
  }, [alertOverlayVisible, hideAlertOverlay]);

  if (!alertOverlayVisible) return null;

  const color   = sc?.color || "#F44336";
  const arrival = kpForecast?.peak_arrival_minutes;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{   opacity: 0 }}
        transition={{ duration: 0.4 }}
        onClick={hideAlertOverlay}
        style={{
          position:   "fixed",
          inset:      0,
          zIndex:     2000,
          background: "rgba(0,0,0,0.88)",
          display:    "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap:        24,
        }}
      >
        {/* Storm badge */}
        <motion.div
          initial={{ scale: 0.3 }}
          animate={{ scale: [0.3, 1.2, 1.0] }}
          transition={{ duration: 0.8, times: [0, 0.6, 1], delay: 0.4 }}
          style={{
            padding:    "24px 48px",
            borderRadius: 16,
            background: `${color}22`,
            border:     `2px solid ${color}`,
            color,
            fontFamily: "Orbitron, sans-serif",
            fontSize:   42, fontWeight: 900,
            letterSpacing: "0.08em",
            textShadow: `0 0 40px ${color}`,
          }}
        >
          {sc?.label || "STORM DETECTED"}
        </motion.div>

        {/* Alert text */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          style={{
            textAlign:  "center",
            fontFamily: "Orbitron, sans-serif",
            color:      "#F44336",
          }}
        >
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: "0.1em", marginBottom: 8 }}>
            ⚠ GEOMAGNETIC STORM DETECTED
          </div>
          <div style={{ fontSize: 14, color: "#90A4AE", fontFamily: "Space Grotesk, sans-serif" }}>
            Storm Class: {sc?.label} &nbsp;·&nbsp; Kp: {kp.toFixed(1)}
            {arrival && ` · Impact in ${arrival} minutes`}
          </div>
        </motion.div>

        {/* Action buttons */}
        <motion.div
          initial={{ y: 30, opacity: 0 }}
          animate={{ y: 0,  opacity: 1 }}
          transition={{ delay: 1.2 }}
          style={{ display: "flex", gap: 12 }}
        >
          {["View Details", "Simulate Impact", "Generate Advisory"].map((label, i) => (
            <motion.button
              key={label}
              initial={{ y: 20, opacity: 0 }}
              animate={{ y: 0,  opacity: 1 }}
              transition={{ delay: 1.2 + i * 0.15 }}
              onClick={hideAlertOverlay}
              style={{
                padding:    "10px 20px",
                borderRadius: 8,
                background: "rgba(0,212,255,0.1)",
                border:     "1px solid rgba(0,212,255,0.4)",
                color:      "#00D4FF",
                fontFamily: "Space Grotesk, sans-serif",
                fontSize:   13, fontWeight: 500,
                cursor:     "pointer",
              }}
            >
              {label}
            </motion.button>
          ))}
        </motion.div>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 2 }}
          style={{ fontSize: 11, color: "#546E7A", fontFamily: "JetBrains Mono, monospace" }}
        >
          Click anywhere or wait 8 seconds to dismiss
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
});
