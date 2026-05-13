import React, { memo, useState } from "react";
import { motion } from "framer-motion";
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  ReferenceLine, Tooltip, ResponsiveContainer, ReferenceArea,
} from "recharts";
import { useStormStore } from "../../store/useStormStore";
import { MOCK_KP_CHART_DATA } from "../../mock/mockData";
import { getStormClass } from "../../utils/stormClassifier";
import { SectionLabel } from "../ui/index";

// ── Custom Tooltip ────────────────────────────────────────────────────────────
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const kp     = payload.find(p => p.dataKey === "forecast")?.value
              ?? payload.find(p => p.dataKey === "actual")?.value;
  const upper  = payload.find(p => p.dataKey === "upper")?.value;
  const lower  = payload.find(p => p.dataKey === "lower")?.value;
  const sc     = kp ? getStormClass(kp) : null;

  return (
    <div style={{
      background:    "rgba(13,27,62,0.95)",
      border:        "1px solid rgba(0,212,255,0.3)",
      borderRadius:  8,
      padding:       "10px 14px",
      backdropFilter:"blur(12px)",
      fontFamily:    "JetBrains Mono, monospace",
      fontSize:      12,
    }}>
      <div style={{ color: "#546E7A", fontSize: 10, marginBottom: 4 }}>{label}</div>
      {kp && (
        <div style={{ color: sc?.color || "#00D4FF", fontWeight: 700, fontSize: 16 }}>
          Kp {kp.toFixed(1)}
        </div>
      )}
      {sc && (
        <div style={{ color: sc.color, fontSize: 10, marginTop: 2 }}>{sc.label}</div>
      )}
      {upper && lower && (
        <div style={{ color: "#546E7A", fontSize: 10, marginTop: 4 }}>
          Range: {lower.toFixed(1)} – {upper.toFixed(1)}
        </div>
      )}
    </div>
  );
};

// ── Kp Forecast Chart ─────────────────────────────────────────────────────────
export const KpForecastChart = memo(() => {
  const { kpChartData } = useStormStore();
  const data = kpChartData?.length ? kpChartData : MOCK_KP_CHART_DATA;

  const THRESHOLDS = [
    { kp: 5, color: "#4CAF50", label: "G1" },
    { kp: 6, color: "#CDDC39", label: "G2" },
    { kp: 7, color: "#FF9800", label: "G3" },
    { kp: 8, color: "#F44336", label: "G4" },
    { kp: 9, color: "#9C27B0", label: "G5" },
  ];

  return (
    <div style={{
      height:     "100%",
      background: "var(--color-bg-card)",
      borderRadius: 12,
      border:     "1px solid rgba(0,212,255,0.12)",
      padding:    "14px 16px",
      display:    "flex",
      flexDirection: "column",
      gap:        8,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <SectionLabel>Kp Forecast — 24 Hour Timeline</SectionLabel>
        <div style={{
          fontSize:   10,
          color:      "#546E7A",
          fontFamily: "JetBrains Mono, monospace",
        }}>
          ── Actual &nbsp; — Forecast &nbsp;
          <span style={{ color: "rgba(0,212,255,0.5)" }}>▓ Confidence</span>
        </div>
      </div>

      <div style={{ flex: 1 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 20, bottom: 4, left: 0 }}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="rgba(0,212,255,0.07)"
              vertical={false}
            />

            {/* Storm zone shading */}
            <ReferenceArea y1={5} y2={6} fill="rgba(76,175,80,0.05)"    ifOverflow="extendDomain" />
            <ReferenceArea y1={6} y2={7} fill="rgba(255,152,0,0.07)"   ifOverflow="extendDomain" />
            <ReferenceArea y1={7} y2={9} fill="rgba(244,67,54,0.09)"   ifOverflow="extendDomain" />

            <XAxis
              dataKey="time"
              tick={{ fill: "#546E7A", fontSize: 10, fontFamily: "JetBrains Mono, monospace" }}
              axisLine={{ stroke: "rgba(0,212,255,0.1)" }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 9]}
              ticks={[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]}
              tick={{ fill: "#546E7A", fontSize: 10, fontFamily: "Orbitron, sans-serif" }}
              axisLine={{ stroke: "rgba(0,212,255,0.1)" }}
              tickLine={false}
              width={24}
            />

            <Tooltip content={<CustomTooltip />} />

            {/* Confidence band */}
            <Area
              type="monotone"
              dataKey="upper"
              fill="rgba(0,212,255,0.08)"
              stroke="none"
              activeDot={false}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="lower"
              fill="var(--color-bg-card)"
              stroke="none"
              activeDot={false}
              isAnimationActive={false}
            />

            {/* Historical actual line */}
            <Line
              type="monotone"
              dataKey="actual"
              stroke="#546E7A"
              strokeWidth={1.5}
              strokeDasharray="5 3"
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />

            {/* Forecast line */}
            <Line
              type="monotone"
              dataKey="forecast"
              stroke="#00D4FF"
              strokeWidth={2.5}
              dot={(props) => {
                const { cx, cy, value } = props;
                if (!value) return null;
                return (
                  <circle
                    key={`dot-${cx}-${cy}`}
                    cx={cx} cy={cy} r={3}
                    fill="#00D4FF"
                    stroke="#020817"
                    strokeWidth={1.5}
                  />
                );
              }}
              connectNulls={false}
              isAnimationActive
              animationDuration={1200}
              animationEasing="ease-out"
            />

            {/* Storm threshold lines */}
            {THRESHOLDS.map(({ kp, color, label }) => (
              <ReferenceLine
                key={kp}
                y={kp}
                stroke={color}
                strokeDasharray="4 4"
                strokeOpacity={0.6}
                label={{
                  value: label,
                  fill: color,
                  fontSize: 9,
                  fontFamily: "Orbitron, sans-serif",
                  position: "insideTopRight",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
});
