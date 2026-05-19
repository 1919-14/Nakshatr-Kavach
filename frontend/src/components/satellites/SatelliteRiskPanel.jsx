import React, { memo, useMemo, useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../../store/useStormStore";
import {
  getRiskColor, getRiskLabel, getRiskBg,
} from "../../utils/riskColorMapper";
import {
  AnimatedBar, RiskLevelBadge, CountdownTimer, SectionLabel,
} from "../ui/index";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

const RecommendationExplanation = memo(({ sat, action }) => {
  const [lang, setLang] = useState("en");
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchExplanation = async (selectedLang) => {
    setLang(selectedLang);
    setLoading(true);
    setText("");
    try {
      const response = await fetch(`${API_BASE}/api/advisory/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: `Explain why the recommendation '${action}' was generated for the satellite '${sat.name}'. Drag risk is ${sat.drag_risk}, charging risk is ${sat.charging_risk}, and radiation risk is ${sat.radiation_risk}. Give a concise 1-2 sentence response.`,
          language: selectedLang,
          context: {},
          history: []
        }),
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const eventText of events) {
          const dataLine = eventText.split("\n").find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          const payload = JSON.parse(dataLine.slice(5).trim() || "{}");
          if (payload.token) setText(prev => prev + payload.token);
          if (payload.error) throw new Error(payload.error);
        }
      }
    } catch (err) {
      setText("Explanation unavailable: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ marginTop: 8, padding: 8, background: "rgba(0,0,0,0.2)", borderRadius: 6, border: "1px solid rgba(255,255,255,0.05)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontSize: 9, color: "#90A4AE", fontFamily: "Orbitron, sans-serif" }}>AI EXPLANATION</span>
        <div style={{ display: "flex", gap: 6 }}>
          <button onClick={(e) => { e.stopPropagation(); fetchExplanation("en"); }} disabled={loading} style={{ background: lang==="en"?"rgba(0,212,255,0.2)":"none", color: "#00D4FF", border: "1px solid rgba(0,212,255,0.3)", borderRadius: 4, fontSize: 9, padding: "2px 6px", cursor: "pointer" }}>EN</button>
          <button onClick={(e) => { e.stopPropagation(); fetchExplanation("hi"); }} disabled={loading} style={{ background: lang==="hi"?"rgba(0,212,255,0.2)":"none", color: "#00D4FF", border: "1px solid rgba(0,212,255,0.3)", borderRadius: 4, fontSize: 9, padding: "2px 6px", cursor: "pointer" }}>हिंदी</button>
        </div>
      </div>
      {text ? <div style={{ fontSize: 11, color: "#C8D7E1", lineHeight: 1.4 }}>{text}</div> : (!loading && <div style={{ fontSize: 10, color: "#546E7A" }}>Select a language to generate an explanation.</div>)}
      {loading && !text && <div style={{ fontSize: 10, color: "#FFD54F" }}>Generating...</div>}
    </div>
  );
});

const SatelliteCard = memo(({ sat, index }) => {
  const [expanded, setExpanded] = useState(false);
  const cardRef = useRef(null);
  const score = Math.round(Number(sat.composite_risk || 0));
  const riskColor = getRiskColor(score);
  const action = sat.action?.split(".")[0] || "Routine monitoring";

  const handleClick = useCallback(() => {
    setExpanded(e => !e);
    // Scroll card into view after toggling so the user can see it
    setTimeout(() => {
      cardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 50);
  }, []);

  return (
    <motion.div
      ref={cardRef}
      layout
      initial={{ opacity: 0, y: 10, filter: "blur(4px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      whileHover={{ scale: 1.01, translateX: 2, borderColor: `${riskColor}55` }}
      transition={{ delay: Math.min(index, 12) * 0.03, duration: 0.3, ease: "easeOut" }}
      style={{
        background: `linear-gradient(135deg, rgba(5,16,36,0.85) 0%, ${getRiskBg(score)} 100%)`,
        backdropFilter: "blur(12px)",
        borderRadius: 10,
        padding: "10px 14px",
        border: `1px solid ${riskColor}24`,
        borderLeft: `4px solid ${riskColor}`,
        minHeight: 64,
        flexShrink: 0,
        boxShadow: `0 4px 12px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)`,
        cursor: "pointer",
        overflow: "hidden",
      }}
      onClick={handleClick}
    >
      <div 
        style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}
      >
        <div style={{
          width: 8, height: 8, borderRadius: "50%",
          background: riskColor,
          boxShadow: `0 0 8px ${riskColor}`,
          flexShrink: 0
        }} />
        <span
          title={sat.name}
          style={{
            fontFamily: "Space Grotesk, sans-serif",
            fontWeight: 700,
            fontSize: 13,
            color: "#E8F4FD",
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            letterSpacing: "0.02em",
          }}
        >
          {sat.name}
        </span>
        <span style={{
          fontFamily: "Orbitron, sans-serif",
          fontSize: 14,
          fontWeight: 800,
          color: riskColor,
          flexShrink: 0,
        }}>
          {score}
        </span>
        <RiskLevelBadge score={score} />
        <motion.div
          animate={{ rotate: expanded ? 180 : 0 }}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: 24, height: 24, borderRadius: "50%",
            background: "rgba(255,255,255,0.05)",
            color: "#00D4FF", fontSize: 10, flexShrink: 0
          }}
        >
          ▼
        </motion.div>
      </div>

      <div
        title={action}
        style={{
          marginTop: 6,
          marginLeft: 18,
          color: "#90A4AE",
          fontSize: 11,
          lineHeight: 1.4,
          fontFamily: "JetBrains Mono, monospace",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: expanded ? "normal" : "nowrap",
        }}
      >
        <span style={{ color: "#607D8B" }}>{sat.type || "SAT"} · {sat.altitude ? `${sat.altitude.toLocaleString()}km` : "Cat"}</span>
        <span style={{ margin: "0 6px", color: "rgba(255,255,255,0.1)" }}>|</span>
        {action}
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.24 }}
            style={{ overflow: "hidden" }}
          >
            <div style={{ marginTop: 10, paddingTop: 9, borderTop: `1px solid ${riskColor}24` }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 9 }}>
                <AnimatedBar value={sat.drag_risk} color="#FF9800" label="DRAG" height={4} />
                <AnimatedBar value={sat.charging_risk} color="#FDD835" label="CHARGE" height={4} />
                <AnimatedBar value={sat.radiation_risk} color="#00D4FF" label="RAD" height={4} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", fontSize: 11 }}>
                {[
                  ["Orbit", sat.type || "Catalog"],
                  ["Inclination", sat.inclination !== undefined ? `${sat.inclination} deg` : "Catalog"],
                  ["Tier", sat.tier ? `T${sat.tier}` : "Catalog"],
                  ["Risk", getRiskLabel(score)],
                ].map(([label, value]) => (
                  <div key={label} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ color: "#546E7A" }}>{label}</span>
                    <span style={{ color: "#B0BEC5", textAlign: "right" }}>{value}</span>
                  </div>
                ))}
              </div>
              {sat.safe_mode_minutes && (
                <div style={{ marginTop: 8 }}>
                  <CountdownTimer totalSeconds={sat.safe_mode_minutes * 60} label="SAFE MODE" urgent={sat.safe_mode_minutes < 10} />
                </div>
              )}
              <RecommendationExplanation sat={sat} action={action} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
});

export const SatelliteRiskPanel = memo(() => {
  const { satellites } = useStormStore();
  const satList = satellites?.length ? satellites : [];

  const sorted = useMemo(() =>
    [...satList].sort((a, b) => b.composite_risk - a.composite_risk),
    [satList]
  );

  const critical = sorted.filter(s => s.composite_risk > 80).length;
  const high = sorted.filter(s => s.composite_risk > 60 && s.composite_risk <= 80).length;

  return (
    <div style={{
      height: "100%",
      background: "linear-gradient(180deg, rgba(13,27,62,0.85) 0%, rgba(5,16,36,0.95) 100%)",
      backdropFilter: "blur(16px)",
      borderRadius: 14,
      border: "1px solid rgba(0,212,255,0.15)",
      boxShadow: "0 8px 32px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)",
      padding: "16px",
      display: "flex",
      flexDirection: "column",
      gap: 12,
      overflow: "hidden",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <SectionLabel>Satellite Risk</SectionLabel>
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          {critical > 0 && (
            <span style={{
              fontSize: 10, padding: "2px 8px", borderRadius: 999,
              background: "rgba(239,83,80,0.15)", color: "#EF5350",
              border: "1px solid rgba(239,83,80,0.3)",
              fontFamily: "Orbitron, sans-serif",
            }}>
              {critical} CRITICAL
            </span>
          )}
          {high > 0 && (
            <span style={{
              fontSize: 10, padding: "2px 8px", borderRadius: 999,
              background: "rgba(255,143,0,0.15)", color: "#FF8F00",
              border: "1px solid rgba(255,143,0,0.3)",
              fontFamily: "Orbitron, sans-serif",
            }}>
              {high} HIGH
            </span>
          )}
        </div>
      </div>

      <div style={{
        flex: 1,
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        paddingRight: 4,
      }}>
        <AnimatePresence>
          {sorted.map((sat, i) => (
            <SatelliteCard key={sat.id} sat={sat} index={i} />
          ))}
        </AnimatePresence>
        {!sorted.length && (
          <div style={{
            color: "#607D8B",
            fontSize: 12,
            border: "1px dashed rgba(0,212,255,0.16)",
            borderRadius: 8,
            padding: 14,
            textAlign: "center",
          }}>
            Waiting for live satellite risk catalog
          </div>
        )}
      </div>
    </div>
  );
});
