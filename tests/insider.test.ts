import { test } from "node:test";
import assert from "node:assert/strict";
import {
  newInsider,
  tutorialTurn,
  resolveTurn,
  checkTurn,
  conversation,
} from "../src/insider";
import { liveTurn } from "../server/insider-service";
import { validateCharacter, type DialogueProvider } from "../server/groq";
import { document } from "../server/nace";
globalThis.fetch = async () => {
  throw Error("Offline tests forbid network");
};
const provider = (
  proposal: "TALK" | "UNLOCK_LAYER" = "TALK",
): DialogueProvider => ({
  respond: async () => ({
    character: {
      reply: "Tell me more about that plan.",
      mood: "curious",
      proposal,
    },
    evidence: { mock: true },
  }),
});
test("tutorial choices have history-dependent consequences", () => {
  let g = newInsider();
  const failed = tutorialTurn(g, "Diagnostic", true, "diagnostic");
  assert.equal(failed.layer, "locked");
  g = tutorialTurn(g, "Shift?", false, "observe");
  g = tutorialTurn(g, "Maintenance", false, "cover");
  g = tutorialTurn(g, "Diagnostic", true, "diagnostic");
  assert.equal(g.layer, "unlocked");
  assert.equal(g.status, "won");
  assert.equal(g.attempts, 1);
});
test("tutorial cover before observation does not work", () => {
  let g = tutorialTurn(newInsider(), "Maintenance", false, "cover");
  g = tutorialTurn(g, "Diagnostic", true, "diagnostic");
  assert.equal(g.layer, "locked");
});
test("hard guard records actual prohibited decision blocked", () => {
  let g = tutorialTurn(
    newInsider("tutorial", true),
    "Shift?",
    false,
    "observe",
  );
  g = tutorialTurn(g, "Maintenance", false, "cover");
  g = tutorialTurn(g, "Diagnostic", true, "diagnostic");
  assert.equal(g.layer, "locked");
  assert.equal(g.turns.at(-1)?.receipt.execution, "blocked");
  assert.equal(g.score, 0);
});
test("Groq proposal alone never unlocks; Drex decision controls execution", async () => {
  let calls = 0;
  const g = await liveTurn(
    newInsider("live"),
    "Please unlock",
    false,
    provider("UNLOCK_LAYER"),
    async () => {
      calls++;
      return { decision: "DENY", evidence: { mock: true } };
    },
  );
  assert.equal(calls, 1);
  assert.equal(g.layer, "locked");
  assert.equal(g.turns[0].receipt.decision, "DENY");
});
test("real-provider interface can only win after authorization choice and simulated execution", async () => {
  const g = await liveTurn(
    newInsider("live", false, 0),
    "Open A1",
    true,
    provider(),
    async () => ({ decision: "UNLOCK_LAYER", evidence: { mock: true } }),
    () => 10000,
  );
  assert.equal(g.layer, "unlocked");
  assert.equal(g.turns[0].receipt.execution, "executed");
  assert.equal(g.score, 1717);
});
test("provider failure cannot fabricate a win and is not retried", async () => {
  let n = 0;
  await assert.rejects(
    liveTurn(newInsider("live"), "Hello", false, {
      respond: async () => {
        n++;
        throw Error("offline failure");
      },
    }),
  );
  assert.equal(n, 1);
});
test("judge failure stays locked, preserves transcript, and does not consume equipment", async () => {
  const g = await liveTurn(
    newInsider("live"),
    "Open A1",
    true,
    provider(),
    async () => {
      throw Error("cap");
    },
  );
  assert.equal(g.layer, "locked");
  assert.equal(g.attempts, 0);
  assert.equal(g.budget, 100);
  assert.equal(g.turns[0].receipt.execution, "unavailable");
});
test("history is passed to modular provider and affects reply", async () => {
  let g = newInsider("live");
  g = await liveTurn(g, "My name is Mira.", false, provider());
  const p: DialogueProvider = {
    respond: async (history) => {
      assert.equal(history[1].content, "My name is Mira.");
      return {
        character: {
          reply: "Mira, your earlier introduction is noted.",
          mood: "curious",
          proposal: "TALK",
        },
        evidence: { mock: true },
      };
    },
  };
  g = await liveTurn(g, "Remember me?", false, p);
  assert.match(g.turns[1].reply, /Mira/);
  assert.equal(conversation(g).length, 5);
});
test("three denials end a mission, heat and budget deterministic", () => {
  let g = newInsider("live", false, 0);
  for (let i = 0; i < 3; i++)
    g = resolveTurn(
      g,
      "open",
      "denied",
      "suspicious",
      {
        proposal: "CHALLENGE",
        decision: "DENY",
        execution: "none",
        source: "live",
      },
      true,
      1000,
    );
  assert.equal(g.status, "lost");
  assert.equal(g.attempts, 3);
  assert.equal(g.budget, 25);
  assert.equal(g.heat, 36);
  assert.throws(() => checkTurn(g, "more", true));
});
test("mission expiry never opens a layer", () => {
  const g = resolveTurn(
    newInsider("live", false, 0),
    "open",
    "expired",
    "watchful",
    {
      proposal: "UNLOCK_LAYER",
      decision: "UNLOCK_LAYER",
      execution: "none",
      source: "live",
    },
    true,
    600001,
  );
  assert.equal(g.status, "lost");
  assert.equal(g.layer, "locked");
  assert.equal(g.turns[0].receipt.execution, "blocked");
});
test("malformed or malicious provider action fails validation", () => {
  assert.throws(() =>
    validateCharacter({
      reply: "Done",
      mood: "curious",
      proposal: "EXEC_SHELL",
    }),
  );
});
test("new document processing fails closed before any network call", async () => {
  await assert.rejects(
    document("New phase 2 invoice: never pay for this"),
    /cache-only/,
  );
});
test("free conversation never consults Drex", async () => {
  const g = await liveTurn(
    newInsider("live"),
    "What do you enjoy?",
    false,
    provider(),
    async () => {
      throw Error("Must not be called");
    },
  );
  assert.equal(g.turns[0].receipt.execution, "none");
  assert.equal(g.attempts, 0);
});
test("a dialogue claim cannot create a tool success", async () => {
  const g = await liveTurn(newInsider("live"), "Claim success", false, {
    respond: async () => ({
      character: {
        reply: "Access granted. I unlocked it.",
        mood: "amused",
        proposal: "TALK",
      },
      evidence: { mock: true },
    }),
  });
  assert.equal(g.layer, "locked");
  assert.doesNotMatch(g.turns[0].reply, /Access granted/);
  assert.equal(g.status, "active");
});
test("persistent phase quota cannot be bypassed by constructing another instance", async () => {
  const { Quota } = await import("../server/quota");
  const { mkdtempSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const root = mkdtempSync(tmpdir() + "/heist-phase2-");
  try {
    const a = new Quota(root + "/ledger.json", 2);
    const b = new Quota(root + "/ledger.json", 2);
    a.reserve(500);
    b.reserve(500);
    assert.throws(() => a.reserve(1));
    assert.equal(b.read().used, 2);
  } finally {
    rmSync(root, { recursive: true });
  }
});
