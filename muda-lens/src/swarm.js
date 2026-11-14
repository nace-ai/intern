// Swarm mode: 12 station agents + 1 dispatcher. Every agent's decision is a Drex call.
//
//   Station agent (x12): reads only its own step (this week vs. its normal) and asks Drex:
//     needs_help (yes/no), problem (which waste), urgency (scale), ask (what it wants).
//   Dispatcher: sees every station's report and a fixed number of engineers, and asks
//     Drex where each engineer goes, one pick at a time, so no station is picked twice.
const F = require("./engine.js");
const { summarize, changes, HISTORY, QUESTIONS, PRIORITY } = require("../bench/cases.js");
const { drexCall, drexCfg } = require("../bench/contenders.js");

const URGENCY = ["Can wait", "This week", "This shift", "Right now"];
const STATION_QUESTIONS = {
  needs_help: { type: "noul", instructions: "Does this station need an engineer this shift? Answer yes only for a real change beyond normal noise." },
  problem: { type: "choice", instructions: "If something is wrong at this station, which Lean waste is it? If nothing is wrong, pick the closest match.", criteria: QUESTIONS.root_waste.options },
  urgency: { type: "score", instructions: "How urgent is this station's problem for product quality and line output?", criteria: URGENCY },
  ask: { type: "choice", instructions: "What should this station ask for?", criteria: {
    engineer_now: "An engineer at the tool this shift",
    watch: "Keep watching; flag if it gets worse",
    nothing: "Nothing; the station is running normally",
  } },
};

async function pool(items, n, fn) { const out = []; let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const j = i++; out[j] = await fn(items[j], j); } })); return out; }

async function runStation(cur, base, ch) {
  const state = {
    role: `You are the agent for the ${cur.name} station on a power-semiconductor line. You only see your own station.`,
    plant_rule: PRIORITY,
    this_week: cur, normal_week: base,
    significant_changes: ch ? ch.significant_changes : "none beyond normal noise",
  };
  const t0 = Date.now();
  const body = await drexCall({ state, questions: STATION_QUESTIONS });
  const a = body.answers || {};
  const urg = Math.max(0, Math.min(URGENCY.length - 1, Math.round(a.urgency?.score ?? 0)));
  return {
    step: cur.step, name: cur.name,
    needsHelp: a.needs_help?.noul ?? 0,
    problem: a.problem?.choice, problemConf: a.problem?.confidence,
    urgency: URGENCY[urg], urgencyScore: a.urgency?.score ?? 0, urgencyConf: a.urgency?.confidence,
    ask: a.ask?.choice, askConf: a.ask?.confidence,
    changes: ch ? ch.significant_changes : [],
    latencyMs: Date.now() - t0, tokens: body.usage?.input_tokens || 0,
  };
}

async function dispatch(reports, engineers) {
  const picks = [], calls = [];
  const candidates = reports.filter(r => r.needsHelp >= 0.5 || r.ask === "engineer_now");
  for (let k = 0; k < engineers; k++) {
    const open = candidates.filter(r => !picks.some(p => p.step === r.step));
    if (!open.length) break;
    const criteria = Object.fromEntries(open.map(r => [r.step,
      `${r.name}: ${String(r.problem).replace(/_/g, " ")}, urgency ${r.urgency}, needs help ${Math.round(r.needsHelp * 100)}%`]));
    const state = {
      role: "You are the shift dispatcher. Stations have reported in. You have a limited number of engineers.",
      plant_rule: PRIORITY + " Send engineers where product quality is at risk first, then to the biggest flow constraint.",
      engineers_total: engineers, already_sent_to: picks.map(p => p.name),
      station_reports: open.map(r => ({ step: r.step, station: r.name, problem: r.problem, urgency: r.urgency, needs_help: +r.needsHelp.toFixed(2), changes: r.changes })),
    };
    const t0 = Date.now();
    const body = await drexCall({ state, questions: { send_to: { type: "choice", instructions: `Where should engineer ${k + 1} go?`, criteria } } });
    const ans = body.answers?.send_to;
    const r = open.find(x => x.step === ans?.choice) || open[0];
    picks.push({ engineer: k + 1, step: r.step, name: r.name, confidence: ans?.confidence, probabilities: ans?.probabilities });
    calls.push({ latencyMs: Date.now() - t0, tokens: body.usage?.input_tokens || 0 });
  }
  return { picks, calls };
}

async function runSwarm({ rows, engineers = 2 }) {
  if (!Array.isArray(rows) || !rows.length) throw new Error("No line data to run the swarm on.");
  engineers = Math.max(1, Math.min(3, Number(engineers) || 2));
  const cur = summarize(F.analyze(rows));
  const base = Object.fromEntries(HISTORY.map(s => [s.step, s]));
  const ch = Object.fromEntries(changes(cur).map(c => [c.step, c]));
  const t0 = Date.now();
  const reports = await pool(cur, 6, s => runStation(s, base[s.step], ch[s.step]));
  const tStations = Date.now() - t0;
  const { picks, calls } = await dispatch(reports, engineers);
  const tokens = reports.reduce((a, r) => a + r.tokens, 0) + calls.reduce((a, c) => a + c.tokens, 0);
  return {
    model: drexCfg().model, engineers, reports, picks,
    drexCalls: reports.length + calls.length, wallMs: Date.now() - t0, stationsMs: tStations,
    costUsd: tokens / 1e6 * drexCfg().pricePerMTok,
  };
}

module.exports = { runSwarm, STATION_QUESTIONS, URGENCY };
