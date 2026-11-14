import { useEffect, useMemo, useRef, type RefObject } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import type { MessageKind, NpcActivity, NpcRole } from "@vice/contracts";
import { fx, glowTexture, light, plain } from "./materials";

export const ROLE_COLOR: Record<NpcRole, string> = { WITNESS: "#ffd166", INFORMANT: "#c77dff", TRADER: "#3dffc8", POLICE: "#4d8dff" };
export const KIND_COLOR: Record<MessageKind, string> = { REPORT: "#4d8dff", GOSSIP: "#ff4fa3", RELAY: "#ffc46b", BULLETIN: "#ff5555" };

/** key -> 3D object a DOM label follows (+ height above it). Labels live in one DOM overlay, not in per-label React roots. */
export type Anchors = Map<string, { obj: THREE.Object3D; h: number }>;
function useAnchor(anchors: Anchors, key: string, ref: RefObject<THREE.Object3D | null>, h: number) {
  useEffect(() => {
    if (ref.current) anchors.set(key, { obj: ref.current, h });
    return () => { anchors.delete(key); };
  }, [anchors, key, ref, h]);
}

/** Smoothly moves a group toward (x, z), turns it to face its motion and reports horizontal speed for the walk cycle. */
export function useGlide(x: number, z: number) {
  const ref = useRef<THREE.Group>(null);
  const speed = useRef(0);
  const init = useRef(false);
  useFrame((_, dt) => {
    const g = ref.current;
    if (!g) return;
    if (!init.current) { g.position.set(x, 0, z); init.current = true; return; }
    const k = Math.min(1, dt * 10);
    const dx = (x - g.position.x) * k, dz = (z - g.position.z) * k;
    g.position.x += dx; g.position.z += dz;
    const v = Math.hypot(dx, dz) / Math.max(dt, 0.001);
    speed.current += (v - speed.current) * 0.2;
    if (v > 0.4) turnToward(g, Math.atan2(dx, dz), dt * 12);
  });
  return { ref, speed };
}
export function turnToward(g: THREE.Object3D, target: number, k: number) {
  let d = target - g.rotation.y;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  g.rotation.y += d * Math.min(1, k);
}

// ---------- articulated figure ----------
const G = {
  thigh: new THREE.CapsuleGeometry(0.095, 0.3, 3, 8), shin: new THREE.CapsuleGeometry(0.08, 0.3, 3, 8), foot: new THREE.BoxGeometry(0.15, 0.09, 0.3),
  hips: new THREE.BoxGeometry(0.36, 0.2, 0.22), chest: new THREE.BoxGeometry(0.44, 0.56, 0.25), shoulders: new THREE.BoxGeometry(0.52, 0.12, 0.26),
  upperArm: new THREE.CapsuleGeometry(0.07, 0.22, 3, 8), foreArm: new THREE.CapsuleGeometry(0.058, 0.2, 3, 8), hand: new THREE.SphereGeometry(0.065, 8, 6),
  neck: new THREE.CylinderGeometry(0.06, 0.07, 0.1, 8), head: new THREE.SphereGeometry(0.135, 14, 12),
  hair: new THREE.SphereGeometry(0.142, 12, 10, 0, Math.PI * 2, 0, Math.PI * 0.55),
  cap: new THREE.CylinderGeometry(0.15, 0.155, 0.09, 12), brim: new THREE.BoxGeometry(0.2, 0.02, 0.14),
};
export interface Outfit { top: string; legs: string; skin: string; hair: string; hat?: string }
const SKINS = ["#f0c8a4", "#d9a47a", "#b07a52", "#8a5a3a", "#6b4428"], HAIRS = ["#1b1512", "#3a2516", "#6b4a2a", "#111318", "#8a6a3a"];
export function outfitFor(id: string, top: string, legs = "#23242c", hat?: string): Outfit {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return { top, legs, skin: SKINS[h % SKINS.length]!, hair: HAIRS[(h >>> 3) % HAIRS.length]!, hat };
}

/** Hips, torso, head, two-segment arms and legs, driven by a speed-dependent walk/run cycle. Shares geometry and materials. */
export function Figure({ outfit, speed, dim = false, scale = 1.12 }: { outfit: Outfit; speed: RefObject<number>; dim?: boolean; scale?: number }) {
  const root = useRef<THREE.Group>(null), torso = useRef<THREE.Group>(null);
  const hipL = useRef<THREE.Group>(null), hipR = useRef<THREE.Group>(null), kneeL = useRef<THREE.Group>(null), kneeR = useRef<THREE.Group>(null);
  const shL = useRef<THREE.Group>(null), shR = useRef<THREE.Group>(null), elL = useRef<THREE.Group>(null), elR = useRef<THREE.Group>(null);
  const phase = useRef(Math.random() * 6);
  useFrame(({ clock }, dt) => {
    const v = speed.current ?? 0, s = Math.min(1, v / 3.5), run = Math.min(1, Math.max(0, (v - 5) / 4));
    phase.current += dt * (4.5 + v * 1.25);
    const p = phase.current, a = Math.sin(p), amp = (0.55 + run * 0.35) * s;
    if (hipL.current) hipL.current.rotation.x = -a * amp;
    if (hipR.current) hipR.current.rotation.x = a * amp;
    if (kneeL.current) kneeL.current.rotation.x = Math.max(0, Math.cos(p)) * (0.9 + run * 0.5) * s + 0.04;
    if (kneeR.current) kneeR.current.rotation.x = Math.max(0, -Math.cos(p)) * (0.9 + run * 0.5) * s + 0.04;
    if (shL.current) shL.current.rotation.x = a * amp * 0.9;
    if (shR.current) shR.current.rotation.x = -a * amp * 0.9;
    const bend = -(0.15 + s * (0.5 + run * 0.6));
    if (elL.current) elL.current.rotation.x = bend;
    if (elR.current) elR.current.rotation.x = bend;
    if (root.current) root.current.position.y = Math.abs(Math.cos(p)) * 0.05 * s;
    if (torso.current) {
      torso.current.rotation.x = 0.04 + s * 0.08 + run * 0.14;
      torso.current.rotation.y = a * 0.12 * s;
      torso.current.position.y = 1.0 + Math.sin(clock.elapsedTime * 1.6) * 0.008 * (1 - s); // idle breathing
    }
  });
  const top = plain(dim ? "#55525e" : outfit.top), legs = plain(dim ? "#3a3840" : outfit.legs), skin = plain(outfit.skin, 0.6);
  const shoe = plain("#141418", 0.5), hair = plain(outfit.hair, 0.9), hat = outfit.hat ? plain(outfit.hat) : null;
  const leg = (hip: RefObject<THREE.Group | null>, knee: RefObject<THREE.Group | null>, x: number) => (
    <group ref={hip} position={[x, 0.98, 0]}>
      <mesh dispose={null} geometry={G.thigh} material={legs} position={[0, -0.23, 0]} castShadow receiveShadow />
      <group ref={knee} position={[0, -0.47, 0]}>
        <mesh dispose={null} geometry={G.shin} material={legs} position={[0, -0.22, 0]} castShadow receiveShadow />
        <mesh dispose={null} geometry={G.foot} material={shoe} position={[0, -0.47, 0.06]} castShadow receiveShadow />
      </group>
    </group>
  );
  const arm = (sh: RefObject<THREE.Group | null>, el: RefObject<THREE.Group | null>, x: number) => (
    <group ref={sh} position={[x, 0.5, 0]} rotation={[0, 0, x > 0 ? 0.08 : -0.08]}>
      <mesh dispose={null} geometry={G.upperArm} material={top} position={[0, -0.16, 0]} castShadow receiveShadow />
      <group ref={el} position={[0, -0.33, 0]}>
        <mesh dispose={null} geometry={G.foreArm} material={skin} position={[0, -0.14, 0]} castShadow receiveShadow />
        <mesh dispose={null} geometry={G.hand} material={skin} position={[0, -0.31, 0]} receiveShadow />
      </group>
    </group>
  );
  return (
    <group ref={root} scale={scale}>
      {leg(hipL, kneeL, -0.115)}
      {leg(hipR, kneeR, 0.115)}
      <mesh dispose={null} geometry={G.hips} material={legs} position={[0, 0.97, 0]} castShadow receiveShadow />
      <group ref={torso} position={[0, 1.0, 0]}>
        <mesh dispose={null} geometry={G.chest} material={top} position={[0, 0.3, 0]} castShadow receiveShadow />
        <mesh dispose={null} geometry={G.shoulders} material={top} position={[0, 0.54, 0]} castShadow receiveShadow />
        {arm(shL, elL, -0.3)}
        {arm(shR, elR, 0.3)}
        <mesh dispose={null} geometry={G.neck} material={skin} position={[0, 0.66, 0]} receiveShadow />
        <mesh dispose={null} geometry={G.head} material={skin} position={[0, 0.82, 0.01]} scale={[0.9, 1.08, 1]} castShadow receiveShadow />
        <mesh dispose={null} geometry={G.hair} material={hair} position={[0, 0.87, -0.02]} scale={[0.95, 0.9, 1.02]} receiveShadow />
        {hat && <>
          <mesh dispose={null} geometry={G.cap} material={hat} position={[0, 0.95, 0]} receiveShadow />
          <mesh dispose={null} geometry={G.brim} material={hat} position={[0, 0.915, 0.13]} receiveShadow />
        </>}
      </group>
    </group>
  );
}

// ---------- shared effect geometry ----------
const FLAT = new THREE.Euler(-Math.PI / 2, 0, 0);
const E = {
  disc: new THREE.CircleGeometry(1, 28), ringHex: new THREE.RingGeometry(1.05, 1.3, 6), ringFoot: new THREE.RingGeometry(0.7, 0.85, 24),
  cone: new THREE.ConeGeometry(0.28, 0.55, 4), octa: new THREE.OctahedronGeometry(0.28), bar: new THREE.BoxGeometry(0.3, 0.16, 0.26),
  beam: new THREE.CylinderGeometry(0.12, 0.55, 14, 14, 1, true), ringMission: new THREE.RingGeometry(2.1, 2.5, 8), crate: new THREE.BoxGeometry(0.8, 0.6, 0.55),
  dot: new THREE.SphereGeometry(0.34, 10, 8), ringBurst: new THREE.RingGeometry(0.8, 1, 36), column: new THREE.CylinderGeometry(1.2, 1.2, 24, 18, 1, true),
};
const M = {
  white: light("#ffffff", 1.8), red: light("#ff2b2b", 3.2), blue: light("#2b6bff", 3.6),
  missionBeam: light("#ffe14d", 1.0, { opacity: 0.11, additive: true }), missionRing: light("#ffe14d", 1.7),
  crate: fx(new THREE.MeshStandardMaterial({ color: "#ffe14d", emissive: "#ffb400", emissiveIntensity: 2.2, roughness: 0.5 })),
  think: (() => { const m = light("#ffffff", 2); m.wireframe = true; return m; })(),
  foot: Object.fromEntries(Object.entries(ROLE_COLOR).map(([k, c]) => [k, light(c, 1.3, { opacity: 0.8 })])) as Record<NpcRole, THREE.MeshBasicMaterial>,
  select: Object.fromEntries(Object.entries({ ...ROLE_COLOR, YOU: "#ffffff" }).map(([k, c]) => [k, light(c, 1.6, { opacity: 0.95 })])) as Record<string, THREE.MeshBasicMaterial>,
};

function SelectRing({ tone }: { tone: string }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    ref.current.rotation.z = clock.elapsedTime * 1.5;
    const s = 1 + Math.sin(clock.elapsedTime * 5) * 0.06;
    ref.current.scale.set(s, s, 1);
  });
  return <mesh dispose={null} ref={ref} geometry={E.ringHex} material={M.select[tone] ?? M.select.YOU!} rotation={FLAT} position={[0, 0.07, 0]} />;
}

export interface HumanProps {
  id: string; x: number; z: number; role: "OUTLAW" | "HUNTER"; heat: number; wanted: boolean; captured: boolean;
  isYou: boolean; selected: boolean; anchors: Anchors; onSelect: (id: string) => void;
}
export function Human(p: HumanProps) {
  const { ref, speed } = useGlide(p.x, p.z);
  useAnchor(p.anchors, p.id, ref, 2.6);
  const glow = useRef<THREE.Mesh>(null);
  const outfit = useMemo(() => p.role === "OUTLAW" ? outfitFor(p.id, "#c2255c", "#1c1d24") : outfitFor(p.id, "#1f6f8a", "#2a2f3a", "#16323d"), [p.id, p.role]);
  const glowMat = useMemo(() => light("#ff9a3c", 2, { opacity: 0.3, map: glowTexture(), additive: true }), []);
  useEffect(() => () => glowMat.dispose(), [glowMat]);
  useFrame(({ clock }) => {
    if (!glow.current) return;
    const h = Math.min(1, p.heat / 100), pulse = p.wanted ? 0.7 + Math.sin(clock.elapsedTime * 8) * 0.3 : 1;
    glow.current.scale.setScalar(1.3 + h * 2.2);
    glowMat.color.set(p.wanted ? "#ff2a2a" : "#ff9a3c").multiplyScalar(2);
    glowMat.opacity = (0.18 + h * 0.42) * pulse;
  });
  return (
    <group ref={ref} onClick={(e) => { e.stopPropagation(); p.onSelect(p.id); }}>
      <Figure outfit={outfit} speed={speed} dim={p.captured} />
      {!p.captured && p.heat > 0 && <mesh dispose={null} ref={glow} geometry={E.disc} material={glowMat} rotation={FLAT} position={[0, 0.06, 0]} />}
      {p.isYou && !p.captured && <mesh dispose={null} geometry={E.cone} material={M.white} position={[0, 3.25, 0]} rotation={[Math.PI, 0, 0]} />}
      {p.selected && <SelectRing tone="YOU" />}
    </group>
  );
}

export interface NpcProps {
  id: string; x: number; z: number; role: NpcRole; activity: NpcActivity; thinking: boolean; selected: boolean;
  anchors: Anchors; onSelect: (id: string) => void;
}
export function Npc(p: NpcProps) {
  const { ref, speed } = useGlide(p.x, p.z);
  useAnchor(p.anchors, p.id, ref, 2.65);
  const think = useRef<THREE.Mesh>(null), red = useRef<THREE.Mesh>(null), blue = useRef<THREE.Mesh>(null), pool = useRef<THREE.Mesh>(null);
  const pursuing = p.role === "POLICE" && p.activity === "PURSUE";
  const outfit = useMemo(() => p.role === "POLICE" ? outfitFor(p.id, "#1c2c55", "#131a30", "#0f1730")
    : p.role === "TRADER" ? outfitFor(p.id, "#e9e6df", "#2b2d36")
    : p.role === "INFORMANT" ? outfitFor(p.id, "#4a2f66", "#1b1b22", "#2a1b3a")
    : outfitFor(p.id, "#c98f2e", "#3b4a5c"), [p.id, p.role]);
  const poolMat = useMemo(() => light("#ff2b2b", 2, { opacity: 0.4, map: glowTexture(), additive: true }), []);
  useEffect(() => () => poolMat.dispose(), [poolMat]);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (think.current) { think.current.rotation.y = t * 4; think.current.position.y = 3.2 + Math.sin(t * 5) * 0.12; }
    const on = Math.floor(t * 6) % 2 === 0;
    if (red.current) red.current.visible = on;
    if (blue.current) blue.current.visible = !on;
    if (pool.current) poolMat.color.set(on ? "#ff2b2b" : "#2b6bff").multiplyScalar(2);
  });
  return (
    <group ref={ref} onClick={(e) => { e.stopPropagation(); p.onSelect(p.id); }}>
      <Figure outfit={outfit} speed={speed} />
      {/* No dynamic lights here on purpose: adding/removing a light recompiles every lit shader mid-game. */}
      {pursuing && <>
        <mesh dispose={null} ref={red} geometry={E.bar} material={M.red} position={[-0.2, 2.42, 0]} />
        <mesh dispose={null} ref={blue} geometry={E.bar} material={M.blue} position={[0.2, 2.42, 0]} />
        <mesh dispose={null} ref={pool} geometry={E.disc} material={poolMat} rotation={FLAT} position={[0, 0.08, 0]} scale={3.6} />
      </>}
      {p.thinking && <mesh dispose={null} ref={think} geometry={E.octa} material={M.think} position={[0, 3.2, 0]} />}
      {p.selected && <SelectRing tone={p.role} />}
      <mesh dispose={null} geometry={E.ringFoot} material={M.foot[p.role]} rotation={FLAT} position={[0, 0.05, 0]} />
    </group>
  );
}

export function MissionBeacon({ id, x, z, anchors }: { id: string; x: number; z: number; anchors: Anchors }) {
  const root = useRef<THREE.Group>(null), ring = useRef<THREE.Mesh>(null), crate = useRef<THREE.Mesh>(null);
  useAnchor(anchors, `m:${id}`, root, 2.4);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (ring.current) { ring.current.rotation.z = t * 1.2; ring.current.scale.setScalar(1 + Math.sin(t * 3) * 0.08); }
    if (crate.current) { crate.current.rotation.y = t * 0.9; crate.current.position.y = 0.75 + Math.sin(t * 2.2) * 0.12; }
  });
  return (
    <group ref={root} position={[x, 0, z]}>
      <mesh dispose={null} geometry={E.beam} material={M.missionBeam} position={[0, 7, 0]} />
      <mesh dispose={null} ref={ring} geometry={E.ringMission} material={M.missionRing} rotation={FLAT} position={[0, 0.06, 0]} />
      <mesh dispose={null} ref={crate} geometry={E.crate} material={M.crate} position={[0, 0.75, 0]} castShadow />
    </group>
  );
}

/** A glowing arc with a travelling pulse: one recorded message between two agents. */
export function MessageArc({ from, to, kind, startedAtMs, durationMs }: { from: { x: number; z: number }; to: { x: number; z: number }; kind: MessageKind; startedAtMs: number; durationMs: number }) {
  const dot = useRef<THREE.Mesh>(null);
  const { curve, tube, tubeMat, dotMat } = useMemo(() => {
    const a = new THREE.Vector3(from.x, 2.3, from.z), b = new THREE.Vector3(to.x, 2.3, to.z);
    const mid = a.clone().add(b).multiplyScalar(0.5);
    mid.y = 5 + a.distanceTo(b) * 0.28;
    const c = new THREE.QuadraticBezierCurve3(a, mid, b);
    return { curve: c, tube: new THREE.TubeGeometry(c, 28, 0.07, 5), tubeMat: light(KIND_COLOR[kind], 3, { opacity: 0.9 }), dotMat: light(KIND_COLOR[kind], 5) };
    // endpoints are captured at send time on purpose: the arc shows where the message travelled
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startedAtMs]);
  useEffect(() => () => { tube.dispose(); tubeMat.dispose(); dotMat.dispose(); }, [tube, tubeMat, dotMat]);
  useFrame(() => {
    const t = (Date.now() - startedAtMs) / durationMs;
    tubeMat.opacity = t < 1 ? 0.9 : Math.max(0, 0.9 - (t - 1) * 3);
    if (dot.current) { dot.current.visible = t <= 1; dot.current.position.copy(curve.getPoint(Math.min(1, Math.max(0, t)))); }
  });
  return (
    <group>
      <mesh dispose={null} geometry={tube} material={tubeMat} />
      <mesh dispose={null} ref={dot} geometry={E.dot} material={dotMat} />
    </group>
  );
}

export function CaptureBurst({ id, x, z, startedAtMs, anchors }: { id: string; x: number; z: number; startedAtMs: number; anchors: Anchors }) {
  const root = useRef<THREE.Group>(null), ring = useRef<THREE.Mesh>(null), beam = useRef<THREE.Mesh>(null);
  useAnchor(anchors, `b:${id}`, root, 4.2);
  const mats = useMemo(() => ({ ring: light("#ff3b3b", 4), beam: light("#ff3b3b", 2.5, { additive: true }) }), []);
  useEffect(() => () => { mats.ring.dispose(); mats.beam.dispose(); }, [mats]);
  useFrame(() => {
    const t = Math.min(1, (Date.now() - startedAtMs) / 1800);
    if (ring.current) { ring.current.scale.setScalar(1 + t * 14); mats.ring.opacity = 1 - t; }
    if (beam.current) { beam.current.scale.set(1 - t * 0.7, 1, 1 - t * 0.7); mats.beam.opacity = (1 - t) * 0.7; }
  });
  return (
    <group ref={root} position={[x, 0, z]}>
      <mesh dispose={null} ref={ring} geometry={E.ringBurst} material={mats.ring} rotation={FLAT} position={[0, 0.1, 0]} />
      <mesh dispose={null} ref={beam} geometry={E.column} material={mats.beam} position={[0, 12, 0]} />
    </group>
  );
}
