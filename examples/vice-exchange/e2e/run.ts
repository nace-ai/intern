// VICE EXCHANGE end-to-end harness.
//
//   npm run e2e            (from the repo root)
//
// Starts its OWN game server and its OWN Vite dev server on spare ports with a fresh SQLite file under
// e2e/out/, plays the PRD section 9 demo in three real Chrome contexts (A = Outlaw ACE, B = Hunter RIO,
// C = Spectator JUDGE), asserts through what the pages show AND through the server HTTP API, and writes
// screenshots plus a machine-readable report to e2e/out/. Exits non-zero when any step failed or was blocked.
// It never touches processes it did not start and never reads or prints API keys: the server under test
// loads them itself from .env, exactly as it does in normal use.
//
// Execution order differs from the step numbers in two places, on purpose, so that a failure means the
// product failed and not the script:
//   - step 6 (A buys one share) runs before step 3 (mission): once a witness reports, the outlaw is wanted
//     and can be captured within seconds, which would otherwise race the trading assertions;
//   - step 11 (provider failure, second server) runs in the background from the start.
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser } from "playwright-core";
import { CITY_MAP, CREDITS_NOTICE, DEFAULT_ROOM_CODE, MISSION_SPOTS, RULES, SPAWN_POINTS, dist, hasLineOfSight } from "@vice/contracts";
import type { AgentView, CharacterView, DecisionTrace, GameEvent, MemoryEntry, RoomSnapshot, Vec2 } from "@vice/contracts";
import { Api, Bot, EventLog, median, sleep } from "./lib/api";
import { providerFailureScenario, type FailoverResult } from "./lib/failover";
import { Recorder, Shots, card, click, dismissOverlay, ident, newActor, pageSend, poseIn, selectAgentByChip, setView, store, until, vd, view, type Actor, type View } from "./lib/harness";
import { positionLegal, setKeys, walkTo, type Walker, type WalkResult } from "./lib/nav";
import { countColourNear } from "./lib/png";
import { MARKER, pickPort, reapStale, startProc, stopAll, stopAllSync, waitHealth, writePids, type Proc } from "./lib/procs";
import { writeHtmlReport } from "./lib/report";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const OUT = path.join(HERE, "out");
const SHOTS = path.join(OUT, "screenshots");
const ROOM = DEFAULT_ROOM_CODE;
const startedAt = new Date();
const stamp = startedAt.toISOString().replace(/[:.]/g, "-");

const rec = new Recorder();
const shots = new Shots(SHOTS, rec);
const STEP_TITLES: Record<string, string> = {
  S01: "Join: three browsers in room VICE-42", S02: "Movement sync", S03: "Mission (AT-01)", S04: "Private memory (AT-02)",
  S05: "Live Drex decision (AT-03, Gate B)", S06: "Market (AT-04, FR-035)", S07: "Capture (AT-05)", S08: "Respawn (AT-06)",
  S09: "Director: seeded reset, scripted mode, experiment", S10: "Replay verify (AT-08)", S11: "Provider failure (AT-07)", S12: "Views: 2D, 3D, inspector, timeline, market crash, director",
  SX: "Extra integrity probes (beyond the numbered steps)",
};
const measured: Record<string, unknown> = {};
const env: Record<string, unknown> = {};
let server: Proc | null = null, vite: Proc | null = null, failServer: Proc | null = null, failVite: Proc | null = null;
let browser: Browser | null = null;

async function step(id: string, fn: () => Promise<void>): Promise<void> {
  console.log(`\n===== ${id} ${STEP_TITLES[id] ?? ""} =====`);
  const t0 = Date.now();
  try { await fn(); }
  catch (e) { rec.rec(id, `${id}.harness`, "step ran to the end", false, `step aborted: ${(e as Error).stack?.split("\n").slice(0, 4).join(" | ") ?? String(e)}`); }
  console.log(`----- ${id} took ${((Date.now() - t0) / 1000).toFixed(1)} s`);
}

const round1 = (n: number): number => Math.round(n * 10) / 10;
/** The dev server has no /favicon.ico, so every page load logs one 404. Counted, not treated as a page error. */
const noiseLine = (l: string): boolean => /Failed to load resource: the server responded with a status of 404/.test(l);
/** The embedded WORLD client asks for pointer lock / fullscreen as it boots; without a real click the browser refuses. Reported with the WORLD check. */
const worldLine = (l: string): boolean => /Pointer Lock|requestFullscreen|requestPointerLock/i.test(l);
const P = (v: Vec2 | null | undefined): string => (v ? `(${round1(v.x)}, ${round1(v.z)})` : "n/a");

async function main(): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  rmSync(SHOTS, { recursive: true, force: true });
  mkdirSync(SHOTS, { recursive: true });
  mkdirSync(path.join(OUT, "db"), { recursive: true });
  mkdirSync(path.join(OUT, "logs"), { recursive: true });
  const pidFile = path.join(OUT, "pids.json");
  const reaped = reapStale(pidFile);
  if (reaped.length) rec.note(`stopped leftovers of an earlier harness run: ${reaped.join(", ")}`);

  // ---------- processes ----------
  const serverPort = await pickPort(3131), webPort = await pickPort(5231), failPort = await pickPort(3141), failWebPort = await pickPort(5241);
  const serverBase = `http://127.0.0.1:${serverPort}`, webBase = `http://127.0.0.1:${webPort}`, failBase = `http://127.0.0.1:${failPort}`, failWebBase = `http://127.0.0.1:${failWebPort}`;
  const tsx = path.join(ROOT, "node_modules/.bin/tsx"), viteBin = path.join(ROOT, "node_modules/.bin/vite");
  const dbMain = path.join(OUT, "db", `main-${stamp}.sqlite`), dbFail = path.join(OUT, "db", `fail-${stamp}.sqlite`);
  const baseEnv = { ...process.env, GAME_TICK_HZ: "10" };
  server = startProc("server", tsx, ["apps/server/src/index.ts", MARKER], { cwd: ROOT, logFile: path.join(OUT, "logs", "server.log"),
    env: { ...baseEnv, PORT: String(serverPort), DATABASE_URL: dbMain, WEB_ORIGIN: webBase } });
  // Second instance for AT-07: same code, same .env, but an obviously invalid Drex key (process env wins over the file).
  failServer = startProc("server-invalid-key", tsx, ["apps/server/src/index.ts", MARKER], { cwd: ROOT, logFile: path.join(OUT, "logs", "server-invalid-key.log"),
    env: { ...baseEnv, PORT: String(failPort), DATABASE_URL: dbFail, WEB_ORIGIN: failWebBase, NACE_API_KEY: "nace_sk_e2e_obviously_invalid_key_0000", DEMO_DIRECTOR_SECRET: randomBytes(12).toString("hex") } });
  vite = startProc("vite", viteBin, ["--config", path.join(HERE, "vite.e2e.config.ts")], { cwd: path.join(ROOT, "apps/web"), logFile: path.join(OUT, "logs", "vite.log"),
    env: { ...process.env, VICE_SERVER: serverBase, E2E_WEB_PORT: String(webPort), VITE_BRIDGE_URL: `ws://127.0.0.1:${serverPort}/bridge` } });
  failVite = startProc("vite-invalid-key", viteBin, ["--config", path.join(HERE, "vite.e2e.config.ts")], { cwd: path.join(ROOT, "apps/web"), logFile: path.join(OUT, "logs", "vite-invalid-key.log"),
    env: { ...process.env, VICE_SERVER: failBase, E2E_WEB_PORT: String(failWebPort), VITE_BRIDGE_URL: `ws://127.0.0.1:${failPort}/bridge` } });
  writePids(pidFile);
  console.log(`harness processes: server :${serverPort} (pid ${server.pid}), vite :${webPort} (pid ${vite.pid}), invalid-key server :${failPort} (pid ${failServer.pid}), its vite :${failWebPort} (pid ${failVite.pid})`);

  const health = await waitHealth(`${serverBase}/health`, server);
  await waitHealth(`${failBase}/health`, failServer);
  const proxied = await waitHealth(`${webBase}/health`, vite, 60000); // also proves the dev-server proxy reaches OUR server
  await waitHealth(`${failWebBase}/health`, failVite, 60000);
  env.ports = { server: serverPort, web: webPort, invalidKeyServer: failPort, invalidKeyWeb: failWebPort };
  env.health = health;
  env.database = path.relative(ROOT, dbMain);
  console.log(`server healthy: ${JSON.stringify(health)}; via the Vite proxy: ok=${proxied.ok}`);

  const api = new Api(serverBase, ROOM);
  const log = new EventLog(api, 150);
  log.start();

  // Step 11 runs on its own instance, in the background.
  const failover: Promise<FailoverResult> = providerFailureScenario(failBase, ROOM, rec);

  // ---------- browser ----------
  browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu", "--ignore-gpu-blocklist", "--use-angle=metal", "--enable-webgl", "--enable-unsafe-swiftshader", "--mute-audio"] });
  env.chrome = browser.version();
  {
    // Warm-up page: lets Vite finish optimizing dependencies (it may reload once) before any assertion runs, and reports the GL renderer.
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
    const page = await ctx.newPage();
    for (let i = 0; i < 3; i++) {
      await page.goto(`${webBase}/`, { waitUntil: "load", timeout: 60000 }).catch(() => {});
      if (await page.waitForSelector(".join-card", { timeout: 20000 }).then(() => true).catch(() => false)) break;
    }
    await page.goto(`${webBase}/src/scene/three/dev/index.html?noauto=1`, { waitUntil: "load", timeout: 30000 }).catch(() => {});
    await page.waitForSelector("canvas", { timeout: 20000 }).catch(() => {});
    await sleep(1500);
    env.webgl = await page.evaluate(`(() => { const c = document.createElement("canvas"); const gl = c.getContext("webgl2") || c.getContext("webgl"); if (!gl) return { available: false }; const x = gl.getExtension("WEBGL_debug_renderer_info"); return { available: true, version: gl.getParameter(gl.VERSION), renderer: x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : null }; })()`).catch(() => ({ available: false }));
    await ctx.close();
    console.log(`chrome ${env.chrome}; WebGL ${JSON.stringify(env.webgl)}`);
  }

  const A = await newActor(browser, "A", "ACE", "OUTLAW");
  const B = await newActor(browser, "B", "RIO", "HUNTER");
  const C = await newActor(browser, "C", "JUDGE", "SPECTATOR");
  const actors = [A, B, C];

  // ---------- shared helpers ----------
  const illegal: string[] = [];
  let samples = 0;
  const onSample = (snap: RoomSnapshot): void => {
    samples++;
    for (const c of snap.characters) { if (c.status === "ARCHIVED") continue; const r = positionLegal(c); if (!r.ok && illegal.length < 20) illegal.push(`${c.nickname} ${r.why} (tick ${snap.tick})`); }
  };
  const walker = (a: Actor): Walker => ({ page: a.page, api, charId: a.charId!, label: a.nick, held: a.held, onSample });
  const walk = (a: Actor, to: Vec2, o: { tol?: number; timeoutMs?: number } = {}): Promise<WalkResult> => walkTo(walker(a), to, o);
  const charA = async (): Promise<CharacterView | undefined> => (await api.snapshot()).characters.find((c) => c.id === A.charId);
  const refreshIdent = async (a: Actor): Promise<void> => { const i = await until(() => ident(a), 8000, 150); if (i) { a.playerId = i.playerId; a.charId = i.characterId; } };
  const press = async (a: Actor, key: string): Promise<void> => {
    await a.page.evaluate("document.activeElement && document.activeElement !== document.body && document.activeElement.blur && document.activeElement.blur()").catch(() => {});
    await a.page.keyboard.press(key, { delay: 40 });
  };
  const openTab = async (a: Actor, which: "director" | "timeline"): Promise<void> => {
    await click(a, `.tabs button:has-text("${which === "director" ? "DEMO DIRECTOR" : "EVENT TIMELINE"}")`, { timeout: 5000 }).catch(() => {});
  };
  const nameOfNpc = (snap: RoomSnapshot, id: string): string => snap.npcs.find((n) => n.id === id)?.name ?? snap.characters.find((c) => c.id === id)?.nickname ?? snap.players.find((p) => p.id === id)?.nickname ?? id;
  const police = (snap: RoomSnapshot): AgentView[] => snap.npcs.filter((n) => n.role === "POLICE");
  const copSees = (snap: RoomSnapshot, at: Vec2, radius: number): AgentView | undefined => police(snap).find((n) => dist(n, at) <= radius && hasLineOfSight(n, at));
  /** Waits until the swarm has gone quiet: nothing pending, every message delivered, no agent event for `quietMs`. */
  const settle = async (maxMs: number, quietMs = 1600, stopIf?: (s: RoomSnapshot) => boolean): Promise<{ ms: number; quiet: boolean; stopped: boolean }> => {
    const t0 = Date.now();
    for (;;) {
      const s = await api.snapshot();
      if (stopIf?.(s)) return { ms: Date.now() - t0, quiet: false, stopped: true };
      const sent = log.of("AGENT_MESSAGE_SENT").length, delivered = log.of("AGENT_MESSAGE_DELIVERED").length;
      const lastAgent = [...log.all].reverse().find((e) => e.type.startsWith("DECISION_") || e.type.startsWith("AGENT_") || e.type === "BULLETIN_PUBLISHED" || e.type === "NPC_OBSERVED");
      const idleFor = lastAgent ? Date.now() - Date.parse(lastAgent.time) : Infinity;
      const pending = s.npcs.some((n) => n.pendingDecisionId !== null) || s.providerStatus.pending > 0;
      if (!pending && sent === delivered && idleFor >= quietMs) return { ms: Date.now() - t0, quiet: true, stopped: false };
      if (Date.now() - t0 > maxMs) return { ms: Date.now() - t0, quiet: false, stopped: false };
      await sleep(150);
    }
  };
  /** Spectator camera of the 2D canvas is fixed: whole map, centred. World metres -> page pixels. */
  const toScreenC = (v: View, p: Vec2): { x: number; y: number; fit: number } | null => {
    if (!v.canvas) return null;
    const fit = Math.min(Math.floor(v.canvas.w) / CITY_MAP.width, Math.floor(v.canvas.h) / CITY_MAP.height);
    return { x: v.canvas.x + (p.x - CITY_MAP.width / 2) * fit + Math.floor(v.canvas.w) / 2, y: v.canvas.y + (p.z - CITY_MAP.height / 2) * fit + Math.floor(v.canvas.h) / 2, fit };
  };
  /** Pixels of one colour around a world position in the spectator's 2D canvas. */
  const colourAtC = async (p: Vec2, rgb: [number, number, number], radius = 13): Promise<number | null> => {
    const v = await view(C);
    const s = toScreenC(v, p);
    if (!s || !v.canvas) return null;
    const cap = await shots.region(C, v.canvas);
    return cap ? countColourNear(cap.img, s.x - Math.max(0, v.canvas.x), s.y - Math.max(0, v.canvas.y), radius, rgb) : null;
  };
  const PINK: [number, number, number] = [255, 95, 162], MINT: [number, number, number] = [93, 253, 203];

  let ticker1 = "", char1 = "", buyCommandId = "", buyPrice = 0;
  let missionSeq = 0, missionIncident: GameEvent<"INCIDENT_OCCURRED"> | null = null;
  let captured: GameEvent<"CAPTURE_SUCCEEDED"> | null = null;
  let usedDirectorIncident = 0;
  let traderMessage: { delivered: GameEvent<"AGENT_MESSAGE_DELIVERED">; life: string } | null = null;
  const allTraces = new Map<string, DecisionTrace>();
  const collectTraces = async (): Promise<void> => { for (const t of await api.allTraces().catch(() => [] as DecisionTrace[])) allTraces.set(`${t.runId}:${t.id}`, t); };
  const verifies: Array<{ at: string; ok: boolean; source: string; runId: string; events: number; mismatches: string[] }> = [];
  const verifyAt = async (at: string): Promise<void> => {
    try { const v = await api.verify(); verifies.push({ at, ok: v.ok, source: v.source, runId: v.runId, events: v.eventCount, mismatches: v.mismatches.slice(0, 6) }); }
    catch (e) { verifies.push({ at, ok: false, source: "error", runId: "", events: 0, mismatches: [(e as Error).message] }); }
  };

  // =====================================================================================================
  await step("S01", async () => {
    const joinUi = async (a: Actor): Promise<void> => {
      await a.page.goto(`${webBase}/`, { waitUntil: "load", timeout: 45000 });
      await a.page.waitForSelector(".join-card", { timeout: 30000 });
      if (a.key === "A") await shots.one(a, "00-joinscreen");
      await a.page.fill('.join-card label:has-text("Nickname") input', a.nick);
      const code = await a.page.inputValue('.join-card label:has-text("Room code") input');
      if (code !== ROOM) await a.page.fill('.join-card label:has-text("Room code") input', ROOM);
      const title = a.role === "OUTLAW" ? "Outlaw" : a.role === "HUNTER" ? "Hunter" : "Spectator";
      await click(a, `.join-card .role:has(b:text-is("${title}"))`);
      await click(a, '.join-card button.primary');
      await a.page.waitForSelector(".app", { timeout: 20000 });
      await a.page.waitForFunction("!!(window.__vice && window.__vice.getState().session && window.__vice.getState().lastTick)", null, { timeout: 15000 });
      await refreshIdent(a);
    };
    const joinNotice = await (async () => { await A.page.goto(`${webBase}/`, { waitUntil: "load", timeout: 45000 }); await A.page.waitForSelector(".join-card", { timeout: 30000 }); return A.page.evaluate(`document.querySelector(".join-card .fine") ? document.querySelector(".join-card .fine").innerText : null`); })();
    for (const a of actors) await joinUi(a); // A first: the first human in the default room directs it
    await sleep(1500);
    const defaultViews = (await Promise.all(actors.map(view))).map((v) => v.seg.find((b) => b.on)?.text ?? "?");
    env.defaultView = defaultViews;
    await shots.all(actors, "01-join-default-view");
    // The judge watches the flat map for the whole run (its camera is fixed, so pixels can be checked); A and B keep the default view.
    const judge2d = await setView(C, "2D");
    if (!judge2d) rec.note("S01: could not switch the spectator to the 2D map with the header toggle");
    await sleep(700);
    const snap = await api.snapshot();
    const views = await Promise.all(actors.map(view));
    const ids = await Promise.all(actors.map((a) => ident(a)));
    char1 = A.charId ?? ""; ticker1 = snap.characters.find((c) => c.id === char1)?.ticker ?? "";

    rec.rec("S01", "S01.join-form", "all three join VICE-42 through the join form with nickname, room code and role", views.every((v) => v.joined && /ROOM VICE-42/.test(v.header ?? "")) && ids.every((i, k) => i?.role === actors[k]!.role),
      { headers: views.map((v) => v.header), roles: ids.map((i) => i?.role), joinScreenNotice: joinNotice }, ["FR-001", "Gate A"]);
    const players = snap.players.filter((p) => p.connected).map((p) => `${p.nickname}:${p.role}`).sort();
    const seen = await Promise.all(actors.map((a) => store<{ players: string[]; chars: string[] }>(a, `return { players: Object.values(s.players).filter((p) => p.connected).map((p) => p.nickname).sort(), chars: Object.values(s.characters).map((c) => c.nickname + ":" + c.role + ":" + (c.ticker || "-")).sort() };`)));
    rec.rec("S01", "S01.sees-others", "each browser shows 3 players and both avatars (ACE the outlaw with ticker, RIO the hunter); the server agrees",
      views.every((v) => /\b3 players\b/.test(v.header ?? "")) && seen.every((s) => s?.players.join() === "ACE,JUDGE,RIO" && s.chars.length === 2) && players.join() === "ACE:OUTLAW,JUDGE:SPECTATOR,RIO:HUNTER",
      { headerCounts: views.map((v) => (v.header ?? "").match(/\d+ players?/)?.[0]), pageStores: seen, server: players }, ["FR-002", "FR-006", "Gate A"]);
    const roles = (v: View): string[] => [...new Set(v.chips.map((c) => (c.title ?? "").split(" · ")[0]!))].sort();
    const serverRoles: Record<string, number> = {};
    for (const n of snap.npcs) serverRoles[n.role] = (serverRoles[n.role] ?? 0) + 1;
    rec.rec("S01", "S01.npcs", "eight NPCs with four distinct roles are listed in every browser and on the server (stable ids)",
      views.every((v) => v.chips.length === 8 && roles(v).join() === "INFORMANT,POLICE,TRADER,WITNESS" && /\b8 agents\b/.test(v.header ?? "")) && snap.npcs.length === 8 && Object.keys(serverRoles).length === 4,
      { chipsPerBrowser: views.map((v) => v.chips.length), rolesInUi: roles(views[2]!), server: serverRoles, ids: snap.npcs.map((n) => n.id).join(" ") }, ["FR-004", "Gate A", "Gate B"]);
    const mission = snap.missions.find((m) => m.status === "AVAILABLE");
    const markerPixels = mission ? await colourAtC(mission, MINT, 12) : null;
    const missionInStores = await Promise.all(actors.map((a) => store<string[]>(a, `return Object.values(s.missions).filter((m) => m.status === "AVAILABLE").map((m) => m.label);`)));
    rec.rec("S01", "S01.mission-marker", "the mission marker is drawn on the map (mint diamond at the dock in the spectator's 2D view) and every browser holds the same available mission",
      !!mission && mission.x === MISSION_SPOTS[0]!.x && mission.z === MISSION_SPOTS[0]!.z && (markerPixels ?? 0) >= 10 && missionInStores.every((m) => m?.length === 1 && m[0] === mission.label),
      { server: mission && { id: mission.id, at: P(mission), label: mission.label }, mintPixelsAtMarkerInJudgeView: markerPixels, labelsPerBrowser: missionInStores }, ["FR-005", "Gate A"]);
    const cards = views.map((v) => card(v, ticker1));
    rec.rec("S01", "S01.market", "market panel shows the outlaw's ticker with a price in every browser; A and B have V$2,000 wallets, the spectator is told it cannot trade",
      !!ticker1 && cards.every((c) => !!c && vd(c.price) !== null && !c.halted) && vd(views[0]!.balance) === 2000 && vd(views[1]!.balance) === 2000 && /Spectators do not trade/.test(views[2]!.walletText ?? ""),
      { ticker: ticker1, prices: cards.map((c) => c?.price), wallets: views.map((v) => v.balance ?? v.walletText), serverSpot: snap.listings.find((l) => l.ticker === ticker1)?.spotPrice }, ["FR-006", "FR-037", "Gate A"]);
    rec.rec("S01", "S01.provider-badge", "provider badge is visible in every browser and matches the server's provider status", views.every((v) => v.badges[0] === snap.providerStatus.badge) && (health.drexConfigured ? snap.providerStatus.badge === "DREX: LIVE" : true),
      { badges: views.map((v) => v.badges[0]), server: snap.providerStatus.badge, drexConfigured: health.drexConfigured, mode: snap.mode }, ["FR-006", "Gate A"]);
    rec.rec("S01", "S01.credits-notice", "the fictional-credits notice is shown on the join screen and in every browser", views.every((v) => v.notice === CREDITS_NOTICE) && joinNotice === CREDITS_NOTICE, { notice: views[0]!.notice, joinScreen: joinNotice }, ["FR-039"]);
    rec.rec("S01", "S01.director", "the first human in the default room is the demo director; the others are not", ids[0]?.isDirector === true && ids[1]?.isDirector === false && ids[2]?.isDirector === false && /DIRECTOR/.test(views[0]!.header ?? ""), { isDirector: ids.map((i) => i?.isDirector) }, ["FR-060"]);
    const sp = SPAWN_POINTS, ca = snap.characters.find((c) => c.id === A.charId), cb = snap.characters.find((c) => c.id === B.charId);
    rec.rec("S01", "S01.api", "server snapshot: outlaw at the first outlaw spawn with ticker ACE-01, hunter at the first hunter spawn with no ticker, spectator with no character",
      ticker1 === "ACE-01" && !!ca && !!cb && dist(ca, sp.OUTLAW[0]!) < 3 && dist(cb, sp.HUNTER[0]!) < 3 && cb.ticker === null && ids[2]?.characterId === null,
      { ace: ca && { id: ca.id, ticker: ca.ticker, at: P(ca), status: ca.status }, rio: cb && { id: cb.id, ticker: cb.ticker, at: P(cb) }, judgeCharacter: ids[2]?.characterId ?? null, listings: snap.listings.map((l) => `${l.ticker}:${l.status}:${l.spotPrice}`) }, ["FR-030", "Gate A"]);
    await shots.all(actors, "01-join");
  });

  // =====================================================================================================
  await step("S02", async () => {
    const start = await charA();
    if (!start) throw new Error("outlaw character missing");
    const pinkBefore = await colourAtC(start, PINK);
    const track: Array<{ server: Vec2; b: Vec2 | null; c: Vec2 | null }> = [];
    let sampling = true;
    const sampler = (async () => {
      while (sampling) {
        const [s, pb, pc] = await Promise.all([api.snapshot(), poseIn(B, A.charId!), poseIn(C, A.charId!)]);
        const c = s.characters.find((x) => x.id === A.charId);
        if (c) track.push({ server: { x: c.x, z: c.z }, b: pb, c: pc });
        await sleep(110);
      }
    })();
    // A walks up the open road east of the marina; B leaves its spawn for a staging point near the dock, around the mall.
    const wpA: Vec2 = { x: 60, z: 57 }, stageB: Vec2 = { x: 56, z: 67 };
    const [ra, rb] = await Promise.all([walk(A, wpA, { tol: 1.0 }), walk(B, stageB, { tol: 1.2, timeoutMs: 40000 })]);
    sampling = false; await sampler;
    await sleep(500);
    const end = await charA();
    const [fb, fc] = await Promise.all([poseIn(B, A.charId!), poseIn(C, A.charId!)]);
    const moving = track.filter((t) => t.b && t.c);
    const maxLag = moving.reduce((m, t) => Math.max(m, dist(t.server, t.b!), dist(t.server, t.c!)), 0);
    const sawB = moving.length ? dist(moving[0]!.b!, fb ?? moving[0]!.b!) : 0, sawC = moving.length ? dist(moving[0]!.c!, fc ?? moving[0]!.c!) : 0;
    rec.rec("S02", "S02.keyboard", "A walks with the keyboard to a waypoint; the server integrates the position", ra.ok && !!end && dist(start, end) > 8,
      { from: P(start), waypoint: P(wpA), serverNow: P(end), metres: end ? round1(dist(start, end)) : 0, ms: ra.ms, samples: ra.samples, reason: ra.reason }, ["FR-002", "FR-003", "Gate A"]);
    rec.rec("S02", "S02.sync", "B and C see A move: their pages track the server position while walking and agree with it at rest",
      !!end && !!fb && !!fc && dist(end, fb) < 0.35 && dist(end, fc) < 0.35 && sawB > 8 && sawC > 8 && maxLag < 3.5,
      { serverAtRest: P(end), hunterPage: P(fb), judgePage: P(fc), metresSeenByB: round1(sawB), metresSeenByC: round1(sawC), worstLagWhileMovingMetres: round1(maxLag), samples: moving.length }, ["FR-002", "FR-006", "Gate A"]);
    const pinkOld = await colourAtC(start, PINK), pinkNew = end ? await colourAtC(end, PINK) : null;
    rec.rec("S02", "S02.pixels", "the outlaw's avatar is drawn at the new place in the spectator's 2D map and no longer at the old one",
      (pinkBefore ?? 0) >= 6 && (pinkNew ?? 0) >= 6 && (pinkOld ?? 99) <= Math.max(1, Math.floor((pinkBefore ?? 0) / 4)), { pinkPixelsAtStartBefore: pinkBefore, pinkPixelsAtStartAfter: pinkOld, pinkPixelsAtNewPosition: pinkNew }, ["FR-006"]);
    const hb = (await api.snapshot()).characters.find((c) => c.id === B.charId);
    const aSeesB = await poseIn(A, B.charId!);
    rec.rec("S02", "S02.hunter", "B walks from its spawn around the mall to the dock; A's page shows B at the server position", rb.ok && !!hb && !!aSeesB && dist(hb, aSeesB) < 0.35,
      { route: rb.route.map(P).join(" -> "), serverNow: P(hb), outlawPage: P(aSeesB), ms: rb.ms, reason: rb.reason }, ["FR-002"]);
    await shots.all(actors, "02-move");
  });

  // =====================================================================================================
  // Step 6 (UI part) runs here, before the mission: see the header comment.
  await step("S06", async () => {
    const [va0, vb0] = await Promise.all([view(A), view(B)]);
    const bal0 = vd(va0.balance);
    const seq0 = log.lastSeq;
    await click(A, `.tcard:has(.tk:text-is("${ticker1}")) button.buy`, { timeout: 6000 });
    const ev = await log.waitFor("TRADE_EXECUTED", (e) => e.payload.actorId === A.playerId, 6000, seq0);
    if (!ev) { rec.rec("S06", "S06.buy", "A buys one share with the BUY button", false, { reason: "no TRADE_EXECUTED event for A within 6 s", toasts: (await view(A)).toasts }, ["AT-04"]); return; }
    buyCommandId = ev.payload.commandId; buyPrice = ev.payload.executionPrice;
    const va = await until(async () => { const v = await view(A); return vd(v.balance) === (bal0 ?? 0) - buyPrice ? v : null; }, 4000, 100) ?? await view(A);
    rec.rec("S06", "S06.buy", "A buys one share: wallet goes down by the fill price and holdings go up by one, in the page and on the server",
      vd(va.balance) === (bal0 ?? 0) - buyPrice && va.holds.some((h) => (h ?? "").startsWith(`${ticker1} ×1`)) && ev.payload.balanceAfter === (bal0 ?? 0) - buyPrice && ev.payload.sharesAfter === 1,
      { walletBefore: va0.balance, walletAfter: va.balance, holdings: va.holds, fill: `V$${buyPrice}`, event: ev.eventId, tradeId: ev.payload.tradeId, serverBalanceAfter: ev.payload.balanceAfter, serverSharesAfter: ev.payload.sharesAfter, toast: va.toasts }, ["AT-04", "FR-034", "Gate C"]);
    // Both browsers: same trade line, same price.
    const same = await until(async () => {
      const [x, y, s] = await Promise.all([view(A), view(B), api.snapshot()]);
      const l = s.listings.find((q) => q.ticker === ticker1);
      const line = `BUY 1 ${ticker1} @ V$${buyPrice.toLocaleString("en-US")} · ACE`;
      const ok = card(x, ticker1)?.price === card(y, ticker1)?.price && vd(card(x, ticker1)?.price) === l?.spotPrice && x.feed.includes(line) && y.feed.includes(line) && x.feed.join("|") === y.feed.join("|");
      return ok ? { priceA: card(x, ticker1)?.price, priceB: card(y, ticker1)?.price, serverSpot: l?.spotPrice, feedA: x.feed.slice(0, 4), feedB: y.feed.slice(0, 4) } : null;
    }, 5000, 250);
    const [xa, xb] = await Promise.all([view(A), view(B)]);
    rec.rec("S06", "S06.same-trade", "A and B show the same trade in the feed and the same price", !!same,
      same ?? { priceA: card(xa, ticker1)?.price, priceB: card(xb, ticker1)?.price, feedA: xa.feed.slice(0, 4), feedB: xb.feed.slice(0, 4), hunterPriceBefore: card(vb0, ticker1)?.price }, ["AT-04", "FR-006", "FR-037", "Gate C"]);

    // Replay the very same commandId over a raw socket that re-attaches to A's session (the reconnect/retry path).
    const saved = await A.page.evaluate(`JSON.parse(sessionStorage.getItem("vice.session") || "null")`) as { sessionToken?: string } | null;
    if (!saved?.sessionToken) { rec.rec("S06", "S06.replay", "replaying the same commandId over a raw socket does not execute again", "blocked", "the page holds no session token to re-attach with", ["AT-04"]); }
    else {
      const bot = await Bot.connect(serverBase, "ACE-replay");
      const j = await bot.joinRoom({ roomCode: ROOM, nickname: "ACE", role: "OUTLAW", sessionToken: saved.sessionToken });
      const again = await bot.cmd<{ trade: { tradeId: string } }>({ type: "market.buy", ticker: ticker1, qty: 1, commandId: buyCommandId });
      await sleep(500); await log.pull();
      const execs = log.of("TRADE_EXECUTED").filter((e) => e.payload.commandId === buyCommandId);
      const v = await api.verify();
      const w = v.projection.wallets[`wal_${A.playerId}`];
      rec.rec("S06", "S06.replay", "replaying the same commandId over a raw socket (re-attached to A's session) is acknowledged as a duplicate and does not execute again",
        j.ok && j.result?.playerId === A.playerId && j.result.characterId === A.charId && again.ok === true && again.duplicate === true && execs.length === 1 && w?.balance === (bal0 ?? 0) - buyPrice && w.holdings[ticker1] === 1,
        { sameSession: j.result?.playerId === A.playerId, sameCharacter: j.result?.characterId === A.charId, ack: { ok: again.ok, duplicate: again.duplicate, tradeId: again.result?.trade?.tradeId, error: again.error }, tradeExecutedEventsForCommand: execs.length, serverWallet: w }, ["AT-04", "FR-035", "NFR-007", "FR-008"]);
      // Hand the session back to the browser before the raw socket goes away.
      const back = await A.page.evaluate(`(async () => { try { const m = await import("/src/net/socketClient.ts"); await m.resync(true); return true; } catch (e) { return String(e); } })()`).catch((e) => String(e));
      await sleep(400);
      bot.close();
      await sleep(500); await log.pull();
      let me = (await api.snapshot()).players.find((p) => p.id === A.playerId);
      if (back !== true || !me?.connected) { await A.page.reload({ waitUntil: "load" }); await A.page.waitForSelector(".app", { timeout: 20000 }); await sleep(800); me = (await api.snapshot()).players.find((p) => p.id === A.playerId); }
      await refreshIdent(A);
      const s2 = await api.snapshot();
      rec.rec("S06", "S06.reattach", "re-attaching with the session token keeps the same player and character (no duplicate avatar or ticker)",
        me?.connected === true && A.charId === char1 && s2.characters.filter((c) => c.nickname === "ACE").length === 1 && s2.listings.length === 1,
        { connected: me?.connected, character: A.charId, aceCharacters: s2.characters.filter((c) => c.nickname === "ACE").length, listings: s2.listings.map((l) => l.ticker) }, ["FR-008"]);
    }
    await shots.all(actors, "06-market");
  });

  // =====================================================================================================
  let snapAtMission: RoomSnapshot | null = null;
  await step("S03", async () => {
    const spot = MISSION_SPOTS[0]!;
    // Keep both officers away from the dock so that nobody but the witness sees the job (police seeing it would make A wanted instantly).
    const far = await until(async () => { const s = await api.snapshot(); const d = police(s).map((n) => round1(dist(n, spot))); return d.every((x) => x > 22) ? d : null; }, 30000, 200);
    if (!far) rec.note("S03: an officer stayed within 22 m of the dock for 30 s; going ahead anyway");
    const w = await walk(A, spot, { tol: 1.3 });
    const s0 = await api.snapshot();
    const c0 = s0.characters.find((c) => c.id === A.charId)!, l0 = s0.listings.find((l) => l.ticker === ticker1)!, m0 = s0.missions.find((m) => m.status === "AVAILABLE");
    const v0 = await view(A);
    missionSeq = log.lastSeq;
    await press(A, "e");
    const done = await log.waitFor("MISSION_COMPLETED", (e) => e.payload.characterId === A.charId, 5000, missionSeq);
    snapAtMission = await api.snapshot(); // taken within a few hundred ms of the incident: used by step 4
    if (!done) { rec.rec("S03", "S03.complete", "pressing E at the marker completes the mission", false, { walkedTo: P(w.pos), walkOk: w.ok, distance: round1(dist(w.pos, spot)), toasts: (await view(A)).toasts, hud: (await view(A)).hud }, ["AT-01"]); return; }
    await log.pull();
    missionIncident = log.of("INCIDENT_OCCURRED", missionSeq).find((e) => e.payload.characterId === A.charId) ?? null;
    const toast1 = (await view(A)).toasts;
    await sleep(700);
    await press(A, "e");                         // again: must do nothing
    await sleep(500);
    const toast2 = (await view(A)).toasts;
    const viaSocket = m0 ? await pageSend(A, { type: "mission.interact", missionId: m0.id, commandId: `e2e-again-${Date.now()}` }) : null;
    await sleep(600); await log.pull();
    const s1 = await api.snapshot();
    const c1 = s1.characters.find((c) => c.id === A.charId)!, l1 = s1.listings.find((l) => l.ticker === ticker1)!;
    const completions = log.of("MISSION_COMPLETED").filter((e) => e.payload.characterId === A.charId && e.runId === s1.runId);
    const v1 = await view(A);
    const ref0 = l0.referenceValue, ref1 = l1.referenceValue;
    rec.rec("S03", "S03.complete", "A walks to the first mission and presses E: exactly one MISSION_COMPLETED", completions.length === 1 && done.payload.missionId === m0?.id,
      { event: done.eventId, sequence: done.serverSequence, mission: m0?.label, at: P(w.pos), completions: completions.length, toast: toast1 }, ["AT-01", "FR-005", "Gate A"]);
    rec.rec("S03", "S03.notoriety", "notoriety, completed missions and heat rise as configured (+2, +1, +12)",
      c1.notorietyPoints === c0.notorietyPoints + RULES.notoriety.perMission && c1.completedMissions === c0.completedMissions + 1 && done.payload.heatGained === RULES.heat.perMission && /jobs 1 · notoriety 2/.test(v1.hud ?? ""),
      { before: { notoriety: c0.notorietyPoints, missions: c0.completedMissions, heat: c0.heat }, after: { notoriety: c1.notorietyPoints, missions: c1.completedMissions, heat: c1.heat }, eventHeatGained: done.payload.heatGained, hud: v1.hud }, ["AT-01", "FR-010"]);
    const refUi0 = vd((card(v0, ticker1)?.meta ?? "").match(/ref V\$[\d,]+/)?.[0]), refUi1 = vd((card(v1, ticker1)?.meta ?? "").match(/ref V\$[\d,]+/)?.[0]);
    rec.rec("S03", "S03.reference", "the reference value rises by at least 15*2 + 60 = 90, on the server and in the market panel",
      ref1 - ref0 >= 90 && ref1 - ref0 <= 110 && (refUi1 ?? 0) - (refUi0 ?? 0) >= 90, { serverBefore: ref0, serverAfter: ref1, panelBefore: refUi0, panelAfter: refUi1 }, ["AT-01", "FR-031", "Gate C"]);
    rec.rec("S03", "S03.again", "pressing E again does nothing, and a second mission command sent through the page's socket is refused",
      completions.length === 1 && c1.notorietyPoints === c0.notorietyPoints + RULES.notoriety.perMission && (viaSocket ? viaSocket.ok === false : true),
      { completionsAfterSecondPress: completions.length, toastAfterSecondPress: toast2, socketAttempt: viaSocket ? (viaSocket.ok ? "ACCEPTED" : `${viaSocket.error?.code}: ${viaSocket.error?.message}`) : "no mission id" }, ["AT-01", "NFR-007"]);
    const prices = log.of("MARKET_PRICE_UPDATED", missionSeq).filter((e) => e.payload.ticker === ticker1);
    const pm = prices.find((e) => e.payload.reason === "MISSION"), ph = prices.find((e) => e.payload.reason === "HEAT");
    rec.rec("S06", "S06.price-reacts", "the price reacted to the mission (reference and spot up) and to heat (spot down while the reference is unchanged)",
      !!pm && pm.payload.spotPrice > pm.payload.previousSpotPrice && !!ph && ph.payload.spotPrice < ph.payload.previousSpotPrice && ph.payload.referenceValue === pm.payload.referenceValue,
      { mission: pm && { event: pm.eventId, spot: `${pm.payload.previousSpotPrice} -> ${pm.payload.spotPrice}`, reference: pm.payload.referenceValue }, heat: ph && { event: ph.eventId, spot: `${ph.payload.previousSpotPrice} -> ${ph.payload.spotPrice}`, heat: ph.payload.heat, reference: ph.payload.referenceValue } }, ["FR-031", "FR-032", "Gate C"]);
    await shots.all(actors, "03-mission");
  });

  // =====================================================================================================
  await step("S04", async () => {
    const inc = missionIncident, s = snapAtMission as RoomSnapshot | null;
    if (!inc || !s) { rec.rec("S04", "S04.witness", "a witness that saw the incident has a DIRECT memory of it", "blocked", "no mission incident was recorded in step 3", ["AT-02"]); return; }
    const observers = inc.payload.observerIds;
    const wit = s.npcs.find((n) => observers.includes(n.id) && n.role === "WITNESS") ?? s.npcs.find((n) => observers.includes(n.id));
    const mem = wit?.memories.find((m) => m.eventId === inc.eventId && m.sourceKind === "DIRECT");
    rec.rec("S04", "S04.witness", "a witness that saw the incident has a DIRECT memory of it, citing the incident event",
      !!wit && !!mem && mem.factType === "INCIDENT" && mem.subjectCharacterId === A.charId && mem.hop === 0 && mem.sourceActorId === wit.id,
      { incident: inc.eventId, observers: observers.map((o) => nameOfNpc(s, o)), witness: wit?.name, memory: mem && { id: mem.id, factType: mem.factType, sourceKind: mem.sourceKind, hop: mem.hop, belief: mem.confidence, summary: mem.summary } }, ["AT-02", "FR-011", "FR-012"]);
    const others = s.npcs.filter((n) => !observers.includes(n.id));
    const leaks = others.filter((n) => n.memories.some((m) => m.subjectCharacterId === A.charId && (m.factType === "INCIDENT" || m.factType === "RUMOR" || m.factType === "SIGHTING")));
    const traders = others.filter((n) => n.role === "TRADER");
    rec.rec("S04", "S04.no-leak", "right after the incident no NPC that was out of range knows about it; the traders only hold the public listing",
      traders.length >= 1 && leaks.length === 0,
      { snapshotTakenMsAfterIncident: s.serverTimeMs - inc.simTimeMs, tradersOutOfRange: traders.map((t) => ({ name: t.name, metresAway: round1(dist(t, inc.payload)), knows: t.memories.map((m) => `${m.factType}/${m.sourceKind}`) })), leaks: leaks.map((n) => n.name) }, ["AT-02", "FR-012", "FR-013", "FR-015"]);
    // What the witness told the model: only its own knowledge.
    const tr = wit ? await api.trace(wit.id) : null;
    const d = tr?.decisions.find((x) => x.triggerEventId && log.byId(x.triggerEventId)?.causationId === inc.eventId) ?? tr?.decisions[tr.decisions.length - 1];
    const state = JSON.stringify(d?.stateSnapshot ?? {});
    const foreign = s.npcs.filter((n) => n.id !== wit?.id).flatMap((n) => n.memories.map((m) => m.id));
    rec.rec("S04", "S04.own-state", "the decision input sent for the witness contains only that witness's own memories (no wallet balances, no other NPC's memory ids)",
      !!d && /my_memories/.test(state) && !foreign.some((idm) => state.includes(idm)) && !/balance/i.test(state),
      { decision: d?.id, keys: Object.keys(d?.stateSnapshot ?? {}), memoriesSent: (d?.stateSnapshot as { my_memories?: unknown[] } | undefined)?.my_memories?.length }, ["FR-015", "FR-021"]);
  });

  // The swarm reacts to the mission. Stop waiting early if an officer gets close to a wanted A.
  const afterMission = await settle(9000, 1600, (s) => { const a = s.characters.find((c) => c.id === A.charId); return !!a && a.status === "WANTED" && !!copSees(s, a, 20); });
  console.log(`swarm after the mission: ${JSON.stringify(afterMission)}`);

  /** Did a message reach a trader yet? (AT-02, second half) */
  const findTraderMessage = (life: string): void => {
    if (traderMessage) return;
    const snapRoles = new Map<string, string>();
    for (const e of log.of("DECISION_REQUESTED")) snapRoles.set(e.payload.agentId, e.payload.role);
    const d = log.of("AGENT_MESSAGE_DELIVERED", missionSeq).find((e) => e.payload.recipientId.startsWith("npc_t") && e.payload.kind !== "BULLETIN");
    if (d) traderMessage = { delivered: d, life };
  };
  findTraderMessage("first life");

  // =====================================================================================================
  await step("S07", async () => {
    let s = await api.snapshot();
    let a = s.characters.find((c) => c.id === A.charId);
    if (!a || (a.status !== "ACTIVE" && a.status !== "WANTED")) { rec.rec("S07", "S07.capture", "B walks to the wanted A and presses E", "blocked", `the outlaw is already ${a?.status ?? "gone"} (captured by ${a?.capturedBy ?? "?"}) before the hunter could act`, ["AT-05"]); return; }
    const walletB0 = vd((await view(B)).balance);
    let how = "a witness report made A wanted";
    if (process.env.E2E_ESCAPE_FIRST === "1" && a.status === "WANTED") {
      // Optional detour (off by default): hide behind the Exchange until the heat decays, which exercises the
      // escape rule and then forces the "nobody reported" branch below.
      const seqE = log.lastSeq;
      const hid = await walk(A, { x: 4, z: 54 }, { tol: 1.5, timeoutMs: 30000 });
      const esc = await log.waitFor("ESCAPE_SUCCEEDED", (e) => e.payload.characterId === A.charId, 120000, seqE);
      await sleep(400); await log.pull();
      const c = await charA();
      const pe = log.of("MARKET_PRICE_UPDATED", seqE).find((e) => e.payload.reason === "ESCAPE");
      rec.rec("SX", "SX.escape", "a wanted outlaw that stays out of police sight escapes once heat decays below 30: ESCAPE_SUCCEEDED, +1 escape, no longer wanted, price reacts",
        !!esc && c?.status === "ACTIVE" && c.successfulEscapes === 1 && c.heat < RULES.heat.wantedThreshold && !!pe && pe.payload.spotPrice > pe.payload.previousSpotPrice,
        { hidAt: P(hid.pos), event: esc?.eventId ?? null, status: c?.status, heat: c?.heat, escapes: c?.successfulEscapes, price: pe && `${pe.payload.previousSpotPrice} -> ${pe.payload.spotPrice} (${pe.eventId})`, hud: (await view(A)).hud }, ["FR-050"]);
      s = await api.snapshot(); a = s.characters.find((x) => x.id === A.charId);
      if (!a || (a.status !== "ACTIVE" && a.status !== "WANTED")) { rec.rec("S07", "S07.capture", "B walks to the wanted A and presses E", "blocked", `the outlaw is ${a?.status ?? "gone"} after hiding (captured by ${a?.capturedBy ?? "?"})`, ["AT-05"]); return; }
    }
    if (a.status !== "WANTED") {
      // Nobody reported (or the heat already decayed). Go where officers pass and use the director's incident trigger with an officer watching.
      how = "director incident trigger with an officer in sight (no witness report had made A wanted)";
      const stage: Vec2 = { x: 59, z: 31.5 };
      const [wa] = await Promise.all([walk(A, stage, { tol: 1.2, timeoutMs: 40000 }), walk(B, { x: stage.x + 3, z: stage.z + 2 }, { tol: 1.5, timeoutMs: 40000 })]);
      if (!wa.ok) rec.note(`S07: A could not reach the stage: ${wa.reason}`);
      for (let i = 0; i < 3; i++) {                             // the hunter steps right up to where the outlaw actually stands
        const q = await api.snapshot(); const x = q.characters.find((c) => c.id === A.charId), y = q.characters.find((c) => c.id === B.charId);
        if (!x || !y || dist(x, y) <= 1.9) break;
        await walk(B, { x: x.x + 0.9, z: x.z + 0.5 }, { tol: 0.9, timeoutMs: 12000 });
      }
      await openTab(A, "director");
      let closest = Infinity, gap = NaN;
      const seen = await until(async () => {
        const q = await api.snapshot(); const x = q.characters.find((c) => c.id === A.charId), y = q.characters.find((c) => c.id === B.charId);
        if (!x || !y) return null;
        gap = dist(x, y);
        for (const n of police(q)) closest = Math.min(closest, dist(n, x));
        const cop = copSees(q, x, 10.5);
        return cop && gap <= 2.3 && dist(cop, x) >= 4.5 ? { cop: cop.name, metres: round1(dist(cop, x)) } : null;
      }, 75000, 120);
      if (!seen) { rec.rec("S07", "S07.capture", "B walks to the wanted A and presses E", "blocked", { reason: "no officer came within sight of the staged outlaw in 75 s, so A could not be made wanted with the director trigger", closestOfficerMetres: round1(closest), hunterToOutlawMetres: round1(gap), stage: P(stage) }, ["AT-05"]); return; }
      const seqI = log.lastSeq;
      await click(A, '.director button:has-text("Trigger incident")', { timeout: 5000 });
      usedDirectorIncident++;
      const wantedEv = await log.waitFor("WANTED_STATUS_CHANGED", (e) => e.payload.characterId === A.charId && e.payload.wanted, 5000, seqI);
      rec.note(`S07: used the director's incident trigger (officer ${seen.cop} at ${seen.metres} m); wanted event ${wantedEv?.eventId ?? "none"}`);
      if (!wantedEv) { rec.rec("S07", "S07.capture", "B walks to the wanted A and presses E", "blocked", { reason: "the director incident did not make A wanted", toasts: (await view(A)).toasts }, ["AT-05"]); return; }
    }
    // B closes in and presses E.
    const seqC = log.lastSeq;
    let attempts = 0;
    for (; attempts < 4 && !captured; attempts++) {
      s = await api.snapshot(); a = s.characters.find((c) => c.id === A.charId);
      const b = s.characters.find((c) => c.id === B.charId);
      if (!a || !b || a.status !== "WANTED") break;
      if (dist(a, b) > 2.0) await walk(B, { x: a.x, z: a.z }, { tol: 1.5, timeoutMs: 15000 });
      await until(async () => ((await poseIn(B, A.charId!))?.status === "WANTED" ? true : null), 2500, 60);
      await press(B, "e");
      captured = await log.waitFor("CAPTURE_SUCCEEDED", (e) => e.payload.characterId === A.charId, 2500, seqC);
      if (!captured) await sleep(RULES.captureCooldownMs);
    }
    captured = captured ?? log.of("CAPTURE_SUCCEEDED", seqC).find((e) => e.payload.characterId === A.charId) ?? null;
    // The crash cinematic runs on a fixed clock (price hits zero at 2.3 s, halt plate at 3.0 s, gone by 4.5 s), so every
    // read and capture below is scheduled against the capture event's own timestamp.
    const tCap = captured ? Date.parse(captured.time) : Date.now();
    const at = async (ms: number): Promise<void> => { const d = tCap + ms - Date.now(); if (d > 0) await sleep(d); };
    const early = await Promise.all(actors.map(view));          // what every page says while the crash is on screen
    const earlyAtMs = Date.now() - tCap;
    await shots.all(actors, "07-capture-impact", true);        // first frames: the last price before it falls
    await at(3150);
    await shots.all(actors, "07-capture", true);               // the peak: V$0 and the halt plate
    const inFrame = await Promise.all(actors.map((a) => view(a).then((v) => v.overlay)));
    await at(4800);
    for (const a of actors) await dismissOverlay(a);
    await sleep(300);
    const views = await Promise.all(actors.map(view));
    await shots.all(actors, "12-views-market-crash");          // the Market panel right after: halted card, V$0, price cliff
    await log.pull();
    if (!captured) {
      const rej = log.of("CAPTURE_REJECTED", seqC).map((e) => e.payload.reason);
      rec.rec("S07", "S07.capture", "B walks to the wanted A and presses E: exactly one CAPTURE_SUCCEEDED", false, { how, attempts, rejections: rej, hunterToasts: views[1]!.toasts, outlaw: a && { status: a.status, heat: a.heat } }, ["AT-05"]);
      return;
    }
    const cp = captured.payload;
    const caps = log.of("CAPTURE_SUCCEEDED").filter((e) => e.payload.characterId === char1);
    const byHunter = cp.captorKind === "HUMAN" && cp.captorId === B.playerId;
    rec.rec("S07", "S07.capture", "B walks to the wanted A and presses E: exactly one CAPTURE_SUCCEEDED, by the hunter", caps.length === 1 && byHunter,
      { how, event: captured.eventId, sequence: captured.serverSequence, captor: `${cp.captorName} (${cp.captorKind})`, attempts, survivalSeconds: cp.survivalSeconds, spotBeforeCapture: cp.spotBeforeCapture, referenceAtCapture: cp.referenceAtCapture, captureEvents: caps.length }, ["AT-05", "FR-051", "FR-052", "Gate D"]);
    const sAfter = await api.snapshot();
    const l = sAfter.listings.find((x) => x.ticker === ticker1), ch = sAfter.characters.find((c) => c.id === char1);
    const halts = log.of("LISTING_HALTED").filter((e) => e.payload.ticker === ticker1), zero = log.of("MARKET_PRICE_UPDATED").filter((e) => e.payload.ticker === ticker1 && e.payload.reason === "CAPTURE");
    rec.rec("S07", "S07.zero-server", "server: the ticker is HALTED_CAPTURED at exactly 0 (bid 0, ask 0), the character is CAPTURED, one halt event and one price-to-zero event",
      l?.status === "HALTED_CAPTURED" && l.spotPrice === 0 && l.bid === 0 && l.ask === 0 && ch?.status === "CAPTURED" && ch.capturedBy === cp.captorId && halts.length === 1 && zero.length === 1 && zero[0]!.payload.spotPrice === 0,
      { listing: l && { status: l.status, spot: l.spotPrice, bid: l.bid, ask: l.ask, finalSpotBeforeHalt: l.finalSpotBeforeHalt }, character: ch && { status: ch.status, capturedBy: ch.capturedBy }, halt: halts[0]?.eventId, priceToZero: zero[0]?.eventId }, ["AT-05", "FR-033", "FR-052", "Gate D"]);
    const cards = views.map((v) => card(v, ticker1));
    rec.rec("S07", "S07.zero-screens", "every screen shows the ticker at exactly V$0 and HALTED, with the crash banner",
      cards.every((c) => !!c && c.halted && c.price === "V$0" && c.chg === "HALTED" && /→ V\$0/.test(c.meta ?? "") && c.buy === null) && early.every((v) => /HALTED/i.test(v.crash ?? "") && /V\$0/.test(v.crash ?? "") && (v.crash ?? "").includes(ticker1)),
      { cards: cards.map((c) => c && `${c.ticker} ${c.price} ${c.chg} | ${c.meta}`), crashBanner: early.map((v) => (v.crash ?? "").slice(0, 200)), bannerReadMsAfterCapture: earlyAtMs, bannerStillUpAfterPeakScreenshot: inFrame, slowestScreenshotMs: shots.slowestMs, outlawHud: views[0]!.hud, outlawHoldings: views[0]!.holds }, ["AT-05", "FR-033", "FR-006", "Gate D"]);
    // A further buy is rejected (the UI removes the buttons, so send the order through the page's own socket).
    const buyAfter = await pageSend(B, { type: "market.buy", ticker: ticker1, qty: 1, commandId: `e2e-after-${Date.now()}` });
    rec.rec("S07", "S07.buy-rejected", "a further buy on the halted ticker is rejected with LISTING_HALTED; the buttons are gone from the card",
      buyAfter?.ok === false && buyAfter.error?.reason === "LISTING_HALTED" && cards.every((c) => c?.buy === null && c?.sell === null), { ack: buyAfter?.error ?? buyAfter, buttonsOnHaltedCard: cards.map((c) => c?.buy) }, ["AT-05", "FR-033", "FR-035", "Gate D"]);
    // Bounty: once.
    await press(B, "e"); await sleep(400);
    const second = await pageSend(B, { type: "capture.attempt", targetCharacterId: char1, commandId: `e2e-cap2-${Date.now()}` });
    await sleep(500); await log.pull();
    const v = await api.verify();
    const wB = v.projection.wallets[`wal_${B.playerId}`];
    const vb = await view(B);
    const expectBounty = Math.max(RULES.bounty.min, Math.round(RULES.bounty.fractionOfReference * cp.referenceAtCapture));
    rec.rec("S07", "S07.bounty", "the hunter receives the bounty exactly once: max(50, half the reference value), credited to its wallet; a second attempt is rejected",
      byHunter && cp.bounty === expectBounty && cp.captorBalanceAfter === (walletB0 ?? 0) + cp.bounty && wB?.balance === (walletB0 ?? 0) + cp.bounty && vd(vb.balance) === (walletB0 ?? 0) + cp.bounty
        && log.of("CAPTURE_SUCCEEDED").filter((e) => e.payload.characterId === char1).length === 1 && second?.ok === false,
      { bounty: cp.bounty, expected: expectBounty, hunterWalletBefore: walletB0, hunterWalletPanel: vb.balance, serverWallet: wB?.balance, secondAttempt: second?.error?.reason ?? second?.error?.code, hunterToasts: vb.toasts }, ["AT-05", "FR-055", "Gate D"]);
    // The captured character cannot run a job any more.
    await press(A, "e"); await sleep(300);
    const mAvail = sAfter.missions.find((m) => m.status === "AVAILABLE");
    const job = mAvail ? await pageSend(A, { type: "mission.interact", missionId: mAvail.id, commandId: `e2e-job-${Date.now()}` }) : null;
    rec.rec("S07", "S07.locked", "the captured character is locked out of missions", /captured/i.test((await view(A)).toasts.join(" ")) && (job ? job.ok === false : true),
      { toast: (await view(A)).toasts, socketAttempt: job ? job.error?.code : "no available mission to try" }, ["AT-05", "FR-052"]);
    await verifyAt("after capture");
  });

  // =====================================================================================================
  await step("S05", async () => {
    // Inspector and timeline, on the judge's screen, with the first life's chain still in memory.
    await collectTraces();
    const snap = await api.snapshot();
    const live = [...allTraces.values()].filter((t) => t.decisionSource === "drex" && t.provider === "drex");
    const sample = live.find((t) => t.role === "WITNESS") ?? live[0];
    if (!sample) {
      const all = [...allTraces.values()];
      rec.rec("S05", "S05.live-trace", "at least one decision resolved with decisionSource drex", false, { decisions: all.length, bySource: all.reduce<Record<string, number>>((m, t) => { const k = t.decisionSource ?? "pending"; m[k] = (m[k] ?? 0) + 1; return m; }, {}), errors: [...new Set(all.map((t) => t.errorType).filter(Boolean))], badge: snap.providerStatus.badge }, ["AT-03", "Gate B"]);
    } else {
      const offered = Object.keys(sample.offeredActions);
      rec.rec("S05", "S05.live-trace", "a decision resolved with decisionSource drex, a provider request id, offered actions, probabilities, confidence and latency",
        !!sample.providerRequestId && offered.length >= 2 && !!sample.probabilities && offered.every((o) => typeof sample.probabilities![o] === "number") && typeof sample.confidence === "number" && typeof sample.latencyMs === "number" && !!sample.model,
        { decision: sample.id, agent: `${sample.agentName} (${sample.role})`, model: sample.model, requestId: sample.providerRequestId, offered, chosen: sample.chosenAction, providerChoice: sample.providerChoice, probabilities: sample.probabilities, confidence: sample.confidence, latencyMs: sample.latencyMs, providerEvalMs: sample.providerEvalMs, status: sample.status, trigger: sample.triggerEventId, effects: sample.effectEventIds }, ["AT-03", "FR-020", "FR-023", "Gate B"]);
      // Click that NPC in the city (spectator 2D map), then read the inspector.
      const npc = snap.npcs.find((n) => n.id === sample.agentId)!;
      await dismissOverlay(C); // the crash banner / cinematic covers the city for ~5 s and swallows clicks
      const vc0 = await view(C);
      const scr = toScreenC(vc0, npc);
      if (scr) await C.page.mouse.click(scr.x, scr.y);
      let vc = await until(async () => { const v = await view(C); return v.agentName === npc.name ? v : null; }, 2500, 100);
      const clickedOnMap = !!vc;
      if (!vc) { await selectAgentByChip(C, npc.name); vc = await until(async () => { const v = await view(C); return v.agentName === npc.name ? v : null; }, 2500, 100); }
      await sleep(300);
      vc = await view(C);
      const shown = (await api.trace(npc.id)).agent.lastDecision;
      const txt = vc.agent ?? "";
      const probsShown = shown?.probabilities ? Object.entries(shown.probabilities).every(([k, p]) => vc!.decOpts.some((o) => (o ?? "").startsWith(k) && (o ?? "").includes(`${(p * 100).toFixed(1)}%`))) : false;
      rec.rec("S05", "S05.inspector", "clicking that NPC in the city opens the Agent Inspector with the live decision: source badge, offered actions with probabilities, confidence, round trip, request id",
        clickedOnMap && !!shown && shown.decisionSource === "drex" && vc.decSrc === "DREX · live" && txt.includes(shown.providerRequestId ?? "~none~") && txt.includes(`confidence ${shown.confidence?.toFixed(3)}`) && probsShown && /round trip \d/.test(txt),
        { clickedOnMap, agent: vc.agentName, badge: vc.decSrc, choice: vc.decChoice, options: vc.decOpts, meta: vc.decMeta, apiRequestId: shown?.providerRequestId, apiConfidence: shown?.confidence, apiLatencyMs: shown?.latencyMs, memoriesShown: vc.mems.length }, ["AT-03", "FR-023", "FR-027", "Gate B"]);
      await shots.all(actors, "05-decision");
      await shots.one(C, "12-views-inspector");
    }
    // Timeline: select the mission and follow its causal chain.
    const mc = log.of("MISSION_COMPLETED").find((e) => e.payload.characterId === char1);
    if (mc) {
      await openTab(C, "timeline");
      const row = C.page.locator(".tl-list .ev", { hasText: `#${mc.serverSequence} ` }).first();
      await row.scrollIntoViewIfNeeded().catch(() => {});
      const clicked = await row.click({ timeout: 4000 }).then(() => true).catch(() => false);
      await sleep(400);
      const vt = await view(C);
      const kids = new Map<string, string[]>();
      for (const e of log.all) if (e.causationId && e.runId === mc.runId) { const k = kids.get(e.causationId); if (k) k.push(e.eventId); else kids.set(e.causationId, [e.eventId]); }
      const chain = new Set<string>([mc.eventId]); const stack = [mc.eventId];
      while (stack.length) for (const k of kids.get(stack.pop()!) ?? []) if (!chain.has(k)) { chain.add(k); stack.push(k); }
      const types = [...new Set([...chain].map((idc) => log.byId(idc, mc.runId)?.type))];
      const linked = Number((vt.chainButton ?? "").match(/\((\d+) linked\)/)?.[1] ?? -1);
      const rows = vt.timeline.filter((r) => /\bchain\b/.test(r.cls) || /\bsel\b/.test(r.cls)).length;
      rec.rec("S05", "S05.timeline-chain", "selecting the mission in the Event Timeline highlights its causal chain: observation, decision, applied action and price move",
        clicked && linked === chain.size && rows >= 4 && ["INCIDENT_OCCURRED", "NPC_OBSERVED", "DECISION_RESOLVED", "MARKET_PRICE_UPDATED"].every((t) => types.includes(t as never)),
        { clicked, linkedInUi: linked, linkedOnServer: chain.size, highlightedRows: rows, typesInChain: types }, ["FR-061", "Gate E"]);
      await shots.one(C, "12-views-timeline-chain");
      await click(C, '.tl-bar button:has-text("Clear chain")').catch(() => {});
    } else rec.rec("S05", "S05.timeline-chain", "selecting the mission in the Event Timeline highlights its causal chain", "blocked", "no mission event to select", ["FR-061"]);
  });

  // =====================================================================================================
  await step("S08", async () => {
    const s0 = await api.snapshot();
    const old = s0.characters.find((c) => c.id === char1);
    if (!old || old.status !== "CAPTURED") { rec.rec("S08", "S08.respawn", "A respawns with a new character and ticker", "blocked", `the first character is ${old?.status ?? "missing"}, not CAPTURED`, ["AT-06"]); return; }
    const seq = log.lastSeq;
    await press(A, "r");
    const ev = await log.waitFor("CHARACTER_RESPAWNED", (e) => e.payload.playerId === A.playerId, 5000, seq);
    await sleep(900);
    await refreshIdent(A);
    const s1 = await api.snapshot();
    const views = await Promise.all(actors.map(view));
    const fresh = s1.characters.find((c) => c.id === ev?.payload.characterId), gone = s1.characters.find((c) => c.id === char1);
    const ln = s1.listings.find((l) => l.ticker === fresh?.ticker), lo = s1.listings.find((l) => l.ticker === ticker1);
    rec.rec("S08", "S08.respawn", "A presses R: a new character id and a new ticker at the baseline price; the old character is archived",
      !!ev && !!fresh && fresh.id !== char1 && !!fresh.ticker && fresh.ticker !== ticker1 && fresh.status === "ACTIVE" && fresh.notorietyPoints === 0 && fresh.heat === 0 && ln?.status === "ACTIVE" && ln.referenceValue <= 104 && ln.referenceValue >= 100 && gone?.status === "ARCHIVED" && A.charId === fresh.id,
      { event: ev?.eventId, oldCharacter: `${char1} (${gone?.status})`, newCharacter: fresh && `${fresh.id} (${fresh.status}) at ${P(fresh)}`, oldTicker: ticker1, newTicker: fresh?.ticker, newReference: ln?.referenceValue, newSpot: ln?.spotPrice, samePlayer: fresh?.playerId === A.playerId }, ["AT-06", "FR-038", "FR-053", "Gate D"]);
    const oldTrade = s1.trades.find((t) => t.ticker === ticker1 && t.actorId === A.playerId);
    rec.rec("S08", "S08.history-server", "server: the old ticker is still listed as HALTED_CAPTURED at 0 with its price history and its trades",
      lo?.status === "HALTED_CAPTURED" && lo.spotPrice === 0 && lo.history.length >= 4 && lo.history[lo.history.length - 1]!.spot === 0 && lo.history.some((h) => h.spot > 0) && !!oldTrade && lo.finalSpotBeforeHalt === captured?.payload.spotBeforeCapture,
      { status: lo?.status, spot: lo?.spotPrice, historyPoints: lo?.history.length, peak: lo ? Math.max(...lo.history.map((h) => h.spot)) : null, finalSpotBeforeHalt: lo?.finalSpotBeforeHalt, volume: lo?.volume, aceTradeKept: oldTrade?.tradeId }, ["AT-06", "FR-054"]);
    const oc = views.map((v) => card(v, ticker1)), nc = views.map((v) => card(v, fresh?.ticker ?? "?"));
    const line = `BUY 1 ${ticker1} @ V$${buyPrice.toLocaleString("en-US")} · ACE`;
    rec.rec("S08", "S08.history-screens", "every screen shows both tickers: the new one tradable, the old one halted at V$0 with its chart and its trade in the feed",
      oc.every((c) => !!c && c.halted && c.price === "V$0" && c.chartPaths >= 2) && nc.every((c) => !!c && !c.halted && vd(c.price) !== null) && (buyPrice ? views.every((v) => v.feed.includes(line)) : true) && /respawned with new ticker/.test(views[2]!.timeline.map((r) => r.text).join(" | ")),
      { newCards: nc.map((c) => c && `${c.ticker} ${c.price}`), oldCards: oc.map((c) => c && `${c.ticker} ${c.price} ${c.chg} charts:${c.chartPaths}`), oldTradeInFeed: views.map((v) => v.feed.includes(line)), outlawHud: views[0]!.hud }, ["AT-06", "FR-037", "FR-054", "Gate D"]);
    await shots.all(actors, "08-respawn");
    await verifyAt("after respawn");
  });

  // =====================================================================================================
  // Second life: give every role a chance to decide live, and get a message through to a trader (AT-02, second half).
  const appliedLiveRoles = (): Set<string> => new Set([...allTraces.values()].filter((t) => t.decisionSource === "drex" && t.status === "APPLIED").map((t) => t.role));
  const ensureAlive = async (): Promise<boolean> => {
    let a = await charA();
    if (a && (a.status === "ACTIVE" || a.status === "WANTED")) return true;
    await press(A, "r"); await sleep(1200); await refreshIdent(A);
    a = await charA();
    return !!a && (a.status === "ACTIVE" || a.status === "WANTED");
  };
  const stimulations: Array<Record<string, unknown>> = [];
  const stimulate = async (why: string, stage: Vec2, wantPolice: boolean): Promise<void> => {
    if (!(await ensureAlive())) { stimulations.push({ why, result: "outlaw could not be respawned" }); return; }
    const w = await walk(A, stage, { tol: 1.2, timeoutMs: 40000 });
    await openTab(A, "director");
    const cond = await until(async () => {
      const q = await api.snapshot(); const x = q.characters.find((c) => c.id === A.charId);
      if (!x) return null;
      const near = copSees(q, x, wantPolice ? 11 : 17);
      return wantPolice ? (near && dist(near, x) > 5 ? { police: near.name } : null) : (near ? null : { police: "none in sight" });
    }, 45000, 150);
    const seq = log.lastSeq;
    await click(A, '.director button:has-text("Trigger incident")', { timeout: 5000 }).catch(() => {});
    usedDirectorIncident++;
    const inc = await log.waitFor("INCIDENT_OCCURRED", () => true, 4000, seq);
    const st = await settle(9000, 1500);
    await collectTraces(); await log.pull();
    findTraderMessage("second life");
    const s = await api.snapshot();
    stimulations.push({ why, at: P(w.pos), reached: w.ok, condition: cond ?? "timed out waiting for the police condition", incident: inc?.eventId ?? null, observers: inc?.payload.observerIds.map((o) => nameOfNpc(s, o)), settledMs: st.ms,
      decisions: log.of("DECISION_RESOLVED", seq).map((e) => `${nameOfNpc(s, e.payload.agentId)}:${e.payload.chosenAction}:${e.payload.decisionSource}:${e.payload.status}`) });
  };
  await step("S05", async () => {
    await collectTraces();
    findTraderMessage("first life");
    const informantStage: Vec2 = { x: 31, z: 49 };   // next to informant Lena Okoye; trader Priya Castellan is 23 m away
    for (let i = 0; i < 3 && (!traderMessage || !appliedLiveRoles().has("INFORMANT")); i++) await stimulate(`informant chain, attempt ${i + 1}`, informantStage, false);
    if (!appliedLiveRoles().has("POLICE")) await stimulate("police decision", { x: 59, z: 31.5 }, true);
    if (!appliedLiveRoles().has("WITNESS")) await stimulate("witness decision", { x: 43, z: 61 }, false);
    if (stimulations.length) rec.note(`S05: the director's incident trigger was used ${stimulations.length} more time(s) to give missing roles something to decide on: ${JSON.stringify(stimulations)}`);
    await collectTraces(); await log.pull();
    const traces = [...allTraces.values()];
    const live = traces.filter((t) => t.decisionSource === "drex" && t.provider === "drex");
    const guard = traces.filter((t) => t.decisionSource === "drex_low_confidence_guardrail");
    const fallback = traces.filter((t) => t.decisionSource === "fallback");
    const applied = live.filter((t) => t.status === "APPLIED" && t.effectEventIds.some((idE) => log.byId(idE, t.runId)?.type === "AGENT_ACTION_APPLIED"));
    const rolesLive: Record<string, number> = {}, rolesApplied: Record<string, number> = {};
    for (const t of live) rolesLive[t.role] = (rolesLive[t.role] ?? 0) + 1;
    for (const t of applied) rolesApplied[t.role] = (rolesApplied[t.role] ?? 0) + 1;
    const lat = live.map((t) => t.latencyMs).filter((x): x is number => typeof x === "number");
    measured.drex_decisions = live.length;
    measured.drex_latency_ms_p50 = median(lat);
    measured.drex_latency_ms_min = lat.length ? Math.min(...lat) : null;
    measured.drex_latency_ms_max = lat.length ? Math.max(...lat) : null;
    measured.drex_eval_ms_p50 = median(live.map((t) => t.providerEvalMs).filter((x): x is number => typeof x === "number"));
    measured.fallback_decisions = fallback.length;
    measured.fallback_error_types = fallback.reduce<Record<string, number>>((m, t) => { const k = t.errorType ?? "none"; m[k] = (m[k] ?? 0) + 1; return m; }, {});
    measured.low_confidence_guardrail_decisions = guard.length;
    measured.roles_with_live_decision = Object.keys(rolesLive).sort();
    measured.live_decisions_by_role = rolesLive;
    measured.live_applied_cycles = applied.length;
    measured.live_applied_by_role = rolesApplied;
    measured.live_rejected_stale = live.filter((t) => t.status === "REJECTED").map((t) => `${t.agentName}:${t.chosenAction}:${t.rejectReason}`);
    measured.live_actions = live.reduce<Record<string, number>>((m, t) => { const k = `${t.role}:${t.chosenAction}`; m[k] = (m[k] ?? 0) + 1; return m; }, {});
    measured.decisions_total_first_run = traces.length;
    measured.director_incident_triggers_used = usedDirectorIncident;
    rec.rec("S05", "S05.roles", "live Drex decisions came from at least 4 NPC roles", Object.keys(rolesLive).length >= 4,
      { liveByRole: rolesLive, appliedByRole: rolesApplied, directorIncidentTriggersUsed: usedDirectorIncident }, ["Gate B", "PRD-10 Agency"]);
    const vc = await view(C);
    const visible = applied.filter((t) => t.effectEventIds.some((idE) => { const e = log.byId(idE, t.runId); return e?.type === "AGENT_ACTION_APPLIED" && vc.timeline.some((r) => (r.text ?? "").startsWith(`#${e.serverSequence} `)); })).length;
    rec.rec("S05", "S05.cycles", "at least 6 live Drex decision cycles, each validated and applied with an effect event in the feed", applied.length >= 6,
      { liveDecisions: live.length, appliedWithEffectEvent: applied.length, stillInJudgeTimelineWindow: visible, rejectedAsStale: (measured.live_rejected_stale as string[]).length, examples: applied.slice(0, 8).map((t) => `${t.agentName} ${t.chosenAction} -> ${t.effectEventIds.join(",")}`) }, ["Gate B", "PRD-10 Agency", "FR-020"]);
    const dishonest = traces.filter((t) => (t.decisionSource === "drex" && (t.provider !== "drex" || !t.providerRequestId || t.latencyMs === null)) || ((t.decisionSource === "fallback" || t.decisionSource === "scripted") && (t.confidence !== null || t.probabilities !== null)));
    rec.rec("S05", "S05.honest", "no decision is labeled drex without a provider request id and latency; fallback decisions carry no confidence", dishonest.length === 0,
      { checked: traces.length, live: live.length, fallback: fallback.length, fallbackErrors: measured.fallback_error_types, guardrail: guard.length, offending: dishonest.slice(0, 3).map((t) => ({ id: t.id, source: t.decisionSource, provider: t.provider, requestId: t.providerRequestId, confidence: t.confidence })) }, ["AT-03", "NFR-009", "FR-024"]);
    const pendingFx = live.filter((t) => t.status !== "APPLIED" && t.status !== "REJECTED");
    if (pendingFx.length) rec.note(`S05: ${pendingFx.length} live decision(s) ended in status ${[...new Set(pendingFx.map((t) => t.status))].join(",")}`);
  });

  // =====================================================================================================
  await step("S04", async () => {
    findTraderMessage("second life");
    if (!traderMessage) {
      const relays = log.of("DECISION_RESOLVED").filter((e) => e.payload.role === "INFORMANT").map((e) => `${e.payload.chosenAction}/${e.payload.decisionSource}`);
      const wit = log.of("DECISION_RESOLVED").filter((e) => e.payload.role === "WITNESS").map((e) => `${e.payload.chosenAction}/${e.payload.decisionSource}`);
      rec.rec("S04", "S04.message", "a message is delivered to an out-of-range trader and its memory names the source", "blocked",
        { reason: "no informant relayed anything to a trader during the run, so no message ever reached a trader", witnessChoices: wit, informantChoices: relays, directorIncidentTriggersUsed: usedDirectorIncident }, ["AT-02"]);
      return;
    }
    const d = traderMessage.delivered, p = d.payload;
    const s = await api.snapshot();
    const tr = await api.trace(p.recipientId);
    const mem = tr.agent.memories.find((m) => m.id === p.memoryId);
    const every = new Map<string, MemoryEntry>();
    for (const n of s.npcs) for (const m of n.memories) every.set(m.id, m);
    const chain: string[] = [];
    for (let m = mem, guard = 0; m && guard < 6; m = m.sourceMemoryId ? every.get(m.sourceMemoryId) : undefined, guard++) chain.push(`${nameOfNpc(s, m.agentId)}[${m.sourceKind}, hop ${m.hop}]`);
    const subject = mem?.subjectCharacterId;
    const earlier = tr.agent.memories.filter((m) => m.subjectCharacterId === subject && (m.factType === "RUMOR" || m.factType === "INCIDENT") && m.sourceKind !== "DIRECT" && m.observedAtMs < d.simTimeMs);
    const sent = log.of("AGENT_MESSAGE_SENT").find((e) => e.payload.messageId === p.messageId);
    rec.rec("S04", "S04.message", "once a message is delivered to a trader, the trader holds a memory that names the sender as its source and links back to what the sender knew",
      !!mem && mem.sourceKind === "MESSAGE" && mem.sourceActorId === p.senderId && mem.hop === p.hop && mem.eventId === d.eventId && !!mem.sourceMemoryId && earlier.length === 0 && !!sent && sent.payload.sourceMemoryId === mem.sourceMemoryId,
      { life: traderMessage.life, delivered: d.eventId, sent: sent?.eventId, kind: p.kind, trader: tr.agent.name, source: nameOfNpc(s, p.senderId), memory: mem && { id: mem.id, factType: mem.factType, sourceKind: mem.sourceKind, hop: mem.hop, belief: mem.confidence, summary: mem.summary }, provenance: chain.join(" <- "), knewBeforeDelivery: earlier.length }, ["AT-02", "FR-013", "FR-014"]);
    // The judge sees the same thing in the inspector.
    const chip = await selectAgentByChip(C, tr.agent.name);
    const vc = await until(async () => { const v = await view(C); return v.agentName === tr.agent.name ? v : null; }, 3000, 100) ?? await view(C);
    if (!chip.ok) rec.note(`S04: could not open ${tr.agent.name} through its chip: ${chip.error}`);
    const sender = nameOfNpc(s, p.senderId);
    const line = vc.mems.find((m) => (m ?? "").includes("was told") && (m ?? "").includes(`from ${sender}`));
    rec.rec("S04", "S04.message-ui", "the Agent Inspector shows the trader's memory as told by the sender, with the hop count", vc.agentName === tr.agent.name && !!line && line.includes(`hop ${p.hop}`),
      { agent: vc.agentName, line: line ?? null, memories: vc.mems.slice(0, 4) }, ["AT-02", "FR-014"]);
    const after = tr.decisions.find((x) => x.triggerEventId === d.eventId);
    rec.rec("S04", "S04.trader-decides", "the delivered message triggers a trade decision by that trader", !!after,
      after ? { decision: after.id, source: after.decisionSource, offered: Object.keys(after.offeredActions), chosen: after.chosenAction, status: after.status, confidence: after.confidence, requestId: after.providerRequestId, effects: after.effectEventIds, errorType: after.errorType } : { reason: "no decision trace was triggered by the delivery event", decisions: tr.decisions.map((x) => `${x.triggerEventId}:${x.chosenAction}`) }, ["AT-02", "FR-020"]);
    await shots.all(actors, "04-memory");
  });

  // Everything the first run decided, before the director wipes the room.
  await collectTraces();
  await verifyAt("end of the first run (before any reset)");
  await log.pull();
  const firstRun = (await api.snapshot()).runId;
  const firstRunLastSeq = log.lastSeq;
  measured.first_run_metrics = (await api.snapshot()).metrics;

  // =====================================================================================================
  await step("S09", async () => {
    await openTab(A, "director");
    await sleep(300);
    await shots.all(actors, "09-director-before");
    const initial = async (): Promise<Record<string, unknown> | null> => {
      const seq = log.lastSeq;
      await click(A, '.director button:has-text("Seeded reset")', { timeout: 6000 });
      const rs = await log.waitFor("ROOM_RESET", () => true, 6000, seq);
      if (!rs) return null;
      const snap = await api.snapshot();
      await until(() => (log.of("CHARACTER_SPAWNED", rs.serverSequence).length >= 2 && log.of("MISSION_SPAWNED", rs.serverSequence).length >= 1 ? true : null), 4000, 80);
      const ev = (t: string) => log.all.filter((e) => e.serverSequence >= rs.serverSequence && e.runId === rs.payload.newRunId && e.type === t);
      const police0 = snap.npcs.filter((n) => n.role === "POLICE").map((n) => ({ id: n.id, x: n.x, z: n.z }));
      return {
        runId: rs.payload.newRunId, seed: rs.payload.seed, snapshotTick: snap.tick,
        characters: (ev("CHARACTER_SPAWNED") as Array<GameEvent<"CHARACTER_SPAWNED">>).map((e) => ({ id: e.payload.characterId, player: e.payload.playerId, nick: e.payload.nickname, role: e.payload.role, ticker: e.payload.ticker, x: e.payload.x, z: e.payload.z })),
        wallets: (ev("WALLET_OPENED") as Array<GameEvent<"WALLET_OPENED">>).map((e) => ({ wallet: e.payload.walletId, kind: e.payload.ownerKind, balance: e.payload.balance })).sort((a, b) => a.wallet.localeCompare(b.wallet)),
        listings: (ev("LISTING_OPENED") as Array<GameEvent<"LISTING_OPENED">>).map((e) => ({ ticker: e.payload.ticker, reference: e.payload.referenceValue, spot: e.payload.spotPrice })),
        missions: (ev("MISSION_SPAWNED") as Array<GameEvent<"MISSION_SPAWNED">>).slice(0, 1).map((e) => ({ id: e.payload.missionId, x: e.payload.x, z: e.payload.z, label: e.payload.label })),
        npcsStatic: snap.npcs.filter((n) => n.role !== "POLICE").map((n) => ({ id: n.id, role: n.role, x: n.x, z: n.z, wallet: n.walletCredits })),
        police0,
      };
    };
    const r1 = await initial();
    await sleep(1500);
    for (const a of actors) await refreshIdent(a);
    const viewsAfter1 = await Promise.all(actors.map(view));
    const r2 = await initial();
    await sleep(1500);
    for (const a of actors) await refreshIdent(a);
    const strip = (r: Record<string, unknown> | null): string => { if (!r) return "null"; const { runId: _r, snapshotTick: _t, police0: _p, ...rest } = r; return JSON.stringify(rest); };
    const policeOk = !!r1 && !!r2 && (r1.police0 as Array<{ id: string; x: number; z: number }>).every((p) => { const q = (r2.police0 as Array<{ id: string; x: number; z: number }>).find((x) => x.id === p.id)!; return dist(p, q) <= 0.35 * (Math.abs((r1.snapshotTick as number) - (r2.snapshotTick as number)) + 2) + 0.5; });
    rec.rec("S09", "S09.reset-identical", "seeded reset twice gives identical initial positions, tickers and wallets (and missions, listings, NPC roster)",
      !!r1 && !!r2 && strip(r1) === strip(r2) && policeOk && r1.runId !== r2.runId,
      { runs: [r1?.runId, r2?.runId], seed: r1?.seed, characters: r1?.characters, wallets: r1?.wallets, listings: r1?.listings, mission: r1?.missions, identical: strip(r1) === strip(r2), policeWithinTickDrift: policeOk, firstDifference: strip(r1) === strip(r2) ? null : { a: strip(r1).slice(0, 400), b: strip(r2).slice(0, 400) } }, ["FR-060", "Gate E"]);
    const va = await view(A);
    rec.rec("S09", "S09.reset-ui", "after the reset every browser is still in the room with a clean state: ticker ACE-01, wallet V$2,000, three players",
      viewsAfter1.every((v) => v.joined && /\b3 players\b/.test(v.header ?? "") && v.tickers.length === 1 && v.tickers[0]!.ticker === "ACE-01") && vd(va.balance) === 2000 && /DIRECTOR/.test(va.header ?? ""),
      { headers: viewsAfter1.map((v) => v.header), tickers: viewsAfter1.map((v) => v.tickers.map((t) => `${t.ticker} ${t.price}`)), wallet: va.balance, holdings: va.holds }, ["FR-060"]);

    // ---- scripted baseline ----
    await settle(6000, 1200);
    const seqM = log.lastSeq;
    await click(A, '.director .seg.wide button:has-text("SCRIPTED")', { timeout: 6000 });
    const mode = await log.waitFor("MODE_CHANGED", (e) => e.payload.mode === "scripted", 5000, seqM);
    await sleep(1200);
    const vm = await Promise.all(actors.map(view));
    rec.rec("S09", "S09.mode-badge", "switching to scripted is announced in every browser: badge SCRIPTED and mode SCRIPTED", !!mode && vm.every((v) => v.badges[0] === "SCRIPTED" && /mode SCRIPTED/.test(v.header ?? "")),
      { event: mode?.eventId, badges: vm.map((v) => v.badges[0]), headers: vm.map((v) => (v.header ?? "").match(/mode \w+/)?.[0]) }, ["FR-028", "NFR-009"]);
    // Something to decide on: the dock job again (the scripted witness always reports).
    await refreshIdent(A);
    const far = await until(async () => { const s = await api.snapshot(); return police(s).every((n) => dist(n, MISSION_SPOTS[0]!) > 20) ? true : null; }, 25000, 200);
    const w = await walk(A, MISSION_SPOTS[0]!, { tol: 1.3 });
    const seqJ = log.lastSeq;
    await press(A, "e");
    const job = await log.waitFor("MISSION_COMPLETED", () => true, 5000, seqJ);
    await settle(7000, 1400);
    await log.pull();
    const reqAfter = new Set(log.of("DECISION_REQUESTED", mode?.serverSequence ?? seqM).map((e) => e.payload.decisionId));
    const res = log.of("DECISION_RESOLVED", mode?.serverSequence ?? seqM).filter((e) => reqAfter.has(e.payload.decisionId));
    const wrong = res.filter((e) => e.payload.decisionSource !== "scripted" || e.payload.provider !== null || e.payload.confidence !== null || e.payload.probabilities !== null || e.payload.providerRequestId !== null);
    rec.rec("S09", "S09.scripted-labels", "decisions requested in scripted mode are labeled scripted, never Drex: no provider, no request id, no confidence",
      !!job && res.length >= 1 && wrong.length === 0,
      { policeFar: !!far, jobDone: job?.eventId ?? `walk ${w.ok ? "ok" : w.reason}`, decisions: res.length, list: res.map((e) => `${e.payload.role}:${e.payload.chosenAction}:${e.payload.decisionSource}`), offending: wrong.slice(0, 3).map((e) => ({ id: e.payload.decisionId, source: e.payload.decisionSource, provider: e.payload.provider, confidence: e.payload.confidence })) }, ["FR-028", "AT-03", "NFR-009"]);
    const sNow = await api.snapshot();
    const w1 = sNow.npcs.find((n) => n.lastDecision?.decisionSource === "scripted");
    if (w1) {
      const chip = await selectAgentByChip(C, w1.name);
      const vc = await until(async () => { const v = await view(C); return v.agentName === w1.name ? v : null; }, 3000, 100) ?? await view(C);
      if (!chip.ok) rec.note(`S09: could not open ${w1.name} through its chip: ${chip.error}`);
      rec.rec("S09", "S09.scripted-ui", "the Agent Inspector labels a scripted decision as a scripted rule and shows no model figures", vc.decSrc === "SCRIPTED rule" && /No model was called/.test(vc.decMeta ?? "") && vc.decBars === 0 && !/confidence \d/.test(vc.agent ?? ""),
        { agent: vc.agentName, badge: vc.decSrc, choice: vc.decChoice, meta: vc.decMeta, probabilityBars: vc.decBars }, ["FR-028", "NFR-009"]);
    } else rec.rec("S09", "S09.scripted-ui", "the Agent Inspector labels a scripted decision as a scripted rule", "blocked", "no NPC had a scripted last decision to inspect", ["FR-028"]);
    await shots.all(actors, "09-director-scripted");

    // ---- experiment: shortest duration the command accepts (20 s). The button in the panel uses the 45 s default,
    //      so the 20 s run is sent through the director page's own socket. ----
    const before = (await api.experiments() as Array<{ id: string }>).map((x) => x.id);
    let sentVia = "page socket, durationSec 20";
    const ack = await pageSend(A, { type: "demo.runExperiment", commandId: `e2e-exp-${Date.now()}`, durationSec: 20 });
    if (!ack?.ok) { sentVia = `button (45 s default); page socket said ${ack?.error?.code}: ${ack?.error?.message}`; await click(A, '.director button:has-text("Run scripted vs Meta vs Drex")', { timeout: 5000 }).catch(() => {}); }
    type Exp = { id: string; status: string; seed: number; durationSec: number; caveat: string; rows: Array<Record<string, unknown>> };
    const running = await until(async () => (await api.experiments() as Exp[]).find((x) => !before.includes(x.id)) ?? null, 8000, 200);
    await sleep(2500);
    await shots.one(A, "09-director-experiment-running");
    const done = running ? await until(async () => { const x = (await api.experiments() as Exp[]).find((q) => q.id === running.id); return x && x.status !== "RUNNING" ? x : null; }, 80000, 500) : null;
    await sleep(800);
    await openTab(A, "director");
    const vd2 = await view(A);
    const table = await A.page.evaluate(`(() => { const x = document.querySelector(".director .exp"); if (!x) return null; return { head: x.querySelector(".exp-head") ? x.querySelector(".exp-head").innerText : null, headers: Array.from(x.querySelectorAll("thead th")).map((t) => t.innerText), rows: Array.from(x.querySelectorAll("tbody tr")).map((r) => Array.from(r.querySelectorAll("td")).map((t) => t.innerText)), caveat: x.querySelector(".caveat") ? x.querySelector(".caveat").innerText : null }; })()`) as { head: string | null; headers: string[]; rows: string[][]; caveat: string | null } | null;
    measured.experiment = done ?? running ?? null;
    const modes = (done?.rows ?? []).map((r) => r.mode);
    const scriptedRow = done?.rows.find((r) => r.mode === "scripted");
    rec.rec("S09", "S09.experiment", "the built-in experiment runs (20 s per mode) and the results table shows one row per mode with seed, measured numbers and a caveat",
      !!done && done.status === "DONE" && modes.length === 3 && !!table && table.rows.length === 3 && (table.head ?? "").includes(done.id) && /seed \d+/.test(table.head ?? "") && (table.caveat ?? "").length > 20 && scriptedRow?.realProviderCalls === 0,
      { sentVia, experiment: done?.id ?? running?.id ?? null, status: done?.status ?? "did not finish", durationSec: done?.durationSec, head: table?.head, headers: table?.headers, table: table?.rows, caveat: table?.caveat, apiRows: done?.rows }, ["FR-028", "FR-065", "Gate E"]);
    void vd2;
    await shots.all(actors, "09-director");
    await shots.one(A, "12-views-director");
    // Did the scripted police make the arrest on their own while the experiment ran?
    await log.pull();
    const npcCap = log.of("CAPTURE_SUCCEEDED", seqJ).find((e) => e.payload.captorKind === "NPC");
    rec.note(npcCap ? `S09: in scripted mode a police NPC captured the outlaw on its own (${npcCap.eventId}, captor ${npcCap.payload.captorName}, bounty ${npcCap.payload.bounty}, captorWalletId ${npcCap.payload.captorWalletId})` : "S09: no police NPC capture happened during the scripted run (not required)");
    await collectTraces();
    await verifyAt("after the scripted run and the experiment");
  });

  // =====================================================================================================
  await step("S12", async () => {
    // Back to Drex and a clean seeded world for the view captures.
    await openTab(A, "director");
    const seq = log.lastSeq;
    await click(A, '.director .seg.wide button:has-text("DREX")', { timeout: 6000 }).catch(() => {});
    await log.waitFor("MODE_CHANGED", (e) => e.payload.mode === "drex", 4000, seq);
    await click(A, '.director button:has-text("Seeded reset")', { timeout: 6000 }).catch(() => {});
    await log.waitFor("ROOM_RESET", () => true, 5000, seq);
    await sleep(1800);
    for (const a of actors) await refreshIdent(a);
    for (const a of actors) await openTab(a, "timeline");
    // The experiment switches every browser to the director tab and enlarges the bottom panel. Put it back like a presenter would.
    const stuck: string[] = [];
    for (const a of actors) {
      const big = await a.page.evaluate(`(() => { const b = Array.from(document.querySelectorAll(".tabs button")).find((x) => /smaller/.test(x.innerText)); const c = document.querySelector(".city canvas"); return b ? Math.round(c ? c.getBoundingClientRect().height : 0) : null; })()`) as number | null;
      if (big !== null) { stuck.push(`${a.key} (${a.role}): city ${big} px tall`); await click(a, '.tabs button:has-text("smaller")').catch(() => {}); }
    }
    if (stuck.length) rec.note(`S12: after the experiment the bottom panel was still enlarged and the city squeezed in ${stuck.join("; ")}. The harness clicked "smaller" to restore the layout before capturing the views.`);
    measured.panel_left_enlarged_after_experiment = stuck;
    await sleep(600);
    const to2d = await Promise.all(actors.map((a) => setView(a, "2D")));
    await sleep(900);
    const v2d = await Promise.all(actors.map(view));
    const flat = "Live map of Coral Bay";
    const stats2d = await Promise.all(actors.map(async (a, i) => (v2d[i]!.canvas ? (await shots.region(a, v2d[i]!.canvas!))?.stats ?? null : null)));
    rec.rec("S12", "S12.2d", "the 2D city renders in every browser (not a blank canvas)", v2d.every((v) => (v.canvas?.label ?? "").startsWith(flat)) && stats2d.every((s) => !!s && s.distinctColours >= 40 && s.lumaStdDev >= 6),
      { toggledTo2d: to2d, defaultViewAtJoin: env.defaultView, canvas: v2d.map((v) => v.canvas && `${Math.round(v.canvas.w)}x${Math.round(v.canvas.h)}`), stats: stats2d }, ["NFR-001"]);
    await shots.all(actors, "12-views-2d");

    // ---- the swarm graph tab, when the build has one ----
    const hasSwarm = v2d[2]!.tabs.some((t) => /SWARM/i.test(t ?? ""));
    if (hasSwarm) {
      await click(C, '.tabs button:has-text("SWARM GRAPH")').catch(() => {});
      await sleep(1800);
      const host = await C.page.evaluate(`(() => { const h = document.querySelector(".swarm-host") || document.querySelector(".bottom"); if (!h) return null; const r = h.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, nodes: h.querySelectorAll("svg circle, svg g, canvas").length, text: (h.innerText || "").replace(/\\s+/g, " ").slice(0, 300) }; })()`) as { x: number; y: number; w: number; h: number; nodes: number; text: string } | null;
      const cap = host ? await shots.region(C, host) : null;
      rec.rec("S12", "S12.swarm-graph", "the Swarm Graph tab renders the agent network (not an empty panel)", !!host && !!cap && cap.stats.distinctColours >= 30 && cap.stats.lumaStdDev >= 4 && host.nodes > 0,
        { size: host && `${Math.round(host.w)}x${Math.round(host.h)}`, elements: host?.nodes, stats: cap?.stats ?? null, text: host?.text }, ["NFR-001"]);
      await shots.one(C, "12-views-swarm-graph");
    }

    // ---- 3D through the header toggle ----
    const results: Array<Record<string, unknown>> = [];
    const autoLow: string[] = [];
    for (const a of actors) {
      const v0 = await view(a);
      const btn = v0.seg.find((b) => b.text === "3D");
      if (!btn || btn.disabled) { results.push({ ctx: a.key, ok: false, why: "the 3D button is missing or disabled" }); continue; }
      const errsBefore = a.console.length;
      const up = await setView(a, "3D");
      if (up) {
        const q0 = ((await view(a)).cityText ?? "").match(/QUALITY: (HIGH|LOW)/)?.[1] ?? null;
        if (q0 === "LOW") { autoLow.push(a.key); await click(a, '.city button:has-text("QUALITY")').catch(() => {}); }
      }
      await sleep(up ? 4500 : 500);
      const v = await view(a);
      const fps = await a.page.evaluate(`new Promise((r) => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 >= 1500) r(Math.round(n / 1.5)); else requestAnimationFrame(f); }; requestAnimationFrame(f); })`).catch(() => null);
      const rect = await a.page.evaluate(`(() => { const c = document.querySelector(".city"); const r = c.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()`) as { x: number; y: number; w: number; h: number };
      const cap = await shots.region(a, rect);
      const quality = (v.cityText ?? "").match(/QUALITY: (HIGH|LOW)/)?.[1] ?? null;
      const fresh = a.console.slice(errsBefore).filter((l) => /scene|3D|WebGL|THREE|shader|context/i.test(l));
      const ok = !!up && !!cap && cap.stats.distinctColours >= 60 && cap.stats.lumaStdDev >= 8 && cap.stats.dominantShare < 0.85;
      results.push({ ctx: a.key, ok, rendered3d: !!up, fellBackTo2d: (v.canvas?.label ?? "").startsWith(flat), fps, quality, stats: cap?.stats ?? null, console: fresh.slice(0, 6) });
    }
    await shots.all(actors, "12-views-3d");
    if (autoLow.length) rec.note(`S12: the 3D view had dropped itself to QUALITY: LOW (no bloom, no shadows) in ${autoLow.join(", ")} earlier in the run, with three browsers rendering at once; the harness clicked the QUALITY button back to HIGH for the captures.`);
    measured.view3d_auto_low = autoLow;
    rec.rec("S12", "S12.3d", "the 2D/3D toggle switches every browser to a rendered 3D city (WebGL frame with real content, no fallback to 2D)", results.every((r) => r.ok === true), { webgl: env.webgl, perBrowser: results }, ["NFR-001", "PRD-D1"]);
    measured.view3d = results;

    // ---- the 3D city while something happens: the dock job with a live swarm reaction ----
    const alive = await charA();
    if (alive && (alive.status === "ACTIVE" || alive.status === "WANTED")) {
      const w = await walk(A, MISSION_SPOTS[0]!, { tol: 1.3 });
      const seqJ = log.lastSeq;
      await press(A, "e");
      const job = await log.waitFor("MISSION_COMPLETED", () => true, 4000, seqJ);
      await sleep(700);
      await shots.all(actors, "12-views-3d-incident");
      await sleep(900);
      await shots.all(actors, "12-views-3d-reaction");
      await sleep(1500);
      await shots.all(actors, "12-views-3d-aftermath");
      rec.rec("S12", "S12.3d-play", "the game is playable in the 3D view: WASD walks the outlaw to the marker and E completes the job", w.ok && !!job, { walk: w.ok ? "ok" : w.reason, job: job?.eventId ?? null }, ["NFR-001"]);
      await settle(6000, 1200);
      await collectTraces();
    }
    if (hasSwarm) { await sleep(300); await shots.one(C, "12-views-swarm-graph-live"); await openTab(C, "timeline"); }
    // Back to 2D.
    const back = await Promise.all(actors.map((a) => setView(a, "2D")));
    await sleep(900);
    const vBack = await Promise.all(actors.map(view));
    rec.rec("S12", "S12.toggle-back", "the toggle returns every browser to the 2D city", back.every(Boolean) && vBack.every((v) => (v.canvas?.label ?? "").startsWith(flat)), { labels: vBack.map((v) => v.canvas?.label) }, ["NFR-001"]);
    await shots.all(actors, "12-views-2d-live");
    // Leave the players in the cinematic view for the remaining captures.
    await Promise.all([setView(A, "3D"), setView(B, "3D")]);

    // ---- WORLD: the embedded Godot city, when the header offers it (time-boxed; the hunter's browser) ----
    if (vBack[1]!.seg.some((b) => b.text === "WORLD") && process.env.E2E_SKIP_WORLD !== "1") {
      const before = B.console.length;
      await click(B, '.hdr .seg button:text-is("WORLD")').catch(() => {});
      const rect = await B.page.evaluate(`(() => { const r = document.querySelector(".city").getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()`) as { x: number; y: number; w: number; h: number };
      const series: Array<Record<string, unknown>> = [];
      let good = 0;
      const t0 = Date.now();
      while (Date.now() - t0 < 75000 && good < 2) {
        await sleep(5000);
        const st = await B.page.evaluate(`(() => { const f = document.querySelector(".world-frame"); if (!f) return null; return { iframe: !!f.querySelector("iframe"), loading: !!f.querySelector(".world-loading"), msg: (f.innerText || "").replace(/\\s+/g, " ").slice(0, 140) }; })()`).catch(() => null) as { iframe: boolean; loading: boolean; msg: string } | null;
        const cap = await shots.region(B, rect);
        series.push({ sec: Math.round((Date.now() - t0) / 1000), iframe: st?.iframe ?? false, loadingNotice: st?.loading ?? null, message: st?.msg || undefined, colours: cap?.stats.distinctColours ?? null, lumaStdDev: cap?.stats.lumaStdDev ?? null });
        good = st?.iframe && !st.loading && cap && cap.stats.distinctColours >= 150 && cap.stats.lumaStdDev >= 10 ? good + 1 : 0;
      }
      await shots.one(B, "12-views-world");
      const frameErrors = B.console.slice(before).filter((l) => !noiseLine(l)).slice(0, 6);
      rec.rec("SX", "SX.world-view", "the WORLD toggle loads the embedded 3D world and it draws a scene (not a loading or error screen) within 75 s", good >= 2,
        { samples: series.slice(-5), console: frameErrors, bridge: `ws://127.0.0.1:${serverPort}/bridge (the build defaults to port 3001 unless VITE_BRIDGE_URL or ?bridge= is given)` }, ["PRD-D1"]);
      await click(B, '.hdr .seg button:text-is("3D")').catch(() => {});
      await sleep(800);
    }
    rec.rec("S12", "S12.captures", "inspector, timeline chain, market crash and director captures were taken",
      ["12-views-inspector-C", "12-views-timeline-chain-C", "12-views-market-crash-A", "12-views-director-A"].every((n) => rec.screenshots.some((s) => s.endsWith(`${n}.png`))),
      { files: rec.screenshots.filter((s) => /12-views/.test(s)).map((s) => path.basename(s)) }, []);
  });

  // =====================================================================================================
  await step("S10", async () => {
    await verifyAt("end of the session");
    rec.rec("S10", "S10.verify", "/replay/verify reports ok with zero mismatches at every checkpoint (events folded without any model call)",
      verifies.length >= 3 && verifies.every((v) => v.ok && v.mismatches.length === 0), verifies, ["AT-08", "FR-063", "Gate E"]);
    const first = verifies.find((v) => v.at.startsWith("end of the first run"));
    const loggedFirstRun = log.all.filter((e) => e.runId === firstRun && e.serverSequence <= firstRunLastSeq).length;
    rec.rec("S10", "S10.store", "the replay of the main run read its events from the persistent store and covered every event of that run", !!first && first.source === "store" && first.runId === firstRun && first.events >= 40 && first.events === loggedFirstRun,
      { checkpoint: first ?? "none", eventsSeenByTheHarnessForThatRun: loggedFirstRun }, ["AT-08", "NFR-005"]);
    await shots.all(actors, "10-replay");
  });

  // =====================================================================================================
  await step("S11", async () => {
    const fo = await failover;
    measured.provider_failure = {
      decisions: fo.traces.length, bySource: fo.traces.reduce<Record<string, number>>((m, t) => { const k = t.decisionSource ?? "pending"; m[k] = (m[k] ?? 0) + 1; return m; }, {}),
      errorTypes: fo.traces.reduce<Record<string, number>>((m, t) => { const k = t.errorType ?? "none"; m[k] = (m[k] ?? 0) + 1; return m; }, {}), badge: fo.finalBadge, events: fo.events.length,
    };
    // What a judge would see on that instance.
    const J = await newActor(browser!, "C", "JUDGE", "SPECTATOR");
    try {
      await J.page.goto(`${failWebBase}/?nick=JUDGE&role=SPECTATOR&room=${ROOM}&auto=1`, { waitUntil: "load", timeout: 45000 });
      await J.page.waitForSelector(".app", { timeout: 25000 });
      await sleep(1500);
      const s = await new Api(failBase, ROOM).snapshot();
      const n = s.npcs.find((x) => x.lastDecision?.decisionSource === "fallback");
      const chip = n ? await selectAgentByChip(J, n.name) : { ok: false, attempts: 0, error: "no NPC on the failing instance has a fallback decision" };
      await sleep(400);
      const v = await view(J);
      if (!chip.ok) rec.note(`S11: could not open an NPC through its chip on the failing instance: ${chip.error}`);
      rec.rec("S11", "S11.ui", "the UI of the failing instance never shows a fabricated model result: fallback badge in the header, FALLBACK rule in the inspector, no confidence and no probability bars",
        !!n && v.badges[0] !== "DREX: LIVE" && /FALLBACK|DOWN/.test(v.badges[0] ?? "") && v.decSrc === "FALLBACK rule" && v.decBars === 0 && !/confidence \d/.test(v.agent ?? "") && /No model was called/.test(v.decMeta ?? ""),
        { headerBadge: v.badges[0], agent: v.agentName, badge: v.decSrc, choice: v.decChoice, meta: v.decMeta, probabilityBars: v.decBars, warning: (v.agent ?? "").match(/(auth|circuit_open|unconfigured|timeout|network)[^·]{0,80}/)?.[0] ?? null }, ["AT-07", "NFR-009"]);
      const file = path.join(SHOTS, "11-provider-failure-C.png");
      await J.page.screenshot({ path: file, fullPage: true }); rec.screenshots.push(file);
    } catch (e) { rec.rec("S11", "S11.ui", "the UI of the failing instance never shows a fabricated model result", "blocked", `could not open the failing instance in a browser: ${(e as Error).message.slice(0, 200)}`, ["AT-07"]); }
    finally { await J.ctx.close().catch(() => {}); }
    await shots.all([A, B], "11-provider-failure");
  });

  // ---------- cross-cutting ----------
  rec.rec("S02", "S02.bounds", "every server position sampled during the run was inside the map and outside every building", illegal.length === 0 && samples > 50, { samples, violations: illegal.slice(0, 5) }, ["FR-003"]);
  const leaked = await Promise.all(actors.map((a) => a.page.evaluate(`(() => { const s = window.__vice ? JSON.stringify(window.__vice.getState()) : ""; return /nace_sk_|Bearer /.test(s + document.documentElement.innerHTML); })()`).catch(() => false)));
  rec.rec("S11", "S11.no-key-in-browser", "no API key material appears in any page's DOM or client state", leaked.every((x) => x === false), { pagesChecked: leaked.length }, ["NFR-006"]);
  const noise = noiseLine;
  const pageErrors = actors.map((a) => ({ ctx: a.key, errors: [...new Set(a.errors.filter((l) => !noise(l) && !worldLine(l)))].slice(0, 8), favicon404: a.errors.filter(noise).length, fromWorldFrame: a.errors.filter(worldLine).length, warnings: [...new Set(a.console.filter((l) => l.startsWith("[warning]") && !worldLine(l)))].slice(0, 6) }));
  measured.console = pageErrors;
  rec.rec("S12", "S12.console", "no uncaught page errors or console errors in any browser during the run", pageErrors.every((p) => p.errors.length === 0), pageErrors, ["NFR-001"]);
  const serverErr = (server?.tail ?? []).filter((l) => /\[(engine|brains|persistence|metrics|transport|room|experiment|decisions|bridge)\].*(fail|threw|error)/i.test(l));
  rec.rec("S10", "S10.server-log", "the server logged no internal errors during the run", serverErr.length === 0, { lines: serverErr.slice(0, 8), log: path.relative(ROOT, server?.logFile ?? "") }, ["NFR-002"]);

  await collectTraces();
  measured.decisions_all_runs = allTraces.size;
  measured.sources_all_runs = [...allTraces.values()].reduce<Record<string, number>>((m, t) => { const k = t.decisionSource ?? "pending"; m[k] = (m[k] ?? 0) + 1; return m; }, {});
  await log.close();
  writeFileSync(path.join(OUT, "events.json"), JSON.stringify(log.all));
  writeFileSync(path.join(OUT, "decisions.json"), JSON.stringify([...allTraces.values()]));
  measured.events_logged = log.all.length;
}

function finish(fatal: string | null): number {
  const steps = Object.keys(STEP_TITLES).map((id) => {
    const list = rec.checks.filter((c) => c.step === id);
    const status = list.length ? (list.some((c) => c.status === "fail") ? "fail" : list.some((c) => c.status === "blocked") ? "blocked" : "pass") : "blocked";
    return { id, title: STEP_TITLES[id]!, status, checks: list };
  });
  const refs = [...new Set(rec.checks.flatMap((c) => c.refs))].sort();
  const acceptance = refs.map((r) => ({ ref: r, status: rec.statusOf((c) => c.refs.includes(r)), checks: rec.checks.filter((c) => c.refs.includes(r)).map((c) => `${c.id}:${c.status}`) }));
  const failed = steps.filter((s) => s.status !== "pass");
  const report = {
    startedAt: startedAt.toISOString(), finishedAt: new Date().toISOString(), durationSec: Math.round((Date.now() - startedAt.getTime()) / 1000),
    ok: !fatal && failed.length === 0, fatal, environment: env, steps, acceptance, measured, notes: rec.notes,
    screenshotsDir: SHOTS, screenshots: rec.screenshots, logs: existsSync(path.join(OUT, "logs")) ? readdirSync(path.join(OUT, "logs")).map((f) => path.join(OUT, "logs", f)) : [],
  };
  writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
  try { writeHtmlReport(path.join(OUT, "report.html"), report); } catch (e) { console.error("could not write report.html:", (e as Error).message); }
  console.log("\n================ SUMMARY ================");
  for (const s of steps) console.log(`${s.status.toUpperCase().padEnd(8)} ${s.id} ${s.title}  (${s.checks.filter((c) => c.status === "pass").length}/${s.checks.length} checks passed)`);
  for (const a of acceptance.filter((x) => /^(AT-|Gate)/.test(x.ref))) console.log(`${String(a.status).toUpperCase().padEnd(8)} ${a.ref}`);
  console.log(`measured: ${JSON.stringify({ drex_decisions: measured.drex_decisions, drex_latency_ms_p50: measured.drex_latency_ms_p50, fallback_decisions: measured.fallback_decisions, roles_with_live_decision: measured.roles_with_live_decision, live_applied_cycles: measured.live_applied_cycles })}`);
  console.log(`report: ${path.join(OUT, "report.json")}\nscreenshots: ${SHOTS} (${rec.screenshots.length} files)`);
  if (fatal) console.log(`FATAL: ${fatal}`);
  return !fatal && failed.length === 0 ? 0 : 1;
}

let exiting = false;
async function shutdown(): Promise<void> {
  if (exiting) return; exiting = true;
  try { await browser?.close(); } catch { /* already closed */ }
  await stopAll();                                    // only the four processes this harness started
  try { writeFileSync(path.join(OUT, "pids.json"), "[]"); } catch { /* ignore */ }
}
process.on("SIGINT", () => { void shutdown().then(() => process.exit(130)); });
process.on("SIGTERM", () => { void shutdown().then(() => process.exit(143)); });
process.on("exit", () => stopAllSync());

let fatal: string | null = null;
try { await main(); }
catch (e) { fatal = (e as Error).stack ?? String(e); console.error("harness aborted:", fatal); }
const code = finish(fatal);
await shutdown();
process.exit(code);
