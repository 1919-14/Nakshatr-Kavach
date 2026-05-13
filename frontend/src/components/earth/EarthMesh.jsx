import React, { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

function createEarthTexture() {
  const W = 2048, H = 1024;
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");

  // Ocean
  const og = ctx.createLinearGradient(0,0,0,H);
  og.addColorStop(0,"#081828"); og.addColorStop(0.5,"#0d2856"); og.addColorStop(1,"#081828");
  ctx.fillStyle = og; ctx.fillRect(0,0,W,H);

  function ll(lat,lon){ return [(lon+180)/360*W,(90-lat)/180*H]; }

  function land(coords,color){
    ctx.beginPath(); ctx.fillStyle=color;
    const [sx,sy]=ll(coords[0][0],coords[0][1]); ctx.moveTo(sx,sy);
    for(let i=1;i<coords.length;i++){const[x,y]=ll(coords[i][0],coords[i][1]);ctx.lineTo(x,y);}
    ctx.closePath(); ctx.fill();
  }

  // INDIA — detailed + highlighted
  land([[8,77],[9,76],[11,75.5],[13,74.5],[15,73.8],[17,73],[19,72.6],[21,72.3],[23,68.2],[24,67.5],[26,66.5],[28,64.8],[30,64.5],[32,65],[34,67],[35,70],[35.5,72],[34.5,74],[33,75.5],[31.5,77],[30,80],[29,82],[28,84],[27,86],[26,88],[25,89.5],[24,91],[22.5,92.5],[21,92],[20,92.5],[18.5,92.5],[17,91.5],[16,80.5],[14,80.1],[12,79.8],[10.5,78.5],[9,77.2],[8,77]],"#2d7a2d");

  // ISRO city dots
  const isroCities=[[12.97,77.59,"#ff9900",5],[13.08,80.27,"#ff6600",4],[8.52,76.94,"#ff6600",3],[23.02,72.57,"#ff6600",3],[22.57,88.36,"#ff5500",3],[28.61,77.21,"#ff4400",4],[19.07,72.87,"#ff5500",3],[17.38,78.49,"#ff6600",3]];
  isroCities.forEach(([la,lo,c,r])=>{
    const[cx,cy]=ll(la,lo);
    ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fillStyle=c;ctx.fill();
    const g=ctx.createRadialGradient(cx,cy,0,cx,cy,r*5);
    g.addColorStop(0,c+"99");g.addColorStop(1,"transparent");
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,r*5,0,Math.PI*2);ctx.fill();
  });

  // EUROPE
  land([[71,28],[70,20],[68,13],[65,13],[63,8],[61,4],[59,5],[57,8],[55,9],[53,7],[51,2],[49,0],[47,6],[45,7],[43,10],[41,13],[39,16],[37,14],[36,14],[38,18],[40,20],[42,22],[44,26],[46,28],[48,26],[50,22],[52,18],[54,10],[56,14],[58,10],[60,8],[62,5],[64,10],[66,16],[68,14],[70,18],[71,28]],"#1e5a1e");

  // AFRICA
  land([[37,10],[35,6],[33,2],[31,-2],[28,-2],[26,2],[24,6],[22,10],[20,14],[18,18],[16,22],[14,26],[12,30],[10,34],[8,36],[6,35],[4,32],[2,28],[0,24],[-2,20],[-4,16],[-6,12],[-8,12],[-10,16],[-14,20],[-18,24],[-22,28],[-26,32],[-30,32],[-34,26],[-34,22],[-32,20],[-28,16],[-24,12],[-20,8],[-16,4],[-12,0],[-8,-4],[-4,-8],[0,-12],[4,-16],[8,-18],[12,-14],[16,-10],[20,-6],[24,-2],[28,2],[32,6],[36,10],[37,10]],"#1e5a1e");

  // ASIA
  land([[70,30],[68,40],[66,50],[64,60],[62,70],[60,80],[58,90],[56,100],[54,110],[52,120],[50,130],[48,140],[44,132],[40,124],[38,120],[34,116],[30,120],[26,116],[22,112],[18,108],[14,102],[10,98],[6,100],[2,104],[0,106],[4,106],[8,102],[12,98],[16,94],[20,90],[24,86],[28,82],[32,78],[36,74],[40,70],[44,66],[48,62],[52,58],[56,54],[60,50],[64,46],[68,42],[70,30]],"#1e5a1e");

  // N.AMERICA
  land([[71,-85],[70,-95],[68,-105],[66,-115],[64,-125],[62,-135],[60,-145],[57,-155],[54,-168],[50,-125],[48,-122],[44,-114],[40,-106],[36,-98],[32,-90],[28,-82],[25,-80],[24,-82],[26,-96],[30,-92],[34,-90],[38,-98],[42,-106],[46,-114],[50,-122],[54,-130],[58,-138],[62,-148],[66,-164],[69,-160],[72,-110],[71,-85]],"#1e5a1e");

  // S.AMERICA
  land([[12,-72],[8,-64],[4,-56],[0,-50],[-4,-46],[-8,-42],[-12,-38],[-16,-40],[-20,-44],[-24,-48],[-28,-52],[-32,-56],[-36,-60],[-40,-64],[-44,-68],[-48,-72],[-52,-70],[-48,-66],[-44,-62],[-40,-58],[-36,-54],[-32,-50],[-28,-46],[-24,-42],[-20,-38],[-16,-36],[-14,-38],[-10,-42],[-6,-46],[-2,-50],[2,-52],[6,-60],[10,-64],[12,-66],[12,-72]],"#1e5a1e");

  // AUSTRALIA
  land([[-14,130],[-14,136],[-14,140],[-16,142],[-18,146],[-22,150],[-26,154],[-30,152],[-34,151],[-38,148],[-38,140],[-35,136],[-34,130],[-32,127],[-28,122],[-24,115],[-22,114],[-20,118],[-16,126],[-14,130]],"#1e5a1e");

  // Ice caps
  ctx.fillStyle="#ddeeff"; ctx.fillRect(0,0,W,H*0.055);
  ctx.fillStyle="#eef4ff"; ctx.fillRect(0,H*0.94,W,H*0.06);

  // India orange glow (ISRO region)
  ctx.globalAlpha=0.2;
  const ig=ctx.createRadialGradient((80+180)/360*W,(90-22)/180*H,0,(80+180)/360*W,(90-22)/180*H,W*0.07);
  ig.addColorStop(0,"#ff8800");ig.addColorStop(1,"transparent");
  ctx.fillStyle=ig;ctx.fillRect(0,0,W,H);
  ctx.globalAlpha=1;

  return new THREE.CanvasTexture(canvas);
}

export function EarthMesh({ kp=0 }) {
  const earthRef  = useRef();
  const cloudsRef = useRef();
  const texture   = useMemo(()=>createEarthTexture(),[]);

  useFrame(()=>{
    if(earthRef.current)  earthRef.current.rotation.y  += 0.0005;
    if(cloudsRef.current) cloudsRef.current.rotation.y += 0.00035;
  });

  return (
    <group>
      <mesh ref={earthRef}>
        <sphereGeometry args={[2,64,64]} />
        <meshPhongMaterial map={texture} specular={new THREE.Color("#224488")}
          shininess={18} emissive={new THREE.Color("#020810")} emissiveIntensity={0.25} />
      </mesh>
      <mesh ref={cloudsRef}>
        <sphereGeometry args={[2.03,32,32]} />
        <meshPhongMaterial color="#ffffff" transparent opacity={0.13} depthWrite={false} />
      </mesh>
    </group>
  );
}
