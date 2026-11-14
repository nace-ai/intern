// Trust dial: "send decisions Drex is less than X% sure about to GPT-6.1 Sol". Data from GET /api/results/{bench}/dial
// (a curve over thresholds, simulated from recorded answers) plus fixed references (GPT only, Drex only) and the
// measured hybrid run. Never hard-codes numbers: until the endpoint answers, a pending state is shown.
// API: mountDial(el, bench, opts) · routedAt(bench, t) · dialCard(bench, opts) · fetchDial · dialState · normDial.
import { h, icon, fmt, fill, diBadge } from "./ui.js";
import { api, state } from "./data.js";
import { metricName } from "./resultsdata.js";

import { prettyModel } from "./ladder.js";
// The escalation model's name comes from the dial API (escalate_model, else gpt_model); set when a dial loads.
let GPT_NAME = "GPT-6.1 Sol";
const escName = (d) => {
  const id = d.escalate_model || d.gpt_model || null;
  if (!id) return "GPT-6.1 Sol";
  if (/gpt-6\.1-sol/i.test(id)) return "GPT-6.1 Sol";
  const n = prettyModel(id);
  return d.provider === "deepinfra" ? `${n} via DeepInfra` : n;
};
const val = (x) => (x && typeof x === "object" && "value" in x ? x.value : x);
const num = (x) => (typeof x === "number" && Number.isFinite(x) ? x : null);
const pick = (o, ...k) => { for (const key of k) { const v = num(val(o?.[key])); if (v != null) return v; } return null; };
// routed shares may be a number or { decisions, questions, cases, ... }: prefer decisions
const share = (v) => (v && typeof v === "object" && !("value" in v) ? pick(v, "decisions", "decision", "questions", "cases", "items", "share") : num(val(v)));

// One point on the curve or a reference: { t, acc, routed, cost, latency, label, n }
function point(p, metric) {
  if (!p || typeof p !== "object") return null;
  // a "decision" is one question Drex answers; questions_routed is the share of them that GPT answers instead
  const routedRaw = p.questions_routed ?? p.questions_routed_share ?? p.routed_share ?? p.routed_shares ?? p.share_routed ?? p.escalation_rate ?? p.cases_routed;
  return {
    t: pick(p, "t", "threshold", "cutoff"),
    acc: num(val(p.metrics?.[metric])) ?? pick(p, "value", "metric_value", "accuracy", "acc"),
    routed: share(routedRaw),
    cost: pick(p, "cost_per_1k", "cost_per_1000_usd", "cost_per_1000", "cost_per_1k_usd"),
    latency: pick(p, "latency_p50_ms", "latency_ms", "latency"),
    label: p.lane || p.name || null, router: p.router || null, note: p.note || null, n: pick(p, "n", "n_cases"),
  };
}

export function normDial(d) {
  const metric = d.chart_metric || (typeof d.chart === "string" ? d.chart : d.chart?.metric) || (typeof d.metric === "string" ? d.metric : null);
  const P = (p) => point(p, metric);
  const curve = (d.points || d.curve || d.thresholds || []).map(P).filter((p) => p && p.t != null && p.acc != null).sort((a, b) => a.t - b.t);
  const refs = d.references || d.refs || {};
  const match = (qwenDial(d) ? d.match_qwen_within_1pt || d.match_escalate_within_1pt : d.match_gpt_within_1pt) || null;
  const measured = [].concat(d.measured || d.checkpoints || []).map(P).filter((p) => p && p.acc != null);
  GPT_NAME = escName(d);
  return {
    escName: GPT_NAME,
    metric, curve, measured, match,
    // An escalated dial (Qwen) compares with the escalation model alone, not GPT.
    gpt: P((qwenDial(d) && (d.qwen_only || d.deepinfra_only || d.escalate_only || d[`${d.escalate}_only`])) || d.gpt_only || refs.gpt_only || refs.gpt || d.gpt),
    drex: P(d.drex_only || refs.drex_only || refs.drex || d.drex),
    drexLane: d.drex_lane || d.drex_lane_id || null, gptLane: d.gpt_lane || d.gpt_lane_id || null,
    caption: d.caption || null, notes: [].concat(d.notes || []), title: d.title || null,
  };
}

const nearest = (c, t) => c.reduce((best, p, i) => (Math.abs(p.t - t) < Math.abs(c[best].t - t) ? i : best), 0);

// Default: the cheapest point within 1 point of GPT-only (the API's pick when given); else the point nearest 0.90.
export function defaultIndex(dl) {
  const c = dl.curve;
  if (!c.length) return 0;
  const mt = num(dl.match?.threshold);
  if (mt != null && (dl.match.router || "global") === "global") return nearest(c, mt);
  if (dl.gpt?.acc != null) {
    const ok = c.map((p, i) => ({ p, i })).filter(({ p }) => p.acc >= dl.gpt.acc - 0.01 - 1e-9 && p.cost != null);
    if (ok.length) return ok.sort((a, b) => a.p.cost - b.p.cost || a.p.t - b.p.t)[0].i;
  }
  return nearest(c, 0.9);
}

// Threshold shared across the detail page and the case explorer (per benchmark: URL ?t= first, else remembered locally).
export const dialState = {
  get(bench) {
    const q = new URLSearchParams(location.hash.split("?")[1] || "").get("t");
    if (q != null && q !== "" && Number.isFinite(Number(q))) return Number(q);
    try { const v = localStorage.getItem(`bp.dial.${bench}`); return v == null ? null : Number(v); } catch { return null; }
  },
  set(bench, t) { try { localStorage.setItem(`bp.dial.${bench}`, String(t)); } catch { /* storage blocked */ } },
};

// Which dial a benchmark uses: escalate=qwen when served, else escalate=deepinfra when it reports a Qwen model,
// else the GPT dial (labelled). Remembered per bench so routedAt asks the same dial.
const qwenDial = (d) => d?.escalate && d.escalate !== "gpt" && /qwen/i.test(d.escalate_model || "");
const ESCALATE = new Map();
async function dialData(bench) {
  for (const esc of ["qwen", "deepinfra"]) {
    const r = await api.resultsDial(bench, null, esc);
    if (r.ok && qwenDial(r.data) && (r.data.curve || r.data.points || []).length) { ESCALATE.set(bench, esc); return r; }
  }
  ESCALATE.set(bench, null);
  return api.resultsDial(bench);
}

export async function fetchDial(bench) {
  const r = await dialData(bench);
  return r.ok ? normDial(r.data) : null;
}

// What is routed to GPT at threshold t (for the case explorer). GET /api/results/{bench}/dial?t=  (t in 0.50–1.00, 2 dp)
// Returns null while the endpoint is not live, else
//   { t, point, cases: [case ids with any routed question], byCase: Map(case id → [question ids]), isRouted(caseId, q), raw }
export async function routedAt(bench, t) {
  const tt = Math.round(Math.min(1, Math.max(0.5, Number(t))) * 100) / 100;
  if (!ESCALATE.has(bench)) await dialData(bench).catch(() => null);
  const r = await api.resultsDial(bench, tt, ESCALATE.get(bench) || undefined);
  if (!r.ok) return null;
  const d = r.data || {};
  const metric = typeof d.chart === "string" ? d.chart : d.chart?.metric;
  const byCase = new Map((d.routed_by_case || []).map((x) => [String(x.case_id), [].concat(x.routed || [])]));
  return {
    t: num(d.threshold) ?? tt, point: point(d, metric), byCase,
    cases: [...byCase].filter(([, q]) => q.length).map(([id]) => id),
    isRouted: (caseId, q) => { const qs = byCase.get(String(caseId)); return !!qs && (q == null ? qs.length > 0 : qs.includes(q)); },
    raw: d,
  };
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const pctT = (t) => `${Math.round(t * 1000) / 10}%`;
const pts = (a, b) => Math.round((a - b) * 1000) / 10;

// ---------------------------------------------------------------- component
// mountDial(el, bench, { metricHint, compact, onChange(point) }) → { setT(t), destroy() }
export function mountDial(el, bench, { metricHint, compact = false, onChange } = {}) {
  let chart = null, alive = true, retry = null;
  const root = h("div", { class: `tdial${compact ? " tdial--compact" : ""}` });
  fill(el, root);
  renderPending(root, bench, metricHint, "Loading recorded answers…");

  const load = async () => {
    if (!alive) return;
    let dl = null;
    try { dl = await fetchDial(bench); } catch { dl = null; }
    if (!alive) return;
    if (!dl || !dl.curve.length) {
      // Demo mode: this benchmark has no recorded dial; show no slider rather than a pending card
      if (state.mode === "demo") { el.style.display = "none"; return; }
      renderPending(root, bench, metricHint, dl ? "No thresholds recorded for this benchmark yet." : "Waiting for recorded answers.");
      if (root.isConnected || !el.isConnected) retry = setTimeout(() => (root.isConnected ? load() : null), 20000);
      return;
    }
    chart = renderDial(root, bench, dl, metricHint, onChange);
  };
  load();
  return {
    setT(t) { root.dispatchEvent(new CustomEvent("tdial:set", { detail: t })); },
    destroy() { alive = false; clearTimeout(retry); chart?.destroy(); },
  };
}

// Card wrapper used by the screens (Results hub, benchmark detail).
export function dialCard(bench, opts = {}) {
  const card = h("div", { class: "card tdial-card" });
  mountDial(card, bench, opts);
  return card;
}

function header(bench, tNode, range) {
  return h("div", { class: "tdial-hd" },
    h("div", { class: "tdial-eyebrow" }, icon("sliders-horizontal"), "Trust dial"),
    h("div", { class: "tdial-sentence" }, "Send decisions Drex is less than ", tNode, " sure about to ", h("span", { class: `model model--${/gpt/i.test(GPT_NAME) ? "gpt" : "di"}` }, GPT_NAME), /deepinfra/i.test(GPT_NAME) ? [" ", diBadge()] : null),
    range);
}

function bigs(metricLabel) {
  const mk = (label, cls) => {
    const v = h("div", { class: "tdial-v num" }), sub = h("div", { class: "tdial-sub" });
    return { v, sub, el: h("div", { class: `tdial-big ${cls}` }, h("div", { class: "tdial-k" }, label), v, sub) };
  };
  return { A: mk(metricLabel, "is-acc"), R: mk(`Sent to ${GPT_NAME}`, "is-routed"), C: mk("Cost per 1,000 cases", "is-cost") };
}

function renderPending(root, bench, metricHint, why) {
  const B = bigs(cap(metricName(metricHint || "accuracy")));
  for (const b of [B.A, B.R, B.C]) { b.v.textContent = "—"; b.v.classList.add("is-pending"); }
  const range = h("div", { class: "tdial-range is-pending" }, h("input", { type: "range", min: 0, max: 1, step: 0.01, value: 0.5, disabled: true, "aria-label": "Confidence threshold (pending)" }));
  fill(root,
    h("div", { class: "tdial-grid" },
      h("div", { class: "tdial-left" }, header(bench, h("span", { class: "tdial-t is-pending" }, "X%"), range), h("div", { class: "tdial-bigs" }, B.A.el, B.R.el, B.C.el)),
      h("div", { class: "tdial-chart tdial-chart--pending" },
        h("div", { class: "tdial-wait" }, icon("hourglass"), h("div", { class: "tdial-wait-t" }, "Trust dial pending"),
          h("div", { class: "t-small" }, why), h("div", { class: "t-tiny mono muted" }, `GET /api/results/${bench}/dial`)))),
    h("div", { class: "tdial-foot" }, h("span", { class: "footnote" }, "Simulated from recorded answers; the marked point is a real run.")));
}

function renderDial(root, bench, dl, metricHint, onChange) {
  const c = dl.curve;
  const metricLabel = cap(metricName(dl.metric || metricHint || "accuracy"));
  const saved = dialState.get(bench);
  let i = saved != null ? nearest(c, saved) : defaultIndex(dl);
  const def = defaultIndex(dl);
  const B = bigs(metricLabel);
  const tNode = h("span", { class: "tdial-t num" });
  const tMin = c[0].t, tMax = c[c.length - 1].t;
  const input = h("input", { type: "range", min: tMin, max: tMax, step: Math.max(0.0005, (tMax - tMin) / 1000), value: c[i].t, "aria-label": `Send to ${GPT_NAME} below this Drex confidence` });
  // tick for the default (recommended) setting
  const f = (c[def].t - tMin) / (tMax - tMin || 1);
  const defTick = h("button", { class: "tdial-def", type: "button", title: `Cheapest setting within 1 point of ${GPT_NAME}${dl.match?.post_hoc ? `. ${dl.match.post_hoc}` : ""}`, style: { left: `${f * 100}%` } }, "★");
  defTick.style.setProperty("--f", f);
  const range = h("div", { class: "tdial-range" }, input, defTick,
    h("div", { class: "tdial-ends" }, h("span", {}, `${pctT(tMin)} · Drex decides more`), h("span", {}, `${GPT_NAME} decides more · ${pctT(tMax)}`)));
  const status = h("div", { class: "tdial-status" });
  const canvas = h("canvas", { "aria-label": `${metricLabel} against cost per 1,000 cases` });
  const explore = h("a", { class: "btn btn--sm", href: "#" }, icon("list-checks"), "See routed decisions");
  const hasMeasured = dl.measured.some((m) => m.cost > 0);
  const legend = h("div", { class: "tdial-legend" },
    key("is-curve", "Simulated dial"), key("is-now", "Current setting"),
    dl.gpt?.cost > 0 ? key("is-gpt", `${GPT_NAME} only`) : null,
    dl.drex?.cost > 0 ? key("is-drex", "Drex only") : null,
    hasMeasured ? key("is-measured", "Measured hybrid run (real)") : null);

  fill(root,
    h("div", { class: "tdial-grid" },
      h("div", { class: "tdial-left" }, header(bench, tNode, range), h("div", { class: "tdial-bigs" }, B.A.el, B.R.el, B.C.el), status),
      h("div", { class: "tdial-chart" }, h("div", { class: "tdial-plot" }, canvas), legend)),
    h("div", { class: "tdial-foot" },
      h("span", { class: "footnote" }, dl.caption || (hasMeasured ? "Simulated from recorded answers; the marked point is a real run." : "Simulated from recorded answers; no measured hybrid run on this benchmark yet."),
        dl.match?.post_hoc ? ` ★ ${dl.match.post_hoc}` : "", dl.notes.length ? ` ${dl.notes.join(" ")}` : ""),
      /gpt/i.test(dl.escName) ? h("span", { class: "tdial-esc-note" }, `escalation model: ${dl.escName} (Qwen dial pending)`) : null,
      h("span", { class: "spacer" }), explore));

  const chart = dialChart(canvas, dl, i, metricLabel);
  const g = dl.gpt;
  let shown = -1, frame = 0;
  const show = () => {
    frame = 0;
    if (i === shown) return;
    shown = i;
    const p = c[i];
    tNode.textContent = pctT(p.t);
    B.A.v.textContent = fmt.pct(p.acc, 1);
    B.R.v.textContent = p.routed != null ? fmt.pct(p.routed, p.routed > 0 && p.routed < 0.01 ? 1 : 0) : "—";
    B.C.v.textContent = p.cost != null ? fmt.usd(p.cost) : "—";
    B.A.sub.textContent = g?.acc != null ? `${GPT_NAME} alone ${fmt.pct(g.acc, 1)}` : "";
    B.R.sub.textContent = p.routed != null ? `${fmt.pct(1 - p.routed, 0)} decided by Drex` : "";
    B.C.sub.textContent = g?.cost ? `${GPT_NAME} alone ${fmt.usd(g.cost)}` : "";
    if (g?.acc != null) {
      const d = pts(p.acc, g.acc);
      const within = p.acc >= g.acc - 0.01 - 1e-9;
      const cheaper = p.cost != null && g.cost ? (p.cost < g.cost ? `${fmt.ratio(g.cost, p.cost)} cheaper than ${GPT_NAME} alone` : `not cheaper than ${GPT_NAME} alone`) : null;
      fill(status,
        h("span", { class: `pill ${within ? "pill--green" : "pill--amber"}` }, icon(within ? "circle-check" : "triangle-alert"),
          d > 0 ? `${d.toFixed(1)} points above ${GPT_NAME} alone` : d === 0 ? `Same score as ${GPT_NAME} alone` : within ? `Within 1 point of ${GPT_NAME} alone (−${Math.abs(d).toFixed(1)})` : `${Math.abs(d).toFixed(1)} points below ${GPT_NAME} alone`),
        cheaper ? h("span", { class: "tdial-cheaper" }, cheaper) : null,
        i === def ? h("span", { class: "tdial-rec" }, "★ recommended setting") : null);
    } else fill(status);
    explore.href = `#/benchmarks/${encodeURIComponent(bench)}/cases?t=${p.t}`;
    chart.data.datasets[1].data = p.cost > 0 ? [{ x: p.cost, y: p.acc, t: p.t }] : [];
    chart.update("none");
    dialState.set(bench, p.t);
    onChange?.(p);
  };
  const setT = (t) => { i = nearest(c, t); show(); };
  input.addEventListener("input", () => { i = nearest(c, Number(input.value)); if (!frame) frame = requestAnimationFrame(show); });
  // let go: snap the thumb onto the recorded threshold it is showing
  input.addEventListener("change", () => { input.value = c[i].t; });
  defTick.addEventListener("click", () => { i = def; input.value = c[i].t; show(); });
  root.addEventListener("tdial:set", (e) => { setT(e.detail); input.value = c[i].t; });
  show();
  return chart;
}

const key = (cls, label) => h("span", { class: `tdial-key ${cls}` }, h("i", {}), label);

// ---------------------------------------------------------------- chart
const isMajor = (v) => { const m = v / 10 ** Math.floor(Math.log10(v) + 1e-9); return [1, 2, 5].some((k) => Math.abs(m - k) < 1e-6); };
const css = (name, fb) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fb;

function dialChart(canvas, dl, i, yLabel) {
  const C = { drex: css("--drex", "#4f46e5"), drexLine: css("--drex-line", "#b4b8f7"), gpt: css("--gpt", "#64748b"), amber: css("--amber", "#d97706"),
    text2: css("--text-2", "#42506b"), text3: css("--text-3", "#75809a"), grid: css("--border", "#e1e6ed") };
  const c = dl.curve.filter((p) => p.cost > 0);
  const all = [...c, dl.gpt, dl.drex, ...dl.measured].filter((p) => p && p.cost > 0 && p.acc != null);
  const costs = all.map((p) => p.cost), accs = all.map((p) => p.acc);
  const xmin = Math.min(...costs) / 1.6, xmax = Math.max(...costs) * 1.6;
  const lo = Math.max(0, Math.floor((Math.min(...accs) - 0.012) * 100) / 100), hi = Math.min(1, Math.ceil((Math.max(...accs) + 0.012) * 100) / 100);
  const ref = (p, label, color, style, bg, r = 8) => (p && p.cost > 0 ? { type: "scatter", label, data: [{ x: p.cost, y: p.acc, t: p.t }], pointStyle: style,
    pointRadius: r, pointHoverRadius: r + 2, backgroundColor: bg ?? color, borderColor: color, borderWidth: 2.5, tag: label, clip: false } : null);
  const sets = [
    { type: "line", label: "Simulated dial", data: c.map((p) => ({ x: p.cost, y: p.acc, t: p.t })), borderColor: C.drexLine, backgroundColor: C.drexLine,
      borderWidth: 3, pointRadius: 0, pointHoverRadius: 4, tension: 0, order: 5 },
    { type: "scatter", label: "Current setting", data: [], pointRadius: 11, pointHoverRadius: 12, backgroundColor: C.drex, borderColor: "#fff", borderWidth: 3.5, order: 0, clip: false },
    ref(dl.gpt, `${GPT_NAME} only`, C.gpt, "rect"),
    ref(dl.drex, "Drex only", C.drex, "rect", "#ffffff"),
    ...dl.measured.filter((m) => m.cost > 0).map((m) => ({ ...ref(m, "Measured hybrid run", C.amber, "rectRot", css("--amber-soft", "#fef3e0"), 10), label: m.label || "Measured hybrid run" })),
  ].filter(Boolean);
  const pct = (v) => fmt.pct(v, hi - lo < 0.06 ? 1 : 0);
  return new Chart(canvas, {
    data: { datasets: sets },
    plugins: [labelsPlugin(C)],
    options: {
      animation: false, maintainAspectRatio: false, layout: { padding: { right: 24, top: 30, left: 4 } },
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}${ctx.raw.t != null ? ` · below ${pctT(ctx.raw.t)}` : ""}: ${fmt.pct(ctx.parsed.y, 1)} · ${fmt.usd(ctx.parsed.x)} per 1k` } },
      },
      scales: {
        x: { type: "logarithmic", min: xmin, max: xmax, border: { display: false },
          grid: { color: (g) => (g.tick && isMajor(g.tick.value) ? C.grid : "transparent") },
          title: { display: true, text: "Cost per 1,000 cases (log scale)", color: C.text3, font: { size: 13, weight: "600" } },
          ticks: { color: C.text3, maxRotation: 0, autoSkip: false, font: { size: 12 }, callback: (v) => (isMajor(v) ? fmt.usd(v) : "") } },
        y: { min: lo, max: hi, grid: { color: C.grid }, border: { display: false },
          title: { display: true, text: yLabel, color: C.text3, font: { size: 13, weight: "600" } },
          ticks: { color: C.text3, font: { size: 12 }, callback: (v) => pct(v), stepSize: hi - lo <= 0.08 ? 0.01 : hi - lo <= 0.2 ? 0.02 : 0.05 } },
      },
    },
  });
}

// Direct labels next to the reference markers, and a soft halo + guide lines behind the current point.
function labelsPlugin(C) {
  return {
    id: "tdialLabels",
    beforeDatasetsDraw(chart) {
      const meta = chart.getDatasetMeta(1), pt = meta?.data?.[0];
      if (!pt) return;
      const { ctx, chartArea: a } = chart;
      ctx.save();
      ctx.strokeStyle = C.drexLine; ctx.lineWidth = 1.25; ctx.setLineDash([4, 5]);
      ctx.beginPath(); ctx.moveTo(pt.x, a.bottom); ctx.lineTo(pt.x, pt.y); ctx.lineTo(a.left, pt.y); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = "rgba(79, 70, 229, .12)";
      ctx.beginPath(); ctx.arc(pt.x, pt.y, 22, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    },
    afterDatasetsDraw(chart) {
      const { ctx, chartArea: a } = chart;
      ctx.save();
      ctx.font = "600 13px Inter, system-ui, sans-serif"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
      // label on a white chip (the curve may pass behind it): first spot that stays in the plot and clear of the
      // other labels, the markers and the current point
      const pad = 6, hh = 20;
      const marks = chart.data.datasets.map((ds, k) => chart.getDatasetMeta(k).data[0]).filter((el) => el && chart.data.datasets.length);
      const now = chart.getDatasetMeta(1)?.data?.[0];
      const placed = [];
      const hits = (r) => placed.some((q) => r.x < q.x + q.w && q.x < r.x + r.w && r.y < q.y + q.h && q.y < r.y + r.h)
        || marks.some((m) => m.x > r.x - 10 && m.x < r.x + r.w + 10 && m.y > r.y - 10 && m.y < r.y + r.h + 10)
        || (now && now.x > r.x - 24 && now.x < r.x + r.w + 24 && now.y > r.y - 24 && now.y < r.y + r.h + 24);
      chart.data.datasets.forEach((ds, k) => {
        if (!ds.tag) return;
        const el = chart.getDatasetMeta(k).data[0];
        if (!el) return;
        const w = ctx.measureText(ds.tag).width + pad * 2;
        const spots = [[-w / 2, -26 - hh / 2], [-w / 2, 26 - hh / 2], [-w - 14, -hh / 2], [14, -hh / 2], [-w - 10, -24 - hh], [10, -24 - hh], [-w - 10, 24], [10, 24], [-w / 2, -50 - hh / 2], [-w / 2, 50 - hh / 2]];
        const box = (dx, dy) => ({ x: Math.min(Math.max(el.x + dx, a.left + 2), chart.width - w - 2), y: Math.min(Math.max(el.y + dy, 2), a.bottom - hh - 2), w, h: hh });
        let r = null;
        for (const [dx, dy] of spots) { const c = box(dx, dy); if (!hits(c)) { r = c; break; } }
        r ||= box(...spots[0]);
        placed.push(r);
        ctx.fillStyle = "rgba(255, 255, 255, .94)";
        ctx.beginPath(); ctx.roundRect(r.x, r.y, r.w, r.h, 5); ctx.fill();
        ctx.fillStyle = ds.borderColor === C.amber ? css("--amber-ink", "#9a4f00") : C.text2;
        ctx.fillText(ds.tag, r.x + pad, r.y + hh / 2 + 0.5);
      });
      ctx.restore();
    },
  };
}
