// One continuous real run of the agents playing (no human player): spectator 3D city, Drex mode, real time.
// Usage: OUT=<dir> BASE=http://localhost:5781 API=http://localhost:3781 node demo/record-agents.mjs
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
const OUT = process.env.OUT, BASE = process.env.BASE, API = process.env.API;
mkdirSync(`${OUT}/C`, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const evlog = async () => (await (await fetch(`${API}/api/rooms/VICE-42/events?limit=1000`)).json()).events;
const p = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["--headless=new", "--remote-debugging-port=9462", "--window-size=1600,900", "--hide-scrollbars", "--use-angle=metal", "--ignore-gpu-blocklist", "--disable-background-timer-throttling", "--disable-renderer-backgrounding", `--user-data-dir=${OUT}/profile`, "about:blank"], { stdio: "ignore" });
try {
  for (let i = 0; i < 80; i++) { try { if ((await fetch("http://127.0.0.1:9462/json/version")).ok) break; } catch {} await sleep(250); }
  const t = await (await fetch("http://127.0.0.1:9462/json/new?about:blank", { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
  let id = 0, seq = 0, casting = false; const frames = [];
  const send = (method, params = {}) => { ws.send(JSON.stringify({ id: ++id, method, params })); };
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.method !== "Page.screencastFrame") return; send("Page.screencastFrameAck", { sessionId: d.params.sessionId }); if (!casting) return;
    const f = `C/${String(++seq).padStart(6, "0")}.jpg`; writeFileSync(`${OUT}/${f}`, Buffer.from(d.params.data, "base64")); frames.push({ f, t: Date.now() }); };
  send("Page.enable"); send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  send("Page.navigate", { url: `${BASE}/?nick=Judge&role=SPECTATOR&room=VICE-42&auto=1&view=3d` });
  await sleep(4500);
  send("Page.startScreencast", { format: "jpeg", quality: 70, maxWidth: 1600, maxHeight: 900, everyNthFrame: 1 });
  await sleep(500); casting = true; const t0 = Date.now();
  const mouse = (type, x, y) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1 });
  let stop = false;
  const cam = (async () => { const plan = [[460, 310, 560, 325], [560, 325, 640, 300], [640, 300, 540, 335], [540, 335, 450, 305]]; let k = 0;
    while (!stop) { const [a, b, c, d] = plan[k++ % plan.length]; mouse("mousePressed", a, b); for (let i = 1; i <= 240 && !stop; i++) { mouse("mouseMoved", a + ((c - a) * i) / 240, b + ((d - b) * i) / 240); await sleep(33); } mouse("mouseReleased", c, d); await sleep(300); } })();
  let cap = null;
  while (Date.now() - t0 < 92000) {
    await sleep(700);
    const ev = await evlog();
    cap = ev.find((e) => e.type === "CAPTURE_SUCCEEDED" && Date.parse(e.time) > t0 && /^RYD/.test(e.payload.ticker ?? ""));
    const el = Date.now() - t0;
    if (cap && el >= 62000 && Date.now() - Date.parse(cap.time) >= 11000) break;
  }
  stop = true; await cam; casting = false;
  const ev = (await evlog()).map((e) => ({ type: e.type, t: Date.parse(e.time), p: e.payload, actor: e.actorId }));
  const snap = await (await fetch(`${API}/api/rooms/VICE-42/snapshot`)).json();
  writeFileSync(`${OUT}/run.json`, JSON.stringify({ t0, t1: Date.now(), frames, events: ev, metrics: snap.metrics, npcs: snap.npcs.map((n) => ({ id: n.id, name: n.name, role: n.role })) }));
  console.log("frames", frames.length, "secs", ((Date.now() - t0) / 1000).toFixed(1), "fps", (frames.length / ((Date.now() - t0) / 1000)).toFixed(1), "captured", !!cap);
} finally { p.kill(); }
