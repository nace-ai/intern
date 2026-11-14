// The Tone.js engine. Sampled instruments (flute, violin, bass, pads, bells)
// plus synths for kick, snare, hats, synth and kazoo. Everything runs through
// one shared Reverb (wet 0.25) and a Limiter into Destination.
// This module never calls Tone.start(); the UI does that on a click.
import * as Tone from "tone";
import { type InstrumentId, MUSICIAN_IDS, MUSICIANS, type MusicianId } from "./musicians";

export type Beat = {
  /** bpm; the beat lasts 60 / tempo seconds */
  tempo: number;
  /** per member id: what it plays plus its instrument (shared voices) */
  notes: Partial<Record<string, { play: string; instrument: InstrumentId }>>;
};

export type NoteEvent = {
  /** member id (classic roster: the instrument id) */
  musician: string;
  /** the member's instrument: picks the voice */
  instrument: InstrumentId;
  /** a melodic note name, or "hit" / "accent" for drums */
  note: string;
  offsetS: number;
  durS: number;
  velocity: number;
};

type Manifest = Record<string, { dir: string; notes: Record<string, string> }>;

const SAMPLED: MusicianId[] = [
  "flute",
  "violin",
  "bass",
  "pad_low",
  "pad_high",
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
  "piano",
];

// Mix: leads sit on top, drums and pads sit underneath.
// Brass and timpani sit ~3 dB below the leads (violin -3).
const SAMPLER_TUNING: Record<string, { volume: number; attack: number; release: number }> = {
  flute: { volume: -2, attack: 0.01, release: 0.6 },
  violin: { volume: -3, attack: 0.02, release: 0.5 },
  bass: { volume: -5, attack: 0.001, release: 0.25 },
  pad_low: { volume: -14, attack: 0.35, release: 1.5 },
  pad_high: { volume: -15, attack: 0.35, release: 1.5 },
  bells: { volume: -8, attack: 0.001, release: 0.9 },
  viola: { volume: -4, attack: 0.02, release: 0.5 },
  harp: { volume: -7, attack: 0.005, release: 1.8 },
  oboe: { volume: -4, attack: 0.03, release: 0.7 },
  clarinet: { volume: -4, attack: 0.02, release: 0.6 },
  bassoon: { volume: -5, attack: 0.03, release: 0.6 },
  horn: { volume: -6, attack: 0.05, release: 0.8 },
  trumpet: { volume: -6, attack: 0.02, release: 0.5 },
  trombone: { volume: -6, attack: 0.03, release: 0.6 },
  tuba: { volume: -7, attack: 0.03, release: 0.7 },
  piano: { volume: -7, attack: 0.001, release: 1.2 },
};

let reverb!: Tone.Reverb;
let limiter!: Tone.Limiter;
/** Master gain before the reverb: everything routes through it so one
 *  mute() silences every scheduled event, including ones already on the
 *  Tone clock that cannot be unscheduled. */
let master!: Tone.Gain;
const samplers: Partial<Record<MusicianId, Tone.Sampler>> = {};
let kick!: Tone.MembraneSynth;
let snare!: Tone.NoiseSynth;
let hats!: Tone.NoiseSynth;
let synthLead!: Tone.PolySynth;
let kazoo!: Tone.PolySynth;
let timpani!: Tone.MembraneSynth;

let ready = false;
let starting: Promise<void> | null = null;

// the note each melodic member is currently sounding (improv bookkeeping, per member id)
const sounding: Record<string, string> = {};

export function init(onProgress?: (loaded: number, total: number) => void): Promise<void> {
  if (ready) return Promise.resolve();
  if (!starting) starting = doInit(onProgress);
  return starting;
}

async function doInit(onProgress?: (loaded: number, total: number) => void): Promise<void> {
  const total = SAMPLED.length + 1; // samplers + reverb
  let loaded = 0;
  const bump = () => {
    loaded += 1;
    onProgress?.(loaded, total);
  };

  const res = await fetch("/samples/manifest.json");
  if (!res.ok) throw new Error(`failed to load /samples/manifest.json (${res.status})`);
  const manifest = (await res.json()) as Manifest;

  // Master chain: everything -> master gain -> shared reverb -> limiter ->
  // Destination. The master gain lets the conductor mute the whole band in
  // ~30 ms, even for events already scheduled on the Tone clock.
  reverb = new Tone.Reverb({ decay: 2.4, preDelay: 0.03 });
  reverb.wet.value = 0.25;
  limiter = new Tone.Limiter(-1);
  master = new Tone.Gain(1);
  master.connect(reverb);
  reverb.connect(limiter);
  limiter.toDestination();

  kick = new Tone.MembraneSynth({
    volume: -7,
    pitchDecay: 0.04,
    octaves: 6,
    oscillator: { type: "sine" },
    envelope: { attack: 0.001, decay: 0.45, sustain: 0.01, release: 1.2 },
  });
  kick.connect(master);

  snare = new Tone.NoiseSynth({
    volume: -8,
    noise: { type: "white" },
    envelope: { attack: 0.001, decay: 0.18, sustain: 0 },
  });
  snare.connect(master);

  const hatsFilter = new Tone.Filter(7000, "highpass");
  hats = new Tone.NoiseSynth({
    volume: -13,
    noise: { type: "white" },
    envelope: { attack: 0.001, decay: 0.06, sustain: 0 },
  });
  hats.connect(hatsFilter);
  hatsFilter.connect(master);

  synthLead = new Tone.PolySynth(Tone.Synth, {
    volume: -11,
    oscillator: { type: "square" },
    envelope: { attack: 0.005, decay: 0.18, sustain: 0.08, release: 0.25 },
  });
  synthLead.connect(master);

  // Timpani: a membrane tuned to the note pitch, longer ring than the kick.
  timpani = new Tone.MembraneSynth({
    volume: -6,
    pitchDecay: 0.05,
    octaves: 1.5,
    oscillator: { type: "sine" },
    envelope: { attack: 0.001, decay: 0.9, sustain: 0.001, release: 1.5 },
  });
  timpani.connect(reverb);

  const kazooVibrato = new Tone.Vibrato(6, 0.2);
  kazoo = new Tone.PolySynth(Tone.Synth, {
    volume: -11,
    oscillator: { type: "sawtooth" },
    envelope: { attack: 0.01, decay: 0.08, sustain: 0.25, release: 0.2 },
  });
  kazoo.connect(kazooVibrato);
  kazooVibrato.connect(master);

  const samplerLoads: Promise<void>[] = [];
  for (const id of SAMPLED) {
    const entry = manifest[id];
    if (!entry) throw new Error(`manifest is missing ${id}`);
    const urls: Record<string, string> = {};
    for (const [note, file] of Object.entries(entry.notes)) {
      urls[note] = `/samples/${entry.dir}/${file}`;
    }
    const tune = SAMPLER_TUNING[id];
    let settle: () => void = () => {};
    const loadDone = new Promise<void>((resolve) => {
      settle = resolve;
    });
    const sampler = new Tone.Sampler({
      urls,
      volume: tune.volume,
      attack: tune.attack,
      release: tune.release,
      onload: () => {
        bump();
        settle();
      },
      onerror: (err: Error) => {
        console.warn(`sample load failed for ${id}: ${err.message}`);
        bump();
        settle();
      },
    });
    samplers[id] = sampler;
    sampler.connect(master);
    samplerLoads.push(loadDone);
  }

  await Promise.all([...samplerLoads, reverb.ready.then(() => bump())]);
  ready = true;
}

function melodicAttack(instrument: InstrumentId, note: string, time: number, velocity: number): void {
  const sampler = samplers[instrument];
  if (sampler) sampler.triggerAttack(note, time, velocity);
  else if (instrument === "synth") synthLead.triggerAttack(note, time, velocity);
  else if (instrument === "kazoo") kazoo.triggerAttack(note, time, velocity);
  else if (instrument === "timpani") timpani.triggerAttack(note, time, velocity);
}

function melodicRelease(memberId: string, instrument: InstrumentId, time: number): void {
  const note = sounding[memberId];
  if (!note) return;
  const sampler = samplers[instrument];
  if (sampler) sampler.triggerRelease(note, time);
  else if (instrument === "synth") synthLead.triggerRelease(note, time);
  else if (instrument === "kazoo") kazoo.triggerRelease(note, time);
  delete sounding[memberId];
}

// Safety net: monophonic voices must have strictly increasing start times.
// If a new start is <= the last one, nudge it to last + 0.001 s.
const lastStart: Partial<Record<MusicianId, number>> = {};
function strictStart(voice: MusicianId, time: number): number {
  const last = lastStart[voice];
  const t = last !== undefined && time <= last ? last + 0.001 : time;
  lastStart[voice] = t;
  return t;
}

function drumHit(id: MusicianId, accent: boolean, time: number, velocity: number): void {
  const t = strictStart(id, time);
  if (id === "kick") kick.triggerAttackRelease("C1", 0.3, t, velocity);
  else if (id === "snare") snare.triggerAttack(t, velocity);
  else hats.triggerAttack(t, id === "hats" && !accent ? velocity * 0.6 : velocity);
}

/** Play one decided quarter-note beat. `time` is Tone time in seconds. */
export function playBeat(beat: Beat, time: number): void {
  if (!ready) return;
  const eighth = 30 / beat.tempo;
  // members hitting the same drum at the same time sound as one hit; an
  // accent by any member wins
  const drumHits = new Map<string, { id: MusicianId; t: number; accent: boolean }>();
  for (const [memberId, entry] of Object.entries(beat.notes)) {
    const play = entry?.play;
    if (!play) continue;
    const m = MUSICIANS.find((x) => x.id === entry.instrument);
    if (!m) continue;
    if (m.kind === "drums") {
      for (let i = 0; i < 2 && i < play.length; i++) {
        const ch = play[i];
        if (ch !== "x" && ch !== "X") continue;
        const t = time + i * eighth;
        const accent = ch === "X";
        const key = `${m.id}:${t}`;
        const merged = drumHits.get(key);
        if (merged) merged.accent = merged.accent || accent;
        else drumHits.set(key, { id: m.id, t, accent });
      }
    } else if (play === "hold") {
      // keeps sounding
    } else if (play === "rest") {
      melodicRelease(memberId, m.id, time);
    } else {
      if (sounding[memberId] && sounding[memberId] !== play) melodicRelease(memberId, m.id, time);
      melodicAttack(m.id, play, time, 0.9);
      sounding[memberId] = play;
    }
  }
  for (const hit of drumHits.values()) {
    drumHit(hit.id, hit.accent, hit.t, hit.accent ? 1 : 0.8);
  }
}

/** Play a list of note events (score mode). `time` is the start of the group. */
export function playEvents(events: NoteEvent[], time: number): void {
  if (!ready) return;
  // same as playBeat: one trigger per drum per slot, accent if any member accented
  const drumHits = new Map<string, { instrument: InstrumentId; t: number; accent: boolean; velocity: number }>();
  for (const ev of events) {
    const t = time + ev.offsetS;
    if (ev.note === "hit" || ev.note === "accent") {
      const accent = ev.note === "accent";
      const key = `${ev.instrument}:${t}`;
      const merged = drumHits.get(key);
      if (merged) merged.accent = merged.accent || accent;
      else drumHits.set(key, { instrument: ev.instrument, t, accent, velocity: ev.velocity });
    } else if (ev.durS > 0) {
      const sampler = samplers[ev.instrument];
      if (sampler) sampler.triggerAttackRelease(ev.note, ev.durS, t, ev.velocity);
      else if (ev.instrument === "synth") synthLead.triggerAttackRelease(ev.note, ev.durS, t, ev.velocity);
      else if (ev.instrument === "kazoo") kazoo.triggerAttackRelease(ev.note, ev.durS, t, ev.velocity);
      else if (ev.instrument === "timpani") timpani.triggerAttackRelease(ev.note, ev.durS, t, ev.velocity);
    }
  }
}

/** Mute the whole band now: the master gain ramps to 0 over 0.03 s.
 *  Already-scheduled events still fire, but inaudibly. */
export function mute(): void {
  if (!ready) return;
  master.gain.cancelScheduledValues(0);
  master.gain.rampTo(0, 0.03);
}

/** Unmute the band: the master gain is back at 1 at `atTime`. */
export function unmute(atTime: number): void {
  if (!ready) return;
  master.gain.cancelScheduledValues(0);
  master.gain.setValueAtTime(0, Tone.now());
  master.gain.rampTo(1, 0.03, atTime);
}

/** Release everything now and forget what is sounding. */
export function stopAll(): void {
  if (!ready) return;
  const now = Tone.now();
  for (const id of MUSICIAN_IDS) {
    const sampler = samplers[id];
    if (sampler) sampler.releaseAll(now);
    if (id === "kick") kick.triggerRelease(strictStart("kick", now));
    else if (id === "snare") snare.triggerRelease(strictStart("snare", now));
    else if (id === "hats") hats.triggerRelease(strictStart("hats", now));
    else if (id === "synth") synthLead.releaseAll(now);
    else if (id === "kazoo") kazoo.releaseAll(now);
    else if (id === "timpani") timpani.triggerRelease(strictStart("timpani", now));
  }
  for (const id of Object.keys(sounding)) delete sounding[id];
}

export function isReady(): boolean {
  return ready;
}

// When Vite hot-reloads this module the old copy keeps its scheduled events
// on the shared Tone clock; silence it as it goes away (ghost notes).
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    mute();
    stopAll();
  });
}
