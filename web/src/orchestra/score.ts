// Score mode: the swarm performs a written score. One Drex round per bar: each
// melodic musician picks how to render its written part, drums pick a 3-eighth
// pattern. The choices render to note events the audio engine plays.

import type { QuestionSet } from "./drex.ts";
import { INSTRUMENTS, MUSICIANS, type Musician, noteToMidi } from "./musicians.ts";
import type { Member } from "./roster.ts";

// The shape of /scores/la_donna.json (and of midiToScore's output).
export type ScoreNote = { note: string; offset: number; dur: number };
export type ScoreBar = { index: number; chord: string; parts: Record<string, ScoreNote[]> };
export type ScoreSection = { name: string; start_bar: number; end_bar: number };
export type ScoreData = {
  title: string;
  composer: string;
  beats_per_bar: number; // eighths per bar
  eighth_bpm: number; // BPM of the eighth note
  sections: ScoreSection[];
  bars: ScoreBar[];
};

export async function loadDefaultScore(signal?: AbortSignal): Promise<ScoreData> {
  const res = await fetch("/scores/la_donna.json", { signal });
  if (!res.ok) throw new Error(`Could not load the score (HTTP ${res.status})`);
  return (await res.json()) as ScoreData;
}

export const DRUM_PATTERNS = ["x..", "x.x", "xxx", "..x", "..."] as const;

const DRUM_CRITERIA: Record<string, string> = {
  "x..": "hit on the first eighth, silent for the rest of the bar",
  "x.x": "hit on the first and third eighths",
  xxx: "hit on all three eighths",
  "..x": "hit on the last eighth only",
  "...": "stay silent for the whole bar",
};

// how adventurous the swarm is when picking a rendering
const TEMPERATURE = 0.8;

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function midiToName(midi: number): string {
  const m = Math.round(midi);
  return `${NAMES[((m % 12) + 12) % 12]}${Math.floor(m / 12) - 1}`;
}

export function shiftNote(note: string, semitones: number): string {
  return midiToName(noteToMidi(note) + semitones);
}

/** Octave-shift the note into [low, high]. */
export function toRegister(midi: number, low: number, high: number): number {
  let m = midi;
  while (m < low) m += 12;
  while (m > high) m -= 12;
  return m;
}

export function sectionForBar(score: ScoreData, barIndex: number): number {
  let i = 0;
  for (const s of score.sections) {
    if (barIndex <= s.end_bar) return i;
    i += 1;
  }
  return score.sections.length - 1;
}

/** NDI section i applies to score section i, spread proportionally. */
export function ndiIndexForScoreSection(scoreSections: number, ndiSections: number, i: number): number {
  if (ndiSections === 0) return -1;
  return Math.min(Math.floor((i * ndiSections) / Math.max(scoreSections, 1)), ndiSections - 1);
}

export type BarNdiSection = {
  summary?: string;
  quote?: string;
  /** keyed by member id (or instrument id, as a fallback) */
  directions: Partial<Record<string, string>>;
} | null;

/** The member's NDI direction: its own, falling back to its instrument's. */
function directionFor(ndi: BarNdiSection, member: Member): string | undefined {
  if (!ndi) return undefined;
  const own = ndi.directions[member.id];
  if (own) return own;
  const byInstrument = ndi.directions[member.instrument];
  return byInstrument || undefined;
}

/** One Drex round per bar: state and a question for every member. */
export function buildBarRound(
  score: ScoreData,
  barIndex: number,
  ndi: BarNdiSection,
  members: readonly Member[],
  lastChoices: Record<string, string>,
): { state: string; questions: QuestionSet } {
  const bar = score.bars[barIndex];
  const section = score.sections[sectionForBar(score, barIndex)];
  const lines = [
    `You are performing "${score.title}" by ${score.composer} in ${score.beats_per_bar}/8 time, eighth note = ${score.eighth_bpm} BPM.`,
  ];
  if (ndi) {
    lines.push(`Section: ${section.name} — ${ndi.summary}${ndi.quote ? ` ("${ndi.quote}")` : ""}.`);
  } else {
    lines.push(`Section: ${section.name}.`);
  }
  lines.push(`You are deciding bar ${barIndex + 1} of ${score.bars.length}. Chord: ${bar.chord}.`);
  lines.push(`The orchestra has ${members.length} musicians.`);
  lines.push("Written in for this bar (offsets in eighths from the bar start):");
  for (const member of members) {
    const m = MUSICIANS.find((x) => x.id === member.instrument)!;
    const part = bar.parts[member.instrument];
    lines.push(
      part && part.length > 0
        ? `  ${member.id}: ${part.map((n) => `${n.note}@${n.offset}`).join(" ")}`
        : `  ${member.id}: (nothing written${m.kind === "drums" ? "; improvise" : ""})`,
    );
  }
  lines.push("What each musician chose last bar:");
  lines.push("  " + members.map((member) => `${member.id}: ${lastChoices[member.id] ?? "(start)"}`).join(", "));
  const questions: QuestionSet = {};
  for (const member of members) {
    const m = MUSICIANS.find((x) => x.id === member.instrument)!;
    const direction = directionFor(ndi, member);
    questions[member.id] = {
      type: "choice",
      instructions:
        `You are ${member.name}, ${member.trait} ${INSTRUMENTS[member.instrument].name} (${m.personality}). ` +
        (direction ? `Your direction: ${direction}. ` : "") +
        (m.kind === "drums"
          ? "Choose a 3-eighth pattern for this bar."
          : "Choose how to render your written part for this bar."),
      criteria: criteriaFor(m, bar),
    };
  }
  return { state: lines.join("\n"), questions };
}

import type { NoteEvent } from "./audio.ts";
import type { Answer } from "./drex.ts";

export type MusicianChoice = { play: string; top: string; probs: Record<string, number> };

export type BarRender = {
  /** keyed by member id */
  choices: Record<string, MusicianChoice>;
  events: NoteEvent[];
};

function argmax(probs: Record<string, number>): string {
  return Object.entries(probs).reduce((a, b) => (b[1] > a[1] ? b : a))[0];
}

function sampleOption(probs: Record<string, number>): string {
  const entries = Object.entries(probs).filter(([, p]) => p > 0);
  if (entries.length === 0) return argmax(probs);
  if (TEMPERATURE <= 0) return argmax(Object.fromEntries(entries));
  const w = (p: number) => Math.max(p, 1e-12) ** (1 / TEMPERATURE);
  let r = Math.random() * entries.reduce((s, [, p]) => s + w(p), 0);
  for (const [option, p] of entries) {
    r -= w(p);
    if (r <= 0) return option;
  }
  return entries[entries.length - 1][0];
}

/** Apply the Drex answers to one bar and render every member to events. */
export function renderBar(
  score: ScoreData,
  barIndex: number,
  answers: Record<string, Answer> | null,
  members: readonly Member[],
  lastChoices: Record<string, string>,
): BarRender {
  const bar = score.bars[barIndex];
  const e = 60 / score.eighth_bpm;
  const choices: Record<string, MusicianChoice> = {};
  const events: NoteEvent[] = [];
  for (const member of members) {
    const m = MUSICIANS.find((x) => x.id === member.instrument)!;
    const valid = criteriaFor(m, bar);
    const part = bar.parts[member.instrument];
    const fallback =
      m.kind === "drums" ? (lastChoices[member.id] ?? "x..") : (part?.length ?? 0) > 0 ? "as_written" : "melody";
    const raw = answers?.[member.id]?.probabilities;
    const probs: Record<string, number> = {};
    if (raw && typeof raw === "object") {
      for (const [k, v] of Object.entries(raw)) if (typeof v === "number" && Number.isFinite(v)) probs[k] = v;
    }
    let play: string;
    let top: string;
    if (answers === null || Object.keys(probs).length === 0) {
      play = valid[fallback] !== undefined ? fallback : "rest";
      top = play;
    } else {
      const choice = answers[member.id]?.choice;
      top = choice != null && probs[choice] !== undefined ? choice : argmax(probs);
      play = sampleOption(probs);
      if (valid[play] === undefined) play = valid[fallback] !== undefined ? fallback : "rest";
    }
    choices[member.id] = { play, top, probs };
    events.push(...renderChoice(score, member, m, bar, play, e));
  }
  return { choices, events };
}

function ev(member: Member, note: string, offset: number, dur: number, e: number): NoteEvent {
  return {
    musician: member.id,
    instrument: member.instrument,
    note,
    offsetS: offset * e,
    durS: dur * e,
    velocity: 0.9,
  };
}

function renderChoice(
  score: ScoreData,
  member: Member,
  m: Musician,
  bar: ScoreBar,
  option: string,
  e: number,
): NoteEvent[] {
  if (m.kind === "drums") {
    const out: NoteEvent[] = [];
    for (let i = 0; i < score.beats_per_bar; i++) {
      if (option[i] === "x")
        out.push({ musician: member.id, instrument: m.id, note: "hit", offsetS: i * e, durS: 0.05, velocity: 0.8 });
    }
    return out;
  }
  const part = [...(bar.parts[m.id] ?? [])].sort((a, b) => a.offset - b.offset);
  switch (option) {
    case "octave_up":
      return part.map((n) => ev(member, shiftNote(n.note, 12), n.offset, n.dur, e));
    case "octave_down":
      return part.map((n) => ev(member, shiftNote(n.note, -12), n.offset, n.dur, e));
    case "sparse": {
      if (part.length === 0) return [];
      const f = part[0];
      return [ev(member, f.note, f.offset, score.beats_per_bar - f.offset, e)];
    }
    case "ornament": {
      const out = part.map((n) => ev(member, n.note, n.offset, n.dur, e));
      for (let i = 0; i + 1 < part.length; i++) {
        const a = part[i];
        const b = part[i + 1];
        const gapStart = a.offset + a.dur;
        if (b.offset > gapStart) {
          out.push(ev(member, shiftNote(a.note, 1), gapStart, Math.min(1, b.offset - gapStart), e));
        }
      }
      return out;
    }
    case "melody":
      return melodyEvents(score, member, m, bar, e);
    case "rest":
      return [];
    default: // as_written
      return part.map((n) => ev(member, n.note, n.offset, n.dur, e));
  }
}

/** The melody line: the highest written note at each eighth, in your register. */
function melodyEvents(score: ScoreData, member: Member, m: Musician, bar: ScoreBar, e: number): NoteEvent[] {
  const topAt = new Map<number, { midi: number; dur: number }>();
  for (const id of Object.keys(bar.parts)) {
    const musician = MUSICIANS.find((x) => x.id === id);
    if (!musician || musician.kind === "drums") continue;
    for (const n of bar.parts[id]) {
      const midi = noteToMidi(n.note);
      const cur = topAt.get(n.offset);
      if (!cur || midi > cur.midi) topAt.set(n.offset, { midi, dur: n.dur });
    }
  }
  const out: NoteEvent[] = [];
  for (const [offset, { midi, dur }] of topAt) {
    const durS = Math.min(dur, score.beats_per_bar - offset);
    if (durS <= 0) continue;
    out.push(ev(member, midiToName(toRegister(midi, m.low, m.high)), offset, durS, e));
  }
  return out.sort((a, b) => a.offsetS - b.offsetS);
}

function criteriaFor(m: Musician, bar: ScoreBar): Record<string, string> {
  if (m.kind === "drums") {
    const criteria: Record<string, string> = {};
    for (const p of DRUM_PATTERNS) criteria[p] = DRUM_CRITERIA[p];
    return criteria;
  }
  const hasPart = (bar.parts[m.id]?.length ?? 0) > 0;
  const criteria: Record<string, string> = {
    melody: "the melody line of the piece, in your register",
    rest: "stay silent for this bar",
  };
  if (hasPart) {
    criteria.as_written = "play exactly the written notes";
    criteria.octave_up = "your written notes one octave higher";
    criteria.octave_down = "your written notes one octave lower";
    criteria.ornament = "the written notes, with a quick passing note added between them";
    criteria.sparse = "only the first written note, held for the rest of the bar";
  }
  return criteria;
}
