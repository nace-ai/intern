// Minimal built-in implementations of the ports. Used by tests and when a swarm module is missing,
// so the server always boots. The real modules replace these in compose.ts.
import { RULES, clamp } from "@vice/contracts";
import type {
  DecisionMode, DecisionTrace, ExperimentResult, GameEvent, ListingView, MemoryEntry, MetricsView, ProviderHealth, WalletView,
} from "@vice/contracts";
import type {
  BrainsPort, CharacterState, EconomyPort, ListingState, MemoryPort, MetricsPort, PersistencePort, Quote, RoomState, SchedulerPort,
} from "../domain";

const P = RULES.price;

function netBuy(l: ListingState, nowMs: number): number {
  l.demand = l.demand.filter((d) => nowMs - d.atMs <= P.sentiment.windowMs);
  return l.demand.reduce((s, d) => s + d.units, 0);
}
function computeQuote(c: CharacterState, l: ListingState, nowMs: number): Quote {
  const survivalSeconds = Math.max(0, Math.floor(((c.captureTimeMs ?? nowMs) - c.spawnTimeMs) / 1000));
  const referenceValue = P.base + P.perNotorietyPoint * c.notorietyPoints + P.perMission * c.completedMissions
    + P.perSurvivalStep * Math.floor(survivalSeconds / P.survivalStepSeconds);
  const f = P.survivalFactor;
  const survivalFactor = clamp(f.base - f.perHeat * c.heat + f.perEscape * c.successfulEscapes, f.min, f.max);
  const netBuyUnits = netBuy(l, nowMs);
  const sentimentFactor = clamp(1 + P.sentiment.perNetUnit * netBuyUnits, P.sentiment.min, P.sentiment.max);
  const halted = l.status === "HALTED_CAPTURED";
  const spotPrice = halted ? 0 : Math.max(P.minActiveSpot, Math.round(referenceValue * survivalFactor * sentimentFactor));
  return { referenceValue, spotPrice, ask: spotPrice, bid: halted ? 0 : Math.max(1, Math.floor(spotPrice * (1 - P.houseSpread))), survivalFactor, sentimentFactor, netBuyUnits, survivalSeconds };
}

export function createFallbackEconomy(): EconomyPort {
  const eco: EconomyPort = {
    openWallet(state, w) {
      const wallet = { id: w.id, ownerId: w.ownerId, ownerKind: w.ownerKind, ownerName: w.ownerName, balanceCredits: w.balance, holdings: {}, lastOrderAtMs: {} };
      state.wallets[w.id] = wallet;
      return wallet;
    },
    openListing(state, character, nowMs) {
      const ticker = character.ticker!;
      const l: ListingState = { ticker, characterId: character.id, nickname: character.nickname, status: "ACTIVE", referenceValue: 0, spotPrice: 0, openedAtMs: nowMs, haltedAtMs: null, finalSpotBeforeHalt: null, history: [], demand: [], volume: 0 };
      const q = computeQuote(character, l, nowMs);
      l.referenceValue = q.referenceValue; l.spotPrice = q.spotPrice;
      l.history.push({ t: nowMs, spot: q.spotPrice, reference: q.referenceValue });
      state.listings[ticker] = l;
      return l;
    },
    quote(state, ticker, nowMs) {
      const l = state.listings[ticker]; const c = l && state.characters[l.characterId];
      return l && c ? computeQuote(c, l, nowMs) : null;
    },
    reprice(state, ticker, nowMs, reason) {
      const l = state.listings[ticker]; const c = l && state.characters[l.characterId];
      if (!l || !c || l.status !== "ACTIVE") return null;
      const q = computeQuote(c, l, nowMs);
      if (q.spotPrice === l.spotPrice && q.referenceValue === l.referenceValue) return null;
      const previousSpotPrice = l.spotPrice;
      l.spotPrice = q.spotPrice; l.referenceValue = q.referenceValue;
      l.history.push({ t: nowMs, spot: q.spotPrice, reference: q.referenceValue });
      if (l.history.length > P.historyMaxPoints) l.history.splice(0, l.history.length - P.historyMaxPoints);
      return { ticker, referenceValue: q.referenceValue, spotPrice: q.spotPrice, previousSpotPrice, reason, heat: c.heat, netBuyUnits: q.netBuyUnits };
    },
    executeOrder(state, o, nowMs) {
      const l = state.listings[o.ticker]; const w = state.wallets[o.walletId];
      if (!l) return { ok: false, reason: "UNKNOWN_TICKER", message: "Unknown ticker" };
      if (!w) return { ok: false, reason: "ACTOR_NOT_ALLOWED", message: "No wallet" };
      if (l.status !== "ACTIVE") return { ok: false, reason: "LISTING_HALTED", message: "Listing is halted at V$0" };
      if (!Number.isInteger(o.qty) || o.qty !== RULES.trade.qtyPerOrder) return { ok: false, reason: "INVALID_QTY", message: "One share per order" };
      if (nowMs - (w.lastOrderAtMs[o.ticker] ?? -Infinity) < RULES.trade.cooldownMs) return { ok: false, reason: "COOLDOWN", message: "Order cooldown" };
      const q = eco.quote(state, o.ticker, nowMs)!;
      const h = w.holdings[o.ticker] ?? { shares: 0, costBasis: 0 };
      const price = o.side === "BUY" ? q.ask : q.bid;
      if (o.side === "BUY") {
        if (h.shares + o.qty > RULES.trade.maxSharesPerTicker) return { ok: false, reason: "POSITION_LIMIT", message: "Position cap reached" };
        if (w.balanceCredits < price * o.qty) return { ok: false, reason: "INSUFFICIENT_FUNDS", message: "Not enough V$" };
        w.balanceCredits -= price * o.qty; h.costBasis += price * o.qty; h.shares += o.qty;
      } else {
        if (h.shares < o.qty) return { ok: false, reason: "INSUFFICIENT_SHARES", message: "Not enough shares" };
        h.costBasis -= Math.round((h.costBasis / h.shares) * o.qty);
        w.balanceCredits += price * o.qty; h.shares -= o.qty;
      }
      w.holdings[o.ticker] = h; w.lastOrderAtMs[o.ticker] = nowMs;
      l.demand.push({ atMs: nowMs, units: o.side === "BUY" ? o.qty : -o.qty }); l.volume += o.qty;
      const trade = { tradeId: o.tradeId, atMs: nowMs, actorId: o.actorId, actorName: o.actorName, actorKind: o.actorKind, ticker: o.ticker, side: o.side, qty: o.qty, executionPrice: price };
      return { ok: true, trade, walletId: w.id, balanceAfter: w.balanceCredits, sharesAfter: h.shares, priceChange: eco.reprice(state, o.ticker, nowMs, "TRADE") };
    },
    haltListing(state, ticker, nowMs) {
      const l = state.listings[ticker];
      if (!l || l.status !== "ACTIVE") return null;
      const finalSpotBeforeHalt = l.spotPrice; const referenceAtHalt = l.referenceValue;
      l.status = "HALTED_CAPTURED"; l.spotPrice = 0; l.haltedAtMs = nowMs; l.finalSpotBeforeHalt = finalSpotBeforeHalt;
      l.history.push({ t: nowMs, spot: 0, reference: referenceAtHalt });
      return { finalSpotBeforeHalt, referenceAtHalt };
    },
    bountyFor(state, characterId, nowMs) {
      const c = state.characters[characterId]; const l = c?.ticker ? state.listings[c.ticker] : undefined;
      if (!c || !l || l.status !== "ACTIVE") return 0;
      return Math.max(RULES.bounty.min, Math.round(computeQuote(c, l, nowMs).referenceValue * RULES.bounty.fractionOfReference));
    },
    credit(state, walletId, amount) { const w = state.wallets[walletId]; if (!w) return 0; w.balanceCredits += Math.round(amount); return w.balanceCredits; },
    walletView(state, walletId): WalletView | null {
      const w = state.wallets[walletId]; if (!w) return null;
      const holdings = Object.entries(w.holdings).filter(([, h]) => h.shares > 0).map(([ticker, h]) => ({ ticker, shares: h.shares, costBasis: h.costBasis, marketValue: h.shares * (state.listings[ticker]?.spotPrice ?? 0) }));
      return { walletId, ownerId: w.ownerId, balanceCredits: w.balanceCredits, holdings, netWorth: w.balanceCredits + holdings.reduce((s, h) => s + h.marketValue, 0) };
    },
    listingView(state, ticker, nowMs): ListingView | null {
      const l = state.listings[ticker]; if (!l) return null;
      const q = eco.quote(state, ticker, nowMs);
      return { ticker, characterId: l.characterId, nickname: l.nickname, status: l.status, referenceValue: l.referenceValue, spotPrice: l.spotPrice, bid: l.status === "ACTIVE" ? (q?.bid ?? 0) : 0, ask: l.spotPrice, history: l.history, volume: l.volume, netBuyUnits: q?.netBuyUnits ?? 0, openedAtMs: l.openedAtMs, haltedAtMs: l.haltedAtMs, finalSpotBeforeHalt: l.finalSpotBeforeHalt, currentBounty: eco.bountyFor(state, l.characterId, nowMs) };
    },
  };
  return eco;
}

export function createFallbackMemory(): MemoryPort {
  let byAgent = new Map<string, MemoryEntry[]>();
  const byId = new Map<string, MemoryEntry>();
  return {
    remember(entry, id) {
      const m: MemoryEntry = { ...entry, id };
      const list = byAgent.get(m.agentId) ?? [];
      list.push(m); byId.set(id, m);
      while (list.length > RULES.memory.maxEntriesPerNpc) byId.delete(list.shift()!.id);
      byAgent.set(m.agentId, list);
      return m;
    },
    recall: (agentId, nowMs) => (byAgent.get(agentId) ?? []).filter((m) => m.expiresAtMs > nowMs).slice().reverse(),
    get: (id) => byId.get(id),
    all: () => [...byId.values()],
    clear() { byAgent = new Map(); byId.clear(); },
  };
}

export function createNoopBrains(memory: MemoryPort, now: () => number): BrainsPort {
  return {
    onEvent() {}, onTick() {}, reset() {}, traces: () => [],
    view: (npcId) => ({ memories: memory.recall(npcId, now()), pendingDecisionId: null, lastDecision: null, recentDecisions: [] }),
  };
}

const health = (configured: boolean): ProviderHealth => ({ configured, state: configured ? "DOWN" : "UNCONFIGURED", model: null, lastOkAtMs: null, lastErrorType: null, callsLastMinute: 0, maxCallsPerMinute: 0, inFlight: 0, circuitOpenUntilMs: null });

/** Scripted-only scheduler: never calls a provider, always labels the result honestly. */
export function createScriptedScheduler(initialMode: DecisionMode = "scripted", now: () => number = Date.now): SchedulerPort {
  let mode = initialMode; let generation = 0; let pending = 0; const seen = new Set<string>();
  return {
    request(req, onDone) {
      if (seen.has(req.dedupeKey)) return null;
      seen.add(req.dedupeKey);
      const trace: DecisionTrace = {
        id: req.decisionId, roomId: req.roomId, runId: req.runId, agentId: req.agentId, agentName: req.agentName, role: req.role,
        status: "REQUESTED", mode, decisionSource: null, provider: null, model: null, question: req.question, offeredActions: req.options,
        chosenAction: null, providerChoice: null, confidence: null, probabilities: null, providerRequestId: null, latencyMs: null, providerEvalMs: null,
        usage: null, attempts: 0, errorType: null, errorMessage: null, rejectReason: null, stateSnapshot: req.state, stateHash: "",
        triggerEventId: req.triggerEventId, correlationId: req.correlationId, effectEventIds: [], requestedAtMs: req.requestedAtMs, resolvedAtMs: null,
        requestedAtSequence: req.requestedAtSequence, citations: req.citations, shadow: null,
      };
      const gen = generation; pending++;
      queueMicrotask(() => {
        pending--;
        if (gen !== generation) return;
        const scripted = mode === "scripted";
        onDone({ ...trace, status: scripted ? "RESOLVED" : "FALLBACK_APPLIED", decisionSource: scripted ? "scripted" : "fallback", chosenAction: scripted ? req.scriptedAction : req.safeAction, errorType: scripted ? null : "unconfigured", errorMessage: scripted ? null : "decision module not loaded", resolvedAtMs: now() });
      });
      return trace;
    },
    setMode(m) { mode = m; }, getMode: () => mode,
    status: () => ({ mode, drex: health(false), meta: health(false), pending, badge: mode === "scripted" ? "SCRIPTED" : `${mode.toUpperCase()}: FALLBACK` }),
    pending: () => pending,
    cancelAll() { generation++; seen.clear(); },
  };
}

export function createMemoryPersistence(): PersistencePort & { events: GameEvent[] } {
  const events: GameEvent[] = []; const decisions = new Map<string, DecisionTrace>(); const experiments = new Map<string, ExperimentResult>();
  return {
    events,
    appendEvent(e) { events.push(e); },
    saveDecision(t) { decisions.set(`${t.roomId}/${t.runId}/${t.id}`, t); },
    saveExperiment(r) { experiments.set(r.id, r); },
    loadEvents: (roomId, runId, after = 0) => events.filter((e) => e.roomId === roomId && e.runId === runId && e.serverSequence > after),
    loadDecisions: (roomId, runId) => [...decisions.values()].filter((d) => d.roomId === roomId && d.runId === runId),
    loadExperiments: () => [...experiments.values()],
    close() {},
  };
}

export function createFallbackMetrics(): MetricsPort {
  let v: MetricsView;
  const blank = (runId: string, mode: DecisionMode, seed: number, startedAtMs: number): MetricsView => ({
    runId, mode, seed, startedAtMs, decisions: { total: 0, bySource: {}, byRole: {}, byAction: {}, rejected: 0, stale: 0 },
    latency: { drex: null, meta: null }, messages: { sent: 0, delivered: 0, maxHops: 0 }, trades: { executed: 0, rejected: 0, byNpc: 0, byHuman: 0 },
    priceUpdates: 0, captures: 0, edges: [],
  });
  v = blank("", "scripted", 0, 0);
  return {
    onEvent(e) {
      if (e.type === "AGENT_MESSAGE_SENT") { v.messages.sent++; v.messages.maxHops = Math.max(v.messages.maxHops, (e as GameEvent<"AGENT_MESSAGE_SENT">).payload.hop); }
      else if (e.type === "AGENT_MESSAGE_DELIVERED") v.messages.delivered++;
      else if (e.type === "TRADE_EXECUTED") { v.trades.executed++; if ((e as GameEvent<"TRADE_EXECUTED">).payload.actorKind === "NPC") v.trades.byNpc++; else v.trades.byHuman++; }
      else if (e.type === "TRADE_REJECTED") v.trades.rejected++;
      else if (e.type === "MARKET_PRICE_UPDATED") v.priceUpdates++;
      else if (e.type === "CAPTURE_SUCCEEDED") v.captures++;
    },
    onDecision(t) {
      if (!t.decisionSource || t.resolvedAtMs === null) return;
      v.decisions.total++;
      v.decisions.bySource[t.decisionSource] = (v.decisions.bySource[t.decisionSource] ?? 0) + 1;
      v.decisions.byRole[t.role] = (v.decisions.byRole[t.role] ?? 0) + 1;
    },
    view: () => v,
    reset(runId, mode, seed, startedAtMs) { v = blank(runId, mode, seed, startedAtMs); },
  };
}
