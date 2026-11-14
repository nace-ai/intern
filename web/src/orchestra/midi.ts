// Turn an uploaded MIDI file into the score-mode shape (scoreTypes.ts), entirely in the
// browser: the highest line becomes the melody (violin, flute, kazoo), the lowest line
// the bass, held chord tones go to the pads, chord tones are arpeggiated to the synth and
// each bar's chord is named with tonal. Drum tracks are ignored: the drums improvise.

import { Midi } from "@tonejs/midi";
import { Chord, Note } from "tonal";
import type { MusicianId } from "./musicians";
import { MUSICIANS } from "./musicians.ts";
import type { ScoreBar, ScoreData, ScoreNote, ScoreSection } from "./scoreTypes";

const SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

/** register of each melodic musician, as MIDI note numbers */
const REG = new Map<MusicianId, { low: number; high: number }>();
for (const m of MUSICIANS) {
  if (m.kind === "melodic") REG.set(m.id, { low: m.low, high: m.high });
}
function reg(id: MusicianId): { low: number; high: number } {
  const r = REG.get(id);
  if (!r) throw new Error(`no register for ${id}`);
  return r;
}

export async function midiToScore(file: File): Promise<ScoreData> {
  const bytes = await file.arrayBuffer();
  return midiBytesToScore(bytes, file.name);
}

export function midiBytesToScore(bytes: ArrayBuffer | Uint8Array, fileName: string): ScoreData {
  let midi: Midi;
  try {
    midi = new Midi(bytes);
  } catch (err) {
    throw new Error(`Could not read MIDI file: ${err instanceof Error ? err.message : String(err)}`);
  }

  // 1. Meter and tempo: the first time signature and tempo mark only.
  const ppq = midi.header.ppq;
  const firstTs = midi.header.timeSignatures[0];
  const [num, den] = firstTs ? firstTs.timeSignature : [4, 4];
  const bpm = midi.header.tempos[0]?.bpm ?? 120;
  const eighthsPerBar = Math.max(1, Math.round((num * 8) / den));
  const eighthBpm = Math.min(320, Math.max(80, Math.round(bpm * 2)));
  const eighthTicks = ppq / 2;
  const ticksPerBar = (eighthsPerBar * ppq) / 2;

  // 2. Every melodic note, in MIDI ticks; drum tracks are ignored.
  const raw: RawNote[] = [];
  for (const track of midi.tracks) {
    if (track.channel === 9 || track.instrument.percussion) continue;
    for (const n of track.notes) raw.push({ midi: n.midi, tick: n.ticks, durTicks: n.durationTicks });
  }
  if (raw.length === 0) throw new Error("No melodic notes in this MIDI file");

  // 3. Bars start at the bar holding the first note.
  let minTick = Infinity;
  let maxEnd = -Infinity;
  for (const n of raw) {
    minTick = Math.min(minTick, n.tick);
    maxEnd = Math.max(maxEnd, n.tick + n.durTicks);
  }
  const firstBar = Math.floor(minTick / ticksPerBar);
  const barCount = Math.ceil(maxEnd / ticksPerBar) - firstBar;

  // 4. Quantize to 16ths; drop notes that fall outside their bar.
  const placed: (BarNote & { bar: number; endTick: number })[] = [];
  for (const n of raw) {
    const absBar = Math.floor(n.tick / ticksPerBar);
    const bar = absBar - firstBar; // relative bar index, 0-based
    if (bar < 0 || bar >= barCount) continue;
    const offset = q((n.tick - absBar * ticksPerBar) / eighthTicks);
    if (offset >= eighthsPerBar) continue;
    const dur = Math.min(Math.max(0.5, q(n.durTicks / eighthTicks)), eighthsPerBar - offset);
    placed.push({ midi: n.midi, bar, offset, dur, endTick: n.tick + n.durTicks });
  }
  if (placed.length === 0) throw new Error("No melodic notes in this MIDI file");

  const inBar: BarNote[][] = Array.from({ length: barCount }, () => []);
  for (const p of placed) inBar[p.bar].push(p);
  for (const list of inBar) list.sort((a, b) => a.offset - b.offset || a.midi - b.midi);

  // MIDI header names are often junk ("control track"), so the title comes from the file name.
  const title =
    fileName
      .replace(/\.[^.]+$/, "")
      .replace(/[_-]+/g, " ")
      .trim() || "Uploaded MIDI";
  // synth: one arpeggio note per beat; a beat is 8/den eighths if that divides the bar.
  const synthBeat = 8 / den;
  const synthStep = eighthsPerBar % synthBeat === 0 ? synthBeat : 1;

  const bars: ScoreBar[] = [];
  let prev: ChordResult = { text: "", pcs: [] };
  for (let i = 0; i < barCount; i++) {
    const barStart = (firstBar + i) * ticksPerBar;
    const barEnd = barStart + ticksPerBar;
    const starting = inBar[i];

    // 6. Chord: weight each pitch class by its sounding time in the bar, counting
    // notes that start here and notes held over from earlier.
    const weights = new Map<number, number>();
    let lowest = Infinity;
    const add = (midi: number, eighths: number) => {
      weights.set(midi % 12, (weights.get(midi % 12) ?? 0) + eighths);
      lowest = Math.min(lowest, midi);
    };
    for (const p of starting) add(p.midi, p.dur);
    for (const p of placed) {
      if (p.bar >= i || p.endTick <= barStart) continue; // started before this bar and still sounding
      const overlap = Math.min(p.endTick, barEnd) - barStart;
      if (overlap > 0) add(p.midi, overlap / eighthTicks);
    }

    const detected = weights.size > 0 ? detectChord(weights, lowest) : null;
    const chord = detected ?? (i > 0 ? prev : fallbackMajor(lowest % 12));

    const parts: Partial<Record<MusicianId, ScoreNote[]>> = {};
    if (starting.length > 0) {
      // 5. Lines from the notes starting in this bar.
      const melody = extremeLine(starting, true);
      for (const id of ["violin", "flute", "kazoo"] as const) {
        const r = reg(id);
        const notes = fitLine(monophonic(melody), r.low, r.high, eighthsPerBar);
        if (notes.length > 0) parts[id] = notes;
      }
      const first = melody[0];
      const b = reg("bells");
      parts.bells = fitLine([{ midi: first.midi, offset: first.offset, dur: 1 }], b.low, b.high, eighthsPerBar);
      const br = reg("bass");
      const bass = fitLine(monophonic(extremeLine(starting, false)), br.low, br.high, eighthsPerBar);
      if (bass.length > 0) parts.bass = bass;
    }
    if (weights.size > 0) {
      // Pads hold the root and the second chord tone all bar; the synth arpeggiates.
      const pl = reg("pad_low");
      const ph = reg("pad_high");
      parts.pad_low = [heldNote(chord.pcs[0], pl.low, pl.high, 0, eighthsPerBar)];
      const secondPc = chord.pcs[1] ?? (chord.pcs[0] + 7) % 12;
      parts.pad_high = [heldNote(secondPc, ph.low, ph.high, 0, eighthsPerBar)];
      const sr = reg("synth");
      const arpeggio: ScoreNote[] = [];
      for (let offset = 0, j = 0; offset < eighthsPerBar; offset += synthStep, j++) {
        arpeggio.push(heldNote(chord.pcs[j % chord.pcs.length], sr.low, sr.high, offset, synthStep));
      }
      parts.synth = arpeggio;
    }

    bars.push({ index: i, chord: chord.text, parts });
    prev = chord;
  }

  // 8. Sections: every 8 bars.
  const sections: ScoreSection[] = [];
  for (let start = 0; start < barCount; start += 8) {
    sections.push({ name: `Part ${start / 8 + 1}`, start_bar: start, end_bar: Math.min(start + 8, barCount) - 1 });
  }

  return {
    title,
    composer: "",
    beats_per_bar: eighthsPerBar,
    eighth_bpm: eighthBpm,
    sections,
    bars,
  };
}

/** A raw note position, in MIDI ticks. */
type RawNote = { midi: number; tick: number; durTicks: number };

/** A note placed in its bar: offset and dur in eighth notes (multiples of 0.5). */
type BarNote = { midi: number; offset: number; dur: number };

type ChordResult = { text: string; pcs: number[] };

/** Round a value in eighths to the nearest 16th. */
function q(x: number): number {
  return Math.round(x * 2) / 2;
}

/** MIDI note number -> "A#4"-style name (60 -> "C4", 70 -> "A#4"). */
function noteName(midi: number): string {
  return `${SHARPS[midi % 12]}${Math.floor(midi / 12) - 1}`;
}

/** A note name as tonal spells it -> sharp name (flats become sharps). */
function sharp(note: string): string {
  return SHARPS[Note.chroma(note)];
}

/** Name the bar's chord from its weighted pitch classes, sharps, root first. */
function detectChord(weights: Map<number, number>, lowest: number): ChordResult | null {
  const maxW = Math.max(...weights.values());
  let pcs = [...weights.entries()]
    .filter(([, w]) => w >= maxW * 0.15)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([pc]) => pc);
  // the lowest sounding pitch class goes first
  const lowPc = lowest % 12;
  if (pcs.includes(lowPc)) pcs = [lowPc, ...pcs.filter((pc) => pc !== lowPc)];

  const attempts: number[][] = [pcs.slice(0, 3), pcs, [...pcs.slice(0, 2), (pcs[0] + 7) % 12]];
  for (const tryPcs of attempts) {
    if (tryPcs.length < 2) continue;
    const found = Chord.detect(tryPcs.map((pc) => SHARPS[pc]));
    if (found.length === 0) continue;
    const plain = found.find((name) => ["major", "minor"].includes(Chord.get(name.split("/")[0]).type));
    const c = Chord.get((plain ?? found[0]).split("/")[0]);
    if (c.tonic && c.notes.length >= 2) {
      const chordPcs = c.notes.map((n) => Note.chroma(n));
      const tonicPc = Note.chroma(c.tonic);
      const rootFirst = [tonicPc, ...chordPcs.filter((pc) => pc !== tonicPc)];
      return {
        text: `${sharp(c.tonic)} ${c.type || "chord"} (${rootFirst.map((pc) => SHARPS[pc]).join(" ")})`,
        pcs: rootFirst,
      };
    }
  }
  return null;
}

/** Chord used when nothing can be detected in the first bar. */
function fallbackMajor(pc: number): ChordResult {
  const pcs = [pc, (pc + 4) % 12, (pc + 7) % 12];
  return { text: `${SHARPS[pc]} major (${pcs.map((p) => SHARPS[p]).join(" ")})`, pcs };
}

/** At each distinct offset, the highest (top) or lowest pitch. */
function extremeLine(notes: BarNote[], top: boolean): BarNote[] {
  const byOffset = new Map<number, BarNote>();
  for (const n of notes) {
    const cur = byOffset.get(n.offset);
    if (cur === undefined || (top ? n.midi > cur.midi : n.midi < cur.midi)) byOffset.set(n.offset, n);
  }
  return [...byOffset.values()].map((n) => ({ ...n })).sort((a, b) => a.offset - b.offset);
}

/** Shorten each note so it ends by the next onset in the same line. */
function monophonic(line: BarNote[]): BarNote[] {
  for (let i = 0; i + 1 < line.length; i++) {
    const gap = line[i + 1].offset - line[i].offset;
    if (line[i].dur > gap) line[i].dur = gap;
  }
  return line;
}

/** Octave-shift that puts the most notes in the register (smallest |shift| on a tie),
 * then fold the remaining outliers in by octaves. */
function fitLine(line: BarNote[], low: number, high: number, barEighths: number): ScoreNote[] {
  let bestK = 0;
  let bestCount = -1;
  for (const k of [0, -1, 1, -2, 2, -3, 3, -4, 4]) {
    const count = line.reduce((acc, n) => (low <= n.midi + 12 * k && n.midi + 12 * k <= high ? acc + 1 : acc), 0);
    if (count > bestCount) {
      bestCount = count;
      bestK = k;
    }
  }
  const out: ScoreNote[] = [];
  for (const n of line) {
    let m = n.midi + 12 * bestK;
    while (m < low) m += 12;
    while (m > high) m -= 12;
    const dur = Math.min(n.dur, barEighths - n.offset);
    if (dur > 0) out.push({ note: noteName(m), offset: n.offset, dur });
  }
  return out;
}

/** A single pitch class at the lowest octave inside the register, held for dur eighths. */
function heldNote(pc: number, low: number, high: number, offset: number, dur: number): ScoreNote {
  let m = pc % 12;
  while (m < low) m += 12;
  while (m > high) m -= 12;
  return { note: noteName(m), offset, dur };
}
