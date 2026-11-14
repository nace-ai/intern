// Data access. Demo mode replays recorded results from the frozen snapshot (snapshot.js wraps fetch/EventSource);
// Live mode calls live endpoints where they exist.
// Nothing here invents numbers: a missing endpoint or file resolves to { ok: false } and screens show "pending".

const listeners = new Set();
// Every load starts in Demo (nothing live can fail on stage); ?mode=live opts in.
let mode = new URLSearchParams(location.search).get("mode") === "live" ? "live" : "demo";
// snapshot.js swaps in the frozen file URL for <img> sources in Demo mode
export const hooks = { src: (path) => path };

export const state = {
  get mode() { return mode; },
  set mode(m) {
    mode = m;
    try { localStorage.setItem("bp.mode", m); } catch { /* ignore */ }
    listeners.forEach((fn) => fn(m));
  },
  onMode(fn) { listeners.add(fn); return () => listeners.delete(fn); },
};

const cache = new Map();
listeners.add(() => cache.clear()); // Demo and Live answers must not mix

export async function get(path, { fresh = false } = {}) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const p = fetch(path, { headers: { accept: "application/json" } }).then(async (r) => {
    let body = null;
    try { body = await r.json(); } catch { /* non-JSON */ }
    return { ok: r.ok, status: r.status, data: r.ok ? body : null, error: r.ok ? null : body?.error || body?.detail || `HTTP ${r.status}` };
  }).catch((e) => ({ ok: false, status: 0, data: null, error: String(e) }));
  cache.set(path, p);
  const res = await p;
  if (!res.ok) cache.delete(path);
  return res;
}

export async function post(path, body) {
  try {
    const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch { /* ignore */ }
    return { ok: r.ok, status: r.status, data, error: r.ok ? null : data?.error || data?.detail || `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, status: 0, data: null, error: String(e) };
  }
}

// SSE with named events; returns close(). Never auto-reconnects (a live stream could spend twice).
export function stream(path, handlers) {
  const es = new EventSource(path);
  for (const [name, fn] of Object.entries(handlers)) {
    if (name === "onerror") continue;
    es.addEventListener(name, (ev) => {
      let d = ev.data;
      try { d = JSON.parse(ev.data); } catch { /* raw */ }
      fn(d, name);
    });
  }
  es.onerror = (e) => { es.close(); handlers.onerror?.(e); };
  return () => es.close();
}

// ---------------------------------------------------------------- endpoints
export const api = {
  paCases: () => get("/api/pa/cases"),
  paCase: (slug) => get(`/api/pa/${encodeURIComponent(slug)}`),
  paPage: (slug, doc, n) => hooks.src(`/api/pa/${encodeURIComponent(slug)}/page/${doc}/${n}.png`),
  paLive: (slug) => post(`/api/pa/${encodeURIComponent(slug)}/live`),
  numbers: () => get("/api/demo/numbers"),
  ccSummary: () => get("/api/cc/summary"),
  ccWhatDecides: () => get("/api/cc/what_decides"),
  ccCase: (id) => get(`/api/cc/case/${encodeURIComponent(id)}`),
  ccRuleChange: () => post("/api/cc/rule_change"),
  ccRuleChangeGet: () => get("/api/cc/rule_change"),
  // results hub (single source for benchmark tables and charts once live)
  resultsSummary: () => get("/api/results/summary"),
  resultsBench: (b) => get(`/api/results/${encodeURIComponent(b)}`),
  resultsCases: (b, q = {}) => get(`/api/results/${encodeURIComponent(b)}/cases?${new URLSearchParams(Object.entries(q).filter(([, v]) => v != null && v !== "" && v !== false))}`),
  resultsDial: (b, t, escalate) => get(`/api/results/${encodeURIComponent(b)}/dial?${new URLSearchParams(Object.entries({ t, escalate }).filter(([, v]) => v != null))}`),
  resultsCase: (b, id) => get(`/api/results/${encodeURIComponent(b)}/case/${encodeURIComponent(id)}`),
  ccStreamUrl: (speed = 1) => `/api/cc/queue/stream?mode=${mode === "live" ? "live" : "replay"}&speed=${speed}`,
};

// Metrics: bind by documented file name, never by "largest number".
export async function metrics() {
  const r = await api.numbers();
  return r.ok ? r.data.metrics || {} : {};
}
