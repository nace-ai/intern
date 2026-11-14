// Benchmarks: overview of every benchmark, one benchmark's lanes / breakdown / cost-vs-accuracy, a paged case list,
// and one case with each lane's answers. Single source: /api/results/*. Every chart and primary column
// plots lanes[].metrics[chart.metric] and prints that metric's name.
import { h, icon, model, pending, empty, screenHeader, fmt, fill, diBadge } from "../ui.js";
import { api } from "../data.js";
import { normBench, normCases, normCase, metricName, onStage, qwenPick, partialText, trio } from "../resultsdata.js";
import { ladderChart, ladderKey } from "../ladder.js";
import { dialSlot, routedAt } from "../dialmount.js";

const PENDING_HUB = () => empty("Results hub not reachable", "GET /api/results/* did not answer. Nothing is shown until it does.", "hourglass");
const PAGE = 50;

export default {
  async mount(el, params) {
    const { bench, view, id } = params;
    if (!bench) return overview(el);
    if (view === "cases") return caseList(el, bench, params);
    if (view === "case" && id) return caseView(el, bench, id, params);
    return detail(el, bench);
  },
};

// ---------------------------------------------------------------- shared bits
const crumbs = (...items) => h("div", { class: "crumbs" }, items.map((it, i) => [i ? icon("chevron-right") : null,
  it.href ? h("a", { href: it.href }, it.label) : h("span", {}, it.label)]));
const chip = (kind) => ({ gpt: "gpt", drex: "drex", system: "hybrid", hybrid: "hybrid", di: "di", combo: "combo" })[kind] || "other";
// Share of questions a lane sends to Qwen: Drex 0, a Qwen lane all of them, a Drex + Qwen lane what the hub reports.
const toQwen = (l) => (!l ? null : l.combo ? l.routed : l.di ? (l.dim === "ds" ? null : 1) : ["drex", "system"].includes(l.kind) ? 0 : null);
// Lane name; DeepInfra lanes carry the DeepInfra badge and their partial n.
const laneName = (l, label = l.label) => (l.di || l.combo ? h("span", { class: "di-who" }, model(l.combo ? "combo" : "di", label), diBadge(), partialText(l) ? tag(partialText(l), true) : null) : model(chip(l.kind), label));
const tag = (t, warn) => h("span", { class: `tag${warn ? " tag--warn" : ""}` }, t);
const flagTags = (flags) => flags.map((f) => tag(f, f !== "subset"));
const pct = (v) => (v == null ? "—" : fmt.pct(v, 1));
const ciText = (ci) => (ci ? `${fmt.pct(ci[0], 1)}–${fmt.pct(ci[1], 1)}` : null);
const kn = (l) => (l.k != null && l.n != null ? `${fmt.int(l.k)}/${fmt.int(l.n)}` : l.n != null ? `n ${fmt.int(l.n)}` : null);
const laneSub = (l) => [l.di ? null : l.flags.includes("partial") && l.nCases ? `partial: ${l.nCases} cases` : l.flags.includes("partial") ? "partial" : null,
  ...l.flags.filter((f) => f !== "partial"), l.costBasis && l.costBasis !== "list" ? `cost: ${l.costBasis.split(/[\s,]/)[0]}` : null].filter(Boolean);
// The API marks some lanes correct:false with k/n = 0/0 and no per-question scoring; that is "not scored", not wrong.
const verdict = (a) => (!a ? null : a.n === 0 ? null : a.correct);
const benchHref = (id) => `#/benchmarks/${encodeURIComponent(id)}`;

// ---------------------------------------------------------------- overview
async function overview(el) {
  el.append(screenHeader({ title: "Benchmarks", sub: "Every test we ran: what it measures, how big it is, and how BillProof compares with Qwen3.8-Max via DeepInfra." }));
  const r = await api.resultsSummary();
  if (!r.ok) { el.append(PENDING_HUB()); return; }
  const benches = (r.data.benchmarks || []).map(normBench);
  if (!benches.length) { el.append(empty("No benchmarks in the results hub yet", "")); return; }
  el.append(h("div", { class: "bench-grid" }, benches.map(benchCard)));
  el.append(h("p", { class: "footnote", style: { marginTop: "1rem" } },
    r.data.generated_at ? `Results hub generated ${String(r.data.generated_at).slice(0, 16).replace("T", " ")} UTC. ` : "", r.data.price_basis || ""));
}

function benchCard(b) {
  const { drex: best, qwen, combo } = trio(b);
  const { small } = qwenPick(b);
  const row = (l, who) => h("div", { class: "bc-row" },
    h("div", { class: "bc-who" }, l ? laneName(l, l.di ? l.short : l.combo ? "Drex + Qwen" : l.label) : who),
    l ? [
      h("div", { class: "bc-acc num" }, pct(l.acc), h("span", { class: "bc-ci" }, [ciText(l.ci), kn(l)].filter(Boolean).join(" · "))),
      h("div", { class: "bc-num num" }, l.cost != null ? fmt.usd(l.cost) : "—", l.cost != null ? h("span", { class: "bc-unit" }, "/1k") : null),
      h("div", { class: "bc-num num" }, l.latency != null ? fmt.secs(l.latency) : "—"),
      h("div", { class: "bc-num num" }, toQwen(l) != null ? fmt.pct(toQwen(l), 0) : "—"),
    ] : h("div", { class: "bc-pend muted t-small" }, pending("pending")));
  return h("a", { class: "card bench-card", href: benchHref(b.id) },
    h("div", { class: "bc-hd" }, h("div", { class: "bc-title" }, b.title), h("span", { class: "spacer" }), h("span", { class: "bc-n num" }, b.n != null ? `n = ${fmt.int(b.n)}` : "")),
    h("div", { class: "bc-what" }, b.tests || h("span", { class: "muted" }, "—")),
    h("div", { class: "bc-tags" }, h("span", { class: "tag" }, icon("target"), metricName(b.metric)), b.split ? tag(b.split) : null, flagTags(b.flags),
      b.addedLater ? tag("metric added after first results", true) : null),
    h("div", { class: "bc-table" },
      h("div", { class: "bc-row bc-head" }, h("div", {}, ""), h("div", {}, `${metricName(b.metric)} · 95% CI`), h("div", {}, "$ per 1,000"), h("div", {}, "Latency"), h("div", {}, "To Qwen")),
      row(best, h("span", { class: "muted" }, "Drex")),
      row(qwen, h("span", { class: "di-who" }, model("di", "Qwen3.8-Max"), diBadge())),
      row(combo, h("span", { class: "di-who" }, model("combo", "Drex + Qwen"), diBadge())), small.slice(0, 1).map((l) => row(l))),
    h("div", { class: "bc-foot" }, h("span", {}, `${b.lanes.length} lane${b.lanes.length === 1 ? "" : "s"} · details`), icon("arrow-right")));
}

// ---------------------------------------------------------------- detail
async function detail(el, id) {
  el.append(crumbs({ label: "Benchmarks", href: "#/benchmarks" }, { label: id }));
  const r = await api.resultsBench(id);
  if (!r.ok) { el.append(screenHeader({ title: id }), r.status === 404 ? empty("Unknown benchmark", "") : PENDING_HUB()); return; }
  const b = normBench(r.data);
  el.append(screenHeader({
    title: b.title, sub: b.tests,
    badges: [b.n != null ? tag(`n = ${fmt.int(b.n)}`) : null, b.split ? tag(b.split) : null, ...flagTags(b.flags)].filter(Boolean),
    right: h("a", { class: "btn", href: `${benchHref(b.id)}/cases` }, icon("list-checks"), "Explore cases"),
  }));
  el.append(h("div", { class: "metric-def card" }, h("span", { class: "tag" }, icon("target"), metricName(b.metric)), h("span", {}, b.metricDef)));
  el.append(trioCards(b), benchSection(b));
  el.append(dialSlot(b.id, { metricHint: b.metric }));
  for (const s of b.secondary) el.append(secondaryTable(s));
  for (const bd of b.breakdowns) el.append(breakdownTable(b, bd));
  if (b.notes.length || b.sources.length) {
    el.append(h("div", { class: "card bd-notes" }, h("div", { class: "card-hd" }, icon("info"), h("span", { class: "card-title" }, "Notes")),
      h("ul", {}, b.notes.map((n) => h("li", {}, n))),
      b.sources.length ? h("div", { class: "footnote" }, "Sources: ", b.sources.join(" · ")) : null));
  }
}

// Lanes table + cost-vs-accuracy chart for one benchmark (also used by the Results screen).
// Three columns for the stage: Drex · Qwen3.8-Max via DeepInfra · Drex + Qwen (accuracy, $ per 1,000, % sent to Qwen).
export function trioCards(b) {
  const t = trio(b);
  const col = (l, kind, name, badge) => h("div", { class: `card trio-card trio--${kind}` },
    h("div", { class: "trio-hd" }, model(kind, l && kind === "di" ? l.short : name), badge ? diBadge() : null, l && partialText(l) ? tag(partialText(l), true) : null),
    l ? [
      h("div", { class: "trio-acc num" }, pct(l.acc)),
      h("div", { class: "trio-sub" }, [metricName(b.metric), ciText(l.ci), kn(l)].filter(Boolean).join(" · ")),
      h("div", { class: "trio-stats" },
        h("div", {}, h("div", { class: "t-label" }, "$ per 1,000"), h("div", { class: "trio-v num" }, l.cost != null ? fmt.usd(l.cost) : "—")),
        h("div", {}, h("div", { class: "t-label" }, "Sent to Qwen"), h("div", { class: "trio-v num" }, toQwen(l) != null ? fmt.pct(toQwen(l), 0) : "—"))),
      l.combo && l.label ? h("div", { class: "trio-sub" }, l.label) : null,
    ] : h("div", { class: "trio-pend" }, pending("Pending")));
  return h("div", { class: "trio" },
    col(t.drex, "drex", "Drex", false),
    col(t.qwen, "di", "Qwen3.8-Max via DeepInfra", true),
    col(t.combo, "combo", "Drex + Qwen", true));
}

// Stage view by default (Drex / BillProof + DeepInfra lanes); "More models" adds GPT-6.1 Sol and the token-router lanes.
export function benchSection(b0) {
  const wrap = h("div");
  let all = false;
  const hidden = b0.lanes.filter((l) => !onStage(l)).length;
  const draw = () => {
    const b = all ? b0 : { ...b0, lanes: b0.lanes.filter(onStage) };
    const toggle = hidden ? h("button", { class: "btn btn--sm more-toggle", onclick: () => { all = !all; draw(); } },
      icon("layers"), all ? "Stage lanes only" : `More models (${hidden})`) : null;
    fill(wrap, benchGrid(b, toggle, all));
  };
  draw();
  return wrap;
}

function benchGrid(b, toggle, all) {
  const grid = h("div", { class: "bd-stack" });
  const { qwen } = qwenPick(b);
  grid.append(h("div", { class: "card bd-lanes" },
    h("div", { class: "card-hd" }, h("span", { class: "card-title" }, "Lanes"), h("span", { class: "card-sub" }, `sorted by ${metricName(b.metric)}; click a header to sort`), toggle),
    b.lanes.length ? lanesTable(b) : pending("No lanes"),
    qwen ? null : h("div", { class: "row-tags", style: { marginTop: ".5rem" } }, model("di", "Qwen3.8-Max"), diBadge(), pending("pending on this benchmark"))));
  const pts = b.lanes.filter((l) => l.acc != null && l.cost > 0).map((l) => ({
    model: l.label, kind: l.kind, acc: l.acc, cost: l.cost, n: l.nCases ?? l.n, partial: l.flags.includes("partial"),
    note: [l.flags.includes("partial") ? `partial${l.nCases ? `, ${l.nCases} cases` : ""}` : null, l.flags.includes("simulated") ? "simulated" : null, l.flags.includes("projected") ? "projected" : null].filter(Boolean).join(" · "),
    tip: [l.ci ? `95% CI ${ciText(l.ci)}` : null, kn(l) ? `${kn(l)} scored` : null, l.costBasis && l.costBasis !== "list" ? `cost basis: ${l.costBasis}` : null,
      ...b.otherMetrics.map((m) => (l.metrics[m]?.value != null ? `${metricName(m)} ${fmt.pct(l.metrics[m].value, 1)}` : null))].filter(Boolean),
  }));
  const chartCard = h("div", { class: "card bd-chart" }, h("div", { class: "card-hd" }, icon("trending-up"), h("span", { class: "card-title" }, "Cost vs accuracy")), ladderKey(all));
  if (pts.length >= 2) {
    const canvas = h("canvas", {});
    chartCard.append(h("div", { class: "ladder-wrap" }, canvas),
      h("div", { class: "footnote" }, `Plotted: lanes[].metrics.${b.metric}. Same frozen cases for every lane · costs as labelled per lane (list price unless stated) · point estimates.`));
    requestAnimationFrame(() => ladderChart(canvas, pts, metricName(b.metric)));
  } else chartCard.append(h("p", { class: "card-sub" }, `Needs ${metricName(b.metric)} and a cost for at least two lanes.`));
  grid.append(chartCard);
  return grid;
}

// Sortable lanes table: primary metric first, then every other metric the API sends, then cost and latency.
function lanesTable(b) {
  const cols = [
    { k: "label", t: "Lane", asc: true, v: (l) => l.label, f: (l) => h("span", {}, laneName(l), laneSub(l).length ? h("div", { class: "row-tags", title: l.costBasis || "" }, laneSub(l).map((t) => tag(t, true))) : null) },
    { k: "acc", t: metricName(b.metric), v: (l) => l.acc, f: (l) => h("span", { class: "prim" }, pct(l.acc), h("div", { class: "res-row-sub" }, [ciText(l.ci), kn(l)].filter(Boolean).join(" · "))) },
    ...b.otherMetrics.map((m) => ({ k: `m:${m}`, t: metricName(m), v: (l) => l.metrics[m]?.value ?? null, f: (l) => pct(l.metrics[m]?.value) })),
    { k: "cost", t: "$ per 1,000", asc: true, v: (l) => l.cost, f: (l) => (l.cost != null ? fmt.usd(l.cost) : "—") },
    { k: "latency", t: "Latency", asc: true, v: (l) => l.latency, f: (l) => (l.latency != null ? fmt.secs(l.latency) : "—") },
  ];
  let sort = { k: "acc", dir: -1 };
  const tbody = h("tbody"), head = h("tr");
  const draw = () => {
    const col = cols.find((c) => c.k === sort.k);
    const rows = [...b.lanes].sort((x, y) => {
      const a = col.v(x), c = col.v(y);
      if (a == null) return 1; if (c == null) return -1;
      return (typeof a === "string" ? a.localeCompare(c) : a - c) * sort.dir;
    });
    fill(tbody, rows.map((l) => h("tr", { class: `lane-${l.kind}` }, cols.map((c) => h("td", { class: c.k === "label" ? "" : "r num" }, c.f(l))))));
    fill(head, cols.map((c) => h("th", { class: `${c.k === "label" ? "" : "r "}sortable${sort.k === c.k ? " is-sorted" : ""}`,
      onclick: () => { sort = { k: c.k, dir: sort.k === c.k ? -sort.dir : c.asc ? 1 : -1 }; draw(); } },
    c.t, sort.k === c.k ? icon("chevron-down", sort.dir > 0 ? "flip" : "") : null)));
  };
  draw();
  return h("table", { class: "table lanes-table" }, h("thead", {}, head), tbody);
}

function secondaryTable(s) {
  return h("div", { class: "card bd-break" },
    h("div", { class: "card-hd" }, icon("layers"), h("span", { class: "card-title" }, `Secondary: ${metricName(s.metric)}`), h("span", { class: "card-sub" }, s.def)),
    h("table", { class: "table break-table" }, h("thead", {}, h("tr", {}, h("th", {}, "Lane"), h("th", { class: "r" }, metricName(s.metric)), h("th", { class: "r" }, "95% CI"), h("th", { class: "r" }, "k / n"))),
      h("tbody", {}, s.lanes.map((l) => h("tr", {}, h("td", {}, model(chip(l.kind), l.label)), h("td", { class: "r num" }, pct(l.acc)), h("td", { class: "r num" }, ciText(l.ci) || "—"), h("td", { class: "r num" }, kn(l) || "—"))))));
}

// One breakdown dimension (e.g. family, decision): groups × lanes, with a switch between measures (precision / recall).
function breakdownTable(b, bd) {
  const laneIds = b.lanes.map((l) => l.id).filter((id) => bd.groups.some((g) => Object.values(g.by).some((m) => m[id] != null)));
  const label = (id) => b.lanes.find((l) => l.id === id)?.label || id;
  let measure = bd.measures.includes("recall") ? "recall" : bd.measures[0];
  const table = h("div");
  const draw = () => fill(table, h("table", { class: "table break-table" },
    h("thead", {}, h("tr", {}, h("th", {}, bd.dim.replace(/_/g, " ")), h("th", { class: "r" }, "n"), laneIds.map((id) => h("th", { class: "r" }, label(id))))),
    h("tbody", {}, bd.groups.map((g) => {
      const vals = laneIds.map((id) => g.by[measure]?.[id]).filter((v) => v != null);
      const top = vals.length > 1 && new Set(vals).size > 1 ? Math.max(...vals) : null;
      return h("tr", {}, h("td", {}, g.key.replace(/_/g, " ")), h("td", { class: "r num" }, g.n != null ? fmt.int(g.n) : "—"),
        laneIds.map((id) => { const v = g.by[measure]?.[id]; return h("td", { class: `r num${v != null && v === top ? " is-top" : ""}` }, pct(v)); }));
    }))));
  const seg = bd.measures.length > 1 ? h("div", { class: "seg seg--sm" }, bd.measures.map((m) => h("button", { class: m === measure ? "is-on" : "",
    onclick: (e) => { measure = m; seg.querySelectorAll("button").forEach((x) => x.classList.toggle("is-on", x === e.currentTarget)); draw(); } }, m))) : null;
  draw();
  return h("div", { class: "card bd-break" },
    h("div", { class: "card-hd" }, icon("layers"), h("span", { class: "card-title" }, `By ${bd.dim.replace(/_/g, " ")}`),
      h("span", { class: "card-sub" }, bd.measures.length === 1 ? bd.measures[0] : ""), h("span", { class: "spacer" }), seg), table);
}

// ---------------------------------------------------------------- case list
async function caseList(el, bench, params) {
  const offset = Math.max(0, Number(params.offset) || 0);
  const q = { lane: params.lane || "", only_wrong: params.wrong === "1" ? "true" : "", offset, limit: PAGE };
  const [br, r] = await Promise.all([api.resultsBench(bench), api.resultsCases(bench, q)]);
  const b = br.ok ? normBench(br.data) : null;
  el.append(crumbs({ label: "Benchmarks", href: "#/benchmarks" }, { label: b?.title || bench, href: benchHref(bench) }, { label: "Cases" }));
  el.append(screenHeader({ title: "Case explorer", sub: "Every case, every lane. Filter to the misses and open one to see the answers." }));
  if (!r.ok) { el.append(PENDING_HUB()); return; }
  const d = normCases(r.data);
  const lanes = b ? b.lanes : [...new Set(d.cases.flatMap((c) => Object.keys(c.lanes)))].map((id) => ({ id, label: id, kind: "other" }));
  const go = (patch) => {
    const next = { lane: q.lane, wrong: q.only_wrong ? "1" : "", offset: 0, t: params.t || "", ...patch };
    const qs = new URLSearchParams(Object.entries(next).filter(([, v]) => v)).toString();
    location.hash = `${benchHref(bench)}/cases${qs ? `?${qs}` : ""}`;
  };
  const from = d.total ? d.offset + 1 : 0, to = d.offset + d.cases.length;
  el.append(h("div", { class: "filters card" },
    h("label", { class: "filter" }, h("span", { class: "t-label" }, "Lane"),
      h("select", { onchange: (e) => go({ lane: e.target.value }) }, h("option", { value: "" }, "All lanes"),
        lanes.map((l) => h("option", { value: l.id, selected: l.id === q.lane ? true : null }, l.label)))),
    h("label", { class: "filter filter--check" }, h("input", { type: "checkbox", checked: q.only_wrong ? true : null, onchange: (e) => go({ wrong: e.target.checked ? "1" : "" }) }),
      q.lane ? "Only cases this lane got wrong" : "Only cases a lane got wrong"),
    h("span", { class: "spacer" }),
    h("span", { class: "t-small muted num" }, d.total != null ? `${fmt.int(from)}–${fmt.int(to)} of ${fmt.int(d.total)}` : ""),
    h("button", { class: "btn btn--sm btn--icon", disabled: d.offset <= 0 ? true : null, onclick: () => go({ offset: Math.max(0, d.offset - PAGE) }), "aria-label": "Previous page" }, icon("chevron-left")),
    h("button", { class: "btn btn--sm btn--icon", disabled: d.total == null || to >= d.total ? true : null, onclick: () => go({ offset: d.offset + PAGE }), "aria-label": "Next page" }, icon("chevron-right"))));
  if (!d.cases.length) { el.append(empty("No cases match these filters", "")); return; }
  const shown = q.lane ? lanes.filter((l) => l.id === q.lane) : lanes.filter((l) => d.cases.some((c) => c.lanes[l.id]));
  const mark = (a) => {
    if (!a) return h("span", { class: "muted" }, "—");
    const v = verdict(a);
    if (a.n === 0) return h("span", { class: "mark", title: "not scored per case in the results hub" }, icon("circle-dashed"), h("span", { class: "t-tiny muted" }, "not scored"));
    return h("span", { class: `mark ${v === true ? "is-ok" : v === false ? "is-bad" : ""}`, title: a.wrong?.length ? `wrong: ${a.wrong.join(", ")}` : "" },
      icon(v === true ? "circle-check" : v === false ? "circle-x" : "circle-dashed"),
      a.k != null && a.n != null && a.n > 1 ? h("span", { class: "num t-tiny" }, `${a.k}/${a.n}`) : a.choice ? h("span", { class: "t-tiny" }, String(a.choice).replace(/_/g, " ")) : null);
  };
  el.append(h("div", { class: "card case-table-card" }, h("table", { class: "table case-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Case"), h("th", {}, "Answer key"), shown.map((l) => h("th", { class: "c" }, l.label)))),
    h("tbody", {}, d.cases.map((c) => h("tr", { class: "click-row", onclick: () => { location.hash = `${benchHref(bench)}/case/${encodeURIComponent(c.id)}${params.t ? `?t=${params.t}` : ""}`; } },
      h("td", {}, h("div", { class: "mono t-small case-id" }, c.id)),
      h("td", { class: "t-small" }, c.gold != null ? String(c.gold).replace(/_/g, " ") : "—"),
      shown.map((l) => h("td", { class: "c" }, mark(c.lanes[l.id])))))))));
}

// ---------------------------------------------------------------- one case
async function caseView(el, bench, id, params = {}) {
  const t = params.t != null && params.t !== "" ? Number(params.t) : null;
  const [br, r] = await Promise.all([api.resultsBench(bench), api.resultsCase(bench, id)]);
  const b = br.ok ? normBench(br.data) : null;
  el.append(crumbs({ label: "Benchmarks", href: "#/benchmarks" }, { label: b?.title || bench, href: benchHref(bench) },
    { label: "Cases", href: `${benchHref(bench)}/cases` }, { label: id }));
  if (!r.ok) { el.append(screenHeader({ title: id }), r.status === 404 ? empty("Case not found", "") : PENDING_HUB()); return; }
  const c = normCase(r.data);
  const label = (lid) => b?.lanes.find((l) => l.id === lid)?.label || lid;
  const laneIds = b ? b.lanes.map((l) => l.id).filter((x) => c.lanes[x]) : Object.keys(c.lanes);
  el.append(screenHeader({ title: h("span", { class: "mono case-title-id" }, c.id), sub: c.goldSummary ? `Answer key: ${String(c.goldSummary).replace(/_/g, " ")}` : "" }));

  el.append(h("div", { class: "lane-strip" }, laneIds.map((lid) => {
    const a = c.lanes[lid], v = verdict(a);
    return h("div", { class: `lane-pill ${v === false ? "is-bad" : v === true ? "is-ok" : ""}` }, icon(v === false ? "circle-x" : v === true ? "circle-check" : "circle-dashed"),
      h("span", { class: "lane-pill-name" }, label(lid)), a.n === 0 ? h("span", { class: "t-small muted" }, "not scored") : a.k != null && a.n != null ? h("span", { class: "num t-small" }, `${a.k}/${a.n}`) : null,
      a.choice != null ? h("span", { class: "t-small" }, String(a.choice).replace(/_/g, " ")) : null);
  })));

  const goldQ = c.gold && typeof c.gold === "object" && c.gold.questions ? c.gold.questions : null;
  const perQuestion = goldQ && laneIds.some((lid) => { const v = Object.values(c.lanes[lid].answers || {})[0]; return v && typeof v === "object" && "choice" in v; });
  if (perQuestion) {
    el.append(questionMatrix(c, goldQ, laneIds, label, t != null ? await routedAt(bench, t) : null, t, bench));
    el.append(h("div", { class: "card", style: { marginTop: "1.25rem" } }, h("div", { class: "card-hd" }, icon("file-text"), h("span", { class: "card-title" }, "Inputs")), renderValue(c.inputs)));
  } else {
    el.append(h("div", { class: "cv-grid" },
      h("div", { class: "cv-right" },
        h("div", { class: "card" }, h("div", { class: "card-hd" }, icon("file-text"), h("span", { class: "card-title" }, "Inputs")), renderValue(c.inputs)),
        h("div", { class: "card" }, h("div", { class: "card-hd" }, icon("flag"), h("span", { class: "card-title" }, "Answer key")), renderValue(c.gold))),
      h("div", { class: "cv-right" }, laneIds.map((lid) => {
        const a = c.lanes[lid];
        const v = verdict(a);
        return h("div", { class: `card lane-answer ${v === false ? "is-bad" : ""}` },
          h("div", { class: "card-hd" }, model(chip(b?.lanes.find((l) => l.id === lid)?.kind || "other"), label(lid)), h("span", { class: "spacer" }),
            v === false ? h("span", { class: "pill pill--red" }, icon("circle-x"), "wrong") : v === true ? h("span", { class: "pill pill--green" }, icon("circle-check"), "right") : h("span", { class: "pill pill--neutral" }, "not scored")),
          a.wrong?.length ? h("div", { class: "t-small wrong-list" }, "Wrong: ", a.wrong.join(", ")) : null,
          renderValue(a.answers ?? a.choice));
      }))));
  }
}

// Question × lane grid; wrong answers in red, confidence beside each answer. Defaults to rows where any lane is wrong.
function questionMatrix(c, goldQ, laneIds, label, routedList, t, bench) {
  const qids = Object.keys(goldQ);
  const wrongAny = (q) => laneIds.some((lid) => c.lanes[lid].answers?.[q]?.correct === false);
  let onlyWrong = qids.some(wrongAny);
  const body = h("div", { class: "ans-scroll" });
  // Trust dial (UI-3's routedAt): which questions of this case go to GPT-6.1 Sol at threshold t. Accepts a list of
  // question ids, or { [caseId]: [...] }, or { routed: [...] }.
  // DIAL endpoint lists routed questions per case in raw.routed_by_case; prefer that, then UI-3's routed list.
  const byCase = routedList?.raw?.routed_by_case;
  const caseEntry = Array.isArray(byCase) ? byCase.find((e) => String(e.case_id) === c.id) : null;
  const ids = Array.isArray(byCase) ? (caseEntry?.routed ?? []) : Array.isArray(routedList) ? routedList : (routedList?.routed?.length ? routedList.routed : null) ?? routedList?.[c.id] ?? routedList?.questions ?? null;
  // ids may be question ids, "case:question" / "case/question" strings, { case_id, question } objects, or whole-case ids
  const routedSet = new Set();
  for (const x of Array.isArray(ids) ? ids : []) {
    if (x && typeof x === "object") { if (String(x.case_id ?? c.id) === c.id) routedSet.add(String(x.question ?? x.qid ?? x.id ?? "*")); continue; }
    const s = String(x);
    if (s === c.id) routedSet.add("*");
    else if (s.startsWith(`${c.id}:`) || s.startsWith(`${c.id}/`) || s.startsWith(`${c.id}#`)) routedSet.add(s.slice(c.id.length + 1));
    else if (!/[:/#]/.test(s)) routedSet.add(s);
  }
  if (routedSet.has("*")) qids.forEach((q) => routedSet.add(q));
  const canRoute = t != null && Array.isArray(ids);
  const routed = (q) => canRoute && routedSet.has(q);
  const routeBar = t == null ? null : h("div", { class: "route-bar t-small" }, canRoute
    ? [h("b", {}, `${qids.filter(routed).length} of ${qids.length} questions`), ` go to GPT-6.1 Sol at the trust-dial setting ${Math.round((routedList?.t ?? t) * 1000) / 10}% (highlighted). `,
      h("span", { class: "muted" }, routedList?.raw?.label || "simulated from recorded answers")]
    : `Trust dial setting ${Math.round(t * 1000) / 10}%: routed questions pending (the dial component or its endpoint is not live yet).`);
  const qlabel = (q) => { const m = q.match(/^([a-z]+)_L(\d+)$/i); return m ? h("span", {}, h("span", { class: "mono" }, `L${m[2]}`), " ", m[1]) : q.replace(/_/g, " "); };
  const draw = () => {
    const rows = onlyWrong ? qids.filter(wrongAny) : qids;
    fill(body, h("table", { class: "table ans-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Question"), h("th", {}, "Key"), canRoute ? h("th", {}, "Route") : null, laneIds.map((lid) => h("th", {}, label(lid))))),
      h("tbody", {}, rows.map((q) => h("tr", { class: routed(q) ? "is-routed" : "" }, h("td", { class: "t-small" }, qlabel(q)), h("td", { class: "t-small" }, fmtAnswer(goldQ[q])),
        canRoute ? h("td", {}, routed(q) ? h("span", { class: "route-chip" }, "→ GPT") : h("span", { class: "t-tiny muted" }, "Drex")) : null,
        laneIds.map((lid) => {
          const a = c.lanes[lid].answers?.[q];
          if (!a) return h("td", {}, h("span", { class: "muted" }, "—"));
          return h("td", {}, h("span", { class: `ans ${a.correct === false ? "is-bad" : a.correct === true ? "is-ok" : ""}` }, fmtAnswer(a.choice),
            a.confidence != null ? h("span", { class: "ans-conf num" }, fmt.pct(a.confidence, 0)) : null));
        }))))));
  };
  const toggle = h("label", { class: "filter filter--check" }, h("input", { type: "checkbox", checked: onlyWrong ? true : null, onchange: (e) => { onlyWrong = e.target.checked; draw(); } }), "Only questions a lane got wrong");
  draw();
  return h("div", { class: "card" }, h("div", { class: "card-hd" }, icon("scale"), h("span", { class: "card-title" }, "Answers by question"),
    h("span", { class: "card-sub" }, `${qids.length} questions · wrong answers in red · confidence beside each answer`), h("span", { class: "spacer" }), toggle), routeBar, body,
    laneIds.some((lid) => c.lanes[lid].n === 0) ? h("div", { class: "footnote", style: { marginTop: ".6rem" } },
      `Not scored per question by the results hub (shown in grey, never marked wrong): ${laneIds.filter((lid) => c.lanes[lid].n === 0).map(label).join(", ")}.`) : null);
}

const fmtAnswer = (v) => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v).replace(/_/g, " "));

function renderValue(v) {
  if (v == null) return h("span", { class: "muted" }, "not provided");
  if (typeof v !== "object") return h("div", { class: "cv-text" }, String(v).replace(/_/g, " "));
  if (Array.isArray(v) && v.length && v.every((x) => x && typeof x === "object" && !Array.isArray(x))) {
    const keys = [...new Set(v.flatMap((x) => Object.keys(x)))];
    return h("table", { class: "table cv-table" }, h("thead", {}, h("tr", {}, keys.map((k) => h("th", {}, k.replace(/_/g, " "))))),
      h("tbody", {}, v.map((x) => h("tr", {}, keys.map((k) => h("td", {}, x[k] == null ? "—" : typeof x[k] === "object" ? JSON.stringify(x[k]) : String(x[k])))))));
  }
  if (Array.isArray(v)) return h("ol", { class: "cv-list" }, v.map((x) => h("li", {}, typeof x === "object" ? renderValue(x) : String(x))));
  return h("dl", { class: "cv-kv" }, Object.entries(v).map(([k, x]) => [h("dt", {}, k.replace(/_/g, " ")),
    h("dd", {}, x != null && typeof x === "object" ? renderValue(x) : typeof x === "boolean" ? h("span", { class: x ? "ok-text" : "bad-text" }, String(x)) : String(x ?? "—").replace(/_/g, " "))]));
}
