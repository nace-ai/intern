import { createHash } from "node:crypto";
import { RULES, dist } from "@vice/contracts";
import type { DecisionTrace, EntityId, GameEvent, MemoryEntry, Vec2 } from "@vice/contracts";
import type { BrainsPort, DecisionRequest, MemoryPort, NpcState, PolicyPort, SchedulerPort, WorldPort } from "../domain";
import { buildPlan, freshSighting } from "./roles";
import type { InformantLedger } from "./roles";

export interface BrainsDeps {
  world: WorldPort; memory: MemoryPort; scheduler: SchedulerPort;
  policy?: PolicyPort | null; onTrace?: (t: DecisionTrace) => void;
}
interface Trigger { npcId: EntityId; eventId: string; correlationId: string; find: () => MemoryEntry | undefined; tries: number }
interface Pending { trace: DecisionTrace; req: DecisionRequest; trigger: MemoryEntry; runId: string }

const hashOf = (v: unknown) => createHash("sha256").update(JSON.stringify(v)).digest("hex").slice(0, 16);
const pos = (m: MemoryEntry, fallback: Vec2): Vec2 => {
  const x = Number(m.payload.x), z = Number(m.payload.z);
  return Number.isFinite(x) && Number.isFinite(z) ? { x, z } : { x: fallback.x, z: fallback.z };
};

export function createBrains(deps: BrainsDeps): BrainsPort {
  const { world, memory, scheduler } = deps;
  const policy = deps.policy ?? undefined;
  const pending = new Map<EntityId, Pending>();       // one decision in flight per NPC
  const deferred = new Map<EntityId, Trigger>();      // newest trigger waiting for cooldown / pending to clear
  const cooldownUntil = new Map<EntityId, number>();
  const seen = new Set<string>();
  const tracesByNpc = new Map<EntityId, DecisionTrace[]>();
  const allTraces: DecisionTrace[] = [];
  const ledgers = new Map<EntityId, InformantLedger>();  // informant side deals: earnings and last side taken
  let collecting: string[] | null = null;              // effect event ids while an action is being applied

  function publish(t: DecisionTrace) {
    const list = tracesByNpc.get(t.agentId) ?? [];
    const i = list.findIndex((x) => x.id === t.id);
    if (i >= 0) list[i] = t; else list.push(t);
    tracesByNpc.set(t.agentId, list);
    const j = allTraces.findIndex((x) => x.id === t.id);
    if (j >= 0) allTraces[j] = t; else allTraces.push(t);
    deps.onTrace?.(t);
  }

  function trigger(t: Trigger) {
    const key = `${t.npcId}:${t.eventId}`;
    if (seen.has(key)) return;
    const npc = world.state.npcs[t.npcId];
    if (!npc) return;
    if (pending.has(npc.id) || world.now() < (cooldownUntil.get(npc.id) ?? 0)) { deferred.set(npc.id, t); return; }
    const mem = t.find();
    if (!mem) { if (t.tries++ < 30) deferred.set(npc.id, t); return; }
    seen.add(key);
    think(npc, mem, t);
  }

  function think(npc: NpcState, mem: MemoryEntry, t: Trigger) {
    const nowMs = world.now();
    const memories = memory.recall(npc.id, nowMs);     // this NPC's memory only
    const plan = buildPlan(world, npc, memories, mem, policy, ledgers.get(npc.id));
    if (!plan) return;
    const req: DecisionRequest = {
      decisionId: world.nextId("dec"), roomId: world.state.id, runId: world.state.runId,
      agentId: npc.id, agentName: npc.name, role: npc.role,
      question: plan.question, options: plan.options, state: plan.state,
      scriptedAction: plan.scriptedAction in plan.options ? plan.scriptedAction : plan.safeAction, safeAction: plan.safeAction,
      triggerEventId: t.eventId, correlationId: t.correlationId, requestedAtMs: nowMs,
      requestedAtSequence: world.state.lastEventSequence, citations: plan.citations, dedupeKey: `${npc.id}:${t.eventId}`,
    };
    const initial = scheduler.request(req, (final) => resolve(npc.id, req.decisionId, final));
    if (!initial) return;
    cooldownUntil.set(npc.id, nowMs + RULES.decisions.npcCooldownMs);
    const stateHash = initial.stateHash || hashOf(plan.state);
    const trace: DecisionTrace = { ...initial, stateHash };
    pending.set(npc.id, { trace, req, trigger: mem, runId: world.state.runId });
    world.emit("DECISION_REQUESTED", npc.id, {
      decisionId: req.decisionId, agentId: npc.id, role: npc.role, mode: scheduler.getMode(),
      offeredActions: Object.keys(plan.options), triggerEventId: t.eventId, stateHash,
    }, { causationId: t.eventId, correlationId: t.correlationId });
    publish(trace);
  }

  /** Re-check the world as it is NOW. Returns a reject reason, or null when the action is still legal. */
  function staleReason(p: Pending, npc: NpcState | undefined, action: string | null): string | null {
    if (world.state.runId !== p.runId) return "room was reset while the decision was pending";
    if (!npc) return "agent no longer exists";
    if (!action || !(action in p.req.options)) return `action "${action}" was not offered`;
    const subjectId = p.trigger.subjectCharacterId;
    const subject = subjectId ? world.state.characters[subjectId] : undefined;
    if (subjectId && (!subject || subject.status === "CAPTURED" || subject.status === "ARCHIVED")) return "subject was captured before the decision arrived";
    if (npc.role === "TRADER" && (action === "buy" || action === "sell")) {
      const ticker = p.trigger.subjectTicker ?? subject?.ticker ?? null;
      const listing = ticker ? world.state.listings[ticker] : undefined;
      if (!listing || listing.status !== "ACTIVE") return "listing is halted";
      const shares = npc.walletId ? world.state.wallets[npc.walletId]?.holdings[listing.ticker]?.shares ?? 0 : 0;
      if (action === "sell" && shares <= 0) return "no shares left to sell";
    }
    if (npc.role === "POLICE" && action === "pursue" && !freshSighting(memory.recall(npc.id, world.now()), subjectId, world.now())) return "no fresh sighting of the suspect any more";
    if (npc.role === "INFORMANT" && (action === "sell_to_crew" || action === "warn_outlaw")) {
      if (!(world.hunterCrews?.() ?? []).length) return "no hunter crew any more";
      if (!subject || subject.status !== "WANTED") return "the outlaw is no longer wanted";
    }
    if (npc.role === "WITNESS" && action === "gossip" && !npc.contacts.some((id) => world.state.npcs[id])) return "no informant contact";
    return null;
  }

  function apply(p: Pending, npc: NpcState, action: string, causationId: string): string {
    const m = p.trigger, opts = { causationId, correlationId: p.req.correlationId };
    const subject = m.subjectCharacterId;
    const about = m.subjectTicker ?? subject ?? "someone";
    const msg = (recipientId: EntityId, kind: "REPORT" | "GOSSIP" | "RELAY") => world.sendMessage({
      senderId: npc.id, recipientId, kind, sourceMemoryId: m.id, sourceEventId: m.eventId, subjectCharacterId: subject,
      summary: m.summary, payload: { ...m.payload },
    }, opts);
    switch (`${npc.role}:${action}`) {
      case "WITNESS:report": {
        const police = Object.values(world.state.npcs).filter((n) => n.role === "POLICE").sort((a, b) => dist(a, npc) - dist(b, npc) || (a.id < b.id ? -1 : 1))[0];
        if (!police) return "no police to report to";
        msg(police.id, "REPORT");
        return `reported ${about} to ${police.name}`;
      }
      case "WITNESS:gossip": {
        const to = world.state.npcs[npc.contacts.find((id) => world.state.npcs[id]) ?? ""]!;
        msg(to.id, "GOSSIP");
        return `gossiped about ${about} to ${to.name}`;
      }
      case "INFORMANT:relay": {
        const to = npc.contacts.map((id) => world.state.npcs[id]).filter((n): n is NpcState => !!n);
        to.forEach((n) => msg(n.id, "RELAY"));
        return `relayed the tip about ${about} to ${to.map((n) => n.name).join(", ") || "nobody"}`;
      }
      case "INFORMANT:sell_to_crew": case "INFORMANT:warn_outlaw": {
        const side = action === "sell_to_crew" ? "crew" as const : "outlaw" as const;
        const led = ledgers.get(npc.id) ?? { earnings: 0, lastSide: null };
        const target = world.state.characters[subject ?? ""];
        const crews = world.hunterCrews?.() ?? [];
        if (!target || crews.length === 0) return "no deal: nobody to sell to";
        const fee = RULES.crew.tipFee;
        const from = led.lastSide;
        const switched = from !== null && from !== side;
        led.earnings += fee; led.lastSide = side; ledgers.set(npc.id, led);
        world.creditNpc?.(npc.id, fee, side === "crew" ? "fee: sold a location to a hunter crew" : "fee: sold a warning to the outlaw", {});
        if (side === "crew") {
          const at = pos(m, target);
          // Sold to the largest crew (first formed on a tie).
          const crew = [...crews].sort((a, b) => b.memberPlayerIds.length - a.memberPlayerIds.length || (a.id < b.id ? -1 : 1))[0]!;
          world.emit("CREW_TIP", npc.id, { crewId: crew.id, crewName: crew.name, memberPlayerIds: crew.memberPlayerIds, informantId: npc.id, informantName: npc.name, characterId: target.id, ticker: target.ticker, x: at.x, z: at.z, fee, earnings: led.earnings }, opts);
        } else {
          world.emit("OUTLAW_WARNED", npc.id, { characterId: target.id, playerId: target.playerId, ticker: target.ticker, crewNames: crews.map((k) => k.name), informantId: npc.id, informantName: npc.name, fee, earnings: led.earnings }, opts);
        }
        if (switched && from) world.emit("INFORMANT_SWITCHED_SIDES", npc.id, { informantId: npc.id, informantName: npc.name, from, to: side, characterId: target.id }, opts);
        return side === "crew" ? `sold the last known location of ${about} to a hunter crew for V$${fee}${switched ? " (switched sides)" : ""}` : `sold ${about} a warning about the hunter crew for V$${fee}${switched ? " (switched sides)" : ""}`;
      }
      case "TRADER:buy": case "TRADER:sell": {
        const ticker = m.subjectTicker ?? (subject ? world.state.characters[subject]?.ticker : null) ?? "";
        const r = world.placeNpcOrder(npc.id, ticker, action === "buy" ? "BUY" : "SELL", opts);
        return r.ok ? `${action === "buy" ? "bought" : "sold"} 1 ${ticker} at V$${r.trade.executionPrice}` : `${action} order for ${ticker} was rejected: ${r.reason}`;
      }
      case "POLICE:investigate": {
        // Same test the engine applies: without a fresh sighting of their own, the officer waits for dispatch, then searches on foot.
        const direct = !!freshSighting(memory.recall(npc.id, world.now()), subject, world.now());
        world.setNpcActivity(npc.id, "INVESTIGATE", { moveTarget: pos(m, npc), targetCharacterId: subject });
        return direct ? `heading to the last known location of ${about}`
          : `investigating ${about}: dispatch in ${Math.round(RULES.policeDispatchDelayMs / 1000)} s, then on foot to the last known location`;
      }
      case "POLICE:pursue":
        world.setNpcActivity(npc.id, "PURSUE", { targetCharacterId: subject, moveTarget: null });
        return `pursuing ${about}`;
      case "POLICE:patrol":
        world.setNpcActivity(npc.id, "PATROL", { moveTarget: null, targetCharacterId: null });
        return "staying on patrol";
      default:
        return action === "hold" ? `holding ${about}` : action === "withhold" ? `kept the tip about ${about} private` : `chose to ${action}`;
    }
  }

  function resolve(npcId: EntityId, decisionId: string, final: DecisionTrace) {
    const p = pending.get(npcId);
    if (!p || p.req.decisionId !== decisionId) return;  // cancelled by reset()
    pending.delete(npcId);
    const npc = world.state.npcs[npcId];
    const source = final.decisionSource ?? "fallback";
    const action = final.chosenAction;
    const reject = staleReason(p, npc, action);
    const trace: DecisionTrace = {
      ...final, id: decisionId, stateHash: p.trace.stateHash, decisionSource: source,
      resolvedAtMs: final.resolvedAtMs ?? world.now(), effectEventIds: [],
      status: reject ? "REJECTED" : source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED",
      rejectReason: reject ?? final.rejectReason ?? null,
    };
    const opts = { causationId: p.req.triggerEventId, correlationId: p.req.correlationId };
    const resolved = world.emit("DECISION_RESOLVED", npcId, {
      decisionId, agentId: npcId, role: p.req.role, status: trace.status, decisionSource: source,
      provider: trace.provider, model: trace.model, chosenAction: action, providerChoice: trace.providerChoice,
      offeredActions: Object.keys(p.req.options), confidence: trace.confidence, probabilities: trace.probabilities,
      providerRequestId: trace.providerRequestId, latencyMs: trace.latencyMs, providerEvalMs: trace.providerEvalMs,
      errorType: trace.errorType, rejectReason: trace.rejectReason,
    }, opts);
    const after = { causationId: resolved.eventId, correlationId: p.req.correlationId };
    if (reject || !npc || !action) {
      const ev = world.emit("AGENT_ACTION_REJECTED", npcId, { decisionId, agentId: npcId, role: p.req.role, action, reason: reject ?? "no action" }, after);
      trace.effectEventIds = [ev.eventId];
    } else {
      collecting = [];
      let detail: string;
      try { detail = apply(p, npc, action, resolved.eventId); } finally { trace.effectEventIds = collecting; collecting = null; }
      const ev = world.emit("AGENT_ACTION_APPLIED", npcId, { decisionId, agentId: npcId, role: p.req.role, action, decisionSource: source, detail }, after);
      trace.effectEventIds = [...trace.effectEventIds, ev.eventId];
    }
    publish(trace);
  }

  const byMemoryId = (id: string) => () => memory.get(id);

  return {
    onEvent(event: GameEvent) {
      if (collecting && !event.type.startsWith("DECISION_") && !event.type.startsWith("AGENT_ACTION_")) collecting.push(event.eventId);
      const base = { eventId: event.eventId, correlationId: event.correlationId, tries: 0 };
      const link = { causationId: event.eventId, correlationId: event.correlationId };
      switch (event.type) {
        case "NPC_OBSERVED": {
          const p = (event as GameEvent<"NPC_OBSERVED">).payload;
          const npc = world.state.npcs[p.npcId], mem = memory.get(p.memoryId);
          if (!npc) return;
          if (npc.role === "POLICE" && p.factType === "INCIDENT" && p.subjectCharacterId) {
            world.publishBulletin(npc.id, p.subjectCharacterId, mem ? pos(mem, npc) : { x: npc.x, z: npc.z }, link);
          }
          trigger({ ...base, npcId: npc.id, find: byMemoryId(p.memoryId) });
          return;
        }
        case "AGENT_MESSAGE_DELIVERED": {
          const p = (event as GameEvent<"AGENT_MESSAGE_DELIVERED">).payload;
          const npc = world.state.npcs[p.recipientId], mem = memory.get(p.memoryId);
          if (!npc || p.kind === "BULLETIN") return;       // bulletins are handled by BULLETIN_PUBLISHED
          if (npc.role === "POLICE" && p.kind === "REPORT" && mem?.subjectCharacterId) {
            world.publishBulletin(npc.id, mem.subjectCharacterId, pos(mem, npc), link);
          }
          trigger({ ...base, npcId: npc.id, find: byMemoryId(p.memoryId) });
          return;
        }
        case "BULLETIN_PUBLISHED": {
          const p = (event as GameEvent<"BULLETIN_PUBLISHED">).payload;
          for (const id of p.recipientIds) {
            const npc = world.state.npcs[id];
            if (!npc || id === p.issuedBy || (npc.role !== "TRADER" && npc.role !== "POLICE")) continue;
            trigger({ ...base, npcId: id, find: () => memory.recall(id, world.now()).find((m) => m.factType === "WANTED_BULLETIN" && m.subjectCharacterId === p.characterId) });
          }
          return;
        }
        case "LISTING_OPENED": {
          const p = (event as GameEvent<"LISTING_OPENED">).payload;
          const nowMs = world.now();
          for (const npc of Object.values(world.state.npcs)) {
            if (npc.role !== "TRADER") continue;
            // ENGINE normally writes the LISTING memory before emitting; create it only if it is missing.
            const mem = memory.recall(npc.id, nowMs).find((m) => m.factType === "LISTING" && m.subjectTicker === p.ticker) ?? memory.remember({
              agentId: npc.id, factType: "LISTING", subjectCharacterId: p.characterId, subjectTicker: p.ticker, eventId: event.eventId,
              sourceActorId: "exchange", sourceKind: "BULLETIN", sourceMemoryId: null, hop: 0, observedAtMs: nowMs,
              expiresAtMs: nowMs + RULES.memory.ttlMs.LISTING, confidence: RULES.memory.confidence.BULLETIN,
              summary: `New listing ${p.ticker} (${p.nickname}) opened at V$${p.spotPrice}`,
              payload: { ticker: p.ticker, spotPrice: p.spotPrice }, citation: null,
            }, world.nextId("mem"));
            trigger({ ...base, npcId: npc.id, find: byMemoryId(mem.id) });
          }
          return;
        }
        default: return;
      }
    },
    onTick(nowMs) {
      for (const [npcId, t] of [...deferred]) {
        if (pending.has(npcId) || nowMs < (cooldownUntil.get(npcId) ?? 0)) continue;
        deferred.delete(npcId);
        trigger(t);
      }
    },
    view(npcId) {
      const list = tracesByNpc.get(npcId) ?? [];
      const done = list.filter((t) => t.status !== "REQUESTED");
      return {
        memories: memory.recall(npcId, world.now()),
        pendingDecisionId: pending.get(npcId)?.req.decisionId ?? null,
        lastDecision: done[done.length - 1] ?? null,
        recentDecisions: list.slice(-8).reverse(),
      };
    },
    traces: () => [...allTraces],
    reset() {
      pending.clear(); deferred.clear(); cooldownUntil.clear(); seen.clear(); tracesByNpc.clear(); allTraces.length = 0; collecting = null; ledgers.clear();
    },
  };
}
