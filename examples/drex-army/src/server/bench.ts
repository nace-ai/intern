// Headless benchmark: N matches per mode, run in parallel.
// npm run bench -- --n 3 --modes 0.25,0.5,1,2,paused --blue drex --red llm --parallel 6 --drex-parallel 2 [--duration 60] [--units 10]
import 'dotenv/config';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { Match, pct, type MatchResult } from '../engine/match.js';
import { makeBrain } from '../brains/registry.js';
import type { Team } from '../shared/types.js';

process.env.DREX_LOG ??= '0';
process.env.LLM_LOG ??= '0';

const args = Object.fromEntries(process.argv.slice(2).reduce<[string, string][]>((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1]?.startsWith('--') || arr[i + 1] === undefined ? 'true' : arr[i + 1]]);
  return acc;
}, []));
const N = Number(args.n ?? 3);
const MODES = String(args.modes ?? '0.25,0.5,1,2,paused').split(',');
const BLUE = args.blue ?? 'drex', RED = args.red ?? 'llm';
const PARALLEL = Number(args.parallel ?? 6);
const DREX_PARALLEL = Number(args['drex-parallel'] ?? 2); // keep Nace edge firewall happy
const DURATION = Number(args.duration ?? 60), UNITS = Number(args.units ?? 10);
const usesDrex = BLUE === 'drex' || RED === 'drex';

export interface BenchMatch extends MatchResult { index: number; tainted: boolean; taintReason?: string }

function taint(r: MatchResult): string | undefined {
  for (const t of ['blue', 'red'] as Team[]) {
    const x = r.teams[t];
    if (x.cycles === 0) return `${t} made 0 decision cycles (${x.errors} errors)`;
    if (x.errors / (x.cycles + x.errors) > 0.1) return `${t} errors ${x.errors}/${x.cycles + x.errors} cycles`;
  }
}

async function runOne(mode: string, index: number): Promise<BenchMatch> {
  const paused = mode === 'paused';
  const m = new Match({ blue: await makeBrain(BLUE), red: await makeBrain(RED) },
    { unitsPerSide: UNITS, duration: DURATION, speed: paused ? 1 : Number(mode), paused, headless: true });
  const r = await m.start();
  const reason = taint(r);
  console.log(`  [${r.mode} #${index}] winner=${r.winner} ${r.teams.blue.score}:${r.teams.red.score} sim=${r.simSeconds}s wall=${r.wallSeconds}s${reason ? `  TAINTED: ${reason}` : ''}`);
  return { ...r, index, tainted: !!reason, taintReason: reason };
}

// simple pool
async function pool<T>(jobs: (() => Promise<T>)[], size: number): Promise<T[]> {
  const out: T[] = []; let i = 0;
  await Promise.all(Array.from({ length: Math.min(size, jobs.length) }, async () => { while (i < jobs.length) { const k = i++; out[k] = await jobs[k](); } }));
  return out;
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

export function summarize(matches: BenchMatch[], modes: string[]) {
  return modes.map((mode) => {
    const all = matches.filter((m) => m.mode === (mode === 'paused' ? 'paused' : `${Number(mode)}x`));
    const ok = all.filter((m) => !m.tainted);
    const team = (t: Team) => {
      const lat = ok.flatMap((m) => m.teams[t].latenciesMs);
      return {
        brain: ok[0]?.teams[t].brain ?? all[0]?.teams[t].brain,
        wins: ok.filter((m) => m.winner === t).length,
        winRate: ok.length ? +(ok.filter((m) => m.winner === t).length / ok.length).toFixed(3) : 0,
        meanScore: +mean(ok.map((m) => m.teams[t].score)).toFixed(2),
        meanLatencyMs: +mean(lat).toFixed(1), p95LatencyMs: +pct(lat, 0.95).toFixed(1),
        meanCyclesPerMatch: +mean(ok.map((m) => m.teams[t].cycles)).toFixed(1),
        meanDecisionsPerSimSec: +mean(ok.map((m) => m.teams[t].decisionsPerSimSec)).toFixed(2),
        meanCostUsdPerMatch: +mean(ok.map((m) => m.teams[t].costUsd)).toFixed(6),
        totalCostUsd: +all.reduce((a, m) => a + m.teams[t].costUsd, 0).toFixed(6),
        invalidOutputs: ok.reduce((a, m) => a + m.teams[t].invalidOutputs, 0),
        errors: all.reduce((a, m) => a + m.teams[t].errors, 0),
        meanUnitsAlive: +mean(ok.map((m) => m.teams[t].unitsAlive)).toFixed(2),
        flagCaptures: ok.reduce((a, m) => a + m.teams[t].flagCaptures, 0),
      };
    };
    return {
      mode: mode === 'paused' ? 'paused' : `${Number(mode)}x`,
      matches: all.length, valid: ok.length, tainted: all.length - ok.length,
      draws: ok.filter((m) => m.winner === 'draw').length,
      meanSimSeconds: +mean(ok.map((m) => m.simSeconds)).toFixed(1),
      blue: team('blue'), red: team('red'),
    };
  });
}

export function table(summary: ReturnType<typeof summarize>) {
  const h = '| Mode | Valid/Run | Blue wins | Red wins | Draws | Blue lat mean/p95 (ms) | Red lat mean/p95 (ms) | Blue cycles | Red cycles | Blue $/match | Red $/match | Red invalid |';
  const sep = '|' + '---|'.repeat(12);
  const rows = summary.map((s) => `| ${s.mode} | ${s.valid}/${s.matches} | ${s.blue.wins} | ${s.red.wins} | ${s.draws} | ${s.blue.meanLatencyMs}/${s.blue.p95LatencyMs} | ${s.red.meanLatencyMs}/${s.red.p95LatencyMs} | ${s.blue.meanCyclesPerMatch} | ${s.red.meanCyclesPerMatch} | ${s.blue.meanCostUsdPerMatch} | ${s.red.meanCostUsdPerMatch} | ${s.red.invalidOutputs} |`);
  return [h, sep, ...rows].join('\n');
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const t0 = Date.now();
  console.log(`[bench] ${BLUE} (blue) vs ${RED} (red), n=${N}, modes=${MODES.join(',')}, parallel=${usesDrex ? Math.min(PARALLEL, DREX_PARALLEL) : PARALLEL}`);
  // Order jobs: longest (slow realtime) first so the pool finishes sooner
  const jobs = MODES.flatMap((mode) => Array.from({ length: N }, (_, i) => () => runOne(mode, i)));
  const matches = await pool(jobs, usesDrex ? Math.min(PARALLEL, DREX_PARALLEL) : PARALLEL);
  const summary = summarize(matches, MODES);
  let commit = ''; try { commit = execSync('git rev-parse --short HEAD').toString().trim(); } catch {}
  const out = {
    meta: {
      createdAt: new Date().toISOString(), commit, blue: BLUE, red: RED, n: N, modes: MODES, unitsPerSide: UNITS, durationS: DURATION,
      models: { drex: process.env.DREX_MODEL ?? 'drex-latest', llm: process.env.LLM_MODEL ?? 'deepseek-ai/DeepSeek-V3' },
      wallSeconds: Math.round((Date.now() - t0) / 1000),
    },
    summary,
    matches,
  };
  mkdirSync('results', { recursive: true });
  const file = `results/bench-${out.meta.createdAt.replace(/[:.]/g, '-')}.json`;
  writeFileSync(file, JSON.stringify(out, null, 2));
  writeFileSync('results/latest.json', JSON.stringify(out, null, 2));
  console.log(`\n${table(summary)}\n\n[bench] wrote ${file} (+ results/latest.json) in ${out.meta.wallSeconds}s`);
  process.exit(0);
}
