// Live side-by-side: in drex mode the same question is also sent to the Meta LLM in the background.
// The Meta answer is recorded next to the Drex decision and is never applied to the world.
import { describe, expect, it } from "vitest";
import type { GameEvent } from "@vice/contracts";
import type { DecisionProvider, ProviderCall, ProviderResult } from "../src/domain";
import { createScheduler } from "../src/decisions";
import { createRoomFactory } from "../src/engine/compose";
import { createMemoryPersistence } from "../src/engine/fallbacks";
import { FakeClock } from "../src/engine/util";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const none = { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 };

function providers(metaDelayMs: number) {
  const metaCalls: ProviderCall[] = [];
  const drex: DecisionProvider = {
    name: "drex", model: "drex-test", isConfigured: () => true,
    decide: async (call): Promise<ProviderResult> => ({ provider: "drex", model: "drex-test", choice: "hold" in call.options ? "hold" : Object.keys(call.options)[0]!, confidence: 0.9, probabilities: { hold: 0.9 }, requestId: "req_drex", latencyMs: 12, providerEvalMs: 5, usage: null, attempts: 1 }),
  };
  const meta: DecisionProvider = {
    name: "meta", model: "meta-test", isConfigured: () => true,
    decide: (call) => new Promise<ProviderResult>((resolve) => {
      metaCalls.push(call);
      // The momentum trader's shadow disagrees with Drex; the cautious trader's agrees.
      const choice = call.state.name === "Priya Castellan" && "buy" in call.options ? "buy" : "hold";
      setTimeout(() => resolve({ provider: "meta", model: "meta-test", choice, confidence: null, probabilities: null, requestId: "req_meta", latencyMs: 480, providerEvalMs: null, usage: null, attempts: 1 }), metaDelayMs);
    }),
  };
  return { drex, meta, metaCalls };
}

describe("Meta shadow of Drex decisions", () => {
  it("records the background answer on the trace, never applies it, and reports measured stats only", async () => {
    const clock = new FakeClock(); const persistence = createMemoryPersistence(); const { drex, meta, metaCalls } = providers(70);
    const build = createRoomFactory({ persistence, clock, providers: none });
    const { engine, metrics } = build({
      roomId: "SHADOW", code: "SHADOW", seed: 42, mode: "drex", clock,
      scheduler: (hooks) => createScheduler({ drex, meta, clock, mode: "drex", concurrency: 2, maxCallsPerMinute: 60, shadowMeta: true, onShadow: hooks.onShadow }),
    });
    engine.addPlayer({ nickname: "Ace", role: "OUTLAW", sessionToken: "tok" }); // both traders are asked about the new listing

    // Drex has answered and been applied; Meta is still thinking: nothing is claimed yet.
    await wait(25);
    const resolved = engine.events.filter((e): e is GameEvent<"DECISION_RESOLVED"> => e.type === "DECISION_RESOLVED");
    expect(resolved.map((e) => e.payload.agentId).sort()).toEqual(["npc_t1", "npc_t2"]);
    for (const e of resolved) expect(e.payload).toMatchObject({ decisionSource: "drex", provider: "drex", chosenAction: "hold", latencyMs: 12 });
    expect(engine.agentView("npc_t1").lastDecision?.shadow).toBeNull();
    expect(metrics.view().shadow).toBeNull();
    engine.drainDirty();

    await wait(110);
    expect(metaCalls).toHaveLength(2);
    // Meta saw exactly the question, options and state Drex saw.
    const drexState = engine.agentView("npc_t1").lastDecision!.stateSnapshot;
    expect(metaCalls.find((c) => c.state.name === "Priya Castellan")!.state).toEqual(drexState);

    const t1 = engine.agentView("npc_t1").lastDecision!; const t2 = engine.agentView("npc_t2").lastDecision!;
    expect(t1.shadow).toEqual({ provider: "meta", model: "meta-test", chosenAction: "buy", latencyMs: 480, providerRequestId: "req_meta", errorType: null, agrees: false });
    expect(t2.shadow).toMatchObject({ provider: "meta", chosenAction: "hold", latencyMs: 480, agrees: true });
    // The decision itself is untouched: still Drex's answer, still labeled Drex.
    expect(t1).toMatchObject({ decisionSource: "drex", provider: "drex", chosenAction: "hold", providerChoice: "hold", status: "APPLIED" });
    // Never applied: Meta said "buy", nobody bought.
    expect(engine.events.some((e) => e.type === "TRADE_EXECUTED")).toBe(false);
    expect(engine.state.wallets["wal_npc_t1"]!.balanceCredits).toBe(2000);

    expect(metrics.view().shadow).toEqual({ compared: 2, agreed: 1, drexP50Ms: 12, metaP50Ms: 480 });
    // Persisted with the trace, and the inspector view is refreshed.
    const stored = persistence.loadDecisions("SHADOW", engine.state.runId).find((d) => d.agentId === "npc_t1")!;
    expect(stored.shadow).toMatchObject({ chosenAction: "buy", agrees: false });
    expect(engine.drainDirty().agents.map((a) => a.id).sort()).toEqual(["npc_t1", "npc_t2"]);
  });

  it("is off in scripted mode and when not enabled", async () => {
    for (const cfg of [{ mode: "scripted" as const, shadowMeta: true }, { mode: "drex" as const, shadowMeta: false }]) {
      const clock = new FakeClock(); const { drex, meta, metaCalls } = providers(5);
      const build = createRoomFactory({ persistence: createMemoryPersistence(), clock, providers: none });
      const { engine, metrics } = build({
        roomId: "SHADOW2", code: "SHADOW2", seed: 42, mode: cfg.mode, clock,
        scheduler: (hooks) => createScheduler({ drex, meta, clock, mode: cfg.mode, concurrency: 2, maxCallsPerMinute: 60, shadowMeta: cfg.shadowMeta, onShadow: hooks.onShadow }),
      });
      engine.addPlayer({ nickname: "Ace", role: "OUTLAW", sessionToken: "tok" });
      await wait(40);
      expect(metaCalls, cfg.mode).toHaveLength(0);
      expect(engine.agentView("npc_t1").lastDecision?.shadow ?? null).toBeNull();
      expect(metrics.view().shadow ?? null).toBeNull();
    }
  });

  it("without both keys the factory never turns the shadow on", () => {
    const build = createRoomFactory({ persistence: createMemoryPersistence(), clock: new FakeClock(), providers: { ...none, naceApiKey: "nace_sk_test", shadowMeta: true } });
    const { scheduler } = build({ roomId: "SHADOW3", code: "SHADOW3", seed: 42, mode: "drex" });
    expect(scheduler.status().meta.configured).toBe(false);
    scheduler.cancelAll();
  });
});
