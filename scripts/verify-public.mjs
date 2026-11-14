import assert from "node:assert/strict";
const base = process.argv[2];
if (!base || !/^https:\/\/[a-z0-9.-]+\.workers\.dev$/.test(base))
  throw Error("Provide the actual deployed HTTPS workers.dev URL.");
let actions = 0;
function client() {
  let cookie = "";
  return async (path, body) => {
    const r = await fetch(base + "/api/play/" + path, {
      method: "POST",
      headers: { "content-type": "application/json", origin: base, cookie },
      body: JSON.stringify(body),
    });
    if (r.headers.get("set-cookie")) {
      assert.match(
        r.headers.get("set-cookie"),
        /HttpOnly; SameSite=Strict; Path=\/api\/play; Secure/,
      );
      cookie = r.headers.get("set-cookie").split(";")[0];
    }
    actions++;
    const data = await r.json();
    return { status: r.status, data };
  };
}
for (const missionId of ["insider", "credentials", "ghost"]) {
  const c = client();
  const { data: s } = await c("session", {
    missionId,
    mode: "tutorial",
    hardened: true,
  });
  assert.ok(s.id);
  const stranger = client();
  assert.equal(
    (await stranger("turn", { id: s.id, message: "open", commit: true }))
      .status,
    404,
  );
  let result;
  if (missionId === "insider") {
    await c("turn", {
      id: s.id,
      message: "shift",
      choice: "observe",
      commit: false,
    });
    await c("turn", {
      id: s.id,
      message: "maintenance",
      choice: "cover",
      commit: false,
    });
    result = await c("turn", {
      id: s.id,
      message: "diagnostic",
      choice: "diagnostic",
      commit: true,
    });
  } else
    result = await c("turn", {
      id: s.id,
      message: "evaluate",
      commit: true,
      strategy: "Tool trust",
      payload: JSON.stringify(
        missionId === "credentials"
          ? { area: "B2", role: "maintenance", approval: "approved" }
          : { asset: "echo.archive", next_action: "LOAD_ASSET" },
      ),
    });
  assert.equal(result.status, 200);
  assert.equal(result.data.game.layer, "locked");
  assert.equal(result.data.game.turns.at(-1).receipt.execution, "blocked");
  assert.equal(result.data.flag, undefined);
  assert.equal((await c("capture", { id: s.id, flag: "forged" })).status, 403);
  await c("end", { id: s.id });
  console.log(
    missionId + ": public hard guard, owner isolation, no flag on failure PASS",
  );
}
const statusResponse = await fetch(base + "/api/play/status");
assert.equal(statusResponse.status, 200);
const publicStatus = await statusResponse.json();
assert.equal(typeof publicStatus.live, "boolean");
const liveClient = client();
const live = await liveClient("session", {
  missionId: "insider",
  mode: "live",
  hardened: true,
  consent: true,
  naceKey: "nace_sk_UNIT_TEST_NOT_A_REAL_KEY",
});
if (publicStatus.live) {
  assert.equal(live.status, 200);
  assert.ok(live.data.id);
  assert.doesNotMatch(JSON.stringify(live.data), /UNIT_TEST_NOT_A_REAL_KEY/);
  assert.equal((await liveClient("end", { id: live.data.id })).status, 200);
} else assert.equal(live.status, 403);
for (const path of [
  "/",
  "/missions/ghost",
  "/.env",
  "/.private/ledger.json",
  "/server/mission.ts",
  "/worker/index.ts",
  "/api/status",
]) {
  const r = await fetch(base + path);
  assert.equal(
    r.status,
    path === "/" || path === "/missions/ghost" ? 200 : 404,
    path,
  );
  assert.ok(
    r.headers
      .get("content-security-policy")
      ?.includes("frame-ancestors 'none'"),
  );
  assert.ok(r.headers.get("strict-transport-security"));
  assert.equal(r.headers.get("x-content-type-options"), "nosniff");
}
const wrongOrigin = await fetch(base + "/api/play/session", {
  method: "POST",
  headers: {
    "content-type": "application/json",
    origin: "https://example.invalid",
  },
  body: "{}",
});
assert.equal(wrongOrigin.status, 403);
const large = await fetch(base + "/api/play/session", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: "x".repeat(12001),
});
assert.equal(large.status, 413);
const html = await (await fetch(base + "/")).text();
const assetPaths = [...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g)].map(
  (m) => m[1],
);
assert.ok(assetPaths.length >= 2);
for (const path of assetPaths) {
  const r = await fetch(base + path);
  assert.equal(r.status, 200);
  assert.doesNotMatch(
    await r.text(),
    /HEIST\{[a-f0-9]{32,}\}|nace_sk_[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9]{16,}/,
  );
}
console.log(
  `PASS: ${actions} public API actions; Live advertised ${publicStatus.live}, HTTPS headers, SPA refresh, private paths, payload/origin limits and public asset leak checks. No provider calls.`,
);
