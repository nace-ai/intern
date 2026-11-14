// App shell: sidebar navigation, hash router, Live | Demo mode, presenter keys and the auto-play Tour.
import "./snapshot.js"; // first: Demo mode answers every /api/ call from the frozen snapshot
import { h, icon, decisionPill, fill, productIcon, mountNaceLegend } from "./ui.js";
import { state, api } from "./data.js";
import { snapshot } from "./snapshot.js";
import cover from "./screens/cover.js";
import queue from "./screens/command.js";
import cases from "./screens/cases.js";
import results from "./screens/results.js";
import benchmarks from "./screens/benchmarks.js";
import read from "./screens/read.js";
import decide from "./screens/decide.js";
import act from "./screens/act.js";
import find from "./screens/browser.js";

export const DEMO_CASE = "PA-D3";

const SCREENS = { overview: cover, queue, cases, results, benchmarks, find, read, decide, act };

const NAV = [
  { id: "overview", label: "Overview", icon: "layout-dashboard", href: "#/overview" },
  { id: "queue", label: "Command Center", icon: "inbox", href: "#/queue" },
  { id: "cases", label: "Cases", icon: "folder-open", href: "#/cases" },
  { id: "results", label: "Results", icon: "chart-column", href: "#/results" },
  { id: "benchmarks", label: "Benchmarks", icon: "list-checks", href: "#/benchmarks" },
];

const CASE_STEPS = [
  { id: "find", label: "Find policy", icon: "globe", n: 1, products: ["browser"] },
  { id: "read", label: "Read", icon: "scan-text", n: 2, products: ["ndi", "search"] },
  { id: "decide", label: "Decide", icon: "scale", n: 3, products: ["drex"] },
  { id: "act", label: "Act", icon: "mail", n: 4, products: [] },
];

// Presenter story (Demo mode): arrow keys step through, T plays it as a timed Tour.
const STORY = [
  { hash: "#/overview", label: "Overview", ms: 10000 },
  { hash: `#/case/${DEMO_CASE}/find`, label: "Find policy", ms: 14000 },
  { hash: `#/case/${DEMO_CASE}/read`, label: "Read", ms: 18000 },
  { hash: `#/case/${DEMO_CASE}/decide`, label: "Decide", ms: 16000 },
  { hash: `#/case/${DEMO_CASE}/act`, label: "Act", ms: 14000 },
  { hash: "#/queue", label: "Command Center", ms: 18000, action: "start" },
  { hash: "#/queue", label: "Rule changed", ms: 12000, action: "rule-change" },
  { hash: "#/results", label: "Results", ms: 16000 },
];

const $ = (id) => document.getElementById(id);
const view = $("view");
let current = { key: null, screen: null, cleanup: null };
let storyIdx = -1;
let tour = { on: false, timer: null };

// ---------------------------------------------------------------- routing
function parse(hash) {
  const [path, query] = (hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const q = Object.fromEntries(new URLSearchParams(query || ""));
  if (parts[0] === "case" && parts[1]) return { screen: parts[2] || "read", params: { slug: parts[1].toUpperCase(), step: parts[2] || "read" } };
  // #/benchmarks[/<bench>[/cases | /case/<id>]]
  if (parts[0] === "benchmarks") return { screen: "benchmarks", params: { ...q, bench: parts[1], view: parts[2], id: parts.slice(3).join("/") } };
  const name = parts[0] || "overview";
  return { screen: SCREENS[name] ? name : "overview", params: q };
}

async function route() {
  const { screen, params } = parse(location.hash);
  const key = screen === "benchmarks" ? location.hash : `${screen}:${params.slug || ""}`;
  const step = STORY[storyIdx];
  if (!step || step.hash !== location.hash.split("?")[0]) storyIdx = STORY.findIndex((s) => s.hash === location.hash);
  renderNav(screen, params);
  renderPresenter();
  if (current.key === key && current.screen) {
    if (state.mode === "demo" && step?.action && current.screen.action) current.screen.action(step.action);
    return;
  }
  try { current.cleanup?.(); } catch { /* ignore */ }
  fill(view);
  view.scrollTop = 0;
  const mod = SCREENS[screen];
  const root = h("div", { class: `screen screen--${screen} fade-in` });
  view.append(root);
  current = { key, screen: mod, cleanup: null };
  try {
    current.cleanup = (await mod.mount(root, params, ctx)) || null;
  } catch (e) {
    console.error(e);
    root.append(h("div", { class: "empty" }, icon("triangle-alert"), h("div", { class: "empty-title" }, "This screen could not load"), h("div", { class: "t-small mono" }, String(e))));
  }
  const s = STORY[storyIdx];
  if (state.mode === "demo" && s?.action && s.hash === location.hash && mod.action) mod.action(s.action);
  // ?scroll=bottom shows the lower part of a long screen (screenshots, presenters)
  if (new URLSearchParams(location.search).get("scroll") === "bottom") setTimeout(() => { view.scrollTop = view.scrollHeight; }, 400);
}

function renderNav(screen, params) {
  const nav = $("nav");
  const inCase = ["find", "read", "decide", "act"].includes(screen);
  fill(nav, ...NAV.map((n) => h("a", {
    class: `nav-item${n.id === screen || (inCase && n.id === "cases") ? " is-active" : ""}`, href: n.href,
  }, icon(n.icon), h("span", {}, n.label))));
  const nc = $("nav-case");
  fill(nc);
  if (inCase) {
    nc.append(h("div", { class: "nav-case-hd" }, h("span", { class: "t-label" }, "Request"), h("span", { class: "mono nav-case-id" }, params.slug)));
    nc.append(...CASE_STEPS.map((s) => h("a", { class: `nav-step${s.id === screen ? " is-active" : ""}`, href: `#/case/${params.slug}/${s.id}` },
      h("span", { class: "nav-step-n" }, s.n), h("span", {}, s.label),
      s.products?.length ? h("span", { class: "nav-pis" }, s.products.map((k) => productIcon(k, "pi--sm"))) : null)));
  }
}

// ---------------------------------------------------------------- mode
function renderMode() {
  const m = state.mode;
  document.body.dataset.mode = m;
  $("demo-pill").hidden = m !== "demo";
  $("demo-pill").title = snapshot.size ? `${snapshot.size} recorded API responses; no live calls` : "Recorded snapshot not loaded";
  for (const b of $("mode-seg").querySelectorAll("button")) b.classList.toggle("is-on", b.dataset.mode === m);
  renderPresenter();
}

$("mode-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (!b || b.dataset.mode === state.mode) return;
  stopTour();
  state.mode = b.dataset.mode;
  renderMode();
  current.key = null; // remount so the screen picks its data source again
  route();
});

// ---------------------------------------------------------------- presenter + tour
function go(i) {
  storyIdx = Math.max(0, Math.min(STORY.length - 1, i));
  const s = STORY[storyIdx];
  if (location.hash === s.hash) route();
  else location.hash = s.hash;
}

function renderPresenter() {
  const p = $("presenter");
  if (state.mode !== "demo") { p.hidden = true; return; }
  p.hidden = false;
  const i = storyIdx;
  fill(p, 
    h("button", { class: "btn btn--ghost btn--icon btn--sm", title: "Previous (←)", onclick: () => { stopTour(); go(i <= 0 ? 0 : i - 1); } }, icon("chevron-left")),
    h("div", { class: "presenter-steps" }, STORY.map((s, k) => h("button", {
      class: `presenter-dot${k === i ? " is-on" : ""}${k < i ? " is-done" : ""}`, title: s.label, onclick: () => { stopTour(); go(k); },
    }))),
    h("span", { class: "presenter-label" }, i >= 0 ? `${i + 1}/${STORY.length} · ${STORY[i].label}` : "Story"),
    h("button", { class: "btn btn--ghost btn--icon btn--sm", title: "Next (→)", onclick: () => { stopTour(); go(i + 1); } }, icon("chevron-right")),
    h("button", { class: `btn btn--sm ${tour.on ? "btn--drex" : ""}`, title: "Auto-play Tour (T)", onclick: () => (tour.on ? stopTour() : startTour()) },
      icon(tour.on ? "pause" : "play"), tour.on ? "Pause" : "Tour"),
  );
}

function startTour() {
  if (state.mode !== "demo") return;
  tour.on = true;
  const tick = () => {
    if (!tour.on) return;
    tour.timer = setTimeout(() => {
      if (storyIdx >= STORY.length - 1) { stopTour(); return; }
      go(storyIdx + 1);
      tick();
    }, STORY[Math.max(0, storyIdx)].ms);
  };
  go(storyIdx < 0 || storyIdx >= STORY.length - 1 ? 0 : storyIdx);
  tick();
  renderPresenter();
}

function stopTour() {
  tour.on = false;
  clearTimeout(tour.timer);
  renderPresenter();
}

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Escape") { closeModal(); stopTour(); return; }
  if (state.mode !== "demo") return;
  if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); stopTour(); go(storyIdx + 1); }
  else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); stopTour(); go(storyIdx <= 0 ? 0 : storyIdx - 1); }
  else if (e.key === "t" || e.key === "T") { tour.on ? stopTour() : startTour(); }
  else if (e.key === " " && tour.on) { e.preventDefault(); stopTour(); }
});

// ---------------------------------------------------------------- search
const searchInput = $("search-input");
const searchResults = $("search-results");
$("search-ic").append(icon("search"));

async function runSearch() {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) { searchResults.hidden = true; return; }
  const r = await api.paCases();
  const list = (r.ok ? r.data.cases : []).filter((c) =>
    [c.case, c.title, c.drug, c.payer, c.beat].some((v) => String(v || "").toLowerCase().includes(q)));
  const items = list.map((c) => h("a", { class: "search-item", href: `#/case/${c.case}/read`, onclick: closeSearch },
    h("span", { class: "mono t-small muted" }, c.case), h("span", { class: "search-title" }, c.title), c.decision ? decisionPill(c.decision) : null));
  items.push(h("a", { class: "search-item", href: `#/queue?case=${encodeURIComponent(searchInput.value.trim())}`, onclick: closeSearch },
    icon("inbox"), h("span", { class: "search-title" }, `Find “${searchInput.value.trim()}” in the Command Center queue`)));
  fill(searchResults, ...items);
  searchResults.hidden = false;
}
function closeSearch() { searchResults.hidden = true; searchInput.value = ""; searchInput.blur(); }
searchInput.addEventListener("input", runSearch);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { searchResults.querySelector("a")?.click(); }
  if (e.key === "Escape") closeSearch();
});
document.addEventListener("click", (e) => { if (!e.target.closest("#search")) searchResults.hidden = true; });

// ---------------------------------------------------------------- upload
const modal = $("modal");
function closeModal() { modal.hidden = true; fill(modal); }
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

$("upload-btn").append(icon("upload"), "Upload");
$("upload-btn").addEventListener("click", async () => {
  const live = state.mode === "live";
  const r = await api.paCases();
  const recorded = (r.ok ? r.data.cases : []).filter((c) => c.recorded);
  const box = h("div", { class: "modal", role: "dialog", "aria-label": "Upload request" },
    h("div", { class: "modal-hd" }, h("div", {}, h("div", { class: "t-h2" }, "New prior-authorization request"),
      h("div", { class: "card-sub" }, "Insurer policy + patient file (PDF)")),
    h("span", { class: "spacer" }), h("button", { class: "btn btn--ghost btn--icon", onclick: closeModal, "aria-label": "Close" }, icon("x"))),
    h("div", { class: `dropzone${live ? "" : " is-disabled"}` }, icon("file-text", "ic-lg"),
      h("div", { class: "t-h3" }, "Drop the policy and the patient file here"),
      h("div", { class: "card-sub" }, live
        ? "Live upload of new prior-auth packets is not connected yet. Open a recorded request below."
        : "Demo mode uses recorded requests. Pick one below.")),
    h("div", { class: "t-label", style: { margin: "1.2rem 0 .5rem" } }, "Recorded requests"),
    h("div", { class: "modal-list" }, recorded.length ? recorded.map((c) => h("a", { class: "modal-item", href: `#/case/${c.case}/read`, onclick: closeModal },
      h("span", { class: "mono t-small muted" }, c.case), h("span", { class: "search-title" }, c.title), c.decision ? decisionPill(c.decision) : null))
      : h("div", { class: "muted" }, "No recorded requests yet.")),
  );
  fill(modal, box);
  modal.hidden = false;
});

// ---------------------------------------------------------------- boot
const ctx = { DEMO_CASE, go: (hash) => { location.hash = hash; } };
state.onMode(renderMode);
mountNaceLegend();
window.addEventListener("hashchange", route);
renderMode();
if (!location.hash) location.hash = "#/overview";
route();
if (new URLSearchParams(location.search).get("tour") === "1") startTour();
