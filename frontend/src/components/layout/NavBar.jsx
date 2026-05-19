import React, { memo } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { useStormStore } from "../../store/useStormStore";
import { StormClassBadge, StatusDot } from "../ui/index";
import { useNowIST } from "../../hooks/index";

const NAV_LINKS = [
  { path: "/",           label: "Dashboard"  },
  { path: "/storm-sim",  label: "Storm Sim"  },
  { path: "/satellites", label: "Satellites" },
  { path: "/grid",       label: "Grid"       },
  { path: "/replay",     label: "Replay"     },
];

// Satellite orbit arc SVG logo
const Logo = () => (
  <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
    <circle cx="16" cy="16" r="6" fill="none" stroke="#00D4FF" strokeWidth="1.5"/>
    <circle cx="16" cy="16" r="2.5" fill="#00D4FF" opacity="0.9"/>
    <ellipse cx="16" cy="16" rx="14" ry="6" fill="none"
      stroke="#00D4FF" strokeWidth="1" strokeDasharray="3 2" opacity="0.5"
      transform="rotate(-30 16 16)"/>
    <circle cx="26" cy="10" r="1.8" fill="#FFD700"
      style={{ filter: "drop-shadow(0 0 3px #FFD700)" }}/>
    <path d="M4 28 L28 4" stroke="#FF6B35" strokeWidth="0.8" opacity="0.4" strokeDasharray="2 3"/>
  </svg>
);

export const NavBar = memo(() => {
  const { pathname } = useLocation();
  const { solarWind, kpForecast, systemStatus, replayMode, replayStormName } = useStormStore();
  const nowIST  = useNowIST();
  const asNum = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const kpCurrent = asNum(solarWind?.kp_current);
  const kp3h = asNum(kpForecast?.kp_3hr?.value);
  const badgeKp = kpCurrent ?? kp3h ?? 0;

  return (
    <motion.nav
      initial={{ y: -64 }}
      animate={{ y: 0 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      style={{
        position:        "fixed",
        top:             0, left: 0, right: 0,
        height:          64,
        zIndex:          1000,
        display:         "flex",
        alignItems:      "center",
        gap:             16,
        padding:         "0 20px",
        background:      "rgba(2,8,23,0.92)",
        backdropFilter:  "blur(20px)",
        borderBottom:    "1px solid rgba(0,212,255,0.12)",
      }}
    >
      {/* ── LEFT: Logo ── */}
      <Link to="/" style={{ textDecoration: "none", display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <Logo />
        <div>
          <div style={{
            fontFamily:    "Orbitron, sans-serif",
            fontSize:      14, fontWeight: 800,
            color:         "#00D4FF",
            letterSpacing: "0.08em",
            lineHeight:    1.1,
          }}>
            NAKSHATRA-KAVACH
          </div>
          <div style={{
            fontFamily: "Space Grotesk, sans-serif",
            fontSize:   9, color: "#546E7A",
            letterSpacing: "0.15em", lineHeight: 1.3,
          }}>
            SPACE WEATHER INTELLIGENCE
          </div>
        </div>
      </Link>

      {/* ── CENTER: Storm badge + Kp ── */}
      <div style={{
        flex: 1, display: "flex", alignItems: "center",
        justifyContent: "center", gap: 14,
      }}>
        <StormClassBadge kp={badgeKp} />
        {replayMode && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            style={{
              padding: "5px 11px",
              borderRadius: 999,
              background: "rgba(255,152,0,0.18)",
              border: "1px solid rgba(255,152,0,0.55)",
              color: "#FFB74D",
              fontFamily: "Orbitron, sans-serif",
              fontSize: 10,
              fontWeight: 800,
              maxWidth: 220,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={replayStormName || "Historical replay active"}
          >
            REPLAY{replayStormName ? `: ${replayStormName}` : ""}
          </motion.div>
        )}
        <div style={{ display:"flex", alignItems:"baseline", gap:10 }}>
          <div style={{
            fontFamily: "Orbitron, sans-serif",
            fontSize:   20, fontWeight: 800,
            color:      "#FFD700",
            letterSpacing: "0.04em",
            textShadow: "0 0 16px rgba(255,215,0,0.5)",
          }}>
            Kp Current {kpCurrent === null ? "--" : kpCurrent.toFixed(1)}
          </div>
          <div style={{
            fontFamily:"JetBrains Mono, monospace",
            fontSize:11,
            color:"#00D4FF",
            whiteSpace:"nowrap",
          }}>
            +3h {kp3h === null ? "--" : kp3h.toFixed(1)}
          </div>
        </div>
      </div>

      {/* ── RIGHT: status + time + nav ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>

        {/* System status dots */}
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <StatusDot status={systemStatus.noaa} label="NOAA" />
          <StatusDot status={systemStatus.ml}   label="ML"   />
          <StatusDot status={systemStatus.llm}  label="LLM"  />
        </div>

        {/* Time */}
        <div style={{
          fontFamily: "JetBrains Mono, monospace",
          fontSize:   11, color: "#546E7A",
          letterSpacing: "0.04em",
        }}>
          {nowIST}
        </div>

        {/* Nav links */}
        <div style={{ display: "flex", gap: 4 }}>
          {NAV_LINKS.map(({ path, label }) => {
            const active = pathname === path;
            return (
              <Link key={path} to={path} style={{ textDecoration: "none" }}>
                <motion.div
                  whileHover={{ color: "#00D4FF", background: "rgba(0,212,255,0.08)" }}
                  style={{
                    padding:      "5px 10px",
                    borderRadius: 6,
                    fontFamily:   "Space Grotesk, sans-serif",
                    fontSize:     12, fontWeight: active ? 600 : 400,
                    color:        active ? "#00D4FF" : "#90A4AE",
                    background:   active ? "rgba(0,212,255,0.1)" : "transparent",
                    border:       active ? "1px solid rgba(0,212,255,0.3)" : "1px solid transparent",
                    letterSpacing:"0.04em",
                    transition:   "all 0.2s",
                  }}
                >
                  {label}
                </motion.div>
              </Link>
            );
          })}
        </div>
      </div>
    </motion.nav>
  );
});

export default NavBar;
