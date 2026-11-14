// Pure trigger logic for the crash cinematic. No React, no DOM, no store import: unit-testable in plain node.
import type { GameEvent } from "@vice/contracts";

export type CaptureEvent = GameEvent<"CAPTURE_SUCCEEDED">;
export type HaltEvent = GameEvent<"LISTING_HALTED">;

/** One ticker crash: the capture that caused it and the halt that confirmed V$0. */
export interface CrashTrigger {
  /** `${runId}:${characterId}`. A character is captured at most once, so this de-duplicates the pair. */
  key: string;
  runId: string;
  characterId: string;
  ticker: string;
  /** serverSequence of the earliest event of the pair (the capture, when it is in the log). */
  sequence: number;
  capture: CaptureEvent | null;
  halt: HaltEvent | null;
}

const isCapture = (e: GameEvent): e is CaptureEvent => e.type === "CAPTURE_SUCCEEDED";
const isHalt = (e: GameEvent): e is HaltEvent => e.type === "LISTING_HALTED";

/** Highest serverSequence in a log, or -Infinity for an empty one. Use it as the `afterSequence` cursor. */
export function highestSequence(events: readonly GameEvent[]): number {
  let max = Number.NEGATIVE_INFINITY;
  for (const e of events) if (e.serverSequence > max) max = e.serverSequence;
  return max;
}

/**
 * Picks the ticker crashes that became visible after `afterSequence`.
 *
 * - Only CAPTURE_SUCCEEDED and LISTING_HALTED count; they are paired by run + characterId.
 * - A crash is returned when at least one event of its pair has serverSequence > afterSequence. Its partner
 *   is attached even when it sits at or before the cursor, so a halt that lands one store update after its
 *   capture still carries the capture payload (callers de-duplicate on `key`).
 * - Captures of characters that have no ticker (hunters) are never returned: nothing is listed, nothing crashes.
 * - Result is ordered by `sequence`. The input order does not matter and the input is not mutated.
 */
export function pickCrashTriggers(events: readonly GameEvent[], afterSequence: number): CrashTrigger[] {
  const slots = new Map<string, { runId: string; characterId: string; capture: CaptureEvent | null; halt: HaltEvent | null; fresh: boolean }>();
  for (const e of events) {
    const capture = isCapture(e) ? e : null;
    const halt = isHalt(e) ? e : null;
    if (!capture && !halt) continue;
    const characterId = (capture ?? halt)!.payload.characterId;
    const key = `${e.runId}:${characterId}`;
    let slot = slots.get(key);
    if (!slot) { slot = { runId: e.runId, characterId, capture: null, halt: null, fresh: false }; slots.set(key, slot); }
    // Keep the earliest of each kind if a log ever repeats one.
    if (capture && (!slot.capture || capture.serverSequence < slot.capture.serverSequence)) slot.capture = capture;
    if (halt && (!slot.halt || halt.serverSequence < slot.halt.serverSequence)) slot.halt = halt;
    if (e.serverSequence > afterSequence) slot.fresh = true;
  }
  const out: CrashTrigger[] = [];
  for (const [key, s] of slots) {
    if (!s.fresh) continue;
    const ticker = s.halt?.payload.ticker ?? s.capture?.payload.ticker ?? null;
    if (!ticker) continue;
    const sequence = Math.min(s.capture?.serverSequence ?? Infinity, s.halt?.serverSequence ?? Infinity);
    out.push({ key, runId: s.runId, characterId: s.characterId, ticker, sequence, capture: s.capture, halt: s.halt });
  }
  return out.sort((a, b) => a.sequence - b.sequence);
}

/** The four world collections that a full snapshot always replaces and a live event never touches. */
export interface WorldRefs { characters: unknown; npcs: unknown; missions: unknown; listings: unknown }

/**
 * True when one store update swapped the whole world (join, resync, respawn or reset snapshot) rather than
 * appending a live event. Events that arrive this way are history: they must never start a cinematic.
 */
export function isSnapshotUpdate(next: WorldRefs, prev: WorldRefs): boolean {
  return next.characters !== prev.characters && next.npcs !== prev.npcs && next.missions !== prev.missions && next.listings !== prev.listings;
}

/** Newest rule-clock time in a log (0 for an empty one). */
export function newestSimTime(events: readonly GameEvent[]): number {
  let max = 0;
  for (const e of events) if (e.simTimeMs > max) max = e.simTimeMs;
  return max;
}

/**
 * Second guard against replaying history, entirely in the server's rule clock (client clock skew cannot matter).
 * An event is stale when
 *  - the log already holds events more than `maxAgeMs` newer than it (a live event is the newest thing in the
 *    log the moment it arrives), or
 *  - the latest server time known from world ticks is more than `maxAgeMs` past it. This second test is only
 *    used when that tick clock is evidently the same clock as the log (within a minute of its newest event),
 *    so a clock mismatch can never silence a live capture.
 * Unknown clocks (0 / NaN, as in bare mocks) never count as stale.
 */
export function isStaleEvent(event: Pick<GameEvent, "simTimeMs">, newestSimTimeMs: number, serverNowMs: number, maxAgeMs = 10_000): boolean {
  if (!(event.simTimeMs > 0)) return false;
  if (newestSimTimeMs > 0 && newestSimTimeMs - event.simTimeMs > maxAgeMs) return true;
  const sameClock = serverNowMs > 0 && newestSimTimeMs > 0 && Math.abs(serverNowMs - newestSimTimeMs) < 60_000;
  return sameClock && serverNowMs - event.simTimeMs > maxAgeMs;
}
