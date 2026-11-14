// Metrics computed purely from authoritative events and decision traces. Observed numbers only:
// latency stats come from measured provider round trips and are null when there are none.
import type { DecisionMode, DecisionTrace, GameEvent, LatencyStats, MessageKind, MetricsView, ProviderName } from "@vice/contracts";
import type { MetricsPort } from "../domain";

interface DecisionRec {
  source: string | null; role: string; action: string | null; status: string;
  provider: ProviderName | null; latencyMs: number | null; evalMs: number | null; rejectReason: string | null;
  /** Background Meta comparison attached to a Drex decision (never applied). */
  shadow?: { action: string | null; latencyMs: number | null; agrees: boolean | null } | null;
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[idx]!;
}
const round1 = (n: number) => Math.round(n * 10) / 10;

export function latencyStats(latencies: number[], evals: number[] = []): LatencyStats | null {
  if (latencies.length === 0) return null;
  const s = [...latencies].sort((a, b) => a - b);
  const e = [...evals].sort((a, b) => a - b);
  return {
    count: s.length,
    meanMs: round1(s.reduce((a, b) => a + b, 0) / s.length),
    p50Ms: round1(percentile(s, 0.5)), p95Ms: round1(percentile(s, 0.95)), maxMs: round1(s[s.length - 1]!),
    evalP50Ms: e.length ? round1(percentile(e, 0.5)) : null,
  };
}

export function createMetrics(): MetricsPort {
  let runId = "", mode: DecisionMode = "drex", seed = 0, startedAtMs = 0;
  let decisions = new Map<string, DecisionRec>();
  let sent = 0, delivered = 0, maxHops = 0;
  let executed = 0, rejectedTrades = 0, byNpc = 0, byHuman = 0;
  let priceUpdates = 0, captures = 0;
  let edges = new Map<string, { from: string; to: string; kind: MessageKind; count: number }>();

  const clear = () => {
    decisions = new Map(); edges = new Map();
    sent = delivered = maxHops = executed = rejectedTrades = byNpc = byHuman = priceUpdates = captures = 0;
  };
  const is = <T extends GameEvent["type"]>(e: GameEvent, type: T): e is GameEvent<T> => e.type === type;

  return {
    reset(newRunId, newMode, newSeed, newStartedAtMs) {
      clear(); runId = newRunId; mode = newMode; seed = newSeed; startedAtMs = newStartedAtMs;
    },

    onEvent(e: GameEvent): void {
      if (!runId) { runId = e.runId; startedAtMs = e.simTimeMs; }
      if (is(e, "ROOM_CREATED")) { mode = e.payload.mode; seed = e.payload.seed; }
      else if (is(e, "MODE_CHANGED")) mode = e.payload.mode;
      else if (is(e, "AGENT_MESSAGE_SENT")) {
        const p = e.payload;
        sent++; if (p.hop > maxHops) maxHops = p.hop;
        const key = `${p.senderId}>${p.recipientId}>${p.kind}`;
        const edge = edges.get(key);
        if (edge) edge.count++; else edges.set(key, { from: p.senderId, to: p.recipientId, kind: p.kind, count: 1 });
      }
      else if (is(e, "AGENT_MESSAGE_DELIVERED")) { delivered++; if (e.payload.hop > maxHops) maxHops = e.payload.hop; }
      else if (is(e, "TRADE_EXECUTED")) { executed++; if (e.payload.actorKind === "NPC") byNpc++; else byHuman++; }
      else if (is(e, "TRADE_REJECTED")) rejectedTrades++;
      else if (is(e, "MARKET_PRICE_UPDATED")) priceUpdates++;
      else if (is(e, "CAPTURE_SUCCEEDED")) captures++;
      else if (is(e, "DECISION_RESOLVED")) {
        // Fallback record when no full trace was supplied through onDecision.
        const p = e.payload;
        if (!decisions.has(p.decisionId)) {
          decisions.set(p.decisionId, { source: p.decisionSource, role: p.role, action: p.chosenAction, status: p.status, provider: p.provider, latencyMs: p.latencyMs, evalMs: p.providerEvalMs, rejectReason: p.rejectReason });
        }
      }
    },

    onDecision(t: DecisionTrace): void {
      decisions.set(t.id, {
        source: t.decisionSource, role: t.role, action: t.chosenAction, status: t.status, provider: t.provider, latencyMs: t.latencyMs, evalMs: t.providerEvalMs, rejectReason: t.rejectReason,
        shadow: t.shadow ? { action: t.shadow.chosenAction, latencyMs: t.shadow.latencyMs, agrees: t.shadow.agrees } : null,
      });
    },

    view(): MetricsView {
      const bySource: Record<string, number> = {}, byRole: Record<string, number> = {}, byAction: Record<string, number> = {};
      const lat: Record<ProviderName, number[]> = { drex: [], meta: [] };
      const evals: Record<ProviderName, number[]> = { drex: [], meta: [] };
      let total = 0, rejected = 0, stale = 0;
      // Side-by-side: Drex decisions whose background Meta call also completed. Both latencies are measured round trips.
      const shadowDrex: number[] = [], shadowMeta: number[] = []; let agreed = 0;
      for (const d of decisions.values()) {
        if (d.provider === "drex" && d.shadow && d.shadow.action !== null && typeof d.shadow.latencyMs === "number" && typeof d.latencyMs === "number") {
          shadowDrex.push(d.latencyMs); shadowMeta.push(d.shadow.latencyMs); if (d.shadow.agrees === true) agreed++;
        }
      }
      const p50 = (xs: number[]) => round1(percentile([...xs].sort((a, b) => a - b), 0.5));
      for (const d of decisions.values()) {
        if (d.source === null) continue; // still pending
        total++;
        bySource[d.source] = (bySource[d.source] ?? 0) + 1;
        byRole[d.role] = (byRole[d.role] ?? 0) + 1;
        if (d.action) byAction[d.action] = (byAction[d.action] ?? 0) + 1;
        // Stale = the world moved on while the provider was thinking (captured, halted, reset, sighting lost...).
        // The brains never use the word "stale"; the only rejection that is not stale is an unoffered label.
        if (d.status === "REJECTED") { rejected++; if (d.rejectReason && !/was not offered/i.test(d.rejectReason)) stale++; }
        if (d.provider && typeof d.latencyMs === "number") {
          lat[d.provider].push(d.latencyMs);
          if (typeof d.evalMs === "number") evals[d.provider].push(d.evalMs);
        }
      }
      return {
        runId, mode, seed, startedAtMs,
        decisions: { total, bySource, byRole, byAction, rejected, stale },
        latency: { drex: latencyStats(lat.drex, evals.drex), meta: latencyStats(lat.meta, evals.meta) },
        messages: { sent, delivered, maxHops },
        trades: { executed, rejected: rejectedTrades, byNpc, byHuman },
        priceUpdates, captures,
        edges: [...edges.values()].map((x) => ({ ...x })),
        shadow: shadowDrex.length ? { compared: shadowDrex.length, agreed, drexP50Ms: p50(shadowDrex), metaP50Ms: p50(shadowMeta) } : null,
      };
    },
  };
}
