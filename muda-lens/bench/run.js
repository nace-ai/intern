#!/usr/bin/env node
// Apex Predator benchmark: Drex vs. a frontier LLM vs. rules, on ground-truth fab cases.
//   node bench/run.js            # all configured contenders, 70 cases
//   node bench/run.js --n 14     # quick run (2 cases per fault type)
// Writes bench/results.json, which the dashboard's "Arena" section displays.
const fs = require("node:fs"), path = require("node:path");
try { process.loadEnvFile?.(path.join(__dirname, "..", ".env")); } catch {}
const { buildCases, QUESTIONS } = require("./cases.js");
const C = require("./contenders.js");

const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : undefined; };
const N = Number(arg("--n") || 70), CONC = Number(arg("--concurrency") || 4);
// --split dev: separate seeds used ONLY for tuning; the default test split is what we report
const DEV = arg("--split") === "dev";
const ONLY = arg("--only");

async function pool(items, n, fn) { const out = []; let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const j = i++; out[j] = await fn(items[j]); } })); return out; }

(async () => {
  const cases = DEV ? buildCases(N, 777, 50000) : buildCases(N);
  const names = ONLY ? ONLY.split(",") : ["drex", "claude", "rules"];
  const results = { split: DEV ? "dev" : "test", stateVersion: process.env.STATE_VERSION || "v2", generatedAt: new Date().toISOString(), cases: cases.length, questions: Object.keys(QUESTIONS), contenders: {} };

  for (const name of names) {
    const fn = C[name];
    if (!fn.enabled()) { console.log(`- ${name}: not configured, skipped`); results.contenders[name] = { label: fn.label(), skipped: true }; continue; }
    console.log(`> ${fn.label()} on ${cases.length} cases...`);
    const runs = await pool(cases, name === "rules" ? 1 : CONC, async c => {
      try { const r = await fn(c.state); return { id: c.id, kind: c.kind, truth: c.truth, ...r }; }
      catch (e) { return { id: c.id, kind: c.kind, truth: c.truth, error: e.message }; }
    });
    const ok = runs.filter(r => !r.error);
    const perQ = Object.fromEntries(Object.keys(QUESTIONS).map(q => [q, ok.filter(r => r.answers[q] === r.truth[q]).length / runs.length]));
    const exact = ok.filter(r => Object.keys(QUESTIONS).every(q => r.answers[q] === r.truth[q])).length / runs.length;
    const byKind = {};
    for (const r of runs) { const k = (byKind[r.kind] ||= { n: 0, exact: 0 }); k.n++; if (!r.error && Object.keys(QUESTIONS).every(q => r.answers[q] === r.truth[q])) k.exact++; }
    const lat = ok.map(r => r.latencyMs).sort((a, b) => a - b);
    results.contenders[name] = {
      label: fn.label(), errors: runs.length - ok.length, exact, perQuestion: perQ, byKind,
      latencyMsP50: lat[Math.floor(lat.length / 2)] || 0, latencyMsP90: lat[Math.floor(lat.length * 0.9)] || 0,
      costPer1kDecisionsUsd: ok.length ? ok.reduce((a, r) => a + r.costUsd, 0) / ok.length * 1000 : 0,
      avgInputTokens: ok.length ? Math.round(ok.reduce((a, r) => a + r.inputTokens, 0) / ok.length) : 0,
      sampleErrors: runs.filter(r => r.error).slice(0, 3).map(r => r.error),
      runs: runs.map(({ id, kind, truth, answers, confidence, latencyMs, costUsd, error }) => ({ id, kind, truth, answers, confidence, latencyMs, costUsd, error })),
    };
    const s = results.contenders[name];
    console.log(`  all-4-correct ${(exact * 100).toFixed(1)}% | p50 ${s.latencyMsP50} ms | $${s.costPer1kDecisionsUsd.toFixed(4)} / 1k decisions | errors ${s.errors}`);
  }
  fs.writeFileSync(path.join(__dirname, DEV ? "results-dev.json" : "results.json"), JSON.stringify(results, null, 2));
  console.log("Wrote bench/" + (DEV ? "results-dev.json" : "results.json"));
})();
