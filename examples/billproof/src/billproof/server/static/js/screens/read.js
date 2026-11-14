// Step 1 · Read: the insurer's real policy page with each criterion boxed, the patient file with each
// record boxed (NDI), and Drex Search's link from every criterion to the record that addresses it.
// Evidence studio: record boxes cycle met → not met → missing (× removes), criteria switch off, the
// rule flips AND/OR; the decision is recomputed server-side from Drex's recorded answers.
import { h, icon, nace, fmt, humanize, fill, decisionPill, criterionPill, pill, productBadge, productIcon } from "../ui.js";
import { loadCase, caseHeader, caseError, docView, around, policyBoxes, packetBoxes } from "./caseview.js";
import { api, metrics } from "../data.js";
import { searchPanel, ndiChip } from "./search.js";
import { wi, wireRecords, paintPolicy, studioBar, editor, ruleToggle } from "./decide.js";

export default {
  async mount(el, { slug }) {
    const c = await loadCase(slug);
    if (c.error) return caseError(el, slug, c.error);
    const { d } = c;
    wi.fromHash(c);
    el.append(caseHeader({
      c, step: "read",
      title: "Read the policy and the patient file",
      badges: [productBadge("ndi", d.timings?.ground_s != null ? `${d.timings.ground_s.toFixed(1)} s` : null, { lg: true }),
        productBadge("search", d.timings?.search_s != null ? `${d.timings.search_s.toFixed(1)} s` : null, { lg: true })],
    }));

    const policy = docView({
      src: api.paPage(slug, "policy", d.policy_pages?.[0] || 1), cls: "doc--read",
      boxes: policyBoxes(c, { decided: false }),
      focus: around(c.criteria.map((k) => k.rect), { py: 0.07 }),
    });
    const packet = docView({
      src: api.paPage(slug, "packet", d.packet_pages?.[0] || 1), cls: "doc--read",
      boxes: packetBoxes(c, { decided: false }),
      focus: around(c.records.map((r) => r.rect), { py: 0.06, px: 0.08 }),
    });

    const svg = h("svg:svg", { class: "links" });
    const stage = h("div", { class: "read-stage" },
      h("div", { class: "read-col" }, docTitle("landmark", "Insurer policy", `${d.payer} · ${d.provenance?.policy_document_title || "published criteria"}`, "Real"), policy.el),
      h("div", { class: "read-gutter" }),
      h("div", { class: "read-col" }, docTitle("stethoscope", "Patient file", "Sent with the request", "Synthetic"), packet.el),
      svg);

    const legend = h("div", { class: "read-legend" });
    const studio = h("div", { class: "read-studio" });
    const bar = h("div", { class: "wi-bar-slot" });
    const side = h("div", { class: "read-side" });
    el.append(h("div", { class: "read-wrap" }, h("div", { class: "read-main" }, stage, h("div", { class: "read-foot" }, legend, studio)), side), bar);
    let chip = null;
    metrics().then((m) => { chip = ndiChip(m); if (chip && !res?.changes?.length) studio.append(chip); });

    let res = null, prev = null, tones = {};
    let schedule = () => {};
    const edit = editor(c, () => res, () => rerun());
    const paintRecords = wireRecords(packet, c, edit);
    for (const k of c.criteria) {
      const box = policy.boxEls[k.criterion];
      if (!box) continue;
      box.classList.add("wi-box");
      box.title = "Click: switch this criterion off / on";
      box.addEventListener("click", () => edit("off", k.criterion));
    }
    async function rerun() {
      const next = await wi.run(slug, wi.get(slug));
      if (!next) return;
      prev = res; res = next;
      paintRecords(res);
      paintPolicy(policy, res);
      const edited = res.changes.length > 0;
      // Unedited: colour every box by its recorded state (met green, not met red, missing amber)
      if (!edited) for (const k of c.criteria) {
        for (const b of [policy.boxEls[k.criterion], k.record && packet.boxEls[k.record]]) {
          if (!b) continue;
          b.classList.remove("ebox--ink", "ebox--green", "ebox--red", "ebox--amber", "ebox--neutral", "ebox--removed", "ebox--off", "ebox--deciding");
          b.classList.add(`ebox--${k.tone}`);
        }
      }
      tones = Object.fromEntries((edited ? res.criteria.map((w) => [w.criterion, w.on ? ({ met: "green", unmet: "red", missing: "amber" }[w.state] || "neutral") : "neutral"])
        : c.criteria.map((k) => [k.criterion, k.tone])));
      schedule();
      fill(legend, h("div", { class: "read-rule" }, h("span", { class: "t-label" }, "Policy rule"), ruleToggle(c, res, edit)),
        c.criteria.map((k) => {
          const w = res.criteria.find((x) => x.criterion === k.criterion) || {};
          return h("div", {
            class: `read-crit${w.on ? "" : " is-off"}`, "data-id": k.criterion,
            onmouseenter: () => focusLink(k.criterion, true), onmouseleave: () => focusLink(k.criterion, false),
          }, h("button", { class: `wi-switch${w.on ? " is-on" : ""}`, title: w.on ? "Switch this criterion off" : "Switch it back on", onclick: () => edit("off", k.criterion) }, h("i")),
          h("span", { class: "crit-tag" }, k.tag), h("span", { class: "read-crit-text" }, k.text || humanize(k.criterion)),
          icon("arrow-right"), k.record ? h("span", { class: "rec-tag" }, k.record) : h("span", { class: "muted t-small" }, "no record"),
          edited ? (w.on ? criterionPill(w.state) : pill("neutral", "Off", { iconName: "circle-dashed" })) : criterionPill(k.state));
        }));
      const linked = c.criteria.filter((k) => k.record).length;
      fill(studio, edited
        ? [h("div", { class: "t-label" }, "Decision · what-if"), decisionPill(res.decision, { lg: true }), h("div", { class: "decide-why" }, res.why)]
        : h("div", { class: "read-stats" },
          stat(`${c.criteria.length}`, "criteria found on the policy page", "ndi"),
          stat(`${c.records.filter((r) => r.isRecord).length}`, "records found in the patient file", "ndi"),
          stat(`${linked}/${c.criteria.length}`, "criteria linked to a record", "search")), chip);
      fill(bar, studioBar(c, res, prev, edit));
    }
    await rerun();

    // connectors between the two documents
    const lines = {};
    const draw = () => {
      const box = stage.getBoundingClientRect();
      svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
      const pv = policy.el.getBoundingClientRect(), kv = packet.el.getBoundingClientRect();
      svg.replaceChildren();
      for (const k of c.criteria) {
        const a = policy.boxEls[k.criterion], b = packet.boxEls[k.record];
        if (!a || !b) continue;
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        const clampY = (y, v) => Math.max(v.top + 8, Math.min(v.bottom - 8, y));
        const x1 = Math.min(ra.right, pv.right) - box.left, y1 = clampY(ra.top + ra.height / 2, pv) - box.top;
        const x2 = Math.max(rb.left, kv.left) - box.left, y2 = clampY(rb.top + rb.height / 2, kv) - box.top;
        const dx = Math.max(60, (x2 - x1) * 0.45);
        const t = tones[k.criterion] || "neutral", dpath = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
        const p = h("svg:path", { d: dpath, class: `link t-${t}`, "data-id": k.criterion });
        const e1 = h("svg:circle", { cx: x1, cy: y1, r: 4.5, class: `link-end t-${t}` });
        const e2 = h("svg:circle", { cx: x2, cy: y2, r: 4.5, class: `link-end t-${t}` });
        // a small dot travels policy → record, so the pairing reads as a flow
        const dot = h("svg:circle", { r: 3.5, class: `link-dot t-${t}` }, h("svg:animateMotion", { dur: "2.6s", repeatCount: "indefinite", path: dpath, begin: `${-c.criteria.indexOf(k) * 0.9}s` }));
        svg.append(p, e1, e2, dot);
        lines[k.criterion] = p;
      }
      svg.querySelectorAll(".link").forEach((p, i) => {
        const L = p.getTotalLength();
        p.style.strokeDasharray = `${L}`;
        p.style.strokeDashoffset = drawn ? "0" : `${L}`;
        if (!drawn) setTimeout(() => { p.style.strokeDashoffset = "0"; }, 500 + i * 350);
      });
      setTimeout(() => { drawn = true; }, 600 + c.criteria.length * 350);
    };
    let drawn = false;
    let raf = 0;
    schedule = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); };
    policy.onLayout = schedule;
    packet.onLayout = schedule;
    window.addEventListener("resize", schedule);

    function focusLink(id, on) {
      stage.classList.toggle("is-focusing", on);
      lines[id]?.classList.toggle("is-on", on);
      policy.boxEls[id]?.classList.toggle("is-on", on);
      const k = c.criteria.find((x) => x.criterion === id);
      if (k?.record) packet.boxEls[k.record]?.classList.toggle("is-on", on);
    }
    for (const k of c.criteria) {
      policy.boxEls[k.criterion]?.addEventListener("mouseenter", () => focusLink(k.criterion, true));
      policy.boxEls[k.criterion]?.addEventListener("mouseleave", () => focusLink(k.criterion, false));
    }
    const stopSearch = searchPanel(side, c, { policy, packet, focusLink });
    return () => { stopSearch(); window.removeEventListener("resize", schedule); };
  },
};

function docTitle(ic, title, sub, tag) {
  return h("div", { class: "doc-title" }, icon(ic), h("span", { class: "doc-title-main" }, title), h("span", { class: "doc-title-sub" }, sub), h("span", { class: "spacer" }), h("span", { class: "tag" }, tag));
}

function stat(v, label, product) {
  return h("div", { class: "read-stat" }, h("div", { class: "read-stat-v num" }, productIcon(product), v), h("div", { class: "kpi-label" }, label));
}

