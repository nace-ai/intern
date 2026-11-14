// Benchmark report: reads a bench results JSON, writes <base>.md + <base>.csv next to it,
// and prints the markdown to stdout. Usage: npx tsx scripts/report.ts [path]
import { readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';

interface TeamModeSummary {
  brain: string; wins: number; winRate: number; meanScore: number;
  meanLatencyMs: number; p95LatencyMs: number; meanCyclesPerMatch: number; meanDecisionsPerSimSec: number;
  meanCostUsdPerMatch: number; totalCostUsd: number; invalidOutputs: number; errors: number;
  meanUnitsAlive: number; flagCaptures: number;
}
interface ModeSummary {
  mode: string;
  matches: number; valid: number; tainted: number; draws: number; meanSimSeconds: number;
  blue: TeamModeSummary; red: TeamModeSummary;
}
interface TeamMatch {
  brain: string; score: number; decisions: number; cycles: number; decisionsPerSimSec: number;
  meanLatencyMs: number; p95LatencyMs: number; costUsd: number; invalidOutputs: number; errors: number;
  unitsAlive: number; flagCaptures: number; latenciesMs: number[];
}
interface BenchMatch {
  index: number; winner: 'blue' | 'red' | 'draw'; mode: string; speed: number; paused: boolean; unitsPerSide: number;
  simSeconds: number; wallSeconds: number; tainted: boolean; taintReason?: string;
  teams: { blue: TeamMatch; red: TeamMatch };
}
interface BenchFile {
  meta: { createdAt: string; commit: string; blue: string; red: string; n: number; modes: string[];
          unitsPerSide: number; durationS: number; models: { drex: string; llm: string }; wallSeconds: number };
  summary: ModeSummary[];
  matches: BenchMatch[];
}

const path = process.argv[2] ?? 'results/latest.json';
const bench: BenchFile = JSON.parse(readFileSync(path, 'utf8'));

const usd = (x: number) => `$${x.toFixed(5)}`;
const pct = (rate: number) => `${Math.round(rate * 100)}%`;

// ---------- Markdown ----------
const lines: string[] = [];
lines.push(`## Benchmark: ${bench.summary[0].blue.brain} (blue) vs ${bench.summary[0].red.brain} (red)`);
lines.push(`${bench.meta.createdAt} | commit ${bench.meta.commit} | ${bench.meta.n} matches/mode | ${bench.meta.unitsPerSide} units/side | models: drex=${bench.meta.models.drex}, llm=${bench.meta.models.llm}`);
lines.push('');

const H = ['Mode', 'Valid/Run', 'Blue win %', 'Red win %', 'Draws', 'Blue latency mean / p95 (ms)', 'Red latency mean / p95 (ms)',
  'Blue decisions/sim-sec', 'Red decisions/sim-sec', 'Blue $/match', 'Red $/match', 'Blue invalid', 'Red invalid', 'Tainted'];
lines.push(`| ${H.join(' | ')} |`);
lines.push(`|${H.map(() => '---|').join('')}`);
for (const s of bench.summary) {
  lines.push(`| ${s.mode} | ${s.valid}/${s.matches} | ${pct(s.blue.winRate)} | ${pct(s.red.winRate)} | ${s.draws} ` +
    `| ${Math.round(s.blue.meanLatencyMs)} / ${Math.round(s.blue.p95LatencyMs)} | ${Math.round(s.red.meanLatencyMs)} / ${Math.round(s.red.p95LatencyMs)} ` +
    `| ${s.blue.meanDecisionsPerSimSec} | ${s.red.meanDecisionsPerSimSec} ` +
    `| ${usd(s.blue.meanCostUsdPerMatch)} | ${usd(s.red.meanCostUsdPerMatch)} ` +
    `| ${s.blue.invalidOutputs} | ${s.red.invalidOutputs} | ${s.tainted} |`);
}
lines.push('');

const totalBlue = bench.summary.reduce((a, s) => a + s.blue.totalCostUsd, 0);
const totalRed = bench.summary.reduce((a, s) => a + s.red.totalCostUsd, 0);
lines.push(`Total cost: blue ${usd(totalBlue)}, red ${usd(totalRed)}`);

const tainted = bench.matches.filter((m) => m.tainted);
if (tainted.length > 0) {
  lines.push('');
  lines.push('Tainted matches:');
  for (const m of tainted) lines.push(`- ${m.mode} #${m.index}: ${m.taintReason ?? 'unknown'}`);
}

const ratios = bench.summary.filter((s) => s.blue.meanCostUsdPerMatch > 0)
  .map((s) => `- Cost ratio (red/blue) per match ${s.mode}: ${(s.red.meanCostUsdPerMatch / s.blue.meanCostUsdPerMatch).toFixed(2)}`);
if (ratios.length > 0) {
  lines.push('');
  lines.push(...ratios);
}

const markdown = lines.join('\n') + '\n';

// ---------- CSV ----------
const CSV_H = ['mode', 'valid', 'matches', 'tainted', 'draws',
  'blue_brain', 'blue_win_rate', 'blue_mean_latency_ms', 'blue_p95_latency_ms', 'blue_cycles_per_match',
  'blue_decisions_per_sim_sec', 'blue_cost_per_match_usd', 'blue_invalid', 'blue_errors', 'blue_mean_units_alive',
  'red_brain', 'red_win_rate', 'red_mean_latency_ms', 'red_p95_latency_ms', 'red_cycles_per_match',
  'red_decisions_per_sim_sec', 'red_cost_per_match_usd', 'red_invalid', 'red_errors', 'red_mean_units_alive'];
const csvRows = [CSV_H.join(',')];
for (const s of bench.summary) {
  csvRows.push([
    s.mode, s.valid, s.matches, s.tainted, s.draws,
    s.blue.brain, s.blue.winRate, s.blue.meanLatencyMs, s.blue.p95LatencyMs, s.blue.meanCyclesPerMatch,
    s.blue.meanDecisionsPerSimSec, s.blue.meanCostUsdPerMatch, s.blue.invalidOutputs, s.blue.errors, s.blue.meanUnitsAlive,
    s.red.brain, s.red.winRate, s.red.meanLatencyMs, s.red.p95LatencyMs, s.red.meanCyclesPerMatch,
    s.red.meanDecisionsPerSimSec, s.red.meanCostUsdPerMatch, s.red.invalidOutputs, s.red.errors, s.red.meanUnitsAlive,
  ].join(','));
}
const csv = csvRows.join('\n') + '\n';

const dir = dirname(path);
const base = basename(path, '.json');
writeFileSync(join(dir, `${base}.md`), markdown);
writeFileSync(join(dir, `${base}.csv`), csv);
console.log(markdown);
