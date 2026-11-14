// "NDI X-RAY": one animated neon SVG of the ingested document. Every page is drawn from the layout blocks NDI
// Parse returned, every glowing box is a region NDI Extract cited for a fact, and a live circuit trace runs from
// each box to its fact card. Pure string building from the cached NDI results: nothing is positioned by hand, and
// when a fact has no region from NDI it is drawn as a margin marker, never at a made-up place.
import type { PageRegion, PolicyFact } from "./facts";
import { totalCostUsd, type IngestOutcome } from "./ingest";

export interface LayoutBlock { type: string; x0: number; y0: number; x1: number; y1: number }
export interface PageLayout { page: number; blocks: LayoutBlock[] }

export interface XrayInput {
  title: string; publisher: string; sourceUrl: string; sha256: string;
  /** "ndi_live": the jobs ran in this process. "ndi_cache": an earlier real job read from disk. */
  source: "ndi_live" | "ndi_cache";
  jobId: string; parseJobId: string; ranAt: string; elapsedMs: number; costUsd: string | null;
  pageCount: number; layout: PageLayout[]; facts: PolicyFact[];
}

const asRecord = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/** Page boxes of every layout block in a raw NDI Parse result ([] when the parse carried no blocks). */
export function pageLayout(parseResult: unknown): PageLayout[] {
  const blocks = asRecord(asRecord(parseResult)?.document)?.blocks;
  const pages = new Map<number, LayoutBlock[]>();
  if (!Array.isArray(blocks)) return [];
  for (const raw of blocks) {
    const b = asRecord(raw); const loc = asRecord(b?.location);
    const page = typeof loc?.page === "number" ? loc.page : null;
    if (!b || !loc || page === null || !Array.isArray(loc.polygons)) continue;
    const pts = (loc.polygons as unknown[]).flatMap((poly) => (Array.isArray(poly) ? poly : []))
      .map((p) => (Array.isArray(p) ? [Number(p[0]), Number(p[1])] : [NaN, NaN]))
      .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1])) as Array<[number, number]>;
    if (pts.length < 2) continue;
    const xs = pts.map((p) => p[0]); const ys = pts.map((p) => p[1]);
    const box = { type: typeof b.type === "string" ? b.type : "text", x0: Math.max(0, Math.min(...xs)), y0: Math.max(0, Math.min(...ys)), x1: Math.min(1, Math.max(...xs)), y1: Math.min(1, Math.max(...ys)) };
    if (box.x1 > box.x0 && box.y1 > box.y0) pages.set(page, [...(pages.get(page) ?? []), box]);
  }
  return [...pages].sort((a, b) => a[0] - b[0]).map(([page, list]) => ({ page, blocks: list }));
}

const NEON = ["#ff2bd6", "#00f0ff", "#ffe45e", "#3dff9a", "#b47bff", "#ff8c3c", "#5aaaff", "#ff6eaa", "#aaff5a", "#78ffe6"];
/** Characters XML 1.0 does not allow, whatever a document's text layer contains. */
const XML_INVALID = new RegExp(`[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F${String.fromCharCode(0xfffe, 0xffff)}]|\\p{Cs}`, "gu");
const esc = (s: string): string => s.replace(XML_INVALID, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const n = (v: number): string => (Math.round(v * 10) / 10).toString();

function wrap(text: string, perLine: number, maxLines: number): string[] {
  const lines: string[] = []; let line = "";
  for (const w of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + w.length > perLine) {
      if (lines.length === maxLines - 1) { lines.push(`${line}…`); return lines; }
      lines.push(line); line = w;
    } else line = line ? `${line} ${w}` : w;
  }
  if (line) lines.push(line);
  return lines;
}

/** A circuit trace through the points, with rounded corners. */
function trace(points: Array<[number, number]>, radius = 10): string {
  const pts = points.filter((p, i) => i === 0 || Math.hypot(p[0] - points[i - 1]![0], p[1] - points[i - 1]![1]) > 0.5);
  let d = `M${n(pts[0]![0])} ${n(pts[0]![1])}`;
  for (let i = 1; i < pts.length; i += 1) {
    const [x, y] = pts[i]!;
    if (i === pts.length - 1) { d += ` L${n(x)} ${n(y)}`; break; }
    const [px, py] = pts[i - 1]!; const [nx, ny] = pts[i + 1]!;
    const inLen = Math.hypot(x - px, y - py); const outLen = Math.hypot(nx - x, ny - y);
    const r = Math.min(radius, inLen / 2, outLen / 2);
    d += ` L${n(x - ((x - px) / inLen) * r)} ${n(y - ((y - py) / inLen) * r)} Q${n(x)} ${n(y)} ${n(x + ((nx - x) / outLen) * r)} ${n(y + ((ny - y) / outLen) * r)}`;
  }
  return d;
}

/** The animated SVG. Width scales to its container; animation stops under prefers-reduced-motion. */
export function renderXraySvg(x: XrayInput): string {
  const W = 1600; const M = 48;
  const pages = Math.max(1, x.pageCount);
  const gap = 34;
  const pw = Math.min(272, (W - 2 * M - (pages - 1) * gap) / pages);
  const ph = pw * (11 / 8.5);
  const rowW = pages * pw + (pages - 1) * gap;
  const px0 = (W - rowW) / 2 - gap / 4; const py0 = 250;
  const cols = x.facts.length > 6 ? 3 : Math.max(1, Math.min(3, x.facts.length));
  const cgap = 30; const cw = (W - 2 * M - (cols - 1) * cgap) / cols; const ch = 152; const rgap = 20;
  const rows = Math.max(1, Math.ceil(x.facts.length / cols));
  const busY0 = py0 + ph + 26; const busStep = 9;
  const cy0 = busY0 + Math.max(1, x.facts.length) * busStep + 64;
  const H = cy0 + rows * ch + (rows - 1) * rgap + 92;
  const pageX = (p: number): number => px0 + (p - 1) * (pw + gap);
  const card = (i: number): { cx: number; cy: number; col: number; row: number } => {
    const col = i % cols; const row = Math.floor(i / cols);
    return { cx: M + col * (cw + cgap), cy: cy0 + row * (ch + rgap), col, row };
  };
  const out: string[] = [];
  const live = x.source === "ndi_live";
  const blockCount = x.layout.reduce((s, p) => s + p.blocks.length, 0);
  let host = ""; try { host = new URL(x.sourceUrl).hostname; } catch { host = ""; }

  out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${n(H)}" width="100%" role="img" aria-label="${esc(`NDI X-ray of ${x.title}: ${x.facts.length} cited facts on ${pages} pages`)}" font-family="'JetBrains Mono','SF Mono',Menlo,Consolas,monospace">`);
  out.push(`<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0a0520"/><stop offset=".55" stop-color="#120a33"/><stop offset="1" stop-color="#04101f"/></linearGradient>
<linearGradient id="hot" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff2bd6"/><stop offset=".5" stop-color="#b47bff"/><stop offset="1" stop-color="#00f0ff"/></linearGradient>
<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#00f0ff" stop-opacity="0"/><stop offset=".8" stop-color="#00f0ff" stop-opacity=".34"/><stop offset="1" stop-color="#eaffff" stop-opacity=".95"/></linearGradient>
<radialGradient id="haloA" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ff2bd6" stop-opacity=".34"/><stop offset="1" stop-color="#ff2bd6" stop-opacity="0"/></radialGradient>
<radialGradient id="haloB" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#00f0ff" stop-opacity=".26"/><stop offset="1" stop-color="#00f0ff" stop-opacity="0"/></radialGradient>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#7a4dff" stroke-opacity=".13" stroke-width="1"/></pattern>
<filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<style>
.pulse{animation:pulse 2.6s ease-in-out infinite}
.wire{stroke-dasharray:7 9;animation:flow 1.1s linear infinite}
.drift{animation:drift 9s ease-in-out infinite alternate}
.blink{animation:blink 1.4s steps(2,jump-none) infinite}
@keyframes pulse{0%,100%{opacity:.5}50%{opacity:1}}
@keyframes flow{to{stroke-dashoffset:-32}}
@keyframes drift{from{transform:translate(-30px,-10px)}to{transform:translate(40px,24px)}}
@keyframes blink{0%{opacity:1}100%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.pulse,.wire,.drift,.blink{animation:none}.dot,.beam{display:none}}
</style>`);
  out.push(`<rect width="${W}" height="${n(H)}" fill="url(#bg)"/><rect width="${W}" height="${n(H)}" fill="url(#grid)"/>`);
  out.push(`<circle class="drift" cx="230" cy="150" r="420" fill="url(#haloA)"/><circle class="drift" cx="${W - 200}" cy="${n(H - 180)}" r="520" fill="url(#haloB)" style="animation-delay:-4s"/>`);

  // Header: what this is, which document, which real job.
  const bw = 520; const bx = W - M - bw;
  out.push(`<text x="${M}" y="76" font-size="48" font-weight="800" letter-spacing="6" fill="url(#hot)" filter="url(#soft)">NDI X-RAY</text>`);
  out.push(`<text x="${M + 344}" y="54" font-size="13.5" letter-spacing="1.5" fill="#c4bff0">REAL DOCUMENT → NACE NDI PARSE + EXTRACT → CITED FACTS</text>`);
  out.push(`<text x="${M + 344}" y="76" font-size="12.5" fill="#8f89c4">every glowing box is a region NDI returned · every trace ends at its fact</text>`);
  wrap(x.title, 62, 2).forEach((line, k) => {
    out.push(`<text x="${M}" y="${124 + k * 28}" font-size="23" font-weight="700" fill="#f2f0ff">${esc(line)}</text>`);
  });
  out.push(`<text x="${M}" y="204" font-size="13.5" fill="#9d97d6">${esc(x.publisher)}  ·  ${esc(host)}  ·  sha-256 ${esc(x.sha256.slice(0, 16))}…${esc(x.sha256.slice(-8))}  ·  ${pages} pages  ·  ${blockCount} layout blocks from NDI Parse  ·  ${x.facts.length} regions cited by NDI Extract</text>`);
  const badge = live ? "#3dff9a" : "#ffe45e";
  out.push(`<g><rect x="${bx}" y="26" width="${bw}" height="144" rx="14" fill="#0b0824" fill-opacity=".8" stroke="${badge}" stroke-opacity=".8" stroke-width="1.5" filter="url(#soft)"/>`);
  out.push(`<circle class="blink" cx="${bx + 26}" cy="54" r="7" fill="${badge}" filter="url(#glow)"/><text x="${bx + 44}" y="60" font-size="17" font-weight="800" letter-spacing="2.5" fill="${badge}">${live ? "LIVE NDI RUN" : "CACHED RESULT OF A REAL NDI JOB"}</text>`);
  out.push(`<text x="${bx + 22}" y="90" font-size="13" fill="#d7d3ff">extract job  <tspan fill="#ff7be6">${esc(x.jobId)}</tspan></text>`);
  out.push(`<text x="${bx + 22}" y="112" font-size="13" fill="#d7d3ff">parse job    <tspan fill="#7df4ff">${esc(x.parseJobId)}</tspan></text>`);
  out.push(`<text x="${bx + 22}" y="136" font-size="13" fill="#a9a4dc">ran ${esc(x.ranAt)}  ·  ${(x.elapsedMs / 1000).toFixed(1)} s  ·  ${x.costUsd === null ? "cost not reported" : `$${esc(x.costUsd)}`}</text>`);
  out.push(`<text x="${bx + 22}" y="157" font-size="12" fill="#7f7ab0">${live ? "these jobs ran in this server process" : "read from data/ndi on this boot: no NDI call, nothing billed"}</text></g>`);

  // Pages: the layout skeleton NDI Parse returned, under a scanning beam.
  for (let p = 1; p <= pages; p += 1) {
    const X = pageX(p); const cited = x.facts.filter((f) => f.page === p).length;
    out.push(`<g>`);
    out.push(`<text x="${n(X)}" y="${n(py0 - 14)}" font-size="14" font-weight="700" letter-spacing="3" fill="${cited ? "#7df4ff" : "#6f6a9c"}">PAGE ${p}</text>`);
    out.push(`<text x="${n(X + pw)}" y="${n(py0 - 14)}" font-size="12" text-anchor="end" fill="${cited ? "#ff7be6" : "#6f6a9c"}">${cited ? `${cited} cited fact${cited === 1 ? "" : "s"}` : "nothing cited"}</text>`);
    out.push(`<clipPath id="clip${p}"><rect x="${n(X)}" y="${n(py0)}" width="${n(pw)}" height="${n(ph)}" rx="10"/></clipPath>`);
    out.push(`<rect x="${n(X)}" y="${n(py0)}" width="${n(pw)}" height="${n(ph)}" rx="10" fill="#0d0a2b" fill-opacity=".86" stroke="${cited ? "#00f0ff" : "#4b4680"}" stroke-width="${cited ? 1.8 : 1.2}" filter="url(#soft)"/>`);
    out.push(`<g clip-path="url(#clip${p})">`);
    for (const b of x.layout.find((l) => l.page === p)?.blocks ?? []) {
      const heading = b.type === "heading";
      out.push(`<rect x="${n(X + b.x0 * pw)}" y="${n(py0 + b.y0 * ph)}" width="${n((b.x1 - b.x0) * pw)}" height="${n(Math.max(1.6, (b.y1 - b.y0) * ph))}" rx="1.5" fill="${heading ? "#b47bff" : "#6fa8ff"}" fill-opacity="${heading ? ".42" : ".17"}"/>`);
    }
    out.push(`<rect class="beam" x="${n(X)}" y="${n(py0 - ph * 0.22)}" width="${n(pw)}" height="${n(ph * 0.22)}" fill="url(#beam)"><animate attributeName="y" values="${n(py0 - ph * 0.22)};${n(py0 + ph)}" dur="3.4s" begin="${n((p - 1) * 0.55)}s" repeatCount="indefinite"/></rect>`);
    out.push(`</g></g>`);
  }

  // Cited regions over the pages, and where each trace leaves its page.
  const starts: Array<{ x: number; y: number; gutter: number } | null> = x.facts.map(() => null);
  const onPageSoFar = new Map<number, number>();
  const badgeYs = new Map<number, number[]>();
  const badges: string[] = []; // drawn after every region, so no number is hidden under a neighbouring box
  x.facts.forEach((f, i) => {
    if (f.page < 1 || f.page > pages) return;
    const col = NEON[i % NEON.length]!; const X = pageX(f.page);
    const k = onPageSoFar.get(f.page) ?? 0; onPageSoFar.set(f.page, k + 1);
    const gutter = X + pw + 7 + (k % 4) * 6;
    const regions: PageRegion[] = f.regions.filter((r) => r.page === f.page);
    if (regions.length === 0) {
      // NDI gave the page but no box: a marker in the page margin, not a guessed position.
      const ax = X + pw - 16; const ay = py0 + 24 + k * 28;
      out.push(`<circle cx="${n(ax)}" cy="${n(ay)}" r="11" fill="${col}" filter="url(#glow)"/><text x="${n(ax)}" y="${n(ay + 4.5)}" font-size="13" font-weight="800" text-anchor="middle" fill="#0a0520">${i + 1}</text>`);
      starts[i] = { x: ax + 11, y: ay, gutter };
      return;
    }
    regions.forEach((r, j) => {
      const rx = X + r.x0 * pw - 3; const ry = py0 + r.y0 * ph - 3; const rw = (r.x1 - r.x0) * pw + 6; const rh = Math.max(8, (r.y1 - r.y0) * ph + 6);
      out.push(`<rect class="pulse" style="animation-delay:${n(-i * 0.29)}s" x="${n(rx)}" y="${n(ry)}" width="${n(rw)}" height="${n(rh)}" rx="4" fill="${col}" fill-opacity=".3" stroke="${col}" stroke-width="2" filter="url(#glow)"/>`);
      if (j > 0) return;
      // Number badge on the left edge; step left when the previous badge on this page is too close.
      const by = ry + rh / 2; const taken = badgeYs.get(f.page) ?? [];
      const bxo = rx - (taken.some((y) => Math.abs(y - by) < 21) ? 22 : 0);
      badgeYs.set(f.page, [...taken, by]);
      badges.push(`<circle cx="${n(bxo)}" cy="${n(by)}" r="10.5" fill="${col}" stroke="#0a0520" stroke-width="1.5"/><text x="${n(bxo)}" y="${n(by + 4.5)}" font-size="13" font-weight="800" text-anchor="middle" fill="#0a0520">${i + 1}</text>`);
      starts[i] = { x: rx + rw, y: by, gutter };
    });
  });
  out.push(...badges);

  // Circuit traces: region -> page gutter -> its own bus lane -> card gutter -> fact card. A particle rides each.
  x.facts.forEach((_f, i) => {
    const s = starts[i]; if (!s) return;
    const col = NEON[i % NEON.length]!; const c = card(i);
    const busY = busY0 + i * busStep;
    const cardGutter = c.cx - 8 - c.row * 6;
    const ty = c.cy + 27;
    const d = trace([[s.x, s.y], [s.gutter, s.y], [s.gutter, busY], [cardGutter, busY], [cardGutter, ty], [c.cx, ty]]);
    out.push(`<path d="${d}" fill="none" stroke="${col}" stroke-opacity=".22" stroke-width="5" stroke-linecap="round" filter="url(#soft)"/>`);
    out.push(`<path class="wire" style="animation-delay:${n(-i * 0.13)}s" d="${d}" fill="none" stroke="${col}" stroke-opacity=".95" stroke-width="1.8" stroke-linecap="round"/>`);
    out.push(`<circle class="dot" r="4.4" fill="#ffffff" filter="url(#glow)"><animateMotion dur="${n(2.6 + (i % 4) * 0.4)}s" begin="${n(i * 0.23)}s" repeatCount="indefinite" path="${d}"/></circle>`);
  });

  // Fact cards.
  // Kept short: it sits left of the first card gutter, so no trace runs through it.
  out.push(`<text x="${M}" y="${n(cy0 - 22)}" font-size="15" font-weight="700" letter-spacing="3" fill="#c4bff0">${x.facts.length} FACT${x.facts.length === 1 ? "" : "S"} · CHECKED WORD FOR WORD</text>`);
  x.facts.forEach((f, i) => {
    const col = NEON[i % NEON.length]!; const { cx, cy } = card(i);
    out.push(`<g><rect x="${n(cx)}" y="${n(cy)}" width="${n(cw)}" height="${ch}" rx="12" fill="#0b0824" fill-opacity=".92" stroke="${col}" stroke-opacity=".85" stroke-width="1.6" filter="url(#soft)"/>`);
    out.push(`<rect x="${n(cx)}" y="${n(cy + 8)}" width="6" height="${ch - 16}" rx="3" fill="${col}" filter="url(#glow)"/>`);
    out.push(`<circle cx="${n(cx + 32)}" cy="${n(cy + 27)}" r="13" fill="${col}" filter="url(#soft)"/><text x="${n(cx + 32)}" y="${n(cy + 32)}" font-size="15" font-weight="800" text-anchor="middle" fill="#0a0520">${i + 1}</text>`);
    out.push(`<text x="${n(cx + 56)}" y="${n(cy + 32)}" font-size="15" font-weight="700" fill="${col}">${esc(f.field)}</text>`);
    wrap(`“${f.quote}”`, Math.floor((cw - 46) / 7.55), 4).forEach((line, k) => {
      out.push(`<text x="${n(cx + 24)}" y="${n(cy + 60 + k * 18)}" font-size="12.5" fill="#ece9ff">${esc(line)}</text>`);
    });
    const meta = `page ${f.page}${f.confidence === null ? "" : `  ·  NDI confidence ${f.confidence.toFixed(2)}`}`;
    out.push(`<text x="${n(cx + cw - 16)}" y="${n(cy + ch - 13)}" font-size="12" text-anchor="end" fill="#a9a4dc">${esc(meta)}</text>`);
    out.push(`</g>`);
  });

  out.push(`<text x="${M}" y="${n(H - 46)}" font-size="12.5" fill="#8f89c4">${esc(x.sourceUrl)}</text>`);
  out.push(`<text x="${M}" y="${n(H - 24)}" font-size="12" fill="#7f7ab0">blue bars: layout blocks (NDI Parse)  ·  glowing boxes: cited regions (NDI Extract)  ·  quotes are NDI's output, shortened here to fit the card</text>`);
  out.push(`</svg>`);
  return out.join("\n");
}

/** The X-ray of a successful ingestion: document identity and job facts straight from the outcome. */
export function xrayOf(outcome: Extract<IngestOutcome, { ok: true }>): string {
  const { record, derived } = outcome;
  return renderXraySvg({
    title: record.document.title, publisher: record.document.publisher, sourceUrl: record.document.sourceUrl, sha256: record.document.sha256,
    source: outcome.cacheHit ? "ndi_cache" : "ndi_live",
    jobId: record.ndi.extract.jobId, parseJobId: record.ndi.parse.jobId, ranAt: record.ndi.ranAt, elapsedMs: record.ndi.elapsedMs,
    costUsd: totalCostUsd(record),
    pageCount: derived.pageCount ?? Math.max(1, ...derived.facts.map((f) => f.page)),
    layout: pageLayout(record.ndi.parse.job.result), facts: derived.facts,
  });
}
