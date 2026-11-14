import type { Clock, Rng } from "../domain";

export const systemClock: Clock = { now: () => Date.now() };

/** Manually advanced clock for tests and deterministic runs. */
export class FakeClock implements Clock {
  constructor(private t = 1_000_000) {}
  now(): number { return this.t; }
  advance(ms: number): void { this.t += ms; }
  set(ms: number): void { this.t = ms; }
}

/** mulberry32: small seeded PRNG. Same seed => same sequence. */
export function createRng(seed: number): Rng {
  let a = seed >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    int: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)),
    pick: <T>(items: readonly T[]): T => items[Math.floor(next() * items.length)] as T,
  };
}

export function pad(n: number, width: number): string { return String(n).padStart(width, "0"); }
