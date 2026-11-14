// Canvas effects for the crash cinematic. Everything here is a pure function of timeline time `t` (ms since the
// cinematic started): no accumulated simulation state, so a dropped frame never desynchronises the price, the
// chart head and the particles, and any frame can be rendered on demand (preview scrubbing, screenshots).
import type { PricePoint } from "@vice/contracts";

/** Timeline beats in ms. */
export const TL = {
  /** History line starts drawing. */
  draw0: 80,
  /** The price starts falling. */
  fall0: 560,
  /** The price is exactly 0. */
  zero: 2300,
  /** The HALTED plate lands. */
  stamp: 3000,
  /** Fade-out begins. */
  out: 4100,
  /** Unmount. */
  end: 4500,
} as const;

/** Chart band as fractions of the viewport (the stage CSS positions the text around these). */
export const CHART = { x0: 0.05, x1: 0.95, yTop: 0.415, yBase: 0.79, xEdge: 0.745, xZero: 0.83 } as const;

export interface Pt { x: number; y: number }
export interface Box { x: number; y: number; w: number; h: number }
export interface FxInput {
  ticker: string;
  /** Spot price just before the halt. */
  lastSpot: number;
  /** Reference value at capture, when the capture payload was available. */
  reference: number | null;
  /** Real price history up to the halt (may be empty). */
  history: PricePoint[];
}
export interface FxLayout {
  w: number; h: number;
  /** Centre of the ticker symbol. */
  ticker: Pt;
  /** The price readout box. */
  readout: Box;
  /** The stamped plate (unrotated box). */
  plate: Box;
}

interface Step { t0: number; tSlide: number; t1: number; from: number; to: number }
interface Particle {
  shard: boolean; t0: number; life: number; x: number; y: number; vx: number; vy: number; g: number; drag: number;
  size: number; rot: number; spin: number; c: string; tri: [number, number, number, number, number, number];
}
interface Ring { t0: number; x: number; y: number; r: number; dur: number; c: string; width: number }
interface Crack { pts: Pt[]; cum: number[]; total: number; delay: number; width: number }
interface Impulse { at: number; amp: number; decay: number }

export interface FxModel {
  w: number; h: number; scale: number; last: number; pMax: number;
  x0: number; x1: number; yTop: number; yBase: number; xEdge: number; xZero: number;
  hist: Pt[]; ref: Pt[]; refY: number | null; reference: number | null;
  ticks: number[];
  steps: Step[]; particles: Particle[]; rings: Ring[]; cracks: Crack[]; impulses: Impulse[];
}

const RED = "255,84,104", PINK = "255,79,163", AQUA = "47,243,224", PURPLE = "165,107,255", WHITE = "255,238,243", AMBER = "255,179,71";

export const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v);
export const easeOutCubic = (u: number): number => 1 - Math.pow(1 - clamp01(u), 3);
export const easeInCubic = (u: number): number => Math.pow(clamp01(u), 3);
export const easeOutBack = (u: number): number => { const c = 1.70158, x = clamp01(u) - 1; return 1 + (c + 1) * x * x * x + c * x * x; };

/** Small deterministic PRNG (mulberry32): the same ticker always crashes the same way. */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
/** Stateless hash noise in [0,1) for per-frame glitch decisions. */
export function noise(a: number, b: number): number {
  let h = (Math.imul(a | 0, 374761393) + Math.imul(b | 0, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function niceTicks(max: number): number[] {
  const raw = max / 4;
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  const m = raw / pow;
  const step = Math.max(1, (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * pow);
  const out: number[] = [];
  for (let v = step; v < max * 0.985; v += step) out.push(Math.round(v));
  return out;
}

/** The fall is a staircase of violent lurches: long ledges at first, a machine-gun collapse at the end. */
function buildSteps(last: number, rand: () => number): Step[] {
  const n = 13 + Math.floor(rand() * 4);
  const span = TL.zero - TL.fall0;
  const bounds: number[] = [TL.fall0];
  for (let k = 1; k <= n; k++) bounds.push(TL.fall0 + span * (1 - Math.pow(1 - k / n, 2)));
  bounds[n] = TL.zero;
  const values: number[] = [last];
  for (let k = 1; k < n; k++) {
    const base = 0.8 * (1 - Math.pow((k - 1) / (n - 1), 1.35));
    const v = last * Math.max(0, base + (rand() - 0.5) * 0.05);
    values.push(Math.max(Math.min(1, last), Math.min(values[k - 1]!, v)));
  }
  values.push(0);
  const steps: Step[] = [];
  for (let k = 1; k <= n; k++) {
    const t0 = bounds[k - 1]!, t1 = bounds[k]!;
    steps.push({ t0, t1, tSlide: k === n ? t1 : t0 + (t1 - t0) * 0.42, from: values[k - 1]!, to: values[k]! });
  }
  return steps;
}

/** Continuous price along the fall. Exactly `last` before the fall and exactly 0 from TL.zero on. */
export function valueAt(m: Pick<FxModel, "steps" | "last">, t: number): number {
  if (t <= TL.fall0) return m.last;
  if (t >= TL.zero) return 0;
  for (const s of m.steps) {
    if (t >= s.t1) continue;
    if (t >= s.tSlide) return s.to;
    return s.from + (s.to - s.from) * easeOutCubic((t - s.t0) / Math.max(1, s.tSlide - s.t0));
  }
  return 0;
}
/** The integer shown in the readout: never 0 before the halt beat, exactly 0 from it on. */
export function priceAt(m: Pick<FxModel, "steps" | "last">, t: number): number {
  if (t >= TL.zero) return 0;
  return Math.max(Math.min(1, Math.round(m.last)), Math.round(valueAt(m, t)));
}

function cliffX(m: FxModel, t: number): number {
  return m.xEdge + (m.xZero - m.xEdge) * clamp01((t - TL.fall0) / (TL.zero - TL.fall0));
}
function priceY(m: Pick<FxModel, "yBase" | "yTop" | "pMax">, p: number): number {
  return m.yBase - (p / m.pMax) * (m.yBase - m.yTop);
}
function headAt(m: FxModel, t: number): Pt {
  return { x: cliffX(m, t), y: priceY(m, valueAt(m, t)) };
}

export function buildFx(input: FxInput, layout: FxLayout): FxModel {
  const { w, h } = layout;
  const rand = seeded(hashString(input.ticker) ^ 0x9e3779b9);
  const scale = Math.max(0.6, Math.min(w / 1600, h / 900) * 1.0);
  const last = Math.max(0, input.lastSpot);
  const pts = input.history.filter((p) => p.spot > 0 && Number.isFinite(p.t));
  // The axis follows the spot price only, so the fall always uses the full height of the chart. The reference
  // value usually sits above the spot (heat discounts it); where it leaves the band it fades out and is labelled.
  let pMax = Math.max(last, 1);
  for (const p of pts) pMax = Math.max(pMax, p.spot);
  pMax *= 1.14;

  const g = {
    x0: w * CHART.x0, x1: w * CHART.x1, yTop: h * CHART.yTop, yBase: h * CHART.yBase,
    xEdge: w * CHART.xEdge, xZero: w * CHART.xZero, pMax,
  };
  const Y = (p: number): number => priceY(g, p);
  let hist: Pt[]; let ref: Pt[] = [];
  if (pts.length >= 2) {
    const t0 = pts[0]!.t, span = Math.max(1, pts[pts.length - 1]!.t - t0);
    const X = (t: number): number => g.x0 + (g.xEdge - g.x0) * 0.985 * ((t - t0) / span);
    hist = pts.map((p) => ({ x: X(p.t), y: Y(p.spot) }));
    ref = pts.map((p) => ({ x: X(p.t), y: Y(p.reference) }));
    hist.push({ x: g.xEdge, y: Y(last) });
    ref.push({ x: g.xEdge, y: Y(input.reference ?? pts[pts.length - 1]!.reference) });
  } else {
    // No recorded history: draw only what is known for certain, a flat line at the last price.
    hist = [{ x: g.x0, y: Y(last) }, { x: g.xEdge, y: Y(last) }];
    if (input.reference != null) ref = [{ x: g.x0, y: Y(input.reference) }, { x: g.xEdge, y: Y(input.reference) }];
  }

  const steps = buildSteps(last, rand);
  const m: FxModel = {
    w, h, scale, last, ...g, hist, ref,
    refY: ref.length ? ref[ref.length - 1]!.y : null, reference: input.reference,
    ticks: niceTicks(pMax), steps, particles: [], rings: [], cracks: [], impulses: [],
  };

  // ---- particles ----
  const P = m.particles;
  const tri = (): Particle["tri"] => {
    const a = rand() * Math.PI * 2;
    const p = (k: number): [number, number] => { const ang = a + k * 2.1 + (rand() - 0.5) * 0.9, r = 0.55 + rand() * 0.6; return [Math.cos(ang) * r, Math.sin(ang) * r]; };
    const [a0, a1] = p(0), [b0, b1] = p(1), [c0, c1] = p(2);
    return [a0, a1, b0, b1, c0, c1];
  };
  const add = (shard: boolean, t0: number, life: number, x: number, y: number, ang: number, speed: number, grav: number, drag: number, size: number, c: string): void => {
    P.push({ shard, t0, life, x, y, vx: Math.cos(ang) * speed * scale, vy: Math.sin(ang) * speed * scale, g: grav * scale, drag, size: size * scale, rot: rand() * 6.28, spin: (rand() - 0.5) * 16, c, tri: tri() });
  };
  const pick = (...cs: string[]): string => cs[Math.floor(rand() * cs.length)]!;
  const rd = layout.readout, rc = { x: rd.x + rd.w / 2, y: rd.y + rd.h / 2 };

  // 1. impact burst from the ticker
  for (let i = 0; i < 90; i++) add(false, 0, 420 + rand() * 520, layout.ticker.x + (rand() - 0.5) * w * 0.3, layout.ticker.y + (rand() - 0.5) * h * 0.08, rand() * 6.283, 500 + rand() * 1900, 500, 3.2, 1.4 + rand() * 2, pick(WHITE, PINK, RED, PINK));
  // 2. sparks torn off the falling head
  for (let i = 0; i < 120; i++) {
    const t0 = TL.fall0 + rand() * (TL.zero - TL.fall0);
    const hd = headAt(m, t0);
    add(false, t0, 280 + rand() * 420, hd.x, hd.y, -Math.PI / 2 + (rand() - 0.5) * 2.3, 160 + rand() * 620, 900, 1.6, 1.2 + rand() * 1.8, pick(RED, AMBER, WHITE, RED));
  }
  // 3. the readout sheds shards with every lurch
  for (const s of steps.slice(0, -1)) {
    const n = 3 + Math.round(9 * ((s.from - s.to) / Math.max(1, last)));
    for (let i = 0; i < n; i++) {
      const x = rd.x + rand() * rd.w, y = rd.y + rand() * rd.h;
      add(true, s.t0 + rand() * 40, 650 + rand() * 500, x, y, Math.atan2(y - rc.y + rd.h * 0.2, x - rc.x) + (rand() - 0.5) * 0.9, 220 + rand() * 560, 1500, 0.9, 5 + rand() * 9, pick(RED, PINK, WHITE, RED));
    }
  }
  // 4. zero: the readout shatters, the line splashes on the floor
  for (let i = 0; i < 170; i++) {
    const x = rd.x + rand() * rd.w, y = rd.y + rand() * rd.h;
    add(true, TL.zero + rand() * 30, 900 + rand() * 700, x, y, Math.atan2(y - rc.y - rd.h * 0.25, x - rc.x) + (rand() - 0.5) * 0.7, 380 + rand() * 1500, 1700, 1.1, 6 + rand() * 17, pick(RED, RED, PINK, WHITE, AQUA));
  }
  for (let i = 0; i < 80; i++) add(false, TL.zero + rand() * 40, 450 + rand() * 500, m.xZero + (rand() - 0.5) * w * 0.02, m.yBase, -Math.PI / 2 + (rand() - 0.5) * 2.5, 400 + rand() * 1400, 1500, 1.4, 1.6 + rand() * 2.2, pick(RED, WHITE, AMBER, PINK));
  // 5. dust off the plate when it lands
  const pl = layout.plate;
  for (let i = 0; i < 70; i++) {
    const side = Math.floor(rand() * 4), u = rand();
    const x = side === 0 ? pl.x + u * pl.w : side === 1 ? pl.x + pl.w : side === 2 ? pl.x + u * pl.w : pl.x;
    const y = side === 0 ? pl.y : side === 1 ? pl.y + u * pl.h : side === 2 ? pl.y + pl.h : pl.y + u * pl.h;
    const ang = [-Math.PI / 2, 0, Math.PI / 2, Math.PI][side]! + (rand() - 0.5) * 1.2;
    add(i % 5 === 0, TL.stamp + rand() * 30, 320 + rand() * 420, x, y, ang, 220 + rand() * 760, 300, 4, i % 5 === 0 ? 4 + rand() * 7 : 1.3 + rand() * 1.8, pick(RED, WHITE, RED, PINK));
  }

  // ---- shockwaves ----
  const diag = Math.hypot(w, h);
  m.rings.push(
    { t0: 0, x: layout.ticker.x, y: layout.ticker.y, r: diag * 0.62, dur: 720, c: PINK, width: 12 },
    { t0: 60, x: layout.ticker.x, y: layout.ticker.y, r: diag * 0.4, dur: 620, c: WHITE, width: 4 },
    { t0: TL.zero, x: rc.x, y: rc.y, r: diag * 0.7, dur: 820, c: RED, width: 16 },
    { t0: TL.zero + 110, x: rc.x, y: rc.y, r: diag * 0.5, dur: 900, c: WHITE, width: 4 },
    { t0: TL.zero, x: m.xZero, y: m.yBase, r: diag * 0.2, dur: 520, c: AMBER, width: 6 },
    { t0: TL.stamp, x: pl.x + pl.w / 2, y: pl.y + pl.h / 2, r: diag * 0.36, dur: 520, c: RED, width: 9 },
  );

  // ---- cracks radiating from the readout ----
  const poly = (pts: Pt[], delay: number, width: number): Crack => {
    const cum = [0];
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1]! + Math.hypot(pts[i]!.x - pts[i - 1]!.x, pts[i]!.y - pts[i - 1]!.y));
    return { pts, cum, total: cum[cum.length - 1]!, delay, width };
  };
  const rays: Pt[][] = [];
  const nRays = 12, base = rand() * 6.283;
  for (let i = 0; i < nRays; i++) {
    let ang = base + (i / nRays) * 6.283 + (rand() - 0.5) * 0.34;
    const len = diag * (0.2 + rand() * 0.36), segs = 7;
    // Start on the readout's edge so the fracture seems to burst out of the number.
    let x = rc.x + Math.cos(ang) * rd.w * 0.26, y = rc.y + Math.sin(ang) * rd.h * 0.3;
    const pts: Pt[] = [{ x, y }];
    for (let k = 0; k < segs; k++) {
      ang += (rand() - 0.5) * 0.55;
      const l = (len / segs) * (0.6 + rand() * 0.8);
      x += Math.cos(ang) * l; y += Math.sin(ang) * l;
      pts.push({ x, y });
      if (k >= 1 && k <= 4 && rand() < 0.42) {
        let ba = ang + (rand() < 0.5 ? -1 : 1) * (0.5 + rand() * 0.5), bx = x, by = y;
        const bp: Pt[] = [{ x: bx, y: by }];
        for (let j = 0; j < 3; j++) { ba += (rand() - 0.5) * 0.5; const bl = (len / 11) * (0.6 + rand() * 0.8); bx += Math.cos(ba) * bl; by += Math.sin(ba) * bl; bp.push({ x: bx, y: by }); }
        m.cracks.push(poly(bp, 60 + k * 14, 0.8));
      }
    }
    rays.push(pts);
    m.cracks.push(poly(pts, rand() * 30, 1.25));
  }
  // chords between neighbouring rays: the spider-web look of struck glass
  for (const idx of [2, 4]) {
    for (let i = 0; i < nRays; i++) {
      if (rand() < 0.3) continue;
      const a = rays[i]![idx]!, b = rays[(i + 1) % nRays]![idx]!;
      m.cracks.push(poly([a, { x: (a.x + b.x) / 2 + (rand() - 0.5) * 14 * scale, y: (a.y + b.y) / 2 + (rand() - 0.5) * 14 * scale }, b], 70 + idx * 16, 0.75));
    }
  }

  // ---- screen-shake impulses ----
  m.impulses.push({ at: 0, amp: 30, decay: 150 });
  for (const s of steps) m.impulses.push({ at: s.t0, amp: 2 + 22 * ((s.from - s.to) / Math.max(1, last)), decay: 70 });
  m.impulses.push({ at: TL.zero, amp: 42, decay: 190 }, { at: TL.stamp, amp: 22, decay: 110 });
  return m;
}

/** Raw impact energy (sum of decaying impulses). Drives chromatic split and glitch intensity. */
export function energyAt(m: Pick<FxModel, "impulses">, t: number): number {
  let a = 0;
  for (const i of m.impulses) if (t >= i.at) a += i.amp * Math.exp(-(t - i.at) / i.decay);
  return a;
}

/** Screen-shake offset in px and degrees. */
export function shakeAt(m: Pick<FxModel, "impulses" | "scale">, t: number): { x: number; y: number; r: number } {
  let a = 0;
  for (const i of m.impulses) if (t >= i.at) a += i.amp * Math.exp(-(t - i.at) / i.decay);
  if (a < 0.05) return { x: 0, y: 0, r: 0 };
  a *= m.scale;
  return {
    x: a * (Math.sin(t * 0.113 + 1.7) * 0.6 + Math.sin(t * 0.071) * 0.4),
    y: a * (Math.cos(t * 0.097 + 0.4) * 0.6 + Math.sin(t * 0.059 + 2.1) * 0.4),
    r: a * 0.014 * Math.sin(t * 0.083),
  };
}

/** Full-screen flash: two beats in the whole cinematic (impact, zero) plus a faint one under the stamp. */
export function flashAt(t: number): { alpha: number; rgb: string } {
  const a0 = t >= 0 ? 0.92 * Math.exp(-t / 95) : 0;
  const a1 = t >= TL.zero ? 0.6 * Math.exp(-(t - TL.zero) / 130) : 0;
  const a2 = t >= TL.stamp ? 0.2 * Math.exp(-(t - TL.stamp) / 80) : 0;
  const alpha = Math.min(1, a0 + a1 + a2);
  return { alpha: alpha < 0.004 ? 0 : alpha, rgb: a0 >= a1 && a0 >= a2 ? "255,214,226" : RED };
}

// ------------------------------------------------------------------------------------------------ drawing
function trace(ctx: CanvasRenderingContext2D, pts: Pt[]): void {
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) { const p = pts[i]!; if (i) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y); }
}
function glowStroke(ctx: CanvasRenderingContext2D, pts: Pt[], main: string | CanvasGradient, glowRgb: string, width: number, alpha = 1): void {
  if (pts.length < 2 || alpha <= 0) return;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  trace(ctx, pts);
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(${glowRgb},${0.07 * alpha})`; ctx.lineWidth = width * 8; ctx.stroke();
  ctx.strokeStyle = `rgba(${glowRgb},${0.16 * alpha})`; ctx.lineWidth = width * 3.6; ctx.stroke();
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = alpha; ctx.strokeStyle = main; ctx.lineWidth = width; ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.lineWidth = Math.max(1, width * 0.3); ctx.stroke();
  ctx.globalAlpha = 1;
}
const mix = (a: string, b: string, k: number): string => {
  const x = a.split(",").map(Number), y = b.split(",").map(Number);
  return [0, 1, 2].map((i) => Math.round(x[i]! + (y[i]! - x[i]!) * k)).join(",");
};

function drawGrid(ctx: CanvasRenderingContext2D, m: FxModel, t: number): void {
  const a = clamp01((t - 40) / 320);
  if (a <= 0) return;
  const fs = Math.max(13, Math.round(15 * m.scale));
  ctx.font = `700 ${fs}px ui-monospace, "SF Mono", Menlo, Consolas, monospace`;
  ctx.textBaseline = "middle"; ctx.textAlign = "left";
  ctx.lineWidth = 1;
  for (const v of m.ticks) {
    const y = Math.round(priceY(m, v)) + 0.5;
    ctx.strokeStyle = `rgba(255,255,255,${0.075 * a})`;
    ctx.beginPath(); ctx.moveTo(m.x0, y); ctx.lineTo(m.x1, y); ctx.stroke();
    ctx.fillStyle = `rgba(214,190,214,${0.62 * a})`;
    ctx.fillText(`V$${v.toLocaleString("en-US")}`, m.x0 + 4 * m.scale, y - fs * 0.78);
  }
  const cols = 14;
  for (let i = 0; i <= cols; i++) {
    const x = Math.round(m.x0 + ((m.x1 - m.x0) * i) / cols) + 0.5;
    ctx.strokeStyle = `rgba(255,255,255,${0.035 * a})`;
    ctx.beginPath(); ctx.moveTo(x, m.yTop); ctx.lineTo(x, m.yBase); ctx.stroke();
  }
  // The floor: V$0.
  const hit = t >= TL.zero ? Math.exp(-(t - TL.zero) / 420) : 0;
  ctx.strokeStyle = `rgba(${RED},${(0.5 + 0.5 * hit) * a})`; ctx.lineWidth = Math.max(1.5, 2 * m.scale);
  ctx.beginPath(); ctx.moveTo(m.x0, m.yBase); ctx.lineTo(m.x1, m.yBase); ctx.stroke();
  if (hit > 0.01) {
    ctx.globalCompositeOperation = "lighter";
    const gr = ctx.createLinearGradient(0, m.yBase - 90 * m.scale, 0, m.yBase);
    gr.addColorStop(0, `rgba(${RED},0)`); gr.addColorStop(1, `rgba(${RED},${0.5 * hit})`);
    ctx.fillStyle = gr; ctx.fillRect(m.x0, m.yBase - 90 * m.scale, m.x1 - m.x0, 90 * m.scale);
    ctx.globalCompositeOperation = "source-over";
  }
  ctx.fillStyle = `rgba(${RED},${0.95 * a})`;
  ctx.fillText("V$0", m.x0 + 4 * m.scale, m.yBase - fs * 0.78);
}

function drawChart(ctx: CanvasRenderingContext2D, m: FxModel, t: number): void {
  const reveal = easeOutCubic((t - TL.draw0) / (TL.fall0 - TL.draw0 - 40));
  if (reveal <= 0) return;
  const xr = m.x0 + (m.xEdge - m.x0) * reveal + 1;
  const bleed = clamp01((t - TL.fall0) / (TL.zero - TL.fall0));
  const lw = Math.max(2.6, 3.8 * m.scale);
  const lineRgb = mix(AQUA, RED, clamp01(bleed * 1.25));

  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, xr, m.h); ctx.clip();
  // area under the history
  const fill = ctx.createLinearGradient(0, m.yTop, 0, m.yBase);
  fill.addColorStop(0, `rgba(${lineRgb},.26)`); fill.addColorStop(1, `rgba(${lineRgb},0)`);
  trace(ctx, m.hist);
  ctx.lineTo(m.hist[m.hist.length - 1]!.x, m.yBase); ctx.lineTo(m.hist[0]!.x, m.yBase); ctx.closePath();
  ctx.fillStyle = fill; ctx.fill();
  // reference value: dashed purple, like the market panel; it fades where it climbs out of the band
  if (m.ref.length >= 2) {
    const band = m.yBase - m.yTop;
    const rg = ctx.createLinearGradient(0, m.yTop - band * 0.04, 0, m.yTop + band * 0.16);
    rg.addColorStop(0, `rgba(${PURPLE},0)`); rg.addColorStop(1, `rgba(${PURPLE},.9)`);
    ctx.save();
    ctx.beginPath(); ctx.rect(0, m.yTop - band * 0.04, xr, band * 1.04); ctx.clip();
    ctx.setLineDash([10 * m.scale, 8 * m.scale]);
    trace(ctx, m.ref);
    ctx.strokeStyle = rg; ctx.lineWidth = Math.max(1.5, 2 * m.scale); ctx.lineJoin = "round"; ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }
  // spot history: the red bleeds backwards from the cliff as the price falls
  const grad = ctx.createLinearGradient(m.x0, 0, m.xEdge, 0);
  const cut = clamp01(1 - bleed * 1.3);
  grad.addColorStop(0, `rgb(${mix(AQUA, RED, clamp01(bleed * 1.6 - 0.6))})`);
  grad.addColorStop(cut, `rgb(${mix(AQUA, RED, clamp01(bleed * 1.6 - 0.6))})`);
  grad.addColorStop(1, `rgb(${lineRgb})`);
  glowStroke(ctx, m.hist, grad, lineRgb, lw);
  ctx.restore();

  if (m.refY != null && m.reference != null && reveal >= 1) {
    const a = clamp01((t - TL.fall0 + 60) / 200);
    const fs = Math.max(14, Math.round(16 * m.scale));
    const inBand = m.refY >= m.yTop + fs * 1.6;
    ctx.font = `800 ${fs}px ui-monospace, "SF Mono", Menlo, Consolas, monospace`;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    if (inBand) {
      ctx.setLineDash([4 * m.scale, 7 * m.scale]);
      ctx.strokeStyle = `rgba(${PURPLE},${0.45 * a})`; ctx.lineWidth = Math.max(1, 1.5 * m.scale);
      ctx.beginPath(); ctx.moveTo(m.xEdge, m.refY); ctx.lineTo(m.x1, m.refY); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.fillStyle = `rgba(${PURPLE},${a})`;
    const label = `REFERENCE V$${Math.round(m.reference).toLocaleString("en-US")}`;
    // Above the band the reference is off this chart's scale: point up rather than draw it in the wrong place.
    ctx.fillText(inBand ? label : `${label} ▲`, m.x1, inBand ? m.refY - fs * 0.9 : m.yTop + fs * 0.5);
    ctx.textAlign = "left";
  }
  if (t <= TL.fall0) {
    if (reveal < 1) return;
    const hd = m.hist[m.hist.length - 1]!;
    drawHead(ctx, hd, AQUA, 22 * m.scale);
    return;
  }

  // ---- the cliff ----
  const pts: Pt[] = [{ x: m.xEdge, y: priceY(m, m.last) }];
  for (const s of m.steps) {
    if (t <= s.t0) break;
    const end = Math.min(t, s.tSlide);
    for (let j = 1; j <= 4; j++) { const tt = s.t0 + ((end - s.t0) * j) / 4; pts.push({ x: cliffX(m, tt), y: priceY(m, valueAt(m, tt)) }); }
    if (t > s.tSlide) pts.push({ x: cliffX(m, Math.min(t, s.t1)), y: priceY(m, s.to) });
  }
  // red wash under the cliff
  const wash = ctx.createLinearGradient(0, m.yTop, 0, m.yBase);
  wash.addColorStop(0, `rgba(${RED},.34)`); wash.addColorStop(1, `rgba(${RED},.02)`);
  trace(ctx, pts);
  ctx.lineTo(pts[pts.length - 1]!.x, m.yBase); ctx.lineTo(m.xEdge, m.yBase); ctx.closePath();
  ctx.fillStyle = wash; ctx.fill();
  glowStroke(ctx, pts, `rgb(${RED})`, RED, lw * 1.35);

  if (t < TL.zero) {
    drawHead(ctx, pts[pts.length - 1]!, RED, 30 * m.scale);
    return;
  }
  // ---- flatline ----
  const k = easeOutCubic((t - TL.zero) / 520);
  const xf = m.xZero + (m.x1 - m.xZero) * k;
  glowStroke(ctx, [{ x: m.xZero, y: m.yBase }, { x: xf, y: m.yBase }], `rgb(${RED})`, RED, lw * 1.35);
  const pulse = 0.55 + 0.45 * Math.exp(-(t - TL.zero) / 600);
  drawHead(ctx, { x: xf, y: m.yBase }, RED, 34 * m.scale * pulse);
}

function drawHead(ctx: CanvasRenderingContext2D, p: Pt, rgb: string, r: number): void {
  ctx.globalCompositeOperation = "lighter";
  const gr = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
  gr.addColorStop(0, "rgba(255,255,255,.95)"); gr.addColorStop(0.18, `rgba(${rgb},.85)`); gr.addColorStop(1, `rgba(${rgb},0)`);
  ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
  ctx.globalCompositeOperation = "source-over";
}

function drawCracks(ctx: CanvasRenderingContext2D, m: FxModel, t: number): void {
  if (t < TL.zero) return;
  const fade = 0.92 * (1 - 0.45 * clamp01((t - TL.zero) / 1900));
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  for (const c of m.cracks) {
    const f = easeOutCubic((t - TL.zero - c.delay) / 190);
    if (f <= 0) continue;
    const lim = c.total * f;
    ctx.beginPath();
    ctx.moveTo(c.pts[0]!.x, c.pts[0]!.y);
    for (let i = 1; i < c.pts.length; i++) {
      const a = c.pts[i - 1]!, b = c.pts[i]!;
      if (c.cum[i]! <= lim) { ctx.lineTo(b.x, b.y); continue; }
      const u = (lim - c.cum[i - 1]!) / Math.max(1e-6, c.cum[i]! - c.cum[i - 1]!);
      if (u > 0) ctx.lineTo(a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u);
      break;
    }
    ctx.globalCompositeOperation = "lighter";
    ctx.strokeStyle = `rgba(${PINK},${0.2 * fade})`; ctx.lineWidth = 6 * m.scale * c.width; ctx.stroke();
    ctx.globalCompositeOperation = "source-over";
    ctx.strokeStyle = `rgba(255,255,255,${0.86 * fade})`; ctx.lineWidth = Math.max(1, 1.5 * m.scale * c.width); ctx.stroke();
  }
}

function drawRings(ctx: CanvasRenderingContext2D, m: FxModel, t: number): void {
  ctx.globalCompositeOperation = "lighter";
  for (const r of m.rings) {
    const u = (t - r.t0) / r.dur;
    if (u <= 0 || u >= 1) continue;
    const rad = r.r * easeOutCubic(u), a = Math.pow(1 - u, 1.6);
    ctx.strokeStyle = `rgba(${r.c},${0.85 * a})`; ctx.lineWidth = Math.max(1, r.width * m.scale * (1 - u * 0.75));
    ctx.beginPath(); ctx.arc(r.x, r.y, rad, 0, 6.283); ctx.stroke();
    ctx.strokeStyle = `rgba(${r.c},${0.18 * a})`; ctx.lineWidth = r.width * m.scale * 4 * (1 - u * 0.5);
    ctx.beginPath(); ctx.arc(r.x, r.y, rad, 0, 6.283); ctx.stroke();
  }
  ctx.globalCompositeOperation = "source-over";
}

function drawParticles(ctx: CanvasRenderingContext2D, m: FxModel, t: number): void {
  ctx.lineCap = "round";
  for (const p of m.particles) {
    const ms = t - p.t0;
    if (ms <= 0 || ms >= p.life) continue;
    const s = ms / 1000, k = (1 - Math.exp(-p.drag * s)) / p.drag, damp = Math.exp(-p.drag * s);
    const x = p.x + p.vx * k, y = p.y + p.vy * k + 0.5 * p.g * s * s;
    const a = Math.pow(1 - ms / p.life, p.shard ? 0.9 : 1.5);
    if (p.shard) {
      const ang = p.rot + p.spin * s, cs = Math.cos(ang) * p.size, sn = Math.sin(ang) * p.size, q = p.tri;
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = `rgba(${p.c},${0.9 * a})`;
      ctx.beginPath();
      ctx.moveTo(x + q[0] * cs - q[1] * sn, y + q[0] * sn + q[1] * cs);
      ctx.lineTo(x + q[2] * cs - q[3] * sn, y + q[2] * sn + q[3] * cs);
      ctx.lineTo(x + q[4] * cs - q[5] * sn, y + q[4] * sn + q[5] * cs);
      ctx.closePath(); ctx.fill();
      ctx.strokeStyle = `rgba(255,255,255,${0.5 * a})`; ctx.lineWidth = 1; ctx.stroke();
    } else {
      const vx = p.vx * damp, vy = p.vy * damp + p.g * s;
      ctx.globalCompositeOperation = "lighter";
      ctx.strokeStyle = `rgba(${p.c},${a})`; ctx.lineWidth = p.size;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - vx * 0.03, y - vy * 0.03); ctx.stroke();
    }
  }
  ctx.globalCompositeOperation = "source-over";
}

/**
 * Draws one frame. `ctx` must already be scaled so that one unit is one CSS pixel.
 * `calm` (reduced motion) draws the finished chart only: no particles, rings or cracks.
 */
export function drawFx(ctx: CanvasRenderingContext2D, m: FxModel, t: number, calm: boolean): void {
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  ctx.clearRect(0, 0, m.w, m.h);
  drawGrid(ctx, m, t);
  drawChart(ctx, m, t);
  if (calm) return;
  drawCracks(ctx, m, t);
  drawRings(ctx, m, t);
  drawParticles(ctx, m, t);
}
