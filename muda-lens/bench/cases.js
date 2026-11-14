// Ground-truth benchmark cases for the "Apex Predator" track.
// Each case = synthetic fab data with ONE injected fault at ONE step, so the correct
// decision is known. Contenders see only the data (current vs. historical baseline),
// never the fault label.

const F = require("../src/engine.js");

// The four decisions every contender must make, with the allowed options.
// Option descriptions are shared word-for-word by Drex and the LLM baseline.
const QUESTIONS = {
  root_waste: {
    instructions: "Which of the 8 Lean wastes (TIMWOODS) is the dominant NEW problem on this line, compared to its historical baseline?",
    options: {
      transportation: "Material moved further than needed between tools/areas",
      inventory: "Excess WIP sitting in queues",
      motion: "Unnecessary operator movement",
      waiting: "Lots waiting in queue at a constraint; cycle time inflated by wait, not processing",
      overprocessing: "More inspections or processing than the control plan requires",
      overproduction: "Lots built ahead of demand, then put on hold",
      defects: "Parameter drift, excess variation, or rising defect counts that hurt yield",
      skills: "Operators repeatedly overriding recipes by hand; know-how not captured in standard work",
    },
  },
  culprit_step: {
    instructions: "At which process step did the problem start?",
    options: Object.fromEntries(F.STEPS.map(s => [s.id, s.name])),
  },
  countermeasure: {
    instructions: "Which Lean / Six Sigma countermeasure should the team run first?",
    options: {
      conwip_dbr: "CONWIP / drum-buffer-rope pacing at the constraint, plus SMED on changeovers",
      recenter_spc: "Re-center the off-target mean (recipe offset / calibration), then tighten SPC limits",
      doe_variation: "Fishbone + designed experiment (DOE) to cut excess process variation",
      root_cause_8d: "8D root-cause containment for a defect-count spike (tool, consumable, handling)",
      sampling_plan: "Replace 100% / repeated inspection with a capability-based sampling plan",
      standard_work: "Kaizen with technicians; turn manual overrides into standard work and recipe updates",
      relayout_amhs: "Spaghetti diagram; re-layout or batch AMHS/FOUP moves to cut transport",
    },
  },
  disposition: {
    instructions: "What should happen to the lots currently at the culprit step?",
    options: {
      release: "No product-quality risk; ship lots on, fix the flow problem in parallel",
      hold: "Measured parameter is off-target or unstable; hold for engineering review before moving on",
      rework: "Elevated defect counts that are recoverable; rework at this step",
    },
  },
};

// Fault catalogue -> how to inject it + the correct answers.
const FAULTS = {
  bottleneck:     { drift: { queue: 4 },              truth: { root_waste: "waiting", countermeasure: "conwip_dbr", disposition: "release" } },
  mean_shift:     { drift: { shiftSd: 2.2 },          truth: { root_waste: "defects", countermeasure: "recenter_spc", disposition: "hold" } },
  variance:       { drift: { sd: 2.3 },               truth: { root_waste: "defects", countermeasure: "doe_variation", disposition: "hold" } },
  defect_spike:   { drift: { dpu: 6 },                truth: { root_waste: "defects", countermeasure: "root_cause_8d", disposition: "rework" } },
  over_inspect:   { drift: { inspect: 4 },            truth: { root_waste: "overprocessing", countermeasure: "sampling_plan", disposition: "release" } },
  manual_override:{ drift: { manual: 7 },             truth: { root_waste: "skills", countermeasure: "standard_work", disposition: "release" } },
  transport:      { drift: { transport: 5 },          truth: { root_waste: "transportation", countermeasure: "relayout_amhs", disposition: "release" } },
};

const QUALITY = ["mean_shift", "variance", "defect_spike"];
const FLOW = Object.keys(FAULTS).filter(k => !QUALITY.includes(k));
// scale a drift multiplier toward 1 (sev<1 = milder, >1 = stronger)
function scale(drift, sev) { return Object.fromEntries(Object.entries(drift).map(([k, v]) => [k, k === "shiftSd" ? v * sev : 1 + (v - 1) * sev])); }

// Plant priority rule, given to every contender: quality risk first, then the flow waste with the largest change.
const PRIORITY = "Plant rule: any product-quality risk (parameter drift, extra variation, defect spike) outranks flow wastes. " +
  "If there is no quality risk, the dominant problem is the flow waste with the largest change versus baseline. " +
  "Background noise of roughly +/-15% is normal.";

// Steps where a measured-parameter or defect fault makes physical sense (test steps have dpu=0)
const DEFECT_STEPS = F.STEPS.filter(s => s.dpu > 0).map(s => s.id);

function rng(seed) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }

// Turn an analysis into the compact "state" both contenders read.
function summarize(res) {
  return res.steps.map(s => ({
    step: s.id, name: s.name,
    va_h: +(s.va / 60).toFixed(2), queue_h: +(s.queue / 60).toFixed(2),
    ctq: s.spec.param, lsl: s.spec.lsl, usl: s.spec.usl, target: s.spec.target,
    mean: +s.mu.toFixed(4), sigma: +s.s.toFixed(4), cpk: +s.cpk.toFixed(2),
    dpmo: Math.round(s.dpmo), rework_lots: s.rework, holds: s.holds,
    transport_m_per_lot: +(s.waste.T / 0.12 / s.n).toFixed(0),
    extra_inspections: Math.round(s.waste.O / 55), manual_overrides: Math.round(s.waste.S / 90),
  }));
}

const HISTORY = summarize(F.analyze(F.generateLots({ lots: 200, seed: 1 }))); // the line's "normal" behavior

// Pre-computed change vs. baseline, so the decision model judges instead of doing arithmetic
// across mixed units. Only metrics that moved beyond normal noise are listed.
function changes(cur) {
  const base = Object.fromEntries(HISTORY.map(s => [s.step, s]));
  const pct = (a, b) => Math.round(((a + 1) / (b + 1) - 1) * 100);
  const out = [];
  for (const s of cur) {
    const b = base[s.step], sig = [];
    const add = (metric, kind, value, unit, threshold) => { if ((metric === "mean_offset" ? Math.abs(value) : value) >= threshold) sig.push({ metric, waste_family: kind, change: value + unit }); };
    add("queue_time", "flow:waiting", pct(s.queue_h, b.queue_h), "%", 30);
    add("transport_distance", "flow:transportation", pct(s.transport_m_per_lot, b.transport_m_per_lot), "%", 30);
    add("extra_inspections", "flow:overprocessing", pct(s.extra_inspections, b.extra_inspections), "%", 50);
    add("manual_overrides", "flow:skills", pct(s.manual_overrides, b.manual_overrides), "%", 50);
    add("defects_dpmo", "quality:defect_count", pct(s.dpmo, b.dpmo), "%", 80);
    add("process_sigma", "quality:variation", Math.round((s.sigma / b.sigma - 1) * 100), "%", 35);
    add("mean_offset", "quality:mean_shift", +((s.mean - b.mean) / b.sigma).toFixed(1), " sigma", 1.2);
    if (sig.length) out.push({ step: s.step, name: s.name, cpk_now: s.cpk, cpk_baseline: b.cpk, significant_changes: sig });
  }
  return out;
}

function buildState(rows, version = process.env.STATE_VERSION || "v2") {
  const current = summarize(F.analyze(rows));
  const base = { line: "Power MOSFET wafer-to-package line (synthetic)", plant_rule: PRIORITY };
  if (version === "v1") return { ...base, current_week: current, historical_baseline: HISTORY };
  return { ...base, changes_vs_baseline: changes(current), current_week: current };
}

function buildCases(n = 70, seed = 2026, dataSeed = 1000) {
  const r = rng(seed);
  const kinds = Object.keys(FAULTS);
  const cases = [];
  for (let i = 0; i < n; i++) {
    const kind = kinds[i % kinds.length];
    const pool = ["mean_shift", "variance", "defect_spike"].includes(kind) ? DEFECT_STEPS : F.STEPS.map(s => s.id);
    const step = pool[Math.floor(r() * pool.length)];
    const st = F.STEPS.find(s => s.id === step);
    const quality = QUALITY.includes(kind);
    // primary fault severity is randomized (quality faults are moderate, flow faults strong)
    const sev = quality ? 0.7 + 0.35 * r() : 1 + 0.4 * r();
    const f = scale(FAULTS[kind].drift, sev);
    const drift = {};
    // background noise on every step so it is not trivially "the only change"
    for (const s of F.STEPS) drift[s.id] = { queue: 0.85 + 0.3 * r(), dpu: 0.8 + 0.4 * r() };
    drift[step] = { ...drift[step], ...f, ...(f.shiftSd ? { shift: (r() < 0.5 ? -1 : 1) * f.shiftSd * st.spec.sd } : {}) };
    // decoy: a secondary FLOW waste at a different step. Visible, sometimes with a bigger
    // raw ratio than the primary, but lower priority under the plant's rules.
    const flowKinds = FLOW.filter(k => k !== kind);
    const decoyKind = flowKinds[Math.floor(r() * flowKinds.length)];
    const others = F.STEPS.map(s => s.id).filter(id => id !== step);
    const decoyStep = others[Math.floor(r() * others.length)];
    const decoySev = quality ? 0.6 + 0.4 * r() : 0.35 + 0.15 * r();
    drift[decoyStep] = { ...drift[decoyStep], ...scale(FAULTS[decoyKind].drift, decoySev) };
    const rows = F.generateLots({ lots: 120, seed: dataSeed + i, drift });
    cases.push({
      id: `case-${String(i + 1).padStart(3, "0")}`, kind, decoy: { kind: decoyKind, step: decoyStep },
      truth: { ...FAULTS[kind].truth, culprit_step: step },
      state: buildState(rows),
    });
  }
  return cases;
}

module.exports = { QUESTIONS, FAULTS, PRIORITY, buildCases, buildState, summarize, changes, HISTORY };
