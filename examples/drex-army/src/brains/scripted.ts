import type { Brain, BrainDecision } from './types.js';
import { enemyOf, type Action, type GameState, type Team, type Unit } from '../shared/types.js';

function dist(a: { x: number; y: number }, b: { x: number; y: number }): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.hypot(dx, dy);
}

export const scriptedBrain: Brain = {
  name: 'scripted',
  async decide(state: GameState, team: Team): Promise<BrainDecision> {
    const t0 = performance.now();

    const mine = state.units.filter((u) => u.alive && u.team === team);
    const enemies: Unit[] = state.units.filter((u) => u.alive && u.team === enemyOf(team));
    const myFlag = state.flags[team];

    // Keep the base from going empty: the two lowest-index living units
    // defend whenever rules 1-3 don't take precedence.
    const defenders = new Set(mine.slice(0, 2).map((u) => u.id));

    const orders: Record<string, Action> = {};
    for (const unit of mine) {
      let order: Action;
      if (unit.hp < 0.3 * unit.maxHp) {
        order = 'retreat_to_base';
      } else if (unit.carryingFlag) {
        order = 'push_enemy_flag';
      } else if (enemies.some((e) => dist(unit, e) <= unit.range * 1.5)) {
        order = 'attack_nearest_enemy';
      } else if (
        defenders.has(unit.id) ||
        myFlag.carriedBy !== null ||
        enemies.some((e) => dist(e, myFlag) <= 200)
      ) {
        order = 'defend_own_flag';
      } else {
        order = 'push_enemy_flag';
      }
      orders[unit.id] = order;
    }

    return {
      orders,
      meta: { latencyMs: performance.now() - t0, costUsd: 0 },
    };
  },
};
