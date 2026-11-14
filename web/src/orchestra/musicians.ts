// The shared list of musicians. Everyone imports from here.

export const MUSICIAN_IDS = [
  "kick",
  "snare",
  "hats",
  "bass",
  "pad_low",
  "pad_high",
  "flute",
  "violin",
  "synth",
  "kazoo",
  "bells",
  "viola",
  "harp",
  "oboe",
  "clarinet",
  "bassoon",
  "horn",
  "trumpet",
  "trombone",
  "tuba",
  "timpani",
  "piano",
] as const;

export type MusicianId = (typeof MUSICIAN_IDS)[number];

export type Musician = {
  id: MusicianId;
  name: string;
  emoji: string;
  kind: "drums" | "melodic";
  /** register as MIDI note numbers (drums: 0, 0) */
  low: number;
  high: number;
  personality: string;
};

export const MUSICIANS: readonly Musician[] = [
  {
    id: "kick",
    name: "Kick",
    emoji: "🥁",
    kind: "drums",
    low: 0,
    high: 0,
    personality: "a steady heartbeat that lands on beats 1 and 3, rarely fancy",
  },
  {
    id: "snare",
    name: "Snare",
    emoji: "🪘",
    kind: "drums",
    low: 0,
    high: 0,
    personality: "answers the kick on beats 2 and 4, fills before a new phrase",
  },
  {
    id: "hats",
    name: "Hats",
    emoji: "🎩",
    kind: "drums",
    low: 0,
    high: 0,
    personality: "keeps time in eighth notes and follows the mood's energy",
  },
  {
    id: "bass",
    name: "Bass",
    emoji: "🎸",
    kind: "melodic",
    low: 36,
    high: 59,
    personality: "anchors the chord root, moves stepwise, locks with the kick",
  },
  {
    id: "pad_low",
    name: "Pad Low",
    emoji: "🌊",
    kind: "melodic",
    low: 60,
    high: 71,
    personality: "long held chord tones, changes with the chord, avoids pad_high's note",
  },
  {
    id: "pad_high",
    name: "Pad High",
    emoji: "☁️",
    kind: "melodic",
    low: 60,
    high: 71,
    personality: "long held chord tones, changes with the chord, avoids pad_low's note",
  },
  {
    id: "flute",
    name: "Flute",
    emoji: "🪈",
    kind: "melodic",
    low: 72,
    high: 83,
    personality: "sparse but plays at least one phrase every bar, answers phrases others start",
  },
  {
    id: "violin",
    name: "Violin",
    emoji: "🎻",
    kind: "melodic",
    low: 60,
    high: 83,
    personality: "romantic, long lines, holds notes",
  },
  {
    id: "synth",
    name: "Synth",
    emoji: "🎹",
    kind: "melodic",
    low: 72,
    high: 83,
    personality: "slow soft arpeggios, one note per beat in calm moods, fast busy runs when the energy is high",
  },
  {
    id: "kazoo",
    name: "Kazoo",
    emoji: "🐝",
    kind: "melodic",
    low: 60,
    high: 83,
    personality: "plays in every mood; the rebel with surprising notes and odd rhythms, but still listens",
  },
  {
    id: "bells",
    name: "Bells",
    emoji: "🔔",
    kind: "melodic",
    low: 84,
    high: 95,
    personality: "sparse sparkle on phrase starts",
  },
  {
    id: "viola",
    name: "Viola",
    emoji: "🎻",
    kind: "melodic",
    low: 48,
    high: 76,
    personality: "the warm middle voice: fills the harmony between violins and cellos, steady and modest",
  },
  {
    id: "harp",
    name: "Harp",
    emoji: "🎼",
    kind: "melodic",
    low: 36,
    high: 91,
    personality: "shimmering arpeggios and sweeping runs on phrase starts, delicate",
  },
  {
    id: "oboe",
    name: "Oboe",
    emoji: "🪈",
    kind: "melodic",
    low: 58,
    high: 89,
    personality: "plaintive singing solos, sweet with a touch of melancholy",
  },
  {
    id: "clarinet",
    name: "Clarinet",
    emoji: "🎷",
    kind: "melodic",
    low: 50,
    high: 89,
    personality: "agile and mellow, playful runs, answers the flute",
  },
  {
    id: "bassoon",
    name: "Bassoon",
    emoji: "🪈",
    kind: "melodic",
    low: 34,
    high: 70,
    personality: "the orchestra's comedian: bouncy staccato bass lines",
  },
  {
    id: "horn",
    name: "French horn",
    emoji: "📯",
    kind: "melodic",
    low: 41,
    high: 77,
    personality: "noble, round sustained notes; heroic calls on downbeats",
  },
  {
    id: "trumpet",
    name: "Trumpet",
    emoji: "🎺",
    kind: "melodic",
    low: 52,
    high: 82,
    personality: "bright fanfares and bold accents on phrase starts",
  },
  {
    id: "trombone",
    name: "Trombone",
    emoji: "🎺",
    kind: "melodic",
    low: 40,
    high: 72,
    personality: "big brassy slides and powerful sustained chords",
  },
  {
    id: "tuba",
    name: "Tuba",
    emoji: "🎺",
    kind: "melodic",
    low: 28,
    high: 58,
    personality: "oom-pah foundation: chord roots on the strong beats",
  },
  {
    id: "timpani",
    name: "Timpani",
    emoji: "🥁",
    kind: "melodic",
    low: 41,
    high: 53,
    personality: "thunderous accents on the downbeat, tuned to the chord root",
  },
  {
    id: "piano",
    name: "Piano",
    emoji: "🎹",
    kind: "melodic",
    low: 33,
    high: 96,
    personality: "the accompanist: broken chords and occasional flourishes",
  },
];

export const MOOD_NAMES = [
  "calm",
  "triumphant",
  "dark",
  "dreamy",
  "funky",
  "chaos",
  "imperial",
  "jazzy",
  "lullaby",
  "cinematic",
  "spooky",
  "march",
  "tango",
] as const;

/** The 11 ids double as instrument ids: audio, ScoreData parts and MIDI key by instrument. */
export type InstrumentId = MusicianId;

export type Section = "strings" | "woodwinds" | "keyboards" | "percussion" | "brass";

export const INSTRUMENTS: Record<InstrumentId, { name: string; section: Section; weight: number }> = {
  violin: { name: "Violin", section: "strings", weight: 30 },
  pad_low: { name: "Cello", section: "strings", weight: 10 },
  bass: { name: "Double bass", section: "strings", weight: 7 },
  flute: { name: "Flute", section: "woodwinds", weight: 7 },
  kazoo: { name: "Kazoo", section: "woodwinds", weight: 3 },
  pad_high: { name: "Harmonium", section: "keyboards", weight: 4 },
  synth: { name: "Synth", section: "keyboards", weight: 4 },
  kick: { name: "Bass drum", section: "percussion", weight: 5 },
  snare: { name: "Snare drum", section: "percussion", weight: 4 },
  hats: { name: "Cymbals", section: "percussion", weight: 4 },
  bells: { name: "Xylophone", section: "percussion", weight: 4 },
  viola: { name: "Viola", section: "strings", weight: 8 },
  harp: { name: "Harp", section: "strings", weight: 2 },
  oboe: { name: "Oboe", section: "woodwinds", weight: 4 },
  clarinet: { name: "Clarinet", section: "woodwinds", weight: 4 },
  bassoon: { name: "Bassoon", section: "woodwinds", weight: 4 },
  horn: { name: "French horn", section: "brass", weight: 5 },
  trumpet: { name: "Trumpet", section: "brass", weight: 4 },
  trombone: { name: "Trombone", section: "brass", weight: 4 },
  tuba: { name: "Tuba", section: "brass", weight: 2 },
  timpani: { name: "Timpani", section: "percussion", weight: 2 },
  piano: { name: "Piano", section: "keyboards", weight: 2 },
};

export type MoodName = (typeof MOOD_NAMES)[number];

const SEMITONES: Record<string, number> = {
  C: 0,
  "C#": 1,
  Db: 1,
  D: 2,
  "D#": 3,
  Eb: 3,
  E: 4,
  F: 5,
  "F#": 6,
  Gb: 6,
  G: 7,
  "G#": 8,
  Ab: 8,
  A: 9,
  "A#": 10,
  Bb: 10,
  B: 11,
};

/** "A#4" -> 70. C4 is MIDI 60. */
export function noteToMidi(note: string): number {
  const m = /^([A-G])([#b]?)(-?\d)$/.exec(note);
  if (!m) throw new Error(`bad note name: ${note}`);
  const octave = Number(m[3]);
  const semitone = SEMITONES[m[1] + m[2]];
  if (semitone === undefined) throw new Error(`bad note name: ${note}`);
  return 12 * (octave + 1) + semitone;
}
