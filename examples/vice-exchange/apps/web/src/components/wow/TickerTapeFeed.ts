// TickerTape data layer: turns the authoritative event log into short plain-language headlines and one-shot
// signals. No React here. Nothing is invented: every headline is built from one recorded event, and a decision
// source / confidence / latency is attached only when the DECISION_RESOLVED event of that decision carries it.
import type { DecisionSource, GameEvent, MessageKind, PriceReason, TradeSide } from "@vice/contracts";
import { nameOf, useGame } from "../../state/gameStore";
import type { GameState } from "../../state/gameStore";

export type Tone =
  | "report" | "gossip" | "relay" | "buy" | "sell" | "wanted" | "capture" | "escape" | "job" | "listing" | "police" | "quiet" | "system";

/** t = plain text, tk = ticker, nm = person, $ = V$ amount, z = V$0, v = emphasised verb. */
export type PartKind = "t" | "tk" | "nm" | "$" | "z" | "v";
export interface Part { k: PartKind; t: string }

export interface Headline {
  /** Unique per run: `${runId}:${eventId}`. */
  id: string;
  eventId: string;
  seq: number;
  /** Wall clock of the event (HH:MM:SS), same format as the event timeline. */
  clock: string;
  tone: Tone;
  label: string;
  parts: Part[];
  text: string;
  /** 3 = breaking, 2 = story beat, 1 = minor. */
  priority: 1 | 2 | 3;
  ticker: string | null;
  /** NPC to open in the inspector when the headline is clicked. */
  agentId: string | null;
  /** Set only when this headline is the outcome of an NPC decision. */
  source: DecisionSource | null;
  /** Provider values copied from DECISION_RESOLVED. null = the provider did not supply them (never invented). */
  confidence: number | null;
  latencyMs: number | null;
}

export type TapeSignal =
  | { type: "price"; ticker: string; dir: 1 | -1 | 0; from: number; to: number; reason: PriceReason }
  | { type: "message"; kind: MessageKind }
  | { type: "decision"; source: DecisionSource }
  | { type: "trade"; ticker: string; side: TradeSide }
  | { type: "headline"; headline: Headline }
  | { type: "alert"; headline: Headline }
  | { type: "capture"; ticker: string; captorName: string; from: number; bounty: number; seq: number }
  | { type: "reset" };

/** fresh = first showing. late = first showing, but the event is already several seconds old (the tape was busy). */
export interface NewsItem { h: Headline; fresh: boolean; late: boolean }

const RING_MAX = 6;
const FRESH_MAX = 10;
/** A story beat that could not get a slot for this long is no longer news: it becomes a recap. */
const EXPIRE_MS = 45000;
const RECAP_MAX_SHOWS = 3;
const RECAP_GAP_MS = 16000;
/** A headline that waited this long for its slot is shown with its clock time. */
const LATE_MS = 9000;
/** Events older than this inside one batch are a backlog (resync), not live news: no one-shot effects. */
const STALE_MS = 20000;

const money = (n: number): string => `V$${Math.round(n).toLocaleString("en-US")}`;
const clockOf = (iso: string): string => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-US", { hour12: false }); };
const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const lower = (s: unknown): string => String(s ?? "").toLowerCase().replace(/_/g, " ");

const t = (s: string): Part => ({ k: "t", t: s });
const tk = (s: string): Part => ({ k: "tk", t: s });
const nm = (s: string): Part => ({ k: "nm", t: s });
const v = (s: string): Part => ({ k: "v", t: s });
const cash = (n: number): Part => (Math.round(n) === 0 ? { k: "z", t: "V$0" } : { k: "$", t: money(n) });

/** Highlights tickers, V$ amounts and known people inside a server-written sentence. */
function rich(raw: string, s: GameState): Part[] {
  const text = raw.replace(/\bchr_[A-Za-z0-9_]+\b/g, (id) => s.characters[id]?.nickname ?? id);
  const names = new Set<string>();
  for (const n of Object.values(s.npcs)) if (n.name.length > 1) names.add(n.name);
  for (const c of Object.values(s.characters)) if (c.nickname.length > 1) names.add(c.nickname);
  const alt = [...names].sort((a, b) => b.length - a.length).map(escapeRe).join("|");
  const re = new RegExp(`(\\b[A-Z0-9]{3}-\\d{2,}\\b)|(V\\$[\\d,]+)${alt ? `|(?<![\\w])(${alt})(?![\\w])` : ""}`, "g");
  const out: Part[] = [];
  let at = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > at) out.push(t(text.slice(at, m.index)));
    if (m[1]) out.push(tk(m[1]));
    else if (m[2]) out.push(/^V\$0$/.test(m[2]) ? { k: "z", t: m[2] } : { k: "$", t: m[2] });
    else out.push(nm(m[0]));
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push(t(text.slice(at)));
  return out;
}

interface DecisionInfo { source: DecisionSource; confidence: number | null; latencyMs: number | null; agentId: string }
interface Ctx { decisions: Map<string, DecisionInfo>; tradeCauses: Set<string> }

function make(e: GameEvent, tone: Tone, label: string, priority: 1 | 2 | 3, parts: Part[], extra: Partial<Headline> = {}): Headline {
  return {
    id: `${e.runId}:${e.eventId}`, eventId: e.eventId, seq: e.serverSequence, clock: clockOf(e.time), tone, label, parts,
    text: parts.map((p) => p.t).join(""), priority, ticker: null, agentId: null, source: null, confidence: null, latencyMs: null, ...extra,
  };
}

/** Provider numbers are shown only for decisions where a provider really answered. */
function providerNumbers(info: DecisionInfo | undefined, source: DecisionSource): Pick<Headline, "confidence" | "latencyMs"> {
  const real = !!info && info.source === source && (source === "drex" || source === "meta" || source === "drex_low_confidence_guardrail");
  return { confidence: real ? info!.confidence : null, latencyMs: real ? info!.latencyMs : null };
}

/** One event -> at most one headline. Returns null for events that are not tape material. */
export function headlineFor(e: GameEvent, s: GameState, ctx: Ctx): Headline | null {
  const who = (id: string | null | undefined) => nameOf(s, id);
  switch (e.type) {
    case "AGENT_ACTION_APPLIED": {
      const p = (e as GameEvent<"AGENT_ACTION_APPLIED">).payload;
      const action = String(p.action ?? "").toLowerCase();
      const isOrder = action === "buy" || action === "sell";
      // An executed NPC order already produced its own TRADE headline (tagged with this same decision).
      if (isOrder && e.causationId && ctx.tradeCauses.has(e.causationId)) return null;
      let detail = String(p.detail || action || "acted").trim().replace(/\.$/, "");
      if (/^chose to ignore$/i.test(detail)) detail = "chose to ignore it";
      else if (/^(buy|sell) order\b/i.test(detail)) detail = `'s ${detail.replace(/:\s*([A-Z_]+)$/, (_m, r: string) => `: ${lower(r)}`)}`;
      else if (/^[a-z]+ing\b/.test(detail)) detail = `is ${detail}`;
      let tone: Tone = "quiet", label = action.toUpperCase() || "ACTION", priority: 1 | 2 | 3 = 1;
      switch (`${p.role}:${action}`) {
        case "WITNESS:report": tone = "report"; label = "REPORT"; priority = 2; break;
        case "WITNESS:gossip": tone = "gossip"; label = "GOSSIP"; priority = 2; break;
        case "WITNESS:ignore": label = "IGNORED"; break;
        case "INFORMANT:relay": tone = "relay"; label = "RELAY"; priority = 2; break;
        case "INFORMANT:withhold": label = "WITHHELD"; break;
        case "TRADER:hold": label = "HOLD"; break;
        case "TRADER:buy": case "TRADER:sell": label = "ORDER"; break;
        case "POLICE:investigate": tone = "police"; label = "POLICE"; break;
        case "POLICE:pursue": tone = "wanted"; label = "PURSUIT"; priority = 3; break;
        case "POLICE:patrol": label = "PATROL"; break;
        default: break;
      }
      const name = who(p.agentId);
      const parts = [nm(name), ...(detail.startsWith("'s ") ? [] : [t(" ")]), ...rich(detail, s)];
      const ticker = parts.find((x) => x.k === "tk")?.t ?? null;
      return make(e, tone, label, priority, parts, {
        agentId: p.agentId, ticker, source: p.decisionSource, ...providerNumbers(e.causationId ? ctx.decisions.get(e.causationId) : undefined, p.decisionSource),
      });
    }
    case "TRADE_EXECUTED": {
      const p = (e as GameEvent<"TRADE_EXECUTED">).payload;
      const npc = p.actorKind === "NPC";
      const info = npc && e.causationId ? ctx.decisions.get(e.causationId) : undefined;
      if (npc && e.causationId) ctx.tradeCauses.add(e.causationId);
      const buy = p.side === "BUY";
      const parts = [nm(p.actorName), t(" "), v(buy ? "BOUGHT" : "SOLD"), t(p.qty > 1 ? ` ${p.qty} ` : " "), tk(p.ticker), t(" @ "), cash(p.executionPrice)];
      return make(e, buy ? "buy" : "sell", buy ? "BUY" : "SELL", 2, parts, {
        ticker: p.ticker, agentId: npc ? p.actorId : null, source: info?.source ?? null, ...(info ? providerNumbers(info, info.source) : {}),
      });
    }
    case "BULLETIN_PUBLISHED": {
      const p = (e as GameEvent<"BULLETIN_PUBLISHED">).payload;
      const subject = p.ticker ? tk(p.ticker) : nm(who(p.characterId));
      return make(e, "wanted", "BULLETIN", 3, [v("WANTED"), t(" bulletin issued for "), subject, t(" by "), nm(who(p.issuedBy))], { ticker: p.ticker, agentId: s.npcs[p.issuedBy] ? p.issuedBy : null });
    }
    case "WANTED_STATUS_CHANGED": {
      const p = (e as GameEvent<"WANTED_STATUS_CHANGED">).payload;
      // "no longer wanted" is told by ESCAPE_SUCCEEDED; a bulletin-caused change is told by the bulletin headline.
      if (!p.wanted || /bulletin/i.test(p.reason)) return null;
      const subject = p.ticker ? tk(p.ticker) : nm(who(p.characterId));
      return make(e, "wanted", "WANTED", 3, [subject, t(" is now "), v("WANTED"), t(` · heat ${Math.round(p.heat)}`)], { ticker: p.ticker });
    }
    case "ESCAPE_SUCCEEDED": {
      const p = (e as GameEvent<"ESCAPE_SUCCEEDED">).payload;
      const subject = p.ticker ? tk(p.ticker) : nm(who(p.characterId));
      return make(e, "escape", "ESCAPE", 2, [subject, t(" "), v("shook the heat"), t(` · escape #${p.successfulEscapes}`)], { ticker: p.ticker });
    }
    case "CAPTURE_SUCCEEDED": {
      const p = (e as GameEvent<"CAPTURE_SUCCEEDED">).payload;
      const subject = p.ticker ? tk(p.ticker) : nm(who(p.characterId));
      const parts = [subject, t(" "), v("CAPTURED"), t(" by "), nm(p.captorName)];
      if (p.ticker) parts.push(t(": "), cash(p.spotBeforeCapture), t(" → "), cash(0));
      if (p.bounty > 0) parts.push(t(" · bounty "), cash(p.bounty));
      if (p.doubleCross) parts.push(t(" · "), v("DOUBLE-CROSS"), t(` in crew ${p.crewName}: took it all`));
      else if (p.shares && p.shares.length > 1) parts.push(t(` · split ${p.shares.length} ways in crew ${p.crewName}`));
      return make(e, "capture", "CAPTURED", 3, parts, { ticker: p.ticker, agentId: s.npcs[p.captorId] ? p.captorId : null });
    }
    case "INCIDENT_OCCURRED": {
      const p = (e as GameEvent<"INCIDENT_OCCURRED">).payload;
      const seen = p.observerIds ?? [];
      const parts = rich(String(p.description || "Incident").trim().replace(/\.$/, ""), s);
      parts.push(t(" · seen by "));
      if (seen.length === 0) parts.push(t("nobody"));
      else if (seen.length === 1) parts.push(nm(who(seen[0])));
      else parts.push(nm(`${seen.length} NPCs`));
      return make(e, "job", p.kind === "MISSION" ? "JOB" : "SIGHTING", 2, parts, { ticker: p.ticker });
    }
    case "LISTING_OPENED": {
      const p = (e as GameEvent<"LISTING_OPENED">).payload;
      return make(e, "listing", "LISTED", 2, [tk(p.ticker), t(" "), v("LISTED"), t(" at "), cash(p.spotPrice), t(" · "), nm(p.nickname), t(" is now tradable")], { ticker: p.ticker });
    }
    case "MISSION_SPAWNED": {
      const p = (e as GameEvent<"MISSION_SPAWNED">).payload;
      return make(e, "job", "NEW JOB", 1, [t("New job on the street: "), nm(p.label)]);
    }
    case "PLAYER_JOINED": {
      const p = (e as GameEvent<"PLAYER_JOINED">).payload;
      if (p.reconnect) return null;
      return make(e, "system", "JOINED", 1, [nm(p.nickname), t(" joined as "), v(p.role)]);
    }
    case "MODE_CHANGED": {
      const p = (e as GameEvent<"MODE_CHANGED">).payload;
      return make(e, "system", "MODE", 2, [t("Decision mode switched: "), nm(String(p.previousMode).toUpperCase()), t(" → "), v(String(p.mode).toUpperCase())]);
    }
    case "ROOM_RESET": {
      const p = (e as GameEvent<"ROOM_RESET">).payload;
      return make(e, "system", "RESET", 2, [t("Seeded reset · new run · seed "), nm(String(p.seed))]);
    }
    case "POLICY_INGESTED": {
      const p = (e as GameEvent<"POLICY_INGESTED">).payload;
      return make(e, "system", "POLICY", 1, [t("Policy document ingested: "), nm(p.documentTitle), t(` · ${p.factCount} facts`)]);
    }
    default: return null;
  }
}

interface RingEntry { h: Headline; shows: number; lastShownAt: number }
interface Queued { h: Headline; at: number; alerted: boolean }

/** Follows the store's event log by serverSequence. Events present at start() never fire one-shot signals. */
export class TapeFeed {
  private lastSeq = Number.NEGATIVE_INFINITY;
  private runId = "";
  private roomCode = "";
  private fresh: Queued[] = [];
  private ring: RingEntry[] = [];
  private latestHeadline: Headline | null = null;
  private latestBeat: Headline | null = null;
  private readonly ctx: Ctx = { decisions: new Map(), tradeCauses: new Set() };
  private readonly subs = new Set<(s: TapeSignal) => void>();
  private unsub: (() => void) | null = null;

  /** Construct during the first render: the log as it is right now is the "already present at mount" line. */
  constructor() { this.seed(useGame.getState()); }

  start(): void {
    if (this.unsub) return;
    this.unsub = useGame.subscribe((s, prev) => {
      if (s.events !== prev.events || s.runId !== prev.runId || s.roomCode !== prev.roomCode) this.ingest(s);
    });
    this.ingest(useGame.getState()); // anything that landed between render and subscribe
  }
  stop(): void { this.unsub?.(); this.unsub = null; }

  on(fn: (s: TapeSignal) => void): () => void { this.subs.add(fn); return () => { this.subs.delete(fn); }; }
  private emit(sig: TapeSignal): void { for (const fn of [...this.subs]) { try { fn(sig); } catch (err) { console.error("[tape] signal handler failed", err); } } }

  /** Everything already in the log is history: it can be recapped as a headline but never replays an effect. */
  private seed(s: GameState): void {
    this.runId = s.runId; this.roomCode = s.roomCode;
    this.fresh = []; this.ring = []; this.latestHeadline = null; this.latestBeat = null; this.ctx.decisions.clear(); this.ctx.tradeCauses.clear();
    let max = Number.NEGATIVE_INFINITY;
    for (const e of s.events) {
      if (e.serverSequence > max) max = e.serverSequence;
      if (e.runId !== s.runId) continue;
      this.remember(e);
      const h = headlineFor(e, s, this.ctx);
      if (h) { this.latestHeadline = h; if (h.priority >= 2) { this.latestBeat = h; this.pushRing(h, 0); } }
    }
    this.lastSeq = max;
  }

  private remember(e: GameEvent): void {
    if (e.type !== "DECISION_RESOLVED") return;
    const p = (e as GameEvent<"DECISION_RESOLVED">).payload;
    const real = p.provider != null;
    this.ctx.decisions.set(e.eventId, { source: p.decisionSource, confidence: real ? p.confidence : null, latencyMs: real ? p.latencyMs : null, agentId: p.agentId });
    if (this.ctx.decisions.size > 160) { const first = this.ctx.decisions.keys().next(); if (!first.done) this.ctx.decisions.delete(first.value); }
    if (this.ctx.tradeCauses.size > 160) { const first = this.ctx.tradeCauses.values().next(); if (!first.done) this.ctx.tradeCauses.delete(first.value); }
  }

  private pushRing(h: Headline, shows: number): void {
    if (this.ring.some((r) => r.h.id === h.id)) return;
    this.ring.push({ h, shows, lastShownAt: shows ? Date.now() : 0 });
    if (this.ring.length > RING_MAX) this.ring.splice(0, this.ring.length - RING_MAX);
  }

  private ingest(s: GameState): void {
    if (s.roomCode !== this.roomCode) { this.seed(s); this.emit({ type: "reset" }); return; }
    if (s.runId !== this.runId) {
      // Seeded reset: cut the old story. serverSequence keeps growing across resets, so the cursor stays valid.
      this.runId = s.runId; this.fresh = []; this.ring = []; this.latestHeadline = null; this.latestBeat = null; this.ctx.decisions.clear(); this.ctx.tradeCauses.clear();
      this.emit({ type: "reset" });
    }
    const evs = s.events;
    let i = evs.length;
    while (i > 0 && evs[i - 1]!.serverSequence > this.lastSeq) i--;
    if (i === evs.length) return;
    const newestAt = evs[evs.length - 1]!.simTimeMs;
    for (; i < evs.length; i++) {
      const e = evs[i]!;
      if (e.serverSequence <= this.lastSeq) continue;
      this.lastSeq = e.serverSequence;
      if (e.runId !== s.runId) continue;
      this.handle(e, s, newestAt - e.simTimeMs <= STALE_MS);
    }
  }

  private handle(e: GameEvent, s: GameState, live: boolean): void {
    this.remember(e);
    const h = headlineFor(e, s, this.ctx);
    if (h) {
      this.latestHeadline = h;
      if (h.priority >= 2) this.latestBeat = h;
      if (!live) { if (h.priority >= 2) this.pushRing(h, 0); }
      else {
        const alerted = h.priority === 3 && h.tone !== "capture";
        const item: Queued = { h, at: Date.now(), alerted };
        // The tape tells the story in the order it happened. Only a capture jumps the queue: it ends the story.
        if (h.tone === "capture") this.fresh.unshift(item); else this.fresh.push(item);
        // Backlog: minor items give way first, then items already shown in full as an alert, then the oldest
        // story beat. Everything but the minor items stays available as a recap.
        while (this.fresh.length > FRESH_MAX) {
          let k = this.fresh.findIndex((x) => x.h.priority === 1);
          if (k < 0) k = this.fresh.findIndex((x) => x.alerted);
          if (k < 0) k = this.fresh.findIndex((x) => x.h.tone !== "capture");
          const [gone] = this.fresh.splice(k < 0 ? this.fresh.length - 1 : k, 1);
          if (gone && gone.h.priority >= 2) this.pushRing(gone.h, 0);
        }
        this.emit({ type: "headline", headline: h });
        if (alerted) this.emit({ type: "alert", headline: h });
      }
    }
    if (!live) return;
    switch (e.type) {
      case "MARKET_PRICE_UPDATED": {
        const p = (e as GameEvent<"MARKET_PRICE_UPDATED">).payload;
        this.emit({ type: "price", ticker: p.ticker, dir: p.spotPrice > p.previousSpotPrice ? 1 : p.spotPrice < p.previousSpotPrice ? -1 : 0, from: p.previousSpotPrice, to: p.spotPrice, reason: p.reason });
        break;
      }
      case "AGENT_MESSAGE_SENT": this.emit({ type: "message", kind: (e as GameEvent<"AGENT_MESSAGE_SENT">).payload.kind }); break;
      case "BULLETIN_PUBLISHED": this.emit({ type: "message", kind: "BULLETIN" }); break;
      case "DECISION_RESOLVED": this.emit({ type: "decision", source: (e as GameEvent<"DECISION_RESOLVED">).payload.decisionSource }); break;
      case "TRADE_EXECUTED": { const p = (e as GameEvent<"TRADE_EXECUTED">).payload; this.emit({ type: "trade", ticker: p.ticker, side: p.side }); break; }
      case "CAPTURE_SUCCEEDED": {
        const p = (e as GameEvent<"CAPTURE_SUCCEEDED">).payload;
        if (p.ticker) this.emit({ type: "capture", ticker: p.ticker, captorName: p.captorName, from: p.spotBeforeCapture, bounty: p.bounty, seq: e.serverSequence });
        break;
      }
      default: break;
    }
  }

  /** True when the next unseen headline is a capture (it takes the very next slot on the belt). */
  hasBreaking(): boolean { return this.fresh[0]?.h.tone === "capture"; }
  backlog(): number { return this.fresh.length; }
  /** Most recent story beat (falls back to the most recent headline of any weight). Used by the static strip. */
  latest(): Headline | null { return this.latestBeat ?? this.latestHeadline; }

  /** Next unseen headline, in causal (sequence) order. While the tape is behind, minor items and items that were
   *  already shown in full as an alert step aside; story beats are never skipped here. */
  takeFresh(): NewsItem | null {
    for (;;) {
      const q = this.fresh.shift();
      if (!q) return null;
      const behind = this.fresh.length, waited = Date.now() - q.at;
      const stepAside = (behind >= 2 && (q.h.priority === 1 || q.alerted)) || (behind >= 1 && waited > EXPIRE_MS && q.h.tone !== "capture");
      if (stepAside) { if (q.h.priority >= 2) this.pushRing(q.h, 0); continue; }
      if (q.h.priority >= 2) this.pushRing(q.h, 1);
      return { h: q.h, fresh: true, late: waited > LATE_MS };
    }
  }

  /** A recent story beat to show again (clearly marked as earlier news by the cell). */
  takeRecap(): NewsItem | null {
    const now = Date.now();
    let best: RingEntry | null = null;
    for (const r of this.ring) {
      if (r.shows >= RECAP_MAX_SHOWS || now - r.lastShownAt < RECAP_GAP_MS) continue;
      // fewest showings first; ties replay the story in the order it happened
      if (!best || r.shows < best.shows || (r.shows === best.shows && r.h.seq < best.h.seq)) best = r;
    }
    if (!best) return null;
    best.shows++; best.lastShownAt = now;
    return { h: best.h, fresh: false, late: false };
  }
}
