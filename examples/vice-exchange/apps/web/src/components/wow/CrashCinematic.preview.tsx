// Dev-only harness for CrashCinematic: no server, the real zustand store fed with a busy mock room.
// Open /src/components/wow/CrashCinematic.preview.html with any of:
//   (nothing)   the real <App /> on the mock room; the app's own <CrashCinematic /> plays the live capture
//   t=<ms>      hold the cinematic at this timeline time (deterministic screenshots; implies bare)
//   calm=1      force the reduced-motion variant (implies bare)   captor=human   a human hunter makes the capture
//   double=1    two captures land in the same burst (queue)
//   old=1       the capture is already in the join snapshot: nothing may play
//   resync=1    a capture arrives inside a resync snapshot: nothing may play; a later live one must
//   bare=1      no app behind: this page mounts the component itself       at=<ms>  delay before the live burst
//   own=1       mount the component here even with the app behind (only if the app stops mounting it)
import { StrictMode, useEffect, useState } from "react";
import type { ComponentType, ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { CITY_MAP, MISSION_SPOTS, clamp } from "@vice/contracts";
import type {
  AgentView, CharacterView, DecisionSource, DecisionTrace, EventPayloads, EventType, GameEvent, JoinResult, ListingView,
  MemoryEntry, MetricsView, NpcRole, PricePoint, ProviderHealth, RoomSnapshot, WorldTick,
} from "@vice/contracts";
import "../../styles.css";
import { useGame } from "../../state/gameStore";
import CrashCinematic from "./CrashCinematic";

const q = new URLSearchParams(location.search);
const FREEZE = q.has("t") ? Number(q.get("t")) : undefined;
const CALM = q.has("calm") ? q.get("calm") !== "0" : undefined;
const HUMAN = q.get("captor") === "human";
const DOUBLE = q.has("double");
const OLD = q.has("old");
const RESYNC = q.has("resync");
// The app mounts <CrashCinematic /> itself, so with the app behind this page must not mount a second one.
const BARE = q.has("bare") || FREEZE != null || CALM != null;
const OWN = BARE || q.has("own");
const AT = q.has("at") ? Number(q.get("at")) : FREEZE != null ? 60 : 2600;
/** rich=3 multiplies every price, to preview four-digit readouts ("V$1,293"). */
const RICH = q.has("rich") ? Math.max(0.01, Number(q.get("rich")) || 1) : 1;

const RUN = "run_demo_42", ROOM = "VICE-42";
const T0 = 1_000_000; // rule clock at room start
const LIFE = 222;     // seconds Ace has survived when captured
const NOW = T0 + LIFE * 1000;

// ---------------------------------------------------------------- price history by the real rule formula
function simulate(lifeSec: number, missions: number[], reports: number[], trades: Array<[number, number]>): { history: PricePoint[]; reference: number; spot: number; heat: number } {
  const history: PricePoint[] = [];
  let notoriety = 0, done = 0, heat = 0, net = 0, reference = 100, spot = 95, quiet = 0;
  for (let s = 0; s <= lifeSec; s++) {
    let changed = s === 0 || s === lifeSec;
    if (missions.includes(s)) { done++; notoriety += 2; heat = Math.min(100, heat + 12); quiet = 0; changed = true; }
    if (reports.includes(s)) { heat = Math.min(100, heat + 30); quiet = 0; changed = true; }
    else if (++quiet > 6 && heat > 0 && s < lifeSec - 45) { heat = Math.max(0, heat - 1); changed = changed || s % 2 === 0; }
    for (const [at, d] of trades) if (at === s) { net += d; changed = true; }
    reference = Math.round((100 + 15 * notoriety + 60 * done + 2 * Math.floor(s / 5)) * RICH);
    spot = Math.max(1, Math.round(reference * clamp(0.95 - 0.007 * heat, 0.15, 0.98) * clamp(1 + 0.03 * net, 0.75, 1.25)));
    if (changed || s % 5 === 0) history.push({ t: T0 + s * 1000, spot, reference });
  }
  return { history, reference, spot, heat };
}
// A typical demo arc: jobs lift the reference, one early report cools the price, the last report makes Ace wanted.
const ACE = simulate(LIFE, [20, 52, 96, 141, 183], [58, 196], [[24, 1], [56, 1], [101, 1], [118, 1], [158, -1], [192, -1], [207, -1]]);
const BLZ = simulate(118, [14, 61], [70, 104], [[18, 1], [75, -1]]);

// ---------------------------------------------------------------- world
const health = (state: ProviderHealth["state"], model: string | null): ProviderHealth => ({
  configured: state !== "UNCONFIGURED", state, model, lastOkAtMs: state === "LIVE" ? NOW - 400 : null, lastErrorType: null,
  callsLastMinute: state === "LIVE" ? 23 : 0, maxCallsPerMinute: 60, inFlight: state === "LIVE" ? 2 : 0, circuitOpenUntilMs: null,
});
let memSeq = 0, decSeq = 0;
const mem = (agentId: string, factType: MemoryEntry["factType"], sourceKind: MemoryEntry["sourceKind"], sourceActorId: string, hop: number, summary: string, confidence: number): MemoryEntry => ({
  id: `mem_${++memSeq}`, agentId, factType, subjectCharacterId: "c_ace", subjectTicker: "ACE-01", eventId: `evt_${String(100 + memSeq).padStart(6, "0")}`,
  sourceActorId, sourceKind, sourceMemoryId: null, hop, observedAtMs: NOW - 9000 + memSeq * 300, expiresAtMs: NOW + 120000, confidence, summary, payload: {}, citation: null,
});
function trace(agent: { id: string; name: string; role: NpcRole }, question: string, offered: Record<string, string>, chosen: string, source: DecisionSource, probs: Record<string, number> | null): DecisionTrace {
  const real = source === "drex" || source === "drex_low_confidence_guardrail";
  const sorted = probs ? Object.values(probs).sort((a, b) => b - a) : [];
  return {
    id: `dec_${String(++decSeq).padStart(4, "0")}`, roomId: "room_1", runId: RUN, agentId: agent.id, agentName: agent.name, role: agent.role,
    status: source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED", mode: "drex", decisionSource: source, provider: real || source === "fallback" ? "drex" : null,
    model: real ? "drex-1" : null, question, offeredActions: offered, chosenAction: chosen, providerChoice: real ? chosen : null,
    confidence: real && probs ? Math.round((sorted[0]! - (sorted[1] ?? 0)) * 1000) / 1000 : null, probabilities: real ? probs : null,
    providerRequestId: real ? `req_${(0x9f3a00 + decSeq * 7919).toString(16)}` : null, latencyMs: real ? 280 + ((decSeq * 53) % 140) : null, providerEvalMs: real ? 190 + ((decSeq * 31) % 90) : null,
    usage: real ? { inputTokens: 410 + decSeq * 3, outputTokens: 1 } : null, attempts: 1,
    errorType: source === "fallback" ? "TIMEOUT" : null, errorMessage: source === "fallback" ? "Drex did not answer within 3000 ms" : null, rejectReason: null,
    stateSnapshot: { role: agent.role, knows: ["ACE-01 seen at the dock with a marked package"] }, stateHash: `h${decSeq}`, triggerEventId: `evt_${String(200 + decSeq).padStart(6, "0")}`,
    correlationId: "story_0007", effectEventIds: [], requestedAtMs: NOW - 6000 + decSeq * 400, resolvedAtMs: NOW - 5700 + decSeq * 400, requestedAtSequence: 300 + decSeq, citations: [], shadow: null,
  };
}
const ROSTER: Array<{ id: string; name: string; role: NpcRole; x: number; z: number; persona: string; goal: string; contacts: string[] }> = [
  { id: "npc_w1", name: "Marisol Vega", role: "WITNESS", x: 40, z: 64, persona: "dock fisher, civic-minded, trusts the police", goal: "Keep the marina safe", contacts: ["npc_i1"] },
  { id: "npc_w2", name: "Otis Brandt", role: "WITNESS", x: 55, z: 45, persona: "plaza vendor, distrusts police, loves to gossip", goal: "Stay in the loop", contacts: ["npc_i2"] },
  { id: "npc_i1", name: "Lena Okoye", role: "INFORMANT", x: 33, z: 52, persona: "sells tips to traders for a cut", goal: "Monetise every rumor", contacts: ["npc_t1", "npc_t2"] },
  { id: "npc_i2", name: "Dex Marlowe", role: "INFORMANT", x: 62, z: 30, persona: "cautious, relays only what he believes", goal: "Protect his reputation", contacts: ["npc_t2"] },
  { id: "npc_t1", name: "Priya Castellan", role: "TRADER", x: 17, z: 31, persona: "momentum trader, risk-seeking", goal: "Ride the hottest ticker", contacts: [] },
  { id: "npc_t2", name: "Rook Halvorsen", role: "TRADER", x: 30, z: 41, persona: "risk-averse, sells on bad news", goal: "Never hold a captured name", contacts: [] },
  { id: "npc_p1", name: "Sgt. Imani Duarte", role: "POLICE", x: 49, z: 60, persona: "by the book", goal: "Capture wanted outlaws", contacts: [] },
  { id: "npc_p2", name: "Ofc. Cole Renner", role: "POLICE", x: 88, z: 50, persona: "aggressive", goal: "Capture wanted outlaws", contacts: [] },
];
const W_OPTS = { report: "Tell the nearest officer", gossip: "Tell your informant contact", ignore: "Say nothing" };
const I_OPTS = { relay: "Pass it to your trader contacts", withhold: "Keep it to yourself" };
const T_OPTS = { buy: "Buy 1 share", sell: "Sell 1 share", hold: "Do nothing" };
const P_OPTS = { investigate: "Go to the last known position", patrol: "Keep patrolling", pursue: "Chase the suspect" };
function buildNpcs(): AgentView[] {
  const plan: Record<string, [string, Record<string, string>, string, DecisionSource, Record<string, number> | null]> = {
    npc_w1: ["You saw ACE-01 lift a marked package at the dock. What do you do?", W_OPTS, "report", "drex", { report: 0.81, gossip: 0.13, ignore: 0.06 }],
    npc_w2: ["You saw ACE-01 drop a bag in Neon Plaza. What do you do?", W_OPTS, "gossip", "drex", { report: 0.09, gossip: 0.84, ignore: 0.07 }],
    npc_i1: ["A witness says ACE-01 is running jobs at the dock. Relay it?", I_OPTS, "relay", "drex", { relay: 0.77, withhold: 0.23 }],
    npc_i2: ["A vendor says ACE-01 is running jobs in the plaza. Relay it?", I_OPTS, "relay", "fallback", null],
    npc_t1: ["ACE-01 has a wanted bulletin. What is your order?", T_OPTS, "hold", "drex_low_confidence_guardrail", { buy: 0.36, sell: 0.31, hold: 0.33 }],
    npc_t2: ["Rumor: ACE-01 is about to be caught. What is your order?", T_OPTS, "sell", "drex", { buy: 0.04, sell: 0.88, hold: 0.08 }],
    npc_p1: ["ACE-01 is wanted and in sight. What do you do?", P_OPTS, "pursue", "drex", { investigate: 0.11, patrol: 0.03, pursue: 0.86 }],
    npc_p2: ["A bulletin names ACE-01 near the marina. What do you do?", P_OPTS, "investigate", "scripted", null],
  };
  return ROSTER.map((r): AgentView => {
    const [question, offered, chosen, source, probs] = plan[r.id]!;
    const holdings: Record<string, number> = r.id === "npc_t1" ? { "ACE-01": 3 } : r.id === "npc_t2" ? { "ACE-01": 1 } : {};
    const t = trace(r, question, offered, chosen, source, probs);
    const memories: MemoryEntry[] = r.role === "WITNESS" ? [mem(r.id, "INCIDENT", "DIRECT", r.id, 0, "Saw ACE-01 take a marked package", 0.95)]
      : r.role === "INFORMANT" ? [mem(r.id, "RUMOR", "MESSAGE", r.contacts.length > 1 ? "npc_w1" : "npc_w2", 1, "ACE-01 is running jobs nearby", 0.7)]
      : r.role === "TRADER" ? [mem(r.id, "RUMOR", "MESSAGE", "npc_i1", 2, "ACE-01 is hot: police were told", 0.5), mem(r.id, "WANTED_BULLETIN", "BULLETIN", "npc_p1", 0, "ACE-01 wanted, last seen at the marina", 0.9)]
      : [mem(r.id, "INCIDENT", "MESSAGE", "npc_w1", 1, "Witness report: ACE-01 took a package at the dock", 0.8), mem(r.id, "SIGHTING", "DIRECT", r.id, 0, "ACE-01 in sight, heading east", 0.95)];
    return {
      id: r.id, name: r.name, role: r.role, goal: r.goal, persona: r.persona, x: r.x, z: r.z,
      activity: r.id === "npc_p1" ? "PURSUE" : r.id === "npc_p2" ? "INVESTIGATE" : "IDLE", targetCharacterId: r.role === "POLICE" ? "c_ace" : null,
      walletCredits: r.role === "TRADER" ? (r.id === "npc_t1" ? 1412 : 1688) : null, holdings,
      contacts: r.contacts, memories, pendingDecisionId: null, lastDecision: t, recentDecisions: [t],
    };
  });
}
const chr = (id: string, playerId: string, nickname: string, role: "OUTLAW" | "HUNTER", ticker: string | null, x: number, z: number, heat: number, missions: number, spawn: number): CharacterView => ({
  id, playerId, nickname, role, ticker, status: heat >= 30 ? "WANTED" : "ACTIVE", x, z, heat, wantedLevel: Math.min(5, Math.floor(heat / 20)), wanted: heat >= 30,
  notorietyPoints: missions * 2, completedMissions: missions, successfulEscapes: 0, spawnTimeMs: spawn, captureTimeMs: null, capturedBy: null,
});
const listing = (ticker: string, characterId: string, nickname: string, sim: typeof ACE, openedAtMs: number): ListingView => ({
  ticker, characterId, nickname, status: "ACTIVE", referenceValue: sim.reference, spotPrice: sim.spot, bid: Math.max(1, Math.floor(sim.spot * 0.97)), ask: sim.spot,
  history: sim.history, volume: 9, netBuyUnits: -1, openedAtMs, haltedAtMs: null, finalSpotBeforeHalt: null, currentBounty: Math.max(50, Math.round(0.5 * sim.reference)),
});
const halted = (l: ListingView, at: number): ListingView => ({
  ...l, status: "HALTED_CAPTURED", spotPrice: 0, bid: 0, ask: 0, haltedAtMs: at, finalSpotBeforeHalt: l.spotPrice, history: [...l.history, { t: at, spot: 0, reference: l.referenceValue }],
});

// ---------------------------------------------------------------- events
let seq = 0;
function ev<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], simTimeMs: number, causationId: string | null = null): GameEvent<T> {
  const n = ++seq;
  return {
    schemaVersion: 1, eventId: `evt_${String(n).padStart(6, "0")}`, roomId: "room_1", runId: RUN, serverSequence: n,
    time: new Date(Date.now() - (NOW - simTimeMs)).toISOString(), simTimeMs, type, actorId, causationId, correlationId: "story_0007", payload,
  };
}
const resolved = (id: string, agentId: string, role: NpcRole, chosen: string, offered: string[], source: DecisionSource, probs: Record<string, number> | null): EventPayloads["DECISION_RESOLVED"] => {
  const real = source === "drex";
  const s = probs ? Object.values(probs).sort((a, b) => b - a) : [];
  return {
    decisionId: id, agentId, role, status: source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED", decisionSource: source, provider: source === "scripted" ? null : "drex",
    model: real ? "drex-1" : null, chosenAction: chosen, providerChoice: real ? chosen : null, offeredActions: offered,
    confidence: real && probs ? Math.round((s[0]! - (s[1] ?? 0)) * 1000) / 1000 : null, probabilities: real ? probs : null,
    providerRequestId: real ? `req_${id}` : null, latencyMs: real ? 312 : null, providerEvalMs: real ? 204 : null, errorType: source === "fallback" ? "TIMEOUT" : null, rejectReason: null,
  };
};
function historyEvents(): GameEvent[] {
  const e: GameEvent[] = [];
  e.push(ev("ROOM_CREATED", "server", { roomCode: ROOM, seed: 42, ruleVersion: "h0.2", mode: "drex", npcCount: 8 }, T0));
  e.push(ev("PLAYER_JOINED", "p_ace", { playerId: "p_ace", nickname: "Ace", role: "OUTLAW", reconnect: false }, T0 + 400));
  e.push(ev("CHARACTER_SPAWNED", "p_ace", { characterId: "c_ace", playerId: "p_ace", nickname: "Ace", role: "OUTLAW", ticker: "ACE-01", x: 60, z: 68, walletId: "w_ace" }, T0 + 400));
  e.push(ev("LISTING_OPENED", "exchange", { ticker: "ACE-01", characterId: "c_ace", nickname: "Ace", referenceValue: 100, spotPrice: 95 }, T0 + 400));
  e.push(ev("PLAYER_JOINED", "p_rio", { playerId: "p_rio", nickname: "Rio", role: "HUNTER", reconnect: false }, T0 + 9000));
  e.push(ev("PLAYER_JOINED", "p_blz", { playerId: "p_blz", nickname: "Blaze", role: "OUTLAW", reconnect: false }, NOW - 118000));
  e.push(ev("LISTING_OPENED", "exchange", { ticker: "BLZ-02", characterId: "c_blz", nickname: "Blaze", referenceValue: 100, spotPrice: 95 }, NOW - 118000));
  for (const [at, who, side, px] of [[24, "Priya Castellan", "BUY", 151], [56, "Priya Castellan", "BUY", 204], [101, "Priya Castellan", "BUY", 316], [158, "Rook Halvorsen", "SELL", 240]] as const) {
    e.push(ev("TRADE_EXECUTED", who === "Priya Castellan" ? "npc_t1" : "npc_t2", { tradeId: `trd_${at}`, commandId: `c${at}`, walletId: "w_t", actorId: who === "Priya Castellan" ? "npc_t1" : "npc_t2", actorName: who, actorKind: "NPC", ticker: "ACE-01", side, qty: 1, executionPrice: px, balanceAfter: 1700, sharesAfter: 2 }, T0 + at * 1000));
  }
  e.push(ev("WANTED_STATUS_CHANGED", "npc_p1", { characterId: "c_ace", ticker: "ACE-01", wanted: true, heat: 42, wantedLevel: 2, reason: "witness report reached police" }, T0 + 150000));
  return e;
}
/** The busy stretch right before the capture: sightings, decisions, messages, a trade, a bulletin. */
function busyEvents(at: number): GameEvent[] {
  const e: GameEvent[] = [];
  const m = ev("MISSION_COMPLETED", "p_ace", { missionId: "m_6", characterId: "c_ace", ticker: "ACE-01", x: 47, z: 60, notorietyGained: 2, heatGained: 12, notorietyPoints: 12, completedMissions: 6, heat: 86 }, at - 5200);
  e.push(m);
  const inc = ev("INCIDENT_OCCURRED", "p_ace", { incidentId: "inc_9", kind: "MISSION", characterId: "c_ace", ticker: "ACE-01", x: 47, z: 60, description: "Ace lifted a marked package at the dock", observerIds: ["npc_w1", "npc_p1"] }, at - 5200, m.eventId);
  e.push(inc);
  e.push(ev("NPC_OBSERVED", "npc_w1", { npcId: "npc_w1", memoryId: "mem_901", factType: "INCIDENT", subjectCharacterId: "c_ace", distance: 8, incidentId: "inc_9", summary: "Ace took the marked package" }, at - 5100, inc.eventId));
  e.push(ev("DECISION_REQUESTED", "npc_w1", { decisionId: "dec_w1", agentId: "npc_w1", role: "WITNESS", mode: "drex", offeredActions: ["report", "gossip", "ignore"], triggerEventId: inc.eventId, stateHash: "a1" }, at - 5000));
  e.push(ev("DECISION_RESOLVED", "npc_w1", resolved("dec_w1", "npc_w1", "WITNESS", "report", ["report", "gossip", "ignore"], "drex", { report: 0.81, gossip: 0.13, ignore: 0.06 }), at - 4700));
  e.push(ev("AGENT_MESSAGE_SENT", "npc_w1", { messageId: "msg_31", senderId: "npc_w1", recipientId: "npc_p1", kind: "REPORT", sourceMemoryId: "mem_901", sourceEventId: inc.eventId, subjectCharacterId: "c_ace", summary: "Ace took a marked package at the dock", hop: 1 }, at - 4600));
  e.push(ev("DECISION_REQUESTED", "npc_w2", { decisionId: "dec_w2", agentId: "npc_w2", role: "WITNESS", mode: "drex", offeredActions: ["report", "gossip", "ignore"], triggerEventId: inc.eventId, stateHash: "a2" }, at - 4500));
  e.push(ev("DECISION_RESOLVED", "npc_w2", resolved("dec_w2", "npc_w2", "WITNESS", "gossip", ["report", "gossip", "ignore"], "drex", { report: 0.09, gossip: 0.84, ignore: 0.07 }), at - 4200));
  e.push(ev("AGENT_MESSAGE_SENT", "npc_w2", { messageId: "msg_32", senderId: "npc_w2", recipientId: "npc_i2", kind: "GOSSIP", sourceMemoryId: "mem_902", sourceEventId: inc.eventId, subjectCharacterId: "c_ace", summary: "Ace is running jobs again", hop: 1 }, at - 4100));
  e.push(ev("DECISION_RESOLVED", "npc_i2", resolved("dec_i2", "npc_i2", "INFORMANT", "relay", ["relay", "withhold"], "fallback", null), at - 3400));
  e.push(ev("AGENT_MESSAGE_SENT", "npc_i2", { messageId: "msg_33", senderId: "npc_i2", recipientId: "npc_t2", kind: "RELAY", sourceMemoryId: "mem_903", sourceEventId: inc.eventId, subjectCharacterId: "c_ace", summary: "Word is Ace is about to get caught", hop: 2 }, at - 3300));
  e.push(ev("BULLETIN_PUBLISHED", "npc_p1", { bulletinId: "bul_4", characterId: "c_ace", ticker: "ACE-01", lastKnown: { x: 47, z: 60 }, issuedBy: "npc_p1", recipientIds: ROSTER.map((r) => r.id) }, at - 3000));
  e.push(ev("DECISION_RESOLVED", "npc_t2", resolved("dec_t2", "npc_t2", "TRADER", "sell", ["buy", "sell", "hold"], "drex", { buy: 0.04, sell: 0.88, hold: 0.08 }), at - 2300));
  e.push(ev("TRADE_EXECUTED", "npc_t2", { tradeId: "trd_live", commandId: "c_live", walletId: "w_t2", actorId: "npc_t2", actorName: "Rook Halvorsen", actorKind: "NPC", ticker: "ACE-01", side: "SELL", qty: 1, executionPrice: Math.max(1, Math.floor(ACE.spot * 0.97)), balanceAfter: 1931, sharesAfter: 0 }, at - 2200));
  e.push(ev("MARKET_PRICE_UPDATED", "exchange", { ticker: "ACE-01", referenceValue: ACE.reference, spotPrice: ACE.spot, previousSpotPrice: ACE.spot + 9, reason: "TRADE", heat: 86, netBuyUnits: -2 }, at - 2200));
  e.push(ev("DECISION_RESOLVED", "npc_p1", resolved("dec_p1", "npc_p1", "POLICE", "pursue", ["investigate", "patrol", "pursue"], "drex", { investigate: 0.11, patrol: 0.03, pursue: 0.86 }), at - 1500));
  e.push(ev("AGENT_ACTION_APPLIED", "npc_p1", { decisionId: "dec_p1", agentId: "npc_p1", role: "POLICE", action: "pursue", decisionSource: "drex", detail: "Sgt. Imani Duarte is chasing ACE-01" }, at - 1500));
  return e;
}
function captureEvents(c: { characterId: string; ticker: string; sim: typeof ACE; life: number }, human: boolean, at: number): GameEvent[] {
  const bounty = human ? Math.max(50, Math.round(0.5 * c.sim.reference)) : 0;
  const cap = ev("CAPTURE_SUCCEEDED", human ? "p_rio" : "npc_p1", {
    captureId: `cap_${c.ticker}`, commandId: `cmd_${c.ticker}`, characterId: c.characterId, ticker: c.ticker, captorId: human ? "p_rio" : "npc_p1",
    captorName: human ? "Rio" : "Sgt. Imani Duarte", captorKind: human ? "HUMAN" : "NPC", captorWalletId: human ? "w_rio" : null, bounty,
    captorBalanceAfter: human ? 2000 + bounty : null, referenceAtCapture: c.sim.reference, spotBeforeCapture: c.sim.spot, survivalSeconds: c.life,
  }, at);
  return [
    cap,
    ev("LISTING_HALTED", "exchange", { ticker: c.ticker, characterId: c.characterId, status: "HALTED_CAPTURED", finalSpotBeforeHalt: c.sim.spot }, at, cap.eventId),
    ev("MARKET_PRICE_UPDATED", "exchange", { ticker: c.ticker, referenceValue: c.sim.reference, spotPrice: 0, previousSpotPrice: c.sim.spot, reason: "CAPTURE", heat: 86, netBuyUnits: 0 }, at, cap.eventId),
  ];
}
const ACE_C = { characterId: "c_ace", ticker: "ACE-01", sim: ACE, life: LIFE };
const BLZ_C = { characterId: "c_blz", ticker: "BLZ-02", sim: BLZ, life: 118 };

const METRICS: MetricsView = {
  runId: RUN, mode: "drex", seed: 42, startedAtMs: T0,
  decisions: { total: 41, bySource: { drex: 33, fallback: 5, drex_low_confidence_guardrail: 3 }, byRole: { WITNESS: 12, INFORMANT: 9, TRADER: 11, POLICE: 9 }, byAction: { report: 6, gossip: 5, relay: 7, sell: 4, pursue: 3 }, rejected: 1, stale: 0 },
  latency: { drex: { count: 33, meanMs: 318, p50Ms: 301, p95Ms: 462, maxMs: 611, evalP50Ms: 204 }, meta: null },
  messages: { sent: 19, delivered: 19, maxHops: 2 }, trades: { executed: 8, rejected: 1, byNpc: 6, byHuman: 2 }, priceUpdates: 96, captures: 0,
  edges: [{ from: "npc_w1", to: "npc_p1", kind: "REPORT", count: 3 }, { from: "npc_w2", to: "npc_i2", kind: "GOSSIP", count: 4 }, { from: "npc_i2", to: "npc_t2", kind: "RELAY", count: 4 }],
};
function snapshot(events: GameEvent[], listings: ListingView[], characters: CharacterView[], serverTimeMs: number): RoomSnapshot {
  return {
    roomId: "room_1", roomCode: ROOM, runId: RUN, seed: 42, ruleVersion: "h0.2", tick: Math.round((serverTimeMs - T0) / 100), serverTimeMs,
    lastEventSequence: events.length ? events[events.length - 1]!.serverSequence : 0, mode: "drex",
    providerStatus: { mode: "drex", drex: health("LIVE", "drex-1"), meta: health("UNCONFIGURED", null), pending: 2, badge: "DREX: LIVE" }, map: CITY_MAP,
    players: [
      { id: "p_ace", nickname: "Ace", role: "OUTLAW", connected: true, characterId: "c_ace", isBot: false },
      { id: "p_rio", nickname: "Rio", role: "HUNTER", connected: true, characterId: "c_rio", isBot: false },
      { id: "p_blz", nickname: "Blaze", role: "OUTLAW", connected: true, characterId: "c_blz", isBot: false },
    ],
    characters, npcs: buildNpcs(), missions: [{ id: "m_7", ...MISSION_SPOTS[1]!, status: "AVAILABLE", completedByCharacterId: null }],
    listings, trades: [
      { tradeId: "trd_24", atMs: T0 + 24000, actorId: "npc_t1", actorName: "Priya Castellan", actorKind: "NPC", ticker: "ACE-01", side: "BUY", qty: 1, executionPrice: 151 },
      { tradeId: "trd_101", atMs: T0 + 101000, actorId: "npc_t1", actorName: "Priya Castellan", actorKind: "NPC", ticker: "ACE-01", side: "BUY", qty: 1, executionPrice: 316 },
      { tradeId: "trd_158", atMs: T0 + 158000, actorId: "npc_t2", actorName: "Rook Halvorsen", actorKind: "NPC", ticker: "ACE-01", side: "SELL", qty: 1, executionPrice: 240 },
    ],
    recentEvents: events, metrics: METRICS, experiments: [], policy: null,
  };
}

// ---------------------------------------------------------------- boot the store exactly like a join
const characters = (): CharacterView[] => [
  chr("c_ace", "p_ace", "Ace", "OUTLAW", "ACE-01", 50, 61, 86, 5, T0),
  chr("c_rio", "p_rio", "Rio", "HUNTER", null, 53, 63, 0, 0, T0 + 9000),
  chr("c_blz", "p_blz", "Blaze", "OUTLAW", "BLZ-02", 79, 26, 41, 2, NOW - 118000),
];
const store = useGame.getState;
const log: string[] = [];
const note = (s: string): void => { log.push(s); window.dispatchEvent(new Event("crash-preview-log")); };

const base = historyEvents();
let listings = [listing("ACE-01", "c_ace", "Ace", ACE, T0 + 400), listing("BLZ-02", "c_blz", "Blaze", BLZ, NOW - 118000)];
let chars = characters();
if (OLD) {
  // The capture happened before this page opened: it is part of the join snapshot.
  base.push(...busyEvents(NOW - 30000), ...captureEvents(ACE_C, HUMAN, NOW - 30000));
  listings = [halted(listings[0]!, NOW - 30000), listings[1]!];
  chars = chars.map((c) => (c.id === "c_ace" ? { ...c, status: "CAPTURED" } : c));
  note("join snapshot already contains the ACE-01 capture → nothing may play");
}
const join: JoinResult = {
  roomCode: ROOM, playerId: "p_rio", sessionToken: "preview", role: "HUNTER", characterId: "c_rio", walletId: "w_rio", isDirector: true,
  snapshot: snapshot(base, listings, chars, NOW),
  wallet: { walletId: "w_rio", ownerId: "p_rio", balanceCredits: 1614, holdings: [{ ticker: "ACE-01", shares: 2, costBasis: 386, marketValue: ACE.spot * 2 }], netWorth: 1614 + ACE.spot * 2 },
};
useGame.setState({ conn: "connected" });
store().applyJoin(join, "Rio");

let simNow = NOW;
function applyLive(events: GameEvent[]): void { for (const e of events) store().applyEvent(e); }
function pushCapture(c: typeof ACE_C, human: boolean): void {
  applyLive(captureEvents(c, human, simNow));
  // what the server flushes right after: the halted listing and the captured character
  const l = store().listings[c.ticker];
  if (l) useGame.setState((st) => ({ listings: { ...st.listings, [c.ticker]: halted(l, simNow) } }));
  useGame.setState((st) => ({ characters: { ...st.characters, [c.characterId]: { ...st.characters[c.characterId]!, status: "CAPTURED", captureTimeMs: simNow, capturedBy: human ? "p_rio" : "npc_p1" } } }));
  note(`live capture of ${c.ticker} by ${human ? "Rio (human hunter)" : "Sgt. Imani Duarte (police NPC)"}`);
}
function tick(): void {
  simNow += 100;
  const k = (simNow - NOW) / 1000;
  const t: WorldTick = {
    tick: Math.round((simNow - T0) / 100), serverTimeMs: simNow,
    characters: Object.values(store().characters).map((c) => ({ id: c.id, x: c.id === "c_ace" && c.status !== "CAPTURED" ? 50 + Math.min(6, k * 2) : c.x, z: c.z, heat: c.heat, status: c.status })),
    npcs: ROSTER.map((r) => ({ id: r.id, x: r.id === "npc_p1" ? Math.min(55, 49 + k * 2.4) : r.x, z: r.z, activity: r.id === "npc_p1" ? "PURSUE" : r.id === "npc_p2" ? "INVESTIGATE" : "IDLE", pending: (r.id === "npc_t1" || r.id === "npc_i1") && Math.floor(k * 2) % 3 === 0 })),
  };
  store().applyTick(t);
}

function Preview(): ReactElement {
  const [App, setApp] = useState<ComponentType | null>(null);
  const [appFailed, setAppFailed] = useState(false);
  const [, bump] = useState(0);
  useEffect(() => {
    const on = (): void => bump((n) => n + 1);
    window.addEventListener("crash-preview-log", on);
    return () => window.removeEventListener("crash-preview-log", on);
  }, []);
  useEffect(() => {
    if (BARE) return;
    let live = true;
    import("../../App").then((m) => { if (live) setApp(() => m.App); }).catch((err: unknown) => { if (live) setAppFailed(true); note(`App backdrop unavailable: ${String(err)}`); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    const ticker = setInterval(tick, 100);
    if (!OLD) {
      const busy = busyEvents(NOW + AT);
      // Spread the busy stretch over the wait so the timeline and inspector are visibly working.
      busy.forEach((e, i) => timers.push(setTimeout(() => store().applyEvent(e), Math.max(0, AT - 40) * (i / busy.length))));
      if (RESYNC) {
        timers.push(setTimeout(() => {
          // A reconnect: the capture of BLZ-02 happened while we were away and comes back inside a snapshot.
          const missed = captureEvents(BLZ_C, true, simNow - 2000);
          const st = store();
          store().applySnapshot(snapshot([...st.events, ...missed], Object.values(st.listings).map((l) => (l.ticker === "BLZ-02" ? halted(l, simNow - 2000) : l)), Object.values(st.characters), simNow));
          note("resync snapshot delivered a BLZ-02 capture we never saw live → nothing may play");
        }, Math.max(20, AT - 30)));
        timers.push(setTimeout(() => pushCapture(ACE_C, HUMAN), AT + 1200));
      } else {
        timers.push(setTimeout(() => {
          pushCapture(ACE_C, HUMAN);
          if (DOUBLE) pushCapture(BLZ_C, !HUMAN);
        }, AT));
      }
    }
    return () => { clearInterval(ticker); timers.forEach(clearTimeout); };
  }, []);
  return (
    <>
      {App ? <App /> : <div style={{ height: "100%", background: "radial-gradient(900px 500px at 50% 0%, #2a1650 0%, #0b0c16 70%)" }} />}
      {(OWN || appFailed) && <CrashCinematic freezeAtMs={FREEZE} reducedMotion={CALM} />}
      <pre data-preview-log style={{ position: "fixed", left: 8, bottom: 8, zIndex: 8000, margin: 0, padding: "6px 10px", font: "12px ui-monospace, Menlo, monospace", color: "#9ff", background: "rgba(0,0,0,.72)", borderRadius: 6, pointerEvents: "none", maxWidth: "46vw", whiteSpace: "pre-wrap" }}>
        {`CrashCinematic preview · events ${store().events.length} · overlay ${document.querySelector(".cc-root") ? "ON" : "off"}\n${log.join("\n")}`}
      </pre>
    </>
  );
}

// Console / CDP handle: fire more captures by hand.
(window as unknown as { __crashPreview?: unknown }).__crashPreview = { ace: (human = false) => pushCapture(ACE_C, human), blaze: (human = true) => pushCapture(BLZ_C, human), log };

createRoot(document.getElementById("root")!).render(<StrictMode><Preview /></StrictMode>);
