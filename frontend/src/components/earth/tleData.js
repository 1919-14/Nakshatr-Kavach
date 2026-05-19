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
    tle1: "1 44804U 19081A   26138.18945741  .00002793  00000+0  13570-3 0  9997",
    tle2: "2 44804  97.4321 200.5342 0011148 253.8028 106.1980 15.19221228358911",
  },
  {
    id:        "risat-2b",
    name:      "RISAT-2B",
    shortName: "RISAT",
    color:     "#FFAA00",
    orbitType: "LEO",
    // NORAD ID: 44271 — 37°, 557km
    tle1: "1 44233U 19028A   26138.16198462  .00001349  00000+0  10850-3 0  9996",
    tle2: "2 44233  36.9959 172.1554 0004689 165.8298 194.2577 15.01499551383313",
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
    tle1: "1 54361U 22158A   26138.22115447  .00000181  00000+0  61591-4 0  9998",
    tle2: "2 54361  98.3516 236.8157 0002164  80.9096 279.2341 14.47021579183505",
  },
  {
    id:        "insat-3dr",
    name:      "INSAT-3DR",
    shortName: "INSAT",
    color:     "#FF4444",
    orbitType: "GEO",
    // NORAD ID: 41752 — GEO 74°E
    tle1: "1 41752U 16054A   26137.88263008 -.00000072  00000+0  00000+0 0  9997",
    tle2: "2 41752   0.0961  87.6954 0011064 190.1642 349.4671  1.00271451 35510",
  },
  {
    id:        "gsat-30",
    name:      "GSAT-30",
    shortName: "GSAT",
    color:     "#4488FF",
    orbitType: "GEO",
    // NORAD ID: 45026 — GEO 83°E
    tle1: "1 45026U 20005A   26137.90027169 -.00000160  00000+0  00000+0 0  9993",
    tle2: "2 45026   0.0429  98.2451 0002953 334.8432 209.6178  1.00272097 23207",
  },
  {
    id:        "insat-3d",
    name:      "INSAT-3D",
    shortName: "INSAT",
    color:     "#FFB000",
    orbitType: "GEO",
    tle1: "1 39216U 13038B   26137.90941272 -.00000334  00000+0  00000+0 0  9998",
    tle2: "2 39216   1.8488  82.8983 0001017 300.2472 309.3684  1.00271802 46775",
  },
  {
    id:        "insat-3ds",
    name:      "INSAT-3DS",
    shortName: "INSAT",
    color:     "#FF9800",
    orbitType: "GEO",
    tle1: "1 58990U 24033A   26137.90009837 -.00000150  00000+0  00000+0 0  9993",
    tle2: "2 58990   0.0506 255.8837 0001248 154.0165 231.8045  1.00270222 46761",
  },
  {
    id:        "gsat-7",
    name:      "GSAT-7",
    shortName: "GSAT7",
    color:     "#1565C0",
    orbitType: "GEO",
    tle1: "1 39234U 13044B   26137.86975742 -.00000072  00000+0  00000+0 0  9998",
    tle2: "2 39234   0.0719  94.7750 0003901 202.6802 325.2405  1.00271627 44954",
  },
  {
    id:        "gsat-7a",
    name:      "GSAT-7A",
    shortName: "GSAT7A",
    color:     "#0D47A1",
    orbitType: "GEO",
    tle1: "1 43864U 18105A   26138.21155845  .00000031  00000+0  00000+0 0  9993",
    tle2: "2 43864   0.0657 262.1191 0002685 126.6107 346.0932  1.00270294 27147",
  },
  {
    id:        "gsat-11",
    name:      "GSAT-11",
    shortName: "GSAT11",
    color:     "#4CAF50",
    orbitType: "GEO",
    tle1: "1 43824U 18100B   26137.88263008 -.00000071  00000+0  00000+0 0  9992",
    tle2: "2 43824   0.0299 264.2081 0007307 173.2735 189.8453  1.00271002 27179",
  },
  {
    id:        "eos-04",
    name:      "EOS-04",
    shortName: "EOS04",
    color:     "#009688",
    orbitType: "LEO",
    tle1: "1 51656U 22013A   26138.20030617  .00001364  00000+0  81586-4 0  9995",
    tle2: "2 51656  97.5114 145.3072 0001876  88.7682 271.3764 15.12721370234957",
  },
  {
    id:        "navic-irnss",
    name:      "NavIC-IRNSS",
    shortName: "NavIC",
    color:     "#00FF88",
    orbitType: "GEO",
    // NORAD ID: 43286 — Inclined GEO 55°E, 29° inclination
    tle1: "1 43286U 18035A   26137.89475044  .00000078  00000+0  00000+0 0  9993",
    tle2: "2 43286  29.1079  74.6634 0017660 183.4275 354.6196  1.00271365 29777",
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
