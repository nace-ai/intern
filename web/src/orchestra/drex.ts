// Drex client: one decision round through the /nace proxy.

import { countApiCall } from "./apiCounter.ts";

export const MODEL = "drex-latest";
export const MAX_QUESTIONS = 16;
export const TIMEOUT_MS = 2000;
/** Drex's published price: $0.03 per 1M input tokens (output is not billed). */
export const DREX_USD_PER_INPUT_TOKEN = 0.03 / 1_000_000;

export type Question = {
  type: "choice";
  instructions: string;
  criteria: Record<string, string>;
};

export type QuestionSet = Record<string, Question>;

export type Answer = {
  type?: string;
  choice?: string;
  confidence?: number;
  probabilities?: Record<string, number> | null;
};

export type Usage = {
  inputTokens: number;
  outputTokens: number;
  cost: number;
  latencyMs: number;
};

export type DecideOptions = {
  /** abort this whole round before the 2 s cap */
  signal?: AbortSignal;
  /** the proxy base; defaults to same-origin "/nace" in the browser */
  base?: string;
};

/** Drex took longer than TIMEOUT_MS to answer a round. */
export class DrexTimeout extends Error {
  constructor(message = `Drex did not answer within ${TIMEOUT_MS / 1000} s`) {
    super(message);
    this.name = "DrexTimeout";
  }
}

/** The proxy answered with an HTTP error status. */
export class DrexHttpError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`Drex HTTP ${status}`);
    this.name = "DrexHttpError";
    this.status = status;
  }
}

/** A client asks Drex one round and gets answers for every question it asked. */
export type DrexClient = (
  state: string,
  questions: QuestionSet,
) => Promise<{ answers: Record<string, Answer>; usage: Usage }>;

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

type RawUsage = Record<string, unknown>;

function parseUsage(raw: RawUsage): { inputTokens: number; outputTokens: number; cost: number } {
  const inputTokens = num(raw.input_tokens ?? raw.inputTokens);
  const outputTokens = num(raw.output_tokens ?? raw.outputTokens);
  // Drex never returns usage.cost; price from the input tokens at the
  // published rate (output isn't billed).
  const cost = raw.cost == null ? inputTokens * DREX_USD_PER_INPUT_TOKEN : num(raw.cost);
  return { inputTokens, outputTokens, cost };
}

/**
 * Ask Drex to answer every question given the state. At most 16 questions per
 * request; chunks are sent concurrently. The whole round is aborted after 2 s
 * and throws DrexTimeout. usage and cost may be missing or null in the
 * response: cost is then computed as inputTokens * DREX_USD_PER_INPUT_TOKEN.
 */
export async function decide(
  state: string,
  questions: QuestionSet,
  options: DecideOptions = {},
): Promise<{ answers: Record<string, Answer>; usage: Usage }> {
  const { signal, base = "/nace" } = options;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), TIMEOUT_MS);
  const onAbort = () => abort.abort();
  signal?.addEventListener("abort", onAbort, { once: true });
  const started = performance.now();
  try {
    const names = Object.keys(questions);
    const windows = [];
    for (let i = 0; i < names.length; i += MAX_QUESTIONS) windows.push(names.slice(i, i + MAX_QUESTIONS));
    const chunks = windows.map((window) => {
      const q: QuestionSet = {};
      for (const name of window) q[name] = questions[name];
      return q;
    });
    const results = await Promise.all(
      chunks.map((chunk) => postChunkOnce(`${base}/systemone`, state, chunk, abort.signal, started)),
    );
    const latencyMs = performance.now() - started;

    const answers: Record<string, Answer> = {};
    const usage = { inputTokens: 0, outputTokens: 0, cost: 0 };
    for (const { answers: chunkAnswers, rawUsage } of results) {
      Object.assign(answers, chunkAnswers);
      const u = parseUsage(rawUsage);
      usage.inputTokens += u.inputTokens;
      usage.outputTokens += u.outputTokens;
      usage.cost += u.cost;
    }
    return { answers, usage: { ...usage, latencyMs } };
  } catch (err) {
    if (abort.signal.aborted) {
      // the caller aborted: let its reason propagate
      if (signal?.aborted) throw signal.reason ?? err;
      throw new DrexTimeout();
    }
    throw err;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

async function postChunk(
  url: string,
  state: string,
  questions: QuestionSet,
  signal: AbortSignal,
): Promise<{ answers: Record<string, Answer>; rawUsage: RawUsage }> {
  countApiCall();
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: MODEL, state, questions }),
    signal,
  });
  if (!res.ok) throw new DrexHttpError(res.status);
  const data = (await res.json()) as { answers?: Record<string, Answer> | null; usage?: RawUsage | null };
  return { answers: data.answers ?? {}, rawUsage: data.usage ?? {} };
}

/**
 * Send one chunk, retrying it once on 429 or 5xx when the round's 2 s budget
 * still has time left (the shared abort timer caps the retry too).
 */
async function postChunkOnce(
  url: string,
  state: string,
  questions: QuestionSet,
  signal: AbortSignal,
  roundStartedAt: number,
): Promise<{ answers: Record<string, Answer>; rawUsage: RawUsage }> {
  try {
    return await postChunk(url, state, questions, signal);
  } catch (err) {
    const retryable =
      err instanceof DrexHttpError &&
      (err.status === 429 || err.status >= 500) &&
      !signal.aborted &&
      performance.now() - roundStartedAt < TIMEOUT_MS;
    if (!retryable) throw err;
    return postChunk(url, state, questions, signal);
  }
}
