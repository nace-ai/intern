// A fake conductor: emits believable random events with no audio and no Drex,
// so the UI can be built and watched before the real conductor exists.
// Roster-aware: one PanelUpdate per MEMBER (id like "violin-2"), with instrument and det.
import type {
  ConductorApi,
  ConductorEvent,
  Det,
  DirectionsLike,
  PanelUpdate,
  Stats,
} from "@/components/conductorTypes";
import { apiCounter } from "@/orchestra/apiCounter";
import { MOOD_NAMES, type MoodName, MUSICIANS, type Musician } from "@/orchestra/musicians";
import { classicRoster, type Member, seatRoster } from "@/orchestra/roster";

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"] as const;
const DIRECTIONS = [
  "Play softer, like a lullaby",
  "Answer the flute",
  "Build toward the chorus",
  "Hold back, leave space",
] as const;
const DRUM_PATTERN = /^[xX.]+$/;
const byInstrument = new Map<string, Musician>(MUSICIANS.map((m) => [m.id, m]));

/** MIDI number -> note name, sharps only. Octave = floor(midi/12) - 1 (C4 = 60). */
function midiToName(midi: number): string {
  return NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 1);
}

function randomNotesInRegister(low: number, high: number, count: number): string[] {
  const size = high - low + 1;
  const picked = new Set<number>();
  while (picked.size < count) picked.add(low + Math.floor(Math.random() * size));
  return [...picked].map(midiToName);
}

/** One Math.random() per option, normalized to sum 1. */
function randomProbs(options: string[]): Record<string, number> {
  let weights = options.map(() => Math.random());
  if (weights.reduce((a, b) => a + b, 0) === 0) weights = weights.map(() => 1);
  const sum = weights.reduce((a, b) => a + b, 0);
  const probs: Record<string, number> = {};
  options.forEach((option, i) => {
    probs[option] = weights[i] / sum;
  });
  return probs;
}

function weightedPick(options: string[], probs: Record<string, number>): string {
  let r = Math.random();
  for (const option of options) {
    r -= probs[option];
    if (r <= 0) return option;
  }
  return options[options.length - 1];
}

const listeners = new Set<(e: ConductorEvent) => void>();
function emit(e: ConductorEvent): void {
  for (const fn of listeners) fn(e);
}

let initPromise: Promise<void> | null = null;
let beatTimer: ReturnType<typeof setInterval> | null = null;
let running = false;
let tempo = 80;
let startedAt = 0;
let decisions = 0;
let cost = 0;
let beatCount = 0;
let members: Member[] = seatRoster(classicRoster());

/** Per-member play history (every play, including rest/hold/drum patterns) and the max probability per beat. */
type MemberHistory = { plays: string[]; topProbs: number[] };
const history = new Map<string, MemberHistory>();

function resetHistory(): void {
  history.clear();
}

/** "Notes" = plays that are not rest or hold (drum patterns count as the note). */
function detFor(h: MemberHistory): Det {
  const notes = h.plays.filter((p) => p !== "rest" && p !== "hold");
  let repeats = 0;
  for (let i = 1; i < notes.length; i++) {
    if (notes[i] === notes[i - 1]) repeats++;
  }
  const meanTop = h.topProbs.length === 0 ? 0 : h.topProbs.reduce((a, b) => a + b, 0) / h.topProbs.length;
  return {
    repeatRate: repeats / Math.max(1, notes.length - 1),
    meanTop,
    distinct: new Set(notes).size,
  };
}

function buildUpdate(member: Member): PanelUpdate {
  const m = byInstrument.get(member.instrument) ?? MUSICIANS[0];
  let options: string[];
  if (m.kind === "drums") {
    options = ["x.", "xx", ".x", ".."];
    if (member.instrument === "hats") options.push("xX");
  } else {
    options = [...randomNotesInRegister(m.low, m.high, 3), "rest"];
    const prev = history.get(member.id)?.plays.at(-1);
    if (prev !== undefined && prev !== "" && prev !== "rest" && prev !== "hold" && !DRUM_PATTERN.test(prev)) {
      options.push("hold");
    }
  }
  const probs = randomProbs(options);
  const play = weightedPick(options, probs);
  const h = history.get(member.id) ?? { plays: [], topProbs: [] };
  h.plays.push(play);
  h.topProbs.push(Math.max(...Object.values(probs)));
  history.set(member.id, h);
  const direction = Math.random() < 0.05 ? DIRECTIONS[Math.floor(Math.random() * DIRECTIONS.length)] : "";
  return { musician: member.id, instrument: member.instrument, play, probs, direction, det: detFor(h) };
}

function buildBeat(): ConductorEvent {
  beatCount += 1;
  decisions += members.length;
  cost += 0.0004 * members.length;
  const elapsedSec = Math.max((performance.now() - startedAt) / 1000, 0.001);
  const stats: Stats = {
    decisions,
    apiCalls: apiCounter.calls,
    decisionsPerSec: decisions / elapsedSec,
    avgLatencyMs: 280 + Math.random() * 80,
    cost,
    timeouts: 0,
    tempo,
    bar: (Math.floor((beatCount - 1) / 4) % 4) + 1,
    beat: ((beatCount - 1) % 4) + 1,
  };
  return { type: "beat", updates: members.map(buildUpdate), stats };
}

function beginRun(mode: "improv" | "score"): void {
  running = true;
  tempo = mode === "improv" ? 80 : 96;
  startedAt = performance.now();
  decisions = 0;
  cost = 0;
  beatCount = 0;
  resetHistory();
  beatTimer = setInterval(() => emit(buildBeat()), 60000 / tempo);
}

export const fakeConductor: ConductorApi = {
  init() {
    if (initPromise === null) {
      initPromise = new Promise<void>((resolve) => {
        for (let i = 0; i <= 12; i++) {
          window.setTimeout(() => {
            emit({ type: "loading", loaded: i, total: 12 });
          }, i * 100);
        }
        window.setTimeout(resolve, 13 * 100);
        if (new URLSearchParams(window.location.search).has("autostart")) {
          // dev only, lets a headless screenshot see the stage playing
          window.setTimeout(() => void fakeConductor.start(), 13 * 100);
        }
      });
    }
    return initPromise;
  },

  async start() {
    if (running) return;
    beginRun("improv");
    emit({ type: "state", running: true, mode: "improv" });
  },

  async perform() {
    if (running) return;
    beginRun("score");
    emit({ type: "state", running: true, mode: "score" });
    emit({ type: "section", section: { name: "Verse 1", quote: "La donna è mobile, qual piuma al vento" } });
  },

  stop() {
    running = false;
    if (beatTimer !== null) {
      clearInterval(beatTimer);
      beatTimer = null;
    }
    resetHistory();
    emit({ type: "state", running: false, mode: null });
  },

  setMood(preset: MoodName) {
    emit({ type: "mood", preset });
  },

  setRoster(m: Member[]) {
    members = m;
  },

  moodFromText(text: string) {
    return new Promise<{ preset: MoodName; probs: Record<MoodName, number> }>((resolve) => {
      window.setTimeout(() => {
        const preset = MOOD_NAMES[Math.floor(Math.random() * MOOD_NAMES.length)];
        const weights = MOOD_NAMES.map((m) => (m === preset ? 2 + Math.random() * 4 : Math.random()));
        const sum = weights.reduce((a, b) => a + b, 0);
        const probs = {} as Record<MoodName, number>;
        MOOD_NAMES.forEach((m, i) => {
          probs[m] = weights[i] / sum;
        });
        emit({ type: "mood", preset, probs, text });
        resolve({ preset, probs });
      }, 400);
    });
  },

  setDirections(d: DirectionsLike) {
    if (d.sections.length === 0) return;
    const first = d.sections[0];
    emit({ type: "section", section: { name: first.name, quote: first.quote, summary: first.summary } });
  },

  subscribe(fn: (e: ConductorEvent) => void) {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
};
