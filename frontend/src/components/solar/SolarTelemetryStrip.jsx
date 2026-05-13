import React, { memo, useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { LineChart, Line, ResponsiveContainer } from "recharts";
import { useStormStore } from "../../store/useStormStore";
import { useAnimatedNumber } from "../../hooks/index";
import { getBzColor, getKpColor, getXRayColor } from "../../utils/stormClassifier";
import { generateSparkline } from "../../mock/mockData";

// ── Single Metric Card ────────────────────────────────────────────────────────
const MetricCard = memo(({ label, value, unit, color, sparkData, warning }) => {
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

      {/* Label */}
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
          label.includes("Kp") ? 1 : label.includes("Speed") ? 0 : 1
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

// ── Solar Telemetry Strip ─────────────────────────────────────────────────────
export const SolarTelemetryStrip = memo(() => {
  const { solarWind } = useStormStore();
  const [sparks, setSparks] = useState({
    bz:      generateSparkline(-18, 3),
    bt:      generateSparkline(28, 2),
    speed:   generateSparkline(720, 25),
    density: generateSparkline(12, 1.2),
    kp:      generateSparkline(7.2, 0.3),
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
          kp:      push(prev.kp,      solarWind?.kp_current     ?? prev.kp[prev.kp.length-1]),
        };
      });
    }, 3000);
    return () => clearInterval(t);
  }, [solarWind]);

  const bz      = solarWind?.bz_gsm        ?? -18.4;
  const bt      = solarWind?.bt_total      ?? 28.7;
  const speed   = solarWind?.sw_speed      ?? 720;
  const density = solarWind?.proton_density?? 12.3;
  const kp      = solarWind?.kp_current    ?? 7.2;
  const xray    = solarWind?.xray_class    ?? "M1.5";

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
      label:     "Kp Index",
      value:     kp,
      unit:      "",
      color:     getKpColor(kp),
      sparkData: sparks.kp,
      warning:   kp >= 7,
    },
    {
      label:     "X-Ray Class",
      value:     xray,
      unit:      "",
      color:     getXRayColor(xray),
      sparkData: sparks.kp.map(v => v * 0.8),
      warning:   xray?.startsWith("X"),
    },
  ];

  return (
    <div style={{ display: "flex", gap: 8, height: "100%" }}>
      {metrics.map((m, i) => (
        <motion.div
          key={m.label}
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.07, duration: 0.4 }}
          style={{ flex: 1, minWidth: 0 }}
        >
          <MetricCard {...m} />
        </motion.div>
      ))}
    </div>
  );
});
