import React, { useRef, useMemo, memo } from "react";
import { useFrame } from "@react-three/fiber";
import { Html, Trail } from "@react-three/drei";
import * as THREE from "three";
import { getRiskColor } from "../../utils/riskColorMapper";
import {
  buildSatrec, getScenePosition, buildOrbitRing,
  getMoonPosition, getL1Position, ORBIT_PERIODS,
} from "./propagation";
import { ISRO_SATELLITES_TLE } from "./tleData";

function keyFor(value) {
  return String(value || "").trim().toLowerCase();
}

function hashSeed(value) {
  let hash = 0;
  const text = String(value || "satellite");
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  return hash / 4294967295;
}

function fallbackPosition(satellite, elapsed) {
  const seed = hashSeed(satellite.id || satellite.name);
  const orbitType = satellite.type || "";
  const radius = orbitType === "GEO" ? 11.3 : orbitType === "MEO" ? 8.0 : 2.55 + seed * 1.15;
  const speed = orbitType === "GEO"
    ? 0.000075
    : orbitType === "MEO"
      ? 0.00028
      : 0.0010 + seed * 0.00035;
  const phase = seed * Math.PI * 2;
  const inclination = ((satellite.inclination ?? (seed * 110)) * Math.PI) / 180;
  const angle = elapsed * speed + phase;
  return {
    x: Math.cos(angle) * radius,
    y: Math.sin(angle) * Math.sin(inclination) * radius * 0.32,
    z: Math.sin(angle) * Math.cos(inclination) * radius,
  };
}

export const SatelliteOrbit = memo(({ satellite, riskScore = 0, isSelected, onClick }) => {
  const satRef = useRef();
  const auraRef = useRef();
  const color = getRiskColor(riskScore);

  const tleEntry = useMemo(() => {
    if (satellite.tle1 && satellite.tle2) {
      return {
        id: satellite.id,
        name: satellite.name,
        shortName: satellite.shortName,
        orbitType: satellite.type,
        tle1: satellite.tle1,
        tle2: satellite.tle2,
      };
    }
    const keys = [satellite.id, satellite.name, satellite.shortName].map(keyFor);
    return ISRO_SATELLITES_TLE.find((s) => keys.includes(keyFor(s.id)) || keys.includes(keyFor(s.name))) || null;
  }, [satellite.id, satellite.name, satellite.shortName, satellite.tle1, satellite.tle2, satellite.type]);

  const satrec = useMemo(() => {
    if (!tleEntry || tleEntry.isSpecial) return null;
    return buildSatrec(tleEntry.tle1, tleEntry.tle2);
  }, [tleEntry]);

  const orbitPoints = useMemo(() => {
    if (!tleEntry || tleEntry.moonOrbit || tleEntry.l1Point || !satrec) return [];
    const period = ORBIT_PERIODS[satellite.id] || (satellite.type === "GEO" ? 1436 : 95);
    return buildOrbitRing(satrec, period).map((p) => new THREE.Vector3(p.x, p.y, p.z));
  }, [satrec, satellite.id, satellite.type, tleEntry]);

  const orbitGeo = useMemo(() => {
    if (!orbitPoints.length) return null;
    return new THREE.BufferGeometry().setFromPoints(orbitPoints);
  }, [orbitPoints]);

  useFrame(({ clock }) => {
    if (!satRef.current) return;

    let pos;
    if (tleEntry?.moonOrbit) {
      pos = getMoonPosition(new Date());
    } else if (tleEntry?.l1Point) {
      pos = getL1Position();
    } else if (satrec) {
      pos = getScenePosition(satrec, new Date());
    } else {
      pos = fallbackPosition(satellite, clock.getElapsedTime());
    }

    satRef.current.position.set(pos.x, pos.y, pos.z);

    if (auraRef.current) {
      const t = clock.getElapsedTime();
      const pulseSpeed = riskScore > 80 ? 4 : riskScore > 60 ? 2.5 : 1;
      const pulseMag = riskScore > 80 ? 0.25 : 0.08;
      auraRef.current.scale.setScalar(1 + Math.sin(t * pulseSpeed) * pulseMag);
      auraRef.current.material.opacity =
        riskScore > 80 ? 0.38 + Math.sin(t * pulseSpeed) * 0.15 :
        riskScore > 60 ? 0.22 : 0.12;
    }
  });

  const orbitAlpha = isSelected ? 0.9 : 0.24;
  const showLabel = isSelected || riskScore >= 50 || Number(satellite.tier || 1) === 1;

  return (
    <group>
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

      <group ref={satRef}>
        <Trail width={0.022} length={14} color={color} attenuation={(t) => t * t} decay={1}>
          <mesh onClick={onClick}>
            <sphereGeometry args={[0.045, 8, 8]} />
            <meshBasicMaterial color={isSelected ? "#ffffff" : color} toneMapped={false} />
          </mesh>
        </Trail>

        <mesh ref={auraRef}>
          <sphereGeometry args={[0.16, 10, 10]} />
          <meshBasicMaterial
            color={color}
            transparent
            opacity={0.15}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </mesh>

        {showLabel && (
          <Html distanceFactor={14} position={[0, 0.22, 0]} center occlude={false} style={{ pointerEvents: "none" }}>
            <div
              onClick={onClick}
              title={satellite.has_live_tle ? "Live TLE propagated position" : "Catalog fallback position"}
              style={{
                color,
                fontSize: 9,
                fontFamily: "Orbitron, sans-serif",
                fontWeight: 700,
                whiteSpace: "nowrap",
                textShadow: `0 0 8px ${color}`,
                padding: "2px 6px",
                background: "rgba(2,8,23,0.78)",
                borderRadius: 4,
                border: `1px solid ${color}44`,
                letterSpacing: "0.06em",
                pointerEvents: "auto",
                cursor: "pointer",
              }}
            >
              {tleEntry?.shortName || satellite.shortName || satellite.name || satellite.id.toUpperCase()}
            </div>
          </Html>
        )}
      </group>
    </group>
  );
});
