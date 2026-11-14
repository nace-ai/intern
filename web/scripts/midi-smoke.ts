// Run: pnpm dlx vite-node scripts/midi-smoke.ts <file.mid> [--out <path.json>]
//
// Smoke test for the MIDI -> score conversion against a real MIDI file.
//
//   node scripts/midi-smoke.ts <file.mid> [--out <path.json>]
//
// Validates the ScoreData (registers, offsets, chords, sections) and prints a summary.

import { readFile, writeFile } from "node:fs/promises";
import { basename } from "node:path";
import { midiBytesToScore } from "../src/orchestra/midi.ts";
import { MUSICIANS, noteToMidi } from "../src/orchestra/musicians.ts";
import type { ScoreData } from "../src/orchestra/scoreTypes.ts";

function fail(message: string): never {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

const args = process.argv.slice(2);
const file = args[0];
const outIdx = args.indexOf("--out");
const out = outIdx >= 0 ? args[outIdx + 1] : null;
if (!file) {
  console.error("usage: node scripts/midi-smoke.ts <file.mid> [--out <path.json>]");
  process.exit(1);
}

const bytes = await readFile(file);
let score: ScoreData;
try {
  score = midiBytesToScore(bytes, basename(file));
} catch (err) {
  fail(err instanceof Error ? err.message : String(err));
}

const eighthsPerBar = score.beats_per_bar;
const CHORD_RE = /^[A-G]#? .+ \(([A-G]#? ?)+\)$/;
const DRUMS = new Set(["kick", "snare", "hats"]);
const registers = new Map(MUSICIANS.map((m) => [m.id, { low: m.low, high: m.high }] as const));

score.bars.forEach((bar, i) => {
  if (bar.index !== i) fail(`bars[${i}].index is ${bar.index}, expected ${i}`);
  if (!CHORD_RE.test(bar.chord)) fail(`bar ${i}: chord does not look right: "${bar.chord}"`);
  for (const [id, notes] of Object.entries(bar.parts)) {
    if (DRUMS.has(id)) fail(`bar ${i}: drums in parts (${id})`);
    const reg = registers.get(id);
    if (!reg) fail(`bar ${i}: unknown musician ${id}`);
    for (const n of notes) {
      const m = noteToMidi(n.note);
      if (m < reg.low || m > reg.high) fail(`bar ${i}: ${id} plays ${n.note}, outside [${reg.low}, ${reg.high}]`);
      if (n.offset < 0) fail(`bar ${i}: ${id} has negative offset ${n.offset}`);
      if (n.dur <= 0) fail(`bar ${i}: ${id} has non-positive dur ${n.dur}`);
      if (n.offset + n.dur > eighthsPerBar)
        fail(`bar ${i}: ${id} runs past the bar (${n.offset}+${n.dur} > ${eighthsPerBar})`);
      if (n.offset % 0.5 !== 0) fail(`bar ${i}: ${id} offset ${n.offset} is not a multiple of 0.5`);
      if (n.dur % 0.5 !== 0) fail(`bar ${i}: ${id} dur ${n.dur} is not a multiple of 0.5`);
    }
  }
});

const sections = score.sections;
if (sections.length === 0 || sections[0].start_bar !== 0) fail("sections must start at bar 0");
for (let i = 1; i < sections.length; i++) {
  if (sections[i].start_bar !== sections[i - 1].end_bar + 1) fail(`sections have a gap before ${sections[i].name}`);
}
if (sections[sections.length - 1].end_bar !== score.bars.length - 1) fail("sections do not reach the last bar");

// Summary, like export_score.py's summarize.
const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const nameOf = (m: number) => `${NAMES[m % 12]}${Math.floor(m / 12) - 1}`;
console.log(
  `${score.title} — ${score.bars.length} bars, beats_per_bar ${score.beats_per_bar}, eighth_bpm ${score.eighth_bpm}`,
);
for (const s of sections) console.log(`  ${s.name}: bars ${s.start_bar}-${s.end_bar}`);
for (let i = 0; i < Math.min(8, score.bars.length); i++) console.log(`  bar ${i}: ${score.bars[i].chord}`);
for (const m of MUSICIANS) {
  if (m.kind === "drums") continue;
  const midis = score.bars.flatMap((b) => (b.parts[m.id] ?? []).map((n) => noteToMidi(n.note)));
  console.log(
    midis.length > 0
      ? `  ${m.id}: ${midis.length} notes, ${nameOf(Math.min(...midis))}-${nameOf(Math.max(...midis))}`
      : `  ${m.id}: 0 notes`,
  );
}
console.log("ok");
if (out) await writeFile(out, JSON.stringify(score, null, 2));
