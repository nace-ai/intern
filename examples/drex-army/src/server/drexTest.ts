import 'dotenv/config';
import { drexDecide, type ChoiceQuestion } from './drex.js';
import { ACTIONS, ACTION_DESCRIPTIONS } from '../shared/types.js';

// Fake battlefield: 10 blue units vs 10 red, compact JSON.
const rnd = (a: number, b: number) => Math.round(a + Math.random() * (b - a));
const units = Array.from({ length: 10 }, (_, i) => ({ id: `b${i}`, x: rnd(50, 400), y: rnd(50, 550), hp: rnd(20, 100) }));
const enemies = Array.from({ length: 10 }, (_, i) => ({ id: `r${i}`, x: rnd(400, 950), y: rnd(50, 550), hp: rnd(20, 100) }));
const state = {
  you: 'blue',
  map: { w: 1000, h: 600 },
  own_flag: { x: 60, y: 300, carried_by: null },
  enemy_flag: { x: 940, y: 300, carried_by: null },
  time_left_s: 45,
  score: { blue: 0, red: 0 },
  allies: units,
  enemies,
};

const options = Object.fromEntries(ACTIONS.map((a) => [a, ACTION_DESCRIPTIONS[a]]));
const questions: Record<string, ChoiceQuestion> = Object.fromEntries(
  units.map((u) => [u.id, { instructions: `Best order for blue unit ${u.id} right now?`, options }]),
);

for (const model of [process.env.DREX_MODEL ?? 'drex-latest']) {
  for (let i = 0; i < 3; i++) {
    const r = await drexDecide(state, questions);
    if (i === 0) {
      for (const [id, a] of Object.entries(r.answers)) {
        const top = Object.entries(a.probabilities).sort((x, y) => y[1] - x[1]).slice(0, 3)
          .map(([k, v]) => `${k}:${v.toFixed(2)}`).join(' ');
        console.log(`  ${id} hp=${units.find((u) => u.id === id)!.hp} -> ${a.choice}   [${top}]`);
      }
    }
  }
}
