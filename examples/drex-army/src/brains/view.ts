// Shared team-perspective view. BOTH Drex and LLM brains get exactly this (fairness).
import { enemyOf, type GameState, type Team, type Vec } from '../shared/types.js';

const d = (a: Vec, b: Vec) => Math.round(Math.hypot(a.x - b.x, a.y - b.y));
const r = (n: number) => Math.round(n);

export const RULES =
  'Capture the flag, 2D map. Win instantly by carrying the enemy flag into your own base, or by eliminating all enemies. ' +
  'Otherwise at time 0 the team with more kills wins. Units: 100 hp, speed 60 px/s (flag carrier 48), range 70 px, 20 dmg/s to nearest enemy in range (automatic). ' +
  'Units inside their own base regenerate 8 hp/s. A unit with order push_enemy_flag picks up the enemy flag by touching it. ' +
  'If a carrier dies the flag drops; touching your own dropped flag returns it home. Orders persist until replaced.';

export const SUPPLY_RULES =
  ' Supply crates appear on the midline every 8 s. A unit with order collect_supply grabs the nearest crate by touching it: +1 point for its team and +30 hp. Points from crates count like kills.';
export const rulesFor = (s: GameState) => RULES + (s.supplies ? SUPPLY_RULES : '');

export function teamView(s: GameState, team: Team) {
  const enemy = enemyOf(team);
  const mine = s.units.filter((u) => u.alive && u.team === team);
  const theirs = s.units.filter((u) => u.alive && u.team === enemy);
  const ownFlag = s.flags[team], enemyFlag = s.flags[enemy];
  const nearestEnemy = (p: Vec) => theirs.reduce((m, e) => Math.min(m, d(p, e)), Infinity);
  return {
    you: team,
    time_left_s: +(s.duration - s.time).toFixed(1),
    kills: { yours: s.score[team], enemy: s.score[enemy] },
    map: s.map,
    your_base: s.bases[team],
    enemy_base: s.bases[enemy],
    your_flag: { x: r(ownFlag.x), y: r(ownFlag.y), at_home: !ownFlag.carriedBy && d(ownFlag, ownFlag.home) < 2, carried_by: ownFlag.carriedBy },
    enemy_flag: { x: r(enemyFlag.x), y: r(enemyFlag.y), at_home: !enemyFlag.carriedBy && d(enemyFlag, enemyFlag.home) < 2, carried_by: enemyFlag.carriedBy },
    your_units: mine.map((u) => ({
      id: u.id, x: r(u.x), y: r(u.y), hp: r(u.hp), order: u.order, carrying_flag: u.carryingFlag,
      nearest_enemy_dist: theirs.length ? nearestEnemy(u) : null,
      dist_to_enemy_flag: d(u, enemyFlag), dist_to_own_base: d(u, s.bases[team]),
    })),
    ...(s.supplies ? { supply_crates: s.supplies.map((c) => ({ x: r(c.x), y: r(c.y) })) } : {}),
    enemy_units: theirs.map((u) => ({ id: u.id, x: r(u.x), y: r(u.y), hp: r(u.hp), carrying_flag: u.carryingFlag })),
  };
}
