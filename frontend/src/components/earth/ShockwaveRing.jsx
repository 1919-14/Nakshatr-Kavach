import React, { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

// Expanding shockwave ring that emanates from Sun direction toward Earth
export function ShockwaveRing({ active }) {
  const ring1Ref = useRef();
  const ring2Ref = useRef();
  const ring3Ref = useRef();
  const progress = useRef(0);

  useFrame(({ clock }) => {
    if (!active) {
      progress.current = 0;
      [ring1Ref, ring2Ref, ring3Ref].forEach(r => {
        if (r.current) r.current.scale.setScalar(0.01);
      });
      return;
    }

    progress.current = (progress.current + 0.008) % 1;
    const t = clock.getElapsedTime();

    // Three rings at different phases
    [ring1Ref, ring2Ref, ring3Ref].forEach((ref, i) => {
      if (!ref.current) return;
      const phase   = (progress.current + i * 0.33) % 1;
      const scale   = 1 + phase * 9;   // expand from 1 → 10
      const opacity = 1 - phase;         // fade out as it expands

      ref.current.scale.setScalar(scale);
      if (ref.current.material) {
        ref.current.material.opacity = opacity * 0.35;
      }
    });
  });

  if (!active) return null;

  const ringStyle = {
    geometry: <ringGeometry args={[1, 1.08, 64]} />,
    material: (
      <meshBasicMaterial
        color="#F44336"
        transparent
        opacity={0.3}
        side={THREE.DoubleSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    ),
  };

  return (
    <group position={[4, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
      <mesh ref={ring1Ref}>
        <ringGeometry args={[1, 1.12, 64]} />
        <meshBasicMaterial color="#F44336" transparent opacity={0.3}
          side={THREE.DoubleSide} depthWrite={false}
          blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <mesh ref={ring2Ref}>
        <ringGeometry args={[1, 1.12, 64]} />
        <meshBasicMaterial color="#FF9800" transparent opacity={0.25}
          side={THREE.DoubleSide} depthWrite={false}
          blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <mesh ref={ring3Ref}>
        <ringGeometry args={[1, 1.12, 64]} />
        <meshBasicMaterial color="#9C27B0" transparent opacity={0.2}
          side={THREE.DoubleSide} depthWrite={false}
          blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
    </group>
  );
}
