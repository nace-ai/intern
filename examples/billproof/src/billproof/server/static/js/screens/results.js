// Results: the confidence-dial explainer (illustration) on top, then one card per headline benchmark with three
// plain comparisons against Qwen3.8-Max via DeepInfra: accuracy, cost per 1,000 (× cheaper), median time (× faster).
// Numbers come from /api/results/summary; full lanes, charts and measured dials live on the Benchmarks pages.
import { h, icon, empty, screenHeader, fmt, diBadge } from "../ui.js";
import { api } from "../data.js";
import { normBench, trio } from "../resultsdata.js";
import { explainDial } from "../dialexplain.js";

const HEADLINE = [
  { id: "pa_criteria_gym", title: "Prior authorization", kind: "Real rules · synthetic patients" },
  { id: "persius", title: "Real appeals", kind: "Real" },
  { id: "hard", title: "Hard medical bills", kind: "Synthetic" },
];

function wilson(k, n, z = 1.96) {
  if (!n) return [0, 1];
  const p = k / n, d = 1 + (z * z) / n, c = p + (z * z) / (2 * n), m = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * n)) / n);
  return [(c - m) / d, (c + m) / d];
}

const times = (x) => `${x >= 10 ? Math.round(x) : x.toFixed(1)}×`;

function accVerdict(a, b) {
  if (a.k == null || b.k == null || !a.n || !b.n) return a.acc >= b.acc ? "higher" : "lower";
  const [al, ah] = wilson(a.k, a.n), [bl, bh] = wilson(b.k, b.n);
  if (al <= bh && bl <= ah) return "about the same";
  return a.acc > b.acc ? "higher" : "lower";
}

function stat(big, label, detail, tone = "") {
  return h("div", { class: `rs-stat ${tone}` },
    h("div", { class: "rs-big" }, big), h("div", { class: "rs-label" }, label), h("div", { class: "rs-detail" }, detail));
}

function card(spec, b) {
  const { drex, qwen } = trio(b);
  if (!drex || !qwen) return null;
  const ours = drex.kind === "system" ? "BillProof" : "Drex";
  const v = accVerdict(drex, qwen);
  const costX = drex.cost > 0 && qwen.cost > 0 ? qwen.cost / drex.cost : null;
  const timeX = drex.latency > 0 && qwen.latency > 0 ? qwen.latency / drex.latency : null;
  return h("div", { class: "card rs-card" },
    h("div", { class: "rs-hd" },
      h("span", { class: "t-h2" }, spec.title),
      h("span", { class: "tag" }, spec.kind),
      b.n != null ? h("span", { class: "tag" }, `n = ${fmt.int(b.n)}`) : null,
      h("span", { class: "spacer" }),
      h("a", { class: "btn btn--sm", href: `#/benchmarks/${encodeURIComponent(b.id)}` }, "Details", icon("arrow-right"))),
    h("div", { class: "rs-stats" },
      stat(fmt.pct(drex.acc, 1), `accuracy · ${v}`, `${ours} ${fmt.pct(drex.acc, 1)} vs Qwen ${fmt.pct(qwen.acc, 1)}`, v === "lower" ? "is-warn" : ""),
      costX ? stat(costX >= 1 ? `${times(costX)} cheaper` : `${times(1 / costX)} costlier`, "cost per 1,000", `${fmt.usd(drex.cost)} vs ${fmt.usd(qwen.cost)}`) : null,
      timeX ? stat(timeX >= 1 ? `${times(timeX)} faster` : `${times(1 / timeX)} slower`, "median time per case", `${fmt.secs(drex.latency)} vs ${fmt.secs(qwen.latency)}`) : null));
}

export default {
  async mount(el) {
    el.append(screenHeader({
      title: "Results",
      sub: h("span", {}, "BillProof vs Qwen3.8-Max ", diBadge(), " on the same cases. Every number is from a recorded real run."),
      right: h("a", { class: "btn", href: "#/benchmarks" }, icon("list-checks"), "All benchmarks"),
    }));
    el.append(explainDial());
    const s = await api.resultsSummary();
    if (!s.ok) { el.append(empty("Results hub not reachable", "GET /api/results/summary did not answer. Nothing is shown until it does.", "hourglass")); return; }
    const by = Object.fromEntries((s.data.benchmarks || []).map((b) => [b.id, b]));
    for (const spec of HEADLINE) {
      if (!by[spec.id]) continue;
      const c = card(spec, normBench(by[spec.id]));
      if (c) el.append(c);
    }
    if (s.data.price_basis) el.append(h("p", { class: "footnote", style: { marginTop: "1rem" } }, s.data.price_basis));
  },
};
