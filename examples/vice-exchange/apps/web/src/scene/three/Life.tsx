// Ambient city life: traffic that yields and steers around people, pedestrians on the sidewalks, sun glitter on the sea.
// Purely cosmetic and client-side. It never touches game state; gameplay agents are the ones with labels and rings.
import { useEffect, useMemo, useRef, type RefObject } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import type { MapView } from "@vice/contracts";
import { Figure, outfitFor, turnToward } from "./Entities";
import { glowTexture, hash, light, plain } from "./materials";
import { CAR_COLORS, SUN_DIR, roadLines } from "./World";

type Blockers = RefObject<Map<string, { x: number; z: number }>>;

/** Rounded-rectangle circuit. `cw` = clockwise seen from above with north up. */
interface Loop { x0: number; z0: number; x1: number; z1: number; r: number; cw: boolean }
function loopLength(L: Loop): number { return 2 * (L.x1 - L.x0 + L.z1 - L.z0) - 8 * L.r + 2 * Math.PI * L.r; }
function loopAt(L: Loop, dist: number, out: { x: number; z: number; tx: number; tz: number }) {
  const a = L.x1 - L.x0 - 2 * L.r, b = L.z1 - L.z0 - 2 * L.r, q = (Math.PI * L.r) / 2, P = 2 * (a + b) + 4 * q;
  let s = ((L.cw ? dist : -dist) % P + P) % P;
  const arc = (cx: number, cz: number, a0: number, t: number) => { const ang = a0 + (t / q) * (Math.PI / 2); out.x = cx + L.r * Math.cos(ang); out.z = cz + L.r * Math.sin(ang); out.tx = -Math.sin(ang); out.tz = Math.cos(ang); };
  if (s < a) { out.x = L.x0 + L.r + s; out.z = L.z0; out.tx = 1; out.tz = 0; }
  else if ((s -= a) < q) arc(L.x1 - L.r, L.z0 + L.r, -Math.PI / 2, s);
  else if ((s -= q) < b) { out.x = L.x1; out.z = L.z0 + L.r + s; out.tx = 0; out.tz = 1; }
  else if ((s -= b) < q) arc(L.x1 - L.r, L.z1 - L.r, 0, s);
  else if ((s -= q) < a) { out.x = L.x1 - L.r - s; out.z = L.z1; out.tx = -1; out.tz = 0; }
  else if ((s -= a) < q) arc(L.x0 + L.r, L.z1 - L.r, Math.PI / 2, s);
  else if ((s -= q) < b) { out.x = L.x0; out.z = L.z1 - L.r - s; out.tx = 0; out.tz = -1; }
  else arc(L.x0 + L.r, L.z0 + L.r, Math.PI, s - b);
  if (!L.cw) { out.tx = -out.tx; out.tz = -out.tz; }
}

// ---------- traffic ----------
const CG = {
  body: new RoundedBoxGeometry(1.82, 0.62, 4.3, 3, 0.22), cabin: new RoundedBoxGeometry(1.62, 0.52, 2.3, 3, 0.2),
  glassSide: new RoundedBoxGeometry(1.66, 0.34, 2.0, 2, 0.12), glassEnds: new RoundedBoxGeometry(1.3, 0.3, 2.34, 2, 0.12),
  wheel: new THREE.CylinderGeometry(0.34, 0.34, 0.24, 14).rotateZ(Math.PI / 2), lamp: new THREE.BoxGeometry(0.36, 0.14, 0.06),
  beam: new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
};
const LANE = 1.9, CAR_HALF = 0.91;
interface CarState { x: number; z: number; tx: number; tz: number; v: number }

function buildCar(color: string): THREE.Group {
  const g = new THREE.Group();
  const add = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, shadow = false) => {
    const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.castShadow = shadow; m.receiveShadow = true; g.add(m); return m;
  };
  const paint = plain(color, 0.2, 0.7, true), glass = plain("#10151d", 0.04, 0.95, true), tyre = plain("#0d0d10", 0.9, 0, true);
  add(CG.body, paint, 0, 0.62, 0, true); add(CG.cabin, paint, 0, 1.08, -0.15, true);
  add(CG.glassSide, glass, 0, 1.07, -0.15); add(CG.glassEnds, glass, 0, 1.07, -0.15);
  for (const [wx, wz] of [[0.9, 1.35], [-0.9, 1.35], [0.9, -1.35], [-0.9, -1.35]] as const) add(CG.wheel, tyre, wx, 0.34, wz);
  const head = light("#fff2cf", 2), tail = light("#ff1a1a", 2.4);
  for (const lx of [0.62, -0.62]) { add(CG.lamp, head, lx, 0.7, 2.16); add(CG.lamp, tail, lx, 0.74, -2.16); }
  const beam = add(CG.beam, light("#ffe6b8", 1.0, { opacity: 0.2, map: glowTexture(), additive: true }), 0, 0.03, 6.2);
  beam.scale.set(5.5, 1, 9); beam.receiveShadow = false;
  return g;
}

function Car({ loop, start, cruise, color, cars, blockers }: { loop: Loop; start: number; cruise: number; color: string; cars: CarState[]; blockers: Blockers }) {
  const group = useMemo(() => buildCar(color), [color]);
  const st = useRef({ s: start, v: cruise, shift: 0, me: { x: 0, z: 0, tx: 1, tz: 0, v: cruise } as CarState, p: { x: 0, z: 0, tx: 1, tz: 0 } });
  useEffect(() => { const me = st.current.me; cars.push(me); return () => { const i = cars.indexOf(me); if (i >= 0) cars.splice(i, 1); }; }, [cars]);
  useFrame((_, dtRaw) => {
    const dt = Math.min(dtRaw, 0.1), c = st.current, p = c.p;
    loopAt(loop, c.s, p);
    const turning = Math.abs(p.tx) > 0.05 && Math.abs(p.tz) > 0.05;
    let target = turning ? Math.min(cruise, 4.2) : cruise, wantShift = 0;
    const look = 6 + c.v * 1.1, px = p.x + p.tz * c.shift, pz = p.z - p.tx * c.shift; // left normal = (tz, -tx)
    const consider = (bx: number, bz: number, radius: number, follow: boolean) => {
      const rx = bx - px, rz = bz - pz, ahead = rx * p.tx + rz * p.tz;
      if (ahead < 0.4 || ahead > look) return;
      const lat = rx * p.tz - rz * p.tx; // >0 = to our left
      const need = CAR_HALF + radius + 0.45;
      if (Math.abs(lat) > need) return;
      if (!follow) { // steer around a person: left toward the road centre if they are kerb-side, right otherwise
        const s = lat <= 0 ? c.shift + lat + need : c.shift + lat - need;
        if (s >= -1.15 && s <= LANE + 0.1) { if (Math.abs(s) > Math.abs(wantShift)) wantShift = s; target = Math.min(target, 3.2); return; }
      }
      target = Math.min(target, Math.max(0, (ahead - (follow ? 6 : 3.4)) * 1.4));
    };
    for (const b of blockers.current.values()) consider(b.x, b.z, 0.45, false);
    for (const o of cars) if (o !== c.me && o.tx * p.tx + o.tz * p.tz > 0.3) consider(o.x, o.z, 1.2, true);
    c.v += Math.max(-11 * dt, Math.min(4 * dt, target - c.v));
    c.shift += Math.max(-1.6 * dt, Math.min(1.6 * dt, wantShift - c.shift));
    c.s += c.v * dt;
    loopAt(loop, c.s, p);
    const x = p.x + p.tz * c.shift, z = p.z - p.tx * c.shift;
    group.position.set(x, 0, z);
    turnToward(group, Math.atan2(p.tx, p.tz), dt * 9);
    c.me.x = x; c.me.z = z; c.me.tx = p.tx; c.me.tz = p.tz; c.me.v = c.v;
  });
  return <primitive object={group} />;
}

// ---------- pedestrians ----------
const PED_TOPS = ["#7d8590", "#a39584", "#5f6f82", "#8a6f6a", "#6f8271", "#93839c", "#b0a48f", "#59616e"];
function Pedestrian({ loop, start, pace, seed, blockers }: { loop: Loop; start: number; pace: number; seed: number; blockers: Blockers }) {
  const ref = useRef<THREE.Group>(null);
  const speed = useRef(pace);
  const st = useRef({ s: start, wait: 0, p: { x: 0, z: 0, tx: 1, tz: 0 } });
  const outfit = useMemo(() => outfitFor(`ped${seed}`, PED_TOPS[seed % PED_TOPS.length]!, seed % 3 ? "#2a2d38" : "#4a4f5e"), [seed]);
  useFrame((_, dtRaw) => {
    const g = ref.current;
    if (!g) return;
    const dt = Math.min(dtRaw, 0.1), c = st.current, p = c.p;
    loopAt(loop, c.s, p);
    let blocked = false;
    for (const b of blockers.current.values()) {
      const rx = b.x - p.x, rz = b.z - p.z, ahead = rx * p.tx + rz * p.tz;
      if (ahead > 0 && ahead < 1.3 && Math.abs(rx * p.tz - rz * p.tx) < 0.7) { blocked = true; break; }
    }
    c.wait = blocked ? c.wait + dt : 0;
    const v = blocked && c.wait < 2.5 ? 0 : pace; // pause politely, then squeeze past
    speed.current += (v - speed.current) * Math.min(1, dt * 6);
    c.s += speed.current * dt;
    loopAt(loop, c.s, p);
    g.position.set(p.x, 0.18, p.z);
    turnToward(g, Math.atan2(p.tx, p.tz), dt * 8);
  });
  return <group ref={ref}><Figure outfit={outfit} speed={speed} scale={1.06} /></group>;
}

// ---------- sun glitter on the water ----------
function Glints({ map }: { map: MapView }) {
  const { geo, mat } = useMemo(() => {
    const N = 900, pos = new Float32Array(N * 3), seed = new Float32Array(N * 2);
    const cx = map.width / 2, cz = map.height / 2, ax = SUN_DIR.x / Math.hypot(SUN_DIR.x, SUN_DIR.z), az = SUN_DIR.z / Math.hypot(SUN_DIR.x, SUN_DIR.z);
    for (let i = 0; i < N; i++) {
      let x: number, z: number;
      if (i < N * 0.65) { // glitter path toward the sun
        const d = 70 + Math.pow(hash(i * 1.3), 0.7) * 520, w = (hash(i * 2.9) - 0.5) * (18 + d * 0.3);
        x = cx + ax * d - az * w; z = cz + az * d + ax * w;
      } else { // general sparkle around the island
        const ang = hash(i * 4.1) * Math.PI * 2, d = 75 + hash(i * 5.7) * 190;
        x = cx + Math.cos(ang) * d * 1.25; z = cz + Math.sin(ang) * d;
      }
      const onLand = x > -3 && x < map.width + 3 && z > -3 && z < map.walkable.z1 + 1.5;
      pos[i * 3] = x; pos[i * 3 + 1] = onLand ? -50 : -0.48; pos[i * 3 + 2] = z;
      seed[i * 2] = hash(i * 7.7) * 6.283; seed[i * 2 + 1] = 0.6 + hash(i * 9.1) * 1.8;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("seed", new THREE.BufferAttribute(seed, 2));
    const m = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false, toneMapped: false,
      uniforms: { uTime: { value: 0 }, uScale: { value: 1 } },
      vertexShader: `attribute vec2 seed; uniform float uTime; uniform float uScale; varying float vA;
        void main(){ vec4 mv = modelViewMatrix * vec4(position, 1.0);
          float tw = pow(max(0.0, sin(uTime * seed.y * 2.2 + seed.x)), 10.0);
          vA = tw; gl_PointSize = clamp(uScale * (3.0 + 5.0 * tw) * 140.0 / max(1.0, -mv.z), 0.0, 22.0) * step(0.02, tw);
          gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `varying float vA; void main(){ vec2 p = gl_PointCoord * 2.0 - 1.0; float d = dot(p, p); if (d > 1.0) discard;
        float star = (1.0 - d) * (1.0 - d) + 0.6 * max(0.0, 1.0 - 14.0 * abs(p.x * p.y));
        gl_FragColor = vec4(vec3(4.0, 2.6, 1.5) * vA * star, vA * star);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    });
    return { geo: g, mat: m };
  }, [map]);
  useEffect(() => () => { geo.dispose(); mat.dispose(); }, [geo, mat]);
  useFrame(({ clock, gl }) => { mat.uniforms.uTime!.value = clock.elapsedTime; mat.uniforms.uScale!.value = gl.getPixelRatio(); });
  return <points dispose={null} geometry={geo} material={mat} frustumCulled={false} />;
}

export function Life({ map, blockers }: { map: MapView; blockers: Blockers }) {
  const cars = useRef<CarState[]>([]).current;
  const plan = useMemo(() => {
    const { xs, zs } = roadLines(map);
    const X = (i: number) => xs[Math.min(i, xs.length - 1)]!, Z = (i: number) => zs[Math.min(i, zs.length - 1)]!;
    // Right-hand traffic: clockwise circuits run on the inside of their block ring, counter-clockwise ones on the outside.
    const ring = (xa: number, za: number, xb: number, zb: number, cw: boolean): Loop => cw
      ? { x0: xa + LANE, z0: za + LANE, x1: xb - LANE, z1: zb - LANE, r: 3, cw }
      : { x0: xa - LANE, z0: za - LANE, x1: xb + LANE, z1: zb + LANE, r: 4.5, cw };
    const loops: Loop[] = xs.length >= 5 && zs.length >= 3
      ? [ring(X(1), Z(1), X(2), Z(2), true), ring(X(2), Z(1), X(3), Z(2), true), ring(X(1), Z(0), X(3), Z(2), false), ring(X(0), Z(1), X(1), Z(2), true), ring(X(2), Z(0), X(4), Z(1), false)]
      : [];
    const carList = loops.flatMap((loop, li) => {
      const P = loopLength(loop), n = li === 2 ? 3 : 2;
      return Array.from({ length: n }, (_, k) => ({ key: `car${li}-${k}`, loop, start: (P * (k + hash(li * 3 + k) * 0.5)) / n, cruise: 6.5 + hash(li * 7 + k) * 2.5, color: CAR_COLORS[(li * 3 + k * 5) % CAR_COLORS.length]! }));
    });
    const peds = map.buildings.filter((b) => b.x1 - b.x0 > 6).flatMap((b, bi) => {
      const loop: Loop = { x0: b.x0 - 0.5, z0: b.z0 - 0.5, x1: b.x1 + 0.5, z1: b.z1 + 0.5, r: 0.5, cw: bi % 2 === 0 }; // between the wall and the tree line
      const P = loopLength(loop), n = (b.x1 - b.x0) * (b.z1 - b.z0) > 240 ? 2 : 1;
      return Array.from({ length: n }, (_, k) => ({ key: `ped${bi}-${k}`, loop, start: P * (hash(bi * 5 + k) + k / n), pace: 1.15 + hash(bi * 9 + k) * 0.5, seed: bi * 2 + k }));
    });
    return { carList, peds };
  }, [map]);
  return (
    <group>
      {plan.carList.map((c) => <Car key={c.key} loop={c.loop} start={c.start} cruise={c.cruise} color={c.color} cars={cars} blockers={blockers} />)}
      {plan.peds.map((p) => <Pedestrian key={p.key} loop={p.loop} start={p.start} pace={p.pace} seed={p.seed} blockers={blockers} />)}
      <Glints map={map} />
    </group>
  );
}
