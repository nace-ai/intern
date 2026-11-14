// TickerTape: the exchange tape under the app header. Reads the zustand store directly (no props).
//
// - Listings scroll past with live price (rolling digits), change since listing, a sparkline, a WANTED heat chip,
//   and a struck-through "V$0 HALTED" once the character is captured.
// - Swarm headlines are interleaved, each built from one recorded event (see TickerTapeFeed.ts) and tagged with
//   who actually decided. Only decisionSource "drex" is ever labelled DREX.
// - The belt is a conveyor: cells are appended off-screen on the right and dropped off-screen on the left, the
//   scroll position is re-anchored in the same frame, so nothing visible ever jumps and the speed is constant.
// - prefers-reduced-motion renders a static strip: no scrolling, no canvas, no flashes.
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ReactElement } from "react";
import { CREDITS_NOTICE, wantedLevel } from "@vice/contracts";
import type { DecisionSource, ListingView, MessageKind, PricePoint } from "@vice/contracts";
import { useGame } from "../../state/gameStore";
import type { GameState } from "../../state/gameStore";
import { TapeFeed } from "./TickerTapeFeed";
import type { Headline, NewsItem, Part, Tone } from "./TickerTapeFeed";
import { createTapeFx } from "./TickerTapeFx";
import type { TapeFx } from "./TickerTapeFx";
import "./TickerTape.css";

// ---------- constants ----------
/** Scroll speed in strip-heights per second: about 143 px/s on a 34 px strip, the same reading pace at any scale. */
const SPEED_PER_H = 4.2;
const LOOKAHEAD_PER_H = 0.9;
const MAX_CELLS = 48;
const FLASH_MS = 4400;
const ALERT_MS = 3600;

const KIND_COLOR: Record<MessageKind, string> = { REPORT: "#4cc9f0", GOSSIP: "#ff5fa2", RELAY: "#c77dff", BULLETIN: "#ffd166" };
const SOURCE_COLOR: Record<DecisionSource, string> = {
  drex: "#3ee68a", meta: "#a56bff", scripted: "#9aa0d0", fallback: "#ffb347", drex_low_confidence_guardrail: "#ffb347", replay: "#9aa0d0",
};
const TONE_COLOR: Record<Tone, string> = {
  report: "#4cc9f0", police: "#4cc9f0", gossip: "#ff5fa2", relay: "#c77dff", buy: "#3ee68a", sell: "#ff5468", wanted: "#ff3d5e",
  capture: "#ff3d5e", escape: "#5dfdcb", job: "#ffd166", listing: "#2ff3e0", system: "#a56bff", quiet: "#9aa0d0",
};
/** Honest source labels. The word DREX appears only for decisionSource "drex". */
const SOURCE_META: Record<DecisionSource, { text: string; cls: string; title: string }> = {
  drex: { text: "DREX", cls: "s-drex", title: "Decided by a live Nace Drex call" },
  meta: { text: "META LLM", cls: "s-meta", title: "Decided by a live Meta LLM call" },
  scripted: { text: "SCRIPTED", cls: "s-scripted", title: "Fixed rule. No model was called" },
  fallback: { text: "FALLBACK", cls: "s-fallback", title: "The model call failed or timed out, so a fallback rule decided" },
  drex_low_confidence_guardrail: { text: "GUARDRAIL", cls: "s-guard", title: "The model's answer was close to a coin flip, so the safe action was applied instead" },
  replay: { text: "REPLAY", cls: "s-replay", title: "A recorded decision, replayed" },
};
const SOURCE_ORDER: DecisionSource[] = ["drex", "meta", "drex_low_confidence_guardrail", "fallback", "scripted", "replay"];

const money = (n: number | null | undefined): string => (n == null ? "—" : `V$${Math.round(n).toLocaleString("en-US")}`);
const fmtPct = (p: number): string => { const a = Math.abs(p); return `${p >= 0 ? "+" : "−"}${a.toFixed(a >= 1000 ? 0 : 1)}%`; };

// ---------- store helpers ----------
/** Active listings in the order they opened, then the three most recently halted ones. */
function listingOrder(s: GameState): string[] {
  const all = Object.values(s.listings);
  const active = all.filter((l) => l.status === "ACTIVE").sort((a, b) => a.openedAtMs - b.openedAtMs || a.ticker.localeCompare(b.ticker));
  const halted = all.filter((l) => l.status !== "ACTIVE").sort((a, b) => (b.haltedAtMs ?? 0) - (a.haltedAtMs ?? 0)).slice(0, 3);
  return [...active, ...halted].map((l) => l.ticker);
}
const selectTickerKey = (s: GameState): string => listingOrder(s).join("|");

const RM_QUERY = "(prefers-reduced-motion: reduce)";
function subscribeMotion(cb: () => void): () => void {
  const mq = window.matchMedia(RM_QUERY);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
const useReducedMotion = (): boolean => useSyncExternalStore(subscribeMotion, () => window.matchMedia(RM_QUERY).matches, () => false);

// ---------- listing cell ----------
/** True opening price per character, remembered so the baseline survives once the server trims old history. */
const OPENS = new Map<string, number>();
function baseline(l: ListingView): { open: number; exact: boolean } {
  const first = l.history[0];
  if (first && first.t <= l.openedAtMs + 1500) {
    if (OPENS.size > 400) OPENS.clear();
    OPENS.set(l.characterId, first.spot);
    return { open: first.spot, exact: true };
  }
  const known = OPENS.get(l.characterId);
  if (known != null) return { open: known, exact: true };
  return { open: first?.spot ?? l.spotPrice, exact: false };
}

const SPARK_W = 100, SPARK_H = 20;
/** Geometry in a 100 x 20 box that the SVG stretches to whatever width the cell gives it. */
function sparkGeometry(history: PricePoint[]): { line: string; area: string; y: number } | null {
  const n = history.length;
  if (n < 2) return null;
  const MAX = 56;
  const pts: PricePoint[] = [];
  if (n <= MAX) pts.push(...history);
  else for (let i = 0; i < MAX; i++) pts.push(history[Math.round((i * (n - 1)) / (MAX - 1))]!);
  const t0 = pts[0]!.t, span = Math.max(1, pts[pts.length - 1]!.t - t0);
  let lo = Infinity, hi = -Infinity;
  for (const p of pts) { if (p.spot < lo) lo = p.spot; if (p.spot > hi) hi = p.spot; }
  if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
  const padY = 3;
  const X = (t: number) => ((t - t0) / span) * SPARK_W;
  const Y = (v: number) => SPARK_H - padY - ((v - lo) / (hi - lo)) * (SPARK_H - padY * 2);
  let line = "";
  for (let i = 0; i < pts.length; i++) line += `${i ? "L" : "M"}${X(pts[i]!.t).toFixed(1)},${Y(pts[i]!.spot).toFixed(1)}`;
  return { line, area: `${line}L${SPARK_W},${SPARK_H}L0,${SPARK_H}Z`, y: Y(pts[pts.length - 1]!.spot) };
}

function Spark({ history }: { history: PricePoint[] }): ReactElement {
  const g = useMemo(() => sparkGeometry(history), [history]);
  return (
    <span className="vxt-spark" aria-hidden>
      <svg viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none">
        {g ? <><path className="ar" d={g.area} /><path className="ln" d={g.line} /></> : <path className="ln" d={`M0,${SPARK_H / 2}L${SPARK_W},${SPARK_H / 2}`} />}
      </svg>
      {g && <i className="vxt-spark-dot" style={{ top: `${(g.y / SPARK_H) * 100}%` }} />}
    </span>
  );
}

const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
/** Price with rolling digit columns. Columns are keyed from the right so they keep their identity as digits are added. */
function Odometer({ value }: { value: number }): ReactElement {
  const chars = Math.max(0, Math.round(value)).toLocaleString("en-US").split("");
  return (
    <span className="vxt-odo" aria-hidden>
      <span className="vxt-odo-cur">V$</span>
      {chars.map((ch, i) => {
        const pos = chars.length - i;
        if (ch < "0" || ch > "9") return <span key={`s${pos}`} className="vxt-odo-sep">{ch}</span>;
        return (
          <span key={`d${pos}`} className="vxt-odo-col">
            <span className="vxt-odo-strip" style={{ transform: `translate3d(0,${-Number(ch) * 10}%,0)` }}>{DIGITS.map((d) => <i key={d}>{d}</i>)}</span>
          </span>
        );
      })}
    </span>
  );
}

const Arrow = (): ReactElement => <svg viewBox="0 0 10 10" aria-hidden><path d="M5 0.8 9.6 9.2H0.4Z" /></svg>;
const Flame = (): ReactElement => (
  <svg viewBox="0 0 10 12" aria-hidden><path d="M5.2 0C5.6 2.1 8.9 3.9 8.9 7.4 8.9 10 7.2 12 5 12S1.1 10.1 1.1 7.7C1.1 6 2 4.9 2.9 4.1 3 5.2 3.5 5.9 4.2 6.2 3.9 4 4.3 1.6 5.2 0Z" /></svg>
);

const ListingCell = memo(function ListingCell({ ticker, feed, calm }: { ticker: string; feed: TapeFeed; calm: boolean }): ReactElement | null {
  const l = useGame((s) => s.listings[ticker]);
  // -1 = not wanted, otherwise the wanted level 0..5. Ticks update heat/status, so both are read from there.
  const heat = useGame((s) => {
    const li = s.listings[ticker];
    const c = li ? s.characters[li.characterId] : undefined;
    return c && c.status === "WANTED" ? wantedLevel(c.heat) : -1;
  });
  const selected = useGame((s) => s.selectedTicker === ticker);
  const ref = useRef<HTMLButtonElement>(null);

  // One-shot price pulse, driven by MARKET_PRICE_UPDATED events that arrived after mount.
  useEffect(() => {
    if (calm) return;
    return feed.on((sig) => {
      if (sig.type !== "price" || sig.ticker !== ticker || sig.dir === 0) return;
      const el = ref.current;
      if (!el || typeof el.animate !== "function") return;
      const strong = sig.reason !== "SURVIVAL_TICK" && sig.reason !== "SENTIMENT_DECAY";
      const rgb = sig.reason === "CAPTURE" || sig.dir < 0 ? "255,84,104" : "62,230,138";
      el.animate([{ backgroundColor: `rgba(${rgb},${strong ? 0.5 : 0.22})` }, { backgroundColor: `rgba(${rgb},0)` }], { duration: strong ? 1100 : 700, easing: "ease-out" });
      const px = el.querySelector<HTMLElement>(".vxt-odo");
      if (px && strong) px.animate([{ transform: "scale(1)" }, { transform: "scale(1.2)" }, { transform: "scale(1)" }], { duration: 520, easing: "cubic-bezier(.2,1.6,.4,1)" });
    });
  }, [feed, ticker, calm]);

  if (!l) return null;
  const halted = l.status !== "ACTIVE";
  const base = baseline(l);
  const pct = base.open > 0 ? ((l.spotPrice - base.open) / base.open) * 100 : 0;
  const dir = halted ? "halt" : pct > 0.05 ? "up" : pct < -0.05 ? "down" : "flat";
  const wanted = !halted && heat >= 0;
  const since = base.exact ? "since listing" : "over the retained price history";
  const label = halted
    ? `${ticker}, ${l.nickname}: captured. Listing halted at V$0${l.finalSpotBeforeHalt != null ? `, last traded ${money(l.finalSpotBeforeHalt)}` : ""}.`
    : `${ticker}, ${l.nickname}: ${money(l.spotPrice)}, ${fmtPct(pct)} ${since}${wanted ? `, WANTED level ${heat} of 5` : ""}.`;
  return (
    <button ref={ref} type="button" tabIndex={-1} data-ticker={ticker}
      className={`vxt-cell vxt-l is-${dir}${wanted ? " is-wanted" : ""}${selected ? " is-sel" : ""}`}
      aria-label={label} title={`${label} Click to select it in the market panel.`}
      onClick={() => useGame.setState({ selectedTicker: ticker })}>
      <span className="vxt-sym">{ticker}</span>
      {/* Fixed-width middle: the sparkline gives up room to the WANTED chip, so the cell itself never changes width. */}
      <span className="vxt-mid">
        <span className={wanted ? "vxt-heat-slot on" : "vxt-heat-slot"}>
          <span className="vxt-heat"><Flame />WANTED<span className="vxt-pips">{[0, 1, 2, 3, 4].map((i) => <i key={i} className={i < heat ? "on" : undefined} />)}</span></span>
        </span>
        <Spark history={l.history} />
      </span>
      <span className="vxt-right">
        {halted ? (
          <>
            <s className="vxt-halt">V$0 HALTED</s>
            {l.finalSpotBeforeHalt != null && <span className="vxt-was">was {money(l.finalSpotBeforeHalt)}</span>}
          </>
        ) : (
          <>
            <Odometer value={l.spotPrice} />
            <span className="vxt-chg"><Arrow />{fmtPct(pct)}</span>
          </>
        )}
      </span>
    </button>
  );
});

// ---------- headline cell ----------
const PART_CLASS: Record<Part["k"], string | undefined> = { t: undefined, tk: "p-tk", nm: "p-nm", $: "p-m", z: "p-z", v: "p-v" };
function Parts({ parts }: { parts: Part[] }): ReactElement {
  return <span className="vxt-text">{parts.map((p, i) => (PART_CLASS[p.k] ? <span key={i} className={PART_CLASS[p.k]}>{p.t}</span> : p.t))}</span>;
}

function SourceChip({ h }: { h: Headline }): ReactElement | null {
  if (!h.source) return null;
  const m = SOURCE_META[h.source] ?? SOURCE_META.scripted;
  return (
    <span className={`vxt-src ${m.cls}`} title={m.title}>
      <b>{m.text}</b>
      {h.confidence != null && <small>conf {h.confidence.toFixed(2)}</small>}
      {h.latencyMs != null && <small>{Math.round(h.latencyMs)} ms</small>}
    </span>
  );
}

function selectHeadline(h: Headline): void {
  const st = useGame.getState();
  st.selectEvent(h.eventId);
  if (h.agentId && st.npcs[h.agentId]) st.selectAgent(h.agentId);
  if (h.ticker && st.listings[h.ticker]) useGame.setState({ selectedTicker: h.ticker });
}

/** fresh = first showing (bright). late / recap = the event is older, so its clock time is printed. */
const NewsCell = memo(function NewsCell({ h, fresh, late }: { h: Headline; fresh: boolean; late: boolean }): ReactElement {
  const src = h.source ? ` Decided by: ${(SOURCE_META[h.source] ?? SOURCE_META.scripted).title}.` : "";
  const dated = (!fresh || late) && !!h.clock;
  return (
    <button type="button" tabIndex={-1} className={`vxt-cell vxt-h tone-${h.tone} ${fresh ? "is-fresh" : "is-recap"}`}
      aria-label={`${h.label}: ${h.text}.${src}`} title={`${h.text}${dated ? ` (at ${h.clock})` : ""}.${src} Click to trace it in the event timeline.`}
      onClick={() => selectHeadline(h)}>
      <span className="vxt-tag"><b>{h.label}</b></span>
      {dated && <span className="vxt-clock">{h.clock}</span>}
      <Parts parts={h.parts} />
      <SourceChip h={h} />
    </button>
  );
});

// ---------- stat cells (measured values from MetricsView, nothing estimated) ----------
type StatKind = "swarm" | "rumor" | "trades" | "latency" | "notice" | "empty";

const StatCell = memo(function StatCell({ stat }: { stat: StatKind }): ReactElement | null {
  // Frozen when the cell is put on the belt: a cell must never change width while it is on screen. The next
  // cell of this kind carries the newer numbers.
  const [{ m, agents }] = useState(() => { const s = useGame.getState(); return { m: s.metrics, agents: Object.keys(s.npcs).length }; });
  const row = (tone: Tone, tag: string, body: ReactElement) => (
    <div className={`vxt-cell vxt-st tone-${tone}`}><span className="vxt-tag"><b>{tag}</b></span>{body}</div>
  );
  switch (stat) {
    case "swarm": {
      const total = m?.decisions.total ?? 0;
      const by = m?.decisions.bySource ?? {};
      return row("system", "SWARM", (
        <>
          <span className="vxt-text"><b>{agents}</b> agents · <b>{total}</b> decision{total === 1 ? "" : "s"}{total === 0 ? " yet" : " by"}</span>
          {SOURCE_ORDER.filter((k) => (by[k] ?? 0) > 0).map((k) => (
            <span key={k} className={`vxt-src ${SOURCE_META[k].cls}`} title={SOURCE_META[k].title}><b>{SOURCE_META[k].text}</b><small>{by[k]}</small></span>
          ))}
        </>
      ));
    }
    case "rumor":
      if (!m) return null;
      return row("gossip", "RUMOR MILL", <span className="vxt-text"><b>{m.messages.sent}</b> messages sent · <b>{m.messages.delivered}</b> delivered · longest chain <b>{m.messages.maxHops}</b> hop{m.messages.maxHops === 1 ? "" : "s"}</span>);
    case "trades":
      if (!m) return null;
      return row("buy", "ORDERS", <span className="vxt-text"><b>{m.trades.executed}</b> trade{m.trades.executed === 1 ? "" : "s"} · NPC <b>{m.trades.byNpc}</b> · human <b>{m.trades.byHuman}</b> · captures <b>{m.captures}</b></span>);
    case "latency": {
      const d = m?.latency.drex, x = m?.latency.meta;
      if (!d?.count && !x?.count) return null;
      return row("listing", "LIVE CALLS", (
        <span className="vxt-text">
          {d?.count ? <>Drex API round trip p50 <b>{Math.round(d.p50Ms)} ms</b> · p95 <b>{Math.round(d.p95Ms)} ms</b> · {d.count} call{d.count === 1 ? "" : "s"}</> : null}
          {d?.count && x?.count ? " · " : null}
          {x?.count ? <>Meta API p50 <b>{Math.round(x.p50Ms)} ms</b> · {x.count} call{x.count === 1 ? "" : "s"}</> : null}
        </span>
      ));
    }
    case "notice": return row("quiet", "FICTIONAL", <span className="vxt-text">{CREDITS_NOTICE}</span>);
    case "empty": return row("listing", "EXCHANGE", <span className="vxt-text">No tickers listed yet · an outlaw has to join</span>);
    default: return null;
  }
});

function statKinds(s: GameState): StatKind[] {
  const m = s.metrics;
  const out: StatKind[] = [];
  if (Object.keys(s.listings).length === 0) out.push("empty");
  out.push("swarm");
  if (m && m.messages.sent > 0) out.push("rumor");
  if (m && (m.trades.executed > 0 || m.captures > 0)) out.push("trades");
  if (m && ((m.latency.drex?.count ?? 0) > 0 || (m.latency.meta?.count ?? 0) > 0)) out.push("latency");
  out.push("notice");
  return out;
}

// ---------- the belt ----------
type Item = { kind: "listing"; ticker: string } | { kind: "news"; h: Headline; fresh: boolean; late: boolean } | { kind: "stat"; stat: StatKind };
interface Cell { key: number; item: Item }

const CellView = memo(function CellView({ cell, feed }: { cell: Cell; feed: TapeFeed }): ReactElement {
  const it = cell.item;
  return (
    <div className={cell.key % 2 ? "vxt-slot alt" : "vxt-slot"} data-k={cell.key}>
      {it.kind === "listing" ? <ListingCell ticker={it.ticker} feed={feed} calm={false} /> : it.kind === "news" ? <NewsCell h={it.h} fresh={it.fresh} late={it.late} /> : <StatCell stat={it.stat} />}
    </div>
  );
});

interface Playlist { lastTicker: string | null; newsRun: number; flip: boolean; statIdx: number; /** listing to show next (it just became wanted, halted or listed) */ spotlight: string | null }
interface Engine {
  cells: Cell[]; nextKey: number;
  scroll: number; speed: number; hover: boolean; snapped: boolean;
  w: number; h: number;
  keys: number[]; lefts: number[]; rights: number[]; total: number;
  pending: boolean; syncFills: number; fx: TapeFx | null; pl: Playlist;
}
const newPlaylist = (): Playlist => ({ lastTicker: null, newsRun: 99, flip: false, statIdx: 0, spotlight: null });

/** What goes on the belt next. Fresh headlines go out in causal order; a capture takes the very next slot. */
function pickNext(e: Engine, feed: TapeFeed): Item {
  const s = useGame.getState();
  const order = listingOrder(s);
  const pl = e.pl;
  const news = (n: NewsItem): Item => ({ kind: "news", h: n.h, fresh: n.fresh, late: n.late });
  if (feed.hasBreaking()) { const n = feed.takeFresh(); if (n) { pl.newsRun++; return news(n); } }
  // A listing that just changed state (wanted, captured, newly listed) is shown right away, with its new look.
  if (pl.spotlight) {
    const ticker = pl.spotlight;
    pl.spotlight = null;
    // ...unless that listing is already the cell right before this one.
    if (s.listings[ticker] && !(pl.lastTicker === ticker && pl.newsRun === 0)) { pl.newsRun = 0; pl.lastTicker = ticker; return { kind: "listing", ticker }; }
  }
  // News cells allowed between two listings. A burst of events is told back to back so the tape keeps up.
  const waiting = feed.backlog();
  const quota = waiting >= 2 ? 4 : waiting === 1 ? 2 : order.length <= 1 ? 2 : 1;
  if (order.length > 0 && pl.newsRun >= quota) {
    pl.newsRun = 0;
    const at = pl.lastTicker ? order.indexOf(pl.lastTicker) : -1;
    const ticker = order[(at + 1) % order.length]!;
    pl.lastTicker = ticker;
    return { kind: "listing", ticker };
  }
  pl.newsRun++;
  const f = feed.takeFresh();
  if (f) return news(f);
  pl.flip = !pl.flip;
  if (pl.flip) { const r = feed.takeRecap(); if (r) return news(r); }
  const kinds = statKinds(s);
  return { kind: "stat", stat: kinds[pl.statIdx++ % kinds.length]! };
}

interface Flash { seq: number; ticker: string; from: number; captorName: string }

function LiveTape({ feed }: { feed: TapeFeed }): ReactElement {
  const rootRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [cells, setCells] = useState<Cell[]>([]);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [alert, setAlert] = useState<Headline | null>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const [spoken, setSpoken] = useState("");
  const conn = useGame((s) => s.conn);
  const engRef = useRef<Engine | null>(null);
  if (!engRef.current) {
    engRef.current = { cells: [], nextKey: 1, scroll: 0, speed: 0, hover: false, snapped: false, w: 0, h: 34, keys: [], lefts: [], rights: [], total: 0, pending: false, syncFills: 0, fx: null, pl: newPlaylist() };
  }

  // The engine lives in refs; these closures never go stale because they only touch refs and stable setters.
  const api = useMemo(() => {
    const eng = (): Engine => engRef.current!;
    const applyTransform = (): void => { const t = trackRef.current; if (t) t.style.transform = `translate3d(${(-eng().scroll).toFixed(2)}px,0,0)`; };

    /** Re-reads the committed layout. The first visible cell is the anchor: if it moved (cells dropped on its
     *  left, or a width changed), the scroll offset moves with it, in this same frame. */
    const measure = (): void => {
      const e = eng(), track = trackRef.current;
      if (!track) return;
      let anchorKey = -1, anchorLeft = 0;
      for (let i = 0; i < e.keys.length; i++) { if (e.rights[i]! - e.scroll > 0) { anchorKey = e.keys[i]!; anchorLeft = e.lefts[i]!; break; } }
      const origin = track.getBoundingClientRect().left;
      const keys: number[] = [], lefts: number[] = [], rights: number[] = [];
      for (let el = track.firstElementChild as HTMLElement | null; el; el = el.nextElementSibling as HTMLElement | null) {
        const r = el.getBoundingClientRect();
        keys.push(Number(el.dataset.k)); lefts.push(r.left - origin); rights.push(r.right - origin);
      }
      if (anchorKey >= 0) {
        const j = keys.indexOf(anchorKey);
        if (j >= 0 && Math.abs(lefts[j]! - anchorLeft) > 0.01) { e.scroll += lefts[j]! - anchorLeft; applyTransform(); }
      }
      e.keys = keys; e.lefts = lefts; e.rights = rights;
      e.total = rights.length ? rights[rights.length - 1]! : 0;
    };

    /** Drops cells that left on the left and appends cells before the belt runs out on the right. */
    const maintain = (): void => {
      const e = eng();
      if (e.pending || e.w <= 0) return;
      const n = e.cells.length;
      let drop = 0;
      while (drop < n - 1 && drop < e.rights.length && e.rights[drop]! - e.scroll < -e.h * 0.3) drop++;
      const gap = e.w + e.h * LOOKAHEAD_PER_H - (e.total - e.scroll);
      if (!drop && gap <= 0) return;
      const next = drop ? e.cells.slice(drop) : e.cells.slice();
      // Exactly one cell per commit: it is measured before the next one is chosen, so the belt never queues more
      // than the cell that is entering and a fresh headline can take the very next slot.
      if (gap > 0 && next.length < MAX_CELLS) next.push({ key: e.nextKey++, item: pickNext(e, feed) });
      if (!drop && next.length === n) return;
      e.cells = next; e.pending = true;
      setCells(next);
    };

    const restart = (): void => {
      const e = eng();
      e.cells = []; e.keys = []; e.lefts = []; e.rights = []; e.total = 0; e.scroll = 0; e.pl = newPlaylist(); e.pending = true;
      e.fx?.clear();
      applyTransform();
      setCells([]);
    };
    return { eng, applyTransform, measure, maintain, restart };
  }, [feed]);

  // Size + canvas. Declared before the cells effect so the first fill already knows the width.
  useLayoutEffect(() => {
    const root = rootRef.current, canvas = canvasRef.current, track = trackRef.current;
    if (!root || !canvas || !track) return;
    const e = api.eng();
    e.fx = createTapeFx(canvas);
    const size = (): void => {
      const r = root.getBoundingClientRect();
      e.w = r.width; e.h = r.height || 34;
      e.fx?.resize(r.width, r.height);
    };
    size();
    e.speed = SPEED_PER_H * e.h;
    const ro = new ResizeObserver(() => { size(); api.measure(); api.maintain(); });
    ro.observe(root); ro.observe(track);
    return () => { ro.disconnect(); e.fx?.clear(); e.fx = null; };
  }, [api]);

  // After every commit of the belt: re-anchor, then top up before the browser paints. The first fill is a short
  // chain of synchronous commits (one per cell); the frame loop takes over if it ever gets long.
  useLayoutEffect(() => {
    const e = api.eng();
    api.measure();
    e.pending = false;
    if (e.syncFills++ < 28) api.maintain();
  }, [cells, api]);

  // The one animation frame loop: constant-speed scroll, eased pause on hover, light effects.
  useEffect(() => {
    let raf = 0, last = performance.now();
    const tick = (now: number): void => {
      raf = requestAnimationFrame(tick);
      const dt = Math.min(50, Math.max(0, now - last));
      last = now;
      const e = api.eng();
      e.syncFills = 0;
      if (e.w > 0) {
        const target = e.hover ? 0 : SPEED_PER_H * e.h;
        e.speed += (target - e.speed) * (1 - Math.exp(-dt / (e.hover ? 120 : 280)));
        if (Math.abs(target - e.speed) < 0.5) e.speed = target;
        if (e.speed > 0) { e.scroll += (e.speed * dt) / 1000; e.snapped = false; api.applyTransform(); }
        else if (!e.snapped) { const dpr = window.devicePixelRatio || 1; e.scroll = Math.round(e.scroll * dpr) / dpr; e.snapped = true; api.applyTransform(); }
        api.maintain();
      }
      e.fx?.frame(dt);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [api]);

  // One-shot effects. The feed only signals events that arrived after mount.
  useEffect(() => feed.on((sig) => {
    const e = api.eng(), fx = e.fx;
    switch (sig.type) {
      case "message": fx?.comet(KIND_COLOR[sig.kind] ?? "#ffffff", 0, false); break;
      case "decision": fx?.comet(SOURCE_COLOR[sig.source] ?? "#9aa0d0", 1, true); break;
      case "trade": {
        const root = rootRef.current, track = trackRef.current;
        if (!root || !track || !fx) break;
        const ox = root.getBoundingClientRect().left;
        // Sparks fly from the price of every visible cell of that ticker, never from under the cap or an alert.
        const from = (alertRef.current?.getBoundingClientRect().right ?? ox + e.h * 2.8) - ox;
        for (const el of track.querySelectorAll<HTMLElement>(`[data-ticker="${CSS.escape(sig.ticker)}"] .vxt-right`)) {
          const r = el.getBoundingClientRect();
          const x = r.left + r.width * 0.2 - ox;
          if (x > from && x < e.w) fx.burst(x, sig.side === "BUY" ? "#3ee68a" : "#ff5468", 12);
        }
        break;
      }
      case "headline":
        if (sig.headline.priority >= 2) fx?.edge(TONE_COLOR[sig.headline.tone] ?? "#2ff3e0");
        if (sig.headline.tone === "listing" && sig.headline.ticker) e.pl.spotlight = sig.headline.ticker;
        break;
      case "alert": setAlert(sig.headline); setSpoken(sig.headline.text); if (sig.headline.ticker) e.pl.spotlight = sig.headline.ticker; break;
      case "capture": {
        setFlash({ seq: sig.seq, ticker: sig.ticker, from: sig.from, captorName: sig.captorName });
        setSpoken(`${sig.ticker} captured by ${sig.captorName}. Listing halted at V$0.`);
        e.pl.spotlight = sig.ticker;
        fx?.shock(e.w / 2, "#ff3d5e");
        break;
      }
      case "reset": setFlash(null); setAlert(null); api.restart(); break;
      default: break;
    }
  }), [feed, api]);

  useEffect(() => { if (!flash) return; const t = setTimeout(() => setFlash(null), FLASH_MS); return () => clearTimeout(t); }, [flash]);
  useEffect(() => { if (!alert) return; const t = setTimeout(() => setAlert(null), ALERT_MS); return () => clearTimeout(t); }, [alert]);

  const live = conn === "connected";
  return (
    <div ref={rootRef} className="vxt" role="marquee" aria-label="Exchange ticker tape: prices and swarm headlines"
      onPointerEnter={() => { api.eng().hover = true; }} onPointerLeave={() => { api.eng().hover = false; }}>
      <div className="vxt-bg" aria-hidden />
      <div className="vxt-view">
        <div ref={trackRef} className="vxt-track">
          {cells.map((c) => <CellView key={c.key} cell={c} feed={feed} />)}
        </div>
      </div>
      <div className="vxt-rail top" aria-hidden /><div className="vxt-rail bot" aria-hidden />
      <div className={live ? "vxt-cap" : "vxt-cap off"} title={live ? "Live feed from the room server" : "Not connected to the room server"}><i aria-hidden />{live ? "LIVE" : "OFFLINE"}</div>
      {alert && (
        <div key={alert.id} ref={alertRef} className={`vxt-alert tone-${alert.tone}`}>
          <span className="vxt-tag"><b>{alert.label}</b></span><Parts parts={alert.parts} /><SourceChip h={alert} />
        </div>
      )}
      {flash && (
        <div key={flash.seq} className="vxt-flash">
          <b className="vxt-flash-sym">{flash.ticker}</b>
          <span className="vxt-flash-word">CAPTURED</span>
          <span className="vxt-flash-px"><s>{money(flash.from)}</s><i>→</i><b>V$0</b></span>
          <span className="vxt-flash-sub">LISTING HALTED · by {flash.captorName}</span>
        </div>
      )}
      <canvas ref={canvasRef} className="vxt-fx" aria-hidden />
      <span className="vxt-sr" aria-live="polite">{spoken}</span>
    </div>
  );
}

// ---------- calm variant ----------
function CalmTape({ feed }: { feed: TapeFeed }): ReactElement {
  const key = useGame(selectTickerKey);
  const tickers = useMemo(() => (key ? key.split("|") : []), [key]);
  const conn = useGame((s) => s.conn);
  const [latest, setLatest] = useState<Headline | null>(() => feed.latest());
  useEffect(() => {
    setLatest(feed.latest());
    return feed.on((sig) => { if (sig.type === "headline" || sig.type === "reset") setLatest(feed.latest()); });
  }, [feed]);
  const live = conn === "connected";
  return (
    <div className="vxt is-calm" role="region" aria-label="Exchange ticker: prices and the latest swarm headline (static)">
      <div className="vxt-bg" aria-hidden />
      <div className="vxt-row">
        <div className="vxt-slot news" aria-live="polite">{latest ? <NewsCell h={latest} fresh late={false} /> : <StatCell stat={tickers.length ? "swarm" : "empty"} />}</div>
        {/* wraps, and only the first line shows: whole cells only, never half a listing */}
        <div className="vxt-board">
          {tickers.map((t, i) => <div key={t} className={i % 2 ? "vxt-slot alt" : "vxt-slot"}><ListingCell ticker={t} feed={feed} calm /></div>)}
        </div>
      </div>
      <div className="vxt-rail top" aria-hidden /><div className="vxt-rail bot" aria-hidden />
      <div className={live ? "vxt-cap" : "vxt-cap off"}><i aria-hidden />{live ? "LIVE" : "OFFLINE"}</div>
    </div>
  );
}

/** Exchange ticker tape. No props: it reads the game store. Mount it once, directly under the app header. */
export default function TickerTape(): ReactElement {
  const calm = useReducedMotion();
  const [feed] = useState(() => new TapeFeed());
  useEffect(() => { feed.start(); return () => feed.stop(); }, [feed]);
  return calm ? <CalmTape feed={feed} /> : <LiveTape feed={feed} />;
}
