// Every agent is out for its own profit: all NPCs have wallets; officers earn bounties, witnesses earn tips,
// informants earn fees. Each credit is an event, so the log replays to the same balances.
import { describe, expect, it } from "vitest";
import { RULES } from "@vice/contracts";
import { verifyReplay } from "../src/replay";
import { makeRoom } from "./helpers";

const T = { x: 60, z: 54 }; // open road

function setup() {
  const r = makeRoom("scripted");
  const out = r.join("Ace", "OUTLAW");
  r.place(out.id, T.x, T.z);
  const s = r.engine.state;
  const npc = (role: string) => Object.values(s.npcs).filter((n) => n.role === role);
  const bal = (npcId: string) => s.wallets[s.npcs[npcId]!.walletId!]!.balanceCredits;
  return { r, out, s, npc, bal };
}

describe("agent profit", () => {
  it("opens a wallet for every NPC: traders with capital, everyone else at V$0, each with an event", () => {
    const { r, s } = setup();
    const npcs = Object.values(s.npcs);
    expect(npcs.every((n) => n.walletId && s.wallets[n.walletId])).toBe(true);
    for (const n of npcs) expect(s.wallets[n.walletId!]!.balanceCredits).toBe(n.role === "TRADER" ? RULES.trade.startingWalletTrader : 0);
    const opened = r.events("WALLET_OPENED").filter((e) => e.payload.ownerKind === "NPC").map((e) => e.payload.ownerId).sort();
    expect(opened).toEqual(npcs.map((n) => n.id).sort());
    expect(r.engine.agentView(npcs.find((n) => n.role === "POLICE")!.id).walletCredits).toBe(0);
  });

  it("only traders can place orders, even though every agent has a wallet", () => {
    const { r, out, npc } = setup();
    const ticker = r.char(out.id).ticker!;
    const res = r.engine.placeNpcOrder(npc("WITNESS")[0]!.id, ticker, "BUY", {});
    expect(res.ok).toBe(false);
    expect(r.events("TRADE_REJECTED").at(-1)!.payload.reason).toBe("ACTOR_NOT_ALLOWED");
  });

  it("an officer who captures earns the bounty exactly once, and the reporting witness earns a tip exactly once", () => {
    const { r, out, s, npc, bal } = setup();
    const officer = npc("POLICE")[0]!; const witness = npc("WITNESS")[0]!; const other = npc("WITNESS")[1]!;
    const c = r.char(out.id);
    // The witness's report about this character is on record.
    r.engine.sendMessage({ senderId: witness.id, recipientId: officer.id, kind: "REPORT", sourceMemoryId: null, sourceEventId: r.engine.events[0]!.eventId, subjectCharacterId: c.id, summary: "saw it", payload: { x: c.x, z: c.z } }, {});
    c.heat = 60; c.status = "WANTED";
    const expected = r.engine.snapshot().listings.find((l) => l.ticker === c.ticker)!.currentBounty;
    officer.x = c.x + 1; officer.z = c.z; officer.patrol = []; officer.home = { x: officer.x, z: officer.z };
    r.engine.setNpcActivity(officer.id, "PURSUE", { targetCharacterId: c.id });
    r.run(3000);
    expect(r.char(out.id).status).toBe("CAPTURED");
    const credits = r.events("WALLET_CREDITED");
    const forOfficer = credits.filter((e) => e.payload.ownerId === officer.id);
    const forWitness = credits.filter((e) => e.payload.ownerId === witness.id);
    expect(forOfficer).toHaveLength(1);
    expect(forOfficer[0]!.payload.amount).toBeGreaterThanOrEqual(RULES.bounty.min);
    expect(Math.abs(forOfficer[0]!.payload.amount - expected)).toBeLessThanOrEqual(6); // survival ticks between the read and the capture
    expect(bal(officer.id)).toBe(forOfficer[0]!.payload.amount);
    expect(forWitness).toHaveLength(1);
    expect(forWitness[0]!.payload.amount).toBe(Math.max(RULES.profit.witnessTipMin, Math.round(forOfficer[0]!.payload.amount * RULES.profit.witnessTipShare)));
    expect(bal(witness.id)).toBe(forWitness[0]!.payload.amount);
    expect(bal(other.id)).toBe(0);
    // The credits are caused by the capture, and nothing more is paid later.
    const cap = r.events("CAPTURE_SUCCEEDED")[0]!;
    expect(credits.every((e) => e.causationId === cap.eventId)).toBe(true);
    r.run(5000);
    expect(r.events("WALLET_CREDITED")).toHaveLength(2);
    expect(Object.values(s.wallets).every((w) => w.balanceCredits >= 0)).toBe(true);
    const v = verifyReplay(r.engine.events, s);
    expect(v.mismatches).toEqual([]);
    expect(v.ok).toBe(true);
  });

  it("an informant's fee is a real wallet credit and replays", () => {
    const { r, s, npc, bal } = setup();
    const informant = npc("INFORMANT")[0]!;
    expect(r.engine.creditNpc(informant.id, RULES.crew.tipFee, "fee: sold a location to a hunter crew", {})).toBe(RULES.crew.tipFee);
    expect(bal(informant.id)).toBe(RULES.crew.tipFee);
    expect(r.engine.lastEarning(informant.id)).toEqual({ amount: RULES.crew.tipFee, reason: "fee: sold a location to a hunter crew" });
    expect(r.engine.creditNpc(informant.id, 0, "nothing", {})).toBeNull();
    expect(r.engine.creditNpc("npc_nobody", 10, "nothing", {})).toBeNull();
    expect(verifyReplay(r.engine.events, s).ok).toBe(true);
  });

  it("a reset returns every agent wallet to its starting balance, the same way each time", () => {
    const { r, s, npc } = setup();
    const dir = r.join("Boss", "SPECTATOR", true);
    r.engine.creditNpc(npc("POLICE")[0]!.id, 77, "test", {});
    const balances = () => Object.values(r.engine.state.npcs).map((n) => [n.id, r.engine.state.wallets[n.walletId!]!.balanceCredits]);
    expect(r.cmd(dir.id, { type: "demo.reset" }).ok).toBe(true);
    const first = balances();
    r.engine.creditNpc(npc("WITNESS")[0]!.id, 5, "test", {});
    expect(r.cmd(dir.id, { type: "demo.reset" }).ok).toBe(true);
    expect(balances()).toEqual(first);
    expect(first.every(([id, b]) => b === (r.engine.state.npcs[id as string]!.role === "TRADER" ? RULES.trade.startingWalletTrader : 0))).toBe(true);
    void s;
  });
});
