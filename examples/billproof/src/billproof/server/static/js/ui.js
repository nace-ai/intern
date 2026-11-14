// Shared UI helpers: DOM builder, icons, meaning/model/product badges, number formatting.
import { ICONS } from "../vendor/lucide/icons.js";

export function h(tag, attrs, ...children) {
  const el = tag.startsWith("svg:")
    ? document.createElementNS("http://www.w3.org/2000/svg", tag.slice(4))
    : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, kids) {
  for (const c of kids) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `ic ${cls}`.trim());
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name] || ICONS["circle-help"];
  return svg;
}

// ---------------------------------------------------------------- meaning
// green = approve / met, red = deny / not met, amber = missing / sent to a person, neutral = not applicable
const MEANING = {
  green: { icon: "circle-check" },
  red: { icon: "circle-x" },
  amber: { icon: "triangle-alert" },
  neutral: { icon: "circle-dashed" },
};

export const DECISION = {
  criteria_met: { tone: "green", label: "Approve", long: "Criteria met" },
  criteria_unmet: { tone: "red", label: "Deny", long: "Criteria not met" },
  incomplete: { tone: "amber", label: "Missing documents", long: "Needs documents" },
  not_applicable: { tone: "neutral", label: "Wrong policy", long: "Not applicable" },
};

export const CRITERION = {
  met: { tone: "green", label: "Met" },
  unmet: { tone: "red", label: "Not met" },
  missing: { tone: "amber", label: "Missing" },
  not_applicable: { tone: "neutral", label: "N/A" },
};

export function pill(tone, label, { lg = false, iconName } = {}) {
  return h("span", { class: `pill pill--${tone}${lg ? " pill--lg" : ""}` },
    icon(iconName || MEANING[tone]?.icon || "circle-dashed"), label);
}

export function decisionPill(decision, opts) {
  const d = DECISION[decision] || { tone: "neutral", label: decision || "Unknown" };
  return pill(d.tone, d.label, opts);
}

export function criterionPill(state, opts) {
  const c = CRITERION[state] || { tone: "neutral", label: state || "Unknown" };
  return pill(c.tone, c.label, opts);
}

// ---------------------------------------------------------------- models + products
export const MODELS = {
  gpt: { label: "GPT", cls: "gpt" },
  drex: { label: "Drex", cls: "drex" },
  hybrid: { label: "Drex first, then GPT", cls: "hybrid" },
  checked: { label: "GPT checked by Drex", cls: "hybrid" },
  other: { label: "Other model", cls: "other" },
  di: { label: "DeepInfra", cls: "di" },
};

export function model(key, label) {
  const m = MODELS[key] || { label: key, cls: "gpt" };
  return h("span", { class: `model model--${m.cls}` }, label || m.label);
}

// Each step is labelled with the Nace product doing it. BillProof is not a Nace product.
export const PRODUCTS = {
  drex: { name: "Drex", verb: "decisions", icon: "scale" },
  ndi: { name: "NDI", verb: "document reading", icon: "scan-text" },
  search: { name: "Drex Search", verb: "evidence search", icon: "file-search" },
  browser: { name: "Browser runs", verb: "web tasks", icon: "globe" },
};

export function nace(product, extra) {
  const p = PRODUCTS[product];
  return h("span", { class: `nace${product === "drex" ? " nace--drex" : ""}`, title: `Nace ${p.name}: ${p.verb}` },
    h("img", { src: "/static/img/nace-mark.svg", alt: "" }), h("b", {}, p.name), extra ? ` · ${extra}` : null);
}

export function pending(text = "Pending") {
  return h("span", { class: "pending" }, icon("hourglass"), text);
}

export function empty(title, sub, iconName = "hourglass") {
  return h("div", { class: "empty" }, icon(iconName), h("div", { class: "empty-title" }, title), sub ? h("div", { class: "t-small" }, sub) : null);
}

export function confBar(p, { gpt = false } = {}) {
  const fill = h("div", { class: "conf-fill" });
  const el = h("div", { class: `conf${gpt ? " conf--gpt" : ""}` },
    h("div", { class: "conf-track" }, fill), h("span", { class: "conf-val num" }, p == null ? "—" : fmt.pct(p, p > 0.999 ? 2 : 1)));
  requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = `${Math.max(0, Math.min(1, p || 0)) * 100}%`; }));
  return el;
}

// ---------------------------------------------------------------- formatting
export const fmt = {
  pct(x, d = 1) { return x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(d)}%`; },
  int(n) { return n == null ? "—" : Math.round(n).toLocaleString("en-US"); },
  usd(x) {
    if (x == null) return "—";
    if (x === 0) return "$0";
    if (Math.abs(x) < 0.1) return `$${x.toPrecision(2)}`;
    if (Math.abs(x) < 100) return `$${x.toFixed(2)}`;
    return `$${Math.round(x).toLocaleString("en-US")}`;
  },
  secs(ms) {
    if (ms == null) return "—";
    const s = ms / 1000;
    return s < 100 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
  },
  ratio(a, b) { return a && b ? `${(a / b) >= 10 ? Math.round(a / b) : (a / b).toFixed(1)}×` : null; },
};

// Criterion ids look like "c_18_years_age"; show them as words when no text is at hand.
export function humanize(id) {
  return String(id || "").replace(/^c_/, "").replace(/_/g, " ");
}

export function bbox(polys) {
  let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
  for (const poly of polys || []) for (const [x, y] of poly) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  return x1 > x0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
}

export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// Screen header: short title (≤12 words), one-line subtitle, product badges on the right.
export function screenHeader({ eyebrow, title, sub, badges, right }) {
  return h("div", { class: "screen-hd" },
    h("div", {},
      eyebrow ? h("div", { class: "eyebrow" }, eyebrow) : null,
      h("h1", { class: "t-title" }, title),
      sub ? h("p", { class: "screen-sub" }, sub) : null),
    h("div", { class: "spacer" }),
    right || null,
    badges?.length ? h("div", { class: "screen-hd-badges" }, badges) : null);
}

// replaceChildren that skips null/false (DOM replaceChildren would print "null").
export function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (k instanceof Node ? k : document.createTextNode(String(k)))));
  return el;
}

// ---------------------------------------------------------------- Nace product icons (one icon + colour each)
// Colours live in products.css (--p-drex, --p-ndi, --p-search, --p-browser); never green/red/amber (those mean met/unmet/missing).
export const PRODUCT_ROLE = {
  browser: "found the policy on the insurer's site",
  ndi: "read both documents, boxed each line",
  search: "linked each criterion to a record",
  drex: "decided each criterion, with confidence",
};

export function productIcon(key, cls = "") {
  return h("span", { class: `pi pi--${key} ${cls}`.trim(), title: `Nace ${PRODUCTS[key]?.name || key}` }, icon(PRODUCTS[key]?.icon || "circle-help"));
}

// Big, coloured product badge: icon tile + name (+ optional detail such as "7.1 s").
export function productBadge(key, extra, { lg = false } = {}) {
  const p = PRODUCTS[key];
  return h("span", { class: `pb pb--${key}${lg ? " pb--lg" : ""}`, title: `Nace ${p.name}: ${PRODUCT_ROLE[key] || p.verb}` },
    productIcon(key), h("b", {}, p.name), extra ? h("span", { class: "pb-extra" }, extra) : null);
}

// Event rules count Intern + partner tools too. Intern = Nace's agent harness; DeepInfra = partner LLM host (not Nace).
Object.assign(PRODUCTS, {
  intern: { name: "Intern", verb: "agent harness", icon: "bot" },
  deepinfra: { name: "DeepInfra", verb: "partner LLMs", icon: "zap", partner: true },
});
Object.assign(PRODUCT_ROLE, {
  intern: "agent harness; Drex watches each turn",
  deepinfra: "LLM for unsure cases + baselines",
});
const LEGEND = ["browser", "ndi", "search", "drex", "intern", "deepinfra"];

// Sidebar legend: "Built with Nace AI", each product and what it did; Intern/DeepInfra show "pending" until their data is served.
export function naceLegend() {
  const row = (k) => h("div", { class: `nl-row nl-row--${k}`, title: PRODUCT_ROLE[k] }, productIcon(k),
    h("div", { class: "nl-body" }, h("div", { class: "nl-name" }, PRODUCTS[k].name, h("span", { class: "nl-status", "data-k": k })),
      h("div", { class: "nl-what" }, PRODUCT_ROLE[k])));
  return h("div", { class: "nl" },
    h("div", { class: "nl-hd" }, h("img", { src: "/static/img/nace-mark.svg", alt: "" }), h("span", {}, "Built with ", h("b", {}, "Nace AI"))),
    LEGEND.filter((k) => !PRODUCTS[k].partner).map(row),
    h("div", { class: "nl-sep" }, "Partner"),
    LEGEND.filter((k) => PRODUCTS[k].partner).map(row));
}

async function legendStatus(el) {
  const j = (u) => fetch(u, { headers: { accept: "application/json" } }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const set = (k, text) => { const s = el.querySelector(`.nl-status[data-k="${k}"]`); if (s) s.replaceChildren(text ? h("span", { class: "nl-live" }, text) : pending("pending")); };
  const [intern, results] = await Promise.all([j("/api/intern/sessions"), j("/api/results/summary")]);
  const sessions = Array.isArray(intern) ? intern : intern?.sessions;
  set("intern", sessions?.length ? `${sessions.length} session${sessions.length > 1 ? "s" : ""}` : null);
  set("deepinfra", results && JSON.stringify(results).toLowerCase().includes("deepinfra") ? "in Results" : null);
}

// DeepInfra product badge, placed next to every Qwen / DeepInfra number. DeepInfra is the partner host, not Nace.
export function diBadge({ sm = true } = {}) {
  const pi = productIcon("deepinfra"); pi.title = "DeepInfra: partner LLM host";
  return h("span", { class: `pb pb--deepinfra${sm ? " pb--sm" : ""}`, title: "Served by DeepInfra (partner LLM host)" }, pi, h("b", {}, "DeepInfra"));
}

export function mountNaceLegend() {
  const slot = document.querySelector(".sidebar .powered, .sidebar .nl");
  if (!slot) return;
  const el = naceLegend();
  slot.replaceWith(el);
  legendStatus(el);
}
