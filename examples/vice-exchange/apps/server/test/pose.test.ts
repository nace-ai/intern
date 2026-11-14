// Audit finding (engine and security reviewers): the player.pose speed check averaged over idle time and had a 50 ms
// floor, so a client could bank a teleport by standing still, or cover 174 m in zero time with many small steps.
import { describe, expect, it } from "vitest";
import { CITY_MAP, RULES } from "@vice/contracts";
import { makeRoom } from "./helpers";

const insideBuilding = (p: { x: number; z: number }) => CITY_MAP.buildings.some((b) => p.x > b.x0 && p.x < b.x1 && p.z > b.z0 && p.z < b.z1);

function world() {
  const t = makeRoom("scripted");
  const g = t.engine.addPlayer({ nickname: "Godot", role: "OUTLAW", sessionToken: "tok_g", socketId: "bridge:1" });
  const b = t.join("Bo", "OUTLAW");
  t.run(300);
  return { t, g, b };
}

describe("player.pose distance budget", () => {
  it("standing still never banks a teleport (external 3D client)", () => {
    const { t, g } = world();
    t.place(g.id, 60, 68);
    t.run(4000);
    const far = t.cmd(g.id, { type: "player.pose", x: 118, z: 2 });
    expect(far.ok).toBe(false);
    expect(far.error).toMatchObject({ code: "MOVE_REJECTED", message: "Too fast" });
    expect(t.char(g.id)).toMatchObject({ x: 60, z: 68 });
    // The most an idle client can ever cover in one report is the burst window at the speed ceiling.
    const burst = RULES.maxPoseSpeed * 0.4;
    expect(t.cmd(g.id, { type: "player.pose", x: 60 - (burst + 1), z: 68 }).ok).toBe(false);
    expect(t.cmd(g.id, { type: "player.pose", x: 60 - (burst - 1), z: 68 }).ok).toBe(true);
  });

  it("many small steps in the same instant cannot add up", () => {
    const { t, g } = world();
    t.place(g.id, 60, 68);
    t.run(2000);
    let accepted = 0, x = 60;
    for (let i = 0; i < 78; i++) { x -= 2; if (t.cmd(g.id, { type: "player.pose", x: Math.max(2, x), z: 68 }).ok) accepted += 1; else x += 2; }
    expect(accepted * 2).toBeLessThanOrEqual(RULES.maxPoseSpeed * 0.4);
    expect(60 - t.char(g.id).x).toBeLessThanOrEqual(RULES.maxPoseSpeed * 0.4);
  });

  it("ordinary 10 Hz reports are accepted: on foot and in a vehicle", () => {
    const { t, g } = world();
    t.place(g.id, 4, 28);
    t.run(1000);
    let x = 4;
    for (let i = 0; i < 20; i++) { t.run(100); x += 0.9; expect(t.cmd(g.id, { type: "player.pose", x, z: 28 }).ok).toBe(true); }   // 9 m/s on foot
    for (let i = 0; i < 20; i++) { t.run(100); x += 4.0; expect(t.cmd(g.id, { type: "player.pose", x, z: 28 }).ok).toBe(true); }   // 40 m/s driving
    expect(t.char(g.id).x).toBeCloseTo(x, 5);
  });

  it("a browser client cannot use pose to outrun walking speed", () => {
    const { t, b } = world();
    t.place(b.id, 4, 28);
    t.run(1000);
    // Two seconds of reports that each ask for 4 m per 100 ms (40 m/s): only the walking-speed budget is ever granted.
    let rejected = 0;
    for (let i = 0; i < 20; i++) { t.run(100); if (!t.cmd(b.id, { type: "player.pose", x: t.char(b.id).x + 4, z: 28 }).ok) rejected += 1; }
    const covered = t.char(b.id).x - 4;
    const foot = RULES.playerSpeed * 1.25;
    expect(covered).toBeLessThanOrEqual(foot * 0.4 + foot * 2 + 1e-6); // burst window + two seconds at 1.25 x walking speed
    expect(rejected).toBeGreaterThanOrEqual(12);
    t.run(100);
    expect(t.cmd(b.id, { type: "player.pose", x: t.char(b.id).x + 1, z: 28 }).ok).toBe(true); // 1 m in 0.1 s is fine
  });

  it("an external client reported inside a building footprint stands at the nearest open point, so it stays capturable", () => {
    const { t, g } = world();
    const rio = t.join("Rio", "HUNTER");
    t.place(g.id, 67, 62); // just west of Bayfront Hotel (x 68..86, z 60..70)
    t.run(1000);
    expect(t.cmd(g.id, { type: "player.pose", x: 70, z: 62 }).ok).toBe(true); // 2 m inside the footprint
    const c = t.char(g.id);
    expect(insideBuilding(c)).toBe(false);
    expect(Math.hypot(c.x - 70, c.z - 62)).toBeLessThan(3); // moved to the nearest open point, not far away
    // Make the character wanted and let a hunter standing next to it capture: line of sight is not blocked.
    c.heat = 60; c.status = "WANTED";
    t.place(rio.id, c.x - 1.5, c.z);
    t.run(2000);
    expect(t.cmd(rio.id, { type: "capture.attempt", targetCharacterId: c.id }).ok).toBe(true);
  });
});
