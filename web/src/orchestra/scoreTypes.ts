// The score-mode data shape: public/scores/*.json, midi.ts (MIDI upload) and the conductor all use it.
import type { MusicianId } from "./musicians";

/** One note in a bar. `offset` and `dur` are in eighth notes from the start of the bar (multiples of 0.5). */
export type ScoreNote = { note: string; offset: number; dur: number };

export type ScoreBar = {
  index: number;
  /** e.g. "F major (F A C)": root, chord kind, then the chord tones (sharps, root first) in parentheses */
  chord: string;
  /** written parts for melodic musicians only; drums have none */
  parts: Partial<Record<MusicianId, ScoreNote[]>>;
};

/** `start_bar` and `end_bar` are inclusive bar indexes. */
export type ScoreSection = { name: string; start_bar: number; end_bar: number };

export type ScoreData = {
  title: string;
  composer: string;
  /** eighth notes per bar: 3 for 3/8, 6 for 3/4 or 6/8, 8 for 4/4 */
  beats_per_bar: number;
  eighth_bpm: number;
  sections: ScoreSection[];
  bars: ScoreBar[];
};
