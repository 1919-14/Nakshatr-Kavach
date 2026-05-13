/** Normalize backend API payloads to shapes expected by existing UI (mock-compatible). */

export function normalizeKpForecast(api) {
  if (!api) return null;
  const f = api.forecast || api;
  const pick = (k) => {
    const o = f[k];
    if (o && typeof o === "object" && "value" in o) return { value: o.value, uncertainty: o.uncertainty ?? 0.5 };
    return { value: typeof o === "number" ? o : 2, uncertainty: 0.5 };
  };
  return {
    kp_3hr: pick("kp_3hr"),
    kp_6hr: pick("kp_6hr"),
    kp_12hr: pick("kp_12hr"),
    kp_24hr: pick("kp_24hr"),
    storm_probability: api.storm_probability ?? 0.2,
    peak_storm_class: api.storm_class || "QUIET",
    peak_arrival_minutes: api.peak_arrival_minutes ?? 45,
    model_notes: api.model_notes,
    current_kp: api.current_kp,
    raw: api,
  };
}

export function buildKpChartData(api) {
  const cur = Number(api?.current_kp ?? 2);
  const f = api?.forecast || {};
  const v = (k) => {
    const o = f[k];
    const val = o?.value ?? o ?? cur;
    const u = o?.uncertainty ?? 0.6;
    return { val: Number(val), u: Number(u) };
  };
  const h3 = v("kp_3hr");
  const h6 = v("kp_6hr");
  const h12 = v("kp_12hr");
  const h24 = v("kp_24hr");
  return [
    { time: "-12h", actual: Math.max(0, cur - 1.2), forecast: null, upper: null, lower: null },
    { time: "-6h", actual: Math.max(0, cur - 0.6), forecast: null, upper: null, lower: null },
    { time: "Now", actual: cur, forecast: cur, upper: cur + 0.4, lower: cur - 0.4 },
    { time: "+3h", actual: null, forecast: h3.val, upper: h3.val + h3.u, lower: Math.max(0, h3.val - h3.u) },
    { time: "+6h", actual: null, forecast: h6.val, upper: h6.val + h6.u, lower: Math.max(0, h6.val - h6.u) },
    { time: "+12h", actual: null, forecast: h12.val, upper: h12.val + h12.u, lower: Math.max(0, h12.val - h12.u) },
    { time: "+18h", actual: null, forecast: (h12.val + h24.val) / 2, upper: h24.val + h24.u, lower: Math.max(0, h12.val - h12.u) },
    { time: "+24h", actual: null, forecast: h24.val, upper: h24.val + h24.u, lower: Math.max(0, h24.val - h24.u) },
  ];
}

export function normalizeAdvisory(api) {
  if (!api) return null;
  const src = api.advisory_source || api.source;
  const source =
    src === "LLM_GROQ" ? "AI_GENERATED" : src === "RULE_BASED" ? "RULE_BASED" : src || "RULE_BASED";
  return { ...api, source };
}
