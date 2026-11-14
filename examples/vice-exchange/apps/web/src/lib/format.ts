import type { DecisionSource, DecisionTrace, EventType, GameEvent } from "@vice/contracts";
import { RULES } from "@vice/contracts";
import type { GameState } from "../state/gameStore";
const RULES_STUN_MS = RULES.weapon.stunMs;
import { nameOf } from "../state/gameStore";

export const vd = (n: number | null | undefined): string => (n == null ? "—" : `V$${Math.round(n).toLocaleString("en-US")}`);
export const ms = (n: number | null | undefined): string => (n == null ? "—" : n < 10 ? `${n.toFixed(1)} ms` : `${Math.round(n)} ms`);
export const pct = (n: number): string => `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
export const clock = (iso: string): string => { const d = new Date(iso); return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-US", { hour12: false }); };

export type EventCategory = "world" | "agent" | "market" | "capture" | "system";
export const CATEGORY: Record<EventType, EventCategory> = {
  ROOM_CREATED: "system", ROOM_RESET: "system", MODE_CHANGED: "system", PLAYER_JOINED: "system", PLAYER_LEFT: "system",
  WALLET_OPENED: "system", WALLET_CREDITED: "market", CHARACTER_SPAWNED: "world", LISTING_OPENED: "market", MISSION_SPAWNED: "world",
  MISSION_COMPLETED: "world", INCIDENT_OCCURRED: "world", NPC_OBSERVED: "agent", AGENT_MESSAGE_SENT: "agent",
  AGENT_MESSAGE_DELIVERED: "agent", BULLETIN_PUBLISHED: "agent", DECISION_REQUESTED: "agent", DECISION_RESOLVED: "agent",
  AGENT_ACTION_APPLIED: "agent", AGENT_ACTION_REJECTED: "agent", MARKET_PRICE_UPDATED: "market", TRADE_EXECUTED: "market",
  TRADE_REJECTED: "market", WANTED_STATUS_CHANGED: "capture", ESCAPE_SUCCEEDED: "capture", CAPTURE_SUCCEEDED: "capture",
  CAPTURE_REJECTED: "capture", LISTING_HALTED: "capture", CHARACTER_ARCHIVED: "capture", CHARACTER_RESPAWNED: "world",
  POLICY_INGESTED: "system", WEAPON_FIRED: "capture", STUNNED: "capture",
  CREW_JOINED: "capture", CREW_LEFT: "capture", CREW_BETRAYED: "capture",
  CREW_TIP: "agent", OUTLAW_WARNED: "agent", INFORMANT_SWITCHED_SIDES: "agent",
};

/** Honest source labels: only "drex" is ever shown as Drex. */
export function sourceLabel(src: DecisionSource | null): { text: string; cls: string } {
  switch (src) {
    case "drex": return { text: "DREX · live", cls: "src-drex" };
    case "meta": return { text: "META LLM · live", cls: "src-meta" };
    case "scripted": return { text: "SCRIPTED rule", cls: "src-scripted" };
    case "fallback": return { text: "FALLBACK rule", cls: "src-fallback" };
    case "drex_low_confidence_guardrail": return { text: "DREX unsure → safe action", cls: "src-guard" };
    case "replay": return { text: "REPLAY (recorded)", cls: "src-scripted" };
    default: return { text: "PENDING…", cls: "src-pending" };
  }
}
export function traceHasProviderData(t: DecisionTrace): boolean {
  return (t.decisionSource === "drex" || t.decisionSource === "meta" || t.decisionSource === "drex_low_confidence_guardrail") && t.provider != null;
}

export function describeEvent(e: GameEvent, s: GameState): string {
  const n = (id: string | null | undefined) => nameOf(s, id);
  const p = e.payload as Record<string, any>;
  switch (e.type) {
    case "ROOM_CREATED": return `Room ${p.roomCode} created (seed ${p.seed}, rules ${p.ruleVersion}, ${p.npcCount} NPCs)`;
    case "ROOM_RESET": return `Seeded reset → new run (seed ${p.seed})`;
    case "MODE_CHANGED": return `Decision mode: ${String(p.previousMode).toUpperCase()} → ${String(p.mode).toUpperCase()}`;
    case "PLAYER_JOINED": return `${p.nickname} ${p.reconnect ? "reconnected" : "joined"} as ${p.role}`;
    case "PLAYER_LEFT": return `${p.nickname} disconnected`;
    case "WALLET_OPENED": return `Wallet opened for ${n(p.ownerId)}: ${vd(p.balance)}`;
    case "WALLET_CREDITED": return `${p.ownerName} earned ${vd(p.amount)}: ${p.reason} (wallet now ${vd(p.balanceAfter)})`;
    case "CHARACTER_SPAWNED": return `${p.nickname} spawned as ${p.role}${p.ticker ? ` · ticker ${p.ticker}` : ""}`;
    case "LISTING_OPENED": return `${p.ticker} listed at ${vd(p.spotPrice)}`;
    case "MISSION_SPAWNED": return `New job available: ${p.label}`;
    case "MISSION_COMPLETED": return `${n(p.characterId)} completed a job (+${p.notorietyGained} notoriety, +${p.heatGained} heat)`;
    case "INCIDENT_OCCURRED": return `Incident: ${p.description} — seen by ${p.observerIds?.length ? p.observerIds.map(n).join(", ") : "nobody"}`;
    case "NPC_OBSERVED": return `${n(p.npcId)} saw it first-hand (${Math.round(p.distance)} m): ${p.summary}`;
    case "AGENT_MESSAGE_SENT": return `${n(p.senderId)} → ${n(p.recipientId)} [${p.kind}, hop ${p.hop}]: ${p.summary}`;
    case "AGENT_MESSAGE_DELIVERED": return `${n(p.recipientId)} received ${String(p.kind).toLowerCase()} from ${n(p.senderId)} (hop ${p.hop})`;
    case "BULLETIN_PUBLISHED": return `Wanted bulletin for ${p.ticker ?? n(p.characterId)} issued by ${n(p.issuedBy)}`;
    case "DECISION_REQUESTED": return `${n(p.agentId)} is deciding: ${p.offeredActions.join(" / ")}`;
    case "DECISION_RESOLVED": {
      const src = sourceLabel(p.decisionSource).text;
      const conf = p.confidence != null ? ` · conf ${Number(p.confidence).toFixed(2)}` : "";
      const lat = p.latencyMs != null ? ` · ${ms(p.latencyMs)}` : "";
      const err = p.errorType ? ` · ${p.errorType}` : "";
      return `${n(p.agentId)} chose “${p.chosenAction ?? "—"}” [${src}${conf}${lat}${err}]`;
    }
    case "AGENT_ACTION_APPLIED": return `${n(p.agentId)}: ${p.detail || p.action}`;
    case "AGENT_ACTION_REJECTED": return `${n(p.agentId)}'s action “${p.action ?? "—"}” rejected: ${p.reason}`;
    case "MARKET_PRICE_UPDATED": return `${p.ticker} ${vd(p.previousSpotPrice)} → ${vd(p.spotPrice)} (${String(p.reason).toLowerCase().replace(/_/g, " ")})`;
    case "TRADE_EXECUTED": return `${p.actorName} ${p.side === "BUY" ? "bought" : "sold"} ${p.qty} ${p.ticker} @ ${vd(p.executionPrice)}`;
    case "TRADE_REJECTED": return `${n(p.actorId)} ${String(p.side).toLowerCase()} ${p.ticker} rejected: ${String(p.reason).toLowerCase().replace(/_/g, " ")}`;
    case "WANTED_STATUS_CHANGED": return `${p.ticker ?? n(p.characterId)} is ${p.wanted ? "now WANTED" : "no longer wanted"} (heat ${Math.round(p.heat)}) — ${p.reason}`;
    case "ESCAPE_SUCCEEDED": return `${p.ticker ?? n(p.characterId)} shook the heat (escape #${p.successfulEscapes})`;
    case "CAPTURE_SUCCEEDED": return `${p.captorName} CAPTURED ${p.ticker ?? n(p.characterId)} after ${Math.round(p.survivalSeconds)} s${p.bounty ? ` · bounty ${vd(p.bounty)}` : ""}${p.doubleCross ? ` · DOUBLE-CROSS: took it all from crew ${p.crewName}` : Array.isArray(p.shares) && p.shares.length > 1 ? ` · split ${p.shares.length} ways in crew ${p.crewName} (${p.shares.map((x: { amount: number }) => vd(x.amount)).join(" / ")})` : ""}`;
    case "CREW_JOINED": return `${p.nickname} ${p.created ? "formed" : "joined"} hunter crew ${p.crewName} (${p.memberPlayerIds.length} of ${RULES.crew.maxMembers})`;
    case "CREW_LEFT": return `${p.nickname} ${p.reason === "EJECTED" ? "was thrown out of" : "left"} crew ${p.crewName}`;
    case "CREW_TIP": return `${p.informantName} sold the last known location of ${p.ticker ?? n(p.characterId)} to crew ${p.crewName} for ${vd(p.fee)} (earned ${vd(p.earnings)} so far)`;
    case "OUTLAW_WARNED": return `${p.informantName} sold ${p.ticker ?? n(p.characterId)} a warning: crew ${p.crewNames.join(", ")} is hunting them (${vd(p.fee)})`;
    case "INFORMANT_SWITCHED_SIDES": return `${p.informantName} SWITCHED SIDES: from the ${p.from} to the ${p.to}`;
    case "CREW_BETRAYED": return `DOUBLE-CROSS in crew ${p.crewName}: ${p.betrayerName} stunned ${p.victimNames.join(" and ")} and kept the whole ${vd(p.bounty)} bounty`;
    case "CAPTURE_REJECTED": return `Capture attempt by ${n(p.captorId)} rejected: ${String(p.reason).toLowerCase().replace(/_/g, " ")}`;
    case "LISTING_HALTED": return `${p.ticker} HALTED — ${vd(p.finalSpotBeforeHalt)} → V$0`;
    case "CHARACTER_ARCHIVED": return `${p.ticker ?? n(p.characterId)} archived (history kept)`;
    case "CHARACTER_RESPAWNED": return `${n(p.playerId)} respawned with new ticker ${p.ticker ?? "—"}`;
    case "WEAPON_FIRED": return `${p.shooterName} fired a stun pulse${p.hitId ? ` and hit ${n(p.hitId)}` : " and missed"} (${p.ammoLeft} left)`;
    case "STUNNED": return `${p.targetName} is stunned for ${(Number(RULES_STUN_MS) / 1000).toFixed(0)} s (no damage)`;
    case "POLICY_INGESTED": return `Policy document ingested: ${p.documentTitle} (${p.factCount} facts)`;
    default: return e.type;
  }
}
