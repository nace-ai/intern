// Step 1 · Find policy: replays the recorded Nace browser run on the insurer's public site — the task, the
// start page, what it found (each quote checked word for word on the page by BillProof), the document it
// returned, and where this request's criteria sit in that document. Recorded data only; nothing is re-run.
import { h, icon, fmt, fill, productBadge, productIcon, sleep } from "../ui.js";
import { loadCase, caseError, docView, around, policyBoxes } from "./caseview.js";
import { api, get } from "../data.js";
import { screenHeader } from "../ui.js";

const DOC_FOR = { "PAD_Criteria.pdf": "pad_url", "PACriteria.pdf": "pacriteria_url" };
const ROW_FOR = { pad_url: "Physician Administered Drug PA Criteria", pacriteria_url: "Prior Authorization Criteria" };

export default {
  async mount(el, { slug }) {
    const [c, r] = await Promise.all([loadCase(slug), get("/api/pa-browser")]);
    if (c.error) return caseError(el, slug, c.error);
    const { d } = c;
    const run = r.ok ? r.data : null;
    const drug = shortDrug(d.drug);
    const meta = run?.cases?.[slug] || {};
    const srcPdf = meta.source_pdf || d.provenance?.source_pdf;
    const docKey = DOC_FOR[srcPdf];
    const covered = run && (meta.covered_by_run ?? !!docKey);
    const docUrl = covered ? (meta.doc_url || run.answer?.[docKey]) : null;
    const page = meta.criteria_pages?.[0];

    el.append(screenHeader({
      eyebrow: [h("span", { class: "mono t-small muted" }, slug), h("span", { class: "t-small ink-2" }, `${d.payer} · ${d.drug}`)],
      title: "Find the insurer's policy",
      badges: run ? [productBadge("browser", `${run.seconds?.toFixed(1)} s · ${fmt.usd(run.cost_usd)}`, { lg: true })] : null,
      right: h("a", { class: "btn", href: `#/case/${slug}/read` }, "Next: Read", icon("arrow-right")),
    }));
    if (!run) {
      el.append(h("div", { class: "empty" }, icon("hourglass"), h("div", { class: "empty-title" }, "No recorded browser run is served"), h("div", { class: "t-small mono" }, r.error || "")));
      return;
    }

    // ------------------------------------------------ left: the run
    const v = run.verification || {};
    const qv = Object.fromEntries((v.quotes || []).map((q) => [q.field, q]));
    const lv = Object.fromEntries((v.doc_links || []).map((q) => [q.field, q]));
    const host = (u) => { try { return new URL(u).host; } catch { return u; } };
    const path = (u) => { try { const x = new URL(u); return x.pathname; } catch { return ""; } };

    const steps = [
      { ic: "globe", title: "Open the insurer's public provider-documents page" },
      { ic: "scan-text", title: "Read the page heading", quote: qv.heading_quote },
      { ic: "search", title: "Find the criteria row", quote: qv.row_quote },
      { ic: "link", title: "Copy both document links", body: h("div", { class: "br-links" },
        ["pacriteria_url", "pad_url"].map((k) => h("div", { class: `br-link${k === docKey ? " is-this" : ""}` },
          h("span", { class: "br-link-name" }, ROW_FOR[k]), h("span", { class: "mono br-url" }, path(run.answer?.[k]).split("/").pop()),
          lv[k]?.serves_pdf ? tick("serves the PDF") : h("span", { class: "muted t-small" }, "not checked")))) },
    ];

    const timeline = h("ol", { class: "br-steps" }, steps.map((s, i) => h("li", { class: "br-step", style: { "--i": i } },
      h("span", { class: "br-step-ic" }, icon(s.ic)),
      h("div", { class: "br-step-body" },
        h("div", { class: "br-step-title" }, s.title),
        s.quote ? h("div", { class: "br-quote" }, h("q", {}, s.quote.quote), s.quote.verbatim ? tick("verified word for word on the page") : h("span", { class: "tag tag--warn" }, "not found on the page")) : null,
        s.body || null))));

    const clock = h("span", { class: "num" }, "0.0 s");
    const bar = h("div", { class: "br-progress-fill" });
    const runCard = h("div", { class: "card br-run" },
      h("div", { class: "br-run-hd" }, productIcon("browser", "pi--lg"),
        h("div", {}, h("div", { class: "t-label" }, "Task given to Nace Browser runs"),
          h("div", { class: "br-task" }, "Find Arkansas Medicaid's prior-auth criteria"),
          h("div", { class: "br-task-sub" }, covered ? `BillProof then opens the document that holds ${drug}'s criteria` : `This request is ${d.payer}: the run below did not cover it (see right)`)),
        h("span", { class: "spacer" }), h("span", { class: "tag br-rec" }, icon("history"), "Recorded run · public site")),
      h("div", { class: "br-frame" },
        h("div", { class: "br-chrome" }, h("i"), h("i"), h("i"),
          h("div", { class: "br-addr" }, icon("shield-check"), h("b", {}, host(run.start_url)), h("span", {}, path(run.start_url)))),
        h("div", { class: "br-progress" }, bar),
        timeline),
      h("div", { class: "br-stats" },
        stat(clock, "run time", `browser ${run.usage?.browser_seconds ?? "—"} s`),
        stat(fmt.usd(run.cost_usd), "cost", "one run"),
        stat(fmt.int(run.usage?.llm_input_tokens), "tokens read", `${fmt.int(run.usage?.llm_output_tokens)} written`),
        stat(`${(v.quotes || []).filter((q) => q.verbatim).length + (v.doc_links || []).filter((q) => q.serves_pdf).length}/${(v.quotes || []).length + (v.doc_links || []).length}`, "checks passed", "by BillProof, after the run")),
      h("div", { class: "footnote" }, `Recorded ${(run.recorded_at || "").slice(0, 16).replace("T", " ")} UTC · run ${run.run_id}. Nace returned no step log or screenshots for this run; the steps above are its answer fields, each checked by BillProof.`));

    // ------------------------------------------------ right: the document it found
    const policy = docView({ src: api.paPage(slug, "policy", d.policy_pages?.[0] || 1), boxes: policyBoxes(c, { decided: false }), cls: "doc--find",
      focus: around(c.criteria.map((k) => k.rect), { py: 0.09 }) });
    const docCard = h("div", { class: "card br-doc" },
      h("div", { class: "card-hd" }, icon("file-check"), h("span", { class: "card-title" }, covered ? "The document it found" : "This request's policy document"),
        h("span", { class: "spacer" }), h("span", { class: "tag" }, "Real, published")),
      h("div", { class: "br-doc-title" }, d.provenance?.policy_document_title || srcPdf || "Policy"),
      docUrl ? h("div", { class: "br-doc-url" }, h("span", { class: "mono" }, docUrl), lv[docKey]?.serves_pdf ? tick("serves the PDF") : null)
        : h("div", { class: "br-note" }, icon("info"), `The recorded run read the Arkansas Medicaid site. This ${d.payer} policy comes from the dataset's source PDF${srcPdf ? ` (${srcPdf})` : ""}.`),
      h("div", { class: "br-doc-where" }, `${drug} criteria${page ? ` on PDF page ${page}` : ""} · ${c.criteria.length} criteria, joined by ${d.connective || "AND"}`),
      policy.el,
      h("div", { class: "br-crits" }, c.criteria.map((k) => h("div", { class: "br-crit", "data-id": k.criterion },
        h("span", { class: "crit-tag" }, k.tag), h("q", {}, k.text),
        k.match_method === "exact" ? tick("word for word in the PDF") : tick("in the PDF, spacing normalised")))));

    el.append(h("div", { class: "br-grid" }, runCard, docCard));
    for (const row of docCard.querySelectorAll(".br-crit")) {
      const b = policy.boxEls[row.dataset.id];
      row.addEventListener("mouseenter", () => b?.classList.add("is-on"));
      row.addEventListener("mouseleave", () => b?.classList.remove("is-on"));
    }

    // ------------------------------------------------ replay: steps appear in order, clock runs to the recorded time
    let alive = true;
    const total = run.seconds || 0, items = [...timeline.children], T = 3200;
    const t0 = performance.now();
    const tickClock = () => {
      if (!alive) return;
      const f = Math.min(1, (performance.now() - t0) / T);
      clock.textContent = `${(total * f).toFixed(1)} s`;
      bar.style.width = `${f * 100}%`;
      if (f < 1) requestAnimationFrame(tickClock); else runCard.classList.add("is-done");
    };
    requestAnimationFrame(tickClock);
    (async () => {
      for (const li of items) { if (!alive) return; li.classList.add("is-on"); await sleep(T / items.length); }
      docCard.classList.add("is-on");
    })();
    return () => { alive = false; };
  },
};

function tick(label) {
  return h("span", { class: "vtick" }, icon("circle-check"), label);
}

function stat(v, label, sub) {
  return h("div", { class: "br-stat" }, h("div", { class: "br-stat-v num" }, v), h("div", { class: "br-stat-l" }, label), sub ? h("div", { class: "br-stat-s" }, sub) : null);
}

function shortDrug(s) {
  return String(s || "").split(/[ (—]/)[0] || s;
}
