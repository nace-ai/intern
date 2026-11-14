// The band: musicians, moods and the one-beat decision step.
// Port of orchestra/band.py with the AGENTS.md changes: chord loops, musical
// option descriptions, hold only while sounding, two-level sampling,
// one-beat-delayed listening, timeout fallback, held notes released on a
// mood change.

import type { Answer, DrexClient, QuestionSet } from "./drex.ts";
import { TIMEOUT_MS } from "./drex.ts";
import type { MoodName, Musician } from "./musicians.ts";
import { INSTRUMENTS, MUSICIANS, noteToMidi } from "./musicians.ts";
import { classicRoster, type Member } from "./roster.ts";

export const MAX_HISTORY = 8;
/** Big rosters (>16 members) show fewer beats in Drex's state, to keep it short. */
export const LARGE_ROSTER_HISTORY = 4;

const REST = "rest";
const HOLD = "hold";

const SCALES: Record<string, readonly string[]> = {
  "A minor pentatonic": ["A", "C", "D", "E", "G"],
  "C major": ["C", "D", "E", "F", "G", "A", "B"],
  "A phrygian": ["A", "A#", "C", "D", "E", "F", "G"],
  "C whole-tone": ["C", "D", "E", "F#", "G#", "A#"],
  "E dorian": ["E", "F#", "G", "A", "B", "C#", "D"],
  "C chromatic": ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"],
  "G harmonic minor": ["G", "A", "A#", "C", "D", "D#", "F#"],
  "F major pentatonic": ["F", "G", "A", "C", "D"],
  "D natural minor": ["D", "E", "F", "G", "A", "B", "C"],
  "A harmonic minor": ["A", "B", "C", "D", "E", "F", "G#"],
};

type Chord = { name: string; tones: readonly string[] }; // root, third, fifth

export type Mood = {
  name: MoodName;
  tempo: number;
  scaleName: string;
  temperature: number;
  feel: string;
  chords: readonly Chord[]; // the 4-bar loop
};

const AM: Chord = { name: "Am", tones: ["A", "C", "E"] };
const F: Chord = { name: "F", tones: ["F", "A", "C"] };
const C: Chord = { name: "C", tones: ["C", "E", "G"] };
const G: Chord = { name: "G", tones: ["G", "B", "D"] };
const A_SHARP: Chord = { name: "A#", tones: ["A#", "C#", "E#"] };
const GM: Chord = { name: "Gm", tones: ["G", "Bb", "D"] };
const CMAJ7: Chord = { name: "Cmaj7", tones: ["C", "E", "G", "B"] };
const D7: Chord = { name: "D7", tones: ["D", "F#", "A", "C"] };
const EM7: Chord = { name: "Em7", tones: ["E", "G", "B", "D"] };
const A7: Chord = { name: "A7", tones: ["A", "C#", "E", "G"] };
const F_SHARP: Chord = { name: "F#", tones: ["F#", "A#", "C#"] };
const GM_SHARP: Chord = { name: "Gm", tones: ["G", "A#", "D"] };
const D_SHARP: Chord = { name: "D#", tones: ["D#", "G", "A#"] };
const CM_SHARP: Chord = { name: "Cm", tones: ["C", "D#", "G"] };
const D: Chord = { name: "D", tones: ["D", "F#", "A"] };
const DM7: Chord = { name: "Dm7", tones: ["D", "F", "A", "C"] };
const G7: Chord = { name: "G7", tones: ["G", "B", "D", "F"] };
const DM: Chord = { name: "Dm", tones: ["D", "F", "A"] };
const E: Chord = { name: "E", tones: ["E", "G#", "B"] };
const A_SHARP_FIFTH: Chord = { name: "A#", tones: ["A#", "D", "F"] };

export const MOODS: Record<MoodName, Mood> = {
  calm: {
    name: "calm",
    tempo: 80,
    scaleName: "A minor pentatonic",
    temperature: 0.6,
    feel: "calm and gentle, like a slow breath",
    chords: [AM, F, C, G],
  },
  triumphant: {
    name: "triumphant",
    tempo: 112,
    scaleName: "C major",
    temperature: 0.5,
    feel: "triumphant and bright, a proud fanfare",
    chords: [C, G, AM, F],
  },
  dark: {
    name: "dark",
    tempo: 72,
    scaleName: "A phrygian",
    temperature: 0.7,
    feel: "dark and brooding, a low tense threat",
    chords: [AM, A_SHARP, AM, GM],
  },
  dreamy: {
    name: "dreamy",
    tempo: 76,
    scaleName: "C whole-tone",
    temperature: 0.9,
    feel: "dreamy and floating, soft and weightless",
    chords: [CMAJ7, D7, CMAJ7, D7],
  },
  funky: {
    name: "funky",
    tempo: 104,
    scaleName: "E dorian",
    temperature: 0.8,
    feel: "funky and groovy, locked in a tight pocket",
    chords: [EM7, A7, EM7, A7],
  },
  chaos: {
    name: "chaos",
    tempo: 120,
    scaleName: "C chromatic",
    temperature: 1.3,
    feel: "chaotic and wild, everything at once",
    chords: [C, F_SHARP, C, F_SHARP],
  },
  imperial: {
    name: "imperial",
    tempo: 100,
    scaleName: "G harmonic minor",
    temperature: 0.5,
    feel: "a dark lord's march: heavy, menacing, unstoppable; brass-like stabs land on the downbeat and the bass marches in steady quarters",
    chords: [GM_SHARP, D_SHARP, CM_SHARP, D],
  },
  jazzy: {
    name: "jazzy",
    tempo: 96,
    scaleName: "C major",
    temperature: 0.8,
    feel: "a late-night jazz club: swinging, laid-back, walking bass, sly melodies",
    chords: [DM7, G7, CMAJ7, CMAJ7],
  },
  lullaby: {
    name: "lullaby",
    tempo: 66,
    scaleName: "F major pentatonic",
    temperature: 0.4,
    feel: "a gentle lullaby rocking a baby to sleep: soft, slow, tender, lots of space",
    chords: [F, C, DM, C],
  },
  cinematic: {
    name: "cinematic",
    tempo: 90,
    scaleName: "D natural minor",
    temperature: 0.6,
    feel: "an epic movie trailer: swelling, heroic, big sustained chords building tension",
    chords: [DM, A_SHARP_FIFTH, F, C],
  },
  spooky: {
    name: "spooky",
    tempo: 70,
    scaleName: "A harmonic minor",
    temperature: 0.9,
    feel: "a haunted house at midnight: creepy, sparse, sudden stabs, eerie silences",
    chords: [AM, F, E, AM],
  },
  march: {
    name: "march",
    tempo: 116,
    scaleName: "C major",
    temperature: 0.4,
    feel: "a proud parade march: crisp, square, everyone in step, the snare drives",
    chords: [C, F, G, C],
  },
  tango: {
    name: "tango",
    tempo: 112,
    scaleName: "A harmonic minor",
    temperature: 0.7,
    feel: "a dramatic Argentine tango: passionate, staccato accents, sudden pauses",
    chords: [AM, DM, E, AM],
  },
};

const DRUM_OPTIONS = ["x.", "xx", ".x", ".."] as const;

const DRUM_CRITERIA: Record<string, string> = {
  "x.": "hit on the first eighth, silent on the second",
  xx: "hit on both eighths",
  ".x": "silent on the first eighth, hit on the second",
  "..": "silent for the whole beat",
  xX: "hit on the first eighth, accented hit on the second",
};

export type MusicianDecision = { play: string; top: string; probs: Record<string, number> };

/** One beat's worth of a member's determinism, as shown to the UI. */
export type MemberDet = { repeatRate: number; meanTop: number; distinct: number };

/** Per-member determinism: does the agent keep playing the same note? */
export class Determinism {
  private prev: string | null = null;
  private played = 0;
  private repeats = 0;
  private topSum = 0;
  private topCount = 0;
  private notes = new Set<string>();

  /**
   * One beat: `note` is the played note or drum pattern, null when the member
   * rested; `top` is Drex's top-choice probability for this beat (0..1).
   */
  observe(note: string | null, top: number): void {
    if (note !== null) {
      if (this.prev === note) this.repeats += 1;
      this.prev = note;
      this.played += 1;
      this.notes.add(note);
    }
    this.topSum += top;
    this.topCount += 1;
  }

  stats(): MemberDet {
    return {
      repeatRate: this.played > 0 ? this.repeats / this.played : 0,
      meanTop: this.topCount > 0 ? this.topSum / this.topCount : 0,
      distinct: this.notes.size,
    };
  }
}

export type BeatDecision = {
  index: number;
  bar: number;
  beatInBar: number;
  tempo: number;
  mood: MoodName;
  /** keyed by member id (classic roster: member id equals the instrument id) */
  notes: Record<string, MusicianDecision>;
  latencyMs: number;
  decisions: number;
  timedOut: boolean;
  /** Drex cost for the round; 0 on a timeout */
  cost: number;
};

export type NdiSection = { name: string; summary?: string; quote?: string } | null;

function pitchClass(note: string): string {
  return note.replace(/-?\d+$/, "");
}

function octavesFor(low: number, high: number): number[] {
  const octaves: number[] = [];
  for (let o = 0; o <= 7; o++) {
    const lo = 12 * (o + 1);
    const hi = lo + 11;
    if (Math.max(lo, low) <= Math.min(hi, high)) octaves.push(o);
  }
  return octaves;
}

function chordRole(note: string, chord: Chord): string {
  const i = chord.tones.indexOf(pitchClass(note));
  if (i === 0) return `root of ${chord.name}, home`;
  if (i === 1) return `3rd of ${chord.name}, sets the color`;
  if (i === 2) return `5th of ${chord.name}, strong`;
  return "color note, adds tension";
}

function motion(sounding: string, note: string): string {
  const d = noteToMidi(note) - noteToMidi(sounding);
  if (d === 0) return "";
  if (d === 1) return `a step up from ${sounding}`;
  if (d === -1) return `a step down from ${sounding}`;
  if (d > 0) return `a skip up from ${sounding}`;
  return `a skip down from ${sounding}`;
}

function argmax(probs: Record<string, number>): string {
  return Object.entries(probs).reduce((a, b) => (b[1] > a[1] ? b : a))[0];
}

/** Pick one option from (option, probability) pairs at the mood's temperature. */
function sampleWeighted(entries: [string, number][], temperature: number): string {
  if (temperature <= 0) return argmax(Object.fromEntries(entries));
  const weight = (p: number) => Math.max(p, 1e-12) ** (1 / temperature);
  let r = Math.random() * entries.reduce((s, [, p]) => s + weight(p), 0);
  for (const [option, p] of entries) {
    r -= weight(p);
    if (r <= 0) return option;
  }
  return entries[entries.length - 1][0];
}

/**
 * Two-level sampling so many note options can't drown in rest: first the kind
 * (note / rest / hold) from the summed probabilities, then the note within
 * notes. Temperature 0 = argmax at both levels.
 */
export function sampleNote(probs: Record<string, number>, sounding: boolean, temperature: number): string {
  let noteSum = 0;
  let restP = 0;
  let holdP = 0;
  for (const [option, p] of Object.entries(probs)) {
    const v = typeof p === "number" && p > 0 ? p : 0;
    if (option === REST) restP += v;
    else if (option === HOLD) holdP += v;
    else noteSum += v;
  }
  if (noteSum === 0 && restP === 0 && holdP === 0) return REST;
  const kinds: [string, number][] = [["note", noteSum]];
  if (restP > 0) kinds.push(["rest", restP]);
  if (holdP > 0 && sounding) kinds.push(["hold", holdP]);
  const kind = sampleWeighted(kinds, temperature);
  if (kind === REST) return REST;
  if (kind === HOLD) return sounding ? HOLD : REST;
  return sampleWeighted(
    Object.entries(probs).filter(([option]) => option !== REST && option !== HOLD),
    temperature,
  );
}

export class Band {
  private _mood: Mood;
  private _index = 0;
  /** how many of the most recent decided beats Drex's state shows */
  private _decidedAhead = 1;
  readonly members: readonly Member[];
  private _byId = new Map<string, { member: Member; instrument: Musician }>();
  private _history: Record<string, string[]> = {};
  private _sounding: Record<string, string | null> = {};
  private _restStreak: Record<string, number> = {};
  private _section: NdiSection = null;
  private _directions: Partial<Record<string, string>> = {};

  constructor(mood: MoodName = "imperial", members: readonly Member[] = classicRoster()) {
    this._mood = MOODS[mood];
    this.members = members;
    for (const member of members) {
      const instrument = MUSICIANS.find((m) => m.id === member.instrument);
      if (!instrument) throw new Error(`unknown instrument: ${member.instrument}`);
      this._byId.set(member.id, { member, instrument });
      this._history[member.id] = [];
      this._sounding[member.id] = null;
      this._restStreak[member.id] = 0;
    }
  }

  get mood(): Mood {
    return this._mood;
  }

  get index(): number {
    return this._index;
  }

  /** The decided beats per member, newest last. */
  get history(): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    for (const member of this.members) out[member.id] = [...this._history[member.id]];
    return out;
  }

  /** How many beats of history Drex's state shows: 4 for a big orchestra, 8 otherwise. */
  get historyLength(): number {
    return this.members.length > 16 ? LARGE_ROSTER_HISTORY : MAX_HISTORY;
  }

  /**
   * One-beat-delayed listening: when deciding beat n the band only knows
   * itself up to beat n - decidedAhead - 1. The conductor sets this to its
   * lookahead (2 beats ahead by default; 3 when it must decide further out,
   * since then two rounds are in flight at once).
   */
  set decidedAhead(n: number) {
    this._decidedAhead = Math.max(1, Math.floor(n));
  }

  /** Switch the mood; it applies from the next beat on. A held note no longer in the new scale is released. */
  setMood(name: MoodName): void {
    const next = MOODS[name];
    const scale = SCALES[next.scaleName];
    for (const { member, instrument } of this._byId.values()) {
      const sounding = this._sounding[member.id];
      if (instrument.kind === "melodic" && sounding !== null && !scale.includes(pitchClass(sounding))) {
        this._sounding[member.id] = null;
      }
    }
    this._mood = next;
  }

  /** The NDI section the musicians should follow, if any. */
  setSection(section: NdiSection): void {
    this._section = section;
  }

  /** One direction sentence per member (or instrument, as a fallback), from NDI. */
  setMusicianDirections(directions: Partial<Record<string, string>>): void {
    this._directions = directions;
  }

  /**
   * Decide and record what the whole band plays on the next beat. If Drex
   * times out, the band plays on by itself: melodic musicians hold (or rest
   * when nothing is sounding) and drums repeat their last pattern.
   */
  async decideBeat(drex: DrexClient): Promise<BeatDecision> {
    // Claim the index synchronously, before the await: with two rounds in
    // flight, both rounds must see different indices (bar, beat and chord).
    const index = this._index++;
    const state = this.buildState(index);
    const questions = this.buildQuestions(index);
    let answers: Record<string, Answer> | null = null;
    let timedOut = false;
    let cost = 0;
    const started = performance.now();
    try {
      const res = await drex(state, questions);
      answers = res.answers;
      cost = res.usage.cost;
    } catch {
      // a failed Drex round falls back exactly like a timeout: the band keeps
      // playing on its own (see _decideMember) and it counts in stats.timeouts
      timedOut = true;
    }
    const latencyMs = timedOut ? TIMEOUT_MS : performance.now() - started;
    const notes: Record<string, MusicianDecision> = {};
    for (const { member, instrument } of this._byId.values()) {
      notes[member.id] = this._decideMember(member, instrument, answers, timedOut);
    }
    return {
      index,
      bar: (Math.floor(index / 4) % 4) + 1,
      beatInBar: (index % 4) + 1,
      tempo: this._mood.tempo,
      mood: this._mood.name,
      notes,
      latencyMs,
      decisions: Object.keys(questions).length,
      timedOut,
      cost,
    };
  }

  private _decideMember(
    member: Member,
    m: Musician,
    answers: Record<string, Answer> | null,
    timedOut: boolean,
  ): MusicianDecision {
    const id = member.id;
    const history = this._history[id];
    let play: string;
    let top: string;
    const probs: Record<string, number> = {};
    if (timedOut || answers === null) {
      if (m.kind === "drums") play = history.length > 0 ? history[history.length - 1] : "..";
      else play = this._sounding[id] !== null ? HOLD : REST;
      top = play;
    } else {
      const answer = answers[id];
      const raw = answer?.probabilities;
      if (raw && typeof raw === "object") {
        for (const [k, v] of Object.entries(raw)) if (typeof v === "number" && Number.isFinite(v)) probs[k] = v;
      }
      if (m.kind === "drums") {
        if (Object.keys(probs).length === 0) {
          play = "..";
          top = "..";
        } else {
          top = answer?.choice != null && probs[answer.choice] !== undefined ? answer.choice : argmax(probs);
          play = sampleWeighted(Object.entries(probs), this._mood.temperature);
        }
      } else {
        const sounding = this._sounding[id] !== null;
        if (Object.keys(probs).length === 0) {
          play = sounding ? HOLD : REST;
          top = sounding ? HOLD : REST;
        } else {
          top = answer?.choice != null && probs[answer.choice] !== undefined ? answer.choice : argmax(probs);
          play = sampleNote(probs, sounding, this._mood.temperature);
          if (play === HOLD && !sounding) play = REST;
        }
      }
    }
    if (m.kind === "melodic") {
      if (play === REST) {
        this._sounding[id] = null;
        this._restStreak[id] += 1;
      } else {
        if (play !== HOLD) this._sounding[id] = play;
        this._restStreak[id] = 0;
      }
    }
    history.push(play);
    if (history.length > MAX_HISTORY) history.shift();
    return { play, top, probs };
  }

  /** The 4-bar chord loop: one chord per bar. */
  private chordAt(index: number): Chord {
    return this._mood.chords[Math.floor(index / 4) % 4];
  }

  /** The text Drex reads; the band is visible up to one beat before the decided one. */
  private buildState(index: number): string {
    const bar = (Math.floor(index / 4) % 4) + 1;
    const beat = (index % 4) + 1;
    const chord = this.chordAt(index);
    const length = this.historyLength;
    const lines = [
      `You are deciding bar ${bar} beat ${beat} of a 4-bar phrase (beat 1 is the downbeat).`,
      `Mood: ${this._mood.name} — ${this._mood.feel}. Tempo ${this._mood.tempo} BPM, ${this._mood.scaleName} scale.`,
      `Chord: ${chord.name} (${chord.tones.join(" ")}).`,
    ];
    if (this._section) {
      const s = this._section;
      lines.push(
        `Current NDI section: ${s.name}${s.summary ? ` — ${s.summary}` : ""}${s.quote ? ` ("${s.quote}")` : ""}.`,
      );
    }
    lines.push(
      `The orchestra has ${this.members.length} musicians. What each played on the last ${length} beats (oldest to newest; "(start)" = not played yet):`,
    );
    for (const member of this.members) {
      lines.push(`${member.id}: ${this.visibleHistory(member.id).join(" ")}`);
    }
    return lines.join("\n");
  }

  /** What the band has "heard" by now: decided beats, minus the ones still in flight. */
  private visibleHistory(id: string): string[] {
    const length = this.historyLength;
    const h = this._history[id];
    const seen = h.length > this._decidedAhead ? h.slice(0, -this._decidedAhead) : [];
    const recent = seen.slice(-length);
    return ["(start)".repeat(length - recent.length), ...recent].flat();
  }

  /** The member's NDI direction: its own, falling back to its instrument's. */
  private directionFor(member: Member): string | undefined {
    const dirs = this._directions;
    const own = dirs[member.id];
    if (own) return own;
    const byInstrument = dirs[member.instrument];
    return byInstrument || undefined;
  }

  private buildQuestions(index: number): QuestionSet {
    const bar = (Math.floor(index / 4) % 4) + 1;
    const beat = (index % 4) + 1;
    const questions: QuestionSet = {};
    for (const { member, instrument } of this._byId.values()) {
      const direction = this.directionFor(member);
      questions[member.id] = {
        type: "choice",
        instructions:
          `You are ${member.name}, ${member.trait} ${INSTRUMENTS[member.instrument].name} (${instrument.personality}). ` +
          (direction ? `Your direction: ${direction}. ` : "") +
          `Decide what ${member.name} plays on this beat (bar ${bar}, beat ${beat}).`,
        criteria: this._criteria(member, instrument, index),
      };
    }
    return questions;
  }

  private _criteria(member: Member, m: Musician, index: number): Record<string, string> {
    const id = member.id;
    if (m.kind === "drums") {
      const criteria: Record<string, string> = {};
      for (const option of DRUM_OPTIONS) criteria[option] = DRUM_CRITERIA[option];
      if (m.id === "hats") criteria.xX = DRUM_CRITERIA.xX;
      return criteria;
    }
    const chord = this.chordAt(index);
    const scale = SCALES[this._mood.scaleName];
    const sounding = this._sounding[id];
    const criteria: Record<string, string> = {};
    for (const octave of octavesFor(m.low, m.high)) {
      for (const pc of scale) {
        const note = `${pc}${octave}`;
        const parts = [chordRole(note, chord)];
        if (sounding !== null) {
          const mo = motion(sounding, note);
          if (mo !== "") parts.push(mo);
        }
        criteria[note] = parts.join("; ");
      }
    }
    const streak = this._restStreak[id];
    criteria[REST] = streak > 0 ? `stay silent this beat; you have rested ${streak} beats` : "stay silent this beat";
    if (sounding !== null) criteria[HOLD] = `keep sounding ${sounding}`;
    return criteria;
  }
}
