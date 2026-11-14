// Muda Lens — analysis engine (pure functions, runs in browser and Node).
// Lean (8 wastes / TIMWOODS) + Six Sigma (DPMO, sigma level, RTY, Cpk) for a semiconductor fab.

(function (root) {
  // ---------- Process model: a simplified power-semiconductor flow ----------
  // vaMin   = value-add processing minutes per lot
  // queueMin= typical wait before the step (minutes)
  // dpu     = mean defects per unit (die) introduced at the step
  // spec    = critical-to-quality parameter monitored at the step (for Cpk)
  const STEPS = [
    { id: "OX",   name: "Thermal Oxidation",   area: "Front-end", vaMin: 240, queueMin: 600,  dpu: 0.004, transportM: 40, inspect: 1, spec: { param: "Gate oxide (nm)", lsl: 47, usl: 53, target: 50, sd: 0.9 } },
    { id: "PH",   name: "Photolithography",    area: "Front-end", vaMin: 90,  queueMin: 900,  dpu: 0.012, transportM: 60, inspect: 3, spec: { param: "CD (µm)", lsl: 0.45, usl: 0.55, target: 0.5, sd: 0.018 } },
    { id: "ET",   name: "Plasma Etch",         area: "Front-end", vaMin: 60,  queueMin: 480,  dpu: 0.008, transportM: 55, inspect: 2, spec: { param: "Etch depth (nm)", lsl: 380, usl: 420, target: 400, sd: 7 } },
    { id: "IM",   name: "Ion Implant",         area: "Front-end", vaMin: 45,  queueMin: 720,  dpu: 0.003, transportM: 70, inspect: 1, spec: { param: "Sheet R (Ω/sq)", lsl: 95, usl: 105, target: 100, sd: 1.4 } },
    { id: "DF",   name: "Diffusion / Anneal",  area: "Front-end", vaMin: 300, queueMin: 540,  dpu: 0.002, transportM: 40, inspect: 1, spec: { param: "Junction depth (µm)", lsl: 2.7, usl: 3.3, target: 3.0, sd: 0.08 } },
    { id: "MT",   name: "Metallization",       area: "Front-end", vaMin: 120, queueMin: 660,  dpu: 0.006, transportM: 65, inspect: 2, spec: { param: "Al thickness (µm)", lsl: 3.6, usl: 4.4, target: 4.0, sd: 0.11 } },
    { id: "WS",   name: "Wafer Probe / Sort",  area: "Test",      vaMin: 180, queueMin: 1440, dpu: 0.000, transportM: 120, inspect: 1, spec: { param: "Rds(on) (mΩ)", lsl: 180, usl: 220, target: 200, sd: 6 } },
    { id: "DC",   name: "Dicing",              area: "Back-end",  vaMin: 50,  queueMin: 960,  dpu: 0.005, transportM: 200, inspect: 2, spec: { param: "Kerf width (µm)", lsl: 35, usl: 45, target: 40, sd: 1.6 } },
    { id: "DA",   name: "Die Attach",          area: "Back-end",  vaMin: 70,  queueMin: 600,  dpu: 0.007, transportM: 50, inspect: 2, spec: { param: "Bond-line (µm)", lsl: 20, usl: 30, target: 25, sd: 1.5 } },
    { id: "WB",   name: "Wire Bond",           area: "Back-end",  vaMin: 110, queueMin: 720,  dpu: 0.010, transportM: 45, inspect: 3, spec: { param: "Pull strength (g)", lsl: 8, usl: 16, target: 12, sd: 1.1 } },
    { id: "MD",   name: "Mold / Encapsulate",  area: "Back-end",  vaMin: 80,  queueMin: 540,  dpu: 0.004, transportM: 45, inspect: 1, spec: { param: "Void %", lsl: 0, usl: 5, target: 1.5, sd: 0.9 } },
    { id: "FT",   name: "Final Test",          area: "Test",      vaMin: 150, queueMin: 1200, dpu: 0.000, transportM: 90, inspect: 2, spec: { param: "BVdss (V)", lsl: 700, usl: 760, target: 730, sd: 7 } },
  ];

  const WASTES = {
    T: { name: "Transportation", color: "#5B7A8C" },
    I: { name: "Inventory (WIP)", color: "#7D6B91" },
    M: { name: "Motion", color: "#4F8072" },
    W: { name: "Waiting", color: "#C08A2E" },
    O: { name: "Over-processing", color: "#B0657A" },
    P: { name: "Over-production", color: "#9A8F7E" },
    D: { name: "Defects", color: "#9C341F" },
    S: { name: "Skills (unused talent)", color: "#6E8A4E" },
  };

  // ---------- deterministic RNG so demos are reproducible ----------
  function rng(seed) {
    let s = seed >>> 0;
    return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  function normal(r) { let u = 0, v = 0; while (u === 0) u = r(); while (v === 0) v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }

  // ---------- synthetic lot-level data (no real fab data is used) ----------
  // Each record = one lot passing one step.
  function generateLots({ lots = 200, diesPerLot = 2500, seed = 42, drift = {} } = {}) {
    const r = rng(seed);
    const rows = [];
    for (let l = 0; l < lots; l++) {
      const lotId = "L" + String(10000 + l);
      for (const st of STEPS) {
        const d = drift[st.id] || {};
        const queueMult = d.queue || 1, dpuMult = d.dpu || 1, meanShift = d.shift || 0, sdMult = d.sd || 1;
        const va = Math.max(1, st.vaMin * (1 + 0.08 * normal(r)));
        const queue = Math.max(0, st.queueMin * queueMult * Math.exp(0.55 * normal(r)) );
        const lam = st.dpu * dpuMult * diesPerLot;
        const defects = Math.max(0, Math.round(lam + Math.sqrt(lam) * normal(r)));
        const rework = r() < Math.min(0.5, st.dpu * dpuMult * 6) ? 1 : 0;
        const holds = r() < 0.04 * queueMult ? 1 : 0;
        const extraInspections = r() < Math.min(1, 0.25 * (d.inspect || 1)) ? (d.inspect ? Math.max(1, st.inspect - 1) * d.inspect : Math.max(0, st.inspect - 1)) : 0; // inspections beyond control plan
        const manualInterventions = r() < Math.min(1, 0.08 * (d.manual || 1)) ? 1 : 0; // operator overrides / manual recipe tweaks
        const measured = st.spec.target + meanShift + st.spec.sd * sdMult * normal(r);
        rows.push({
          lot: lotId, step: st.id, va_min: +va.toFixed(1), queue_min: +queue.toFixed(1),
          dies_in: diesPerLot, defects, rework, holds, transport_m: +(st.transportM * (d.transport || 1) * (1 + 0.2 * r())).toFixed(1),
          extra_inspections: extraInspections, manual_interventions: manualInterventions,
          measured: +measured.toFixed(4),
        });
      }
    }
    return rows;
  }

  // ---------- statistics ----------
  const mean = a => a.reduce((s, x) => s + x, 0) / (a.length || 1);
  const sd = a => { const m = mean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / Math.max(1, a.length - 1)); };

  // inverse standard normal (Acklam)
  function normInv(p) {
    if (p <= 0) return -Infinity; if (p >= 1) return Infinity;
    const a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2, -3.066479806614716e1, 2.506628277459239];
    const b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1, -1.328068155288572e1];
    const c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
    const d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416];
    const pl = 0.02425; let q, r2;
    if (p < pl) { q = Math.sqrt(-2 * Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    if (p <= 1 - pl) { q = p - 0.5; r2 = q*q; return (((((a[0]*r2+a[1])*r2+a[2])*r2+a[3])*r2+a[4])*r2+a[5])*q / (((((b[0]*r2+b[1])*r2+b[2])*r2+b[3])*r2+b[4])*r2+1); }
    q = Math.sqrt(-2 * Math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1);
  }
  // short-term sigma level with the conventional 1.5σ shift
  const sigmaLevel = dpmo => dpmo <= 0 ? 6 + 1.5 : normInv(1 - dpmo / 1e6) + 1.5;

  // ---------- core analysis ----------
  // costs: $ per unit of each waste driver (editable in the UI)
  const DEFAULT_COSTS = { waitPerHour: 35, defectPerDie: 1.8, reworkPerLot: 420, holdPerLot: 650, transportPerM: 0.12, inspectPerEvent: 55, manualPerEvent: 90, wipPerLotDay: 60 };

  function analyze(rows, costs = DEFAULT_COSTS) {
    const byStep = {};
    for (const st of STEPS) byStep[st.id] = [];
    for (const r of rows) if (byStep[r.step]) byStep[r.step].push(r);
    const lotCount = new Set(rows.map(r => r.lot)).size || 1;

    let survival = 1, totalVA = 0, totalLead = 0;
    const steps = STEPS.map(st => {
      const rs = byStep[st.id];
      const n = rs.length || 1;
      const va = mean(rs.map(r => r.va_min)), q = mean(rs.map(r => r.queue_min));
      const dies = rs.reduce((s, r) => s + r.dies_in, 0) || 1;
      const defects = rs.reduce((s, r) => s + r.defects, 0);
      const dpu = defects / dies;
      const opportunities = 1; // one CTQ opportunity per die per step
      const dpmo = dpu / opportunities * 1e6;
      const fpy = Math.exp(-dpu); // Poisson first-pass yield
      survival *= fpy;
      totalVA += va; totalLead += va + q;
      const meas = rs.map(r => r.measured), mu = mean(meas), s = sd(meas);
      const { lsl, usl } = st.spec;
      const cp = (usl - lsl) / (6 * s);
      const cpk = Math.min(usl - mu, mu - lsl) / (3 * s);
      const oos = meas.filter(x => x < lsl || x > usl).length / n;

      // Waste $ (per lot averaged, then x lots)
      const waste = {
        W: (rs.reduce((a, r) => a + r.queue_min, 0) / 60) * costs.waitPerHour,
        I: (rs.reduce((a, r) => a + r.queue_min, 0) / 1440) * costs.wipPerLotDay * 0.5, // WIP carrying cost of queued lots
        D: defects * costs.defectPerDie + rs.reduce((a, r) => a + r.rework, 0) * costs.reworkPerLot,
        T: rs.reduce((a, r) => a + r.transport_m, 0) * costs.transportPerM,
        O: rs.reduce((a, r) => a + r.extra_inspections, 0) * costs.inspectPerEvent,
        M: rs.reduce((a, r) => a + r.transport_m, 0) * costs.transportPerM * 0.35, // operator walking ~ transport proxy
        P: rs.reduce((a, r) => a + r.holds, 0) * costs.holdPerLot * 0.4, // lots built ahead, then held
        S: rs.reduce((a, r) => a + r.manual_interventions, 0) * costs.manualPerEvent,
      };
      const wasteTotal = Object.values(waste).reduce((a, b) => a + b, 0);
      return { ...st, n, va, queue: q, pce: va / (va + q), dpu, dpmo, fpy, sigma: sigmaLevel(dpmo), mu, s, cp, cpk, oos, waste, wasteTotal,
               holds: rs.reduce((a, r) => a + r.holds, 0), rework: rs.reduce((a, r) => a + r.rework, 0) };
    });

    const totals = {};
    for (const k of Object.keys(WASTES)) totals[k] = steps.reduce((a, s) => a + s.waste[k], 0);
    const pareto = Object.entries(totals).map(([k, v]) => ({ key: k, name: WASTES[k].name, value: v })).sort((a, b) => b.value - a.value);
    const grand = pareto.reduce((a, b) => a + b.value, 0) || 1;
    let cum = 0; pareto.forEach(p => { cum += p.value; p.cum = cum / grand; });

    const totalDefects = rows.reduce((a, r) => a + r.defects, 0);
    const totalDies = rows.filter(r => r.step === STEPS[0].id).reduce((a, r) => a + r.dies_in, 0) || 1;
    const overallDpmo = totalDefects / (totalDies * STEPS.length) * 1e6;

    const bottleneck = [...steps].sort((a, b) => b.queue - a.queue)[0];
    const worstCpk = [...steps].sort((a, b) => a.cpk - b.cpk)[0];
    const topWasteStep = [...steps].sort((a, b) => b.wasteTotal - a.wasteTotal)[0];

    return {
      lotCount, steps, pareto, totals, grand,
      kpi: { pce: totalVA / totalLead, leadDays: totalLead / 1440, vaHours: totalVA / 60, rty: survival, dpmo: overallDpmo, sigma: sigmaLevel(overallDpmo), wastePerLot: grand / lotCount },
      bottleneck, worstCpk, topWasteStep,
      recommendations: recommend(steps, pareto),
    };
  }

  // ---------- DMAIC recommendation rules ----------
  function recommend(steps, pareto) {
    const recs = [];
    const top = pareto.slice(0, 3).map(p => p.key);
    const bn = [...steps].sort((a, b) => b.queue - a.queue)[0];
    if (top.includes("W") || top.includes("I")) recs.push({ waste: "W", step: bn.name, phase: "Improve",
      text: `${bn.name} has the longest queue (${(bn.queue / 60).toFixed(1)} h avg). Apply CONWIP / drum-buffer-rope at this constraint, cap upstream WIP, and add SMED on tool changeovers.` });
    for (const s of steps.filter(s => s.cpk < 1.33).sort((a, b) => a.cpk - b.cpk).slice(0, 3))
      recs.push({ waste: "D", step: s.name, phase: s.cpk < 1 ? "Analyze" : "Control",
        text: `${s.spec.param} Cpk = ${s.cpk.toFixed(2)} (${Math.abs(s.mu - s.spec.target) > 0.5 * s.s ? "off-center: mean shift" : "too much spread"}). ${s.cpk < 1 ? "Run a fishbone + DOE on recipe/chamber variables" : "Tighten SPC limits with Western Electric rules"}; target Cpk ≥ 1.33.` });
    const ovp = [...steps].sort((a, b) => b.waste.O - a.waste.O)[0];
    if (ovp.waste.O > 0) recs.push({ waste: "O", step: ovp.name, phase: "Improve",
      text: `Inspections at ${ovp.name} exceed the control plan. Move to sampling inspection where Cpk allows and rely on in-line SPC.` });
    const tr = [...steps].sort((a, b) => b.waste.T - a.waste.T)[0];
    recs.push({ waste: "T", step: tr.name, phase: "Improve",
      text: `${tr.name} has the longest material moves. Map a spaghetti diagram; co-locate or schedule AMHS/FOUP moves in batches.` });
    const sk = [...steps].sort((a, b) => b.waste.S - a.waste.S)[0];
    if (sk.waste.S > 0) recs.push({ waste: "S", step: sk.name, phase: "Control",
      text: `Frequent manual overrides at ${sk.name}. Capture operator know-how in standard work and run a kaizen with the technicians who make the tweaks.` });
    return recs;
  }

  // ---------- CSV ----------
  const COLS = ["lot", "step", "va_min", "queue_min", "dies_in", "defects", "rework", "holds", "transport_m", "extra_inspections", "manual_interventions", "measured"];
  function toCSV(rows) { return [COLS.join(","), ...rows.map(r => COLS.map(c => r[c]).join(","))].join("\n"); }
  function fromCSV(text) {
    const lines = text.trim().split(/\r?\n/); const head = lines.shift().split(",").map(h => h.trim());
    const missing = COLS.filter(c => !head.includes(c)); if (missing.length) throw new Error("Missing columns: " + missing.join(", "));
    return lines.filter(Boolean).map(line => { const v = line.split(","); const o = {}; head.forEach((h, i) => { o[h] = h === "lot" || h === "step" ? v[i].trim() : Number(v[i]); }); return o; });
  }

  const api = { STEPS, WASTES, DEFAULT_COSTS, COLS, generateLots, analyze, sigmaLevel, normInv, toCSV, fromCSV };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.MudaLens = api;
})(typeof window !== "undefined" ? window : globalThis);
