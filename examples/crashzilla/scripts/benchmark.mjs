// Drex vs the LLM baseline on 12 crashes (server must be running). Prints medians, costs and agreement as JSON.
const cases = [];
for (let i = 0; i < 12; i++) cases.push({ speedKmh: 30 + i * 6, belted: i % 3 !== 0, airbag: i % 4 !== 1, age: 20 + i * 5 });
const BASE = process.env.CRASHZILLA_URL || "http://localhost:4310";
const call = (input) => fetch(BASE + "/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input, compare: true }) }).then(r => r.json());
const t0 = performance.now();
const res = await Promise.all(cases.map(call));
const rows = res.map((r, i) => ({ i, drexMs: r.drex?.roundTripMs, drexEval: r.drex?.evaluation_time_ms, drexCost: r.drex?.cost, llmMs: r.llm?.ms, llmCost: r.llm?.cost, llmLive: r.llm && !r.llm.cached, dDest: r.drex?.answers?.destination?.choice, lDest: r.llm?.answers?.destination, risk: r.sim?.risk?.joint }));
const med = (a) => { a = a.filter(x => x != null).sort((x, y) => x - y); return a[Math.floor(a.length / 2)]; };
const out = { n: rows.length, wallMs: performance.now() - t0, drexMedMs: med(rows.map(r => r.drexMs)), llmMedMs: med(rows.map(r => r.llmMs)), drexMedCost: med(rows.map(r => r.drexCost)), llmMedCost: med(rows.map(r => r.llmCost)), live: rows.filter(r => r.llmLive).length, agree: rows.filter(r => r.dDest && r.lDest && r.dDest === r.lDest).length, rows };
console.log(JSON.stringify(out));
