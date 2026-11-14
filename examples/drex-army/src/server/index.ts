import 'dotenv/config';
import { WebSocketServer, WebSocket } from 'ws';
import { existsSync, readFileSync } from 'node:fs';
import { Match, type MatchConfig } from '../engine/match.js';
import { makeBrain } from '../brains/registry.js';

const PORT = Number(process.env.PORT ?? 8787);
const wss = new WebSocketServer({ port: PORT });
let match: Match | null = null;

const send = (ws: WebSocket, msg: unknown) => ws.readyState === ws.OPEN && ws.send(JSON.stringify(msg));
const broadcast = (msg: unknown) => { const s = JSON.stringify(msg); for (const c of wss.clients) if (c.readyState === c.OPEN) c.send(s); };

function liveMetrics(m: Match) {
  const out: any = {};
  for (const t of ['blue', 'red'] as const) {
    const x = m.metrics[t], lat = x.latencies;
    const recent = lat.slice(-10);
    out[t] = {
      brain: x.brain, decisions: x.decisions, cycles: x.cycles, cost: x.cost, invalid: x.invalid, errors: x.errors, lastError: x.consecutiveErrors ? x.lastError : null,
      avgLatencyMs: recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : 0,
      decisionsPerSec: x.decisions / Math.max(m.state.time, 0.1),
      alive: m.state.units.filter((u) => u.alive && u.team === t).length,
      orderAgeS: x.cycles ? m.state.time - m.lastApplied[t] : m.state.time,
    };
  }
  return out;
}

async function startMatch(opts: any) {
  match?.stop();
  const cfg: MatchConfig = {
    unitsPerSide: Number(opts.units ?? 10), duration: Number(opts.duration ?? 60),
    speed: Number(opts.speed ?? 1), paused: !!opts.paused, supplies: !!opts.supplies,
  };
  const [blue, red] = await Promise.all([makeBrain(opts.blue ?? 'scripted'), makeBrain(opts.red ?? 'scripted')]);
  const m = new Match({ blue, red }, cfg);
  match = m;
  m.on('tick', () => broadcast({ type: 'state', state: m.state, metrics: liveMetrics(m), probs: m.probs, paused: cfg.paused }));
  m.on('log', (s: string) => { console.log(s); broadcast({ type: 'log', text: s }); });
  m.on('end', (r) => { console.log('[match]', JSON.stringify(r)); broadcast({ type: 'end', result: r }); });
  console.log(`[match] start blue=${blue.name} red=${red.name}`, cfg);
  m.start();
}

wss.on('connection', (ws) => {
  send(ws, { type: 'hello' });
  try { if (existsSync('results/latest.json')) { const b = JSON.parse(readFileSync('results/latest.json', 'utf8')); send(ws, { type: 'bench', meta: b.meta, summary: b.summary }); } } catch {}
  ws.on('message', (raw) => {
    let msg: any; try { msg = JSON.parse(String(raw)); } catch { return; }
    if (msg.type === 'start') startMatch(msg).catch((e) => console.error(e));
    if (msg.type === 'stop') match?.stop();
  });
});
console.log(`[server] ws://localhost:${PORT}`);
