import React, { useState } from "react";
import { motion } from "framer-motion";
import { useStormStore } from "../store/useStormStore";
import { MOCK_ADVISORY, MOCK_SATELLITES } from "../mock/mockData";
import { getStormClass } from "../utils/stormClassifier";
import { getRiskColor } from "../utils/riskColorMapper";
import { formatIST } from "../utils/timeFormatter";
import { SectionLabel, RiskLevelBadge } from "../components/ui/index";

export default function Advisory() {
  const { advisory, solarWind, satellites } = useStormStore();
  const data     = advisory || MOCK_ADVISORY;
  const satList  = satellites?.length ? satellites : MOCK_SATELLITES;
  const kp       = solarWind?.kp_current ?? 7.2;
  const sc       = getStormClass(kp);
  const [hindi, setHindi] = useState(false);
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    setExporting(true);
    try {
      const { default: jsPDF }       = await import("jspdf");
      const { default: html2canvas } = await import("html2canvas");
      const el     = document.getElementById("advisory-export");
      const canvas = await html2canvas(el, { backgroundColor:"#020817", scale:2 });
      const pdf    = new jsPDF({ orientation:"portrait", unit:"mm", format:"a4" });
      const imgW   = 190;
      const imgH   = (canvas.height * imgW) / canvas.width;
      pdf.setFillColor(2, 8, 23);
      pdf.rect(0, 0, 210, 297, "F");
      pdf.addImage(canvas.toDataURL("image/png"), "PNG", 10, 10, imgW, imgH);
      pdf.save(`nakshatra-kavach-advisory-${Date.now()}.pdf`);
    } catch(e) { console.error(e); }
    setExporting(false);
  };

  return (
    <div style={{ minHeight:"100vh", background:"var(--color-bg-primary)",
      paddingTop:74, padding:"74px 24px 32px",
      fontFamily:"Space Grotesk,sans-serif", color:"#E8F4FD" }}>
      <div style={{ maxWidth:1100, margin:"0 auto" }}>

        {/* Header */}
        <div style={{ display:"flex", justifyContent:"space-between",
          alignItems:"center", marginBottom:24, flexWrap:"wrap", gap:12 }}>
          <div>
            <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:20,
              fontWeight:800, color:"#00D4FF", letterSpacing:"0.08em", marginBottom:4 }}>
              ⚡ FULL ADVISORY REPORT
            </div>
            <div style={{ fontSize:12, color:"#546E7A" }}>
              {data?.generated_at ? formatIST(data.generated_at) : "Live report"} &nbsp;·&nbsp;
              <span style={{ color: data?.source==="AI_GENERATED"?"#00D4FF":"#FF9800" }}>
                {data?.source==="AI_GENERATED" ? "AI Generated" : "Rule-based"}
              </span>
            </div>
          </div>
          <div style={{ display:"flex", gap:8, alignItems:"center" }}>
            <div style={{ textAlign:"right" }}>
              <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:32,
                fontWeight:900, color:sc.color, textShadow:`0 0 20px ${sc.color}` }}>
                Kp {kp.toFixed(1)}
              </div>
              <div style={{ fontSize:11, color:sc.color, fontFamily:"Orbitron,sans-serif" }}>
                {sc.label}
              </div>
            </div>
          </div>
        </div>

        {/* Action buttons */}
        <div style={{ display:"flex", gap:8, marginBottom:20, flexWrap:"wrap" }}>
          {[
            { icon:"📋", label: exporting?"Exporting...":"Export PDF", onClick: handleExport },
            { icon:"📱", label:"WhatsApp Summary", onClick:()=>{} },
            { icon:"📧", label:"Email Alert",      onClick:()=>{} },
          ].map(btn => (
            <motion.button key={btn.label} onClick={btn.onClick}
              whileHover={{ background:"rgba(0,212,255,0.15)", borderColor:"rgba(0,212,255,0.5)" }}
              style={{ padding:"8px 16px", borderRadius:8,
                background:"rgba(0,212,255,0.07)", border:"1px solid rgba(0,212,255,0.2)",
                color:"#90A4AE", fontSize:12, cursor:"pointer",
                fontFamily:"Space Grotesk,sans-serif", display:"flex",
                alignItems:"center", gap:6, transition:"all 0.2s" }}>
              {btn.icon} {btn.label}
            </motion.button>
          ))}
        </div>

        {/* Exportable content */}
        <div id="advisory-export">

          {/* Satellite risk summary */}
          <div style={{ background:"var(--color-bg-card)", borderRadius:12,
            border:"1px solid rgba(0,212,255,0.12)", padding:"16px 18px", marginBottom:12 }}>
            <SectionLabel>Satellite Risk Summary</SectionLabel>
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))", gap:8 }}>
              {[...satList].sort((a,b)=>b.composite_risk-a.composite_risk).map(s => {
                const c = getRiskColor(s.composite_risk);
                return (
                  <div key={s.id} style={{ padding:"10px 12px", borderRadius:8,
                    background:`${c}0d`, border:`1px solid ${c}33`,
                    borderLeft:`3px solid ${c}` }}>
                    <div style={{ display:"flex", justifyContent:"space-between",
                      alignItems:"center", marginBottom:5 }}>
                      <span style={{ fontWeight:600, fontSize:12 }}>{s.name}</span>
                      <RiskLevelBadge score={s.composite_risk} />
                    </div>
                    <div style={{ fontSize:11, color:"#546E7A", marginBottom:4 }}>
                      {s.type} · {s.altitude?.toLocaleString()} km
                    </div>
                    <div style={{ fontSize:11, color:"#90A4AE", lineHeight:1.5 }}>
                      {s.action?.split(".")[0]}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Advisory sections */}
          <div style={{ background:"var(--color-bg-card)", borderRadius:12,
            border:"1px solid rgba(0,212,255,0.12)", padding:"18px 20px", marginBottom:12 }}>
            <SectionLabel>Mission Advisory</SectionLabel>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"0 28px" }}>
              {(data?.sections||[]).map(section => (
                <div key={section.title} style={{ marginBottom:18 }}>
                  <div style={{ fontFamily:"Orbitron,sans-serif", fontSize:10,
                    fontWeight:700, color:"#00D4FF", letterSpacing:"0.14em",
                    marginBottom:8, display:"flex", alignItems:"center", gap:8 }}>
                    {section.title}
                    <div style={{ flex:1, height:1, background:"rgba(0,212,255,0.2)" }} />
                  </div>
                  <div style={{ fontSize:12, lineHeight:1.75, color:"#90A4AE" }}>
                    {section.content.split("\n").map((line,i) =>
                      line.startsWith("▸") ? (
                        <div key={i} style={{ display:"flex", gap:6, marginBottom:5,
                          padding:"4px 8px", background:"rgba(255,143,0,0.1)",
                          border:"1px solid rgba(255,143,0,0.2)", borderRadius:5,
                          color:"#E8F4FD", fontSize:11 }}>
                          <span style={{ color:"#FF9800", flexShrink:0 }}>▸</span>
                          <span>{line.slice(1).trim()}</span>
                        </div>
                      ) : (
                        <p key={i} style={{ marginBottom:4 }}>{line}</p>
                      )
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Hindi summary */}
          {data?.hindi_summary && (
            <div style={{ background:"var(--color-bg-card)", borderRadius:12,
              border:"1px solid rgba(0,212,255,0.12)", padding:"14px 18px" }}>
              <button onClick={()=>setHindi(h=>!h)}
                style={{ background:"none", border:"none", color:"#546E7A",
                  cursor:"pointer", fontFamily:"Orbitron,sans-serif", fontSize:10,
                  letterSpacing:"0.1em", display:"flex", alignItems:"center", gap:6,
                  marginBottom: hindi ? 10 : 0 }}>
                <motion.span animate={{rotate:hindi?180:0}}>▼</motion.span>
                हिंदी सारांश
              </button>
              {hindi && (
                <div style={{ fontSize:13, lineHeight:1.9, color:"#90A4AE",
                  padding:"10px 12px", background:"rgba(0,212,255,0.05)",
                  borderRadius:8, border:"1px solid rgba(0,212,255,0.12)" }}>
                  {data.hindi_summary}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
