import React, { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { HISTORICAL_STORMS } from "../mock/mockData";
import { getStormClass } from "../utils/stormClassifier";

const USE_MOCK = import.meta.env.VITE_USE_MOCK_DATA !== "false";
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

export default function Replay() {
  const [selectedStorm, setSelectedStorm] = useState(null);
  const [frameIndex,    setFrameIndex]    = useState(0);
  const [playing,       setPlaying]       = useState(false);
  const [speed,         setSpeed]         = useState(1);
  const [apiStorm,      setApiStorm]      = useState(null);
  const timerRef = useRef(null);
  const { setSolarWind, setSatellites } = useStormStore();
  const mockStorm = selectedStorm ? HISTORICAL_STORMS.find((s) => s.id === selectedStorm) : null;

  const storm  = selectedStorm ? (USE_MOCK ? mockStorm : (apiStorm || mockStorm)) : null;
  const frames = storm?.frames || [];
  const frame  = frames[frameIndex] || null;
  const kp     = frame?.kp ?? 0;
  const sc     = getStormClass(kp);

  useEffect(() => {
    if (!selectedStorm || USE_MOCK) return;
    fetch(`${API_BASE}/api/history/${selectedStorm}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => setApiStorm(payload))
      .catch(() => setApiStorm(null));
  }, [selectedStorm]);

  // Sync frame to store
  useEffect(() => {
    if (!frame) return;
    setSolarWind(sw => ({
      ...(sw||{}),
      kp_current: frame.kp,
      bz_gsm: frame.bz,
      sw_speed: frame.sw_speed,
      storm_class: frame.storm_class,
      storm_active: frame.kp >= 5,
    }));
    if (frame.satellites?.length) setSatellites(frame.satellites);
  }, [frame]);

  // Playback
  useEffect(() => {
    if (!playing || !frames.length) { clearInterval(timerRef.current); return; }
    const interval = Math.max(200, 1000 / speed);
    timerRef.current = setInterval(() => {
      setFrameIndex(i => {
        if (i >= frames.length-1) { setPlaying(false); return frames.length-1; }
        return i+1;
      });
    }, interval);
    return () => clearInterval(timerRef.current);
  }, [playing, speed, frames.length]);

  const handleLoad = useCallback((id) => {
    setSelectedStorm(id); setFrameIndex(0); setPlaying(false);
  }, []);

  const handleTogglePlay = useCallback(() => {
    if (frameIndex >= frames.length-1) setFrameIndex(0);
    setPlaying(p=>!p);
  }, [frameIndex, frames.length]);

  const pct = frames.length ? (frameIndex/(frames.length-1))*100 : 0;

  return (
    <div style={{ minHeight:"100vh",background:"var(--color-bg-primary)",paddingTop:74,
      fontFamily:"Space Grotesk,sans-serif",color:"#E8F4FD" }}>
      <div style={{ padding:"20px 24px" }}>

        {/* Header */}
        <div style={{ marginBottom:24 }}>
          <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:20,fontWeight:800,
            color:"#00D4FF",letterSpacing:"0.08em",marginBottom:4 }}>
            STORM REPLAY THEATRE
          </div>
          <div style={{ color:"#546E7A",fontSize:13 }}>
            Replay historical geomagnetic storms — watch the full impact unfold in real time
          </div>
        </div>

        {/* Storm selection grid */}
        <div style={{ display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",
          gap:12,marginBottom:24 }}>
          {HISTORICAL_STORMS.map(s => {
            const sc2 = getStormClass(s.peak_kp);
            const active = selectedStorm===s.id;
            return (
              <motion.div key={s.id}
                whileHover={{ scale:1.02, borderColor:`${s.color}66` }}
                onClick={() => handleLoad(s.id)}
                style={{
                  background:  active ? `${s.color}18` : "var(--color-bg-card)",
                  border:      `1px solid ${active?s.color+"66":"rgba(0,212,255,0.12)"}`,
                  borderRadius:12, padding:"16px",cursor:"pointer",
                  transition:  "all 0.2s",
                  boxShadow:   active?`0 0 20px ${s.color}33`:"none",
                }}>
                <div style={{ display:"flex",justifyContent:"space-between",
                  alignItems:"flex-start",marginBottom:8 }}>
                  <div>
                    <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:11,
                      fontWeight:700,color:s.color,letterSpacing:"0.08em",marginBottom:3 }}>
                      {s.name}
                    </div>
                    <div style={{ fontSize:11,color:"#546E7A" }}>{s.date}</div>
                  </div>
                  <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:20,
                    fontWeight:900,color:s.color,textShadow:`0 0 12px ${s.color}` }}>
                    {s.peak_kp}
                  </div>
                </div>
                <div style={{ fontSize:12,color:"#90A4AE",lineHeight:1.5,marginBottom:10 }}>
                  {s.headline}
                </div>
                <div style={{ display:"flex",justifyContent:"space-between",
                  alignItems:"center" }}>
                  <span style={{ fontSize:10,padding:"2px 8px",borderRadius:999,
                    background:`${s.color}22`,color:s.color,
                    border:`1px solid ${s.color}44`,fontFamily:"Orbitron,sans-serif" }}>
                    {sc2.label}
                  </span>
                  <span style={{ fontSize:11,color:active?"#00D4FF":"#546E7A",
                    fontFamily:"Orbitron,sans-serif",letterSpacing:"0.08em" }}>
                    {active?"● LOADED":"LOAD STORM →"}
                  </span>
                </div>
              </motion.div>
            );
          })}
        </div>

        {/* Player — only when storm loaded */}
        <AnimatePresence>
          {storm && (
            <motion.div
              initial={{ opacity:0,y:20 }} animate={{ opacity:1,y:0 }}
              exit={{ opacity:0,y:10 }}
              style={{ background:"var(--color-bg-card)",borderRadius:12,
                border:"1px solid rgba(0,212,255,0.15)",padding:"20px 24px" }}>

              {/* Now playing */}
              <div style={{ display:"flex",justifyContent:"space-between",
                alignItems:"center",marginBottom:16 }}>
                <div>
                  <div style={{ fontSize:10,color:"#546E7A",fontFamily:"Orbitron,sans-serif",
                    letterSpacing:"0.1em",marginBottom:3 }}>NOW REPLAYING</div>
                  <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:15,
                    fontWeight:700,color:storm.color,letterSpacing:"0.06em" }}>
                    {storm.name}
                  </div>
                </div>
                <div style={{ textAlign:"right" }}>
                  <div style={{ fontFamily:"Orbitron,sans-serif",fontSize:28,
                    fontWeight:900,color:sc.color,textShadow:`0 0 20px ${sc.color}` }}>
                    Kp {kp.toFixed(1)}
                  </div>
                  <div style={{ fontSize:11,color:sc.color,fontFamily:"Orbitron,sans-serif" }}>
                    {sc.label}
                  </div>
                </div>
              </div>

              {/* Progress bar */}
              <div style={{ marginBottom:14 }}>
                <div style={{ height:6,background:"rgba(255,255,255,0.08)",
                  borderRadius:3,cursor:"pointer",position:"relative" }}
                  onClick={e=>{
                    const r=e.currentTarget.getBoundingClientRect();
                    const p=(e.clientX-r.left)/r.width;
                    setFrameIndex(Math.round(p*(frames.length-1)));
                  }}>
                  <div style={{ height:"100%",width:`${pct}%`,borderRadius:3,transition:"width 0.2s",
                    background:`linear-gradient(90deg,${storm.color},#00D4FF)` }} />
                  <div style={{ position:"absolute",top:"50%",left:`${pct}%`,
                    transform:"translate(-50%,-50%)",width:12,height:12,
                    borderRadius:"50%",background:"#fff",
                    boxShadow:`0 0 8px ${storm.color}` }} />
                </div>
                <div style={{ display:"flex",justifyContent:"space-between",
                  marginTop:4,fontSize:10,color:"#546E7A",fontFamily:"JetBrains Mono,monospace" }}>
                  <span>Frame {frameIndex+1} / {frames.length}</span>
                  <span>T+{frame?.time_offset_hours??0}h from storm start</span>
                </div>
              </div>

              {/* Controls */}
              <div style={{ display:"flex",alignItems:"center",gap:8,flexWrap:"wrap" }}>
                <button onClick={()=>{setFrameIndex(0);setPlaying(false);}}
                  style={btnStyle}>⏮</button>
                <button onClick={()=>setFrameIndex(i=>Math.max(0,i-1))}
                  style={btnStyle}>◀</button>
                <button onClick={handleTogglePlay}
                  style={{...btnStyle,background:"rgba(0,212,255,0.15)",
                    border:"1px solid rgba(0,212,255,0.4)",color:"#00D4FF",
                    fontSize:16,width:44,height:44 }}>
                  {playing?"⏸":"▶"}
                </button>
                <button onClick={()=>setFrameIndex(i=>Math.min(frames.length-1,i+1))}
                  style={btnStyle}>▶</button>
                <button onClick={()=>{setFrameIndex(frames.length-1);setPlaying(false);}}
                  style={btnStyle}>⏭</button>

                <div style={{ marginLeft:"auto",display:"flex",alignItems:"center",gap:6 }}>
                  <span style={{ fontSize:11,color:"#546E7A",fontFamily:"Orbitron,sans-serif",
                    letterSpacing:"0.08em" }}>SPEED:</span>
                  {[1,3,6].map(s=>(
                    <button key={s} onClick={()=>setSpeed(s)}
                      style={{ padding:"4px 10px",borderRadius:6,cursor:"pointer",fontSize:11,
                        fontFamily:"Orbitron,sans-serif",
                        background:speed===s?"rgba(0,212,255,0.2)":"rgba(0,212,255,0.06)",
                        border:`1px solid ${speed===s?"rgba(0,212,255,0.5)":"rgba(0,212,255,0.15)"}`,
                        color:speed===s?"#00D4FF":"#546E7A" }}>
                      {s}×
                    </button>
                  ))}
                </div>
              </div>

              {/* Kp progression */}
              <div style={{ marginTop:16,display:"flex",gap:3,alignItems:"flex-end",height:48 }}>
                {frames.map((f,i)=>{
                  const h=(f.kp/9)*100;
                  const sc3=getStormClass(f.kp);
                  return (
                    <div key={i}
                      onClick={()=>setFrameIndex(i)}
                      style={{ flex:1,height:`${h}%`,background:i===frameIndex?"#fff":sc3.color,
                        borderRadius:"2px 2px 0 0",opacity:i===frameIndex?1:0.5,
                        cursor:"pointer",transition:"all 0.2s",minWidth:2 }} />
                  );
                })}
              </div>
              <div style={{ display:"flex",justifyContent:"space-between",
                fontSize:9,color:"#546E7A",fontFamily:"JetBrains Mono,monospace",marginTop:3 }}>
                <span>Storm start</span><span>Peak</span><span>Recovery</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

const btnStyle = {
  width:36,height:36,borderRadius:8,
  background:"rgba(255,255,255,0.05)",
  border:"1px solid rgba(255,255,255,0.1)",
  color:"#90A4AE",fontSize:14,cursor:"pointer",
  display:"flex",alignItems:"center",justifyContent:"center",
};
