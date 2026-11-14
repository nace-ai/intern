// Dev-only harness for TickerTape. No server: it feeds the real zustand store with a mock room and then plays a
// scripted busy scene (incident -> witnesses decide -> report / gossip -> bulletin -> NPC trades -> capture).
// Open /src/components/wow/TickerTapePreview.html   ?idle=1 (no scene)   ?empty=1 (no listings)   ?speed=2
import { createRoot } from "react-dom/client";
import { CITY_MAP, RULES_VERSION } from "@vice/contracts";
import type {
  AgentView, CharacterView, DecisionSource, EventPayloads, EventType, GameEvent, ListingView, MetricsView, NpcRole,
  PriceReason, ProviderStatus, RoomSnapshot,
} from "@vice/contracts";
import "../../styles.css";
import { useGame } from "../../state/gameStore";
import TickerTape from "./TickerTape";

const q = new URLSearchParams(location.search);
const SPEED = Math.max(0.25, Number(q.get("speed") ?? 1) || 1);
const RUN = "run_001";
const NOW = Date.now();

// ---------- mock world ----------
const npc = (id: string, name: string, role: NpcRole, x: number, z: number): AgentView => ({
  id, name, role, goal: "", persona: "", x, z, activity: role === "POLICE" ? "PATROL" : "IDLE", targetCharacterId: null,
  walletCredits: role === "TRADER" ? 2000 : null, holdings: {}, contacts: [], memories: [], pendingDecisionId: null, lastDecision: null, recentDecisions: [],
});
const NPCS: AgentView[] = [
  npc("npc_w1", "Marisol Vega", "WITNESS", 40, 64), npc("npc_w2", "Otis Brandt", "WITNESS", 55, 45),
  npc("npc_i1", "Lena Okoye", "INFORMANT", 33, 52), npc("npc_i2", "Dex Marlowe", "INFORMANT", 62, 30),
  npc("npc_t1", "Priya Castellan", "TRADER", 17, 31), npc("npc_t2", "Rook Halvorsen", "TRADER", 30, 41),
  npc("npc_p1", "Sgt. Imani Duarte", "POLICE", 32, 28), npc("npc_p2", "Ofc. Cole Renner", "POLICE", 92, 28),
];
const chr = (id: string, nickname: string, ticker: string | null, over: Partial<CharacterView> = {}): CharacterView => ({
  id, playerId: `ply_${id}`, nickname, role: ticker ? "OUTLAW" : "HUNTER", ticker, status: "ACTIVE", x: 60, z: 60, heat: 0, wantedLevel: 0, wanted: false,
  notorietyPoints: 0, completedMissions: 0, successfulEscapes: 0, spawnTimeMs: NOW - 400000, captureTimeMs: null, capturedBy: null, ...over,
});

/** Price history the way the economy writes it: opens at V$95, +2 every 5 s, plus a few steps and wobbles. */
function history(openedAgoMs: number, steps: Array<[atFrac: number, delta: number]>, wobble: number): ListingView["history"] {
  const out: ListingView["history"] = [];
  const t0 = NOW - openedAgoMs, n = Math.floor(openedAgoMs / 5000);
  let ref = 100;
  for (let i = 0; i <= n; i++) {
    const f = i / Math.max(1, n);
    let extra = 0;
    for (const [at, d] of steps) if (f >= at) extra += d;
    const reference = ref + extra;
    const spot = Math.max(1, Math.round(reference * 0.95 + Math.sin(i * 0.9) * wobble + Math.sin(i * 0.23) * wobble * 1.6));
    out.push({ t: t0 + i * 5000, spot, reference });
    ref += 2;
  }
  return out;
}
function listing(ticker: string, characterId: string, nickname: string, openedAgoMs: number, steps: Array<[number, number]>, wobble: number): ListingView {
  const h = history(openedAgoMs, steps, wobble);
  const last = h[h.length - 1]!;
  return {
    ticker, characterId, nickname, status: "ACTIVE", referenceValue: last.reference, spotPrice: last.spot, bid: Math.floor(last.spot * 0.97), ask: last.spot,
    history: h, volume: 3, netBuyUnits: 0, openedAtMs: NOW - openedAgoMs, haltedAtMs: null, finalSpotBeforeHalt: null, currentBounty: Math.round(last.reference / 2),
  };
}

const metrics: MetricsView = {
  runId: RUN, mode: "drex", seed: 42, startedAtMs: NOW - 400000,
  decisions: { total: 9, bySource: { drex: 7, drex_low_confidence_guardrail: 1, fallback: 1 }, byRole: { TRADER: 5, WITNESS: 2, POLICE: 2 }, byAction: { hold: 4, buy: 2, report: 1, patrol: 2 }, rejected: 0, stale: 0 },
  latency: { drex: { count: 8, meanMs: 331, p50Ms: 318, p95Ms: 462, maxMs: 505, evalP50Ms: 158 }, meta: null },
  messages: { sent: 3, delivered: 3, maxHops: 2 }, trades: { executed: 3, rejected: 0, byNpc: 2, byHuman: 1 }, priceUpdates: 210, captures: 1, edges: [],
};
const health = { configured: true, state: "LIVE" as const, model: "drex-max-v1.1", lastOkAtMs: NOW, lastErrorType: null, callsLastMinute: 4, maxCallsPerMinute: 60, inFlight: 0, circuitOpenUntilMs: null };
const providerStatus: ProviderStatus = { mode: "drex", drex: health, meta: { ...health, model: "muse-ember-1.0", lastOkAtMs: null, callsLastMinute: 0 }, pending: 0, badge: "DREX: LIVE" };

let seq = 400, evtN = 0, decN = 20, clockMs = NOW - 90000;
function ev<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], causationId: string | null = null, correlationId = "story_0007"): GameEvent<T> {
  clockMs += 7;
  return {
    schemaVersion: 1, eventId: `evt_${String(++evtN).padStart(6, "0")}`, roomId: "VICE-42", runId: RUN, serverSequence: ++seq,
    time: new Date(clockMs).toISOString(), simTimeMs: clockMs, type, actorId, causationId, correlationId, payload,
  };
}

function buildSnapshot(): RoomSnapshot {
  const empty = q.has("empty");
  const vex = listing("VEX-01", "chr_0001", "Vex", 380000, [[0.3, 60], [0.55, 60]], 5);
  // captured earlier: the economy appends a final 0 point
  vex.history = vex.history.slice(0, 50);
  const vexLast = vex.history[vex.history.length - 1]!;
  vex.history.push({ t: vexLast.t + 2300, spot: 0, reference: vexLast.reference });
  Object.assign(vex, { status: "HALTED_CAPTURED", spotPrice: 0, bid: 0, ask: 0, haltedAtMs: vexLast.t + 2300, finalSpotBeforeHalt: vexLast.spot, currentBounty: 0 });
  const listings = empty ? [] : [
    vex,
    listing("ACE-02", "chr_0002", "Ace", 300000, [[0.35, 64], [0.7, 64]], 4),
    listing("BLA-03", "chr_0003", "Blaze", 215000, [[0.5, 62], [0.8, -34]], 7),
    listing("NOV-04", "chr_0004", "Nova", 120000, [[0.6, -22]], 3),
  ];
  const characters = empty ? [] : [
    chr("chr_0001", "Vex", "VEX-01", { status: "CAPTURED", captureTimeMs: NOW - 120000, capturedBy: "npc_p2" }),
    chr("chr_0002", "Ace", "ACE-02", { completedMissions: 2, notorietyPoints: 4, heat: 12 }),
    chr("chr_0003", "Blaze", "BLA-03", { completedMissions: 1, notorietyPoints: 2, heat: 6 }),
    chr("chr_0004", "Nova", "NOV-04"),
    chr("chr_0005", "Rio", null),
  ];
  // Story beats that already happened before this client mounted. They may be recapped but must never replay an effect.
  clockMs = NOW - 150000;
  const old: GameEvent[] = [];
  if (!empty) {
    const res = ev("DECISION_RESOLVED", "npc_w2", { decisionId: "dec_0011", agentId: "npc_w2", role: "WITNESS", status: "APPLIED", decisionSource: "drex", provider: "drex", model: "drex-max-v1.1", chosenAction: "report", providerChoice: "report", offeredActions: ["report", "gossip", "ignore"], confidence: 0.74, probabilities: { report: 0.86, gossip: 0.12, ignore: 0.02 }, providerRequestId: "req_mock", latencyMs: 301, providerEvalMs: 150, errorType: null, rejectReason: null });
    old.push(res,
      ev("AGENT_ACTION_APPLIED", "npc_w2", { decisionId: "dec_0011", agentId: "npc_w2", role: "WITNESS", action: "report", decisionSource: "drex", detail: "reported VEX-01 to Ofc. Cole Renner" }, res.eventId),
      ev("BULLETIN_PUBLISHED", "npc_p2", { bulletinId: "bul_0001", characterId: "chr_0001", ticker: "VEX-01", lastKnown: { x: 60, z: 40 }, issuedBy: "npc_p2", recipientIds: NPCS.map((n) => n.id) }),
      ev("CAPTURE_SUCCEEDED", "npc_p2", { captureId: "cap_0001", commandId: "ncmd_1", characterId: "chr_0001", ticker: "VEX-01", captorId: "npc_p2", captorName: "Ofc. Cole Renner", captorKind: "NPC", captorWalletId: null, bounty: 0, captorBalanceAfter: null, referenceAtCapture: vexLast.reference, spotBeforeCapture: vexLast.spot, survivalSeconds: 252 }),
      ev("LISTING_HALTED", "exchange", { ticker: "VEX-01", characterId: "chr_0001", status: "HALTED_CAPTURED", finalSpotBeforeHalt: vexLast.spot }),
    );
  }
  clockMs = NOW;
  return {
    roomId: "VICE-42", roomCode: "VICE-42", runId: RUN, seed: 42, ruleVersion: RULES_VERSION, tick: 4000, serverTimeMs: NOW, lastEventSequence: seq,
    mode: "drex", providerStatus, map: CITY_MAP, players: [], characters, npcs: NPCS, missions: [], listings, trades: [], recentEvents: old,
    metrics, experiments: [], policy: null,
  };
}

// ---------- scripted scene ----------
const store = useGame;
const emit = (e: GameEvent): GameEvent => { store.getState().applyEvent(e); return e; };
function bumpMetrics(patch: (m: MetricsView) => void): void {
  const m = structuredClone(store.getState().metrics ?? metrics);
  patch(m);
  store.setState({ metrics: m });
}
function price(ticker: string, spot: number, reason: PriceReason, causationId: string | null = null): void {
  const l = store.getState().listings[ticker];
  if (!l || l.status !== "ACTIVE") return;
  clockMs = Math.max(clockMs, Date.now());
  const reference = reason === "SURVIVAL_TICK" ? l.referenceValue + 2 : reason === "MISSION" ? l.referenceValue + 64 : l.referenceValue;
  const next: ListingView = { ...l, spotPrice: spot, ask: spot, bid: Math.max(1, Math.floor(spot * 0.97)), referenceValue: reference, history: [...l.history, { t: clockMs, spot, reference }] };
  store.setState((st) => ({ listings: { ...st.listings, [ticker]: next } }));
  emit(ev("MARKET_PRICE_UPDATED", "exchange", { ticker, referenceValue: reference, spotPrice: spot, previousSpotPrice: l.spotPrice, reason, heat: 0, netBuyUnits: 0 }, causationId));
}
const spotOf = (ticker: string): number => store.getState().listings[ticker]?.spotPrice ?? 0;
function character(id: string, patch: Partial<CharacterView>): void {
  store.setState((st) => (st.characters[id] ? { characters: { ...st.characters, [id]: { ...st.characters[id]!, ...patch } } } : {}));
}

interface Decide { agentId: string; role: NpcRole; action: string; offered: string[]; source: DecisionSource; confidence: number | null; latencyMs: number | null; detail: string; providerChoice?: string }
/** DECISION_RESOLVED -> effects (causationId = the resolved event) -> AGENT_ACTION_APPLIED, exactly like the server. */
function decide(d: Decide, effects: (resolvedId: string) => void = () => {}): void {
  const real = d.source === "drex" || d.source === "meta" || d.source === "drex_low_confidence_guardrail";
  const decisionId = `dec_${String(++decN).padStart(4, "0")}`;
  const res = emit(ev("DECISION_RESOLVED", d.agentId, {
    decisionId, agentId: d.agentId, role: d.role, status: d.source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED", decisionSource: d.source,
    provider: real || d.source === "fallback" ? (d.source === "meta" ? "meta" : "drex") : null, model: real ? (d.source === "meta" ? "muse-ember-1.0" : "drex-max-v1.1") : null,
    chosenAction: d.action, providerChoice: real ? d.providerChoice ?? d.action : null, offeredActions: d.offered,
    confidence: real ? d.confidence : null, probabilities: null, providerRequestId: real ? "req_mock" : null, latencyMs: real ? d.latencyMs : null, providerEvalMs: null,
    errorType: d.source === "fallback" ? "TIMEOUT" : null, rejectReason: null,
  }));
  effects(res.eventId);
  emit(ev("AGENT_ACTION_APPLIED", d.agentId, { decisionId, agentId: d.agentId, role: d.role, action: d.action, decisionSource: d.source, detail: d.detail }, res.eventId));
  bumpMetrics((m) => { m.decisions.total++; m.decisions.bySource[d.source] = (m.decisions.bySource[d.source] ?? 0) + 1; if (real && m.latency.drex) m.latency.drex.count++; });
}
function message(senderId: string, recipientId: string, kind: "REPORT" | "GOSSIP" | "RELAY", hop: number, causationId: string): void {
  emit(ev("AGENT_MESSAGE_SENT", senderId, { messageId: `msg_${seq}`, senderId, recipientId, kind, sourceMemoryId: null, sourceEventId: causationId, subjectCharacterId: "chr_0002", summary: "Saw Ace grab the marked package at the dock.", hop }, causationId));
  bumpMetrics((m) => { m.messages.sent++; m.messages.delivered++; m.messages.maxHops = Math.max(m.messages.maxHops, hop); });
}
function trade(actorId: string, actorName: string, kind: "NPC" | "HUMAN", ticker: string, side: "BUY" | "SELL", causationId: string | null): void {
  const px = side === "BUY" ? spotOf(ticker) : Math.floor(spotOf(ticker) * 0.97);
  const t = emit(ev("TRADE_EXECUTED", actorId, { tradeId: `trd_${seq}`, commandId: `cmd_${seq}`, walletId: `wal_${actorId}`, actorId, actorName, actorKind: kind, ticker, side, qty: 1, executionPrice: px, balanceAfter: 1800, sharesAfter: 1 }, causationId));
  price(ticker, Math.max(1, Math.round(spotOf(ticker) * (side === "BUY" ? 1.03 : 0.97))), "TRADE", t.eventId);
  bumpMetrics((m) => { m.trades.executed++; if (kind === "NPC") m.trades.byNpc++; else m.trades.byHuman++; });
}

const SCENE: Array<[atMs: number, run: () => void]> = [
  [900, () => {
    const done = emit(ev("MISSION_COMPLETED", "ply_chr_0002", { missionId: "mis_0003", characterId: "chr_0002", ticker: "ACE-02", x: 47, z: 60, notorietyGained: 2, heatGained: 12, notorietyPoints: 6, completedMissions: 3, heat: 24 }));
    price("ACE-02", spotOf("ACE-02") + 58, "MISSION", done.eventId);
    character("chr_0002", { heat: 24, completedMissions: 3 });
    const inc = emit(ev("INCIDENT_OCCURRED", "chr_0002", { incidentId: "inc_0004", kind: "MISSION", characterId: "chr_0002", ticker: "ACE-02", x: 47, z: 60, description: 'Ace grabbed "Marked package at the dock".', observerIds: ["npc_w1", "npc_w2"] }, done.eventId));
    for (const id of ["npc_w1", "npc_w2"]) emit(ev("DECISION_REQUESTED", id, { decisionId: `dec_r${seq}`, agentId: id, role: "WITNESS", mode: "drex", offeredActions: ["report", "gossip", "ignore"], triggerEventId: inc.eventId, stateHash: "mock" }, inc.eventId));
  }],
  [1700, () => decide({ agentId: "npc_w1", role: "WITNESS", action: "report", offered: ["report", "gossip", "ignore"], source: "drex", confidence: 0.81, latencyMs: 318, detail: "reported ACE-02 to Sgt. Imani Duarte" }, (id) => message("npc_w1", "npc_p1", "REPORT", 1, id))],
  [2300, () => decide({ agentId: "npc_w2", role: "WITNESS", action: "gossip", offered: ["report", "gossip", "ignore"], source: "drex", confidence: 0.64, latencyMs: 287, detail: "gossiped about ACE-02 to Dex Marlowe" }, (id) => message("npc_w2", "npc_i2", "GOSSIP", 1, id))],
  [3000, () => {
    const b = emit(ev("BULLETIN_PUBLISHED", "npc_p1", { bulletinId: "bul_0002", characterId: "chr_0002", ticker: "ACE-02", lastKnown: { x: 47, z: 60 }, issuedBy: "npc_p1", recipientIds: NPCS.map((n) => n.id) }));
    emit(ev("WANTED_STATUS_CHANGED", "chr_0002", { characterId: "chr_0002", ticker: "ACE-02", wanted: true, heat: 54, wantedLevel: 2, reason: "police bulletin" }, b.eventId));
    character("chr_0002", { heat: 54, status: "WANTED", wanted: true, wantedLevel: 2 });
    price("ACE-02", Math.round(spotOf("ACE-02") * 0.8), "HEAT", b.eventId);
  }],
  [3900, () => decide({ agentId: "npc_i2", role: "INFORMANT", action: "relay", offered: ["relay", "withhold"], source: "fallback", confidence: null, latencyMs: null, detail: "relayed the tip about ACE-02 to Rook Halvorsen" }, (id) => message("npc_i2", "npc_t2", "RELAY", 2, id))],
  [4700, () => decide({ agentId: "npc_t1", role: "TRADER", action: "buy", offered: ["buy", "hold"], source: "drex", confidence: 0.58, latencyMs: 341, detail: `bought 1 ACE-02 at V$${spotOf("ACE-02")}` }, (id) => trade("npc_t1", "Priya Castellan", "NPC", "ACE-02", "BUY", id))],
  [5600, () => decide({ agentId: "npc_t2", role: "TRADER", action: "sell", offered: ["sell", "hold"], source: "drex", confidence: 0.77, latencyMs: 296, detail: `sold 1 ACE-02 at V$${Math.floor(spotOf("ACE-02") * 0.97)}` }, (id) => trade("npc_t2", "Rook Halvorsen", "NPC", "ACE-02", "SELL", id))],
  [6500, () => decide({ agentId: "npc_p1", role: "POLICE", action: "pursue", offered: ["investigate", "patrol", "pursue"], source: "drex", confidence: 0.71, latencyMs: 309, detail: "pursuing ACE-02" })],
  [7300, () => decide({ agentId: "npc_p2", role: "POLICE", action: "investigate", offered: ["investigate", "patrol", "pursue"], source: "drex_low_confidence_guardrail", confidence: 0.06, latencyMs: 402, detail: "heading to the last known location of ACE-02", providerChoice: "pursue" })],
  [8300, () => trade("ply_chr_0005", "Rio", "HUMAN", "BLA-03", "BUY", null)],
  [9200, () => { for (const t of ["BLA-03", "NOV-04"]) price(t, spotOf(t) + 2, "SURVIVAL_TICK"); character("chr_0002", { heat: 66, wantedLevel: 3 }); }],
  [10200, () => decide({ agentId: "npc_t1", role: "TRADER", action: "hold", offered: ["buy", "hold", "sell"], source: "drex", confidence: 0.52, latencyMs: 277, detail: "holding ACE-02" })],
  [12000, () => {
    const from = spotOf("ACE-02");
    const l = store.getState().listings["ACE-02"]!;
    clockMs = Math.max(clockMs, Date.now());
    const cap = emit(ev("CAPTURE_SUCCEEDED", "npc_p1", { captureId: "cap_0002", commandId: "ncmd_2", characterId: "chr_0002", ticker: "ACE-02", captorId: "npc_p1", captorName: "Sgt. Imani Duarte", captorKind: "NPC", captorWalletId: null, bounty: 0, captorBalanceAfter: null, referenceAtCapture: l.referenceValue, spotBeforeCapture: from, survivalSeconds: 312 }));
    store.setState((st) => ({ listings: { ...st.listings, "ACE-02": { ...l, status: "HALTED_CAPTURED", spotPrice: 0, bid: 0, ask: 0, haltedAtMs: clockMs, finalSpotBeforeHalt: from, currentBounty: 0, history: [...l.history, { t: clockMs, spot: 0, reference: l.referenceValue }] } } }));
    character("chr_0002", { status: "CAPTURED", wanted: false, captureTimeMs: clockMs, capturedBy: "npc_p1" });
    emit(ev("LISTING_HALTED", "exchange", { ticker: "ACE-02", characterId: "chr_0002", status: "HALTED_CAPTURED", finalSpotBeforeHalt: from }, cap.eventId));
    emit(ev("MARKET_PRICE_UPDATED", "exchange", { ticker: "ACE-02", referenceValue: l.referenceValue, spotPrice: 0, previousSpotPrice: from, reason: "CAPTURE", heat: 66, netBuyUnits: 0 }, cap.eventId));
    bumpMetrics((m) => { m.captures++; });
  }],
  [17500, () => {
    const l = listing("ACE-05", "chr_0006", "Ace", 0, [], 0);
    store.setState((st) => ({ listings: { ...st.listings, "ACE-05": l }, characters: { ...st.characters, chr_0006: chr("chr_0006", "Ace", "ACE-05", { spawnTimeMs: Date.now() }) } }));
    emit(ev("LISTING_OPENED", "exchange", { ticker: "ACE-05", characterId: "chr_0006", nickname: "Ace", referenceValue: 100, spotPrice: 95 }));
  }],
  [19500, () => { for (const t of ["BLA-03", "NOV-04", "ACE-05"]) price(t, spotOf(t) + 2, "SURVIVAL_TICK"); }],
  [21000, () => { emit(ev("MODE_CHANGED", "director", { mode: "scripted", previousMode: "drex" })); store.setState({ mode: "scripted" }); }],
  [22500, () => decide({ agentId: "npc_t2", role: "TRADER", action: "buy", offered: ["buy", "hold"], source: "scripted", confidence: null, latencyMs: null, detail: `bought 1 ACE-05 at V$${spotOf("ACE-05")}` }, (id) => trade("npc_t2", "Rook Halvorsen", "NPC", "ACE-05", "BUY", id))],
  [24000, () => { emit(ev("MODE_CHANGED", "director", { mode: "meta", previousMode: "scripted" })); store.setState({ mode: "meta" }); }],
  [25500, () => decide({ agentId: "npc_w2", role: "WITNESS", action: "ignore", offered: ["report", "gossip", "ignore"], source: "meta", confidence: null, latencyMs: 1840, detail: "chose to ignore" })],
  [27000, () => { emit(ev("ESCAPE_SUCCEEDED", "chr_0003", { characterId: "chr_0003", ticker: "BLA-03", successfulEscapes: 1 })); price("BLA-03", spotOf("BLA-03") + 9, "ESCAPE"); }],
];

store.getState().applySnapshot(buildSnapshot());
store.setState({ conn: q.has("offline") ? "disconnected" : "connected", selectedTicker: q.get("sel") });
if (!q.has("idle") && !q.has("empty")) {
  for (const [at, run] of SCENE) setTimeout(run, at / SPEED);
  // keep the market breathing after the scene
  setInterval(() => { for (const l of Object.values(store.getState().listings)) if (l.status === "ACTIVE") price(l.ticker, l.spotPrice + 2, "SURVIVAL_TICK"); }, 5000 / SPEED);
}

function Preview() {
  const mode = useGame((s) => s.mode);
  return (
    <div className="app">
      <header className="hdr">
        <div className="brand"><span className="brand-a">VICE</span><span className="brand-b">EXCHANGE</span></div>
        <div className="hdr-item">ROOM <b>VICE-42</b></div><div className="hdr-item"><b>2</b> players</div><div className="hdr-item"><b>8</b> agents</div>
        <div className="badge scripted">PREVIEW · MOCK DATA</div><div className="hdr-item dim">mode <b>{mode.toUpperCase()}</b></div>
        <div className="spacer" /><div className="hdr-item">Ace · <b>OUTLAW</b></div>
      </header>
      <TickerTape />
      <main className="grid"><section className="city" /><aside className="side"><section className="panel market" style={{ flex: 1 }} /></aside><section className="bottom" /></main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<Preview />);
