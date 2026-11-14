// Hunter crews: join, shared bounty, double-cross. Decided entirely by the server.
import { describe, expect, it } from "vitest";
import { RULES } from "@vice/contracts";
import { verifyReplay } from "../src/replay";
import { makeRoom } from "./helpers";

// Open road east of the plaza: no building between these points.
const T = { x: 60, z: 54 };

function hunt(hunters: string[] = ["Rio", "Vex"]) {
  const r = makeRoom("scripted");
  const out = r.join("Ace", "OUTLAW");
  const hs = hunters.map((n) => r.join(n, "HUNTER"));
  for (const n of Object.values(r.engine.state.npcs)) if (n.role === "POLICE") { n.x = 4; n.z = 4; n.patrol = []; n.activity = "IDLE"; n.home = { x: 4, z: 4 }; }
  r.place(out.id, T.x, T.z);
  hs.forEach((h, i) => r.place(h.id, T.x + 1 + i * 0.6, T.z));
  const wanted = () => { const c = r.char(out.id); c.heat = 60; c.status = "WANTED"; };
  const bal = (pid: string) => r.engine.state.wallets[r.engine.state.players[pid]!.walletId!]!.balanceCredits;
  const capture = (pid: string) => r.cmd(pid, { type: "capture.attempt", targetCharacterId: r.char(out.id).id });
  return { r, out, hs, wanted, bal, capture };
}
const join = (r: ReturnType<typeof makeRoom>, pid: string, crewName = "WOLVES") => r.cmd(pid, { type: "crew.join", crewName });

describe("hunter crews", () => {
  it("joining creates the crew, a second hunter joins it, views and snapshot show it, leaving removes it", () => {
    const { r, hs } = hunt();
    const a = join(r, hs[0]!.id, "wolves");
    expect(a.ok).toBe(true);
    expect(join(r, hs[1]!.id).ok).toBe(true);
    const snap = r.engine.snapshot();
    expect(snap.crews).toHaveLength(1);
    expect(snap.crews![0]).toMatchObject({ name: "WOLVES", memberPlayerIds: [hs[0]!.id, hs[1]!.id] });
    expect(r.engine.characterView(r.char(hs[1]!.id)).crewName).toBe("WOLVES");
    expect(r.events("CREW_JOINED").map((e) => e.payload.created)).toEqual([true, false]);
    expect(r.cmd(hs[0]!.id, { type: "crew.leave" }).ok).toBe(true);
    expect(r.engine.snapshot().crews![0]!.memberPlayerIds).toEqual([hs[1]!.id]);
    expect(r.cmd(hs[0]!.id, { type: "crew.leave" }).error).toMatchObject({ code: "CREW_REJECTED", reason: "NOT_IN_CREW" });
    r.cmd(hs[1]!.id, { type: "crew.leave" });
    expect(r.engine.snapshot().crews).toHaveLength(0);
  });

  it("caps a crew at four, keeps one crew per player, and refuses outlaws and spectators", () => {
    const { r, out, hs } = hunt(["H1", "H2", "H3", "H4", "H5"]);
    for (const h of hs.slice(0, 4)) expect(join(r, h.id).ok).toBe(true);
    expect(join(r, hs[4]!.id).error).toMatchObject({ code: "CREW_REJECTED", reason: "CREW_FULL" });
    expect(join(r, hs[0]!.id, "FOXES").ok).toBe(true); // switching leaves the first crew
    const crews = r.engine.snapshot().crews!;
    expect(crews.find((k) => k.name === "WOLVES")!.memberPlayerIds).not.toContain(hs[0]!.id);
    expect(crews.find((k) => k.name === "FOXES")!.memberPlayerIds).toEqual([hs[0]!.id]);
    expect(r.events("CREW_LEFT").at(-1)!.payload.reason).toBe("SWITCHED");
    expect(join(r, out.id).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    const spec = r.join("Judge", "SPECTATOR");
    expect(join(r, spec.id).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    expect(r.engine.dispatch(hs[1]!.id, { type: "crew.join", crewName: "WOLVES", commandId: "cmd-dup-0001" }).ok).toBe(true); // already a member: no-op
  });

  it("splits the bounty equally among the captor and crewmates in range, remainder to the captor", () => {
    const { r, hs, wanted, bal, capture } = hunt(["Rio", "Vex", "Kai"]);
    hs.forEach((h) => join(r, h.id));
    wanted();
    const before = hs.map((h) => bal(h.id));
    const ack = capture(hs[0]!.id);
    expect(ack.ok).toBe(true);
    const cap = r.events("CAPTURE_SUCCEEDED")[0]!.payload;
    const each = Math.floor(cap.bounty / 3);
    expect(cap.shares!.map((s) => s.amount)).toEqual([cap.bounty - 2 * each, each, each]);
    expect(cap.shares!.reduce((a, s) => a + s.amount, 0)).toBe(cap.bounty);
    expect(bal(hs[0]!.id) - before[0]!).toBe(cap.bounty - 2 * each);
    expect(bal(hs[1]!.id) - before[1]!).toBe(each);
    expect(bal(hs[2]!.id) - before[2]!).toBe(each);
    expect(cap.captorBalanceAfter).toBe(bal(hs[0]!.id));
    expect(cap.doubleCross).toBe(false);
    expect(verifyReplay(r.engine.events, r.engine.state)).toMatchObject({ ok: true, mismatches: [] });
  });

  it("a crewmate out of range, or stunned by someone else, gets nothing", () => {
    const { r, out, hs, wanted, bal, capture } = hunt(["Rio", "Vex", "Kai"]);
    hs.forEach((h) => join(r, h.id));
    r.place(hs[1]!.id, T.x + RULES.crew.assistRadiusM + 5, T.z); // too far
    // The outlaw stuns Kai (an outsider's stun is not a betrayal).
    expect(r.cmd(out.id, { type: "weapon.fire", targetId: r.char(hs[2]!.id).id }).ok).toBe(true);
    wanted();
    const before = hs.map((h) => bal(h.id));
    expect(capture(hs[0]!.id).ok).toBe(true);
    const cap = r.events("CAPTURE_SUCCEEDED")[0]!.payload;
    expect(cap.shares).toHaveLength(1);
    expect(bal(hs[0]!.id) - before[0]!).toBe(cap.bounty);
    expect(bal(hs[1]!.id)).toBe(before[1]);
    expect(bal(hs[2]!.id)).toBe(before[2]);
    expect(r.events("CREW_BETRAYED")).toHaveLength(0);
  });

  it("double-cross: stunning a crewmate and capturing within the window takes it all, is announced, and ejects the betrayer", () => {
    const { r, hs, wanted, bal, capture } = hunt();
    hs.forEach((h) => join(r, h.id));
    wanted();
    const before = hs.map((h) => bal(h.id));
    expect(r.cmd(hs[0]!.id, { type: "weapon.fire", targetId: r.char(hs[1]!.id).id }).ok).toBe(true);
    r.run(RULES.weapon.stunMs + 500); // the victim is on their feet again, in range, but it is still a double-cross
    expect(capture(hs[0]!.id).ok).toBe(true);
    const cap = r.events("CAPTURE_SUCCEEDED")[0]!.payload;
    expect(cap.doubleCross).toBe(true);
    expect(bal(hs[0]!.id) - before[0]!).toBe(cap.bounty);
    expect(bal(hs[1]!.id)).toBe(before[1]);
    const b = r.events("CREW_BETRAYED")[0]!.payload;
    expect(b).toMatchObject({ crewName: "WOLVES", betrayerPlayerId: hs[0]!.id, victimPlayerIds: [hs[1]!.id], bounty: cap.bounty });
    expect(r.engine.snapshot().crews![0]!.memberPlayerIds).toEqual([hs[1]!.id]);
    expect(r.events("CREW_LEFT").at(-1)!.payload).toMatchObject({ playerId: hs[0]!.id, reason: "EJECTED" });
    // The swarm hears of it: both informants hold a public memory naming the betrayer, and an incident was raised.
    for (const n of Object.values(r.engine.state.npcs).filter((x) => x.role === "INFORMANT")) {
      const mem = r.engine.agentView(n.id).memories.find((m) => m.summary.includes("turned on its own"));
      expect(mem).toMatchObject({ factType: "RUMOR", sourceKind: "BULLETIN" });
    }
    expect(r.events("INCIDENT_OCCURRED").some((e) => e.payload.description.includes("turned on its own"))).toBe(true);
    expect(verifyReplay(r.engine.events, r.engine.state)).toMatchObject({ ok: true, mismatches: [] });
  });

  it("a stun older than the betrayal window is forgiven: the bounty is shared", () => {
    const { r, hs, wanted, capture } = hunt();
    hs.forEach((h) => join(r, h.id));
    r.cmd(hs[0]!.id, { type: "weapon.fire", targetId: r.char(hs[1]!.id).id });
    r.run(RULES.crew.betrayalWindowMs + 1000);
    wanted();
    expect(capture(hs[0]!.id).ok).toBe(true);
    const cap = r.events("CAPTURE_SUCCEEDED")[0]!.payload;
    expect(cap.doubleCross).toBe(false);
    expect(cap.shares).toHaveLength(2);
  });

  it("two crewmates capturing at the same instant pay out exactly once, and no wallet goes negative", () => {
    const { r, hs, wanted, bal, capture } = hunt();
    hs.forEach((h) => join(r, h.id));
    wanted();
    const total = () => hs.reduce((a, h) => a + bal(h.id), 0);
    const before = total();
    const a = capture(hs[0]!.id), b = capture(hs[1]!.id);
    expect([a.ok, b.ok]).toEqual([true, false]);
    expect(r.events("CAPTURE_SUCCEEDED")).toHaveLength(1);
    expect(total() - before).toBe(r.events("CAPTURE_SUCCEEDED")[0]!.payload.bounty);
    for (const w of Object.values(r.engine.state.wallets)) expect(w.balanceCredits).toBeGreaterThanOrEqual(0);
  });

  it("a solo hunter's capture is unchanged, and a reset clears crews with the same crew id next run", () => {
    const { r, hs, wanted, bal, capture } = hunt();
    wanted();
    const before = bal(hs[0]!.id);
    expect(capture(hs[0]!.id).ok).toBe(true);
    const cap = r.events("CAPTURE_SUCCEEDED")[0]!.payload;
    expect(cap.shares).toBeUndefined();
    expect(bal(hs[0]!.id) - before).toBe(cap.bounty);
    const first = join(r, hs[1]!.id).result as { crewId: string };
    r.engine.reset();
    expect(r.engine.snapshot().crews).toHaveLength(0);
    const again = r.engine.dispatch(hs[1]!.id, { type: "crew.join", crewName: "WOLVES", commandId: "cmd-after-reset-1" });
    expect((again.result as { crewId: string }).crewId).toBe(first.crewId);
  });
});
