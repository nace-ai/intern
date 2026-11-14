// Readers for results hub (/api/results/*), the single source for benchmark tables and charts.
// Rule: a benchmark's chart and primary column use lanes[].metrics[chart.metric] and nothing else.
import { prettyModel } from "./ladder.js";

const KNOWN_FLAGS = ["simulated", "projected", "partial", "subset", "pilot"];

export function laneKind(id, label = "") {
  const s = `${id} ${label}`.toLowerCase();
  if (/billproof-system|billproof system|^chain\b/.test(s)) return "system";
  if (/hybrid|checked|gpt_check|gpt check|then gpt/.test(s)) return "hybrid";
  if (/^(gpt|frontier)\b|gpt-6\.1-sol|gpt-6\.1 sol/.test(s)) return "gpt";
  if (/^drex|drex-max/.test(s)) return "drex";
  return "other";
}

// DeepInfra lanes (partner host): marked by provider, or a "via DeepInfra" label/id. Qwen only runs on DeepInfra here;
// the older token-router DeepSeek lane (deepseek__deepseek-v4.1-flash) is not DeepInfra.
// "Drex first, Qwen if unsure" lanes (thread QCOMBO), e.g. drex_then_qwen38max.
export const isCombo = (l) => /drex_then_|drex[- ]first,? qwen|drex \+ qwen|then qwen/i.test(`${l.id} ${l.label || ""}`);
const num = (x) => (typeof x === "number" && Number.isFinite(x) ? x : null);
const shareOf = (v) => (v && typeof v === "object" ? num(v.share) ?? num(v.value) ?? (num(v.k) != null && num(v.n) ? v.k / v.n : null) : num(v));
// Share of questions sent to Qwen on a combo lane, from whichever field the hub uses.
function routedShare(l, m) {
  for (const o of [l, l.extra, m, l.metrics]) {
    if (!o) continue;
    for (const k of ["questions_routed_share", "questions_routed", "routed_share", "sent_to_qwen", "escalation_rate", "share_routed", "cases_routed_share", "cases_routed"]) {
      const v = shareOf(o[k]); if (v != null) return v;
    }
  }
  return null;
}

export function isDeepInfra(l) {
  const s = `${l.id} ${l.label || ""} ${l.provider || ""} ${l.extra?.provider || ""}`.toLowerCase();
  return /deepinfra/.test(s) || /^deepseek-ai\//i.test(String(l.id)) || /qwen/.test(s);
}
// Which DeepInfra model a lane is: "max" (stage comparison), "q27", "ds" (smaller lanes), or null.
export function diModel(l) {
  const s = `${l.id} ${l.label || ""}`.toLowerCase();
  if (/qwen3\.8-max|qwen3\.8 max/.test(s)) return "max";
  if (/qwen3\.8-27b|qwen3\.8 27b/.test(s)) return "q27";
  if (/qwen/.test(s)) return "qwen";
  if (/deepseek/.test(s)) return "ds";
  return null;
}
const DI_NAMES = { max: "Qwen3.8-Max", q27: "Qwen3.8-27B", ds: "DeepSeek V4.1 Flash" };

export function normLane(l, metric) {
  const m = l.metrics?.[metric] || null;
  const flags = KNOWN_FLAGS.filter((f) => l[f] || m?.[f]);
  const combo = isCombo(l);
  const di = !combo && isDeepInfra(l), dim = di ? diModel(l) : null;
  const base = /\//.test(l.label || "") ? prettyModel(l.label) : l.label || prettyModel(l.id);
  return {
    id: String(l.id),
    label: di && !/deepinfra/i.test(base) ? `${DI_NAMES[dim] || base} via DeepInfra` : base,
    short: di ? DI_NAMES[dim] || base.replace(/\s*\(?via DeepInfra\)?/i, "") : base,
    di, dim,
    kind: combo ? "combo" : di ? "di" : laneKind(l.id, l.label), combo,
    routed: combo ? routedShare(l, m) : null, escModel: combo ? l.escalate_model || l.extra?.escalate_model || null : null,
    acc: m?.value ?? null, k: m?.k ?? null, n: m?.n ?? null, ci: m?.ci95 ?? null,
    nCases: l.extra?.n_cases ?? l.n_cases ?? null, nExpected: l.extra?.n_expected ?? l.n_expected ?? null,
    cost: l.cost_per_1000_usd ?? null, costBasis: l.cost_basis || null,
    latency: l.latency_p50_ms ?? null,
    flags, note: l.note || m?.note || null,
    metrics: l.metrics || {},
  };
}

export function normBench(b) {
  const metric = b.chart?.metric || b.metric;
  const lanes = (b.lanes || []).map((l) => normLane(l, metric));
  const flags = KNOWN_FLAGS.filter((f) => b[f]);
  if (lanes.some((l) => l.flags.includes("partial")) && !flags.includes("partial")) flags.push("partial lane");
  return {
    id: b.id, title: b.title || b.id, tests: b.tests || "", n: b.n, split: b.split || null,
    metric, metricDef: b.metric_definition || "", chartNote: b.chart?.note || "",
    flags, addedLater: !!b.added_after_first_results,
    lanes, otherMetrics: [...new Set(lanes.flatMap((l) => Object.keys(l.metrics)))].filter((k) => k !== metric),
    secondary: (b.secondary || []).map((s) => ({ id: s.id, metric: s.metric, def: s.metric_definition, lanes: (s.lanes || []).map((l) => normLane({ ...l, metrics: { [s.metric]: l } }, s.metric)) })),
    breakdowns: normBreakdowns(b.breakdowns),
    notes: [].concat(b.notes || []), sources: [].concat(b.sources || []), computed_at: b.computed_at || null,
  };
}

export const metricName = (m) => String(m || "").replace(/__/g, " · ").replace(/_/g, " ");

// Best Drex/BillProof lane (GPT hybrids only when nothing else) and the GPT-6.1 Sol lane on the chart metric
// (partial lanes excluded from "best").
export function headline(b) {
  const ok = (kinds) => b.lanes.filter((l) => kinds.includes(l.kind) && l.acc != null && !l.flags.includes("partial"));
  const ours = ok(["system", "drex"]).length ? ok(["system", "drex"]) : ok(["hybrid"]);
  const best = ours.sort((x, y) => y.acc - x.acc || (x.cost ?? Infinity) - (y.cost ?? Infinity))[0] || null;
  const gpt = b.lanes.find((l) => l.kind === "gpt" && l.acc != null) || null;
  return { best, gpt };
}

// Stage view: Drex / BillProof lanes and DeepInfra lanes. GPT-6.1 Sol, token-router models and GPT hybrids stay
// behind "More models" (b.lanes keeps everything).
export const onStage = (l) => l.di || ["system", "drex", "combo"].includes(l.kind);

// The stage comparison: Qwen3.8-Max via DeepInfra, else the best DeepInfra Qwen lane (named as itself), else null.
// Smaller DeepInfra lanes alongside. Partial lanes count (they carry the partial flag and n).
export function qwenPick(b) {
  const di = b.lanes.filter((l) => l.di && l.acc != null);
  const best = (xs) => [...xs].sort((x, y) => (x.flags.includes("partial") - y.flags.includes("partial")) || y.acc - x.acc)[0] || null;
  const max = best(di.filter((l) => l.dim === "max"));
  const qwen = max || best(di.filter((l) => ["q27", "qwen"].includes(l.dim)));
  const small = di.filter((l) => l !== qwen && ["q27", "ds", "qwen"].includes(l.dim));
  return { qwen, isMax: !!max, small };
}
// Stage trio for a benchmark: Drex, Qwen3.8-Max via DeepInfra (or the best Qwen lane, named), Drex + Qwen.
// Combo: prefer the one escalating to Qwen3.8-Max; non-partial first.
export function trio(b) {
  const { qwen, isMax } = qwenPick(b);
  const combos = b.lanes.filter((l) => l.combo && l.acc != null);
  const isMaxCombo = (l) => /max/i.test(`${l.id} ${l.label} ${l.escModel || ""}`);
  const combo = [...combos].sort((x, y) => isMaxCombo(y) - isMaxCombo(x) || x.flags.includes("partial") - y.flags.includes("partial") || y.acc - x.acc)[0] || null;
  return { drex: headline(b).best, qwen, isMax, combo };
}
export const partialText = (l) => (l?.flags.includes("partial") ? `partial, n=${l.nCases ?? l.n ?? "?"}` : null);

// breakdowns[lane][dimension][group] = { acc, n } or { measure: { value, k, n } } → per dimension: groups × lanes, by measure.
function normBreakdowns(x) {
  if (!x || typeof x !== "object") return [];
  const dims = {};
  for (const [lane, byDim] of Object.entries(x)) {
    for (const [dim, groups] of Object.entries(byDim || {})) {
      if (!groups || typeof groups !== "object") continue;
      const d = (dims[dim] ||= { dim, measures: new Set(), groups: {} });
      for (const [g, v] of Object.entries(groups || {})) {
        if (!v || typeof v !== "object") continue; // bare counts are not a measure
        const cell = (d.groups[g] ||= { key: g, n: null, by: {} });
        if (v && "acc" in v) { d.measures.add("accuracy"); (cell.by.accuracy ||= {})[lane] = v.acc; cell.n ??= v.n; }
        else for (const [meas, env] of Object.entries(v || {})) {
          if (env && typeof env === "object" && "value" in env) { d.measures.add(meas); (cell.by[meas] ||= {})[lane] = env.value; cell.n ??= env.n; }
        }
      }
    }
  }
  return Object.values(dims).map((d) => ({ dim: d.dim.replace(/^by_/, ""), measures: [...d.measures], groups: Object.values(d.groups) }));
}

// /cases: { n, offset, limit, cases: [{ case_id, gold_summary, lanes: { lane: { correct, choice, confidence, k, n, wrong[] } } }] }
export function normCases(r) {
  return {
    total: r?.n ?? null, offset: r?.offset ?? 0, limit: r?.limit ?? null,
    cases: (r?.cases || []).map((c) => ({ id: String(c.case_id ?? c.id), gold: c.gold_summary ?? null, lanes: c.lanes || {} })),
  };
}

// /case/{id}: { case_id, gold_summary, inputs_summary, gold, lanes: { lane: { correct, choice, confidence, k, n, wrong, answers } } }
export function normCase(r) {
  return { id: String(r?.case_id ?? ""), goldSummary: r?.gold_summary ?? null, inputs: r?.inputs_summary ?? null, gold: r?.gold ?? null, lanes: r?.lanes || {} };
}
