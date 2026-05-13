import React, { memo, useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useStormStore } from "../../store/useStormStore";
import { MOCK_ADVISORY } from "../../mock/mockData";
import { getRiskColor } from "../../utils/riskColorMapper";
import { SectionLabel } from "../ui/index";
import { formatISTShort } from "../../utils/timeFormatter";

// ── Typewriter hook ───────────────────────────────────────────────────────────
function useTypewriter(text, speed = 18) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone]           = useState(false);
  const intervalRef               = useRef(null);
  const indexRef                  = useRef(0);

  useEffect(() => {
    if (!text) return;
    if (intervalRef.current) clearInterval(intervalRef.current);
    indexRef.current = 0;
    setDisplayed("");
    setDone(false);

    intervalRef.current = setInterval(() => {
      indexRef.current++;
      setDisplayed(text.slice(0, indexRef.current));
      if (indexRef.current >= text.length) {
        clearInterval(intervalRef.current);
        setDone(true);
      }
    }, speed);

    return () => clearInterval(intervalRef.current);
  }, [text, speed]);

  return { displayed, done };
}

// ── Format advisory text with highlights ─────────────────────────────────────
function FormattedText({ text }) {
  if (!text) return null;

  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        // Action items starting with ▸
        if (line.startsWith("▸")) {
          return (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
              style={{
                display:      "inline-flex",
                alignItems:   "flex-start",
                gap:          6,
                background:   "rgba(255,143,0,0.12)",
                border:       "1px solid rgba(255,143,0,0.25)",
                borderRadius: 6,
                padding:      "4px 10px",
                marginBottom: 5,
                fontSize:     12,
                lineHeight:   1.6,
                color:        "#FF9800",
                width:        "100%",
              }}
            >
              <span style={{ flexShrink: 0 }}>▸</span>
              <span style={{ color: "#E8F4FD" }}>{line.slice(1).trim()}</span>
            </motion.div>
          );
        }
        return (
          <p key={i} style={{
            fontSize:    12,
            lineHeight:  1.7,
            color:       "#90A4AE",
            marginBottom: 4,
          }}>
            {line}
          </p>
        );
      })}
    </>
  );
}

// ── Advisory section ──────────────────────────────────────────────────────────
function AdvisorySection({ section, active }) {
  const { displayed } = useTypewriter(
    active ? section.content : section.content,
    active ? 12 : 0
  );

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontFamily:    "Orbitron, sans-serif",
        fontSize:      10,
        fontWeight:    700,
        color:         "#00D4FF",
        letterSpacing: "0.14em",
        marginBottom:  6,
        display:       "flex",
        alignItems:    "center",
        gap:           8,
      }}>
        {section.title}
        <div style={{ flex: 1, height: 1, background: "rgba(0,212,255,0.2)" }} />
      </div>
      <FormattedText text={active ? displayed : section.content} />
    </div>
  );
}

// ── Advisory Panel ────────────────────────────────────────────────────────────
export const AdvisoryPanel = memo(() => {
  const { advisory }     = useStormStore();
  const data             = advisory || MOCK_ADVISORY;
  const [hindi, setHindi]= useState(false);
  const [animKey, setAnimKey] = useState(0);

  // Re-trigger animation on new advisory
  useEffect(() => {
    setAnimKey(k => k + 1);
  }, [data?.generated_at]);

  const handleExportPDF = async () => {
    try {
      const { default: jsPDF }       = await import("jspdf");
      const { default: html2canvas } = await import("html2canvas");
      const el  = document.getElementById("advisory-content");
      const canvas = await html2canvas(el, { backgroundColor: "#020817", scale: 2 });
      const pdf = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
      pdf.setFontSize(16);
      pdf.setTextColor("#00D4FF");
      pdf.text("NAKSHATRA-KAVACH ADVISORY", 20, 20);
      pdf.setFontSize(10);
      pdf.setTextColor("#546E7A");
      pdf.text(`Generated: ${formatISTShort(data.generated_at || new Date().toISOString())}`, 20, 28);
      pdf.addImage(canvas.toDataURL("image/png"), "PNG", 10, 35, 190, 0);
      pdf.save("nakshatra-kavach-advisory.pdf");
    } catch (e) {
      console.error("PDF export failed:", e);
    }
  };

  const isAI = data?.source === "AI_GENERATED" || data?.source === "LLM_GROQ";

  return (
    <div style={{
      background:    "var(--color-bg-card)",
      borderRadius:  12,
      border:        "1px solid rgba(0,212,255,0.12)",
      padding:       "14px 18px",
    }}>
      {/* Header */}
      <div style={{
        display:       "flex",
        justifyContent:"space-between",
        alignItems:    "center",
        marginBottom:  14,
        flexWrap:      "wrap",
        gap:           8,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            fontFamily:    "Orbitron, sans-serif",
            fontSize:      13, fontWeight: 800,
            color:         "#00D4FF",
            letterSpacing: "0.08em",
          }}>
            ⚡ NAKSHATRA-KAVACH ADVISORY
          </span>
          <span style={{
            fontSize:  10, padding: "2px 8px", borderRadius: 999,
            background: isAI ? "rgba(0,212,255,0.12)" : "rgba(255,143,0,0.12)",
            color:      isAI ? "#00D4FF" : "#FF9800",
            border:     `1px solid ${isAI ? "rgba(0,212,255,0.3)" : "rgba(255,143,0,0.3)"}`,
            fontFamily: "Orbitron, sans-serif",
          }}>
            {isAI ? "AI GENERATED" : "RULE-BASED"}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            fontSize:   10, color: "#546E7A",
            fontFamily: "JetBrains Mono, monospace",
          }}>
            {data?.generated_at ? formatISTShort(data.generated_at) : "--:--:-- IST"}
          </span>
          <button
            onClick={() => setAnimKey(k => k + 1)}
            style={{
              padding:    "4px 10px", borderRadius: 6,
              background: "rgba(0,212,255,0.08)",
              border:     "1px solid rgba(0,212,255,0.25)",
              color:      "#00D4FF", fontSize: 11, cursor: "pointer",
              fontFamily: "Space Grotesk, sans-serif",
            }}
          >
            ↺ Refresh
          </button>
        </div>
      </div>

      {/* Advisory content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={animKey}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{   opacity: 0, y: -8 }}
          transition={{ duration: 0.4 }}
          id="advisory-content"
        >
          <div style={{
            display:             "grid",
            gridTemplateColumns: "1fr 1fr",
            gap:                 "0 28px",
          }}>
            {(data?.sections || []).map((section, i) => (
              <AdvisorySection
                key={section.title}
                section={section}
                active={i === 0}
              />
            ))}
          </div>
        </motion.div>
      </AnimatePresence>

      {/* Hindi summary */}
      {data?.hindi_summary && (
        <div style={{ marginTop: 12 }}>
          <button
            onClick={() => setHindi(h => !h)}
            style={{
              background: "none", border: "none",
              color: "#546E7A", cursor: "pointer",
              fontFamily: "Orbitron, sans-serif",
              fontSize: 10, letterSpacing: "0.1em",
              display: "flex", alignItems: "center", gap: 6,
            }}
          >
            <motion.span animate={{ rotate: hindi ? 180 : 0 }}>▼</motion.span>
            हिंदी सारांश
          </button>
          <AnimatePresence>
            {hindi && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{   height: 0, opacity: 0 }}
                style={{ overflow: "hidden" }}
              >
                <div style={{
                  marginTop:    8,
                  padding:      "10px 14px",
                  background:   "rgba(0,212,255,0.05)",
                  border:       "1px solid rgba(0,212,255,0.15)",
                  borderRadius: 8,
                  fontSize:     13,
                  lineHeight:   1.8,
                  color:        "#90A4AE",
                  fontFamily:   "Space Grotesk, sans-serif",
                }}>
                  {data.hindi_summary}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Action buttons */}
      <div style={{
        display:   "flex",
        gap:       8,
        marginTop: 14,
        flexWrap:  "wrap",
      }}>
        {[
          { icon: "📋", label: "Export PDF",      onClick: handleExportPDF },
          { icon: "📱", label: "WhatsApp Summary", onClick: () => {} },
          { icon: "📧", label: "Email Alert",      onClick: () => {} },
          { icon: "🔔", label: "Full Report →",    onClick: () => window.location.href = "/advisory" },
        ].map(btn => (
          <motion.button
            key={btn.label}
            whileHover={{
              background: "rgba(0,212,255,0.15)",
              borderColor:"rgba(0,212,255,0.5)",
            }}
            onClick={btn.onClick}
            style={{
              padding:    "7px 14px",
              borderRadius: 8,
              background: "rgba(0,212,255,0.06)",
              border:     "1px solid rgba(0,212,255,0.2)",
              color:      "#90A4AE",
              fontSize:   12,
              cursor:     "pointer",
              fontFamily: "Space Grotesk, sans-serif",
              display:    "flex",
              alignItems: "center",
              gap:        6,
              transition: "all 0.2s",
            }}
          >
            <span>{btn.icon}</span>
            <span>{btn.label}</span>
          </motion.button>
        ))}
      </div>
    </div>
  );
});
