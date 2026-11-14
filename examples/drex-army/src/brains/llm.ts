import 'dotenv/config';
import { ACTION_DESCRIPTIONS, actionsFor, type Action, type GameState } from '../shared/types.js';
import type { Brain } from './types.js';
import { rulesFor, teamView } from './view.js';

const URL = 'https://api.deepinfra.com/v1/openai/chat/completions';
export const LLM_MODEL = process.env.LLM_MODEL ?? 'deepseek-ai/DeepSeek-V3';
const PRICE_IN = Number(process.env.LLM_PRICE_IN_PER_MTOK ?? '0.32');   // DeepInfra list price, USD / 1M tokens
const PRICE_OUT = Number(process.env.LLM_PRICE_OUT_PER_MTOK ?? '0.89');
const MAX_PLAN = 3;

const systemFor = (s: GameState) => { const ACTIONS = actionsFor(s); return (
  `You are the commander of an army in a real-time capture-the-flag game. ${rulesFor(s)}\n` +
  `Allowed orders (exact strings):\n${ACTIONS.map((a) => `- ${a}: ${ACTION_DESCRIPTIONS[a]}`).join('\n')}\n` +
  `The game keeps running while you think, so give each unit a short plan of 1-${MAX_PLAN} orders: the first runs immediately, ` +
  `each next one starts 2 seconds later unless you send new orders first.\n` +
  `Respond with ONLY a JSON object: {"orders": {"<unit id>": ["<order>", ...], ...}} with one entry for every one of your living units.`); };

export function parseOrders(text: string, unitIds: string[], ACTIONS: readonly Action[]): { orders: Record<string, Action>; plans: Record<string, Action[]> } | null {
  let obj: any;
  try { obj = JSON.parse(text.trim().replace(/^```(?:json)?\s*|\s*```$/g, '')); } catch { return null; }
  const o = obj?.orders;
  if (!o || typeof o !== 'object' || Array.isArray(o)) return null;
  const orders: Record<string, Action> = {}, plans: Record<string, Action[]> = {};
  for (const id of unitIds) {
    let v = o[id];
    if (typeof v === 'string') v = [v];
    if (!Array.isArray(v) || !v.length || !v.every((x) => ACTIONS.includes(x))) return null; // strict: any bad unit = invalid output
    orders[id] = v[0]; plans[id] = v.slice(1, MAX_PLAN);
  }
  return { orders, plans };
}

export const llmBrain: Brain = {
  name: `llm:${LLM_MODEL.split('/').pop()}`,
  async decide(state, team, signal) {
    const view = teamView(state, team);
    const ids = view.your_units.map((u) => u.id);
    if (!ids.length) return { orders: {}, meta: { latencyMs: 0, costUsd: 0 } };
    const t0 = performance.now();
    const res = await fetch(URL, {
      method: 'POST', signal,
      headers: { Authorization: `Bearer ${process.env.DEEPINFRA_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: LLM_MODEL, temperature: 0.2, max_tokens: 800,
        response_format: { type: 'json_object' },
        messages: [{ role: 'system', content: systemFor(state) }, { role: 'user', content: JSON.stringify(view) }],
      }),
    });
    const latencyMs = performance.now() - t0;
    const body: any = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err: any = new Error(`DeepInfra ${res.status}: ${JSON.stringify(body).slice(0, 200)}`);
      const ra = Number(res.headers.get('retry-after') ?? '') * 1000; if (ra) err.retryAfterMs = ra;
      throw err;
    }
    const u = body.usage ?? {};
    const costUsd = u.estimated_cost ?? ((u.prompt_tokens ?? 0) * PRICE_IN + (u.completion_tokens ?? 0) * PRICE_OUT) / 1e6;
    const parsed = parseOrders(body.choices?.[0]?.message?.content ?? '', ids, actionsFor(state));
    if (process.env.LLM_LOG !== '0') console.log(`[llm] ${team} latency=${latencyMs.toFixed(0)}ms in=${u.prompt_tokens} out=${u.completion_tokens} $${costUsd.toFixed(6)} ${parsed ? 'ok' : 'INVALID'}`);
    if (!parsed) {
      // Fallback: invalid output -> all units hold
      return { orders: Object.fromEntries(ids.map((id) => [id, 'hold' as Action])), meta: { latencyMs, costUsd, invalid: true } };
    }
    return { orders: parsed.orders, plans: parsed.plans, meta: { latencyMs, costUsd } };
  },
};
