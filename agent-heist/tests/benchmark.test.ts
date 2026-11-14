import { test } from "node:test";
import assert from "node:assert/strict";
import cases from "../benchmarks/cases.json";
import { compare, collect } from "../benchmarks/run";
test("benchmark compares identical labeled sets, keeps missing metrics null and labels mocks", async () => {
  const rows = await collect("fixture", async (input) => ({
    source: "mock",
    decision: cases.find((c) => c.policy === input.policy)!.acceptable[0],
  }));
  assert.deepEqual(compare(rows), [
    {
      provider: "mock:fixture",
      cases: 8,
      accuracy: 1,
      latencyMeanMs: null,
      latencyP95Ms: null,
      costUsd: null,
    },
  ]);
  assert.throws(() => compare(rows.slice(1)), /identical/);
  assert.throws(() => compare([...rows, rows[0]]), /Duplicate/);
});
