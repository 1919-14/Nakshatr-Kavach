import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

export function LoadingScreen({ onDone }) {
  const [progress, setProgress] = useState(0);
  const [step, setStep]         = useState(0);

  const steps = [
    "Connecting to NOAA SWPC...",
    "Loading satellite catalog...",
    "Initializing orbital engine...",
    "Fetching Kp forecast...",
    "Calibrating storm model...",
    "NAKSHATRA-KAVACH ready",
  ];

  useEffect(() => {
    const t = setInterval(() => {
      setProgress(p => {
        const next = p + (Math.random() * 18 + 8);
        if (next >= 100) {
          clearInterval(t);
          setTimeout(onDone, 600);
          return 100;
        }
        setStep(s => Math.min(steps.length - 1, Math.floor(next / 20)));
        return next;
      });
    }, 180);
    return () => clearInterval(t);
  }, []);

  return (
    <motion.div
      initial={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.6 }}
      style={{
        position:       "fixed", inset: 0,
        background:     "#020817",
        display:        "flex",
        flexDirection:  "column",
        alignItems:     "center",
        justifyContent: "center",
        zIndex:         9999,
        gap:            24,
      }}
    >
      {/* Logo */}
      <motion.div
        initial={{ scale: 0.5, opacity: 0 }}
        animate={{ scale: 1,   opacity: 1 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        style={{ textAlign: "center" }}
      >
        {/* Animated orbit ring */}
        <svg width="80" height="80" viewBox="0 0 80 80" style={{ marginBottom: 12 }}>
          <circle cx="40" cy="40" r="18" fill="none" stroke="#00D4FF" strokeWidth="2" opacity="0.9" />
          <circle cx="40" cy="40" r="6" fill="#00D4FF" opacity="0.9" />
          <motion.ellipse cx="40" cy="40" rx="36" ry="14" fill="none"
            stroke="#00D4FF" strokeWidth="1" strokeDasharray="5 3" opacity="0.45"
            animate={{ rotate: 360 }} transition={{ duration: 4, repeat: Infinity, ease:"linear" }}
            style={{ transformOrigin:"40px 40px" }} />
          <motion.circle cx="72" cy="40" r="4" fill="#FFD700"
            animate={{ rotate: 360 }} transition={{ duration: 4, repeat: Infinity, ease:"linear" }}
            style={{ transformOrigin:"40px 40px" }} />
        </svg>

        <div style={{
          fontFamily:    "Orbitron, sans-serif",
          fontSize:      22, fontWeight: 900,
          color:         "#00D4FF",
          letterSpacing: "0.1em",
          textShadow:    "0 0 20px rgba(0,212,255,0.5)",
        }}>
          NAKSHATRA-KAVACH
        </div>
        <div style={{
          fontFamily: "Space Grotesk, sans-serif",
          fontSize:   11, color: "#546E7A",
          letterSpacing: "0.18em", marginTop: 4,
        }}>
          SPACE WEATHER IMPACT INTELLIGENCE
        </div>
      </motion.div>

      {/* Progress bar */}
      <div style={{ width: 280 }}>
        <div style={{
          height:       4,
          background:   "rgba(255,255,255,0.06)",
          borderRadius: 2,
          overflow:     "hidden",
          marginBottom: 10,
        }}>
          <motion.div
            animate={{ width: `${progress}%` }}
            transition={{ duration: 0.2 }}
            style={{
              height:     "100%",
              background: "linear-gradient(90deg, #00D4FF, #FFD700)",
              borderRadius: 2,
            }}
          />
        </div>
        <div style={{
          fontFamily:    "JetBrains Mono, monospace",
          fontSize:      11,
          color:         "#546E7A",
          textAlign:     "center",
          letterSpacing: "0.06em",
          minHeight:     18,
        }}>
          {steps[step]}
        </div>
      </div>

      {/* ISRO badge */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.8 }}
        style={{
          fontSize:      10,
          color:         "#334455",
          fontFamily:    "Space Grotesk, sans-serif",
          letterSpacing: "0.12em",
        }}
      >
        PROTECTING INDIA'S SPACE ASSETS
      </motion.div>
    </motion.div>
  );
}
