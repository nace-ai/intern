// The swarm reacts to crews: traders hear of a crew as market news, informants may sell to either side and switch.
import { describe, expect, it } from "vitest";
import { RULES } from "@vice/contracts";
import { makeRoom } from "./helpers";

function town() {
  const r = makeRoom("drex");
  const out = r.join("Ace", "OUTLAW"), hun = r.join("Rio", "HUNTER");
  for (const n of Object.values(r.engine.state.npcs)) if (n.role === "POLICE") { n.x = 4; n.z = 4; n.patrol = []; n.activity = "IDLE"; n.home = { x: 4, z: 4 }; }
  // Answer the opening listing decisions so every NPC is idle and off cooldown.
  const settle = () => { while (r.scheduler.queue.length) { const q = r.scheduler.queue[0]!; r.scheduler.resolve(q.req.agentId, q.req.safeAction, "drex"); } r.run(RULES.decisions.npcCooldownMs + 500); };
  settle(); settle();
  const wanted = () => { const c = r.char(out.id); c.heat = 60; c.status = "WANTED"; };
  const reqOf = (agentId: string) => r.scheduler.queue.find((q) => q.req.agentId === agentId)?.req;
  return { r, out, hun, settle, wanted, reqOf };
}

describe("the swarm reacts to hunter crews", () => {
  it("a crew forming while an outlaw is wanted reaches each trader as market news about that ticker, and nothing it was not told", () => {
    const { r, out, hun, wanted, reqOf } = town();
    r.doMission(out.id); // seen (if at all) near the dock; the traders are far away and are told nothing
    wanted();
    const ticker = r.char(out.id).ticker!;
    expect(r.cmd(hun.id, { type: "crew.join", crewName: "WOLVES" }).ok).toBe(true);
    for (const id of ["npc_t1", "npc_t2"]) {
      const req = reqOf(id)!;
      expect(req, `${id} should be deciding`).toBeTruthy();
      const state = JSON.stringify(req.state);
      expect(state).toContain("Hunter crew WOLVES");
      expect(state).toContain(ticker);
      expect((req.state as { what_just_happened: { source: string } }).what_just_happened.source).toBe("public bulletin");
      expect(Object.keys(req.options)).toContain("hold");
      // Private facts it never received: the job at the dock.
      const jobText = r.events("INCIDENT_OCCURRED")[0]!.payload.description;
      expect(state).not.toContain(jobText);
    }
  });

  it("a crew that already exists when an outlaw becomes wanted is also market news for the traders", () => {
    const { r, out, hun, settle, reqOf } = town();
    expect(r.cmd(hun.id, { type: "crew.join", crewName: "WOLVES" }).ok).toBe(true);
    settle();
    expect(reqOf("npc_t1")).toBeUndefined(); // nobody is wanted yet: no crew note
    const c = r.char(out.id);
    (r.engine as unknown as { addHeat(c: unknown, n: number, why: string, o: object): void }).addHeat(c, 60, "test heat", {});
    expect(c.status).toBe("WANTED");
    for (const id of ["npc_t1", "npc_t2"]) {
      const req = reqOf(id)!;
      expect(req, `${id} should be deciding`).toBeTruthy();
      expect(JSON.stringify(req.state)).toContain("Hunter crew WOLVES");
      expect(JSON.stringify(req.state)).toContain(c.ticker!);
    }
  });

  it("crew forms -> trader decides -> the order goes through the normal settlement path", () => {
    const { r, out, hun, wanted } = town();
    wanted();
    const ticker = r.char(out.id).ticker!;
    r.cmd(hun.id, { type: "crew.join", crewName: "WOLVES" });
    const before = r.events("TRADE_EXECUTED").length;
    expect(r.scheduler.resolve("npc_t1", "buy", "drex")).toBe(true);
    const trades = r.events("TRADE_EXECUTED").slice(before);
    expect(trades).toHaveLength(1);
    expect(trades[0]!.payload).toMatchObject({ actorId: "npc_t1", actorKind: "NPC", ticker, side: "BUY", qty: 1 });
    const applied = r.events("AGENT_ACTION_APPLIED").at(-1)!.payload;
    expect(applied).toMatchObject({ agentId: "npc_t1", action: "buy", decisionSource: "drex" });
    expect(r.engine.state.wallets["wal_npc_t1"]!.holdings[ticker]!.shares).toBe(1);
  });

  it("an informant is offered side deals only when a crew exists and the subject is wanted; it can sell to the crew, then switch sides", () => {
    const { r, out, hun, settle, wanted, reqOf } = town();
    const lena = r.engine.state.npcs["npc_i1"]!;
    r.place(out.id, lena.x + 3, lena.z); r.place(hun.id, lena.x + 9, lena.z);
    const fire = () => expect(r.cmd(out.id, { type: "weapon.fire", targetId: r.char(hun.id).id }).ok).toBe(true);

    // No crew yet: only relay / withhold.
    wanted(); fire();
    expect(Object.keys(reqOf("npc_i1")!.options).sort()).toEqual(["relay", "withhold"]);
    settle();

    // Crew exists and the outlaw is wanted: both side deals are offered, with the informant's own ledger in its state.
    r.cmd(hun.id, { type: "crew.join", crewName: "WOLVES" });
    settle(); wanted(); fire();
    const req = reqOf("npc_i1")!;
    expect(Object.keys(req.options).sort()).toEqual(["relay", "sell_to_crew", "warn_outlaw", "withhold"]);
    expect(req.state).toMatchObject({ my_earnings_from_deals: 0, my_last_deal: "none yet", hunter_crews_in_town: [{ name: "WOLVES", hunters: 1 }] });
    expect(req.scriptedAction).toBe("relay"); expect(req.safeAction).toBe("withhold");
    r.scheduler.resolve("npc_i1", "sell_to_crew", "drex");
    const tip = r.events("CREW_TIP")[0]!.payload;
    expect(tip).toMatchObject({ crewName: "WOLVES", informantId: "npc_i1", characterId: r.char(out.id).id, fee: RULES.crew.tipFee, earnings: RULES.crew.tipFee, memberPlayerIds: [hun.id] });
    expect(r.events("INFORMANT_SWITCHED_SIDES")).toHaveLength(0);
    // Privacy: the other informant was told nothing about the deal.
    expect(JSON.stringify(r.engine.agentView("npc_i2").memories)).not.toContain("sold");
    settle();

    // Next time it sells a warning to the outlaw instead: a side switch, announced.
    wanted(); fire();
    const again = reqOf("npc_i1")!;
    expect(again.state).toMatchObject({ my_earnings_from_deals: RULES.crew.tipFee, my_last_deal: "sold a location to the hunter crew" });
    r.scheduler.resolve("npc_i1", "warn_outlaw", "drex");
    expect(r.events("OUTLAW_WARNED")[0]!.payload).toMatchObject({ informantId: "npc_i1", crewNames: ["WOLVES"], earnings: 2 * RULES.crew.tipFee });
    expect(r.events("INFORMANT_SWITCHED_SIDES")[0]!.payload).toMatchObject({ informantId: "npc_i1", from: "crew", to: "outlaw" });
  });

  it("a side deal decided before the crew disbanded is rejected as stale, with no effect", () => {
    const { r, out, hun, settle, wanted, reqOf } = town();
    const lena = r.engine.state.npcs["npc_i1"]!;
    r.place(out.id, lena.x + 3, lena.z); r.place(hun.id, lena.x + 9, lena.z);
    r.cmd(hun.id, { type: "crew.join", crewName: "WOLVES" });
    settle(); wanted();
    r.cmd(out.id, { type: "weapon.fire", targetId: r.char(hun.id).id });
    expect(reqOf("npc_i1")!.options).toHaveProperty("sell_to_crew");
    r.cmd(hun.id, { type: "crew.leave" });
    r.scheduler.resolve("npc_i1", "sell_to_crew", "drex");
    expect(r.events("CREW_TIP")).toHaveLength(0);
    expect(r.events("AGENT_ACTION_REJECTED").at(-1)!.payload).toMatchObject({ agentId: "npc_i1", action: "sell_to_crew" });
  });
});
