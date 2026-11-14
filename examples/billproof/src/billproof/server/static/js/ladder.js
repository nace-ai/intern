// Cost-vs-accuracy scatter shared by Results and the Benchmarks pages. Points come from data files only.
import { fmt, h } from "./ui.js";

// Model display names; unknown ids fall back to the part after the provider slash.
export const PRETTY = {
  "openai/gpt-5.4-mini": "GPT-5.4 mini", "anthropic/claude-haiku-5.5": "Claude Haiku 5.5", "google/gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
  "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash", "drex-max-v1.1": "Drex", "drex-max-finance": "Drex finance",
  "openai/gpt-6.1-sol": "GPT-6.1 Sol", "BillProof system: code + Drex": "BillProof system",
};
export const prettyModel = (name) => PRETTY[name] || String(name).split("/").pop();
export const modelKind = (name) => (/billproof|system/i.test(name) ? "system" : /^drex/i.test(name) ? "drex" : /gpt-6\.1-sol/i.test(name) ? "gpt" : "other");

export const LADDER_STYLE = {
  drex: { bg: "#4f46e5", border: "#4f46e5", w: 0, r: 8 },
  system: { bg: "#ffffff", border: "#4f46e5", w: 3, r: 8 },
  hybrid: { bg: "#c7c9f6", border: "#4f46e5", w: 2, r: 7 },
  gpt: { bg: "#64748b", border: "#64748b", w: 0, r: 8 },
  di: { bg: "#0f766e", border: "#0f766e", w: 0, r: 8 },
  combo: { bg: "#99d5ca", border: "#4f46e5", w: 2, r: 8 },
  other: { bg: "#b6bfcc", border: "#94a3b8", w: 1, r: 6 },
  partial: { bg: "#ffffff", border: "#94a3b8", w: 2, r: 6 },
};

// Point labels: try several spots around each point and keep the one that overlaps least with placed labels,
// every point, and the canvas edge.
export const ladderLabels = { id: "ladderLabels", afterDatasetsDraw(c) {
  const ctx = c.ctx, placed = [];
  const pts = c.data.datasets.map((d, i) => c.getDatasetMeta(i).data[0]).filter(Boolean);
  const dots = pts.map((el) => ({ x: el.x - 9, y: el.y - 9, w: 18, h: 18 }));
  const ov = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  ctx.save();
  c.data.datasets.forEach((d, i) => {
    const el = c.getDatasetMeta(i).data[0];
    if (!el) return;
    ctx.font = "600 12px Inter, sans-serif";
    let w = ctx.measureText(d.label).width;
    if (d.note) { ctx.font = "500 10.5px Inter, sans-serif"; w = Math.max(w, ctx.measureText(d.note).width); }
    const hgt = d.note ? 27 : 14;
    const tries = [[12, -7], [-w - 12, -7], [-w / 2, 12], [-w / 2, -hgt - 12], [12, 6], [12, -hgt - 4], [-w - 12, 6], [-w - 12, -hgt - 4]];
    let best = null, bestScore = Infinity;
    for (const [dx, dy] of tries) {
      const b = { x: el.x + dx, y: el.y + dy, w, h: hgt };
      const out = (b.x < 2 ? 2 - b.x : 0) + Math.max(0, b.x + b.w - c.width + 2) + (b.y < 0 ? -b.y : 0) + Math.max(0, b.y + b.h - c.chartArea.bottom);
      const score = placed.reduce((s, p) => s + ov(b, p), 0) + dots.reduce((s, p) => s + ov(b, p), 0) + out * 400;
      if (score < bestScore) { bestScore = score; best = b; }
      if (score === 0) break;
    }
    placed.push(best);
    ctx.textBaseline = "top";
    ctx.fillStyle = "#0f1b33"; ctx.font = "600 12px Inter, sans-serif"; ctx.fillText(d.label, best.x, best.y);
    if (d.note) { ctx.fillStyle = "#75809a"; ctx.font = "500 10.5px Inter, sans-serif"; ctx.fillText(d.note, best.x, best.y + 15); }
  });
  ctx.restore();
} };

export function ladderChart(canvas, pts, yLabel) {
  const lo = Math.max(0, Math.floor((Math.min(...pts.map((p) => p.acc)) - 0.05) * 10) / 10);
  const costs = pts.map((p) => p.cost);
  const xmin = 10 ** Math.floor(Math.log10(Math.min(...costs))), xmax = 10 ** Math.ceil(Math.log10(Math.max(...costs)));
  return new Chart(canvas, {
    type: "scatter",
    data: { datasets: pts.map((p) => {
      const s = LADDER_STYLE[p.partial ? "partial" : p.kind];
      return { label: p.model, note: p.note, row: p, data: [{ x: p.cost, y: p.acc }], backgroundColor: s.bg, borderColor: s.border, borderWidth: s.w, pointRadius: s.r, pointHoverRadius: s.r + 2, clip: false };
    }) },
    options: {
      animation: { duration: 200 }, maintainAspectRatio: false, layout: { padding: { right: 96, top: 18 } },
      plugins: { legend: { display: false }, title: { display: true, align: "start", text: `y = ${yLabel}`, color: "#42506b", font: { size: 12, weight: "600" }, padding: { bottom: 10 } }, tooltip: { callbacks: { label: (c) => {
        const r = c.dataset.row;
        return [`${r.model}: ${fmt.pct(r.acc, 1)} · ${fmt.usd(r.cost)} per 1,000 (list)`, r.n != null ? `n = ${fmt.int(r.n)}${r.partial ? " (partial prefix)" : ""}` : null, ...r.tip].filter(Boolean);
      } } } },
      scales: {
        x: { type: "logarithmic", min: xmin, max: xmax, title: { display: true, text: "$ per 1,000 cases, list price (log)" },
          ticks: { maxRotation: 0, callback: (v) => (Number.isInteger(Math.round(Math.log10(v) * 1e6) / 1e6) ? fmt.usd(v) : "") } },
        y: { min: lo, max: 1, title: { display: true, text: yLabel }, ticks: { callback: (v) => fmt.pct(v, 0) } },
      },
    },
    plugins: [ladderLabels],
  });
}


// Legend shared by every cost-vs-accuracy card.
export function ladderKey(all = true) {
  return h("span", { class: "ladder-key" },
    h("span", {}, h("i", { class: "lk lk-drex" }), "Drex"), h("span", {}, h("i", { class: "lk lk-system" }), "BillProof system"), h("span", {}, h("i", { class: "lk lk-hybrid" }), "hybrid"),
    h("span", {}, h("i", { class: "lk lk-di" }), "Qwen / DeepSeek via DeepInfra"), h("span", {}, h("i", { class: "lk lk-combo" }), "Drex + Qwen"),
    all ? [h("span", {}, h("i", { class: "lk lk-gpt" }), "GPT-6.1 Sol"), h("span", {}, h("i", { class: "lk lk-other" }), "other models")] : null,
    h("span", {}, h("i", { class: "lk lk-partial" }), "partial run"));
}
