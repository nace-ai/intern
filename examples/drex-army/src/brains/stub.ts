// TEMPORARY stub until Intern's src/brains/scripted.ts lands.
import type { Brain } from './types.js';
export const stubBrain: Brain = { name: 'stub', async decide(s, team) {
  return { orders: Object.fromEntries(s.units.filter((u) => u.team === team && u.alive).map((u) => [u.id, u.hp < 30 ? 'retreat_to_base' : 'attack_nearest_enemy'])) };
} };
