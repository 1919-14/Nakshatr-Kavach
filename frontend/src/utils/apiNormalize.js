/** Normalize backend API payloads to shapes expected by existing UI (mock-compatible). */

export function normalizeKpForecast(api) {
  if (!api) return null;
  const f = api.forecast || api;
  const pick = (k) => {
    const newKey = k.replace("kp_", "");
    const o = f[k] ?? f[newKey];
    if (o && typeof o === "object" && "value" in o) return { value: o.value, uncertainty: o.uncertainty ?? 0.5 };
    if (o && typeof o === "object" && "kp" in o) return { value: o.kp, uncertainty: o.uncertainty ?? 0.5 };
    return { value: typeof o === "number" ? o : 2, uncertainty: 0.5 };
  };
  const currentKp = api.current_kp ?? api.current?.kp ?? api.kp_current;
  const peakClass = api.peak_storm_class ?? api.storm_class ?? api.summary?.peak_storm_class ?? api.current?.storm_class ?? "QUIET";
  const branch = {};
  for (const horizon of ["3hr", "6hr", "12hr", "24hr"]) {
    const source = f[horizon] ?? f[`kp_${horizon}`] ?? {};
    const fused = pick(`kp_${horizon}`);
    branch[horizon] = {
      fused: fused.value,
      uncertainty: fused.uncertainty,
      xgb: source.xgb_component ?? source.xgb_kp ?? null,
      lstm: source.lstm_component ?? source.lstm_kp ?? null,
      ci_lower_90: source.ci_lower_90 ?? null,
      ci_upper_90: source.ci_upper_90 ?? null,
      storm_class: source.storm_class ?? "QUIET",
      model_agreement: source.model_agreement ?? null,
    };
  }
  return {
    kp_3hr: pick("kp_3hr"),
    kp_6hr: pick("kp_6hr"),
    kp_12hr: pick("kp_12hr"),
    kp_24hr: pick("kp_24hr"),
    branch_predictions: branch,
    storm_probability: api.storm_probability ?? api.summary?.storm_probability_12hr ?? 0.2,
    peak_storm_class: peakClass,
    peak_arrival_minutes: api.peak_arrival_minutes ?? api.summary?.transit_warning_minutes ?? null,
    prediction_confidence: api.prediction_confidence,
    model_info: api.model_info || {},
    model_notes: api.model_notes,
    current_kp: currentKp,
    raw: api,
  };
}

export function buildKpChartData(api) {
  const cur = Number(api?.current_kp ?? api?.current?.kp ?? 2);
  const f = api?.forecast || {};
  const v = (k) => {
    const o = f[k] ?? f[k.replace("kp_", "")];
    const val = o?.value ?? o?.kp ?? o ?? cur;
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

export function buildKpChartDataWithHistory(api, historyApi) {
  const forecastPoints = buildKpChartData(api);
  const records = Array.isArray(historyApi?.records) ? historyApi.records : [];
  const history = records
    .slice()
    .sort((a, b) => String(a.computed_at_utc || "").localeCompare(String(b.computed_at_utc || "")))
    .slice(-18)
    .map((row) => ({
      time: formatChartTime(row.computed_at_utc),
      actual: numberOrNull(row.kp_current),
      forecast: null,
      upper: null,
      lower: null,
      source: "history",
    }))
    .filter((row) => row.actual !== null);

  if (!history.length) return forecastPoints;

  const nowPoint = forecastPoints.find((point) => point.time === "Now");
  const future = forecastPoints.filter((point) => point.time.startsWith("+"));
  return [
    ...history,
    ...(nowPoint ? [{ ...nowPoint, source: "latest" }] : []),
    ...future.map((point) => ({ ...point, source: "forecast" })),
  ];
}

function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatChartTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(11, 16) || String(value);
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function normalizeAdvisory(api) {
  if (!api) return null;
  const src = api.source ?? api.advisory_source;
  const source =
    src === "LLM_GROQ" ? "AI_GENERATED" : src === "RULE_BASED" ? "RULE_BASED" : src || "RULE_BASED";
  if (api.content && !api.sections) {
    return {
      ...api,
      generated_at: api.generated_at_utc,
      source,
      storm_class: api.context_snapshot?.storm_class,
      sections: [
        { title: "THREAT ASSESSMENT", content: api.content.threat_assessment || "" },
        { title: "SATELLITE OPERATIONS", content: (api.content.satellite_operations || []).map(String).join("\n") },
        { title: "INDIA GRID ASSESSMENT", content: (api.content.grid_operations || []).map(String).join("\n") },
        { title: "PRIORITY ACTION", content: api.content.priority_action || "" },
      ],
      raw: api,
    };
  }
  return { ...api, source };
}

export function normalizeSolarSnapshot(snapshot) {
  if (!snapshot) return null;
  const sw = snapshot.solar_wind || snapshot;
  const kp = snapshot.kp || snapshot;
  const xray = snapshot.xray || snapshot;
  return {
    ...snapshot,
    timestamp: sw.timestamp_utc || snapshot.last_updated_utc || snapshot.timestamp,
    bz_gsm: sw.bz_gsm ?? snapshot.bz_gsm,
    by_gsm: sw.by_gsm ?? snapshot.by_gsm,
    bx_gsm: sw.bx_gsm ?? snapshot.bx_gsm,
    bt_total: sw.bt_total ?? snapshot.bt_total,
    sw_speed: sw.sw_speed ?? sw.sw_speed_kmps ?? snapshot.sw_speed,
    proton_density: sw.proton_density ?? sw.proton_density_ccm ?? snapshot.proton_density,
    proton_temp: sw.proton_temp_kelvin ?? snapshot.proton_temp,
    xray_flux: xray.xray_flux ?? xray.xray_flux_wm2 ?? snapshot.xray_flux,
    xray_class: xray.xray_class ?? snapshot.xray_class,
    kp_current: kp.kp_current ?? snapshot.kp_current,
    storm_class: kp.storm_class ?? snapshot.storm_class,
    data_quality: snapshot.data_quality ?? snapshot.quality ?? sw.data_quality,
    data_source: snapshot.data_source ?? snapshot.source ?? sw.data_source ?? sw.source,
    is_historical: Boolean(snapshot.is_historical),
    replay_data_type: snapshot.replay_data_type,
  };
}

function slugify(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function keyFor(value) {
  return String(value || "").trim().toUpperCase();
}

function buildTleMap(tlePayload) {
  const out = new Map();
  const entries = Array.isArray(tlePayload?.satellites) ? tlePayload.satellites : [];
  entries.forEach((tle) => {
    [tle.name, tle.display_name, tle.norad_id].forEach((key) => {
      if (key !== undefined && key !== null) out.set(keyFor(key), tle);
    });
  });
  return out;
}

function normalizeSatelliteEntry(raw, tier, tleMap, index) {
  const name = raw.name || raw.display_name || `Satellite ${index + 1}`;
  const riskScores = raw.risk_scores || {};
  const norad = raw.norad_id ?? raw.tlenorad_id ?? raw.norad ?? raw.catalog_number;
  const tle = tleMap.get(keyFor(name)) || tleMap.get(keyFor(raw.display_name)) || tleMap.get(keyFor(norad));
  const composite = riskScores.composite_final ?? raw.composite_final ?? raw.composite_risk ?? 0;
  return {
    id: slugify(name) || `sat-${index}`,
    name,
    shortName: raw.short_name || raw.display_name || name,
    type: raw.orbit_type || raw.type || "CATALOG",
    altitude: raw.altitude_km ?? raw.altitude,
    inclination: raw.inclination_deg ?? raw.inclination,
    mission: raw.mission || raw.payload || (tier === 3 ? "Catalogued fleet asset" : "Operational spacecraft"),
    tier: raw.tier ?? tier,
    norad_id: norad,
    tle1: raw.tle1 || tle?.tle1,
    tle2: raw.tle2 || tle?.tle2,
    tle_epoch: tle?.epoch,
    tle_source: tle?.source,
    has_live_tle: Boolean(raw.tle1 || tle?.tle1),
    drag_risk: riskScores.drag_risk ?? raw.drag_risk ?? 0,
    charging_risk: riskScores.charging_risk ?? raw.charging_risk ?? 0,
    radiation_risk: riskScores.radiation_risk ?? raw.radiation_risk ?? 0,
    composite_risk: composite,
    risk_level: raw.risk_level || "MINIMAL",
    action: raw.recommended_action || raw.action || (tier === 3 ? "Monitor catalog asset during storm escalation" : undefined),
    safe_mode_minutes: raw.safe_mode_countdown?.safe_mode_deadline_minutes ?? raw.safe_mode_minutes ?? null,
    raw,
  };
}

export function normalizeSatelliteRisks(api, tlePayload = null) {
  if (!api) return [];
  const tleMap = buildTleMap(tlePayload);
  const makeUnique = (items) => {
    const seen = new Map();
    return items.map((sat, index) => {
      const baseId = sat.id || `sat-${index}`;
      const count = seen.get(baseId) || 0;
      seen.set(baseId, count + 1);
      return { ...sat, id: count ? `${baseId}-${count + 1}` : baseId };
    });
  };
  if (Array.isArray(api)) {
    return makeUnique(api.map((sat, index) => normalizeSatelliteEntry(sat, sat.tier || 1, tleMap, index)));
  }
  const tier1 = Object.values(api.tier1 || {});
  const tier2 = Array.isArray(api.tier2) ? api.tier2 : Object.values(api.tier2 || {});
  const tier3 = Array.isArray(api.tier3) ? api.tier3 : [];
  return makeUnique([...tier1, ...tier2, ...tier3].map((sat, index) =>
    normalizeSatelliteEntry(
      sat,
      sat.tier || (index < tier1.length ? 1 : index < tier1.length + tier2.length ? 2 : 3),
      tleMap,
      index
    )
  ));
}

export function normalizeGridRisks(api) {
  if (!api) return [];
  if (Array.isArray(api)) return api;
  return (api.corridors || []).map((risk) => ({
    id: risk.corridor_id,
    name: risk.corridor_name,
    states: (risk.states_affected || []).join(" -> "),
    voltage: `${risk.voltage_kv}kV`,
    coords: risk.map_data?.polyline_coords || [],
    gic_amps: Math.round(risk.gic_amps ?? 0),
    risk_percent: Math.round(risk.saturation_risk ?? 0),
    impact_crore: Math.round(risk.economic_impact?.total_economic_impact_crore ?? 0),
    population_millions: risk.population_served_million ?? 0,
    action: risk.load_reduction?.action || "Monitor",
    raw: risk,
  }));
}

export function normalizeShapExplain(api) {
  if (!api) return null;
  const sourceFeatures = api.features || api.top_features || api.all_features || [];
  const features = sourceFeatures.map((item) => ({
    feature: item.feature || item.feature_name || item.name,
    value: item.value ?? item.feature_value,
    shap_value: Number(item.shap_value ?? item.impact_value ?? 0),
    direction: item.direction,
    impact: item.impact,
    physics_note: item.physics_note || item.physics_explanation || item.explanation,
  }));
  return {
    method: api.method || `TreeSHAP ${api.horizon || "6hr"}`,
    horizon: api.horizon,
    base_value: api.base_value,
    predicted_kp: api.predicted_kp,
    dominant_driver: api.dominant_driver,
    features,
    raw: api,
  };
}
