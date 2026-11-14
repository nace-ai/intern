// Drex Search on Read: "Ask the documents". One question per criterion, Search's recorded answer (the record,
// its page, the quote) and the quote's NDI box on the patient-file page, zoomed into view. Recorded data only:
// answers come from evidence.json criteria[].link (via the recorded Search job); verbatim/page ticks are BillProof's checks.
import { h, icon, fmt, fill, productIcon, sleep, criterionPill } from "../ui.js";
import { around } from "./caseview.js";

export function searchPanel(slot, c, { policy, packet, focusLink }) {
  const { d } = c;
  const s = d.search || {};
  const doc = (s.matches || [])[0]?.document?.split("/").pop() || "packet.pdf";
  const allPacket = packet.focus;
  let alive = true, active = null, pinned = false;

  const input = h("span", { class: "sp-typed" });
  const cards = c.criteria.map((k) => {
    const L = k.link || {};
    const ok = L.verbatim && L.page_ok !== false;
    const ans = h("div", { class: "sp-ans" },
      L.record
        ? [h("div", { class: "sp-ans-hd" }, h("span", { class: "rec-tag" }, L.record), h("span", { class: "sp-page" }, icon("file-text"), `${doc} · page ${L.record_page || L.search_page || "—"}`), h("span", { class: "spacer" }), criterionPill(k.state)),
          h("q", { class: "sp-quote" }, L.quote || "(no quote returned)"),
          ok ? h("span", { class: "vtick" }, icon("circle-check"), `word for word on page ${L.record_page || L.search_page}`)
            : h("span", { class: "tag tag--warn" }, icon("triangle-alert"), L.verbatim === false ? "quote not found word for word" : "page not checked")]
        : h("div", { class: "muted" }, "No record in the file addresses this criterion."));
    const card = h("button", { class: "sp-card", type: "button", "data-id": k.criterion },
      h("div", { class: "sp-q" }, h("span", { class: `crit-tag t-${k.tone}` }, k.tag), h("span", {}, "Which record addresses “", h("b", {}, k.text || k.criterion), "”?")),
      ans);
    card.addEventListener("mouseenter", () => { pinned = true; show(k); });
    card.addEventListener("focus", () => { pinned = true; show(k); });
    card.addEventListener("click", () => { pinned = true; show(k); });
    return card;
  });

  fill(slot, h("div", { class: "sp card" },
    h("div", { class: "sp-hd" }, productIcon("search", "pi--lg"),
      h("div", {}, h("div", { class: "sp-title" }, "Ask the documents"),
        h("div", { class: "sp-sub" }, `Drex Search · ${s.search_seconds != null ? `${s.search_seconds} s` : "—"} · ${fmt.usd(s.cost_usd)} · ${(s.matches || []).length} answers`))),
    h("div", { class: "sp-ask" }, icon("search"), input, h("span", { class: "sp-caret" })),
    h("div", { class: "sp-cards" }, cards),
    h("div", { class: "footnote" }, "Recorded Drex Search job over the policy page and the patient file. BillProof re-checks every quote word for word against the PDF page it cites.")));

  function show(k) {
    active = k?.criterion || null;
    cards.forEach((el) => el.classList.toggle("is-on", el.dataset.id === active));
    for (const kk of c.criteria) focusLink(kk.criterion, false);
    Object.values(packet.boxEls).forEach((b) => b.classList.remove("ebox--quote"));
    if (!k) { packet.setFocus(allPacket); return; }
    focusLink(k.criterion, true);
    const rec = k.record && c.records.find((r) => r.record === k.record);
    if (rec?.rect) {
      packet.boxEls[k.record]?.classList.add("ebox--quote");
      packet.setFocus(around([rec.rect], { py: 0.14, px: 0.03, fullWidth: false }));
    }
  }
  slot.addEventListener("mouseleave", () => { pinned = false; show(null); });

  // On load: type one example question, then show Search's answer on the page.
  const first = c.criteria.find((k) => k.state === "met" && k.record) || c.criteria.find((k) => k.record) || c.criteria[0];
  (async () => {
    if (!first) return;
    await sleep(900);
    const q = `Which record addresses ${first.tag}: “${first.text || first.criterion}”?`;
    for (let i = 1; i <= q.length && alive; i += 2) { input.textContent = q.slice(0, i); await sleep(22); }
    if (!alive) return;
    input.textContent = q;
    slot.querySelector(".sp-ask")?.classList.add("is-sent");
    await sleep(500);
    if (!alive || pinned) return;
    show(first);
    cards.forEach((el) => el.classList.add("is-shown"));
  })();
  return () => { alive = false; };
}

// "NDI read N fields, 98.9% exact (measured)" from data/metrics/ndi_*.json; null when the files are absent.
export function ndiChip(m) {
  const ex = m?.ndi_exact_match, n = m?.ndi_fields_read;
  if (ex?.value == null || ex.simulated) return null;
  const docs = n?.n ? `${n.n} test documents` : `${ex.cases?.length || "test"} bill packets`;
  return h("div", { class: "ndi-chip", title: `run ${ex.source_run} · ${ex.matched}/${ex.n} cells exact` },
    productIcon("ndi", "pi--sm"),
    h("span", {}, "NDI read ", h("b", { class: "num" }, fmt.int(n?.value ?? ex.n)), " fields, ", h("b", { class: "num" }, fmt.pct(ex.value, 1)), " exact"),
    h("span", { class: "muted" }, `measured on ${docs}`));
}
