// Shared plumbing for the scenario: result recorder, browser actors, and "what the user can see" readers.
import { writeFileSync } from "node:fs";
import path from "node:path";
import type { Browser, BrowserContext, Page } from "playwright-core";
import type { HumanRole } from "@vice/contracts";
import { sleep } from "./api";
import { decodePng, regionStats, type Image, type RegionStats } from "./png";

export type Status = "pass" | "fail" | "blocked";
export interface Check { step: string; id: string; title: string; status: Status; evidence: string; refs: string[] }

export class Recorder {
  checks: Check[] = [];
  notes: string[] = [];
  screenshots: string[] = [];
  /** ok: true = pass, false = fail, "blocked" = could not be exercised (with the reason in the evidence). */
  rec(step: string, id: string, title: string, ok: boolean | Status, evidence: unknown, refs: string[] = []): boolean {
    const status: Status = ok === true ? "pass" : ok === false ? "fail" : ok;
    const text = typeof evidence === "string" ? evidence : JSON.stringify(evidence);
    this.checks.push({ step, id, title, status, evidence: text.length > 1400 ? `${text.slice(0, 1400)}…` : text, refs });
    const tag = status === "pass" ? "PASS   " : status === "fail" ? "FAIL   " : "BLOCKED";
    console.log(`${tag} [${step}] ${title}${text ? `  — ${text.length > 300 ? `${text.slice(0, 300)}…` : text}` : ""}`);
    return status === "pass";
  }
  note(text: string): void { this.notes.push(text); console.log(`NOTE    ${text}`); }
  statusOf(filter: (c: Check) => boolean): Status | null {
    const list = this.checks.filter(filter);
    if (!list.length) return null;
    if (list.some((c) => c.status === "fail")) return "fail";
    if (list.some((c) => c.status === "blocked")) return "blocked";
    return "pass";
  }
}

export interface Actor {
  key: "A" | "B" | "C"; nick: string; role: HumanRole; ctx: BrowserContext; page: Page;
  console: string[]; errors: string[]; held: Set<string>;
  playerId: string | null; charId: string | null;
}

export async function newActor(browser: Browser, key: Actor["key"], nick: string, role: HumanRole): Promise<Actor> {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1, reducedMotion: "no-preference" });
  // tsx keeps function names with a helper that does not exist inside the page; give the page a harmless one.
  await ctx.addInitScript("globalThis.__name = globalThis.__name || ((f) => f);");
  const page = await ctx.newPage();
  const a: Actor = { key, nick, role, ctx, page, console: [], errors: [], held: new Set(), playerId: null, charId: null };
  page.on("console", (m) => {
    const t = m.type();
    if (t === "error" || t === "warning") { const line = `[${t}] ${m.text().slice(0, 600)}`; a.console.push(line); if (t === "error") a.errors.push(line); }
  });
  page.on("pageerror", (e) => { const line = `[pageerror] ${String(e.message).slice(0, 600)}`; a.console.push(line); a.errors.push(line); });
  page.on("crash", () => { a.errors.push("[crash] page crashed"); });
  return a;
}

/** Everything a person could read off the page right now, as plain data. */
export const VIEW_JS = `(() => {
  const txt = (el) => (el && el.innerText ? el.innerText.replace(/\\s+/g, " ").trim() : null);
  const all = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const canvas = document.querySelector(".city canvas");
  const rect = canvas ? canvas.getBoundingClientRect() : null;
  const dec = document.querySelector(".inspector .agent .dec");
  const chainBtn = all(".tl-bar button").find((b) => /Clear chain/.test(b.innerText));
  return {
    joined: !!document.querySelector(".app"),
    joinScreen: !!document.querySelector(".join-card"),
    header: txt(document.querySelector(".hdr")),
    badges: all(".hdr .badge").map(txt),
    hud: txt(document.querySelector(".hud")),
    walletText: txt(document.querySelector(".market .wallet")),
    balance: txt(document.querySelector(".market .wallet > div:nth-child(1) b")),
    netWorth: txt(document.querySelector(".market .wallet > div:nth-child(2) b")),
    holds: all(".market .wallet .hold").map(txt),
    tickers: all(".market .tcard").map((c) => {
      const buy = c.querySelector("button.buy"), sell = c.querySelector("button.sell"), svg = c.querySelector("svg.chart");
      return {
        ticker: txt(c.querySelector(".tk")), name: txt(c.querySelector(".nm")), price: txt(c.querySelector(".px")), chg: txt(c.querySelector(".chg")),
        meta: txt(c.querySelector(".tcard-meta")), halted: c.classList.contains("halted"),
        buy: txt(buy), buyDisabled: buy ? buy.disabled : null, sell: txt(sell), sellDisabled: sell ? sell.disabled : null,
        own: txt(c.querySelector(".own")), chart: svg ? svg.getAttribute("aria-label") : null, chartPaths: c.querySelectorAll("svg.chart path").length,
      };
    }),
    feed: all(".market .feed li").map(txt),
    chips: all(".inspector .chip").map((c) => ({ text: txt(c), title: c.getAttribute("title"), on: c.classList.contains("on") })),
    agent: txt(document.querySelector(".inspector .agent")),
    agentName: txt(document.querySelector(".inspector .agent-name")),
    decSrc: dec ? txt(dec.querySelector(".src")) : null,
    decChoice: dec ? txt(dec.querySelector(".dec-choice")) : null,
    decMeta: dec ? all(".dec-meta", dec).map(txt).join(" | ") : null,
    decOpts: dec ? all(".opts li", dec).map(txt) : [],
    decBars: dec ? dec.querySelectorAll(".opts .bar").length : 0,
    mems: all(".inspector .agent .mems li").map(txt),
    timeline: all(".tl-list .ev").map((e) => ({ text: txt(e), cls: e.className })),
    chainButton: chainBtn ? txt(chainBtn) : null,
    notice: txt(document.querySelector(".notice")),
    crash: (() => { const c = document.querySelector(".cc-root") || document.querySelector(".crash"); return c ? ((c.innerText || c.textContent || "").replace(/\s+/g, " ").trim() || null) : null; })(),
    overlay: !!document.querySelector(".cc-root, .crash"),
    tape: txt(document.querySelector(".vxt")),
    tabs: all(".tabs button[role=tab]").map(txt),
    toasts: all(".toasts .toast").map(txt),
    canvas: rect ? { x: rect.x, y: rect.y, w: rect.width, h: rect.height, label: canvas.getAttribute("aria-label") } : null,
    canvasCount: document.querySelectorAll(".city canvas").length,
    cityText: txt(document.querySelector(".city")),
    tab: txt(document.querySelector('.tabs button[aria-selected="true"]')),
    director: txt(document.querySelector(".director")),
    seg: all(".hdr .seg button").map((b) => ({ text: txt(b), on: b.classList.contains("on"), disabled: b.disabled })),
  };
})()`;

export interface TickerCard {
  ticker: string | null; name: string | null; price: string | null; chg: string | null; meta: string | null; halted: boolean;
  buy: string | null; buyDisabled: boolean | null; sell: string | null; sellDisabled: boolean | null; own: string | null; chart: string | null; chartPaths: number;
}
export interface View {
  joined: boolean; joinScreen: boolean; header: string | null; badges: Array<string | null>; hud: string | null;
  walletText: string | null; balance: string | null; netWorth: string | null; holds: Array<string | null>;
  tickers: TickerCard[]; feed: Array<string | null>;
  chips: Array<{ text: string | null; title: string | null; on: boolean }>;
  agent: string | null; agentName: string | null; decSrc: string | null; decChoice: string | null; decMeta: string | null; decOpts: Array<string | null>; decBars: number;
  mems: Array<string | null>; timeline: Array<{ text: string | null; cls: string }>; chainButton: string | null;
  notice: string | null; crash: string | null; overlay: boolean; tape: string | null; tabs: Array<string | null>; toasts: Array<string | null>;
  canvas: { x: number; y: number; w: number; h: number; label: string | null } | null; canvasCount: number; cityText: string | null;
  tab: string | null; director: string | null; seg: Array<{ text: string | null; on: boolean; disabled: boolean }>;
}
export const view = (a: Actor): Promise<View> => a.page.evaluate(VIEW_JS) as Promise<View>;

/** "V$1,234" -> 1234 */
export function vd(s: string | null | undefined): number | null {
  if (!s) return null;
  const m = s.replace(/,/g, "").match(/V\$\s*(-?\d+)/);
  return m ? Number(m[1]) : null;
}
export const card = (v: View, ticker: string): TickerCard | undefined => v.tickers.find((t) => t.ticker === ticker);

/** The page's own store (dev handle window.__vice): the data the page renders from. */
export function store<T>(a: Actor, body: string): Promise<T | null> {
  return a.page.evaluate(`(() => { const s = window.__vice && window.__vice.getState(); if (!s) return null; ${body} })()`) as Promise<T | null>;
}
export interface Ident { playerId: string; role: HumanRole; isDirector: boolean; walletId: string | null; characterId: string | null; conn: string; runId: string; viewMode: string }
export const ident = (a: Actor): Promise<Ident | null> => store<Ident>(a, `
  if (!s.session) return null;
  const mine = Object.values(s.characters).filter((c) => c.playerId === s.session.playerId);
  const live = mine.find((c) => c.status === "ACTIVE" || c.status === "WANTED");
  const latest = mine.sort((x, y) => y.spawnTimeMs - x.spawnTimeMs)[0];
  return { playerId: s.session.playerId, role: s.session.role, isDirector: s.session.isDirector, walletId: s.session.walletId,
    characterId: live ? live.id : latest ? latest.id : null, conn: s.conn, runId: s.runId, viewMode: s.viewMode };`);
export const poseIn = (a: Actor, charId: string): Promise<{ x: number; z: number; status: string; heat: number } | null> => store(a, `
  const t = s.lastTick && s.lastTick.characters.find((c) => c.id === ${JSON.stringify(charId)});
  return t ? { x: t.x, z: t.z, status: t.status, heat: t.heat } : null;`);

/** Sends one command through the page's own socket client (Vite dev module). Used only for commands the UI has no control for. */
export async function pageSend(a: Actor, cmd: Record<string, unknown>): Promise<{ ok: boolean; id?: string; duplicate?: boolean; error?: { code: string; message: string; reason?: string }; result?: unknown } | null> {
  try {
    return await a.page.evaluate(`(async () => { const m = await import("/src/net/socketClient.ts"); return await m.send(${JSON.stringify(cmd)}); })()`) as never;
  } catch (e) { return { ok: false, error: { code: "HARNESS", message: `page socket unavailable: ${(e as Error).message.slice(0, 200)}` } }; }
}

/** The capture cinematic covers the whole page for a few seconds and swallows clicks. Dismiss it the way a user would (Escape). */
export async function dismissOverlay(a: Actor): Promise<boolean> {
  const has = (): Promise<boolean> => a.page.evaluate(`!!document.querySelector(".cc-root, .crash")`).catch(() => false) as Promise<boolean>;
  if (!(await has())) return false;
  for (let i = 0; i < 4 && (await has()); i++) {
    await a.page.keyboard.press("Escape").catch(() => {});
    await sleep(350);
    if (await has()) await a.page.evaluate(`(() => { const e = document.querySelector(".cc-root, .crash"); if (e) e.click(); })()`).catch(() => {});
    await sleep(350);
  }
  const end = Date.now() + 7000;
  while (Date.now() < end && (await has())) await sleep(150);
  return true;
}
/** Click that first gets any full-page overlay out of the way. */
export async function click(a: Actor, selector: string, o: { timeout?: number } = {}): Promise<void> {
  await dismissOverlay(a);
  await a.page.click(selector, { timeout: o.timeout ?? 6000 });
}

/** Uses the header's 2D / 3D toggle and waits until the city view has really switched. */
export async function setView(a: Actor, mode: "2D" | "3D"): Promise<boolean> {
  const flat = "Live map of Coral Bay";
  const is = async (): Promise<boolean> => {
    const v = await view(a);
    const on = v.seg.find((b) => b.on)?.text;
    const label = v.canvas?.label ?? "";
    return on === mode && (mode === "2D" ? label.startsWith(flat) : !label.startsWith(flat) && v.canvasCount >= 1);
  };
  if (await is()) return true;
  await click(a, `.hdr .seg button:text-is("${mode}")`).catch(() => {});
  const end = Date.now() + (mode === "3D" ? 25000 : 8000);
  while (Date.now() < end) { if (await is()) return true; await sleep(200); }
  return false;
}

/** Opens one NPC in the Agent Inspector through its chip; retries, and says what went wrong if it cannot. */
export async function selectAgentByChip(a: Actor, npcName: string): Promise<{ ok: boolean; attempts: number; error: string | null }> {
  const last = npcName.split(" ").slice(-1)[0]!;
  let error: string | null = null;
  for (let i = 1; i <= 3; i++) {
    const v0 = await view(a);
    if (v0.agentName === npcName) return { ok: true, attempts: i - 1, error };
    await dismissOverlay(a);
    try { await a.page.click(`.inspector .chips .chip:has-text("${last}")`, { timeout: 5000 }); }
    catch (e) { error = String((e as Error).message).split("\n").slice(0, 3).join(" ").slice(0, 300); }
    const got = await until(async () => ((await view(a)).agentName === npcName ? true : null), 2500, 100);
    if (got) return { ok: true, attempts: i, error };
  }
  return { ok: false, attempts: 3, error };
}

export async function until<T>(fn: () => Promise<T | null | undefined | false> | T | null | undefined | false, timeoutMs: number, everyMs = 100): Promise<T | null> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    let v: T | null | undefined | false = null;
    try { v = await fn(); } catch { v = null; }
    if (v) return v;
    if (Date.now() >= end) return null;
    await sleep(everyMs);
  }
}

export class Shots {
  constructor(private dir: string, private rec: Recorder) {}
  /** Slowest capture so far (ms): 3D pages need a GPU read-back, which matters for time-critical frames. */
  slowestMs = 0;
  /** `fast` skips the full-page measuring pass (the app never scrolls, so the picture is the same). */
  async one(a: Actor, name: string, fast = false): Promise<string | null> {
    const file = path.join(this.dir, `${name}-${a.key}.png`);
    const t0 = Date.now();
    try { await a.page.screenshot({ path: file, fullPage: !fast, timeout: 15000 }); this.rec.screenshots.push(file); this.slowestMs = Math.max(this.slowestMs, Date.now() - t0); return file; }
    catch (e) { this.rec.note(`screenshot ${name}-${a.key} failed: ${(e as Error).message.slice(0, 160)}`); return null; }
  }
  async all(actors: Actor[], name: string, fast = false): Promise<Array<string | null>> { return Promise.all(actors.map((a) => this.one(a, name, fast))); }
  /** Screenshot of one rectangle, decoded, with colour statistics. */
  async region(a: Actor, r: { x: number; y: number; w: number; h: number }, saveAs?: string): Promise<{ img: Image; stats: RegionStats } | null> {
    try {
      const buf = await a.page.screenshot({ clip: { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.max(1, r.w), height: Math.max(1, r.h) }, timeout: 15000 });
      if (saveAs) { const file = path.join(this.dir, `${saveAs}-${a.key}.png`); writeFileSync(file, buf); this.rec.screenshots.push(file); }
      const img = decodePng(buf);
      return { img, stats: regionStats(img, { x: 0, y: 0, w: img.width, h: img.height }) };
    } catch (e) { this.rec.note(`region capture failed for ${a.key}: ${(e as Error).message.slice(0, 160)}`); return null; }
  }
}
