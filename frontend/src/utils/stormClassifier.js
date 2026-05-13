// NAKSHATRA-KAVACH — Storm Classifier Utility

export const STORM_CLASSES = {
  QUIET:  { label: "QUIET",      kpMin: 0, kpMax: 4.9, color: "#607D8B", glowClass: "",       bg: "rgba(96,125,139,0.2)"  },
  G1:     { label: "G1 MINOR",   kpMin: 5, kpMax: 5.9, color: "#4CAF50", glowClass: "glow-g1", bg: "rgba(76,175,80,0.2)"  },
  G2:     { label: "G2 MODERATE",kpMin: 6, kpMax: 6.9, color: "#CDDC39", glowClass: "glow-g2", bg: "rgba(205,220,57,0.2)" },
  G3:     { label: "G3 STRONG",  kpMin: 7, kpMax: 7.9, color: "#FF9800", glowClass: "glow-g3", bg: "rgba(255,152,0,0.2)"  },
  G4:     { label: "G4 SEVERE",  kpMin: 8, kpMax: 8.9, color: "#F44336", glowClass: "glow-g4", bg: "rgba(244,67,54,0.2)"  },
  G5:     { label: "G5 EXTREME", kpMin: 9, kpMax: 9.9, color: "#9C27B0", glowClass: "glow-g5", bg: "rgba(156,39,176,0.2)" },
};

export function getStormClass(kp) {
  if (kp >= 9) return STORM_CLASSES.G5;
  if (kp >= 8) return STORM_CLASSES.G4;
  if (kp >= 7) return STORM_CLASSES.G3;
  if (kp >= 6) return STORM_CLASSES.G2;
  if (kp >= 5) return STORM_CLASSES.G1;
  return STORM_CLASSES.QUIET;
}

export function getEdgeGlow(kp) {
  if (kp >= 9) return "inset 0 0 80px rgba(156,39,176,0.5)";
  if (kp >= 8) return "inset 0 0 80px rgba(244,67,54,0.4)";
  if (kp >= 7) return "inset 0 0 80px rgba(255,152,0,0.3)";
  return "none";
}

export function getXRayColor(xrayClass) {
  if (!xrayClass) return "#607D8B";
  const c = xrayClass[0].toUpperCase();
  return { A:"#607D8B", B:"#00D4FF", C:"#CDDC39", M:"#FF9800", X:"#F44336" }[c] || "#607D8B";
}

export function getBzColor(bz) {
  if (bz < -20) return "#F44336";
  if (bz < -10) return "#FF9800";
  if (bz < 0)   return "#CDDC39";
  return "#4CAF50";
}

export function getKpColor(kp) {
  return getStormClass(kp).color;
}
