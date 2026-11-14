import { describe, expect, it } from "vitest";
import { RULES } from "@vice/contracts";
import type { DecisionTrace, EventType, GameEvent, MemoryEntry } from "@vice/contracts";
import type { DecisionRequest, MessageRecord, NpcState, RoomState, SchedulerPort, WorldPort } from "../domain";
import { createBrains, createMemoryStore } from "./index";

const npc = (id: string, role: NpcState["role"], x: number, z: number, contacts: string[] = [], walletId: string | null = null): NpcState => ({
  id, name: id.toUpperCase(), role, persona: "p", goal: "g", x, z, home: { x, z }, patrol: [], patrolIndex: 0, activity: "IDLE",
  moveTarget: null, targetCharacterId: null, walletId, contacts, cooldownUntilMs: 0, lastCaptureAttemptAtMs: 0 });

function harness() {
  let now = 1000, seq = 0; const ids: Record<string, number> = {};
  const state = {
    id: "room", code: "VICE-42", runId: "run1", seed: 42, tick: 0, ruleVersion: "t", mode: "drex", createdAtMs: 0, runStartedAtMs: 0, lastEventSequence: 0,
    players: {}, missions: {}, messages: [], trades: [], counters: {},
    characters: { chr1: { id: "chr1", playerId: "p1", nickname: "Ace", role: "OUTLAW", ticker: "ACE-01", status: "ACTIVE", x: 47, z: 66, notorietyPoints: 0, completedMissions: 0, heat: 0, successfulEscapes: 0, spawnTimeMs: 0, captureTimeMs: null, capturedBy: null, lastHeatGainAtMs: 0, lastPoliceSightingAtMs: 0 } },
    npcs: { w1: npc("w1", "WITNESS", 40, 64, ["i1"]), i1: npc("i1", "INFORMANT", 33, 52, ["t1"]), t1: npc("t1", "TRADER", 17, 31, [], "wal_t1"), p1: npc("p1", "POLICE", 32, 28), p2: npc("p2", "POLICE", 92, 28) },
    listings: { "ACE-01": { ticker: "ACE-01", characterId: "chr1", nickname: "Ace", status: "ACTIVE", referenceValue: 100, spotPrice: 95, openedAtMs: 0, haltedAtMs: null, finalSpotBeforeHalt: null, history: [], demand: [], volume: 0 } },
    wallets: { wal_t1: { id: "wal_t1", ownerId: "t1", ownerKind: "NPC", ownerName: "T1", balanceCredits: 2000, holdings: {}, lastOrderAtMs: {} } },
  } as unknown as RoomState;
  const memory = createMemoryStore();
  const events: GameEvent[] = []; const orders: string[] = []; const bulletins: string[] = []; const activity: string[] = [];
  const jobs: Array<{ req: DecisionRequest; done: (t: DecisionTrace) => void; trace: DecisionTrace }> = [];
  let brains!: ReturnType<typeof createBrains>;
  const world: WorldPort = {
    state, now: () => now, nextId: (p) => `${p}_${(ids[p] = (ids[p] ?? 0) + 1)}`,
    emit(type, actorId, payload, opts) {
      const e = { schemaVersion: 1, eventId: `evt_${++seq}`, roomId: "room", runId: state.runId, serverSequence: seq, time: "", simTimeMs: now, type, actorId, causationId: opts?.causationId ?? null, correlationId: opts?.correlationId ?? "c", payload } as GameEvent<typeof type>;
      state.lastEventSequence = seq; events.push(e as GameEvent); brains.onEvent(e as GameEvent); return e;
    },
    sendMessage(m, opts) {
      const src = m.sourceMemoryId ? memory.get(m.sourceMemoryId) : undefined;
      const sent = world.emit("AGENT_MESSAGE_SENT", m.senderId, { messageId: world.nextId("msg"), senderId: m.senderId, recipientId: m.recipientId, kind: m.kind, sourceMemoryId: m.sourceMemoryId, sourceEventId: m.sourceEventId, subjectCharacterId: m.subjectCharacterId, summary: m.summary, hop: (src?.hop ?? 0) + 1 }, opts);
      const mem = memory.remember({ agentId: m.recipientId, factType: "RUMOR", subjectCharacterId: m.subjectCharacterId, subjectTicker: "ACE-01", eventId: sent.eventId, sourceActorId: m.senderId, sourceKind: "MESSAGE", sourceMemoryId: m.sourceMemoryId, hop: (src?.hop ?? 0) + 1, observedAtMs: now, expiresAtMs: now + 60000, confidence: 0.7, summary: m.summary, payload: m.payload, citation: null }, world.nextId("mem"));
      world.emit("AGENT_MESSAGE_DELIVERED", m.recipientId, { messageId: "m", senderId: m.senderId, recipientId: m.recipientId, kind: m.kind, memoryId: mem.id, hop: mem.hop }, { causationId: sent.eventId, correlationId: opts.correlationId });
      return { id: "m", sentEventId: sent.eventId } as unknown as MessageRecord;
    },
    placeNpcOrder(id, ticker, side, opts) { orders.push(`${id}:${side}:${ticker}`); world.emit("TRADE_EXECUTED", id, {} as never, opts); return { ok: true, trade: { executionPrice: 95 } as never, walletId: "wal_t1", balanceAfter: 1905, sharesAfter: 1, priceChange: null }; },
    setNpcActivity(id, a, t) { activity.push(`${id}:${a}:${t.targetCharacterId ?? ""}`); },
    publishBulletin(id, chr) { bulletins.push(`${id}:${chr}`); },
    marketSummary: (ticker) => ({ ticker, status: state.listings[ticker]!.status, spotPrice: 95, referenceValue: 100, changePct60s: 0, netBuyUnits: 0 }),
  };
  const scheduler: SchedulerPort = {
    request(req, done) {
      const trace = { id: req.decisionId, roomId: req.roomId, runId: req.runId, agentId: req.agentId, agentName: req.agentName, role: req.role, status: "REQUESTED", mode: "drex", decisionSource: null, provider: null, model: null, question: req.question, offeredActions: req.options, chosenAction: null, providerChoice: null, confidence: null, probabilities: null, providerRequestId: null, latencyMs: null, providerEvalMs: null, usage: null, attempts: 0, errorType: null, errorMessage: null, rejectReason: null, stateSnapshot: req.state, stateHash: "", triggerEventId: req.triggerEventId, correlationId: req.correlationId, effectEventIds: [], requestedAtMs: req.requestedAtMs, resolvedAtMs: null, requestedAtSequence: req.requestedAtSequence, citations: req.citations, shadow: null } as DecisionTrace;
      jobs.push({ req, done, trace }); return trace;
    },
    setMode() {}, getMode: () => "drex", status: () => ({}) as never, pending: () => jobs.length, cancelAll() {},
  };
  const traces: DecisionTrace[] = [];
  brains = createBrains({ world, memory, scheduler, onTrace: (t) => traces.push(t) });
  const observe = (npcId: string, factType: MemoryEntry["factType"] = "INCIDENT") => {
    const mem = memory.remember({ agentId: npcId, factType, subjectCharacterId: "chr1", subjectTicker: "ACE-01", eventId: "evt_inc", sourceActorId: npcId, sourceKind: "DIRECT", sourceMemoryId: null, hop: 0, observedAtMs: now, expiresAtMs: now + 60000, confidence: 0.95, summary: "saw Ace grab the marked package at the dock", payload: { x: 47, z: 66 }, citation: null }, world.nextId("mem"));
    return world.emit("NPC_OBSERVED", npcId, { npcId, memoryId: mem.id, factType, subjectCharacterId: "chr1", distance: 7, incidentId: "inc1", summary: mem.summary });
  };
  /** Finish the oldest queued decision as the scheduler would. */
  const answer = (action: string, patch: Partial<DecisionTrace> = {}) => {
    const j = jobs.shift()!;
    j.done({ ...j.trace, status: "RESOLVED", decisionSource: "drex", provider: "drex", model: "drex-max", chosenAction: action, providerChoice: action, confidence: 0.8, probabilities: { [action]: 0.9 }, providerRequestId: "req_x", latencyMs: 300, resolvedAtMs: now, ...patch });
  };
  const types = () => events.map((e) => e.type as EventType);
  return { state, memory, world, brains, jobs, events, orders, bulletins, activity, traces, observe, answer, types, advance: (ms: number) => { now += ms; brains.onTick(now); } };
}

describe("brains", () => {
  it("keeps knowledge private: an NPC told nothing has no trace of the incident (AT-02, FR-015)", () => {
    const h = harness();
    h.observe("w1");
    expect(h.memory.recall("w1", 1000)).toHaveLength(1);
    expect(h.memory.recall("t1", 1000)).toHaveLength(0);
    expect(h.memory.recall("i1", 1000)).toHaveLength(0);
    // The trader is asked to think about a new listing while the witness alone knows about the incident.
    h.world.emit("LISTING_OPENED", "exchange", { ticker: "ACE-01", characterId: "chr1", nickname: "Ace", referenceValue: 100, spotPrice: 95 });
    const trader = h.jobs.find((j) => j.req.agentId === "t1")!;
    const text = JSON.stringify(trader.req.state);
    expect(text).not.toMatch(/package|dock|grab/i);
    expect(text).toContain("New listing ACE-01");
    expect(JSON.stringify(h.jobs.find((j) => j.req.agentId === "w1")!.req.state)).toContain("marked package");
  });

  it("offers only legal actions per role", () => {
    const h = harness();
    h.observe("w1");
    expect(Object.keys(h.jobs[0]!.req.options).sort()).toEqual(["gossip", "ignore", "report"]);
    expect(h.jobs[0]!.req.scriptedAction).toBe("report");
    h.world.emit("LISTING_OPENED", "exchange", { ticker: "ACE-01", characterId: "chr1", nickname: "Ace", referenceValue: 100, spotPrice: 95 });
    const t = h.jobs.find((j) => j.req.agentId === "t1")!.req;
    expect(Object.keys(t.options).sort()).toEqual(["buy", "hold"]);       // no shares -> sell not offered
    expect(t.scriptedAction).toBe("hold");
    h.observe("p1", "SIGHTING");
    const p = h.jobs.find((j) => j.req.agentId === "p1")!.req;
    expect(Object.keys(p.options).sort()).toEqual(["investigate", "patrol", "pursue"]);
    expect(p.scriptedAction).toBe("pursue");
    for (const j of h.jobs) { expect(j.req.options).toHaveProperty(j.req.safeAction); expect(j.req.options).toHaveProperty(j.req.scriptedAction); }
  });

  it("runs witness -> informant -> trader with provenance and hop counts", () => {
    const h = harness();
    h.observe("w1");
    h.answer("gossip");
    expect(h.memory.recall("i1", 1000)[0]).toMatchObject({ sourceKind: "MESSAGE", sourceActorId: "w1", hop: 1 });
    expect(h.jobs[0]!.req.agentId).toBe("i1");
    expect(Object.keys(h.jobs[0]!.req.options).sort()).toEqual(["relay", "withhold"]);
    h.answer("relay");
    expect(h.memory.recall("t1", 1000)[0]).toMatchObject({ sourceKind: "MESSAGE", sourceActorId: "i1", hop: 2 });
    expect(JSON.stringify(h.jobs[0]!.req.state)).toContain("told by I1 (informant)");
    h.answer("buy");
    expect(h.orders).toEqual(["t1:BUY:ACE-01"]);
    const done = h.brains.traces().filter((t) => t.status === "APPLIED");
    expect(done.map((t) => t.agentId)).toEqual(["w1", "i1", "t1"]);
    expect(done[2]!.effectEventIds.length).toBeGreaterThanOrEqual(2);     // trade event + AGENT_ACTION_APPLIED
    const order = h.types().filter((t) => t.startsWith("DECISION_") || t.startsWith("AGENT_ACTION"));
    expect(order.slice(0, 3)).toEqual(["DECISION_REQUESTED", "DECISION_RESOLVED", "DECISION_REQUESTED"]);
    expect(h.brains.view("t1").lastDecision?.chosenAction).toBe("buy");
    expect(h.brains.view("t1").pendingDecisionId).toBeNull();
  });

  it("runs witness -> police -> bulletin -> other police and traders", () => {
    const h = harness();
    h.observe("w1");
    h.answer("report");
    expect(h.bulletins).toEqual(["p1:chr1"]);                              // nearest officer got the report
    expect(h.jobs[0]!.req.agentId).toBe("p1");
    expect(Object.keys(h.jobs[0]!.req.options).sort()).toEqual(["investigate", "patrol"]);   // no direct sighting -> no pursue
    h.answer("investigate");
    expect(h.activity).toEqual(["p1:INVESTIGATE:chr1"]);
    for (const id of ["t1", "p2"]) h.memory.remember({ agentId: id, factType: "WANTED_BULLETIN", subjectCharacterId: "chr1", subjectTicker: "ACE-01", eventId: "evt_b", sourceActorId: "p1", sourceKind: "BULLETIN", sourceMemoryId: null, hop: 2, observedAtMs: 1000, expiresAtMs: 99999, confidence: 0.9, summary: "ACE-01 is wanted", payload: { x: 47, z: 66 }, citation: null }, h.world.nextId("mem"));
    h.world.emit("BULLETIN_PUBLISHED", "p1", { bulletinId: "b1", characterId: "chr1", ticker: "ACE-01", lastKnown: { x: 47, z: 66 }, issuedBy: "p1", recipientIds: ["w1", "i1", "t1", "p1", "p2"] });
    expect(h.jobs.map((j) => j.req.agentId).sort()).toEqual(["p2", "t1"]);
  });

  it("rejects a stale decision with no effect", () => {
    const h = harness();
    h.world.emit("LISTING_OPENED", "exchange", { ticker: "ACE-01", characterId: "chr1", nickname: "Ace", referenceValue: 100, spotPrice: 95 });
    h.state.characters.chr1!.status = "CAPTURED";
    h.state.listings["ACE-01"]!.status = "HALTED_CAPTURED";
    h.answer("buy");
    expect(h.orders).toEqual([]);
    const t = h.brains.view("t1").lastDecision!;
    expect(t.status).toBe("REJECTED");
    expect(t.rejectReason).toMatch(/captured/);
    expect(h.types()).toContain("AGENT_ACTION_REJECTED");
    expect(h.types()).not.toContain("AGENT_ACTION_APPLIED");
  });

  it("passes fallback labeling through untouched", () => {
    const h = harness();
    h.observe("w1");
    h.answer("ignore", { decisionSource: "fallback", provider: "drex", providerChoice: null, confidence: null, probabilities: null, providerRequestId: null, errorType: "timeout" });
    const t = h.brains.view("w1").lastDecision!;
    expect(t).toMatchObject({ status: "FALLBACK_APPLIED", decisionSource: "fallback", confidence: null, probabilities: null, errorType: "timeout" });
    const resolved = h.events.find((e) => e.type === "DECISION_RESOLVED")! as GameEvent<"DECISION_RESOLVED">;
    expect(resolved.payload).toMatchObject({ decisionSource: "fallback", confidence: null, probabilities: null });
    expect(h.traces.at(-1)!.decisionSource).toBe("fallback");
  });

  it("respects cooldown and de-duplicates triggers", () => {
    const h = harness();
    const e = h.observe("w1");
    h.brains.onEvent(e as GameEvent);                                       // same trigger again
    expect(h.jobs).toHaveLength(1);
    h.answer("ignore");
    h.observe("w1");                                                        // new trigger inside the cooldown
    expect(h.jobs).toHaveLength(0);
    h.advance(RULES.decisions.npcCooldownMs + 1);
    expect(h.jobs).toHaveLength(1);
    h.brains.reset();
    expect(h.brains.traces()).toHaveLength(0);
  });
});
