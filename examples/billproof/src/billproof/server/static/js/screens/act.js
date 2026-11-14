// Step 3 · Act: the approval, missing-documents request, appeal or routing note, each sentence linked to
// the evidence box it rests on (policy criterion and/or patient-file record).
// The letter follows the evidence studio: edit a record or the rule and the letter type, clauses and
// citations are re-assembled server-side from the same clause library (generated words stay 0).
import { h, icon, humanize, pill, fill, productBadge } from "../ui.js";
import { loadCase, caseHeader, caseError, docView, around, policyBoxes, packetBoxes } from "./caseview.js";
import { api } from "../data.js";
import { wi, wireRecords, paintPolicy, studioBar, editor, CRIT_TONE } from "./decide.js";

const KIND = {
  approval: { label: "Approval", icon: "circle-check", tone: "green" },
  records_request: { label: "Missing-documents request", icon: "file-search", tone: "amber" },
  appeal: { label: "Appeal", icon: "scale", tone: "red" },
  routing: { label: "Routing note", icon: "arrow-up-right", tone: "neutral" },
};
const TO = { insurer: "Insurer", requesting_provider: "Requesting doctor", provider: "Requesting doctor" };

export default {
  async mount(el, { slug }) {
    const c = await loadCase(slug);
    if (c.error) return caseError(el, slug, c.error);
    const { d } = c;
    wi.fromHash(c);
    const header = h("div");
    el.append(header);

    const policy = docView({ src: api.paPage(slug, "policy", d.policy_pages?.[0] || 1), boxes: policyBoxes(c, { decided: true }), cls: "doc--act" });
    const packet = docView({ src: api.paPage(slug, "packet", d.packet_pages?.[0] || 1), boxes: packetBoxes(c, { decided: true }), cls: "doc--act" });
    const allPolicy = around(c.criteria.map((k) => k.rect), { py: 0.05 });
    const allPacket = around(c.records.filter((r) => r.isRecord).map((r) => r.rect), { py: 0.05, fullWidth: false, px: 0.05 });
    policy.setFocus(allPolicy);
    packet.setFocus(allPacket);

    const letterSlot = h("div", { class: "act-letter-slot" });
    const evidence = h("div", { class: "act-evidence" },
      h("div", { class: "doc-title" }, icon("landmark"), h("span", { class: "doc-title-main" }, "Policy"), h("span", { class: "doc-title-sub" }, d.payer)), policy.el,
      h("div", { class: "doc-title" }, icon("stethoscope"), h("span", { class: "doc-title-main" }, "Patient file"), h("span", { class: "doc-title-sub" }, "Synthetic · click a record to change it")), packet.el);
    const bar = h("div", { class: "wi-bar-slot" });
    el.append(h("div", { class: "act-grid" }, letterSlot, evidence), bar);

    let res = null, prev = null, stopWalk = () => {};
    const edit = editor(c, () => res, () => rerun());
    const paint = wireRecords(packet, c, edit);
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
      paint(res);
      paintPolicy(policy, res);
      const tones = Object.fromEntries(res.criteria.map((k) => [k.criterion, CRIT_TONE[k.state] === "off" ? "neutral" : CRIT_TONE[k.state]]));
      const cc = { ...c, criteria: c.criteria.map((k) => ({ ...k, tone: tones[k.criterion] || k.tone })) };
      const L = res.letter || d.letter || {};
      const kind = KIND[L.type] || { label: L.type || "Letter", icon: "mail", tone: "neutral" };
      fill(header, caseHeader({
        c, step: "act",
        title: kind.tone === "amber" ? "Ask for exactly what is missing" : "Draft the next letter, with evidence",
        badges: [productBadge("drex", "decision"), h("span", { class: "tag" }, icon("file-text"), "Fixed clause library · 0 model-written words")],
      }));
      stopWalk();
      stopWalk = renderLetter(letterSlot, cc, L, kind, policy, packet, allPolicy, allPacket, res.changes.length > 0, !!prev && prev.letter?.type !== L.type);
      fill(bar, studioBar(c, res, prev, edit));
    }
    await rerun();
    return () => stopWalk();
  },
};

function renderLetter(slot, c, L, kind, policy, packet, allPolicy, allPacket, isWhatIf, swapped) {
  const { d } = c;
    // sentences → evidence links
    const sentences = [];
    const body = h("div", { class: "letter-body" });
    for (const cl of L.clauses || []) {
      const para = h("p", { class: "letter-p" });
      for (const s of splitSentences(cl.text || "")) {
        const links = linksFor(s, c);
        const text = renderSentence(s, c);
        const inline = Array.isArray(text); // already carries its C-chips
        const span = h("span", { class: `sent${links.length ? " is-linked" : ""}` }, text,
          links.length && !inline ? h("span", { class: "sent-chips" }, links.map((k) => h("span", { class: `crit-tag t-${k.tone}` }, k.tag))) : null);
        if (links.length) {
          const idx = sentences.length;
          sentences.push({ span, links });
          span.addEventListener("mouseenter", () => { stop(); show(idx); });
          span.addEventListener("click", () => { stop(); show(idx); });
        }
        para.append(span, " ");
      }
      body.append(para);
    }

    const letter = h("div", { class: `letter paper${swapped ? " wi-swap" : ""}` },
      h("div", { class: "letter-head" },
        h("div", { class: "letter-kind" }, pill(kind.tone, kind.label, { iconName: kind.icon }), isWhatIf ? h("span", { class: "letter-wi" }, icon("sliders-horizontal"), "What-if draft") : null),
        h("div", { class: "letter-meta" },
          h("div", {}, h("span", { class: "t-label" }, "To"), h("span", {}, TO[L.recipient] || humanize(L.recipient))),
          h("div", {}, h("span", { class: "t-label" }, "Re"), h("span", {}, d.drug)))),
      h("div", { class: "letter-subject" }, L.subject || ""),
      body,
      h("div", { class: "letter-foot" }, icon("user-round"), "Human review required before sending · Not medical or legal advice"));
    fill(slot, letter);

    // walk through the linked sentences once, then rest on the overview
    let cur = -1, timer = null;
    function show(i) {
      sentences.forEach((s, k) => s.span.classList.toggle("is-on", k === i));
      for (const v of [policy, packet]) { v.el.classList.toggle("is-dim", i >= 0); Object.values(v.boxEls).forEach((b) => b.classList.remove("is-on")); }
      if (i < 0) { policy.setFocus(allPolicy); packet.setFocus(allPacket); return; }
      const ks = sentences[i].links;
      ks.forEach((k) => { policy.boxEls[k.criterion]?.classList.add("is-on"); if (k.record) packet.boxEls[k.record]?.classList.add("is-on"); });
      policy.setFocus(around(ks.map((k) => k.rect), { py: 0.05 }) || allPolicy);
      packet.setFocus(around(ks.map((k) => k.record && c.records.find((r) => r.record === k.record)?.rect), { py: 0.05, fullWidth: false, px: 0.05 }) || allPacket);
      cur = i;
    }
    function stop() { clearInterval(timer); timer = null; }
    if (sentences.length && !isWhatIf) {
      timer = setInterval(() => { if (cur + 1 >= sentences.length) { stop(); show(-1); } else show(cur + 1); }, 2600);
      setTimeout(() => show(0), 700);
    } else show(-1);
    letter.addEventListener("mouseleave", () => { if (!timer) show(-1); });
    return stop;
}

function splitSentences(text) {
  return text.split(/\n+/).flatMap((line) => line.split(/(?<=[.!?]["”]?)\s+(?=[A-Z])/)).map((s) => s.trim()).filter(Boolean);
}

function linksFor(s, c) {
  const hit = new Set();
  for (const k of c.criteria) {
    if (s.includes(k.criterion) || (k.text && s.includes(k.text.slice(0, 40)))) hit.add(k);
  }
  for (const m of s.matchAll(/\brecord (R\d+)\b/g)) {
    const k = c.criteria.find((x) => x.record === m[1]);
    if (k) hit.add(k);
  }
  return [...hit];
}

// Show internal criterion keys ("c_18_years_age:") as the C-number chip used on every screen.
function renderSentence(s, c) {
  const keys = c.criteria.filter((k) => s.includes(k.criterion));
  if (!keys.length) return s;
  const re = new RegExp(`(${keys.map((k) => k.criterion.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")}):?\\s*`, "g");
  const out = [];
  let last = 0;
  for (const m of s.matchAll(re)) {
    out.push(s.slice(last, m.index));
    const k = keys.find((x) => x.criterion === m[1]);
    out.push(h("span", { class: `crit-inline t-${k.tone}` }, k.tag));
    last = m.index + m[0].length;
  }
  out.push(s.slice(last));
  return out;
}

