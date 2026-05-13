import React, { useRef, useMemo, memo, useEffect, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Html, Trail } from "@react-three/drei";
import * as THREE from "three";
import { getRiskColor } from "../../utils/riskColorMapper";
import {
  buildSatrec, getScenePosition, buildOrbitRing,
  getMoonPosition, getL1Position, ORBIT_PERIODS,
} from "./propagation";
import { ISRO_SATELLITES_TLE } from "./tleData";

export const SatelliteOrbit = memo(({ satellite, riskScore = 0, isSelected, onClick }) => {
  const satRef  = useRef();
  const auraRef = useRef();
  const color   = getRiskColor(riskScore);

  // Find TLE data for this satellite
  const tleEntry = useMemo(() =>
    ISRO_SATELLITES_TLE.find(s => s.id === satellite.id) || null,
    [satellite.id]
  );

  // Build satrec once
  const satrec = useMemo(() => {
    if (!tleEntry || tleEntry.isSpecial) return null;
    return buildSatrec(tleEntry.tle1, tleEntry.tle2);
  }, [tleEntry]);

  // Build orbit ring (computed once at mount, reflects real orbital plane)
  const orbitPoints = useMemo(() => {
    if (!tleEntry) return [];
    if (tleEntry.moonOrbit || tleEntry.l1Point) return [];
    if (!satrec) return [];

    const period = ORBIT_PERIODS[satellite.id] || 95;
    const raw    = buildOrbitRing(satrec, period);
    return raw.map(p => new THREE.Vector3(p.x, p.y, p.z));
  }, [satrec, satellite.id, tleEntry]);

  const orbitGeo = useMemo(() => {
    if (!orbitPoints.length) return null;
    const geo = new THREE.BufferGeometry().setFromPoints(orbitPoints);
    return geo;
  }, [orbitPoints]);

  // Animate satellite position every frame using real SGP4 propagation
  useFrame(({ clock }) => {
    if (!satRef.current) return;

    let pos;

    if (tleEntry?.moonOrbit) {
      pos = getMoonPosition(new Date());
    } else if (tleEntry?.l1Point) {
      pos = getL1Position();
    } else if (satrec) {
      // Real SGP4 propagation — accelerated for visualization
      // Real time × 60 = orbital positions advance 60× faster
      const speedUp  = 1;
      const fakeDate = new Date(Date.now() + clock.getElapsedTime() * 1000 * speedUp * 60);
      pos = getScenePosition(satrec, fakeDate);
    } else {
      // Fallback simple circular orbit
      const t = clock.getElapsedTime();
      const r = 2.6;
      pos = { x: Math.cos(t * 0.3) * r, y: 0, z: Math.sin(t * 0.3) * r };
    }

    satRef.current.position.set(pos.x, pos.y, pos.z);

    // Aura pulse based on risk
    if (auraRef.current) {
      const t2         = clock.getElapsedTime();
      const pulseSpeed = riskScore > 80 ? 4 : riskScore > 60 ? 2.5 : 1;
      const pulseMag   = riskScore > 80 ? 0.25 : 0.08;
      auraRef.current.scale.setScalar(1 + Math.sin(t2 * pulseSpeed) * pulseMag);
      auraRef.current.material.opacity =
        riskScore > 80 ? 0.38 + Math.sin(t2 * pulseSpeed) * 0.15 :
        riskScore > 60 ? 0.22 : 0.12;
    }
  });

  const orbitAlpha = isSelected ? 0.9 : 0.3;

  return (
    <group>
      {/* Real orbit ring */}
      {orbitGeo && (
        <line geometry={orbitGeo}>
          <lineBasicMaterial
            color={color}
            transparent
            opacity={orbitAlpha}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </line>
      )}

      {/* Satellite body with trail */}
      <group ref={satRef}>
        <Trail
          width={0.022}
          length={14}
          color={color}
          attenuation={t => t * t}
          decay={1}
        >
          <mesh onClick={onClick}>
            <sphereGeometry args={[0.05, 8, 8]} />
            <meshBasicMaterial
              color={isSelected ? "#ffffff" : color}
              toneMapped={false}
            />
          </mesh>
        </Trail>

        {/* Aura glow */}
        <mesh ref={auraRef}>
          <sphereGeometry args={[0.18, 10, 10]} />
          <meshBasicMaterial
            color={color}
            transparent
            opacity={0.15}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </mesh>

        {/* Label */}
        <Html
          distanceFactor={14}
          position={[0, 0.22, 0]}
          center
          occlude={false}
          style={{ pointerEvents: "none" }}
        >
          <div
            onClick={onClick}
            style={{
              color,
              fontSize:      9,
              fontFamily:    "Orbitron, sans-serif",
              fontWeight:    700,
              whiteSpace:    "nowrap",
              textShadow:    `0 0 8px ${color}`,
              padding:       "2px 6px",
              background:    "rgba(2,8,23,0.78)",
              borderRadius:  4,
              border:        `1px solid ${color}44`,
              letterSpacing: "0.06em",
              pointerEvents: "auto",
              cursor:        "pointer",
            }}
          >
            {tleEntry?.shortName || satellite.shortName || satellite.id.toUpperCase()}
          </div>
        </Html>
      </group>
    </group>
  );
});
