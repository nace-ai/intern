import type { Building, MapView } from "@vice/contracts";

export const PALETTE = {
  void: "#07051a", asphalt: "#1a1533", asphaltEdge: "#241c47", lane: "rgba(255,214,102,0.5)",
  curb: "#2f2560", sand: "#3a2d52", waterTop: "#0b6e8f", waterDeep: "#06304f",
};
const TONE: Record<Building["tone"], { fill: string; roof: string; edge: string }> = {
  pink: { fill: "#4a1d4f", roof: "#6a2a66", edge: "#ff5fa2" },
  aqua: { fill: "#123f5a", roof: "#1b5a78", edge: "#4cc9f0" },
  purple: { fill: "#2d1d63", roof: "#42298a", edge: "#c77dff" },
  sand: { fill: "#4d3a35", roof: "#6b5146", edge: "#ffb38a" },
  teal: { fill: "#0f4a48", roof: "#176a62", edge: "#5dfdcb" },
};

/** Tiny deterministic PRNG so the decor is identical on every client. */
function rng(seed: number) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

function palm(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, rot: number) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  ctx.fillStyle = "rgba(0,0,0,0.35)"; ctx.beginPath(); ctx.arc(r * 0.25, r * 0.25, r * 0.9, 0, Math.PI * 2); ctx.fill();
  for (let i = 0; i < 7; i++) {
    ctx.rotate((Math.PI * 2) / 7);
    ctx.fillStyle = i % 2 ? "#1f9d6b" : "#27c084";
    ctx.beginPath(); ctx.ellipse(r * 0.55, 0, r * 0.6, r * 0.17, 0, 0, Math.PI * 2); ctx.fill();
  }
  ctx.fillStyle = "#7a4b2a"; ctx.beginPath(); ctx.arc(0, 0, r * 0.16, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

/** Draws everything that never moves, once, at S pixels per metre. */
export function drawStaticCity(ctx: CanvasRenderingContext2D, map: MapView, S: number): void {
  const W = map.width * S, H = map.height * S;
  const rand = rng(4242);
  // ground / asphalt
  ctx.fillStyle = PALETTE.asphalt; ctx.fillRect(0, 0, W, H);
  // subtle asphalt grain
  for (let i = 0; i < 2600; i++) {
    ctx.fillStyle = `rgba(255,255,255,${0.012 + rand() * 0.02})`;
    ctx.fillRect(rand() * W, rand() * H, 1 + rand() * 2, 1 + rand() * 2);
  }
  // water + beach strip
  for (const r of map.water) {
    const g = ctx.createLinearGradient(0, r.z0 * S, 0, r.z1 * S);
    g.addColorStop(0, PALETTE.waterTop); g.addColorStop(1, PALETTE.waterDeep);
    ctx.fillStyle = g; ctx.fillRect(r.x0 * S, r.z0 * S, (r.x1 - r.x0) * S, (r.z1 - r.z0) * S);
    ctx.fillStyle = "#c9a26b"; ctx.fillRect(r.x0 * S, (r.z0 - 0.9) * S, (r.x1 - r.x0) * S, 0.9 * S);
    ctx.fillStyle = "rgba(255,255,255,0.5)"; ctx.fillRect(r.x0 * S, r.z0 * S, (r.x1 - r.x0) * S, 0.18 * S);
    // marina piers
    ctx.fillStyle = "#6b4a2f";
    for (const px of [38, 47, 56]) ctx.fillRect((px - 0.8) * S, (r.z0 - 0.9) * S, 1.6 * S, 5 * S);
  }
  // sidewalks: a lighter pad under each building block, with lane markings in the gaps between pads
  const pads = map.buildings.filter((b) => b.x1 - b.x0 > 6);
  for (const b of map.buildings) {
    const pad = 2;
    ctx.fillStyle = PALETTE.curb;
    ctx.beginPath(); ctx.roundRect((b.x0 - pad) * S, (b.z0 - pad) * S, (b.x1 - b.x0 + pad * 2) * S, (b.z1 - b.z0 + pad * 2) * S, 1.2 * S); ctx.fill();
  }
  // lane dashes along road centrelines, derived from gaps between distinct building columns/rows
  const centres = (lo: (b: Building) => number, hi: (b: Building) => number, max: number) => {
    const spans = pads.map((b) => [lo(b), hi(b)] as const).sort((a, b) => a[0] - b[0]);
    const merged: number[][] = [];
    for (const [a, b] of spans) {
      const lastSpan = merged[merged.length - 1];
      if (lastSpan && a <= lastSpan[1]! + 5) lastSpan[1] = Math.max(lastSpan[1]!, b); else merged.push([a, b]);
    }
    const out: number[] = [];
    let prev = 0;
    for (const [a, b] of merged) { if (a! - prev > 5) out.push((prev + a!) / 2); prev = b!; }
    if (max - prev > 5) out.push((prev + max) / 2);
    return out;
  };
  const land = map.water.length ? Math.min(...map.water.map((r) => r.z0)) : map.height;
  ctx.strokeStyle = PALETTE.lane; ctx.lineWidth = 0.22 * S; ctx.setLineDash([1.6 * S, 1.8 * S]);
  for (const x of centres((b) => b.x0, (b) => b.x1, map.width)) { ctx.beginPath(); ctx.moveTo(x * S, 0); ctx.lineTo(x * S, (land - 1) * S); ctx.stroke(); }
  for (const z of centres((b) => b.z0, (b) => b.z1, land)) { ctx.beginPath(); ctx.moveTo(0, z * S); ctx.lineTo(W, z * S); ctx.stroke(); }
  ctx.setLineDash([]);

  // plaza paving around the kiosk
  const kiosk = map.buildings.find((b) => b.id === "b_kiosk");
  if (kiosk) {
    const cx = ((kiosk.x0 + kiosk.x1) / 2) * S, cz = ((kiosk.z0 + kiosk.z1) / 2) * S;
    for (let r = 9; r >= 3; r -= 2) {
      ctx.strokeStyle = r % 4 === 1 ? "rgba(255,95,162,0.22)" : "rgba(76,201,240,0.22)"; ctx.lineWidth = 0.35 * S;
      ctx.beginPath(); ctx.arc(cx, cz, r * S, 0, Math.PI * 2); ctx.stroke();
    }
  }

  // solid parked cars (from the shared map: the server stops players and NPCs at these exact footprints)
  for (const c of map.cars ?? []) {
    const L = c.length * S, Wd = c.width * S;
    ctx.save(); ctx.translate(c.x * S, c.z * S); if (c.heading === 90) ctx.rotate(Math.PI / 2);
    ctx.fillStyle = "rgba(0,0,0,0.5)"; ctx.fillRect(-L / 2 + 0.25 * S, -Wd / 2 + 0.25 * S, L, Wd);
    const r = 0.45 * S;
    ctx.beginPath();
    ctx.moveTo(-L / 2 + r, -Wd / 2); ctx.lineTo(L / 2 - r, -Wd / 2); ctx.quadraticCurveTo(L / 2, -Wd / 2, L / 2, -Wd / 2 + r);
    ctx.lineTo(L / 2, Wd / 2 - r); ctx.quadraticCurveTo(L / 2, Wd / 2, L / 2 - r, Wd / 2); ctx.lineTo(-L / 2 + r, Wd / 2);
    ctx.quadraticCurveTo(-L / 2, Wd / 2, -L / 2, Wd / 2 - r); ctx.lineTo(-L / 2, -Wd / 2 + r); ctx.quadraticCurveTo(-L / 2, -Wd / 2, -L / 2 + r, -Wd / 2);
    ctx.closePath();
    ctx.fillStyle = c.tone; ctx.fill();
    ctx.lineWidth = 0.1 * S; ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.stroke();
    ctx.fillStyle = "rgba(10,14,26,0.85)"; ctx.fillRect(-L * 0.2, -Wd * 0.36, L * 0.42, Wd * 0.72);          // cabin glass
    ctx.fillStyle = "#fff2cf"; ctx.fillRect(L / 2 - 0.22 * S, -Wd * 0.38, 0.16 * S, Wd * 0.2); ctx.fillRect(L / 2 - 0.22 * S, Wd * 0.18, 0.16 * S, Wd * 0.2); // headlights
    ctx.fillStyle = "#ff2a3c"; ctx.fillRect(-L / 2 + 0.06 * S, -Wd * 0.38, 0.14 * S, Wd * 0.2); ctx.fillRect(-L / 2 + 0.06 * S, Wd * 0.18, 0.14 * S, Wd * 0.2); // tail-lights
    ctx.restore();
  }

  // buildings
  for (const b of map.buildings) {
    const t = TONE[b.tone];
    const x = b.x0 * S, z = b.z0 * S, w = (b.x1 - b.x0) * S, h = (b.z1 - b.z0) * S;
    const lift = Math.min(1.6, b.height * 0.1) * S; // fake height: offset shadow
    ctx.fillStyle = "rgba(0,0,0,0.45)"; ctx.fillRect(x + lift, z + lift, w, h);
    ctx.save(); ctx.shadowColor = t.edge; ctx.shadowBlur = 1.6 * S;
    ctx.fillStyle = t.fill; ctx.fillRect(x, z, w, h); ctx.restore();
    const inset = Math.min(1.2 * S, w * 0.12);
    ctx.fillStyle = t.roof; ctx.fillRect(x + inset, z + inset, w - inset * 2, h - inset * 2);
    // roof details: lit windows grid / vents
    const cols = Math.max(1, Math.floor((w - inset * 2) / (2.2 * S))), rows = Math.max(1, Math.floor((h - inset * 2) / (2.2 * S)));
    for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
      if (rand() < 0.45) continue;
      ctx.fillStyle = rand() < 0.5 ? t.edge + "55" : "rgba(255,236,170,0.35)";
      ctx.fillRect(x + inset + (i + 0.25) * ((w - inset * 2) / cols), z + inset + (j + 0.25) * ((h - inset * 2) / rows), 0.9 * S, 0.9 * S);
    }
    ctx.strokeStyle = t.edge; ctx.lineWidth = 0.28 * S; ctx.strokeRect(x, z, w, h);
    // label
    const fs = Math.max(0.95 * S, Math.min(1.5 * S, (w / b.label.length) * 1.5));
    ctx.font = `700 ${fs}px ui-sans-serif, system-ui, -apple-system, sans-serif`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.lineJoin = "round"; ctx.lineWidth = 0.35 * S; ctx.strokeStyle = "rgba(7,5,26,0.9)";
    const words = b.label.split(" "), narrow = w < 9 * S && words.length > 1;
    const lines = narrow ? words : [b.label];
    lines.forEach((ln, i) => {
      const y = z + h / 2 + (i - (lines.length - 1) / 2) * fs * 1.1;
      ctx.strokeText(ln, x + w / 2, y); ctx.fillStyle = "#ffffff"; ctx.fillText(ln, x + w / 2, y);
    });
  }

  // palms along the sidewalks (never inside a building)
  for (const b of pads) {
    const step = 6;
    for (let x = b.x0 + 1; x < b.x1; x += step) for (const z of [b.z0 - 1.2, b.z1 + 1.2])
      if (z > 1 && z < land - 2 && rand() < 0.75) palm(ctx, x * S, z * S, (1.1 + rand() * 0.5) * S, rand() * 6.28);
  }
  // streetlight glows at intersections
  for (const x of centres((b) => b.x0, (b) => b.x1, map.width)) for (const z of centres((b) => b.z0, (b) => b.z1, land)) {
    const g = ctx.createRadialGradient(x * S, z * S, 0, x * S, z * S, 5 * S);
    g.addColorStop(0, "rgba(255,190,120,0.16)"); g.addColorStop(1, "rgba(255,190,120,0)");
    ctx.fillStyle = g; ctx.fillRect((x - 5) * S, (z - 5) * S, 10 * S, 10 * S);
  }
  // district labels
  ctx.font = `800 ${1.5 * S}px ui-sans-serif, system-ui, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const d of map.districts) {
    ctx.fillStyle = "rgba(199,125,255,0.38)";
    ctx.fillText(d.label.toUpperCase().split("").join(" "), d.x * S, (d.z - 9) * S > 0 ? d.z * S - 4.6 * S : d.z * S);
  }
  // vignette
  const v = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.max(W, H) * 0.75);
  v.addColorStop(0, "rgba(7,5,26,0)"); v.addColorStop(1, "rgba(7,5,26,0.55)");
  ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
}
