// Confidence-dial explainer. ILLUSTRATION ONLY: the numbers below are example values chosen to show the idea,
// not a measured run, and the card says so. Measured dials stay on each benchmark's detail page.
import { h, icon } from "./ui.js";

// Example models: a cheap fast decision model and a big expensive one.
const SMALL = { acc: 0.85, cost: 1, secs: 0.5 };
const BIG = { acc: 0.99, cost: 100, secs: 10 };

// Share of questions sent to the big model at threshold t (0.5..1). Low-confidence answers are where the errors
// sit, so accuracy rises fast for the first questions routed and flattens after.
const routed = (t) => Math.pow((t - 0.5) / 0.5, 6);
function point(t) {
  const s = routed(t);
  return {
    t, s,
    acc: SMALL.acc + (BIG.acc - SMALL.acc) * (1 - Math.pow(1 - s, 6)),
    cost: SMALL.cost + s * BIG.cost,
    secs: SMALL.secs + s * BIG.secs,
  };
}

const W = 560, H = 260, PAD = { l: 48, r: 16, t: 14, b: 38 };
const X = (cost) => PAD.l + (cost / (SMALL.cost + BIG.cost)) * (W - PAD.l - PAD.r);
const Y = (acc) => PAD.t + (1 - (acc - 0.84) / (1 - 0.84)) * (H - PAD.t - PAD.b);

function svg() {
  const pts = [];
  for (let i = 0; i <= 100; i++) pts.push(point(0.5 + i * 0.005));
  const path = pts.map((p, i) => `${i ? "L" : "M"}${X(p.cost).toFixed(1)},${Y(p.acc).toFixed(1)}`).join(" ");
  const yt = [0.85, 0.9, 0.95, 1].map((a) => `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${Y(a)}" y2="${Y(a)}" class="dx-grid"/><text x="${PAD.l - 8}" y="${Y(a) + 4}" class="dx-axis" text-anchor="end">${Math.round(a * 100)}%</text>`).join("");
  const xt = [0, 25, 50, 75, 100].map((c) => `<text x="${X(c)}" y="${H - PAD.b + 18}" class="dx-axis" text-anchor="middle">$${c}</text>`).join("");
  const a = pts[0], b = pts[pts.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" class="dx-svg" role="img" aria-label="Illustration: accuracy rises quickly as more low-confidence questions go to the big model, while cost rises steadily">
    ${yt}${xt}
    <text x="${(W + PAD.l) / 2}" y="${H - 4}" class="dx-axis" text-anchor="middle">cost per 1,000 cases</text>
    <path d="${path}" class="dx-curve"/>
    <circle cx="${X(a.cost)}" cy="${Y(a.acc)}" r="6" class="dx-end dx-end--small"/>
    <text x="${X(a.cost) + 12}" y="${Y(a.acc) + 18}" class="dx-lbl">Drex only</text>
    <circle cx="${X(b.cost)}" cy="${Y(b.acc)}" r="6" class="dx-end dx-end--big"/>
    <text x="${X(b.cost) - 10}" y="${Y(b.acc) + 20}" class="dx-lbl" text-anchor="end">Big model only</text>
    <circle r="9" class="dx-dot" cx="0" cy="0"/>
  </svg>`;
}

export function explainDial({ start = 0.9 } = {}) {
  const chart = h("div", { class: "dx-chart" });
  chart.innerHTML = svg();
  const dot = chart.querySelector(".dx-dot");
  const out = {
    acc: h("div", { class: "dx-num" }), cost: h("div", { class: "dx-num" }), secs: h("div", { class: "dx-num" }),
    sent: h("div", { class: "dx-num" }), thr: h("span", { class: "dx-thr" }), vs: h("p", { class: "dx-vs" }),
  };
  const slider = h("input", { type: "range", min: "50", max: "100", step: "1", value: String(Math.round(start * 100)), class: "dx-range", "aria-label": "Confidence threshold" });
  const big = point(1);
  const times = (x) => `${x >= 10 ? Math.round(x) : x.toFixed(1)}×`;
  function update() {
    const p = point(Number(slider.value) / 100);
    dot.setAttribute("cx", X(p.cost).toFixed(1));
    dot.setAttribute("cy", Y(p.acc).toFixed(1));
    out.thr.textContent = `${slider.value}%`;
    out.acc.textContent = `${(p.acc * 100).toFixed(1)}%`;
    out.cost.textContent = `$${p.cost.toFixed(p.cost < 10 ? 2 : 0)}`;
    out.secs.textContent = `${p.secs.toFixed(1)} s`;
    out.sent.textContent = `${Math.round(p.s * 100)}%`;
    out.vs.textContent = p.s >= 0.999 ? "Everything goes to the big model: best accuracy, highest cost."
      : `Compared with the big model alone: ${times(big.cost / p.cost)} cheaper and ${times(big.secs / p.secs)} faster, for ${((big.acc - p.acc) * 100).toFixed(1)} points less accuracy.`;
  }
  slider.addEventListener("input", update);
  const stat = (label, node) => h("div", { class: "dx-stat" }, node, h("div", { class: "dx-stat-l" }, label));
  const card = h("div", { class: "card dx" },
    h("div", { class: "dx-hd" },
      h("span", { class: "t-h2" }, "The confidence dial"),
      h("span", { class: "tag tag--warn" }, icon("info"), "Illustration · example numbers, not a measured result")),
    h("p", { class: "dx-def" }, "Drex answers every question with a probability. Below the threshold, the question goes to a big model. Slide to trade cost and speed for accuracy."),
    h("div", { class: "dx-body" },
      chart,
      h("div", { class: "dx-side" },
        h("label", { class: "dx-ctl" }, h("span", {}, "Send to the big model when Drex is less than ", out.thr, " sure"), slider),
        h("div", { class: "dx-stats" },
          stat("accuracy", out.acc), stat("cost per 1,000", out.cost), stat("average time", out.secs), stat("sent to big model", out.sent)),
        out.vs,
        h("p", { class: "footnote" }, "Measured dials for each benchmark: Benchmarks → open one."))));
  update();
  return card;
}
