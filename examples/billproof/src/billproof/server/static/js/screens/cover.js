// Cover: one line on what BillProof does, the datasets we tested on (two lines each), and the chain of Nace
// products. Dataset sizes come from the results hub and the Command Center summary; a missing size is left out.
import { h, icon, fmt } from "../ui.js";
import { metrics, api } from "../data.js";

const CHAIN = [
  { icon: "globe", step: "Find the policy", product: "Browser runs" },
  { icon: "scan-text", step: "Read the documents", product: "NDI" },
  { icon: "file-search", step: "Find the evidence", product: "Drex Search" },
  { icon: "scale", step: "Decide each rule", product: "Drex" },
  { icon: "mail", step: "Draft the letter", product: "Fixed clauses" },
];

const KIND = {
  real: { label: "Real", cls: "ds-tag--real" },
  mixed: { label: "Real rules · synthetic patients", cls: "ds-tag--mixed" },
  synth: { label: "Synthetic", cls: "ds-tag--synth" },
};

function datasets(n) {
  const size = (v, unit) => (v != null ? `${fmt.int(v)} ${unit}` : null);
  return [
    { kind: "mixed", title: "Prior authorization", size: size(n.pa, "requests"),
      lines: ["Real published Medicaid drug rules, with synthetic patient files.",
        "Drex decides each rule and the request: approve, deny, or ask for documents."] },
    { kind: "mixed", title: "Command Center queue", size: size(n.cc, "requests"),
      lines: ["Every request in that same set, across 8 states and hundreds of policies.",
        "Shows a whole queue decided, then re-decided when one rule changes."] },
    { kind: "real", title: "Real appeals", size: size(n.persius, "rulings"),
      lines: ["Real California independent medical review decisions.",
        "Predict whether the reviewer overturned the insurer's denial."] },
    { kind: "synth", title: "Hard medical bills", size: n.hardLines ? `${fmt.int(n.hard)} bills · ${fmt.int(n.hardLines)} lines` : size(n.hard, "bills"),
      lines: ["Messy bills priced with real Medicare fee and coding tables.",
        "Each line is OK or dispute: duplicates, unit limits, banned code pairs, overpricing."] },
    { kind: "synth", title: "Document packets", size: size(n.ndiFields, "fields checked"),
      lines: ["Bills, explanations of benefits, denials and plans rendered as PDFs.",
        "Measures how exactly NDI reads every field off the page."] },
    { kind: "synth", title: "Simple bills", size: size(n.syn, "bills"),
      lines: ["Clean, easier bills from our first generator.",
        "A sanity check: every model scores about 99% here."] },
  ];
}

async function sizes() {
  const [s, cc, m] = await Promise.all([
    api.resultsSummary().catch(() => null), api.ccSummary().catch(() => null), metrics().catch(() => ({})),
  ]);
  const by = Object.fromEntries(((s?.ok && s.data.benchmarks) || []).map((b) => [b.id, b]));
  const hardLane = (by.hard?.lanes || []).find((l) => l.n != null);
  return {
    pa: by.pa_criteria_gym?.n, persius: by.persius?.n, hard: by.hard?.n, hardLines: hardLane?.n,
    syn: by.synthetic_v1?.n,
    cc: cc?.ok ? (cc.data.n_cases ?? cc.data.n ?? cc.data.drex?.n) : null,
    ndiFields: m.ndi_exact_match?.n,
  };
}

export default {
  async mount(el, _params, ctx) {
    const n = await sizes();
    el.append(h("div", { class: "cover cover--simple" },
      h("div", { class: "cover-eyebrow" }, h("span", { class: "wordmark-mark" }, "B"), h("span", {}, "BillProof")),
      h("h1", { class: "cover-title" }, "Insurance paperwork, checked with evidence."),
      h("p", { class: "cover-sub" }, "Reads the policy and the patient’s documents, decides each rule with Drex, and drafts the letter. A person signs."),
      h("div", { class: "chain" }, CHAIN.map((c, i) => [
        i ? h("div", { class: "chain-arrow" }, icon("chevron-right")) : null,
        h("div", { class: `chain-step${c.product === "Drex" ? " is-drex" : ""}` },
          h("div", { class: "chain-ic" }, icon(c.icon)),
          h("div", {}, h("div", { class: "chain-label" }, c.step),
            h("div", { class: "chain-product" }, c.product === "Fixed clauses" ? "BillProof rules" : h("span", {}, h("img", { src: "/static/img/nace-mark.svg", alt: "" }), c.product)))),
      ])),
      h("div", { class: "ds-hd" }, "The data we tested on"),
      h("div", { class: "ds-grid" }, datasets(n).map((d) => h("div", { class: "ds-card" },
        h("div", { class: "ds-top" },
          h("span", { class: "ds-title" }, d.title),
          h("span", { class: `ds-tag ${KIND[d.kind].cls}` }, KIND[d.kind].label)),
        d.size ? h("div", { class: "ds-size" }, d.size) : null,
        d.lines.map((t) => h("p", { class: "ds-line" }, t))))),
      h("div", { class: "cover-cta" },
        h("a", { class: "btn btn--primary btn--lg", href: `#/case/${ctx.DEMO_CASE}/find` }, "See one request", icon("arrow-right")),
        h("a", { class: "btn btn--lg", href: "#/results" }, icon("chart-column"), "Results")),
    ));
  },
};
