// Cases: the recorded prior-auth requests (full chain: policy found, read, decided, letter drafted).
import { h, icon, decisionPill, screenHeader, nace, empty, fmt } from "../ui.js";
import { api } from "../data.js";

const BEAT = { approve: "Approve", unmet: "Deny", missing: "Missing documents", wrong_payer: "Wrong payer", or_branch: "One of several options" };

export default {
  async mount(el) {
    el.append(screenHeader({
      title: "Prior-authorization requests",
      sub: "Each request is read, decided criterion by criterion, and answered with a letter.",
      badges: [nace("ndi"), nace("search"), nace("drex")],
    }));
    const r = await api.paCases();
    if (!r.ok || !r.data.cases?.length) {
      el.append(empty("No recorded requests yet", "The full-chain recordings (/api/pa/cases) are pending."));
      return;
    }
    const table = h("table", { class: "table cases-table card" },
      h("thead", {}, h("tr", {}, h("th", {}, "Request"), h("th", {}, "Drug"), h("th", {}, "Payer"), h("th", {}, "Decision"), h("th", { class: "r" }, "Cost"), h("th", {}))),
      h("tbody", {}, r.data.cases.map((c) => h("tr", { class: "click-row", onclick: () => { location.hash = `#/case/${c.case}/read`; } },
        h("td", {}, h("div", { class: "mono t-small muted" }, c.case), h("div", { class: "case-title" }, c.title)),
        h("td", { class: "ink-2" }, c.drug),
        h("td", { class: "ink-2" }, c.payer || "—"),
        h("td", {}, c.recorded ? (c.decision ? decisionPill(c.decision) : "—") : h("span", { class: "tag" }, "Not recorded")),
        h("td", { class: "r num ink-2" }, c.total_cost_usd != null ? fmt.usd(c.total_cost_usd) : "—"),
        h("td", { class: "r" }, icon("chevron-right"))))));
    el.append(table);
    el.append(h("p", { class: "footnote", style: { marginTop: "1rem" } },
      "Policies are real published Medicaid criteria; patient files are synthetic (Hammer Labs criteria-gym). Cost covers every Nace and GPT call for the request."));
  },
};
