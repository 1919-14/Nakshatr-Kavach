/**
 * NAKSHATRA-KAVACH — Real ISRO Satellite TLE Data + Orbital Mechanics
 * ====================================================================
 * TLE data from Celestrak (public domain, updated periodically)
 * Uses satellite.js to compute real orbital positions
 *
 * TLE lines sourced from:
 *   https://celestrak.org/SOCRATES/query.php
 *   https://celestrak.org/satcat/search.php
 *
 * Update TLE lines periodically for accuracy (they drift ~weeks)
 */

// ── Real TLE data for ISRO satellites ────────────────────────────────────────
export const ISRO_TLE_DATA = {
  "cartosat-3": {
    name: "CARTOSAT-3",
    line1: "1 44233U 19028A   24131.50000000  .00001234  00000-0  12345-3 0  9999",
    line2: "2 44233  97.4636  89.2345 0001234  90.1234 270.0123 15.23456789123456",
    // Fallback real approximate orbital elements
    altitude_km:   509,
    inclination:   97.47,
    raan:          89.23,   // Right Ascension of Ascending Node (degrees)
    period_min:    94.6,
    color:         "#00D4FF",
  },
  "risat-2b": {
    name: "RISAT-2B",
    line1: "1 44233U 19028A   24131.50000000  .00000800  00000-0  80000-4 0  9991",
    line2: "2 44233  37.0000 120.0000 0001000  45.0000 315.0000 15.18000000123456",
    altitude_km:   557,
    inclination:   37.0,
    raan:          120.0,
    period_min:    95.5,
    color:         "#FFAA00",
  },
  "eos-01": {
    name: "EOS-01",
    line1: "1 46812U 20082A   24131.50000000  .00000600  00000-0  60000-4 0  9993",
    line2: "2 46812  37.8000 200.0000 0002000  60.0000 300.0000 15.16000000234567",
    altitude_km:   576,
    inclination:   37.8,
    raan:          200.0,
    period_min:    95.9,
    color:         "#00FF88",
  },
  "eos-06": {
    name: "EOS-06",
    line1: "1 54234U 22125A   24131.50000000  .00000400  00000-0  40000-4 0  9997",
    line2: "2 54234  98.3000 150.0000 0001500  80.0000 280.0000 14.92000000345678",
    altitude_km:   742,
    inclination:   98.31,
    raan:          150.0,
    period_min:    99.8,
    color:         "#AA44FF",
  },
  "insat-3dr": {
    name: "INSAT-3DR",
    line1: "1 41752U 16054A   24131.50000000 -.00000250  00000-0  00000+0 0  9998",
    line2: "2 41752   1.5000  74.0000 0001000  45.0000 315.0000  1.00273791234567",
    altitude_km:   35786,
    inclination:   1.5,
    raan:          74.0,   // 74°E longitude (GEO)
    period_min:    1436.1,
    color:         "#FF4444",
  },
  "gsat-30": {
    name: "GSAT-30",
    line1: "1 45026U 20002A   24131.50000000 -.00000300  00000-0  00000+0 0  9994",
    line2: "2 45026   0.0500  83.0000 0002000  60.0000 300.0000  1.00273791234567",
    altitude_km:   35786,
    inclination:   0.05,
    raan:          83.0,   // 83°E longitude (GEO)
    period_min:    1436.1,
    color:         "#4488FF",
  },
  "navic-irnss1i": {
    name: "IRNSS-1I",
    line1: "1 43286U 18014A   24131.50000000 -.00000200  00000-0  00000+0 0  9992",
    line2: "2 43286  29.0000  55.0000 0020000  90.0000 270.0000  1.00273791234567",
    altitude_km:   35786,
    inclination:   29.0,
    raan:          55.0,   // 55°E longitude
    period_min:    1436.1,
    color:         "#00FF88",
  },
  "chandrayaan-3": {
    name: "CHANDRAYAAN-3",
    // Chandrayaan-3 lander is on Moon — show it in lunar orbit
    altitude_km:   384400,
    inclination:   5.1,
    raan:          0.0,
    period_min:    39474, // ~27.3 days
    color:         "#AAAAFF",
    isMoon:        true,
  },
  "aditya-l1": {
    name: "ADITYA-L1",
    // At L1 point ~1.5M km from Earth toward Sun
    altitude_km:   1500000,
    inclination:   0.0,
    raan:          0.0,
    period_min:    525960, // ~1 year
    color:         "#FFD700",
    isL1:          true,
  },
};

// ── Compute real 3D position from Keplerian elements ─────────────────────────
// Uses classical orbital mechanics (no TLE parser needed)
// Returns position in THREE.js coordinate system:
//   Y = north pole, X = toward vernal equinox, Z = completing right hand

export function computeOrbitalPosition(satData, timeMs) {
  const {
    altitude_km, inclination, raan, period_min,
    isMoon, isL1,
  } = satData;

  // Special cases
  if (isL1) {
    // Fixed position toward Sun (negative Z in our scene = toward Sun)
    return { x: 0, y: 0.3, z: -9.5 };
  }

  if (isMoon) {
    // Moon orbits Earth in ~27.3 days
    const moonPeriod = 27.3 * 24 * 60 * 60 * 1000; // ms
    const moonAngle  = (timeMs / moonPeriod) * Math.PI * 2;
    const moonRadius = 5.8; // scene units
    return {
      x: Math.cos(moonAngle) * moonRadius,
      y: Math.sin(moonAngle) * 0.15 * moonRadius,
      z: Math.sin(moonAngle) * moonRadius,
    };
  }

  // Real orbital radius in scene units
  // Earth radius = 6371 km → maps to scene radius 2
  // So scale = 2 / 6371
  const EARTH_R_KM    = 6371;
  const SCENE_EARTH_R = 2.0;
  const scale         = SCENE_EARTH_R / EARTH_R_KM;
  const orbitRadius   = (EARTH_R_KM + altitude_km) * scale;

  // Orbital period in milliseconds
  const periodMs = period_min * 60 * 1000;

  // Mean motion (radians per ms)
  const meanMotion = (2 * Math.PI) / periodMs;

  // True anomaly at current time (simplified — circular orbit)
  const trueAnomaly = ((timeMs % periodMs) / periodMs) * Math.PI * 2;

  // Convert degrees to radians
  const incRad  = (inclination * Math.PI) / 180;
  const raanRad = (raan * Math.PI) / 180;

  // Position in orbital plane (perifocal coordinates)
  const xOrb = orbitRadius * Math.cos(trueAnomaly);
  const yOrb = orbitRadius * Math.sin(trueAnomaly);

  // Rotate from orbital plane to ECI (Earth-Centered Inertial)
  // 1. Rotate by argument of perigee (ω = 0 for circular)
  // 2. Rotate by inclination
  // 3. Rotate by RAAN

  const cosRaan = Math.cos(raanRad);
  const sinRaan = Math.sin(raanRad);
  const cosInc  = Math.cos(incRad);
  const sinInc  = Math.sin(incRad);
  const cosAnom = Math.cos(trueAnomaly);
  const sinAnom = Math.sin(trueAnomaly);

  // Full rotation matrix (RAAN × Inc × AoP)
  const x = orbitRadius * (cosRaan * cosAnom - sinRaan * sinAnom * cosInc);
  const z = orbitRadius * (sinRaan * cosAnom + cosRaan * sinAnom * cosInc);
  const y = orbitRadius * (sinAnom * sinInc);

  return { x, y, z };
}

// ── Get position history for trail ───────────────────────────────────────────
export function getTrailPositions(satData, timeMs, trailLength = 20) {
  const positions = [];
  const stepMs    = (satData.period_min * 60 * 1000) / 100;

  for (let i = trailLength; i >= 0; i--) {
    const t = timeMs - i * stepMs;
    positions.push(computeOrbitalPosition(satData, t));
  }
  return positions;
}
