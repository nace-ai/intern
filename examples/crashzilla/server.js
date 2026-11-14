import "dotenv/config";
import express from "express";
import fs from "node:fs";
import path from "node:path";
import Anthropic from "@anthropic-ai/sdk";
import { simulate, describe } from "./public/physics.js";
import { decisionQuestions, drex } from "./lib/triage.js";
import { upload, extractIntake, toTriageInput } from "./lib/ndi.js";

const app = express();
app.use(express.json({ limit: "20mb" }));
app.get("/app", (_req, res) => res.sendFile(path.resolve("public/app.html")));
app.use(express.static("public"));
app.use("/three", express.static("node_modules/three"));

// Last good answers per input, so a network drop on stage shows a recorded run (labeled as such).
const CACHE = path.resolve("data/cache.json");
fs.mkdirSync("data", { recursive: true });
const cache = fs.existsSync(CACHE) ? JSON.parse(fs.readFileSync(CACHE, "utf8")) : {};
const saveCache = () => fs.writeFileSync(CACHE, JSON.stringify(cache, null, 1));

// ---------- frontier LLM baseline: same state, same questions ----------
const claude = process.env.ANTHROPIC_API_KEY ? new Anthropic() : null;
const LLM_MODEL = process.env.LLM_MODEL || "claude-opus-5-5";
const PRICE = { "claude-opus-5-5": [5, 25], "claude-fable-5-2": [8, 40] }; // $ per 1M tokens in/out (list)

async function llmBaseline(state, questions) {
  if (!claude) throw new Error("ANTHROPIC_API_KEY missing");
  const spec = Object.entries(questions).map(([id, q]) =>
    q.type === "score" ? `"${id}": integer 0-${q.criteria.length - 1} (${q.criteria.join(" | ")}) — ${q.instructions}`
      : q.type === "choice" ? `"${id}": one of ${Object.keys(q.criteria).join(", ")} — ${q.instructions}`
        : `"${id}": probability 0-1 — ${q.instructions}`).join("\n");
  const t0 = performance.now();
  const msg = await claude.messages.create({
    model: LLM_MODEL,
    max_tokens: 2000,
    system: "You are an emergency-medicine decision engine. Answer with ONE JSON object only, no prose.",
    messages: [{ role: "user", content: `${state}\n\nReturn JSON with these keys:\n${spec}` }],
  });
  const ms = performance.now() - t0;
  const text = msg.content.filter(b => b.type === "text").map(b => b.text).join("");
  const answers = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
  const [pi, po] = PRICE[LLM_MODEL] ?? [5, 25];
  return { model: LLM_MODEL, answers, ms, usage: msg.usage, cost: (msg.usage.input_tokens * pi + msg.usage.output_tokens * po) / 1e6 };
}

// ---------- routes ----------
app.get("/api/status", (_req, res) => res.json({ nace: Boolean(process.env.NACE_API_KEY), claude: Boolean(claude), llm: LLM_MODEL, drexProvider: process.env.DREX_PROVIDER || "nace" }));

// Crash → physics → Drex (and the LLM baseline in parallel).
app.post("/api/analyze", async (req, res) => {
  const { input = {}, perception = {}, notes = [], compare = true } = req.body ?? {};
  const sim = simulate(input);
  const state = [describe(sim, perception), ...notes].join("\n");
  const questions = decisionQuestions();
  const key = JSON.stringify(input);
  const [d, l] = await Promise.allSettled([
    drex({ state, questions }),
    compare ? llmBaseline(state, questions) : Promise.reject(new Error("skipped")),
  ]);
  const result = {
    sim: { metrics: sim.metrics, risk: sim.risk, input: sim.input },
    state,
    drex: d.status === "fulfilled" ? d.value : null,
    drexError: d.status === "rejected" ? String(d.reason.message) : null,
    llm: l.status === "fulfilled" ? l.value : null,
    llmError: l.status === "rejected" ? String(l.reason.message) : null,
  };
  if (result.drex) cache[key] = { ...cache[key], drex: result.drex };
  if (result.llm) cache[key] = { ...cache[key], llm: result.llm };
  if (!result.drex && cache[key]?.drex) result.drex = { ...cache[key].drex, cached: true };
  if (!result.llm && compare) {
    const rec = cache[key]?.llm ?? Object.values(cache).map(v => v?.llm).filter(Boolean).at(-1);
    if (rec) result.llm = { ...rec, cached: true };
  }
  saveCache();
  res.json(result);
});

// Driver camera: Drex Max reads one video frame.
const FRAME_Q = {
  phase: {
    type: "choice", instructions: "Image 1 is from an in-cabin camera watching the driver. What moment of a crash is this?",
    criteria: {
      before: "Driver sitting upright, normal driving, nothing deployed",
      impact: "Crash starting: body beginning to move forward, airbag starting to inflate",
      peak: "Peak load: head or chest pressed into the airbag, steering wheel or belt",
      rebound: "Driver moving back into the seat after the hit, airbag deflating",
    },
  },
  airbag: { type: "noul", instructions: "Has a frontal airbag deployed in front of the driver?" },
  belt: { type: "noul", instructions: "Is the driver wearing a shoulder seat belt across the chest?" },
  head_contact: { type: "choice", instructions: "What is the driver's head hitting or closest to hitting?", criteria: { none: "Nothing, head is free", airbag: "The inflated airbag", steering_wheel: "The steering wheel or dashboard", side_window: "The side window or pillar", roof: "The roof" } },
  lean: { type: "score", instructions: "How far has the driver's upper body been thrown forward from the seat back?", criteria: ["not at all", "slightly", "moderately", "far", "extremely, fully folded forward"] },
  violence: { type: "score", instructions: "How violent is the motion of the driver's body in this frame?", criteria: ["none", "mild", "moderate", "serious", "severe", "extreme"] },
};
app.post("/api/frame", async (req, res) => {
  const { image, t } = req.body ?? {};
  try {
    const r = await drex({ images: [image], state: "Image 1 is one frame from an in-cabin camera that watches the driver of a car.", questions: FRAME_Q });
    res.json({ t, answers: r.answers, ms: r.roundTripMs, eval: r.evaluation_time_ms, tokens: r.usage?.input_tokens ?? 0 });
  } catch (e) { res.status(500).json({ t, error: String(e.message) }); }
});

// NDI: read a crash / EMS report (PDF, scan, photo) and return the patient facts.
app.post("/api/intake", express.raw({ type: "*/*", limit: "25mb" }), async (req, res) => {
  try {
    const name = String(req.query.name || "report.pdf").replace(/[^\w.\-]/g, "_");
    const ndi = await extractIntake(await upload(req.body, name));
    res.json({ ...toTriageInput(ndi.data), data: ndi.data, fields: ndi.fields, ms: ndi.ms });
  } catch (e) { res.status(500).json({ error: String(e.message) }); }
});

// Mass-casualty triage: many crashes at once, every one decided by Drex.
app.post("/api/batch", async (req, res) => {
  const n = Math.min(Number(req.body?.n) || 60, 300);
  const rand = (a, b) => a + Math.random() * (b - a);
  const cases = Array.from({ length: n }, (_, i) => ({ id: i, input: { speedKmh: Math.round(rand(25, 95)), belted: Math.random() > 0.25, airbag: Math.random() > 0.15, age: Math.round(rand(8, 88)) } }));
  const all = decisionQuestions();
  const questions = { destination: all.destination, trauma_activation: all.trauma_activation };
  const t0 = performance.now();
  let cost = 0, tokens = 0, next = 0;
  const results = [];
  await Promise.all(Array.from({ length: 16 }, async () => {
    while (next < cases.length) {
      const c = cases[next++];
      const sim = simulate(c.input);
      try {
        const r = await drex({ state: describe(sim), questions });
        cost += r.cost; tokens += r.usage?.input_tokens ?? 0;
        results.push({ ...c, metrics: sim.metrics, risk: sim.risk, destination: r.answers.destination.choice, p: r.answers.destination.probabilities, activate: r.answers.trauma_activation.noul, ms: r.roundTripMs });
      } catch (e) { results.push({ ...c, error: String(e.message) }); }
    }
  }));
  res.json({ n, wallMs: performance.now() - t0, cost, tokens, results: results.sort((a, b) => a.id - b.id) });
});

const PORT = Number(process.env.PORT) || 4310;
app.listen(PORT, () => console.log(`Crashzilla on http://localhost:${PORT}`));
