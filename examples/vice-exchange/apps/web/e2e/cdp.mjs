// Minimal Chrome DevTools Protocol driver (no Playwright/Puppeteer dependency). Used by the browser smoke test.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import WebSocket from "ws";

const CHROME = process.env.CHROME_BIN ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Page {
  constructor(ws, name) { this.ws = ws; this.name = name; this.seq = 0; this.waiting = new Map(); this.errors = []; this.down = new Set();
    ws.on("message", (raw) => {
      const m = JSON.parse(String(raw));
      if (m.id && this.waiting.has(m.id)) { const { resolve, reject } = this.waiting.get(m.id); this.waiting.delete(m.id); m.error ? reject(new Error(m.error.message)) : resolve(m.result); return; }
      if (m.method === "Runtime.exceptionThrown") this.errors.push("EXCEPTION " + (m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text));
      if (m.method === "Runtime.consoleAPICalled" && (m.params.type === "error" || m.params.type === "warning")) this.errors.push(m.params.type.toUpperCase() + " " + m.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 400));
    });
  }
  send(method, params = {}) { const id = ++this.seq; return new Promise((resolve, reject) => { this.waiting.set(id, { resolve, reject }); this.ws.send(JSON.stringify({ id, method, params })); }); }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(`[${this.name}] eval failed: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    return r.result.value;
  }
  async waitFor(expression, timeoutMs = 10000, label = expression) {
    const end = Date.now() + timeoutMs;
    for (;;) { const v = await this.eval(expression).catch(() => null); if (v) return v; if (Date.now() > end) throw new Error(`[${this.name}] timed out waiting for: ${label}`); await sleep(100); }
  }
  async shot(file) { const r = await this.send("Page.captureScreenshot", { format: "png" }); writeFileSync(file, Buffer.from(r.data, "base64")); return file; }
  keyParams(key) {
    const named = { ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, Enter: 13, Escape: 27, Tab: 9 };
    if (named[key]) return { key, code: key, windowsVirtualKeyCode: named[key] };
    const up = key.toUpperCase();
    return { key, code: `Key${up}`, windowsVirtualKeyCode: up.charCodeAt(0) };
  }
  async keyDown(key) { if (this.down.has(key)) return; this.down.add(key); await this.send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...this.keyParams(key) }); }
  async keyUp(key) { if (!this.down.has(key)) return; this.down.delete(key); await this.send("Input.dispatchKeyEvent", { type: "keyUp", ...this.keyParams(key) }); }
  async press(key) { await this.keyDown(key); await sleep(50); await this.keyUp(key); }
  async releaseAll() { for (const k of [...this.down]) await this.keyUp(k); }
  /** Click the centre of the first element matching a CSS selector, optionally filtered by text. */
  async click(selector, text) {
    const pt = await this.eval(`(() => { const els = [...document.querySelectorAll(${JSON.stringify(selector)})]; const el = ${text ? `els.find((e) => e.textContent.includes(${JSON.stringify(text)}))` : "els[0]"}; if (!el) return null; el.scrollIntoView({ block: "nearest" }); const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
    if (!pt) throw new Error(`[${this.name}] no element for ${selector} ${text ?? ""}`);
    await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pt.x, y: pt.y });
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
  }
  async clickAt(x, y) {
    await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  }
}

export async function launch({ port = 9333, width = 1440, height = 900 } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), "vice-e2e-"));
  const proc = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, `--window-size=${width},${height}`,
    "--no-first-run", "--no-default-browser-check", "--mute-audio", "--hide-scrollbars", "--enable-unsafe-swiftshader", "about:blank"], { stdio: "ignore" });
  const base = `http://127.0.0.1:${port}`;
  let up = false;
  for (let i = 0; i < 60 && !up; i++) { try { up = (await fetch(`${base}/json/version`)).ok; } catch { /* not yet */ } if (!up) await sleep(200); }
  if (!up) { proc.kill(); throw new Error("Chrome did not start"); }
  return {
    async newPage(url, name = "page") {
      const t = await (await fetch(`${base}/json/new?about:blank`, { method: "PUT" })).json();
      const ws = new WebSocket(t.webSocketDebuggerUrl, { perMessageDeflate: false });
      await new Promise((res, rej) => { ws.once("open", res); ws.once("error", rej); });
      const page = new Page(ws, name);
      await page.send("Page.enable"); await page.send("Runtime.enable");
      await page.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      await page.send("Emulation.setFocusEmulationEnabled", { enabled: true });
      await page.send("Page.navigate", { url });
      return page;
    },
    close() { proc.kill(); },
  };
}

const ME = `(() => { const s = window.__vice.getState(); if (!s.session) return null; const me = Object.values(s.characters).find((c) => c.playerId === s.session.playerId && (c.status === "ACTIVE" || c.status === "WANTED")); if (!me) return null; const t = s.lastTick && s.lastTick.characters.find((c) => c.id === me.id); return { id: me.id, x: t ? t.x : me.x, z: t ? t.z : me.z, status: me.status, heat: me.heat, ticker: me.ticker }; })()`;
export const me = (page) => page.eval(ME);

/** Walk the player's avatar with real key events until it is within `tol` metres of (tx, tz). */
export async function walkTo(page, tx, tz, tol = 1.2, timeoutMs = 20000) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const p = await me(page);
    if (!p) { await page.releaseAll(); return false; }
    const dx = tx - p.x, dz = tz - p.z;
    if (Math.hypot(dx, dz) <= tol) { await page.releaseAll(); return true; }
    const want = new Set();
    if (dx > 0.5) want.add("d"); else if (dx < -0.5) want.add("a");
    if (dz > 0.5) want.add("s"); else if (dz < -0.5) want.add("w");
    for (const k of [...page.down]) if (!want.has(k)) await page.keyUp(k);
    for (const k of want) await page.keyDown(k);
    if (Date.now() > end) { await page.releaseAll(); return false; }
    await sleep(50);
  }
}
