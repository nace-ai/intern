// Run from the repo root: npx vitest run apps/web/src/components/wow/CrashCinematicTriggers.test.ts
import { describe, expect, it } from "vitest";
import type { EventPayloads, EventType, GameEvent } from "@vice/contracts";
import { highestSequence, isSnapshotUpdate, isStaleEvent, newestSimTime, pickCrashTriggers } from "./CrashCinematicTriggers";

function ev<T extends EventType>(seq: number, type: T, payload: EventPayloads[T], runId = "run_1", simTimeMs = 1000 * seq): GameEvent {
  return {
    schemaVersion: 1, eventId: `evt_${seq}`, roomId: "room", runId, serverSequence: seq, time: "", simTimeMs, type,
    actorId: "x", causationId: null, correlationId: "story", payload,
  } as GameEvent;
}
const capture = (seq: number, characterId: string, ticker: string | null, runId?: string): GameEvent =>
  ev(seq, "CAPTURE_SUCCEEDED", {
    captureId: `cap_${seq}`, commandId: `cmd_${seq}`, characterId, ticker, captorId: "npc_p1", captorName: "Sgt. Imani Duarte", captorKind: "NPC",
    captorWalletId: null, bounty: 0, captorBalanceAfter: null, referenceAtCapture: 638, spotBeforeCapture: 431, survivalSeconds: 222,
  }, runId);
const halt = (seq: number, characterId: string, ticker: string, runId?: string): GameEvent =>
  ev(seq, "LISTING_HALTED", { ticker, characterId, status: "HALTED_CAPTURED", finalSpotBeforeHalt: 431 }, runId);
const noise = (seq: number): GameEvent => ev(seq, "MARKET_PRICE_UPDATED", { ticker: "ACE-01", referenceValue: 638, spotPrice: 0, previousSpotPrice: 431, reason: "CAPTURE", heat: 42, netBuyUnits: 0 });

describe("pickCrashTriggers", () => {
  it("returns nothing for an empty log or a log without captures", () => {
    expect(pickCrashTriggers([], -Infinity)).toEqual([]);
    expect(pickCrashTriggers([noise(1), noise(2)], -Infinity)).toEqual([]);
  });

  it("pairs a capture with its halt into one trigger", () => {
    const out = pickCrashTriggers([noise(9), capture(10, "c_ace", "ACE-01"), halt(11, "c_ace", "ACE-01"), noise(12)], 9);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ key: "run_1:c_ace", ticker: "ACE-01", characterId: "c_ace", sequence: 10 });
    expect(out[0]!.capture?.serverSequence).toBe(10);
    expect(out[0]!.halt?.serverSequence).toBe(11);
  });

  it("never returns a crash whose events are all at or before the cursor (history at mount)", () => {
    const log = [capture(10, "c_ace", "ACE-01"), halt(11, "c_ace", "ACE-01")];
    expect(pickCrashTriggers(log, 11)).toEqual([]);
    expect(pickCrashTriggers(log, 500)).toEqual([]);
  });

  it("attaches a capture that sits before the cursor when its halt arrives after it", () => {
    const out = pickCrashTriggers([capture(10, "c_ace", "ACE-01"), halt(11, "c_ace", "ACE-01")], 10);
    expect(out).toHaveLength(1);
    expect(out[0]!.capture?.serverSequence).toBe(10);
    expect(out[0]!.halt?.serverSequence).toBe(11);
    expect(out[0]!.sequence).toBe(10);
  });

  it("returns a capture alone, then the completed pair, under the same key", () => {
    const first = pickCrashTriggers([capture(10, "c_ace", "ACE-01")], 9);
    expect(first).toHaveLength(1);
    expect(first[0]!.halt).toBeNull();
    const second = pickCrashTriggers([capture(10, "c_ace", "ACE-01"), halt(11, "c_ace", "ACE-01")], 10);
    expect(second[0]!.key).toBe(first[0]!.key);
    expect(second[0]!.halt).not.toBeNull();
  });

  it("returns two captures that land together, in sequence order, whatever the input order", () => {
    const log = [halt(23, "c_blz", "BLZ-02"), capture(20, "c_ace", "ACE-01"), capture(22, "c_blz", "BLZ-02"), halt(21, "c_ace", "ACE-01")];
    const copy = [...log];
    const out = pickCrashTriggers(log, 19);
    expect(out.map((t) => t.ticker)).toEqual(["ACE-01", "BLZ-02"]);
    expect(out.map((t) => t.sequence)).toEqual([20, 22]);
    expect(log).toEqual(copy); // input untouched
  });

  it("ignores the capture of a character that has no ticker", () => {
    expect(pickCrashTriggers([capture(10, "c_rio", null)], 0)).toEqual([]);
  });

  it("accepts a halt without its capture", () => {
    const out = pickCrashTriggers([halt(11, "c_ace", "ACE-01")], 0);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ ticker: "ACE-01", sequence: 11, capture: null });
  });

  it("keeps the same character id in two runs apart", () => {
    const out = pickCrashTriggers([capture(10, "c_1", "ACE-01", "run_a"), halt(11, "c_1", "ACE-01", "run_a"), capture(30, "c_1", "ACE-01", "run_b"), halt(31, "c_1", "ACE-01", "run_b")], 0);
    expect(out.map((t) => t.key)).toEqual(["run_a:c_1", "run_b:c_1"]);
  });
});

describe("cursor and history guards", () => {
  it("highestSequence is -Infinity for an empty log and the maximum otherwise", () => {
    expect(highestSequence([])).toBe(Number.NEGATIVE_INFINITY);
    expect(highestSequence([noise(4), noise(9), noise(7)])).toBe(9);
  });

  it("isSnapshotUpdate is true only when all four world collections were replaced at once", () => {
    const a = { characters: {}, npcs: {}, missions: {}, listings: {} };
    expect(isSnapshotUpdate({ characters: {}, npcs: {}, missions: {}, listings: {} }, a)).toBe(true);
    expect(isSnapshotUpdate(a, a)).toBe(false);
    expect(isSnapshotUpdate({ ...a, listings: {} }, a)).toBe(false);            // a live listing push
    expect(isSnapshotUpdate({ ...a, characters: {}, npcs: {}, listings: {} }, a)).toBe(false); // leaving the room
  });

  it("newestSimTime is the largest rule-clock time in the log", () => {
    expect(newestSimTime([])).toBe(0);
    expect(newestSimTime([noise(4), noise(9), noise(7)])).toBe(9000);
  });

  it("isStaleEvent: a live event is the newest thing in the log and close to the tick clock", () => {
    expect(isStaleEvent({ simTimeMs: 100_000 }, 100_000, 100_900)).toBe(false);
    expect(isStaleEvent({ simTimeMs: 100_000 }, 100_000, 99_000)).toBe(false); // event newer than the last tick we saw
  });

  it("isStaleEvent: history is stale when newer events follow it, or when the same clock has moved on", () => {
    expect(isStaleEvent({ simTimeMs: 100_000 }, 130_000, 130_500)).toBe(true);  // 30 s of newer events in the log
    expect(isStaleEvent({ simTimeMs: 100_000 }, 100_000, 125_000)).toBe(true);  // last event in the log, but ticks are 25 s past it
  });

  it("isStaleEvent: a tick clock that is not the log's clock can never silence a live capture", () => {
    expect(isStaleEvent({ simTimeMs: 100_000 }, 100_000, 1_794_691_345_298)).toBe(false); // epoch ticks, relative log
    expect(isStaleEvent({ simTimeMs: 1_794_691_345_298 }, 1_794_691_345_298, 52_000)).toBe(false);
  });

  it("isStaleEvent never trips on unknown clocks", () => {
    expect(isStaleEvent({ simTimeMs: 0 }, 130_000, 130_000)).toBe(false);
    expect(isStaleEvent({ simTimeMs: 100_000 }, 0, 0)).toBe(false);
    expect(isStaleEvent({ simTimeMs: 100_000 }, 100_000, 0)).toBe(false);
  });
});
