import { describe, expect, it } from "vitest";
import { RULES } from "@vice/contracts";
import type { DecisionMode } from "@vice/contracts";
import { createRoomFactory } from "../src/engine/compose";
import { createMemoryPersistence } from "../src/engine/fallbacks";
import { FakeClock } from "../src/engine/util";
import { verifyReplay } from "../src/replay";
import { MockScheduler, makeRoom } from "./helpers";

function fugitiveRoom(mode: DecisionMode = "drex", fugitive = true) {
  const clock = new FakeClock();
  const scheduler = new MockScheduler(clock); scheduler.mode = mode;
  const persistence = createMemoryPersistence();
  const build = createRoomFactory({ persistence, clock, providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 } });
  const room = build({ roomId: "FUG-1", code: "FUG-1", seed: 42, mode, scheduler, clock, fugitive });
  const { engine } = room; const agent = room.fugitive ?? null;
  const api = {
    clock, scheduler, engine, agent, persistence,
    run(ms: number) { for (let t = 0; t < ms; t += 100) { clock.advance(100); agent?.step(); engine.tick(); scheduler.flush(); } },
    char() { return agent!.character()!; },
    /** Keeps an officer a fixed distance east of the fugitive (clear line of sight) for the given time. */
    shadowWithOfficer(ms: number, gap: number) {
      const p1 = engine.state.npcs.npc_p1!; p1.patrol = [];
      for (let t = 0; t < ms; t += 100) { const c = agent!.character()!; p1.x = c.x + gap; p1.z = c.z; p1.home = { x: p1.x, z: p1.z }; api.run(100); }
    },
    outlaws() { return Object.values(engine.state.characters).filter((c) => c.role === "OUTLAW"); },
    fugitiveRequests() { return engine.events.filter((e) => e.type === "DECISION_REQUESTED" && (e.payload as { role: string }).role === "FUGITIVE"); },
  };
  return api;
}
/** Answers the fugitive with one action and every NPC with its scripted baseline. */
const answer = (action: string) => (req: { role: string; options: Record<string, string>; scriptedAction: string }) =>
  ({ action: (req.role as string) === "FUGITIVE" ? (action in req.options ? action : "lay_low") : req.scriptedAction, source: "drex" as const });

describe("fugitive agent", () => {
  it("is off unless a room asks for it, so existing rooms are unchanged", () => {
    const plain = makeRoom("scripted"); plain.run(8000);
    expect(Object.keys(plain.engine.state.players)).toHaveLength(0);
    const off = fugitiveRoom("scripted", false); off.run(8000);
    expect(off.agent).toBeNull(); expect(Object.keys(off.engine.state.players)).toHaveLength(0);
  });

  it("spawns once as a bot outlaw with its own ticker, wallet and listing", () => {
    const r = fugitiveRoom("scripted"); r.run(10000);
    const players = Object.values(r.engine.state.players);
    expect(players).toHaveLength(1); expect(players[0]).toMatchObject({ nickname: "Ryder", role: "OUTLAW", isBot: true });
    const c = r.char();
    expect(c.ticker).toMatch(/^RYD-\d{2}$/);
    expect(r.engine.state.listings[c.ticker!]?.status).toBe("ACTIVE");
    expect(r.outlaws()).toHaveLength(1);
  });

  it("decides from its own knowledge only: an officer it cannot see is absent from its state", () => {
    const r = fugitiveRoom("drex"); r.run(4500);
    const req = r.scheduler.queue.find((q) => (q.req.role as string) === "FUGITIVE")!.req;
    expect(req.state.pursuers_in_sight).toEqual([]);
    expect(JSON.stringify(req.state)).not.toMatch(/Duarte|Renner|npc_p/);
    expect(Object.keys(req.options)).toEqual(expect.arrayContaining(["lay_low", "do_job"]));
    expect(req.options.flee).toBeUndefined(); expect(req.options.fire).toBeUndefined();
    expect(req.safeAction).toBe("lay_low"); expect(req.scriptedAction).toBe("do_job");
    // Put an officer next to it with a clear line of sight: now it is in the state and flee / fire are legal.
    r.scheduler.resolve(req.agentId, "lay_low");
    r.shadowWithOfficer(RULES.fugitive.thinkEveryMs + 500, 4);
    const next = r.scheduler.queue.filter((q) => (q.req.role as string) === "FUGITIVE").pop()!.req;
    expect(next.state.pursuers_in_sight).toEqual([expect.objectContaining({ kind: "police", name: "Sgt. Imani Duarte" })]);
    expect(Object.keys(next.options)).toEqual(expect.arrayContaining(["flee", "fire"]));
  });

  it("do_job walks to the open job and completes it through the normal mission command", () => {
    const r = fugitiveRoom("drex"); r.scheduler.auto = answer("do_job");
    r.run(40000);
    const done = r.engine.events.filter((e) => e.type === "MISSION_COMPLETED");
    expect(done.length).toBeGreaterThanOrEqual(1);
    expect((done[0]!.payload as { characterId: string }).characterId).toMatch(/^chr_/);
    const applied = r.engine.events.find((e) => e.type === "AGENT_ACTION_APPLIED" && (e.payload as { role: string }).role === "FUGITIVE")!;
    expect(applied.payload).toMatchObject({ action: "do_job", decisionSource: "drex" });
    expect(r.agent!.decisions).toBeGreaterThan(0);
  });

  it("fire uses the normal weapon command against a pursuer in range", () => {
    const r = fugitiveRoom("drex"); r.run(4500);
    r.scheduler.auto = answer("fire"); r.scheduler.flush();
    r.shadowWithOfficer(RULES.fugitive.thinkEveryMs + 1500, 5);
    const shots = r.engine.events.filter((e) => e.type === "WEAPON_FIRED");
    expect(shots.length).toBeGreaterThanOrEqual(1);
    expect(shots[0]!.payload).toMatchObject({ hitId: "npc_p1" });
  });

  it("a capture leads to exactly one respawn with a new ticker; the old listing stays halted at zero", () => {
    const r = fugitiveRoom("scripted"); r.scheduler.auto = answer("do_job");
    r.run(120000);
    const captures = r.engine.events.filter((e) => e.type === "CAPTURE_SUCCEEDED");
    expect(captures.length).toBeGreaterThanOrEqual(1);
    const first = (captures[0]!.payload as { ticker: string }).ticker;
    expect(r.engine.state.listings[first]).toMatchObject({ status: "HALTED_CAPTURED", spotPrice: 0 });
    const respawns = r.engine.events.filter((e) => e.type === "CHARACTER_RESPAWNED");
    expect(respawns.length).toBe(captures.length === respawns.length ? captures.length : captures.length - 1);
    const tickers = r.outlaws().map((c) => c.ticker);
    expect(new Set(tickers).size).toBe(tickers.length);
    expect(Object.values(r.engine.state.players)).toHaveLength(1);
    const live = r.outlaws().filter((c) => c.status === "ACTIVE" || c.status === "WANTED");
    expect(live.length).toBeLessThanOrEqual(1);
  });

  it("the self-running hunt replays from the event log, and two runs on one seed match", () => {
    const run = () => { const r = fugitiveRoom("scripted"); r.run(60000); return r; };
    const a = run(); const b = run();
    const v = verifyReplay(a.engine.events, a.engine.state);
    expect(v.mismatches).toEqual([]); expect(v.ok).toBe(true);
    expect(a.engine.events.map((e) => e.type)).toEqual(b.engine.events.map((e) => e.type));
    expect(a.outlaws().map((c) => c.ticker)).toEqual(b.outlaws().map((c) => c.ticker));
  });

  it("a reset keeps one fugitive and starts it again on the new run", () => {
    const r = fugitiveRoom("scripted"); r.run(10000);
    r.engine.reset();
    r.run(10000);
    expect(Object.values(r.engine.state.players)).toHaveLength(1);
    expect(r.outlaws()).toHaveLength(1);
    expect(r.char().ticker).toMatch(/^RYD-\d{2}$/);
  });
});
