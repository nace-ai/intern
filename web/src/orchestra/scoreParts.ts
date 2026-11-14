// Parts for the new orchestral instruments, copied from an old instrument's part
// and octave-shifted into the new instrument's register. Plain string keys so this
// compiles before the new ids exist in musicians.ts.

import { noteToMidi } from "./musicians";
import { midiToName } from "./score";
import type { ScoreBar, ScoreData, ScoreNote } from "./scoreTypes";

export const NEW_PARTS: Record<string, { source: string; low: number; high: number }> = {
  viola: { source: "pad_high", low: 48, high: 76 },
  harp: { source: "synth", low: 36, high: 91 },
  oboe: { source: "flute", low: 58, high: 89 },
  clarinet: { source: "violin", low: 50, high: 89 },
  bassoon: { source: "bass", low: 34, high: 70 },
  horn: { source: "pad_low", low: 41, high: 77 },
  trumpet: { source: "violin", low: 52, high: 82 },
  trombone: { source: "pad_low", low: 40, high: 72 },
  tuba: { source: "bass", low: 28, high: 58 },
  timpani: { source: "bass", low: 41, high: 53 },
  piano: { source: "synth", low: 33, high: 96 },
};

/** Fold a MIDI number into [low, high] by single-octave shifts. */
function foldInto(midi: number, low: number, high: number): number {
  let m = midi;
  while (m < low) m += 12;
  while (m > high) m -= 12;
  return m;
}

/** The k in -4..4 whose k*12 shift puts the most notes in range; smallest |k| on ties. */
function pickShift(midis: number[], low: number, high: number): number {
  let bestK = 0;
  let bestCount = -1;
  for (let k = -4; k <= 4; k++) {
    const count = midis.filter((m) => m + 12 * k >= low && m + 12 * k <= high).length;
    if (count > bestCount || (count === bestCount && Math.abs(k) < Math.abs(bestK))) {
      bestCount = count;
      bestK = k;
    }
  }
  return bestK;
}

/**
 * Give every NEW_PARTS instrument a part for every bar, copying its source
 * instrument's part octave-shifted into its register. Returns a NEW ScoreData;
 * the input is never mutated. Existing non-empty parts are kept untouched.
 */
export function fillMissingParts(score: ScoreData): ScoreData {
  const bars = score.bars.map((bar) => {
    const existing = bar.parts as Record<string, ScoreNote[] | undefined>;
    const parts: Record<string, ScoreNote[] | undefined> = { ...existing };
    for (const [id, spec] of Object.entries(NEW_PARTS)) {
      if ((parts[id] ?? []).length > 0) continue;
      const source = existing[spec.source] ?? [];
      if (source.length === 0) continue;
      let notes: ScoreNote[];
      if (id === "timpani") {
        const first = [...source].sort((a, b) => a.offset - b.offset)[0];
        notes = [
          { note: midiToName(foldInto(noteToMidi(first.note), spec.low, spec.high)), offset: first.offset, dur: 1 },
        ];
      } else {
        const k = pickShift(
          source.map((n) => noteToMidi(n.note)),
          spec.low,
          spec.high,
        );
        notes = source.map((n) => ({
          note: midiToName(foldInto(noteToMidi(n.note) + 12 * k, spec.low, spec.high)),
          offset: n.offset,
          dur: n.dur,
        }));
      }
      parts[id] = notes;
    }
    return { ...bar, parts: parts as unknown as ScoreBar["parts"] };
  });
  return { ...score, bars };
}
