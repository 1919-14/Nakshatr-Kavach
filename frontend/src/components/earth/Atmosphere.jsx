import React, { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

export function Atmosphere({ kp = 0 }) {
  const meshRef = useRef();

  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    const t = clock.getElapsedTime();
    meshRef.current.material.opacity =
      0.18 + Math.sin(t * 0.8) * 0.03 + (kp / 9) * 0.1;
  });

  return (
    <mesh ref={meshRef}>
      <sphereGeometry args={[2.18, 32, 32]} />
      <meshBasicMaterial
        color={kp >= 7 ? "#ff4400" : "#00aaff"}
        transparent
        opacity={0.18}
        side={THREE.BackSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}
