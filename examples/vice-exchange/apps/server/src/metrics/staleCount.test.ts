// Audit finding (store and agents reviewers): metrics.decisions.stale was always 0 because the metrics looked for the
// word "stale" in reasons such as "subject was captured before the decision arrived".
import { describe, expect, it } from "vitest";
import type { DecisionTrace } from "@vice/contracts";
import { createMetrics } from "./index";

const trace = (id: string, over: Partial<DecisionTrace>): DecisionTrace => ({
  id, roomId: "r", runId: "run_001", agentId: "npc_t1", agentName: "T", role: "TRADER", status: "APPLIED", mode: "drex",
  decisionSource: "drex", provider: "drex", model: "m", question: "q", offeredActions: { buy: "b", hold: "h" }, chosenAction: "buy",
  providerChoice: "buy", confidence: 0.9, probabilities: { buy: 0.9, hold: 0.1 }, providerRequestId: "req", latencyMs: 300, providerEvalMs: 120,
  usage: null, attempts: 1, errorType: null, errorMessage: null, rejectReason: null, stateSnapshot: {}, stateHash: "h", triggerEventId: "evt_1",
  correlationId: "story_1", effectEventIds: [], requestedAtMs: 1, resolvedAtMs: 2, requestedAtSequence: 1, citations: [], shadow: null, ...over,
});

describe("metrics: stale decisions (FR-025)", () => {
  it("counts a decision that arrived after the world moved on as stale, and an unoffered label as rejected only", () => {
    const m = createMetrics();
    m.reset("run_001", "drex", 42, 0);
    m.onDecision(trace("dec_1", {}));
    m.onDecision(trace("dec_2", { status: "REJECTED", rejectReason: "subject was captured before the decision arrived" }));
    m.onDecision(trace("dec_3", { status: "REJECTED", rejectReason: "listing is halted" }));
    m.onDecision(trace("dec_4", { status: "REJECTED", rejectReason: 'action "teleport" was not offered' }));
    const d = m.view().decisions;
    expect(d.rejected).toBe(3);
    expect(d.stale).toBe(2);
  });
});
