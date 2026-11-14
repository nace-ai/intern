// Short real-time capture for the opening: one headless Chrome, spectator 3D city, Drex mode, varied orbit angles.
// Usage: OUT=<dir> BASE=http://localhost:5781 node demo/record-opening.mjs
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
const OUT = process.env.OUT, BASE = process.env.BASE, SECS = Number(process.env.SECS ?? 62);
mkdirSync(`${OUT}/C`, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const p = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", ["--headless=new", "--remote-debugging-port=9461", "--window-size=1600,900", "--hide-scrollbars", "--use-angle=metal", "--ignore-gpu-blocklist", "--disable-background-timer-throttling", "--disable-renderer-backgrounding", `--user-data-dir=${OUT}/profile`, "about:blank"], { stdio: "ignore" });
try {
  for (let i = 0; i < 80; i++) { try { if ((await fetch("http://127.0.0.1:9461/json/version")).ok) break; } catch {} await sleep(250); }
  const t = await (await fetch("http://127.0.0.1:9461/json/new?about:blank", { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => (ws.onopen = r));
  let id = 0, seq = 0, casting = false; const frames = [];
  const send = (method, params = {}) => { ws.send(JSON.stringify({ id: ++id, method, params })); };
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.method !== "Page.screencastFrame") return; send("Page.screencastFrameAck", { sessionId: d.params.sessionId }); if (!casting) return;
    const f = `C/${String(++seq).padStart(6, "0")}.jpg`; writeFileSync(`${OUT}/${f}`, Buffer.from(d.params.data, "base64")); frames.push({ f, t: Date.now() }); };
  send("Page.enable"); send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  send("Page.navigate", { url: `${BASE}/?nick=Judge&role=SPECTATOR&room=VICE-42&auto=1&view=3d` });
  await sleep(6000);
  send("Page.startScreencast", { format: "jpeg", quality: 70, maxWidth: 1600, maxHeight: 900, everyNthFrame: 1 });
  await sleep(600); casting = true; const t0 = Date.now();
  const mouse = (type, x, y) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1 });
  const drag = async (x1, y1, x2, y2, ms) => { mouse("mousePressed", x1, y1); const n = Math.round(ms / 33); for (let i = 1; i <= n; i++) { mouse("mouseMoved", x1 + ((x2 - x1) * i) / n, y1 + ((y2 - y1) * i) / n); await sleep(33); } mouse("mouseReleased", x2, y2); };
  const wheel = async (dy, n) => { for (let i = 0; i < n; i++) { send("Input.dispatchMouseEvent", { type: "mouseWheel", x: 600, y: 360, deltaX: 0, deltaY: dy }); await sleep(60); } };
  // A different angle and distance for each stretch, moving slowly the whole time so no two moments look alike.
  const plan = [[420, 300, 640, 330], [640, 330, 760, 250], [760, 250, 560, 410], [560, 410, 380, 260], [380, 260, 700, 380], [700, 380, 480, 300]];
  let k = 0;
  while (Date.now() - t0 < SECS * 1000) { const [a, b, c, d] = plan[k % plan.length]; await drag(a, b, c, d, 7000); await wheel(k % 2 ? 90 : -90, 10); await sleep(1500); k++; }
  casting = false;
  writeFileSync(`${OUT}/marks.json`, JSON.stringify({ marks: [{ name: "city", t: t0 }, { name: "bg0", t: t0 }], framesC: frames }));
  console.log("frames", frames.length, "fps", (frames.length / ((Date.now() - t0) / 1000)).toFixed(1));
} finally { p.kill(); }
