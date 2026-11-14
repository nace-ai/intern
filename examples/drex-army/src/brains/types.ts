import type { Action, GameState, Team } from '../shared/types.js';

export interface BrainMeta {
  latencyMs: number;
  costUsd: number;
  invalid?: boolean;     // output could not be parsed -> units hold
  probabilities?: Record<string, Partial<Record<Action, number>>>; // unitId -> action -> p (Drex)
}

export interface BrainDecision {
  orders: Record<string, Action>;            // unitId -> order (only own living units)
  plans?: Record<string, Action[]>;          // optional follow-up orders per unit (LLM multi-step plan)
  meta?: BrainMeta;
}

export interface Brain {
  name: string;
  // Called with a snapshot of the full game state. Return orders for `team`'s living units.
  decide(state: GameState, team: Team, signal?: AbortSignal): Promise<BrainDecision>;
}
