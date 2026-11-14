// Shared GPU resources for the 3D city. The goal is FEW shader programs: every material here is built so that
// materials of the same family compile to one program (first frame fast, no mid-game compile hitches).
import * as THREE from "three";

/** Uniforms shared by every "fx" material: camera keyhole/near fade and wind time. Updated once per frame. */
export const FX = {
  ocNdc: { value: new THREE.Vector2(0, 0) },
  /** View-space depth of the player. 0 disables the keyhole (orbit camera). */
  ocDepth: { value: 0 },
  ocRes: { value: new THREE.Vector2(1, 1) },
  ocRadius: { value: 0.36 },
  time: { value: 0 },
};

const VERT_PARS = "uniform float uSway; uniform float uTime;";
// Wind: lift the far end of palm fronds (local +x) in world-up, after the stock projection so it works with instancing.
const VERT_MAIN = `
  if (uSway > 0.0) {
    #ifdef USE_INSTANCING
      vec3 fxW = (modelMatrix * instanceMatrix * vec4(transformed, 1.0)).xyz;
    #else
      vec3 fxW = (modelMatrix * vec4(transformed, 1.0)).xyz;
    #endif
    float fxS = sin(uTime * 1.7 + fxW.x * 0.31 + fxW.z * 0.23) * uSway * (position.x + 0.5);
    mvPosition.xyz += (viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz * fxS;
    gl_Position = projectionMatrix * mvPosition;
  }`;
const FRAG_PARS = "uniform vec2 uOcNdc; uniform float uOcDepth; uniform vec2 uOcRes; uniform float uOcRadius; uniform float uOcEnable;"; // uOcEnable = 0 off, else the lens distance (m) at which the prop is fully solid
// Dithered dissolve: (a) anything about to touch the lens, (b) a keyhole around the player for whatever stands in front of them.
const FRAG_MAIN = `
  if (uOcEnable > 0.5) {
    float fxVd = vViewPosition.z;
    float fxFade = 1.0 - smoothstep(uOcEnable * 0.42, uOcEnable, fxVd);
    if (fxVd < uOcDepth - 1.2) {
      vec2 fxP = gl_FragCoord.xy / uOcRes * 2.0 - 1.0;
      float fxD = length((fxP - uOcNdc) * vec2(uOcRes.x / uOcRes.y, 1.0));
      fxFade = max(fxFade, 1.0 - smoothstep(uOcRadius * 0.5, uOcRadius, fxD));
    }
    if (fxFade > 0.0 && fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))) < fxFade) discard;
  }`;

/** Adds the shared keyhole fade (opt-in) and wind sway (opt-in) to a standard material without changing its program family. */
export function fx<T extends THREE.Material>(m: T, o: { fade?: boolean | number; sway?: number } = {}): T {
  const uOcEnable = { value: o.fade === true ? 6.5 : o.fade || 0 }, uSway = { value: o.sway ?? 0 };
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, { uOcNdc: FX.ocNdc, uOcDepth: FX.ocDepth, uOcRes: FX.ocRes, uOcRadius: FX.ocRadius, uOcEnable, uSway, uTime: FX.time });
    shader.vertexShader = shader.vertexShader.replace("#include <common>", `#include <common>\n${VERT_PARS}`).replace("#include <project_vertex>", `#include <project_vertex>\n${VERT_MAIN}`);
    shader.fragmentShader = shader.fragmentShader.replace("#include <common>", `#include <common>\n${FRAG_PARS}`).replace("#include <clipping_planes_fragment>", `#include <clipping_planes_fragment>\n${FRAG_MAIN}`);
  };
  m.customProgramCacheKey = () => "vice-fx";
  return m;
}

/** Near-lens dissolve for unlit glow geometry (neon tubes, lamp heads), so a tube beside the camera never smears across the frame. */
export function fxGlow<T extends THREE.MeshBasicMaterial>(m: T, solidAt = 9): T {
  const uNear = { value: solidAt };
  m.onBeforeCompile = (shader) => {
    shader.uniforms.uNear = uNear;
    shader.fragmentShader = shader.fragmentShader.replace("#include <common>", "#include <common>\nuniform float uNear;").replace("#include <clipping_planes_fragment>", `#include <clipping_planes_fragment>
  { float fxF = 1.0 - smoothstep(uNear * 0.42, uNear, 1.0 / gl_FragCoord.w);
    if (fxF > 0.0 && fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))) < fxF) discard; }`);
  };
  m.customProgramCacheKey = () => "vice-fx-glow";
  return m;
}

function pixel(r: number, g: number, b: number, srgb: boolean): THREE.DataTexture {
  const t = new THREE.DataTexture(new Uint8Array([r, g, b, 255]), 1, 1, THREE.RGBAFormat);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.needsUpdate = true;
  return t;
}
export const WHITE = pixel(255, 255, 255, true);
export const BLACK = pixel(0, 0, 0, true);
/** 1x1 occlusion/roughness/metalness texel (G = roughness, B = metalness). */
export function orm(roughness: number, metalness: number): THREE.DataTexture { return pixel(0, Math.round(roughness * 255), Math.round(metalness * 255), false); }

/**
 * "Surface" family: one program for facades, roofs, wet asphalt and water. Every slot is always bound
 * (placeholders when unused), so swapping real textures in later never recompiles anything.
 */
export function surface(o: { color?: THREE.ColorRepresentation; map?: THREE.Texture; emissiveMap?: THREE.Texture; emissiveIntensity?: number; ormMap?: THREE.Texture; envMapIntensity?: number; fade?: boolean | number }): THREE.MeshStandardMaterial {
  const ormMap = o.ormMap ?? orm(0.9, 0);
  return fx(new THREE.MeshStandardMaterial({
    color: o.color ?? "#ffffff", map: o.map ?? WHITE,
    emissive: "#ffffff", emissiveMap: o.emissiveMap ?? BLACK, emissiveIntensity: o.emissiveIntensity ?? 0,
    roughness: 1, metalness: 1, roughnessMap: ormMap, metalnessMap: ormMap, envMapIntensity: o.envMapIntensity ?? 1,
  }), { fade: o.fade });
}

const plainCache = new Map<string, THREE.MeshStandardMaterial>();
/** "Plain" family (figures, vehicles, props): cached by colour so thousands of meshes share a handful of materials. */
export function plain(color: string, roughness = 0.75, metalness = 0, fade = false): THREE.MeshStandardMaterial {
  const k = `${color}|${roughness}|${metalness}|${fade}`;
  let m = plainCache.get(k);
  if (!m) { m = fx(new THREE.MeshStandardMaterial({ color, roughness, metalness }), { fade }); plainCache.set(k, m); }
  return m;
}

let glowTex: THREE.CanvasTexture | null = null;
/** Soft radial falloff used for light pools and halos. */
export function glowTexture(): THREE.CanvasTexture {
  if (glowTex) return glowTex;
  const c = document.createElement("canvas"); c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)"); grad.addColorStop(0.35, "rgba(255,255,255,0.45)"); grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
  glowTex = new THREE.CanvasTexture(c); glowTex.colorSpace = THREE.SRGBColorSpace;
  return glowTex;
}

/**
 * "Light" family: unlit HDR colour (values above 1 bloom), always transparent + double sided + mapped,
 * so signs, beacons, rings, arcs, halos and light pools all share one program.
 */
export function light(color: THREE.ColorRepresentation, boost = 1, o: { opacity?: number; map?: THREE.Texture; additive?: boolean } = {}): THREE.MeshBasicMaterial {
  const m = new THREE.MeshBasicMaterial({ map: o.map ?? WHITE, transparent: true, opacity: o.opacity ?? 1, side: THREE.DoubleSide, forceSinglePass: true, toneMapped: false, depthWrite: false });
  m.color.set(color).multiplyScalar(boost);
  if (o.additive) m.blending = THREE.AdditiveBlending;
  return m;
}

export function hash(n: number): number { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
