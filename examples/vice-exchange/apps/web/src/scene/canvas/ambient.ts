// Ambient2D: decorative city life for the top-down Canvas 2D city (traffic, strollers, neon, water, palms, haze).
//
// It is scenery only. It never reads or writes game state: the only inputs are the MapView, a seed and elapsed
// time, so every client draws the same thing. Nothing here is an agent and nothing here carries game meaning.
//
//   const ambient = createAmbient(map, seed);
//   // every animation frame, inside the renderer's own loop:
//   ambient.update(dtMs);
//   ambient.drawUnder(ctx, view);   // right after the static city, before missions / NPCs / players
//   ambient.drawOver(ctx, view);    // after NPCs and players (light in the air only, never opaque)
//
// `view` uses the renderer's own camera: { x, z, scale } exactly as CanvasCity's `cam`, plus the canvas size in
// CSS pixels. The context must already carry the device-pixel-ratio transform; both draw calls save and restore it.
import type { Building, MapView } from "@vice/contracts";
import { MISSION_SPOTS, SPAWN_POINTS } from "@vice/contracts";
import { bakeBeam, bakeGlow, bakePalm, bakeSign, SIGN_MARGIN } from "./Ambient2DSprites";
import type { Sprite } from "./Ambient2DSprites";
import { createTraffic, KIND_SCOOTER, KIND_SEDAN, KIND_SPORT, KIND_VAN, SIG_AMBER, SIG_GREEN, TRAFFIC_STEP } from "./Ambient2DTraffic";

export interface AmbientView {
  /** Camera centre in metres (CanvasCity's cam.x / cam.z). */
  x: number; z: number;
  /** CSS pixels per metre (CanvasCity's cam.scale). */
  scale: number;
  /** Canvas size in CSS pixels. */
  width: number; height: number;
}
export interface AmbientOptions {
  /** Calm variant: a still tableau with steady lights. Defaults to the user's prefers-reduced-motion setting. */
  reducedMotion?: boolean;
  /** Moving vehicles (default 26). */
  cars?: number;
  /** Stroller density multiplier (default 1, about 45 people). */
  pedestrians?: number;
  /** "low" drops the haze and searchlights and halves the water sparkle. */
  quality?: "high" | "low";
  /**
   * Fixed spots the traffic must never drive over (for example where a stall or a standing figure is). Lanes and
   * junction movements within 1.6 m of a spot are closed when the layer is created; it is plain geometry, read
   * once, and the layer still never looks at live state. Defaults to the map's known standing posts; pass []
   * to open every lane.
   */
  keepClear?: ReadonlyArray<{ x: number; z: number }>;
}
export interface Ambient {
  /** Advance the scenery. Call once per frame with the frame time in milliseconds. */
  update(dtMs: number): void;
  /** Ground level: water, boats, road paint, traffic, strollers, palms, rooftop neon. Draw before any agent. */
  drawUnder(ctx: CanvasRenderingContext2D, view: AmbientView): void;
  /** Light in the air: haze, searchlights, the lighthouse sweep, gulls. Draw after the agents. */
  drawOver(ctx: CanvasRenderingContext2D, view: AmbientView): void;
  /** Switch the calm variant on or off at runtime. */
  setReducedMotion(reduced: boolean): void;
}

type Tone = Building["tone"];
const TAU = Math.PI * 2;
const RGB: Record<Tone, string> = { pink: "255,95,162", aqua: "76,201,240", purple: "199,125,255", sand: "255,179,138", teal: "93,253,203" };
const HEX: Record<Tone, string> = { pink: "#ff5fa2", aqua: "#4cc9f0", purple: "#c77dff", sand: "#ffb38a", teal: "#5dfdcb" };
const HOT: Record<Tone, string> = { pink: "#ffd3e6", aqua: "#d6f4ff", purple: "#f0deff", sand: "#ffeada", teal: "#dcfff3" };
const ALT: Record<Tone, string> = { pink: "#4cc9f0", aqua: "#ff5fa2", purple: "#5dfdcb", sand: "#ff5fa2", teal: "#c77dff" };
const CHASE = 0, BREATHE = 1, FAULTY = 2, DUAL = 3;
const STYLE_BY_ID: Record<string, number> = {
  b_precinct: BREATHE, b_arcade: CHASE, b_alley_w: BREATHE, b_alley_e: FAULTY, b_motel: FAULTY, b_exchange: CHASE,
  b_kiosk: CHASE, b_mall: DUAL, b_court: BREATHE, b_warehouse: FAULTY, b_hotel: DUAL, b_diner: CHASE,
};
// Decorative wording only: nothing here names a ticker, a price, a role or a game state.
const SIGN_BY_ID: Record<string, { text: string; rgb: string; w: number; h: number; side: number }> = {
  b_arcade: { text: "PLAY", rgb: "255,92,178", w: 5.2, h: 2, side: 0 },
  b_motel: { text: "VACANCY", rgb: "255,160,96", w: 6.4, h: 1.9, side: 0 },
  b_diner: { text: "EATS 24H", rgb: "96,232,255", w: 6.6, h: 1.9, side: 0 },
  b_mall: { text: "OPEN LATE", rgb: "132,222,255", w: 7.2, h: 1.9, side: 0 },
  b_hotel: { text: "SKY BAR", rgb: "255,110,190", w: 5.6, h: 1.8, side: -1 },
  b_warehouse: { text: "FISH + ICE", rgb: "110,255,214", w: 6.8, h: 1.8, side: 0 },
};
const PIER_X = [38, 47, 56];           // the three piers cityArt paints into every bay
// Where this map's six standing figures keep their posts. Static design data (the roster), not live state:
// lanes and junction movements that would run over a post stay closed, so traffic never drives under one of
// them. The two patrolling figures walk the road centre lines, which no lane uses.
const POSTS_BY_MAP: Record<string, ReadonlyArray<{ x: number; z: number }>> = {
  "Coral Bay": [{ x: 40, z: 64 }, { x: 55, z: 45 }, { x: 33, z: 52 }, { x: 62, z: 30 }, { x: 17, z: 31 }, { x: 30, z: 41 }],
};
const CAR_PAINT = ["#3a4072", "#632e66", "#1d6573", "#4b3b99", "#743143", "#716c90", "#2b2e44", "#86603f", "#34634f", "#a09ebb", "#b9ad92", "#44538a"];
const SPORT_PAINT = ["#cf3b82", "#1fb9b9", "#8f52ff", "#e36b3d"];
const PED_PAINT = ["#6f6a96", "#80607c", "#557a85", "#877660", "#617260", "#7a6f9c"];
const DASH_NONE: number[] = [], DASH_ZEBRA = [0.42, 0.42], DASH_CHASE = [0.62, 1.38], DASH_DUAL = [1, 1], DASH_PLAZA = [0.5, 2.2];

function mulberry32(seed: number): () => number {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash01(n: number): number {
  let h = Math.imul(n | 0, 0x9e3779b1);
  h ^= h >>> 15; h = Math.imul(h, 0x85ebca6b); h ^= h >>> 13;
  return (h >>> 0) / 4294967296;
}
const fract = (v: number): number => v - Math.floor(v);

export function createAmbient(map: MapView, seed = 42, options: AmbientOptions = {}): Ambient {
  const rand = mulberry32(seed * 7919 + 17);
  const high = options.quality !== "low";
  let reduced = options.reducedMotion ?? (typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  const posts = options.keepClear ?? POSTS_BY_MAP[map.name] ?? [];
  const traffic = createTraffic(map, seed, options.cars ?? 26, posts);
  const land = traffic.land;
  const W = map.width, H = map.height;
  let t = 0;                       // scenery clock in seconds
  let acc = 0;                     // time not yet consumed by the fixed traffic step
  let vx0 = 0, vx1 = 0, vz0 = 0, vz1 = 0;   // visible world rectangle, refreshed by every draw call

  // ---------------------------------------------------------------- sprites (baked once)
  const glowWarm = bakeGlow("255,228,176", 96, 2);
  const glowRed = bakeGlow("255,56,88", 96, 2);
  const glowAmber = bakeGlow("255,180,64", 64, 2);
  const glowGreen = bakeGlow("72,255,170", 64, 2);
  const glowSoft = bakeGlow("255,255,255", 160, 1.6);
  const toneGlow: Record<Tone, Sprite> = { pink: bakeGlow(RGB.pink), aqua: bakeGlow(RGB.aqua), purple: bakeGlow(RGB.purple), sand: bakeGlow(RGB.sand), teal: bakeGlow(RGB.teal) };
  const hazeGlow = [bakeGlow("255,80,170", 160, 1.5), bakeGlow("120,90,255", 160, 1.5), bakeGlow("60,210,255", 160, 1.5)];
  const beamWarm = bakeBeam("255,234,190");
  const beamCool = bakeBeam("205,232,255", 512, 96, 0.92);
  const beamSky = [bakeBeam("255,120,200", 512, 64, 0.9), bakeBeam("120,220,255", 512, 64, 0.9)];
  const palmSprite = [bakePalm(160, seed + 11), bakePalm(160, seed + 23), bakePalm(160, seed + 37)];
  const sportGlow = [toneGlow.pink, toneGlow.aqua, toneGlow.purple, toneGlow.sand];

  // ---------------------------------------------------------------- buildings: tubes, signs, windows, roof lights
  const B = map.buildings, nb = B.length;
  const bStyle = new Uint8Array(nb), bPhase = new Float32Array(nb), bVis = new Uint8Array(nb);
  const signs: Array<{ sprite: Sprite; glow: Sprite; x: number; z: number; w: number; h: number; b: number }> = [];
  const roofLampX: number[] = [], roofLampZ: number[] = [], roofLampB: number[] = [];
  const winTmp: Array<{ x: number; z: number; b: number; warm: boolean; period: number; phase: number }> = [];
  let pool: { x: number; z: number; w: number; h: number; b: number } | null = null;
  let sky: { x: number; z: number; b: number } | null = null;
  for (let i = 0; i < nb; i++) {
    const b = B[i]!, w = b.x1 - b.x0, h = b.z1 - b.z0;
    bStyle[i] = STYLE_BY_ID[b.id] ?? Math.floor(rand() * 4);
    bPhase[i] = rand() * TAU;
    const inset = Math.min(1.2, w * 0.12);
    const keep: Array<[number, number, number, number]> = [];       // roof areas the twinkling windows must leave alone
    const lines = w < 9 && b.label.includes(" ") ? 2 : 1;
    keep.push([b.x0, (b.z0 + b.z1) / 2 - 0.55 - lines * 0.75, b.x1, (b.z0 + b.z1) / 2 + 0.55 + lines * 0.75]);
    const s = SIGN_BY_ID[b.id];
    if (s && w >= s.w + 2.6 && h >= 9) {
      const x = s.side < 0 ? b.x0 + inset + 0.3 : (b.x0 + b.x1) / 2 - s.w / 2, z = b.z0 + inset + 0.3;
      signs.push({ sprite: bakeSign(s.text, s.rgb, s.w, s.h), glow: toneGlow[b.tone], x, z, w: s.w, h: s.h, b: i });
      keep.push([x - 0.3, z - 0.3, x + s.w + 0.3, z + s.h + 0.3]);
    }
    if (b.id === "b_hotel" && w >= 16) {
      pool = { x: b.x1 - inset - 5.9, z: b.z0 + inset + 0.35, w: 5.5, h: 2.1, b: i };
      keep.push([pool.x - 0.4, pool.z - 0.4, pool.x + pool.w + 0.4, pool.z + pool.h + 0.4]);
    }
    if (b.id === "b_exchange") sky = { x: (b.x0 + b.x1) / 2, z: b.z0 + inset + 1.6, b: i };
    if (b.height >= 10) {
      roofLampX.push(b.x0 + 0.9, b.x1 - 0.9); roofLampZ.push(b.z0 + 0.9, b.z1 - 0.9); roofLampB.push(i, i);
    }
    // same roof grid cityArt paints, so every twinkle lands on a window cell
    const cols = Math.max(1, Math.floor((w - inset * 2) / 2.2)), rows = Math.max(1, Math.floor((h - inset * 2) / 2.2));
    for (let c = 0; c < cols; c++) for (let r = 0; r < rows; r++) {
      const x = b.x0 + inset + (c + 0.25) * ((w - inset * 2) / cols), z = b.z0 + inset + (r + 0.25) * ((h - inset * 2) / rows);
      const pick = rand(), warm = rand() < 0.6, period = 5 + rand() * 13, phase = rand();
      if (pick > 0.46) continue;
      let blocked = false;
      for (const k of keep) if (x + 0.9 > k[0] && x < k[2] && z + 0.9 > k[1] && z < k[3]) blocked = true;
      if (!blocked) winTmp.push({ x, z, b: i, warm, period, phase });
    }
  }
  winTmp.sort((p, q) => (p.warm === q.warm ? p.b - q.b : p.warm ? -1 : 1));   // warm first, then grouped by building
  const nWin = winTmp.length;
  const winX = new Float32Array(nWin), winZ = new Float32Array(nWin), winPeriod = new Float32Array(nWin), winPhase = new Float32Array(nWin);
  const winB = new Int16Array(nWin);
  let nWarm = 0;
  for (let i = 0; i < nWin; i++) {
    const q = winTmp[i]!;
    winX[i] = q.x; winZ[i] = q.z; winB[i] = q.b; winPeriod[i] = q.period; winPhase[i] = q.phase;
    if (q.warm) nWarm++;
  }
  const kiosk = B.find((b) => b.id === "b_kiosk") ?? null;
  const plazaX = kiosk ? (kiosk.x0 + kiosk.x1) / 2 : 0, plazaZ = kiosk ? (kiosk.z0 + kiosk.z1) / 2 : 0;

  // ---------------------------------------------------------------- palms (on cityArt's own lattice, so every palm sways)
  const palmTmp: number[] = [];
  for (const b of B) {
    if (b.x1 - b.x0 <= 6) continue;
    for (let x = b.x0 + 1; x < b.x1; x += 6) for (let side = 0; side < 2; side++) {
      const z = side ? b.z1 + 1.2 : b.z0 - 1.2;
      const r = 1.85 + rand() * 0.4, rot = rand() * TAU, ph = rand() * TAU, kind = Math.floor(rand() * 3);
      if (z > 1 && z < land - 2) palmTmp.push(x, z, r, rot, ph, kind);
    }
  }
  const nPalm = palmTmp.length / 6;
  const palm = new Float32Array(palmTmp);

  // ---------------------------------------------------------------- strollers: closed loops on the pavements
  const pedTmp: number[] = [];
  const density = Math.max(0, options.pedestrians ?? 1);
  const addWalker = (x0: number, z0: number, x1: number, z1: number, dir: number, maxZ: number): void => {
    const a = Math.max(0.6, x0), b = Math.max(0.6, z0), c = Math.min(W - 0.6, x1), d = Math.min(maxZ, z1);
    if (c - a < 0.3 || d - b < 0.3) return;
    pedTmp.push(a, b, c, d, rand() * 2 * (c - a + d - b), dir * (0.85 + rand() * 0.6), Math.floor(rand() * PED_PAINT.length), rand() * TAU);
  };
  const walkers = (x0: number, z0: number, x1: number, z1: number, count: number): void => {
    for (let k = 0; k < count; k++) {
      const outer = rand() < 0.5, o = outer ? 0.62 : 0;
      addWalker(x0 - o, z0 - o, x1 + o, z1 + o, outer ? -1 : 1, land - 0.4);
    }
  };
  for (const b of B) {
    const per = 2 * (b.x1 - b.x0 + b.z1 - b.z0);
    walkers(b.x0 - 0.8, b.z0 - 0.8, b.x1 + 0.8, b.z1 + 0.8, Math.round((per / 24) * density));
  }
  if (kiosk) { walkers(kiosk.x0 - 3.4, kiosk.z0 - 3.4, kiosk.x1 + 3.4, kiosk.z1 + 3.4, Math.round(3 * density)); walkers(kiosk.x0 - 6.6, kiosk.z0 - 6.6, kiosk.x1 + 6.6, kiosk.z1 + 6.6, Math.round(3 * density)); }
  if (map.water.length) {
    walkers(5, land - 1.75, W - 5, land - 1.25, Math.round(9 * density));          // the boardwalk
    // out along each pier and back (the only strollers allowed past the shoreline)
    for (const px of PIER_X) if (px > 4 && px < W - 4) for (let k = 0; k < Math.round(1.4 * density); k++) addWalker(px - 0.4, land - 0.7, px + 0.4, land + 3.7, k % 2 ? -1 : 1, H - 1.2);
  }
  const nPed = pedTmp.length / 8;
  const ped = new Float32Array(pedTmp);

  // ---------------------------------------------------------------- kerbside parked cars on the dead-end stubs by the water
  // Only for maps that define no solid parked cars of their own: where the shared map has real ones (they block
  // players and NPCs), a decorative look-alike that can be walked through would be a lie.
  const parkTmp: number[] = [];
  const keepClear: Array<{ x: number; z: number }> = [...MISSION_SPOTS, ...SPAWN_POINTS.OUTLAW, ...SPAWN_POINTS.HUNTER, ...posts];
  if (traffic.zs.length && !(map.cars && map.cars.length)) {
    const j = traffic.zs.length - 1, zTop = traffic.zs[j]! + traffic.zPos[j]! + 3.6;
    for (let i = 0; i < traffic.xs.length; i++) for (let side = -1; side <= 1; side += 2) {
      const cx = traffic.xs[i]!;
      for (let z = zTop; z < land - 4.4; z += 5.7) {
        let kerb = side < 0 ? 0 : W;
        for (const b of B) {
          if (z < b.z0 - 2 || z > b.z1 + 2) continue;
          const edge = side < 0 ? b.x1 + 2 : b.x0 - 2;
          if (side < 0 ? edge <= cx && edge > kerb : edge >= cx && edge < kerb) kerb = edge;
        }
        if (Math.abs(kerb - cx) > 6.5 || Math.abs(kerb - cx) < 3) continue;
        const x = kerb - side * 1.12;
        const roll = rand(), paint = Math.floor(rand() * CAR_PAINT.length), van = rand() < 0.2;
        if (roll > 0.66) continue;
        let free = true;
        for (const p of keepClear) if (Math.hypot(p.x - x, p.z - z) < 3.4) free = false;
        // Parked cars are solid and come from the shared map (drawn by the static city layer), so no cosmetic ones here.
        void free; void paint; void van;
      }
    }
  }
  const nPark = parkTmp.length / 5;
  const park = new Float32Array(parkTmp);

  // ---------------------------------------------------------------- water: sparkle, neon reflections, boats, lighthouse
  const bay = map.water.length ? map.water[0]! : null;
  const glintTmp: number[] = [], reflTmp: number[] = [];
  const reflTone: Tone[] = [];
  for (const r of map.water) {
    const count = Math.max(8, Math.min(220, Math.round(((r.x1 - r.x0) * (r.z1 - r.z0)) / (high ? 6 : 12))));
    for (let i = 0; i < count; i++) glintTmp.push(r.x0 + rand() * (r.x1 - r.x0), r.z0 + 0.5 + rand() * (r.z1 - r.z0 - 0.9), 0.4 + rand() * 1.5, (rand() - 0.5) * 0.9, rand() * TAU, 0.7 + rand() * 1.9, r.x0, r.x1);
    for (const b of B) {
      if (r.z0 - b.z1 > 9 || r.z0 < b.z1 || b.x1 < r.x0 || b.x0 > r.x1) continue;
      for (const x of [b.x0 + 2.4, (b.x0 + b.x1) / 2, b.x1 - 2.4]) { reflTmp.push(x, r.z0, rand() * TAU, 2.6); reflTone.push(b.tone); }
    }
    for (const px of PIER_X) if (px > r.x0 + 3 && px < r.x1 - 3) { reflTmp.push(px, r.z0 + 4.3, rand() * TAU, 1.5); reflTone.push("sand"); }
  }
  const nGlint = glintTmp.length / 8, nRefl = reflTmp.length / 4;
  const glint = new Float32Array(glintTmp), refl = new Float32Array(reflTmp);
  const pierLamp: number[] = [];
  if (bay) for (const px of PIER_X) if (px > bay.x0 + 3 && px < bay.x1 - 3) pierLamp.push(px, bay.z0 + 1.2, px, bay.z0 + 3.7);
  const moorTmp: number[] = [];
  if (bay && bay.z1 - bay.z0 >= 5) for (const px of PIER_X) for (let side = -1; side <= 1; side += 2) {
    const roll = rand(), ph = rand() * TAU, hue = Math.floor(rand() * 3);
    if (px > bay.x0 + 4 && px < bay.x1 - 4 && roll < 0.7) moorTmp.push(px + side * 1.72, bay.z0 + 2.35, ph, hue);
  }
  const nMoor = moorTmp.length / 4;
  const cruiseZ = bay ? bay.z1 - 0.92 : 0, cruiseSpan = bay ? bay.x1 - bay.x0 + 30 : 1;
  const nCruise = bay && bay.z1 - bay.z0 >= 5 ? 2 : 0;
  const lighthouse = bay && bay.x1 - bay.x0 > 30 && bay.z1 - bay.z0 >= 5 ? { x: bay.x1 - 3.6, z: bay.z0 + 2.4 } : null;

  // ---------------------------------------------------------------- haze and gulls
  const nHaze = high ? 7 : 0;
  const haze = new Float32Array(nHaze * 6);
  for (let i = 0; i < nHaze; i++) {
    haze[i * 6] = rand() * (W + 60); haze[i * 6 + 1] = 6 + rand() * (land - 12); haze[i * 6 + 2] = 17 + rand() * 13;
    haze[i * 6 + 3] = -(0.5 + rand() * 0.9); haze[i * 6 + 4] = rand() * TAU; haze[i * 6 + 5] = i % 3;
  }
  const marina = map.districts.find((d) => d.id === "d_marina");
  const nGull = bay ? 5 : 0;
  const gull = new Float32Array(nGull * 6);
  for (let i = 0; i < nGull; i++) {
    gull[i * 6] = (marina ? marina.x : W / 2) + (rand() - 0.5) * 44; gull[i * 6 + 1] = land - 3 + (rand() - 0.5) * 9;
    gull[i * 6 + 2] = 5 + rand() * 9; gull[i * 6 + 3] = 2.4 + rand() * 3;
    gull[i * 6 + 4] = (rand() < 0.5 ? -1 : 1) * (0.16 + rand() * 0.16); gull[i * 6 + 5] = rand() * TAU;
  }

  // ---------------------------------------------------------------- neon brightness per building (steady when calm)
  const neon = (i: number): number => {
    if (reduced) return 0.86;
    const ph = bPhase[i]!;
    switch (bStyle[i]) {
      case FAULTY: {
        if (Math.sin(t * 0.43 + ph * 3.1) < 0.7) return 0.92;           // healthy most of the time
        const n = hash01(Math.floor(t * 11) + i * 977);
        return n < 0.4 ? 0.2 : n < 0.6 ? 0.55 : 1;
      }
      case BREATHE: return 0.72 + 0.28 * Math.sin(t * 0.9 + ph);
      default: return 0.86 + 0.14 * Math.sin(t * 1.7 + ph);
    }
  };

  // ---------------------------------------------------------------- water
  const drawWater = (ctx: CanvasRenderingContext2D): void => {
    for (let w = 0; w < map.water.length; w++) {
      const r = map.water[w]!;
      if (r.z1 < vz0 || r.z0 > vz1 || r.x1 < vx0 || r.x0 > vx1) continue;
      const xa = Math.max(r.x0, vx0 - 2), xb = Math.min(r.x1, vx1 + 2);
      ctx.save();
      ctx.beginPath(); ctx.rect(r.x0, r.z0, r.x1 - r.x0, r.z1 - r.z0); ctx.clip();
      ctx.globalCompositeOperation = "lighter";
      // moonlit patches sliding along the bay
      for (let k = 0; k < 2; k++) {
        const span = r.x1 - r.x0 + 60;
        const cx = r.x0 - 30 + fract((t * (k ? 0.9 : -0.6)) / span + k * 0.47) * span;
        ctx.globalAlpha = 0.16;
        ctx.drawImage(toneGlow.aqua, cx - 22, r.z0 - 3, 44, r.z1 - r.z0 + 6);
      }
      // long swell lines
      ctx.strokeStyle = "#8feaff"; ctx.lineWidth = 0.09; ctx.globalAlpha = 0.2;
      const rowsOfSwell = Math.max(2, Math.floor((r.z1 - r.z0) / 1.35));
      for (let i = 0; i < rowsOfSwell; i++) {
        const z = r.z0 + 0.95 + i * 1.3;
        ctx.beginPath();
        for (let x = xa; x <= xb + 1.6; x += 1.6) {
          const y = z + Math.sin(x * 0.33 + t * 0.9 + i * 1.7) * 0.17 + Math.sin(x * 0.11 - t * 0.55 + i) * 0.13;
          if (x === xa) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      // surf line breathing against the sand
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 0.13; ctx.globalAlpha = 0.32;
      ctx.beginPath();
      for (let x = xa; x <= xb + 1.2; x += 1.2) {
        const y = r.z0 + 0.34 + Math.sin(x * 0.5 + t * 1.15) * 0.16 + Math.sin(t * 0.7) * 0.1;
        if (x === xa) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      // neon and pier lights smeared down the ripples
      for (let i = 0; i < nRefl; i++) {
        const x = refl[i * 4]!, z0 = refl[i * 4 + 1]!, ph = refl[i * 4 + 2]!, wid = refl[i * 4 + 3]!;
        if (z0 < r.z0 - 0.01 || z0 > r.z1 || x < vx0 - 3 || x > vx1 + 3) continue;
        ctx.fillStyle = HEX[reflTone[i]!];
        for (let k = 0; k < 7; k++) {
          const zz = z0 + 0.45 + k * 0.78;
          if (zz > r.z1 - 0.2) break;
          const ww = wid * (1 - k * 0.09) * (0.55 + 0.45 * Math.sin(t * 1.9 + k * 1.3 + ph));
          const ox = Math.sin(t * 1.1 + k * 0.9 + ph) * 0.32;
          ctx.globalAlpha = 0.34 * (1 - k / 8);
          ctx.fillRect(x - ww / 2 + ox, zz, ww, 0.27);
        }
      }
      // sparkle
      ctx.fillStyle = "#d6f8ff";
      for (let i = 0; i < nGlint; i++) {
        const o = i * 8;
        if (glint[o + 6] !== r.x0) continue;
        const span = glint[o + 7]! - glint[o + 6]!;
        const x = glint[o + 6]! + fract((glint[o]! - glint[o + 6]! + glint[o + 3]! * t) / span) * span;
        if (x < vx0 - 2 || x > vx1 + 2) continue;
        const tw = Math.sin(t * glint[o + 5]! + glint[o + 4]!);
        if (tw <= 0) continue;
        ctx.globalAlpha = tw * tw * tw * 0.75;
        ctx.fillRect(x, glint[o + 1]!, glint[o + 2]!, 0.075);
      }
      ctx.restore();
    }
    if (!bay) return;
    // pier lamps
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < pierLamp.length; i += 2) {
      const x = pierLamp[i]!, z = pierLamp[i + 1]!;
      if (x < vx0 - 4 || x > vx1 + 4 || z < vz0 - 4 || z > vz1 + 4) continue;
      ctx.globalAlpha = reduced ? 0.6 : 0.52 + 0.1 * Math.sin(t * 2.1 + i);
      ctx.drawImage(glowWarm, x - 2.3, z - 2.3, 4.6, 4.6);
    }
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    for (let i = 0; i < nMoor; i++) {
      const x = moorTmp[i * 4]!, ph = moorTmp[i * 4 + 2]!;
      if (x < vx0 - 4 || x > vx1 + 4) continue;
      drawBoat(ctx, x + Math.sin(t * 0.7 + ph) * 0.05, moorTmp[i * 4 + 1]! + Math.sin(t * 0.95 + ph) * 0.09, -Math.PI / 2 + Math.sin(t * 0.6 + ph) * 0.045, 3.5, 1.3, moorTmp[i * 4 + 3]!, false);
    }
    for (let i = 0; i < nCruise; i++) {
      const x = bay.x0 - 15 + fract((t * 2.7) / cruiseSpan + i * 0.5 + 0.18) * cruiseSpan;
      if (x < vx0 - 16 || x > vx1 + 6) continue;
      drawBoat(ctx, x, cruiseZ + Math.sin(t * 0.8 + i * 2) * 0.05, Math.sin(t * 0.5 + i) * 0.025, 4.4, 1.36, i + 1, true);
    }
  };

  const drawBoat = (ctx: CanvasRenderingContext2D, x: number, z: number, a: number, len: number, beam: number, hue: number, moving: boolean): void => {
    const hl = len / 2, hb = beam / 2;
    ctx.save();
    ctx.translate(x, z); ctx.rotate(a);
    if (moving) {
      // wake: two spreading foam lines and a churned strip behind the stern
      ctx.strokeStyle = "#eafcff"; ctx.lineWidth = 0.11; ctx.lineCap = "round";
      for (let k = 0; k < 6; k++) {
        const x0 = -hl - k * 1.5, x1 = x0 - 1.5, y0 = 0.22 + k * 0.1, y1 = y0 + 0.1;
        const wob = Math.sin(t * 3 + k * 1.4) * 0.03;
        ctx.globalAlpha = 0.5 * (1 - k / 6);
        ctx.beginPath(); ctx.moveTo(x0, y0 + wob); ctx.lineTo(x1, y1 - wob); ctx.moveTo(x0, -y0 - wob); ctx.lineTo(x1, -y1 + wob); ctx.stroke();
      }
      ctx.globalAlpha = 0.22; ctx.fillStyle = "#d9f7ff";
      ctx.beginPath(); ctx.moveTo(-hl, -0.2); ctx.lineTo(-hl - 7, -0.05); ctx.lineTo(-hl - 7, 0.05); ctx.lineTo(-hl, 0.2); ctx.closePath(); ctx.fill();
    }
    ctx.globalAlpha = 0.35; ctx.fillStyle = "#02101e";
    ctx.beginPath(); ctx.ellipse(-0.1, 0.22, hl * 1.02, hb * 1.08, 0, 0, TAU); ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#ece9f6";
    ctx.beginPath(); ctx.moveTo(hl, 0); ctx.quadraticCurveTo(hl * 0.45, -hb * 1.05, -hl + 0.25, -hb); ctx.lineTo(-hl, -hb * 0.72); ctx.lineTo(-hl, hb * 0.72); ctx.lineTo(-hl + 0.25, hb); ctx.quadraticCurveTo(hl * 0.45, hb * 1.05, hl, 0); ctx.closePath(); ctx.fill();
    ctx.fillStyle = hue === 0 ? "#2a7f95" : hue === 1 ? "#b0507c" : "#6a55b8";
    ctx.beginPath(); ctx.moveTo(hl * 0.74, 0); ctx.quadraticCurveTo(hl * 0.3, -hb * 0.72, -hl + 0.45, -hb * 0.68); ctx.lineTo(-hl + 0.45, hb * 0.68); ctx.quadraticCurveTo(hl * 0.3, hb * 0.72, hl * 0.74, 0); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#f6f3ff";
    ctx.beginPath(); ctx.roundRect(-hl * 0.42, -hb * 0.46, hl * 0.8, hb * 0.92, 0.16); ctx.fill();
    ctx.fillStyle = "#16233f"; ctx.fillRect(hl * 0.2, -hb * 0.4, 0.2, hb * 0.8);
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = moving ? 0.85 : 0.5;
    ctx.drawImage(glowWarm, -hl - 0.75, -0.75, 1.5, 1.5);
    if (moving) { ctx.globalAlpha = 0.7; ctx.drawImage(glowGreen, hl - 0.9, -hb - 0.5, 1.1, 1.1); ctx.drawImage(glowRed, hl - 0.9, hb - 0.6, 1.1, 1.1); }
    ctx.restore();
  };

  // ---------------------------------------------------------------- glow on the ground around every block
  const drawGroundGlow = (ctx: CanvasRenderingContext2D): void => {
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < nb; i++) {
      if (!bVis[i]) continue;
      const b = B[i]!, w = b.x1 - b.x0, h = b.z1 - b.z0, lum = neon(i);
      ctx.strokeStyle = HEX[b.tone];
      ctx.globalAlpha = 0.05 * lum; ctx.lineWidth = 5; ctx.strokeRect(b.x0, b.z0, w, h);
      ctx.globalAlpha = 0.08 * lum; ctx.lineWidth = 2.4; ctx.strokeRect(b.x0, b.z0, w, h);
      // the same colour mirrored on the wet road south of the block
      if (w < 8 || b.z1 + 6.5 > land) continue;
      ctx.fillStyle = HEX[b.tone];
      for (let k = 0; k < 4; k++) {
        const wob = reduced ? 0 : Math.sin(t * 1.3 + k * 1.1 + bPhase[i]!) * 0.35;
        ctx.globalAlpha = 0.085 * lum * (1 - k / 4.6);
        ctx.fillRect(b.x0 + 0.8 + k * 0.5 + wob, b.z1 + 2.25 + k * 0.95, w - 1.6 - k, 0.5);
      }
    }
    if (kiosk && plazaX > vx0 - 11 && plazaX < vx1 + 11 && plazaZ > vz0 - 11 && plazaZ < vz1 + 11) {
      // bulbs chasing round the plaza rings
      ctx.setLineDash(DASH_PLAZA); ctx.lineCap = "round";
      for (let k = 0; k < 2; k++) {
        const rad = k ? 5 : 9;
        ctx.strokeStyle = k ? "#7fe0ff" : "#ff8cc0";
        ctx.lineDashOffset = reduced ? 0 : (k ? 1 : -1) * t * 1.6;
        ctx.globalAlpha = 0.22; ctx.lineWidth = 0.95; ctx.beginPath(); ctx.arc(plazaX, plazaZ, rad, 0, TAU); ctx.stroke();
        ctx.globalAlpha = 0.9; ctx.lineWidth = 0.3; ctx.beginPath(); ctx.arc(plazaX, plazaZ, rad, 0, TAU); ctx.stroke();
      }
      ctx.setLineDash(DASH_NONE); ctx.lineDashOffset = 0; ctx.lineCap = "butt";
    }
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  };

  // ---------------------------------------------------------------- road paint and signals
  const drawRoadPaint = (ctx: CanvasRenderingContext2D): void => {
    const sl = traffic.stopLines, cw = traffic.crosswalks;
    ctx.strokeStyle = "#e9e8ff";
    ctx.globalAlpha = 0.17; ctx.lineWidth = 1.1; ctx.setLineDash(DASH_ZEBRA);
    ctx.beginPath();
    for (let i = 0; i < cw.length; i += 4) {
      if (cw[i + 2]! < vx0 - 1 || cw[i]! > vx1 + 1 || cw[i + 3]! < vz0 - 1 || cw[i + 1]! > vz1 + 1) continue;
      ctx.moveTo(cw[i]!, cw[i + 1]!); ctx.lineTo(cw[i + 2]!, cw[i + 3]!);
    }
    ctx.stroke();
    ctx.setLineDash(DASH_NONE);
    ctx.globalAlpha = 0.34; ctx.lineWidth = 0.2;
    ctx.beginPath();
    for (let i = 0; i < sl.length; i += 4) {
      const L = i >> 2;
      if (!traffic.linkAlive[L] || !traffic.nodeSignal[traffic.linkNode[L]!]) continue;
      if (Math.max(sl[i]!, sl[i + 2]!) < vx0 || Math.min(sl[i]!, sl[i + 2]!) > vx1 || Math.max(sl[i + 1]!, sl[i + 3]!) < vz0 || Math.min(sl[i + 1]!, sl[i + 3]!) > vz1) continue;
      ctx.moveTo(sl[i]!, sl[i + 1]!); ctx.lineTo(sl[i + 2]!, sl[i + 3]!);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  };

  const drawSignals = (ctx: CanvasRenderingContext2D): void => {
    const n = traffic.linkCount, lx = traffic.lampX, lz = traffic.lampZ;
    ctx.fillStyle = "#0a0a1c";
    for (let L = 0; L < n; L++) {
      if (!traffic.linkAlive[L] || !traffic.nodeSignal[traffic.linkNode[L]!]) continue;
      const x = lx[L]!, z = lz[L]!;
      if (x < vx0 - 2 || x > vx1 + 2 || z < vz0 - 2 || z > vz1 + 2) continue;
      ctx.beginPath(); ctx.roundRect(x - 0.34, z - 0.34, 0.68, 0.68, 0.16); ctx.fill();
    }
    ctx.globalCompositeOperation = "lighter";
    for (let L = 0; L < n; L++) {
      const node = traffic.linkNode[L]!;
      if (!traffic.linkAlive[L] || !traffic.nodeSignal[node]) continue;
      const x = lx[L]!, z = lz[L]!;
      if (x < vx0 - 2 || x > vx1 + 2 || z < vz0 - 2 || z > vz1 + 2) continue;
      const st = traffic.lamp[L]!;
      const sprite = st === SIG_GREEN ? glowGreen : st === SIG_AMBER ? glowAmber : glowRed;
      ctx.globalAlpha = 0.55; ctx.drawImage(sprite, x - 1.5, z - 1.5, 3, 3);
      ctx.globalAlpha = 1; ctx.drawImage(sprite, x - 0.42, z - 0.42, 0.84, 0.84);
    }
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  };

  // ---------------------------------------------------------------- vehicles
  const carShape = (ctx: CanvasRenderingContext2D, kind: number, paint: string, hl: number, hw: number, lit: boolean): void => {
    const L = hl * 2, Wd = hw * 2;
    if (kind === KIND_SCOOTER) {
      ctx.fillStyle = paint; ctx.beginPath(); ctx.roundRect(-hl, -hw * 0.7, L, Wd * 0.7, 0.22); ctx.fill();
      ctx.fillStyle = "#14162a"; ctx.beginPath(); ctx.arc(-0.14, 0, 0.37, 0, TAU); ctx.fill();
      ctx.fillStyle = "#8a85a6"; ctx.beginPath(); ctx.arc(0.02, 0, 0.2, 0, TAU); ctx.fill();
      ctx.fillStyle = lit ? "#fff4cf" : "#6f6a58"; ctx.fillRect(hl - 0.16, -0.13, 0.16, 0.26);
      ctx.fillStyle = lit ? "#ff3b5c" : "#5a1a26"; ctx.fillRect(-hl, -0.12, 0.12, 0.24);
      return;
    }
    ctx.fillStyle = paint;
    ctx.beginPath(); ctx.roundRect(-hl, -hw, L, Wd, kind === KIND_VAN ? 0.3 : 0.52); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.2)"; ctx.lineWidth = 0.07; ctx.stroke();
    ctx.fillStyle = "#090c20";
    if (kind === KIND_VAN) {
      ctx.beginPath(); ctx.roundRect(hl - 1.72, -hw + 0.15, 0.95, Wd - 0.3, 0.16); ctx.fill();
      ctx.fillStyle = "rgba(255,255,255,0.13)"; ctx.fillRect(-hl + 0.28, -hw + 0.2, L - 2.3, Wd - 0.4);
    } else {
      ctx.beginPath(); ctx.roundRect(-hl + L * 0.2, -hw + 0.17, L * 0.53, Wd - 0.34, 0.32); ctx.fill();
      ctx.fillStyle = paint;
      ctx.beginPath(); ctx.roundRect(-hl + L * 0.31, -hw + 0.25, L * 0.27, Wd - 0.5, 0.2); ctx.fill();
      ctx.fillStyle = "rgba(160,215,255,0.3)"; ctx.fillRect(-hl + L * 0.61, -hw + 0.32, 0.1, Wd - 0.64);
    }
    ctx.fillStyle = lit ? "#fff4cf" : "#6f6a58";
    ctx.fillRect(hl - 0.17, -hw + 0.13, 0.15, 0.44); ctx.fillRect(hl - 0.17, hw - 0.57, 0.15, 0.44);
    ctx.fillStyle = lit ? "#ff3b5c" : "#5a1a26";
    ctx.fillRect(-hl + 0.02, -hw + 0.11, 0.13, 0.48); ctx.fillRect(-hl + 0.02, hw - 0.59, 0.13, 0.48);
  };
  const paintOf = (a: number): string => {
    const st = traffic.style[a]!;
    return traffic.kind[a] === KIND_SPORT ? SPORT_PAINT[st % SPORT_PAINT.length]! : CAR_PAINT[st % CAR_PAINT.length]!;
  };

  const drawTraffic = (ctx: CanvasRenderingContext2D): void => {
    const n = traffic.carCount, X = traffic.x, Z = traffic.z, A = traffic.ang;
    // 1. light thrown on the ground: headlight cones never climb the roofs
    ctx.save();
    ctx.beginPath(); ctx.rect(vx0 - 1, vz0 - 1, vx1 - vx0 + 2, vz1 - vz0 + 2);
    for (let i = 0; i < nb; i++) if (bVis[i]) { const b = B[i]!; ctx.rect(b.x0, b.z0, b.x1 - b.x0, b.z1 - b.z0); }
    ctx.clip("evenodd");
    ctx.globalCompositeOperation = "lighter";
    for (let a = 0; a < n; a++) {
      const x = X[a]!, z = Z[a]!;
      if (x < vx0 - 15 || x > vx1 + 15 || z < vz0 - 15 || z > vz1 + 15) continue;
      const c = Math.cos(A[a]!), s = Math.sin(A[a]!), hl = traffic.halfLen[a]!, hw = traffic.halfWid[a]!, kind = traffic.kind[a]!;
      ctx.save(); ctx.transform(c, s, -s, c, x, z);
      if (kind === KIND_SPORT) { ctx.globalAlpha = 0.62; ctx.drawImage(sportGlow[traffic.style[a]! % 4]!, -hl - 1.5, -hw - 1.5, hl * 2 + 3, hw * 2 + 3); }
      if (kind === KIND_SCOOTER) { ctx.globalAlpha = 0.42; ctx.drawImage(beamWarm, hl - 0.3, -2.1, 9.5, 4.2); }
      else { ctx.globalAlpha = 0.5; ctx.drawImage(beamWarm, hl - 0.5, -3.7, 13.5, 7.4); }
      ctx.restore();
    }
    ctx.restore();
    // 2. bodies
    for (let i = 0; i < nPark; i++) {
      const x = park[i * 5]!, z = park[i * 5 + 1]!;
      if (x < vx0 - 4 || x > vx1 + 4 || z < vz0 - 4 || z > vz1 + 4) continue;
      const van = park[i * 5 + 4] === KIND_VAN, hl = van ? 2.55 : 2.12, hw = van ? 0.95 : 0.9, ang = park[i * 5 + 2]!;
      const c = Math.cos(ang), s = Math.sin(ang);
      ctx.save(); ctx.transform(c, s, -s, c, x, z);
      ctx.fillStyle = "rgba(0,0,0,0.4)"; ctx.beginPath(); ctx.roundRect(-hl + 0.3 * c + 0.38 * s, -hw - 0.3 * s + 0.38 * c, hl * 2, hw * 2, 0.5); ctx.fill();
      ctx.globalAlpha = 0.82;
      carShape(ctx, van ? KIND_VAN : KIND_SEDAN, CAR_PAINT[park[i * 5 + 3]! | 0]!, hl, hw, false);
      ctx.restore();
    }
    for (let a = 0; a < n; a++) {
      const x = X[a]!, z = Z[a]!;
      if (x < vx0 - 4 || x > vx1 + 4 || z < vz0 - 4 || z > vz1 + 4) continue;
      const c = Math.cos(A[a]!), s = Math.sin(A[a]!), hl = traffic.halfLen[a]!, hw = traffic.halfWid[a]!;
      ctx.save(); ctx.transform(c, s, -s, c, x, z);
      // the shadow falls south-east whatever the heading
      ctx.fillStyle = "rgba(0,0,0,0.42)"; ctx.beginPath(); ctx.roundRect(-hl + 0.3 * c + 0.38 * s, -hw - 0.3 * s + 0.38 * c, hl * 2, hw * 2, 0.5); ctx.fill();
      carShape(ctx, traffic.kind[a]!, paintOf(a), hl, hw, true);
      ctx.restore();
    }
    // 3. lamps: head, tail (brighter under braking), indicators
    ctx.globalCompositeOperation = "lighter";
    for (let a = 0; a < n; a++) {
      const x = X[a]!, z = Z[a]!;
      if (x < vx0 - 4 || x > vx1 + 4 || z < vz0 - 4 || z > vz1 + 4) continue;
      const c = Math.cos(A[a]!), s = Math.sin(A[a]!), hl = traffic.halfLen[a]!, hw = traffic.halfWid[a]!;
      const br = traffic.brake[a]!, tail = 1.25 + br * 1.25;
      ctx.save(); ctx.transform(c, s, -s, c, x, z);
      if (traffic.kind[a] === KIND_SCOOTER) {
        ctx.globalAlpha = 0.95; ctx.drawImage(glowWarm, hl - 0.8, -0.8, 1.6, 1.6);
        ctx.globalAlpha = 0.55 + 0.45 * br; ctx.drawImage(glowRed, -hl - tail / 2, -tail / 2, tail, tail);
      } else {
        ctx.globalAlpha = 0.95;
        ctx.drawImage(glowWarm, hl - 0.95, -hw - 0.42, 1.7, 1.7); ctx.drawImage(glowWarm, hl - 0.95, hw - 1.28, 1.7, 1.7);
        ctx.globalAlpha = 0.55 + 0.45 * br;
        ctx.drawImage(glowRed, -hl - tail / 2 + 0.1, -hw + 0.35 - tail / 2, tail, tail); ctx.drawImage(glowRed, -hl - tail / 2 + 0.1, hw - 0.35 - tail / 2, tail, tail);
        const side = traffic.blink[a]!;
        if (side !== 0 && (reduced || fract(t * 1.5 + a * 0.37) < 0.55)) {
          ctx.globalAlpha = 0.95;
          ctx.drawImage(glowAmber, hl - 0.75, side * hw - 0.6, 1.2, 1.2); ctx.drawImage(glowAmber, -hl - 0.45, side * hw - 0.6, 1.2, 1.2);
        }
      }
      ctx.restore();
    }
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  };

  // ---------------------------------------------------------------- strollers (small, matte, unlit: never mistaken for an agent)
  const drawPeds = (ctx: CanvasRenderingContext2D): void => {
    for (let i = 0; i < nPed; i++) {
      const o = i * 8;
      const x0 = ped[o]!, z0 = ped[o + 1]!, x1 = ped[o + 2]!, z1 = ped[o + 3]!;
      if (x1 < vx0 - 1 || x0 > vx1 + 1 || z1 < vz0 - 1 || z0 > vz1 + 1) continue;
      const w = x1 - x0, h = z1 - z0, per = 2 * (w + h), sp = ped[o + 5]!;
      const u = fract((ped[o + 4]! + sp * t) / per) * per;
      let x: number, z: number, ang: number;
      if (u < w) { x = x0 + u; z = z0; ang = 0; }
      else if (u < w + h) { x = x1; z = z0 + (u - w); ang = Math.PI / 2; }
      else if (u < 2 * w + h) { x = x1 - (u - w - h); z = z1; ang = Math.PI; }
      else { x = x0; z = z1 - (u - 2 * w - h); ang = -Math.PI / 2; }
      if (x < vx0 - 1 || x > vx1 + 1 || z < vz0 - 1 || z > vz1 + 1) continue;
      const sway = reduced ? 0 : Math.sin(t * 7.5 * Math.abs(sp) + ped[o + 7]!) * 0.22;
      ctx.globalAlpha = 0.3; ctx.fillStyle = "#000000";
      ctx.beginPath(); ctx.ellipse(x + 0.13, z + 0.17, 0.3, 0.2, ang, 0, TAU); ctx.fill();
      ctx.globalAlpha = 0.9; ctx.fillStyle = PED_PAINT[ped[o + 6]! | 0]!;
      ctx.beginPath(); ctx.ellipse(x, z, 0.19, 0.33, ang + sway, 0, TAU); ctx.fill();
      ctx.fillStyle = "#b3a596";
      ctx.beginPath(); ctx.arc(x, z, 0.13, 0, TAU); ctx.fill();
    }
    ctx.globalAlpha = 1;
  };

  // ---------------------------------------------------------------- rooftops
  const drawRoofs = (ctx: CanvasRenderingContext2D): void => {
    ctx.globalCompositeOperation = "lighter";
    // windows coming on and going dark
    ctx.fillStyle = "#ffe7a6";
    let lastB = -1;
    for (let i = 0; i < nWin; i++) {
      const bi = winB[i]!;
      if (!bVis[bi]) continue;
      const u = reduced ? 0.2 : fract(t / winPeriod[i]! + winPhase[i]!);
      if (u > 0.5) continue;
      const a = u < 0.05 ? u / 0.05 : u > 0.45 ? (0.5 - u) / 0.05 : 1;
      if (i >= nWarm && bi !== lastB) { ctx.fillStyle = HEX[B[bi]!.tone]; lastB = bi; }
      ctx.globalAlpha = a * (i >= nWarm ? 0.6 : 0.5);
      ctx.fillRect(winX[i]!, winZ[i]!, 0.9, 0.9);
    }
    // tubes round the roof edge
    for (let i = 0; i < nb; i++) {
      if (!bVis[i]) continue;
      const b = B[i]!, lum = neon(i), style = bStyle[i]!;
      const x = b.x0 + 0.5, z = b.z0 + 0.5, w = b.x1 - b.x0 - 1, h = b.z1 - b.z0 - 1;
      if (style === CHASE) {
        ctx.setLineDash(DASH_CHASE); ctx.lineDashOffset = reduced ? 0 : -t * 3.2;
        ctx.strokeStyle = HEX[b.tone]; ctx.globalAlpha = 0.34 * lum; ctx.lineWidth = 0.85; ctx.strokeRect(x, z, w, h);
        ctx.strokeStyle = HOT[b.tone]; ctx.globalAlpha = lum; ctx.lineWidth = 0.27; ctx.strokeRect(x, z, w, h);
      } else if (style === DUAL) {
        const mix = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.2 + bPhase[i]!);
        ctx.setLineDash(DASH_DUAL); ctx.lineWidth = 0.3;
        ctx.lineDashOffset = 0; ctx.strokeStyle = HOT[b.tone]; ctx.globalAlpha = (0.25 + 0.75 * mix) * lum; ctx.strokeRect(x, z, w, h);
        ctx.lineDashOffset = 1; ctx.strokeStyle = ALT[b.tone]; ctx.globalAlpha = (0.25 + 0.75 * (1 - mix)) * lum; ctx.strokeRect(x, z, w, h);
      } else {
        ctx.setLineDash(DASH_NONE);
        ctx.strokeStyle = HEX[b.tone]; ctx.globalAlpha = 0.3 * lum; ctx.lineWidth = 0.7; ctx.strokeRect(x, z, w, h);
        ctx.strokeStyle = HOT[b.tone]; ctx.globalAlpha = 0.8 * lum; ctx.lineWidth = 0.17; ctx.strokeRect(x, z, w, h);
      }
    }
    ctx.setLineDash(DASH_NONE); ctx.lineDashOffset = 0;
    // slow red beacons on the tall roofs
    for (let i = 0; i < roofLampX.length; i++) {
      if (!bVis[roofLampB[i]!]) continue;
      const p = reduced ? 0.7 : Math.pow(0.5 + 0.5 * Math.sin(t * 1.5 + i * 2.2), 3);
      ctx.globalAlpha = 0.25 + 0.75 * p;
      ctx.drawImage(glowRed, roofLampX[i]! - 0.9, roofLampZ[i]! - 0.9, 1.8, 1.8);
    }
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    // rooftop pool
    if (pool && bVis[pool.b]) {
      const p = pool;
      ctx.fillStyle = "#d9f4ff"; ctx.beginPath(); ctx.roundRect(p.x - 0.22, p.z - 0.22, p.w + 0.44, p.h + 0.44, 0.5); ctx.fill();
      ctx.fillStyle = "#0b93b4"; ctx.beginPath(); ctx.roundRect(p.x, p.z, p.w, p.h, 0.36); ctx.fill();
      ctx.save();
      ctx.beginPath(); ctx.roundRect(p.x, p.z, p.w, p.h, 0.36); ctx.clip();
      ctx.strokeStyle = "#b5fbff"; ctx.lineWidth = 0.09; ctx.globalAlpha = 0.75;
      for (let k = 0; k < 4; k++) {
        ctx.beginPath();
        for (let x = p.x; x <= p.x + p.w + 0.5; x += 0.5) {
          const y = p.z + 0.3 + k * 0.52 + Math.sin(x * 2.1 + t * 1.7 + k * 1.9) * 0.13 + Math.sin(x * 0.9 - t * 1.1 + k) * 0.09;
          if (x === p.x) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.restore();
      ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = 0.5;
      ctx.drawImage(toneGlow.aqua, p.x - 2.2, p.z - 2.2, p.w + 4.4, p.h + 4.4);
      ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    }
    // signs
    for (let i = 0; i < signs.length; i++) {
      const s = signs[i]!;
      if (!bVis[s.b]) continue;
      const lum = neon(s.b);
      ctx.globalAlpha = 0.3 + 0.7 * lum;
      ctx.drawImage(s.sprite, s.x - SIGN_MARGIN, s.z - SIGN_MARGIN, s.w + SIGN_MARGIN * 2, s.h + SIGN_MARGIN * 2);
      ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = 0.3 * lum;
      ctx.drawImage(s.glow, s.x - 2, s.z - 2, s.w + 4, s.h + 4);
      ctx.globalCompositeOperation = "source-over";
    }
    ctx.globalAlpha = 1;
  };

  // ---------------------------------------------------------------- palms
  const drawPalms = (ctx: CanvasRenderingContext2D): void => {
    ctx.fillStyle = "#000000"; ctx.globalAlpha = 0.2;
    for (let i = 0; i < nPalm; i++) {
      const o = i * 6, x = palm[o]!, z = palm[o + 1]!, r = palm[o + 2]!;
      if (x < vx0 - 4 || x > vx1 + 4 || z < vz0 - 4 || z > vz1 + 4) continue;
      const gust = reduced ? 0.3 : 0.5 + 0.5 * Math.sin(t * 0.55 - x * 0.07 + z * 0.05);
      ctx.beginPath(); ctx.arc(x + 0.6 + gust * 0.3, z + 0.72, r * 0.82, 0, TAU); ctx.fill();
    }
    ctx.globalAlpha = 1;
    for (let i = 0; i < nPalm; i++) {
      const o = i * 6, x = palm[o]!, z = palm[o + 1]!, r = palm[o + 2]!, ph = palm[o + 4]!;
      if (x < vx0 - 4 || x > vx1 + 4 || z < vz0 - 4 || z > vz1 + 4) continue;
      let rot = palm[o + 3]!, ox = 0, st = 0;
      if (!reduced) {
        const gust = 0.5 + 0.5 * Math.sin(t * 0.55 - x * 0.07 + z * 0.05);     // gust fronts roll across the city
        rot += 0.05 * Math.sin(t * 1.9 + ph) + gust * 0.11 * Math.sin(t * 1.3 + ph * 1.7);
        ox = (0.08 + 0.26 * gust) * (0.6 + 0.4 * Math.sin(t * 1.5 + ph));
        st = 0.04 * gust * Math.sin(t * 2.3 + ph);
      }
      ctx.save();
      ctx.translate(x + ox, z + ox * 0.35); ctx.rotate(rot); ctx.scale(1 + st, 1 - st * 0.6);
      ctx.drawImage(palmSprite[palm[o + 5]! | 0]!, -r, -r, r * 2, r * 2);
      ctx.restore();
    }
  };

  // ---------------------------------------------------------------- lighthouse
  const drawLighthouse = (ctx: CanvasRenderingContext2D): void => {
    if (!lighthouse) return;
    const x = lighthouse.x, z = lighthouse.z;
    if (x < vx0 - 6 || x > vx1 + 6 || z < vz0 - 6 || z > vz1 + 6) return;
    ctx.fillStyle = "#141a33"; ctx.beginPath(); ctx.ellipse(x + 0.2, z + 0.25, 2.05, 1.6, 0.3, 0, TAU); ctx.fill();
    ctx.fillStyle = "#2a3154"; ctx.beginPath(); ctx.ellipse(x, z, 1.75, 1.35, 0.3, 0, TAU); ctx.fill();
    ctx.fillStyle = "#f3f0ff"; ctx.beginPath(); ctx.arc(x, z, 0.98, 0, TAU); ctx.fill();
    ctx.fillStyle = "#ff7fa6"; ctx.beginPath(); ctx.arc(x, z, 0.72, 0, TAU); ctx.fill();
    ctx.fillStyle = "#f3f0ff"; ctx.beginPath(); ctx.arc(x, z, 0.5, 0, TAU); ctx.fill();
    ctx.fillStyle = "#1a1d36"; ctx.beginPath(); ctx.arc(x, z, 0.33, 0, TAU); ctx.fill();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.38; ctx.drawImage(glowWarm, x - 3.6, z - 3.6, 7.2, 7.2);
    ctx.globalAlpha = 1; ctx.drawImage(glowWarm, x - 0.5, z - 0.5, 1, 1);
    ctx.globalCompositeOperation = "source-over";
  };

  const frame = (ctx: CanvasRenderingContext2D, view: AmbientView): boolean => {
    const sc = view.scale;
    if (!(sc > 0) || !(view.width > 0) || !(view.height > 0)) return false;
    const hw = view.width / (2 * sc), hh = view.height / (2 * sc);
    vx0 = Math.max(0, view.x - hw); vx1 = Math.min(W, view.x + hw); vz0 = Math.max(0, view.z - hh); vz1 = Math.min(H, view.z + hh);
    if (vx1 <= vx0 || vz1 <= vz0) return false;
    ctx.save();
    ctx.translate(view.width / 2 - view.x * sc, view.height / 2 - view.z * sc);
    ctx.scale(sc, sc);
    ctx.beginPath(); ctx.rect(vx0, vz0, vx1 - vx0, vz1 - vz0); ctx.clip();
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over"; ctx.shadowBlur = 0; ctx.setLineDash(DASH_NONE); ctx.lineCap = "butt"; ctx.lineJoin = "miter";
    return true;
  };

  const drawUnder = (ctx: CanvasRenderingContext2D, view: AmbientView): void => {
    if (!frame(ctx, view)) return;
    for (let i = 0; i < nb; i++) { const b = B[i]!; bVis[i] = b.x1 + 7 < vx0 || b.x0 - 7 > vx1 || b.z1 + 7 < vz0 || b.z0 - 7 > vz1 ? 0 : 1; }
    drawWater(ctx);
    drawGroundGlow(ctx);
    drawRoadPaint(ctx);
    drawTraffic(ctx);
    drawSignals(ctx);
    drawPeds(ctx);
    drawRoofs(ctx);
    drawPalms(ctx);
    drawLighthouse(ctx);
    ctx.restore();
  };

  const drawOver = (ctx: CanvasRenderingContext2D, view: AmbientView): void => {
    if (!frame(ctx, view)) return;
    ctx.globalCompositeOperation = "lighter";
    // neon haze drifting on the sea breeze
    for (let i = 0; i < nHaze; i++) {
      const o = i * 6, r = haze[o + 2]!, span = W + 60;
      const x = -30 + fract((haze[o]! + haze[o + 3]! * t) / span) * span;
      const z = haze[o + 1]! + Math.sin(t * 0.07 + haze[o + 4]!) * 5;
      if (x + r < vx0 || x - r > vx1 || z + r * 0.7 < vz0 || z - r * 0.7 > vz1) continue;
      ctx.globalAlpha = 0.085 + 0.03 * Math.sin(t * 0.21 + haze[o + 4]!);
      ctx.drawImage(hazeGlow[haze[o + 5]! | 0]!, x - r, z - r * 0.7, r * 2, r * 1.4);
    }
    // premiere searchlights over the exchange
    if (sky && high && bVis[sky.b]) {
      for (let k = 0; k < 2; k++) {
        const a = (k ? 0.5 : -2.4) + (reduced ? 0 : 0.85 * Math.sin(t * (k ? 0.23 : 0.17) + k * 2));
        ctx.save(); ctx.translate(sky.x + (k ? 5.5 : -5.5), sky.z); ctx.rotate(a);
        ctx.globalAlpha = 0.34; ctx.drawImage(beamSky[k]!, 0, -1.2, 24, 2.4);
        ctx.restore();
        ctx.globalAlpha = 0.9; ctx.drawImage(glowSoft, sky.x + (k ? 5.5 : -5.5) - 0.8, sky.z - 0.8, 1.6, 1.6);
      }
    }
    // the lighthouse sweep
    if (lighthouse) {
      const a = reduced ? -2.5 : -t * 0.42;
      ctx.save(); ctx.translate(lighthouse.x, lighthouse.z); ctx.rotate(a);
      ctx.globalAlpha = 0.5; ctx.drawImage(beamCool, 0.4, -5.2, 58, 10.4);
      ctx.rotate(Math.PI);
      ctx.globalAlpha = 0.2; ctx.drawImage(beamCool, 0.4, -3.4, 30, 6.8);
      ctx.restore();
    }
    ctx.globalCompositeOperation = "source-over";
    // gulls wheeling over the marina
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (let i = 0; i < nGull; i++) {
      const o = i * 6, w = gull[o + 4]!, a = (reduced ? 0 : t) * w + gull[o + 5]!;
      const x = gull[o]! + Math.cos(a) * gull[o + 2]!, z = gull[o + 1]! + Math.sin(a) * gull[o + 3]!;
      if (x < vx0 - 2 || x > vx1 + 2 || z < vz0 - 2 || z > vz1 + 2) continue;
      const hx = -Math.sin(a) * gull[o + 2]! * w, hz = Math.cos(a) * gull[o + 3]! * w, hl = Math.hypot(hx, hz) || 1;
      const dx = hx / hl, dz = hz / hl, flap = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(t * 5.5 + i * 1.9);
      const span = 0.66 + 0.12 * flap, tip = -0.14 - 0.24 * flap;
      for (let pass = 0; pass < 2; pass++) {
        // seen from above: a small swept-wing silhouette, with its shadow on the ground below
        const px = pass ? x : x + 1.1, pz = pass ? z : z + 1.3;
        ctx.fillStyle = pass ? "#f4f8ff" : "#000000"; ctx.globalAlpha = pass ? 0.95 : 0.2;
        ctx.beginPath();
        ctx.moveTo(px + dx * 0.32, pz + dz * 0.32);
        ctx.lineTo(px + dx * 0.1 - dz * span * 0.38, pz + dz * 0.1 + dx * span * 0.38);
        ctx.lineTo(px + dx * tip - dz * span, pz + dz * tip + dx * span);
        ctx.lineTo(px - dx * 0.07 - dz * span * 0.3, pz - dz * 0.07 + dx * span * 0.3);
        ctx.lineTo(px - dx * 0.36, pz - dz * 0.36);
        ctx.lineTo(px - dx * 0.07 + dz * span * 0.3, pz - dz * 0.07 - dx * span * 0.3);
        ctx.lineTo(px + dx * tip + dz * span, pz + dz * tip - dx * span);
        ctx.lineTo(px + dx * 0.1 + dz * span * 0.38, pz + dz * 0.1 - dx * span * 0.38);
        ctx.closePath(); ctx.fill();
      }
    }
    ctx.restore();
  };

  const update = (dtMs: number): void => {
    if (reduced || !(dtMs > 0)) return;
    const dt = Math.min(0.25, dtMs / 1000);
    t += dt; acc += dt;
    let steps = 0;
    while (acc >= TRAFFIC_STEP && steps < 9) { traffic.step(); acc -= TRAFFIC_STEP; steps++; }
    if (acc >= TRAFFIC_STEP) acc = 0;
    traffic.sample(acc / TRAFFIC_STEP);
  };

  return {
    update, drawUnder, drawOver,
    setReducedMotion(next: boolean): void { reduced = next; if (next) traffic.sample(1); },
  };
}

export default createAmbient;
