import 'dotenv/config';

const BASE = process.env.NACE_BASE_URL ?? 'https://console.nace.ai';
export const DREX_MODEL = process.env.DREX_MODEL ?? 'drex-latest';
// Price: $0.03 per 1M input tokens (drex-v2.0, drex-max-v1.1, drex-max-finance). Override with DREX_PRICE_PER_MTOK (USD per 1M input tokens) to get cost. Only input tokens billed.
const PRICE_PER_MTOK = Number(process.env.DREX_PRICE_PER_MTOK ?? '0.03');

export interface ChoiceQuestion {
  instructions: string;
  options: Record<string, string>; // label -> description (<=255)
}

export interface DrexResult {
  answers: Record<string, { choice: string; confidence: number; probabilities: Record<string, number> }>;
  model: string;
  inputTokens: number;
  evalMs: number;
  latencyMs: number; // wall clock incl. network
  costUsd: number;
  requestId: string;
}

export async function drexDecide(
  state: unknown,
  questions: Record<string, ChoiceQuestion>,
  signal?: AbortSignal,
): Promise<DrexResult> {
  const key = process.env.NACE_API_KEY;
  if (!key) throw new Error('NACE_API_KEY missing in .env');
  const qs: Record<string, unknown> = {};
  for (const [id, q] of Object.entries(questions)) {
    qs[id] = { type: 'choice', instructions: q.instructions, criteria: q.options };
  }
  const t0 = performance.now();
  const res = await fetch(`${BASE}/v1/systemone`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: DREX_MODEL, state, questions: qs }),
    signal,
  });
  const latencyMs = performance.now() - t0;
  const body: any = await res.json().catch(() => ({}));
  if (!res.ok) {
    const mitigated = res.headers.get('x-vercel-mitigated');
    const err: any = new Error(`Drex ${res.status}${mitigated ? ` (edge firewall: ${mitigated})` : ''}: ${JSON.stringify(body.error ?? body).slice(0, 200)}`);
    const ra = Number(res.headers.get('retry-after-ms') ?? '') || Number(res.headers.get('retry-after') ?? '') * 1000;
    if (ra) err.retryAfterMs = ra;
    throw err;
  }
  const inputTokens = body.usage?.input_tokens ?? 0;
  const costUsd = (inputTokens / 1e6) * PRICE_PER_MTOK;
  const result: DrexResult = {
    answers: body.answers,
    model: body.model,
    inputTokens,
    evalMs: body.evaluation_time_ms,
    latencyMs,
    costUsd,
    requestId: body.request_id,
  };
  if (process.env.DREX_LOG !== '0') {
    console.log(
      `[drex] ${result.model} q=${Object.keys(questions).length} latency=${latencyMs.toFixed(0)}ms eval=${result.evalMs?.toFixed?.(1)}ms tokens=${inputTokens} est$=${costUsd.toFixed(6)}`,
    );
  }
  return result;
}
