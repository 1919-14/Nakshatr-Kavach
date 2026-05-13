import React, { memo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { getStormClass } from "../../utils/stormClassifier";
import { getRiskColor, getRiskLabel, getRiskBg } from "../../utils/riskColorMapper";
import { useAnimatedNumber, useCountdown } from "../../hooks/index";
import { formatCountdown } from "../../utils/timeFormatter";

// ── GlassCard ─────────────────────────────────────────────────────────────────
export const GlassCard = memo(({ children, className="", style={}, onClick, bright=false }) => (
  <motion.div
    className={`glass${bright?"-bright":""} ${className}`}
    style={{ padding:"14px 16px", ...style }}
    onClick={onClick}
    whileHover={onClick?{borderColor:"rgba(0,212,255,0.4)",scale:1.005}:undefined}
    transition={{ duration:0.2 }}
  >{children}</motion.div>
));

// ── Skeleton ──────────────────────────────────────────────────────────────────
export const Skeleton = ({ height=40, width="100%", borderRadius=8 }) => (
  <div className="skeleton" style={{ height, width, borderRadius }} />
);

// ── PageSkeleton ──────────────────────────────────────────────────────────────
export const PageSkeleton = () => (
  <div style={{ minHeight:"100vh", background:"var(--color-bg-primary)",
    paddingTop:80, padding:"80px 20px 20px", display:"grid", gap:12 }}>
    <div style={{ display:"flex", gap:10 }}>
      {[1,2,3,4,5,6].map(i=>(
        <div key={i} className="skeleton" style={{ flex:1, height:80, borderRadius:10 }} />
      ))}
    </div>
    <div style={{ display:"grid", gridTemplateColumns:"400px 1fr 360px", gap:10, flex:1 }}>
      <div className="skeleton" style={{ height:600, borderRadius:12 }} />
      <div style={{ display:"grid", gridTemplateRows:"120px 1fr 1fr", gap:10 }}>
        <div className="skeleton" style={{ borderRadius:10 }} />
        <div className="skeleton" style={{ borderRadius:12 }} />
        <div className="skeleton" style={{ borderRadius:12 }} />
      </div>
      <div style={{ display:"grid", gridTemplateRows:"1fr 1fr", gap:10 }}>
        <div className="skeleton" style={{ borderRadius:12 }} />
        <div className="skeleton" style={{ borderRadius:12 }} />
      </div>
    </div>
    <div className="skeleton" style={{ height:200, borderRadius:12 }} />
  </div>
);

// ── StormClassBadge ───────────────────────────────────────────────────────────
export const StormClassBadge = memo(({ kp, large=false }) => {
  const sc = getStormClass(kp);
  return (
    <AnimatePresence mode="wait">
      <motion.div key={sc.label}
        initial={{ scale:0.8, opacity:0 }} animate={{ scale:1, opacity:1 }}
        exit={{ scale:1.1, opacity:0 }}
        transition={{ type:"spring", stiffness:400, damping:20 }}
        className={sc.glowClass}
        style={{ display:"inline-flex", alignItems:"center", gap:8,
          padding:large?"8px 20px":"5px 14px", borderRadius:999,
          background:sc.bg, border:`1.5px solid ${sc.color}`,
          color:sc.color, fontFamily:"Orbitron,sans-serif",
          fontSize:large?18:13, fontWeight:700, letterSpacing:"0.08em",
          cursor:"default", userSelect:"none" }}>
        <span style={{ width:large?10:7, height:large?10:7, borderRadius:"50%",
          background:sc.color, boxShadow:`0 0 8px ${sc.color}`, flexShrink:0 }} />
        {sc.label}
      </motion.div>
    </AnimatePresence>
  );
});

// ── RiskLevelBadge ────────────────────────────────────────────────────────────
export const RiskLevelBadge = memo(({ score, label:overrideLabel }) => {
  const color = getRiskColor(score);
  const label = overrideLabel || getRiskLabel(score);
  return (
    <span style={{ display:"inline-block", padding:"2px 10px", borderRadius:999,
      background:getRiskBg(score), border:`1px solid ${color}66`,
      color, fontFamily:"Orbitron,sans-serif", fontSize:10, fontWeight:700,
      letterSpacing:"0.1em" }}>
      {label}
    </span>
  );
});

// ── OrbitTypeBadge ────────────────────────────────────────────────────────────
export const OrbitTypeBadge = memo(({ type }) => {
  const colors = {
    LEO:{color:"#00D4FF",bg:"rgba(0,212,255,0.12)"},
    GEO:{color:"#FFD700",bg:"rgba(255,215,0,0.12)"},
    L1: {color:"#FF6B35",bg:"rgba(255,107,53,0.12)"},
    MEO:{color:"#AB47BC",bg:"rgba(171,71,188,0.12)"},
    LUNAR:{color:"#AAAAFF",bg:"rgba(170,170,255,0.12)"},
  };
  const c = colors[type] || colors.LEO;
  return (
    <span style={{ padding:"2px 8px", borderRadius:999, background:c.bg,
      border:`1px solid ${c.color}44`, color:c.color, fontSize:10,
      fontFamily:"JetBrains Mono,monospace", fontWeight:500 }}>
      {type}
    </span>
  );
});

// ── AnimatedNumber ────────────────────────────────────────────────────────────
export const AnimatedNumber = memo(({ value, decimals=1, style={}, prefix="", suffix="" }) => {
  const animated = useAnimatedNumber(parseFloat(value)||0);
  return <motion.span style={style}>{prefix}{animated.toFixed(decimals)}{suffix}</motion.span>;
});

// ── CircularProgress ──────────────────────────────────────────────────────────
export const CircularProgress = memo(({ score, size=64, strokeWidth=4 }) => {
  const color  = getRiskColor(score);
  const r      = (size - strokeWidth*2) / 2;
  const circ   = 2 * Math.PI * r;
  const offset = circ - (score/100)*circ;
  const cx     = size/2;
  return (
    <div style={{ position:"relative", width:size, height:size, flexShrink:0 }}>
      <svg width={size} height={size} style={{ transform:"rotate(-90deg)" }}>
        <circle cx={cx} cy={cx} r={r} fill="none"
          stroke="rgba(255,255,255,0.06)" strokeWidth={strokeWidth} />
        <motion.circle cx={cx} cy={cx} r={r} fill="none" stroke={color}
          strokeWidth={strokeWidth} strokeLinecap="round"
          strokeDasharray={circ}
          initial={{ strokeDashoffset:circ }}
          animate={{ strokeDashoffset:offset }}
          transition={{ duration:1, ease:"easeOut" }} />
      </svg>
      <div style={{ position:"absolute", inset:0, display:"flex",
        flexDirection:"column", alignItems:"center", justifyContent:"center" }}>
        <span style={{ fontFamily:"Orbitron,sans-serif", fontSize:size*0.22,
          fontWeight:700, color, lineHeight:1 }}>{score}</span>
        <span style={{ fontSize:size*0.13, color:"#546E7A", lineHeight:1.2 }}>%</span>
      </div>
    </div>
  );
});

// ── CountdownTimer ────────────────────────────────────────────────────────────
export const CountdownTimer = memo(({ totalSeconds, label="", urgent=false }) => {
  const remaining = useCountdown(totalSeconds);
  const isUrgent  = remaining < 600;
  const color     = isUrgent?"#EF5350":"#FF9800";
  return (
    <motion.div
      animate={isUrgent?{x:[0,-3,3,-2,2,0]}:{}}
      transition={isUrgent?{repeat:Infinity,duration:0.5,repeatDelay:3}:{}}
      style={{ display:"inline-flex", alignItems:"center", gap:8,
        padding:"4px 12px", borderRadius:999,
        background:`${color}18`, border:`1px solid ${color}55`,
        color, fontFamily:"Orbitron,sans-serif", fontSize:12, fontWeight:700 }}>
      <span style={{ fontSize:10 }}>⏱</span>
      {label&&<span style={{ opacity:0.7, fontSize:10 }}>{label}</span>}
      <span>{formatCountdown(remaining)}</span>
    </motion.div>
  );
});

// ── SectionLabel ──────────────────────────────────────────────────────────────
export const SectionLabel = ({ children }) => (
  <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:10, fontWeight:600,
    color:"var(--color-electric)", letterSpacing:"0.14em", textTransform:"uppercase",
    marginBottom:10, display:"flex", alignItems:"center", gap:8 }}>
    {children}
    <div style={{ flex:1, height:1, background:"rgba(0,212,255,0.2)" }} />
  </div>
);

// ── StatusDot ─────────────────────────────────────────────────────────────────
export const StatusDot = memo(({ status, label }) => {
  const colors = { online:"#4CAF50", degraded:"#FF9800", offline:"#EF5350" };
  const c = colors[status]||"#607D8B";
  return (
    <div style={{ display:"flex", alignItems:"center", gap:5, fontSize:11 }}>
      <motion.div animate={{ opacity:[1,0.4,1] }} transition={{ duration:2, repeat:Infinity }}
        style={{ width:6, height:6, borderRadius:"50%", background:c, boxShadow:`0 0 6px ${c}` }} />
      <span style={{ color:"#546E7A", fontFamily:"JetBrains Mono,monospace" }}>{label}</span>
    </div>
  );
});

// ── AnimatedBar ───────────────────────────────────────────────────────────────
export const AnimatedBar = memo(({ value, max=100, color, label, showValue=true, height=5 }) => {
  const pct = Math.min(100, (value/max)*100);
  return (
    <div style={{ marginBottom:6 }}>
      {(label||showValue)&&(
        <div style={{ display:"flex", justifyContent:"space-between",
          fontSize:11, marginBottom:3, color:"var(--color-text-secondary)",
          fontFamily:"JetBrains Mono,monospace" }}>
          {label&&<span>{label}</span>}
          {showValue&&<span style={{ color }}>{value.toFixed(0)}%</span>}
        </div>
      )}
      <div style={{ height, background:"rgba(255,255,255,0.06)",
        borderRadius:height/2, overflow:"hidden" }}>
        <motion.div initial={{ width:0 }} animate={{ width:`${pct}%` }}
          transition={{ duration:0.8, ease:"easeOut" }}
          style={{ height:"100%", background:color, borderRadius:height/2 }} />
      </div>
    </div>
  );
});

// ── ErrorCard ─────────────────────────────────────────────────────────────────
export const ErrorCard = ({ message, height="100%" }) => (
  <div style={{ height, display:"flex", flexDirection:"column",
    alignItems:"center", justifyContent:"center", gap:8,
    background:"var(--color-bg-card)", borderRadius:12,
    border:"1px solid rgba(244,67,54,0.2)", padding:16 }}>
    <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:10,
      color:"#EF5350", letterSpacing:"0.1em" }}>PANEL ERROR</div>
    <div style={{ fontSize:11, color:"#546E7A", textAlign:"center",
      fontFamily:"JetBrains Mono,monospace", maxWidth:200 }}>{message}</div>
  </div>
);
