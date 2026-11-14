// Solid parked cars: the server stops players and NPCs at their footprints; they never block line of sight.
import { describe, expect, it } from "vitest";
import { CITY_MAP, MISSION_SPOTS, RULES, SPAWN_POINTS, carRect, carRects, hasLineOfSight, insideRect } from "@vice/contracts";
import type { Rect, Vec2 } from "@vice/contracts";
import { NPC_SEEDS } from "../src/demo/seedScenario";
import { findPath } from "../src/engine/pathfinding";
import { makeRoom } from "./helpers";

const RADIUS = 0.6;
const cars = CITY_MAP.cars ?? [];
const rects = carRects();
const inAnyCar = (p: Vec2, pad = 0) => rects.some((r) => insideRect(p, r, pad));
const overlap = (a: Rect, b: Rect) => a.x0 < b.x1 && a.x1 > b.x0 && a.z0 < b.z1 && a.z1 > b.z0;

describe("parked cars: layout", () => {
  it("has 20 to 28 cars, none overlapping a building, the water, the map edge or each other", () => {
    expect(cars.length).toBeGreaterThanOrEqual(20);
    expect(cars.length).toBeLessThanOrEqual(28);
    expect(new Set(cars.map((c) => c.id)).size).toBe(cars.length);
    const w = CITY_MAP.walkable;
    rects.forEach((r, i) => {
      expect(r.x0).toBeGreaterThan(w.x0); expect(r.x1).toBeLessThan(w.x1); expect(r.z0).toBeGreaterThan(w.z0); expect(r.z1).toBeLessThan(w.z1);
      for (const b of CITY_MAP.buildings) expect(overlap(r, b), `${cars[i]!.id} vs ${b.id}`).toBe(false);
      rects.forEach((o, j) => { if (j > i) expect(overlap(r, o), `${cars[i]!.id} vs ${cars[j]!.id}`).toBe(false); });
    });
  });
  it("footprint follows the heading", () => {
    expect(carRect({ id: "a", x: 10, z: 10, length: 4, width: 2, heading: 0, tone: "#fff" })).toEqual({ x0: 8, z0: 9, x1: 12, z1: 11 });
    expect(carRect({ id: "b", x: 10, z: 10, length: 4, width: 2, heading: 90, tone: "#fff" })).toEqual({ x0: 9, z0: 8, x1: 11, z1: 12 });
  });
  it("keeps every job spot, spawn point, NPC post and patrol waypoint at least 2 m clear of any car", () => {
    const points: Vec2[] = [...MISSION_SPOTS, ...SPAWN_POINTS.OUTLAW, ...SPAWN_POINTS.HUNTER, ...NPC_SEEDS.map((s) => s.at), ...NPC_SEEDS.flatMap((s) => s.patrol ?? [])];
    for (const p of points) expect(inAnyCar(p, 2), `(${p.x},${p.z})`).toBe(false);
  });
  it("does not block line of sight", () => {
    const c = cars.find((k) => k.heading === 0)!;
    expect(hasLineOfSight({ x: c.x, z: c.z - 2 }, { x: c.x, z: c.z + 2 })).toBe(true);
  });
});

describe("parked cars: collision", () => {
  it("a player walking into a car stops at its edge and never enters it", () => {
    const t = makeRoom(); const a = t.join("Ace", "OUTLAW"); const ch = t.char(a.id);
    const c = cars.find((k) => k.id === "car_03")!; const r = carRect(c); // south kerb of the arcade, heading 0
    t.place(a.id, c.x, r.z1 + 3);
    t.cmd(a.id, { type: "player.move", dirX: 0, dirZ: -1 });
    t.run(3000);
    expect(ch.z).toBeGreaterThanOrEqual(r.z1 + RADIUS - 1e-6);
    expect(ch.z).toBeLessThan(r.z1 + RADIUS + RULES.playerSpeed / RULES.tickHz + 0.01);
    expect(inAnyCar(ch)).toBe(false);
  });
  it("a player pushing diagonally into a car slides along it", () => {
    const t = makeRoom(); const a = t.join("Ace", "OUTLAW"); const ch = t.char(a.id);
    const c = cars.find((k) => k.id === "car_03")!; const r = carRect(c);
    t.place(a.id, r.x0 + 0.5, r.z1 + RADIUS + 0.2);
    const x0 = ch.x;
    t.cmd(a.id, { type: "player.move", dirX: 1, dirZ: -1 });
    t.run(400);
    expect(ch.x).toBeGreaterThan(x0 + 1);                      // moved along the car
    expect(ch.z).toBeGreaterThanOrEqual(r.z1 + RADIUS - 1e-6); // never into it
  });
  it("a browser client's reported pose inside a car is rejected; a bridge client's is not checked against this map", () => {
    const t = makeRoom(); const a = t.join("Ace", "OUTLAW");
    const c = cars[0]!;
    t.place(a.id, c.x, c.z + 1.6); t.clock.advance(200);
    const ack = t.cmd(a.id, { type: "player.pose", x: c.x, z: c.z });
    expect(ack.ok).toBe(false);
    expect(ack.error).toMatchObject({ code: "MOVE_REJECTED", message: "Inside a parked car" });
  });
  it("walking a full minute in every direction never leaves a player inside a car or a building", () => {
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]] as const) {
      const t = makeRoom(); const a = t.join("Ace", "OUTLAW"); const ch = t.char(a.id);
      t.place(a.id, 32, 28);
      t.cmd(a.id, { type: "player.move", dirX: dx, dirZ: dz });
      for (let i = 0; i < 60; i++) { t.run(1000); expect(inAnyCar(ch)).toBe(false); expect(CITY_MAP.buildings.some((b) => insideRect(ch, b))).toBe(false); }
    }
  });
});

describe("parked cars: the city stays walkable", () => {
  it("every job spot and spawn point is reachable from every other on foot", () => {
    const pts: Vec2[] = [...MISSION_SPOTS, ...SPAWN_POINTS.OUTLAW, ...SPAWN_POINTS.HUNTER];
    for (const from of pts) for (const to of pts) if (from !== to) expect(findPath(from, to, RADIUS), `(${from.x},${from.z}) -> (${to.x},${to.z})`).not.toBeNull();
  });
  it("routes go around cars, never through them", () => {
    const c = cars.find((k) => k.id === "car_03")!;
    const r = carRect(c); const from = { x: r.x0 - 2, z: c.z }, to = { x: r.x1 + 2, z: c.z }; // straight line runs through the car
    const path = findPath(from, to, RADIUS)!;
    expect(path).not.toBeNull();
    expect(path.length).toBeGreaterThan(1);
    let prev: Vec2 = from;
    for (const p of path) { for (let k = 1; k <= 20; k++) { const q = { x: prev.x + ((p.x - prev.x) * k) / 20, z: prev.z + ((p.z - prev.z) * k) / 20 }; expect(inAnyCar(q)).toBe(false); } prev = p; }
  });
  it("both police patrol loops still complete", () => {
    const t = makeRoom("scripted");
    const seen: Record<string, Set<number>> = { npc_p1: new Set(), npc_p2: new Set() };
    for (let i = 0; i < 2400; i++) { // four minutes of patrol
      t.run(100);
      for (const id of Object.keys(seen)) { const n = t.engine.state.npcs[id]!; seen[id]!.add(n.patrolIndex % n.patrol.length); expect(inAnyCar(n)).toBe(false); }
    }
    expect(seen.npc_p1!.size).toBe(t.engine.state.npcs.npc_p1!.patrol.length);
    expect(seen.npc_p2!.size).toBe(t.engine.state.npcs.npc_p2!.patrol.length);
  });
  it("a seeded reset still gives an identical map and identical NPC positions", () => {
    const a = makeRoom(), b = makeRoom();
    expect(a.engine.snapshot().map.cars).toEqual(b.engine.snapshot().map.cars);
    expect(a.engine.snapshot().map.cars!.length).toBe(cars.length);
    expect(a.engine.snapshot().npcs.map((n) => [n.id, n.x, n.z])).toEqual(b.engine.snapshot().npcs.map((n) => [n.id, n.x, n.z]));
  });
});
