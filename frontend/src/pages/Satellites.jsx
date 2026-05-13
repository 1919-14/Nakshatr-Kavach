import React, { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { MOCK_SATELLITES } from "../mock/mockData";
import { getRiskColor, getRiskBg, getRiskLabel } from "../utils/riskColorMapper";
import { CircularProgress, AnimatedBar, RiskLevelBadge, OrbitTypeBadge, CountdownTimer, SectionLabel } from "../components/ui/index";
import { RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell } from "recharts";

function RiskRadar({ sat }) {
  const color = getRiskColor(sat.composite_risk);
  const data = [
    {s:"Drag",v:sat.drag_risk},{s:"Charging",v:sat.charging_risk},
    {s:"Radiation",v:sat.radiation_risk},{s:"Composite",v:sat.composite_risk},
    {s:"Thermal",v:Math.round(sat.composite_risk*0.6)},
  ];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <RadarChart data={data}>
        <PolarGrid stroke="rgba(0,212,255,0.12)" />
        <PolarAngleAxis dataKey="s" tick={{fill:"#546E7A",fontSize:10,fontFamily:"JetBrains Mono,monospace"}} />
        <Radar dataKey="v" stroke={color} fill={color} fillOpacity={0.18} strokeWidth={2} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

function SatDetail({ sat }) {
  const color = getRiskColor(sat.composite_risk);
  return (
    <motion.div key={sat.id} initial={{opacity:0,x:20}} animate={{opacity:1,x:0}} transition={{duration:0.35}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:20}}>
        <div>
          <div style={{fontFamily:"Orbitron,sans-serif",fontSize:22,fontWeight:800,color:"#fff",marginBottom:6}}>{sat.name}</div>
          <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
            <OrbitTypeBadge type={sat.type} />
            <RiskLevelBadge score={sat.composite_risk} />
            {sat.safe_mode_minutes && <CountdownTimer totalSeconds={sat.safe_mode_minutes*60} label="SAFE MODE" />}
          </div>
          <div style={{fontSize:12,color:"#90A4AE",marginTop:8,maxWidth:480,lineHeight:1.6}}>{sat.mission}</div>
        </div>
        <CircularProgress score={sat.composite_risk} size={88} strokeWidth={5} />
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginBottom:12}}>
        <div style={{background:"var(--color-bg-card)",borderRadius:10,padding:"14px 16px",border:"1px solid rgba(0,212,255,0.1)"}}>
          <SectionLabel>Specifications</SectionLabel>
          {[["Orbit",sat.type],["Altitude",`${sat.altitude?.toLocaleString()} km`],["Inclination",`${sat.inclination}°`],["Risk Score",`${sat.composite_risk}%`]].map(([l,v])=>(
            <div key={l} style={{display:"flex",justifyContent:"space-between",padding:"5px 0",borderBottom:"1px solid rgba(255,255,255,0.04)",fontSize:12}}>
              <span style={{color:"#546E7A"}}>{l}</span>
              <span style={{color:"#90A4AE",fontSize:11}}>{v}</span>
            </div>
          ))}
        </div>
        <div style={{background:"var(--color-bg-card)",borderRadius:10,padding:"14px 16px",border:"1px solid rgba(0,212,255,0.1)"}}>
          <SectionLabel>Risk Radar</SectionLabel>
          <RiskRadar sat={sat} />
        </div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
        <div style={{background:"var(--color-bg-card)",borderRadius:10,padding:"14px 16px",border:"1px solid rgba(0,212,255,0.1)"}}>
          <SectionLabel>Risk Breakdown</SectionLabel>
          <AnimatedBar value={sat.drag_risk}      color="#FF9800" label="Atmospheric Drag" />
          <AnimatedBar value={sat.charging_risk}  color="#FDD835" label="Surface Charging" />
          <AnimatedBar value={sat.radiation_risk} color="#00D4FF" label="Radiation (SEU)"  />
        </div>
        <div style={{background:`${color}0d`,borderRadius:10,padding:"14px 16px",border:`1px solid ${color}33`}}>
          <SectionLabel>Recommended Action</SectionLabel>
          <div style={{fontSize:13,lineHeight:1.75,color:"#E8F4FD",marginBottom:12}}>▸ {sat.action}</div>
          <div style={{fontSize:11,color:"#546E7A",lineHeight:1.7}}>Storm window: 14:30–18:00 IST<br/>Recovery: T+18 hours<br/>Priority: {getRiskLabel(sat.composite_risk)}</div>
        </div>
      </div>
    </motion.div>
  );
}

export default function Satellites() {
  const { satellites } = useStormStore();
  const sats = useMemo(()=>{
    const base = satellites?.length ? satellites : MOCK_SATELLITES;
    return [...base].sort((a,b)=>b.composite_risk-a.composite_risk);
  },[satellites]);
  const [selected, setSelected] = useState(sats[0]?.id);
  const sat = sats.find(s=>s.id===selected);

  return (
    <div style={{minHeight:"100vh",background:"var(--color-bg-primary)",paddingTop:74,display:"grid",gridTemplateColumns:"290px 1fr",fontFamily:"Space Grotesk,sans-serif"}}>
      <div style={{background:"var(--color-bg-secondary)",borderRight:"1px solid rgba(0,212,255,0.1)",overflowY:"auto",padding:"16px 12px"}}>
        <div style={{fontFamily:"Orbitron,sans-serif",fontSize:9,color:"#00D4FF",letterSpacing:"0.14em",marginBottom:12}}>ISRO SATELLITE FLEET</div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginBottom:14}}>
          {[["CRIT",sats.filter(s=>s.composite_risk>80).length,"#EF5350"],["HIGH",sats.filter(s=>s.composite_risk>60&&s.composite_risk<=80).length,"#FF8F00"],["MOD",sats.filter(s=>s.composite_risk>40&&s.composite_risk<=60).length,"#FDD835"],["LOW",sats.filter(s=>s.composite_risk<=40).length,"#43A047"]].map(([l,v,c])=>(
            <div key={l} style={{background:`${c}12`,borderRadius:6,padding:"6px 8px",border:`1px solid ${c}33`,textAlign:"center"}}>
              <div style={{fontFamily:"Orbitron,sans-serif",fontSize:14,fontWeight:800,color:c}}>{v}</div>
              <div style={{fontSize:8,color:c,letterSpacing:"0.1em"}}>{l}</div>
            </div>
          ))}
        </div>
        {sats.map((s,i)=>{
          const c=getRiskColor(s.composite_risk);
          const active=selected===s.id;
          return (
            <motion.div key={s.id} onClick={()=>setSelected(s.id)}
              initial={{opacity:0,x:-10}} animate={{opacity:1,x:0}} transition={{delay:i*0.04}}
              style={{padding:"10px 12px",borderRadius:8,marginBottom:5,cursor:"pointer",
                background:active?getRiskBg(s.composite_risk):"transparent",
                border:`1px solid ${active?c+"55":"transparent"}`,
                borderLeft:`3px solid ${c}`,transition:"all 0.2s"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}}>
                <span style={{fontWeight:600,fontSize:12,color:active?"#fff":"#E8F4FD"}}>{s.name}</span>
                <OrbitTypeBadge type={s.type} />
              </div>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <span style={{fontSize:10,color:"#546E7A",fontFamily:"JetBrains Mono,monospace"}}>{s.altitude?.toLocaleString()} km</span>
                <RiskLevelBadge score={s.composite_risk} />
              </div>
              <div style={{marginTop:5,height:2,background:"rgba(255,255,255,0.06)",borderRadius:1}}>
                <motion.div initial={{width:0}} animate={{width:`${s.composite_risk}%`}} transition={{duration:0.8,delay:i*0.05}}
                  style={{height:"100%",background:c,borderRadius:1}} />
              </div>
            </motion.div>
          );
        })}
        <div style={{marginTop:16}}>
          <SectionLabel>Fleet Comparison</SectionLabel>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={sats} margin={{top:4,right:4,bottom:4,left:0}}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.07)" vertical={false} />
              <XAxis dataKey="shortName" tick={{fill:"#546E7A",fontSize:8,fontFamily:"JetBrains Mono,monospace"}} axisLine={false} tickLine={false} />
              <YAxis domain={[0,100]} tick={{fill:"#546E7A",fontSize:8}} axisLine={false} tickLine={false} width={20} />
              <Tooltip contentStyle={{background:"rgba(13,27,62,0.95)",border:"1px solid rgba(0,212,255,0.3)",borderRadius:8,fontSize:11}} />
              <Bar dataKey="composite_risk" name="Risk %" radius={[3,3,0,0]}>
                {sats.map(s=><Cell key={s.id} fill={getRiskColor(s.composite_risk)} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div style={{overflowY:"auto",padding:"20px 24px"}}>
        <AnimatePresence mode="wait">
          {sat && <SatDetail key={sat.id} sat={sat} />}
        </AnimatePresence>
      </div>
    </div>
  );
}
