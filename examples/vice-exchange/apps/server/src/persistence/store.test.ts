import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { DecisionTrace, EventPayloads, EventType, ExperimentResult, GameEvent } from "@vice/contracts";
import type { RoomState } from "../domain";
import { createMetrics, latencyStats } from "../metrics/index";
import { projectEvents, verifyReplay } from "../replay/index";
import { createPersistence } from "./index";

let seq = 0;
function ev<T extends EventType>(type: T, payload: EventPayloads[T], runId = "run_1"): GameEvent<T> {
  seq++;
  return { schemaVersion: 1, eventId: `evt_${seq}`, roomId: "room_1", runId, serverSequence: seq, time: new Date(1_700_000_000_000 + seq).toISOString(), simTimeMs: seq * 100, type, actorId: "server", causationId: null, correlationId: "story_1", payload };
}
function trace(id: string, over: Partial<DecisionTrace> = {}): DecisionTrace {
  return {
    id, roomId: "room_1", runId: "run_1", agentId: "npc_w1", agentName: "Marisol Vega", role: "WITNESS", status: "APPLIED", mode: "drex",
    decisionSource: "drex", provider: "drex", model: "drex-max-v1.1", question: "q", offeredActions: { report: "r", gossip: "g", ignore: "i" },
    chosenAction: "report", providerChoice: "report", confidence: 0.8, probabilities: { report: 0.86, gossip: 0.09, ignore: 0.05 },
    providerRequestId: "req_x", latencyMs: 300, providerEvalMs: 140, usage: { inputTokens: 200, outputTokens: 100 }, attempts: 1,
    errorType: null, errorMessage: null, rejectReason: null, stateSnapshot: { role: "witness" }, stateHash: "h", triggerEventId: "evt_1",
    correlationId: "story_1", effectEventIds: [], requestedAtMs: 1000, resolvedAtMs: 1300, requestedAtSequence: 1, citations: [], shadow: null, ...over,
  };
}

function story(): GameEvent[] {
  seq = 0;
  return [
    ev("ROOM_CREATED", { roomCode: "VICE-42", seed: 42, ruleVersion: "h0.2", mode: "drex", npcCount: 8 }),
    ev("WALLET_OPENED", { walletId: "w_a", ownerId: "p_a", ownerKind: "HUMAN", balance: 2000 }),
    ev("WALLET_OPENED", { walletId: "w_h", ownerId: "p_h", ownerKind: "HUMAN", balance: 2000 }),
    ev("WALLET_OPENED", { walletId: "w_t1", ownerId: "npc_t1", ownerKind: "NPC", balance: 2000 }),
    ev("CHARACTER_SPAWNED", { characterId: "chr_1", playerId: "p_a", nickname: "Ace", role: "OUTLAW", ticker: "ACE-01", x: 60, z: 68, walletId: "w_a" }),
    ev("LISTING_OPENED", { ticker: "ACE-01", characterId: "chr_1", nickname: "Ace", referenceValue: 100, spotPrice: 95 }),
    ev("TRADE_EXECUTED", { tradeId: "t1", commandId: "cmd_00000001", walletId: "w_a", actorId: "p_a", actorName: "Ace", actorKind: "HUMAN", ticker: "ACE-01", side: "BUY", qty: 1, executionPrice: 95, balanceAfter: 1905, sharesAfter: 1 }),
    ev("MARKET_PRICE_UPDATED", { ticker: "ACE-01", referenceValue: 100, spotPrice: 98, previousSpotPrice: 95, reason: "TRADE", heat: 0, netBuyUnits: 1 }),
    ev("AGENT_MESSAGE_SENT", { messageId: "m1", senderId: "npc_w1", recipientId: "npc_i1", kind: "GOSSIP", sourceMemoryId: "mem_1", sourceEventId: "evt_5", subjectCharacterId: "chr_1", summary: "saw it", hop: 1 }),
    ev("AGENT_MESSAGE_DELIVERED", { messageId: "m1", senderId: "npc_w1", recipientId: "npc_i1", kind: "GOSSIP", memoryId: "mem_2", hop: 1 }),
    ev("AGENT_MESSAGE_SENT", { messageId: "m2", senderId: "npc_i1", recipientId: "npc_t1", kind: "RELAY", sourceMemoryId: "mem_2", sourceEventId: "evt_10", subjectCharacterId: "chr_1", summary: "heard it", hop: 2 }),
    ev("TRADE_EXECUTED", { tradeId: "t2", commandId: "cmd_00000002", walletId: "w_t1", actorId: "npc_t1", actorName: "Priya", actorKind: "NPC", ticker: "ACE-01", side: "BUY", qty: 1, executionPrice: 98, balanceAfter: 1902, sharesAfter: 1 }),
    ev("TRADE_REJECTED", { commandId: "cmd_00000003", walletId: "w_t1", actorId: "npc_t1", actorKind: "NPC", ticker: "ACE-01", side: "BUY", qty: 1, reason: "COOLDOWN" }),
    ev("WANTED_STATUS_CHANGED", { characterId: "chr_1", ticker: "ACE-01", wanted: true, heat: 42, wantedLevel: 2, reason: "report" }),
    ev("MARKET_PRICE_UPDATED", { ticker: "ACE-01", referenceValue: 190, spotPrice: 130, previousSpotPrice: 98, reason: "HEAT", heat: 42, netBuyUnits: 2 }),
    ev("CAPTURE_SUCCEEDED", { captureId: "cap_1", commandId: "cmd_00000004", characterId: "chr_1", ticker: "ACE-01", captorId: "p_h", captorName: "Hunter", captorKind: "HUMAN", captorWalletId: "w_h", bounty: 95, captorBalanceAfter: 2095, referenceAtCapture: 190, spotBeforeCapture: 130, survivalSeconds: 60 }),
    ev("LISTING_HALTED", { ticker: "ACE-01", characterId: "chr_1", status: "HALTED_CAPTURED", finalSpotBeforeHalt: 130 }),
    ev("MARKET_PRICE_UPDATED", { ticker: "ACE-01", referenceValue: 190, spotPrice: 0, previousSpotPrice: 130, reason: "CAPTURE", heat: 42, netBuyUnits: 2 }),
    ev("CHARACTER_ARCHIVED", { characterId: "chr_1", ticker: "ACE-01", playerId: "p_a" }),
  ];
}

function liveState(): RoomState {
  const wallet = (id: string, ownerId: string, balance: number, shares = 0): RoomState["wallets"][string] => ({ id, ownerId, ownerKind: "HUMAN" as const, ownerName: ownerId, balanceCredits: balance, holdings: (shares ? { "ACE-01": { shares, costBasis: 95 } } : {}) as Record<string, { shares: number; costBasis: number }>, lastOrderAtMs: {} });
  return {
    id: "room_1", code: "VICE-42", runId: "run_1", seed: 42, tick: 0, ruleVersion: "h0.2", mode: "drex", createdAtMs: 0, runStartedAtMs: 0, lastEventSequence: 19,
    players: {}, npcs: {}, missions: {}, messages: [], trades: [], counters: {},
    characters: { chr_1: { id: "chr_1", playerId: "p_a", nickname: "Ace", role: "OUTLAW", ticker: "ACE-01", status: "CAPTURED", x: 0, z: 0, notorietyPoints: 2, completedMissions: 1, heat: 42, successfulEscapes: 0, spawnTimeMs: 0, captureTimeMs: 1, capturedBy: "p_h", lastHeatGainAtMs: 0, lastPoliceSightingAtMs: 0 } },
    listings: { "ACE-01": { ticker: "ACE-01", characterId: "chr_1", nickname: "Ace", status: "HALTED_CAPTURED", referenceValue: 190, spotPrice: 0, openedAtMs: 0, haltedAtMs: 1, finalSpotBeforeHalt: 130, history: [], demand: [], volume: 2 } },
    wallets: { w_a: wallet("w_a", "p_a", 1905, 1), w_h: wallet("w_h", "p_h", 2095), w_t1: wallet("w_t1", "npc_t1", 1902, 1) },
  };
}

describe("persistence (node:sqlite)", () => {
  it("survives close and reopen with events, decisions and experiments intact", () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "vice store "));
    const file = path.join(dir, "nested", "vice.sqlite");
    try {
      const events = story();
      const a = createPersistence(file);
      expect(a.healthy).toBe(true);
      for (const e of events) a.appendEvent(e);
      a.appendEvent(events[0]!); // duplicate sequence is ignored, not thrown
      a.saveDecision(trace("dec_1", { status: "REQUESTED", decisionSource: null, chosenAction: null }));
      a.saveDecision(trace("dec_1")); // upsert to the final trace
      const exp: ExperimentResult = { id: "exp_1", seed: 42, durationSec: 45, startedAtMs: 1, finishedAtMs: null, status: "RUNNING", rows: [], caveat: "one run per mode" };
      a.saveExperiment(exp); a.saveExperiment({ ...exp, status: "DONE", finishedAtMs: 2 });
      a.close();

      const b = createPersistence(file);
      const loaded = b.loadEvents("room_1", "run_1");
      expect(loaded).toHaveLength(events.length);
      expect(loaded).toEqual(events);
      expect(b.loadEvents("room_1", "run_1", 17).map((e) => e.type)).toEqual(["MARKET_PRICE_UPDATED", "CHARACTER_ARCHIVED"]);
      const decs = b.loadDecisions("room_1", "run_1");
      expect(decs).toHaveLength(1);
      expect(decs[0]).toEqual(trace("dec_1"));
      expect(b.loadExperiments()).toEqual([{ ...exp, status: "DONE", finishedAtMs: 2 }]);
      expect(b.findRoomId("vice-42")).toBe("room_1");
      expect(b.listRuns()[0]).toMatchObject({ roomId: "room_1", runId: "run_1", roomCode: "VICE-42", events: events.length });
      // replay straight from disk
      expect(verifyReplay(loaded, liveState()).mismatches).toEqual([]);
      b.close();
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });

  it("supports :memory: and never throws, even after close or on a bad path", () => {
    const m = createPersistence(":memory:");
    m.appendEvent(story()[0]!);
    expect(m.loadEvents("room_1", "run_1")).toHaveLength(1);
    m.close();
    expect(() => { m.appendEvent(story()[0]!); m.saveDecision(trace("d")); m.close(); }).not.toThrow();
    expect(m.loadEvents("room_1", "run_1")).toEqual([]);
    const bad = createPersistence("/dev/null/nope/x.sqlite");
    expect(bad.healthy).toBe(false);
    expect(() => bad.appendEvent(story()[0]!)).not.toThrow();
    expect(bad.loadDecisions("a", "b")).toEqual([]);
  });

  it("does not persist anything resembling an API key", () => {
    const m = createPersistence(":memory:");
    for (const e of story()) m.appendEvent(e);
    m.saveDecision(trace("dec_1"));
    const dump = JSON.stringify([m.loadEvents("room_1", "run_1"), m.loadDecisions("room_1", "run_1")]);
    expect(dump).not.toMatch(/nace_sk_|LLM_\d|Bearer /);
    m.close();
  });
});

describe("metrics", () => {
  it("counts from events and traces and reports only measured latency", () => {
    const m = createMetrics();
    m.reset("run_1", "drex", 42, 0);
    expect(m.view().latency).toEqual({ drex: null, meta: null });
    for (const e of story()) m.onEvent(e);
    m.onDecision(trace("d1", { latencyMs: 300, providerEvalMs: 140 }));
    m.onDecision(trace("d2", { latencyMs: 100, providerEvalMs: 110, role: "TRADER", chosenAction: "buy" }));
    m.onDecision(trace("d3", { decisionSource: "fallback", provider: null, latencyMs: null, providerEvalMs: null, status: "FALLBACK_APPLIED", chosenAction: "ignore", errorType: "timeout" }));
    m.onDecision(trace("d4", { decisionSource: "meta", provider: "meta", latencyMs: 1000, providerEvalMs: null, confidence: null, probabilities: null }));
    m.onDecision(trace("d5", { status: "REJECTED", rejectReason: "stale: target already captured", latencyMs: 200 }));
    m.onDecision(trace("d6", { status: "REQUESTED", decisionSource: null, provider: null, latencyMs: null, chosenAction: null }));
    const v = m.view();
    expect(v.decisions.total).toBe(5);
    expect(v.decisions.bySource).toEqual({ drex: 3, fallback: 1, meta: 1 });
    expect(v.decisions.byRole).toEqual({ WITNESS: 4, TRADER: 1 });
    expect(v.decisions.byAction).toMatchObject({ report: 3, buy: 1, ignore: 1 });
    expect(v.decisions.rejected).toBe(1); expect(v.decisions.stale).toBe(1);
    expect(v.latency.drex).toEqual({ count: 3, meanMs: 200, p50Ms: 200, p95Ms: 300, maxMs: 300, evalP50Ms: 140 });
    expect(v.latency.meta).toMatchObject({ count: 1, p50Ms: 1000, evalP50Ms: null });
    expect(v.messages).toEqual({ sent: 2, delivered: 1, maxHops: 2 });
    expect(v.trades).toEqual({ executed: 2, rejected: 1, byNpc: 1, byHuman: 1 });
    expect(v.priceUpdates).toBe(3); expect(v.captures).toBe(1);
    expect(v.edges).toEqual([
      { from: "npc_w1", to: "npc_i1", kind: "GOSSIP", count: 1 },
      { from: "npc_i1", to: "npc_t1", kind: "RELAY", count: 1 },
    ]);
    m.reset("run_2", "scripted", 42, 5);
    expect(m.view()).toMatchObject({ runId: "run_2", mode: "scripted", decisions: { total: 0 }, captures: 0, edges: [] });
  });
  it("latencyStats returns null without samples", () => {
    expect(latencyStats([])).toBeNull();
    expect(latencyStats([5])).toEqual({ count: 1, meanMs: 5, p50Ms: 5, p95Ms: 5, maxMs: 5, evalP50Ms: null });
  });
});

describe("replay (AT-08)", () => {
  it("rebuilds wallets, listings and capture outcome from the log alone", () => {
    const p = projectEvents(story());
    expect(p.wallets.w_a).toMatchObject({ balance: 1905, holdings: { "ACE-01": 1 } });
    expect(p.wallets.w_h!.balance).toBe(2095);
    expect(p.wallets.w_t1).toMatchObject({ balance: 1902, holdings: { "ACE-01": 1 } });
    expect(p.listings["ACE-01"]).toMatchObject({ status: "HALTED_CAPTURED", spotPrice: 0, finalSpotBeforeHalt: 130, trades: 2 });
    expect(p.characters.chr_1).toMatchObject({ status: "ARCHIVED", capturedBy: "p_h", bounty: 95 });
    expect(p.captures).toBe(1);
  });
  it("matches live state, in any input order, and reports real differences", () => {
    const events = story();
    expect(verifyReplay([...events].reverse(), liveState())).toMatchObject({ ok: true, mismatches: [] });
    const tampered = liveState();
    tampered.wallets.w_h!.balanceCredits = 9999;
    tampered.listings["ACE-01"]!.spotPrice = 5;
    const r = verifyReplay(events, tampered);
    expect(r.ok).toBe(false);
    expect(r.mismatches.join("\n")).toMatch(/w_h: balance replay=2095 live=9999/);
    expect(r.mismatches.join("\n")).toMatch(/ACE-01: spot replay=0 live=5/);
  });
  it("a halted listing stays at zero and a reset starts a clean run", () => {
    const events = story();
    events.push(ev("MARKET_PRICE_UPDATED", { ticker: "ACE-01", referenceValue: 200, spotPrice: 150, previousSpotPrice: 0, reason: "SURVIVAL_TICK", heat: 0, netBuyUnits: 0 }));
    expect(projectEvents(events).listings["ACE-01"]!.spotPrice).toBe(0);
    events.push(ev("ROOM_RESET", { seed: 42, previousRunId: "run_1", newRunId: "run_2", mode: "drex" }, "run_2"));
    events.push(ev("WALLET_OPENED", { walletId: "w_a", ownerId: "p_a", ownerKind: "HUMAN", balance: 2000 }, "run_2"));
    const p = projectEvents(events);
    expect(p.runId).toBe("run_2");
    expect(Object.keys(p.listings)).toEqual([]);
    expect(p.wallets.w_a!.balance).toBe(2000);
  });
});
