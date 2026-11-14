// Quick headless single match: npx tsx src/server/sim.ts [blue] [red] [speed|paused]
import 'dotenv/config';
import { Match } from '../engine/match.js';
import { makeBrain } from '../brains/registry.js';

const [blue = 'scripted', red = 'scripted', mode = 'paused'] = process.argv.slice(2);
const paused = mode === 'paused';
const m = new Match({ blue: await makeBrain(blue), red: await makeBrain(red) },
  { unitsPerSide: 10, duration: 60, speed: paused ? 1 : Number(mode), paused, headless: true });
const r = await m.start();
console.log(JSON.stringify(r, null, 2));
process.exit(0);
