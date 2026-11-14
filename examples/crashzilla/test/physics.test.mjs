import test from "node:test";
import assert from "node:assert/strict";
import { simulate, hic, CURVES, describe, G } from "../public/physics.js";
import { decisionQuestions, summarize, ORGANS } from "../lib/triage.js";
import { toTriageInput, INTAKE_SCHEMA } from "../lib/ndi.js";

test("HIC of a square pulse matches the closed form dt * a^2.5", () => {
  // 60 g held for 10 ms inside a 15 ms window
  const t = [], a = [];
  for (let ms = 0; ms <= 40; ms += 0.1) { t.push(ms); a.push(ms >= 10 && ms < 20 ? 60 : 0); }
  const expected = 0.010 * Math.pow(60, 2.5);
  assert.ok(Math.abs(hic(t, a, 15) - expected) / expected < 0.03, `${hic(t, a, 15)} vs ${expected}`);
});

test("the crash pulse integrates to delta-V (impact speed + rebound)", () => {
  const r = simulate({ speedKmh: 56 });
  const s = r.series; let dv = 0;
  for (let i = 1; i < s.t.length; i++) dv += ((s.car[i] + s.car[i - 1]) / 2) * G * ((s.t[i] - s.t[i - 1]) / 1000);
  assert.ok(Math.abs(dv * 3.6 - 56 * 1.12) < 1, `delta-V ${dv * 3.6}`);
  assert.ok(Math.abs(r.metrics.deltaVKmh - 56 * 1.12) < 0.01);
});

test("NHTSA risk curves are monotonic and bounded", () => {
  for (const [name, xs] of [["head", [100, 400, 700, 1500, 3000]], ["neck", [0.2, 0.6, 1, 1.5]], ["femur", [1, 5, 10, 15]]]) {
    const ys = xs.map((x) => CURVES[name].f(x));
    ys.forEach((y) => assert.ok(y >= 0 && y <= 1));
    for (let i = 1; i < ys.length; i++) assert.ok(ys[i] > ys[i - 1], `${name} not increasing`);
  }
  assert.ok(CURVES.chest.f(40, 70) > CURVES.chest.f(40, 30), "older chest must be more fragile");
  assert.ok(CURVES.chest.f(60, 40) > CURVES.chest.f(30, 40));
});

test("anchor: the NCAP head curve gives 11% AIS3+ at the HIC15 limit of 700", () => {
  // Phi((ln 700 - 7.45231) / 0.73998) = Phi(-1.218) = 0.1116, computed by hand
  assert.ok(Math.abs(CURVES.head.f(700) - 0.1116) < 0.003, `P(HIC=700) = ${CURVES.head.f(700)}`);
});

test("a belt lowers risk; speed and age raise it", () => {
  const belted = simulate({ speedKmh: 56 }), unbelted = simulate({ speedKmh: 56, belted: false });
  assert.ok(unbelted.risk.joint > belted.risk.joint + 0.3);
  assert.ok(simulate({ speedKmh: 90 }).risk.joint > belted.risk.joint);
  assert.ok(simulate({ speedKmh: 56, age: 78 }).risk.chest > belted.risk.chest);
  assert.ok(simulate({ speedKmh: 56, belted: false, airbag: false }).metrics.hic15 > unbelted.metrics.hic15);
});

test("belted 56 km/h (the NCAP test) stays in 5-star territory", () => {
  const r = simulate({ speedKmh: 56 });
  assert.ok(r.metrics.hic15 < 700 && r.metrics.chestDeflMm < 63 && r.metrics.nij < 1 && r.metrics.femurKN < 10);
  assert.ok(r.risk.joint < 0.15, `joint risk ${r.risk.joint}`);
});

test("the state text handed to the models carries every reading", () => {
  const text = describe(simulate({ speedKmh: 72, belted: false, age: 67 }));
  for (const word of ["UNBELTED", "67-year-old", "HIC15", "chest deflection", "Nij", "femur load", "delta-V"]) assert.ok(text.includes(word), word);
});

test("Drex gets 20 typed questions: 14 organs + 6 ER calls", () => {
  const q = decisionQuestions();
  assert.equal(Object.keys(q).length, 20);
  assert.equal(Object.values(q).filter((x) => x.type === "score").length, Object.keys(ORGANS).length);
  assert.deepEqual(Object.keys(q.destination.criteria), ["level1_trauma", "level2_trauma", "community_er", "urgent_care"]);
});

test("summarize turns severity distributions into P(serious)", () => {
  const answers = { destination: { choice: "level1_trauma", probabilities: { level1_trauma: 0.9 } } };
  for (const id of Object.keys(ORGANS)) answers[id] = { probabilities: { 0: 0.5, 1: 0.2, 2: 0.1, 3: 0.1, 4: 0.06, 5: 0.04 } };
  for (const k of ["trauma_activation", "ct_head", "ct_cspine", "fast_exam", "blood_ready"]) answers[k] = { noul: 0.7 };
  const s = summarize(answers);
  assert.equal(s.destination, "level1_trauma");
  assert.equal(s.organs.length, 14);
  assert.ok(Math.abs(s.organs[0].p_serious - 0.2) < 1e-9);
});

test("NDI report fields map onto simulator inputs and notes for Drex", () => {
  const { input, notes } = toTriageInput({ age: 67, belted: false, airbag_deployed: true, estimated_speed_kmh: 72.4, anticoagulants: true, loss_of_consciousness: true, complaints: "chest pain" });
  assert.deepEqual(input, { age: 67, belted: false, airbag: true, speedKmh: 72 });
  assert.equal(notes.length, 3);
  assert.equal(INTAKE_SCHEMA.additionalProperties, false);
});
