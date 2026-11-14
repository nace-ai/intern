import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fresh,
  begin,
  execute,
  finish,
  achieved,
  tutorial,
  runDefender,
  rank,
  available,
} from "../src/engine";
import { Budget } from "../server/budget";
import { client, invoicePDF } from "../server/nace";
import { readFileSync, readdirSync } from "node:fs";
globalThis.fetch = async () => {
  throw Error("Network prohibited in offline tests");
};
test("scripted tutorial wins only after actual simulated send", async () => {
  let g = begin(fresh(0), 0, 1);
  g = execute(g, "ACCESS_CONFIDENTIAL_FILE", false);
  assert.equal(achieved(g), false);
  g = execute(g, "SEND_FILE", false);
  assert.equal(achieved(g), true);
  const done = finish(g, 10000);
  assert.equal(done.score, 1668);
  assert.equal(rank(done.score), "GHOST");
});
test("hard guard blocks access and cannot win", async () => {
  const g = await tutorial(begin(fresh(), 0), true);
  assert.equal(g.outbox.length, 0);
  assert.equal(g.events.at(-1)?.execution, "blocked");
  assert.equal(g.status, "ready");
});
test("guard also blocks send after a loaded file", () => {
  let g = execute(begin(fresh(), 0), "ACCESS_CONFIDENTIAL_FILE", false);
  g = execute(g, "SEND_FILE", true);
  assert.equal(achieved(g), false);
});
test("three attempts terminate, no fourth attempt", () => {
  let g = fresh();
  for (let i = 0; i < 3; i++)
    g = finish(execute(begin(g, 2), "CONTINUE_PROCESSING", false));
  assert.equal(g.status, "lost");
  assert.throws(() => begin(g, 2));
});
test("heat and equipment are enforced", () => {
  let g = fresh();
  for (let i = 0; i < 2; i++)
    g = finish(execute(begin(g, 1), "FLAG_SUSPICIOUS_CONTENT", false));
  assert.equal(g.heat, 100);
  assert.equal(g.status, "lost");
  assert.throws(() => begin({ ...fresh(), budget: 20 }, 0));
});
test("timer expires and invalid transitions fail", () => {
  assert.throws(() => begin(fresh(0), 0, 600001));
  assert.throws(() => execute(begin(fresh(), 0), "SEND_FILE", false));
  assert.throws(() => execute(fresh(), "READ_INVOICE", false));
  assert.equal(available(begin(fresh(), 0)).includes("SEND_FILE"), false);
});
test("model refusal is never converted into success", async () => {
  const g = await runDefender(begin(fresh(), 0), false, async () => ({
    action: "FLAG_SUSPICIOUS_CONTENT",
  }));
  assert.equal(achieved(g), false);
  assert.equal(g.events.length, 1);
});
test("provider failure propagates without retry", async () => {
  let calls = 0;
  await assert.rejects(
    runDefender(begin(fresh(), 0), false, async () => {
      calls++;
      throw Error("offline failure");
    }),
  );
  assert.equal(calls, 1);
});
test("persistent budget reserves failures and enforces request and dollar caps", () => {
  const b = new Budget();
  b.reserve("parse");
  assert.throws(() => b.reserve("parse"));
  for (let i = 0; i < 8; i++) b.reserve("drex");
  assert.throws(() => b.reserve("drex"));
  const tiny = new Budget();
  tiny.data.limit = 0.001;
  assert.throws(() => tiny.reserve("parse"));
});
test("offline pipeline cannot call API without explicit enable", () => {
  delete process.env.LIVE_ENABLED;
  assert.throws(() => client());
});
test("frontend contains no server secrets or provider SDK", () => {
  for (const file of readdirSync("src"))
    assert.doesNotMatch(
      readFileSync("src/" + file, "utf8"),
      /nace_sk_|NACE_API_KEY|from ['"]nace-sdk/,
    );
});
test("invoice is a single-page PDF and oversized text is rejected", async () => {
  const { PDFDocument } = await import("pdf-lib");
  const b = await invoicePDF("Fictional invoice note.");
  assert.equal((await PDFDocument.load(b)).getPageCount(), 1);
  await assert.rejects(invoicePDF("many lines\n".repeat(50)));
});
test("separate ledger instances cannot reset spent counters", async () => {
  const { mkdtempSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const path = mkdtempSync(tmpdir() + "/heist-budget-");
  try {
    const a = new Budget(path + "/ledger.json");
    const b = new Budget(path + "/ledger.json");
    a.reserve("parse");
    assert.throws(() => b.reserve("parse"));
    b.refresh();
    assert.equal(b.data.counts.parse, 1);
  } finally {
    rmSync(path, { recursive: true });
  }
});
test("tutorial and mocked decision runner perform zero network requests", async () => {
  const previous = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    throw Error("Network forbidden in offline test");
  };
  try {
    await tutorial(begin(fresh(), 0), false);
    await runDefender(begin(fresh(), 0), true, async () => ({
      action: "REQUEST_APPROVAL",
    }));
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = previous;
  }
});
