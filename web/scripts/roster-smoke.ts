// Smoke test for roster generation, seating and DiceBear faces.
// Run from web/: pnpm dlx tsx scripts/roster-smoke.ts

import { faceUrl } from "../src/orchestra/faces";
import type { Section } from "../src/orchestra/musicians";
import { INSTRUMENTS, MUSICIAN_IDS } from "../src/orchestra/musicians";
import { classicRoster, generateRoster } from "../src/orchestra/roster";

const SEED = "demo";
let failures = 0;

function check(ok: boolean, label: string): void {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) failures++;
}

function instrumentCounts(members: { instrument: string }[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const m of members) counts.set(m.instrument, (counts.get(m.instrument) ?? 0) + 1);
  return counts;
}

function sectionExtents(members: { section: Section; seat: { x: number; y: number } }[]) {
  const ext = new Map<Section, { x0: number; x1: number; y0: number; y1: number }>();
  for (const m of members) {
    const e = ext.get(m.section) ?? { x0: 1, x1: 0, y0: 1, y1: 0 };
    e.x0 = Math.min(e.x0, m.seat.x);
    e.x1 = Math.max(e.x1, m.seat.x);
    e.y0 = Math.min(e.y0, m.seat.y);
    e.y1 = Math.max(e.y1, m.seat.y);
    ext.set(m.section, e);
  }
  return ext;
}

const percussion = new Set(["kick", "snare", "hats", "bells"]);

for (const count of [4, 11, 16, 40]) {
  console.log(`\n=== count ${count}, seed "${SEED}" ===`);
  const roster = generateRoster(count, SEED);
  check(roster.length === count, `roster has ${count} members`);

  const counts = instrumentCounts(roster);
  const present = MUSICIAN_IDS.filter((id) => counts.has(id))
    .map((id) => `${id}=${counts.get(id)}`)
    .join(" ");
  console.log(`instruments: ${present}`);

  const ext = sectionExtents(roster);
  for (const [section, e] of ext) {
    const f = (n: number) => n.toFixed(2);
    console.log(`  ${section.padEnd(10)} x [${f(e.x0)} .. ${f(e.x1)}]  y [${f(e.y0)} .. ${f(e.y1)}]`);
  }
  for (const e of ext.values()) {
    check(
      e.x0 >= 0.03 && e.x1 <= 0.97 && e.y0 >= 0.03 && e.y1 <= 0.97,
      `seats inside 0.03..0.97 (${e.x0.toFixed(2)}..${e.x1.toFixed(2)}, ${e.y0.toFixed(2)}..${e.y1.toFixed(2)})`,
    );
  }

  check((counts.get("violin") ?? 0) >= 1, "guarantee: at least one violin");
  check((counts.get("bass") ?? 0) >= 1, "guarantee: at least one double bass");
  check((counts.get("flute") ?? 0) >= 1, "guarantee: at least one flute");
  check(
    [...counts.entries()].some(([id, n]) => n >= 1 && percussion.has(id)),
    "guarantee: at least one percussion (bass drum or snare)",
  );

  const again = generateRoster(count, SEED);
  const same = roster.every((m, i) => m.id === again[i].id && m.name === again[i].name);
  check(same, `same seed gives the same ids for ${count}`);
}

console.log("\n=== classicRoster() ===");
const classic = classicRoster();
check(classic.length === 11, "classic roster has 11 members");
check(
  classic.every((m, i) => m.instrument === MUSICIAN_IDS[i] && m.id === MUSICIAN_IDS[i]),
  "classic roster: one of each instrument, id equals the instrument id, in MUSICIAN_IDS order",
);
console.log(
  `classic seats: ${classic.map((m) => `${m.id}@(${m.seat.x.toFixed(2)},${m.seat.y.toFixed(2)})`).join(" ")}`,
);

console.log("\n=== faces ===");
const url = faceUrl(`${SEED}:violin-1`);
check(url.startsWith("data:image/svg+xml,"), "faceUrl returns a data:image/svg+xml URL");
check(url.includes("svg"), "faceUrl output contains svg markup");
check(url === faceUrl(`${SEED}:violin-1`), "faceUrl is memoized/deterministic");
check(faceUrl(`${SEED}:violin-2`) !== url, "different seeds give different faces");
const unique = new Set(classic.map((m) => faceUrl(m.faceSeed)));
check(unique.size === 11, "all 11 classic faces are distinct");

function minPairwise(ms: { seat: { x: number; y: number } }[]) {
  let d = Infinity;
  for (let i = 0; i < ms.length; i++)
    for (let j = i + 1; j < ms.length; j++)
      d = Math.min(d, Math.hypot(ms[i].seat.x - ms[j].seat.x, ms[i].seat.y - ms[j].seat.y));
  return d;
}
console.log(`min pairwise seat distance (16): ${minPairwise(generateRoster(16, SEED)).toFixed(4)}`);
console.log(`min pairwise seat distance (40): ${minPairwise(generateRoster(40, SEED)).toFixed(4)}`);

console.log(`faceUrl('x').slice(0, 40) = ${faceUrl("x").slice(0, 40)}`);
console.log(`\n${INSTRUMENTS.bass.name} display check: "${INSTRUMENTS.bass.name}"`);
console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
