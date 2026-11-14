// The conductor: the clock. Runs the band ahead of playback (at most 2 beats
// decided, at most 2 Drex rounds in flight), schedules the audio with Tone and
// the UI updates with Tone.getDraw() at the moment each beat sounds.

import * as Tone from "tone";
import { apiCounter } from "./apiCounter.ts";
import * as audio from "./audio.ts";
import { Band, type BeatDecision, Determinism, MOODS } from "./band.ts";
import { type Answer, decide, TIMEOUT_MS } from "./drex.ts";
import { type InstrumentId, MOOD_NAMES, type MoodName, MUSICIANS } from "./musicians.ts";
import { type Directions, loadDefaultDirections } from "./ndi.ts";
import { classicRoster, type Member } from "./roster.ts";
import {
  type BarRender,
  buildBarRound,
  loadDefaultScore,
  ndiIndexForScoreSection,
  renderBar,
  type ScoreData,
  sectionForBar,
} from "./score.ts";
import { fillMissingParts } from "./scoreParts.ts";
import { type Segment, timeline } from "./timeline.ts";

/** How deterministic a member has been: does it keep playing the same note? */
export type Det = { repeatRate: number; meanTop: number; distinct: number };

export type PanelUpdate = {
  /** member id (classic roster: the instrument id) */
  musician: string;
  /** the member's instrument */
  instrument: InstrumentId;
  play: string;
  probs: Record<string, number>;
  direction?: string;
  det: Det;
};

export type Stats = {
  decisions: number;
  apiCalls: number;
  decisionsPerSec: number;
  avgLatencyMs: number;
  cost: number;
  timeouts: number;
  tempo: number;
  bar: number;
  beat: number;
};

export type SectionInfo = {
  name: string;
  quote: string;
  summary?: string;
  /** 1-based index of the section within its source (score sections, or the uploaded document) */
  index?: number;
  /** total number of sections in the source */
  total?: number;
} | null;

export type ConductorEvent =
  | { type: "beat"; updates: PanelUpdate[]; stats: Stats }
  | { type: "section"; section: SectionInfo }
  | { type: "mood"; preset: MoodName; probs?: Record<MoodName, number>; text?: string }
  | { type: "loading"; loaded: number; total: number }
  | { type: "state"; running: boolean; mode: "improv" | "score" | null; paused?: boolean }
  | { type: "error"; message: string };

const MAX_ROUNDS_IN_FLIGHT = 3;
const TAIL_LATENCY_S = 1.5; // Drex p95 is about 1.25 s; decide this far ahead
const TICK_MS = 50;
const START_DELAY_S = 0.4;
const MIN_LEAD_S = 0.05; // a decision that lands with less lead time is shifted
const BEATS_PER_SECTION = 16; // improv: one NDI section per 4-bar phrase
const RELEASE_TAIL_S = 1.5; // longest sampler release; kept off the re-start anchor

/** The default mood (the band's house style): what a fresh run starts in. */
export const DEFAULT_MOOD: MoodName = "imperial";

/** Decide up to this many units ahead of playback: at least 2, enough to cover a slow Drex. */
function lookaheadFor(unitSeconds: number): number {
  return Math.max(2, Math.ceil(TAIL_LATENCY_S / Math.max(unitSeconds, 1e-6)));
}

type Listener = (e: ConductorEvent) => void;

type QueuedBeat = {
  index: number;
  time: number; // Tone context seconds when the beat sounds
  decision: BeatDecision;
  updates: PanelUpdate[];
};

/** An improv beat that is decided but not yet scheduled (waiting its turn). */
type ScheduleDecision = {
  index: number;
  decision: BeatDecision;
  updates: PanelUpdate[];
};

/** A score bar that is decided but not yet scheduled (waiting its turn). */
type ScoreDecision = {
  index: number;
  render: BarRender;
  updates: PanelUpdate[];
  stats: Stats;
};

type ScoreSchedule = {
  index: number;
  time: number;
  updates: PanelUpdate[];
  stats: Stats;
};

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function argmaxName(probs: Record<string, number>): MoodName {
  let best: MoodName = "calm";
  let bestP = -1;
  for (const name of MOOD_NAMES) {
    const p = probs[name] ?? 0;
    if (p > bestP) {
      bestP = p;
      best = name;
    }
  }
  return best;
}

class Conductor {
  private listeners = new Set<Listener>();
  private band: Band | null = null;
  private mood: MoodName = DEFAULT_MOOD;
  private directions: Directions | null = null;
  private members: Member[] = classicRoster();
  /** per-member determinism, tracked across the run */
  private det = new Map<string, Determinism>();
  private running = false;
  private paused = false;
  /** bumped on stop/pause: stale Draw callbacks and late Drex results see it */
  private generation = 0;
  /** Tone time by which all scheduled audio (incl. release tails) is done */
  private tailEnd = 0;
  /** ms spent paused, excluded from decisionsPerSec */
  private pausedMsAccum = 0;
  private pausedAtMs = 0;
  /** the current directions came from setDirections() (an uploaded document) */
  private directionsFromUpload = false;
  /** last improv section index the UI was told about */
  private stateSection = -1;

  private initPromise: Promise<void> | null = null;
  private timer: number | null = null;
  private aborter = new AbortController();
  private lastTime = 0;
  private nextIndex = 0;
  /** decided beats not yet scheduled: index -> decision, so they play in order */
  private done = new Map<number, ScheduleDecision>();
  private nextToSchedule = 0;
  private queue: QueuedBeat[] = [];
  private inflight = new Set<number>();
  /** the last real note each melodic member played, for labeling holds (member id -> note) */
  private lastNote = new Map<string, string>();

  // score mode
  private score: ScoreData | null = null;
  private mode: "improv" | "score" | null = null;
  private scoreQueue: ScoreSchedule[] = [];
  private scoreDone = new Map<number, ScoreDecision>();
  private scoreNextToSchedule = 0;
  private scoreInflight = new Set<number>();
  private scoreNext = 0;
  /** score bars that got a rendered decision (pause resumes at the first gap) */
  private scoreApplied = new Set<number>();
  private scoreLastTime = 0;
  private scoreLastChoices: Record<string, string> = {};
  private scoreSectionIndex = -1;

  private startTime = 0;
  private decisions = 0; // one per question answered
  private rounds = 0; // one Drex round per beat/bar; avgLatencyMs is per round
  private latencySum = 0;
  private cost = 0;
  private timeouts = 0;
  private sectionIndex = 0;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  private emit(e: ConductorEvent): void {
    for (const fn of this.listeners) fn(e);
  }

  /** Loads the samples once; idempotent (React StrictMode runs effects twice). */
  init(): Promise<void> {
    if (!this.initPromise) {
      // the UI starts with no mood lit; name the conductor's default mood so it
      // lights up (subscribers attach before init is called)
      this.emit({ type: "mood", preset: this.mood });
      this.initPromise = audio
        .init((loaded, total) => this.emit({ type: "loading", loaded, total }))
        .catch((err) => {
          this.emit({ type: "error", message: messageOf(err) });
          throw err;
        });
    }
    return this.initPromise;
  }

  /**
   * The roster that plays: one Drex agent per member. Only while stopped;
   * the default is the classic 11-musician band (member id = instrument id).
   */
  setRoster(members: Member[]): void {
    if (this.running) {
      this.emit({ type: "error", message: "stop the orchestra before changing the roster" });
      return;
    }
    this.members = members;
  }

  /** Start improvising in the current mood; the click calls Tone.start().
   *  While paused this is a full fresh start. */
  async start(): Promise<void> {
    this.stop();
    try {
      await this.init();
      await Tone.start();
    } catch (err) {
      this.emit({ type: "error", message: messageOf(err) });
      throw err;
    }
    this.aborter = new AbortController();
    this.band = new Band(this.mood, this.members);
    this.det.clear();
    // keep the UI's highlight in sync with the mood this run plays (a fresh
    // conductor is on the default)
    this.emit({ type: "mood", preset: this.mood });
    // La donna's default directions are score-only; improv only uses
    // directions the user uploaded
    if (!this.directionsFromUpload) this.directions = null;
    this.running = true;
    this.paused = false;
    this.mode = "improv";
    this.queue = [];
    this.inflight.clear();
    this.nextIndex = 0;
    this.done.clear();
    this.nextToSchedule = 0;
    this.lastNote.clear();
    this.lastTime = this.anchor(); // anchor past the previous run's scheduled audio
    audio.unmute(this.lastTime);
    this.startTime = Date.now();
    this.pausedMsAccum = 0;
    this.decisions = 0;
    this.rounds = 0;
    this.latencySum = 0;
    this.cost = 0;
    this.timeouts = 0;
    this.stateSection = -1;
    this.applySection(0);
    this.tick();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    this.emit({ type: "state", running: true, mode: "improv", paused: false });
  }

  stop(): void {
    this.halt();
    this.running = false;
    this.paused = false;
    this.mode = null;
    this.queue = [];
    this.inflight.clear();
    this.done.clear();
    this.nextToSchedule = 0;
    this.scoreQueue = [];
    this.scoreInflight.clear();
    this.scoreDone.clear();
    this.scoreNextToSchedule = 0;
    this.emit({ type: "state", running: false, mode: null, paused: false });
  }

  /** Silence the band and kill the clock, at once:
   *  - the master gain mutes, so events already on the Tone clock (which
   *    cannot be unscheduled) play on inaudibly;
   *  - sounding notes are released;
   *  - pending Draw callbacks are cancelled and, as a second guard, every
   *    callback and late Drex result checks the generation this bumps. */
  private halt(): void {
    this.generation += 1;
    this.stopScheduler();
    this.aborter.abort();
    audio.mute();
    audio.stopAll();
    Tone.getDraw().cancel(0);
    timeline.clear();
  }

  /** Pause: silence the band and stop the clock, but keep the Band (history,
   *  mood, NDI directions), the roster, the score and the stats, so
   *  resume() continues where we left off. */
  pause(): void {
    if (!this.running || this.paused) return;
    this.paused = true;
    this.pausedAtMs = Date.now();
    if (this.mode === "score" && this.score) {
      // continue at the first bar that never got a rendered decision
      let next = 0;
      while (next < this.score.bars.length && this.scoreApplied.has(next)) next++;
      this.scoreNext = next;
      this.scoreDone.clear();
      this.scoreNextToSchedule = next;
    }
    // improv: this.nextIndex already points at the first un-decided beat
    this.halt();
    this.queue = [];
    this.inflight.clear();
    this.done.clear();
    this.nextToSchedule = this.nextIndex;
    this.scoreQueue = [];
    this.scoreInflight.clear();
    this.scoreDone.clear();
    this.scoreNextToSchedule = this.scoreNext;
    this.emit({ type: "state", running: false, mode: this.mode, paused: true });
  }

  /** Resume a paused run: re-anchor the clock and restart the scheduler from
   *  the remembered index. Only while paused. */
  resume(): void {
    if (!this.paused || !this.running) return;
    this.generation += 1;
    this.aborter = new AbortController();
    this.paused = false;
    this.pausedMsAccum += Date.now() - this.pausedAtMs;
    const anchor = this.anchor();
    audio.unmute(anchor);
    if (this.mode === "score") this.scoreLastTime = anchor;
    else this.lastTime = anchor;
    this.tick();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    this.emit({ type: "state", running: true, mode: this.mode, paused: false });
  }

  /** Where the next fresh unit may sound: at least 0.4 s away, and after all
   *  audio the previous run left scheduled on the Tone clock. */
  private anchor(): number {
    return Math.max(Tone.now() + START_DELAY_S, this.tailEnd + MIN_LEAD_S);
  }

  private stopScheduler(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Run a round of the clock: play due units and start rounds up to the limits. */
  private tick(): void {
    if (!this.running || this.paused) return;
    if (this.mode === "score" && this.score) this.scoreTick();
    else this.improvTick();
  }

  private improvTick(): void {
    const now = Tone.now();
    // beats whose time has passed can leave the queue (their audio is scheduled)
    this.queue = this.queue.filter((b) => b.time > now + 0.001);
    const beatS = 60 / MOODS[this.mood].tempo;
    const lookahead = lookaheadFor(beatS);
    if (this.band) this.band.decidedAhead = lookahead;
    while (this.queue.length < lookahead && this.inflight.size < MAX_ROUNDS_IN_FLIGHT) {
      const index = this.nextIndex++;
      const gen = this.generation;
      this.inflight.add(index);
      this.decideBeatRound(index)
        .catch((err) => {
          // a round from a stopped/paused run is already dead
          if (this.generation === gen && !this.aborter.signal.aborted) {
            console.error(err);
            this.emit({ type: "error", message: messageOf(err) });
          }
        })
        .finally(() => {
          this.inflight.delete(index);
        });
    }
  }

  /** Ask the band for one beat and schedule its audio and UI update. */
  private async decideBeatRound(index: number): Promise<void> {
    const band = this.band;
    if (!band) return;
    // this run's own signal and generation: start()/perform()/resume()
    // replace this.aborter, so checking this.aborter would let a round
    // from the previous run through
    const gen = this.generation;
    const signal = this.aborter.signal;
    let decision: BeatDecision;
    try {
      decision = await band.decideBeat((state, questions) => decide(state, questions, { signal }));
    } catch (err) {
      if (signal.aborted || this.generation !== gen) return;
      throw err;
    }
    if (!this.running || this.generation !== gen) return;

    // one decision per question answered in the round (one per member)
    this.decisions += decision.decisions;
    this.rounds += 1;
    this.latencySum += decision.latencyMs;
    this.cost += decision.cost;
    if (decision.timedOut) this.timeouts += 1;

    const sectionIndex = this.sectionForBeat(index);
    if (sectionIndex !== this.sectionIndex) this.applySection(sectionIndex);

    // Drex rounds finish in arbitrary order; hold each decision by its beat
    // index and schedule strictly in index order (see scheduleDecision).
    const updates = this.panelUpdates(decision);
    this.done.set(index, { index, decision, updates });
    while (this.done.has(this.nextToSchedule)) {
      const d = this.done.get(this.nextToSchedule)!;
      this.done.delete(this.nextToSchedule);
      this.scheduleDecision(d);
      this.nextToSchedule++;
    }
  }

  /** Schedule one decided beat's audio and UI update; called in index order. */
  private scheduleDecision(d: ScheduleDecision): void {
    const { index, decision, updates } = d;
    const gen = this.generation;
    // beats are scheduled strictly in order: n = n-1 + 60/tempo. A decision that
    // arrives too late is shifted forward; every later beat follows it.
    const computed = this.lastTime + 60 / decision.tempo;
    const time = Math.max(computed, Tone.now() + MIN_LEAD_S);
    this.lastTime = time;
    this.tailEnd = Math.max(this.tailEnd, time + 60 / decision.tempo + RELEASE_TAIL_S);

    timeline.add(this.beatSegments(decision, time));
    const notes: Partial<Record<string, { play: string; instrument: InstrumentId }>> = {};
    for (const member of this.members) {
      notes[member.id] = { play: decision.notes[member.id].play, instrument: member.instrument };
    }
    audio.playBeat({ tempo: decision.tempo, notes }, time);
    this.queue.push({ index, time, decision, updates });
    Tone.getDraw().schedule(() => {
      if (this.generation === gen) this.emitBeat(decision, updates);
    }, time);
  }

  /** One member's played note or drum pattern this beat, null when it rested. */
  private playedNote(member: Member, d: { play: string; top: string; probs: Record<string, number> }): string | null {
    const isDrums = MUSICIANS.find((m) => m.id === member.instrument)?.kind === "drums";
    if (isDrums) return d.play === ".." ? null : d.play;
    if (d.play === "rest") return null;
    if (d.play === "hold") return this.lastNote.get(member.id) ?? null;
    return d.play;
  }

  private emitBeat(decision: BeatDecision, updates: PanelUpdate[]): void {
    if (!this.running) return;
    const elapsedSec = this.elapsedSec();
    const stats: Stats = {
      decisions: this.decisions,
      apiCalls: apiCounter.calls,
      decisionsPerSec: elapsedSec > 0 ? this.decisions / elapsedSec : 0,
      avgLatencyMs: this.rounds > 0 ? this.latencySum / this.rounds : 0,
      cost: this.cost,
      timeouts: this.timeouts,
      tempo: decision.tempo,
      bar: decision.bar,
      beat: decision.beatInBar,
    };
    this.emit({ type: "beat", updates, stats });
  }

  /** Lane feed: one segment per played option (drums one per eighth). Rests add nothing. */
  private beatSegments(decision: BeatDecision, time: number): Segment[] {
    const beatS = 60 / decision.tempo;
    const out: Segment[] = [];
    for (const member of this.members) {
      const m = MUSICIANS.find((x) => x.id === member.instrument)!;
      const d = decision.notes[member.id];
      const prob = d.probs[d.play] ?? 0;
      const isTop = d.play === d.top;
      if (m.kind === "drums") {
        for (let i = 0; i < 2 && i < d.play.length; i++) {
          const ch = d.play[i];
          if (ch !== "x" && ch !== "X") continue;
          out.push({
            musician: member.id,
            instrument: m.id,
            kind: ch === "X" ? "accent" : "hit",
            label: ch,
            start: time + (i * beatS) / 2,
            dur: beatS / 2,
            prob,
            isTop,
          });
        }
      } else if (d.play === "rest") {
        // rests add nothing
      } else if (d.play === "hold") {
        const label = this.lastNote.get(member.id);
        if (label)
          out.push({
            musician: member.id,
            instrument: m.id,
            kind: "hold",
            label,
            start: time,
            dur: beatS,
            prob,
            isTop,
          });
      } else {
        this.lastNote.set(member.id, d.play);
        out.push({
          musician: member.id,
          instrument: m.id,
          kind: "note",
          label: d.play,
          start: time,
          dur: beatS,
          prob,
          isTop,
        });
      }
    }
    return out;
  }

  private panelUpdates(decision: BeatDecision): PanelUpdate[] {
    const dirs = this.currentDirections();
    return this.members.map((member) => {
      const d = decision.notes[member.id];
      const det = this.detFor(member.id);
      const note = this.playedNote(member, d);
      det.observe(note, d.probs[d.top] ?? 0);
      return {
        musician: member.id,
        instrument: member.instrument,
        play: d.play,
        probs: d.probs,
        direction:
          dirs[member.id] || (member.id === member.instrument ? undefined : dirs[member.instrument] || undefined),
        det: det.stats(),
      };
    });
  }

  private detFor(memberId: string): Determinism {
    let det = this.det.get(memberId);
    if (!det) {
      det = new Determinism();
      this.det.set(memberId, det);
    }
    return det;
  }

  private currentDirections(): Partial<Record<string, string>> {
    return this.directions?.sections[this.sectionIndex]?.directions ?? {};
  }

  /** Improv: NDI section i holds for a 4-bar phrase, cycling. */
  private sectionForBeat(index: number): number {
    const sections = this.directions?.sections.length ?? 0;
    if (sections === 0) return 0;
    return Math.floor(index / BEATS_PER_SECTION) % sections;
  }

  /** Point the band at NDI section i and tell the UI about it (improv).
   *  In improv the directions only exist when an upload supplied them, so a
   *  non-null section always carries its 1-based index and the total. */
  private applySection(i: number): void {
    this.sectionIndex = i;
    const sections = this.directions?.sections;
    const section = sections?.[i];
    this.band?.setMusicianDirections(section ? section.directions : {});
    if (i === this.stateSection) return;
    this.stateSection = i;
    this.emit({
      type: "section",
      section:
        section && sections
          ? {
              name: section.name,
              quote: section.quote,
              summary: section.summary,
              index: i + 1,
              total: sections.length,
            }
          : null,
    });
  }

  /** Switch the mood; held notes outside the new scale are released. */
  setMood(preset: MoodName): void {
    this.mood = preset;
    this.band?.setMood(preset);
    this.emit({ type: "mood", preset });
  }

  /** Drex picks the mood preset from a sentence; the top choice is applied. */
  async moodFromText(text: string): Promise<{ preset: MoodName; probs: Record<MoodName, number> }> {
    try {
      const criteria: Record<string, string> = {};
      for (const name of MOOD_NAMES) criteria[name] = `${name}: ${MOODS[name].feel}`;
      const res = await decide("You are choosing which mood preset an 11-musician band should play.", {
        mood: {
          type: "choice",
          instructions: `The request: "${text}". Pick the mood preset that fits it best.`,
          criteria,
        },
      });
      const raw = res.answers.mood?.probabilities ?? {};
      const probs = {} as Record<MoodName, number>;
      for (const name of MOOD_NAMES) {
        const v = raw[name];
        probs[name] = typeof v === "number" && Number.isFinite(v) ? v : 0;
      }
      const choice = res.answers.mood?.choice;
      const preset =
        choice && (MOOD_NAMES as readonly string[]).includes(choice) ? (choice as MoodName) : argmaxName(probs);
      this.setMood(preset);
      this.emit({ type: "mood", preset, probs, text });
      return { preset, probs };
    } catch (err) {
      this.emit({ type: "error", message: messageOf(err) });
      throw err;
    }
  }

  /** NDI directions for the musicians, from an uploaded document;
   *  the current section is applied at once. */
  setDirections(d: Directions): void {
    this.directions = d;
    this.directionsFromUpload = true;
    if (this.running && !this.paused) this.applySection(this.sectionForBeat(this.band?.index ?? 0));
  }

  /** A score to perform (e.g. from a MIDI upload); used until replaced. */
  setScore(score: ScoreData): void {
    this.score = fillMissingParts(score);
  }

  /** Perform a score: the one given, the last setScore()'d, or La donna è mobile. */
  async perform(scoreArg?: ScoreData): Promise<void> {
    this.stop();
    try {
      await this.init();
      await Tone.start();
    } catch (err) {
      this.emit({ type: "error", message: messageOf(err) });
      throw err;
    }
    this.aborter = new AbortController();
    let score: ScoreData | null = scoreArg ?? this.score;
    if (!score) {
      try {
        score = await loadDefaultScore(this.aborter.signal);
      } catch (err) {
        if (!this.aborter.signal.aborted) {
          this.emit({ type: "error", message: messageOf(err) });
          throw err;
        }
        return;
      }
    }
    // every score we perform gets parts for the full orchestra
    score = fillMissingParts(score);
    this.score = score;
    // the default directions belong to La donna only: for any other score,
    // play without directions unless an upload supplied them
    const isLaDonna = /donna/i.test(score.title);
    if (this.directions === null && !this.directionsFromUpload && isLaDonna) {
      try {
        this.directions = await loadDefaultDirections(this.aborter.signal);
      } catch (err) {
        if (!this.aborter.signal.aborted) {
          this.emit({ type: "error", message: messageOf(err) });
          throw err;
        }
        return;
      }
    }
    this.mode = "score";
    this.running = true;
    this.paused = false;
    this.scoreQueue = [];
    this.scoreInflight.clear();
    this.scoreNext = 0;
    this.scoreDone.clear();
    this.scoreNextToSchedule = 0;
    this.scoreApplied.clear();
    this.scoreLastChoices = {};
    this.scoreLastTime = this.anchor(); // anchor past the previous run's audio
    audio.unmute(this.scoreLastTime);
    this.startTime = Date.now();
    this.pausedMsAccum = 0;
    this.decisions = 0;
    this.rounds = 0;
    this.latencySum = 0;
    this.cost = 0;
    this.timeouts = 0;
    timeline.clear();
    this.scoreSectionIndex = sectionForBar(score, 0);
    this.emit({ type: "section", section: this.sectionInfoForBar(0) });
    this.tick();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    this.emit({ type: "state", running: true, mode: "score", paused: false });
  }

  /** Score mode: keep up to the lookahead decided ahead, at most 3 Drex rounds in flight. */
  private scoreTick(): void {
    const score = this.score;
    if (!score) return;
    const now = Tone.now();
    this.scoreQueue = this.scoreQueue.filter((b) => b.time > now + 0.001);
    const barS = (score.beats_per_bar * 60) / score.eighth_bpm;
    const lookahead = lookaheadFor(barS);
    while (
      this.scoreQueue.length < lookahead &&
      this.scoreInflight.size < MAX_ROUNDS_IN_FLIGHT &&
      this.scoreNext < score.bars.length
    ) {
      const index = this.scoreNext++;
      const gen = this.generation;
      this.scoreInflight.add(index);
      this.decideBarRound(index)
        .catch((err) => {
          // a round from a stopped/paused run is already dead
          if (this.generation === gen && !this.aborter.signal.aborted) {
            console.error(err);
            this.emit({ type: "error", message: messageOf(err) });
          }
        })
        .finally(() => {
          this.scoreInflight.delete(index);
        });
    }
    if (
      this.scoreNext >= score.bars.length &&
      this.scoreQueue.length === 0 &&
      this.scoreInflight.size === 0 &&
      this.scoreDone.size === 0
    ) {
      this.stop();
    }
  }

  /** Ask Drex one bar and schedule its events, timeline segments and UI update. */
  private async decideBarRound(index: number): Promise<void> {
    const score = this.score;
    if (!score) return;
    // this run's own signal and generation (see decideBeatRound)
    const gen = this.generation;
    const signal = this.aborter.signal;
    const round = buildBarRound(score, index, this.scoreNdiForBar(index), this.members, this.scoreLastChoices);
    let answers: Record<string, Answer> | null = null;
    let timedOut = false;
    let latencyMs = 0;
    let cost = 0;
    try {
      const res = await decide(round.state, round.questions, { signal });
      answers = res.answers;
      latencyMs = res.usage.latencyMs;
      cost = res.usage.cost;
    } catch {
      if (signal.aborted || this.generation !== gen) return;
      // a failed Drex round falls back exactly like a timeout: renderBar
      // plays the last choice (or the written part) and it counts in stats
      timedOut = true;
      latencyMs = TIMEOUT_MS;
    }
    if (!this.running || this.generation !== gen) return;

    // one decision per question answered in the bar round
    this.decisions += Object.keys(round.questions).length;
    this.rounds += 1;
    this.latencySum += latencyMs;
    this.cost += cost;
    if (timedOut) this.timeouts += 1;

    const render = renderBar(score, index, answers, this.members, this.scoreLastChoices);
    const lastChoices: Record<string, string> = {};
    for (const member of this.members) lastChoices[member.id] = render.choices[member.id].play;
    this.scoreLastChoices = lastChoices;

    // Drex rounds finish in arbitrary order; hold each bar by its index and
    // schedule strictly in index order (see scheduleScoreBar).
    const updates = this.scoreUpdates(index, render);
    const stats = this.statsFor(index + 1, score.eighth_bpm, score.beats_per_bar);
    this.scoreDone.set(index, { index, render, updates, stats });
    while (this.scoreDone.has(this.scoreNextToSchedule)) {
      const s = this.scoreDone.get(this.scoreNextToSchedule)!;
      this.scoreDone.delete(this.scoreNextToSchedule);
      this.scheduleScoreBar(s);
      this.scoreNextToSchedule++;
    }
  }

  /** Schedule one decided bar's audio, timeline and UI update; in index order. */
  private scheduleScoreBar(s: ScoreDecision): void {
    const { index, render, updates, stats } = s;
    const gen = this.generation;
    const score = this.score;
    if (!score) return;
    const barS = (score.beats_per_bar * 60) / score.eighth_bpm;
    const time = Math.max(this.scoreLastTime + barS, Tone.now() + MIN_LEAD_S);
    this.scoreLastTime = time;
    this.tailEnd = Math.max(this.tailEnd, time + barS + RELEASE_TAIL_S);

    audio.playEvents(render.events, time);
    timeline.add(this.scoreSegments(render, time));
    this.scoreQueue.push({ index, time, updates, stats });
    this.scoreApplied.add(index);
    Tone.getDraw().schedule(() => {
      if (this.generation === gen) this.emit({ type: "beat", updates, stats });
    }, time);

    const secIdx = sectionForBar(score, index);
    if (secIdx !== this.scoreSectionIndex) {
      this.scoreSectionIndex = secIdx;
      this.emit({ type: "section", section: this.sectionInfoForBar(index) });
    }
  }

  /** The NDI section (spread proportionally) that covers score bar index. */
  private scoreNdiForBar(
    index: number,
  ): { summary?: string; quote?: string; directions: Partial<Record<string, string>> } | null {
    const score = this.score;
    const ndi = this.directions;
    if (!score || !ndi) return null;
    const i = ndiIndexForScoreSection(score.sections.length, ndi.sections.length, sectionForBar(score, index));
    const s = ndi.sections[i];
    return s ? { summary: s.summary, quote: s.quote, directions: s.directions } : null;
  }

  private sectionInfoForBar(index: number): SectionInfo {
    const score = this.score;
    if (!score) return null;
    const i = sectionForBar(score, index);
    const sec = score.sections[i];
    const ndi = this.scoreNdiForBar(index);
    return {
      name: sec.name,
      quote: ndi?.quote ?? "",
      summary: ndi?.summary,
      index: i + 1,
      total: score.sections.length,
    };
  }

  private scoreUpdates(index: number, render: BarRender): PanelUpdate[] {
    const dirs = this.scoreNdiForBar(index)?.directions ?? {};
    return this.members.map((member) => {
      const m = MUSICIANS.find((x) => x.id === member.instrument)!;
      const c = render.choices[member.id];
      const firstNote = render.events.find((ev) => ev.musician === member.id && ev.note !== "hit")?.note;
      const det = this.detFor(member.id);
      const play = m.kind === "drums" ? c.play : (firstNote ?? "rest");
      const note = m.kind === "drums" ? (play === "..." ? null : c.play) : (firstNote ?? null);
      det.observe(note, c.probs[c.top] ?? 0);
      return {
        musician: member.id,
        instrument: m.id,
        play,
        probs: c.probs,
        direction:
          dirs[member.id] || (member.id === member.instrument ? undefined : dirs[member.instrument] || undefined),
        det: det.stats(),
      };
    });
  }

  private scoreSegments(render: BarRender, time: number): Segment[] {
    const out: Segment[] = [];
    for (const member of this.members) {
      const c = render.choices[member.id];
      const prob = c.probs[c.play] ?? 0;
      const isTop = c.play === c.top;
      for (const ev of render.events) {
        if (ev.musician !== member.id) continue;
        out.push({
          musician: member.id,
          instrument: ev.instrument,
          kind: ev.note === "hit" ? "hit" : "note",
          label: ev.note === "hit" ? "x" : ev.note,
          start: time + ev.offsetS,
          dur: ev.durS,
          prob,
          isTop,
        });
      }
    }
    return out;
  }

  /** Wall-clock playing time, with the time spent paused excluded. */
  private elapsedSec(): number {
    const now = Date.now();
    const pausedMs = this.pausedMsAccum + (this.paused ? now - this.pausedAtMs : 0);
    return Math.max(0, (now - this.startTime - pausedMs) / 1000);
  }

  private statsFor(bar: number, tempo: number, beat: number): Stats {
    const elapsedSec = this.elapsedSec();
    return {
      decisions: this.decisions,
      apiCalls: apiCounter.calls,
      decisionsPerSec: elapsedSec > 0 ? this.decisions / elapsedSec : 0,
      avgLatencyMs: this.rounds > 0 ? this.latencySum / this.rounds : 0,
      cost: this.cost,
      timeouts: this.timeouts,
      tempo,
      bar,
      beat,
    };
  }
}

export const conductor: InstanceType<typeof Conductor> = new Conductor();

// When Vite hot-reloads this module the old conductor keeps its interval and
// keeps playing, invisible to the UI; make the old one stop itself.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    conductor.stop();
  });
}
