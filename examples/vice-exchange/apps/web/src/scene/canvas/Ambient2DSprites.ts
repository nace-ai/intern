// Ambient2D helper: one-time sprite baking. Nothing here runs per frame; the ambient layer only blits the results.
export type Sprite = HTMLCanvasElement;

const TAU = Math.PI * 2;

function canvas(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h));
  return c;
}

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

/** Soft round light. `rgb` is "r,g,b"; a higher `power` gives a tighter hot core. */
export function bakeGlow(rgb: string, size = 128, power = 2.2): Sprite {
  const c = canvas(size, size), g = c.getContext("2d")!;
  const r = size / 2, grad = g.createRadialGradient(r, r, 0, r, r, r);
  for (let i = 0; i <= 12; i++) {
    const u = i / 12;
    grad.addColorStop(u, `rgba(${rgb},${Math.pow(1 - u, power).toFixed(4)})`);
  }
  g.fillStyle = grad; g.fillRect(0, 0, size, size);
  return c;
}

/** Light cone pointing along +x from the middle of the left edge (headlights, searchlights, the lighthouse). */
export function bakeBeam(rgb: string, w = 384, h = 192, spread = 0.86): Sprite {
  const c = canvas(w, h), g = c.getContext("2d")!;
  const cy = h / 2, layers = 14;
  for (let i = 0; i < layers; i++) {
    const u = i / (layers - 1);                         // 0 = wide and faint, 1 = narrow and hot
    const far = (h / 2) * spread * (1 - u * 0.78), near = (h / 2) * 0.1 * (1 - u * 0.6) + 1;
    const grad = g.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, `rgba(${rgb},0.2)`);
    grad.addColorStop(0.18, `rgba(${rgb},0.13)`);
    grad.addColorStop(0.55, `rgba(${rgb},0.05)`);
    grad.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = grad;
    g.beginPath(); g.moveTo(0, cy - near); g.lineTo(w, cy - far); g.lineTo(w, cy + far); g.lineTo(0, cy + near); g.closePath(); g.fill();
  }
  return c;
}

/** Top-down palm crown, drawn once; the layer rotates and stretches it to make it sway. */
export function bakePalm(px: number, seed: number): Sprite {
  const c = canvas(px, px), g = c.getContext("2d")!;
  const rand = rng(seed), r = px / 2;
  g.translate(r, r);
  for (let layer = 0; layer < 3; layer++) {
    const count = layer === 0 ? 9 : layer === 1 ? 8 : 6;
    const reach = r * (layer === 0 ? 0.97 : layer === 1 ? 0.78 : 0.5);
    for (let i = 0; i < count; i++) {
      const a = (i / count) * TAU + rand() * 0.4 + layer * 0.37;
      const len = reach * (0.8 + rand() * 0.2), wid = len * (0.2 + rand() * 0.05), bend = (rand() - 0.5) * len * 0.3;
      g.save(); g.rotate(a);
      const grad = g.createLinearGradient(0, 0, len, 0);
      if (layer === 0) { grad.addColorStop(0, "#07352b"); grad.addColorStop(0.6, "#0f6a4e"); grad.addColorStop(1, "#18936a"); }
      else if (layer === 1) { grad.addColorStop(0, "#0c4f3c"); grad.addColorStop(0.6, "#1a9a6c"); grad.addColorStop(1, "#3fe0a0"); }
      else { grad.addColorStop(0, "#157a57"); grad.addColorStop(0.7, "#35d596"); grad.addColorStop(1, "#b4ffd8"); }
      g.fillStyle = grad;
      g.beginPath(); g.moveTo(0, 0);
      g.quadraticCurveTo(len * 0.42, -wid + bend * 0.4, len, bend);
      g.quadraticCurveTo(len * 0.5, wid + bend * 0.4, 0, 0);
      g.fill();
      g.strokeStyle = "rgba(3,26,22,0.5)"; g.lineWidth = Math.max(1, px * 0.007);
      g.beginPath(); g.moveTo(0, 0); g.quadraticCurveTo(len * 0.46, bend * 0.42, len * 0.94, bend * 0.96); g.stroke();
      g.restore();
    }
  }
  g.fillStyle = "#4a2f1c"; g.beginPath(); g.arc(0, 0, r * 0.1, 0, TAU); g.fill();
  g.fillStyle = "#96633a"; g.beginPath(); g.arc(-r * 0.02, -r * 0.025, r * 0.055, 0, TAU); g.fill();
  return c;
}

/** Margin (in metres) of glow baked around every sign plate. */
export const SIGN_MARGIN = 0.7;

/** A small rooftop neon sign: dark plate, glowing tube lettering. Purely decorative wording. */
export function bakeSign(text: string, rgb: string, widthM: number, heightM: number, ppm = 44): Sprite {
  const m = SIGN_MARGIN * ppm, w = widthM * ppm, h = heightM * ppm;
  const c = canvas(w + m * 2, h + m * 2), g = c.getContext("2d")!;
  g.fillStyle = "rgba(8,6,24,0.86)";
  g.beginPath(); g.roundRect(m, m, w, h, 0.22 * ppm); g.fill();
  g.save();
  g.shadowColor = `rgba(${rgb},0.9)`; g.shadowBlur = 0.35 * ppm;
  g.strokeStyle = `rgba(${rgb},0.95)`; g.lineWidth = 0.07 * ppm;
  g.beginPath(); g.roundRect(m + 0.1 * ppm, m + 0.1 * ppm, w - 0.2 * ppm, h - 0.2 * ppm, 0.16 * ppm); g.stroke();
  let fs = h * 0.6;
  const font = (size: number): string => `italic 800 ${size.toFixed(1)}px ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`;
  g.font = font(fs);
  const maxW = w * 0.8, tw = g.measureText(text).width;
  if (tw > maxW) { fs *= maxW / tw; g.font = font(fs); }
  g.textAlign = "center"; g.textBaseline = "middle"; g.lineJoin = "round";
  const cx = m + w / 2, cy = m + h / 2 + fs * 0.04;
  g.shadowBlur = 0.55 * ppm;
  g.fillStyle = `rgb(${rgb})`; g.fillText(text, cx, cy); g.fillText(text, cx, cy);
  g.shadowBlur = 0.12 * ppm; g.shadowColor = "rgba(255,255,255,0.9)";
  g.fillStyle = "rgba(255,255,255,0.86)"; g.fillText(text, cx, cy);
  g.restore();
  return c;
}
