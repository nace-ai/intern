// The three contenders in the Apex Predator benchmark.
//   drex   - Nace.AI Drex decision model: one pass, typed choice questions, probabilities back
//   claude - frontier LLM baseline (Claude Opus 5.5) with JSON-schema structured output
//   rules  - classic hand-written Six Sigma heuristics (no AI), the "do nothing smart" floor
const { QUESTIONS, HISTORY } = require("./cases.js");

// ---------------- Drex ----------------
// Configure via env (see .env.example). Request/response follow Nace's System One format:
//   POST {DREX_BASE_URL}{DREX_PATH}  { model, state, questions: { name: { type:"choice", instructions, criteria } } }
//   -> { answers: { name: { choice, confidence, probabilities } }, usage: { input_tokens }, latency_ms }
const drexCfg = () => ({
  base: process.env.DREX_BASE_URL, key: process.env.DREX_API_KEY || process.env.NACE_API_KEY,
  model: process.env.DREX_MODEL || "drex-v2.0", path: process.env.DREX_PATH || "/v1/systemone",
  pricePerMTok: Number(process.env.DREX_PRICE_PER_MTOK || 0.06),
});

function drexQuestions() {
  return Object.fromEntries(Object.entries(QUESTIONS).map(([name, q]) => [name, { type: "choice", instructions: q.instructions, criteria: q.options }]));
}

// Low-level call. `images` (Drex Max only) = array of data:image/...;base64 URLs, max 8,
// whole body <= 4 MiB. Refer to them in text as "Image 1", "Image 2", ...
async function drexCall({ state, questions, images }) {
  const c = drexCfg();
  if (!c.base) throw new Error("DREX_BASE_URL not set");
  const res = await fetch(c.base.replace(/\/$/, "") + c.path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nace-Client": "muda-lens/1", ...(c.key ? { Authorization: `Bearer ${c.key}` } : {}) },
    body: JSON.stringify({ model: c.model, state, questions, ...(images?.length ? { images } : {}) }),
  });
  if (!res.ok) {
    const text = await res.text();
    if (/Security Checkpoint|<!DOCTYPE/i.test(text))
      throw new Error(`Drex HTTP ${res.status}: the Drex API's firewall is blocking this network right now. Try another connection (e.g. a phone hotspot) or wait a few minutes.`);
    throw new Error(`Drex HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  return res.json();
}

async function drex(state) {
  const c = drexCfg();
  const t0 = Date.now();
  const body = await drexCall({ state, questions: drexQuestions() });
  const answers = body.answers || body; // tolerate providers that return answers at top level
  const out = {}, confidence = {};
  for (const name of Object.keys(QUESTIONS)) { out[name] = answers[name]?.choice; confidence[name] = answers[name]?.confidence; }
  const inTok = body.usage?.input_tokens || 0;
  return { answers: out, confidence, latencyMs: Date.now() - t0, inputTokens: inTok, outputTokens: 0, costUsd: inTok / 1e6 * c.pricePerMTok };
}
drex.enabled = () => !!drexCfg().base;
drex.label = () => `Drex (${drexCfg().model})`;

// ---------------- Claude (frontier LLM baseline) ----------------
const CLAUDE_MODEL = () => process.env.CLAUDE_MODEL || "claude-opus-5-5";
const CLAUDE_PRICE = { "claude-opus-5-5": [4, 20], "claude-sonnet-5-5": [2, 10], "claude-haiku-5-5": [0.1, 0.5] };
let client;

function claudePrompt(state) {
  const qs = Object.entries(QUESTIONS).map(([name, q]) =>
    `${name}: ${q.instructions}\n` + Object.entries(q.options).map(([k, v]) => `  - ${k}: ${v}`).join("\n")).join("\n\n");
  return `You are a Lean Six Sigma black belt reviewing one week of data from a semiconductor line.\n` +
    `Compare current_week against historical_baseline and answer each question with exactly one option key.\n\n${qs}\n\nDATA:\n${JSON.stringify(state)}`;
}

async function claude(state) {
  if (!client) { const Anthropic = require("@anthropic-ai/sdk"); client = new (Anthropic.default || Anthropic)(); }
  const model = CLAUDE_MODEL();
  const schema = {
    type: "object", additionalProperties: false, required: Object.keys(QUESTIONS),
    properties: Object.fromEntries(Object.entries(QUESTIONS).map(([n, q]) => [n, { type: "string", enum: Object.keys(q.options) }])),
  };
  const t0 = Date.now();
  const msg = await client.beta.messages.create({
    model, max_tokens: 16000,
    betas: ["server-side-fallback-2026-07-01"], fallbacks: "default",
    output_config: { format: { type: "json_schema", schema } },
    messages: [{ role: "user", content: claudePrompt(state) }],
  });
  if (msg.stop_reason === "refusal") throw new Error("Claude refused");
  const text = msg.content.filter(b => b.type === "text").map(b => b.text).join("");
  const [pin, pout] = CLAUDE_PRICE[model] || [4, 20];
  const u = msg.usage;
  return { answers: JSON.parse(text), latencyMs: Date.now() - t0, inputTokens: u.input_tokens, outputTokens: u.output_tokens,
           costUsd: (u.input_tokens * pin + u.output_tokens * pout) / 1e6 };
}
claude.enabled = () => !!(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN || process.env.ANTHROPIC_PROFILE);
claude.label = () => `Claude (${CLAUDE_MODEL()})`;

// ---------------- Rules (no-AI floor) ----------------
// Picks the step/metric with the largest relative change vs. baseline.
async function rules(state) {
  const t0 = Date.now();
  const cur = state.current_week, base = Object.fromEntries((state.historical_baseline || HISTORY).map(s => [s.step, s]));
  let best = { score: 0 };
  const consider = (score, step, a) => { if (score > best.score) best = { score, step, ...a }; };
  for (const s of cur) {
    const b = base[s.step];
    const ratio = (x, y) => (x + 1) / (y + 1);
    consider(ratio(s.queue_h, b.queue_h), s.step, { root_waste: "waiting", countermeasure: "conwip_dbr", disposition: "release" });
    consider(ratio(s.transport_m_per_lot, b.transport_m_per_lot), s.step, { root_waste: "transportation", countermeasure: "relayout_amhs", disposition: "release" });
    consider(ratio(s.extra_inspections, b.extra_inspections), s.step, { root_waste: "overprocessing", countermeasure: "sampling_plan", disposition: "release" });
    consider(ratio(s.manual_overrides, b.manual_overrides), s.step, { root_waste: "skills", countermeasure: "standard_work", disposition: "release" });
    consider(ratio(s.dpmo, b.dpmo), s.step, { root_waste: "defects", countermeasure: "root_cause_8d", disposition: "rework" });
    consider(s.sigma / b.sigma, s.step, { root_waste: "defects", countermeasure: "doe_variation", disposition: "hold" });
    consider(1 + Math.abs(s.mean - b.mean) / b.sigma, s.step, { root_waste: "defects", countermeasure: "recenter_spc", disposition: "hold" });
  }
  const { score, step, ...a } = best;
  return { answers: { ...a, culprit_step: step }, latencyMs: Date.now() - t0, inputTokens: 0, outputTokens: 0, costUsd: 0 };
}
rules.enabled = () => true;
rules.label = () => "Rule-based SPC (no AI)";

module.exports = { drex, claude, rules, drexCall, drexCfg, drexQuestions, claudePrompt };
