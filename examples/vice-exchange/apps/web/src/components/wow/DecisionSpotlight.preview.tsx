// Dev-only harness for DecisionSpotlight (not imported by the app).
// Open /src/components/wow/DecisionSpotlight.preview.html?scene=busy|think|gallery1|gallery2|gallery3
// It drives the REAL zustand store with mock events, so the component is exercised exactly as in the app.
import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { CITY_MAP, MISSION_SPOTS } from "@vice/contracts";
import type {
  AgentView, CharacterView, DecisionMode, DecisionSource, DecisionStatus, DecisionTrace, EventPayloads, EventType, GameEvent,
  MissionView, NpcRole, ProviderName,
} from "@vice/contracts";
import "../../styles.css";
import { useGame } from "../../state/gameStore";
import DecisionSpotlight from "./DecisionSpotlight";

const q = new URLSearchParams(location.search);
const SCENE = q.get("scene") ?? "busy";
const DWELL = Number(q.get("dwell") ?? (SCENE === "busy" ? 6000 : 600000));
const RUN = "run_preview";

const npc = (id: string, name: string, role: NpcRole, x: number, z: number, contacts: string[] = []): AgentView => ({
  id, name, role, goal: "", persona: "", x, z, activity: role === "POLICE" ? "PATROL" : "IDLE", targetCharacterId: null,
  walletCredits: role === "TRADER" ? 2000 : null, holdings: {}, contacts, memories: [], pendingDecisionId: null, lastDecision: null, recentDecisions: [],
});
const NPCS: AgentView[] = [
  npc("npc_w1", "Marisol Vega", "WITNESS", 40, 64, ["npc_i1"]), npc("npc_w2", "Otis Brandt", "WITNESS", 55, 45, ["npc_i2"]),
  npc("npc_i1", "Lena Okoye", "INFORMANT", 33, 52, ["npc_t1", "npc_t2"]), npc("npc_i2", "Dex Marlowe", "INFORMANT", 62, 30, ["npc_t2"]),
  npc("npc_t1", "Priya Castellan", "TRADER", 17, 31), npc("npc_t2", "Rook Halvorsen", "TRADER", 30, 41),
  npc("npc_p1", "Sgt. Imani Duarte", "POLICE", 32, 28), npc("npc_p2", "Ofc. Cole Renner", "POLICE", 92, 28),
];
const ROLE_OF: Record<string, NpcRole> = Object.fromEntries(NPCS.map((n) => [n.id, n.role]));
const CHARS: CharacterView[] = [{
  id: "c1", playerId: "p1", nickname: "Ace", role: "OUTLAW", ticker: "ACE-01", status: "WANTED", x: 47, z: 61, heat: 42, wantedLevel: 2, wanted: true,
  notorietyPoints: 2, completedMissions: 1, successfulEscapes: 0, spawnTimeMs: 0, captureTimeMs: null, capturedBy: null,
}];
const MISSIONS: MissionView[] = [{ id: "m1", ...MISSION_SPOTS[1]!, status: "AVAILABLE", completedByCharacterId: null }];

let seq = 40;
function ev<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], causationId: string | null = null): GameEvent<T> {
  seq += 1;
  return { schemaVersion: 1, eventId: `ev_${seq}`, roomId: "room", runId: RUN, serverSequence: seq, time: new Date().toISOString(), simTimeMs: seq * 100, type, actorId, causationId, correlationId: "story", payload };
}
function push<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], causationId: string | null = null): GameEvent<T> {
  const e = ev(type, actorId, payload, causationId);
  useGame.getState().applyEvent(e as unknown as GameEvent);
  return e;
}
const timers: number[] = [];
const later = (ms: number, fn: () => void) => { timers.push(window.setTimeout(fn, ms)); };

interface Result {
  source: DecisionSource; status?: DecisionStatus; chosen: string; providerChoice?: string | null; provider?: ProviderName | null; model?: string | null;
  confidence?: number | null; probabilities?: Record<string, number> | null; requestId?: string | null; latencyMs?: number | null; evalMs?: number | null;
  errorType?: string | null; rejectReason?: string | null;
}
interface Spec {
  agent: string; mode?: DecisionMode; offered: Record<string, string>; trigger: GameEvent; question?: string; knows?: string;
  /** ms until the result arrives; null = never (stays pending) */
  after: number | null; result?: Result; applied?: string; rejected?: string; effects?: (resolved: GameEvent) => void;
  /** also push agent.update style traces into the store (the fuller-detail path) */
  withTrace?: boolean;
}
let decN = 0;
function setTrace(agentId: string, t: DecisionTrace, pending: boolean): void {
  useGame.setState((s) => {
    const a = s.npcs[agentId]!;
    const rest = a.recentDecisions.filter((x) => x.id !== t.id);
    return { npcs: { ...s.npcs, [agentId]: { ...a, pendingDecisionId: pending ? t.id : null, lastDecision: pending ? a.lastDecision : t, recentDecisions: [t, ...rest].slice(0, 8) } } };
  });
}
function decide(s: Spec): void {
  const id = `dec_${++decN}`;
  const role = ROLE_OF[s.agent]!;
  const mode = s.mode ?? "drex";
  const name = NPCS.find((n) => n.id === s.agent)!.name;
  push("DECISION_REQUESTED", s.agent, { decisionId: id, agentId: s.agent, role, mode, offeredActions: Object.keys(s.offered), triggerEventId: s.trigger.eventId, stateHash: "9f2c1a7b4d3e0f61" }, s.trigger.eventId);
  const base: DecisionTrace = {
    id, roomId: "room", runId: RUN, agentId: s.agent, agentName: name, role, status: "REQUESTED", mode, decisionSource: null, provider: null, model: null,
    question: s.question ?? "What do you do about what you know? Use only your own knowledge.", offeredActions: s.offered, chosenAction: null, providerChoice: null,
    confidence: null, probabilities: null, providerRequestId: null, latencyMs: null, providerEvalMs: null, usage: null, attempts: 0,
    errorType: null, errorMessage: null, rejectReason: null, stateSnapshot: s.knows ? { what_just_happened: { summary: s.knows } } : {}, stateHash: "9f2c1a7b4d3e0f61",
    triggerEventId: s.trigger.eventId, correlationId: "story", effectEventIds: [], requestedAtMs: Date.now(), resolvedAtMs: null, requestedAtSequence: seq, citations: [], shadow: null,
  };
  if (s.withTrace) later(70, () => setTrace(s.agent, base, true));
  if (s.after == null || !s.result) return;
  const r = s.result;
  later(s.after, () => {
    const status: DecisionStatus = r.status ?? (r.source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED");
    const provider = r.provider === undefined ? (r.source === "drex" || r.source === "drex_low_confidence_guardrail" ? "drex" : r.source === "meta" ? "meta" : null) : r.provider;
    const model = r.model === undefined ? (provider === "drex" ? "drex-max-v1.1" : provider === "meta" ? "muse-ember-1.0" : null) : r.model;
    const resolved = push("DECISION_RESOLVED", s.agent, {
      decisionId: id, agentId: s.agent, role, status, decisionSource: r.source, provider, model, chosenAction: r.chosen, providerChoice: r.providerChoice ?? null,
      offeredActions: Object.keys(s.offered), confidence: r.confidence ?? null, probabilities: r.probabilities ?? null, providerRequestId: r.requestId ?? null,
      latencyMs: r.latencyMs ?? null, providerEvalMs: r.evalMs ?? null, errorType: r.errorType ?? null, rejectReason: r.rejectReason ?? null,
    }, s.trigger.eventId);
    s.effects?.(resolved as unknown as GameEvent);
    if (s.rejected) push("AGENT_ACTION_REJECTED", s.agent, { decisionId: id, agentId: s.agent, role, action: r.chosen, reason: s.rejected }, resolved.eventId);
    else push("AGENT_ACTION_APPLIED", s.agent, { decisionId: id, agentId: s.agent, role, action: r.chosen, decisionSource: r.source, detail: s.applied ?? r.chosen }, resolved.eventId);
    if (s.withTrace) later(60, () => setTrace(s.agent, {
      ...base, status, decisionSource: r.source, provider, model, chosenAction: r.chosen, providerChoice: r.providerChoice ?? null, confidence: r.confidence ?? null,
      probabilities: r.probabilities ?? null, providerRequestId: r.requestId ?? null, latencyMs: r.latencyMs ?? null, providerEvalMs: r.evalMs ?? null,
      usage: provider ? { inputTokens: 184, outputTokens: 118 } : null, attempts: provider ? 1 : 0, errorType: r.errorType ?? null, rejectReason: r.rejectReason ?? null, resolvedAtMs: Date.now(),
    }, false));
  });
}

const WITNESS_OPTS = { report: "Tell the city police what I know so they can act on it", ignore: "Stay out of it and say nothing for now", gossip: "Quietly tell my informant contact instead of the police" };
const INFORMANT_OPTS = { relay: "Pass the tip to my trader contacts now", withhold: "Keep it to myself; it is too weak or too risky to pass on" };
const POLICE_OPTS = { investigate: "Go to the last known location and look for the suspect", patrol: "Keep to my normal patrol; the information is too weak to act on" };
const POLICE_PURSUE = { ...POLICE_OPTS, pursue: "Chase the suspect I can see right now and attempt an arrest" };
const TRADER_SELL = { hold: "Do nothing; the information does not justify a trade", sell: "Sell one share of ACE-01: I expect capture risk to rise" };
const TRADER_BUY = { hold: "Do nothing; the information does not justify a trade", buy: "Buy one share of ACE-01: I expect its value to rise" };

const observed = (npcId: string, summary: string, distance: number, factType: "INCIDENT" | "SIGHTING" = "INCIDENT") =>
  push("NPC_OBSERVED", npcId, { npcId, memoryId: `mem_${seq}`, factType, subjectCharacterId: "c1", distance, incidentId: "inc_1", summary });
const delivered = (senderId: string, recipientId: string, kind: "REPORT" | "GOSSIP" | "RELAY", hop: number, summary: string) => {
  const sent = push("AGENT_MESSAGE_SENT", senderId, { messageId: `msg_${seq}`, senderId, recipientId, kind, sourceMemoryId: null, sourceEventId: "ev_1", subjectCharacterId: "c1", summary, hop });
  return push("AGENT_MESSAGE_DELIVERED", recipientId, { messageId: sent.payload.messageId, senderId, recipientId, kind, memoryId: `mem_${seq}`, hop }, sent.eventId);
};
const bulletin = () => push("BULLETIN_PUBLISHED", "npc_p1", { bulletinId: `b_${seq}`, characterId: "c1", ticker: "ACE-01", lastKnown: { x: 47, z: 61 }, issuedBy: "npc_p1", recipientIds: ["npc_t1", "npc_t2", "npc_p2"] });

const DREX_REPORT: Result = { source: "drex", chosen: "report", providerChoice: "report", confidence: 0.993, probabilities: { report: 0.995, ignore: 0.003, gossip: 0.002 }, requestId: "req_8322c0de91f43029", latencyMs: 343, evalMs: 133.7 };
const DREX_GOSSIP: Result = { source: "drex", chosen: "gossip", providerChoice: "gossip", confidence: 0.52, probabilities: { report: 0.19, ignore: 0.1, gossip: 0.71 }, requestId: "req_5b01e7aa3c9d7741", latencyMs: 398, evalMs: 151.2 };
const GUARD_HOLD: Result = { source: "drex_low_confidence_guardrail", chosen: "hold", providerChoice: "buy", confidence: 0.06, probabilities: { hold: 0.47, buy: 0.53 }, requestId: "req_1c44b90f2e6a8815", latencyMs: 371, evalMs: 140.4 };
const FALLBACK_TIMEOUT: Result = { source: "fallback", status: "TIMEOUT", chosen: "patrol", provider: "drex", model: null, errorType: "timeout", latencyMs: 3004 };
const META_BUY: Result = { source: "meta", chosen: "buy", providerChoice: "buy", requestId: "ee586985-2b1c-4d0e-9a51-0c9f3e7ab8d2", latencyMs: 1196 };
const SCRIPTED_HOLD: Result = { source: "scripted", chosen: "hold" };
const STALE_SELL: Result = { source: "drex", status: "REJECTED", chosen: "sell", providerChoice: "sell", confidence: 0.84, probabilities: { hold: 0.08, sell: 0.92 }, requestId: "req_77d2fa019b3e5560", latencyMs: 2412, evalMs: 162.9, rejectReason: "subject was captured before the decision arrived" };

function busyStory(): void {
  later(400, () => {
    push("MISSION_COMPLETED", "c1", { missionId: "m0", characterId: "c1", ticker: "ACE-01", x: 47, z: 60, notorietyGained: 2, heatGained: 12, notorietyPoints: 2, completedMissions: 1, heat: 12 });
    const w1 = observed("npc_w1", "Ace grabbed the marked package at the dock", 9);
    const w2 = observed("npc_w2", "Ace ran from the dock carrying a package", 13);
    decide({ agent: "npc_w1", offered: WITNESS_OPTS, trigger: w1, after: 343, result: DREX_REPORT, applied: "reported ACE-01 to Sgt. Imani Duarte", withTrace: true, knows: "Ace grabbed the marked package at the dock" });
    later(60, () => decide({ agent: "npc_w2", offered: WITNESS_OPTS, trigger: w2, after: 398, result: DREX_GOSSIP, applied: "gossiped about ACE-01 to Dex Marlowe" }));
  });
  later(2300, () => {
    const rep = delivered("npc_w1", "npc_p1", "REPORT", 1, "Ace grabbed the marked package at the dock");
    const b = bulletin();
    push("WANTED_STATUS_CHANGED", "server", { characterId: "c1", ticker: "ACE-01", wanted: true, heat: 42, wantedLevel: 2, reason: "police report" });
    decide({ agent: "npc_p1", offered: POLICE_OPTS, trigger: rep, after: 301, result: { source: "drex", chosen: "investigate", providerChoice: "investigate", confidence: 0.86, probabilities: { investigate: 0.93, patrol: 0.07 }, requestId: "req_0aa3915cd27e4b12", latencyMs: 301, evalMs: 121.8 }, applied: "heading to the last known location of ACE-01", withTrace: true });
    later(40, () => decide({
      agent: "npc_t2", offered: TRADER_SELL, trigger: b, after: 356, withTrace: true,
      result: { source: "drex", chosen: "sell", providerChoice: "sell", confidence: 0.56, probabilities: { hold: 0.22, sell: 0.78 }, requestId: "req_c41f7e02a85b9d36", latencyMs: 356, evalMs: 138.3 },
      effects: (r) => { push("TRADE_EXECUTED", "npc_t2", { tradeId: `t_${seq}`, commandId: `c_${seq}`, walletId: "w_t2", actorId: "npc_t2", actorName: "Rook Halvorsen", actorKind: "NPC", ticker: "ACE-01", side: "SELL", qty: 1, executionPrice: 182, balanceAfter: 2182, sharesAfter: 0 }, r.eventId); },
      applied: "sold 1 ACE-01 at V$182",
    }));
    later(80, () => decide({ agent: "npc_t1", offered: TRADER_BUY, trigger: b, after: 371, result: GUARD_HOLD, applied: "holding ACE-01", withTrace: true }));
    later(120, () => decide({ agent: "npc_p2", offered: POLICE_OPTS, trigger: b, after: 3004, result: FALLBACK_TIMEOUT, applied: "staying on patrol" }));
    later(900, () => {
      const g = delivered("npc_w2", "npc_i2", "GOSSIP", 1, "Ace ran from the dock carrying a package");
      decide({ agent: "npc_i2", offered: INFORMANT_OPTS, trigger: g, after: 322, result: { source: "drex", chosen: "relay", providerChoice: "relay", confidence: 0.68, probabilities: { relay: 0.84, withhold: 0.16 }, requestId: "req_e9b2d4410c67f8a3", latencyMs: 322, evalMs: 129.5 }, applied: "relayed the tip about ACE-01 to Rook Halvorsen" });
    });
  });
  later(7600, () => {
    const s = observed("npc_p1", "Ace is in the open on the marina road", 6, "SIGHTING");
    decide({ agent: "npc_p1", offered: POLICE_PURSUE, trigger: s, after: 288, result: { source: "drex", chosen: "pursue", providerChoice: "pursue", confidence: 0.95, probabilities: { investigate: 0.02, patrol: 0.01, pursue: 0.97 }, requestId: "req_3d5e8f7b61a09c24", latencyMs: 288, evalMs: 117.2 }, applied: "pursuing ACE-01", withTrace: true });
    const rl = delivered("npc_i2", "npc_t2", "RELAY", 2, "Ace ran from the dock carrying a package");
    later(300, () => decide({ agent: "npc_t2", offered: TRADER_SELL, trigger: rl, after: 2412, result: STALE_SELL, rejected: "subject was captured before the decision arrived", withTrace: true }));
    later(1900, () => {
      push("CAPTURE_SUCCEEDED", "npc_p1", { captureId: "cap_1", commandId: "c_cap", characterId: "c1", ticker: "ACE-01", captorId: "npc_p1", captorName: "Sgt. Imani Duarte", captorKind: "NPC", captorWalletId: null, bounty: 0, captorBalanceAfter: null, referenceAtCapture: 232, spotBeforeCapture: 176, survivalSeconds: 71 });
      push("LISTING_HALTED", "server", { ticker: "ACE-01", characterId: "c1", status: "HALTED_CAPTURED", finalSpotBeforeHalt: 176 });
    });
  });
  later(12600, () => {
    const l = push("LISTING_OPENED", "server", { ticker: "ACE-02", characterId: "c2", nickname: "Ace", referenceValue: 100, spotPrice: 100 });
    decide({ agent: "npc_t1", mode: "meta", offered: { hold: TRADER_BUY.hold, buy: "Buy one share of ACE-02" }, trigger: l, after: 1196, result: META_BUY, applied: "bought 1 ACE-02 at V$100", withTrace: true });
    later(200, () => decide({ agent: "npc_t2", mode: "scripted", offered: { hold: TRADER_BUY.hold, buy: "Buy one share of ACE-02" }, trigger: l, after: 30, result: SCRIPTED_HOLD, applied: "holding ACE-02" }));
  });
  later(19500, busyStory);
}

function think(): void {
  later(300, () => {
    const w1 = observed("npc_w1", "Ace grabbed the marked package at the dock", 9);
    decide({ agent: "npc_w1", offered: WITNESS_OPTS, trigger: w1, after: null, withTrace: true, knows: "Ace grabbed the marked package at the dock" });
    const b = bulletin();
    later(250, () => decide({ agent: "npc_t2", offered: TRADER_SELL, trigger: b, after: null }));
    later(520, () => decide({ agent: "npc_p2", mode: "meta", offered: POLICE_PURSUE, trigger: b, after: null }));
  });
}
function gallery(specs: Array<() => void>): void {
  specs.forEach((fn, i) => later(300 + i * 260, fn));
}
function gallery1(): void {
  const w1 = () => observed("npc_w1", "Ace grabbed the marked package at the dock", 9);
  gallery([
    () => decide({ agent: "npc_p2", offered: POLICE_OPTS, trigger: bulletin(), after: 500, result: FALLBACK_TIMEOUT, applied: "staying on patrol" }),
    () => decide({ agent: "npc_t1", offered: TRADER_BUY, trigger: bulletin(), after: 371, result: GUARD_HOLD, applied: "holding ACE-01", withTrace: true }),
    () => decide({ agent: "npc_w1", offered: WITNESS_OPTS, trigger: w1(), after: 343, result: DREX_REPORT, applied: "reported ACE-01 to Sgt. Imani Duarte", withTrace: true }),
  ]);
}
function gallery2(): void {
  gallery([
    () => decide({ agent: "npc_t2", mode: "scripted", offered: TRADER_SELL, trigger: bulletin(), after: 40, result: SCRIPTED_HOLD, applied: "holding ACE-01" }),
    () => decide({ agent: "npc_t1", mode: "meta", offered: TRADER_BUY, trigger: bulletin(), after: 700, result: META_BUY, applied: "bought 1 ACE-01 at V$182", withTrace: true }),
    () => decide({ agent: "npc_t2", offered: TRADER_SELL, trigger: delivered("npc_i2", "npc_t2", "RELAY", 2, "Ace ran from the dock carrying a package"), after: 600, result: STALE_SELL, rejected: "subject was captured before the decision arrived", withTrace: true }),
  ]);
}
function gallery3(): void {
  gallery([
    () => decide({ agent: "npc_i2", offered: INFORMANT_OPTS, trigger: delivered("npc_w2", "npc_i2", "GOSSIP", 1, "Ace ran from the dock carrying a package"), after: 322, result: { source: "drex", chosen: "relay", providerChoice: "relay", confidence: 0.68, probabilities: { relay: 0.84, withhold: 0.16 }, requestId: "req_e9b2d4410c67f8a3", latencyMs: 322, evalMs: 129.5 }, applied: "relayed the tip about ACE-01 to Rook Halvorsen" }),
    () => decide({ agent: "npc_p1", offered: POLICE_PURSUE, trigger: observed("npc_p1", "Ace is in the open on the marina road", 6, "SIGHTING"), after: 288, result: { source: "drex", chosen: "pursue", providerChoice: "pursue", confidence: 0.95, probabilities: { investigate: 0.02, patrol: 0.01, pursue: 0.97 }, requestId: "req_3d5e8f7b61a09c24", latencyMs: 288, evalMs: 117.2 }, applied: "pursuing ACE-01", withTrace: true }),
    () => decide({ agent: "npc_w2", offered: WITNESS_OPTS, trigger: observed("npc_w2", "Ace ran from the dock carrying a package", 13), after: 398, result: DREX_GOSSIP, applied: "gossiped about ACE-01 to Dex Marlowe" }),
  ]);
}

// ---- ?scene=live: no mocks at all. Joins a real server through the Vite proxy as an outlaw, walks to the first
// job, completes it and lets the real NPC pipeline (real provider calls or real fallbacks) drive the cards. ----
async function liveStory(): Promise<void> {
  const net = await import("../../net/socketClient");
  const store = await import("../../state/gameStore");
  const ack = await net.joinRoom({ create: true, roomCode: "", nickname: "Ace", role: "OUTLAW" });
  if (!ack.ok) { console.error("live join failed", ack.error); return; }
  const sleep = (ms: number) => new Promise<void>((r) => { later(ms, r); });
  const me = () => store.selectMyCharacter(useGame.getState());
  const walkTo = async (tx: number, tz: number) => {
    for (let i = 0; i < 120; i++) {
      const c = me(); if (!c) return;
      const p = store.getPose(c.id) ?? c;
      const dx = tx - p.x, dz = tz - p.z, d = Math.hypot(dx, dz);
      if (d < 1.4) break;
      net.sendNoAck({ type: "player.move", dirX: dx / d, dirZ: dz / d });
      await sleep(90);
    }
    net.sendNoAck({ type: "player.move", dirX: 0, dirZ: 0 });
  };
  await sleep(1200);
  const m = Object.values(useGame.getState().missions).find((x) => x.status === "AVAILABLE");
  if (m) {
    await walkTo(m.x, m.z);
    await sleep(200);
    const r = await net.send({ type: "mission.interact", missionId: m.id, commandId: net.newId() });
    console.warn("live mission", JSON.stringify(r.ok ? { ok: true } : r.error));
  }
  for (let i = 0; i < 3; i++) {
    await sleep(7000);
    const r = await net.send({ type: "demo.incident", incidentType: "SIGHTING", commandId: net.newId() });
    console.warn("live incident", JSON.stringify(r.ok ? r.result : r.error));
  }
}

// ---- store bootstrap: history that must NEVER be replayed is already in the log before the component mounts ----
const history: GameEvent[] = [
  ev("ROOM_CREATED", "server", { roomCode: "VICE-42", seed: 42, ruleVersion: "h0.2", mode: "drex", npcCount: 8 }),
  ev("DECISION_REQUESTED", "npc_w1", { decisionId: "dec_old", agentId: "npc_w1", role: "WITNESS", mode: "drex", offeredActions: ["report", "ignore", "gossip"], triggerEventId: "ev_0", stateHash: "x" }),
  ev("DECISION_RESOLVED", "npc_w1", { decisionId: "dec_old", agentId: "npc_w1", role: "WITNESS", status: "APPLIED", decisionSource: "drex", provider: "drex", model: "drex-max-v1.1", chosenAction: "ignore", providerChoice: "ignore", offeredActions: ["report", "ignore", "gossip"], confidence: 0.9, probabilities: { report: 0.03, ignore: 0.95, gossip: 0.02 }, providerRequestId: "req_old", latencyMs: 300, providerEvalMs: 120, errorType: null, rejectReason: null }),
] as unknown as GameEvent[];
if (SCENE !== "live") {
  useGame.setState({
    conn: "connected", runId: RUN, roomCode: "VICE-42", mode: "drex", events: history,
    npcs: Object.fromEntries(NPCS.map((n) => [n.id, n])), characters: Object.fromEntries(CHARS.map((c) => [c.id, c])),
  });
}

function Backdrop() {
  const [City, setCity] = useState<ComponentType<Record<string, unknown>> | null>(null);
  useEffect(() => {
    if (q.has("nocity")) return;
    let alive = true;
    // The real 2D city as the backdrop, loaded defensively: another agent may be editing it right now.
    import("../../scene/canvas/CanvasCity").then((m) => { if (alive) setCity(() => m.CanvasCity as unknown as ComponentType<Record<string, unknown>>); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  if (!City) return <div style={{ width: "100%", height: "100%", background: "radial-gradient(900px 500px at 30% 60%, #2a1650 0%, #07051a 70%)" }} />;
  return <City map={CITY_MAP} characters={CHARS} npcs={NPCS} missions={MISSIONS} listings={[]} tick={null} events={[]} youCharacterId="c1" selectedId={null} onSelect={() => undefined} />;
}

function Preview() {
  const selected = useGame((s) => s.selectedAgentId);
  useEffect(() => {
    const scenes: Record<string, () => void> = { busy: busyStory, think, gallery1, gallery2, gallery3, idle: () => undefined, live: () => { void liveStory(); } };
    (scenes[SCENE] ?? busyStory)();
    return () => { for (const t of timers) window.clearTimeout(t); timers.length = 0; };
  }, []);
  return (
    <div className="app">
      <header className="hdr">
        <div className="brand"><span className="brand-a">VICE</span><span className="brand-b">EXCHANGE</span></div>
        <div className="hdr-item">ROOM <b>VICE-42</b></div><div className="hdr-item"><b>2</b> players</div><div className="hdr-item"><b>8</b> agents</div>
        <div className="badge live">DREX: LIVE</div><div className="spacer" /><div className="hdr-item dim">DecisionSpotlight preview · scene {SCENE}</div>
      </header>
      <main className="grid">
        <section className="city">
          <Backdrop />
          <DecisionSpotlight dock="top-right" dwellMs={DWELL} />
          <div className="hud"><span className="hud-chip strong">ACE-01 V$182</span><span className="hud-chip bad">WANTED 2/5</span><span className="hud-chip dim">WASD move · E do the job at the marker</span></div>
        </section>
        <aside className="side">
          <section className="panel market"><h2>MARKET</h2><div className="dim">placeholder</div></section>
          <section className="panel inspector"><h2>AGENT INTELLIGENCE</h2><div className="dim pad">selected agent: <b style={{ color: "#fff" }}>{selected ?? "none"}</b></div></section>
        </aside>
        <section className="bottom"><div className="tabs"><button className="on">EVENT TIMELINE</button></div></section>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<Preview />);
