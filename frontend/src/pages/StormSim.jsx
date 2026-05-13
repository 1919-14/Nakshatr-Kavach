import React, { useState, useRef, useEffect, useCallback, memo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { EarthGlobe } from "../components/earth/EarthGlobe";
import { getStormClass } from "../utils/stormClassifier";

const TIMELINE_EVENTS = [
  { offset:-60, label:"DSCOVR detects CME",          icon:"🛰", color:"#00D4FF" },
  { offset:-45, label:"NAKSHATRA-KAVACH alert",       icon:"⚡", color:"#FFD700" },
  { offset:-30, label:"INSAT-3DR safe mode",          icon:"🔒", color:"#FF9800" },
  { offset:-15, label:"Grid load reduction",          icon:"⚡", color:"#FF9800" },
  { offset:  0, label:"STORM IMPACT",                 icon:"💥", color:"#F44336" },
  { offset: 60, label:"Peak intensity",               icon:"📈", color:"#9C27B0" },
  { offset:360, label:"Recovery begins",              icon:"📉", color:"#4CAF50" },
];

function kpAtOffset(offset) {
  if (offset<=-60) return 2.0; if (offset<=-45) return 3.5;
  if (offset<=-30) return 5.2; if (offset<=-15) return 6.8;
  if (offset<=0)   return 8.2; if (offset<=60)  return 9.0;
  if (offset<=120) return 8.5; if (offset<=240) return 7.0;
  if (offset<=360) return 5.5; return 3.5;
}

export default function StormSim() {
  const [offset,  setOffset]  = useState(-60);
  const [playing, setPlaying] = useState(false);
  const [flash,   setFlash]   = useState(false);
  const timerRef = useRef(null);
  const { setSolarWind } = useStormStore();
  const kp = kpAtOffset(offset);
  const sc = getStormClass(kp);

  useEffect(() => {
    setSolarWind(sw => ({
      ...(sw||{}), kp_current:kp,
      storm_class:sc.label.split(" ")[0],
      bz_gsm:-(kp*2.5), sw_speed:300+kp*55, storm_active:kp>=5,
    }));
  }, [kp]);

  useEffect(() => {
    if (Math.abs(offset)<3) { setFlash(true); setTimeout(()=>setFlash(false),1500); }
  }, [offset]);

  useEffect(() => {
    if (!playing) { clearInterval(timerRef.current); return; }
    timerRef.current = setInterval(() => {
      setOffset(o => { if(o>=360){setPlaying(false);return 360;} return o+3; });
    }, 120);
    return () => clearInterval(timerRef.current);
  }, [playing]);

  const MIN=-60, MAX=360;
  const pct = ((offset-MIN)/(MAX-MIN))*100;

  return (
    <div style={{ width:"100vw", height:"100vh", background:"#020817", position:"relative", overflow:"hidden" }}>
      <div style={{ position:"absolute", inset:0 }}>
        <EarthGlobe height="100vh" fullScreen />
      </div>

      {/* Flash */}
      <AnimatePresence>
        {flash && (
          <motion.div initial={{opacity:0}} animate={{opacity:[0,0.6,0]}} exit={{opacity:0}}
            transition={{duration:1.2}}
            style={{ position:"fixed",inset:0,zIndex:200,pointerEvents:"none",
              background:"radial-gradient(ellipse at center,rgba(244,67,54,0.5) 0%,transparent 70%)" }} />
        )}
      </AnimatePresence>

      {/* Edge glow */}
      {kp>=7 && (
        <div style={{ position:"absolute",inset:0,zIndex:10,pointerEvents:"none",
          boxShadow:kp>=9?"inset 0 0 120px rgba(156,39,176,0.5)":kp>=8?"inset 0 0 100px rgba(244,67,54,0.4)":"inset 0 0 80px rgba(255,152,0,0.3)" }} />
      )}

      {/* Topbar */}
      <div style={{ position:"absolute",top:0,left:0,right:0,height:64,zIndex:100,
        background:"rgba(2,8,23,0.9)",backdropFilter:"blur(20px)",
        borderBottom:"1px solid rgba(0,212,255,0.12)",
        display:"flex",alignItems:"center",padding:"0 24px",gap:16 }}>
        <a href="/" style={{ color:"#546E7A",textDecoration:"none",
          fontFamily:"Orbitron,sans-serif",fontSize:11,letterSpacing:"0.08em" }}>← DASHBOARD</a>
        <div style={{ flex:1,textAlign:"center",fontFamily:"Orbitron,sans-serif",
          fontSize:14,fontWeight:800,color:"#00D4FF",letterSpacing:"0.1em" }}>
          STORM SIMULATION — MAY 2024 G5 EVENT
        </div>
        <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:13,color:sc.color,
          fontWeight:700,padding:"4px 14px",borderRadius:999,
          background:`${sc.color}22`,border:`1px solid ${sc.color}55` }}>
          {sc.label}
        </div>
      </div>

      {/* Stats panel */}
      <div style={{ position:"absolute",top:80,right:20,width:200,zIndex:50,
        background:"rgba(2,8,23,0.92)",backdropFilter:"blur(16px)",
        border:`1px solid ${sc.color}44`,borderRadius:12,padding:16,
        fontFamily:"Space Grotesk,sans-serif" }}>
        <div style={{ fontSize:9,color:"#546E7A",fontFamily:"Orbitron,sans-serif",
          letterSpacing:"0.12em",marginBottom:12 }}>STORM STATS</div>
        <div style={{ textAlign:"center",marginBottom:12 }}>
          <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:48,fontWeight:900,
            color:sc.color,lineHeight:1,textShadow:`0 0 30px ${sc.color}` }}>
            {kp.toFixed(1)}
          </div>
          <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:11,
            color:sc.color,letterSpacing:"0.1em" }}>{sc.label}</div>
        </div>
        {[
          ["Satellites at risk", kp>=7?5:kp>=5?3:1, "#EF5350"],
          ["Grid corridors",     kp>=7?4:kp>=5?2:0, "#FF8F00"],
          ["Economic impact",    `₹${Math.round(kp*48)}Cr`, "#FDD835"],
          ["Population",         `${(kp*1.8).toFixed(1)}M`, "#00D4FF"],
        ].map(([l,v,c])=>(
          <div key={l} style={{ display:"flex",justifyContent:"space-between",
            padding:"5px 0",borderBottom:"1px solid rgba(255,255,255,0.05)",fontSize:11 }}>
            <span style={{color:"#546E7A"}}>{l}</span>
            <span style={{color:c,fontFamily:"Orbitron,sans-serif",fontSize:10}}>{v}</span>
          </div>
        ))}
        <div style={{ marginTop:10,textAlign:"center",fontFamily:"Orbitron,sans-serif",
          fontSize:12,color:offset<0?"#00D4FF":offset===0?"#F44336":"#FF9800",letterSpacing:"0.06em" }}>
          {offset<0?`T${offset}min`:offset===0?"T+0 IMPACT":`T+${offset}min`}
        </div>
      </div>

      {/* Timeline */}
      <div style={{ position:"absolute",bottom:0,left:0,right:0,zIndex:50,
        background:"rgba(2,8,23,0.95)",backdropFilter:"blur(20px)",
        borderTop:"1px solid rgba(0,212,255,0.15)",padding:"14px 24px 18px" }}>
        {/* Event icons */}
        <div style={{ position:"relative",marginBottom:10,height:34 }}>
          {TIMELINE_EVENTS.map(ev=>{
            const evPct=((ev.offset-MIN)/(MAX-MIN))*100;
            const active=Math.abs(offset-ev.offset)<8;
            return (
              <div key={ev.offset} onClick={()=>setOffset(ev.offset)}
                style={{ position:"absolute",left:`${evPct}%`,transform:"translateX(-50%)",
                  cursor:"pointer",textAlign:"center" }}>
                <div style={{ fontSize:ev.offset===0?16:12,lineHeight:1,marginBottom:2 }}>{ev.icon}</div>
                <div style={{ fontSize:8,color:active?ev.color:"#546E7A",
                  fontFamily:"Orbitron,sans-serif",whiteSpace:"nowrap",letterSpacing:"0.05em" }}>
                  {ev.offset===0?"IMPACT":`T${ev.offset>0?"+":""}${ev.offset}`}
                </div>
              </div>
            );
          })}
        </div>
        {/* Controls */}
        <div style={{ display:"flex",alignItems:"center",gap:12 }}>
          <button onClick={()=>{if(offset>=360)setOffset(-60);setPlaying(p=>!p);}}
            style={{ width:36,height:36,borderRadius:8,background:"rgba(0,212,255,0.1)",
              border:"1px solid rgba(0,212,255,0.3)",color:"#00D4FF",fontSize:16,
              cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center" }}>
            {playing?"⏸":"▶"}
          </button>
          <div style={{ flex:1,position:"relative",height:6,cursor:"pointer" }}
            onClick={e=>{
              const r=e.currentTarget.getBoundingClientRect();
              const p=(e.clientX-r.left)/r.width;
              setOffset(Math.round(MIN+p*(MAX-MIN)));
            }}>
            <div style={{ height:"100%",background:"rgba(255,255,255,0.08)",borderRadius:3 }} />
            <div style={{ position:"absolute",top:0,left:0,height:"100%",width:`${pct}%`,
              background:"linear-gradient(90deg,#00D4FF,#FF9800)",borderRadius:3,transition:"width 0.2s" }} />
            <div style={{ position:"absolute",top:"50%",left:`${pct}%`,
              transform:"translate(-50%,-50%)",width:14,height:14,borderRadius:"50%",
              background:"#fff",boxShadow:"0 0 8px rgba(0,212,255,0.8)",border:"2px solid #00D4FF" }} />
          </div>
          <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:11,color:"#00D4FF",
            flexShrink:0,minWidth:80,letterSpacing:"0.06em" }}>
            {offset<0?`T${offset}min`:`T+${offset}min`}
          </div>
        </div>
        <div style={{ textAlign:"center",marginTop:6,fontFamily:"Orbitron,sans-serif",
          fontSize:9,color:"#546E7A",letterSpacing:"0.1em" }}>
          DRAG SLIDER OR CLICK EVENTS TO SCRUB THROUGH STORM TIMELINE
        </div>
      </div>
    </div>
  );
}
