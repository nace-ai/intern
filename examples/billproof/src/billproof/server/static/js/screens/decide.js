// Step 2 · Decide: one card per criterion (green / red / amber), Drex's confidence, the record that decides
// the case pulsing in the patient file, and GPT's answer checked by Drex.
// Evidence studio: every record box, criterion and the AND/OR rule can be edited; the server re-decides
// from Drex's recorded answers with the benchmark's decision code (POST /api/pa/<case>/whatif).
import { h, icon, nace, fmt, humanize, confBar, criterionPill, decisionPill, model, fill, pill, DECISION } from "../ui.js";
import { loadCase, caseHeader, caseError, docView, around, packetBoxes } from "./caseview.js";
import { api, post, state } from "../data.js";
import { snapshot } from "../snapshot.js";

// ================================================================ shared what-if studio (Read / Decide / Act)
const NEXT = { met: "not_met", not_met: "missing", missing: "met", removed: "met" };
const REC_LABEL = { met: "Met", not_met: "Not met", missing: "Missing", removed: "Removed" };
export const REC_TONE = { met: "green", not_met: "red", missing: "amber", removed: "removed" };
export const CRIT_TONE = { met: "green", unmet: "red", missing: "amber", off: "off" };
const TONES = ["ink", "green", "red", "amber", "neutral", "removed", "off"];

const key = (slug) => `bp.whatif.${slug}`;
export const wi = {
  get(slug) {
    try { return JSON.parse(sessionStorage.getItem(key(slug)) || "{}"); } catch { return {}; }
  },
  set(slug, e) {
    try { sessionStorage.setItem(key(slug), JSON.stringify(e)); } catch { /* storage blocked */ }
  },
  active(e) { return !!(Object.keys(e.records || {}).length || e.connective || (e.off || []).length || e.payer); },
  // ?r=R2:missing,R1:removed&rule=OR&off=C1&payer=flip  (presets for a demo link / screenshot)
  fromHash(c) {
    const q = new URLSearchParams(location.hash.split("?")[1] || "");
    if (![...q.keys()].some((k) => ["r", "rule", "off", "payer", "reset"].includes(k))) return;
    const e = {};
    if (q.get("r")) e.records = Object.fromEntries(q.get("r").split(",").map((p) => p.split(":")).filter((p) => p[1]));
    if (q.get("rule")) e.connective = q.get("rule").toUpperCase();
    if (q.get("off")) e.off = q.get("off").split(",").map((t) => c.criteria.find((k) => k.tag === t.toUpperCase())?.criterion).filter(Boolean);
    if (q.get("payer")) e.payer = "flip";
    wi.set(c.slug, e);
  },
  async run(slug, e) {
    const r = await post(`/api/pa/${encodeURIComponent(slug)}/whatif`, e);
    return r.ok ? r.data : null;
  },
  cycle(slug, rid, current) {
    const e = wi.get(slug);
    e.records = { ...(e.records || {}), [rid]: NEXT[current] || "met" };
    wi.set(slug, e);
  },
  remove(slug, rid, current) {
    const e = wi.get(slug);
    e.records = { ...(e.records || {}), [rid]: current === "removed" ? "met" : "removed" };
    wi.set(slug, e);
  },
  toggleOff(slug, fact) {
    const e = wi.get(slug);
    const off = new Set(e.off || []);
    off.has(fact) ? off.delete(fact) : off.add(fact);
    e.off = [...off];
    wi.set(slug, e);
  },
  toggleRule(slug, recorded, current) {
    const e = wi.get(slug);
    const next = current === "AND" ? "OR" : "AND";
    if (next === recorded) delete e.connective; else e.connective = next;
    wi.set(slug, e);
  },
  togglePayer(slug) {
    const e = wi.get(slug);
    if (e.payer) delete e.payer; else e.payer = "flip";
    wi.set(slug, e);
  },
  reset(slug) { wi.set(slug, {}); },
};

function setTone(el, tone) {
  if (!el) return;
  for (const t of TONES) el.classList.remove(`ebox--${t}`);
  el.classList.add(`ebox--${tone}`);
}

// Record boxes: click cycles met → not met → missing; × removes the document. Payer field flips the payer.
export function wireRecords(view, c, onEdit) {
  const chips = {};
  for (const r of c.records) {
    const box = view.boxEls[r.record];
    if (!box) continue;
    box.classList.add("wi-box");
    if (r.isRecord) {
      const chip = h("span", { class: "wi-chip" });
      const x = h("button", { class: "wi-x", title: "Remove this document", onclick: (ev) => { ev.stopPropagation(); onEdit("remove", r.record); } }, icon("x"));
      box.append(h("span", { class: "wi-ctl" }, chip, x));
      box.title = "Click: met → not met → missing";
      box.addEventListener("click", () => onEdit("cycle", r.record));
      chips[r.record] = chip;
    } else if (r.record === "request::payer") {
      box.title = "Click: switch the request's payer";
      box.addEventListener("click", () => onEdit("payer"));
    }
  }
  return (res) => {
    for (const r of res.records) {
      const box = view.boxEls[r.record];
      setTone(box, REC_TONE[r.state] || "ink");
      box?.classList.toggle("ebox--deciding", !!r.deciding);
      box?.classList.toggle("is-edited", !!res.edits.records[r.record]);
      if (chips[r.record]) fill(chips[r.record], REC_LABEL[r.state] || r.state);
    }
    const payer = view.boxEls["request::payer"];
    if (payer) {
      payer.classList.toggle("is-edited", res.edits.payer === "flip");
      payer.classList.toggle("wi-bad", res.scope === "wrong_payer");
      const tag = payer.querySelector(".wi-payer") || payer.appendChild(h("span", { class: "wi-payer" }));
      fill(tag, res.edits.payer === "flip" ? `what-if: ${res.request_payer}` : "");
    }
  };
}

export function paintPolicy(view, res) {
  for (const k of res.criteria) {
    const box = view.boxEls[k.criterion];
    setTone(box, CRIT_TONE[k.state] || "ink");
    box?.classList.toggle("is-edited", k.changed);
  }
}

const ruleText = (conn) => (conn === "OR" ? "Any one criterion is enough (OR)" : "All criteria must be met (AND)");

export function ruleToggle(c, res, onEdit) {
  return h("button", { class: `wi-rule${res.connective !== res.recorded_connective ? " is-edited" : ""}`, title: "Switch the policy rule between AND and OR", onclick: () => onEdit("rule") },
    icon("workflow"), h("span", {}, ruleText(res.connective)), h("span", { class: "wi-rule-sw" }, h("b", { class: res.connective === "AND" ? "is-on" : "" }, "AND"), h("b", { class: res.connective === "OR" ? "is-on" : "" }, "OR")));
}

// One sentence on what changed since the previous result, and why.
export function diffLine(prev, res, c) {
  if (!prev) return null;
  const tag = (f) => c.criteria.find((k) => k.criterion === f)?.tag || f;
  const parts = [];
  const pc = Object.fromEntries(prev.criteria.map((k) => [k.criterion, k.state]));
  const lab = { met: "met", unmet: "not met", missing: "missing", off: "off" };
  for (const k of res.criteria) if (pc[k.criterion] !== k.state) parts.push(`${tag(k.criterion)} ${lab[pc[k.criterion]]} → ${lab[k.state]}`);
  if (prev.connective !== res.connective) parts.push(`rule ${prev.connective} → ${res.connective}`);
  if (prev.request_payer !== res.request_payer) parts.push(`payer → ${res.request_payer}`);
  const dec = prev.decision !== res.decision
    ? h("span", { class: "wi-dec" }, decisionPill(prev.decision), icon("arrow-right"), decisionPill(res.decision))
    : h("span", { class: "wi-dec" }, "Decision unchanged: ", decisionPill(res.decision));
  const pd = (prev.deciding || []).join(", "), nd = (res.deciding || []).join(", ");
  return h("div", { class: "wi-diff" }, icon("refresh-cw"),
    h("span", { class: "wi-diff-what" }, parts.join(" · ") || "No criterion changed"), dec,
    h("span", { class: "wi-diff-why" }, res.why),
    pd !== nd ? h("span", { class: "deciding-tag" }, icon("target"), nd ? `Deciding: ${nd}` : "No single record decides") : null);
}

// Footer strip: what is edited, reset, and the Command Center hand-off.
export function studioBar(c, res, prev, onEdit) {
  const active = res.changes.length > 0;
  return h("div", { class: `wi-bar${active ? " is-active" : ""}` },
    h("span", { class: "wi-badge" }, icon("sliders-horizontal"), "What-if"),
    active
      ? h("div", { class: "wi-bar-body" }, diffLine(prev, res, c) || h("div", { class: "wi-diff" }, icon("refresh-cw"), h("span", { class: "wi-diff-what" }, res.changes.join(" · ")), h("span", { class: "wi-diff-why" }, res.why)))
      : h("div", { class: "wi-bar-body wi-hint" }, "Click a record box to cycle met → not met → missing, × to remove it; switch the rule or a criterion. BillProof re-decides from Drex's recorded answers with the benchmark's code."),
    active ? h("button", { class: "btn btn--sm", onclick: () => onEdit("reset") }, icon("history"), "Back to recorded") : null,
    ccLink(c, res));
}

function ccLink(c, res) {
  const ruleEdit = res.connective !== res.recorded_connective || (res.edits.off || []).length;
  const ccBody = { source: "pa_whatif", case: c.slug, policy_id: c.d.policy_id, connective: res.connective, off: res.edits.off || [] };
  // Demo mode: only when the snapshot holds a successful run of this exact rule change (no preset matches these policies)
  if (snapshot.demo && snapshot.find("POST", "/api/cc/what_if", ccBody)?.status !== 200) return null;
  const out = h("span", { class: "wi-cc-out t-small" });
  const btn = h("button", {
    class: "wi-cc", disabled: !ruleEdit, title: ruleEdit ? "" : "Change the rule (AND/OR or a criterion) first",
    onclick: async () => {
      btn.disabled = true;
      fill(out, "Asking Command Center…");
      const r = await post("/api/cc/what_if", ccBody);
      btn.disabled = false;
      const d = r.data || {};
      const n = d.n_total ?? d.total ?? d.n, ch = d.n_changed ?? d.changed ?? d.flipped;
      if (!r.ok) fill(out, h("span", { class: "muted" }, "Coming from Command Center"));
      else if (typeof n === "number" && typeof ch === "number") fill(out, h("a", { href: "#/queue" }, `${fmt.int(ch)} of ${fmt.int(n)} requests change decision`));
      else fill(out, h("a", { href: "#/queue" }, "Result in Command Center"));
    },
  }, icon("layers"), "Apply this rule change to all requests");
  return h("span", { class: "wi-cc-wrap" }, btn, out);
}

// Live: one Drex question on an edited record text. The recorded answer stays the default.
export function askDrexDialog(c, res, crit, onUse) {
  const k = c.criteria.find((x) => x.criterion === crit);
  const rec = c.records.find((r) => r.record === k?.record);
  // Demo mode: replay a recorded live answer for this criterion (the button is hidden when none exists)
  const recorded = snapshot.demo ? recordedAsks(c, crit)[0] : null;
  const ta = h("textarea", { class: "wi-ta", rows: 4, readonly: recorded ? "" : null }, recorded ? recorded.body.text : rec?.text || "");
  const out = h("div", { class: "wi-live-out" });
  const close = () => dlg.remove();
  const ask = h("button", {
    class: "btn btn--drex", onclick: async () => {
      ask.disabled = true;
      fill(out, h("span", { class: "muted" }, "Asking Drex…"));
      const r = await post(`/api/pa/${encodeURIComponent(c.slug)}/whatif/live`, { criterion: crit, text: ta.value });
      ask.disabled = false;
      const d = r.data || {};
      if (!r.ok || d.error) { fill(out, h("span", { class: "muted" }, `Live call failed: ${d.error || r.error}`)); return; }
      fill(out,
        h("div", { class: "row" }, h("span", { class: "wi-live-tag" }, recorded ? "RECORDED" : "LIVE"), model("drex", d.model), criterionPill({ met: "met", not_met: "unmet", missing: "missing" }[d.record_state])),
        h("div", { class: "crit-conf" }, h("span", { class: "kpi-label" }, "Drex confidence"), bar(d.confidence)),
        h("div", { class: "t-small muted" }, `${fmt.secs(d.latency_ms)} · ${fmt.usd(d.cost_usd)}`),
        d.record ? h("button", { class: "btn btn--sm", onclick: () => { close(); onUse(d.record, d.record_state); } }, icon("circle-check"), `Use this answer for ${d.record}`) : null);
    },
  }, icon(recorded ? "play" : "zap"), recorded ? "Replay Drex's answer · recorded" : "Ask Drex · LIVE");
  const dlg = h("div", { class: "wi-modal", onclick: (e) => { if (e.target === dlg) close(); } },
    h("div", { class: "card wi-dialog" },
      h("div", { class: "card-hd" }, icon("zap"), h("span", { class: "card-title" }, `${recorded ? "Ask Drex · recorded" : "Ask Drex live"} · ${k?.tag}`), h("span", { class: "spacer" }), h("button", { class: "wi-x wi-x--lg", onclick: close }, icon("x"))),
      h("div", { class: "crit-text" }, k?.text || humanize(crit)),
      h("div", { class: "t-small ink-2" }, recorded
        ? `Demo mode: this edited ${k?.record || "record"} text was really sent to Drex earlier; replaying that answer. Switch to Live to type your own.`
        : `Edit what ${k?.record || "the record"} says, then ask Drex whether it documents this criterion. One real API call; the recorded answer stays the default.`),
      ta, h("div", { class: "row" }, ask), out));
  document.body.append(dlg);
  ta.focus();
}

function recordedAsks(c, crit) {
  return snapshot.list("POST", `/api/pa/${encodeURIComponent(c.slug)}/whatif/live`).filter((e) => e.body?.criterion === crit && e.body?.text != null);
}

// Wires an edit kind to the store; returns a function used by all three screens.
export function editor(c, getRes, rerun) {
  return (kind, arg) => {
    const res = getRes();
    if (kind === "cycle") wi.cycle(c.slug, arg, res.records.find((r) => r.record === arg)?.state);
    else if (kind === "remove") wi.remove(c.slug, arg, res.records.find((r) => r.record === arg)?.state);
    else if (kind === "off") wi.toggleOff(c.slug, arg);
    else if (kind === "rule") wi.toggleRule(c.slug, res.recorded_connective, res.connective);
    else if (kind === "payer") wi.togglePayer(c.slug);
    else if (kind === "set") { const e = wi.get(c.slug); e.records = { ...(e.records || {}), [arg.record]: arg.state }; wi.set(c.slug, e); }
    else if (kind === "reset") wi.reset(c.slug);
    rerun();
  };
}

// ================================================================ Decide screen
export default {
  async mount(el, { slug }) {
    const c = await loadCase(slug);
    if (c.error) return caseError(el, slug, c.error);
    const { d } = c;
    wi.fromHash(c);
    el.append(caseHeader({
      c, step: "decide",
      title: "Decide every criterion, with its evidence",
      badges: [nace("drex", d.timings?.drex_ms != null ? `${fmt.secs(d.timings.drex_ms)} · ${fmt.usd(d.costs?.drex_decide)}` : null)],
    }));

    const packet = docView({
      src: api.paPage(slug, "packet", d.packet_pages?.[0] || 1), cls: "doc--decide",
      boxes: packetBoxes(c, { decided: true, deciding: d.deciding_record }),
      focus: around(c.records.filter((r) => r.rect).map((r) => r.rect), { py: 0.06, fullWidth: false, px: 0.07 }),
    });
    const note = h("span", { class: "deciding-note" });
    const right = h("div", { class: "decide-right" });
    const bar = h("div", { class: "wi-bar-slot" });
    el.append(h("div", { class: "decide-grid" },
      h("div", { class: "decide-left" },
        h("div", { class: "doc-title" }, icon("stethoscope"), h("span", { class: "doc-title-main" }, "Patient file"), note),
        packet.el),
      right), bar);

    let res = null, prev = null, first = true;
    const edit = editor(c, () => res, () => rerun());
    const paint = wireRecords(packet, c, edit);
    async function rerun() {
      const next = await wi.run(slug, wi.get(slug));
      if (!next) return;
      prev = res; res = next;
      paint(res);
      fill(note, res.deciding.length ? [h("i", { class: "pulse-dot" }), `${res.deciding.join(", ")} ${res.deciding.length > 1 ? "each decide" : "decides"} this case`] : "");
      const cards = c.criteria.map((k) => critCard(c, k, res, edit));
      fill(right,
        banner(c, res, edit),
        h("div", { class: "crit-cards" }, cards),
        h("div", { class: "decide-bottom" }, whatDecides(c, res), gptCheck(d)));
      if (first) cards.forEach((card, i) => { card.style.animationDelay = `${150 + i * 220}ms`; });
      else right.classList.add("no-reveal");
      cards.forEach((card, i) => {
        const box = packet.boxEls[c.criteria[i].record];
        card.addEventListener("mouseenter", () => { box?.classList.add("is-on"); packet.el.classList.add("is-dim"); });
        card.addEventListener("mouseleave", () => { box?.classList.remove("is-on"); packet.el.classList.remove("is-dim"); });
      });
      fill(bar, studioBar(c, res, prev, edit));
      if (prev && prev.decision !== res.decision) right.querySelector(".decide-banner")?.classList.add("wi-flash");
      first = false;
    }
    await rerun();
  },
};

// confBar animates on rAF; also set the width on a timer so a re-render never leaves it empty
export function bar(p) {
  const el = confBar(p);
  setTimeout(() => { const f = el.querySelector(".conf-fill"); if (f) f.style.width = `${Math.max(0, Math.min(1, p || 0)) * 100}%`; }, 40);
  return el;
}

const WHO = { drex: "Drex confidence", edit: "Set by you" };

function banner(c, res, edit) {
  const { d } = c;
  const qa = d.drex?.qa || {};
  const check = (keyName, label, ok) => {
    if (!qa[keyName]) return null;
    return h("span", { class: `scope-check ${ok ? "is-ok" : "is-bad"}` }, icon(ok ? "circle-check" : "circle-x"), label);
  };
  const recLive = state.mode === "demo" && snapshot.find("POST", `/api/pa/${encodeURIComponent(c.slug)}/live`);
  const liveBtn = state.mode === "live" ? h("button", { class: "btn btn--drex btn--sm", onclick: (e) => runLive(e.currentTarget, c) }, icon("zap"), "Re-decide live")
    : recLive ? h("button", { class: "btn btn--drex btn--sm", onclick: (e) => runLive(e.currentTarget, c, true) }, icon("play"), "Re-decide · recorded") : null;
  const dec = DECISION[res.decision] || {};
  const payerOk = res.scope !== "wrong_payer";
  return h("div", { class: `card decide-banner tone-${dec.tone || "neutral"}` },
    h("div", { class: "decide-banner-main" },
      h("div", { class: "t-label" }, res.changes.length ? "Decision · what-if" : "Decision"),
      decisionPill(res.decision, { lg: true }), h("span", { class: "decide-why" }, res.why)),
    h("div", { class: "decide-banner-side" },
      ruleToggle(c, res, edit),
      h("div", { class: "row", style: { gap: ".9rem" } },
        h("button", { class: "wi-plain", title: "Switch the request's payer", onclick: () => edit("payer") }, check("scope::payer_match", payerOk ? "Right payer" : `Payer: ${res.request_payer}`, payerOk)),
        check("scope::date_in_window", "Date in window", res.scope !== "wrong_date")),
      liveBtn, h("div", { class: "live-out t-small" })));
}

async function runLive(btn, c, recorded = false) {
  btn.disabled = true;
  const out = btn.parentElement.querySelector(".live-out");
  fill(out, "Asking Drex…");
  const r = await api.paLive(c.slug);
  btn.disabled = false;
  if (!r.ok || r.data?.error) { fill(out, h("span", { class: "muted" }, `Live call failed: ${r.data?.error || r.error}`)); return; }
  fill(out, h("span", {}, recorded ? "Recorded live run: " : "Live: ", decisionPill(r.data.decision), ` ${fmt.usd(r.data.cost_usd)}`));
}

function critCard(c, k, res, edit) {
  const w = res.criteria.find((x) => x.criterion === k.criterion) || {};
  const tone = CRIT_TONE[w.state] || k.tone;
  const isDeciding = k.record && res.deciding.includes(k.record);
  const quote = k.link?.quote ? k.link.quote.replace(/\\\(\s*\\geq\s*\\\)/g, "≥").replace(/\s+/g, " ") : null;
  const sw = h("button", { class: `wi-switch${w.on ? " is-on" : ""}`, title: w.on ? "Switch this criterion off" : "Switch it back on", onclick: () => edit("off", k.criterion) }, h("i"));
  return h("div", { class: `card crit-card tone-${tone}${isDeciding ? " is-deciding" : ""}${w.changed ? " is-edited" : ""}${w.on ? "" : " is-off"}` },
    h("div", { class: "crit-card-top" },
      h("span", { class: `crit-tag t-${tone === "off" ? "neutral" : tone}` }, k.tag),
      h("div", { class: "crit-text" }, k.text || humanize(k.criterion)),
      w.on ? criterionPill(w.state) : pill("neutral", "Off", { iconName: "circle-dashed" }), sw),
    h("div", { class: "crit-card-mid" },
      h("div", { class: "crit-conf" }, h("span", { class: "kpi-label" }, WHO[w.source] || "Drex confidence"),
        w.source === "edit" ? h("span", { class: "t-small ink-2" }, `${w.record} marked ${String(w.record_state).replace("_", " ")} (recorded: Drex ${fmt.pct(w.drex_confidence, 2)})`) : bar(w.drex_confidence ?? k.confidence)),
      k.record ? h("div", { class: "crit-evidence" },
        h("button", { class: "rec-tag wi-rec", title: "Cycle this record: met → not met → missing", onclick: () => edit("cycle", k.record) }, k.record),
        h("span", { class: "crit-quote" }, quote ? `“${quote}”` : "linked record"),
        isDeciding ? h("span", { class: "deciding-tag" }, icon("target"), "Deciding record") : null,
        !snapshot.demo || recordedAsks(c, k.criterion).length
          ? h("button", { class: "wi-ask", title: snapshot.demo ? "Replay a recorded live Drex answer for an edited record" : "Edit this record's text and ask Drex live", onclick: () => askDrexDialog(c, res, k.criterion, (record, st) => edit("set", { record, state: st })) }, icon(snapshot.demo ? "play" : "zap"), snapshot.demo ? "Ask Drex · recorded" : "Ask Drex live")
          : null)
        : h("div", { class: "crit-evidence muted" }, "No record addresses this criterion")));
}

function whatDecides(c, res) {
  const ab = res.probes || [];
  const card = h("div", { class: "card mini-card" },
    h("div", { class: "card-hd" }, icon("target"), h("span", { class: "card-title" }, "What decides this case"), h("span", { class: "spacer" }), nace("drex")));
  if (!ab.length) { card.append(h("div", { class: "muted t-small" }, "Every record is removed.")); return card; }
  const changed = res.deciding;
  card.append(h("div", { class: "card-sub", style: { marginBottom: ".55rem" } }, changed.length > 1
    ? `Remove any one of ${changed.join(", ")} and the decision changes.`
    : changed.length ? `Remove ${changed[0]} and the decision changes.` : "No single record flips this decision.",
  " Recomputed for this what-if from Drex's answers.",
  res.drex_check ? h("span", { class: `wi-check${res.drex_check.agrees ? "" : " is-bad"}` }, icon(res.drex_check.agrees ? "circle-check" : "circle-x"),
    ` Drex re-asked without ${res.drex_check.record}: `, decisionPill(res.drex_check.drex_decision)) : null));
  card.append(h("div", { class: "ablate" }, ab.map((a) => h("div", { class: `ablate-row${a.changed ? " is-changed" : ""}` },
    h("span", { class: "t-small ink-2" }, "Without"), h("span", { class: "rec-tag" }, a.dropped), icon("arrow-right"), decisionPill(a.decision),
    a.changed ? h("span", { class: "deciding-tag" }, "changes") : h("span", { class: "t-tiny muted" }, "same")))));
  return card;
}

function gptCheck(d) {
  const g = d.gpt_check || {};
  const caught = (g.overrides?.length || 0) + (g.flagged?.length || 0);
  const card = h("div", { class: `card mini-card gpt-card${caught ? " is-caught" : ""}` },
    h("div", { class: "card-hd" }, icon("shield-check"), h("span", { class: "card-title" }, "GPT checked by Drex"), h("span", { class: "spacer" }), h("span", { class: "tag" }, "recorded")));
  if (!g.gpt_decision) { card.append(h("div", { class: "muted t-small" }, "GPT was not run on this request.")); return card; }
  card.append(h("div", { class: "row", style: { marginBottom: ".5rem" } }, model("gpt", "GPT said"), decisionPill(g.gpt_decision)));
  if (caught) {
    card.append(h("div", { class: "caught" }, icon("triangle-alert"), h("b", {}, `Drex caught ${caught} GPT claim${caught > 1 ? "s" : ""} the file does not support.`)),
      h("div", { class: "row" }, h("span", { class: "t-small ink-2" }, "Checked decision"), decisionPill(g.checked_decision)));
    for (const o of [...(g.overrides || []), ...(g.flagged || [])].slice(0, 2)) {
      card.append(h("div", { class: "t-small ink-2" }, typeof o === "string" ? humanize(o) : `${humanize(o.fact || o.criterion || o.key || "")}: GPT ${o.gpt ?? o.gpt_answer ?? "?"}, Drex ${o.drex ?? o.drex_answer ?? "?"}`));
    }
  } else {
    card.append(h("div", { class: "agree" }, icon("circle-check"), "Drex checked each criterion: no disagreements."));
  }
  return card;
}
