import type { ClientCommand, CommandAck, DecisionMode, DecisionTrace, GameEvent, HumanRole } from "@vice/contracts";
import type { DecisionRequest, SchedulerPort } from "../src/domain";
import { createRoomFactory } from "../src/engine/compose";
import { createMemoryPersistence } from "../src/engine/fallbacks";
import { FakeClock } from "../src/engine/util";

/** Scheduler stand-in: records requests; tests resolve them by hand (or auto-answer) with an honest source label. */
export class MockScheduler implements SchedulerPort {
  mode: DecisionMode = "drex";
  queue: Array<{ req: DecisionRequest; trace: DecisionTrace; done: (t: DecisionTrace) => void }> = [];
  seen = new Set<string>();
  /** When set, every request is answered synchronously on the next flush() with this picker. */
  auto: ((req: DecisionRequest) => { action: string; source: DecisionTrace["decisionSource"]; errorType?: string }) | null = null;
  constructor(private clock: FakeClock) {}
  request(req: DecisionRequest, done: (t: DecisionTrace) => void): DecisionTrace | null {
    if (this.seen.has(req.dedupeKey)) return null;
    this.seen.add(req.dedupeKey);
    const trace: DecisionTrace = {
      id: req.decisionId, roomId: req.roomId, runId: req.runId, agentId: req.agentId, agentName: req.agentName, role: req.role, status: "REQUESTED", mode: this.mode,
      decisionSource: null, provider: null, model: null, question: req.question, offeredActions: req.options, chosenAction: null, providerChoice: null, confidence: null,
      probabilities: null, providerRequestId: null, latencyMs: null, providerEvalMs: null, usage: null, attempts: 0, errorType: null, errorMessage: null, rejectReason: null,
      stateSnapshot: req.state, stateHash: "", triggerEventId: req.triggerEventId, correlationId: req.correlationId, effectEventIds: [], requestedAtMs: req.requestedAtMs,
      resolvedAtMs: null, requestedAtSequence: req.requestedAtSequence, citations: req.citations, shadow: null,
    };
    this.queue.push({ req, trace, done });
    return trace;
  }
  resolve(agentId: string, action: string, source: DecisionTrace["decisionSource"] = "drex", errorType: string | null = null): boolean {
    const i = this.queue.findIndex((q) => q.req.agentId === agentId);
    if (i < 0) return false;
    const [q] = this.queue.splice(i, 1);
    const real = source === "drex";
    q!.done({ ...q!.trace, status: source === "fallback" ? "FALLBACK_APPLIED" : "RESOLVED", decisionSource: source, provider: real ? "drex" : null, model: real ? "mock-drex" : null,
      chosenAction: action, providerChoice: real ? action : null, confidence: real ? 0.9 : null, probabilities: real ? { [action]: 0.9 } : null, providerRequestId: real ? "req_mock" : null,
      latencyMs: real ? 120 : null, errorType, errorMessage: errorType ? "mock provider failure" : null, attempts: 1, resolvedAtMs: this.clock.now() });
    return true;
  }
  flush(): void { while (this.auto && this.queue.length) { const q = this.queue[0]!; const a = this.auto(q.req); this.resolve(q.req.agentId, a.action, a.source, a.errorType ?? null); } }
  setMode(m: DecisionMode): void { this.mode = m; }
  getMode(): DecisionMode { return this.mode; }
  status() { const h = { configured: true, state: "LIVE" as const, model: "mock", lastOkAtMs: null, lastErrorType: null, callsLastMinute: 0, maxCallsPerMinute: 60, inFlight: this.queue.length, circuitOpenUntilMs: null }; return { mode: this.mode, drex: h, meta: h, pending: this.queue.length, badge: "MOCK" }; }
  pending(): number { return this.queue.length; }
  cancelAll(): void { this.queue = []; this.seen.clear(); }
}

export function makeRoom(mode: DecisionMode = "drex", seed = 42) {
  const clock = new FakeClock();
  const scheduler = new MockScheduler(clock); scheduler.mode = mode;
  const persistence = createMemoryPersistence();
  const build = createRoomFactory({ persistence, clock, providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 } });
  const { engine } = build({ roomId: "TEST-1", code: "TEST-1", seed, mode, scheduler, clock });
  let n = 0;
  const api = {
    clock, scheduler, persistence, engine,
    join(nickname: string, role: HumanRole, isDirector = false) { return engine.addPlayer({ nickname, role, isDirector, sessionToken: `tok_${nickname}` }); },
    cmd(playerId: string, c: Record<string, unknown>): CommandAck { return engine.dispatch(playerId, { commandId: `cmd-test-${++n}`, ...c } as ClientCommand); },
    /** Advance the fake clock in 100 ms ticks. */
    run(ms: number) { for (let t = 0; t < ms; t += 100) { clock.advance(100); engine.tick(); scheduler.flush(); } },
    char(playerId: string) { return engine.state.characters[engine.state.players[playerId]!.characterId!]!; },
    place(playerId: string, x: number, z: number) { const c = api.char(playerId); c.x = x; c.z = z; },
    events<T extends GameEvent["type"]>(type: T) { return engine.events.filter((e): e is GameEvent<T> => e.type === type); },
    mission() { return Object.values(engine.state.missions).find((m) => m.status === "AVAILABLE")!; },
    /** Teleports the outlaw onto the open mission and completes it. */
    doMission(playerId: string) { const m = api.mission(); api.place(playerId, m.x, m.z); return api.cmd(playerId, { type: "mission.interact", missionId: m.id }); },
  };
  return api;
}
