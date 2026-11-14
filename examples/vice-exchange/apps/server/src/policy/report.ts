// Terminal report for `npm run ndi:ingest`: document, NDI jobs, a page map of where each fact sits, and the facts.
// Pure string building. Everything shown is read from the ingestion outcome; nothing is made up for display.
import type { PolicyDocumentSpec } from "./document";
import type { PageRegion, PolicyFact } from "./facts";
import type { IngestOutcome } from "./ingest";

export interface ReportOptions { color: boolean; document: PolicyDocumentSpec; keyConfigured: boolean; width?: number }

type Rgb = [number, number, number];
const PINK: Rgb = [255, 43, 214]; const CYAN: Rgb = [0, 240, 255]; const PURPLE: Rgb = [157, 77, 255];
const YELLOW: Rgb = [255, 228, 94]; const GREEN: Rgb = [61, 255, 154]; const RED: Rgb = [255, 85, 100];
const GRAY: Rgb = [120, 124, 150]; const WHITE: Rgb = [236, 238, 255];
/** One colour per fact, cycling: the page map and the fact cards share it. */
const FACT_COLORS: Rgb[] = [PINK, CYAN, YELLOW, GREEN, PURPLE, [255, 140, 60], [90, 170, 255], [255, 110, 170], [170, 255, 90], [120, 255, 230]];

interface Pen { fg(c: Rgb, s: string): string; bg(c: Rgb, s: string): string; bold(s: string): string; grad(a: Rgb, b: Rgb, s: string): string }
function pen(color: boolean): Pen {
  if (!color) return { fg: (_c, s) => s, bg: (_c, s) => s, bold: (s) => s, grad: (_a, _b, s) => s };
  const fg = (c: Rgb, s: string): string => `\x1b[38;2;${c[0]};${c[1]};${c[2]}m${s}\x1b[39m`;
  return {
    fg,
    bg: (c, s) => `\x1b[48;2;${c[0]};${c[1]};${c[2]}m\x1b[38;2;10;8;24m${s}\x1b[39m\x1b[49m`,
    bold: (s) => `\x1b[1m${s}\x1b[22m`,
    grad: (a, b, s) => {
      const chars = [...s]; const n = Math.max(1, chars.length - 1);
      return chars.map((ch, i) => fg([0, 1, 2].map((k) => Math.round(a[k]! + ((b[k]! - a[k]!) * i) / n)) as Rgb, ch)).join("");
    },
  };
}

function wrap(text: string, width: number): string[] {
  const out: string[] = []; let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (line && line.length + 1 + word.length > width) { out.push(line); line = word; } else line = line ? `${line} ${word}` : word;
  }
  if (line) out.push(line);
  return out.length > 0 ? out : [""];
}

const secs = (ms: number): string => `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
const short = (hash: string): string => `${hash.slice(0, 12)}…${hash.slice(-8)}`;

/** Draws every page as a small sheet; a cell lights up in the colour of the fact NDI cited there. */
function pageMap(p: Pen, facts: PolicyFact[], pageCount: number, width: number): string[] {
  const W = 18; const H = 11; const gap = 2;
  const perRow = Math.max(1, Math.floor((width + gap) / (W + 2 + gap)));
  const sheets: string[][] = [];
  for (let page = 1; page <= pageCount; page += 1) {
    const cells: Array<Array<number | null>> = Array.from({ length: H }, () => Array.from({ length: W }, () => null));
    const onPage = facts.map((f, i) => ({ f, i })).filter(({ f }) => f.page === page);
    let placedWithoutRegion = 0;
    for (const { f, i } of onPage) {
      const regions: PageRegion[] = f.regions.filter((r) => r.page === page);
      if (regions.length === 0) {
        // NDI gave a page but no box: show a marker row in the margin, never a made-up position.
        const row = Math.min(H - 1, placedWithoutRegion); placedWithoutRegion += 1;
        cells[row]![W - 1] = i;
        continue;
      }
      for (const r of regions) {
        const x0 = Math.max(0, Math.floor(r.x0 * W)); const x1 = Math.min(W - 1, Math.ceil(r.x1 * W) - 1);
        const y0 = Math.max(0, Math.floor(r.y0 * H)); const y1 = Math.min(H - 1, Math.max(y0, Math.ceil(r.y1 * H) - 1));
        for (let y = y0; y <= y1; y += 1) for (let x = x0; x <= Math.max(x0, x1); x += 1) cells[y]![x] = i;
      }
    }
    const label = ` page ${page} `;
    const top = `╭${label}${"─".repeat(Math.max(0, W - label.length))}╮`;
    const lines = [p.fg(onPage.length > 0 ? CYAN : GRAY, top)];
    for (const row of cells) {
      let body = "";
      for (const cell of row) body += cell === null ? p.fg(GRAY, "·") : p.bg(FACT_COLORS[cell % FACT_COLORS.length]!, String((cell + 1) % 10));
      lines.push(`${p.fg(onPage.length > 0 ? CYAN : GRAY, "│")}${body}${p.fg(onPage.length > 0 ? CYAN : GRAY, "│")}`);
    }
    const count = onPage.length === 0 ? "no facts" : `${onPage.length} fact${onPage.length === 1 ? "" : "s"}`;
    const foot = ` ${count} `;
    lines.push(p.fg(onPage.length > 0 ? CYAN : GRAY, `╰${foot}${"─".repeat(Math.max(0, W - foot.length))}╯`));
    sheets.push(lines);
  }
  const out: string[] = [];
  for (let i = 0; i < sheets.length; i += perRow) {
    const row = sheets.slice(i, i + perRow);
    for (let l = 0; l < H + 2; l += 1) out.push(row.map((s) => s[l]).join(" ".repeat(gap)));
    out.push("");
  }
  return out;
}

export function renderReport(outcome: IngestOutcome, o: ReportOptions): string {
  const p = pen(o.color);
  const width = Math.max(72, Math.min(o.width ?? process.stdout.columns ?? 100, 120));
  const L: string[] = [];
  const row = (k: string, v: string): void => { L.push(`  ${p.fg(PURPLE, k.padEnd(13))}${v}`); };
  const head = (title: string): void => { L.push(""); L.push(p.bold(p.grad(PINK, CYAN, `▌ ${title} ${"━".repeat(Math.max(4, width - title.length - 4))}`))); };

  const title = "  NDI POLICY INGEST  ·  real document → Nace NDI → cited facts for the police NPCs  ";
  const inner = Math.max(title.length, width - 2);
  L.push(p.grad(PINK, PURPLE, `╔${"═".repeat(inner)}╗`));
  L.push(p.grad(PINK, CYAN, `║${title.padEnd(inner)}║`));
  L.push(p.grad(PURPLE, CYAN, `╚${"═".repeat(inner)}╝`));

  const doc = o.document;
  head("DOCUMENT");
  row("title", p.bold(p.fg(WHITE, doc.title)));
  row("publisher", doc.publisher);
  row("url", p.fg(CYAN, doc.sourceUrl));
  row("revision", doc.revision);
  row("sha-256", `${doc.sha256}`);

  if (!outcome.ok) {
    row("size", `${doc.bytes.toLocaleString("en-US")} bytes, ${doc.pages} pages (pinned)`);
    head("RESULT");
    L.push(`  ${p.bold(p.fg(RED, "✖ NO NDI FACTS"))}  ${p.fg(YELLOW, outcome.reason)}`);
    for (const line of wrap(outcome.message, width - 4)) L.push(`  ${line}`);
    if (outcome.jobId) row("ndi job", outcome.jobId);
    row("key", o.keyConfigured ? "NACE_API_KEY is configured" : "NACE_API_KEY is NOT configured");
    L.push("");
    L.push(p.fg(GRAY, "  The policy view stays null: nothing is shown as coming from NDI."));
    return L.join("\n");
  }

  const { record, derived } = outcome;
  row("size", `${record.document.bytes.toLocaleString("en-US")} bytes, ${derived.pageCount ?? doc.pages} pages${derived.pageCount !== null ? " (page count from NDI)" : " (pinned)"}`);
  row("retrieved", `${record.document.retrievedAt}${record.document.httpLastModified ? p.fg(GRAY, `   (publisher last-modified ${record.document.httpLastModified})`) : ""}`);
  row("hash check", p.fg(GREEN, `✔ downloaded bytes match the pinned SHA-256 (${short(record.document.sha256)})`));

  head("NDI JOBS");
  const badge = outcome.cacheHit
    ? p.bg(YELLOW, " CACHE HIT ") + p.fg(YELLOW, "  no NDI call in this run, nothing billed: showing the original real job")
    : p.bg(GREEN, " LIVE NDI RUN ") + p.fg(GREEN, "  these jobs ran just now");
  L.push(`  ${badge}`);
  const timing = (job: unknown): string => {
    const t = (job as { timing?: { total_ms?: unknown } | null } | null)?.timing;
    return t && typeof t.total_ms === "number" ? p.fg(GRAY, `  (server ${secs(t.total_ms)})`) : "";
  };
  const cost = (job: unknown): string => {
    const c = (job as { cost?: { total_usd?: unknown } | null } | null)?.cost;
    return c && typeof c.total_usd === "string" ? `$${c.total_usd}` : "not reported";
  };
  row("extract job", `${p.bold(p.fg(PINK, record.ndi.extract.jobId))}  ${secs(record.ndi.extract.elapsedMs)}${timing(record.ndi.extract.job)}  cost ${cost(record.ndi.extract.job)}`);
  row("parse job", `${p.fg(CYAN, record.ndi.parse.jobId)}  ${secs(record.ndi.parse.elapsedMs)}${timing(record.ndi.parse.job)}  cost ${cost(record.ndi.parse.job)}`);
  row("ran at", record.ndi.ranAt);
  row("elapsed", `${p.bold(secs(record.ndi.elapsedMs))} for upload + parse + extract of the original run${outcome.cacheHit ? p.fg(GRAY, `   (this cached read: ${outcome.tookMs} ms)`) : ""}`);
  const id = derived.identity;
  row("ndi read", `order ${id.order_number ?? "?"}  "${id.order_title ?? "?"}"  rev ${id.revision_date ?? "?"}  eff ${id.effective_date ?? "?"}`);

  head("WHERE THE FACTS ARE");
  if (derived.pageCount !== null) L.push(...pageMap(p, derived.facts, derived.pageCount, width - 2).map((l) => `  ${l}`));
  if (derived.facts.some((f) => f.regions.length === 0)) L.push(p.fg(GRAY, "  A digit in the right margin = NDI cited the page without a region box."));

  head(`FACTS  ${derived.facts.length} verified word for word${derived.rejected.length > 0 ? `, ${derived.rejected.length} not admitted` : ""}`);
  derived.facts.forEach((f, i) => {
    const col = FACT_COLORS[i % FACT_COLORS.length]!;
    const conf = f.confidence === null ? p.fg(GRAY, "confidence not given") : `confidence ${f.confidence.toFixed(2)} ${p.fg(col, "█".repeat(Math.round(f.confidence * 10)))}${p.fg(GRAY, "░".repeat(10 - Math.round(f.confidence * 10)))}`;
    L.push("");
    L.push(`  ${p.bg(col, ` ${(i + 1) % 10} `)} ${p.bold(p.fg(col, f.field))}  ${p.fg(CYAN, `page ${f.page}`)}  ${conf}`);
    for (const line of wrap(`“${f.quote}”`, width - 8)) L.push(`      ${p.fg(WHITE, line)}`);
    for (const line of wrap(`in the game: ${f.gameUse}`, width - 8)) L.push(`      ${p.fg(GRAY, line)}`);
  });
  if (derived.rejected.length > 0) {
    L.push("");
    for (const r of derived.rejected) L.push(`  ${p.fg(RED, "✖")} ${r.field}  ${p.fg(GRAY, `not admitted: ${r.reason}${r.ndiStatus ? ` (NDI status ${r.ndiStatus})` : ""}`)}`);
  }
  for (const w of derived.warnings) L.push(`  ${p.fg(YELLOW, "!")} NDI warning: ${w}`);

  L.push("");
  L.push(p.fg(GRAY, `  cache: ${outcome.cachePath}`));
  return L.join("\n");
}
