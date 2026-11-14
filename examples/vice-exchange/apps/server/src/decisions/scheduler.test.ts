import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RULES } from "@vice/contracts";
import type { DecisionTrace } from "@vice/contracts";
import type { DecisionProvider, DecisionRequest, ProviderResult } from "../domain";
import { ProviderError } from "../domain";
import { createScheduler, createDrexProvider, createMetaProvider } from "./index";

const clock = { now: () => Date.now() };
let n = 0;
function req(over: Partial<DecisionRequest> = {}): DecisionRequest {
  n += 1;
  return {
    decisionId: `dec_${n}`, roomId: "r", runId: "run", agentId: `npc_${n}`, agentName: "W", role: "WITNESS",
    question: "q", options: { report: "a", gossip: "b", ignore: "c" }, state: { k: n },
    scriptedAction: "report", safeAction: "ignore", triggerEventId: `evt_${n}`, correlationId: "c",
    requestedAtMs: Date.now(), requestedAtSequence: 1, citations: [], dedupeKey: `k_${n}`, ...over,
  };
}
const ok = (choice = "gossip", confidence: number | null = 0.8): ProviderResult => ({
  provider: "drex", model: "drex-test", choice, confidence, probabilities: confidence === null ? null : { report: 0.1, gossip: 0.8, ignore: 0.1 },
  requestId: "req_x", latencyMs: 12, providerEvalMs: 5, usage: { inputTokens: 1, outputTokens: 1 }, attempts: 1,
});
function fake(name: "drex" | "meta", impl: DecisionProvider["decide"], configured = true): DecisionProvider & { calls: number } {
  const p = { name, model: `${name}-test`, calls: 0, isConfigured: () => configured, decide: ((c, o) => { p.calls += 1; return impl(c, o); }) as DecisionProvider["decide"] };
  return p;
}
const never: DecisionProvider["decide"] = () => new Promise(() => {});
function make(drex: DecisionProvider, over: Record<string, unknown> = {}) {
  return createScheduler({ drex, meta: fake("meta", async () => ({ ...ok("ignore", null), provider: "meta" })), clock, mode: "drex", concurrency: 4, maxCallsPerMinute: 100, ...over } as never);
}
async function run(s: ReturnType<typeof make>, r: DecisionRequest, advance = 0): Promise<DecisionTrace> {
  let out: DecisionTrace | undefined;
  s.request(r, (t) => { out = t; });
  await vi.advanceTimersByTimeAsync(advance);
  return out!;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("scheduler", () => {
  it("applies a real drex answer with its metadata", async () => {
    const t = await run(make(fake("drex", async () => ok())), req());
    expect(t).toMatchObject({ status: "RESOLVED", decisionSource: "drex", provider: "drex", chosenAction: "gossip", providerChoice: "gossip", confidence: 0.8, providerRequestId: "req_x", latencyMs: 12 });
  });

  it("scripted mode never calls a provider", async () => {
    const d = fake("drex", async () => ok());
    const t = await run(make(d, { mode: "scripted" }), req());
    expect(d.calls).toBe(0);
    expect(t).toMatchObject({ decisionSource: "scripted", provider: null, chosenAction: "report", confidence: null, probabilities: null, latencyMs: null });
  });

  it.each([["auth", 401], ["credit", 402], ["invalid_request", 422], ["rate_limit", 429], ["overloaded", 529]] as const)(
    "%s becomes a labeled fallback with no fabricated confidence", async (kind, status) => {
      const d = fake("drex", async () => { throw new ProviderError(kind, "boom", { status }); });
      const t = await run(make(d), req());
      expect(d.calls).toBe(1); // the scheduler itself never retries
      expect(t).toMatchObject({ status: "ERROR", decisionSource: "fallback", chosenAction: "ignore", errorType: kind, confidence: null, probabilities: null, providerChoice: null });
    });

  it("unconfigured provider falls back without a call", async () => {
    const d = fake("drex", async () => ok(), false);
    const t = await run(make(d), req());
    expect(d.calls).toBe(0);
    expect(t).toMatchObject({ decisionSource: "fallback", errorType: "unconfigured", provider: null });
  });

  it("times out at the deadline, labels TIMEOUT, and discards the late answer", async () => {
    let resolve!: (r: ProviderResult) => void;
    const d = fake("drex", () => new Promise<ProviderResult>((r) => { resolve = r; }));
    const done = vi.fn();
    const s = make(d);
    s.request(req(), done);
    await vi.advanceTimersByTimeAsync(RULES.decisions.deadlineMs.drex + 1);
    expect(done).toHaveBeenCalledTimes(1);
    expect(done.mock.calls[0]![0]).toMatchObject({ status: "TIMEOUT", decisionSource: "fallback", errorType: "timeout", chosenAction: "ignore", confidence: null });
    resolve(ok("report"));
    await vi.advanceTimersByTimeAsync(10);
    expect(done).toHaveBeenCalledTimes(1);
    expect(done.mock.calls[0]![0].chosenAction).toBe("ignore");
  });

  it("rejects a label that was not offered", async () => {
    const t = await run(make(fake("drex", async () => ok("teleport"))), req());
    expect(t).toMatchObject({ decisionSource: "fallback", errorType: "invalid_response", chosenAction: "ignore", providerChoice: null });
  });

  it("low confidence applies the safe action but keeps the real provider numbers", async () => {
    // Below RULES.decisions.lowConfidenceThreshold (0.1): Drex's top two labels are a near coin-flip.
    const t = await run(make(fake("drex", async () => ok("report", 0.05))), req());
    expect(t).toMatchObject({ decisionSource: "drex_low_confidence_guardrail", chosenAction: "ignore", providerChoice: "report", confidence: 0.05, status: "RESOLVED" });
  });

  it("a clear Drex margin above the threshold is applied as Drex's own choice", async () => {
    const t = await run(make(fake("drex", async () => ok("report", 0.22))), req());
    expect(t).toMatchObject({ decisionSource: "drex", chosenAction: "report", providerChoice: "report", confidence: 0.22 });
  });

  it("never lets a credential echoed in a provider error reach the trace", async () => {
    const leaky = new ProviderError("auth", "Invalid API key nace_sk_ABCdef123456789_xyz for Bearer LLM|2700000000000000|SeCrEt_value-1", { status: 401 });
    const t = await run(make(fake("drex", async () => { throw leaky; })), req());
    expect(t.decisionSource).toBe("fallback");
    expect(t.errorMessage).not.toMatch(/nace_sk_ABC|SeCrEt_value|2700000000000000/);
    expect(t.errorMessage).toContain("[redacted");
    expect(t.confidence).toBeNull();
    expect(t.probabilities).toBeNull();
  });

  it("caps concurrency under a burst and drains the queue", async () => {
    let inFlight = 0, peak = 0;
    const d = fake("drex", async () => { inFlight += 1; peak = Math.max(peak, inFlight); await new Promise((r) => setTimeout(r, 50)); inFlight -= 1; return ok(); });
    const s = make(d, { concurrency: 2 });
    const done = vi.fn();
    for (let i = 0; i < 10; i++) s.request(req(), done);
    await vi.advanceTimersByTimeAsync(1000);
    expect(peak).toBe(2);
    expect(done).toHaveBeenCalledTimes(10);
    expect(s.pending()).toBe(0);
  });

  it("drops when the queue is full", () => {
    const s = make(fake("drex", never), { concurrency: 1, maxQueue: 2 });
    const results = Array.from({ length: 6 }, () => s.request(req(), () => {}));
    expect(results.filter((r) => r === null).length).toBeGreaterThan(0);
  });

  it("enforces the per-minute budget with a labeled fallback", async () => {
    const d = fake("drex", async () => ok());
    const s = make(d, { maxCallsPerMinute: 2 });
    const out: DecisionTrace[] = [];
    for (let i = 0; i < 3; i++) s.request(req(), (t) => out.push(t));
    await vi.advanceTimersByTimeAsync(5);
    expect(d.calls).toBe(2);
    expect(out.filter((t) => t.errorType === "budget_exceeded" && t.decisionSource === "fallback")).toHaveLength(1);
  });

  it("de-duplicates by dedupeKey while pending", () => {
    const s = make(fake("drex", never));
    expect(s.request(req({ dedupeKey: "same" }), () => {})).not.toBeNull();
    expect(s.request(req({ dedupeKey: "same" }), () => {})).toBeNull();
  });

  it("cancelAll drops queued and in-flight work", async () => {
    let resolve!: (r: ProviderResult) => void;
    const s = make(fake("drex", () => new Promise<ProviderResult>((r) => { resolve = r; })));
    const done = vi.fn();
    s.request(req(), done);
    await vi.advanceTimersByTimeAsync(0); // the provider call starts on a microtask, never inside request()
    s.cancelAll();
    resolve(ok());
    await vi.advanceTimersByTimeAsync(RULES.decisions.deadlineMs.drex + 10);
    expect(done).not.toHaveBeenCalled();
    expect(s.pending()).toBe(0);
  });

  it("cancelAll before the call has started never reaches the provider", async () => {
    const d = fake("drex", async () => ok());
    const s = make(d);
    const done = vi.fn();
    s.request(req(), done);
    s.cancelAll();
    await vi.advanceTimersByTimeAsync(RULES.decisions.deadlineMs.drex + 10);
    expect(d.calls).toBe(0);
    expect(done).not.toHaveBeenCalled();
    expect(s.pending()).toBe(0);
  });

  it("opens the circuit after repeated failures and reports FALLBACK", async () => {
    const d = fake("drex", async () => { throw new ProviderError("server", "500", { status: 500 }); });
    const s = make(d);
    for (let i = 0; i < 3; i++) await run(s, req(), 1);
    const t = await run(s, req(), 1);
    expect(t.errorType).toBe("circuit_open");
    expect(d.calls).toBe(3);
    expect(s.status().badge).toBe("DREX: FALLBACK");
    expect(s.status().drex.state).toBe("DOWN");
    await vi.advanceTimersByTimeAsync(RULES.decisions.circuitBreakerOpenMs + 1);
    expect(s.status().drex.circuitOpenUntilMs).toBeNull();
  });

  it("meta mode reports null probabilities, never invented ones", async () => {
    const s = createScheduler({ drex: fake("drex", never), meta: fake("meta", async () => ({ ...ok("gossip", null), provider: "meta" })), clock, mode: "meta", concurrency: 2, maxCallsPerMinute: 10 });
    const t = await run(s as never, req(), 1);
    expect(t).toMatchObject({ decisionSource: "meta", provider: "meta", chosenAction: "gossip", confidence: null, probabilities: null });
    expect(s.status().badge).toBe("META: LIVE");
  });

  it("flat config with empty keys constructs and falls back", async () => {
    const s = createScheduler({ roomId: "exp_1", mode: "drex", clock, naceApiKey: "", naceModel: "drex-latest", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 5 });
    const t = await run(s as never, req(), 1);
    expect(t).toMatchObject({ decisionSource: "fallback", errorType: "unconfigured" });
  });
});

describe("providers (injected fetch, no network)", () => {
  beforeEach(() => { vi.useRealTimers(); });
  const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
  const call = { question: "q", options: { report: "a", ignore: "b" }, state: { a: 1 } };

  it.each([[401, "auth"], [402, "credit"], [422, "invalid_request"]] as const)("drex %i is %s and is not retried", async (status, kind) => {
    const f = vi.fn(async () => json(status, { error: { type: "x", message: "no" }, request_id: "req_1" }));
    const p = createDrexProvider({ apiKey: "nace_sk_test", fetch: f as never });
    await expect(p.decide(call, { timeoutMs: 2000 })).rejects.toMatchObject({ kind });
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("drex parses a real-shaped 200", async () => {
    const f = vi.fn(async () => json(200, { model: "drex-max-v1.1", answers: { next_action: { type: "choice", choice: "report", confidence: 0.79, probabilities: { report: 0.86, ignore: 0.14 } } }, usage: { input_tokens: 10, output_tokens: 5 }, evaluation_time_ms: 120, request_id: "req_abc" }));
    const r = await createDrexProvider({ apiKey: "nace_sk_test", fetch: f as never }).decide(call, { timeoutMs: 2000 });
    expect(r).toMatchObject({ provider: "drex", model: "drex-max-v1.1", choice: "report", confidence: 0.79, requestId: "req_abc", providerEvalMs: 120, attempts: 1 });
  });

  it("drex rejects an unoffered label", async () => {
    const f = vi.fn(async () => json(200, { model: "m", answers: { next_action: { type: "choice", choice: "fly", confidence: 1, probabilities: {} } }, usage: {}, evaluation_time_ms: 1, request_id: "r" }));
    await expect(createDrexProvider({ apiKey: "nace_sk_test", fetch: f as never }).decide(call, { timeoutMs: 2000 })).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("drex without a key throws unconfigured instead of crashing", async () => {
    const p = createDrexProvider({ apiKey: "" });
    expect(p.isConfigured()).toBe(false);
    await expect(p.decide(call, { timeoutMs: 100 })).rejects.toMatchObject({ kind: "unconfigured" });
  });

  it("meta parses a label and reports null probabilities; null content is a failure", async () => {
    const good = vi.fn(async () => json(200, { model: "muse-ember-1.0", choices: [{ finish_reason: "stop", message: { content: '{"choice":"ignore"}' } }], usage: { prompt_tokens: 3, completion_tokens: 4 } }, { "x-request-id": "uuid-1" }));
    const r = await createMetaProvider({ apiKey: "k", fetch: good as never }).decide(call, { timeoutMs: 2000 });
    expect(r).toMatchObject({ provider: "meta", choice: "ignore", confidence: null, probabilities: null, providerEvalMs: null, requestId: "uuid-1" });
    const bad = vi.fn(async () => json(200, { choices: [{ finish_reason: "length", message: { content: null } }] }));
    await expect(createMetaProvider({ apiKey: "k", fetch: bad as never }).decide(call, { timeoutMs: 2000 })).rejects.toMatchObject({ kind: "invalid_response" });
    const r429 = vi.fn(async () => json(429, { error: { message: "slow" } }, { "retry-after": "2" }));
    await expect(createMetaProvider({ apiKey: "k", fetch: r429 as never }).decide(call, { timeoutMs: 2000 })).rejects.toMatchObject({ kind: "rate_limit" });
  });

  it("meta times out at timeoutMs", async () => {
    const slow = ((_: unknown, init: RequestInit) => new Promise((_, rej) => init.signal!.addEventListener("abort", () => rej(new Error("aborted"))))) as never;
    await expect(createMetaProvider({ apiKey: "k", fetch: slow }).decide(call, { timeoutMs: 30 })).rejects.toMatchObject({ kind: "timeout" });
  });
});

// Audit regressions (2026-11-14): the scheduler must never finish a decision inside request(), must restart its
// queue whenever a slot frees up, must not claim LIVE for decisions the provider did not make, and must judge
// shadow agreement whichever model answers first.
describe("scheduler audit regressions", () => {
  const hung: DecisionProvider["decide"] = (_c, o) => new Promise((_res, rej) => {
    o.signal?.addEventListener("abort", () => rej(new ProviderError("aborted", "aborted by deadline")));
  });

  it("never calls onDone inside request() when the budget is spent", async () => {
    const s = make(fake("drex", async () => ok()), { maxCallsPerMinute: 1 });
    await run(s, req());
    let returned = false, calledInside = false, final: DecisionTrace | undefined;
    const trace = s.request(req(), (t) => { calledInside = !returned; final = t; });
    returned = true;
    expect(trace?.status).toBe("REQUESTED");
    await vi.advanceTimersByTimeAsync(0);
    expect(calledInside).toBe(false);
    expect(final).toMatchObject({ decisionSource: "fallback", errorType: "budget_exceeded", chosenAction: "ignore", confidence: null });
  });

  it("never calls onDone inside request() while the circuit breaker is open", async () => {
    const s = make(fake("drex", async () => { throw new ProviderError("auth", "bad key", { status: 401 }); }));
    await run(s, req());
    let returned = false, calledInside = false, final: DecisionTrace | undefined;
    const trace = s.request(req(), (t) => { calledInside = !returned; final = t; });
    returned = true;
    expect(trace?.status).toBe("REQUESTED");
    await vi.advanceTimersByTimeAsync(0);
    expect(calledInside).toBe(false);
    expect(final).toMatchObject({ decisionSource: "fallback", errorType: "circuit_open" });
  });

  it("a slot freed by a timed-out call restarts the queue", async () => {
    let first = true;
    const d = fake("drex", (c, o) => { if (first) { first = false; return hung(c, o); } return Promise.resolve(ok()); });
    const s = make(d, { concurrency: 1 });
    const out: DecisionTrace[] = [];
    s.request(req(), (t) => out.push(t));
    await vi.advanceTimersByTimeAsync(RULES.decisions.deadlineMs.drex - 1000);
    s.request(req(), (t) => out.push(t)); // queued behind the hung call, with 3 s of its own deadline left
    await vi.advanceTimersByTimeAsync(1200); // the first call hits its deadline and is aborted
    expect(out.map((t) => t.decisionSource)).toEqual(["fallback", "drex"]);
    expect(d.calls).toBe(2);
  });

  it("the badge is PENDING before the first answer, LIVE after a real answer, FALLBACK after a budget fallback", async () => {
    const s = make(fake("drex", async () => ok()), { maxCallsPerMinute: 1 });
    expect(s.status().badge).toBe("DREX: PENDING");
    await run(s, req());
    expect(s.status().badge).toBe("DREX: LIVE");
    await run(s, req());
    expect(s.status().badge).toBe("DREX: FALLBACK");
  });

  it("a wrong key never shows LIVE", async () => {
    const s = make(fake("drex", async () => { throw new ProviderError("auth", "bad key", { status: 401 }); }));
    await run(s, req());
    expect(s.status().badge).toBe("DREX: FALLBACK");
  });

  it("shadow agreement is judged even when Meta answers before Drex", async () => {
    const drex = fake("drex", () => new Promise<ProviderResult>((res) => setTimeout(() => res(ok("gossip")), 100)));
    const meta = fake("meta", async () => ({ ...ok("gossip", null), provider: "meta" as const }));
    const s = createScheduler({ drex, meta, clock, mode: "drex", concurrency: 4, maxCallsPerMinute: 100, shadowMeta: true } as never);
    const t = await run(s, req(), 200);
    expect(t.shadow).toMatchObject({ provider: "meta", chosenAction: "gossip", agrees: true });
  });

  it("a failing Meta shadow shows in Meta's health and opens its breaker instead of being called forever", async () => {
    const meta = fake("meta", async () => { throw new ProviderError("auth", "bad meta key", { status: 401 }); });
    const s = createScheduler({ drex: fake("drex", async () => ok()), meta, clock, mode: "drex", concurrency: 4, maxCallsPerMinute: 100, shadowMeta: true } as never);
    for (let i = 0; i < 4; i++) await run(s, req(), 10);
    expect(meta.calls).toBe(1);
    expect(s.status().meta.state).toBe("DOWN");
    expect(s.status().badge).toBe("DREX: LIVE");
  });

  it("an inherited object key is never accepted as an offered label", async () => {
    const t = await run(make(fake("drex", async () => ok("constructor"))), req());
    expect(t).toMatchObject({ decisionSource: "fallback", errorType: "invalid_response", chosenAction: "ignore", providerChoice: null });
  });
});
