// Canvas 2D effects layer for SwarmGraph: message comets, bulletin ripples, capture bolts, sparks and ambient dust.
// Pure imperative code: no React, no store. Every coordinate is a viewBox unit supplied by the SwarmGraph layout,
// so the effects ride exactly on the SVG edges drawn underneath and follow the layout when the container resizes.

export interface NodeGeom { x: number; y: number; r: number }
/** Quadratic Bezier between two node rims. */
export interface Curve { x0: number; y0: number; cx: number; cy: number; x1: number; y1: number; len: number }
export interface LaneGeom { x0: number; y0: number; x1: number; y1: number; color: string }
export interface FxGeometry {
  /** Container size in CSS px. */
  cssW: number; cssH: number;
  /** CSS px per viewBox unit. */
  scale: number;
  vw: number; vh: number;
  node(id: string): NodeGeom | null;
  curve(from: string, to: string, bend?: number): Curve | null;
  lanes: LaneGeom[];
}
export interface BeamOpts { color: string; dur?: number; delay?: number; size?: number; bend?: number; impact?: boolean; tail?: number }
export interface RingOpts { color: string; dur?: number; delay?: number; from?: number; to?: number; width?: number; count?: number; gap?: number; alpha?: number }
export interface SwarmFx {
  setGeometry(g: FxGeometry): void;
  setReduced(reduced: boolean): void;
  /** A message leaves the sender and cruises toward the recipient until `deliver` is called for the same id. */
  flight(id: string, from: string, to: string, color: string): void;
  /** The message arrived: the comet finishes its path and bursts on the recipient. */
  deliver(id: string, from: string, to: string, color: string): void;
  /** One-shot comet with a fixed duration (trade, bulletin ray, eye flash, crash). */
  beam(from: string, to: string, o: BeamOpts): void;
  /** Expanding ring(s) centred on a node. */
  ring(at: string, o: RingOpts): void;
  /** Jagged lightning strike (capture). */
  bolt(from: string, to: string, color: string, delay?: number): void;
  clear(): void;
  destroy(): void;
}

export function curveAt(c: Curve, t: number): { x: number; y: number } {
  const u = 1 - t;
  return { x: u * u * c.x0 + 2 * u * t * c.cx + t * t * c.x1, y: u * u * c.y0 + 2 * u * t * c.cy + t * t * c.y1 };
}
const f1 = (n: number): string => (Math.round(n * 10) / 10).toString();
export function curvePath(c: Curve): string { return `M${f1(c.x0)} ${f1(c.y0)}Q${f1(c.cx)} ${f1(c.cy)} ${f1(c.x1)} ${f1(c.y1)}`; }
/** Small arrow head at the end of a curve, pointing along its final tangent. */
export function arrowPath(c: Curve, size: number): string {
  let dx = c.x1 - c.cx, dy = c.y1 - c.cy;
  const d = Math.hypot(dx, dy) || 1; dx /= d; dy /= d;
  const bx = c.x1 - dx * size, by = c.y1 - dy * size, w = size * 0.55;
  return `M${f1(c.x1)} ${f1(c.y1)}L${f1(bx - dy * w)} ${f1(by + dx * w)}L${f1(bx + dy * w)} ${f1(by - dx * w)}Z`;
}
/**
 * Curve from the rim of `a` to the rim of `b`. `bend` is the sideways offset of the control point as a fraction of
 * the centre distance (positive = clockwise side when travelling a -> b; for a left-to-right edge that is downward).
 */
export function buildCurve(a: NodeGeom, b: NodeGeom, bend: number, padA = 2.5, padB = 4.5): Curve {
  const dx = b.x - a.x, dy = b.y - a.y;
  const cx = (a.x + b.x) / 2 - dy * bend, cy = (a.y + b.y) / 2 + dx * bend;
  const ax = cx - a.x, ay = cy - a.y, al = Math.hypot(ax, ay) || 1;
  const bx = cx - b.x, by = cy - b.y, bl = Math.hypot(bx, by) || 1;
  const c: Curve = {
    x0: a.x + (ax / al) * (a.r + padA), y0: a.y + (ay / al) * (a.r + padA), cx, cy,
    x1: b.x + (bx / bl) * (b.r + padB), y1: b.y + (by / bl) * (b.r + padB), len: 0,
  };
  let px = c.x0, py = c.y0, len = 0;
  for (let i = 1; i <= 12; i++) { const p = curveAt(c, i / 12); len += Math.hypot(p.x - px, p.y - py); px = p.x; py = p.y; }
  c.len = len;
  return c;
}

interface Flight { id: string; from: string; to: string; color: string; t0: number; deliveredAt: number | null; p0: number }
interface Beam { from: string; to: string; color: string; t0: number; dur: number; size: number; bend: number | undefined; impact: boolean; tail: number; hit: boolean }
interface Ring { at: string | null; x: number; y: number; color: string; t0: number; dur: number; from: number; to: number; width: number; alpha: number }
interface Bolt { from: string; to: string; color: string; t0: number; dur: number; seed: number; hit: boolean }
interface Spark { x: number; y: number; vx: number; vy: number; color: string; t0: number; dur: number; size: number }
interface Mote { lane: number; u: number; v: number; speed: number; size: number; phase: number }
/** The edge keeps glowing for a moment after a comet has travelled it. */
interface Glow { from: string; to: string; bend: number | undefined; color: string; t0: number; dur: number; width: number }
interface Flare { x: number; y: number; r: number; color: string; t0: number; dur: number }

const NOMINAL_FLIGHT_MS = 950;   // a little over the server's delivery delay
const LOST_FLIGHT_MS = 7000;     // a message that is never delivered fades out
const TAU = Math.PI * 2;

function rgba(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((ch) => ch + ch).join("") : h, 16);
  if (!Number.isFinite(n)) return `rgba(255,255,255,${a})`;
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
function mulberry(seed: number): () => number {
  let s = seed >>> 0;
  return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

export function createSwarmFx(canvas: HTMLCanvasElement, reducedInit: boolean): SwarmFx {
  const ctx = canvas.getContext("2d");
  let geom: FxGeometry | null = null;
  let reduced = reducedInit;
  let dead = false, raf = 0, last = 0, dpr = 1;
  const flights = new Map<string, Flight>();
  const beams: Beam[] = [], rings: Ring[] = [], bolts: Bolt[] = [], sparks: Spark[] = [], motes: Mote[] = [], glows: Glow[] = [], flares: Flare[] = [];
  const sprites = new Map<string, HTMLCanvasElement>();

  const sprite = (color: string): HTMLCanvasElement => {
    let s = sprites.get(color);
    if (s) return s;
    s = document.createElement("canvas"); s.width = s.height = 96;
    const g = s.getContext("2d");
    if (g) {
      const grad = g.createRadialGradient(48, 48, 0, 48, 48, 48);
      grad.addColorStop(0, "rgba(255,255,255,1)");
      grad.addColorStop(0.14, rgba(color, 0.95));
      grad.addColorStop(0.42, rgba(color, 0.36));
      grad.addColorStop(0.72, rgba(color, 0.09));
      grad.addColorStop(1, rgba(color, 0));
      g.fillStyle = grad; g.fillRect(0, 0, 96, 96);
    }
    sprites.set(color, s);
    return s;
  };

  const wake = (): void => { if (!dead && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); } };
  const cap = <T>(list: T[], max: number): void => { if (list.length > max) list.splice(0, list.length - max); };

  const spark = (x: number, y: number, vx: number, vy: number, color: string, now: number, dur: number, size: number): void => {
    if (reduced) return;
    sparks.push({ x, y, vx, vy, color, t0: now, dur, size });
    cap(sparks, 260);
  };
  const burst = (at: NodeGeom, color: string, now: number, power: number): void => {
    rings.push({ at: null, x: at.x, y: at.y, color, t0: now, dur: 560, from: at.r + 1, to: at.r + 18 + power * 10, width: 2.6, alpha: 0.95 });
    flares.push({ x: at.x, y: at.y, r: Math.max(26, at.r * 2.4) * (1 + power * 0.45), color, t0: now, dur: 620 });
    cap(flares, 60);
    const n = Math.round(9 + power * 7);
    for (let i = 0; i < n; i++) {
      const a = Math.random() * TAU, v = 40 + Math.random() * 90 * power;
      spark(at.x + Math.cos(a) * at.r, at.y + Math.sin(a) * at.r, Math.cos(a) * v, Math.sin(a) * v, color, now, 380 + Math.random() * 320, 1.4 + Math.random() * 1.6);
    }
  };

  /** The part of the edge the comet has already travelled lights up like a fibre. */
  const litPath = (c: Curve, p: number, color: string, alpha: number, width: number): void => {
    if (!ctx || alpha <= 0.004 || p <= 0.01) return;
    ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(c.x0, c.y0);
    const n = 16;
    for (let i = 1; i <= n; i++) { const q = curveAt(c, (p * i) / n); ctx.lineTo(q.x, q.y); }
    ctx.globalAlpha = alpha * 0.2; ctx.lineWidth = width * 4.2; ctx.stroke();
    ctx.globalAlpha = alpha * 0.8; ctx.lineWidth = width; ctx.stroke();
  };
  const comet = (c: Curve, p: number, color: string, size: number, alpha: number, tail: number): void => {
    if (!ctx) return;
    litPath(c, p, color, alpha * 0.75, size * 0.3);
    const sp = sprite(color);
    const n = 26, span = Math.min(0.94, tail / Math.max(40, c.len));
    for (let i = n; i >= 1; i--) {
      const t = p - (i / n) * span;
      if (t < 0) continue;
      const q = curveAt(c, t), f = 1 - i / n, r = size * (0.4 + 1.15 * f);
      ctx.globalAlpha = alpha * (0.08 + 0.72 * f * f);
      ctx.drawImage(sp, q.x - r, q.y - r, r * 2, r * 2);
    }
    ctx.globalAlpha = alpha; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = size * 0.2;
    ctx.beginPath();
    for (let i = 8; i >= 0; i--) {
      const q = curveAt(c, Math.max(0, p - (i / 8) * span * 0.45));
      if (i === 8) ctx.moveTo(q.x, q.y); else ctx.lineTo(q.x, q.y);
    }
    ctx.stroke();
    const h = curveAt(c, p), R = size * 3;
    ctx.globalAlpha = alpha; ctx.drawImage(sp, h.x - R, h.y - R, R * 2, R * 2);
    // anamorphic flare: one long horizontal streak and a short vertical one
    ctx.globalAlpha = alpha * 0.85; ctx.drawImage(sp, h.x - R * 2.5, h.y - R * 0.17, R * 5, R * 0.34);
    ctx.globalAlpha = alpha * 0.5; ctx.drawImage(sp, h.x - R * 0.13, h.y - R * 1.3, R * 0.26, R * 2.6);
    ctx.globalAlpha = alpha; ctx.fillStyle = "#ffffff"; ctx.beginPath(); ctx.arc(h.x, h.y, size * 0.44, 0, TAU); ctx.fill();
  };
  /** Calm variant of a comet: the whole edge glows softly and fades, nothing travels. */
  const calmLine = (c: Curve, color: string, alpha: number, width: number): void => {
    if (!ctx || alpha <= 0.005) return;
    ctx.strokeStyle = color;
    ctx.beginPath(); ctx.moveTo(c.x0, c.y0); ctx.quadraticCurveTo(c.cx, c.cy, c.x1, c.y1);
    ctx.globalAlpha = alpha * 0.28; ctx.lineWidth = width * 3.4; ctx.stroke();
    ctx.globalAlpha = alpha; ctx.lineWidth = width; ctx.stroke();
    ctx.fillStyle = color; ctx.globalAlpha = alpha; ctx.beginPath(); ctx.arc(c.x1, c.y1, width * 1.7, 0, TAU); ctx.fill();
  };
  const trailSparks = (c: Curve, p: number, color: string, now: number, rate: number): void => {
    if (reduced || Math.random() > rate) return;
    const h = curveAt(c, p), b = curveAt(c, Math.max(0, p - 0.03));
    let tx = h.x - b.x, ty = h.y - b.y; const d = Math.hypot(tx, ty) || 1; tx /= d; ty /= d;
    const side = (Math.random() - 0.5) * 70;
    spark(h.x, h.y, -tx * (20 + Math.random() * 40) - ty * side, -ty * (20 + Math.random() * 40) + tx * side, color, now, 300 + Math.random() * 380, 1 + Math.random() * 1.3);
  };

  const cruise = (elapsed: number): number => {
    const x = Math.min(1, elapsed / NOMINAL_FLIGHT_MS);
    const eased = x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
    const creep = elapsed > NOMINAL_FLIGHT_MS ? 0.08 * (1 - Math.exp(-(elapsed - NOMINAL_FLIGHT_MS) / 1400)) : 0;
    return 0.86 * eased + creep;
  };

  function draw(now: number): void {
    const g = geom;
    if (!g || !ctx || g.cssW < 2 || g.cssH < 2) return;
    const dt = Math.min(0.05, Math.max(0, (now - last) / 1000)); last = now;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const k = g.scale * dpr;
    ctx.setTransform(k, 0, 0, k, 0, 0);
    ctx.globalCompositeOperation = "lighter"; ctx.lineCap = "round"; ctx.lineJoin = "round";

    // ambient dust drifting left to right inside each lane
    if (!reduced) {
      for (const m of motes) {
        const lane = g.lanes[m.lane]; if (!lane) continue;
        const w = lane.x1 - lane.x0, h = lane.y1 - lane.y0;
        m.u += (m.speed * dt) / Math.max(1, w); if (m.u > 1) { m.u -= 1; m.v = Math.random(); }
        const x = lane.x0 + m.u * w, y = lane.y0 + 4 + m.v * Math.max(1, h - 8) + Math.sin(now / 900 + m.phase) * 2.2;
        const edge = Math.min(1, m.u * 8, (1 - m.u) * 8);
        ctx.globalAlpha = (0.2 + 0.3 * (0.5 + 0.5 * Math.sin(now / 520 + m.phase * 3))) * edge;
        const r = m.size * 3;
        ctx.drawImage(sprite(lane.color), x - r, y - r, r * 2, r * 2);
      }
    }

    // rings
    for (let i = rings.length - 1; i >= 0; i--) {
      const r = rings[i]!; const p = (now - r.t0) / r.dur;
      if (p < 0) continue;
      if (p >= 1) { rings.splice(i, 1); continue; }
      const at = r.at ? g.node(r.at) : null;
      if (r.at && !at) { rings.splice(i, 1); continue; }
      const x = at ? at.x : r.x, y = at ? at.y : r.y;
      const e = 1 - Math.pow(1 - p, 2.4);
      const rad = r.from + (r.to - r.from) * (reduced ? 0.5 : e);
      const a = (reduced ? Math.sin(Math.PI * p) * 0.45 : Math.pow(1 - p, 1.5)) * r.alpha;
      ctx.strokeStyle = r.color; ctx.beginPath(); ctx.arc(x, y, Math.max(0.5, rad), 0, TAU);
      ctx.globalAlpha = a * 0.3; ctx.lineWidth = r.width * 3.6; ctx.stroke();
      ctx.globalAlpha = a; ctx.lineWidth = Math.max(0.4, r.width * (1 - 0.55 * p)); ctx.stroke();
    }

    // node flares and edge afterglow
    for (let i = flares.length - 1; i >= 0; i--) {
      const f = flares[i]!; const p = (now - f.t0) / f.dur;
      if (p >= 1) { flares.splice(i, 1); continue; }
      if (reduced) continue;
      const r = f.r * (0.75 + 0.5 * p);
      ctx.globalAlpha = Math.pow(1 - p, 1.6) * 0.95;
      ctx.drawImage(sprite(f.color), f.x - r, f.y - r, r * 2, r * 2);
    }
    for (let i = glows.length - 1; i >= 0; i--) {
      const gl = glows[i]!; const p = (now - gl.t0) / gl.dur;
      const c = g.curve(gl.from, gl.to, gl.bend);
      if (!c || p >= 1) { glows.splice(i, 1); continue; }
      if (!reduced) litPath(c, 1, gl.color, Math.pow(1 - p, 1.3) * 0.75, gl.width);
    }

    // one-shot beams
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i]!; const p = (now - b.t0) / b.dur;
      if (p < 0) continue;
      const c = g.curve(b.from, b.to, b.bend);
      if (!c || p >= 1.25) { beams.splice(i, 1); continue; }
      if (reduced) { calmLine(c, b.color, Math.sin(Math.PI * Math.min(1, p / 1.25)) * 0.85, Math.max(1.2, b.size * 0.36)); continue; }
      if (p >= 1) {
        if (!b.hit) {
          b.hit = true;
          glows.push({ from: b.from, to: b.to, bend: b.bend, color: b.color, t0: now, dur: 520, width: b.size * 0.3 }); cap(glows, 120);
          const at = g.node(b.to); if (at && b.impact) burst(at, b.color, now, b.size / 9);
        }
        continue;
      }
      const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      comet(c, e, b.color, b.size, Math.min(1, p * 6), b.tail);
      trailSparks(c, e, b.color, now, 0.35);
    }

    // message flights
    for (const [id, f] of flights) {
      const c = g.curve(f.from, f.to);
      const el = now - f.t0;
      if (!c || el < 0) { if (!c) flights.delete(id); continue; }
      if (f.deliveredAt === null) {
        if (el > LOST_FLIGHT_MS) { flights.delete(id); continue; }
        const fade = el > LOST_FLIGHT_MS - 500 ? (LOST_FLIGHT_MS - el) / 500 : 1;
        if (reduced) calmLine(c, f.color, Math.min(1, el / 220) * 0.8 * fade, 2);
        else { const p = cruise(el); comet(c, p, f.color, 8.5, Math.min(1, el / 90) * fade, 120); trailSparks(c, p, f.color, now, 0.8); }
        continue;
      }
      const since = now - f.deliveredAt;
      if (reduced) {
        if (since > 600) { flights.delete(id); continue; }
        calmLine(c, f.color, 0.8 * (1 - since / 600), 2);
        continue;
      }
      const rem = 1 - f.p0, fin = Math.max(170, rem * 560), x = Math.min(1, since / fin);
      if (x >= 1) {
        const at = g.node(f.to); if (at) burst(at, f.color, now, 1);
        glows.push({ from: f.from, to: f.to, bend: undefined, color: f.color, t0: now, dur: 700, width: 2.6 }); cap(glows, 120);
        flights.delete(id); continue;
      }
      const p = f.p0 + rem * (1 - Math.pow(1 - x, 2));
      comet(c, p, f.color, 8.5, 1, 120); trailSparks(c, p, f.color, now, 0.8);
    }

    // lightning
    for (let i = bolts.length - 1; i >= 0; i--) {
      const b = bolts[i]!; const p = (now - b.t0) / b.dur;
      if (p < 0) continue;
      const a = g.node(b.from), z = g.node(b.to);
      if (!a || !z || p >= 1) { bolts.splice(i, 1); continue; }
      const dx = z.x - a.x, dy = z.y - a.y, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d;
      const ax = a.x + ux * (a.r + 2), ay = a.y + uy * (a.r + 2), bx = z.x - ux * (z.r + 2), by = z.y - uy * (z.r + 2);
      if (reduced) {
        const al = Math.sin(Math.PI * p) * 0.85;
        ctx.strokeStyle = b.color; ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
        ctx.globalAlpha = al * 0.3; ctx.lineWidth = 7; ctx.stroke(); ctx.globalAlpha = al; ctx.lineWidth = 2.2; ctx.stroke();
        continue;
      }
      if (!b.hit) { b.hit = true; burst(z, b.color, now, 2.2); burst(z, "#ffffff", now, 1); }
      const env = p < 0.12 ? p / 0.12 : Math.pow(1 - (p - 0.12) / 0.88, 0.8);
      const rnd = mulberry(b.seed + Math.floor((now - b.t0) / 55));
      for (let s = 0; s < 3; s++) {
        const amp = (s === 0 ? 9 : 17) * (0.6 + rnd() * 0.8), segs = 11;
        ctx.beginPath(); ctx.moveTo(ax, ay);
        for (let j = 1; j < segs; j++) {
          const t = j / segs, off = (rnd() * 2 - 1) * amp * Math.sin(Math.PI * t);
          ctx.lineTo(ax + (bx - ax) * t - uy * off, ay + (by - ay) * t + ux * off);
        }
        ctx.lineTo(bx, by);
        ctx.strokeStyle = b.color; ctx.globalAlpha = env * (s === 0 ? 0.5 : 0.22); ctx.lineWidth = s === 0 ? 7.5 : 4; ctx.stroke();
        ctx.globalAlpha = env * (s === 0 ? 1 : 0.6); ctx.lineWidth = s === 0 ? 2.3 : 1.1; ctx.stroke();
        if (s === 0) { ctx.strokeStyle = "#ffffff"; ctx.globalAlpha = env; ctx.lineWidth = 0.9; ctx.stroke(); }
      }
    }

    // sparks
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i]!; const p = (now - s.t0) / s.dur;
      if (p >= 1) { sparks.splice(i, 1); continue; }
      s.x += s.vx * dt; s.y += s.vy * dt; s.vx *= 1 - 2.4 * dt; s.vy *= 1 - 2.4 * dt;
      const r = s.size * (1.6 - p);
      ctx.globalAlpha = (1 - p) * 0.9;
      ctx.drawImage(sprite(s.color), s.x - r * 2, s.y - r * 2, r * 4, r * 4);
    }
    ctx.globalAlpha = 1;
  }

  function frame(now: number): void {
    raf = 0;
    if (dead) return;
    draw(now);
    const busy = flights.size > 0 || beams.length > 0 || rings.length > 0 || bolts.length > 0 || sparks.length > 0 || glows.length > 0 || flares.length > 0;
    // Calm mode has no ambient motion, so the loop sleeps until the next effect arrives.
    if (busy || !reduced) raf = requestAnimationFrame(frame);
  }

  return {
    setGeometry(g) {
      geom = g;
      const px = Math.max(1, g.cssW * g.cssH);
      dpr = Math.max(0.5, Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(4.2e6 / px)));
      const w = Math.max(1, Math.round(g.cssW * dpr)), h = Math.max(1, Math.round(g.cssH * dpr));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      const want = Math.max(18, Math.min(72, Math.round((g.vw * g.vh) / 7500)));
      if (motes.length !== want || motes.some((m) => m.lane >= g.lanes.length)) {
        motes.length = 0;
        for (let i = 0; i < want && g.lanes.length; i++) {
          motes.push({ lane: i % g.lanes.length, u: Math.random(), v: Math.random(), speed: 7 + Math.random() * 22, size: 0.8 + Math.random() * 1.5, phase: Math.random() * TAU });
        }
      }
      wake();
    },
    setReduced(r) { reduced = r; if (r) sparks.length = 0; wake(); },
    flight(id, from, to, color) {
      flights.set(id, { id, from, to, color, t0: performance.now(), deliveredAt: null, p0: 0 });
      if (flights.size > 90) { const first = flights.keys().next().value; if (first !== undefined) flights.delete(first); }
      wake();
    },
    deliver(id, from, to, color) {
      const now = performance.now();
      const f = flights.get(id);
      if (f) { if (f.deliveredAt === null) { f.p0 = Math.min(0.97, cruise(now - f.t0)); f.deliveredAt = now; } }
      else { beams.push({ from, to, color, t0: now, dur: 430, size: 8.5, bend: undefined, impact: true, tail: 120, hit: false }); cap(beams, 140); }
      wake();
    },
    beam(from, to, o) {
      beams.push({ from, to, color: o.color, t0: performance.now() + (o.delay ?? 0), dur: o.dur ?? 600, size: o.size ?? 6, bend: o.bend, impact: o.impact ?? true, tail: o.tail ?? 80, hit: false });
      cap(beams, 140); wake();
    },
    ring(at, o) {
      const now = performance.now(), n = o.count ?? 1;
      for (let i = 0; i < n; i++) {
        rings.push({ at, x: 0, y: 0, color: o.color, t0: now + (o.delay ?? 0) + i * (o.gap ?? 150), dur: o.dur ?? 700, from: o.from ?? 12, to: o.to ?? 48, width: o.width ?? 2, alpha: o.alpha ?? 0.9 });
      }
      cap(rings, 140); wake();
    },
    bolt(from, to, color, delay = 0) {
      bolts.push({ from, to, color, t0: performance.now() + delay, dur: 620, seed: Math.floor(Math.random() * 1e6), hit: false });
      cap(bolts, 12); wake();
    },
    clear() { flights.clear(); beams.length = 0; rings.length = 0; bolts.length = 0; sparks.length = 0; glows.length = 0; flares.length = 0; wake(); },
    destroy() {
      dead = true;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      flights.clear(); beams.length = 0; rings.length = 0; bolts.length = 0; sparks.length = 0; glows.length = 0; flares.length = 0; motes.length = 0; sprites.clear();
    },
  };
}
