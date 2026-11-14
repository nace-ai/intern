// Command Center: all 9,686 recorded Drex decisions as one tile wall, plus a live what-if.
// "Change the insurer's rule" re-decides the cached Drex facts under a hypothetical rule (POST /api/cc/what_if, SSE),
// and only the tiles that change flip. "Add a new criterion" asks Drex one new question live (SSE, capped).
// Every number on screen comes from /api/cc/* or data/metrics; projections are labelled.
import { h, icon, nace, pill, pending, screenHeader, fmt, fill } from "../ui.js";
import { api, get, stream, metrics } from "../data.js";
import { snapshot } from "../snapshot.js";

// label -> tone (g approve, r deny, a incomplete, n not applicable)
const LABEL = { approve: "g", deny: "r", incomplete: "a", "not applicable": "n", not_applicable: "n" };
const TONE_LABEL = { g: "Approve", r: "Deny", a: "Missing docs", n: "Not applicable" };
const TONE_PILL = { g: "green", r: "red", a: "amber", n: "neutral" };
const toneOf = (label) => LABEL[String(label ?? "").toLowerCase()] || null;
const plural = (n, one, many = `${one}s`) => (n === 1 ? one : many);
// Demo mode replays recorded live runs: tag them RECORDED, never LIVE
const liveTag = () => (snapshot.demo ? h("span", { class: "cx-rec" }, "RECORDED") : h("span", { class: "cx-live" }, "LIVE"));
const gptName = (m) => (/gpt-6\.1-sol/i.test(m || "") ? "GPT-6.1 Sol" : m || "GPT");

// POST + text/event-stream over fetch (EventSource is GET-only). Resolves when the stream ends.
async function ssePost(url, body, on, signal) {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json", accept: "text/event-stream" }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    let err = `HTTP ${r.status}`;
    try { err = (await r.json()).error || err; } catch { /* not JSON */ }
    throw new Error(err);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let name = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      let d = data;
      try { d = JSON.parse(data); } catch { /* raw */ }
      on[name]?.(d);
    }
  }
}

export default {
  async mount(el, params) {
    const [sumRes, presetRes, polRes, m] = await Promise.all([
      api.ccSummary(), get("/api/cc/presets"), get("/api/cc/policies"), metrics(),
    ]);
    const summary = sumRes.ok && sumRes.data.status !== "empty" ? sumRes.data : null;
    const cc = new CommandCenter(el, {
      summary,
      presets: presetRes.ok ? presetRes.data : null,
      policies: polRes.ok ? polRes.data.policies || [] : [],
      gptList: m.cc_gpt_projected_cost_per_1000_list?.value != null ? m.cc_gpt_projected_cost_per_1000_list : null,
    });
    this._cc = cc;
    if (summary) cc.replay();
    if (params.tab) cc.tab(params.tab);
    // Deep link for the tour/screenshots: #/queue?ask=<criterion>&policy=<list id> (answers are cached by text).
    if (params.ask) {
      if (params.policy) Object.assign(cc.scope, { kind: "policy", policy: params.policy });
      cc.newText = params.ask;
      cc.tab("new");
      cc.whenReady(() => cc.runNewCriterion(params.ask));
    }
    if (params.case) cc.showCase(params.case);
    if (params.action) this.action(params.action);
    return () => cc.destroy();
  },
  action(name) {
    const cc = this._cc;
    if (!cc) return;
    if (name === "start") cc.replay();
    if (name === "rule-change") cc.whenReady(() => cc.runPreset(cc.presets[0]));
    const p = /^preset-(\d)$/.exec(name);
    if (p) cc.whenReady(() => cc.runPreset(cc.presets[Number(p[1])]));
  },
};

// ---------------------------------------------------------------- the screen
class CommandCenter {
  constructor(el, { summary, presets, policies, gptList }) {
    this.el = el;
    this.summary = summary;
    this.presets = presets?.scenarios || [];
    this.policies = [...policies].sort((a, b) => (a.state || "").localeCompare(b.state || "") || (b.n_cases || 0) - (a.n_cases || 0));
    this.gptList = gptList;
    this.total = Number(summary?.n_cases || summary?.n_planned || summary?.drex?.n || 0);
    this.tiles = [];
    this.rows = [];          // per tile: { id, t, label, correct, split, stage, gold }
    this.byId = new Map();
    this.flipped = new Map(); // tile index -> flip record
    this.ready = [];
    this.busy = false;
    this.abort = null;
    this.timers = new Set();
    this.build();
  }

  // ---------------------------------------------------------- layout
  build() {
    const s = this.summary;
    this.replayBtn = h("button", { class: "btn", onclick: () => this.replay() }, icon("play"), "Replay");
    this.resetBtn = h("button", { class: "btn", disabled: true, onclick: () => this.reset() }, icon("history"), "Reset");
    this.el.classList.add("cx");
    this.el.append(screenHeader({
      eyebrow: [nace("drex", "decides each criterion"), h("span", { class: "tag" }, s ? `${fmt.int(this.total)} recorded decisions` : "Recorded run")],
      title: "Every decision, re-decided when the rule changes",
      right: h("div", { class: "row cx-hd-btns" }, this.resetBtn, this.replayBtn),
    }));

    const kpi = (label) => {
      const v = h("div", { class: "kpi-value num" }, "—");
      return { box: h("div", { class: "kpi" }, h("div", { class: "kpi-label" }, label), v), v };
    };
    this.k = { done: kpi("Decisions"), acc: kpi("Matches answer key"), cost: kpi("Spent on Drex"), rate: kpi("Decisions per minute") };
    this.countsEl = h("div", { class: "cx-counts" });

    this.wall = h("div", { class: "cx-wall" });
    this.wallWrap = h("div", { class: "cx-wall-wrap" }, this.wall);
    this.banner = h("div", { class: "cx-banner" });
    this.detail = h("div", { class: "cx-detail", hidden: true });
    this.wallWrap.append(this.detail);
    this.gpt = h("div", { class: "cx-gpt" });
    const legend = h("div", { class: "cx-legend" },
      ["g", "r", "a", "n"].map((t) => h("span", {}, h("i", { class: `cx-sw t-${t}` }), TONE_LABEL[t])),
      h("span", {}, h("i", { class: "cx-sw t-g is-flip" }), "Flipped by the rule change"));

    this.panel = h("div", { class: "card cx-panel" });
    this.el.append(h("div", { class: "cx-grid" },
      h("div", { class: "cx-main" },
        h("div", { class: "card cx-kpis" }, Object.values(this.k).map((k) => k.box), this.countsEl),
        h("div", { class: "card cx-wall-card" }, this.banner, this.wallWrap,
          h("div", { class: "cx-foot" }, legend, h("span", { class: "spacer" }), h("span", { class: "t-tiny muted" }, "Click a tile for its decision")),
          this.gpt)),
      this.panel));

    this.wall.addEventListener("click", (e) => {
      const t = e.target.closest(".cx-tile");
      if (t) this.showTile(Number(t.dataset.i), t);
    });
    this.makeTiles(this.total);
    this.ro = new ResizeObserver(() => this.layout());
    this.ro.observe(this.wallWrap);
    this.renderCounters();
    this.renderBanner(null);
    this.renderGpt(null);
    this.buildPanel();
    if (!s) {
      this.wallWrap.append(h("div", { class: "cx-overlay" }, pending(sumRes0(s))));
      this.replayBtn.disabled = true;
    }
  }

  makeTiles(n) {
    fill(this.wall);
    this.tiles = [];
    const frag = document.createDocumentFragment();
    for (let i = 0; i < n; i++) {
      const t = document.createElement("i");
      t.className = "cx-tile";
      t.dataset.i = i;
      frag.append(t);
      this.tiles.push(t);
    }
    this.wall.append(frag);
    this.layout();
  }

  layout() {
    const box = this.wallWrap.getBoundingClientRect();
    if (!box.width || !box.height || !this.tiles.length) return;
    const n = this.tiles.length;
    let s = 30, gap = 2;
    for (; s > 3; s -= 0.25) {
      gap = s >= 9 ? 2 : 1.5;
      const cols = Math.floor((box.width + gap) / (s + gap));
      if (Math.ceil(n / cols) * (s + gap) - gap <= box.height) break;
    }
    this.wall.style.setProperty("--tile", `${s}px`);
    this.wall.style.setProperty("--gap", `${gap}px`);
  }

  // ---------------------------------------------------------- recorded wall (full replay)
  replay() {
    if (!this.summary) return;
    this.closeStream?.();
    this.clearWhatIf(false);
    this.byId.clear();
    this.rows = [];
    this.cum = null;
    this.counts = { g: 0, r: 0, a: 0, n: 0 };
    for (const t of this.tiles) t.className = "cx-tile";
    this.replayBtn.disabled = true;
    this.loaded = false;
    // Recorded service time / 500 ≈ a couple of seconds for the whole wall.
    this.closeStream = stream("/api/cc/queue/stream?full=1&speed=500", {
      start: (d) => { if (d.n && d.n !== this.tiles.length) this.makeTiles(d.n); },
      decision: (d) => this.onDecision(d),
      finish: () => this.onReplayDone(),
      error: () => this.onReplayDone(),
      onerror: () => this.onReplayDone(),
    });
    this.tick = setInterval(() => this.renderCounters(), 120);
  }

  onDecision(d) {
    const i = d.i;
    const t = toneOf(d.label);
    if (!t || !(i >= 0 && i < this.tiles.length)) return;
    this.byId.set(String(d.case_id), i);
    this.rows[i] = { id: d.case_id, t, label: d.label, correct: d.correct, split: d.split, gold: d.gold_decision, decision: d.decision };
    this.tiles[i].className = `cx-tile t-${t}`;
    this.counts[t]++;
    this.cum = d.cum;
  }

  onReplayDone() {
    this.closeStream?.();
    this.closeStream = null;
    clearInterval(this.tick);
    this.replayBtn.disabled = false;
    this.renderCounters();
    if (!this.loaded) {
      this.loaded = true;
      const q = this.ready.splice(0);
      q.forEach((fn) => fn());
    }
  }

  whenReady(fn) { if (this.loaded) fn(); else this.ready.push(fn); }

  renderCounters() {
    const c = this.cum, s = this.summary;
    const k = this.k;
    fill(k.done.v, c ? fmt.int(c.done) : s ? "0" : pending(), this.total ? h("span", { class: "kpi-of" }, ` / ${fmt.int(this.total)}`) : null);
    fill(k.acc.v, c ? fmt.pct(c.accuracy, 1) : "—");
    fill(k.cost.v, c ? fmt.usd(c.cost_usd) : "—");
    fill(k.rate.v, s?.drex?.cases_per_minute != null ? fmt.int(s.drex.cases_per_minute) : "—");
    const counts = this.liveCounts();
    fill(this.countsEl, ["g", "r", "a", "n"].map((t) =>
      h("div", { class: `cx-count c-${t}` }, h("i", { class: `cx-sw t-${t}` }), h("span", { class: "num" }, fmt.int(counts[t])), TONE_LABEL[t])));
  }

  // counts on the wall right now (recorded + any hypothetical flips)
  liveCounts() {
    const out = { ...(this.counts || { g: 0, r: 0, a: 0, n: 0 }) };
    for (const [i, f] of this.flipped) {
      const a = this.rows[i]?.t, b = toneOf(f.to_label);
      if (a && b) { out[a]--; out[b]++; }
    }
    return out;
  }

  // ---------------------------------------------------------- what-if panel
  buildPanel() {
    const tabs = [
      ["presets", "Scenarios"],
      ["custom", "Edit the rule"],
      ["new", h("span", {}, "New criterion ", liveTag())],
    ].filter(([id]) => id !== "new" || !snapshot.demo || this.recordedNew().length);
    this.tabEls = {};
    this.tabBar = h("div", { class: "cx-tabs", role: "tablist" }, tabs.map(([id, label]) =>
      (this.tabEls[id] = h("button", { role: "tab", onclick: () => this.tab(id) }, label))));
    this.tabBody = h("div", { class: "cx-tab-body" });
    fill(this.panel,
      h("div", { class: "cx-panel-hd" },
        h("div", {}, h("div", { class: "card-title cx-panel-title" }, "Change the insurer’s rule"),
          h("div", { class: "cx-hypo" }, icon("sliders-horizontal"), "Hypothetical rule change · same Drex answers, new rule")),
        nace("drex")),
      this.tabBar, this.tabBody);
    this.scope = { kind: "state", state: Object.keys(this.summary?.by_state || {})[0] || "AR", policy: this.policies[0]?.id || null };
    this.edit = { connective: null, missing: false, shift: 0, clauses: {} };
    this.tab("presets");
  }

  tab(id) {
    this.curTab = id;
    for (const [k, b] of Object.entries(this.tabEls)) b.classList.toggle("is-on", k === id);
    if (id === "presets") this.renderPresets();
    if (id === "custom") this.renderCustom();
    if (id === "new") this.renderNew();
  }

  renderPresets() {
    if (!this.presets.length) { fill(this.tabBody, pending("Preset scenarios pending")); return; }
    fill(this.tabBody,
      h("p", { class: "cx-help" }, "Pick a rule change. BillProof re-decides every affected request from Drex’s recorded answers and flips only the tiles that change."),
      h("div", { class: "cx-presets" }, this.presets.map((p) => {
        const where = p.policy ? `${p.policy.state} · ${short(p.policy.drug || p.policy.title, 46)}` : p.apply_to?.state ? `All of ${p.apply_to.state}` : "";
        return h("button", { class: "cx-preset", disabled: this.busy, onclick: () => this.runPreset(p) },
          h("div", { class: "cx-preset-t" }, p.title),
          h("div", { class: "cx-preset-s" }, where, ` · ${fmt.int(p.n_affected)} ${plural(p.n_affected, "request")}`),
          icon("chevron-right", "cx-preset-go"));
      })));
  }

  scopeControls(onChange) {
    const states = Object.entries(this.summary?.by_state || {});
    const sc = this.scope;
    const kindSeg = seg([["state", "A whole state"], ["policy", "One policy"]], sc.kind, (v) => { sc.kind = v; onChange(); });
    let picker;
    if (sc.kind === "state") {
      picker = h("select", { class: "cx-select", onchange: (e) => { sc.state = e.target.value; onChange(); } },
        states.map(([st, n]) => h("option", { value: st, selected: st === sc.state }, `${st} · ${fmt.int(n)} requests`)));
    } else {
      picker = h("select", { class: "cx-select", onchange: (e) => { sc.policy = e.target.value; this.edit.clauses = {}; onChange(); } },
        this.policies.map((p) => h("option", { value: p.id, selected: p.id === sc.policy },
          `${p.state} · ${short(p.drug || p.title, 58)} · ${p.n_cases} · ${p.connective}`)));
    }
    return h("div", { class: "cx-field" }, h("div", { class: "cx-label" }, "Apply to"), kindSeg, picker);
  }

  applyTo() {
    const sc = this.scope;
    return sc.kind === "state" ? { state: sc.state } : { list_id: sc.policy };
  }

  scopeCount() {
    const sc = this.scope;
    if (sc.kind === "state") return this.summary?.by_state?.[sc.state] ?? null;
    return this.policies.find((p) => p.id === sc.policy)?.n_cases ?? null;
  }

  renderCustom() {
    const e = this.edit;
    const pol = this.scope.kind === "policy" ? this.policies.find((p) => p.id === this.scope.policy) : null;
    const re = () => this.renderCustom();
    const editBody = () => {
      const out = {};
      if (e.connective) out.connective = e.connective;
      if (e.missing) out.missing_as = "not_met";
      if (e.shift) out.window_shift_years = e.shift;
      const req = Object.entries(e.clauses).filter(([, v]) => v === "require").map(([k]) => k);
      const drop = Object.entries(e.clauses).filter(([, v]) => v === "drop").map(([k]) => k);
      if (req.length) out.require_criteria = req;
      if (drop.length) out.drop_criteria = drop;
      return out;
    };
    const body = editBody();
    const n = this.scopeCount();
    const clauses = pol?.clauses || [];
    fill(this.tabBody,
      this.scopeControls(re),
      pol ? h("div", { class: "cx-pol" },
        h("b", {}, short(pol.drug || pol.title, 80)),
        h("span", {}, ` · ${pol.payer || pol.state} · published rule: `, h("b", {}, pol.connective === "OR" ? "any one is enough (OR)" : "all required (AND)")),
        pol.window ? h("span", {}, ` · window ${pol.window.from} → ${pol.window.through}`) : null) : null,
      h("div", { class: "cx-field" }, h("div", { class: "cx-label" }, "Combine criteria"),
        seg([[null, "As published"], ["AND", "All required (AND)"], ["OR", "Any one (OR)"]], e.connective, (v) => { e.connective = v; re(); })),
      h("div", { class: "cx-field" }, h("div", { class: "cx-label" }, "Date window"),
        seg([[0, "As published"], [-1, "1 year earlier"], [1, "1 year later"]], e.shift, (v) => { e.shift = v; re(); })),
      h("label", { class: "cx-check" },
        h("input", { type: "checkbox", checked: e.missing, onchange: (ev) => { e.missing = ev.target.checked; re(); } }),
        h("span", {}, h("b", {}, "Missing counts as not met"), h("span", { class: "muted" }, " · a criterion with no record is a deny, not a request for documents"))),
      pol && clauses.length ? h("div", { class: "cx-field" }, h("div", { class: "cx-label" }, "Require or drop a criterion"),
        h("div", { class: "cx-clauses" }, clauses.map((c) => h("div", { class: "cx-clause" },
          h("span", { class: "cx-clause-t", title: c.text }, c.text),
          seg([[undefined, "Keep"], ["require", "Require"], ["drop", "Drop"]], e.clauses[c.fact], (v) => { if (v) e.clauses[c.fact] = v; else delete e.clauses[c.fact]; re(); }, true))))) : null,
      !pol ? h("div", { class: "t-tiny muted" }, "Pick “One policy” to require or drop a single criterion.") : null,
      h("button", {
        class: "btn btn--drex btn--lg cx-apply", disabled: this.busy || !Object.keys(body).length,
        onclick: () => this.runWhatIf({ edit: body, apply_to: this.applyTo() }, "Custom rule"),
      }, icon("refresh-cw"), n != null ? `Re-decide ${fmt.int(n)} ${plural(n, "request")}` : "Re-decide"));
  }

  // Demo mode: the new-criterion runs the snapshot recorded (real Drex calls, replayed)
  recordedNew() {
    return snapshot.list("POST", "/api/cc/what_if/new_criterion").filter((e) => e.kind === "sse" && e.body?.text);
  }

  renderNewRecorded() {
    const recs = this.recordedNew();
    this.newProg = h("div", { class: "cx-prog" }, this.progView?.());
    fill(this.tabBody,
      h("p", { class: "cx-help" }, "Demo mode replays a new criterion Drex was really asked earlier: same answers, same cost, recorded timing. Switch to Live to type your own."),
      recs.map((e) => h("div", { class: "cx-field" },
        h("div", { class: "cx-label" }, "Recorded criterion", e.body.apply_to?.state ? ` · ${e.body.apply_to.state}` : ""),
        h("div", { class: "cx-text cx-text--ro" }, e.body.text),
        h("button", { class: "btn btn--drex btn--lg cx-apply", disabled: this.busy, onclick: () => this.runNewCriterion(e.body.text, e.body.apply_to) },
          icon("play"), "Replay Drex's answers · recorded"))),
      this.newProg);
  }

  renderNew() {
    if (snapshot.demo) { this.renderNewRecorded(); return; }
    const re = () => this.renderNew();
    const n = this.scopeCount();
    this.newText = this.newText ?? "";
    const ta = h("textarea", {
      class: "cx-text", rows: 3, maxlength: 800,
      placeholder: "e.g. Patient has tried and failed at least one preferred alternative medication",
      oninput: (ev) => { this.newText = ev.target.value; go.disabled = this.busy || !this.newText.trim(); },
    });
    ta.value = this.newText;
    const go = h("button", {
      class: "btn btn--drex btn--lg cx-apply", disabled: this.busy || !this.newText.trim(),
      onclick: () => this.runNewCriterion(this.newText.trim()),
    }, icon("zap"), "Ask Drex live");
    this.newProg = h("div", { class: "cx-prog" }, this.progView?.());
    fill(this.tabBody,
      h("p", { class: "cx-help" }, "Type a criterion the insurer might add. Drex reads each patient file for that one question, live; the code then requires it on top of the published rule."),
      this.scopeControls(re),
      h("div", { class: "cx-field" }, h("div", { class: "cx-label" }, "New criterion"), ta),
      h("div", { class: "t-tiny muted" }, n != null ? `${fmt.int(n)} requests in scope · ` : "", "real Drex calls, at most 500 cases and $0.05 per run; repeat questions reuse saved answers"),
      go, this.newProg);
  }

  // ---------------------------------------------------------- run a what-if
  async runPreset(p) {
    if (!p) return;
    await this.runWhatIf({ preset: p.id }, p.title);
  }

  async runWhatIf(body, title) {
    if (this.busy || !this.loaded) return;
    this.begin();
    this.renderBanner({ state: "running", title });
    const flips = [];
    let start = null, summary = null;
    try {
      await ssePost("/api/cc/what_if?stream=1", body, {
        start: (d) => { start = d; },
        flip: (d) => flips.push(d),
        summary: (d) => { summary = d; },
      }, this.abort.signal);
      if (!summary) throw new Error("no summary from the server");
      this.applyFlips(flips);
      this.renderBanner({ state: "done", title: summary.title || title, s: summary, start });
      this.renderGpt(summary);
    } catch (e) {
      if (e.name !== "AbortError") this.renderBanner({ state: "error", title, error: String(e.message || e) });
    }
    this.end();
  }

  async runNewCriterion(text, applyTo = null) {
    if (this.busy || !this.loaded || !text) return;
    this.begin();
    const t0 = performance.now();
    const flips = [];
    let start = null, summary = null, prog = null, err = null;
    const showProg = () => {
      this.progView = view;
      if (this.newProg) fill(this.newProg, view());
    };
    const view = () => {
      const asked = prog?.done ?? 0, of = prog?.n ?? (start ? start.n_selected - start.n_already_cached : 0);
      return [
        liveTag(), nace("drex"),
        !start ? pending("Starting…")
          : of ? h("span", { class: "num" }, `Drex asked ${fmt.int(asked)} / ${fmt.int(of)} cases live · ${fmt.usd(prog?.cost_usd ?? 0)}`)
          : h("span", { class: "num" }, "No new Drex calls · $0"),
        start?.n_already_cached ? h("span", { class: "muted" }, ` · ${fmt.int(start.n_already_cached)} reused`) : null];
    };
    this.renderBanner({ state: "live", title: `New criterion: “${short(text, 90)}”` });
    showProg();
    try {
      await ssePost("/api/cc/what_if/new_criterion", { text, apply_to: applyTo || this.applyTo() }, {
        start: (d) => { start = d; showProg(); },
        progress: (d) => { prog = d; showProg(); this.renderBanner({ state: "live", title: `New criterion: “${short(text, 90)}”`, prog: d }); },
        error: (d) => { err = d.error || "error"; },
        flip: (d) => flips.push(d),
        summary: (d) => { summary = d; },
      }, this.abort.signal);
      const wallMs = performance.now() - t0;
      if (!summary) throw new Error(err || "no summary from the server");
      this.applyFlips(flips);
      this.renderBanner({ state: "done", live: true, title: `New criterion: “${short(text, 90)}”`, s: summary, start, wallMs, partial: err });
      this.renderGpt(summary);
      showProg();
    } catch (e) {
      if (e.name !== "AbortError") this.renderBanner({ state: "error", title: "New criterion", error: String(e.message || e) });
    }
    this.end();
  }

  begin() {
    this.busy = true;
    this.abort = new AbortController();
    this.clearWhatIf(false);
    this.detail.hidden = true;
    this.tab(this.curTab);
  }

  end() {
    this.busy = false;
    this.abort = null;
    this.resetBtn.disabled = !this.flipped.size;
    this.tab(this.curTab);
    this.renderCounters();
  }

  // Flip only the changed tiles, in wall order, staggered over ≤1.6 s; each flip is a 200 ms turn.
  applyFlips(flips) {
    const items = [];
    for (const f of flips) {
      const i = this.byId.get(String(f.case_id));
      if (i == null) continue;
      this.flipped.set(i, f);
      items.push(i);
    }
    items.sort((a, b) => a - b);
    this.wall.classList.toggle("has-whatif", items.length > 0);
    const span = Math.min(1600, 300 + items.length * 1.5);
    const t0 = performance.now();
    let k = 0;
    const step = () => {
      const upto = items.length * Math.min(1, (performance.now() - t0) / span);
      for (; k < items.length && k <= upto; k++) {
        const i = items[k];
        const f = this.flipped.get(i);
        if (f) this.tiles[i].className = `cx-tile t-${toneOf(f.to_label)} is-flip`;
      }
      if (k < items.length) this.flipTimer = setTimeout(step, 16);
    };
    step();
  }

  // Put flipped tiles back to their recorded colour (animated on Reset, instant before a new run).
  clearWhatIf(animate) {
    clearTimeout(this.flipTimer);
    for (const i of this.flipped.keys()) {
      const r = this.rows[i];
      if (r) this.tiles[i].className = `cx-tile t-${r.t}${animate ? " is-back" : ""}`;
    }
    this.flipped.clear();
    this.wall.classList.remove("has-whatif");
    this.resetBtn.disabled = true;
  }

  reset() {
    this.abort?.abort();
    this.clearWhatIf(true);
    this.detail.hidden = true;
    this.renderBanner(null);
    this.renderGpt(null);
    this.renderCounters();
  }

  // ---------------------------------------------------------- banner + GPT strip
  renderBanner(b) {
    const el = this.banner;
    const hypo = h("span", { class: "tag tag--warn" }, icon("sliders-horizontal"), "Hypothetical rule change");
    if (!b) {
      fill(el, h("div", { class: "cx-ban-idle" },
        h("div", { class: "cx-ban-n num" }, this.total ? fmt.int(this.total) : "—"),
        h("div", {}, h("div", { class: "cx-ban-t" }, "decisions on the wall, each one a real Drex answer"),
          h("div", { class: "cx-ban-s" }, "Change the insurer’s rule on the right to see which ones flip."))));
      return;
    }
    if (b.state === "running" || b.state === "live") {
      fill(el, h("div", { class: "cx-ban-idle" },
        h("div", { class: "cx-ban-n num cx-pulse" }, b.prog ? fmt.int(b.prog.done) : "…"),
        h("div", {}, h("div", { class: "row cx-ban-tags" }, hypo, b.state === "live" ? liveTag() : null),
          h("div", { class: "cx-ban-t" }, b.title),
          h("div", { class: "cx-ban-s" }, b.state === "live"
            ? (b.prog ? `Drex asked ${fmt.int(b.prog.done)} of ${fmt.int(b.prog.n)} cases live · ${fmt.usd(b.prog.cost_usd)}` : "Asking Drex…")
            : "Re-deciding…"))));
      return;
    }
    if (b.state === "error") {
      fill(el, h("div", { class: "cx-ban-idle" }, icon("triangle-alert", "ic-lg"),
        h("div", {}, h("div", { class: "cx-ban-t" }, b.title), h("div", { class: "cx-ban-s" }, b.error))));
      return;
    }
    const s = b.s;
    const counts = Object.entries(s.flip_counts || {}).sort((x, y) => y[1] - x[1]);
    const reused = b.start?.n_already_cached || 0;
    const asked = b.live ? (s.n_selected ?? 0) - reused : 0;
    const how = b.live ? `re-decided in ${fmtMs(b.wallMs)} end to end` : `re-decided in ${fmtMs(s.elapsed_ms)}`;
    const liveLine = b.live ? h("div", { class: "cx-ban-live" }, nace("drex"),
      `Drex asked ${fmt.int(asked)} ${plural(asked, "case")} live · ${fmt.usd(s.cost_usd)}`,
      reused ? h("span", { class: "muted" }, ` · ${fmt.int(reused)} saved ${plural(reused, "answer")} reused`) : null) : null;
    fill(el, h("div", { class: "cx-ban-col" },
      h("div", { class: "row cx-ban-tags" }, hypo, b.live ? liveTag() : nace("drex"),
        s.partial || b.partial ? h("span", { class: "tag" }, "partial") : null,
        h("div", { class: "cx-ban-t" }, b.title)),
      h("div", { class: "cx-ban-done" },
      h("div", { class: "cx-ban-left" },
        h("div", { class: "cx-ban-big" },
          h("span", { class: "num cx-ban-n" }, fmt.int(s.n_decided)),
          h("span", { class: "cx-ban-unit" }, `${plural(s.n_decided, "decision")} ${how}`)),
        h("div", { class: "cx-ban-s" },
          h("b", { class: "num" }, fmt.int(s.n_flips)), ` ${plural(s.n_flips, "decision")} changed · ${fmt.int(s.n_unchanged)} stayed the same`,
          s.truncated ? ` · first ${fmt.int(s.n_selected)} of ${fmt.int(s.n_in_scope)} in scope` : "",
          s.unmatched?.length ? ` · not found: ${s.unmatched.join(", ")}` : ""),
        liveLine),
      h("div", { class: "cx-ban-flips" }, counts.length > 3 ? h("div", { class: "cx-flip-more t-tiny muted" }, (() => { const n = counts.slice(3).reduce((m, [, v]) => m + v, 0); return `+ ${fmt.int(n)} more ${plural(n, "decision")} in ${counts.length - 3} smaller ${plural(counts.length - 3, "change")}`; })()) : null,
        counts.length ? counts.slice(0, 3).map(([k, v]) => {
        const [a, z] = k.split("_to_").map((x) => toneOf(x.replace(/_/g, " ")));
        return h("div", { class: "cx-flip-row" },
          h("span", { class: "num cx-flip-n" }, fmt.int(v)),
          a ? pill(TONE_PILL[a], TONE_LABEL[a]) : k, icon("arrow-right"), z ? pill(TONE_PILL[z], TONE_LABEL[z]) : null);
      }) : h("div", { class: "cx-ban-s" }, "No decision changed.")))));
  }

  // "Re-reading these with GPT": measured per-case GPT mean × the cases this run re-decided. Labelled projected.
  renderGpt(s) {
    const g = this.summary?.gpt;
    const el = this.gpt;
    if (!g?.n_measured || g.mean_latency_ms == null) { fill(el, h("span", { class: "model model--gpt" }, "GPT"), pending("GPT sample pending")); return; }
    const n = s ? s.n_decided : this.total;
    const hours = (n * g.mean_latency_ms) / 3.6e6;
    const L = this.gptList;
    const perCase = L ? L.value / 1000 : g.mean_cost_usd;
    const usd = n * perCase;
    const drex = s ? (s.cost_usd != null ? `Drex: ${fmt.usd(s.cost_usd)} live` : `Drex: ${fmtMs(s.elapsed_ms)}, $0 (recorded answers reused)`) : null;
    fill(el,
      h("span", { class: "model model--gpt" }, gptName(g.model)),
      h("span", { class: "cx-gpt-t" }, `Re-reading ${s ? "these" : "all"} ${fmt.int(n)} with ${gptName(g.model)}: `,
        h("b", { class: "num" }, `≈ ${hours >= 1 ? `${hours.toFixed(1)} hours` : `${Math.max(1, Math.round(hours * 60))} min`}`), ", ",
        h("b", { class: "num" }, `≈ ${fmt.usd(usd)}`)),
      h("span", { class: "tag tag--warn" }, "projected"),
      drex ? h("span", { class: "cx-gpt-drex" }, drex) : null,
      h("span", { class: "spacer" }),
      h("span", { class: "footnote cx-gpt-note" }, `From ${fmt.int(g.n_measured)} measured GPT cases: ${fmtMs(g.mean_latency_ms)} each (includes router wait)${L
        ? `, list price $${L.list_price_usd_per_mtok?.input}/$${L.list_price_usd_per_mtok?.output} per Mtok` : `, ${fmt.usd(g.mean_cost_usd)} each`}. GPT was not run on these.`));
  }

  // ---------------------------------------------------------- tile detail
  showTile(i, tileEl) {
    const r = this.rows[i];
    if (!r) return;
    const f = this.flipped.get(i);
    const box = this.detail;
    const close = h("button", { class: "btn btn--ghost btn--icon btn--sm", "aria-label": "Close", onclick: () => { box.hidden = true; } }, icon("x"));
    const now = f ? toneOf(f.to_label) : r.t;
    fill(box,
      h("div", { class: "row cx-det-hd" }, f ? h("span", { class: "tag tag--warn" }, "Flipped · hypothetical") : h("span", { class: "tag" }, "Recorded decision"), h("span", { class: "spacer" }), close),
      f ? h("div", { class: "cx-det-flip" }, pill(TONE_PILL[r.t], TONE_LABEL[r.t]), icon("arrow-right"), pill(TONE_PILL[now], TONE_LABEL[now])) : h("div", { class: "cx-det-flip" }, pill(TONE_PILL[r.t], TONE_LABEL[r.t], { lg: true })),
      f ? h("div", { class: "cx-det-sec" }, h("div", { class: "cx-label" }, "Why it flipped"), h("div", { class: "cx-det-why" }, f.reason)) : null,
      f ? h("div", { class: "cx-det-sec" }, h("div", { class: "cx-label" }, "Policy"), h("div", {}, `${f.state} · ${short(f.drug || f.title, 90)}`), h("div", { class: "mono t-tiny muted" }, f.policy_id)) : null,
      !f && r.gold ? h("div", { class: "cx-det-sec t-small" }, r.correct ? icon("circle-check") : icon("circle-x"), r.correct ? " Matches the answer key" : ` Answer key says ${r.gold}`) : null,
      h("div", { class: "cx-det-sec" }, h("div", { class: "cx-label" }, "Request"), h("div", { class: "mono t-tiny cx-det-id" }, r.id)));
    box.hidden = false;
    const wrap = this.wallWrap.getBoundingClientRect(), t = tileEl.getBoundingClientRect();
    const w = box.offsetWidth || 380;
    const x = t.left - wrap.left;
    const left = Math.min(Math.max(0, x > wrap.width / 2 ? x - w - 14 : x + t.width + 14), wrap.width - w);
    const hgt = box.offsetHeight;
    const top = Math.min(Math.max(0, t.top - wrap.top - hgt / 2), Math.max(0, wrap.height - hgt));
    Object.assign(box.style, { left: `${left}px`, top: `${top}px` });
  }

  showCase(id) {
    const go = () => {
      if (this.busy) { setTimeout(go, 200); return; }
      const i = this.byId.get(String(id));
      if (i != null) this.showTile(i, this.tiles[i]);
    };
    this.whenReady(() => setTimeout(go, 1800));
  }

  destroy() {
    this.closeStream?.();
    this.abort?.abort();
    clearInterval(this.tick);
    clearTimeout(this.flipTimer);
    this.ro?.disconnect();
  }
}

// ---------------------------------------------------------------- small helpers
function seg(options, value, onPick, small = false) {
  return h("div", { class: `cx-seg${small ? " cx-seg--sm" : ""}`, role: "group" }, options.map(([v, label]) =>
    h("button", { class: v === value ? "is-on" : null, onclick: () => onPick(v) }, label)));
}

function short(s, n) {
  s = String(s || "").replace(/\s+/g, " ").trim();
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function fmtMs(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
  return fmt.secs(ms);
}

function sumRes0(s) {
  return s ? "Loading…" : "The full Command Center run (/api/cc/summary) has not landed yet.";
}
