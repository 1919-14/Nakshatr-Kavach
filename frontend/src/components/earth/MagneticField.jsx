import React, { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

// Dipole magnetic field line approximation
// r = r0 * cos²(lat) — standard dipole equation
function dipoleFieldLine(lat0Deg, lonDeg, steps = 80) {
  const points = [];
  const lon    = (lonDeg * Math.PI) / 180;

  for (let i = 0; i <= steps; i++) {
    const t   = (i / steps) * Math.PI;   // 0 → π (north pole → south pole)
    const lat = lat0Deg * Math.cos(t - Math.PI / 2);
    const r   = 2.05 * Math.cos((lat * Math.PI) / 180) ** 2 + 0.4 + Math.abs(lat0Deg / 90) * 2.5;

    const latR = (lat * Math.PI) / 180;
    const x = r * Math.cos(latR) * Math.cos(lon);
    const y = r * Math.sin(latR);
    const z = r * Math.cos(latR) * Math.sin(lon);
    points.push(new THREE.Vector3(x, y, z));
  }
  return points;
}

export function MagneticField({ kp = 0 }) {
  const groupRef = useRef();

  // 10 field lines at different longitudes
  const fieldLines = useMemo(() => {
    const lines = [];
    const lons  = [0, 36, 72, 108, 144, 180, 216, 252, 288, 324];
    const lats  = [55, 65, 70, 65, 55, 65, 70, 65, 55, 65];
    lons.forEach((lon, i) => {
      lines.push(dipoleFieldLine(lats[i], lon));
    });
    return lines;
  }, []);

  const baseOpacity = 0.06;
  const stormOpacity = Math.min(0.28, baseOpacity + (kp / 9) * 0.22);

  useFrame(({ clock }) => {
    if (!groupRef.current) return;
    const t = clock.getElapsedTime();
    groupRef.current.children.forEach((line, i) => {
      if (line.material) {
        line.material.opacity =
          stormOpacity + Math.sin(t * 0.6 + i * 0.4) * 0.02;
      }
    });
  });

  return (
    <group ref={groupRef}>
      {fieldLines.map((points, i) => {
        const geo = new THREE.BufferGeometry().setFromPoints(points);
        return (
          <line key={i} geometry={geo}>
            <lineBasicMaterial
              color="#00D4FF"
              transparent
              opacity={stormOpacity}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
            />
          </line>
        );
      })}
    </group>
  );
}
