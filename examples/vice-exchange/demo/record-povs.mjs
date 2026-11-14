// One real run seen from three seats at once: Outlaw (3D), Hunter (3D) and Spectator (full dashboard). Drex mode, real time.
// Usage: OUT=<dir> BASE=http://localhost:5781 API=http://localhost:3781 node demo/record-povs.mjs
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
const OUT = process.env.OUT, BASE = process.env.BASE, API = process.env.API;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const kids = [];
const freeGb = () => { try { return Number(execFileSync("df", ["-k", OUT]).toString().trim().split("\n").pop().split(/\s+/)[3]) / 1048576; } catch { return 99; } };
async function browser(port, tag) {
  mkdirSync(`${OUT}/${tag}`, { recursive: true });
  const p = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["--headless=new", `--remote-debugging-port=${port}`, "--window-size=1600,900", "--hide-scrollbars", "--use-angle=metal", "--ignore-gpu-blocklist", "--disable-background-timer-throttling", "--disable-renderer-backgrounding", `--user-data-dir=${OUT}/profile-${tag}`, "about:blank"], { stdio: "ignore" });
  kids.push(p);
  for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break; } catch {} await sleep(250); }
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
  let id = 0, seq = 0, casting = false; const pending = new Map(); const frames = [];
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
    if (d.method === "Page.screencastFrame") { send("Page.screencastFrameAck", { sessionId: d.params.sessionId }); if (!casting) return; const f = `${tag}/${String(++seq).padStart(6, "0")}.jpg`; writeFileSync(`${OUT}/${f}`, Buffer.from(d.params.data, "base64")); frames.push({ f, t: Date.now() }); } };
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  const key = (type, k) => send("Input.dispatchKeyEvent", { type, key: k, code: "Key" + k.toUpperCase(), windowsVirtualKeyCode: k.toUpperCase().charCodeAt(0), nativeVirtualKeyCode: k.toUpperCase().charCodeAt(0) });
  return { tag, frames, nav: (url) => send("Page.navigate", { url }),
    async cast() { await send("Page.startScreencast", { format: "jpeg", quality: 70, maxWidth: 1600, maxHeight: 900, everyNthFrame: 1 }); casting = true; }, stop() { casting = false; },
    async hold(k, ms) { await key("rawKeyDown", k); await sleep(ms); await key("keyUp", k); }, async press(k) { await key("rawKeyDown", k); await sleep(70); await key("keyUp", k); },
    async js(expr) { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }); return r.result?.result?.value; },
    clickText(re, sel = "button") { return this.js(`(()=>{const b=[...document.querySelectorAll(${JSON.stringify(sel)})].find(x=>${re}.test(x.textContent||""));if(!b)return false;b.click();return (b.textContent||"").trim().slice(0,60)})()`); } };
}
const snap = async () => (await fetch(`${API}/api/rooms/VICE-42/snapshot`)).json();
const evlog = async () => (await (await fetch(`${API}/api/rooms/VICE-42/events?limit=1000`)).json()).events;
const live = (s, nick) => s.characters.find((c) => c.nickname === nick && !["CAPTURED", "ARCHIVED"].includes(c.status));
// Chase camera at its default yaw: W is north (-z), D is east (+x).
async function walkTo(P, nick, tx, tz, maxSteps = 70, near = 1.5, until = () => false) {
  let last = null, stuck = 0;
  for (let i = 0; i < maxSteps && !until(); i++) {
    const c = live(await snap(), nick); if (!c) return false;
    const dx = tx - c.x, dz = tz - c.z; if (Math.hypot(dx, dz) < near) return true;
    if (last && Math.hypot(c.x - last.x, c.z - last.z) < 0.15) stuck++; else stuck = 0; last = { x: c.x, z: c.z };
    let k = Math.abs(dx) > Math.abs(dz) ? (dx > 0 ? "d" : "a") : (dz > 0 ? "s" : "w");
    if (stuck >= 2) { k = "wasd"[(i + stuck) % 4]; stuck = 0; await P.hold(k, 350); continue; }
    await P.hold(k, Math.min(500, Math.max(70, (Math.max(Math.abs(dx), Math.abs(dz)) / 9) * 800)));
  }
  return false;
}
const marks = {};
try {
  const C = await browser(9471, "C"); await C.nav(`${BASE}/?nick=Judge&role=SPECTATOR&room=VICE-42&auto=1&view=3d`); await sleep(1500);
  const A = await browser(9472, "A"); await A.nav(`${BASE}/?nick=Ace&role=OUTLAW&room=VICE-42&auto=1&view=3d`);
  const B = await browser(9473, "B"); await B.nav(`${BASE}/?nick=Rio&role=HUNTER&room=VICE-42&auto=1&view=3d`);
  await sleep(7500);
  // Lighter 3D quality on the two player pages so three pages can be captured at once.
  console.log('quality:', await A.clickText('/QUALITY/'), await B.clickText('/QUALITY/'));
  await sleep(800);
  if (freeGb() < 3) throw new Error("disk guard");
  await Promise.all([A.cast(), B.cast(), C.cast()]);
  const t0 = Date.now(); marks.t0 = t0;
  const guard = setInterval(() => { if (freeGb() < 3) { console.log("DISK GUARD"); for (const k of kids) try { k.kill(); } catch {} process.exit(3); } }, 20000);
  let captured = null, J = null;
  // Hunter stages near the marina while the outlaw looks around and then walks to a job.
  const stage = (async () => { for (const [x, z] of [[92, 54], [63, 54]]) await walkTo(B, "Rio", x, z, 45, 2.2, () => !!J && Date.now() - J > 15000); })();
  await sleep(5000);
  let jobEv = null;
  // The fugitive agent may take a job first; the next one appears about 20 s later at the next spot. Wait there.
  const SPOTS = [[47, 60], [52, 48], [78, 23], [92, 54], [32, 28]];
  const tJob = Date.now();
  while (!jobEv && Date.now() - tJob < 70000) {
    const s = await snap(); const me = live(s, "Ace"); if (!me) break;
    const ms = s.missions.find((m) => m.status === "AVAILABLE");
    const [tx, tz] = ms ? [ms.x, ms.z] : SPOTS[s.missions.length % SPOTS.length];
    const there = await walkTo(A, "Ace", tx, tz, 12, 1.5);
    if (ms && there) { await A.press("e"); await sleep(500); }
    else if (!ms) await sleep(300);
    jobEv = (await evlog()).find((e) => e.type === "MISSION_COMPLETED" && /^ACE/.test(e.payload.ticker ?? "") && Date.parse(e.time) > t0);
  }
  if (!jobEv) throw new Error("outlaw could not take a job");
  J = Date.parse(jobEv.time); marks.J = J; console.log("job at", ((J - t0) / 1000).toFixed(1));
  const since = () => Date.now() - J;
  // Spectator opens the inspector on the witness that decided, at about J+10 s, and closes it at about J+17.5 s.
  const insp = (async () => { while (since() < 10000) await sleep(100);
    const w = (await evlog()).find((e) => e.type === "DECISION_RESOLVED" && e.payload.role === "WITNESS" && Date.parse(e.time) > J) ?? (await evlog()).find((e) => e.type === "DECISION_RESOLVED" && Date.parse(e.time) > J);
    const s = await snap(); const npc = s.npcs.find((n) => n.id === w?.payload.agentId) ?? s.npcs.find((n) => n.role === "WITNESS"); const last = npc.name.split(" ").pop();
    marks.insAgent = npc.id; marks.insChip = await C.clickText(`/${last}/`, "button.chip"); marks.insOpen = Date.now();
    while (since() < 17500) await sleep(100); await C.clickText(`/${last}/`, "button.chip"); marks.insClose = Date.now(); })();
  // The outlaw draws attention if no bulletin is out by J+7 s.
  const heat = (async () => { while (since() < 7000) await sleep(200); for (let i = 0; i < 3 && since() < 16000; i++) { const a = live(await snap(), "Ace"); if (!a || a.wanted) break; await A.press("f"); await sleep(2700); } })();
  await stage;
  // The hunter goes after whichever outlaw is wanted (the human outlaw or the fugitive agent): closes in, stuns from
  // J+18.5 s and tries the capture from J+23 s. Whoever makes a capture after J+21 s ends the hunt.
  let fired = 0; const capAfter = async () => (await evlog()).find((e) => e.type === "CAPTURE_SUCCEEDED" && Date.parse(e.time) > J + 21000) ?? null;
  while (!captured && since() < 45000) {
    const s = await snap(); const h = live(s, "Rio"); captured = s.recentEvents.find((e) => e.type === "CAPTURE_SUCCEEDED" && Date.parse(e.time) > J + 21000) ?? null; if (captured) break;
    const outs = s.characters.filter((c) => c.role === "OUTLAW" && ["ACTIVE", "WANTED"].includes(c.status));
    if (!h || !outs.length) { await sleep(250); continue; }
    const dist = (c) => Math.hypot(c.x - h.x, c.z - h.z);
    const tgt = outs.filter((c) => c.wanted).sort((x, y) => dist(x) - dist(y))[0] ?? outs.sort((x, y) => dist(x) - dist(y))[0];
    const d = dist(tgt);
    if (tgt.wanted && d < 13 && fired < 2 && since() > 18500 && Date.now() - (marks.hunterShot ?? 0) > 2800) { await B.press("f"); fired++; marks.hunterShot = Date.now(); marks.hunterTarget = tgt.nickname; await sleep(250); continue; }
    const near = tgt.wanted && since() > 22500 ? 1.7 : 6;
    if (d > near + 0.3) { await walkTo(B, "Rio", tgt.x + 0.9, tgt.z + 0.7, 2, near); continue; }
    if (tgt.wanted && since() > 23000 && d < 2.4) { await B.press("e"); await sleep(400); } else await sleep(200);
  }
  if (!captured) captured = await capAfter();
  await Promise.all([insp, heat]);
  const K = captured ? Date.parse(captured.time) : null; marks.K = K; console.log("capture at", K ? ((K - J) / 1000).toFixed(1) + " s after job: " + captured.payload.ticker + " by " + captured.payload.captorName : "none");
  const endAt = J + 49000;
  while (Date.now() < endAt) await sleep(100);
  A.stop(); B.stop(); C.stop(); clearInterval(guard);
  const ev = (await evlog()).map((e) => ({ type: e.type, t: Date.parse(e.time), p: e.payload, actor: e.actorId })); const s = await snap();
  writeFileSync(`${OUT}/run.json`, JSON.stringify({ ...marks, t1: Date.now(), frames: { A: A.frames, B: B.frames, C: C.frames }, events: ev, metrics: s.metrics, npcs: s.npcs.map((n) => ({ id: n.id, name: n.name, role: n.role })), chars: s.characters.map((c) => ({ id: c.id, nickname: c.nickname, ticker: c.ticker, role: c.role })) }));
  const secs = (Date.now() - t0) / 1000; console.log("secs", secs.toFixed(1), "fps A", (A.frames.length / secs).toFixed(1), "B", (B.frames.length / secs).toFixed(1), "C", (C.frames.length / secs).toFixed(1));
} catch (e) { console.log("RECORD FAILED", String(e)); process.exitCode = 2; } finally { for (const k of kids) try { k.kill(); } catch {} setTimeout(() => process.exit(process.exitCode ?? 0), 300); }
