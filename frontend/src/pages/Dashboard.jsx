import React, { memo, useState, useEffect } from "react";
import { motion } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { EarthGlobe }          from "../components/earth/EarthGlobe";
import { SolarTelemetryStrip } from "../components/solar/SolarTelemetryStrip";
import { KpForecastChart }     from "../components/forecast/KpForecastChart";
import { StormProbabilityGauge } from "../components/forecast/StormProbabilityGauge";
import { ShapExplainPanel }    from "../components/forecast/ShapExplainPanel";
import { SatelliteRiskPanel }  from "../components/satellites/SatelliteRiskPanel";
import { IndiaGridMap }        from "../components/grid/IndiaGridMap";
import { AdvisoryPanel }       from "../components/advisory/AdvisoryPanel";
import { ErrorCard }           from "../components/ui/index";

// ── Error boundary ────────────────────────────────────────────────────────────
class PanelBoundary extends React.Component {
  state = { error:null };
  static getDerivedStateFromError(e) { return { error:e }; }
  render() {
    if (this.state.error)
      return <ErrorCard message={this.state.error?.message} height="100%" />;
    return this.props.children;
  }
}

// ── Stat card ─────────────────────────────────────────────────────────────────
const StatCard = memo(({ label, value, unit, color, sub }) => (
  <motion.div initial={{ opacity:0, y:-10 }} animate={{ opacity:1, y:0 }}
    style={{ background:"var(--color-bg-card)", border:`1px solid ${color}33`,
      borderRadius:10, padding:"10px 14px", flex:1, minWidth:0,
      boxShadow:`0 0 16px ${color}15` }}>
    <div style={{ fontSize:9, color:"#546E7A", fontFamily:"Orbitron,sans-serif",
      letterSpacing:"0.1em", marginBottom:3 }}>{label}</div>
    <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:18,
      fontWeight:800, color, lineHeight:1 }}>
      {typeof value==="number" ? value.toFixed(label.includes("Kp")?1:0) : value}
      {unit&&<span style={{ fontSize:10, fontWeight:400, marginLeft:3, color:"#546E7A" }}>{unit}</span>}
    </div>
    {sub&&<div style={{ fontSize:9, color:"#546E7A", marginTop:2,
      fontFamily:"JetBrains Mono,monospace" }}>{sub}</div>}
  </motion.div>
));

// ── Quick advisory mini panel ─────────────────────────────────────────────────
const QuickAdvisory = memo(() => {
  const items = [
    { color:"#F44336", text:"INSAT-3DR: safe mode in 35 min" },
    { color:"#FF9800", text:"Cartosat-3: drag elevated 2.3×" },
    { color:"#FF9800", text:"NavIC: accuracy ±8m degraded" },
    { color:"#FF9800", text:"RJ-GJ 400kV corridor at risk" },
    { color:"#4CAF50", text:"Aditya-L1: storm observation mode" },
  ];
  return (
    <div style={{ height:"100%", background:"var(--color-bg-card)", borderRadius:12,
      border:"1px solid rgba(0,212,255,0.12)", padding:"14px", overflow:"hidden",
      display:"flex", flexDirection:"column" }}>
      <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:9, color:"#00D4FF",
        letterSpacing:"0.12em", marginBottom:10 }}>QUICK ADVISORY</div>
      {items.map((item,i)=>(
        <motion.div key={i} initial={{ opacity:0, x:10 }}
          animate={{ opacity:1, x:0 }} transition={{ delay:i*0.08 }}
          style={{ display:"flex", alignItems:"flex-start", gap:8,
            padding:"7px 0", borderBottom:"1px solid rgba(255,255,255,0.04)",
            fontSize:11, lineHeight:1.5, flex:1 }}>
          <div style={{ width:6, height:6, borderRadius:"50%", background:item.color,
            flexShrink:0, marginTop:3, boxShadow:`0 0 6px ${item.color}` }} />
          <span style={{ color:"#90A4AE" }}>{item.text}</span>
        </motion.div>
      ))}
    </div>
  );
});

const containerVariants = {
  hidden:{}, visible:{ transition:{ staggerChildren:0.07 } },
};
const panelVariants = {
  hidden:  { opacity:0, y:16 },
  visible: { opacity:1, y:0, transition:{ duration:0.45, ease:[0.25,0.1,0.25,1] } },
};

export default function Dashboard() {
  const { solarWind, satellites } = useStormStore();
  const kp       = solarWind?.kp_current    ?? 7.2;
  const bz       = solarWind?.bz_gsm        ?? -18.4;
  const speed    = solarWind?.sw_speed      ?? 720;
  const highRisk = (satellites||[]).filter(s=>s.composite_risk>60).length;

  // Mobile detection
  const [mobile, setMobile] = useState(window.innerWidth < 768);
  useEffect(()=>{
    const fn = () => setMobile(window.innerWidth < 768);
    window.addEventListener("resize", fn);
    return () => window.removeEventListener("resize", fn);
  },[]);

  if (mobile) {
    return (
      <div style={{ minHeight:"100vh", background:"var(--color-bg-primary)",
        paddingTop:74, padding:"74px 12px 20px",
        display:"flex", flexDirection:"column", gap:10 }}>
        {/* Stats row - horizontal scroll */}
        <div style={{ display:"flex", gap:8, overflowX:"auto", paddingBottom:4 }}>
          <StatCard label="Kp"     value={kp}    unit=""     color="#FFD700" sub={solarWind?.storm_class||"G3"} />
          <StatCard label="Bz"     value={bz}    unit="nT"   color={bz<-10?"#F44336":"#4CAF50"} sub="Danger<-10" />
          <StatCard label="SPEED"  value={speed} unit="km/s" color="#00D4FF" sub="Solar wind" />
          <StatCard label="AT RISK" value={highRisk} unit="" color={highRisk>3?"#F44336":"#FF9800"} sub="Satellites" />
        </div>
        {/* Globe */}
        <PanelBoundary><EarthGlobe height="300px" /></PanelBoundary>
        {/* Telemetry */}
        <div style={{ height:110, overflowX:"auto" }}>
          <div style={{ minWidth:600, height:"100%" }}>
            <PanelBoundary><SolarTelemetryStrip /></PanelBoundary>
          </div>
        </div>
        {/* Kp chart */}
        <div style={{ height:220 }}>
          <StormProbabilityGauge />
          <div style={{ height: 160, marginTop: 6 }}>
            <PanelBoundary><KpForecastChart /></PanelBoundary>
          </div>
          <div style={{ height: 120, marginTop: 6 }}>
            <PanelBoundary><ShapExplainPanel /></PanelBoundary>
          </div>
        </div>
        {/* Satellite risk */}
        <div style={{ height:320 }}>
          <PanelBoundary><SatelliteRiskPanel /></PanelBoundary>
        </div>
        {/* Grid map */}
        <div style={{ height:260 }}>
          <PanelBoundary><IndiaGridMap /></PanelBoundary>
        </div>
        {/* Advisory */}
        <PanelBoundary><AdvisoryPanel /></PanelBoundary>
      </div>
    );
  }

  return (
    <div style={{ minHeight:"100vh", background:"var(--color-bg-primary)", paddingTop:74 }}>
      <motion.div variants={containerVariants} initial="hidden" animate="visible"
        style={{ display:"grid",
          gridTemplateColumns:"clamp(300px,28vw,400px) 1fr clamp(260px,24vw,360px)",
          gridTemplateRows:"auto auto 1fr auto auto",
          gap:10, padding:"10px 14px 14px", minHeight:"calc(100vh - 74px)" }}>

        {/* Stats strip */}
        <motion.div variants={panelVariants} style={{ gridColumn:"1 / -1", display:"flex", gap:8 }}>
          <StatCard label="Kp INDEX"     value={kp}    unit=""      color="#FFD700" sub={solarWind?.storm_class||"G3"} />
          <StatCard label="Bz"           value={bz}    unit="nT"    color={bz<-10?"#F44336":"#4CAF50"} sub="Southward=danger" />
          <StatCard label="WIND SPEED"   value={speed} unit="km/s"  color="#00D4FF" sub="Solar wind" />
          <StatCard label="DENSITY"      value={solarWind?.proton_density??12.3} unit="p/cm³" color="#AB47BC" sub="Proton density" />
          <StatCard label="X-RAY"        value={solarWind?.xray_class??"M1.5"} unit="" color="#FF9800" sub="Flare class" />
          <StatCard label="SATS AT RISK" value={highRisk} unit="" color={highRisk>3?"#F44336":highRisk>0?"#FF9800":"#4CAF50"} sub="High risk" />
        </motion.div>

        {/* Earth Globe */}
        <motion.div variants={panelVariants} style={{ gridColumn:1, gridRow:"2 / 6", minHeight:600 }}>
          <PanelBoundary><EarthGlobe height="100%" /></PanelBoundary>
        </motion.div>

        {/* Solar Telemetry */}
        <motion.div variants={panelVariants} style={{ gridColumn:"2 / -1", height:120 }}>
          <PanelBoundary><SolarTelemetryStrip /></PanelBoundary>
        </motion.div>

        {/* Kp Forecast */}
        <motion.div variants={panelVariants} style={{ gridColumn:2, height:420, display:"flex", flexDirection:"column", gap:6 }}>
          <StormProbabilityGauge />
          <div style={{ flex: "1 1 55%", minHeight: 0 }}>
            <PanelBoundary><KpForecastChart /></PanelBoundary>
          </div>
          <div style={{ flex: "1 1 40%", minHeight: 0 }}>
            <PanelBoundary><ShapExplainPanel /></PanelBoundary>
          </div>
        </motion.div>

        {/* Grid Map */}
        <motion.div variants={panelVariants} style={{ gridColumn:3, height:260 }}>
          <PanelBoundary><IndiaGridMap /></PanelBoundary>
        </motion.div>

        {/* Satellite Risk */}
        <motion.div variants={panelVariants} style={{ gridColumn:2, height:320 }}>
          <PanelBoundary><SatelliteRiskPanel /></PanelBoundary>
        </motion.div>

        {/* Quick advisory */}
        <motion.div variants={panelVariants} style={{ gridColumn:3, height:320 }}>
          <PanelBoundary><QuickAdvisory /></PanelBoundary>
        </motion.div>

        {/* Full Advisory */}
        <motion.div variants={panelVariants} style={{ gridColumn:"1 / -1" }}>
          <PanelBoundary><AdvisoryPanel /></PanelBoundary>
        </motion.div>
      </motion.div>
    </div>
  );
}
