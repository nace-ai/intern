import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { MISSION_SPOTS, type Building, type MapView } from "@vice/contracts";
import { fx, fxGlow, glowTexture, hash, light, orm, surface } from "./materials";

export const TONES: Record<Building["tone"], { wall: string; neon: string }> = {
  pink: { wall: "#a87486", neon: "#ff4fa3" },
  aqua: { wall: "#6c93a8", neon: "#35e6ff" },
  purple: { wall: "#7d72b0", neon: "#a97bff" },
  sand: { wall: "#b8987a", neon: "#ffc46b" },
  teal: { wall: "#63968f", neon: "#3dffc8" },
};
/** Direction TO the sun: low in the north-west, i.e. ahead-left of the follow camera, so streets run into the sunset. */
export const SUN_DIR = new THREE.Vector3(-0.5, 0.26, -0.79).normalize();

export interface Inst { x: number; y: number; z: number; sx: number; sy: number; sz: number; color: string; rx?: number; ry?: number; rz?: number }
const _m = new THREE.Matrix4(), _q = new THREE.Quaternion(), _e = new THREE.Euler(), _p = new THREE.Vector3(), _s = new THREE.Vector3(), _c = new THREE.Color();

const GEO = {
  box: new THREE.BoxGeometry(1, 1, 1),
  rbox: new RoundedBoxGeometry(1, 1, 1, 2, 0.16),
  cyl: new THREE.CylinderGeometry(0.5, 0.5, 1, 12),
  pool: new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
};
interface InstProps {
  items: Inst[]; shape?: keyof typeof GEO;
  /** solid = lit PBR; glow = unlit HDR colour (blooms); pool = soft additive light patch on the ground. */
  kind?: "solid" | "glow" | "pool";
  boost?: number; opacity?: number; roughness?: number; metalness?: number; shadows?: boolean; fade?: boolean | number; sway?: number;
  /** Receives the mesh so callers can animate individual instances (e.g. flicker). */
  meshRef?: (mesh: THREE.InstancedMesh | null) => void;
}
/** One draw call per kind. All solid instances share one shader program, all glow instances another. */
export function Instances({ items, shape = "box", kind = "solid", boost = 1, opacity = 1, roughness = 0.85, metalness = 0.05, shadows = false, fade = false, sway = 0, meshRef }: InstProps) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const material = useMemo(() => {
    if (kind === "solid") return fx(new THREE.MeshStandardMaterial({ roughness, metalness }), { fade, sway });
    if (kind === "glow") return fxGlow(new THREE.MeshBasicMaterial({ toneMapped: false }));
    return new THREE.MeshBasicMaterial({ map: glowTexture(), transparent: true, opacity, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending });
  }, [kind, roughness, metalness, fade, sway, opacity]);
  useEffect(() => () => material.dispose(), [material]);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    items.forEach((it, i) => {
      _e.set(it.rx ?? 0, it.ry ?? 0, it.rz ?? 0, "YXZ");
      _m.compose(_p.set(it.x, it.y, it.z), _q.setFromEuler(_e), _s.set(it.sx, it.sy, it.sz));
      mesh.setMatrixAt(i, _m);
      mesh.setColorAt(i, _c.set(it.color).multiplyScalar(kind === "solid" ? 1 : boost));
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    meshRef?.(mesh);
  }, [items, kind, boost, meshRef]);
  if (items.length === 0) return null;
  return <instancedMesh dispose={null} ref={ref} args={[GEO[shape], material, items.length]} frustumCulled={false} castShadow={shadows && kind === "solid"} receiveShadow={kind === "solid"} />;
}

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  return [c, c.getContext("2d")!];
}
function tex(c: HTMLCanvasElement, srgb: boolean): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

/** Background work queue: one job per animation frame, so texture generation never blocks the first frame. */
const jobs: Array<() => void> = [];
let pumping = false;
function schedule(job: () => void): () => void {
  jobs.push(job);
  if (!pumping) {
    pumping = true;
    const pump = () => { jobs.shift()?.(); if (jobs.length) requestAnimationFrame(pump); else pumping = false; };
    requestAnimationFrame(pump);
  }
  return () => { const i = jobs.indexOf(job); if (i >= 0) jobs.splice(i, 1); };
}

/** Procedural facade: colour, emissive (lit rooms + storefront) and roughness/metalness (glass vs wall) maps. */
function facadeMaps(b: Building, seed: number) {
  const tone = TONES[b.tone];
  const span = Math.max(b.x1 - b.x0, b.z1 - b.z0);
  const cols = Math.max(2, Math.round(span / 1.7)), rows = Math.max(1, Math.round(b.height / 2.7));
  const W = 512, H = 512, cw = W / cols, rh = H / rows;
  const [cc, g] = canvas(W, H), [ec, eg] = canvas(W, H), [rc, rg] = canvas(W, H);
  const dark = new THREE.Color(tone.wall).multiplyScalar(0.62).getStyle();
  const grad = g.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, tone.wall); grad.addColorStop(1, dark);
  g.fillStyle = grad; g.fillRect(0, 0, W, H);
  for (let i = 0; i < 140; i++) { g.fillStyle = `rgba(${hash(seed + i) > 0.5 ? "255,255,255" : "0,0,0"},${0.02 + hash(seed * 3 + i) * 0.05})`; g.fillRect(hash(i * 7 + seed) * W, 0, 1 + hash(i) * 6, H); }
  eg.fillStyle = "#000"; eg.fillRect(0, 0, W, H);
  rg.fillStyle = "rgb(0,210,0)"; rg.fillRect(0, 0, W, H); // G = roughness, B = metalness
  for (let r = 0; r < rows; r++) {
    const ground = r === rows - 1;
    g.fillStyle = "rgba(0,0,0,0.32)"; g.fillRect(0, r * rh, W, Math.max(2, rh * 0.035));
    g.fillStyle = "rgba(255,255,255,0.12)"; g.fillRect(0, r * rh + Math.max(2, rh * 0.035), W, 1);
    for (let c = 0; c < cols; c++) {
      const k = seed * 131 + r * 17 + c * 53;
      const x = c * cw + cw * 0.16, y = r * rh + rh * (ground ? 0.2 : 0.24), w = cw * 0.68, h = rh * (ground ? 0.72 : 0.52);
      const lit = ground ? hash(k) > 0.2 : hash(k) > 0.45;
      g.fillStyle = "#1c1e26"; g.fillRect(x - 2, y - 2, w + 4, h + 4);
      if (lit) {
        const col = ground ? tone.neon : hash(k + 5) > 0.35 ? `hsl(${30 + hash(k + 9) * 16},88%,${60 + hash(k + 2) * 14}%)` : `hsl(${190 + hash(k + 9) * 40},65%,70%)`;
        g.fillStyle = col; g.fillRect(x, y, w, h);
        eg.fillStyle = col; eg.globalAlpha = ground ? 1 : 0.5 + hash(k + 1) * 0.5; eg.fillRect(x, y, w, h); eg.globalAlpha = 1;
        g.fillStyle = "rgba(0,0,0,0.28)"; eg.fillStyle = "rgba(0,0,0,0.5)";
        const bh = h * hash(k + 3) * 0.6; g.fillRect(x, y, w, bh); eg.fillRect(x, y, w, bh);
        if (hash(k + 4) > 0.6) eg.fillRect(x + w * 0.3, y + h * 0.55, w * 0.22, h * 0.45);
      } else {
        const gg = g.createLinearGradient(x, y, x + w, y + h);
        gg.addColorStop(0, "#5a6f93"); gg.addColorStop(1, "#1a2238");
        g.fillStyle = gg; g.fillRect(x, y, w, h);
      }
      g.fillStyle = "rgba(0,0,0,0.5)"; g.fillRect(x + w / 2 - 1, y, 2, h);
      rg.fillStyle = "rgb(0,26,170)"; rg.fillRect(x, y, w, h); // glass: smooth + reflective
    }
  }
  return { map: tex(cc, true), emissiveMap: tex(ec, true), orm: tex(rc, false) };
}

function signTexture(label: string, color: string): THREE.CanvasTexture {
  const [c, g] = canvas(512, 96);
  g.font = "800 56px 'Avenir Next', 'Segoe UI', Helvetica, Arial, sans-serif";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.shadowColor = color; g.shadowBlur = 18; g.fillStyle = color;
  g.fillText(label.toUpperCase(), 256, 50, 490);
  g.shadowBlur = 0; g.fillStyle = "#ffffff"; g.globalAlpha = 0.9;
  g.fillText(label.toUpperCase(), 256, 50, 490);
  return tex(c, true);
}

/** Wet asphalt: roughness varies between damp aggregate and standing water. */
function asphaltMaps() {
  const S = 512, [cc, g] = canvas(S, S), [rc, rg] = canvas(S, S);
  g.fillStyle = "#4a4953"; g.fillRect(0, 0, S, S);
  rg.fillStyle = "rgb(0,150,0)"; rg.fillRect(0, 0, S, S);
  for (let i = 0; i < 3500; i++) {
    const x = hash(i) * S, y = hash(i + 0.5) * S, v = 50 + hash(i * 3) * 55;
    g.fillStyle = `rgba(${v},${v},${v + 8},0.45)`; g.fillRect(x, y, 2, 2);
  }
  for (let i = 0; i < 46; i++) {
    const x = hash(i * 11) * S, y = hash(i * 13) * S, r = 12 + hash(i * 17) * 40;
    const pg = rg.createRadialGradient(x, y, 0, x, y, r);
    pg.addColorStop(0, "rgba(0,72,0,1)"); pg.addColorStop(0.7, "rgba(0,86,0,0.85)"); pg.addColorStop(1, "rgba(0,150,0,0)");
    rg.fillStyle = pg; rg.beginPath(); rg.ellipse(x, y, r * 1.6, r, hash(i) * 3, 0, 7); rg.fill();
    const dg = g.createRadialGradient(x, y, 0, x, y, r);
    dg.addColorStop(0, "rgba(40,38,52,0.6)"); dg.addColorStop(1, "rgba(40,38,52,0)");
    g.fillStyle = dg; g.beginPath(); g.ellipse(x, y, r * 1.6, r, hash(i) * 3, 0, 7); g.fill();
  }
  const m = tex(cc, true), r = tex(rc, false);
  for (const t of [m, r]) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(3, 2); }
  return { map: m, orm: r };
}
function waterOrm() {
  const [c, g] = canvas(256, 256);
  g.fillStyle = "rgb(0,108,235)"; g.fillRect(0, 0, 256, 256); // glossy, metallic (never mirror-smooth: the sun highlight would white out)
  for (let i = 0; i < 420; i++) { g.fillStyle = `rgba(0,${120 + hash(i) * 90},235,0.45)`; g.fillRect(hash(i * 3) * 256, hash(i * 7) * 256, 14 + hash(i) * 46, 1 + hash(i * 5) * 2); }
  const t = tex(c, false); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(56, 56);
  return t;
}

const SKY_VERT = "varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }";
const SKY_FRAG = `varying vec3 vDir; uniform vec3 sunDir; uniform float disc;
  float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
  float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
    return mix(mix(h21(i),h21(i+vec2(1,0)),f.x), mix(h21(i+vec2(0,1)),h21(i+vec2(1,1)),f.x), f.y); }
  float fbm(vec2 p){ float a=0.5, s=0.0; for(int i=0;i<4;i++){ s+=a*noise(p); p*=2.03; a*=0.5; } return s; }
  void main(){
    vec3 d = normalize(vDir);
    float h = max(d.y, 0.0);
    float s = max(dot(d, sunDir), 0.0);
    // dusk atmosphere: hot horizon that turns gold toward the sun, magenta mid sky, indigo zenith
    vec3 zenith = vec3(0.012, 0.018, 0.085);
    vec3 midSky = vec3(0.20, 0.045, 0.24);
    vec3 horizon = mix(vec3(0.60, 0.13, 0.12), vec3(1.25, 0.42, 0.07), pow(s, 2.2));
    vec3 col = mix(horizon, midSky, smoothstep(0.0, 0.26, h));
    col = mix(col, zenith, smoothstep(0.18, 0.8, h));
    col += vec3(1.0, 0.36, 0.08) * pow(s, 7.0) * 0.45;
    if (d.y > 0.01) {
      vec2 uv = d.xz / (d.y + 0.14) * 0.85;
      float c = smoothstep(0.50, 0.82, fbm(uv + vec2(3.1, 1.7)));
      vec3 cloud = mix(vec3(0.10, 0.035, 0.14), vec3(1.0, 0.30, 0.12), pow(s, 1.6) * 0.85 + 0.15);
      col = mix(col, cloud, c * smoothstep(0.02, 0.22, d.y) * 0.8);
      float star = step(0.9978, h21(floor(d.xz / (d.y + 0.2) * 220.0)));
      col += vec3(star) * smoothstep(0.5, 0.95, h) * 0.9;
    }
    // the sun: a hot disc with a wide corona
    float core = smoothstep(0.9988, 0.9993, s);
    col = mix(col, vec3(2.4, 1.15, 0.35), core * disc);
    col += vec3(1.2, 0.5, 0.15) * pow(s, 260.0) * 0.4 * disc;
    if (d.y < 0.0) col = mix(col, vec3(0.05, 0.03, 0.10), smoothstep(0.0, -0.1, d.y));
    gl_FragColor = vec4(col, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;
function skyMaterial(disc: number) {
  return new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false, fog: false, uniforms: { sunDir: { value: SUN_DIR }, disc: { value: disc } }, vertexShader: SKY_VERT, fragmentShader: SKY_FRAG });
}

/** Sky dome + image-based lighting baked from that same sky, so glass, water and wet roads reflect the sunset. */
function Atmosphere({ center }: { center: [number, number, number] }) {
  const { gl, scene } = useThree();
  const mat = useMemo(() => skyMaterial(1), []);
  useLayoutEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const envScene = new THREE.Scene();
    const envMat = skyMaterial(0.12), envGeo = new THREE.SphereGeometry(50, 24, 16);
    envScene.add(new THREE.Mesh(envGeo, envMat));
    const rt = pmrem.fromScene(envScene, 0.02, 0.1, 200);
    scene.environment = rt.texture;
    scene.environmentIntensity = 1.7;
    envMat.dispose(); envGeo.dispose(); pmrem.dispose();
    return () => { scene.environment = null; rt.dispose(); };
  }, [gl, scene]);
  useEffect(() => () => mat.dispose(), [mat]);
  return <mesh dispose={null} material={mat} scale={[700, 700, 700]} position={center} frustumCulled={false}><sphereGeometry args={[1, 40, 20]} /></mesh>;
}

function BuildingMesh({ b, index, shadows }: { b: Building; index: number; shadows: boolean }) {
  const mats = useMemo(() => {
    const side = surface({ color: TONES[b.tone].wall, emissiveIntensity: 1.9, ormMap: orm(0.85, 0), envMapIntensity: 1.25, fade: true });
    const roof = surface({ color: "#4a4853", ormMap: orm(0.95, 0), fade: true });
    return { side, roof, list: [side, side, roof, roof, side, side] };
  }, [b]);
  // Facade detail streams in a frame or two after mount; the placeholder already has the right colour and program.
  useEffect(() => {
    let maps: ReturnType<typeof facadeMaps> | null = null;
    const cancel = schedule(() => {
      maps = facadeMaps(b, index + 1);
      mats.side.color.set("#ffffff");
      mats.side.map = maps.map; mats.side.emissiveMap = maps.emissiveMap; mats.side.roughnessMap = mats.side.metalnessMap = maps.orm;
    });
    return () => { cancel(); maps?.map.dispose(); maps?.emissiveMap.dispose(); maps?.orm.dispose(); mats.side.dispose(); mats.roof.dispose(); };
  }, [b, index, mats]);
  const w = b.x1 - b.x0, d = b.z1 - b.z0;
  return <mesh dispose={null} geometry={GEO.box} material={mats.list} scale={[w, b.height, d]} position={[(b.x0 + b.x1) / 2, b.height / 2, (b.z0 + b.z1) / 2]} castShadow={shadows} receiveShadow />;
}

/** Road centre lines: midpoints of the gaps between building columns / rows, plus the perimeter. */
export function roadLines(map: MapView): { xs: number[]; zs: number[] } {
  const mids = (ranges: Array<[number, number]>, max: number): number[] => {
    const merged: Array<[number, number]> = [];
    for (const r of [...ranges].sort((a, b) => a[0] - b[0])) {
      const last = merged[merged.length - 1];
      if (last && r[0] <= last[1] + 3) last[1] = Math.max(last[1], r[1]); else merged.push([r[0], r[1]]);
    }
    const out: number[] = []; let prev = 0;
    for (const r of merged) { out.push((prev + r[0]) / 2); prev = r[1]; }
    out.push((prev + max) / 2);
    return out;
  };
  const xs = mids(map.buildings.filter((b) => b.x1 - b.x0 > 6).map((b) => [b.x0, b.x1]), map.width);
  const zs = mids(map.buildings.filter((b) => b.z1 - b.z0 > 6).map((b) => [b.z0, b.z1]), map.walkable.z1 + 1);
  // perimeter roads are bounded by a kerb on one side only: nudge their centre line away from it
  if (xs.length > 1) { xs[0]! -= 0.8; xs[xs.length - 1]! += 0.8; }
  if (zs.length > 1) zs[0]! -= 0.8;
  return { xs, zs };
}

export const CAR_COLORS = ["#c0222d", "#1f56b8", "#ece8de", "#1b1c21", "#e09a1f", "#2a9f88", "#8a2fa0", "#a7b0ba"];

export function World({ map, shadows }: { map: MapView; shadows: boolean }) {
  const data = useMemo(() => {
    const A = () => [] as Inst[];
    const kerbs = A(), ledges = A(), neon = A(), marks = A(), trunks = A(), fronds = A(), metal = A(), lamps = A(), pools = A(), roofKit = A(), tanks = A();
    const wood = A(), carBody = A(), carGlass = A(), wheels = A(), head = A(), tail = A(), sigRed = A(), sigGreen = A(), bins = A(), planters = A(), shrubs = A();
    const far = A(), farLights = A();
    const inB = (x: number, z: number, pad: number) => map.buildings.some((b) => x > b.x0 - pad && x < b.x1 + pad && z > b.z0 - pad && z < b.z1 + pad);
    const z1 = map.walkable.z1;
    const keepClear = (x: number, z: number, r: number) => MISSION_SPOTS.some((m) => Math.hypot(m.x - x, m.z - z) < r);

    map.buildings.forEach((b, bi) => {
      const w = b.x1 - b.x0, d = b.z1 - b.z0, cx = (b.x0 + b.x1) / 2, cz = (b.z0 + b.z1) / 2, tone = TONES[b.tone];
      kerbs.push({ x: cx, y: 0.09, z: cz, sx: w + 3.4, sy: 0.18, sz: d + 3.4, color: "#827f8c" });
      ledges.push({ x: cx, y: 0.3, z: cz, sx: w + 0.3, sy: 0.6, sz: d + 0.3, color: "#55525e" });
      for (let y = 5.4; y < b.height - 1; y += 5.4) ledges.push({ x: cx, y, z: cz, sx: w + 0.36, sy: 0.16, sz: d + 0.36, color: "#6a6675" });
      for (const [px, pz, sx, sz] of [[cx, b.z0, w + 0.3, 0.3], [cx, b.z1, w + 0.3, 0.3], [b.x0, cz, 0.3, d + 0.3], [b.x1, cz, 0.3, d + 0.3]] as const)
        ledges.push({ x: px, y: b.height + 0.3, z: pz, sx, sy: 0.6, sz, color: "#625f6d" });
      // neon: cornice tubes, storefront canopy line, and their smeared reflections on the wet street
      neon.push({ x: cx, y: b.height + 0.66, z: b.z1 + 0.05, sx: w + 0.3, sy: 0.1, sz: 0.1, color: tone.neon });
      neon.push({ x: b.x1 + 0.05, y: b.height + 0.66, z: cz, sx: 0.1, sy: 0.1, sz: d + 0.3, color: tone.neon });
      neon.push({ x: b.x0 - 0.05, y: b.height + 0.66, z: cz, sx: 0.1, sy: 0.1, sz: d + 0.3, color: tone.neon });
      neon.push({ x: cx, y: 2.75, z: b.z1 + 0.5, sx: w * 0.8, sy: 0.08, sz: 0.08, color: tone.neon });
      ledges.push({ x: cx, y: 2.9, z: b.z1 + 0.45, sx: w * 0.82, sy: 0.14, sz: 1.0, color: "#3d3a47" });
      if (w > 6) {
        pools.push({ x: cx, y: 0.2, z: b.z1 + 2.2, sx: w * 1.05, sy: 1, sz: 5.5, color: tone.neon });
        pools.push({ x: cx, y: 0.022, z: b.z1 + 6, sx: w * 0.9, sy: 1, sz: 9, color: tone.neon });
      }
      const n = Math.max(1, Math.floor((w * d) / 60));
      for (let i = 0; i < n; i++) {
        const rx = b.x0 + 1.5 + hash(bi * 7 + i) * (w - 3), rz = b.z0 + 1.5 + hash(bi * 11 + i) * (d - 3);
        if (i % 3 === 0) tanks.push({ x: rx, y: b.height + 1.2, z: rz, sx: 1.5, sy: 1.6, sz: 1.5, color: "#8a8790" });
        else roofKit.push({ x: rx, y: b.height + 0.45, z: rz, sx: 1.6 + hash(i) * 1.2, sy: 0.9, sz: 1.2, color: "#a9adb5", ry: hash(i + bi) > 0.5 ? 0 : Math.PI / 2 });
      }
      if (w > 6) roofKit.push({ x: cx + w * 0.25, y: b.height + 1.3, z: cz + d * 0.2, sx: 2.2, sy: 2.6, sz: 2.2, color: "#6f6c79" });
    });

    const { xs, zs } = roadLines(map);
    for (const x of xs) for (let z = 3; z < z1 - 1; z += 4) if (!inB(x, z, 2) && !zs.some((zz) => Math.abs(zz - z) < 5.5)) marks.push({ x, y: 0.012, z, sx: 0.16, sy: 0.02, sz: 2, color: "#b9a352" });
    for (const z of zs) for (let x = 3; x < map.width - 2; x += 4) if (!inB(x, z, 2) && !xs.some((xx) => Math.abs(xx - x) < 5.5)) marks.push({ x, y: 0.012, z, sx: 2, sy: 0.02, sz: 0.16, color: "#b9a352" });
    for (const x of xs) for (const z of zs) {
      if (inB(x, z, 1)) continue;
      for (let k = -3; k <= 3; k++) {
        marks.push({ x: x + k * 0.9, y: 0.012, z: z - 5.2, sx: 0.5, sy: 0.02, sz: 2.2, color: "#a9acb6" });
        marks.push({ x: x + k * 0.9, y: 0.012, z: z + 5.2, sx: 0.5, sy: 0.02, sz: 2.2, color: "#a9acb6" });
        marks.push({ x: x - 5.2, y: 0.012, z: z + k * 0.9, sx: 2.2, sy: 0.02, sz: 0.5, color: "#a9acb6" });
        marks.push({ x: x + 5.2, y: 0.012, z: z + k * 0.9, sx: 2.2, sy: 0.02, sz: 0.5, color: "#a9acb6" });
      }
      for (const [sx, sz] of [[-4.6, -4.6], [4.6, 4.6]] as const) {
        const px = x + sx, pz = z + sz;
        if (inB(px, pz, 0) || px < 1 || px > map.width - 1 || pz > z1 || pz < 1) continue;
        const red = hash(x + z) > 0.5;
        metal.push({ x: px, y: 2.4, z: pz, sx: 0.14, sy: 4.8, sz: 0.14, color: "#2e3037" });
        metal.push({ x: px, y: 4.4, z: pz, sx: 0.38, sy: 1.0, sz: 0.38, color: "#1b1c21" });
        (red ? sigRed : sigGreen).push({ x: px, y: red ? 4.7 : 4.1, z: pz + (sz > 0 ? 0.2 : -0.2), sx: 0.22, sy: 0.22, sz: 0.08, color: red ? "#ff2a2a" : "#2aff7a" });
      }
    }

    let carN = 0, carsPlaced = false;
    map.buildings.forEach((b, bi) => {
      if (b.x1 - b.x0 < 6) return;
      const pts: Array<[number, number, number]> = [];
      for (let x = b.x0 + 1; x <= b.x1 - 1; x += 5.5) { pts.push([x, b.z0 - 1.25, 0]); pts.push([x + 2.7, b.z1 + 1.25, 0]); }
      for (let z = b.z0 + 2; z <= b.z1 - 2; z += 6) { pts.push([b.x0 - 1.25, z, 1]); pts.push([b.x1 + 1.25, z + 2, 1]); }
      pts.forEach(([x, z, side], i) => {
        if (inB(x, z, 0.3) || x < 1.5 || x > map.width - 1.5 || z < 1.5 || z > z1 - 0.5) return;
        const r = hash(bi * 97 + i), kind = i % 4;
        if (kind === 1) {
          const ax = side ? (x < (b.x0 + b.x1) / 2 ? -1 : 1) : 0, az = side ? 0 : (z < (b.z0 + b.z1) / 2 ? -1 : 1);
          metal.push({ x, y: 3.1, z, sx: 0.15, sy: 6.2, sz: 0.15, color: "#34363e" });
          metal.push({ x: x + ax * 0.9, y: 6.15, z: z + az * 0.9, sx: ax ? 1.9 : 0.1, sy: 0.1, sz: az ? 1.9 : 0.1, color: "#34363e" });
          const warm = (bi + i) % 3 !== 0;
          lamps.push({ x: x + ax * 1.7, y: 6.05, z: z + az * 1.7, sx: ax ? 0.7 : 0.3, sy: 0.12, sz: az ? 0.7 : 0.3, color: warm ? "#ffd9a6" : "#ff9ae0" });
          pools.push({ x: x + ax * 1.9, y: 0.21, z: z + az * 1.9, sx: 9, sy: 1, sz: 9, color: warm ? "#ffc98a" : "#ff8ad6" });
        } else if (kind === 3) {
          if (r > 0.5) bins.push({ x, y: 0.63, z, sx: 0.55, sy: 0.9, sz: 0.55, color: "#3f6652" });
          else { planters.push({ x, y: 0.45, z, sx: 1.5, sy: 0.55, sz: 0.8, color: "#8a857d", ry: side ? Math.PI / 2 : 0 }); shrubs.push({ x, y: 0.95, z, sx: 1.3, sy: 0.8, sz: 0.7, color: "#3f9a5f", ry: side ? Math.PI / 2 : 0 }); }
        } else {
          const h = 5 + r * 2.6, lean = (r - 0.5) * 0.16;
          trunks.push({ x, y: h / 2, z, sx: 0.3, sy: h, sz: 0.3, color: "#94735b", rz: lean });
          planters.push({ x, y: 0.24, z, sx: 1.2, sy: 0.14, sz: 1.2, color: "#5d564e" });
          for (let k = 0; k < 9; k++) {
            const ry = (k / 9) * Math.PI * 2 + r * 3, tilt = -0.35 - hash(i + k) * 0.35;
            const off = new THREE.Vector3(1.5, 0, 0).applyEuler(new THREE.Euler(0, ry, tilt, "YXZ"));
            fronds.push({ x: x + off.x - lean * h, y: h + off.y + 0.05, z: z + off.z, sx: 3.1, sy: 0.05, sz: 0.5, color: k % 2 ? "#3b9a62" : "#4db877", ry, rz: tilt });
          }
        }
      });
      // Parked cars are SOLID: they come from the shared map, where the server stops players and NPCs at their footprints.
      const park = (x: number, z: number, ry: number, col: string) => {
        carN++;
        const e = new THREE.Euler(0, ry, 0);
        const at = (lx: number, ly: number, lz: number) => { const v = new THREE.Vector3(lx, ly, lz).applyEuler(e); return { x: x + v.x, y: ly, z: z + v.z }; };
        const suv = hash(carN) > 0.6;
        carBody.push({ ...at(0, 0.62, 0), sx: 4.3, sy: suv ? 0.78 : 0.62, sz: 1.82, color: col, ry });
        carBody.push({ ...at(-0.15, suv ? 1.22 : 1.08, 0), sx: suv ? 2.9 : 2.3, sy: suv ? 0.62 : 0.5, sz: 1.62, color: col, ry });
        carGlass.push({ ...at(-0.15, suv ? 1.2 : 1.07, 0), sx: suv ? 2.6 : 2.0, sy: suv ? 0.44 : 0.34, sz: 1.66, color: "#10151d", ry });
        carGlass.push({ ...at(-0.15, suv ? 1.2 : 1.07, 0), sx: suv ? 2.94 : 2.34, sy: suv ? 0.4 : 0.3, sz: 1.3, color: "#10151d", ry });
        for (const [wx, wz] of [[1.35, 0.9], [1.35, -0.9], [-1.35, 0.9], [-1.35, -0.9]] as const) wheels.push({ ...at(wx, 0.34, wz), sx: 0.68, sy: 0.24, sz: 0.68, color: "#0d0d10", ry, rx: Math.PI / 2 });
        for (const wz of [0.62, -0.62]) { head.push({ ...at(2.16, 0.7, wz), sx: 0.06, sy: 0.14, sz: 0.36, color: "#fff2cf", ry }); tail.push({ ...at(-2.16, 0.74, wz), sx: 0.06, sy: 0.12, sz: 0.4, color: "#ff1a1a", ry }); }
      };
      if (!carsPlaced) { carsPlaced = true; for (const c of map.cars ?? []) park(c.x, c.z, c.heading === 90 ? Math.PI / 2 : 0, c.tone); }
    });

    const wz = map.water[0]?.z0 ?? map.height - 6;
    wood.push({ x: map.width / 2, y: 0.1, z: wz - 0.4, sx: map.width - 2, sy: 0.2, sz: 1.6, color: "#8a684d" });
    for (const px of [36, 44, 52, 60, 88]) {
      wood.push({ x: px, y: 0.12, z: wz + 6, sx: 1.8, sy: 0.24, sz: 13, color: "#8a684d" });
      for (const dz of [1, 6, 11.5]) for (const dx of [-0.8, 0.8]) metal.push({ x: px + dx, y: -0.3, z: wz + dz, sx: 0.22, sy: 1.6, sz: 0.22, color: "#4a3d34" });
      lamps.push({ x: px, y: 0.5, z: wz + 12.3, sx: 0.25, sy: 0.25, sz: 0.25, color: "#3dffc8" });
    }
    for (let x = 3; x < map.width - 2; x += 3) metal.push({ x, y: 0.65, z: wz - 0.05, sx: 0.08, sy: 0.9, sz: 0.08, color: "#d5d8de" });
    metal.push({ x: map.width / 2, y: 1.08, z: wz - 0.05, sx: map.width - 4, sy: 0.07, sz: 0.07, color: "#d5d8de" });

    // far shore: a hazy skyline across the water to the north so the streets run toward a larger world
    for (let i = 0; i < 64; i++) {
      const fxp = -260 + i * 10.5 + hash(i) * 6, fz = -250 - hash(i * 3) * 110, fw = 9 + hash(i * 5) * 14, fh = 22 + hash(i * 7) * hash(i * 2) * 120;
      far.push({ x: fxp, y: fh / 2 - 2, z: fz, sx: fw, sy: fh, sz: fw, color: hash(i) > 0.5 ? "#3a305f" : "#463a6b" });
      for (let k = 0; k < 7; k++) farLights.push({ x: fxp - fw * 0.4 + hash(i * 13 + k) * fw * 0.8, y: 4 + hash(i * 17 + k) * (fh - 8), z: fz + fw / 2 + 0.3, sx: 1.6, sy: 1.1, sz: 0.2, color: hash(k + i) > 0.4 ? "#ffd9a0" : "#9be8ff" });
      if (fh > 90) farLights.push({ x: fxp, y: fh + 1, z: fz, sx: 1.4, sy: 1.4, sz: 1.4, color: "#ff3b3b" });
    }
    far.push({ x: map.width / 2, y: -0.2, z: -318, sx: 820, sy: 3.2, sz: 170, color: "#2f2750" }); // the far shore itself, so the skyline stands on land
    return { kerbs, ledges, neon, marks, trunks, fronds, metal, lamps, pools, roofKit, tanks, wood, carBody, carGlass, wheels, head, tail, sigRed, sigGreen, bins, planters, shrubs, far, farLights };
  }, [map]);

  const signs = useMemo(() => map.buildings.filter((b) => b.x1 - b.x0 >= 6).map((b, i) => ({ b, mat: light("#ffffff", 1.35, { map: signTexture(b.label, TONES[b.tone].neon) }), flicker: i === 1 || i === 6 })), [map]);
  useEffect(() => () => signs.forEach((s) => { s.mat.map?.dispose(); s.mat.dispose(); }), [signs]);
  const ground = useMemo(() => {
    const a = asphaltMaps();
    return { asphalt: a, mat: surface({ map: a.map, ormMap: a.orm, envMapIntensity: 1.35 }), slab: surface({ color: "#4a4954", ormMap: orm(0.9, 0) }) };
  }, []);
  const sea = useMemo(() => { const o = waterOrm(); return { orm: o, mat: surface({ color: "#3f6f86", ormMap: o, envMapIntensity: 1.5 }) }; }, []);
  useEffect(() => () => { ground.asphalt.map.dispose(); ground.asphalt.orm.dispose(); ground.mat.dispose(); ground.slab.dispose(); sea.orm.dispose(); sea.mat.dispose(); }, [ground, sea]);

  const cx = map.width / 2, cz = map.height / 2;
  const sun = useRef<THREE.DirectionalLight>(null);
  useLayoutEffect(() => {
    const l = sun.current;
    if (!l) return;
    l.target.position.set(cx, 0, cz - 4);
    l.target.updateMatrixWorld();
    const cam = l.shadow.camera;
    cam.left = -92; cam.right = 92; cam.top = 52; cam.bottom = -52; cam.near = 40; cam.far = 380;
    cam.updateProjectionMatrix();
  }, [cx, cz]);
  const neonMesh = useRef<THREE.InstancedMesh | null>(null);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    sea.orm.offset.set(t * 0.012, t * 0.007);
    // a couple of signs buzz like tired neon
    signs.forEach((s, i) => {
      if (!s.flicker) return;
      const n = Math.sin(t * 31 + i * 7) * Math.sin(t * 17.3 + i) * Math.sin(t * 4.1 + i * 3);
      s.mat.color.setScalar(n > 0.55 ? 0.4 : 1.35 + Math.sin(t * 60) * 0.1);
    });
  });

  return (
    <group>
      <Atmosphere center={[cx, 0, cz]} />
      <fogExp2 attach="fog" args={["#7d4a78", 0.0027]} />
      <hemisphereLight args={["#c9a6f2", "#e08a62", 2.3]} />
      <directionalLight
        ref={sun} color="#ff9d57" intensity={4.2} castShadow={shadows}
        position={[cx + SUN_DIR.x * 200, SUN_DIR.y * 200, cz + SUN_DIR.z * 200]}
        shadow-mapSize={[2048, 2048]} shadow-bias={-0.0006} shadow-normalBias={0.08} shadow-radius={4}
      />

      {/* wet asphalt island */}
      <mesh dispose={null} geometry={GEO.pool} material={ground.mat} scale={[map.width + 6, 1, map.walkable.z1 + 5]} position={[cx, 0, (map.walkable.z1 + 1) / 2]} receiveShadow />
      <mesh dispose={null} geometry={GEO.box} material={ground.slab} scale={[map.width + 6, 3, map.walkable.z1 + 5]} position={[cx, -1.52, (map.walkable.z1 + 1) / 2]} receiveShadow />
      {/* sea all around */}
      <mesh dispose={null} geometry={GEO.pool} material={sea.mat} scale={[1800, 1, 1800]} position={[cx, -0.55, cz]} receiveShadow />

      {map.buildings.map((b, i) => <BuildingMesh key={b.id} b={b} index={i} shadows={shadows} />)}
      <Instances items={data.kerbs} roughness={0.9} shadows={shadows} />
      <Instances items={data.ledges} roughness={0.8} shadows={shadows} fade />
      <Instances items={data.roofKit} roughness={0.55} metalness={0.5} shadows={shadows} fade />
      <Instances items={data.tanks} shape="cyl" roughness={0.6} metalness={0.4} shadows={shadows} fade />
      <Instances items={data.marks} roughness={0.55} />
      <Instances items={data.wood} roughness={0.8} shadows={shadows} />
      <Instances items={data.metal} roughness={0.4} metalness={0.7} shadows={shadows} fade={11} />
      <Instances items={data.trunks} shape="cyl" roughness={0.95} shadows={shadows} fade={14} />
      <Instances items={data.fronds} roughness={0.7} shadows={shadows} fade={14} sway={0.16} />
      <Instances items={data.bins} shape="cyl" roughness={0.5} metalness={0.4} shadows={shadows} />
      <Instances items={data.planters} roughness={0.9} shadows={shadows} />
      <Instances items={data.shrubs} shape="rbox" roughness={0.95} shadows={shadows} />
      <Instances items={data.carBody} shape="rbox" roughness={0.2} metalness={0.7} shadows={shadows} fade />
      <Instances items={data.carGlass} shape="rbox" roughness={0.04} metalness={0.95} fade />
      <Instances items={data.wheels} shape="cyl" roughness={0.9} fade />
      <Instances items={data.far} roughness={1} />
      <Instances items={data.head} kind="glow" boost={1.5} />
      <Instances items={data.tail} kind="glow" boost={2.0} />
      <Instances items={data.sigRed} kind="glow" boost={2.6} />
      <Instances items={data.sigGreen} kind="glow" boost={2.6} />
      <Instances items={data.neon} kind="glow" boost={2.4} meshRef={(m) => { neonMesh.current = m; }} />
      <Instances items={data.lamps} kind="glow" boost={2} />
      <Instances items={data.farLights} kind="glow" boost={1.5} />
      <Instances items={data.pools} shape="pool" kind="pool" boost={1} opacity={0.13} />

      {signs.map(({ b, mat }) => {
        const w = Math.min(b.x1 - b.x0 - 1, 15);
        return <mesh dispose={null} key={b.id} geometry={GEO.pool} material={mat} rotation={[Math.PI / 2, 0, 0]} scale={[w, 1, w * (96 / 512)]} position={[(b.x0 + b.x1) / 2, b.height + 1.9, b.z1 + 0.12]} />;
      })}
    </group>
  );
}
