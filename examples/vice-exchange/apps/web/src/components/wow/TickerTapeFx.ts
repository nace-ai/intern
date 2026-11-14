// TickerTape light layer: a transparent Canvas 2D overlay for one-shot effects (rail comets for swarm messages,
// spark bursts for trades and incoming headlines, a shockwave for a capture). It draws nothing while idle.
// The owner drives it from its own animation frame; this file starts no timers and no frames of its own.

export interface TapeFx {
  /** CSS pixel size of the strip. Call on mount and whenever the strip resizes. */
  resize(width: number, height: number): void;
  /** Advances and draws. dt in milliseconds. */
  frame(dt: number): void;
  /** A light pulse that races along the top (0) or bottom (1) rail. */
  comet(color: string, rail?: 0 | 1, reverse?: boolean): void;
  /** Sparks thrown from x (CSS px from the left edge of the strip). */
  burst(x: number, color: string, count?: number): void;
  /** Two fronts racing outwards from x. */
  shock(x: number, color: string): void;
  /** A glow that swells at the right edge: "news incoming". */
  edge(color: string): void;
  clear(): void;
}

interface Comet { x: number; v: number; rail: 0 | 1; rgb: string; len: number }
interface Spark { x: number; y: number; vx: number; vy: number; life: number; max: number; rgb: string; size: number }
interface Shock { x: number; r: number; v: number; rgb: string; life: number; max: number }
interface Edge { rgb: string; life: number; max: number }

function rgbOf(color: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return "255,255,255";
  const n = parseInt(m[1]!, 16);
  return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

export function createTapeFx(canvas: HTMLCanvasElement): TapeFx {
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0, dpr = 1, dirty = false;
  const comets: Comet[] = [], sparks: Spark[] = [], shocks: Shock[] = [], edges: Edge[] = [];
  const unit = () => Math.max(1, H / 34);

  return {
    resize(width, height) {
      const nextDpr = Math.min(2, window.devicePixelRatio || 1);
      if (width === W && height === H && nextDpr === dpr) return;
      W = width; H = height; dpr = nextDpr;
      canvas.width = Math.max(1, Math.round(W * dpr));
      canvas.height = Math.max(1, Math.round(H * dpr));
      dirty = true;
    },
    comet(color, rail = 0, reverse = false) {
      // At most a handful per rail at once: a burst of swarm messages should read as traffic, not as fog.
      if (!ctx || W <= 0 || comets.filter((c) => c.rail === rail).length >= 5) return;
      const u = unit();
      const len = Math.min(W * 0.34, 260 * u) * (0.75 + Math.random() * 0.5);
      // Crossing time is the same on a laptop and on a 4K wall: speed scales with the strip width.
      const speed = (W + len) / (1.15 + Math.random() * 0.35);
      comets.push({ x: reverse ? W + len : -len * 0.2, v: reverse ? -speed : speed, rail, rgb: rgbOf(color), len });
    },
    burst(x, color, count = 14) {
      if (!ctx || W <= 0) return;
      const u = unit(), rgb = rgbOf(color);
      const room = Math.max(0, 140 - sparks.length);
      for (let i = 0; i < Math.min(count, room); i++) {
        const dir = Math.random() < 0.5 ? -1 : 1;
        const life = 420 + Math.random() * 520;
        sparks.push({
          x, y: H * (0.25 + Math.random() * 0.5), vx: dir * (60 + Math.random() * 300) * u, vy: (Math.random() - 0.5) * 46 * u,
          life, max: life, rgb, size: (0.8 + Math.random() * 1.3) * u,
        });
      }
    },
    shock(x, color) {
      if (!ctx || W <= 0 || shocks.length > 6) return;
      shocks.push({ x, r: 0, v: Math.max(W, 900) * 1.7, rgb: rgbOf(color), life: 720, max: 720 });
    },
    edge(color) {
      if (!ctx || W <= 0) return;
      edges.length = 0; // one glow at a time: the newest colour takes over instead of stacking up to white
      edges.push({ rgb: rgbOf(color), life: 900, max: 900 });
    },
    clear() { comets.length = 0; sparks.length = 0; shocks.length = 0; edges.length = 0; dirty = true; },
    frame(dt) {
      if (!ctx) return;
      const busy = comets.length + sparks.length + shocks.length + edges.length > 0;
      if (!busy && !dirty) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      dirty = busy;
      if (!busy) return;
      const u = unit(), s = dt / 1000;
      ctx.globalCompositeOperation = "lighter";

      for (let i = comets.length - 1; i >= 0; i--) {
        const c = comets[i]!;
        c.x += c.v * s;
        const dir = c.v >= 0 ? 1 : -1;
        const tail = c.x - dir * c.len;
        if ((dir > 0 && tail > W) || (dir < 0 && tail < 0)) { comets.splice(i, 1); continue; }
        const y = c.rail === 0 ? 1.2 * u : H - 1.2 * u;
        const g = ctx.createLinearGradient(tail, 0, c.x, 0);
        g.addColorStop(0, `rgba(${c.rgb},0)`); g.addColorStop(0.7, `rgba(${c.rgb},0.55)`); g.addColorStop(1, `rgba(${c.rgb},1)`);
        const x0 = Math.min(tail, c.x);
        // the streak on the rail, then a taller, fainter halo around it
        ctx.fillStyle = g;
        ctx.fillRect(x0, y - 1.5 * u, c.len, 3 * u);
        ctx.globalAlpha = 0.34;
        ctx.fillRect(x0, y - 6 * u, c.len, 12 * u);
        ctx.globalAlpha = 1;
        // a wide wash of light under the head, so the text it passes over picks up the colour
        const wr = H * 1.7;
        const wash = ctx.createRadialGradient(c.x, y, 0, c.x, y, wr);
        wash.addColorStop(0, `rgba(${c.rgb},0.24)`); wash.addColorStop(1, `rgba(${c.rgb},0)`);
        ctx.fillStyle = wash; ctx.fillRect(c.x - wr, 0, wr * 2, H);
        // the hot head
        const hr = 13 * u;
        const head = ctx.createRadialGradient(c.x, y, 0, c.x, y, hr);
        head.addColorStop(0, "rgba(255,255,255,0.95)"); head.addColorStop(0.25, `rgba(${c.rgb},0.85)`); head.addColorStop(1, `rgba(${c.rgb},0)`);
        ctx.fillStyle = head; ctx.fillRect(c.x - hr, y - hr, hr * 2, hr * 2);
      }

      for (let i = sparks.length - 1; i >= 0; i--) {
        const p = sparks[i]!;
        p.life -= dt;
        if (p.life <= 0) { sparks.splice(i, 1); continue; }
        p.x += p.vx * s; p.y += p.vy * s;
        p.vx *= Math.pow(0.12, s); // drag
        if (p.y < 2 * u || p.y > H - 2 * u) { p.vy = -p.vy; p.y = Math.max(2 * u, Math.min(H - 2 * u, p.y)); }
        const a = Math.min(1, p.life / p.max * 1.4);
        const trail = Math.max(2 * u, Math.abs(p.vx) * 0.035);
        const g = ctx.createLinearGradient(p.x - Math.sign(p.vx) * trail, 0, p.x, 0);
        g.addColorStop(0, `rgba(${p.rgb},0)`); g.addColorStop(1, `rgba(${p.rgb},${a.toFixed(3)})`);
        ctx.fillStyle = g;
        ctx.fillRect(Math.min(p.x, p.x - Math.sign(p.vx) * trail), p.y - p.size / 2, trail, p.size);
        // round ember with a small halo
        ctx.fillStyle = `rgba(${p.rgb},${(a * 0.4).toFixed(3)})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.size * 2.2, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = `rgba(255,255,255,${(a * 0.8).toFixed(3)})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.size * 0.55, 0, Math.PI * 2); ctx.fill();
      }

      for (let i = edges.length - 1; i >= 0; i--) {
        const g0 = edges[i]!;
        g0.life -= dt;
        if (g0.life <= 0) { edges.splice(i, 1); continue; }
        const k = g0.life / g0.max;                 // 1 -> 0
        const a = Math.sin(Math.min(1, (1 - k) * 4) * Math.PI / 2) * k; // quick swell, slow fade
        const reach = 190 * u;
        const g = ctx.createLinearGradient(W - reach, 0, W, 0);
        g.addColorStop(0, `rgba(${g0.rgb},0)`); g.addColorStop(0.7, `rgba(${g0.rgb},${(0.16 * a).toFixed(3)})`); g.addColorStop(1, `rgba(${g0.rgb},${(0.55 * a).toFixed(3)})`);
        ctx.fillStyle = g; ctx.fillRect(W - reach, 0, reach, H);
      }

      for (let i = shocks.length - 1; i >= 0; i--) {
        const k = shocks[i]!;
        k.life -= dt;
        if (k.life <= 0) { shocks.splice(i, 1); continue; }
        k.r += k.v * s;
        const a = Math.max(0, k.life / k.max);
        const band = 90 * u;
        for (const dir of [-1, 1] as const) {
          const front = k.x + dir * k.r;
          if (front < -band || front > W + band) continue;
          const g = ctx.createLinearGradient(front - dir * band, 0, front, 0);
          g.addColorStop(0, `rgba(${k.rgb},0)`); g.addColorStop(0.8, `rgba(${k.rgb},${(0.55 * a).toFixed(3)})`); g.addColorStop(1, `rgba(255,255,255,${(0.9 * a).toFixed(3)})`);
          ctx.fillStyle = g;
          ctx.fillRect(Math.min(front, front - dir * band), 0, band, H);
        }
      }
      ctx.globalCompositeOperation = "source-over";
    },
  };
}
