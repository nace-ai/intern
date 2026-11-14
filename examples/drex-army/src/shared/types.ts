export const ACTIONS = [
  'attack_nearest_enemy',
  'push_enemy_flag',
  'defend_own_flag',
  'support_nearest_ally',
  'retreat_to_base',
  'hold',
  'collect_supply',
] as const;
export type Action = (typeof ACTIONS)[number];

export const ACTION_DESCRIPTIONS: Record<Action, string> = {
  attack_nearest_enemy: 'Move toward and fight the closest living enemy unit',
  push_enemy_flag: "Go for the enemy flag; if carrying it, run it back to own base to score",
  defend_own_flag: 'Return to and guard own flag; chase down an enemy flag carrier',
  support_nearest_ally: 'Move to the closest ally and fight alongside it',
  retreat_to_base: 'Fall back to own base to regenerate health',
  hold: 'Stay in place and shoot enemies in range',
  collect_supply: 'Go grab the nearest supply crate (+1 point, +30 hp)',
};

export type Team = 'blue' | 'red';

export interface Vec { x: number; y: number }

export interface Unit {
  id: string;            // e.g. "blue-3"
  team: Team;
  x: number; y: number;
  hp: number; maxHp: number;
  speed: number;         // px per second
  range: number;         // px
  damage: number;        // hp per second while an enemy is in range
  alive: boolean;
  order: Action;         // current order (engine executes it every tick)
  carryingFlag: boolean; // true if carrying the ENEMY flag
}

export interface Flag {
  team: Team;            // owner
  x: number; y: number;  // current position
  home: Vec;             // spawn position (inside owner's base)
  carriedBy: string | null; // unit id of the enemy carrying it
}

export interface GameState {
  tick: number;
  time: number;          // seconds elapsed (sim time)
  duration: number;      // match length in seconds (60)
  map: { w: number; h: number };
  bases: Record<Team, Vec & { r: number }>;
  flags: Record<Team, Flag>;
  units: Unit[];
  score: Record<Team, number>;
  winner: Team | 'draw' | null;
  supplies?: Vec[];       // active supply crates (only when the supplies mode is on)
  nextSupplyAt?: number;  // sim time of next crate spawn
}

// Without supplies mode the action space is exactly the benchmarked 6 actions.
export const BASE_ACTIONS: readonly Action[] = ACTIONS.filter((a) => a !== 'collect_supply');
export const actionsFor = (s: GameState): readonly Action[] => (s.supplies ? ACTIONS : BASE_ACTIONS);

export const enemyOf = (t: Team): Team => (t === 'blue' ? 'red' : 'blue');
