// Smoke test for the band core against the real Drex, through the dev server's proxy.
//
// Start the dev server first (adds the API key to /nace/*):
//   pnpm dev --port 5180 --strictPort
// Then:
//   pnpm dlx tsx scripts/band-smoke.ts --mood calm --beats 8
//
// Prints one line per beat: index, bar:beat, latency, each musician's play.

import { Band } from "../src/orchestra/band.ts";
import { decide } from "../src/orchestra/drex.ts";
import type { MoodName } from "../src/orchestra/musicians.ts";
import { MOOD_NAMES, MUSICIAN_IDS, MUSICIANS } from "../src/orchestra/musicians.ts";

const args = process.argv.slice(2);
function arg(name: string, fallback: string): string {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : fallback;
}

const mood = arg("mood", "calm") as MoodName;
if (!MOOD_NAMES.includes(mood)) {
  console.error(`bad mood: ${mood}. Use one of: ${MOOD_NAMES.join(", ")}`);
  process.exit(1);
}
const beats = Number(arg("beats", "8"));
const base = process.env.DREX_BASE ?? "http://localhost:5180/nace";

const band = new Band(mood);
for (let i = 0; i < beats; i++) {
  const beat = await band.decideBeat((state, questions) => decide(state, questions, { base }));
  const parts = MUSICIAN_IDS.map((id) => `${id}=${beat.notes[id].play}`);
  const flag = beat.timedOut ? " TIMEOUT" : "";
  console.log(
    `beat ${String(beat.index).padStart(2)}  bar ${beat.bar}:${beat.beatInBar}  ${String(Math.round(beat.latencyMs)).padStart(3)}ms${flag}  ${parts.join(" ")}`,
  );
}

// count plays per musician: melodic = non-rest, non-hold; drums = anything but ".."
const counts = MUSICIANS.map((m) => {
  const played = band.history[m.id].filter((p) => (m.kind === "drums" ? p !== ".." : p !== "rest" && p !== "hold"));
  return `${m.id}: ${played.length}`;
});
console.log(`plays in ${beats} beats — ${counts.join(", ")}`);
