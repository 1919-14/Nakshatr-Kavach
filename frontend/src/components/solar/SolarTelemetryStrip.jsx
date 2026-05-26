import React, { memo, useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { LineChart, Line, ResponsiveContainer } from "recharts";
import { useStormStore } from "../../store/useStormStore";
import { useAnimatedNumber } from "../../hooks/index";
import { getBzColor, getKpColor, getXRayColor } from "../../utils/stormClassifier";
import { generateSparkline } from "../../mock/mockData";

// ── Single Metric Card ────────────────────────────────────────────────────────
const MetricCard = memo(({ label, value, unit, color, sparkData, warning, badge }) => {
  const animated  = useAnimatedNumber(parseFloat(value) || 0, 1000);
  const prevRef   = useRef(value);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (prevRef.current !== value) {
      setFlash(true);
      setTimeout(() => setFlash(false), 600);
      prevRef.current = value;
    }
  }, [value]);

  const chartData = (sparkData || []).map((v, i) => ({ i, v }));

  return (
    <motion.div
      animate={flash ? { scale: [1, 1.03, 1] } : {}}
      transition={{ duration: 0.4 }}
      style={{
        flex:         "1 1 0",
        minWidth:     0,
        background:   "var(--color-bg-card)",
        borderRadius: 10,
        padding:      "10px 12px 6px",
        border:       `1px solid ${color}33`,
        boxShadow:    warning
          ? `0 0 18px ${color}55, inset 0 0 12px ${color}11`
          : `0 0 8px ${color}22`,
        display:      "flex",
        flexDirection:"column",
        gap:          4,
        transition:   "box-shadow 0.5s ease",
        position:     "relative",
        overflow:     "hidden",
      }}
    >
      {/* Warning pulse overlay */}
      {warning && (
        <motion.div
          animate={{ opacity: [0, 0.12, 0] }}
          transition={{ duration: 1.2, repeat: Infinity }}
          style={{
            position:   "absolute", inset: 0,
            background: color,
            pointerEvents: "none",
            borderRadius: 10,
          }}
        />
      )}

      {/* Label row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{
          fontFamily:    "Orbitron, sans-serif",
          fontSize:      9,
          fontWeight:    600,
          color:         "#546E7A",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}>
          {label}
        </div>
        {badge && (
          <span style={{
            fontSize:    8,
            fontFamily:  "Orbitron, sans-serif",
            fontWeight:  700,
            padding:     "1px 5px",
            borderRadius: 999,
            background:  `${color}22`,
            color:       color,
            border:      `1px solid ${color}44`,
            letterSpacing: "0.08em",
          }}>
            {badge}
          </span>
        )}
      </div>

      {/* Value */}
      <div style={{
        fontFamily:    "Orbitron, sans-serif",
        fontSize:      24,
        fontWeight:    800,
        color,
        lineHeight:    1,
        letterSpacing: "0.02em",
        textShadow:    `0 0 12px ${color}66`,
      }}>
        {typeof value === "number" ? animated.toFixed(
          label.includes("Kp") ? 1 : label.includes("Speed") || label.includes("SEP") ? 0 : 1
        ) : value}
        {unit && (
          <span style={{ fontSize: 11, fontWeight: 400, color: "#546E7A", marginLeft: 3 }}>
            {unit}
          </span>
        )}
      </div>

      {/* Sparkline */}
      <div style={{ height: 36, marginTop: 2 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData}>
            <Line
              type="monotone"
              dataKey="v"
              stroke={color}
              strokeWidth={1.2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </motion.div>
  );
});

// ── Dst colour helper ─────────────────────────────────────────────────────────
function getDstColor(dst) {
  if (dst === null || dst === undefined) return "#607D8B";
  if (dst < -200) return "#E53935";   // EXTREME
  if (dst < -100) return "#F4511E";   // INTENSE
  if (dst < -50)  return "#FF9800";   // MODERATE
  if (dst < -20)  return "#FDD835";   // MINOR
  return "#4CAF50";                    // QUIET
}

function getDstBadge(dst) {
  if (dst === null || dst === undefined) return null;
  if (dst < -200) return "EXTREME";
  if (dst < -100) return "INTENSE";
  if (dst < -50)  return "MOD";
  if (dst < -20)  return "MINOR";
  return null;
}

// ── SEP colour helper ─────────────────────────────────────────────────────────
function getSepColor(flux, alertActive) {
  if (alertActive) return "#E53935";
  if (!flux) return "#607D8B";
  if (flux >= 1000) return "#E53935";
  if (flux >= 100)  return "#F4511E";
  if (flux >= 10)   return "#FF9800";
  return "#4CAF50";
}

// ── Solar Telemetry Strip ─────────────────────────────────────────────────────
export const SolarTelemetryStrip = memo(() => {
  const { solarWind, kpForecast, dst, sep } = useStormStore();
  const asNum = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const kpCurrent = asNum(solarWind?.kp_current);
  const kp3h = asNum(kpForecast?.kp_3hr?.value);

  // Dst / SEP from store
  const dstVal        = asNum(dst?.dst_nt);
  const sepFlux       = asNum(sep?.proton_flux_gt10mev);
  const sepAlertActive = Boolean(sep?.sep_alert_active);

  const [sparks, setSparks] = useState({
    bz:      generateSparkline(0, 0),
    bt:      generateSparkline(0, 0),
    speed:   generateSparkline(0, 0),
    density: generateSparkline(0, 0),
    kpCurrent: generateSparkline(0, 0),
    kp3h:      generateSparkline(0, 0),
    dst:       generateSparkline(0, 0),
    sep:       generateSparkline(0, 0),
  });

  // Update sparklines with new value every 3s
  useEffect(() => {
    const t = setInterval(() => {
      setSparks(prev => {
        const push = (arr, newVal) => [...arr.slice(1), newVal];
        return {
          bz:      push(prev.bz,      solarWind?.bz_gsm        ?? prev.bz[prev.bz.length-1]),
          bt:      push(prev.bt,      solarWind?.bt_total       ?? prev.bt[prev.bt.length-1]),
          speed:   push(prev.speed,   solarWind?.sw_speed       ?? prev.speed[prev.speed.length-1]),
          density: push(prev.density, solarWind?.proton_density ?? prev.density[prev.density.length-1]),
          kpCurrent: push(prev.kpCurrent, kpCurrent ?? prev.kpCurrent[prev.kpCurrent.length-1]),
          kp3h:      push(prev.kp3h,      kp3h      ?? prev.kp3h[prev.kp3h.length-1]),
          dst:       push(prev.dst,  dstVal  ?? prev.dst[prev.dst.length-1]),
          sep:       push(prev.sep,  sepFlux ?? prev.sep[prev.sep.length-1]),
        };
      });
    }, 3000);
    return () => clearInterval(t);
  }, [solarWind, kpCurrent, kp3h, dstVal, sepFlux]);

  const bz      = solarWind?.bz_gsm        ?? 0;
  const bt      = solarWind?.bt_total      ?? 0;
  const speed   = solarWind?.sw_speed      ?? 0;
  const density = solarWind?.proton_density?? 0;
  const xray    = solarWind?.xray_class    ?? "-";

  const metrics = [
    {
      label:     "Bz (nT)",
      value:     bz,
      unit:      "nT",
      color:     getBzColor(bz),
      sparkData: sparks.bz,
      warning:   bz < -10,
    },
    {
      label:     "Bt Total",
      value:     bt,
      unit:      "nT",
      color:     bt > 20 ? "#FF9800" : "#00D4FF",
      sparkData: sparks.bt,
      warning:   bt > 25,
    },
    {
      label:     "Wind Speed",
      value:     speed,
      unit:      "km/s",
      color:     speed > 600 ? "#F44336" : speed > 450 ? "#FF9800" : "#00D4FF",
      sparkData: sparks.speed,
      warning:   speed > 600,
    },
    {
      label:     "Density",
      value:     density,
      unit:      "p/cm³",
      color:     density > 15 ? "#FF9800" : "#AB47BC",
      sparkData: sparks.density,
      warning:   density > 20,
    },
    {
      label:     "Kp Current",
      value:     kpCurrent === null ? "--" : kpCurrent,
      unit:      "",
      color:     kpCurrent === null ? "#607D8B" : getKpColor(kpCurrent),
      sparkData: sparks.kpCurrent,
      warning:   kpCurrent !== null && kpCurrent >= 7,
    },
    {
      label:     "Kp Forecast 3h",
      value:     kp3h === null ? "--" : kp3h,
      unit:      "",
      color:     kp3h === null ? "#607D8B" : getKpColor(kp3h),
      sparkData: sparks.kp3h,
      warning:   kp3h !== null && kp3h >= 7,
    },
    {
      label:     "X-Ray Class",
      value:     xray,
      unit:      "",
      color:     getXRayColor(xray),
      sparkData: sparks.kp3h.map(v => v * 0.8),
      warning:   xray?.startsWith("X"),
    },
    // ── Enhancement: Dst ring-current index ──
    {
      label:     "Dst Index",
      value:     dstVal === null ? "--" : dstVal,
      unit:      dstVal !== null ? "nT" : "",
      color:     getDstColor(dstVal),
      sparkData: sparks.dst,
      warning:   dstVal !== null && dstVal < -50,
      badge:     getDstBadge(dstVal),
    },
    // ── Enhancement: SEP proton flux ──
    {
      label:     "SEP Proton",
      value:     sepFlux === null ? "--" : sepFlux,
      unit:      sepFlux !== null ? "pfu" : "",
      color:     getSepColor(sepFlux, sepAlertActive),
      sparkData: sparks.sep,
      warning:   sepAlertActive,
      badge:     sep?.sep_class || null,
    },
  ];

  return (
    <div style={{ display: "flex", gap: 8, height: "100%" }}>
      {metrics.map((m, i) => (
        <motion.div
          key={m.label}
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.06, duration: 0.4 }}
          style={{ flex: 1, minWidth: 0 }}
        >
          <MetricCard {...m} />
        </motion.div>
      ))}
    </div>
  );
});
