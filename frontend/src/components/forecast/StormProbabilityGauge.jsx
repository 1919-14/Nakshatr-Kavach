import React, { memo } from "react";
import { useStormStore } from "../../store/useStormStore";

export const StormProbabilityGauge = memo(() => {
  const { kpForecast } = useStormStore();
  const p = Math.round((kpForecast?.storm_probability ?? 0) * 100);
  const c = p > 70 ? "#F44336" : p > 40 ? "#FF9800" : "#4CAF50";
  return (
    <div style={{
      background: "var(--color-bg-card)",
      borderRadius: 10,
      border: "1px solid rgba(0,212,255,0.12)",
      padding: "10px 12px",
      fontFamily: "Orbitron, sans-serif",
    }}>
      <div style={{ fontSize: 9, color: "#546E7A", letterSpacing: "0.1em", marginBottom: 6 }}>
        STORM PROBABILITY (12h)
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{
          flex: 1, height: 8, borderRadius: 4,
          background: "rgba(255,255,255,0.06)",
          overflow: "hidden",
        }}>
          <div style={{
            width: `${Math.min(100, p)}%`, height: "100%", background: c,
            boxShadow: `0 0 12px ${c}66`,
            transition: "width 0.6s ease",
          }} />
        </div>
        <div style={{ fontSize: 16, fontWeight: 800, color: c, minWidth: 42 }}>{p}%</div>
      </div>
      <div style={{ fontSize: 9, color: "#546E7A", marginTop: 6 }}>
        Peak class: <span style={{ color: "#00D4FF" }}>{kpForecast?.peak_storm_class ?? "—"}</span>
      </div>
    </div>
  );
});
