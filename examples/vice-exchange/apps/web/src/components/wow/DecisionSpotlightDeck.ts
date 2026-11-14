// Card deck for DecisionSpotlight: turns the authoritative event log into at most N on-stage cards.
// It is a tiny external store (subscribe / getSnapshot) so React re-renders only when a card really changes,
// never on the 10 Hz world tick.
import type {
  DecisionMode, DecisionSource, DecisionStatus, DecisionTrace, GameEvent, NpcRole, ProviderName,
} from "@vice/contracts";
import { RULES } from "@vice/contracts";
import type { GameState } from "../../state/gameStore";
import { nameOf } from "../../state/gameStore";

/** Everything a resolved decision reports. Provider numbers stay null when the provider did not supply them. */
export interface Resolution {
  status: DecisionStatus;
  source: DecisionSource;
  provider: ProviderName | null;
  model: string | null;
  chosen: string | null;
  providerChoice: string | null;
  confidence: number | null;
  probabilities: Record<string, number> | null;
  requestId: string | null;
  latencyMs: number | null;
  evalMs: number | null;
  errorType: string | null;
  rejectReason: string | null;
}

export interface SpotCard {
  /** decisionId */
  id: string;
  agentId: string;
  role: NpcRole;
  name: string;
  /** Room mode when the decision was requested (null when we only ever saw the result). */
  mode: DecisionMode | null;
  /** Offered action labels, in the order they were offered. */
  labels: string[];
  /** serverSequence of the first event seen for this decision. */
  seq: number;
  /** What triggered the decision, e.g. "gossip from Marisol Vega". */
  why: string | null;
  /** The memory the NPC is reasoning about, in its own words. */
  knows: string | null;
  /** performance.now() when the request reached this client. */
  bornAt: number;
  phase: "thinking" | "resolved" | "leaving";
  res: Resolution | null;
  /** Fuller record from the agent's recentDecisions / lastDecision, when the store has it. */
  trace: DecisionTrace | null;
  outcome: { ok: boolean; text: string } | null;
  /** True when no result ever reached this client. */
  lost: boolean;
}

export interface DeckSnapshot {
  /** Newest first. Includes cards that are playing their exit animation. */
  stage: SpotCard[];
  /** Decisions waiting for a free slot. */
  queued: number;
  /** Decisions still waiting for an answer (on stage or queued). */
  thinking: number;
}

export interface Deck {
  subscribe(fn: () => void): () => void;
  getSnapshot(): DeckSnapshot;
  /** Marks everything currently in the log as already seen (never replayed). */
  baseline(st: GameState): void;
  /** Processes events that arrived since the last call. */
  ingest(st: GameState): void;
  /** Pulls fuller detail for on-stage cards from the agents in the store. */
  syncTraces(st: GameState): void;
  /** Housekeeping: dismissal, queue promotion, exit removal. Call a few times per second. */
  tick(now: number): void;
  setCapacity(n: number): void;
  setTimings(t: { dwellMs: number; exitMs: number }): void;
  /** Pause / resume the dismissal countdown of one card (hover, keyboard focus). */
  hold(id: string, on: boolean): void;
  clear(): void;
}

const MIN_DWELL_MS = 2400;   // a resolved card is never bumped by a queued one before it has been readable this long
const QUEUE_MAX = 8;
const HOLD_GRACE_MS = 1300;
const LOST_GRACE_MS = 9000;
const LOST_DWELL_MS = 3500;
const SEEN_MAX = 4000;

interface Clock { remaining: number; held: boolean; since: number; leavingAt: number }

function resolutionFromPayload(p: GameEvent<"DECISION_RESOLVED">["payload"]): Resolution {
  return {
    status: p.status, source: p.decisionSource, provider: p.provider ?? null, model: p.model ?? null,
    chosen: p.chosenAction ?? null, providerChoice: p.providerChoice ?? null,
    confidence: p.confidence ?? null, probabilities: p.probabilities ?? null,
    requestId: p.providerRequestId ?? null, latencyMs: p.latencyMs ?? null, evalMs: p.providerEvalMs ?? null,
    errorType: p.errorType ?? null, rejectReason: p.rejectReason ?? null,
  };
}
export function resolutionFromTrace(t: DecisionTrace): Resolution | null {
  if (!t.decisionSource || t.status === "REQUESTED") return null;
  return {
    status: t.status, source: t.decisionSource, provider: t.provider ?? null, model: t.model ?? null,
    chosen: t.chosenAction ?? null, providerChoice: t.providerChoice ?? null,
    confidence: t.confidence ?? null, probabilities: t.probabilities ?? null,
    requestId: t.providerRequestId ?? null, latencyMs: t.latencyMs ?? null, evalMs: t.providerEvalMs ?? null,
    errorType: t.errorType ?? null, rejectReason: t.rejectReason ?? null,
  };
}

function findEvent(events: GameEvent[], id: string | null): GameEvent | null {
  if (!id) return null;
  for (let i = events.length - 1; i >= 0; i--) if (events[i]!.eventId === id) return events[i]!;
  return null;
}
function traceFor(st: GameState, agentId: string, decisionId: string): DecisionTrace | null {
  const a = st.npcs[agentId];
  if (!a) return null;
  if (a.lastDecision?.id === decisionId) return a.lastDecision;
  return a.recentDecisions?.find((t) => t.id === decisionId) ?? null;
}
function knowsFromTrace(t: DecisionTrace | null): string | null {
  const w = t?.stateSnapshot?.["what_just_happened"];
  if (w && typeof w === "object") {
    const s = (w as Record<string, unknown>)["summary"];
    if (typeof s === "string" && s.trim()) return s.trim();
  }
  return null;
}

/** Why this NPC is deciding right now, read from the event that triggered the decision. */
function triggerInfo(st: GameState, triggerEventId: string | null, agentId: string): { why: string | null; knows: string | null } {
  const ev = findEvent(st.events, triggerEventId);
  if (!ev) return { why: null, knows: null };
  switch (ev.type) {
    case "NPC_OBSERVED": {
      const p = (ev as GameEvent<"NPC_OBSERVED">).payload;
      const d = Number.isFinite(p.distance) ? ` · ${Math.round(p.distance)} m` : "";
      return { why: `${p.factType === "SIGHTING" ? "spotted them" : "saw it first-hand"}${d}`, knows: p.summary || null };
    }
    case "AGENT_MESSAGE_DELIVERED": {
      const p = (ev as GameEvent<"AGENT_MESSAGE_DELIVERED">).payload;
      const mem = st.npcs[agentId]?.memories?.find((m) => m.id === p.memoryId);
      let knows = mem?.summary ?? null;
      if (!knows) {
        for (let i = st.events.length - 1; i >= 0; i--) {
          const e = st.events[i]!;
          if (e.type === "AGENT_MESSAGE_SENT" && (e as GameEvent<"AGENT_MESSAGE_SENT">).payload.messageId === p.messageId) {
            knows = (e as GameEvent<"AGENT_MESSAGE_SENT">).payload.summary || null;
            break;
          }
        }
      }
      return { why: `${String(p.kind).toLowerCase()} from ${nameOf(st, p.senderId)}`, knows };
    }
    case "BULLETIN_PUBLISHED": {
      const p = (ev as GameEvent<"BULLETIN_PUBLISHED">).payload;
      const who = p.ticker ?? nameOf(st, p.characterId);
      return { why: `wanted bulletin ${who}`, knows: `Wanted bulletin for ${who}, issued by ${nameOf(st, p.issuedBy)}` };
    }
    case "LISTING_OPENED": {
      const p = (ev as GameEvent<"LISTING_OPENED">).payload;
      return { why: `new listing ${p.ticker}`, knows: `${p.ticker} (${p.nickname}) listed at V$${p.spotPrice}` };
    }
    default:
      return { why: null, knows: null };
  }
}

export function createDeck(init: { capacity: number; dwellMs: number; exitMs: number }): Deck {
  let capacity = Math.max(1, init.capacity);
  let dwellMs = init.dwellMs;
  let exitMs = init.exitMs;
  let stage: SpotCard[] = [];
  let queue: SpotCard[] = [];
  let snap: DeckSnapshot = { stage, queued: 0, thinking: 0 };
  const clocks = new Map<string, Clock>();
  const listeners = new Set<() => void>();
  const seen = new Set<string>();
  let floorSeq = -1;
  let runId = "";
  let lastTick = 0;

  const emit = () => {
    let thinking = 0;
    for (const c of stage) if (c.phase === "thinking") thinking++;
    for (const c of queue) if (c.phase === "thinking") thinking++;
    snap = { stage, queued: queue.length, thinking };
    for (const l of listeners) l();
  };
  const live = () => stage.reduce((n, c) => n + (c.phase === "leaving" ? 0 : 1), 0);
  const find = (id: string): SpotCard | undefined => stage.find((c) => c.id === id) ?? queue.find((c) => c.id === id);
  function patch(id: string, p: Partial<SpotCard>): boolean {
    let hit = false;
    const f = (c: SpotCard) => { if (c.id !== id) return c; hit = true; return { ...c, ...p }; };
    stage = stage.map(f);
    if (!hit) queue = queue.map(f);
    return hit;
  }
  function putOnStage(card: SpotCard, now: number): void {
    stage = [card, ...stage];
    clocks.set(card.id, { remaining: card.lost ? LOST_DWELL_MS : dwellMs, held: false, since: now, leavingAt: 0 });
  }
  function dismiss(id: string, now: number): void {
    const c = stage.find((x) => x.id === id);
    if (!c || c.phase === "leaving") return;
    const k = clocks.get(id);
    if (k) k.leavingAt = now;
    stage = stage.map((x) => (x.id === id ? { ...x, phase: "leaving" as const } : x));
  }
  /** The resolved card that has been readable the longest (and long enough), unless someone is inspecting it. */
  function bumpable(now: number): SpotCard | null {
    let best: SpotCard | null = null, bestAge = MIN_DWELL_MS;
    for (const c of stage) {
      const k = clocks.get(c.id);
      if (c.phase !== "resolved" || !k || k.held) continue;
      const age = now - k.since;
      if (age >= bestAge) { best = c; bestAge = age; }
    }
    return best;
  }
  function admit(card: SpotCard, now: number): void {
    if (live() >= capacity) {
      const b = bumpable(now);
      if (b) dismiss(b.id, now);
    }
    if (live() < capacity) { putOnStage(card, now); return; }
    queue = [...queue, card];
    if (queue.length > QUEUE_MAX) {
      // keep the newest; drop a queued card that already resolved first, otherwise the oldest
      const i = queue.findIndex((c) => c.phase !== "thinking");
      queue = queue.filter((_, j) => j !== (i >= 0 ? i : 0));
    }
  }

  function apply(e: GameEvent, st: GameState, now: number): boolean {
    switch (e.type) {
      case "DECISION_REQUESTED": {
        const p = (e as GameEvent<"DECISION_REQUESTED">).payload;
        if (!p?.decisionId || find(p.decisionId)) return false;
        const trace = traceFor(st, p.agentId, p.decisionId);
        const t = triggerInfo(st, p.triggerEventId ?? e.causationId, p.agentId);
        admit({
          id: p.decisionId, agentId: p.agentId, role: p.role, name: st.npcs[p.agentId]?.name ?? st.characters?.[p.agentId]?.nickname ?? p.agentId,
          mode: p.mode ?? null, labels: [...(p.offeredActions ?? [])], seq: e.serverSequence,
          why: t.why, knows: knowsFromTrace(trace) ?? t.knows, bornAt: now, phase: "thinking",
          res: null, trace, outcome: null, lost: false,
        }, now);
        return true;
      }
      case "DECISION_RESOLVED": {
        const p = (e as GameEvent<"DECISION_RESOLVED">).payload;
        if (!p?.decisionId) return false;
        const res = resolutionFromPayload(p);
        const cur = find(p.decisionId);
        if (cur) {
          if (cur.phase !== "thinking") return false;
          patch(cur.id, { phase: "resolved", res, lost: false, labels: p.offeredActions?.length ? [...p.offeredActions] : cur.labels });
          const k = clocks.get(cur.id);
          if (k) { k.since = now; k.remaining = dwellMs; }
          return true;
        }
        // The request was before our time (or was skipped): still show the result.
        const trace = traceFor(st, p.agentId, p.decisionId);
        const t = triggerInfo(st, e.causationId, p.agentId);
        admit({
          id: p.decisionId, agentId: p.agentId, role: p.role, name: st.npcs[p.agentId]?.name ?? st.characters?.[p.agentId]?.nickname ?? p.agentId,
          mode: null, labels: [...(p.offeredActions ?? [])], seq: e.serverSequence,
          why: t.why, knows: knowsFromTrace(trace) ?? t.knows, bornAt: now, phase: "resolved",
          res, trace, outcome: null, lost: false,
        }, now);
        return true;
      }
      case "AGENT_ACTION_APPLIED": {
        const p = (e as GameEvent<"AGENT_ACTION_APPLIED">).payload;
        return !!p?.decisionId && patch(p.decisionId, { outcome: { ok: true, text: p.detail || p.action } });
      }
      case "AGENT_ACTION_REJECTED": {
        const p = (e as GameEvent<"AGENT_ACTION_REJECTED">).payload;
        return !!p?.decisionId && patch(p.decisionId, { outcome: { ok: false, text: p.reason } });
      }
      default:
        return false;
    }
  }

  function reset(): void {
    stage = []; queue = []; clocks.clear();
  }
  function baseline(st: GameState): void {
    runId = st.runId;
    seen.clear();
    floorSeq = -1;
    for (const e of st.events) {
      seen.add(e.eventId);
      if (e.serverSequence > floorSeq) floorSeq = e.serverSequence;
    }
  }

  return {
    subscribe(fn) { listeners.add(fn); return () => { listeners.delete(fn); }; },
    getSnapshot: () => snap,

    baseline,

    ingest(st) {
      if (st.runId !== runId) {
        // seeded reset or another room: drop every card and never replay what the new log already holds
        const had = stage.length + queue.length > 0;
        reset();
        baseline(st);
        if (had) emit();
        return;
      }
      const evs = st.events;
      const fresh: GameEvent[] = [];
      let known = 0;
      for (let i = evs.length - 1; i >= 0 && known < 64; i--) {
        const e = evs[i]!;
        if (e.serverSequence <= floorSeq || seen.has(e.eventId)) { known++; continue; }
        known = 0;
        fresh.push(e);
      }
      if (!fresh.length) return;
      fresh.sort((a, b) => a.serverSequence - b.serverSequence);
      const now = performance.now();
      let changed = false;
      for (const e of fresh) {
        seen.add(e.eventId);
        changed = apply(e, st, now) || changed;
      }
      if (seen.size > SEEN_MAX) {
        let drop = seen.size - SEEN_MAX / 2;
        for (const id of seen) { if (drop-- <= 0) break; seen.delete(id); }
      }
      if (changed) emit();
    },

    syncTraces(st) {
      if (!stage.length && !queue.length) return;
      const now = performance.now();
      let changed = false;
      const f = (c: SpotCard): SpotCard => {
        if (c.phase === "leaving") return c;
        const a = st.npcs[c.agentId];
        const t = traceFor(st, c.agentId, c.id);
        const name = a?.name ?? c.name;
        const sameTrace = !t || (c.trace != null && c.trace.status === t.status && c.trace.effectEventIds?.length === t.effectEventIds?.length
          && c.trace.providerRequestId === t.providerRequestId);
        if (sameTrace && name === c.name) return c;
        changed = true;
        const next: SpotCard = { ...c, name, trace: t ?? c.trace, knows: c.knows ?? knowsFromTrace(t) };
        if (t && c.phase === "thinking") {
          // The agent record already carries the final result (e.g. the event was lost in a reconnect).
          const res = resolutionFromTrace(t);
          if (res) {
            next.phase = "resolved"; next.res = res; next.lost = false;
            const k = clocks.get(c.id);
            if (k) { k.since = now; k.remaining = dwellMs; }
          }
        }
        return next;
      };
      stage = stage.map(f);
      queue = queue.map(f);
      if (changed) emit();
    },

    tick(now) {
      const dt = lastTick ? Math.min(1000, now - lastTick) : 0;
      lastTick = now;
      if (!stage.length && !queue.length) return;
      let changed = false;

      // 1. decisions whose result never arrived
      const lostCheck = (c: SpotCard): SpotCard => {
        if (c.phase !== "thinking") return c;
        const deadline = c.mode === "drex" || c.mode === "meta" ? RULES.decisions.deadlineMs[c.mode] : 3000;
        if (now - c.bornAt < deadline + LOST_GRACE_MS) return c;
        changed = true;
        const k = clocks.get(c.id);
        if (k) { k.since = now; k.remaining = LOST_DWELL_MS; }
        return { ...c, phase: "resolved", lost: true };
      };
      stage = stage.map(lostCheck);
      queue = queue.map(lostCheck).filter((c) => !c.lost);

      // 2. dismissal countdown (paused while a card is hovered or focused)
      for (const c of stage) {
        if (c.phase !== "resolved") continue;
        const k = clocks.get(c.id);
        if (!k || k.held) continue;
        k.remaining -= dt;
        if (k.remaining <= 0) { dismiss(c.id, now); changed = true; }
      }

      // 3. somebody is waiting: bump the oldest resolved card that has had its moment
      if (queue.length && live() >= capacity) {
        const b = bumpable(now);
        if (b) { dismiss(b.id, now); changed = true; }
      }
      // 4. over capacity after a resize
      while (live() > capacity) {
        const victim = [...stage].reverse().find((c) => c.phase === "resolved") ?? [...stage].reverse().find((c) => c.phase !== "leaving");
        if (!victim) break;
        dismiss(victim.id, now); changed = true;
      }
      // 5. promote from the queue
      while (queue.length && live() < capacity) {
        const next = queue[0]!;
        queue = queue.slice(1);
        putOnStage(next, now);
        changed = true;
      }
      // 6. remove cards whose exit animation has finished
      const before = stage.length;
      stage = stage.filter((c) => {
        if (c.phase !== "leaving") return true;
        const k = clocks.get(c.id);
        if (k && now - k.leavingAt < exitMs) return true;
        clocks.delete(c.id);
        return false;
      });
      if (stage.length !== before) changed = true;

      if (changed) emit();
    },

    setCapacity(n) {
      capacity = Math.max(1, Math.floor(n));
    },
    setTimings(t) {
      dwellMs = t.dwellMs;
      exitMs = t.exitMs;
    },
    hold(id, on) {
      const k = clocks.get(id);
      if (!k) return;
      if (k.held && !on) k.remaining = Math.max(k.remaining, HOLD_GRACE_MS);
      k.held = on;
    },
    clear() {
      const had = stage.length + queue.length > 0;
      reset();
      if (had) emit();
    },
  };
}
