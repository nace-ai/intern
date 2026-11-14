// Shared pieces for one prior-auth request (Read / Decide / Act): loading, criterion numbering,
// the document viewer with evidence boxes, and the case header.
import { h, icon, nace, screenHeader, decisionPill, bbox, fill, empty, CRITERION } from "../ui.js";
import { api } from "../data.js";

const TONE = { met: "green", unmet: "red", missing: "amber", not_applicable: "neutral" };

export async function loadCase(slug) {
  const r = await api.paCase(slug);
  if (!r.ok) return { error: r.error || "Not recorded" };
  const d = r.data;
  const criteria = (d.criteria || []).map((c, i) => ({
    ...c, n: i + 1, tag: `C${i + 1}`, tone: TONE[c.state] || "neutral", rect: bbox(c.polygons),
    recRect: bbox(c.link?.record_polygons), record: c.link?.record || null,
  }));
  const records = (d.records || []).map((r) => ({ ...r, rect: bbox(r.polygons), isRecord: r.kind === "record" }));
  return { d, criteria, records, slug };
}

export function caseHeader({ c, step, title, sub, badges }) {
  const steps = [["read", "Read"], ["decide", "Decide"], ["act", "Act"]];
  const i = steps.findIndex(([s]) => s === step);
  const next = steps[i + 1];
  return screenHeader({
    eyebrow: [h("span", { class: "mono t-small muted" }, c.slug), h("span", { class: "t-small ink-2" }, `${c.d.payer} · ${c.d.drug}`)],
    title, sub, badges,
    right: next ? h("a", { class: "btn", href: `#/case/${c.slug}/${next[0]}` }, `Next: ${next[1]}`, icon("arrow-right")) : h("a", { class: "btn", href: "#/queue" }, "Command Center", icon("arrow-right")),
  });
}

export function caseError(el, slug, err) {
  el.append(screenHeader({ title: `Request ${slug}` }), empty("This request has no recording yet", String(err || ""), "file-warning"));
}

// ---------------------------------------------------------------- document viewer
// Renders a page image with boxes; zooms so `focus` (normalised rect) fills the width and is centred vertically.
export function docView({ src, label, boxes = [], focus = null, aspect = 1651 / 1275, cls = "" }) {
  const page = h("div", { class: "doc-page" }, h("img", { src, alt: label || "", draggable: "false" }));
  const boxEls = {};
  for (const b of boxes) {
    if (!b.rect) continue;
    const el = h("div", {
      class: `ebox ebox--${b.tone || "ink"}${b.kind ? ` ebox--${b.kind}` : ""}`,
      style: { left: `${b.rect.x * 100}%`, top: `${b.rect.y * 100}%`, width: `${b.rect.w * 100}%`, height: `${b.rect.h * 100}%` },
      "data-id": b.id,
    }, b.tag ? h("span", { class: "ebox-tag" }, b.tag) : null);
    boxEls[b.id] = el;
    page.append(el);
  }
  const vp = h("div", { class: `doc ${cls}` }, page, label ? h("div", { class: "doc-label" }, label) : null);
  const view = { el: vp, page, boxEls, focus };
  const layout = () => {
    const W = vp.clientWidth, H = vp.clientHeight;
    if (!W || !H) return;
    const f = view.focus || { x: 0, y: 0, w: 1, h: 1 };
    // fit the focus width; if the focus is taller than the viewport, fit its height instead
    let pw = W / f.w;
    if (f.h * pw * aspect > H) pw = H / (f.h * aspect);
    pw = Math.min(pw, W / 0.42);
    const ph = pw * aspect;
    let x = -(f.x + f.w / 2) * pw + W / 2;
    let y = -(f.y + f.h / 2) * ph + H / 2;
    x = Math.min(0, Math.max(W - pw, x)); if (pw < W) x = (W - pw) / 2;
    y = Math.min(0, Math.max(H - ph, y)); if (ph < H) y = (H - ph) / 2;
    page.style.transition = view.animate ? "" : "none";
    page.style.width = `${pw}px`;
    page.style.height = `${ph}px`;
    page.style.transform = `translate(${x}px, ${y}px)`;
    if (view.animate) setTimeout(() => view.onLayout?.(), 520);
    else view.onLayout?.();
  };
  view.layout = layout;
  view.setFocus = (f) => { view.focus = f; view.animate = true; layout(); view.animate = false; };
  new ResizeObserver(layout).observe(vp);
  return view;
}

// Union of rects, padded, clamped to the page.
export function around(rects, { px = 0.04, py = 0.05, fullWidth = true } = {}) {
  const rs = rects.filter(Boolean);
  if (!rs.length) return null;
  let x0 = Math.min(...rs.map((r) => r.x)), y0 = Math.min(...rs.map((r) => r.y));
  let x1 = Math.max(...rs.map((r) => r.x + r.w)), y1 = Math.max(...rs.map((r) => r.y + r.h));
  if (fullWidth) { x0 = Math.min(x0, 0.06); x1 = Math.max(x1, 0.94); }
  x0 = Math.max(0, x0 - px); y0 = Math.max(0, y0 - py); x1 = Math.min(1, x1 + px); y1 = Math.min(1, y1 + py);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export function policyBoxes(c, { decided }) {
  return c.criteria.map((k) => ({ id: k.criterion, rect: k.rect, tag: k.tag, tone: decided ? k.tone : "ink", kind: "crit" }));
}

export function packetBoxes(c, { decided, deciding }) {
  const tone = {};
  if (decided) for (const k of c.criteria) if (k.record) tone[k.record] = k.tone;
  return c.records.filter((r) => r.rect).map((r) => ({
    id: r.record, rect: r.rect, tag: r.isRecord ? r.record : null,
    tone: r.isRecord ? tone[r.record] || "ink" : "field", kind: `${r.isRecord ? "rec" : "field"}${deciding && r.record === deciding ? " ebox--deciding" : ""}`,
  }));
}

export function stateLabel(state) { return CRITERION[state]?.label || state; }
export { decisionPill, nace, fill };
