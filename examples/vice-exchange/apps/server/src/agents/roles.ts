// Role logic: what an NPC may choose right now, and the state it is allowed to reason over.
// Everything here is built from the NPC's OWN memory, wallet and contacts plus public market data.
import { RULES } from "@vice/contracts";
import type { Citation, MemoryEntry } from "@vice/contracts";
import type { NpcState, PolicyPort, WorldPort } from "../domain";

export const FRESH_SIGHTING_MS = 10_000;

export interface RolePlan {
  question: string;
  options: Record<string, string>;
  scriptedAction: string;
  safeAction: string;
  state: Record<string, unknown>;
  citations: Citation[];
}

/** A direct, recent sighting of the subject by this NPC (required for `pursue`). */
export function freshSighting(memories: MemoryEntry[], subjectId: string | null, nowMs: number): MemoryEntry | undefined {
  if (!subjectId) return undefined;
  return memories.find((m) => m.subjectCharacterId === subjectId && m.sourceKind === "DIRECT"
    && (m.factType === "SIGHTING" || m.factType === "INCIDENT") && nowMs - m.observedAtMs <= FRESH_SIGHTING_MS);
}

function actorLabel(world: WorldPort, id: string, selfId: string): string {
  if (id === selfId) return "myself";
  const n = world.state.npcs[id];
  if (n) return `${n.name} (${n.role.toLowerCase()})`;
  const c = world.state.characters[id];
  return c ? c.nickname : id;
}

function describeMemory(world: WorldPort, npc: NpcState, m: MemoryEntry, nowMs: number): Record<string, unknown> {
  const source = m.sourceKind === "DIRECT" ? "saw it myself"
    : m.sourceKind === "MESSAGE" ? `told by ${actorLabel(world, m.sourceActorId, npc.id)}`
    : m.sourceKind === "BULLETIN" ? "public bulletin" : "official document";
  return {
    fact: m.factType.toLowerCase(), about: m.subjectTicker ?? m.subjectCharacterId ?? "unknown",
    summary: m.summary, source, hops_from_eyewitness: m.hop,
    my_belief: Number(m.confidence.toFixed(2)), seconds_ago: Math.max(0, Math.round((nowMs - m.observedAtMs) / 1000)),
  };
}

/** What an informant has earned by selling information, and whose side its last sale took. Kept by the brains. */
export interface InformantLedger { earnings: number; lastSide: "crew" | "outlaw" | null }

export function buildPlan(world: WorldPort, npc: NpcState, memories: MemoryEntry[], trigger: MemoryEntry, policy?: PolicyPort, ledger?: InformantLedger): RolePlan | null {
  const nowMs = world.now();
  const subjectId = trigger.subjectCharacterId;
  const ticker = trigger.subjectTicker ?? (subjectId ? world.state.characters[subjectId]?.ticker ?? null : null);
  const wallet = npc.walletId ? world.state.wallets[npc.walletId] : undefined;
  const base: Record<string, unknown> = {
    role: npc.role.toLowerCase(), name: npc.name, persona: npc.persona, goal: npc.goal,
    what_just_happened: describeMemory(world, npc, trigger, nowMs),
    my_memories: memories.slice(0, 8).map((m) => describeMemory(world, npc, m, nowMs)),
    my_contacts: npc.contacts.map((id) => actorLabel(world, id, npc.id)),
  };
  // Every agent is out for its own gain: non-traders see their own earnings (traders see their wallet below).
  if (wallet && npc.role !== "TRADER") {
    const last = world.lastEarning?.(npc.id) ?? null;
    base.my_money = `V$${wallet.balanceCredits} in my own wallet${last ? `; last earned V$${last.amount} (${last.reason})` : "; nothing earned yet"}`;
  }
  const citations: Citation[] = [];
  const about = ticker ?? "the person involved";

  if (npc.role === "WITNESS") {
    const options: Record<string, string> = {
      report: "Tell the city police what I know so they can act on it",
      ignore: "Stay out of it and say nothing for now",
    };
    if (npc.contacts.length > 0) options.gossip = "Quietly tell my informant contact instead of the police";
    return { question: `As this witness, what do you do about what you know regarding ${about}? Use only your own knowledge.`,
      options, scriptedAction: "report", safeAction: "ignore", state: base, citations };
  }
  if (npc.role === "INFORMANT") {
    if (npc.contacts.length === 0) return null;
    const options: Record<string, string> = { relay: "Pass the tip to my trader contacts now", withhold: "Keep it to myself; it is too weak or too risky to pass on" };
    // Side deals exist only while a hunter crew is in town and the subject is a wanted outlaw. Each is for my own gain.
    const crews = world.hunterCrews?.() ?? [];
    const subject = subjectId ? world.state.characters[subjectId] : undefined;
    if (crews.length === 0 || !subject || subject.role !== "OUTLAW" || subject.status !== "WANTED") {
      return { question: `As this informant, do you pass what you know about ${about} on to your trader contacts?`,
        options, scriptedAction: "relay", safeAction: "withhold", state: base, citations };
    }
    const hasLocation = Number.isFinite(Number(trigger.payload.x)) && Number.isFinite(Number(trigger.payload.z));
    if (hasLocation) options.sell_to_crew = `Sell the last known location of ${about} to the hunter crew for a V$${RULES.crew.tipFee} fee: I gain if they catch the outlaw`;
    options.warn_outlaw = `Sell ${about} a warning that a hunter crew is after them for a V$${RULES.crew.tipFee} fee: I gain if the outlaw stays free and keeps paying`;
    const state: Record<string, unknown> = {
      ...base,
      hunter_crews_in_town: crews.map((k) => ({ name: k.name, hunters: k.memberPlayerIds.length })),
      my_earnings_from_deals: ledger?.earnings ?? 0,
      my_last_deal: ledger?.lastSide === "crew" ? "sold a location to the hunter crew" : ledger?.lastSide === "outlaw" ? "sold a warning to the outlaw" : "none yet",
    };
    return { question: `As this informant, what do you do with what you know about ${about}? You may pass it to your trader contacts, keep it, or sell it to one side for your own gain.`,
      options, scriptedAction: "relay", safeAction: "withhold", state, citations };
  }
  if (npc.role === "TRADER") {
    if (!ticker || !wallet) return null;
    const market = world.marketSummary(ticker);
    if (!market || market.status !== "ACTIVE") return null;
    const shares = wallet.holdings[ticker]?.shares ?? 0;
    const options: Record<string, string> = { hold: "Do nothing; the information does not justify a trade" };
    if (shares < RULES.trade.maxSharesPerTicker && wallet.balanceCredits >= market.spotPrice) {
      options.buy = `Buy one share of ${ticker}: I expect its value to rise (more notoriety, still free)`;
    }
    if (shares > 0) options.sell = `Sell one share of ${ticker}: I expect capture risk to rise or the price to fall`;
    return { question: `As this trader, what do you do with ${ticker} right now? A captured character's stock goes to zero.`,
      options, scriptedAction: "hold", safeAction: "hold",
      state: { ...base, my_wallet: { credits: wallet.balanceCredits, shares_held: shares, max_shares: RULES.trade.maxSharesPerTicker },
        public_market: { ticker, price: market.spotPrice, reference_value: market.referenceValue, change_pct_60s: market.changePct60s, net_buy_units_60s: market.netBuyUnits } },
      citations };
  }
  // POLICE
  if (!subjectId) return null;
  const sighting = freshSighting(memories, subjectId, nowMs);
  const options: Record<string, string> = {
    investigate: "Go to the last known location and look for the suspect",
    patrol: "Keep to my normal patrol; the information is too weak to act on",
  };
  if (sighting) options.pursue = "Chase the suspect I can see right now and attempt an arrest";
  if (policy) citations.push(...policy.factsFor("POLICE"));
  const state = { ...base, department_policy: citations.map((c) => ({ rule: c.extractedField, text: c.quote, source: c.documentTitle })) };
  return { question: `As this police officer, what do you do about ${about}?`,
    options, scriptedAction: sighting ? "pursue" : "investigate", safeAction: "patrol", state, citations };
}
