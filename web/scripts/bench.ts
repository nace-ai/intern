// Benchmark: Drex vs an LLM on the band's real per-beat decisions.
//
// Run from web/:
//   pnpm dlx tsx scripts/bench.ts
//
// Drives a real Band (calm 8 beats, then chaos 8 beats). For every beat it asks
// Drex (console.nace.ai/v1/systemone, all 11 questions in one call) and, on the
// very same state and questions, Qwen3.8-27B on DeepInfra's
// OpenAI-compatible endpoint. The band advances on Drex's answers; the LLM's
// answers are only measured and validated. Beats run sequentially.
//
// Keys come ONLY from NACE_API_KEY / DEEPINFRA_API_KEY in the environment.
// This script never reads, prints or writes a key.

import { Band } from "../src/orchestra/band.ts";
import type { Answer, QuestionSet } from "../src/orchestra/drex.ts";
import { MUSICIAN_IDS } from "../src/orchestra/musicians.ts";

const DREX_URL = "https://console.nace.ai/v1/systemone";
const LLM_URL = "https://api.deepinfra.com/v1/openai/chat/completions";
const LLM_MODEL = "Qwen/Qwen3.8-27B";
const LLM_TIMEOUT_MS = 60_000;
const BEATS_PER_MOOD = 8;
const TOTAL_BEATS = BEATS_PER_MOOD * 2;
const DECISIONS_PER_ROUND = MUSICIAN_IDS.length;
const DREX_USD_PER_M_INPUT = 0.03; // published price; output tokens are not billed

const naceKey = process.env.NACE_API_KEY;
const deepinfraKey = process.env.DEEPINFRA_API_KEY;
if (!naceKey || !deepinfraKey) {
  console.error("NACE_API_KEY and DEEPINFRA_API_KEY must be set in the environment");
  process.exit(1);
}

/** One measured round. null = unknown / the call did not complete. */
type Round = {
  latencyMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  cost: number | null; // null = not reported by the API
  invalidJson: boolean; // the response was unusable (HTTP error, timeout, JSON that does not parse)
  invalidDecisions: number; // musicians whose answer is missing or not among their option keys
};

const newRound = (): Round => ({
  latencyMs: null,
  inputTokens: null,
  outputTokens: null,
  cost: null,
  invalidJson: false,
  invalidDecisions: 0,
});

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Count musicians whose answer is missing or not one of their option keys. */
function countInvalidChoices(pick: (id: string) => unknown, questions: QuestionSet): number {
  let invalid = 0;
  for (const id of MUSICIAN_IDS) {
    const choice = pick(id);
    if (typeof choice !== "string" || !(choice in questions[id].criteria)) invalid += 1;
  }
  return invalid;
}

/** One Drex round: all 11 questions in a single call. Times fetch + JSON parse. */
async function drexRound(
  state: string,
  questions: QuestionSet,
): Promise<{ answers: Record<string, Answer>; round: Round }> {
  const round = newRound();
  const started = performance.now();
  const res = await fetch(DREX_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${naceKey}` },
    body: JSON.stringify({ model: "drex-latest", state, questions }),
  });
  if (!res.ok) throw new Error(`Drex HTTP ${res.status}`);
  const data = (await res.json()) as {
    answers?: Record<string, Answer> | null;
    usage?: Record<string, unknown> | null;
  };
  round.latencyMs = performance.now() - started;
  if (data.usage && typeof data.usage === "object") {
    const u = data.usage;
    round.inputTokens = numOrNull(u.input_tokens ?? u.inputTokens);
    round.outputTokens = numOrNull(u.output_tokens ?? u.outputTokens);
    // usage.cost null or missing -> unknown, NOT 0
    round.cost = numOrNull(u.cost);
  }
  const answers = data.answers ?? {};
  round.invalidDecisions = countInvalidChoices((id) => answers[id]?.choice, questions);
  return { answers, round };
}

/** The one user message for the LLM: the state, then each musician's instructions and options. */
function llmMessage(state: string, questions: QuestionSet): string {
  const lines = [state, "", "Decide for each of these musicians:"];
  for (const id of MUSICIAN_IDS) {
    const q = questions[id];
    lines.push(`--- ${id} ---`, `instructions: ${q.instructions}`, "options:");
    for (const [key, desc] of Object.entries(q.criteria)) lines.push(`- ${JSON.stringify(key)}: ${desc}`);
    lines.push("");
  }
  lines.push("Reply with only a JSON object mapping every musician id to exactly one of its option keys.");
  return lines.join("\n");
}

function llmBody(content: string, thinkingOff: boolean): Record<string, unknown> {
  return {
    model: LLM_MODEL,
    messages: [{ role: "user", content }],
    temperature: 0,
    response_format: { type: "json_object" },
    ...(thinkingOff ? { chat_template_kwargs: { enable_thinking: false } } : {}),
  };
}

async function llmProbe(extra: Record<string, unknown>): Promise<{ thinking: boolean }> {
  const res = await fetch(LLM_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${deepinfraKey}` },
    body: JSON.stringify({
      model: LLM_MODEL,
      messages: [{ role: "user", content: 'Reply with only a JSON object: {"ping": "pong"}' }],
      temperature: 0,
      response_format: { type: "json_object" },
      ...extra,
    }),
  });
  if (!res.ok) throw new Error(`LLM probe HTTP ${res.status}`);
  const data = (await res.json()) as {
    choices?: { message: Record<string, unknown> }[] | null;
  };
  const msg = data.choices?.[0]?.message ?? {};
  const hasReasoning =
    (typeof msg.reasoning_content === "string" && msg.reasoning_content.length > 0) ||
    (typeof msg.reasoning === "string" && msg.reasoning.length > 0);
  const thinksInContent = typeof msg.content === "string" && msg.content.includes("think");
  return { thinking: hasReasoning || thinksInContent };
}

/** One LLM round on the same state and questions. A timeout counts as a failed beat. */
async function llmRound(state: string, questions: QuestionSet, thinkingOff: boolean): Promise<Round> {
  const round = newRound();
  const started = performance.now();
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), LLM_TIMEOUT_MS);
  try {
    const res = await fetch(LLM_URL, {
      method: "POST",
      signal: abort.signal,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${deepinfraKey}` },
      body: JSON.stringify(llmBody(llmMessage(state, questions), thinkingOff)),
    });
    if (!res.ok) throw new Error(`LLM HTTP ${res.status}`);
    const data = (await res.json()) as {
      choices?: { message: Record<string, unknown> }[] | null;
      usage?: Record<string, unknown> | null;
    };
    round.latencyMs = performance.now() - started;
    if (data.usage && typeof data.usage === "object") {
      const u = data.usage;
      round.inputTokens = numOrNull(u.prompt_tokens);
      round.outputTokens = numOrNull(u.completion_tokens);
      round.cost = numOrNull(u.estimated_cost); // else unknown
    }
    const content = data.choices?.[0]?.message?.content;
    let obj: unknown;
    try {
      obj = JSON.parse(typeof content === "string" ? content : "not json");
    } catch {
      round.invalidJson = true;
      round.invalidDecisions = DECISIONS_PER_ROUND;
      return round;
    }
    if (typeof obj !== "object" || obj === null) {
      round.invalidJson = true;
      round.invalidDecisions = DECISIONS_PER_ROUND;
      return round;
    }
    const record = obj as Record<string, unknown>;
    round.invalidDecisions = countInvalidChoices((id) => record[id], questions);
    return round;
  } catch (err) {
    if (abort.signal.aborted) {
      // 60 s timeout: a failed beat, with no latency or usage recorded
      round.invalidJson = true;
      round.invalidDecisions = DECISIONS_PER_ROUND;
      return round;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** Decide the thinking config with one probe (then, if it thinks, one retry). */
async function probeThinking(): Promise<boolean> {
  const first = await llmProbe({});
  if (!first.thinking) return false;
  try {
    const second = await llmProbe({ chat_template_kwargs: { enable_thinking: false } });
    return !second.thinking;
  } catch {
    return false;
  }
}

// p50 = mean of the 8th and 9th of 16 sorted latencies; p95 = the 16th (for 16
// samples the max is the honest p95).
function percentiles(sorted: number[]): { p50: number; p95: number } {
  if (sorted.length === TOTAL_BEATS) {
    return { p50: (sorted[7] + sorted[8]) / 2, p95: sorted[TOTAL_BEATS - 1] };
  }
  return { p50: sorted[Math.floor(sorted.length / 2)], p95: sorted[sorted.length - 1] };
}

function meanKnown(rounds: Round[], get: (r: Round) => number | null): { mean: number; known: number } {
  const values = rounds.map(get).filter((v): v is number => v !== null);
  return { mean: values.reduce((s, v) => s + v, 0) / values.length, known: values.length };
}

const ms = (v: number) => `${Math.round(v)} ms`;
const mean = (v: number) => v.toFixed(1);

const drexRounds: Round[] = [];
const llmRounds: Round[] = [];

const thinkingOff = await probeThinking();
console.log(`LLM config: ${thinkingOff ? "thinking off" : "default, thinking on"}`);

const band = new Band("calm");
for (let i = 0; i < TOTAL_BEATS; i += 1) {
  if (i === BEATS_PER_MOOD) band.setMood("chaos");
  const beat = await band.decideBeat(async (state, questions) => {
    // Drex first, then the LLM baseline on the identical state and questions.
    const { answers, round } = await drexRound(state, questions);
    drexRounds.push(round);
    const llm = await llmRound(state, questions, thinkingOff);
    llmRounds.push(llm);
    return {
      answers,
      usage: {
        inputTokens: round.inputTokens ?? 0,
        outputTokens: round.outputTokens ?? 0,
        cost: round.cost ?? 0,
        latencyMs: round.latencyMs ?? 0,
      },
    };
  });
  const parts = MUSICIAN_IDS.map((id) => `${id}=${beat.notes[id].play}`).join(" ");
  console.log(`beat ${String(beat.index).padStart(2)} [${beat.mood}] ${parts}`);
}

function report(label: string, rounds: Round[]) {
  const lats = rounds
    .filter((r) => r.latencyMs !== null)
    .map((r) => r.latencyMs as number)
    .sort((a, b) => a - b);
  if (lats.length !== TOTAL_BEATS) {
    console.error(
      `WARNING: ${label}: ${TOTAL_BEATS - lats.length} round(s) have no latency; percentiles over ${lats.length}`,
    );
  }
  const { p50, p95 } = percentiles(lats);
  const max = lats[lats.length - 1];
  const input = meanKnown(rounds, (r) => r.inputTokens);
  const output = meanKnown(rounds, (r) => r.outputTokens);
  const cost = meanKnown(rounds, (r) => r.cost);
  const invalidJson = rounds.filter((r) => r.invalidJson).length;
  const invalidDecisions = rounds.reduce((s, r) => s + r.invalidDecisions, 0);
  return {
    label,
    stats: {
      p50,
      p95,
      max,
      dpsP50: DECISIONS_PER_ROUND / (p50 / 1000),
      input:
        input.known === 0
          ? "unknown"
          : `${Math.round(input.mean)}${input.known < rounds.length ? ` (${input.known}/${rounds.length} known)` : ""}`,
      output:
        output.known === 0
          ? "unknown"
          : `${Math.round(output.mean)}${output.known < rounds.length ? ` (${output.known}/${rounds.length} known)` : ""}`,
      cost: cost.known === 0 ? "not reported" : `$${cost.mean.toExponential(3)}`,
      invalidJson,
      invalidDecisions,
      maxTempo: Math.floor((2 * 60) / (p95 / 1000)),
    },
  };
}

const d = report("Drex", drexRounds).stats;
const l = report("LLM", llmRounds).stats;

console.log("");
console.log("| | Drex (drex-latest) | Qwen3.8-27B (DeepInfra) |");
console.log("|---|---|---|");
console.log(`| rounds | ${TOTAL_BEATS} | ${TOTAL_BEATS} |`);
console.log(`| decisions per round | ${DECISIONS_PER_ROUND} | ${DECISIONS_PER_ROUND} |`);
console.log(`| latency p50 | ${ms(d.p50)} | ${ms(l.p50)} |`);
console.log(`| latency p95 | ${ms(d.p95)} | ${ms(l.p95)} |`);
console.log(`| max latency | ${ms(d.max)} | ${ms(l.max)} |`);
console.log(`| decisions/second at p50 | ${mean(d.dpsP50)} | ${mean(l.dpsP50)} |`);
console.log(`| input tokens per round (mean) | ${d.input} | ${l.input} |`);
console.log(`| output tokens per round (mean) | ${d.output} | ${l.output} |`);
console.log(`| cost per round (mean) | ${d.cost} | ${l.cost} |`);
const dInputMean = meanKnown(drexRounds, (r) => r.inputTokens).mean;
console.log(
  `| cost per round, from published price | $${((dInputMean * DREX_USD_PER_M_INPUT) / 1e6).toExponential(3)} | — |`,
);
console.log(`| invalid JSON rounds | ${d.invalidJson} | ${l.invalidJson} |`);
console.log(
  `| invalid decisions (out of ${TOTAL_BEATS * DECISIONS_PER_ROUND}) | ${d.invalidDecisions} (out of ${TOTAL_BEATS * DECISIONS_PER_ROUND}) | ${l.invalidDecisions} (out of ${TOTAL_BEATS * DECISIONS_PER_ROUND}) |`,
);
console.log(`| max tempo with 2-beat lookahead at p95 | ${d.maxTempo} BPM | ${l.maxTempo} BPM |`);
console.log(
  "Footnote: with 16 samples the max latency is the honest p95 (p50 = mean of the 8th and 9th, p95 = the 16th of the sorted latencies).",
);
console.log(
  `Exact latencies (ms, unrounded): Drex p50=${d.p50.toFixed(2)} p95=${d.p95.toFixed(2)} max=${d.max.toFixed(2)} · LLM p50=${l.p50.toFixed(2)} p95=${l.p95.toFixed(2)} max=${l.max.toFixed(2)}`,
);
const dTok = meanKnown(drexRounds, (r) =>
  r.inputTokens !== null && r.outputTokens !== null ? r.inputTokens + r.outputTokens : null,
);
console.log(`Exact Drex tokens per round (mean of input+output): ${dTok.mean.toFixed(1)} (${dTok.known}/16 known)`);
console.log(
  `Run ${new Date().toISOString()} local ${new Date().toLocaleString()} · ${BEATS_PER_MOOD} beats calm + ${BEATS_PER_MOOD} beats chaos · LLM: ${LLM_MODEL}, temperature 0, json_object, ${thinkingOff ? "thinking off" : "default, thinking on"}`,
);
