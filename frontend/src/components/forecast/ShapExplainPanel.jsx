import React, { memo, useEffect, useMemo, useState } from "react";
import { useStormStore } from "../../store/useStormStore";
import { SectionLabel } from "../ui/index";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

export const ShapExplainPanel = memo(() => {
  const { shapExplain, solarWind, kpForecast, satellites, gridCorridors } = useStormStore();
  const feats = shapExplain?.features || [];
  const method = shapExplain?.method || "TreeSHAP";
  const predicted = shapExplain?.predicted_kp;
  const dominant = shapExplain?.dominant_driver;
  const [groqExplanation, setGroqExplanation] = useState(null);
  const [groqState, setGroqState] = useState("idle");
  const context = useMemo(() => ({
    solar: {
      kp_current: solarWind?.kp_current ?? null,
      kp_forecast_3h: kpForecast?.kp_3hr?.value ?? null,
      storm_class: solarWind?.storm_class || kpForecast?.peak_storm_class,
      bz_gsm: solarWind?.bz_gsm,
      sw_speed: solarWind?.sw_speed,
      data_quality: solarWind?.data_quality || solarWind?.quality,
      source: solarWind?.is_historical ? "replay" : "live",
    },
    kp_forecast: kpForecast,
    top_satellites: [...(satellites || [])]
      .sort((a, b) => Number(b.composite_risk || 0) - Number(a.composite_risk || 0))
      .slice(0, 5)
      .map((sat) => ({ name: sat.name, risk: sat.composite_risk, level: sat.risk_level, action: sat.action })),
    top_grid: [...(gridCorridors || [])]
      .sort((a, b) => Number(b.risk_percent || 0) - Number(a.risk_percent || 0))
      .slice(0, 3)
      .map((grid) => ({ name: grid.name, gic_amps: grid.gic_amps, risk_percent: grid.risk_percent })),
  }), [solarWind, kpForecast, satellites, gridCorridors]);

  const shapKey = JSON.stringify({
    predicted,
    features: feats.slice(0, 6).map((f) => [f.feature, f.shap_value, f.value]),
  });

  useEffect(() => {
    if (!feats.length) return;
    let cancelled = false;
    setGroqState("loading");
    fetch(`${API_BASE}/api/advisory/explain/shap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: "en", shap: shapExplain?.raw || shapExplain, context }),
    })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Groq SHAP explanation failed");
        return data;
      })
      .then((data) => {
        if (!cancelled) {
          setGroqExplanation(data);
          setGroqState("ready");
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setGroqExplanation({ english_summary: error.message });
          setGroqState("error");
        }
      });
    return () => { cancelled = true; };
  }, [shapKey]);

  if (!feats.length) {
    return (
      <div style={{
        minHeight: 100,
        background: "var(--color-bg-card)",
        borderRadius: 10,
        border: "1px solid rgba(0,212,255,0.1)",
        padding: 12,
        color: "#607D8B",
        fontSize: 11,
      }}>
        <SectionLabel>SHAP - Feature drivers</SectionLabel>
        <div style={{ marginTop: 8 }}>Waiting for live TreeSHAP output from the XGBoost forecast branch.</div>
      </div>
    );
  }

  const maxAbs = Math.max(...feats.map((f) => Math.abs(f.shap_value || 0)), 1e-6);

  return (
    <div style={{
      height: "100%",
      minHeight: 180,
      background: "var(--color-bg-card)",
      borderRadius: 10,
      border: "1px solid rgba(0,212,255,0.12)",
      padding: "10px 12px",
      display: "flex",
      flexDirection: "column",
      gap: 6,
      overflow: "hidden",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <SectionLabel>SHAP - Kp drivers</SectionLabel>
        <span style={{ fontSize: 9, color: "#607D8B", fontFamily: "JetBrains Mono, monospace" }}>{method}</span>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 10, color: "#78909C" }}>
        {predicted !== undefined && (
          <span style={{ color: "#FFD54F", fontFamily: "JetBrains Mono, monospace" }}>
            predicted Kp {Number(predicted).toFixed(2)}
          </span>
        )}
        {dominant?.feature_name && (
          <span title={dominant.physics_explanation || ""}>top driver: {dominant.feature_name}</span>
        )}
      </div>

      <div style={{
        border: "1px solid rgba(0,212,255,0.12)",
        background: "rgba(0,212,255,0.045)",
        borderRadius: 8,
        padding: "7px 8px",
        color: groqState === "error" ? "#FFB74D" : "#B0BEC5",
        fontSize: 10,
        lineHeight: 1.45,
        maxHeight: 120,
        overflowY: "auto",
        flexShrink: 0,
      }}>
        <div style={{ color: "#00D4FF", fontFamily: "Orbitron, sans-serif", fontSize: 8, letterSpacing: "0.12em", marginBottom: 3 }}>
          GROQ EXPLANATION
        </div>
        {groqState === "loading"
          ? "Generating operator explanation with LLaMA 4 Scout..."
          : (groqExplanation?.english_summary || "Waiting for Groq explanation.")}
        {groqExplanation?.operator_takeaway && (
          <div style={{ color: "#FFD54F", marginTop: 4 }}>{groqExplanation.operator_takeaway}</div>
        )}
        {groqExplanation?.hindi_summary && (
          <div style={{ color: "#90CAF9", marginTop: 4 }}>{groqExplanation.hindi_summary}</div>
        )}
      </div>

      <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 5 }}>
        {feats.slice(0, 8).map((f, i) => {
          const w = (Math.abs(f.shap_value || 0) / maxAbs) * 100;
          const col = (f.shap_value || 0) >= 0 ? "#FF9800" : "#00D4FF";
          return (
            <div key={`${f.feature}-${i}`}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10, marginBottom: 2 }}>
                <span style={{ color: "#B0BEC5", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
                  {f.feature}
                </span>
                <span style={{ color: col, flexShrink: 0 }}>{Number(f.shap_value).toFixed(3)}</span>
              </div>
              <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.05)" }}>
                <div style={{ width: `${w}%`, height: "100%", borderRadius: 2, background: col, opacity: 0.85 }} />
              </div>
              {(f.value !== undefined || f.physics_note) && (
                <div style={{ fontSize: 8, color: "#607D8B", marginTop: 2, lineHeight: 1.3 }}>
                  {f.value !== undefined ? `value ${f.value} - ` : ""}{f.physics_note || f.impact || ""}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});
