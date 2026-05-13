import React, { useRef, Suspense, memo, useCallback } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Stars, Trail, Html } from "@react-three/drei";
import * as THREE from "three";
import { motion, AnimatePresence } from "framer-motion";

import { EarthMesh }      from "./EarthMesh";
import { Atmosphere }     from "./Atmosphere";
import { MagneticField }  from "./MagneticField";
import { StormCone }      from "./StormCone";
import { SatelliteOrbit } from "./SatelliteOrbit";

import { useStormStore }   from "../../store/useStormStore";
import { MOCK_SATELLITES } from "../../mock/mockData";
import { getRiskColor }    from "../../utils/riskColorMapper";
import { CircularProgress, AnimatedBar, RiskLevelBadge, OrbitTypeBadge, CountdownTimer } from "../ui/index";

// ── Sun ───────────────────────────────────────────────────────────────────────
function Sun({ kp = 0 }) {
  const coronaRef = useRef();
  useFrame(({ clock }) => {
    if (coronaRef.current) {
      const t = clock.getElapsedTime();
      coronaRef.current.material.opacity =
        0.1 + Math.sin(t * 1.4) * 0.04 + (kp / 9) * 0.08;
    }
  });
  return (
    <group position={[18, 3, -8]}>
      <mesh>
        <sphereGeometry args={[1.8, 16, 16]} />
        <meshBasicMaterial color="#FFF4C2" toneMapped={false} />
      </mesh>
      <mesh>
        <sphereGeometry args={[2.2, 16, 16]} />
        <meshBasicMaterial color="#FFD700" transparent opacity={0.18}
          side={THREE.BackSide} toneMapped={false} />
      </mesh>
      <mesh ref={coronaRef}>
        <sphereGeometry args={[2.9, 16, 16]} />
        <meshBasicMaterial color="#FF8C00" transparent opacity={0.08}
          side={THREE.BackSide} depthWrite={false}
          blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <pointLight color="#FFF5E0" intensity={3.5} distance={80} decay={1.5} />
      <directionalLight position={[18, 3, -8]} color="#FFF5E0" intensity={2.0} />
    </group>
  );
}

// ── Solar wind particles ──────────────────────────────────────────────────────
function SolarWindParticles({ active, kp }) {
  const pts   = useRef();
  const COUNT = 120;
  const positions = React.useMemo(() => {
    const arr = new Float32Array(COUNT * 3);
    for (let i = 0; i < COUNT; i++) {
      arr[i*3]   = 18 + (Math.random()-0.5)*5;
      arr[i*3+1] = (Math.random()-0.5)*7;
      arr[i*3+2] = -8 + (Math.random()-0.5)*5;
    }
    return arr;
  }, []);

  useFrame(() => {
    if (!pts.current || !active) return;
    const pos   = pts.current.geometry.attributes.position.array;
    const speed = 0.05 + (kp/9)*0.1;
    for (let i = 0; i < COUNT; i++) {
      const ix=i*3,iy=i*3+1,iz=i*3+2;
      pos[ix] += (0 - pos[ix]) * 0.012 * speed * 8;
      pos[iy] += (0 - pos[iy]) * 0.006 * speed * 4;
      pos[iz] += (0 - pos[iz]) * 0.012 * speed * 4;
      if (Math.abs(pos[ix]) < 3) {
        pos[ix] = 18+(Math.random()-0.5)*4;
        pos[iy] = (Math.random()-0.5)*6;
        pos[iz] = -8+(Math.random()-0.5)*4;
      }
    }
    pts.current.geometry.attributes.position.needsUpdate = true;
  });

  if (!active) return null;
  return (
    <points ref={pts}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial color="#FFD700" size={0.05} transparent
        opacity={0.5+(kp/9)*0.3} sizeAttenuation depthWrite={false}
        blending={THREE.AdditiveBlending} toneMapped={false} />
    </points>
  );
}

// ── Scene ────────────────────────────────────────────────────────────────────
function Scene({ kp, stormActive, satellites, selectedId, onSelectSat }) {
  return (
    <>
      <ambientLight color="#1A237E" intensity={0.4} />
      <Stars radius={180} depth={55} count={2500} factor={3} fade saturation={0.3} />
      <Sun kp={kp} />
      <SolarWindParticles active={stormActive} kp={kp} />
      <EarthMesh kp={kp} />
      <Atmosphere kp={kp} />
      <MagneticField kp={kp} />
      <StormCone kp={kp} />
      {satellites.map(sat => (
        <SatelliteOrbit
          key={sat.id}
          satellite={{ ...sat, initialAngle: sat.id.charCodeAt(0) * 0.9 }}
          riskScore={sat.composite_risk ?? 0}
          isSelected={selectedId === sat.id}
          onClick={() => onSelectSat(sat.id)}
        />
      ))}
      <OrbitControls enablePan={false} minDistance={4} maxDistance={22}
        autoRotate autoRotateSpeed={0.18} enableDamping dampingFactor={0.06} />
    </>
  );
}

// ── Satellite detail panel ────────────────────────────────────────────────────
function SatDetailPanel({ satellite, onClose }) {
  if (!satellite) return null;
  const color = getRiskColor(satellite.composite_risk);
  return (
    <motion.div
      initial={{ x:"100%" }} animate={{ x:0 }} exit={{ x:"100%" }}
      transition={{ type:"spring", stiffness:300, damping:34 }}
      style={{ position:"absolute",top:0,right:0,bottom:0,
        width:"min(340px,88vw)",background:"rgba(2,8,23,0.96)",
        backdropFilter:"blur(20px)",borderLeft:`1px solid ${color}33`,
        zIndex:40,overflowY:"auto",fontFamily:"Space Grotesk,sans-serif",
        color:"#E8F4FD" }}>
      <div style={{ padding:"16px 18px 12px",borderBottom:`1px solid ${color}22` }}>
        <div style={{ display:"flex",justifyContent:"space-between",alignItems:"flex-start" }}>
          <div>
            <div style={{ fontSize:9,color:"#546E7A",fontFamily:"Orbitron,sans-serif",
              letterSpacing:"0.12em",marginBottom:3 }}>ISRO SATELLITE</div>
            <div style={{ fontSize:18,fontWeight:700,color:"#fff",
              fontFamily:"Orbitron,sans-serif",letterSpacing:"0.04em" }}>{satellite.name}</div>
          </div>
          <button onClick={onClose} style={{ background:"none",border:"1px solid #1e3a5f",
            color:"#546E7A",cursor:"pointer",borderRadius:6,padding:"3px 9px",fontSize:13 }}>✕</button>
        </div>
        <div style={{ display:"flex",gap:8,marginTop:10,alignItems:"center",flexWrap:"wrap" }}>
          <RiskLevelBadge score={satellite.composite_risk} />
          <OrbitTypeBadge type={satellite.type} />
          {satellite.safe_mode_minutes && (
            <CountdownTimer totalSeconds={satellite.safe_mode_minutes*60} label="SAFE MODE IN" />
          )}
        </div>
      </div>
      <div style={{ padding:"14px 18px",display:"flex",gap:14,alignItems:"center" }}>
        <CircularProgress score={satellite.composite_risk} size={68} />
        <div style={{ flex:1 }}>
          <div style={{ fontSize:9,color:"#546E7A",letterSpacing:"0.1em",
            fontFamily:"Orbitron,sans-serif",marginBottom:7 }}>RISK BREAKDOWN</div>
          <AnimatedBar value={satellite.drag_risk}      color="#FF9800" label="🌪 DRAG"     />
          <AnimatedBar value={satellite.charging_risk}  color="#FDD835" label="⚡ CHARGE"   />
          <AnimatedBar value={satellite.radiation_risk} color="#00D4FF" label="☢ RADIATION" />
        </div>
      </div>
      {satellite.action && (
        <div style={{ margin:"0 18px 14px",padding:"10px 12px",
          background:`${color}11`,border:`1px solid ${color}33`,
          borderRadius:8,fontSize:12,lineHeight:1.65 }}>
          <div style={{ fontSize:9,color:"#546E7A",marginBottom:5,
            fontFamily:"Orbitron,sans-serif",letterSpacing:"0.1em" }}>ADVISORY</div>
          {satellite.action}
        </div>
      )}
      <div style={{ padding:"0 18px 14px" }}>
        <div style={{ fontSize:9,color:"#546E7A",letterSpacing:"0.1em",
          fontFamily:"Orbitron,sans-serif",marginBottom:8 }}>SPECIFICATIONS</div>
        {[
          ["Mission",    satellite.mission],
          ["Orbit",      satellite.type],
          ["Altitude",   `${satellite.altitude?.toLocaleString()} km`],
          ["Inclination",`${satellite.inclination}°`],
        ].map(([l,v])=>(
          <div key={l} style={{ display:"flex",justifyContent:"space-between",
            padding:"5px 0",borderBottom:"1px solid #0d1a30",fontSize:11 }}>
            <span style={{ color:"#546E7A" }}>{l}</span>
            <span style={{ color:"#90A4AE",fontSize:11,textAlign:"right",maxWidth:"60%" }}>{v}</span>
          </div>
        ))}
      </div>
      <div style={{ padding:"0 18px 22px" }}>
        <a href="/satellites" style={{ display:"block",textAlign:"center",
          padding:10,borderRadius:8,background:"#0d1e3a",border:"1px solid #1e3a5f",
          color:"#90A4AE",fontSize:11,textDecoration:"none",
          fontFamily:"Orbitron,sans-serif",letterSpacing:"0.08em" }}>
          VIEW SATELLITE DEEP DIVE →
        </a>
      </div>
    </motion.div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────
export const EarthGlobe = memo(({ height="100%", fullScreen=false }) => {
  const { solarWind, satellites, selectedSatellite, selectSatellite, clearSatellite } = useStormStore();
  const kp          = solarWind?.kp_current ?? 0;
  const stormActive = kp >= 5;
  const satList     = satellites?.length ? satellites : MOCK_SATELLITES;
  const selectedSat = satList.find(s=>s.id===selectedSatellite) ?? null;

  const handleSelect = useCallback((id) => {
    selectSatellite(id===selectedSatellite ? null : id);
  }, [selectedSatellite, selectSatellite]);

  return (
    <div style={{ position:"relative",width:"100%",height,
      background:"radial-gradient(ellipse at center,#0D1B3E 0%,#020817 100%)",
      borderRadius:fullScreen?0:12,overflow:"hidden",
      border:fullScreen?"none":"1px solid rgba(0,212,255,0.12)" }}>
      <Canvas
        camera={{ position:[0,2.5,8],fov:58,near:0.1,far:500 }}
        dpr={[1,1.5]}
        gl={{ antialias:true,powerPreference:"default",alpha:true }}
        style={{ background:"transparent" }}
      >
        <Suspense fallback={null}>
          <Scene
            kp={kp}
            stormActive={stormActive}
            satellites={satList}
            selectedId={selectedSatellite}
            onSelectSat={handleSelect}
          />
        </Suspense>
      </Canvas>

      {!selectedSatellite && (
        <div style={{ position:"absolute",bottom:14,left:"50%",
          transform:"translateX(-50%)",fontSize:10,color:"#546E7A",
          fontFamily:"JetBrains Mono,monospace",pointerEvents:"none",
          letterSpacing:"0.06em" }}>
          CLICK SATELLITE LABEL TO INSPECT
        </div>
      )}

      <AnimatePresence>
        {selectedSat && (
          <SatDetailPanel key={selectedSat.id} satellite={selectedSat} onClose={clearSatellite} />
        )}
      </AnimatePresence>
    </div>
  );
});

export default EarthGlobe;
