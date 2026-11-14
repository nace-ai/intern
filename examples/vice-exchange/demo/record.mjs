// Records a real run of VICE EXCHANGE in the 3D city view: headless Chrome driven in real time over the Chrome DevTools
// Protocol against a real server with real Drex decisions. Frames come from Page.startScreencast (every frame, acked),
// one page at a time wherever possible so motion stays smooth.
// Usage: OUT=<take dir> BASE=http://localhost:5781 API=http://localhost:3781 node demo/record.mjs
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const OUT = process.env.OUT, BASE = process.env.BASE, API = process.env.API;
const CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const marks = [];
const mark = (name, extra = {}) => { marks.push({ name, t: Date.now(), ...extra }); console.log("MARK", name, JSON.stringify(extra)); };
const kids = [];
const save = (extra = {}) => writeFileSync(`${OUT}/marks.json`, JSON.stringify({ marks, ...extra }, null, 0));

async function browser(port, tag) {
  mkdirSync(`${OUT}/${tag}`, { recursive: true });
  const p = spawn(CH, ["--headless=new", `--remote-debugging-port=${port}`, "--window-size=1600,900", "--hide-scrollbars", "--use-angle=metal",
    "--ignore-gpu-blocklist", "--disable-background-timer-throttling", "--disable-renderer-backgrounding", `--user-data-dir=${OUT}/profile-${tag}`, "about:blank"], { stdio: "ignore" });
  kids.push(p);
  for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch {} await sleep(250); }
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const frames = []; let seq = 0; let casting = false; const logs = []; let lastFrame = 0; let shotMode = false; let restarts = 0; let started = false; let recent = [];
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
    if (d.method === "Page.screencastFrame") {
      send("Page.screencastFrameAck", { sessionId: d.params.sessionId });
      lastFrame = Date.now();
      if (!casting) return;
      const f = `${tag}/${String(++seq).padStart(6, "0")}.jpg`;
      writeFileSync(`${OUT}/${f}`, Buffer.from(d.params.data, "base64"));
      frames.push({ f, t: Date.now() }); lastFrame = Date.now();
    }
    if (d.method === "Runtime.exceptionThrown") logs.push("EXC " + (d.params.exceptionDetails.exception?.description ?? d.params.exceptionDetails.text).slice(0, 200));
  };
  // Watchdog: headless Chrome sometimes stops delivering screencast frames. Restart the screencast, and if it stalls
  // again fall back to a screenshot loop for this page so the take is never lost.
  const shotLoop = async () => { while (casting && shotMode) { const r = await send("Page.captureScreenshot", { format: "jpeg", quality: 70 }); if (!casting || !r.result?.data) continue; const f = `${tag}/${String(++seq).padStart(6, "0")}.jpg`; writeFileSync(`${OUT}/${f}`, Buffer.from(r.result.data, "base64")); frames.push({ f, t: Date.now() }); lastFrame = Date.now(); } };
  setInterval(async () => {
    if (!started || shotMode || Date.now() - lastFrame < 1500) return;
    restarts++; const now = Date.now(); recent = recent.filter((t) => now - t < 12000); recent.push(now);
    if (casting) console.log("WATCHDOG", tag, "screencast stalled, restart", restarts);
    if (recent.length > 6) { console.log("WATCHDOG", tag, "switching to screenshot loop"); shotMode = true; send("Page.stopScreencast"); if (casting) shotLoop(); return; }
    lastFrame = Date.now();
    await send("Page.stopScreencast"); await send("Page.startScreencast", { format: "jpeg", quality: 70, maxWidth: 1600, maxHeight: 900, everyNthFrame: 1 });
  }, 400).unref();
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  const key = (type, k) => send("Input.dispatchKeyEvent", { type, key: k, code: "Key" + k.toUpperCase(), windowsVirtualKeyCode: k.toUpperCase().charCodeAt(0), nativeVirtualKeyCode: k.toUpperCase().charCodeAt(0) });
  const mouse = (type, x, y, extra = {}) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, ...extra });
  return {
    tag, frames, logs, send, proc: p,
    nav: (url) => send("Page.navigate", { url }),
    async cast(on) {
      if (on && !casting) { casting = true; lastFrame = Date.now(); if (shotMode) { shotLoop(); return; } if (started) return; started = true; await send("Page.startScreencast", { format: "jpeg", quality: 70, maxWidth: 1600, maxHeight: 900, everyNthFrame: 1 }); }
      else if (!on) casting = false; // the screencast keeps running; frames are acked and dropped
    },
    async hold(k, ms) { await key("rawKeyDown", k); await sleep(ms); await key("keyUp", k); },
    async press(k) { await key("rawKeyDown", k); await sleep(70); await key("keyUp", k); },
    /** Left-drag on the 3D canvas: orbits the camera. */
    async drag(x0, y0, x1, y1, ms = 900) {
      await mouse("mouseMoved", x0, y0, { buttons: 0 }); await mouse("mousePressed", x0, y0);
      const n = Math.max(6, Math.round(ms / 30));
      for (let i = 1; i <= n; i++) { await mouse("mouseMoved", x0 + ((x1 - x0) * i) / n, y0 + ((y1 - y0) * i) / n); await sleep(ms / n); }
      await mouse("mouseReleased", x1, y1);
    },
    wheel: (x, y, dy) => send("Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX: 0, deltaY: dy }),
    async js(expr) { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }); return r.result?.result?.value; },
    clickText: function (re, sel = "button") { return this.js(`(()=>{const b=[...document.querySelectorAll(${JSON.stringify(sel)})].find(x=>${re}.test(x.textContent||""));if(!b)return false;b.click();return (b.textContent||"").trim().slice(0,60)})()`); },
    close() { try { p.kill(); } catch {} },
  };
}
const snap = async () => (await fetch(`${API}/api/rooms/VICE-42/snapshot`)).json();
const live = (s, nick) => s.characters.find((c) => c.nickname === nick && !["CAPTURED", "ARCHIVED"].includes(c.status));
/** Walks with the chase camera at its default yaw, where W is north (-z) and D is east (+x). Nudges sideways if stuck. */
async function walkTo(P, nick, tx, tz, maxSteps = 70, near = 1.5) {
  let last = null, stuck = 0;
  for (let i = 0; i < maxSteps; i++) {
    const c = live(await snap(), nick); if (!c) return false;
    const dx = tx - c.x, dz = tz - c.z; if (Math.hypot(dx, dz) < near) return true;
    if (last && Math.hypot(c.x - last.x, c.z - last.z) < 0.15) stuck++; else stuck = 0;
    last = { x: c.x, z: c.z };
    let k = Math.abs(dx) > Math.abs(dz) ? (dx > 0 ? "d" : "a") : (dz > 0 ? "s" : "w");
    if (stuck >= 2) { k = "wasd"[(i + stuck) % 4]; stuck = 0; await P.hold(k, 350); continue; }
    await P.hold(k, Math.min(500, Math.max(70, (Math.max(Math.abs(dx), Math.abs(dz)) / 9) * 800)));
  }
  return false;
}
const route = async (P, nick, pts) => { for (const [x, z] of pts) if (!(await walkTo(P, nick, x, z, 45, 2.2))) return false; return true; };
const fpsOf = (fr, t0, t1) => { const f = fr.filter((x) => x.t >= t0 && x.t <= t1); return f.length > 1 ? Number((f.length / ((t1 - t0) / 1000)).toFixed(1)) : 0; };
// Disk guard: stop cleanly if free space on the take volume falls under 3 GB.
import { execFileSync as __x } from "node:child_process";
const freeGb = () => { try { const l = __x("df", ["-k", OUT]).toString().trim().split("\n").pop().split(/\s+/); return Number(l[3]) / 1048576; } catch { return 99; } };
setInterval(() => { const g = freeGb(); if (g < 3) { console.log("DISK GUARD: only", g.toFixed(1), "GB free, stopping"); try { save(all()); } catch {} for (const k of kids) try { k.kill(); } catch {} process.exit(3); } }, 20000).unref();
const SHORT = !!process.env.SHORT; // dry run: stops after the capture
const all = () => ({ framesA: A?.frames ?? [], framesB: B?.frames ?? [], framesC: C?.frames ?? [], framesD: D2?.frames ?? [] });

let A, B, C, D2;
const evlog = async () => (await (await fetch(`${API}/api/rooms/VICE-42/events?limit=1000`)).json()).events;
const traders = (s) => s.npcs.filter((n) => n.role === "TRADER").map((n) => ({ name: n.name, credits: n.walletCredits, holdings: n.holdings }));
const waitEvent = async (pred, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const e = (await evlog()).find(pred); if (e) return e; await sleep(400); } return null; };
try {
  // ---- Part 1: agent versus agent, seen by a spectator. The fugitive "Ryder" is itself a Drex-driven agent. ----
  C = await browser(9443, "C");
  await C.nav(`${BASE}/?nick=Judge&role=SPECTATOR&room=VICE-42&auto=1&view=3d`);
  await sleep(5000);
  let s = await snap();
  const ryder = s.characters.find((c) => /^Ryder/i.test(c.nickname) && c.role === "OUTLAW");
  await C.cast(true);
  mark("city", { ryder: ryder ? ryder.ticker : null, traders: traders(s) });
  await sleep(1500);
  // Two human hunters join as one crew (pages in the light 2D view, not recorded). The terminal shows the crew forming
  // and the agents who react to it; then the crew goes after the fugitive.
  B = await browser(9442, "B"); D2 = await browser(9444, "D");
  await B.nav(`${BASE}/?nick=Rio&role=HUNTER&room=VICE-42&auto=1&view=2d&crew=WOLVES`);
  await D2.nav(`${BASE}/?nick=Vex&role=HUNTER&room=VICE-42&auto=1&view=2d&crew=WOLVES`);
  await B.cast(true); await B.cast(false); await D2.cast(true); await D2.cast(false); // keep both pages in the foreground so they take input
  mark("crew0");
  let cap = null;
  if (ryder) {
    const tEnd = Date.now() + 75000;
    const chase = async (P, nick, fire) => {
      while (!cap && Date.now() < tEnd) {
        const sn = await snap(); const r = sn.characters.find((c) => /^Ryder/i.test(c.nickname) && ["ACTIVE", "WANTED"].includes(c.status)); const h = live(sn, nick);
        if (!r || !h) { await sleep(300); continue; }
        const d = Math.hypot(r.x - h.x, r.z - h.z);
        if (fire && r.wanted && d < 14) { await P.press("f"); marks.push({ name: "crewshot", t: Date.now(), d: Math.round(d) }); }
        if (fire && r.wanted && d < 2.6) { await P.press("e"); await sleep(250); continue; }
        // Close in along the road grid: go to the road row nearest the target first, then along it.
        await walkTo(P, nick, r.x + (fire ? 0.8 : -3), r.z + (fire ? 0.8 : 3), 3, fire ? 1.6 : 4);
      }
    };
    const watcher = (async () => { const tc = marks.find((m) => m.name === "city").t; cap = await waitEvent((e) => e.type === "CAPTURE_SUCCEEDED" && Date.parse(e.time) >= tc + 1500, 75000); })();
    await Promise.race([Promise.all([chase(B, "Rio", true), chase(D2, "Vex", false), watcher]), watcher]);
    const cs = (await snap()).crews ?? [];
    mark("rydercap", { captured: !!cap, by: cap ? cap.payload.captorName : null, kind: cap ? cap.payload.captorKind : null, bounty: cap ? cap.payload.bounty : null, crewName: cap ? cap.payload.crewName ?? null : null, doubleCross: cap ? !!cap.payload.doubleCross : false, shares: cap ? cap.payload.shares ?? null : null, t: cap ? Date.parse(cap.time) : null, crews: cs.map((c) => [c.name, c.memberPlayerIds.length]) });
    await sleep(11000); // the capture cinematic, then five clean seconds of the panels (agent P&L with real earnings)
  } else await sleep(8000);
  B.close(); D2.close();
  s = await snap();
  if (SHORT) { mark("ryder1", { traders: traders(s) }); throw new Error("short run end"); }
  mark("ryder1", { traders: traders(s), trades: s.trades.map((t) => [t.actorName, t.side, t.ticker, t.executionPrice]), listings: s.listings.map((l) => [l.ticker, l.status, l.spotPrice]) });
  // Inspector on the spectator page: what each agent knows and where it came from.
  const inspect = async (re, name, scroll) => {
    const chip = await C.clickText(re, "button.chip"); mark(name, { chip });
    await sleep(scroll ? 2600 : 4200);
    if (scroll) { await C.js(`(()=>{const el=[...document.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+40&&getComputedStyle(e).overflowY!=='visible'&&e.closest('.inspector, [class*=inspect], [class*=agent]'));const t=el[el.length-1];if(t){t.scrollBy({top:260,behavior:'smooth'});return t.className}return null})()`); await sleep(3200); }
    mark(name + "end"); await C.clickText(re, "button.chip");
  };
  await inspect("/Vega/", "insWitness");
  await inspect("/Castellan/", "insTrader1");
  await inspect("/Halvorsen/", "insTrader2");
  await inspect("/Duarte/", "insPolice", true);
  // Traders deciding on a fresh listing (the fugitive's respawn opens a new ticker): cards, trade feed, chart.
  mark("traders0");
  const relist = ryder ? await waitEvent((e) => e.type === "LISTING_OPENED" && Date.parse(e.time) > marks.find((m) => m.name === "ryder1").t - 8000, 20000) : null;
  await sleep(relist ? 7000 : 2000);
  s = await snap();
  mark("traders1", { relist: relist ? relist.payload.ticker : null, traders: traders(s), trades: s.trades.map((t) => [t.actorName, t.side, t.ticker, t.executionPrice]) });
  // Forty seconds of the city overview with nothing selected: background footage for the title and text cards.
  mark("bg0");
  await C.drag(500, 300, 640, 320, 6000); await sleep(14000); await C.drag(640, 320, 520, 300, 6000); await sleep(14000);
  mark("bg1");
  await C.cast(false);
  save(all());
  if (process.env.HUMANS) {
  // ---- Part 2: humans. An outlaw and a crew of two hunters. ----
  A = await browser(9441, "A");
  await A.nav(`${BASE}/?nick=Ace&role=OUTLAW&room=VICE-42&auto=1&view=3d`);
  B = await browser(9442, "B");
  await B.nav(`${BASE}/?nick=Rio&role=HUNTER&room=VICE-42&auto=1&view=3d&crew=WOLF`);
  D2 = await browser(9444, "D");
  await D2.nav(`${BASE}/?nick=Vex&role=HUNTER&room=VICE-42&auto=1&view=2d&crew=WOLF`);
  await sleep(7500);
  s = await snap();
  mark("crewinfo", { crews: (s.crews ?? []).map((c) => [c.name, c.memberPlayerIds.length]), who: s.characters.filter((c) => ["Ace", "Rio", "Vex"].includes(c.nickname)).map((c) => [c.nickname, Math.round(c.x), Math.round(c.z), c.crewName ?? null]) });
  await A.cast(true); await B.cast(true);
  mark("two");
  await Promise.all([
    (async () => { await A.hold("d", 800); await sleep(400); await A.hold("a", 800); await sleep(300); await A.drag(520, 330, 640, 310, 1200); })(),
    (async () => { await B.hold("s", 1300); await sleep(400); await B.hold("a", 900); await sleep(300); await B.hold("d", 900); })(),
  ]);
  await sleep(800);
  mark("twoend");
  await B.cast(false);
  await A.press("v");
  console.log("B to 2D:", await B.clickText("/^2D$/"));
  let ace = live(await snap(), "Ace");
  const staged = await Promise.all([
    route(B, "Rio", [[92, 54], [61, 54.5], [61, 65]]),
    route(D2, "Vex", [[4, 54], [40, 54.5], [42, 66]]),
  ]);
  mark("staged", { staged });
  // The hunt. The outlaw fires: each shot adds heat and is a sighting the NPCs who saw it decide on.
  mark("hunt0");
  for (let i = 0; i < 5; i++) {
    await A.press("f"); mark("outlawshot", { i });
    await sleep(2700);
    if (i === 0) await A.press("c");
    ace = live(await snap(), "Ace");
    if (!ace || ace.wanted) break;
  }
  mark("wanted", { wanted: !!ace?.wanted, heat: ace?.heat });
  await A.press("v");
  if (ace) await Promise.all([walkTo(B, "Rio", ace.x + 3, ace.z + 2, 30, 2.6), walkTo(D2, "Vex", ace.x - 5, ace.z + 4, 30, 4)]);
  let captured = false;
  for (let i = 0; i < 6 && !captured; i++) {
    await B.press("f"); mark("huntershot", { i });
    await sleep(600);
    ace = live(await snap(), "Ace");
    if (ace) { const h = live(await snap(), "Rio"); if (h && Math.hypot(h.x - ace.x, h.z - ace.z) > 2.1) await walkTo(B, "Rio", ace.x + 1, ace.z + 0.8, 10, 1.8); }
    await B.press("e");
    await sleep(600);
    s = await snap();
    captured = s.characters.some((c) => c.nickname === "Ace" && c.status === "CAPTURED");
    if (!captured) await sleep(1700);
  }
  const t0 = Date.now();
  while (!captured && Date.now() - t0 < 40000) { s = await snap(); captured = s.characters.some((c) => c.nickname === "Ace" && c.status === "CAPTURED"); await sleep(300); }
  s = await snap();
  const capEv = s.recentEvents.filter((e) => e.type === "CAPTURE_SUCCEEDED" && e.payload.ticker && /^ACE/.test(e.payload.ticker)).pop();
  mark("crash", { captured, t: capEv ? Date.parse(capEv.time) : null, by: capEv ? [[capEv.payload.captorName, capEv.payload.bounty, capEv.payload.survivalSeconds]] : [],
    crew: capEv ? { crewName: capEv.payload.crewName ?? null, doubleCross: !!capEv.payload.doubleCross, shares: capEv.payload.shares ?? null, captorKind: capEv.payload.captorKind } : null });
  mark("hunt1");
  save(all());
  await sleep(7500);
  mark("halted");
  if (SHORT) throw new Error("short run end");
  B.close(); D2.close();
  const rej = await A.clickText("/BUY 1/");
  mark("rejected", { clicked: rej });
  await sleep(2500);
  mark("respawn");
  await A.press("r");
  await sleep(1500);
  await A.hold("w", 900); await sleep(300); await A.hold("d", 900);
  await sleep(2500);
  mark("respawnend");
  await A.cast(false);
  A.close();
  }
  // ---- Part 3: proof, on the spectator page (first local browser, so it holds the director controls). ----
  s = await snap();
  await C.cast(true);
  mark("proof", { listings: s.listings.map((l) => [l.ticker, l.status, l.spotPrice]), metrics: s.metrics.decisions, latency: s.metrics.latency, shadow: s.metrics.shadow, traders: traders(s), trades: s.trades.map((t) => [t.actorName, t.side, t.ticker, t.executionPrice]) });
  await C.clickText("/DEMO DIRECTOR/");
  await sleep(800);
  console.log("larger:", await C.clickText("/larger/"));
  await sleep(1700);
  const exp = await C.clickText("/Run scripted/");
  console.log("experiment button:", exp);
  const t1 = Date.now(); let done = null;
  while (exp && Date.now() - t1 < 60000) {
    const xs = await (await fetch(`${API}/api/experiments`)).json();
    done = xs.find((x) => x.status === "DONE"); if (done) break;
    await sleep(700);
  }
  mark("expdone", { rows: done?.rows?.map((r) => ({ mode: r.mode, calls: r.realProviderCalls, p50: r.latencyP50Ms, p95: r.latencyP95Ms, trades: r.npcTrades, captured: r.captured })) });
  await sleep(1500);
  await C.js(`(()=>{const el=[...document.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+30&&getComputedStyle(e).overflowY!=='visible'&&e.querySelector('.exp-head'));const t=el[el.length-1];if(t){const h=t.querySelector('.exp-head');h.scrollIntoView({block:'start',behavior:'smooth'});return t.className}return null})()`);
  await sleep(5000);
  mark("proofend");
  await C.cast(false);
  await C.send("Emulation.setDeviceMetricsOverride", { width: 800, height: 450, deviceScaleFactor: 2, mobile: false });
  await C.nav(`${API}/api/rooms/VICE-42/replay/verify`);
  await sleep(1500);
  const r = await C.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${OUT}/verify.png`, Buffer.from(r.result.data, "base64"));
  const v = await (await fetch(`${API}/api/rooms/VICE-42/replay/verify`)).json();
  mark("verify", { ok: v.ok, source: v.source, events: v.eventCount, mismatches: v.mismatches.length });
} catch (e) { console.error("DRIVER END/ERROR", String(e).slice(0, 300)); }
finally {
  try {
    const evs = await (await fetch(`${API}/api/rooms/VICE-42/events?limit=1000`)).json();
    const T = Object.fromEntries(marks.map((m) => [m.name, m.t]));
    const rates = { ryder_chain_C: T.city && T.ryder1 ? fpsOf(C.frames, T.city, T.ryder1) : 0, two_A: A && T.two && T.twoend ? fpsOf(A.frames, T.two, T.twoend) : 0, two_B: B && T.two && T.twoend ? fpsOf(B.frames, T.two, T.twoend) : 0, hunt_A: A && T.hunt0 && T.hunt1 ? fpsOf(A.frames, T.hunt0, T.hunt1) : 0, crash_A: A && T.hunt1 && T.halted ? fpsOf(A.frames, T.hunt1, T.halted) : 0, proof_C: T.proof && T.proofend ? fpsOf(C.frames, T.proof, T.proofend) : 0 };
    console.log("CAPTURE FPS", JSON.stringify(rates));
    save({ ...all(), rates, events: evs.events.map((e) => ({ type: e.type, t: Date.parse(e.time), actor: e.actorId, p: e.type === "DECISION_RESOLVED" ? { src: e.payload.decisionSource, role: e.payload.role, action: e.payload.chosenAction, ms: e.payload.latencyMs, agent: e.payload.agentId } : e.type === "MISSION_COMPLETED" ? { ticker: e.payload.ticker } : e.type === "LISTING_OPENED" ? { ticker: e.payload.ticker } : ["TRADE_REJECTED", "WEAPON_FIRED", "STUNNED", "CAPTURE_SUCCEEDED", "CREW_JOINED", "CREW_BETRAYED", "CREW_TIP", "OUTLAW_WARNED"].includes(e.type) ? e.payload : undefined })), logsA: A?.logs, logsB: B?.logs, logsC: C?.logs });
  } catch (e) { console.error("SAVE ERROR", String(e)); try { save(all()); } catch {} }
  for (const k of kids) { try { k.kill(); } catch {} }
  process.exit(0);
}
