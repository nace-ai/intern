// Canvas particle layer + shared animation clock for DecisionSpotlight.
// One requestAnimationFrame loop serves every card; it stops by itself when nobody is subscribed.
// Nothing here invents data: stream weights and bar "comets" are driven only by the fills the card passes in.

type FrameFn = (now: number) => void;
const frameFns = new Set<FrameFn>();
let rafId = 0;

function loop(now: number): void {
  rafId = 0;
  for (const fn of frameFns) fn(now);
  if (frameFns.size > 0) rafId = requestAnimationFrame(loop);
}

/** Runs `fn` on every animation frame until the returned disposer is called. */
export function onFrame(fn: FrameFn): () => void {
  frameFns.add(fn);
  if (!rafId) rafId = requestAnimationFrame(loop);
  return () => {
    frameFns.delete(fn);
    if (frameFns.size === 0 && rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  };
}

/** CSS cubic-bezier(x1, y1, x2, y2) evaluated in JS so canvas effects stay glued to CSS transitions. */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t: number) => ((ax * t + bx) * t + cx) * t;
  const sy = (t: number) => ((ay * t + by) * t + cy) * t;
  const dx = (t: number) => (3 * ax * t + 2 * bx) * t + cx;
  return (x: number) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 6; i++) {
      const err = sx(t) - x;
      if (Math.abs(err) < 1e-4) break;
      const d = dx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= err / d;
    }
    return sy(Math.max(0, Math.min(1, t)));
  };
}

/** Timing shared with the stylesheet (keep in sync with --dspot-fill-ms / the fill transition). */
export const FILL_MS = 900;
export const FILL_STAGGER_MS = 70;
export const FILL_DELAY_MS = 120;
export const STAMP_AT_MS = 560;
export const fillEase = cubicBezier(0.16, 1, 0.3, 1);

export interface FxTrack { x0: number; x1: number; y: number; h: number }
export interface FxGeometry {
  w: number; h: number;
  /** Centre of the role sigil: every thought stream starts here. */
  origin: { x: number; y: number };
  tracks: FxTrack[];
}
export interface FxVerdict {
  /** Real fill fraction per track (provider probability) or null when none was reported. */
  fills: Array<number | null>;
  /** Index of the applied action's track, or -1. */
  chosen: number;
  color: string;
}
export interface CardFx {
  setGeometry(g: FxGeometry): void;
  /** Start the "thinking" streams: equal flow into every offered action (no value implied). */
  think(roleColor: string): void;
  /** Stop thinking; ride the leading edge of each growing bar. */
  resolve(v: FxVerdict): void;
  /** Shockwave + sparks where the stamp lands (card-local CSS px). */
  stamp(x: number, y: number, color: string): void;
  /** Stop everything and clear the canvas. */
  stop(): void;
  destroy(): void;
}

interface Stream { track: number; t: number; speed: number; size: number; tint: number; wob: number }
interface Spark { x: number; y: number; vx: number; vy: number; life: number; max: number; size: number; sprite: HTMLCanvasElement; drag: number; grav: number }
interface Ring { x: number; y: number; t0: number; dur: number; r1: number; color: string }
interface Comet { track: number; fill: number; t0: number; chosen: boolean }

const sprites = new Map<string, HTMLCanvasElement>();
function sprite(color: string): HTMLCanvasElement {
  let s = sprites.get(color);
  if (s) return s;
  s = document.createElement("canvas");
  s.width = s.height = 48;
  const c = s.getContext("2d");
  if (c) {
    const g = c.createRadialGradient(24, 24, 0, 24, 24, 24);
    g.addColorStop(0, "#ffffff");
    g.addColorStop(0.18, color);
    g.addColorStop(0.5, color + "55");
    g.addColorStop(1, color + "00");
    c.fillStyle = g;
    c.fillRect(0, 0, 48, 48);
  }
  sprites.set(color, s);
  return s;
}

// Pending streams never use aqua: that colour is reserved for a live Drex result.
const TINTS = ["#ff4fa3", "#a56bff", "#e6eaff"];
const SPLIT = 0.36; // share of a stream's life spent on the curve from the sigil to its track

export function createCardFx(canvas: HTMLCanvasElement): CardFx {
  const ctx = canvas.getContext("2d");
  let geo: FxGeometry | null = null;
  let dpr = 1;
  let mode: "idle" | "think" | "verdict" = "idle";
  let roleColor = "#2ff3e0";
  let verdictColor = "#2ff3e0";
  let spawnCarry = 0;
  let last = 0;
  let dispose: (() => void) | null = null;
  let dead = false;
  const streams: Stream[] = [];
  const sparks: Spark[] = [];
  const rings: Ring[] = [];
  let comets: Comet[] = [];

  function size(): void {
    if (!geo) return;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(geo.w * dpr)), h = Math.max(1, Math.round(geo.h * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  }
  function start(): void {
    if (dead || dispose) return;
    last = 0;
    dispose = onFrame(frame);
  }
  function halt(): void {
    if (dispose) { dispose(); dispose = null; }
  }
  function clear(): void {
    if (ctx) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height); }
  }

  function streamPoint(s: Stream, t: number): { x: number; y: number } | null {
    const g = geo; if (!g) return null;
    const tr = g.tracks[s.track]; if (!tr) return null;
    const o = g.origin;
    if (t < SPLIT) {
      const k = t / SPLIT, m = 1 - k;
      const dx = tr.x0 - o.x, dy = tr.y - o.y;
      const p1x = o.x + dx * 0.04, p1y = o.y + dy * 0.92;
      const p2x = o.x + dx * 0.5, p2y = tr.y;
      return {
        x: m * m * m * o.x + 3 * m * m * k * p1x + 3 * m * k * k * p2x + k * k * k * tr.x0,
        y: m * m * m * o.y + 3 * m * m * k * p1y + 3 * m * k * k * p2y + k * k * k * tr.y + Math.sin(s.wob + k * 6) * tr.h * 0.12 * (1 - k),
      };
    }
    const k = (t - SPLIT) / (1 - SPLIT);
    return { x: tr.x0 + (tr.x1 - tr.x0) * k, y: tr.y + Math.sin(s.wob + k * 9) * tr.h * 0.14 };
  }

  function blit(img: HTMLCanvasElement, x: number, y: number, r: number, alpha: number): void {
    if (!ctx || alpha <= 0.01) return;
    ctx.globalAlpha = Math.min(1, alpha);
    ctx.drawImage(img, x - r, y - r, r * 2, r * 2);
  }

  function frame(now: number): void {
    const g = geo;
    if (!ctx || !g) return;
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
    last = now;
    const u = g.h / 11; // roughly one layout unit in CSS px, so effects scale with the card

    // fade what is already there (leaves short light trails), then draw additively
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "destination-out";
    ctx.globalAlpha = 1;
    ctx.fillStyle = `rgba(0,0,0,${Math.min(1, dt * 9)})`;
    ctx.fillRect(0, 0, g.w, g.h);
    ctx.globalCompositeOperation = "lighter";

    // ---- thought streams ----
    if (mode === "think" && g.tracks.length) {
      spawnCarry += dt * 21 * g.tracks.length;
      while (spawnCarry >= 1) {
        spawnCarry -= 1;
        streams.push({
          track: Math.floor(Math.random() * g.tracks.length), t: 0, speed: 0.75 + Math.random() * 0.7,
          size: (0.2 + Math.random() * 0.22) * u, tint: Math.random() < 0.5 ? -1 : Math.floor(Math.random() * TINTS.length), wob: Math.random() * 6.28,
        });
      }
    }
    for (let i = streams.length - 1; i >= 0; i--) {
      const s = streams[i]!;
      s.t += dt * s.speed * (mode === "think" ? 1 : 1.9);
      if (s.t >= 1) { streams.splice(i, 1); continue; }
      const p = streamPoint(s, s.t);
      if (!p) { streams.splice(i, 1); continue; }
      const fadeIn = Math.min(1, s.t / 0.08), fadeOut = Math.min(1, (1 - s.t) / 0.22);
      const onTrack = s.t < SPLIT ? 0.5 : 1; // dimmer while crossing the label column, brightest on the track
      const a = fadeIn * fadeOut * onTrack * (mode === "think" ? 0.95 : 0.45);
      blit(sprite(s.tint < 0 ? roleColor : TINTS[s.tint]!), p.x, p.y, s.size * (s.t < SPLIT ? 1.0 : 1.35), a);
    }

    // ---- comets riding the leading edge of each growing bar ----
    if (mode === "verdict") {
      for (const c of comets) {
        const tr = g.tracks[c.track]; if (!tr) continue;
        const k = (now - c.t0) / FILL_MS;
        if (k < 0 || k > 1.08) continue;
        const e = fillEase(Math.min(1, k));
        const x = tr.x0 + (tr.x1 - tr.x0) * c.fill * e;
        const speed = 1 - Math.min(1, k); // 1 at launch, 0 when the bar has landed
        const r = (c.chosen ? 1.25 : 0.7) * u * (0.55 + speed * 0.9);
        blit(sprite(verdictColor), x, tr.y, r, c.chosen ? 1 : 0.65);
        blit(sprite("#ffffff"), x, tr.y, r * 0.45, 0.9);
        const n = c.chosen ? 3 : 1;
        if (speed > 0.04 && c.fill > 0.02) {
          for (let j = 0; j < n; j++) {
            if (Math.random() > speed * 1.4) continue;
            sparks.push({
              x, y: tr.y + (Math.random() - 0.5) * tr.h * 0.7,
              vx: -(40 + Math.random() * 190) * speed * (u / 14), vy: (Math.random() - 0.5) * 90 * (u / 14),
              life: 0, max: 0.28 + Math.random() * 0.4, size: (0.12 + Math.random() * 0.2) * u,
              sprite: sprite(Math.random() < 0.35 ? "#ffffff" : verdictColor), drag: 2.4, grav: 0,
            });
          }
        }
      }
    }

    // ---- sparks ----
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i]!;
      s.life += dt;
      if (s.life >= s.max) { sparks.splice(i, 1); continue; }
      const dk = Math.exp(-s.drag * dt);
      s.vx *= dk; s.vy = s.vy * dk + s.grav * dt;
      s.x += s.vx * dt; s.y += s.vy * dt;
      const a = 1 - s.life / s.max;
      blit(s.sprite, s.x, s.y, s.size * (0.6 + a), a);
    }

    // ---- shockwave rings ----
    for (let i = rings.length - 1; i >= 0; i--) {
      const r = rings[i]!;
      const k = (now - r.t0) / r.dur;
      if (k >= 1) { rings.splice(i, 1); continue; }
      if (k < 0) continue;
      const e = 1 - Math.pow(1 - k, 3);
      ctx.globalAlpha = (1 - k) * 0.85;
      ctx.strokeStyle = r.color;
      ctx.lineWidth = Math.max(1, (1 - k) * 0.28 * u);
      ctx.beginPath();
      ctx.ellipse(r.x, r.y, r.r1 * e, r.r1 * e * 0.62, -0.12, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";

    // go idle once the verdict effects have burnt out
    if (mode === "verdict") {
      const cometsDone = comets.every((c) => now - c.t0 > FILL_MS * 1.1);
      if (cometsDone && sparks.length === 0 && rings.length === 0 && streams.length === 0) {
        mode = "idle";
        clear();
        halt();
      }
    }
  }

  return {
    setGeometry(g) { geo = g; size(); },
    think(color) {
      if (dead) return;
      roleColor = color;
      mode = "think";
      start();
    },
    resolve(v) {
      if (dead) return;
      verdictColor = v.color;
      mode = "verdict";
      const t0 = performance.now() + FILL_DELAY_MS;
      comets = [];
      v.fills.forEach((f, i) => {
        if (f == null || !(f > 0)) return;
        comets.push({ track: i, fill: Math.max(0, Math.min(1, f)), t0: t0 + i * FILL_STAGGER_MS, chosen: i === v.chosen });
      });
      start();
    },
    stamp(x, y, color) {
      if (dead || !geo) return;
      const u = geo.h / 11;
      const now = performance.now();
      rings.push({ x, y, t0: now, dur: 480, r1: 3.6 * u, color }, { x, y, t0: now + 70, dur: 600, r1: 5.2 * u, color: "#ffffff" });
      for (let i = 0; i < 30; i++) {
        const a = Math.random() * Math.PI * 2, sp = (120 + Math.random() * 380) * (u / 14);
        sparks.push({
          x: x + Math.cos(a) * u * 0.8, y: y + Math.sin(a) * u * 0.35, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp * 0.6 - 30 * (u / 14),
          life: 0, max: 0.35 + Math.random() * 0.55, size: (0.12 + Math.random() * 0.24) * u,
          sprite: sprite(Math.random() < 0.3 ? "#ffffff" : color), drag: 3.2, grav: 260 * (u / 14),
        });
      }
      if (mode === "idle") mode = "verdict";
      start();
    },
    stop() {
      mode = "idle";
      streams.length = 0; sparks.length = 0; rings.length = 0; comets = [];
      halt();
      clear();
    },
    destroy() {
      dead = true;
      streams.length = 0; sparks.length = 0; rings.length = 0; comets = [];
      halt();
    },
  };
}
