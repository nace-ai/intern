// Starts and stops the processes the harness owns (its own game server(s) and its own Vite dev server).
// Nothing here ever signals a process the harness did not start.
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { createWriteStream, existsSync, readFileSync, writeFileSync } from "node:fs";
import net from "node:net";

export interface Proc { name: string; child: ChildProcess; pid: number; logFile: string; tail: string[]; exited: boolean; exitCode: number | null }

const procs: Proc[] = [];
/** Marker argument so a process can be recognised as ours in `ps` output. */
export const MARKER = "--vice-e2e-harness";

/** Keys must never reach a log file, even if a child prints one by mistake. */
function redact(s: string): string {
  return s.replace(/nace_sk_[A-Za-z0-9_-]{4,}/g, "nace_sk_<redacted>").replace(/(Bearer\s+)[A-Za-z0-9._-]{8,}/g, "$1<redacted>");
}

function listens(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once("error", () => resolve(false));
    s.listen({ port, host, exclusive: true }, () => s.close(() => resolve(true)));
  });
}
export async function portFree(port: number): Promise<boolean> {
  return (await listens(port, "0.0.0.0")) && (await listens(port, "::").catch(() => true));
}
const taken = new Set<number>();
export async function pickPort(preferred: number): Promise<number> {
  for (let p = preferred; p < preferred + 80; p++) {
    if (taken.has(p)) continue;
    if (await portFree(p)) { taken.add(p); return p; }
  }
  throw new Error(`no free port near ${preferred}`);
}

export function startProc(name: string, cmd: string, args: string[], o: { cwd: string; env: NodeJS.ProcessEnv; logFile: string }): Proc {
  const out = createWriteStream(o.logFile, { flags: "w" });
  // Own process group, so the whole tree (tsx -> node) can be stopped together and nothing else is touched.
  const child = spawn(cmd, args, { cwd: o.cwd, env: o.env, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  const p: Proc = { name, child, pid: child.pid ?? -1, logFile: o.logFile, tail: [], exited: false, exitCode: null };
  const onData = (b: Buffer) => {
    const text = redact(b.toString("utf8"));
    out.write(text);
    for (const line of text.split("\n")) if (line.trim()) { p.tail.push(line); if (p.tail.length > 400) p.tail.shift(); }
  };
  child.stdout?.on("data", onData);
  child.stderr?.on("data", onData);
  child.on("exit", (code) => { p.exited = true; p.exitCode = code; out.end(); });
  procs.push(p);
  return p;
}

const alive = (pid: number): boolean => { try { process.kill(pid, 0); return true; } catch { return false; } };

export async function stopProc(p: Proc): Promise<void> {
  if (p.pid <= 0 || p.exited) return;
  try { process.kill(-p.pid, "SIGTERM"); } catch { /* already gone */ }
  const end = Date.now() + 4000;
  while (!p.exited && Date.now() < end) await new Promise((r) => setTimeout(r, 50));
  if (!p.exited) { try { process.kill(-p.pid, "SIGKILL"); } catch { /* already gone */ } }
}
export async function stopAll(): Promise<void> { await Promise.all(procs.map(stopProc)); }
export function stopAllSync(): void { for (const p of procs) if (!p.exited && p.pid > 0) { try { process.kill(-p.pid, "SIGKILL"); } catch { /* gone */ } } }
export function livePids(): Array<{ name: string; pid: number }> { return procs.filter((p) => !p.exited).map((p) => ({ name: p.name, pid: p.pid })); }

/** A previous harness run that was killed hard may have left its children behind. Stop only those: pid recorded by us AND still carrying our marker. */
export function reapStale(pidFile: string): string[] {
  const reaped: string[] = [];
  if (!existsSync(pidFile)) return reaped;
  try {
    const old = JSON.parse(readFileSync(pidFile, "utf8")) as Array<{ name: string; pid: number }>;
    for (const o of old) {
      if (!alive(o.pid)) continue;
      let cmd = "";
      try { cmd = execFileSync("ps", ["-o", "command=", "-p", String(o.pid)], { encoding: "utf8" }); } catch { continue; }
      if (!cmd.includes(MARKER) && !cmd.includes("vite.e2e.config")) continue; // pid was reused by someone else: leave it alone
      try { process.kill(-o.pid, "SIGKILL"); reaped.push(`${o.name}:${o.pid}`); } catch { /* not a group leader any more */ }
    }
  } catch { /* unreadable pid file: ignore */ }
  return reaped;
}
export function writePids(pidFile: string): void { writeFileSync(pidFile, JSON.stringify(livePids())); }

export async function waitHealth(url: string, p: Proc, timeoutMs = 45000): Promise<Record<string, unknown>> {
  const end = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < end) {
    if (p.exited) throw new Error(`${p.name} exited with code ${p.exitCode} before it was healthy:\n${p.tail.slice(-25).join("\n")}`);
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (r.ok) return (await r.json()) as Record<string, unknown>;
      last = `HTTP ${r.status}`;
    } catch (e) { last = e instanceof Error ? e.message : String(e); }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`${p.name} not healthy at ${url} after ${timeoutMs} ms (${last})\n${p.tail.slice(-25).join("\n")}`);
}
