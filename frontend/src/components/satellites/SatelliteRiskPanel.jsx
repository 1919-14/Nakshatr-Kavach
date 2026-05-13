import React, { memo, useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../../store/useStormStore";
import { MOCK_SATELLITES } from "../../mock/mockData";
import {
  getRiskColor, getRiskLabel, getRiskBg,
} from "../../utils/riskColorMapper";
import {
  CircularProgress, AnimatedBar, RiskLevelBadge,
  OrbitTypeBadge, CountdownTimer, SectionLabel,
} from "../ui/index";

// ── Single Satellite Card ─────────────────────────────────────────────────────
const SatelliteCard = memo(({ sat, index }) => {
  const [expanded, setExpanded] = useState(false);
  const riskColor  = getRiskColor(sat.composite_risk);
  const isCritical = sat.composite_risk > 80;
  const isHigh     = sat.composite_risk > 60;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.06, duration: 0.4 }}
      onClick={() => setExpanded(e => !e)}
      style={{
        background:   getRiskBg(sat.composite_risk),
        borderRadius: 8,
        borderLeft:   `4px solid ${riskColor}`,
        padding:      "11px 13px",
        cursor:       "pointer",
        border:       `1px solid ${riskColor}22`,
        borderLeft:   `4px solid ${riskColor}`,
        boxShadow:    isCritical
          ? `0 0 16px ${riskColor}33`
          : "none",
        position:     "relative",
        overflow:     "hidden",
      }}
    >
      {/* Critical pulse */}
      {isCritical && (
        <motion.div
          animate={{ opacity: [0, 0.08, 0] }}
          transition={{ duration: 2, repeat: Infinity }}
          style={{
            position: "absolute", inset: 0,
            background: riskColor, pointerEvents: "none",
          }}
        />
      )}

      {/* Row 1: Name + badges */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
        <span style={{
          fontFamily: "Space Grotesk, sans-serif",
          fontWeight: 700, fontSize: 13,
          color: "#E8F4FD", flex: 1,
        }}>
          {sat.name}
        </span>
        <OrbitTypeBadge type={sat.type} />
        <RiskLevelBadge score={sat.composite_risk} />
      </div>

      {/* Row 2: Risk bars */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 8 }}>
        <AnimatedBar value={sat.drag_risk}      color="#FF9800" label="🌪 DRAG"      height={4} />
        <AnimatedBar value={sat.charging_risk}  color="#FDD835" label="⚡ CHARGE"    height={4} />
        <AnimatedBar value={sat.radiation_risk} color="#00D4FF" label="☢ RAD"        height={4} />
      </div>

      {/* Row 3: Composite score */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <CircularProgress score={sat.composite_risk} size={48} strokeWidth={3} />
        <div style={{ flex: 1 }}>
          <div style={{
            fontSize: 10, color: "#546E7A",
            fontFamily: "JetBrains Mono, monospace",
            marginBottom: 3,
          }}>
            COMPOSITE RISK
          </div>
          {sat.safe_mode_minutes && (
            <CountdownTimer
              totalSeconds={sat.safe_mode_minutes * 60}
              label="SAFE MODE"
              urgent={sat.safe_mode_minutes < 10}
            />
          )}
          {!sat.safe_mode_minutes && (
            <div style={{
              fontSize: 11, color: riskColor,
              fontFamily: "Space Grotesk, sans-serif",
            }}>
              {sat.action?.split(".")[0]}
            </div>
          )}
        </div>
        <motion.div
          animate={{ rotate: expanded ? 180 : 0 }}
          style={{ color: "#546E7A", fontSize: 12 }}
        >
          ▼
        </motion.div>
      </div>

      {/* Expanded details */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{   height: 0, opacity: 0 }}
            transition={{ duration: 0.3 }}
            style={{ overflow: "hidden" }}
          >
            <div style={{
              marginTop:    10,
              paddingTop:   10,
              borderTop:    `1px solid ${riskColor}22`,
              fontSize:     12,
              lineHeight:   1.6,
            }}>
              {/* Specs */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", marginBottom: 8 }}>
                {[
                  ["Altitude",  `${sat.altitude?.toLocaleString()} km`],
                  ["Orbit",     sat.type],
                  ["Inclination", `${sat.inclination ?? 0}°`],
                  ["Risk score", `${sat.composite_risk}%`],
                ].map(([l, v]) => (
                  <div key={l} style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#546E7A" }}>{l}</span>
                    <span style={{ color: "#90A4AE" }}>{v}</span>
                  </div>
                ))}
              </div>
              {/* Mission */}
              <div style={{ color: "#546E7A", fontSize: 11, marginBottom: 6 }}>
                {sat.mission}
              </div>
              {/* Full advisory */}
              <div style={{
                background:   `${riskColor}11`,
                border:       `1px solid ${riskColor}33`,
                borderRadius: 6,
                padding:      "8px 10px",
                color:        "#E8F4FD",
                fontSize:     11,
                lineHeight:   1.6,
              }}>
                ▸ {sat.action}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
});

// ── Satellite Risk Panel ──────────────────────────────────────────────────────
export const SatelliteRiskPanel = memo(() => {
  const { satellites } = useStormStore();
  const satList = satellites?.length ? satellites : MOCK_SATELLITES;

  // Sort by composite risk highest first
  const sorted = useMemo(() =>
    [...satList].sort((a, b) => b.composite_risk - a.composite_risk),
    [satList]
  );

  const critical = sorted.filter(s => s.composite_risk > 80).length;
  const high     = sorted.filter(s => s.composite_risk > 60 && s.composite_risk <= 80).length;

  return (
    <div style={{
      height:        "100%",
      background:    "var(--color-bg-card)",
      borderRadius:  12,
      border:        "1px solid rgba(0,212,255,0.12)",
      padding:       "14px 14px 8px",
      display:       "flex",
      flexDirection: "column",
      gap:           8,
      overflow:      "hidden",
    }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <SectionLabel>Satellite Risk</SectionLabel>
        <div style={{ display: "flex", gap: 6 }}>
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

      {/* Scrollable card list */}
      <div style={{
        flex:       1,
        overflowY:  "auto",
        display:    "flex",
        flexDirection: "column",
        gap:        6,
        paddingRight: 2,
      }}>
        <AnimatePresence>
          {sorted.map((sat, i) => (
            <SatelliteCard key={sat.id} sat={sat} index={i} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
});
