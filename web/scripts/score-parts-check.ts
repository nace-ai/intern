// Run: cd web && pnpm dlx vite-node scripts/score-parts-check.ts
//
// Check for scoreParts.fillMissingParts against both a JSON score and a MIDI file.
// For each new instrument prints the note count and lowest-highest note name, and
// asserts: all new notes in range, all timpani notes dur 1, old parts untouched,
// input not mutated. Prints 'OK' at the end.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { midiToScore } from "../src/orchestra/midi.ts";
import { noteToMidi } from "../src/orchestra/musicians.ts";
import { midiToName } from "../src/orchestra/score.ts";
import { fillMissingParts, NEW_PARTS } from "../src/orchestra/scoreParts.ts";
import type { ScoreData } from "../src/orchestra/scoreTypes.ts";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function fail(message: string): never {
  throw new Error(`FAIL: ${message}`);
}

function check(score: ScoreData, label: string): void {
  const before = JSON.stringify(score);
  const out = fillMissingParts(score);
  const outBefore = JSON.stringify(score);
  if (before !== outBefore) fail(`${label}: input was mutated`);

  const counts = new Map<string, number[]>();
  out.bars.forEach((bar, i) => {
    const parts = bar.parts as Record<string, { note: string }[] | undefined>;
    for (const [id, spec] of Object.entries(NEW_PARTS)) {
      for (const n of parts[id] ?? []) {
        const m = noteToMidi(n.note);
        if (m < spec.low || m > spec.high) fail(`${label}: ${id} note ${n.note} outside [${spec.low}, ${spec.high}]`);
        if (id === "timpani" && n.dur !== 1) fail(`${label}: timpani note dur ${n.dur}, expected 1`);
        const midis = counts.get(id) ?? [];
        midis.push(m);
        counts.set(id, midis);
      }
    }
    const inParts = score.bars[i].parts as Record<string, unknown>;
    for (const [id, notes] of Object.entries(inParts)) {
      const got = (out.bars[i].parts as Record<string, unknown>)[id];
      if (JSON.stringify(got) !== JSON.stringify(notes)) fail(`${label}: bar ${bar.index} old part ${id} changed`);
    }
  });

  console.log(`${label} (${out.bars.length} bars)`);
  for (const id of Object.keys(NEW_PARTS)) {
    const midis = counts.get(id) ?? [];
    console.log(
      midis.length > 0
        ? `  ${id}: ${midis.length} notes, ${midiToName(Math.min(...midis))}-${midiToName(Math.max(...midis))}`
        : `  ${id}: 0 notes`,
    );
  }
}

const laDonna = JSON.parse(readFileSync(join(webRoot, "public/scores/la_donna.json"), "utf8")) as ScoreData;
check(laDonna, "la_donna.json");

const midiBytes = readFileSync(join(webRoot, "public/scores/fur_elise.mid"));
const score = await midiToScore(new File([new Uint8Array(midiBytes)], "fur_elise.mid"));
check(score, "fur_elise.mid");

console.log("OK");
