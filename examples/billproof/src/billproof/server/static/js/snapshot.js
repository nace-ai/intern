// Demo mode reads frozen real API responses (static/snapshot/manifest.json, written by scripts/snapshot_demo.py),
// so the stage demo makes no live call and works with the Python server down. Live mode passes straight through.
// Wraps window.fetch and window.EventSource for every /api/ URL: snapshot first; a call that is not in the snapshot
// falls through to the same-origin backend (it serves recorded data from disk), except live-API endpoints (Drex,
// DeepInfra, NDI, Browser runs), which never run in Demo: they answer 404 { error: "Recorded data not available" }.
// With the backend down, an unrecorded call gets the same clean 404.
import { state, hooks } from "./data.js";

const BASE = new URL("../snapshot/", import.meta.url);
const realFetch = window.fetch.bind(window);
const RealES = window.EventSource;
export const MISSING = "Recorded data not available";

// canonical JSON (sorted keys, no whitespace) = Python json.dumps(sort_keys=True, separators=(",", ":"))
function canon(v) {
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  if (v && typeof v === "object") return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`;
  return JSON.stringify(v ?? null);
}
// numbers compare by value, so t=0.8 (JS) matches t=0.80 (Python)
const normVal = (v) => (/^-?\d+(\.\d+)?$/.test(v) ? String(Number(v)) : v);
const sortQuery = (q) => [...new URLSearchParams(q || "")].map(([k, v]) => [k, normVal(v)]).sort((a, b) => (a[0] + "\0" + a[1] < b[0] + "\0" + b[1] ? -1 : 1))
  .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
const decodePath = (p) => { try { return decodeURI(p); } catch { return p; } };
const keyOf = (method, path, query, body) => `${method.toUpperCase()} ${decodePath(path)}?${sortQuery(query)}#${body == null ? "" : canon(body)}`;

let entries = [];
const index = new Map();
try {
  const r = await realFetch(new URL("manifest.json", BASE), { cache: "no-cache" });
  if (r.ok) {
    const m = await r.json();
    entries = Array.isArray(m) ? m : m.entries || [];
    for (const e of entries) index.set(keyOf(e.method || "GET", e.path, e.query, e.body), e);
  }
} catch { /* no snapshot yet: Demo shows "recorded data not available" */ }

const isDemo = () => state.mode === "demo";
// endpoints that spend live API calls; Demo mode only replays their recorded results
const LIVE_ONLY = [
  /^\/api\/pa\/[^/]+\/live$/, /^\/api\/pa\/[^/]+\/whatif\/live$/, /^\/api\/cc\/what_if\/new_criterion$/,
  /^\/api\/browser\/[^/]+\/run$/, /^\/api\/ask$/,
];
const liveOnly = (u) => LIVE_ONLY.some((re) => re.test(decodePath(u.pathname))) || new URLSearchParams(u.search).get("mode") === "live";
const isApi = (u) => u.origin === location.origin && u.pathname.startsWith("/api/");
const fileUrl = (e) => new URL(e.file, BASE).href;

export const snapshot = {
  get demo() { return isDemo(); },
  get size() { return entries.length; },
  find(method, path, body) {
    const u = new URL(path, location.href);
    return index.get(keyOf(method, u.pathname, u.search, body)) || null;
  },
  // every recorded entry for one method + path (e.g. the recorded new-criterion runs), any query or body
  list(method, path) {
    const p = decodePath(new URL(path, location.href).pathname);
    return entries.filter((e) => (e.method || "GET").toUpperCase() === method && decodePath(e.path) === p);
  },
  // a URL an <img> can load: the frozen file in Demo mode, the live route otherwise
  src(path) {
    if (!isDemo()) return path;
    const e = this.find("GET", path);
    return e ? fileUrl(e) : path;
  },
};

hooks.src = (path) => snapshot.src(path);

const json = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", "x-snapshot": "missing" } });
const sleep = (ms, signal) => new Promise((res, rej) => {
  if (signal?.aborted) { rej(new DOMException("Aborted", "AbortError")); return; }
  const t = setTimeout(res, ms);
  signal?.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("Aborted", "AbortError")); }, { once: true });
});
// Recorded timeline (ms from stream start), each gap clamped so a slow recording never stalls the stage.
const timeline = (events) => {
  let at = 0;
  return events.map((ev, i) => {
    const t = Number(ev.t_ms), p = i ? Number(events[i - 1].t_ms) : 0;
    at += Number.isFinite(t) && Number.isFinite(p) ? Math.max(0, Math.min(2500, t - p)) : 40;
    return at;
  });
};
// Replays events on that timeline: events due together go out in one batch (9,688 tile events take ~1 s, as recorded).
async function replay(events, emit, stopped, signal) {
  const at = timeline(events), t0 = performance.now();
  for (let i = 0; i < events.length; i++) {
    if (stopped()) return;
    const due = t0 + at[i] - performance.now();
    if (due > 8 || i % 400 === 399) await sleep(Math.max(0, due), signal);
    if (stopped()) return;
    emit(events[i]);
  }
}
const loadEvents = async (e) => { const r = await realFetch(fileUrl(e)); return r.ok ? r.json() : []; };
const dataStr = (d) => (typeof d === "string" ? d : JSON.stringify(d));

async function demoFetch(u, init) {
  const method = (init.method || "GET").toUpperCase();
  let body = null;
  if (init.body != null) { try { body = JSON.parse(init.body); } catch { body = String(init.body); } }
  const e = index.get(keyOf(method, u.pathname, u.search, body));
  if (!e) {
    console.info("[snapshot] not recorded:", method, u.pathname + u.search, body ?? "");
    if (!liveOnly(u)) {
      try { return await realFetch(u.href, init); } catch { /* backend down: fall to the clean state */ }
    }
    return json(404, { error: `${MISSING} for this view in Demo mode`, snapshot_missing: true });
  }
  const headers = { "x-snapshot": "recorded" };
  if (e.kind === "sse") {
    const events = await loadEvents(e);
    const enc = new TextEncoder();
    const signal = init.signal;
    let cancelled = false;
    const rs = new ReadableStream({
      start(ctl) {
        // not awaited: the stream is readable while the replay runs
        replay(events, (ev) => {
          const lines = dataStr(ev.data).split("\n").map((l) => `data: ${l}`).join("\n");
          ctl.enqueue(enc.encode(`${ev.event && ev.event !== "message" ? `event: ${ev.event}\n` : ""}${lines}\n\n`));
        }, () => cancelled, signal)
          .then(() => { if (!cancelled) ctl.close(); })
          .catch((err) => { if (!cancelled) ctl.error(err); });
      },
      cancel() { cancelled = true; },
    });
    return new Response(rs, { status: e.status || 200, headers: { ...headers, "content-type": "text/event-stream" } });
  }
  const r = await realFetch(fileUrl(e));
  if (!r.ok) return json(404, { error: `${MISSING} (snapshot file ${e.file} missing)`, snapshot_missing: true });
  const ct = e.content_type || (e.kind === "binary" ? r.headers.get("content-type") : "application/json");
  return new Response(await r.blob(), { status: e.status || 200, headers: { ...headers, "content-type": ct || "application/octet-stream" } });
}

window.fetch = function (input, init = {}) {
  const req = input instanceof Request ? input : null;
  const u = new URL(req ? req.url : String(input), location.href);
  if (!isDemo() || !isApi(u)) return realFetch(input, init);
  return demoFetch(u, { method: req?.method, headers: req?.headers, body: req ? undefined : init.body, ...init });
};

// EventSource replay: same events, same order, recorded timing; ends with "error" the way a closed stream does.
class DemoEventSource extends EventTarget {
  constructor(url) {
    super();
    this.url = String(url);
    this.readyState = 0;
    this.onopen = this.onmessage = this.onerror = null;
    this._closed = false;
    this._run();
  }
  _emit(type, data) {
    if (this._closed) return;
    const ev = type === "error" || type === "open" ? new Event(type) : new MessageEvent(type, { data });
    this.dispatchEvent(ev);
    const h = type === "message" ? this.onmessage : type === "error" ? this.onerror : type === "open" ? this.onopen : null;
    h?.call(this, ev);
  }
  async _run() {
    await Promise.resolve();
    const u = new URL(this.url, location.href);
    const e = index.get(keyOf("GET", u.pathname, u.search, null));
    if (!e || e.kind !== "sse") {
      console.info("[snapshot] stream not recorded:", u.pathname + u.search);
      if (!liveOnly(u) && !this._closed) { this._proxy(); return; }
      this.readyState = 2; this._emit("error"); return;
    }
    const events = await loadEvents(e);
    this.readyState = 1; this._emit("open");
    await replay(events, (ev) => this._emit(ev.event || "message", dataStr(ev.data)), () => this._closed);
    if (!this._closed) { this.readyState = 2; this._emit("error"); }
  }
  // not recorded: the backend's own (recorded-data) stream, re-dispatched through this object
  _proxy() {
    const es = (this._real = new RealES(this.url));
    const fwd = (ev) => {
      if (this._closed) return;
      const out = ev instanceof MessageEvent ? new MessageEvent(ev.type, { data: ev.data }) : new Event(ev.type);
      this.readyState = es.readyState;
      this.dispatchEvent(out);
      const h = ev.type === "message" ? this.onmessage : ev.type === "error" ? this.onerror : ev.type === "open" ? this.onopen : null;
      h?.call(this, out);
    };
    const add = this.addEventListener.bind(this);
    for (const t of ["open", "message", "error"]) es.addEventListener(t, fwd);
    // named events: forward each type a listener asks for
    this._types = new Set(["open", "message", "error"]);
    this.addEventListener = (t, fn, o) => { if (!this._types.has(t)) { this._types.add(t); es.addEventListener(t, fwd); } add(t, fn, o); };
    for (const t of this._pending || []) if (!this._types.has(t)) { this._types.add(t); es.addEventListener(t, fwd); }
  }
  addEventListener(t, fn, o) { (this._pending ||= new Set()).add(t); super.addEventListener(t, fn, o); }
  close() { this._closed = true; this.readyState = 2; this._real?.close(); }
}

window.EventSource = function (url, opts) {
  const u = new URL(String(url), location.href);
  return isDemo() && isApi(u) ? new DemoEventSource(url) : new RealES(url, opts);
};
