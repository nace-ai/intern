import type { GameState, Unit } from '../src/shared/types';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const cv = $<HTMLCanvasElement>('cv');
const ctx = cv.getContext('2d')!;
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const C = {
  blue: css('--blue'), red: css('--red'), ink: css('--ink'), ink2: css('--ink-2'),
  field: css('--field'), grid: css('--grid'), line: css('--line'), blueTint: css('--blue-tint'), redTint: css('--red-tint'),
  bg: css('--bg'), go: css('--go'), warn: css('--warn'),
};
const ORDER_ABBR: Record<string, string> = {
  attack_nearest_enemy: 'A', push_enemy_flag: 'F', defend_own_flag: 'D',
  support_nearest_ally: 'S', retreat_to_base: 'R', hold: 'H', collect_supply: 'C',
};
const ORDER_LABEL: Record<string, string> = {
  attack_nearest_enemy: 'Attack nearest enemy', push_enemy_flag: 'Push enemy flag', defend_own_flag: 'Defend own flag',
  support_nearest_ally: 'Support nearest ally', retreat_to_base: 'Retreat to base', hold: 'Hold', collect_supply: 'Collect supply',
};

// Optional images from web/public/assets (shape fallback if missing)
const img = (name: string) => { const i = new Image(); i.src = `/assets/${name}`; return i; };
const IMAGES = { blue: img('blue_unit.png'), red: img('red_unit.png'), map: img('map.png'), flag_blue: img('flag_blue.png'), flag_red: img('flag_red.png') };
const ready = (i: HTMLImageElement) => i.complete && i.naturalWidth > 0;

let state: GameState | null = null;
let metrics: any = null;
let probs: Record<string, Record<string, number>> = {};
let selected: string | null = null;
let lastResult: any = null;

// --- combat effects (client-side, mirrors engine: every unit auto-fires at nearest enemy in range) ---
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const prevHp = new Map<string, number>();
const prevAlive = new Map<string, boolean>();
const hitAt = new Map<string, number>();
const bursts: { x: number; y: number; team: 'blue' | 'red'; t0: number }[] = [];
let shakeUntil = 0;
function trackEffects(s: GameState) {
  const now = performance.now();
  for (const u of s.units) {
    const hp = prevHp.get(u.id), wasAlive = prevAlive.get(u.id);
    if (hp !== undefined && u.hp < hp - 0.01) hitAt.set(u.id, now);
    if (wasAlive && !u.alive) { bursts.push({ x: u.x, y: u.y, team: u.team, t0: now }); shakeUntil = now + 180; }
    prevHp.set(u.id, u.hp); prevAlive.set(u.id, u.alive);
  }
  if (s.tick <= 1) { bursts.length = 0; hitAt.clear(); }
}
const easeOut = (t: number) => 1 - Math.pow(1 - t, 4);
function drawCombat(s: GameState, now: number) {
  const alive = s.units.filter((u) => u.alive);
  alive.forEach((u, i) => {
    let tgt: Unit | null = null, bd = Infinity;
    for (const e of alive) if (e.team !== u.team) { const d = Math.hypot(e.x - u.x, e.y - u.y); if (d < bd) { bd = d; tgt = e; } }
    if (!tgt || bd > u.range) return;
    const dx = tgt.x - u.x, dy = tgt.y - u.y;
    const flicker = reduceMotion ? 0.5 : 0.3 + 0.35 * (0.5 + 0.5 * Math.sin(now / 35 + i * 1.7));
    ctx.globalAlpha = flicker; ctx.strokeStyle = C[u.team]; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(u.x + dx * 0.15, u.y + dy * 0.15); ctx.lineTo(tgt.x - dx * 0.1, tgt.y - dy * 0.1); ctx.stroke();
    if (!reduceMotion) {
      const t = ((now / 180) + i * 0.37) % 1;
      ctx.globalAlpha = 1; ctx.fillStyle = C[u.team];
      ctx.beginPath(); ctx.arc(u.x + dx * (0.15 + 0.75 * t), u.y + dy * (0.15 + 0.75 * t), 2.5, 0, Math.PI * 2); ctx.fill();
      // impact sparks as the round lands
      if (t > 0.8) {
        const k = (t - 0.8) / 0.2, ix = u.x + dx * 0.9, iy = u.y + dy * 0.9;
        ctx.globalAlpha = 1 - k; ctx.strokeStyle = C[u.team]; ctx.lineWidth = 1.5;
        for (let j = 0; j < 4; j++) {
          const a = Math.atan2(-dy, -dx) + (j - 1.5) * 0.6, r = 3 + 7 * k;
          ctx.beginPath(); ctx.moveTo(ix + Math.cos(a) * 2, iy + Math.sin(a) * 2); ctx.lineTo(ix + Math.cos(a) * r, iy + Math.sin(a) * r); ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }
      // muzzle flash
      ctx.globalAlpha = 0.5 * (1 - t); ctx.beginPath(); ctx.arc(u.x + dx * 0.15, u.y + dy * 0.15, 3 + 3 * (1 - t), 0, Math.PI * 2); ctx.fill();
    }
  });
  ctx.globalAlpha = 1;
}
function drawHitsAndBursts(s: GameState, now: number) {
  if (reduceMotion) { bursts.length = 0; return; }
  for (const u of s.units) {
    const h = hitAt.get(u.id); if (!u.alive || h === undefined) continue;
    const a = 1 - (now - h) / 160; if (a <= 0) continue;
    ctx.globalAlpha = a; ctx.strokeStyle = C.bg; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(u.x, u.y, 11, 0, Math.PI * 2); ctx.stroke();
  }
  for (let i = bursts.length - 1; i >= 0; i--) {
    const b = bursts[i], t = (now - b.t0) / 650;
    if (t >= 1) { bursts.splice(i, 1); continue; }
    const e = easeOut(t);
    if (t < 0.25) { ctx.globalAlpha = 0.8 * (1 - t / 0.25); ctx.fillStyle = C.bg; ctx.beginPath(); ctx.arc(b.x, b.y, 14 + 10 * e, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = C[b.team]; ctx.globalAlpha = 0.5 * (1 - t / 0.25); ctx.beginPath(); ctx.arc(b.x, b.y, 10 + 6 * e, 0, Math.PI * 2); ctx.fill(); }
    ctx.globalAlpha = 1 - t; ctx.strokeStyle = C[b.team]; ctx.lineWidth = 3 * (1 - t) + 0.5;
    ctx.beginPath(); ctx.arc(b.x, b.y, 8 + 26 * e, 0, Math.PI * 2); ctx.stroke();
    for (let k = 0; k < 8; k++) {
      const ang = (k / 8) * Math.PI * 2 + b.t0;
      const r0 = 6 + 14 * e, r1 = r0 + 8 * (1 - t);
      ctx.beginPath(); ctx.moveTo(b.x + Math.cos(ang) * r0, b.y + Math.sin(ang) * r0); ctx.lineTo(b.x + Math.cos(ang) * r1, b.y + Math.sin(ang) * r1); ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}

const wsUrl = `ws://${location.hostname}:8787`;
let ws: WebSocket;
const setConn = (s: 'on' | 'off', text: string) => { const el = $('conn'); el.dataset.state = s; el.textContent = text; };
function connect() {
  ws = new WebSocket(wsUrl);
  ws.onopen = () => setConn('on', 'Sim server connected');
  ws.onclose = () => { setConn('off', 'Sim server offline, retrying'); setTimeout(connect, 1000); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'state') { trackEffects(msg.state); state = msg.state; metrics = msg.metrics; probs = msg.probs ?? {}; lastResult = null; renderHud(); }
    if (msg.type === 'end') { lastResult = msg.result; renderHud(); }
    if (msg.type === 'bench') renderBench(msg.meta, msg.summary);
  };
}
connect();

$('start').onclick = () => ws.send(JSON.stringify({
  type: 'start', blue: $<HTMLSelectElement>('blue').value, red: $<HTMLSelectElement>('red').value,
  speed: Number($<HTMLSelectElement>('speed').value), paused: $<HTMLInputElement>('paused').checked,
  units: Number($<HTMLInputElement>('units').value), supplies: $<HTMLInputElement>('supplies').checked,
}));
document.querySelectorAll<HTMLButtonElement>('[data-preset]').forEach((b) => (b.onclick = () => {
  const [blue, red, speed, paused, supplies] = b.dataset.preset!.split(',');
  $<HTMLInputElement>('supplies').checked = supplies === '1';
  $<HTMLSelectElement>('blue').value = blue; $<HTMLSelectElement>('red').value = red;
  $<HTMLSelectElement>('speed').value = speed; $<HTMLInputElement>('paused').checked = paused === '1';
  $('start').click();
}));
$('stop').onclick = () => ws.send(JSON.stringify({ type: 'stop' }));

cv.addEventListener('click', (e) => {
  if (!state) return;
  const r = cv.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * cv.width, y = ((e.clientY - r.top) / r.height) * cv.height;
  let best: Unit | null = null, bd = 25;
  for (const u of state.units) { const d = Math.hypot(u.x - x, u.y - y); if (d < bd) { bd = d; best = u; } }
  selected = best?.id ?? null; renderHud();
});

function draw() {
  requestAnimationFrame(draw);
  const W = cv.width, H = cv.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = C.field; ctx.fillRect(0, 0, W, H);
  const tNow = performance.now();
  if (!reduceMotion && tNow < shakeUntil) { const m = 3 * ((shakeUntil - tNow) / 180); ctx.translate((Math.random() - 0.5) * 2 * m, (Math.random() - 0.5) * 2 * m); }
  if (ready(IMAGES.map)) ctx.drawImage(IMAGES.map, 0, 0, W, H);
  else {
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    for (let x = 50; x < W; x += 50) { ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, H); ctx.stroke(); }
    for (let y = 50; y < H; y += 50) { ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(W, y + 0.5); ctx.stroke(); }
    ctx.strokeStyle = C.line; ctx.setLineDash([6, 6]); ctx.beginPath(); ctx.moveTo(W / 2, 0); ctx.lineTo(W / 2, H); ctx.stroke(); ctx.setLineDash([]);
  }
  if (!state) return;
  for (const t of ['blue', 'red'] as const) {
    const b = state.bases[t];
    ctx.fillStyle = t === 'blue' ? C.blueTint : C.redTint;
    ctx.beginPath(); ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = C[t]; ctx.lineWidth = 1.5; ctx.setLineDash([4, 4]); ctx.stroke(); ctx.setLineDash([]);
  }
  for (const u of state.units) {
    if (u.alive) continue;
    ctx.strokeStyle = C.line; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(u.x - 4, u.y - 4); ctx.lineTo(u.x + 4, u.y + 4); ctx.moveTo(u.x + 4, u.y - 4); ctx.lineTo(u.x - 4, u.y + 4); ctx.stroke();
  }
  const now = performance.now();
  for (const c of state.supplies ?? []) {
    const bob = reduceMotion ? 0 : Math.sin(now / 300 + c.y) * 1.5;
    ctx.save(); ctx.translate(c.x, c.y + bob); ctx.rotate(Math.PI / 4);
    ctx.fillStyle = C.go; ctx.strokeStyle = C.bg; ctx.lineWidth = 2;
    ctx.fillRect(-7, -7, 14, 14); ctx.strokeRect(-7, -7, 14, 14); ctx.restore();
    ctx.fillStyle = C.bg; ctx.font = '700 11px Barlow, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('+', c.x, c.y + bob + 0.5);
  }
  drawCombat(state, now);
  for (const u of state.units) {
    if (!u.alive) continue;
    const im = IMAGES[u.team];
    if (ready(im)) ctx.drawImage(im, u.x - 12, u.y - 12, 24, 24);
    else {
      ctx.fillStyle = C[u.team]; ctx.strokeStyle = C.bg; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(u.x, u.y, 10, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = C.bg; ctx.font = '600 11px Barlow, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(ORDER_ABBR[u.order] ?? '?', u.x, u.y + 0.5);
    }
    if (u.id === selected) { ctx.strokeStyle = C.ink; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(u.x, u.y, 15, 0, Math.PI * 2); ctx.stroke(); }
    const frac = u.hp / u.maxHp;
    ctx.fillStyle = C.grid; ctx.fillRect(u.x - 11, u.y - 18, 22, 3);
    ctx.fillStyle = frac > 0.5 ? C.go : frac > 0.25 ? C.ink2 : C.warn; ctx.fillRect(u.x - 11, u.y - 18, 22 * frac, 3);
  }
  drawHitsAndBursts(state, now);
  for (const u of state.units) {
    if (!u.alive || !u.carryingFlag) continue;
    const ph = reduceMotion ? 0.5 : (now / 700) % 1;
    ctx.globalAlpha = 0.9 * (1 - ph); ctx.strokeStyle = C[u.team === 'blue' ? 'red' : 'blue']; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(u.x, u.y, 14 + 14 * ph, 0, Math.PI * 2); ctx.stroke(); ctx.globalAlpha = 1;
  }
  for (const t of ['blue', 'red'] as const) {
    const f = state.flags[t], im = IMAGES[`flag_${t}`];
    if (ready(im)) ctx.drawImage(im, f.x - 10, f.y - 24, 20, 24);
    else {
      ctx.strokeStyle = C.ink; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(f.x, f.y + 2); ctx.lineTo(f.x, f.y - 24); ctx.stroke();
      ctx.fillStyle = C[t]; ctx.beginPath(); ctx.moveTo(f.x + 1, f.y - 24); ctx.lineTo(f.x + 17, f.y - 18.5); ctx.lineTo(f.x + 1, f.y - 13); ctx.fill();
    }
  }
}
requestAnimationFrame(draw);

const fmt$ = (x: number) => (x < 0.01 ? `$${x.toFixed(5)}` : `$${x.toFixed(4)}`);
const esc = (s: string) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));
const pretty = (brain: string) => brain === 'drex' ? 'Drex' : brain.startsWith('llm:') ? brain.slice(4) : brain === 'scripted' ? 'Scripted rules' : brain;

// [label, value fn, better: 'hi' | 'lo']
const TAPE: [string, (m: any) => number, (v: number) => string, 'hi' | 'lo'][] = [
  ['Units alive', (m) => m.alive, (v) => String(v), 'hi'],
  ['Decisions / sec', (m) => m.decisionsPerSec, (v) => v.toFixed(1), 'hi'],
  ['Avg latency', (m) => m.avgLatencyMs, (v) => (v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v.toFixed(0)}ms`), 'lo'],
  ['Order age', (m) => m.orderAgeS, (v) => `${v.toFixed(1)}s`, 'lo'],
  ['Decision cycles', (m) => m.cycles, (v) => String(v), 'hi'],
  ['Spend so far', (m) => m.cost, fmt$, 'lo'],
  ['Cost / 1k orders', (m) => (m.decisions ? (m.cost / m.decisions) * 1000 : 0), (v) => (v ? `$${v.toFixed(4)}` : '–'), 'lo'],
  ['Invalid outputs', (m) => m.invalid, (v) => String(v), 'lo'],
];

function renderHud() {
  if (state) {
    $('clock').textContent = `${state.time.toFixed(1)} / ${state.duration}s`;
    for (const t of ['blue', 'red'] as const) {
      const el = $(`${t}Kills`), v = String(state.score[t]);
      if (el.textContent !== v) { el.textContent = v; if (!reduceMotion) el.animate([{ transform: 'scale(1.35)' }, { transform: 'scale(1)' }], { duration: 260, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }); }
    }
  }
  if (metrics) {
    $('blueName').textContent = pretty(metrics.blue.brain); $('redName').textContent = pretty(metrics.red.brain);
    $('tape').innerHTML = TAPE.map(([label, get, fmt, better]) => {
      const b = get(metrics.blue) ?? 0, r = get(metrics.red) ?? 0;
      const cmp = b !== r && !(label === 'Cost / 1k orders' && (!b || !r));
      const bLead = cmp && (better === 'hi' ? b > r : b < r), rLead = cmp && !bLead;
      return `<tr><td class="bv${bLead ? ' lead' : ''}">${fmt(b)}</td><td class="metric">${label}</td><td class="rv${rLead ? ' lead' : ''}">${fmt(r)}</td></tr>`;
    }).join('');
    $('errBlue').textContent = metrics.blue.lastError ? `Blue brain error: ${metrics.blue.lastError}` : '';
    $('errRed').textContent = metrics.red.lastError ? `Red brain error: ${metrics.red.lastError}` : '';
  }
  const banner = $('banner');
  if (lastResult) {
    const w = lastResult.winner, t = lastResult.teams;
    banner.dataset.win = w;
    banner.textContent = w === 'draw' ? `Draw, ${t.blue.score} to ${t.red.score}` : `${pretty(t[w].brain)} wins, ${t.blue.score} to ${t.red.score}`;
  } else if (state) {
    delete banner.dataset.win;
    banner.textContent = metrics?.blue && $<HTMLInputElement>('paused').checked ? 'Paused mode: both brains decide each round' : 'Live: the sim does not wait for either brain';
  }

  const u = state?.units.find((x) => x.id === selected);
  if (!u) return;
  const p = probs[u.id];
  let html = `<div class="unit-head"><b>${esc(u.id)}</b><span class="hint">${u.alive ? `${u.hp.toFixed(0)} hp` : 'down'} · ${ORDER_LABEL[u.order] ?? u.order}</span></div>`;
  if (p) {
    html += Object.entries(p).sort((a, b) => b[1] - a[1]).map(([k, v], i) =>
      `<div class="prob${i === 0 ? ' top' : ''}"><span>${ORDER_LABEL[k] ?? esc(k)}</span><span>${(v * 100).toFixed(0)}%</span><div class="track"><i style="transform:scaleX(${v.toFixed(3)})"></i></div></div>`).join('');
  } else html += `<p class="hint">${u.team === 'blue' && metrics?.blue.brain === 'drex' ? 'Waiting for the first Drex decision.' : 'Only Drex reports a probability for each order. LLM orders arrive as text.'}</p>`;
  $('unitInfo').innerHTML = html;
}

function renderBench(meta: any, summary: any[]) {
  $('results').hidden = false;
  const pctf = (x: number) => `${Math.round(x * 100)}%`;
  const b = pretty(summary[0]?.blue.brain ?? 'blue'), r = pretty(summary[0]?.red.brain ?? 'red');
  const wins = summary.reduce((a, s) => a + s.blue.wins, 0), total = summary.reduce((a, s) => a + s.valid, 0);
  $('resultsBody').innerHTML =
    `<p class="bench-meta">${esc(b)} won <b>${wins} of ${total}</b> against ${esc(r)} · ${meta?.n} matches per mode</p>` +
    `<table class="bench"><tr><th>Mode</th><th>${esc(b)}</th><th>${esc(r)}</th><th>Latency</th><th>$ / match</th></tr>` +
    summary.map((s) => `<tr><td>${s.mode.replace('x', '×')}${s.tainted ? ` <span class="hint" title="runs with API failures, excluded">(${s.tainted} excluded)</span>` : ''}</td>` +
      `<td class="b">${pctf(s.blue.winRate)}</td><td class="r">${pctf(s.red.winRate)}</td>` +
      `<td>${(s.blue.meanLatencyMs / 1000).toFixed(2)}s / ${(s.red.meanLatencyMs / 1000).toFixed(2)}s</td>` +
      `<td>${s.blue.meanCostUsdPerMatch.toFixed(4)} / ${s.red.meanCostUsdPerMatch.toFixed(4)}</td></tr>`).join('') +
    `</table>`;
}
