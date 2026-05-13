import React, { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const coneVert = `
  varying vec2 vUv;
  varying vec3 vPosition;
  void main() {
    vUv = uv;
    vPosition = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const coneFrag = `
  uniform float uTime;
  uniform float uOpacity;
  uniform vec3  uColor;
  varying vec2  vUv;
  varying vec3  vPosition;

  void main() {
    // Ripple from tip to base
    float dist   = length(vUv - vec2(0.5, 0.0));
    float ripple = sin(dist * 18.0 - uTime * 3.5) * 0.5 + 0.5;

    // Fade toward edges
    float edge = 1.0 - smoothstep(0.0, 0.5, dist);
    float alpha = edge * ripple * uOpacity;

    gl_FragColor = vec4(uColor, alpha);
  }
`;

export function StormCone({ kp = 0 }) {
  const meshRef = useRef();
  const active  = kp >= 7;

  const uniforms = useMemo(() => ({
    uTime:    { value: 0 },
    uOpacity: { value: 0 },
    uColor:   { value: new THREE.Color(kp >= 8 ? "#9C27B0" : "#F44336") },
  }), []);

  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    const t       = clock.getElapsedTime();
    const target  = active ? (0.15 + Math.sin(t * 1.2) * 0.08) : 0;
    uniforms.uTime.value    = t;
    uniforms.uOpacity.value = THREE.MathUtils.lerp(uniforms.uOpacity.value, target, 0.04);

    // Update color based on kp
    const c = kp >= 8 ? new THREE.Color("#9C27B0") : new THREE.Color("#F44336");
    uniforms.uColor.value.lerp(c, 0.05);
  });

  // Cone points from Sun direction (positive X) toward Earth
  // ConeGeometry: tip at top, open at bottom → rotate so tip faces +X
  return (
    <mesh
      ref={meshRef}
      position={[4.5, 0, 0]}
      rotation={[0, 0, -Math.PI / 2]}
    >
      <coneGeometry args={[3.2, 7, 32, 8, true]} />
      <shaderMaterial
        vertexShader={coneVert}
        fragmentShader={coneFrag}
        uniforms={uniforms}
        transparent
        side={THREE.DoubleSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}
