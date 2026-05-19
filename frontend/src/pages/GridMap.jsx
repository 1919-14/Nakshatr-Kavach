import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { getRiskColor, getRiskBg } from "../utils/riskColorMapper";
import { IndiaGridMap } from "../components/grid/IndiaGridMap";
import { SectionLabel } from "../components/ui/index";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";

export default function GridMap() {
  const { gridCorridors, solarWind, kpForecast } = useStormStore();
  const corridors = gridCorridors?.length ? gridCorridors : [];
  const [selected, setSelected] = useState(null);
  const sel = corridors.find(c => c.id === selected);
  const asNum = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const kpCurrent = asNum(solarWind?.kp_current);
  const kp3h = asNum(kpForecast?.kp_3hr?.value);
  const totalRisk   = corridors.length ? Math.round(corridors.reduce((s,c)=>s+c.risk_percent,0)/corridors.length) : 0;
  const totalImpact = corridors.reduce((s,c)=>s+c.impact_crore,0);
  const totalPop    = corridors.reduce((s,c)=>s+c.population_millions,0).toFixed(1);
  const peakGic     = corridors.length ? Math.max(...corridors.map(c=>c.gic_amps)) : 0;

  return (
    <div style={{ minHeight:"100vh", background:"var(--color-bg-primary)",
      display:"grid",
      gridTemplateColumns:"1fr 320px",
      gridTemplateRows:"auto 1fr auto",
      gap:10, padding:"84px 14px 14px",
      fontFamily:"Space Grotesk,sans-serif", color:"#E8F4FD" }}>

      {/* ── Top stats bar (full width) ── */}
      <div style={{ gridColumn:"1/-1", display:"flex", gap:8, flexWrap:"wrap" }}>
        {[
          ["AVG GRID RISK",     `${totalRisk}%`,         getRiskColor(totalRisk)],
          ["CORRIDORS AT RISK", `${corridors.filter(c=>c.risk_percent>50).length} / ${corridors.length}`, "#FF8F00"],
          ["ECONOMIC EXPOSURE", `₹${totalImpact}Cr`,     "#FDD835"],
          ["POPULATION AT RISK",`${totalPop}M people`,   "#00D4FF"],
          ["PEAK GIC",          `${peakGic}A`, "#EF5350"],
          ["Kp Current",        kpCurrent === null ? "--" : kpCurrent.toFixed(1), "#FFD700"],
          ["Kp Forecast 3h",    kp3h === null ? "--" : kp3h.toFixed(1), "#00D4FF"],
        ].map(([l,v,c]) => (
          <motion.div key={l} initial={{opacity:0,y:-8}} animate={{opacity:1,y:0}}
            style={{ flex:1, minWidth:120, background:"var(--color-bg-card)",
              borderRadius:10, padding:"10px 14px",
              border:`1px solid ${c}33`, boxShadow:`0 0 14px ${c}15` }}>
            <div style={{ fontSize:9, color:"#546E7A", fontFamily:"Orbitron,sans-serif",
              letterSpacing:"0.1em", marginBottom:3 }}>{l}</div>
            <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:18,
              fontWeight:800, color:c }}>{v}</div>
          </motion.div>
        ))}
      </div>

      {/* ── Map (left) ── */}
      <div style={{ gridColumn:1, gridRow:"2/4", minHeight:500 }}>
        <IndiaGridMap onSelect={setSelected} />
      </div>

      {/* ── Corridor list (right top) ── */}
      <div style={{ gridColumn:2, gridRow:2,
        background:"var(--color-bg-card)", borderRadius:12,
        border:"1px solid rgba(0,212,255,0.12)",
        padding:"14px 14px 8px", overflowY:"auto" }}>
        <SectionLabel>Transmission Corridors</SectionLabel>
        {[...corridors].sort((a,b)=>b.risk_percent-a.risk_percent).map((c,i) => {
          const color  = getRiskColor(c.risk_percent);
          const active = selected===c.id;
          return (
            <motion.div key={c.id} onClick={()=>setSelected(c.id===selected?null:c.id)}
              initial={{opacity:0,x:10}} animate={{opacity:1,x:0}}
              transition={{delay:i*0.05}}
              style={{ padding:"9px 11px", borderRadius:8, marginBottom:6,
                cursor:"pointer", background:active?getRiskBg(c.risk_percent):"transparent",
                border:`1px solid ${active?color+"44":"rgba(255,255,255,0.05)"}`,
                borderLeft:`3px solid ${color}`, transition:"all 0.2s" }}>
              <div style={{ display:"flex", justifyContent:"space-between",
                alignItems:"center", marginBottom:4 }}>
                <span style={{ fontSize:11, fontWeight:600,
                  color:active?"#fff":"#E8F4FD" }}>{c.name}</span>
                <span style={{ fontFamily:"Orbitron,sans-serif", fontSize:12,
                  fontWeight:700, color }}>{c.risk_percent}%</span>
              </div>
              <div style={{ display:"flex", justifyContent:"space-between", fontSize:10 }}>
                <span style={{ color:"#546E7A" }}>{c.voltage} · GIC {c.gic_amps}A</span>
                <span style={{ color:"#546E7A" }}>₹{c.impact_crore}Cr</span>
              </div>
              <div style={{ marginTop:5, height:3,
                background:"rgba(255,255,255,0.06)", borderRadius:2 }}>
                <motion.div initial={{width:0}}
                  animate={{width:`${c.risk_percent}%`}}
                  transition={{duration:0.8, delay:i*0.05}}
                  style={{height:"100%",background:color,borderRadius:2}} />
              </div>
            </motion.div>
          );
        })}
        {!corridors.length && (
          <div style={{ color:"#607D8B", fontSize:12, padding:"12px 4px" }}>
            Waiting for live grid corridor risk data.
          </div>
        )}
      </div>

      {/* ── Detail / chart (right bottom) ── */}
      <div style={{ gridColumn:2, gridRow:3,
        background:"var(--color-bg-card)", borderRadius:12,
        border:"1px solid rgba(0,212,255,0.12)", padding:"14px" }}>
        <AnimatePresence mode="wait">
          {sel ? (
            <motion.div key={sel.id}
              initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} exit={{opacity:0}}>
              <SectionLabel>{sel.name}</SectionLabel>
              <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:8, marginBottom:10 }}>
                {[
                  ["GIC Amplitude", `${sel.gic_amps}A`,              getRiskColor(sel.risk_percent)],
                  ["Risk",          `${sel.risk_percent}%`,           getRiskColor(sel.risk_percent)],
                  ["Economic risk", `₹${sel.impact_crore}Cr`,         "#FDD835"],
                  ["Population",    `${sel.population_millions}M`,    "#00D4FF"],
                ].map(([l,v,c]) => (
                  <div key={l} style={{ background:"var(--color-bg-secondary)",
                    borderRadius:7, padding:"8px 10px" }}>
                    <div style={{ fontSize:9, color:"#546E7A",
                      fontFamily:"Orbitron,sans-serif", letterSpacing:"0.08em", marginBottom:2 }}>{l}</div>
                    <div style={{ fontFamily:"Orbitron,sans-serif",
                      fontSize:14, fontWeight:700, color:c }}>{v}</div>
                  </div>
                ))}
              </div>
              <div style={{ fontSize:11, lineHeight:1.65,
                color:"#FF9800", padding:"8px 10px",
                background:"rgba(255,143,0,0.08)",
                border:"1px solid rgba(255,143,0,0.2)", borderRadius:7 }}>
                ▸ {sel.action}
              </div>
            </motion.div>
          ) : (
            <motion.div key="chart" initial={{opacity:0}} animate={{opacity:1}}>
              <SectionLabel>GIC by Corridor</SectionLabel>
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={corridors} margin={{top:4,right:4,bottom:20,left:0}}>
                  <CartesianGrid strokeDasharray="3 3"
                    stroke="rgba(0,212,255,0.07)" vertical={false} />
                  <XAxis dataKey="states" tick={{fill:"#546E7A",fontSize:9,
                    fontFamily:"JetBrains Mono,monospace"}}
                    axisLine={false} tickLine={false} />
                  <YAxis tick={{fill:"#546E7A",fontSize:9}} axisLine={false}
                    tickLine={false} width={24} />
                  <Tooltip contentStyle={{background:"rgba(13,27,62,0.95)",
                    border:"1px solid rgba(0,212,255,0.3)",borderRadius:8,fontSize:11}} />
                  <Bar dataKey="gic_amps" name="GIC (Amps)" radius={[3,3,0,0]}>
                    {corridors.map(c=>(
                      <Cell key={c.id} fill={getRiskColor(c.risk_percent)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div style={{ fontSize:10, color:"#546E7A", textAlign:"center",
                fontFamily:"JetBrains Mono,monospace", marginTop:4 }}>
                Click a corridor on the map or list for details
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
