// Stun pulse: one non-lethal weapon, decided entirely by the server.
import { describe, expect, it } from "vitest";
import { RULES } from "@vice/contracts";
import { verifyReplay } from "../src/replay";
import { makeRoom } from "./helpers";

const W = RULES.weapon;
// Open road east of the plaza: no building between these points.
const A = { x: 60, z: 54 }, NEAR = { x: 66, z: 54 }, FAR = { x: 60 + W.rangeM + 4, z: 54 };

function duel() {
  const r = makeRoom("scripted");
  const out = r.join("Ace", "OUTLAW"), hun = r.join("Rio", "HUNTER");
  // Park the police far away so they do not interfere unless a test wants them.
  for (const n of Object.values(r.engine.state.npcs)) if (n.role === "POLICE") { n.x = 4; n.z = 4; n.patrol = []; n.activity = "IDLE"; n.home = { x: 4, z: 4 }; }
  r.place(out.id, A.x, A.z); r.place(hun.id, NEAR.x, NEAR.z);
  return { r, out, hun };
}

describe("stun pulse", () => {
  it("a hunter's shot at a target in range stuns it: it cannot move until the stun expires", () => {
    const { r, out, hun } = duel();
    const ack = r.cmd(hun.id, { type: "weapon.fire", targetId: r.char(out.id).id });
    expect(ack.ok).toBe(true);
    expect(r.events("WEAPON_FIRED")).toHaveLength(1);
    expect(r.events("STUNNED")[0]!.payload).toMatchObject({ targetId: r.char(out.id).id, targetKind: "CHARACTER", byId: r.char(hun.id).id });
    r.cmd(out.id, { type: "player.move", dirX: -1, dirZ: 0 });
    r.run(W.stunMs - 500);
    expect(r.char(out.id).x).toBe(A.x);
    r.run(1500);
    expect(r.char(out.id).x).toBeLessThan(A.x); // moving again
    expect(r.engine.characterView(r.char(hun.id)).ammo).toBe(W.ammo - 1);
  });

  it("rejects out of range, blocked line of sight, spectators, invalid targets and firing while stunned", () => {
    const { r, out, hun } = duel();
    const target = r.char(out.id).id;
    r.place(hun.id, FAR.x, FAR.z);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).error).toMatchObject({ code: "WEAPON_REJECTED", reason: "OUT_OF_RANGE" });
    // Mirage Mall (x 68..86, z 34..48) stands between these two points.
    r.place(out.id, 66, 41); r.place(hun.id, 67.5, 41);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).ok).toBe(true); // same side: fine
    r.run(W.cooldownMs + 100);
    r.place(hun.id, 88, 41); r.place(out.id, 66, 41);
    r.run(W.stunMs);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).error?.reason).toMatch(/LINE_OF_SIGHT_BLOCKED|OUT_OF_RANGE/);
    const spec = r.join("Judge", "SPECTATOR");
    expect(r.cmd(spec.id, { type: "weapon.fire", targetId: target }).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: "npc_w1" }).error?.reason).toBe("INVALID_TARGET"); // witnesses cannot be hit
    // A stunned shooter cannot fire back.
    r.place(out.id, A.x, A.z); r.place(hun.id, NEAR.x, NEAR.z); r.run(W.cooldownMs + 100);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).ok).toBe(true);
    expect(r.cmd(out.id, { type: "weapon.fire", targetId: r.char(hun.id).id }).error?.reason).toBe("STUNNED");
  });

  it("line of sight: a building between shooter and target blocks the shot", () => {
    const { r, out, hun } = duel();
    r.place(hun.id, 62, 41); r.place(out.id, 66.5, 41); // both west of the mall
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: r.char(out.id).id }).ok).toBe(true);
    r.run(W.cooldownMs + W.stunMs);
    r.place(hun.id, 43, 41); r.place(out.id, 51, 41); // Plaza Kiosk (x 45..49, z 39..43) between, 8 m apart
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: r.char(out.id).id }).error?.reason).toBe("LINE_OF_SIGHT_BLOCKED");
  });

  it("cooldown and ammo are enforced, and a rejected shot spends nothing", () => {
    const { r, out, hun } = duel();
    const target = r.char(out.id).id;
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).ok).toBe(true);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).error?.reason).toBe("COOLDOWN");
    expect(r.engine.characterView(r.char(hun.id)).ammo).toBe(W.ammo - 1);
    for (let i = 1; i < W.ammo; i++) { r.run(W.cooldownMs + 100); expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).ok).toBe(true); }
    r.run(W.cooldownMs + 100);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: target }).error?.reason).toBe("NO_AMMO");
    expect(r.events("WEAPON_FIRED")).toHaveLength(W.ammo);
  });

  it("aiming by direction hits the nearest legal target inside the cone and misses outside it", () => {
    const { r, out, hun } = duel();
    const hit = r.cmd(hun.id, { type: "weapon.fire", dirX: -1, dirZ: 0 }); // outlaw is due west
    expect((hit.result as { hitId: string | null }).hitId).toBe(r.char(out.id).id);
    r.run(W.cooldownMs + 100);
    const miss = r.cmd(hun.id, { type: "weapon.fire", dirX: 1, dirZ: 0 });
    expect(miss.ok).toBe(true);
    expect((miss.result as { hitId: string | null }).hitId).toBeNull();
    expect(r.events("STUNNED")).toHaveLength(1);
    expect(r.cmd(hun.id, { type: "weapon.fire" }).error?.reason).toMatch(/COOLDOWN|NO_AIM/);
  });

  it("the same commandId fires once", () => {
    const { r, out, hun } = duel();
    const cmd = { type: "weapon.fire" as const, targetId: r.char(out.id).id, commandId: "cmd-weapon-same-1" };
    const a = r.engine.dispatch(hun.id, cmd), b = r.engine.dispatch(hun.id, cmd);
    expect(a.ok).toBe(true); expect(b.duplicate).toBe(true);
    expect(r.events("WEAPON_FIRED")).toHaveLength(1);
  });

  it("an outlaw's shot adds heat and is an incident that nearby NPCs witness in their own memory", () => {
    const r = makeRoom("scripted");
    const out = r.join("Ace", "OUTLAW"), hun = r.join("Rio", "HUNTER");
    const witness = r.engine.state.npcs["npc_w1"]!; // dock fisher
    r.place(out.id, witness.x + 5, witness.z - 4); r.place(hun.id, witness.x + 9, witness.z - 4);
    const heat = r.char(out.id).heat;
    expect(r.cmd(out.id, { type: "weapon.fire", targetId: r.char(hun.id).id }).ok).toBe(true);
    expect(r.char(out.id).heat).toBe(heat + W.heatPerShot);
    const inc = r.events("INCIDENT_OCCURRED").at(-1)!;
    expect(inc.payload.kind).toBe("SIGHTING");
    expect(inc.payload.observerIds).toContain("npc_w1");
    expect(r.engine.agentView("npc_w1").memories.some((m) => m.eventId === inc.eventId && m.sourceKind === "DIRECT")).toBe(true);
    expect(r.engine.agentView("npc_t1").memories.some((m) => m.eventId === inc.eventId)).toBe(false); // out of sight: knows nothing
    // A hunter's shot is not an incident and adds no heat.
    const n = r.events("INCIDENT_OCCURRED").length;
    r.run(W.stunMs + 100);
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: r.char(out.id).id }).ok).toBe(true);
    expect(r.events("INCIDENT_OCCURRED")).toHaveLength(n);
  });

  it("a stunned officer stops and cannot capture; the pursuit resumes when the stun ends", () => {
    const r = makeRoom("scripted");
    const out = r.join("Ace", "OUTLAW");
    const cop = r.engine.state.npcs["npc_p1"]!;
    for (const n of Object.values(r.engine.state.npcs)) if (n.id !== cop.id && n.role === "POLICE") { n.x = 4; n.z = 4; n.patrol = []; n.activity = "IDLE"; n.home = { x: 4, z: 4 }; }
    r.place(out.id, 60, 54); cop.x = 52; cop.z = 54;
    const c = r.char(out.id); c.heat = 60; c.status = "WANTED";
    r.engine.setNpcActivity(cop.id, "PURSUE", { targetCharacterId: c.id });
    expect(r.cmd(out.id, { type: "weapon.fire", targetId: cop.id }).ok).toBe(true);
    const at = { x: cop.x, z: cop.z };
    r.run(W.stunMs - 300);
    expect({ x: cop.x, z: cop.z }).toEqual(at);
    expect(r.events("CAPTURE_SUCCEEDED")).toHaveLength(0);
    expect(r.engine.worldTick().npcs.find((n) => n.id === cop.id)!.stunned).toBe(true);
    r.run(6000);
    expect(r.engine.worldTick().npcs.find((n) => n.id === cop.id)!.stunned).toBeUndefined();
    expect(r.events("CAPTURE_SUCCEEDED")).toHaveLength(1); // nobody dies: capture is still the only way to V$0
  });

  it("a hunter stuns a wanted outlaw and makes the capture; a stunned hunter cannot capture", () => {
    const { r, out, hun } = duel();
    const c = r.char(out.id); c.heat = 60; c.status = "WANTED";
    expect(r.cmd(hun.id, { type: "weapon.fire", targetId: c.id }).ok).toBe(true);
    r.place(hun.id, c.x + 1.5, c.z);
    expect(r.cmd(hun.id, { type: "capture.attempt", targetCharacterId: c.id }).ok).toBe(true);
    expect(r.engine.state.listings[c.ticker!]!.spotPrice).toBe(0);

    const d = duel(); const c2 = d.r.char(d.out.id); c2.heat = 60; c2.status = "WANTED";
    d.r.place(d.hun.id, c2.x + 1.5, c2.z);
    expect(d.r.cmd(d.out.id, { type: "weapon.fire", targetId: d.r.char(d.hun.id).id }).ok).toBe(true);
    expect(d.r.cmd(d.hun.id, { type: "capture.attempt", targetCharacterId: c2.id }).error?.reason).toBe("CAPTOR_INACTIVE");
  });

  it("replay still verifies and a reset restores full ammo", () => {
    const { r, out, hun } = duel();
    r.cmd(hun.id, { type: "weapon.fire", targetId: r.char(out.id).id });
    r.cmd(out.id, { type: "market.buy", ticker: r.char(out.id).ticker, qty: 1 });
    r.run(4000);
    expect(verifyReplay(r.engine.events, r.engine.state)).toMatchObject({ ok: true, mismatches: [] });
    r.engine.reset();
    expect(r.engine.characterView(r.char(hun.id)).ammo).toBe(W.ammo);
    expect(r.engine.characterView(r.char(hun.id)).stunnedUntilMs).toBeNull();
  });
});
