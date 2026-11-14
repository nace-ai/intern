import { enemyOf, type GameState, type Team, type Unit, type Vec } from '../shared/types.js';

export const TICK_DT = 0.1; // sim seconds per tick (10 ticks/sec)
export const SUPPLY = { spots: [{ x: 500, y: 110 }, { x: 500, y: 300 }, { x: 500, y: 490 }], every: 8, first: 3, heal: 30 };
export const STATS = { hp: 100, speed: 60, range: 70, damage: 20, carrySpeedMul: 0.8, regen: 8, pickupR: 18 };

export const dist = (a: Vec, b: Vec) => Math.hypot(a.x - b.x, a.y - b.y);
export function nearest<T extends Vec>(from: Vec, list: T[]): T | undefined {
  let best: T | undefined, bd = Infinity;
  for (const o of list) { const d = dist(from, o); if (d < bd) { bd = d; best = o; } }
  return best;
}

export function createGame(unitsPerSide = 10, duration = 60, supplies = false): GameState {
  const map = { w: 1000, h: 600 };
  const bases = { blue: { x: 90, y: 300, r: 70 }, red: { x: 910, y: 300, r: 70 } };
  const units: Unit[] = [];
  for (const team of ['blue', 'red'] as Team[]) {
    const b = bases[team], dir = team === 'blue' ? 1 : -1;
    for (let i = 0; i < unitsPerSide; i++) {
      const col = Math.floor(i / 5), row = i % 5;
      units.push({
        id: `${team}-${i}`, team,
        x: b.x + dir * (30 + col * 30), y: b.y + (row - 2) * 45,
        hp: STATS.hp, maxHp: STATS.hp, speed: STATS.speed, range: STATS.range, damage: STATS.damage,
        alive: true, order: 'hold', carryingFlag: false,
      });
    }
  }
  const flag = (team: Team) => ({ team, x: bases[team].x, y: bases[team].y, home: { x: bases[team].x, y: bases[team].y }, carriedBy: null });
  return {
    tick: 0, time: 0, duration, map, bases,
    flags: { blue: flag('blue'), red: flag('red') },
    units, score: { blue: 0, red: 0 }, winner: null,
    ...(supplies ? { supplies: [], nextSupplyAt: SUPPLY.first } : {}),
  };
}

function moveTarget(s: GameState, u: Unit): Vec | null {
  const enemies = s.units.filter((o) => o.alive && o.team !== u.team);
  const allies = s.units.filter((o) => o.alive && o.team === u.team && o.id !== u.id);
  const own = s.flags[u.team], their = s.flags[enemyOf(u.team)], base = s.bases[u.team];
  const engage = (e: Unit | undefined) => (e && dist(u, e) > u.range * 0.85 ? e : null);
  switch (u.order) {
    case 'attack_nearest_enemy': return engage(nearest(u, enemies));
    case 'push_enemy_flag': return u.carryingFlag ? base : their;
    case 'defend_own_flag': {
      if (own.carriedBy) return s.units.find((x) => x.id === own.carriedBy) ?? own;
      const e = nearest(own, enemies);
      if (e && dist(e, own) < 180) return engage(e);
      return dist(u, own) > 35 ? own : null;
    }
    case 'support_nearest_ally': { const a = nearest(u, allies); return a && dist(u, a) > 40 ? a : null; }
    case 'retreat_to_base': return dist(u, base) > 20 ? base : null;
    case 'hold': return null;
    case 'collect_supply': return s.supplies?.length ? nearest(u, s.supplies) ?? null : null;
  }
}

export interface StepEvents { captures: Team[] }

export function step(s: GameState, dt = TICK_DT): StepEvents {
  const ev: StepEvents = { captures: [] };
  if (s.winner) return ev;
  const alive = s.units.filter((u) => u.alive);

  // movement
  // compute all targets first (as fixed points) so neither team moves on fresher info
  const targets = alive.map((u) => { const t = moveTarget(s, u); return t ? { x: t.x, y: t.y } : null; });
  for (let i = 0; i < alive.length; i++) {
    const u = alive[i], t = targets[i];
    if (!t) continue;
    const d = dist(u, t); if (d < 1) continue;
    const sp = u.speed * (u.carryingFlag ? STATS.carrySpeedMul : 1) * dt;
    const k = Math.min(1, sp / d);
    u.x += (t.x - u.x) * k; u.y += (t.y - u.y) * k;
  }
  // separation
  for (let i = 0; i < alive.length; i++) for (let j = i + 1; j < alive.length; j++) {
    const a = alive[i], b = alive[j], d = dist(a, b);
    if (d < 14 && d > 0.01) { const p = (14 - d) / 2 / d; const dx = (a.x - b.x) * p, dy = (a.y - b.y) * p; a.x += dx; a.y += dy; b.x -= dx; b.y -= dy; }
  }
  for (const u of alive) { u.x = Math.max(5, Math.min(s.map.w - 5, u.x)); u.y = Math.max(5, Math.min(s.map.h - 5, u.y)); }

  // combat (simultaneous)
  const dmg = new Map<string, number>();
  for (const u of alive) {
    const e = nearest(u, alive.filter((o) => o.team !== u.team));
    if (e && dist(u, e) <= u.range) dmg.set(e.id, (dmg.get(e.id) ?? 0) + u.damage * dt);
  }
  for (const u of alive) {
    u.hp -= dmg.get(u.id) ?? 0;
    if (u.hp <= 0) {
      u.hp = 0; u.alive = false; s.score[enemyOf(u.team)] += 1;
      if (u.carryingFlag) { u.carryingFlag = false; s.flags[enemyOf(u.team)].carriedBy = null; }
    } else if (dist(u, s.bases[u.team]) <= s.bases[u.team].r) {
      u.hp = Math.min(u.maxHp, u.hp + STATS.regen * dt);
    }
  }

  // flags
  for (const team of ['blue', 'red'] as Team[]) {
    const f = s.flags[team];
    if (f.carriedBy) {
      const c = s.units.find((x) => x.id === f.carriedBy)!;
      f.x = c.x; f.y = c.y;
      if (dist(c, s.bases[c.team]) <= s.bases[c.team].r) { s.winner = c.team; s.score[c.team] += 5; ev.captures.push(c.team); }
      continue;
    }
    const grabber = s.units.find((u) => u.alive && u.team !== team && u.order === 'push_enemy_flag' && dist(u, f) <= STATS.pickupR);
    if (grabber) { f.carriedBy = grabber.id; grabber.carryingFlag = true; continue; }
    if (dist(f, f.home) > 1 && s.units.some((u) => u.alive && u.team === team && dist(u, f) <= STATS.pickupR)) { f.x = f.home.x; f.y = f.home.y; }
  }

  // supply crates: spawn at fixed spots (deterministic), nearest collecting unit picks up
  if (s.supplies) {
    if (s.time >= (s.nextSupplyAt ?? 0)) {
      const free = SUPPLY.spots.find((p) => !s.supplies!.some((c) => dist(c, p) < 1));
      if (free) s.supplies.push({ ...free });
      s.nextSupplyAt = (s.nextSupplyAt ?? 0) + SUPPLY.every;
    }
    s.supplies = s.supplies.filter((c) => {
      const u = nearest(c, s.units.filter((x) => x.alive && x.order === 'collect_supply' && dist(x, c) <= STATS.pickupR));
      if (!u) return true;
      s.score[u.team] += 1; u.hp = Math.min(u.maxHp, u.hp + SUPPLY.heal);
      return false;
    });
  }

  s.tick++; s.time = +(s.tick * dt).toFixed(3);
  if (!s.winner) {
    const bAlive = s.units.some((u) => u.alive && u.team === 'blue'), rAlive = s.units.some((u) => u.alive && u.team === 'red');
    if (!bAlive || !rAlive) s.winner = bAlive ? 'blue' : rAlive ? 'red' : 'draw';
    else if (s.time >= s.duration) s.winner = s.score.blue > s.score.red ? 'blue' : s.score.red > s.score.blue ? 'red' : 'draw';
  }
  return ev;
}
