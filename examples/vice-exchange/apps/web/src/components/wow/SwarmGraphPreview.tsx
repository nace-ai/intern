// Dev-only harness for SwarmGraph: a scripted mock feed pushed through the REAL zustand store (no server), so the
// component runs exactly as it will in the app. Open /src/components/wow/SwarmGraphPreview.html
//   ?w=420&h=320   fixed-size box (default: fill the window)      ?loop=0   play the scenario once
//   ?quiet=1       no scenario, just the seeded state             ?tick=0   do not simulate the 10 Hz world tick
//   ?live=1&room=VICE-42   no mock at all: join the real server as a spectator through the normal socket client
import { Profiler } from "react";
import { createRoot } from "react-dom/client";
import { CITY_MAP } from "@vice/contracts";
import type {
  AgentView, CharacterView, DecisionSource, DecisionTrace, EventPayloads, EventType, GameEvent, ListingView, MemoryEntry, MessageKind,
  MetricsView, NpcRole, RoomSnapshot, TradeView,
} from "@vice/contracts";
import "../../styles.css";
import SwarmGraph from "./SwarmGraph";
import { useGame } from "../../state/gameStore";
import { joinRoom } from "../../net/socketClient";

// Other modules are being edited while this page is open: a hot update must restart the page, not re-run this module.
if (import.meta.hot) import.meta.hot.accept(() => location.reload());
const q = new URLSearchParams(location.search);
const ROOM = "VICE-42";
let seq = 100, runNo = 0, runId = "run_mock_0", decSeq = 0, msgSeq = 0, memSeq = 0, tradeSeq = 0, simMs = 0;
const g = useGame.getState;

const mem = (agentId: string, sourceKind: MemoryEntry["sourceKind"], summary: string): MemoryEntry => ({
  id: `mem_${++memSeq}`, agentId, factType: sourceKind === "BULLETIN" ? "WANTED_BULLETIN" : sourceKind === "MESSAGE" ? "RUMOR" : "INCIDENT",
  subjectCharacterId: "c_ace", subjectTicker: "ACE-01", eventId: "evt", sourceActorId: agentId, sourceKind, sourceMemoryId: null,
  hop: sourceKind === "DIRECT" ? 0 : 1, observedAtMs: simMs, expiresAtMs: simMs + 120000, confidence: 0.9, summary, payload: {}, citation: null,
});
function trace(agentId: string, name: string, role: NpcRole, action: string, source: DecisionSource, offered: string[], confidence: number | null,
  probabilities: Record<string, number> | null, id = `dec_${++decSeq}`): DecisionTrace {
  const real = source === "drex" || source === "meta" || source === "drex_low_confidence_guardrail";
  return {
    id, roomId: "room", runId, agentId, agentName: name, role, status: source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED",
    mode: source === "meta" ? "meta" : source === "scripted" ? "scripted" : "drex", decisionSource: source,
    provider: real ? (source === "meta" ? "meta" : "drex") : null, model: real ? (source === "meta" ? "muse-ember-1.0" : "drex-max-v1.1") : null,
    question: "", offeredActions: Object.fromEntries(offered.map((o) => [o, o])), chosenAction: action, providerChoice: real ? action : null,
    confidence, probabilities, providerRequestId: real ? "req_mock" : null, latencyMs: real ? (source === "meta" ? 1196 : 343) : null,
    providerEvalMs: source === "meta" ? null : real ? 133.7 : null, usage: null, attempts: 1, errorType: source === "fallback" ? "timeout" : null,
    errorMessage: null, rejectReason: null, stateSnapshot: {}, stateHash: "", triggerEventId: "", correlationId: "", effectEventIds: [],
    requestedAtMs: simMs, resolvedAtMs: simMs, requestedAtSequence: seq, citations: [], shadow: null,
  };
}
const NAMES: Record<string, [string, NpcRole, string[]]> = {
  npc_w1: ["Marisol Vega", "WITNESS", ["npc_i1"]], npc_w2: ["Otis Brandt", "WITNESS", ["npc_i2"]],
  npc_i1: ["Lena Okoye", "INFORMANT", ["npc_t1", "npc_t2"]], npc_i2: ["Dex Marlowe", "INFORMANT", ["npc_t2"]],
  npc_t1: ["Priya Castellan", "TRADER", []], npc_t2: ["Rook Halvorsen", "TRADER", []],
  npc_p1: ["Sgt. Imani Duarte", "POLICE", []], npc_p2: ["Ofc. Cole Renner", "POLICE", []],
};
function npc(id: string, memories: MemoryEntry[], last: DecisionTrace | null): AgentView {
  const [name, role, contacts] = NAMES[id]!;
  return {
    id, name, role, goal: "", persona: "", x: 40, z: 40, activity: role === "POLICE" ? "PATROL" : "IDLE", targetCharacterId: null,
    walletCredits: role === "TRADER" ? 2000 : null, holdings: id === "npc_t2" ? { "ACE-01": 2 } : {}, contacts, memories,
    pendingDecisionId: null, lastDecision: last, recentDecisions: last ? [last] : [],
  };
}
const chr = (id: string, nickname: string, ticker: string, spawn: number): CharacterView => ({
  id, playerId: `p_${id}`, nickname, role: "OUTLAW", ticker, status: "ACTIVE", x: 60, z: 68, heat: 0, wantedLevel: 0, wanted: false,
  notorietyPoints: 2, completedMissions: 1, successfulEscapes: 0, spawnTimeMs: spawn, captureTimeMs: null, capturedBy: null,
});
const listing = (ticker: string, characterId: string, nickname: string, spot: number): ListingView => ({
  ticker, characterId, nickname, status: "ACTIVE", referenceValue: spot + 20, spotPrice: spot, bid: Math.floor(spot * 0.97), ask: spot,
  history: [], volume: 3, netBuyUnits: 1, openedAtMs: characterId === "c_ace" ? 1 : 2, haltedAtMs: null, finalSpotBeforeHalt: null, currentBounty: 90,
});
const trade = (actorId: string, actorName: string, kind: "NPC" | "HUMAN", ticker: string, side: "BUY" | "SELL", price: number): TradeView => ({
  tradeId: `trd_${++tradeSeq}`, atMs: simMs, actorId, actorName, actorKind: kind, ticker, side, qty: 1, executionPrice: price,
});

let metrics: MetricsView;
function seed(): RoomSnapshot {
  runId = `run_mock_${runNo++}`; simMs = 0;
  const npcs: AgentView[] = [
    npc("npc_w1", [mem("npc_w1", "DIRECT", "Saw a pickup at the dock")], trace("npc_w1", "Marisol Vega", "WITNESS", "gossip", "drex", ["report", "ignore", "gossip"], 0.71, { report: 0.14, ignore: 0.01, gossip: 0.85 })),
    npc("npc_w2", [mem("npc_w2", "DIRECT", "Saw a drop in Neon Plaza"), mem("npc_w2", "BULLETIN", "Old bulletin")], trace("npc_w2", "Otis Brandt", "WITNESS", "gossip", "drex", ["report", "ignore", "gossip"], 0.83, { report: 0.08, ignore: 0.01, gossip: 0.91 })),
    npc("npc_i1", [mem("npc_i1", "MESSAGE", "Tip from Marisol")], trace("npc_i1", "Lena Okoye", "INFORMANT", "relay", "meta", ["relay", "withhold"], null, null)),
    npc("npc_i2", [mem("npc_i2", "MESSAGE", "Tip from Otis"), mem("npc_i2", "MESSAGE", "Second tip from Otis")], trace("npc_i2", "Dex Marlowe", "INFORMANT", "relay", "drex", ["relay", "withhold"], 0.62, { relay: 0.81, withhold: 0.19 })),
    npc("npc_t1", [mem("npc_t1", "BULLETIN", "ACE-01 listed"), mem("npc_t1", "MESSAGE", "Relayed tip")], trace("npc_t1", "Priya Castellan", "TRADER", "hold", "scripted", ["hold", "buy"], null, null)),
    npc("npc_t2", [mem("npc_t2", "BULLETIN", "ACE-01 listed"), mem("npc_t2", "MESSAGE", "Relayed tip"), mem("npc_t2", "MESSAGE", "Relayed tip 2")], trace("npc_t2", "Rook Halvorsen", "TRADER", "buy", "drex", ["hold", "buy"], 0.58, { hold: 0.21, buy: 0.79 })),
    npc("npc_p1", [], null),
    npc("npc_p2", [mem("npc_p2", "BULLETIN", "Old bulletin")], trace("npc_p2", "Ofc. Cole Renner", "POLICE", "patrol", "fallback", ["investigate", "patrol"], null, null)),
  ];
  metrics = {
    runId, mode: "drex", seed: 42, startedAtMs: 0,
    decisions: { total: 7, bySource: { drex: 4, meta: 1, scripted: 1, fallback: 1 }, byRole: {}, byAction: {}, rejected: 0, stale: 0 },
    latency: { drex: null, meta: null }, messages: { sent: 7, delivered: 7, maxHops: 2 }, trades: { executed: 3, rejected: 0, byNpc: 3, byHuman: 0 },
    priceUpdates: 12, captures: 0,
    edges: [
      { from: "npc_w2", to: "npc_i2", kind: "GOSSIP", count: 2 }, { from: "npc_i2", to: "npc_t2", kind: "RELAY", count: 2 },
      { from: "npc_w1", to: "npc_i1", kind: "GOSSIP", count: 1 }, { from: "npc_i1", to: "npc_t1", kind: "RELAY", count: 1 }, { from: "npc_i1", to: "npc_t2", kind: "RELAY", count: 1 },
    ],
  };
  return {
    roomId: "room", roomCode: ROOM, runId, seed: 42, ruleVersion: "h0.2", tick: 0, serverTimeMs: 0, lastEventSequence: seq, mode: "drex",
    providerStatus: { mode: "drex", pending: 0, badge: "DREX: LIVE",
      drex: { configured: true, state: "LIVE", model: "drex-max-v1.1", lastOkAtMs: 0, lastErrorType: null, callsLastMinute: 4, maxCallsPerMinute: 100, inFlight: 0, circuitOpenUntilMs: null },
      meta: { configured: true, state: "LIVE", model: "muse-ember-1.0", lastOkAtMs: 0, lastErrorType: null, callsLastMinute: 0, maxCallsPerMinute: 60, inFlight: 0, circuitOpenUntilMs: null } },
    map: CITY_MAP,
    players: [{ id: "p_c_ace", nickname: "Ace", role: "OUTLAW", connected: true, characterId: "c_ace", isBot: false }, { id: "p_c_blz", nickname: "Blaze", role: "OUTLAW", connected: true, characterId: "c_blz", isBot: false },
      { id: "p_rio", nickname: "Rio", role: "HUNTER", connected: true, characterId: null, isBot: false }],
    characters: [chr("c_ace", "Ace", "ACE-01", 1), chr("c_blz", "Blaze", "BLA-02", 2)], npcs, missions: [],
    listings: [listing("ACE-01", "c_ace", "Ace", 182), listing("BLA-02", "c_blz", "Blaze", 131)],
    trades: [trade("npc_t2", "Rook Halvorsen", "NPC", "ACE-01", "BUY", 160), trade("npc_t2", "Rook Halvorsen", "NPC", "ACE-01", "BUY", 166), trade("npc_t1", "Priya Castellan", "NPC", "ACE-01", "BUY", 171)],
    recentEvents: [], metrics, experiments: [], policy: null,
  };
}

// ---- pushes, shaped like the socket client's ----
// Mock causation, wired like the engine: observed/delivered/bulletin -> decision -> its effects (message, trade).
let cause: string | null = null;
const heard: Record<string, string> = {}, sentEvent: Record<string, string> = {};
function caused<R>(by: string | null | undefined, run: () => R): R { const prev = cause; cause = by ?? null; try { return run(); } finally { cause = prev; } }
function ev<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T]): GameEvent<T> {
  const e: GameEvent<T> = { schemaVersion: 1, eventId: `evt_${++seq}`, roomId: "room", runId, serverSequence: seq, time: new Date().toISOString(), simTimeMs: simMs, type, actorId, causationId: cause, correlationId: "story_1", payload };
  g().applyEvent(e as unknown as GameEvent);
  return e;
}
const agent = (id: string, patch: Partial<AgentView>): void => useGame.setState((st) => ({ npcs: { ...st.npcs, [id]: { ...st.npcs[id]!, ...patch } } }));
const character = (id: string, patch: Partial<CharacterView>): void => useGame.setState((st) => ({ characters: { ...st.characters, [id]: { ...st.characters[id]!, ...patch } } }));
const setListing = (t: string, patch: Partial<ListingView>): void => useGame.setState((st) => ({ listings: { ...st.listings, [t]: { ...st.listings[t]!, ...patch } } }));
const learn = (id: string, kind: MemoryEntry["sourceKind"], text: string): void => agent(id, { memories: [mem(id, kind, text), ...g().npcs[id]!.memories] });

function request(id: string, offered: string[]): string {
  const [, role] = NAMES[id]!; const decisionId = `dec_${++decSeq}`;
  caused(heard[id], () => ev("DECISION_REQUESTED", id, { decisionId, agentId: id, role, mode: "drex", offeredActions: offered, triggerEventId: "", stateHash: "" }));
  agent(id, { pendingDecisionId: decisionId });
  return decisionId;
}
function resolve(id: string, decisionId: string, action: string, source: DecisionSource, offered: string[], confidence: number | null, effect?: () => void): void {
  const [name, role] = NAMES[id]!;
  const rest = offered.filter((o) => o !== action);
  const top = confidence === null ? 0 : source === "drex_low_confidence_guardrail" ? 0.5 : Math.min(0.995, 0.5 + confidence / 2);
  const probabilities = confidence === null ? null : Object.fromEntries([[action, top], ...rest.map((o, i) => [o, (1 - top) * (i === 0 ? 0.8 : 0.2 / Math.max(1, rest.length - 1))])]) as Record<string, number>;
  const t = trace(id, name, role, action, source, offered, confidence, probabilities, decisionId);
  const resolved = caused(heard[id], () => ev("DECISION_RESOLVED", id, { decisionId, agentId: id, role, status: t.status, decisionSource: source, provider: t.provider, model: t.model, chosenAction: action, providerChoice: t.providerChoice,
    offeredActions: offered, confidence, probabilities, providerRequestId: t.providerRequestId, latencyMs: t.latencyMs, providerEvalMs: t.providerEvalMs, errorType: t.errorType, rejectReason: null }));
  caused(resolved.eventId, () => {
    effect?.();
    ev("AGENT_ACTION_APPLIED", id, { decisionId, agentId: id, role, action, decisionSource: source, detail: action });
  });
  agent(id, { pendingDecisionId: null, lastDecision: t, recentDecisions: [t, ...g().npcs[id]!.recentDecisions].slice(0, 8) });
  metrics = { ...metrics, decisions: { ...metrics.decisions, total: metrics.decisions.total + 1, bySource: { ...metrics.decisions.bySource, [source]: (metrics.decisions.bySource[source] ?? 0) + 1 } } };
}
function send(from: string, to: string, kind: MessageKind, hop: number): string {
  const messageId = `msg_${++msgSeq}`;
  sentEvent[messageId] = ev("AGENT_MESSAGE_SENT", from, { messageId, senderId: from, recipientId: to, kind, sourceMemoryId: null, sourceEventId: "", subjectCharacterId: "c_ace", summary: "", hop }).eventId;
  const edges = metrics.edges.map((e) => ({ ...e }));
  const hit = edges.find((e) => e.from === from && e.to === to && e.kind === kind);
  if (hit) hit.count++; else edges.push({ from, to, kind, count: 1 });
  metrics = { ...metrics, edges, messages: { ...metrics.messages, sent: metrics.messages.sent + 1, maxHops: Math.max(metrics.messages.maxHops, hop) } };
  return messageId;
}
function deliver(messageId: string, from: string, to: string, kind: MessageKind, hop: number): void {
  heard[to] = caused(sentEvent[messageId], () => ev("AGENT_MESSAGE_DELIVERED", to, { messageId, senderId: from, recipientId: to, kind, memoryId: "", hop })).eventId;
  learn(to, "MESSAGE", "Told about the incident");
  metrics = { ...metrics, messages: { ...metrics.messages, delivered: metrics.messages.delivered + 1 } };
}
function npcTrade(id: string, ticker: string, side: "BUY" | "SELL", price: number): void {
  const [name] = NAMES[id]!; const t = trade(id, name, "NPC", ticker, side, price);
  ev("TRADE_EXECUTED", id, { tradeId: t.tradeId, commandId: "", walletId: "", actorId: id, actorName: name, actorKind: "NPC", ticker, side, qty: 1, executionPrice: price, balanceAfter: 0, sharesAfter: 0 });
  setListing(ticker, { spotPrice: price + (side === "BUY" ? 4 : -5) });
  metrics = { ...metrics, trades: { ...metrics.trades, executed: metrics.trades.executed + 1, byNpc: metrics.trades.byNpc + 1 } };
}
function bulletin(by: string, characterId: string, ticker: string): void {
  const b = caused(heard[by], () => ev("BULLETIN_PUBLISHED", by, { bulletinId: `bul_${seq}`, characterId, ticker, lastKnown: { x: 47, z: 60 }, issuedBy: by, recipientIds: Object.keys(NAMES) }));
  for (const id of Object.keys(NAMES)) if (id !== by) heard[id] = b.eventId;
  ev("WANTED_STATUS_CHANGED", characterId, { characterId, ticker, wanted: true, heat: 42, wantedLevel: 2, reason: "police bulletin" });
  character(characterId, { status: "WANTED", wanted: true, heat: 42, wantedLevel: 2 });
  for (const id of Object.keys(NAMES)) learn(id, "BULLETIN", `Bulletin: ${ticker} is wanted`);
}
function observe(id: string, characterId: string, factType: "INCIDENT" | "SIGHTING"): void {
  heard[id] = ev("NPC_OBSERVED", id, { npcId: id, memoryId: "", factType, subjectCharacterId: characterId, distance: 9, incidentId: null, summary: "" }).eventId;
  learn(id, "DIRECT", factType === "INCIDENT" ? "Saw the job first-hand" : "Has eyes on the suspect");
}

const W = ["report", "ignore", "gossip"], I = ["relay", "withhold"], T = ["hold", "buy", "sell"], P = ["investigate", "patrol"], PP = ["investigate", "patrol", "pursue"];
function scenario(): Array<[number, () => void]> {
  const d: Record<string, string> = {}, m: Record<string, string> = {};
  return [
    [700, () => {
      ev("MISSION_COMPLETED", "p_c_ace", { missionId: "m1", characterId: "c_ace", ticker: "ACE-01", x: 47, z: 60, notorietyGained: 2, heatGained: 12, notorietyPoints: 4, completedMissions: 2, heat: 12 });
      ev("INCIDENT_OCCURRED", "c_ace", { incidentId: "inc1", kind: "MISSION", characterId: "c_ace", ticker: "ACE-01", x: 47, z: 60, description: "Marked package grabbed at the dock", observerIds: ["npc_w1", "npc_w2"] });
      character("c_ace", { heat: 12 });
      observe("npc_w1", "c_ace", "INCIDENT"); d.w1 = request("npc_w1", W);
      observe("npc_w2", "c_ace", "INCIDENT"); d.w2 = request("npc_w2", W);
    }],
    [1150, () => resolve("npc_w1", d.w1!, "report", "drex", W, 0.99, () => { m.a = send("npc_w1", "npc_p1", "REPORT", 1); })],
    [1320, () => resolve("npc_w2", d.w2!, "gossip", "drex", W, 0.76, () => { m.b = send("npc_w2", "npc_i2", "GOSSIP", 1); })],
    [2060, () => {
      deliver(m.a!, "npc_w1", "npc_p1", "REPORT", 1);
      bulletin("npc_p1", "c_ace", "ACE-01");
      d.p1 = request("npc_p1", P); d.t1 = request("npc_t1", T); d.t2 = request("npc_t2", T); d.p2 = request("npc_p2", P);
    }],
    [2230, () => { deliver(m.b!, "npc_w2", "npc_i2", "GOSSIP", 1); d.i2 = request("npc_i2", I); }],
    [2470, () => resolve("npc_p1", d.p1!, "investigate", "drex", P, 0.91, () => agent("npc_p1", { activity: "INVESTIGATE", targetCharacterId: "c_ace" }))],
    [2580, () => resolve("npc_i2", d.i2!, "relay", "drex", I, 0.88, () => { m.c = send("npc_i2", "npc_t2", "RELAY", 2); })],
    [2680, () => resolve("npc_t1", d.t1!, "hold", "drex_low_confidence_guardrail", T, 0.06)],
    [2820, () => resolve("npc_t2", d.t2!, "sell", "drex", T, 0.93, () => npcTrade("npc_t2", "ACE-01", "SELL", 176))],
    [2960, () => resolve("npc_p2", d.p2!, "patrol", "fallback", P, null)],
    [3500, () => { deliver(m.c!, "npc_i2", "npc_t2", "RELAY", 2); d.t2 = request("npc_t2", T); }],
    [3860, () => resolve("npc_t2", d.t2!, "sell", "drex", T, 0.97, () => npcTrade("npc_t2", "ACE-01", "SELL", 168))],
    [4400, () => {
      const t = trade("p_rio", "Rio", "HUMAN", "BLA-02", "BUY", 131);
      ev("TRADE_EXECUTED", "p_rio", { tradeId: t.tradeId, commandId: "", walletId: "", actorId: "p_rio", actorName: "Rio", actorKind: "HUMAN", ticker: "BLA-02", side: "BUY", qty: 1, executionPrice: 131, balanceAfter: 0, sharesAfter: 1 });
      setListing("BLA-02", { spotPrice: 135 });
      metrics = { ...metrics, trades: { ...metrics.trades, executed: metrics.trades.executed + 1, byHuman: metrics.trades.byHuman + 1 } };
    }],
    [5000, () => { observe("npc_p1", "c_ace", "SIGHTING"); d.p1 = request("npc_p1", PP); }],
    [5360, () => resolve("npc_p1", d.p1!, "pursue", "drex", PP, 0.97, () => agent("npc_p1", { activity: "PURSUE", targetCharacterId: "c_ace" }))],
    [5700, () => { observe("npc_p2", "c_ace", "SIGHTING"); d.p2 = request("npc_p2", PP); }],
    [6200, () => resolve("npc_p2", d.p2!, "pursue", "meta", PP, null, () => agent("npc_p2", { activity: "PURSUE", targetCharacterId: "c_ace" }))],
    [6400, () => { observe("npc_w1", "c_ace", "SIGHTING"); d.w1 = request("npc_w1", W); }],
    [6760, () => resolve("npc_w1", d.w1!, "gossip", "drex", W, 0.69, () => { m.d = send("npc_w1", "npc_i1", "GOSSIP", 1); })],
    [7600, () => {
      ev("CAPTURE_SUCCEEDED", "npc_p1", { captureId: "cap1", commandId: "", characterId: "c_ace", ticker: "ACE-01", captorId: "npc_p1", captorName: "Sgt. Imani Duarte", captorKind: "NPC", captorWalletId: null, bounty: 0, captorBalanceAfter: null, referenceAtCapture: 202, spotBeforeCapture: 163, survivalSeconds: 74 });
      ev("LISTING_HALTED", "exchange", { ticker: "ACE-01", characterId: "c_ace", status: "HALTED_CAPTURED", finalSpotBeforeHalt: 163 });
      ev("MARKET_PRICE_UPDATED", "exchange", { ticker: "ACE-01", referenceValue: 202, spotPrice: 0, previousSpotPrice: 163, reason: "CAPTURE", heat: 60, netBuyUnits: 0 });
      character("c_ace", { status: "CAPTURED", wanted: false, captureTimeMs: simMs, capturedBy: "npc_p1" });
      setListing("ACE-01", { status: "HALTED_CAPTURED", spotPrice: 0, finalSpotBeforeHalt: 163 });
      agent("npc_p1", { activity: "PATROL", targetCharacterId: null }); agent("npc_p2", { activity: "PATROL", targetCharacterId: null });
      metrics = { ...metrics, captures: 1 };
    }],
    [7700, () => { deliver(m.d!, "npc_w1", "npc_i1", "GOSSIP", 1); d.i1 = request("npc_i1", I); }],
    [7760, () => resolve("npc_i1", d.i1!, "withhold", "scripted", I, null)],
    [10200, () => {
      ev("INCIDENT_OCCURRED", "c_blz", { incidentId: "inc2", kind: "MISSION", characterId: "c_blz", ticker: "BLA-02", x: 52, z: 48, description: "Drop bag lifted in Neon Plaza", observerIds: ["npc_w2", "npc_p2"] });
      observe("npc_w2", "c_blz", "INCIDENT"); d.w2 = request("npc_w2", W);
      observe("npc_p2", "c_blz", "INCIDENT"); bulletin("npc_p2", "c_blz", "BLA-02");
      d.p2 = request("npc_p2", PP); d.t1 = request("npc_t1", T); d.t2 = request("npc_t2", T); d.p1 = request("npc_p1", P);
    }],
    [10560, () => resolve("npc_w2", d.w2!, "ignore", "drex", W, 0.64)],
    [10640, () => resolve("npc_p2", d.p2!, "pursue", "drex", PP, 0.95, () => agent("npc_p2", { activity: "PURSUE", targetCharacterId: "c_blz" }))],
    [10760, () => resolve("npc_t1", d.t1!, "buy", "drex", T, 0.81, () => npcTrade("npc_t1", "BLA-02", "BUY", 133))],
    [10880, () => resolve("npc_t2", d.t2!, "hold", "drex", T, 0.88)],
    [11000, () => resolve("npc_p1", d.p1!, "investigate", "drex", P, 0.9, () => agent("npc_p1", { activity: "INVESTIGATE", targetCharacterId: "c_blz" }))],
    [14000, () => {
      ev("ESCAPE_SUCCEEDED", "c_blz", { characterId: "c_blz", ticker: "BLA-02", successfulEscapes: 1 });
      character("c_blz", { status: "ACTIVE", wanted: false, heat: 20, successfulEscapes: 1 });
      agent("npc_p2", { activity: "PATROL", targetCharacterId: null }); agent("npc_p1", { activity: "PATROL", targetCharacterId: null });
    }],
  ];
}

const timers: Array<ReturnType<typeof setTimeout>> = [];
function play(): void {
  g().applySnapshot(seed());
  if (q.get("quiet") === "1") return;
  const t0 = performance.now();
  for (const [at, run] of scenario()) timers.push(setTimeout(() => { simMs = Math.round(performance.now() - t0); run(); }, at));
  if (q.get("loop") !== "0") timers.push(setTimeout(play, 17000));
}
const LIVE = q.get("live") === "1";
// metrics.update arrives once a second in the real app
if (!LIVE) setInterval(() => { if (metrics && g().metrics !== metrics) useGame.setState({ metrics }); }, 1000);
// the 10 Hz world tick (the graph must NOT re-render because of it)
if (!LIVE && q.get("tick") !== "0") {
  let n = 0;
  setInterval(() => {
    n++;
    const st = g();
    st.applyTick({
      tick: n, serverTimeMs: n * 100,
      characters: Object.values(st.characters).map((c) => ({ id: c.id, x: 47 + Math.cos(n / 30) * 12, z: 60 + Math.sin(n / 30) * 7, heat: c.heat, status: c.status })),
      npcs: Object.values(st.npcs).map((a) => ({ id: a.id, x: a.x + Math.sin(n / 20), z: a.z, activity: a.activity, pending: a.pendingDecisionId !== null })),
    });
  }, 100);
}

const stats = { renders: 0 };
(window as unknown as { __sgStats: typeof stats }).__sgStats = stats;
const w = Number(q.get("w")), h = Number(q.get("h"));
const boxed = w > 0 && h > 0;
function Preview() {
  return (
    <div style={{ height: "100%", display: "grid", placeItems: "center", background: boxed ? "#0b0c16" : "#070812" }}>
      <div style={boxed ? { width: w, height: h, border: "1px solid #2a2e52", borderRadius: 10, overflow: "hidden" } : { width: "100%", height: "100%" }}>
        <Profiler id="swarm" onRender={() => { stats.renders++; }}><SwarmGraph /></Profiler>
      </div>
    </div>
  );
}
if (LIVE) {
  const roomCode = q.get("room") ?? ROOM;
  void joinRoom({ create: false, roomCode, nickname: "SwarmCam", role: "SPECTATOR" }).then((ack) => {
    if (!ack.ok) void joinRoom({ create: true, roomCode, nickname: "SwarmCam", role: "SPECTATOR" });
  });
} else play();
createRoot(document.getElementById("root")!).render(<Preview />);
