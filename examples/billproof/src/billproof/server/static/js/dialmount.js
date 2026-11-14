// Mounts UI-3's trust dial (static/js/dial.js: mountDial(el, bench), routedAt(bench, t)) when that file exists.
// Until it lands, or if its endpoint is not live, the slot shows "pending". This file builds no slider of its own.
import { h, pending, fill } from "./ui.js";

let mod = null;
export async function dialModule() {
  if (mod) return mod;
  try { mod = await import("./dial.js"); } catch { mod = null; }
  if (mod && !document.querySelector('link[data-dial-css]')) {
    const link = Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/static/css/dial.css" });
    link.dataset.dialCss = "1";
    document.head.append(link);
  }
  return mod;
}

export function dialSlot(bench, opts = {}) {
  const el = h("div", { class: "dial-slot" }, pending("Loading trust dial"));
  dialModule().then((m) => {
    if (!m?.mountDial) { fill(el, h("div", { class: "card dial-pending" }, pending("Trust dial pending"))); return; }
    fill(el);
    try { m.mountDial(el, bench, opts); } catch (e) { fill(el, h("div", { class: "card dial-pending" }, pending("Trust dial pending"))); console.warn("dial", e); }
  });
  return el;
}

// Which questions UI-3's dial would send to GPT at threshold t for this benchmark; null when unavailable.
export async function routedAt(bench, t) {
  const m = await dialModule();
  if (!m?.routedAt) return null;
  try { return await m.routedAt(bench, t); } catch { return null; }
}
