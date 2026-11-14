// Acceptance stories from VICE_EXCHANGE_REQUIREMENTS.md §7, run against the real engine, brains, economy and
// memory store with a fake clock. Only the decision provider is replaced (MockScheduler), and every mocked
// decision carries an honest source label.
import { describe, expect, it } from "vitest";
import { RULES, clientCommandSchema } from "@vice/contracts";
import type { GameEvent } from "@vice/contracts";
import { ProviderError } from "../src/domain";
import type { DecisionProvider } from "../src/domain";
import { createScheduler } from "../src/decisions";
import { createRoomFactory } from "../src/engine/compose";
import { createMemoryPersistence } from "../src/engine/fallbacks";
import { FakeClock } from "../src/engine/util";
import { BotOutlaw } from "../src/demo/BotOutlaw";
import { verifyReplay } from "../src/replay";
import { makeRoom } from "./helpers";

type Room = ReturnType<typeof makeRoom>;
const memoriesOf = (t: Room, npcId: string) => t.engine.deps.memory.recall(npcId, t.clock.now());
const walletOf = (t: Room, playerId: string) => t.engine.state.wallets[`wal_${playerId}`]!;
const listingOf = (t: Room, ticker: string) => t.engine.state.listings[ticker]!;
const pendingFor = (t: Room, npcId: string) => t.scheduler.queue.find((q) => q.req.agentId === npcId);

/** Outlaw joins; the traders' opening "new listing" decisions are answered (hold) and their cooldown passes. */
function withOutlaw(t: Room, nickname = "Ace") {
  const p = t.join(nickname, "OUTLAW", true);
  t.scheduler.resolve("npc_t1", "hold"); t.scheduler.resolve("npc_t2", "hold");
  t.run(4000);
  return p;
}
/** Mission at the dock, the dock witness reports it, the report reaches police: the outlaw is now WANTED. */
function makeWanted(t: Room, playerId: string) {
  expect(t.doMission(playerId).ok).toBe(true);
  expect(t.scheduler.resolve("npc_w1", "report")).toBe(true);
  t.run(1000);
  expect(t.char(playerId).status).toBe("WANTED");
}

describe("seeded room (FR-004, FR-005)", () => {
  it("spawns 8 NPCs with stable ids across the four roles and one open mission", () => {
    const t = makeRoom();
    const npcs = Object.values(t.engine.state.npcs);
    expect(npcs).toHaveLength(8);
    expect(new Set(npcs.map((n) => n.role))).toEqual(new Set(["WITNESS", "INFORMANT", "TRADER", "POLICE"]));
    expect(new Set(npcs.map((n) => n.id)).size).toBe(8);
    expect(Object.values(t.engine.state.missions).filter((m) => m.status === "AVAILABLE")).toHaveLength(1);
    expect(t.events("ROOM_CREATED")).toHaveLength(1);
  });
});

describe("AT-01: a mission raises reference value", () => {
  it("completes exactly once, raises notoriety and reference, and cannot be repeated", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    const ticker = t.char(a.id).ticker!; const before = listingOf(t, ticker).referenceValue;
    const m = t.mission();

    // Out of range: nothing happens.
    t.place(a.id, m.x + 10, m.z);
    const far = t.cmd(a.id, { type: "mission.interact", missionId: m.id });
    expect(far.ok).toBe(false); expect(far.error?.code).toBe("OUT_OF_RANGE");
    expect(t.events("MISSION_COMPLETED")).toHaveLength(0);

    t.place(a.id, m.x, m.z);
    const first = t.engine.dispatch(a.id, { type: "mission.interact", missionId: m.id, commandId: "mission-cmd-0001" });
    expect(first.ok).toBe(true);
    expect(t.events("MISSION_COMPLETED")).toHaveLength(1);
    expect(t.events("INCIDENT_OCCURRED")).toHaveLength(1);
    const c = t.char(a.id);
    expect(c.notorietyPoints).toBe(RULES.notoriety.perMission);
    expect(c.completedMissions).toBe(1);
    expect(c.heat).toBe(RULES.heat.perMission);
    const gain = RULES.price.perNotorietyPoint * RULES.notoriety.perMission + RULES.price.perMission;
    expect(listingOf(t, ticker).referenceValue).toBe(before + gain);
    expect(t.events("MISSION_COMPLETED")[0]!.payload).toMatchObject({ missionId: m.id, characterId: c.id, notorietyGained: RULES.notoriety.perMission });

    // Same objective again with a new command id: no effect.
    const again = t.cmd(a.id, { type: "mission.interact", missionId: m.id });
    expect(again.ok).toBe(false); expect(again.error?.code).toBe("MISSION_UNAVAILABLE");
    // Same command id replayed (retry after reconnect): the original result, flagged duplicate, no second event.
    const replay = t.engine.dispatch(a.id, { type: "mission.interact", missionId: m.id, commandId: "mission-cmd-0001" });
    expect(replay.ok).toBe(true); expect(replay.duplicate).toBe(true);
    expect(t.events("MISSION_COMPLETED")).toHaveLength(1);
    expect(t.char(a.id).notorietyPoints).toBe(RULES.notoriety.perMission);
  });

  it("only outlaws can run missions", () => {
    const t = makeRoom(); const h = t.join("Bo", "HUNTER"); const s = t.join("Judge", "SPECTATOR");
    const m = t.mission(); t.place(h.id, m.x, m.z);
    expect(t.cmd(h.id, { type: "mission.interact", missionId: m.id }).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    expect(t.cmd(s.id, { type: "mission.interact", missionId: m.id }).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    expect(t.events("MISSION_COMPLETED")).toHaveLength(0);
  });
});

describe("AT-02: private memory does not magically spread", () => {
  it("only the eyewitness knows until explicit messages are delivered, and each hop names its source", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    const charId = t.char(a.id).id; const ticker = t.char(a.id).ticker!;
    expect(t.doMission(a.id).ok).toBe(true);

    const incident = t.events("INCIDENT_OCCURRED")[0]!;
    expect(incident.payload.observerIds).toEqual(["npc_w1"]); // the dock fisher, 7.3 m away with line of sight
    const seen = memoriesOf(t, "npc_w1").find((m) => m.factType === "INCIDENT")!;
    expect(seen).toMatchObject({ sourceKind: "DIRECT", sourceActorId: "npc_w1", hop: 0, subjectCharacterId: charId, eventId: incident.eventId });
    expect(seen.expiresAtMs).toBeGreaterThan(t.clock.now());
    // Nobody else has any memory of the incident.
    for (const id of Object.keys(t.engine.state.npcs).filter((x) => x !== "npc_w1")) {
      expect(memoriesOf(t, id).some((m) => m.factType === "INCIDENT" || m.factType === "RUMOR"), id).toBe(false);
    }
    // The witness is asked to decide from its own snapshot.
    const ask = pendingFor(t, "npc_w1")!;
    expect(Object.keys(ask.req.options).sort()).toEqual(["gossip", "ignore", "report"]);
    expect(JSON.stringify(ask.req.state)).toContain("grabbed");
    expect(t.events("DECISION_REQUESTED").some((e) => e.payload.agentId === "npc_w1")).toBe(true);

    // Witness gossips to its informant contact. Until delivery the informant knows nothing.
    t.scheduler.resolve("npc_w1", "gossip");
    const sent = t.events("AGENT_MESSAGE_SENT")[0]!;
    expect(sent.payload).toMatchObject({ senderId: "npc_w1", recipientId: "npc_i1", kind: "GOSSIP", hop: 1, sourceMemoryId: seen.id });
    expect(memoriesOf(t, "npc_i1").some((m) => m.factType === "RUMOR")).toBe(false);
    t.run(RULES.messages.deliveryDelayMs + 100);
    const heard = memoriesOf(t, "npc_i1").find((m) => m.factType === "RUMOR")!;
    expect(heard).toMatchObject({ sourceKind: "MESSAGE", sourceActorId: "npc_w1", hop: 1, sourceMemoryId: seen.id, subjectCharacterId: charId });
    expect(heard.confidence).toBeLessThan(seen.confidence);
    // The trader still knows nothing, and its pending model input (if any) does not contain the incident.
    expect(memoriesOf(t, "npc_t1").some((m) => m.factType === "RUMOR" || m.factType === "INCIDENT")).toBe(false);
    for (const q of t.scheduler.queue.filter((x) => x.req.agentId !== "npc_i1")) expect(JSON.stringify(q.req.state)).not.toContain("grabbed");

    // Informant relays to its trader contacts.
    expect(Object.keys(pendingFor(t, "npc_i1")!.req.options).sort()).toEqual(["relay", "withhold"]);
    t.scheduler.resolve("npc_i1", "relay");
    t.run(RULES.messages.deliveryDelayMs + 100);
    const tip = memoriesOf(t, "npc_t1").find((m) => m.factType === "RUMOR")!;
    expect(tip).toMatchObject({ sourceKind: "MESSAGE", sourceActorId: "npc_i1", hop: 2, sourceMemoryId: heard.id });
    // Now the trader may decide, and its input names the informant as the source.
    const traderAsk = pendingFor(t, "npc_t1")!;
    expect(JSON.stringify(traderAsk.req.state)).toContain("Lena Okoye");
    expect(Object.keys(traderAsk.req.options)).toContain("buy");
    expect(Object.keys(traderAsk.req.options)).not.toContain("sell"); // holds no shares, so selling is not offered

    // The trader's order uses the same settlement path as a human order.
    const balance = t.engine.state.wallets["wal_npc_t1"]!.balanceCredits;
    t.scheduler.resolve("npc_t1", "buy");
    const trade = t.events("TRADE_EXECUTED").find((e) => e.payload.actorId === "npc_t1")!;
    expect(trade.payload).toMatchObject({ actorKind: "NPC", side: "BUY", qty: 1, ticker });
    expect(t.engine.state.wallets["wal_npc_t1"]!.balanceCredits).toBe(balance - trade.payload.executionPrice);

    // FR-061: the trade links back, event by event, to the mission that started the story.
    const byId = new Map(t.engine.events.map((e) => [e.eventId, e]));
    const links: GameEvent[] = [];
    for (let e: GameEvent | undefined = trade; e; e = e.causationId ? byId.get(e.causationId) : undefined) links.push(e);
    const chain = links.map((e) => e.type);
    expect(chain).toEqual([
      "TRADE_EXECUTED", "DECISION_RESOLVED", "AGENT_MESSAGE_DELIVERED", "AGENT_MESSAGE_SENT", "DECISION_RESOLVED",
      "AGENT_MESSAGE_DELIVERED", "AGENT_MESSAGE_SENT", "DECISION_RESOLVED", "NPC_OBSERVED", "INCIDENT_OCCURRED", "MISSION_COMPLETED",
    ]);
    expect(new Set(links.map((e) => e.correlationId)).size).toBe(1); // one story id from mission to trade
  });

  it("FR-011: an NPC inside the radius but behind a building does not observe", () => {
    const t = makeRoom(); const a = t.join("Ace", "OUTLAW", true);
    t.place(a.id, 42, 40); // 13.9 m from the plaza vendor, but the kiosk blocks the view; 12 m from a trader with a clear view
    const r = t.cmd(a.id, { type: "demo.incident", incidentType: "SIGHTING" });
    expect(r.ok).toBe(true);
    const obs = t.events("INCIDENT_OCCURRED")[0]!.payload.observerIds;
    expect(obs).toContain("npc_t2");
    expect(obs).not.toContain("npc_w2");
    expect(memoriesOf(t, "npc_w2").some((m) => m.factType === "INCIDENT")).toBe(false);
    expect(memoriesOf(t, "npc_t2").some((m) => m.factType === "INCIDENT" && m.sourceKind === "DIRECT")).toBe(true);
  });

  it("demo commands need the director", () => {
    const t = makeRoom(); const a = t.join("Ace", "OUTLAW"); // not a director
    for (const c of [{ type: "demo.incident", incidentType: "SIGHTING" }, { type: "demo.reset" }, { type: "demo.setMode", mode: "scripted" }]) {
      expect(t.cmd(a.id, c).error?.code).toBe("FORBIDDEN");
    }
    expect(t.events("INCIDENT_OCCURRED")).toHaveLength(0);
    expect(t.events("ROOM_RESET")).toHaveLength(0);
  });
});

describe("AT-04: shared market integrity", () => {
  it("moves credits and shares together, once, at the server's quote", () => {
    const t = makeRoom(); const a = withOutlaw(t); const ticker = t.char(a.id).ticker!;
    const quote = t.engine.deps.economy.quote(t.engine.state, ticker, t.clock.now())!;
    const r = t.engine.dispatch(a.id, { type: "market.buy", ticker, qty: 1, commandId: "buy-cmd-000001" });
    expect(r.ok).toBe(true);
    const w = walletOf(t, a.id);
    expect(w.balanceCredits).toBe(RULES.trade.startingWalletHuman - quote.ask);
    expect(w.holdings[ticker]!.shares).toBe(1);
    const ex = t.events("TRADE_EXECUTED");
    expect(ex).toHaveLength(1);
    expect(ex[0]!.payload).toMatchObject({ actorId: a.id, actorKind: "HUMAN", side: "BUY", qty: 1, executionPrice: quote.ask, balanceAfter: w.balanceCredits, sharesAfter: 1 });
    // Demand moved the public price, and the event says so.
    expect(listingOf(t, ticker).spotPrice).toBeGreaterThan(quote.spotPrice);
    expect(t.events("MARKET_PRICE_UPDATED").at(-1)!.payload).toMatchObject({ ticker, reason: "TRADE", spotPrice: listingOf(t, ticker).spotPrice });

    // Replayed command id: not executed again.
    const replay = t.engine.dispatch(a.id, { type: "market.buy", ticker, qty: 1, commandId: "buy-cmd-000001" });
    expect(replay.duplicate).toBe(true);
    expect(t.events("TRADE_EXECUTED")).toHaveLength(1);
    expect(walletOf(t, a.id).balanceCredits).toBe(RULES.trade.startingWalletHuman - quote.ask);
  });

  it("rejects without partial mutation: cooldown, no shares, no funds, position cap, spectators, unknown ticker", () => {
    const t = makeRoom(); const a = withOutlaw(t); const s = t.join("Judge", "SPECTATOR"); const ticker = t.char(a.id).ticker!;
    const snap = () => JSON.stringify(t.engine.state.wallets);

    let before = snap();
    expect(t.cmd(a.id, { type: "market.sell", ticker, qty: 1 }).error?.reason).toBe("INSUFFICIENT_SHARES");
    expect(snap()).toBe(before);
    expect(t.cmd(a.id, { type: "market.buy", ticker: "NOPE-99", qty: 1 }).error?.reason).toBe("UNKNOWN_TICKER");
    expect(t.cmd(s.id, { type: "market.buy", ticker, qty: 1 }).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    expect(snap()).toBe(before);

    expect(t.cmd(a.id, { type: "market.buy", ticker, qty: 1 }).ok).toBe(true);
    before = snap();
    expect(t.cmd(a.id, { type: "market.buy", ticker, qty: 1 }).error?.reason).toBe("COOLDOWN"); // 2 s per wallet+ticker
    expect(snap()).toBe(before);

    for (let i = 1; i < RULES.trade.maxSharesPerTicker; i++) { t.run(RULES.trade.cooldownMs); expect(t.cmd(a.id, { type: "market.buy", ticker, qty: 1 }).ok, `buy ${i + 1}`).toBe(true); }
    t.run(RULES.trade.cooldownMs);
    before = snap();
    expect(t.cmd(a.id, { type: "market.buy", ticker, qty: 1 }).error?.reason).toBe("POSITION_LIMIT");
    expect(snap()).toBe(before);

    // Selling fills at the bid (house spread), never above the quote.
    t.run(RULES.trade.cooldownMs);
    const q = t.engine.deps.economy.quote(t.engine.state, ticker, t.clock.now())!;
    const bal = walletOf(t, a.id).balanceCredits;
    expect(t.cmd(a.id, { type: "market.sell", ticker, qty: 1 }).ok).toBe(true);
    // Anti-echo fill: after ten buys of its own, the wallet sells at the bid the market would show without its own
    // orders, which is below the public bid those buys inflated. Never above the quote.
    const got = walletOf(t, a.id).balanceCredits - bal;
    expect(got).toBeGreaterThan(0);
    expect(got).toBeLessThan(q.bid);
    expect(q.bid).toBeLessThan(q.ask);

    // Not enough credits.
    t.run(RULES.trade.cooldownMs);
    walletOf(t, a.id).balanceCredits = 3;
    before = snap();
    expect(t.cmd(a.id, { type: "market.buy", ticker, qty: 1 }).error?.reason).toBe("INSUFFICIENT_FUNDS");
    expect(snap()).toBe(before);
    expect(walletOf(t, a.id).balanceCredits).toBeGreaterThanOrEqual(0);
    expect(t.events("TRADE_REJECTED").map((e) => e.payload.reason)).toEqual(expect.arrayContaining(["INSUFFICIENT_SHARES", "COOLDOWN", "POSITION_LIMIT", "INSUFFICIENT_FUNDS"]));
  });

  it("FR-036: an NPC with an empty wallet cannot buy", () => {
    const t = makeRoom(); const a = withOutlaw(t); const ticker = t.char(a.id).ticker!;
    t.engine.state.wallets["wal_npc_t2"]!.balanceCredits = 0;
    const r = t.engine.placeNpcOrder("npc_t2", ticker, "BUY", {});
    expect(r.ok).toBe(false);
    expect(r.ok ? null : r.reason).toBe("INSUFFICIENT_FUNDS");
    expect(t.events("TRADE_REJECTED").at(-1)!.payload).toMatchObject({ actorId: "npc_t2", actorKind: "NPC", reason: "INSUFFICIENT_FUNDS" });
    expect(t.events("TRADE_EXECUTED")).toHaveLength(0);
  });
});

describe("AT-05: capture crash is atomic", () => {
  it("one capture wins, the ticker is exactly V$0, later orders and missions fail, the bounty is paid once", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    const h1 = t.join("Hunter1", "HUNTER"); const h2 = t.join("Hunter2", "HUNTER");
    const target = t.char(a.id); const ticker = target.ticker!;
    expect(t.cmd(h2.id, { type: "market.buy", ticker, qty: 1 }).ok).toBe(true); // a position that will be marked to zero
    t.place(h1.id, target.x + 1, target.z);

    // Not wanted yet: capture is refused even at point-blank range.
    expect(t.cmd(h1.id, { type: "capture.attempt", targetCharacterId: target.id }).error?.reason).toBe("TARGET_NOT_WANTED");
    makeWanted(t, a.id);
    expect(t.events("WANTED_STATUS_CHANGED").at(-1)!.payload).toMatchObject({ characterId: target.id, wanted: true });
    expect(t.events("BULLETIN_PUBLISHED")).toHaveLength(1);

    // Remote capture is refused.
    t.place(h1.id, target.x + 30, target.z);
    t.run(RULES.captureCooldownMs);
    expect(t.cmd(h1.id, { type: "capture.attempt", targetCharacterId: target.id }).error?.reason).toBe("OUT_OF_RANGE");
    // An outlaw cannot capture at all.
    expect(t.cmd(a.id, { type: "capture.attempt", targetCharacterId: target.id }).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");

    // Two eligible hunters, both in range, both attempt.
    t.place(h1.id, target.x + 1, target.z); t.place(h2.id, target.x - 1, target.z);
    t.run(RULES.captureCooldownMs);
    const bounty = t.engine.deps.economy.bountyFor(t.engine.state, target.id, t.clock.now());
    const spotBefore = listingOf(t, ticker).spotPrice;
    expect(spotBefore).toBeGreaterThan(0);
    const r1 = t.engine.dispatch(h1.id, { type: "capture.attempt", targetCharacterId: target.id, commandId: "capture-h1-0001" });
    const r2 = t.engine.dispatch(h2.id, { type: "capture.attempt", targetCharacterId: target.id, commandId: "capture-h2-0001" });
    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(false); expect(r2.error?.reason).toBe("TARGET_ALREADY_CAPTURED");
    expect(t.events("CAPTURE_SUCCEEDED")).toHaveLength(1);
    expect(t.events("LISTING_HALTED")).toHaveLength(1);

    const l = listingOf(t, ticker);
    expect(l.status).toBe("HALTED_CAPTURED");
    expect(l.spotPrice).toBe(0);
    expect(l.finalSpotBeforeHalt).toBe(spotBefore);
    expect(t.char(a.id).status).toBe("CAPTURED");
    expect(t.events("MARKET_PRICE_UPDATED").at(-1)!.payload).toMatchObject({ ticker, spotPrice: 0, reason: "CAPTURE", previousSpotPrice: spotBefore });
    const cap = t.events("CAPTURE_SUCCEEDED")[0]!.payload;
    expect(cap).toMatchObject({ characterId: target.id, captorId: h1.id, captorKind: "HUMAN", bounty, spotBeforeCapture: spotBefore });

    // Bounty: exactly once, to the winner. It tracks reference value, so a later capture pays more.
    expect(bounty).toBe(Math.max(RULES.bounty.min, Math.round(cap.referenceAtCapture * RULES.bounty.fractionOfReference)));
    expect(walletOf(t, h1.id).balanceCredits).toBe(RULES.trade.startingWalletHuman + bounty);
    const h2Balance = walletOf(t, h2.id).balanceCredits;
    const replay = t.engine.dispatch(h1.id, { type: "capture.attempt", targetCharacterId: target.id, commandId: "capture-h1-0001" });
    expect(replay.duplicate).toBe(true);
    expect(walletOf(t, h1.id).balanceCredits).toBe(RULES.trade.startingWalletHuman + bounty);
    expect(walletOf(t, h2.id).balanceCredits).toBe(h2Balance);
    expect(t.events("CAPTURE_SUCCEEDED")).toHaveLength(1);

    // Holdings are marked to zero but not erased.
    const view = t.engine.walletView(`wal_${h2.id}`)!;
    expect(view.holdings.find((x) => x.ticker === ticker)).toMatchObject({ shares: 1, marketValue: 0 });

    // No further trades or missions on that character.
    t.run(RULES.trade.cooldownMs);
    for (const side of ["market.buy", "market.sell"]) expect(t.cmd(h2.id, { type: side, ticker, qty: 1 }).error?.reason).toBe("LISTING_HALTED");
    t.run(RULES.missionRespawnMs); // the next objective appears; the captured character still cannot take it
    const m = t.mission(); t.place(a.id, m.x, m.z);
    expect(t.cmd(a.id, { type: "mission.interact", missionId: m.id }).error?.code).toBe("CHARACTER_INACTIVE");
    expect(t.mission().id).toBe(m.id);
    expect(listingOf(t, ticker).spotPrice).toBe(0);
    // Time passing never reopens or reprices a halted listing.
    t.run(15000);
    expect(listingOf(t, ticker)).toMatchObject({ status: "HALTED_CAPTURED", spotPrice: 0 });
  });

  it("the later the capture, the bigger the bounty", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    const early = t.engine.deps.economy.bountyFor(t.engine.state, t.char(a.id).id, t.clock.now());
    t.run(60000);
    const late = t.engine.deps.economy.bountyFor(t.engine.state, t.char(a.id).id, t.clock.now());
    expect(late).toBeGreaterThan(early);
  });

  it("FR-025: a model answer that arrives after the capture is rejected, not traded", () => {
    const t = makeRoom(); const a = withOutlaw(t); const h = t.join("Hunter1", "HUNTER");
    const target = t.char(a.id);
    makeWanted(t, a.id);
    expect(pendingFor(t, "npc_t1"), "trader is thinking about the bulletin").toBeTruthy();
    t.place(h.id, target.x + 1, target.z);
    expect(t.cmd(h.id, { type: "capture.attempt", targetCharacterId: target.id }).ok).toBe(true);
    const trades = t.events("TRADE_EXECUTED").length;
    t.scheduler.resolve("npc_t1", "buy"); // the slow answer lands now
    expect(t.events("TRADE_EXECUTED")).toHaveLength(trades);
    const resolved = t.events("DECISION_RESOLVED").filter((e) => e.payload.agentId === "npc_t1").at(-1)!;
    expect(resolved.payload.status).toBe("REJECTED");
    expect(resolved.payload.rejectReason).toMatch(/captured/);
    expect(t.events("AGENT_ACTION_REJECTED").at(-1)!.payload.agentId).toBe("npc_t1");
  });
});

describe("AT-06: a new life, an old history", () => {
  it("respawn gives a new character and ticker; the old listing stays halted with its history", () => {
    const t = makeRoom(); const a = withOutlaw(t); const h = t.join("Hunter1", "HUNTER");
    const old = t.char(a.id); const oldTicker = old.ticker!;
    expect(t.cmd(a.id, { type: "character.respawn" }).error?.code).toBe("NOT_CAPTURED");
    expect(t.cmd(a.id, { type: "market.buy", ticker: oldTicker, qty: 1 }).ok).toBe(true);
    makeWanted(t, a.id);
    t.place(h.id, old.x + 1, old.z);
    expect(t.cmd(h.id, { type: "capture.attempt", targetCharacterId: old.id }).ok).toBe(true);

    const r = t.cmd(a.id, { type: "character.respawn" });
    expect(r.ok).toBe(true);
    const fresh = t.char(a.id);
    expect(fresh.id).not.toBe(old.id);
    expect(fresh.ticker).not.toBe(oldTicker);
    expect(fresh).toMatchObject({ status: "ACTIVE", heat: 0, notorietyPoints: 0, completedMissions: 0, playerId: a.id });
    const nl = listingOf(t, fresh.ticker!);
    expect(nl.status).toBe("ACTIVE");
    expect(nl.referenceValue).toBe(RULES.price.base);
    expect(nl.spotPrice).toBe(Math.round(RULES.price.base * RULES.price.survivalFactor.base));

    // The old life is still on the record.
    const ol = listingOf(t, oldTicker);
    expect(ol).toMatchObject({ status: "HALTED_CAPTURED", spotPrice: 0, characterId: old.id });
    expect(ol.history.length).toBeGreaterThan(2);
    expect(t.engine.state.characters[old.id]!.status).toBe("ARCHIVED");
    expect(t.engine.state.trades.some((x) => x.ticker === oldTicker)).toBe(true);
    expect(t.events("CHARACTER_ARCHIVED")[0]!.payload).toMatchObject({ characterId: old.id, ticker: oldTicker });
    expect(t.events("CHARACTER_RESPAWNED")[0]!.payload).toMatchObject({ playerId: a.id, previousCharacterId: old.id, characterId: fresh.id, ticker: fresh.ticker });
    const snap = t.engine.snapshot();
    expect(snap.listings.map((x) => x.ticker).sort()).toEqual([oldTicker, fresh.ticker].sort());
    // Same wallet, and the dead ticker still cannot trade.
    expect(t.engine.state.players[a.id]!.walletId).toBe(`wal_${a.id}`);
    t.run(RULES.trade.cooldownMs);
    expect(t.cmd(a.id, { type: "market.sell", ticker: oldTicker, qty: 1 }).error?.reason).toBe("LISTING_HALTED");
    expect(t.cmd(a.id, { type: "character.respawn" }).error?.code).toBe("NOT_CAPTURED");
  });
});

describe("AT-07: provider failure is transparent", () => {
  it("a fallback decision is labeled fallback everywhere and carries no invented confidence", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    expect(t.doMission(a.id).ok).toBe(true);
    t.scheduler.resolve("npc_w1", "ignore", "fallback", "rate_limit");
    const resolved = t.events("DECISION_RESOLVED").filter((e) => e.payload.agentId === "npc_w1").at(-1)!.payload;
    expect(resolved).toMatchObject({ decisionSource: "fallback", provider: null, confidence: null, probabilities: null, providerRequestId: null, errorType: "rate_limit", status: "FALLBACK_APPLIED", chosenAction: "ignore" });
    expect(t.events("AGENT_ACTION_APPLIED").at(-1)!.payload).toMatchObject({ agentId: "npc_w1", decisionSource: "fallback", action: "ignore" });
    const view = t.engine.agentView("npc_w1");
    expect(view.lastDecision).toMatchObject({ decisionSource: "fallback", confidence: null, probabilities: null, errorType: "rate_limit" });
    expect(t.events("DECISION_RESOLVED").some((e) => e.payload.agentId === "npc_w1" && e.payload.decisionSource === "drex")).toBe(false);
    // The safe action had no effect on the world, and the room keeps running.
    expect(t.events("AGENT_MESSAGE_SENT")).toHaveLength(0);
    t.run(3000);
    expect(t.cmd(a.id, { type: "market.buy", ticker: t.char(a.id).ticker!, qty: 1 }).ok).toBe(true);
  });

  it("with the real scheduler: a 429 and a hung provider both end as labeled fallbacks while the world keeps ticking", async () => {
    const clock = new FakeClock();
    let mode: "rate_limit" | "hang" = "rate_limit"; let calls = 0;
    const drex: DecisionProvider = {
      name: "drex", model: "drex-test", isConfigured: () => true,
      decide: (_call, opts) => {
        calls++;
        if (mode === "rate_limit") return Promise.reject(new ProviderError("rate_limit", "429 from provider", { status: 429, retryable: true }));
        return new Promise((_res, rej) => opts.signal?.addEventListener("abort", () => rej(new ProviderError("aborted", "aborted"))));
      },
    };
    const meta: DecisionProvider = { name: "meta", model: "meta-test", isConfigured: () => false, decide: () => Promise.reject(new ProviderError("unconfigured", "no key")) };
    const scheduler = createScheduler({ drex, meta, clock, mode: "drex", concurrency: 2, maxCallsPerMinute: 60, deadlineMs: { drex: 40 } });
    const build = createRoomFactory({ persistence: createMemoryPersistence(), clock, providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 } });
    const { engine } = build({ roomId: "AT07", code: "AT07", seed: 42, mode: "drex", scheduler, clock });
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const resolved = () => engine.events.filter((e): e is GameEvent<"DECISION_RESOLVED"> => e.type === "DECISION_RESOLVED");

    const p = engine.addPlayer({ nickname: "Ace", role: "OUTLAW", sessionToken: "tok" }); // traders are asked about the new listing
    await wait(30);
    expect(calls).toBeGreaterThan(0);
    expect(resolved().length).toBeGreaterThanOrEqual(2);
    for (const e of resolved()) {
      expect(e.payload).toMatchObject({ decisionSource: "fallback", errorType: "rate_limit", chosenAction: "hold", confidence: null, probabilities: null });
      expect(e.payload.status).toBe("FALLBACK_APPLIED");
    }
    expect(engine.events.some((e) => e.type === "TRADE_EXECUTED")).toBe(false);

    // Now the provider hangs. The witness's decision is pending while the simulation and the market keep moving.
    mode = "hang";
    const before = resolved().length;
    clock.advance(5000); engine.tick();
    const c = engine.state.characters[p.characterId!]!; const m = Object.values(engine.state.missions)[0]!;
    c.x = m.x; c.z = m.z;
    expect(engine.dispatch(p.id, { type: "mission.interact", missionId: m.id, commandId: "mission-at07-1" }).ok).toBe(true);
    expect(engine.agentView("npc_w1").pendingDecisionId).not.toBeNull();
    for (let i = 0; i < 5; i++) { clock.advance(100); engine.tick(); }
    expect(engine.dispatch(p.id, { type: "market.buy", ticker: c.ticker!, qty: 1, commandId: "buy-at07-0001" }).ok).toBe(true);
    await wait(90); // past the 40 ms decision deadline
    const late = resolved().slice(before).find((e) => e.payload.agentId === "npc_w1")!;
    expect(late.payload).toMatchObject({ decisionSource: "fallback", errorType: "timeout", chosenAction: "ignore", confidence: null });
    expect(engine.agentView("npc_w1").pendingDecisionId).toBeNull();
    expect(engine.events.some((e) => e.type === "AGENT_MESSAGE_SENT")).toBe(false);
    scheduler.cancelAll();
  });
});

describe("FR-003: movement is server-integrated", () => {
  it("normalises diagonal intents, stops at buildings and bounds, and rejects teleports", () => {
    const t = makeRoom(); const a = t.join("Ace", "OUTLAW");
    const c = t.char(a.id); const start = { x: c.x, z: c.z };
    // The wire schema refuses out-of-range intents outright.
    expect(clientCommandSchema.safeParse({ type: "player.move", dirX: 100, dirZ: 0 }).success).toBe(false);
    expect(clientCommandSchema.safeParse({ type: "market.buy", ticker: "ACE-01", qty: 5, commandId: "abcdefgh" }).success).toBe(false);
    expect(clientCommandSchema.safeParse({ type: "market.buy", ticker: "ACE-01", qty: 1 }).success).toBe(false); // idempotency key required

    t.cmd(a.id, { type: "player.move", dirX: -1, dirZ: -1 });
    t.run(1000);
    const moved = Math.hypot(c.x - start.x, c.z - start.z);
    expect(moved).toBeGreaterThan(RULES.playerSpeed * 0.8);
    expect(moved).toBeLessThanOrEqual(RULES.playerSpeed * 1.0001); // diagonal is not faster

    // Walk north into the hotel block's neighbour: the avatar never ends up inside a building or outside the map.
    t.cmd(a.id, { type: "player.move", dirX: 1, dirZ: 0 });
    t.run(20000);
    const inside = t.engine.snapshot().map.buildings.some((b) => c.x > b.x0 && c.x < b.x1 && c.z > b.z0 && c.z < b.z1);
    expect(inside).toBe(false);
    expect(c.x).toBeLessThanOrEqual(119);
    t.cmd(a.id, { type: "player.move", dirX: 0, dirZ: 0 });

    // Pose clients: a jump that would need more than the speed ceiling is refused and nothing moves.
    const at = { x: c.x, z: c.z };
    t.run(100);
    const tp = t.cmd(a.id, { type: "player.pose", x: at.x - 60, z: at.z });
    expect(tp.ok).toBe(false); expect(tp.error?.code).toBe("MOVE_REJECTED");
    expect(c.x).toBe(at.x);
    expect(t.cmd(a.id, { type: "player.pose", x: 500, z: 500 }).error?.code).toBe("MOVE_REJECTED");
    // A browser client cannot report itself inside a building; an external 3D world (bridge) client can, its geometry is its own.
    const b = t.join("Bo", "HUNTER"); const g = t.engine.addPlayer({ nickname: "Godot", role: "HUNTER", sessionToken: "tok_g", socketId: "bridge:1" });
    t.place(b.id, 17, 6); t.place(g.id, 17, 6); t.run(1000); // just north of Precinct 9 (x 8..26, z 8..22)
    const into = t.cmd(b.id, { type: "player.pose", x: 17, z: 15 });
    expect(into.error).toMatchObject({ code: "MOVE_REJECTED", message: "Inside a building" });
    expect(t.char(b.id)).toMatchObject({ x: 17, z: 6 });
    expect(t.cmd(g.id, { type: "player.pose", x: 17, z: 15 }).ok).toBe(true);
    t.run(100);
    expect(t.cmd(a.id, { type: "player.pose", x: at.x - 2, z: at.z }).ok).toBe(true);
    expect(c.x).toBe(at.x - 2);
  });
});

describe("FR-060: seeded reset", () => {
  it("resets to the same initial positions, tickers and wallets, twice", () => {
    const t = makeRoom(); t.scheduler.auto = (req) => ({ action: req.scriptedAction, source: "scripted" });
    const a = t.join("Ace", "OUTLAW", true); const h = t.join("Bo", "HUNTER");
    const picture = () => {
      const s = t.engine.snapshot();
      return JSON.stringify({
        characters: s.characters.map((c) => [c.id, c.playerId, c.nickname, c.role, c.ticker, c.status, c.x, c.z, c.heat, c.notorietyPoints]),
        npcs: s.npcs.map((n) => [n.id, n.role, n.x, n.z, n.activity, n.walletCredits, n.holdings]),
        missions: s.missions.map((m) => [m.x, m.z, m.label, m.status]),
        listings: s.listings.map((l) => [l.ticker, l.status, l.referenceValue, l.spotPrice]),
        wallets: Object.values(t.engine.state.wallets).map((w) => [w.id, w.ownerId, w.balanceCredits, w.holdings]),
        mode: s.mode, seed: s.seed,
      });
    };
    const initial = picture(); const run1 = t.engine.state.runId; const seq1 = t.engine.state.lastEventSequence;

    // Make a mess: mission, trades, time, decisions.
    expect(t.doMission(a.id).ok).toBe(true);
    t.cmd(a.id, { type: "market.buy", ticker: t.char(a.id).ticker!, qty: 1 });
    t.run(12000);
    expect(picture()).not.toBe(initial);

    expect(t.cmd(a.id, { type: "demo.reset" }).ok).toBe(true);
    expect(picture()).toBe(initial);
    expect(t.engine.state.runId).not.toBe(run1);
    expect(t.engine.state.lastEventSequence).toBeGreaterThan(seq1); // sequence stays monotonic per room
    expect(t.engine.events[0]!.type).toBe("ROOM_RESET");
    expect(Object.keys(t.engine.state.players).sort()).toEqual([a.id, h.id].sort()); // player ids survive
    expect(t.persistence.events.some((e) => e.runId === run1 && e.type === "MISSION_COMPLETED")).toBe(true); // history kept in the store
    expect(t.engine.brains.traces()).toHaveLength(t.events("DECISION_REQUESTED").length);

    t.doMission(a.id); t.run(5000);
    expect(t.cmd(a.id, { type: "demo.reset" }).ok).toBe(true);
    expect(picture()).toBe(initial);
  });
});

describe("AT-08 / reproducibility: scripted bot scenario", () => {
  function scenario() {
    const t = makeRoom("scripted");
    t.scheduler.auto = (req) => ({ action: req.scriptedAction, source: "scripted" });
    const bot = new BotOutlaw(t.engine);
    for (let i = 0; i < 900; i++) { bot.step(); t.clock.advance(100); t.engine.tick(); t.scheduler.flush(); }
    return t;
  }
  const shape = (t: Room) => t.engine.events.map((e) => `${e.serverSequence}|${e.simTimeMs}|${e.type}|${e.actorId}|${e.causationId}|${JSON.stringify(e.payload)}`);

  it("the same seed, bot path and scripted decisions give the identical event log", () => {
    const one = scenario(); const two = scenario();
    expect(one.events("MISSION_COMPLETED")).toHaveLength(1);
    expect(one.events("AGENT_MESSAGE_SENT").some((e) => e.payload.kind === "REPORT")).toBe(true);
    expect(one.events("BULLETIN_PUBLISHED").length).toBeGreaterThanOrEqual(1);
    expect(shape(one)).toEqual(shape(two));
  });

  it("folding the recorded log, with no model calls, reproduces wallets, listings and characters", () => {
    const t = scenario();
    const v = verifyReplay(t.persistence.events, t.engine.state);
    expect(v.mismatches).toEqual([]);
    expect(v.ok).toBe(true);
    // Scripted police pursue what they can see, so this run ends in a capture and a zeroed ticker.
    expect(t.events("CAPTURE_SUCCEEDED")).toHaveLength(1);
    expect(Object.values(t.engine.state.listings)[0]).toMatchObject({ status: "HALTED_CAPTURED", spotPrice: 0 });
  });
});

describe("police response pacing (RULES.policeDispatchDelayMs, policeSearchPaceFactor)", () => {
  const officer = (t: Room) => t.engine.state.npcs["npc_p1"]!; // nearest officer to the dock witness, so the report goes to her

  it("a second-hand report is investigated after the dispatch delay, on foot at the search pace, and heat does not cool meanwhile", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    makeWanted(t, a.id);
    const heat = t.char(a.id).heat;
    expect(pendingFor(t, "npc_p1"), "the sergeant received the report").toBeTruthy();
    expect(Object.keys(pendingFor(t, "npc_p1")!.req.options).sort()).toEqual(["investigate", "patrol"]); // no sighting of her own, so no pursue
    t.scheduler.resolve("npc_p1", "investigate");
    expect(t.events("AGENT_ACTION_APPLIED").at(-1)!.payload.detail).toContain(`dispatch in ${RULES.policeDispatchDelayMs / 1000} s`);
    const from = { x: officer(t).x, z: officer(t).z };

    t.run(RULES.policeDispatchDelayMs - 500);
    expect(officer(t).activity).toBe("INVESTIGATE");
    expect({ x: officer(t).x, z: officer(t).z }).toEqual(from); // still radioing in

    t.run(2500); // two seconds after setting off
    const moved = Math.hypot(officer(t).x - from.x, officer(t).z - from.z);
    const pace = RULES.npcWalkSpeed * RULES.policeSearchPaceFactor;
    expect(moved).toBeGreaterThan(pace * 1.5);
    expect(moved).toBeLessThan(pace * 2.5);

    // 20 s after the bulletin the suspect has stood still and nobody has seen them, yet they are still wanted:
    // an officer is on the case, so the heat has not started to cool.
    t.run(8000);
    expect(t.events("NPC_OBSERVED").filter((e) => e.payload.factType === "SIGHTING")).toHaveLength(0);
    expect(t.char(a.id)).toMatchObject({ status: "WANTED", heat });
    expect(t.events("ESCAPE_SUCCEEDED")).toHaveLength(0);
  });

  it("an officer who has just seen the suspect is not delayed", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    makeWanted(t, a.id);
    t.place(a.id, officer(t).x + 10, officer(t).z); // on the Strip, in plain view of the sergeant
    t.run(100);
    expect(t.events("NPC_OBSERVED").some((e) => e.payload.npcId === "npc_p1" && e.payload.factType === "SIGHTING")).toBe(true);
    const from = { x: officer(t).x, z: officer(t).z };
    t.scheduler.resolve("npc_p1", "investigate");
    expect(t.events("AGENT_ACTION_APPLIED").at(-1)!.payload.detail).not.toContain("dispatch in");
    t.run(1000);
    const moved = Math.hypot(officer(t).x - from.x, officer(t).z - from.z);
    expect(moved).toBeGreaterThan(RULES.npcWalkSpeed * 0.8); // left at once, at walking pace
  });

  it("with no officer on the case, heat cools and the suspect escapes", () => {
    const t = makeRoom(); const a = withOutlaw(t);
    makeWanted(t, a.id);
    t.scheduler.resolve("npc_p1", "patrol"); t.scheduler.resolve("npc_p2", "patrol");
    t.place(a.id, 110, 70); // far corner, out of every patrol's sight
    t.run(RULES.heat.decayGraceMs + 15000);
    expect(t.events("ESCAPE_SUCCEEDED")).toHaveLength(1);
    expect(t.char(a.id)).toMatchObject({ status: "ACTIVE", successfulEscapes: 1 });
    expect(t.events("WANTED_STATUS_CHANGED").at(-1)!.payload.wanted).toBe(false);
  });
});
