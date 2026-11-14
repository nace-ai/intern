// Human-readable companion to report.json: one self-contained HTML page next to the screenshots.
import { writeFileSync } from "node:fs";
import path from "node:path";

interface Check { id: string; title: string; status: string; evidence: string; refs: string[] }
interface Step { id: string; title: string; status: string; checks: Check[] }
interface Report {
  startedAt: string; finishedAt: string; durationSec: number; ok: boolean; fatal: string | null; environment: Record<string, unknown>;
  steps: Step[]; acceptance: Array<{ ref: string; status: string | null; checks: string[] }>; measured: Record<string, unknown>; notes: string[]; screenshots: string[];
}

const esc = (s: unknown): string => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function writeHtmlReport(file: string, r: Report): void {
  const dir = path.dirname(file);
  const rel = (p: string): string => path.relative(dir, p).split(path.sep).join("/");
  const pass = r.steps.filter((s) => s.status === "pass").length;
  const m = r.measured;
  const tile = (k: string, v: unknown): string => `<div class="tile"><b>${esc(v ?? "n/a")}</b><span>${esc(k)}</span></div>`;
  const groups = new Map<string, string[]>();
  for (const s of r.screenshots) { const g = path.basename(s).replace(/-[ABC]\.png$/, ""); groups.set(g, [...(groups.get(g) ?? []), s]); }
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VICE EXCHANGE end-to-end run</title>
<style>
:root{--bg:#0b0720;--panel:#150d33;--line:#2c1f5e;--ink:#efeaff;--dim:#9a90c8;--pink:#ff5fa2;--aqua:#5dfdcb;--amber:#ffd166;--red:#ff4d6d}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,#3a1466 0,transparent 60%),radial-gradient(900px 500px at 0 0,#0d3b52 0,transparent 55%),var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1500px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:34px;letter-spacing:2px;margin:0}h1 i{font-style:normal;color:var(--pink);text-shadow:0 0 18px var(--pink)}h1 u{text-decoration:none;color:var(--aqua);text-shadow:0 0 18px var(--aqua)}
h2{margin:34px 0 12px;font-size:13px;letter-spacing:3px;color:var(--dim);text-transform:uppercase}
.sub{color:var(--dim)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.tile b{display:block;font-size:22px;color:var(--aqua);overflow-wrap:anywhere}.tile span{color:var(--dim);font-size:12px}
.step{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:12px;margin:10px 0;padding:0 14px}
.step.pass{border-left-color:var(--aqua)}.step.fail{border-left-color:var(--red)}.step.blocked{border-left-color:var(--amber)}
summary{cursor:pointer;padding:12px 0;display:flex;gap:12px;align-items:center}summary b{font-size:15px}
.chip{font:700 11px ui-monospace,Menlo,monospace;padding:3px 9px;border-radius:99px;letter-spacing:1px;text-transform:uppercase;white-space:nowrap}
.chip.pass{background:#0f3d33;color:var(--aqua)}.chip.fail{background:#4a1024;color:var(--red)}.chip.blocked{background:#4a3a10;color:var(--amber)}
.check{border-top:1px solid var(--line);padding:9px 0;display:grid;grid-template-columns:86px 1fr;gap:10px}.check pre{margin:4px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--dim);font:12px/1.45 ui-monospace,Menlo,monospace}
.refs{color:var(--pink);font-size:11px}.acc{display:flex;flex-wrap:wrap;gap:6px}.acc span{border:1px solid var(--line);border-radius:8px;padding:4px 8px;background:var(--panel)}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(440px,1fr));gap:12px}.gal a{display:block;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#000}.gal img{width:100%;display:block}
.gal figure{margin:0}.gal figcaption{font:12px ui-monospace,Menlo,monospace;color:var(--dim);padding:6px 2px}
li{color:var(--dim);margin:4px 0;overflow-wrap:anywhere}
</style></head><body><main>
<h1><i>VICE</i> <u>EXCHANGE</u> · end-to-end run</h1>
<div class="sub">${esc(r.startedAt)} · ${r.durationSec} s · ${pass}/${r.steps.length} steps passed · <span class="chip ${r.ok ? "pass" : "fail"}">${r.ok ? "all green" : "not green"}</span>${r.fatal ? ` · harness aborted: ${esc(r.fatal.split("\n")[0])}` : ""}</div>
<div class="tiles">
${tile("live Drex decisions", m.drex_decisions)}${tile("Drex round trip p50 (ms)", m.drex_latency_ms_p50)}${tile("fallback decisions", m.fallback_decisions)}
${tile("roles with a live decision", Array.isArray(m.roles_with_live_decision) ? (m.roles_with_live_decision as string[]).join(", ") : "n/a")}${tile("live cycles applied", m.live_applied_cycles)}${tile("events logged", m.events_logged)}
${tile("Chrome", r.environment.chrome)}${tile("WebGL", (r.environment.webgl as { renderer?: string } | undefined)?.renderer ?? "n/a")}
</div>
<h2>Acceptance tests and gates</h2><div class="acc">${r.acceptance.filter((a) => /^(AT-|Gate)/.test(a.ref)).map((a) => `<span><span class="chip ${esc(a.status)}">${esc(a.status)}</span> ${esc(a.ref)}</span>`).join("")}</div>
<h2>Steps</h2>
${r.steps.map((s) => `<details class="step ${esc(s.status)}"${s.status === "pass" ? "" : " open"}><summary><span class="chip ${esc(s.status)}">${esc(s.status)}</span><b>${esc(s.id)} · ${esc(s.title)}</b><span class="sub">${s.checks.filter((c) => c.status === "pass").length}/${s.checks.length} checks</span></summary>
${s.checks.map((c) => `<div class="check"><div><span class="chip ${esc(c.status)}">${esc(c.status)}</span></div><div>${esc(c.title)} <span class="refs">${esc(c.refs.join(" · "))}</span><pre>${esc(c.evidence)}</pre></div></div>`).join("")}</details>`).join("")}
${r.notes.length ? `<h2>Notes</h2><ul>${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
<h2>Screenshots (${r.screenshots.length})</h2>
${[...groups.entries()].map(([g, files]) => `<h2>${esc(g)}</h2><div class="gal">${files.map((f) => `<figure><a href="${esc(rel(f))}"><img loading="lazy" src="${esc(rel(f))}" alt="${esc(path.basename(f))}"></a><figcaption>${esc(path.basename(f))}</figcaption></figure>`).join("")}</div>`).join("")}
</main></body></html>`;
  writeFileSync(file, html);
}
