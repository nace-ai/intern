import { ACTION_DESCRIPTIONS, actionsFor, type Action } from '../shared/types.js';
import { drexDecide, type ChoiceQuestion } from '../server/drex.js';
import type { Brain } from './types.js';
import { rulesFor, teamView } from './view.js';

export const drexBrain: Brain = {
  name: 'drex',
  async decide(state, team, signal) {
    const view = teamView(state, team);
    const allowed = actionsFor(state);
    const OPTIONS = Object.fromEntries(allowed.map((a) => [a, ACTION_DESCRIPTIONS[a]]));
    const questions: Record<string, ChoiceQuestion> = {};
    for (const u of view.your_units) {
      questions[u.id] = {
        instructions: `You command the ${team} army. Choose the best order right now for your unit ${u.id} to help your team win.`,
        options: OPTIONS,
      };
    }
    if (!Object.keys(questions).length) return { orders: {}, meta: { latencyMs: 0, costUsd: 0 } };
    const r = await drexDecide({ rules: rulesFor(state), ...view }, questions, signal);
    const orders: Record<string, Action> = {};
    const probabilities: Record<string, Partial<Record<Action, number>>> = {};
    for (const [id, a] of Object.entries(r.answers ?? {})) {
      if (allowed.includes(a.choice as Action)) orders[id] = a.choice as Action;
      probabilities[id] = a.probabilities as Partial<Record<Action, number>>;
    }
    return { orders, meta: { latencyMs: r.latencyMs, costUsd: r.costUsd, invalid: Object.keys(orders).length === 0, probabilities } };
  },
};
