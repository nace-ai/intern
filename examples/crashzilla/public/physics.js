// Occupant crash physics + NHTSA injury criteria. Shared by browser and server.
// Lumped-mass model: vehicle half-sine crash pulse, occupant restrained by belt (slack + spring
// + load limiter) and airbag (engages after travel), head on a neck, knees to the dash.
// Injury risk curves are the NHTSA NCAP (2011+) Hybrid III 50th-male curves.

export const G = 9.81;

export const DEFAULTS = {
  speedKmh: 56,       // NCAP frontal: 35 mph into a rigid barrier
  pulseMs: 95,        // crash pulse duration
  rebound: 0.12,      // fraction of impact speed recovered as rebound
  belted: true,
  airbag: true,
  age: 35,
  massKg: 78,
};

const normCdf = (x) => {
  // Abramowitz-Stegun erf approximation
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(x * x) / 2);
  return x >= 0 ? (1 + y) / 2 : (1 - y) / 2;
};
const logistic = (z) => 1 / (1 + Math.exp(z));

export const CURVES = {
  head: { label: "Head AIS3+", formula: "P = Φ((ln HIC₁₅ − 7.45231) / 0.73998)", f: (hic) => hic <= 0 ? 0 : normCdf((Math.log(hic) - 7.45231) / 0.73998) },
  chest: { label: "Chest AIS3+", formula: "P = 1 / (1 + e^(12.597 − 0.05861·age − 1.568·δ^0.4612))", f: (d, age) => logistic(12.597 - 0.05861 * age - 1.568 * Math.pow(Math.max(d, 0), 0.4612)) },
  neck: { label: "Neck AIS3+", formula: "P = 1 / (1 + e^(3.2269 − 1.9688·Nij))", f: (nij) => logistic(3.2269 - 1.9688 * nij) },
  femur: { label: "Knee-thigh-hip AIS2+", formula: "P = 1 / (1 + e^(5.7949 − 0.5196·F))", f: (kN) => logistic(5.7949 - 0.5196 * kN) },
};

/** Run the simulation. Returns time series (ms, m/s², m) and injury criteria. */
export function simulate(input = {}) {
  const p = { ...DEFAULTS, ...input };
  const dt = 0.0001, T = p.pulseMs / 1000, tEnd = 0.2;
  const v0 = p.speedKmh / 3.6;
  const dv = v0 * (1 + p.rebound);
  const A = (dv * Math.PI) / (2 * T); // half-sine peak so ∫a dt = Δv
  const m = p.massKg * 0.62;           // upper body mass carried by belt/airbag
  const mHead = 4.5;

  // restraint parameters
  const slack = p.belted ? 0.025 : 0.6;     // m of travel before belt loads
  const kBelt = 95000, cBelt = 900, limiter = 6200; // N/m, Ns/m, N
  const bagGap = 0.17, kBag = 42000, cBag = 1200, bagVent = 7500;
  const wheelGap = p.airbag ? 0.42 : 0.30, kWheel = 600000; // hard contact

  let x = 0, v = 0;          // occupant torso relative to car (forward +)
  let hx = 0, hv = 0;        // head relative to torso
  let knee = 0, kv = 0;      // lower body relative to car
  const out = { t: [], car: [], chest: [], head: [], torsoX: [], headX: [], kneeF: [] };
  let peakBelt = 0, peakBag = 0, peakWheel = 0, peakKnee = 0, peakMy = 0, peakFz = 0;

  for (let t = 0; t <= tEnd; t += dt) {
    const aCar = t < T ? A * Math.sin((Math.PI * t) / T) : 0; // vehicle deceleration (+)
    // torso: in the car frame it is pushed forward by aCar, held back by restraints
    let Fb = 0, Fa = 0, Fw = 0;
    if (x > slack) Fb = Math.min(kBelt * (x - slack) + cBelt * Math.max(v, 0), limiter);
    if (p.airbag && x > bagGap) Fa = Math.min(kBag * (x - bagGap) + cBag * Math.max(v, 0), bagVent);
    if (x > wheelGap) Fw = kWheel * (x - wheelGap) + 4000 * Math.max(v, 0);
    const F = Fb + Fa + Fw;
    peakBelt = Math.max(peakBelt, Fb); peakBag = Math.max(peakBag, Fa); peakWheel = Math.max(peakWheel, Fw);
    const aRel = aCar - F / m;
    v += aRel * dt; x += v * dt;
    const torsoDecel = F / m; // absolute deceleration of torso

    // head: spring-damper neck relative to torso; airbag/wheel contact on the head
    const kNeck = 7000, cNeck = 160;
    let Fh = kNeck * hx + cNeck * hv;
    const headAbs = x + hx;
    if (p.airbag && headAbs > bagGap + 0.08) Fh += Math.min(20000 * (headAbs - bagGap - 0.08) + 150 * Math.max(hv + v, 0), 1900);
    if (headAbs > wheelGap + 0.1) Fh += Math.min(160000 * (headAbs - wheelGap - 0.1) + 400 * Math.max(hv + v, 0), 18000);
    hv += (F / m - Fh / mHead) * dt; hx += hv * dt;
    const headDecel = Fh / mHead;
    peakFz = Math.max(peakFz, Math.abs(kNeck * hx));
    peakMy = Math.max(peakMy, Math.abs(kNeck * hx) * 0.07 + Math.abs(cNeck * hv) * 0.05);

    // knees: free until dash contact (belt holds pelvis when belted)
    const kneeGap = p.belted ? 0.12 : 0.09, kKnee = p.belted ? 70000 : 260000;
    let Fk = 0;
    const mLeg = 13;
    if (knee > kneeGap) Fk = kKnee * (knee - kneeGap) + 1500 * Math.max(kv, 0);
    const pelvisHold = p.belted ? Math.max(0, 90000 * (knee - 0.03)) : 0;
    kv += (aCar - (Fk + pelvisHold) / mLeg) * dt; knee += kv * dt;
    peakKnee = Math.max(peakKnee, Fk);

    if (Math.round(t / dt) % 10 === 0) {
      out.t.push(t * 1000);
      out.car.push(aCar / G);
      out.chest.push(torsoDecel / G);
      out.head.push(Math.max(headDecel, 0) / G);
      out.torsoX.push(x);
      out.headX.push(hx);
      out.kneeF.push(Fk / 1000);
    }
  }

  const hic15 = hic(out.t, out.head, 15);
  const chestG3ms = clip3ms(out.t, smooth(out.chest, 30));
  // sternum deflection: belt is a narrow band (stiff on the ribs), airbag spreads load, wheel rim is concentrated
  const chestDefl = Math.min(peakBelt / 260 + peakBag / 1400 + peakWheel / 420, 110); // mm
  const nij = peakFz / 6806 + peakMy / 310;
  const femurKN = peakKnee / 1000;

  const risk = {
    head: CURVES.head.f(hic15),
    chest: CURVES.chest.f(chestDefl, p.age),
    neck: CURVES.neck.f(nij),
    femur: CURVES.femur.f(femurKN),
  };
  risk.joint = 1 - (1 - risk.head) * (1 - risk.chest) * (1 - risk.neck) * (1 - risk.femur);

  return {
    input: p,
    series: out,
    metrics: {
      deltaVKmh: dv * 3.6,
      peakCarG: A / G,
      peakChestG: Math.max(...out.chest),
      chestG3ms,
      peakHeadG: Math.max(...out.head),
      hic15,
      chestDeflMm: chestDefl,
      nij,
      femurKN,
      maxTorsoTravelCm: Math.max(...out.torsoX) * 100,
      stoppingDistanceCm: (v0 * T * (2 / Math.PI) + v0 * T * 0.5) * 100,
    },
    risk,
  };
}

/** HIC over a sliding window up to maxMs. Head acceleration in g, time in ms. */
export function hic(tMs, aG, maxMs = 15) {
  const n = tMs.length;
  const cum = [0];
  for (let i = 1; i < n; i++) cum.push(cum[i - 1] + ((aG[i] + aG[i - 1]) / 2) * ((tMs[i] - tMs[i - 1]) / 1000));
  let best = 0;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n && tMs[j] - tMs[i] <= maxMs; j++) {
      const dt = (tMs[j] - tMs[i]) / 1000;
      const avg = (cum[j] - cum[i]) / dt;
      const v = dt * Math.pow(Math.max(avg, 0), 2.5);
      if (v > best) best = v;
    }
  }
  return best;
}

const smooth = (a, w) => a.map((_, i) => { let s = 0, n = 0; for (let k = Math.max(0, i - w); k <= Math.min(a.length - 1, i + w); k++) { s += a[k]; n++; } return s / n; });

/** Highest acceleration sustained for 3 ms (the "3 ms clip"). */
function clip3ms(tMs, aG) {
  let best = 0;
  for (let i = 0; i < tMs.length; i++) {
    let j = i; let min = Infinity;
    while (j < tMs.length && tMs[j] - tMs[i] <= 3) { min = Math.min(min, aG[j]); j++; }
    if (tMs[j - 1] - tMs[i] >= 2.9) best = Math.max(best, min);
  }
  return best;
}

/** Plain-text summary for decision models. */
export function describe(sim, perception = {}) {
  const { input: p, metrics: m, risk } = sim;
  return [
    `Crash reconstruction (frontal${perception.crash_type ? ", camera says " + perception.crash_type : ""}).`,
    `Occupant: ${p.age}-year-old adult, ${p.massKg} kg, driver seat, ${p.belted ? "BELTED" : "UNBELTED"}, airbag ${p.airbag ? "deployed" : "NOT deployed"}.`,
    `Impact speed ${p.speedKmh.toFixed(0)} km/h, delta-V ${m.deltaVKmh.toFixed(0)} km/h, vehicle peak ${m.peakCarG.toFixed(0)} g over ${p.pulseMs} ms.`,
    `Measured on occupant: chest 3 ms clip ${m.chestG3ms.toFixed(0)} g, chest deflection ${m.chestDeflMm.toFixed(0)} mm (limit 63 mm), head HIC15 ${m.hic15.toFixed(0)} (limit 700), neck Nij ${m.nij.toFixed(2)} (limit 1.0), femur load ${m.femurKN.toFixed(1)} kN (limit 10 kN), forward torso travel ${m.maxTorsoTravelCm.toFixed(0)} cm.`,
    `NHTSA injury-curve risks: head AIS3+ ${(risk.head * 100).toFixed(0)}%, chest AIS3+ ${(risk.chest * 100).toFixed(0)}%, neck AIS3+ ${(risk.neck * 100).toFixed(0)}%, knee-thigh-hip AIS2+ ${(risk.femur * 100).toFixed(0)}%.`,
    perception.intrusion != null ? `Driver camera: body motion ${perception.intrusion}, airbag ${perception.airbag_seen}, head struck ${perception.head_contact ?? "unknown"}.` : "",
  ].filter(Boolean).join("\n");
}
