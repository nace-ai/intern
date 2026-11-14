import { EventEmitter } from 'node:events';
import { ACTIONS, type Action, type GameState, type Team } from '../shared/types.js';
import type { Brain, BrainDecision } from '../brains/types.js';
import { createGame, step, TICK_DT } from './engine.js';

export interface MatchConfig {
  unitsPerSide: number;
  duration: number;      // sim seconds
  speed: number;         // 0.25 | 0.5 | 1 | 2  (real-time multiplier)
  paused: boolean;       // sim freezes while both brains decide each round
  headless?: boolean;    // paused+headless runs ticks as fast as possible
  roundTicks?: number;
  supplies?: boolean;    // supply crates + collect_supply action (off = benchmarked rules)   // paused mode: decide every N ticks (default 5 = 0.5s sim)
}

export interface TeamMetrics {
  brain: string; decisions: number; cycles: number; latencies: number[];
  cost: number; invalid: number; errors: number; flagCaptures: number;
  lastError?: string; consecutiveErrors: number;
}

export interface TeamSummary {
  brain: string; score: number; decisions: number; cycles: number; decisionsPerSimSec: number;
  meanLatencyMs: number; p95LatencyMs: number; costUsd: number; invalidOutputs: number; errors: number;
  unitsAlive: number; flagCaptures: number;
  latenciesMs: number[]; // per decision cycle
}

export interface MatchResult {
  winner: Team | 'draw'; mode: string; speed: number; paused: boolean; unitsPerSide: number;
  simSeconds: number; wallSeconds: number; teams: Record<Team, TeamSummary>;
}

const PLAN_STEP_S = 2; // LLM plan: advance to next planned order every 2 sim seconds without new orders
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
export const pct = (xs: number[], p: number) => { if (!xs.length) return 0; const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

export class Match extends EventEmitter {
  state: GameState;
  metrics: Record<Team, TeamMetrics>;
  probs: Record<string, Partial<Record<Action, number>>> = {}; // last Drex probabilities per unit
  plans = new Map<string, { queue: Action[]; nextAt: number }>();
  lastApplied: Record<Team, number> = { blue: 0, red: 0 }; // sim time of last applied orders
  private abort = new AbortController();
  private tickWaiters: { n: number; r: () => void }[] = [];
  private roundTick = -1;
  private roundPending = false;
  private timer?: NodeJS.Timeout;
  private t0 = 0;
  private done!: (r: MatchResult) => void;
  stopped = false;

  constructor(public brains: Record<Team, Brain>, public cfg: MatchConfig) {
    super();
    this.state = createGame(cfg.unitsPerSide, cfg.duration, !!cfg.supplies);
    const m = (b: Brain): TeamMetrics => ({ brain: b.name, decisions: 0, cycles: 0, latencies: [], cost: 0, invalid: 0, errors: 0, flagCaptures: 0, consecutiveErrors: 0 });
    this.metrics = { blue: m(brains.blue), red: m(brains.red) };
  }

  get over() { return this.stopped || this.state.winner !== null; }

  start(): Promise<MatchResult> {
    this.t0 = performance.now();
    const p = new Promise<MatchResult>((r) => (this.done = r));
    if (!this.cfg.paused) { this.loop('blue'); this.loop('red'); }
    if (this.cfg.paused && this.cfg.headless) this.fastDrive();
    else this.timer = setInterval(() => this.tick(), (TICK_DT * 1000) / this.cfg.speed);
    return p;
  }

  stop() { this.stopped = true; this.finish(); }

  private tick() {
    if (this.over) return this.finish();
    if (this.cfg.paused) {
      const n = this.cfg.roundTicks ?? 5;
      if (this.roundPending) return;
      if (this.state.tick % n === 0 && this.roundTick !== this.state.tick) { this.startRound(); return; }
    }
    // advance multi-step plans
    for (const [id, p] of this.plans) {
      if (p.queue.length && this.state.time >= p.nextAt) {
        const u = this.state.units.find((x) => x.id === id);
        if (u?.alive) u.order = p.queue.shift()!;
        p.nextAt = this.state.time + PLAN_STEP_S;
      }
    }
    const ev = step(this.state);
    for (const t of ev.captures) this.metrics[t].flagCaptures++;
    this.tickWaiters = this.tickWaiters.filter((w) => (this.state.tick >= w.n ? (w.r(), false) : true));
    this.emit('tick');
    if (this.over) this.finish();
  }

  private async fastDrive() {
    while (!this.over) {
      if (this.roundPending) { await this.roundPromise; continue; }
      this.tick();
      if (this.state.tick % 50 === 0) await new Promise((r) => setImmediate(r));
    }
    this.finish();
  }

  private roundPromise: Promise<void> = Promise.resolve();
  private startRound() {
    this.roundTick = this.state.tick; this.roundPending = true;
    const snap = structuredClone(this.state);
    this.roundPromise = Promise.all((['blue', 'red'] as Team[]).map((t) => this.decideOnce(t, snap))).then(() => { this.roundPending = false; });
  }

  private async decideOnce(team: Team, snap: GameState) {
    const t0 = performance.now();
    try {
      const d = await this.brains[team].decide(snap, team, this.abort.signal);
      if (!this.over) { this.metrics[team].consecutiveErrors = 0; this.apply(team, d, performance.now() - t0); }
    } catch (e: any) {
      if (this.over) return;
      const m = this.metrics[team];
      m.errors++; m.consecutiveErrors++; m.lastError = e.message;
      this.emit('log', `${team} brain error: ${e.message}`);
      // honor Retry-After, else exponential backoff (0.5s .. 8s)
      await sleep(e.retryAfterMs ?? Math.min(8000, 500 * 2 ** (m.consecutiveErrors - 1)));
    }
  }

  private async loop(team: Team) {
    while (!this.over) {
      const at = this.state.tick;
      await this.decideOnce(team, structuredClone(this.state));
      await new Promise<void>((r) => this.tickWaiters.push({ n: at + 1, r })); // at most one cycle per tick
    }
  }

  private apply(team: Team, d: BrainDecision, latencyMs: number) {
    const m = this.metrics[team];
    m.cycles++; m.latencies.push(d.meta?.latencyMs ?? latencyMs); m.cost += d.meta?.costUsd ?? 0;
    if (d.meta?.invalid) m.invalid++;
    this.lastApplied[team] = this.state.time;
    for (const [id, a] of Object.entries(d.orders ?? {})) {
      const u = this.state.units.find((x) => x.id === id);
      if (!u || !u.alive || u.team !== team || !ACTIONS.includes(a)) continue;
      u.order = a; m.decisions++;
      const q = (d.plans?.[id] ?? []).filter((x) => ACTIONS.includes(x));
      this.plans.set(id, { queue: q, nextAt: this.state.time + PLAN_STEP_S });
    }
    if (d.meta?.probabilities) Object.assign(this.probs, d.meta.probabilities);
    this.emit('decision', team);
  }

  private finished = false;
  private finish() {
    if (this.finished) return; this.finished = true;
    this.stopped = true; if (this.timer) clearInterval(this.timer);
    this.abort.abort();
    for (const w of this.tickWaiters) w.r();
    const s = this.state;
    const sum = (t: Team): TeamSummary => {
      const m = this.metrics[t];
      return {
        brain: m.brain, score: s.score[t], decisions: m.decisions, cycles: m.cycles,
        decisionsPerSimSec: +(m.decisions / Math.max(s.time, 0.1)).toFixed(2),
        meanLatencyMs: +mean(m.latencies).toFixed(1), p95LatencyMs: +pct(m.latencies, 0.95).toFixed(1),
        costUsd: +m.cost.toFixed(6), invalidOutputs: m.invalid, errors: m.errors,
        unitsAlive: s.units.filter((u) => u.alive && u.team === t).length, flagCaptures: m.flagCaptures,
        latenciesMs: m.latencies.map((x) => +x.toFixed(1)),
      };
    };
    const result: MatchResult = {
      winner: s.winner ?? 'draw', mode: this.cfg.paused ? 'paused' : `${this.cfg.speed}x`,
      speed: this.cfg.speed, paused: this.cfg.paused, unitsPerSide: this.cfg.unitsPerSide,
      simSeconds: s.time, wallSeconds: +((performance.now() - this.t0) / 1000).toFixed(2),
      teams: { blue: sum('blue'), red: sum('red') },
    };
    this.emit('end', result);
    this.done?.(result);
  }
}
