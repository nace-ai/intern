import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import net from "node:net";
import { createHash, randomBytes } from "node:crypto";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  closeSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const root = realpathSync(join(dirname(fileURLToPath(import.meta.url)), ".."));
const runtime = join(root, ".runtime");
const statePath = join(runtime, "state.json");
const lock = join(runtime, "control.lock");
const logPath = join(runtime, "server.log");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const command = process.argv[2];
function state() {
  if (!existsSync(statePath)) return null;
  if (!lstatSync(statePath).isFile())
    throw Error(
      "Unsafe runtime state file. Inspect .runtime/state.json manually.",
    );
  try {
    const s = JSON.parse(readFileSync(statePath, "utf8"));
    if (s.root !== root)
      throw Error(
        "Runtime state belongs to a different checkout. Inspect .runtime/state.json manually.",
      );
    if (
      !Number.isInteger(s.port) ||
      s.port < 1 ||
      s.port > 65535 ||
      typeof s.token !== "string" ||
      !/^[a-f0-9]{64}$/.test(s.token)
    )
      return null;
    return s;
  } catch (error) {
    if (error.message.includes("different checkout")) throw error;
    return null;
  }
}
async function control(s, action = "status") {
  try {
    const response = await fetch(`http://127.0.0.1:${s.port}/${action}`, {
      method: action === "stop" ? "POST" : "GET",
      headers: { Authorization: `Bearer ${s.token}` },
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) return null;
    const data = await response.json();
    return data.root === root &&
      data.pid === s.pid &&
      data.instance === s.instance
      ? data
      : null;
  } catch {
    return null;
  }
}
function removeState(s) {
  if (!existsSync(statePath)) return;
  const current = state();
  if (!current || (s && current.instance === s.instance)) rmSync(statePath);
}
function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
}
async function withLock(fn) {
  mkdirSync(runtime, { recursive: true, mode: 0o700 });
  let owned = false;
  for (let n = 0; n < 150 && !owned; n++) {
    try {
      mkdirSync(lock, { mode: 0o700 });
      owned = true;
      writeFileSync(join(lock, "owner"), String(process.pid), { mode: 0o600 });
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
      let pid;
      try {
        pid = Number(readFileSync(join(lock, "owner"), "utf8"));
      } catch {}
      if (Number.isInteger(pid) && pid > 0 && !alive(pid)) {
        rmSync(join(lock, "owner"));
        try {
          rmSync(lock, { recursive: true });
        } catch {}
      } else await delay(200);
    }
  }
  if (!owned)
    throw Error(
      "Another start/stop is in progress. If it crashed, inspect .runtime/control.lock; no process was killed.",
    );
  try {
    return await fn();
  } finally {
    rmSync(lock, { recursive: true });
  }
}
async function checkPort(port) {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", () =>
      reject(
        Error(
          `Port ${port} is occupied or unavailable. Stop the app using that port, or use ./stop.sh for an Agent Heist launch. No unrelated process was stopped.`,
        ),
      ),
    );
    probe.listen(port, "127.0.0.1", () => probe.close(resolve));
  });
}
function dependencies() {
  const digest = createHash("sha256")
    .update(readFileSync(join(root, "package.json")))
    .update(readFileSync(join(root, "package-lock.json")))
    .update(
      `${process.versions.node.split(".")[0]}:${process.platform}:${process.arch}`,
    )
    .digest("hex");
  const marker = join(runtime, "dependencies.sha256");
  if (
    existsSync(marker) &&
    readFileSync(marker, "utf8") === digest &&
    [
      "tsx/dist/cli.mjs",
      "vite/bin/vite.js",
      "typescript/bin/tsc",
      "nace-sdk/package.json",
    ].every((p) => existsSync(join(root, "node_modules", p)))
  )
    return;
  console.log(
    "Installing locked dependencies with npm ci (first launch or changed dependencies)…",
  );
  const result = spawnSync("npm", ["ci", "--no-audit", "--no-fund"], {
    cwd: root,
    stdio: "inherit",
  });
  if (result.error || result.status !== 0)
    throw Error(
      "npm ci failed. Check the npm output and your network/registry access, then rerun ./start.sh.",
    );
  writeFileSync(marker, digest, { mode: 0o600 });
}
async function start(live) {
  await withLock(async () => {
    const old = state();
    if (old && (await control(old))) {
      console.log(
        `Agent Heist is already running (${old.mode}). http://127.0.0.1:5173\nUse ./stop.sh before changing modes.`,
      );
      return;
    }
    if (existsSync(statePath)) {
      removeState(old);
      console.log("Removed stale runtime state; no PID was signaled.");
    }
    await checkPort(5173);
    await checkPort(8787);
    dependencies();
    const log = openSync(logPath, "w", 0o600);
    const child = spawn(
      process.execPath,
      [fileURLToPath(import.meta.url), "supervise", live ? "live" : "offline"],
      {
        cwd: root,
        detached: true,
        stdio: ["ignore", log, log],
        env: {
          ...process.env,
          HEIST_OFFLINE: live ? "false" : "true",
          LIVE_ENABLED: "false",
        },
      },
    );
    closeSync(log);
    child.unref();
    let spawnError;
    child.once("error", (e) => {
      spawnError = e;
    });
    for (let n = 0; n < 120; n++) {
      if (spawnError)
        throw Error(
          "Could not start the supervisor. Inspect .runtime/server.log.",
        );
      const s = state();
      const status = s && (await control(s));
      if (status?.ready) {
        console.log(
          `Agent Heist started in ${live ? "Live-capable mode (explicit BYOK consent required)" : "safe offline Training mode (no provider calls)"}.\nWebsite: http://127.0.0.1:5173\nStop: ./stop.sh\nLogs: ${logPath}`,
        );
        return;
      }
      if (child.exitCode !== null || child.signalCode !== null) break;
      await delay(250);
    }
    const s = state();
    if (s) await control(s, "stop");
    throw Error(
      "The app did not become ready. Inspect .runtime/server.log for startup errors. No unrelated process was stopped.",
    );
  });
}
async function stop() {
  await withLock(async () => {
    const s = state();
    if (!s || !(await control(s))) {
      removeState(s);
      console.log(
        "Agent Heist is already stopped. Stale runtime state removed if present; no PID was signaled.",
      );
      return;
    }
    if (!(await control(s, "stop")))
      throw Error(
        "Supervisor could not confirm shutdown. No external PID was signaled.",
      );
    for (let n = 0; n < 100; n++) {
      if (!existsSync(statePath)) {
        console.log("Agent Heist stopped.");
        return;
      }
      await delay(100);
    }
    throw Error(
      "Shutdown is taking longer than expected. Inspect .runtime/server.log and rerun ./stop.sh.",
    );
  });
}
async function supervise() {
  const instance = randomBytes(16).toString("hex");
  const token = randomBytes(32).toString("hex");
  let ready = false,
    stopping = false;
  const children = [];
  const info = {
    root,
    pid: process.pid,
    instance,
    mode: process.argv[3],
    token,
    port: 0,
  };
  const server = createServer((req, res) => {
    if (req.headers.authorization !== `Bearer ${token}`) {
      res.writeHead(403);
      res.end();
      return;
    }
    if (!(
      (req.url === "/status" && req.method === "GET") ||
      (req.url === "/stop" && req.method === "POST")
    )) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "no-store");
    res.end(JSON.stringify({ root, pid: process.pid, instance, ready }));
    if (req.url === "/stop") setImmediate(shutdown);
  });
  function finish() {
    removeState(info);
    process.exit(0);
  }
  function shutdown() {
    if (stopping) return;
    stopping = true;
    ready = false;
    server.close();
    // Only this authenticated, detached supervisor signals its own process group.
    try {
      process.kill(-process.pid, "SIGTERM");
    } catch {}
    const timer = setInterval(() => {
      if (children.every((c) => c.exitCode !== null || c.signalCode !== null)) {
        clearInterval(timer);
        finish();
      }
    }, 50);
    setTimeout(() => {
      removeState(info);
      try {
        process.kill(-process.pid, "SIGKILL");
      } catch {}
      process.exit(1);
    }, 5000).unref();
  }
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
  process.on("uncaughtException", () => {
    console.error("Supervisor error. Shutting down owned processes.");
    shutdown();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  info.port = server.address().port;
  const temp = join(runtime, `state-${instance}.tmp`);
  writeFileSync(temp, JSON.stringify(info), { mode: 0o600 });
  renameSync(temp, statePath);
  for (const args of [
    [
      join(root, "node_modules/tsx/dist/cli.mjs"),
      join(root, "server/index.ts"),
    ],
    [
      join(root, "node_modules/vite/bin/vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      "5173",
      "--strictPort",
    ],
  ]) {
    const child = spawn(process.execPath, args, {
      cwd: root,
      stdio: "inherit",
      env: process.env,
    });
    children.push(child);
    child.on("error", () => {
      console.error("A game process could not start.");
      shutdown();
    });
    child.on("exit", () => {
      if (!stopping) shutdown();
    });
  }
  for (let n = 0; n < 100 && !stopping; n++) {
    try {
      const [web, api] = await Promise.all([
        fetch("http://127.0.0.1:5173/", { signal: AbortSignal.timeout(1000) }),
        fetch("http://127.0.0.1:8787/api/play/status", {
          signal: AbortSignal.timeout(1000),
        }),
      ]);
      if (web.ok && api.ok) {
        const status = await api.json();
        if (info.mode === "offline" && status.live)
          throw Error("Offline guard failed");
        ready = true;
        return;
      }
    } catch {}
    await delay(200);
  }
  if (!stopping) shutdown();
}
try {
  if (command === "supervise") await supervise();
  else if (
    command === "start" &&
    process.argv.slice(3).every((x) => x === "--live")
  )
    await start(process.argv.includes("--live"));
  else if (command === "stop" && process.argv.length === 3) await stop();
  else throw Error("Usage: ./start.sh [--live] or ./stop.sh");
} catch (e) {
  console.error(`Agent Heist: ${e.message}`);
  process.exitCode = 1;
}
