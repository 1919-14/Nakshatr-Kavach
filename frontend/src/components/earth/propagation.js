/**
 * NAKSHATRA-KAVACH — Real Satellite Propagation
 * ==============================================
 * Uses satellite.js SGP4/SDP4 propagator for true orbital mechanics
 *
 * Coordinate system:
 *   satellite.js ECI: x=vernal equinox, y=90°E equator, z=north pole
 *   Three.js scene:   x=right, y=up, z=toward viewer
 *
 *   Mapping:
 *     scene.x =  eci.x * SCALE
 *     scene.y =  eci.z * SCALE   (ECI north pole → Three.js up)
 *     scene.z = -eci.y * SCALE   (ECI y → negative Three.js z)
 *
 * Earth radius 6371km → scene radius 2.0 units
 * SCALE = 2.0 / 6371
 */

import * as satellite from "satellite.js";

const EARTH_R_KM = 6371;
const SCENE_R    = 2.0;
export const KM_TO_SCENE = SCENE_R / EARTH_R_KM;

// ── Precompile TLE → satrec (do once, reuse every frame) ─────────────────────
export function buildSatrec(tle1, tle2) {
  try {
    return satellite.twoline2satrec(tle1, tle2);
  } catch (e) {
    console.warn("TLE parse error:", e);
    return null;
  }
}

// ── Propagate to current time → Three.js scene coordinates ───────────────────
export function getScenePosition(satrec, date) {
  if (!satrec) return { x: 3, y: 0, z: 0 };

  try {
    const pv = satellite.propagate(satrec, date);
    if (!pv || !pv.position || typeof pv.position.x !== "number") {
      return { x: 3, y: 0, z: 0 };
    }

    // ECI km → scene units with axis remap
    return {
      x:  pv.position.x * KM_TO_SCENE,
      y:  pv.position.z * KM_TO_SCENE,   // ECI z (north) → Three.js y (up)
      z: -pv.position.y * KM_TO_SCENE,   // ECI y → negative Three.js z
    };
  } catch (e) {
    return { x: 3, y: 0, z: 0 };
  }
}

// ── Get lat/lon for HUD display ────────────────────────────────────────────────
export function getLatLon(satrec, date) {
  if (!satrec) return { lat: 0, lon: 0, alt: 0 };

  try {
    const pv   = satellite.propagate(satrec, date);
    const gmst = satellite.gstime(date);
    const geo  = satellite.eciToGeodetic(pv.position, gmst);
    return {
      lat: satellite.degreesLat(geo.latitude),
      lon: satellite.degreesLong(geo.longitude),
      alt: geo.height,
    };
  } catch (e) {
    return { lat: 0, lon: 0, alt: 0 };
  }
}

// ── Build full orbit ring (one complete revolution) ───────────────────────────
export function buildOrbitRing(satrec, periodMinutes) {
  if (!satrec) return [];

  const points    = [];
  const steps     = 120;
  const now       = new Date();
  const periodMs  = (periodMinutes || 95) * 60 * 1000;
  const stepMs    = periodMs / steps;

  for (let i = 0; i <= steps; i++) {
    const t   = new Date(now.getTime() + i * stepMs);
    const pos = getScenePosition(satrec, t);
    points.push(pos);
  }

  return points;
}

// ── Special orbit: Moon ────────────────────────────────────────────────────────
export function getMoonPosition(date) {
  // Moon orbital period: 27.32 days
  const T_MS   = 27.32 * 24 * 60 * 60 * 1000;
  const angle  = (date.getTime() % T_MS) / T_MS * Math.PI * 2;
  // Moon is ~384,400 km from Earth
  // Scale: 384400 * (2/6371) = ~120 scene units — too far, cap at 5.5
  const r = 5.5;
  return {
    x: Math.cos(angle) * r,
    y: Math.sin(angle) * 0.09 * r,  // slight inclination
    z: Math.sin(angle) * r,
  };
}

// ── Special orbit: L1 point (toward Sun) ─────────────────────────────────────
export function getL1Position() {
  // Sun is always in the +X direction in our scene
  // L1 is 1.5M km from Earth toward Sun
  // Scaled: 1500000 * (2/6371) ≈ 470 units → cap at 8.5
  return { x: 8.5, y: 0.2, z: 0 };
}

// ── Period estimates (minutes) ────────────────────────────────────────────────
export const ORBIT_PERIODS = {
  "cartosat-3":    94.6,
  "risat-2b":      95.5,
  "eos-01":        95.9,
  "eos-06":        99.8,
  "insat-3dr":     1436.1,
  "gsat-30":       1436.1,
  "navic-irnss1i": 1436.1,
};
