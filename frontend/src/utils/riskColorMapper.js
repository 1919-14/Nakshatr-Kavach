// Risk 0–100 → color + label
export function getRiskColor(score) {
  if (score > 80) return "#EF5350";
  if (score > 60) return "#FF8F00";
  if (score > 40) return "#FDD835";
  return "#43A047";
}

export function getRiskLabel(score) {
  if (score > 80) return "CRITICAL";
  if (score > 60) return "HIGH";
  if (score > 40) return "MODERATE";
  return "LOW";
}

export function getRiskBg(score) {
  if (score > 80) return "rgba(239,83,80,0.15)";
  if (score > 60) return "rgba(255,143,0,0.15)";
  if (score > 40) return "rgba(253,216,53,0.15)";
  return "rgba(67,160,71,0.15)";
}
