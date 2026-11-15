// Open addresses in headless Chrome and report what a browser shows. For pages drawn by a script, where an HTTP
// status proves nothing: the Texas statutes site answers 200 for any address. No dependencies (Node 22+, Google Chrome).
//
//   node tools/browser_check.mjs [--wait MS] URL [URL...]
//
// One JSON line per address: {url, final, title, found, ok}. An address with a fragment (#31.03) is ok when the page
// shows the heading "Sec. 31.03." or "Art. 31.03."; one without a fragment is ok when the page shows 500 or more
// characters of text. Exit code 1 when any address is not ok.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const waitMs = args[0] === "--wait" ? Number(args.splice(0, 2)[1]) : 6500;
if (!args.length) { console.error("usage: node tools/browser_check.mjs [--wait MS] URL [URL...]"); process.exit(2); }
const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9335, profile = mkdtempSync(join(tmpdir(), "tad-browser-"));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--window-size=1280,900", "--remote-debugging-port=" + PORT, "--user-data-dir=" + profile, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let bad = 0;
try {
  let target;
  // Chrome's own debugging endpoint, on this machine only (loopback): it speaks plain HTTP, there is no TLS variant
  for (let i = 0; i < 60 && !target; i++) { await sleep(200); try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch {} }
  if (!target) throw new Error("Chrome did not start: set CHROME_PATH to the browser");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const waiting = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m.result || {}); waiting.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res) => { const n = ++id; waiting.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
  const read = async (expression) => { const r = await send("Runtime.evaluate", { expression, returnByValue: true }); return r.result ? r.result.value : null; };
  await send("Page.enable");
  for (const url of args) {
    const want = decodeURIComponent(new URL(url).hash.slice(1));
    await send("Page.navigate", { url }); await sleep(waitMs);
    const seen = await read(`(() => { const text = document.body ? document.body.innerText : "", want = ${JSON.stringify(want)};
      if (!want) return { found: "", ok: text.length >= 500 };
      const hit = text.match(new RegExp("(?:Sec|Art)\\\\.\\\\s*" + want.replace(/[.*+?^$()|[\\]{}\\\\]/g, "\\\\$&") + "\\\\.[^\\\\n]{0,60}"));
      return { found: hit ? hit[0] : "", ok: !!hit }; })()`) || { found: "", ok: false };
    const row = { url, final: await read("location.href"), title: await read("document.title"), found: seen.found, ok: seen.ok };
    if (!row.ok) bad++;
    console.log(JSON.stringify(row));
  }
  ws.close();
} finally { chrome.kill(); await sleep(300); rmSync(profile, { recursive: true, force: true }); }
process.exit(bad ? 1 : 0);
