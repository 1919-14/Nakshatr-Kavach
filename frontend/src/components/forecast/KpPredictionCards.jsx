import React, { memo } from "react";
import { useStormStore } from "../../store/useStormStore";
import { getKpColor } from "../../utils/stormClassifier";
import { SectionLabel } from "../ui/index";

const HORIZONS = [
  ["3hr", "+3h"],
  ["6hr", "+6h"],
  ["12hr", "+12h"],
  ["24hr", "+24h"],
];

function fmt(value) {
  if (value === null || value === undefined || value === "") return "--";
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(1) : "--";
}

const MiniValue = ({ label, value, color }) => (
  <div style={{ minWidth: 0 }}>
    <div style={{
      fontSize: 8,
      color: "#607D8B",
      fontFamily: "JetBrains Mono, monospace",
      textTransform: "uppercase",
      marginBottom: 2,
    }}>
      {label}
    </div>
    <div style={{
      color,
      fontSize: 12,
      fontFamily: "Orbitron, sans-serif",
      fontWeight: 800,
      lineHeight: 1,
    }}>
      {fmt(value)}
    </div>
  </div>
);

export const KpPredictionCards = memo(() => {
  const { solarWind, kpForecast } = useStormStore();
  const current = solarWind?.kp_current;
  const branch = kpForecast?.branch_predictions || {};
  const modelInfo = kpForecast?.model_info || {};
  const lstmLabel = modelInfo.lstm_loaded ? `LSTM ${modelInfo.lstm_backend || ""}` : "LSTM fallback";

  return (
    <div style={{
      background: "linear-gradient(180deg, rgba(13,27,62,0.94), rgba(5,16,35,0.96))",
      border: "1px solid rgba(0,212,255,0.12)",
      borderRadius: 8,
      padding: "10px 12px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
        <SectionLabel>Kp Predictions</SectionLabel>
        <div style={{
          color: "#78909C",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 10,
          whiteSpace: "nowrap",
        }}>
          Current {fmt(current)} | {lstmLabel}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
        {HORIZONS.map(([key, label]) => {
          const item = branch[key] || {};
          const color = getKpColor(Number(item.fused || 0));
          return (
            <div
              key={key}
              style={{
                border: `1px solid ${color}33`,
                background: `${color}0d`,
                borderRadius: 8,
                padding: "9px 10px",
                minWidth: 0,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
                <div style={{
                  color: "#E8F4FD",
                  fontFamily: "Orbitron, sans-serif",
                  fontSize: 11,
                  fontWeight: 800,
                }}>
                  {label}
                </div>
                <div style={{
                  color,
                  fontFamily: "Orbitron, sans-serif",
                  fontSize: 18,
                  fontWeight: 900,
                  lineHeight: 1,
                }}>
                  {fmt(item.fused)}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 7 }}>
                <MiniValue label="XGB" value={item.xgb} color="#90CAF9" />
                <MiniValue label="LSTM" value={item.lstm} color="#CE93D8" />
                <MiniValue label="Unc" value={item.uncertainty} color="#FFD54F" />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
});

export default KpPredictionCards;
