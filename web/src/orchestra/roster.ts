// The orchestra roster: who plays, what instrument, where they sit, and their face.
// Deterministic for a seed: the same (count, seed) always gives the same orchestra.

import { INSTRUMENTS, type InstrumentId, MUSICIAN_IDS, type Section } from "./musicians";

export type Member = {
  id: string;
  instrument: InstrumentId;
  name: string;
  section: Section;
  trait: string;
  seat: { x: number; y: number };
  faceSeed: string;
};

export const TRAITS = ["steady", "shy", "bold", "dreamy", "rebellious", "show-off", "lazy", "precise"] as const;

/** Small seeded PRNG (mulberry32). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Hash a string to a 32-bit integer (FNV-1a). */
export function hashSeed(str: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Draw an orchestra of `count` (4..40) members for `seed`.
 * Guarantees at least one violin, one double bass, one flute and one
 * percussion (bass drum or snare); the rest are weighted random draws.
 */
export function generateRoster(count: number, seed: string): Member[] {
  const n = Math.min(40, Math.max(4, Math.floor(count)));
  const rand = mulberry32(hashSeed(seed));

  const guarantees: InstrumentId[] = ["violin", "bass", "flute", rand() < 0.5 ? "kick" : "snare"];
  const pool = Object.keys(INSTRUMENTS) as InstrumentId[];
  const totalWeight = pool.reduce((s, id) => s + INSTRUMENTS[id].weight, 0);

  const instruments: InstrumentId[] = [...guarantees];
  while (instruments.length < n) {
    let r = rand() * totalWeight;
    for (const id of pool) {
      r -= INSTRUMENTS[id].weight;
      if (r <= 0) {
        instruments.push(id);
        break;
      }
    }
  }

  const counters = new Map<InstrumentId, number>();
  const members: Member[] = instruments.map((instrument) => {
    const num = (counters.get(instrument) ?? 0) + 1;
    counters.set(instrument, num);
    const id = `${instrument}-${num}`;
    return {
      id,
      instrument,
      name: `${INSTRUMENTS[instrument].name} ${num}`,
      section: INSTRUMENTS[instrument].section,
      trait: TRAITS[Math.floor(rand() * TRAITS.length)],
      seat: { x: 0.5, y: 0.5 },
      faceSeed: `${seed}:${id}`,
    };
  });
  return seatRoster(members);
}

/** Exactly one of each of the 11 instruments, in MUSICIAN_IDS order (the old band). */
export function classicRoster(): Member[] {
  const members: Member[] = MUSICIAN_IDS.map((instrument) => ({
    id: instrument,
    instrument,
    name: INSTRUMENTS[instrument].name,
    section: INSTRUMENTS[instrument].section,
    trait: "steady",
    seat: { x: 0.5, y: 0.5 },
    faceSeed: `classic:${instrument}`,
  }));
  return seatRoster(members);
}

/** Section arcs: instruments, startDeg, endDeg, base radius (rows grow by 0.13). */
const GROUPS: [string[], number, number, number][] = [
  [["violin"], 180, 122, 0.3],
  [["pad_low"], 58, 0, 0.3],
  [["bass"], 40, 0, 0.52],
  [["flute", "kazoo", "oboe", "clarinet", "bassoon"], 112, 68, 0.42],
  [["pad_high", "synth", "piano"], 170, 132, 0.62],
  [["kick", "snare", "hats", "bells", "timpani"], 122, 58, 0.72],
  [["viola"], 118, 62, 0.3],
  [["harp"], 178, 152, 0.5],
  [["horn", "trumpet", "trombone", "tuba"], 120, 60, 0.58],
];

/** Seat members in rows per section arc, then stretch all seats to fill the stage. */
export function seatRoster(members: Member[]): Member[] {
  const seated: Member[] = members.map((m) => ({ ...m }));

  for (const [instruments, startDeg, endDeg, r0] of GROUPS) {
    const group = seated.filter((m) => instruments.includes(m.instrument));
    let rest = 0;
    for (let k = 0; rest < group.length; k++) {
      const r = r0 + 0.13 * k;
      const span = ((startDeg - endDeg) * Math.PI) / 180;
      const capacity = Math.max(1, Math.floor((r * span) / 0.085));
      const row = group.slice(rest, rest + capacity);
      row.forEach((m, i) => {
        const t = row.length === 1 ? 0.5 : i / (row.length - 1);
        const a = ((startDeg + (endDeg - startDeg) * t) * Math.PI) / 180;
        m.seat = { x: 0.5 + r * Math.cos(a), y: 0.97 - r * Math.sin(a) };
      });
      rest += row.length;
    }
  }

  if (seated.length === 1) {
    seated[0].seat = { x: 0.5, y: 0.5 };
    return seated;
  }

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const m of seated) {
    minX = Math.min(minX, m.seat.x);
    maxX = Math.max(maxX, m.seat.x);
    minY = Math.min(minY, m.seat.y);
    maxY = Math.max(maxY, m.seat.y);
  }
  for (const m of seated) {
    m.seat.x = 0.06 + 0.88 * ((m.seat.x - minX) / (maxX - minX || 1));
    m.seat.y = 0.08 + 0.84 * ((m.seat.y - minY) / (maxY - minY || 1));
  }
  return seated;
}
