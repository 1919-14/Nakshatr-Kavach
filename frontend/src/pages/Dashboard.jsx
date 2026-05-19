import React, { memo, useState, useEffect } from "react";
import { motion } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { EarthGlobe }          from "../components/earth/EarthGlobe";
import { SolarTelemetryStrip } from "../components/solar/SolarTelemetryStrip";
import { KpForecastChart }     from "../components/forecast/KpForecastChart";
import { KpPredictionCards }   from "../components/forecast/KpPredictionCards";
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
    style={{ background:"linear-gradient(180deg, rgba(13,27,62,0.96), rgba(5,16,35,0.96))", border:`1px solid ${color}33`,
      borderRadius:8, padding:"10px 14px", flex:1, minWidth:0,
      boxShadow:`0 0 18px ${color}12, inset 0 1px 0 rgba(255,255,255,0.04)` }}>
    <div style={{ fontSize:9, color:"#546E7A", fontFamily:"Orbitron,sans-serif",
      letterSpacing:"0.1em", marginBottom:3 }}>{label}</div>
    <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:18,
      fontWeight:800, color, lineHeight:1 }}>
      {typeof value==="number" ? value.toFixed(label.toUpperCase().includes("KP")?1:0) : value}
      {unit&&<span style={{ fontSize:10, fontWeight:400, marginLeft:3, color:"#546E7A" }}>{unit}</span>}
    </div>
    {sub&&<div style={{ fontSize:9, color:"#546E7A", marginTop:2,
      fontFamily:"JetBrains Mono,monospace" }}>{sub}</div>}
  </motion.div>
));

const HistoricalWatermark = memo(() => (
  <div style={{
    position: "fixed",
    inset: 0,
    zIndex: 30,
    pointerEvents: "none",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "rgba(255,152,0,0.14)",
    fontFamily: "Orbitron,sans-serif",
    fontSize: "clamp(34px,8vw,112px)",
    fontWeight: 900,
    letterSpacing: "0.08em",
    transform: "rotate(-14deg)",
    textAlign: "center",
  }}>
    HISTORICAL DATA
  </div>
));

// ── Quick advisory mini panel ─────────────────────────────────────────────────
const QuickAdvisory = memo(() => {
  const { satellites, gridCorridors, shapExplain, kpForecast } = useStormStore();
  const liveSatItems = [...(satellites || [])]
    .sort((a, b) => Number(b.composite_risk || 0) - Number(a.composite_risk || 0))
    .slice(0, 3)
    .map((sat) => ({
      color: sat.composite_risk >= 60 ? "#F44336" : sat.composite_risk >= 30 ? "#FF9800" : "#4CAF50",
      text: `${sat.name}: ${Number(sat.composite_risk || 0).toFixed(0)}% ${sat.risk_level || "risk"}`,
    }));
  const items = [
    { color:"#F44336", text:"INSAT-3DR: safe mode in 35 min" },
    { color:"#FF9800", text:"Cartosat-3: drag elevated 2.3×" },
    { color:"#FF9800", text:"NavIC: accuracy ±8m degraded" },
    { color:"#FF9800", text:"RJ-GJ 400kV corridor at risk" },
    { color:"#4CAF50", text:"Aditya-L1: storm observation mode" },
  ];
  const grid = [...(gridCorridors || [])].sort((a, b) => Number(b.risk_percent || 0) - Number(a.risk_percent || 0))[0];
  const shap = shapExplain?.features?.[0];
  const liveItems = [
    ...liveSatItems,
    grid ? { color:"#FF9800", text:`${grid.name}: ${grid.gic_amps}A GIC, ${grid.risk_percent}% grid risk` } : null,
    shap ? { color:"#00D4FF", text:`Top SHAP driver: ${shap.feature} (${Number(shap.shap_value || 0).toFixed(3)})` } : null,
    kpForecast ? { color:"#FFD700", text:`Peak class: ${kpForecast.peak_storm_class || "UNKNOWN"}` } : null,
  ].filter(Boolean).slice(0, 5);
  const displayItems = liveItems.length ? liveItems : [{ color:"#607D8B", text:"Waiting for live advisory context from backend" }];
  return (
    <div style={{ height:"100%", background:"linear-gradient(180deg, rgba(13,27,62,0.94), rgba(5,16,35,0.96))", borderRadius:8,
      border:"1px solid rgba(0,212,255,0.12)", padding:"14px", overflow:"hidden",
      display:"flex", flexDirection:"column" }}>
      <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:9, color:"#00D4FF",
        letterSpacing:"0.12em", marginBottom:10 }}>QUICK ADVISORY</div>
      {displayItems.map((item,i)=>(
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
  const { solarWind, satellites, kpForecast } = useStormStore();
  const asNum = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const kpCurrent = asNum(solarWind?.kp_current);
  const kp3h      = asNum(kpForecast?.kp_3hr?.value);
  const bz       = solarWind?.bz_gsm        ?? 0;
  const speed    = solarWind?.sw_speed      ?? 0;
  const highRisk = (satellites||[]).filter(s=>s.composite_risk>60).length;
  const historical = Boolean(solarWind?.is_historical);

  // Mobile detection
  const [mobile, setMobile] = useState(window.innerWidth < 768);
  useEffect(()=>{
    const fn = () => setMobile(window.innerWidth < 768);
    window.addEventListener("resize", fn);
    return () => window.removeEventListener("resize", fn);
  },[]);

  if (mobile) {
    return (
      <div style={{ minHeight:"100vh", background:"linear-gradient(180deg,#020817 0%,#061126 52%,#020817 100%)",
        paddingTop:74, padding:"74px 12px 20px",
        display:"flex", flexDirection:"column", gap:10, position:"relative" }}>
        {historical && <HistoricalWatermark />}
        {/* Stats row - horizontal scroll */}
        <div style={{ display:"flex", gap:8, overflowX:"auto", paddingBottom:4 }}>
          <StatCard label="KP CURRENT" value={kpCurrent === null ? "--" : kpCurrent} unit="" color="#FFD700" sub={kpCurrent === null ? "NOAA Kp unavailable" : (solarWind?.storm_class||"OBSERVED")} />
          <StatCard label="KP FORECAST 3H" value={kp3h === null ? "--" : kp3h} unit="" color="#00D4FF" sub={kpForecast?.prediction_confidence || "MODEL"} />
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
        <div style={{ minHeight:330 }}>
          <StormProbabilityGauge />
          <div style={{ marginTop: 6 }}>
            <PanelBoundary><KpPredictionCards /></PanelBoundary>
          </div>
          <div style={{ height: 170, marginTop: 6 }}>
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
    <div style={{ minHeight:"100vh", background:"linear-gradient(180deg,#020817 0%,#061126 48%,#020817 100%)", paddingTop:74, position:"relative", overflow:"hidden" }}>
      <div style={{
        position:"fixed", inset:0, pointerEvents:"none", opacity:0.14,
        background:"linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(180deg, rgba(255,255,255,0.025) 1px, transparent 1px)",
        backgroundSize:"72px 72px", zIndex:0,
      }} />
      <div style={{
        position:"fixed", inset:0, pointerEvents:"none",
        background:"linear-gradient(180deg, rgba(0,212,255,0.05), transparent 22%, rgba(255,152,0,0.035) 58%, transparent)",
        zIndex:0,
      }} />
      {historical && <HistoricalWatermark />}
      <motion.div variants={containerVariants} initial="hidden" animate="visible"
        style={{ display:"grid",
          gridTemplateColumns:"clamp(300px,28vw,400px) 1fr clamp(260px,24vw,360px)",
          gridTemplateRows:"auto auto 1fr auto auto",
          gap:10, padding:"10px 14px 14px", minHeight:"calc(100vh - 74px)", position:"relative", zIndex:1 }}>

        {/* Stats strip */}
        <motion.div variants={panelVariants} style={{ gridColumn:"1 / -1", display:"flex", gap:8 }}>
          <StatCard label="KP CURRENT"   value={kpCurrent === null ? "--" : kpCurrent} unit="" color="#FFD700" sub={kpCurrent === null ? "NOAA Kp unavailable" : (solarWind?.storm_class||"OBSERVED")} />
          <StatCard label="KP FORECAST 3H" value={kp3h === null ? "--" : kp3h} unit="" color="#00D4FF" sub={kpForecast?.prediction_confidence || "MODEL"} />
          <StatCard label="Bz"           value={bz}    unit="nT"    color={bz<-10?"#F44336":"#4CAF50"} sub="Southward=danger" />
          <StatCard label="WIND SPEED"   value={speed} unit="km/s"  color="#00D4FF" sub="Solar wind" />
          <StatCard label="DENSITY"      value={solarWind?.proton_density??0} unit="p/cm3" color="#AB47BC" sub="Proton density" />
          <StatCard label="X-RAY"        value={solarWind?.xray_class??"-"} unit="" color="#FF9800" sub="Flare class" />
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
        <motion.div variants={panelVariants} style={{ gridColumn:2, display:"flex", flexDirection:"column", gap:6 }}>
          <StormProbabilityGauge />
          <PanelBoundary><KpPredictionCards /></PanelBoundary>
          <div style={{ height: 220 }}>
            <PanelBoundary><KpForecastChart /></PanelBoundary>
          </div>
          <div style={{ height: 200 }}>
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
