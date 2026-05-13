import React, { memo } from "react";
import { useStormStore } from "../../store/useStormStore";
import { SectionLabel } from "../ui/index";

export const ShapExplainPanel = memo(() => {
  const { shapExplain } = useStormStore();
  const feats = shapExplain?.features || [];
  const method = shapExplain?.method || "—";

  if (!feats.length) {
    return (
      <div style={{
        height: "100%", minHeight: 100,
        background: "var(--color-bg-card)",
        borderRadius: 10,
        border: "1px solid rgba(0,212,255,0.1)",
        padding: 12,
        color: "#546E7A",
        fontSize: 11,
      }}>
        <SectionLabel>SHAP — Feature drivers</SectionLabel>
        <div style={{ marginTop: 8 }}>Load forecast data to populate TreeSHAP drivers for the XGBoost branch.</div>
      </div>
    );
  }

  const maxAbs = Math.max(...feats.map((f) => Math.abs(f.shap_value || 0)), 1e-6);

  return (
    <div style={{
      height: "100%", minHeight: 100,
      background: "var(--color-bg-card)",
      borderRadius: 10,
      border: "1px solid rgba(0,212,255,0.12)",
      padding: "10px 12px",
      display: "flex",
      flexDirection: "column",
      gap: 6,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <SectionLabel>SHAP — Kp drivers</SectionLabel>
        <span style={{ fontSize: 9, color: "#546E7A", fontFamily: "JetBrains Mono, monospace" }}>{method}</span>
      </div>
      <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 5 }}>
        {feats.slice(0, 8).map((f, i) => {
          const w = (Math.abs(f.shap_value || 0) / maxAbs) * 100;
          const col = (f.shap_value || 0) >= 0 ? "#FF9800" : "#00D4FF";
          return (
            <div key={i}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginBottom: 2 }}>
                <span style={{ color: "#B0BEC5", fontFamily: "JetBrains Mono, monospace" }}>{f.feature}</span>
                <span style={{ color: col }}>{Number(f.shap_value).toFixed(3)}</span>
              </div>
              <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.05)" }}>
                <div style={{ width: `${w}%`, height: "100%", borderRadius: 2, background: col, opacity: 0.85 }} />
              </div>
              {f.physics_note && (
                <div style={{ fontSize: 8, color: "#607D8B", marginTop: 2, lineHeight: 1.3 }}>{f.physics_note}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});
