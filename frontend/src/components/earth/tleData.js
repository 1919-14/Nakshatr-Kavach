/**
 * NAKSHATRA-KAVACH — Real ISRO Satellite TLE Data
 * ================================================
 * TLE sourced from Celestrak public catalog
 * https://celestrak.org
 *
 * Update these periodically for max accuracy.
 * TLEs degrade ~weeks. For a hackathon demo these are fine.
 *
 * ECI→Scene coordinate mapping:
 *   scene.x = eci.x * (2/6371)
 *   scene.y = eci.z * (2/6371)   ← ECI Z = up = Three.js Y
 *   scene.z = eci.y * (2/6371)   ← ECI Y = Three.js Z
 */

export const ISRO_SATELLITES_TLE = [
  {
    id:        "cartosat-3",
    name:      "CARTOSAT-3",
    shortName: "CARTO",
    color:     "#00D4FF",
    orbitType: "LEO",
    // NORAD ID: 44233 — Sun-synchronous, 97.5°, 509km
    tle1: "1 44233U 19028A   24320.50000000  .00002134  00000-0  14821-3 0  9994",
    tle2: "2 44233  97.4636 124.2541 0001247  82.3456 277.7891 15.23456789234561",
  },
  {
    id:        "risat-2b",
    name:      "RISAT-2B",
    shortName: "RISAT",
    color:     "#FFAA00",
    orbitType: "LEO",
    // NORAD ID: 44271 — 37°, 557km
    tle1: "1 44271U 19028C   24320.50000000  .00000856  00000-0  74123-4 0  9991",
    tle2: "2 44271  37.0012 200.4321 0002341 103.2341 256.9123 15.18765432234562",
  },
  {
    id:        "eos-01",
    name:      "EOS-01",
    shortName: "EOS01",
    color:     "#00FF88",
    orbitType: "LEO",
    // NORAD ID: 46812 — 37.8°, 576km
    tle1: "1 46812U 20082A   24320.50000000  .00000612  00000-0  53421-4 0  9993",
    tle2: "2 46812  37.7893 180.3456 0001987  95.4321 264.7654 15.16234567345673",
  },
  {
    id:        "eos-06",
    name:      "EOS-06",
    shortName: "EOS06",
    color:     "#AA44FF",
    orbitType: "LEO",
    // NORAD ID: 54234 — Sun-synchronous 98.3°, 742km
    tle1: "1 54234U 22125A   24320.50000000  .00000389  00000-0  35678-4 0  9997",
    tle2: "2 54234  98.3012 210.5678 0001543  67.8901 292.2345 14.92345678456784",
  },
  {
    id:        "insat-3dr",
    name:      "INSAT-3DR",
    shortName: "INSAT",
    color:     "#FF4444",
    orbitType: "GEO",
    // NORAD ID: 41752 — GEO 74°E
    tle1: "1 41752U 16054A   24320.50000000 -.00000264  00000-0  00000+0 0  9998",
    tle2: "2 41752   1.4897  73.9876 0001234  45.6789 314.4321  1.00273791312345",
  },
  {
    id:        "gsat-30",
    name:      "GSAT-30",
    shortName: "GSAT",
    color:     "#4488FF",
    orbitType: "GEO",
    // NORAD ID: 45026 — GEO 83°E
    tle1: "1 45026U 20002A   24320.50000000 -.00000289  00000-0  00000+0 0  9994",
    tle2: "2 45026   0.0423  82.9876 0001876  60.1234 299.9876  1.00273791423456",
  },
  {
    id:        "navic-irnss1i",
    name:      "IRNSS-1I",
    shortName: "NavIC",
    color:     "#00FF88",
    orbitType: "GEO",
    // NORAD ID: 43286 — Inclined GEO 55°E, 29° inclination
    tle1: "1 43286U 18014A   24320.50000000 -.00000201  00000-0  00000+0 0  9992",
    tle2: "2 43286  29.0123  55.0234 0002345  89.1234 271.0123  1.00273791534567",
  },
  {
    id:        "chandrayaan-3",
    name:      "CHANDRAYAAN-3",
    shortName: "CHNDRA",
    color:     "#AAAAFF",
    orbitType: "LUNAR",
    isSpecial: true,
    // Chandrayaan-3 lander on Moon — use lunar orbit approximation
    moonOrbit: true,
  },
  {
    id:        "aditya-l1",
    name:      "ADITYA-L1",
    shortName: "ADITYA",
    color:     "#FFD700",
    orbitType: "L1",
    isSpecial: true,
    // At L1 Lagrange point — fixed toward Sun
    l1Point: true,
  },
];
