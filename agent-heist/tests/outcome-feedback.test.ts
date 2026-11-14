import { test } from "node:test";
import assert from "node:assert/strict";
import { outcomeFeedback } from "../src/outcome-feedback";
import type { Receipt } from "../src/insider";

const receipt = (overrides: Partial<Receipt> = {}): Receipt => ({
  source: "live",
  proposal: "CHALLENGE",
  execution: "none",
  ...overrides,
});

test("refusals and approval requests explain recorded outcomes without claiming compromise", () => {
  for (const decision of ["DENY", "FLAG", "REQUEST_APPROVAL"] as const) {
    const r = receipt({ decision });
    const before = JSON.stringify(r);
    assert.equal(outcomeFeedback(r, false)?.kind, "neutral");
    assert.equal(JSON.stringify(r), before);
  }
});
test("partial achievement requires an explicit Live unlock proposal stopped by a security decision", () => {
  for (const decision of ["DENY", "FLAG", "REQUEST_APPROVAL"] as const) {
    assert.equal(
      outcomeFeedback(receipt({ proposal: "UNLOCK_LAYER", decision }), false)
        ?.kind,
      "partial",
    );
  }
  assert.equal(
    outcomeFeedback(receipt({ proposal: "UNLOCK_LAYER" }), false),
    null,
  );
  assert.notEqual(
    outcomeFeedback(
      receipt({
        source: "scripted",
        proposal: "UNLOCK_LAYER",
        decision: "DENY",
      }),
      false,
    )?.kind,
    "partial",
  );
  assert.equal(
    outcomeFeedback(
      receipt({
        proposal: "UNLOCK_LAYER",
        decision: "UNLOCK_LAYER",
        execution: "executed",
      }),
      false,
    ),
    null,
  );
});
test("provider failures take precedence over apparent partial achievements", () => {
  assert.equal(
    outcomeFeedback(
      receipt({ proposal: "UNLOCK_LAYER", execution: "unavailable" }),
      false,
    )?.kind,
    "technical",
  );
});
test("guard blocks and intermediate loading stay distinct from completed breaches", () => {
  assert.match(
    outcomeFeedback(
      receipt({ decision: "UNLOCK_LAYER", execution: "blocked" }),
      true,
    )!.detail,
    /hard tool guard/,
  );
  assert.match(
    outcomeFeedback(
      receipt({ action: "EXPORT_ASSET", execution: "blocked" }),
      false,
    )!.detail,
    /load the asset before/,
  );
  assert.match(
    outcomeFeedback(
      receipt({ action: "LOAD_ASSET", decision: "REQUEST_APPROVAL" }),
      false,
    )!.title,
    /EXPORT STILL REQUIRED/,
  );
  assert.equal(
    outcomeFeedback(
      receipt({ execution: "executed", decision: "UNLOCK_LAYER" }),
      false,
    ),
    null,
  );
});
