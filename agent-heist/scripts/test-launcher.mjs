import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, readFileSync, writeFileSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
const root = realpathSync(join(dirname(fileURLToPath(import.meta.url)), ".."));
const record = join(root, ".runtime/state.json");
const invoke = (name, args = []) =>
  new Promise((resolve, reject) => {
    const child = spawn(join(root, name), args, {
      cwd: tmpdir(),
      env: { ...process.env, HEIST_OFFLINE: "false", LIVE_ENABLED: "true" },
    });
    let output = "";
    child.stdout.on("data", (b) => (output += b));
    child.stderr.on("data", (b) => (output += b));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, output }));
  });
let dummy;
try {
  let r = await invoke("start.sh");
  assert.equal(r.code, 0, r.output);
  const initial = JSON.parse(readFileSync(record));
  assert.equal(initial.mode, "offline");
  const status = await fetch("http://127.0.0.1:5173/api/play/status");
  assert.equal(status.status, 200);
  assert.equal((await status.json()).live, false);
  r = await invoke("start.sh");
  assert.equal(r.code, 0, r.output);
  assert.match(r.output, /already running/);
  assert.equal(JSON.parse(readFileSync(record)).pid, initial.pid);
  dummy = createServer((_req, res) => res.end("unrelated"));
  await new Promise((resolve) => dummy.listen(0, "127.0.0.1", resolve));
  const unrelated = `http://127.0.0.1:${dummy.address().port}`;
  r = await invoke("stop.sh");
  assert.equal(r.code, 0, r.output);
  assert.ok(!existsSync(record));
  assert.equal(await (await fetch(unrelated)).text(), "unrelated");
  for (const port of [5173, 8787])
    await assert.rejects(
      fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) }),
    );
  r = await invoke("stop.sh");
  assert.equal(r.code, 0, r.output);
  assert.match(r.output, /already stopped/);
  writeFileSync(
    record,
    JSON.stringify({
      root,
      pid: process.pid,
      port: dummy.address().port,
      token: "a".repeat(64),
      instance: "stale",
      mode: "offline",
    }),
    { mode: 0o600 },
  );
  r = await invoke("stop.sh");
  assert.equal(r.code, 0, r.output);
  assert.ok(!existsSync(record));
  assert.equal(await (await fetch(unrelated)).text(), "unrelated");
  await new Promise((resolve) => dummy.close(resolve));
  dummy = createServer((_req, res) => res.end("occupied"));
  await new Promise((resolve) => dummy.listen(5173, "127.0.0.1", resolve));
  r = await invoke("start.sh");
  assert.notEqual(r.code, 0);
  assert.match(r.output, /Port 5173 is occupied/);
  assert.equal(
    await (await fetch("http://127.0.0.1:5173/")).text(),
    "occupied",
  );
  await new Promise((resolve) => dummy.close(resolve));
  dummy = null;
  r = await invoke("start.sh");
  assert.equal(r.code, 0, r.output);
  r = await invoke("stop.sh");
  assert.equal(r.code, 0, r.output);
  console.log(
    "PASS: start from another directory, forced offline mode, duplicate prevention, stop, repeat stop, stale PID safety, unrelated-server survival, occupied-port error, restart.",
  );
} finally {
  if (dummy) await new Promise((resolve) => dummy.close(resolve));
  await invoke("stop.sh");
}
