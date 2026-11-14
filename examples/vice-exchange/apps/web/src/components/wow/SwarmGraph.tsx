/**
 * SwarmGraph — the live picture of the agent swarm: who saw what, who told whom, who decided what, and what it did
 * to the market. It reads the zustand store directly (no props) and never subscribes to the 10 Hz world tick.
 *
 *  - Steady state (nodes, thinking rings, last-decision stamps, pursuit lines, counted edges) is rendered from narrow
 *    store selectors that return change signatures, so unrelated store updates do not re-render it.
 *  - One-shot effects (message comets, bulletin ripples, trades, eye flashes, capture strikes) are derived from store
 *    events by serverSequence in a store subscription and played on a Canvas 2D layer (SwarmGraphFx). Events that
 *    were already in the store when the component mounted, or that arrive as history after a join or reset, are
 *    never replayed.
 *  - Honesty: the word DREX only ever labels decisions whose decisionSource is "drex"; confidence is drawn only when
 *    the trace carries one; V$ are fictional credits.
 *
 * Mount: <SwarmGraph /> inside any element with a definite size. It fills 100% x 100% of its parent.
 */
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, ReactElement, ReactNode } from "react";
import type { AgentView, CharacterView, DecisionSource, DecisionTrace, GameEvent, ListingView, MessageKind, MetricsView, NpcRole } from "@vice/contracts";
import { useGame } from "../../state/gameStore";
import type { GameState } from "../../state/gameStore";
import { arrowPath, buildCurve, createSwarmFx, curveAt, curvePath } from "./SwarmGraphFx";
import type { Curve, FxGeometry, LaneGeom, NodeGeom, SwarmFx } from "./SwarmGraphFx";
import "./SwarmGraph.css";

// ---------- visual language (same role and message colours as the city view) ----------
const ROLE_COLOR: Record<NpcRole, string> = { WITNESS: "#ffd166", INFORMANT: "#c77dff", TRADER: "#5dfdcb", POLICE: "#4cc9f0" };
const ROLE_PLURAL: Record<NpcRole, string> = { WITNESS: "WITNESSES", INFORMANT: "INFORMANTS", TRADER: "TRADERS", POLICE: "POLICE" };
const KIND_COLOR: Record<MessageKind, string> = { REPORT: "#4cc9f0", GOSSIP: "#ff5fa2", RELAY: "#c77dff", BULLETIN: "#ffd166" };
const C = {
  outlaw: "#ff5fa2", wanted: "#ff4d6d", captured: "#7b7896", exchange: "#2ff3e0", bulletin: "#ffd166", buy: "#3ee68a", sell: "#ff5468",
  pending: "#2ff3e0", ink: "#070812", glass: "#0b0c18", text: "#e9ebff", dim: "#8d92bd", soft: "#c6c9ee", heat: "#ffb703", mint: "#5dfdcb",
};
const EXCHANGE = "EXCHANGE", BULLETIN = "BULLETIN", HUMAN = "HUMAN_ORDERS";

interface SrcStyle { word: string; letter: string; color: string; cls: string; text: string; note: string }
/** Honest source labels. Only decisionSource "drex" is ever called DREX; everything else looks and reads differently. */
function srcStyle(src: DecisionSource | null): SrcStyle {
  switch (src) {
    case "drex": return { word: "DREX", letter: "D", color: "#3ee68a", cls: "drex", text: "#04130b", note: "live model call" };
    case "meta": return { word: "META", letter: "M", color: "#a56bff", cls: "meta", text: "#dccbff", note: "live model call" };
    case "scripted": return { word: "SCRIPTED", letter: "S", color: "#8d92bd", cls: "scripted", text: "#b4b8dc", note: "fixed rule, no model was called" };
    case "fallback": return { word: "FALLBACK", letter: "F", color: "#ffb347", cls: "fallback", text: "#ffb347", note: "safe rule, the model gave no usable answer" };
    case "drex_low_confidence_guardrail": return { word: "GUARDRAIL", letter: "G", color: "#ffb347", cls: "guard", text: "#ffb347", note: "the model was unsure, so the safe action was applied" };
    case "replay": return { word: "REPLAY", letter: "R", color: "#8d92bd", cls: "replay", text: "#b4b8dc", note: "recorded decision" };
    default: return { word: "PENDING", letter: "…", color: "#2ff3e0", cls: "pending", text: "#2ff3e0", note: "waiting for the answer" };
  }
}
const LEGEND_SOURCES: DecisionSource[] = ["drex", "meta", "scripted", "fallback", "drex_low_confidence_guardrail"];
const LEGEND_KINDS: MessageKind[] = ["REPORT", "GOSSIP", "RELAY", "BULLETIN"];

const shortName = (name: string): string => { const parts = name.trim().split(/\s+/); return parts[parts.length - 1] ?? name; };
const vd = (n: number): string => `V$${Math.round(n).toLocaleString("en-US")}`;
const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v));
const r1 = (n: number): number => Math.round(n * 10) / 10;

// ---------- text measurement (pills are sized from the real font, not from guesses) ----------
let fontFamily = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
let measureCtx: CanvasRenderingContext2D | null | undefined;
const widthCache = new Map<string, number>();
/** Width of `text` in viewBox units. Measured at the real size: system fonts track wider at small sizes. */
function tw(text: string, size: number, weight = 800, spacingEm = 0): number {
  const key = `${weight}|${size}|${text}`;
  let w = widthCache.get(key);
  if (w === undefined) {
    if (measureCtx === undefined) measureCtx = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
    if (measureCtx) { measureCtx.font = `${weight} ${size}px ${fontFamily}`; w = measureCtx.measureText(text).width * 1.015; }
    else w = text.length * 0.66 * size;
    if (widthCache.size > 4000) widthCache.clear();
    widthCache.set(key, w);
  }
  return w + spacingEm * size * text.length;
}
/** Width of the one-row legend, used to decide between one and two legend rows. */
function legendWidth(): number {
  const fs = 12;
  const kinds = LEGEND_KINDS.reduce((a, k) => a + 19 + tw(k, fs, 700, 0.05) + 13, 0);
  const extra = 11 + tw("BUY", fs, 700, 0.05) + 9 + 11 + tw("SELL", fs, 700, 0.05) + 13 + 22 + tw("PURSUIT", fs, 700, 0.05) + 13;
  const src = tw("DECIDED BY", 10.5, 700, 0.1) + 9 + LEGEND_SOURCES.reduce((a, k) => a + 17 + tw(srcStyle(k).word, fs, 700, 0.05) + 12, 0) - 12;
  return kinds + extra + src + 22;
}

// ---------- narrow store selectors: a signature string changes only when something this graph draws changes ----------
function signature<T>(pick: (s: GameState) => T, sig: (v: T) => string): (s: GameState) => string {
  let last: T | undefined, out = "", primed = false;
  return (s) => { const v = pick(s); if (!primed || v !== last) { primed = true; last = v; out = sig(v); } return out; };
}
const selNpcs = signature((s) => s.npcs, (npcs) => Object.values(npcs).map((n) => [
  n.id, n.role, n.name, n.activity, n.targetCharacterId ?? "", n.pendingDecisionId ?? "", n.lastDecision?.id ?? "", n.lastDecision?.status ?? "",
  n.lastDecision?.chosenAction ?? "", n.lastDecision?.decisionSource ?? "", n.lastDecision?.confidence ?? "", n.memories.length, n.contacts.join("+"),
].join("|")).join(";"));
const selChars = signature((s) => s.characters, (chars) => Object.values(chars).map((c) => [
  c.id, c.nickname, c.role, c.ticker ?? "", c.status, c.wanted ? 1 : 0, Math.round(c.heat / 3), c.spawnTimeMs, c.captureTimeMs ?? "",
].join("|")).join(";"));
const selListings = signature((s) => s.listings, (ls) => Object.values(ls).map((l) => [l.ticker, l.status, l.spotPrice, l.characterId, l.openedAtMs].join("|")).join(";"));
const selMetrics = signature((s) => s.metrics, (m) => (m ? [
  m.runId, m.messages.sent, m.messages.delivered, m.messages.maxHops, m.decisions.total, JSON.stringify(m.decisions.bySource),
  m.trades.executed, m.trades.byNpc, m.trades.byHuman, m.edges.map((e) => `${e.from}>${e.to}>${e.kind}:${e.count}`).join(","),
].join("|") : ""));
const selTrades = signature((s) => s.trades, (ts) => `${ts.length}:${ts.length ? ts[ts.length - 1]!.tradeId : ""}`);

// ---------- layout ----------
type NodeKind = "npc" | "outlaw" | "exchange" | "bulletin" | "anchor";
interface GNode extends NodeGeom { id: string; kind: NodeKind; role?: NpcRole }
interface Box { x0: number; y0: number; x1: number; y1: number }
interface Layout {
  cw: number; ch: number; s: number; vw: number; vh: number;
  /** Wide and short containers put each pair of NPCs side by side. */
  inline: boolean;
  /** Enough pixels for secondary text. */
  detail: boolean;
  /** Enough room between columns to spell the decision source out on the stamp. */
  wide: boolean;
  pad: number; headerH: number; legendRows: number; legendY: number; gTop: number; gBot: number;
  street: Box; main: Box; law: Box; lawY: number; headY: number;
  nodes: Map<string, GNode>;
  cols: Array<{ x: number; role: NpcRole }>;
  outlawLabels: boolean; boardRows: number;
  curve(a: string, b: string, bend?: number): Curve | null;
}

function computeLayout(cw: number, ch: number, npcs: ReadonlyArray<{ id: string; role: NpcRole }>, outlaws: ReadonlyArray<string>): Layout {
  const w = Math.max(cw, 60), h = Math.max(ch, 60);
  const rs = Math.min(w / 600, h / 400), ri = Math.min(w / 1000, h / 290);
  const inline = ri > rs;
  const raw = Math.max(rs, ri);
  // Grow sub-linearly past the minimum design size: big screens get both larger type and more room.
  const s = raw <= 0.7 ? raw : 0.92 * Math.pow(raw, 0.766);
  const vw = w / s, vh = h / s;
  const pad = 8, headerH = inline ? 38 : 42;
  const legendRows = legendWidth() + pad * 2 <= vw ? 1 : 2;
  const legendH = legendRows * 19 + 6;
  const gTop = headerH + 2, gBot = vh - legendH - 4, H = gBot - gTop;
  const streetW = clamp(vw * 0.15, 92, 150);
  const street: Box = { x0: pad, y0: gTop, x1: pad + streetW, y1: gBot };
  const lx0 = street.x1 + 6, lx1 = vw - pad, LW = lx1 - lx0;
  const main: Box = { x0: lx0, y0: gTop, x1: lx1, y1: gTop + H * (inline ? 0.57 : 0.63) };
  const law: Box = { x0: lx0, y0: main.y1 + 6, x1: lx1, y1: gBot };
  const headH = 17, rNpc = inline ? 19 : 21;
  const nodes = new Map<string, GNode>();

  const byRole: Record<NpcRole, string[]> = { WITNESS: [], INFORMANT: [], TRADER: [], POLICE: [] };
  for (const n of [...npcs].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) byRole[n.role].push(n.id);
  const frac: Record<"WITNESS" | "INFORMANT" | "TRADER", number> = inline
    ? { WITNESS: 0.125, INFORMANT: 0.405, TRADER: 0.685 } : { WITNESS: 0.115, INFORMANT: 0.375, TRADER: 0.635 };
  const cols: Array<{ x: number; role: NpcRole }> = [];
  for (const role of ["WITNESS", "INFORMANT", "TRADER"] as const) {
    const ids = byRole[role], n = ids.length, cx = lx0 + LW * frac[role];
    cols.push({ x: cx, role });
    ids.forEach((id, i) => {
      let x = cx, y: number;
      if (inline) { x = cx + (i - (n - 1) / 2) * LW * 0.13; y = main.y0 + headH + 6 + rNpc; }
      else { const top = main.y0 + headH, span = main.y1 - top; y = top + span * (n === 1 ? 0.55 : 0.31 + (0.48 * i) / (n - 1)); }
      nodes.set(id, { id, kind: "npc", role, x, y, r: rNpc });
    });
  }
  const lawY = inline ? law.y0 + 6 + rNpc : law.y0 + headH + (law.y1 - law.y0 - headH) * 0.5;
  byRole.POLICE.forEach((id, i, all) => {
    const a = inline ? 0.26 : 0.245, span = inline ? 0.28 : 0.26;
    nodes.set(id, { id, kind: "npc", role: "POLICE", x: lx0 + LW * (all.length === 1 ? a + span / 2 : a + (span * i) / (all.length - 1)), y: lawY, r: rNpc });
  });
  const exR = inline ? 24 : 28;
  const rowA = main.y0 + headH + (main.y1 - main.y0 - headH) * 0.31, rowB = main.y0 + headH + (main.y1 - main.y0 - headH) * 0.79;
  const ex: GNode = { id: EXCHANGE, kind: "exchange", x: lx0 + LW * (inline ? 0.925 : 0.895), y: inline ? main.y0 + headH + 6 + exR : (rowA + rowB) / 2 - 16, r: exR };
  nodes.set(EXCHANGE, ex);
  nodes.set(BULLETIN, { id: BULLETIN, kind: "bulletin", x: lx0 + LW * (inline ? 0.83 : 0.765), y: lawY, r: inline ? 22 : 25 });
  nodes.set(HUMAN, { id: HUMAN, kind: "anchor", x: vw + 16, y: ex.y - ex.r * 0.9, r: 0 });

  const oTop = street.y0 + headH + (inline ? 22 : 30), oBot = street.y1 - (inline ? 26 : 34), on = outlaws.length;
  outlaws.forEach((id, i) => nodes.set(id, { id, kind: "outlaw", x: (street.x0 + street.x1) / 2, y: oTop + ((i + 0.5) / on) * (oBot - oTop), r: inline ? 19 : 22 }));
  const outlawLabels = on <= 1 || (oBot - oTop) / on >= 62;
  const boardRows = Math.max(0, Math.min(3, Math.floor((main.y1 - (ex.y + ex.r + 20)) / 13.5)));

  // Edge routing: pick the gentlest bend that keeps clear of every other node and its labels.
  const solid = [...nodes.values()].filter((n) => n.kind !== "anchor");
  const midY = (gTop + gBot) / 2;
  const cache = new Map<string, Curve | null>();
  const route = (a: GNode, b: GNode): Curve => {
    let best = buildCurve(a, b, 0.06), bestScore = -Infinity;
    for (const bend of [0.06, -0.06, 0.13, -0.13, 0.21, -0.21, 0.3, -0.3]) {
      const c = buildCurve(a, b, bend);
      let clear = 60;
      for (let i = 1; i <= 9; i++) {
        const p = curveAt(c, i / 10);
        for (const o of solid) {
          if (o === a || o === b) continue;
          clear = Math.min(clear, Math.hypot(p.x - o.x, p.y - o.y) - o.r - 5);
          clear = Math.min(clear, Math.hypot(p.x - o.x, p.y - (o.y + o.r + 12)) - 15);                       // name under the node
          clear = Math.min(clear, Math.hypot(p.x - o.x, p.y - (inline ? o.y + o.r + 30 : o.y - o.r - 15)) - 13); // decision stamp
        }
        if (p.y < gTop + 3 || p.y > gBot - 3 || p.x < 3 || p.x > vw - 3) clear = Math.min(clear, -10);
      }
      const score = Math.min(clear, 22) - Math.abs(bend) * 46 + Math.abs(c.cy - midY) * 0.004;
      if (score > bestScore) { bestScore = score; best = c; }
    }
    return best;
  };
  const curve = (a: string, b: string, bend?: number): Curve | null => {
    const key = `${a}>${b}>${bend ?? "auto"}`;
    const hit = cache.get(key);
    if (hit !== undefined) return hit;
    const A = nodes.get(a), B = nodes.get(b);
    const c = !A || !B || a === b ? null : bend !== undefined ? buildCurve(A, B, bend) : route(A, B);
    cache.set(key, c);
    return c;
  };
  const colGap = LW * (inline ? 0.13 : 0.26);
  return {
    cw: w, ch: h, s, vw, vh, inline, detail: s >= 0.95, wide: colGap >= 172, pad, headerH, legendRows, legendY: vh - legendH - 1, gTop, gBot,
    street, main, law, lawY, headY: main.y0 + 12.5, nodes, cols, outlawLabels, boardRows, curve,
  };
}

// ---------- event-derived, non-store state ----------
interface Stamp { decisionId: string; action: string | null; source: DecisionSource | null; confidence: number | null; rejected: boolean; fresh: boolean }
interface EvtState {
  pending: Map<string, { decisionId: string; offered: string[]; at: number }>;
  resolved: Set<string>;
  stamps: Map<string, Stamp & { at: number }>;
  seen: Map<string, { id: string; at: number }>;
}
interface Pop { id: number; kind: "pill" | "eye"; x: number; y: number; text: string; color: string; size: number; dur: number; strong: boolean; slot: number }

function stampOf(n: AgentView, E: EvtState, now: number): Stamp | null {
  const ld = n.lastDecision, ev = E.stamps.get(n.id);
  if (ld) { const s = E.seen.get(n.id); if (!s || s.id !== ld.id) E.seen.set(n.id, { id: ld.id, at: now }); }
  const fromStore = (t: DecisionTrace, fresh: boolean): Stamp => ({ decisionId: t.id, action: t.chosenAction, source: t.decisionSource, confidence: t.confidence, rejected: t.status === "REJECTED", fresh });
  const fresh = !!ev && ev.fresh && now - ev.at < 9000;
  if (ev && ld && ld.id === ev.decisionId) return fromStore(ld, fresh);
  if (ev && (!ld || ev.at >= (E.seen.get(n.id)?.at ?? 0))) return { ...ev, fresh };
  return ld ? fromStore(ld, false) : null;
}
function isPending(n: AgentView, E: EvtState, now: number): boolean {
  if (n.pendingDecisionId && !E.resolved.has(n.pendingDecisionId)) return true;
  const p = E.pending.get(n.id);
  return !!p && now - p.at < 12000;
}
function offeredFor(n: AgentView, E: EvtState): string[] {
  const p = E.pending.get(n.id);
  if (p) return p.offered;
  const t = n.pendingDecisionId ? n.recentDecisions.find((d) => d.id === n.pendingDecisionId) : undefined;
  return t ? Object.keys(t.offeredActions) : [];
}

/**
 * The recorded causal chain behind an NPC trade, as graph node ids from the outlaw to the exchange. It follows
 * causationId upstream through the events still in the store, so it only ever shows links that were recorded.
 */
function causalChain(trade: GameEvent, events: GameEvent[], traderId: string): string[] {
  const byId = new Map(events.map((x) => [x.eventId, x]));
  const out: string[] = [EXCHANGE, traderId];
  const push = (id: string | null | undefined): void => { if (id && out[out.length - 1] !== id) out.push(id); };
  let cur = trade.causationId ? byId.get(trade.causationId) : undefined;
  for (let guard = 0; cur && guard < 40; guard++) {
    if (cur.type === "AGENT_MESSAGE_DELIVERED") push((cur as GameEvent<"AGENT_MESSAGE_DELIVERED">).payload.recipientId);
    else if (cur.type === "AGENT_MESSAGE_SENT") push((cur as GameEvent<"AGENT_MESSAGE_SENT">).payload.senderId);
    else if (cur.type === "BULLETIN_PUBLISHED") { push(BULLETIN); push((cur as GameEvent<"BULLETIN_PUBLISHED">).payload.issuedBy); }
    else if (cur.type === "NPC_OBSERVED") { const o = (cur as GameEvent<"NPC_OBSERVED">).payload; push(o.npcId); push(o.subjectCharacterId); break; }
    else if (cur.type === "INCIDENT_OCCURRED") { push((cur as GameEvent<"INCIDENT_OCCURRED">).payload.characterId); break; }
    cur = cur.causationId ? byId.get(cur.causationId) : undefined;
  }
  return out.reverse();
}

// ---------- small SVG pieces ----------
function Pill({ text, color, size, strong }: { text: string; color: string; size: number; strong?: boolean }): ReactElement {
  const w = tw(text, size, 800, 0.05) + size * 1.35, h = size * 1.66;
  return (
    <>
      <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={h / 2} fill={C.glass} fillOpacity={0.93} stroke={color} strokeWidth={strong ? 1.7 : 1.1} />
      {strong && <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={h / 2} fill="none" stroke={color} strokeOpacity={0.25} strokeWidth={5} />}
      <text textAnchor="middle" dy=".36em" fontSize={size} fontWeight={800} letterSpacing=".05em" fill={strong ? "#ffffff" : color}>{text}</text>
    </>
  );
}

function shapeOf(role: NpcRole, r: number, fill: string, stroke: string): ReactElement {
  const q = r / 1.27, common = { className: "sg-shape", fill, stroke, strokeWidth: 2.2, strokeLinejoin: "round" as const };
  if (role === "WITNESS") return <circle r={r1(q * 1.1)} {...common} />;
  if (role === "TRADER") { const k = r1(q * 0.94); return <rect x={-k} y={-k} width={k * 2} height={k * 2} rx={r1(q * 0.24)} {...common} />; }
  if (role === "INFORMANT") { const d = r1(q * 1.25); return <path d={`M0 ${-d}L${d} 0L0 ${d}L${-d} 0Z`} {...common} />; }
  const a = q;
  return <path d={`M0 ${r1(-a * 1.2)}L${r1(a * 1.04)} ${r1(-a * 0.74)}L${r1(a * 0.98)} ${r1(a * 0.16)}Q${r1(a * 0.8)} ${r1(a * 0.9)} 0 ${r1(a * 1.27)}Q${r1(-a * 0.8)} ${r1(a * 0.9)} ${r1(-a * 0.98)} ${r1(a * 0.16)}L${r1(-a * 1.04)} ${r1(-a * 0.74)}Z`} {...common} />;
}
const STAR = (() => { let d = ""; for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + (i * Math.PI) / 5, r = i % 2 ? 3.3 : 7.6; d += `${i ? "L" : "M"}${r1(Math.cos(a) * r)} ${r1(Math.sin(a) * r)}`; } return `${d}Z`; })();
function glyphOf(role: NpcRole, r: number, color: string): ReactElement {
  const k = r / 21, line = { fill: "none", stroke: color, strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (role === "WITNESS") return <g transform={`scale(${r1(k)})`}><path d="M-9.5 0Q0 -8 9.5 0Q0 8 -9.5 0Z" {...line} /><circle r={2.9} fill={color} /></g>;
  if (role === "INFORMANT") return <g transform={`scale(${r1(k)})`}><path d="M-8 -6.4H8V3.4H0.6L-4.2 7.4V3.4H-8Z" {...line} /><circle cx={-3.6} cy={-1.5} r={1} fill={color} /><circle cx={0} cy={-1.5} r={1} fill={color} /><circle cx={3.6} cy={-1.5} r={1} fill={color} /></g>;
  if (role === "TRADER") return <g transform={`scale(${r1(k)})`}><path d="M-8.6 5.6L-3 -0.6L1 3L8 -5.6" {...line} /><path d="M3.2 -5.6H8V-0.8" {...line} /></g>;
  return <g transform={`scale(${r1(k)}) translate(0 -0.6)`}><path d={STAR} fill={color} /></g>;
}
const HEX = (r: number): string => { let d = ""; for (let i = 0; i < 6; i++) { const a = -Math.PI / 2 + (i * Math.PI) / 3; d += `${i ? "L" : "M"}${r1(Math.cos(a) * r)} ${r1(Math.sin(a) * r)}`; } return `${d}Z`; };

/** Decision stamp: [source badge | ACTION]. The badge is filled solid green only for a real Drex decision. */
function StampPill({ st, wide, hatch }: { st: Stamp; wide: boolean; hatch: string }): ReactElement {
  const ss = srcStyle(st.source), action = (st.action ?? "none").toUpperCase();
  const h = 18, fa = 12, fb = 9.6;
  const wa = tw(action, fa, 800, 0.05) + 13, wb = wide ? tw(ss.word, fb, 800, 0.08) + 12 : h + 1;
  const w = wa + wb, x0 = -w / 2, xb = x0 + wb, rr = h / 2;
  const dash = ss.cls === "scripted" || ss.cls === "fallback" ? "3.2 2.4" : ss.cls === "replay" ? "1 2.6" : undefined;
  const badgeFill = ss.cls === "drex" ? ss.color : ss.cls === "meta" ? "rgba(165,107,255,.26)" : ss.cls === "guard" ? "rgba(255,179,71,.17)" : ss.cls === "fallback" ? `url(#${hatch})` : "none";
  return (
    <g className={`sg-stamp ${st.rejected ? "sg-stamp-void" : st.fresh ? "sg-stamp-fresh" : "sg-stamp-quiet"}`}>
      {ss.cls === "drex" && <rect x={x0} y={-rr} width={w} height={h} rx={rr} fill="none" stroke={ss.color} strokeOpacity={0.3} strokeWidth={5} />}
      <rect x={x0} y={-rr} width={w} height={h} rx={rr} fill={C.glass} fillOpacity={0.95} stroke={ss.color} strokeWidth={1.4} strokeDasharray={dash} />
      <path d={`M${r1(x0 + rr)} ${-rr}H${r1(xb)}V${rr}H${r1(x0 + rr)}A${rr} ${rr} 0 0 1 ${r1(x0 + rr)} ${-rr}Z`} fill={badgeFill} />
      {ss.cls !== "drex" && <line x1={r1(xb)} x2={r1(xb)} y1={-rr + 2.5} y2={rr - 2.5} stroke={ss.color} strokeOpacity={0.55} strokeWidth={1} />}
      <text x={r1(x0 + wb / 2 + (wide ? 1 : 0.6))} textAnchor="middle" dy=".36em" fontSize={wide ? fb : 10.5} fontWeight={900} letterSpacing={wide ? ".08em" : "0"} fill={ss.text}>{wide ? ss.word : ss.letter}</text>
      <text x={r1(xb + wa / 2 - 0.5)} textAnchor="middle" dy=".36em" fontSize={fa} fontWeight={800} letterSpacing=".05em" fill="#ffffff">{action}</text>
      {st.rejected && <line x1={r1(xb + 4)} x2={r1(x0 + w - 5)} y1={0} y2={0} stroke="#ffffff" strokeOpacity={0.8} strokeWidth={1.4} />}
    </g>
  );
}
function ThinkPill({ offered, wide }: { offered: string[]; wide: boolean }): ReactElement {
  const label = wide && offered.length ? offered.join(" / ") : "";
  const h = 18, wt = label ? tw(label, 10.5, 700, 0.02) + 8 : 0, w = 34 + wt, x0 = -w / 2;
  return (
    <g>
      <rect x={x0} y={-h / 2} width={w} height={h} rx={h / 2} fill={C.glass} fillOpacity={0.95} stroke={C.pending} strokeWidth={1.3} />
      <g className="sg-dots" transform={`translate(${r1(x0 + 17)} 0)`}>
        <circle cx={-6.5} r={2.1} fill={C.pending} /><circle cx={0} r={2.1} fill={C.pending} /><circle cx={6.5} r={2.1} fill={C.pending} />
      </g>
      {label && <text x={r1(x0 + 30)} dy=".36em" fontSize={10.5} fontWeight={700} letterSpacing=".02em" fill={C.pending}>{label}</text>}
    </g>
  );
}

// ---------- the component ----------
export default function SwarmGraph(): ReactElement {
  const uid = `sg${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const fxRef = useRef<SwarmFx | null>(null);
  const evtRef = useRef<EvtState>({ pending: new Map(), resolved: new Set(), stamps: new Map(), seen: new Map() });
  const popsRef = useRef<Pop[]>([]);
  const armed = useRef(false);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [reduced, setReduced] = useState<boolean>(() => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  const [hover, setHover] = useState<string | null>(null);
  const [, setVersion] = useState(0);

  // store slices (signatures keep unrelated updates, including the 10 Hz tick, from re-rendering the graph)
  const npcSig = useGame(selNpcs);
  const charSig = useGame(selChars);
  const listSig = useGame(selListings);
  const metricSig = useGame(selMetrics);
  const tradeSig = useGame(selTrades);
  const selectedId = useGame((s) => s.selectedAgentId);

  const npcs = useMemo<AgentView[]>(() => Object.values(useGame.getState().npcs).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)), [npcSig]);
  const outlaws = useMemo<CharacterView[]>(() => {
    const all = Object.values(useGame.getState().characters).filter((c) => c.role === "OUTLAW" && c.status !== "ARCHIVED");
    const live = all.filter((c) => c.status !== "CAPTURED");
    const gone = all.filter((c) => c.status === "CAPTURED").sort((a, b) => (b.captureTimeMs ?? 0) - (a.captureTimeMs ?? 0));
    return [...live, ...gone].slice(0, 4).sort((a, b) => a.spawnTimeMs - b.spawnTimeMs || (a.id < b.id ? -1 : 1));
  }, [charSig]);
  const hiddenOutlaws = useMemo(() => Object.values(useGame.getState().characters).filter((c) => c.role === "OUTLAW" && (c.status === "ACTIVE" || c.status === "WANTED")).length - outlaws.filter((c) => c.status !== "CAPTURED").length, [charSig, outlaws]);
  const listings = useMemo<ListingView[]>(() => Object.values(useGame.getState().listings).sort((a, b) => (a.status === b.status ? b.openedAtMs - a.openedAtMs : a.status === "ACTIVE" ? -1 : 1)), [listSig]);
  const metrics = useMemo<MetricsView | null>(() => useGame.getState().metrics, [metricSig]);
  const tradeCounts = useMemo(() => {
    const st = useGame.getState(), counts = new Map<string, number>();
    for (const t of st.trades) if (t.actorKind === "NPC") counts.set(t.actorId, (counts.get(t.actorId) ?? 0) + 1);
    return { counts, partial: (st.metrics?.trades.executed ?? 0) > st.trades.length };
  }, [tradeSig, metricSig]);

  const layoutKey = `${npcs.map((n) => `${n.id}:${n.role}`).join(",")}|${outlaws.map((c) => c.id).join(",")}`;
  const L = useMemo(() => computeLayout(size.w || 600, size.h || 400, npcs, outlaws.map((c) => c.id)), [size.w, size.h, layoutKey]);
  const layoutRef = useRef(L);
  layoutRef.current = L;

  // container size, font, reduced motion
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const fam = getComputedStyle(host).fontFamily;
    if (fam && fam !== fontFamily) { fontFamily = fam; widthCache.clear(); }
    const apply = (): void => {
      const r = host.getBoundingClientRect(), w = Math.round(r.width), h = Math.round(r.height);
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(host);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const on = (): void => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  useEffect(() => { armed.current = true; }, []);

  // canvas effects engine (created in a layout effect so it exists before the first geometry is pushed)
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const fx = createSwarmFx(canvas, typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
    fxRef.current = fx;
    return () => { fx.destroy(); fxRef.current = null; };
  }, []);
  useEffect(() => { fxRef.current?.setReduced(reduced); }, [reduced]);
  useLayoutEffect(() => {
    if (!size.w || !size.h) return;
    const lanes: LaneGeom[] = [
      { ...L.main, color: "#ff5fa2" }, { ...L.main, color: "#a56bff" }, { ...L.law, color: "#4cc9f0" }, { ...L.street, color: "#ff5fa2" },
    ];
    const g: FxGeometry = { cssW: L.cw, cssH: L.ch, scale: L.s, vw: L.vw, vh: L.vh, node: (id) => L.nodes.get(id) ?? null, curve: L.curve, lanes };
    fxRef.current?.setGeometry(g);
  }, [L, size.w, size.h]);

  // one-shot effects, derived from store events by serverSequence
  useEffect(() => {
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const bump = (): void => setVersion((v) => (v + 1) % 1e9);
    let popSeq = 1;
    const later = (fn: () => void, ms: number): void => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };
    const pop = (p: Omit<Pop, "id" | "slot"> & { slot?: number }, delay = 0): void => {
      const add = (): void => {
        const item: Pop = { ...p, id: popSeq++, slot: p.slot ?? -1 };
        popsRef.current = [...popsRef.current.slice(-13), item];
        bump();
        later(() => { popsRef.current = popsRef.current.filter((x) => x.id !== item.id); bump(); }, p.dur);
      };
      if (delay > 0) later(add, delay); else add();
    };
    /** A message tag looks for a spot beside its edge that covers no node, no label and no other tag. */
    const tagRects: Array<{ x0: number; y0: number; x1: number; y1: number; until: number }> = [];
    const freeSpot = (c: Curve, w: number, h: number, life: number): { x: number; y: number } => {
      const t = performance.now(), Lc = layoutRef.current;
      for (let i = tagRects.length - 1; i >= 0; i--) if (tagRects[i]!.until < t) tagRects.splice(i, 1);
      let first: { x: number; y: number } | null = null;
      for (const u of [0.5, 0.36, 0.64, 0.26, 0.74]) for (const side of [-1, 1]) {
        const a = curveAt(c, u), b = curveAt(c, Math.min(1, u + 0.02));
        let tx = b.x - a.x, ty = b.y - a.y; const d = Math.hypot(tx, ty) || 1; tx /= d; ty /= d;
        const off = (Math.abs(tx) * h) / 2 + (Math.abs(ty) * w) / 2 + 7;
        const x = clamp(a.x - ty * side * off, w / 2 + 4, Lc.vw - w / 2 - 4), y = a.y + tx * side * off;
        if (!first) first = { x, y };
        const r = { x0: x - w / 2 - 2, y0: y - h / 2 - 2, x1: x + w / 2 + 2, y1: y + h / 2 + 2 };
        let ok = r.y0 > Lc.gTop + 16 && r.y1 < Lc.gBot && !(r.x0 < Lc.law.x0 + 84 && r.y1 > Lc.main.y1 - 2 && r.y0 < Lc.law.y0 + 22);
        if (ok) for (const n of Lc.nodes.values()) {
          if (n.kind === "anchor") continue;
          const top = n.y - n.r - (Lc.inline ? 4 : 27), bot = n.y + n.r + (Lc.inline ? 42 : 20);
          if (r.x1 > n.x - 48 && r.x0 < n.x + 48 && r.y1 > top && r.y0 < bot) { ok = false; break; }
        }
        if (ok) for (const o of tagRects) if (r.x1 > o.x0 && r.x0 < o.x1 && r.y1 > o.y0 && r.y0 < o.y1) { ok = false; break; }
        if (ok) { tagRects.push({ ...r, until: t + life }); return { x, y }; }
      }
      const f = first ?? { x: c.cx, y: c.cy };
      tagRects.push({ x0: f.x - w / 2, y0: f.y - h / 2, x1: f.x + w / 2, y1: f.y + h / 2, until: t + life });
      return f;
    };
    /** Labels at the exchange stack upward instead of overlapping; a slot is reserved for the label's whole life. */
    const slotBusyUntil: number[] = [], slotWidth: number[] = [];
    let lastSweepAt = -1e9;
    /** Stacked layout: labels pile up above the exchange. Inline layout: they line up leftward along the lane top. */
    const exchangeSpot = (text: string, size: number, lifeMs: number): { x: number; y: number; slot: number } => {
      const t = performance.now(), Lc = layoutRef.current, ex = Lc.nodes.get(EXCHANGE);
      const w = tw(text, size, 800, 0.05) + size * 1.35 + 4, half = w / 2;
      let i = 0;
      while (i < 4 && (slotBusyUntil[i] ?? 0) > t) i++;
      if (i >= 4) i = 0;
      slotBusyUntil[i] = t + lifeMs; slotWidth[i] = w;
      if (!ex) return { x: Lc.vw / 2, y: Lc.gTop + 20, slot: i };
      if (!Lc.inline) return { x: clamp(ex.x, half + 8, Lc.vw - half - 8), y: ex.y - ex.r - 15 - i * 21, slot: i };
      let right = Lc.vw - 8;
      for (let k = 0; k < i; k++) right -= (slotWidth[k] ?? 0) + 6;
      return { x: Math.max(half + 8, right - half), y: ex.y - ex.r - 15, slot: i };
    };
    const reset = (): void => {
      fxRef.current?.clear();
      const E = evtRef.current; E.pending.clear(); E.resolved.clear(); E.stamps.clear(); E.seen.clear();
      popsRef.current = [];
      slotBusyUntil.length = 0; slotWidth.length = 0; tagRects.length = 0;
      for (const t of timers) clearTimeout(t);
      timers.clear();
      bump();
    };

    const handle = (fresh: GameEvent[], st: GameState): void => {
      const fx = fxRef.current, Lc = layoutRef.current, E = evtRef.current, now = performance.now();
      const newest = fresh[fresh.length - 1]!.simTimeMs, flood = fresh.length > 40;
      const node = (id: string | null | undefined): GNode | null => (id ? Lc.nodes.get(id) ?? null : null);
      /** An upward arc from a to b that is as high as `want` allows but never leaves the graph area. */
      const arcBend = (aId: string, bId: string, want: number): number => {
        const a = node(aId), b = node(bId);
        if (!a || !b) return -want;
        const d = Math.hypot(b.x - a.x, b.y - a.y) || 1, room = Math.max(8, Math.min(a.y, b.y) - Lc.gTop - 6);
        return -Math.min(want, (2 * room) / d);
      };
      let dirty = false;
      for (const e of fresh) {
        // A catch-up burst (resync after a gap) only animates its most recent moments.
        const animate = !!fx && (!flood || newest - e.simTimeMs < 2500);
        switch (e.type) {
          case "AGENT_MESSAGE_SENT": {
            const p = (e as GameEvent<"AGENT_MESSAGE_SENT">).payload;
            if (!animate || !fx) break;
            fx.flight(p.messageId, p.senderId, p.recipientId, KIND_COLOR[p.kind]);
            const c = Lc.curve(p.senderId, p.recipientId);
            if (c && Lc.detail) {
              const text = `${p.kind} · HOP ${p.hop}`, at = freeSpot(c, tw(text, 10.5, 800, 0.05) + 14.2, 17.5, 1500);
              pop({ kind: "pill", x: at.x, y: at.y, text, color: KIND_COLOR[p.kind], size: 10.5, dur: 1500, strong: false });
            }
            break;
          }
          case "AGENT_MESSAGE_DELIVERED": {
            const p = (e as GameEvent<"AGENT_MESSAGE_DELIVERED">).payload;
            if (animate && fx) fx.deliver(p.messageId, p.senderId, p.recipientId, KIND_COLOR[p.kind]);
            break;
          }
          case "BULLETIN_PUBLISHED": {
            const p = (e as GameEvent<"BULLETIN_PUBLISHED">).payload;
            if (!animate || !fx) break;
            const gold = KIND_COLOR.BULLETIN, from = node(p.issuedBy), hub = node(BULLETIN);
            if (from) fx.ring(p.issuedBy, { color: gold, from: from.r + 2, to: from.r + 30, dur: 560, width: 2.4 });
            fx.beam(p.issuedBy, BULLETIN, { color: gold, dur: 340, size: 8, tail: 90 });
            if (hub) fx.ring(BULLETIN, { color: gold, delay: 330, from: hub.r, to: Math.hypot(Lc.vw, Lc.vh) * 0.72, dur: 1900, count: 3, gap: 210, width: 2.6, alpha: 0.85 });
            p.recipientIds.forEach((id, i) => { if (id !== p.issuedBy && node(id)) fx.beam(BULLETIN, id, { color: gold, delay: 380 + i * 34, dur: 600, size: 5.4, tail: 84 }); });
            if (hub) {
              const who = p.ticker ?? st.characters[p.characterId]?.nickname ?? "SUSPECT";
              pop({ kind: "pill", x: hub.x, y: hub.y - hub.r - 16, text: `WANTED · ${who}`, color: C.wanted, size: 12.5, dur: 2600, strong: true }, 330);
            }
            break;
          }
          case "NPC_OBSERVED": {
            const p = (e as GameEvent<"NPC_OBSERVED">).payload;
            if (!animate || !fx) break;
            const n = node(p.npcId);
            if (p.subjectCharacterId && node(p.subjectCharacterId)) fx.beam(p.subjectCharacterId, p.npcId, { color: "#ffffff", dur: 340, size: 5, tail: 130 });
            if (n) {
              fx.ring(p.npcId, { color: "#ffffff", delay: 300, from: n.r, to: n.r + 20, dur: 460, width: 1.6, alpha: 0.8 });
              pop({ kind: "eye", x: n.x - n.r * 0.86, y: n.y - n.r * 0.86, text: p.factType, color: "#ffffff", size: 9, dur: 1500, strong: false }, 240);
            }
            break;
          }
          case "INCIDENT_OCCURRED": {
            const p = (e as GameEvent<"INCIDENT_OCCURRED">).payload, n = node(p.characterId);
            if (animate && fx && n) fx.ring(p.characterId, { color: C.outlaw, from: n.r, to: n.r + 64, dur: 950, count: 2, gap: 170, width: 2.4 });
            break;
          }
          case "MISSION_COMPLETED": {
            const p = (e as GameEvent<"MISSION_COMPLETED">).payload, n = node(p.characterId);
            if (!animate || !fx || !n) break;
            fx.ring(p.characterId, { color: C.mint, from: n.r, to: n.r + 30, dur: 700, width: 2 });
            if (Lc.detail) pop({ kind: "pill", x: Math.max(n.x, 52), y: n.y - n.r - 30, text: "JOB DONE", color: C.mint, size: 10.5, dur: 1500, strong: false });
            break;
          }
          case "DECISION_REQUESTED": {
            const p = (e as GameEvent<"DECISION_REQUESTED">).payload;
            if (!E.resolved.has(p.decisionId)) { E.pending.set(p.agentId, { decisionId: p.decisionId, offered: p.offeredActions, at: now }); dirty = true; }
            break;
          }
          case "DECISION_RESOLVED": {
            const p = (e as GameEvent<"DECISION_RESOLVED">).payload;
            if (E.pending.get(p.agentId)?.decisionId === p.decisionId) E.pending.delete(p.agentId);
            if (E.resolved.size > 400) E.resolved.clear();
            E.resolved.add(p.decisionId);
            E.stamps.set(p.agentId, { decisionId: p.decisionId, action: p.chosenAction, source: p.decisionSource, confidence: p.confidence, rejected: p.status === "REJECTED", fresh: animate, at: now });
            dirty = true;
            const n = node(p.agentId);
            if (animate && fx && n) fx.ring(p.agentId, { color: srcStyle(p.decisionSource).color, from: n.r + 2, to: n.r + 26, dur: 560, width: 2.2 });
            break;
          }
          case "AGENT_ACTION_REJECTED": {
            const p = (e as GameEvent<"AGENT_ACTION_REJECTED">).payload, s = E.stamps.get(p.agentId);
            if (s && s.decisionId === p.decisionId) { s.rejected = true; dirty = true; }
            break;
          }
          case "TRADE_EXECUTED": {
            const p = (e as GameEvent<"TRADE_EXECUTED">).payload, ex = node(EXCHANGE);
            if (!animate || !fx || !ex) break;
            const col = p.side === "BUY" ? C.buy : C.sell, npc = p.actorKind === "NPC" && node(p.actorId);
            fx.beam(npc ? p.actorId : HUMAN, EXCHANGE, { color: col, dur: npc ? 620 : 520, size: 9.5, tail: 110, bend: npc ? undefined : 0.12 });
            fx.ring(EXCHANGE, { color: col, delay: npc ? 600 : 500, from: ex.r, to: ex.r + 34, dur: 620, width: 2.6 });
            const text = `${npc ? "" : `${p.actorName} · `}${p.side} ${p.ticker} @ ${vd(p.executionPrice)}`.toUpperCase();
            const at = exchangeSpot(text, 12, 2560);
            pop({ kind: "pill", x: at.x, y: at.y, text, color: col, size: 12, dur: 2300, strong: true, slot: at.slot }, 260);
            // Once the order has landed, one white pulse races down the whole recorded chain that led to it.
            const chain = npc ? causalChain(e, st.events, p.actorId).filter((id) => !!node(id)) : [];
            if (chain.length >= 4 && now - lastSweepAt > 1000) {
              lastSweepAt = now;
              for (let i = 0; i + 1 < chain.length; i++) fx.beam(chain[i]!, chain[i + 1]!, { color: "#ffffff", delay: 820 + i * 210, dur: 270, size: 7.5, tail: 240 });
              const cap = `CAUSAL CHAIN · ${chain.length - 1} LINKS · ${p.side} ${p.ticker}`, ch = (tw(cap, 12, 800, 0.05) + 17) / 2;
              pop({ kind: "pill", x: clamp((Lc.main.x0 + Lc.main.x1) / 2, ch + 8, Lc.vw - ch - 8), y: Lc.detail && !Lc.inline ? Lc.main.y0 + 34 : Lc.main.y1 + 3, text: cap, color: "#ffffff", size: 12, dur: 3000, strong: true }, 820);
            }
            break;
          }
          case "LISTING_OPENED": {
            const p = (e as GameEvent<"LISTING_OPENED">).payload, ex = node(EXCHANGE);
            if (!animate || !fx || !ex || !node(p.characterId)) break;
            fx.beam(p.characterId, EXCHANGE, { color: C.mint, dur: 900, size: 7, tail: 140, bend: arcBend(p.characterId, EXCHANGE, 0.2) });
            const text = `LISTED ${p.ticker} @ ${vd(p.spotPrice)}`, at = exchangeSpot(text, 12, 2900);
            pop({ kind: "pill", x: at.x, y: at.y, text, color: C.mint, size: 12, dur: 2200, strong: false, slot: at.slot }, 700);
            break;
          }
          case "WANTED_STATUS_CHANGED": {
            const p = (e as GameEvent<"WANTED_STATUS_CHANGED">).payload, n = node(p.characterId);
            if (animate && fx && n && p.wanted) fx.ring(p.characterId, { color: C.wanted, from: n.r, to: n.r + 44, dur: 800, count: 2, gap: 180, width: 2.6 });
            break;
          }
          case "ESCAPE_SUCCEEDED": {
            const p = (e as GameEvent<"ESCAPE_SUCCEEDED">).payload, n = node(p.characterId);
            if (!animate || !fx || !n) break;
            fx.ring(p.characterId, { color: C.mint, from: n.r, to: n.r + 44, dur: 900, count: 2, gap: 180 });
            pop({ kind: "pill", x: Math.max(n.x, 50), y: n.y - n.r - 30, text: "ESCAPED", color: C.mint, size: 11.5, dur: 2000, strong: true });
            break;
          }
          case "CAPTURE_SUCCEEDED": {
            const p = (e as GameEvent<"CAPTURE_SUCCEEDED">).payload, n = node(p.characterId);
            if (!animate || !fx || !n) break;
            if (p.captorKind === "NPC" && node(p.captorId)) { fx.bolt(p.captorId, p.characterId, C.wanted); fx.bolt(p.captorId, p.characterId, "#ffffff", 140); }
            fx.ring(p.characterId, { color: C.wanted, from: n.r, to: n.r + 90, dur: 1100, count: 3, gap: 150, width: 3 });
            const text = `CAPTURED BY ${shortName(p.captorName).toUpperCase()}`, half = (tw(text, 12.5, 800, 0.05) + 17) / 2;
            pop({ kind: "pill", x: clamp(n.x, half + 8, Lc.vw - half - 8), y: n.y - n.r - 32, text, color: C.wanted, size: 12.5, dur: 3200, strong: true }, 120);
            break;
          }
          case "LISTING_HALTED": {
            const p = (e as GameEvent<"LISTING_HALTED">).payload, ex = node(EXCHANGE);
            if (!animate || !fx || !ex) break;
            if (node(p.characterId)) fx.beam(p.characterId, EXCHANGE, { color: C.sell, delay: 260, dur: 760, size: 12, tail: 190, bend: arcBend(p.characterId, EXCHANGE, 0.24) });
            fx.ring(EXCHANGE, { color: C.sell, delay: 980, from: ex.r, to: ex.r + 70, dur: 1000, count: 3, gap: 140, width: 3 });
            const text = `${p.ticker} HALTED · ${vd(p.finalSpotBeforeHalt)} → V$0`, at = exchangeSpot(text, 13, 4500);
            pop({ kind: "pill", x: at.x, y: at.y, text, color: C.sell, size: 13, dur: 3600, strong: true, slot: at.slot }, 900);
            break;
          }
          default: break;
        }
      }
      if (dirty) bump();
    };

    const st0 = useGame.getState();
    let runKey = `${st0.roomCode}/${st0.runId}`;
    let lastEvents = st0.events;
    // Everything already in the store is history: it is shown as state, never replayed as an effect.
    let lastSeq = st0.events.length ? st0.events[st0.events.length - 1]!.serverSequence : -1;
    const unsub = useGame.subscribe((st) => {
      if (st.events === lastEvents) return;
      lastEvents = st.events;
      const key = `${st.roomCode}/${st.runId}`;
      const top = st.events.length ? st.events[st.events.length - 1]!.serverSequence : -1;
      if (key !== runKey || top < lastSeq) { runKey = key; lastSeq = top; reset(); return; }   // joined, reset or switched room
      if (top <= lastSeq) return;
      const fresh: GameEvent[] = [];
      for (let i = st.events.length - 1; i >= 0; i--) { const e = st.events[i]!; if (e.serverSequence <= lastSeq) break; fresh.push(e); }
      fresh.reverse();
      lastSeq = top;
      handle(fresh, st);
    });
    return () => { unsub(); for (const t of timers) clearTimeout(t); timers.clear(); popsRef.current = []; };
  }, []);

  // ---------- render ----------
  const st = useGame.getState();
  const E = evtRef.current, now = typeof performance !== "undefined" ? performance.now() : 0;
  const { vw, vh, pad } = L;
  const viewBox = `0 0 ${r1(vw)} ${r1(vh)}`;
  const haloId = (k: string): string => `${uid}-h-${k}`;
  const HALOS: Array<[string, string]> = [["WITNESS", ROLE_COLOR.WITNESS], ["INFORMANT", ROLE_COLOR.INFORMANT], ["TRADER", ROLE_COLOR.TRADER], ["POLICE", ROLE_COLOR.POLICE],
    ["outlaw", C.outlaw], ["wanted", C.wanted], ["captured", C.captured], ["exchange", C.exchange], ["bulletin", C.bulletin]];
  const touches = (a: string, b: string): boolean => !hover || hover === a || hover === b;
  const wantedNow = outlaws.filter((c) => c.status === "WANTED");
  const pendingCount = npcs.filter((n) => isPending(n, E, now)).length;

  // persistent structure
  const metricEdges = (metrics?.edges ?? []).filter((e) => L.nodes.has(e.from) && L.nodes.has(e.to));
  const lit = new Set(metricEdges.map((e) => `${e.from}>${e.to}`));
  const wires: Array<[string, string]> = [];
  for (const n of npcs) {
    for (const c of n.contacts) if (L.nodes.has(c) && !lit.has(`${n.id}>${c}`)) wires.push([n.id, c]);
    if (n.role === "POLICE") wires.push([n.id, BULLETIN]);
  }

  const edgeEls: ReactNode[] = [];
  for (const [a, b] of wires) {
    const c = L.curve(a, b);
    if (c) edgeEls.push(<path key={`w-${a}-${b}`} className={`sg-wire ${touches(a, b) ? "" : "sg-dim"}`} d={curvePath(c)} strokeWidth={1.1} strokeOpacity={0.34} strokeDasharray="1.5 5" />);
  }
  for (const e of metricEdges) {
    const c = L.curve(e.from, e.to);
    if (!c) continue;
    const col = KIND_COLOR[e.kind], w = 1.7 + Math.min(3, Math.log2(e.count + 1) * 0.9), on = touches(e.from, e.to), strong = !!hover && on;
    const m = curveAt(c, 0.5), label = String(e.count), bw = Math.max(17, tw(label, 11.5, 800) + 9);
    edgeEls.push(
      <g key={`e-${e.from}-${e.to}-${e.kind}`} className={on ? undefined : "sg-dim"}>
        <path className="sg-edge" d={curvePath(c)} stroke={col} strokeWidth={r1(w * 3)} strokeOpacity={strong ? 0.26 : 0.13} />
        <path className="sg-edge" d={curvePath(c)} stroke={col} strokeWidth={r1(w)} strokeOpacity={strong ? 1 : 0.62} />
        <path className="sg-edge-flow" d={curvePath(c)} stroke="#ffffff" strokeWidth={r1(w)} strokeOpacity={0.9} strokeDasharray="2 16" />
        <path d={arrowPath(c, 7)} fill={col} fillOpacity={strong ? 1 : 0.8} />
        <g transform={`translate(${r1(m.x)} ${r1(m.y)})`}>
          <rect x={-bw / 2} y={-8.5} width={bw} height={17} rx={8.5} fill={C.glass} stroke={col} strokeWidth={1.3} />
          <text textAnchor="middle" dy=".36em" fontSize={11.5} fontWeight={800} fill="#ffffff">{label}</text>
        </g>
      </g>,
    );
  }
  for (const [id, count] of tradeCounts.counts) {
    const c = L.curve(id, EXCHANGE);
    if (!c) continue;
    const m = curveAt(c, 0.5), label = `${count}${tradeCounts.partial ? "+" : ""}`, bw = Math.max(17, tw(label, 11.5, 800) + 9), on = touches(id, EXCHANGE);
    edgeEls.push(
      <g key={`t-${id}`} className={on ? undefined : "sg-dim"}>
        <path className="sg-edge" d={curvePath(c)} stroke={C.mint} strokeWidth={6} strokeOpacity={0.12} />
        <path className="sg-edge" d={curvePath(c)} stroke={C.mint} strokeWidth={2} strokeOpacity={0.62} />
        <path className="sg-edge-flow" d={curvePath(c)} stroke="#ffffff" strokeWidth={2} strokeOpacity={0.9} strokeDasharray="2 16" />
        <path d={arrowPath(c, 7)} fill={C.mint} fillOpacity={0.8} />
        <g transform={`translate(${r1(m.x)} ${r1(m.y)})`}>
          <rect x={-bw / 2} y={-8.5} width={bw} height={17} rx={8.5} fill={C.glass} stroke={C.mint} strokeWidth={1.3} />
          <text textAnchor="middle" dy=".36em" fontSize={11.5} fontWeight={800} fill="#ffffff">{label}</text>
        </g>
      </g>,
    );
  }
  // pursuit and investigation, straight from each officer's live activity
  const chaseEls: ReactNode[] = [];
  const labelled = new Set<string>();
  for (const n of npcs) {
    if ((n.activity !== "PURSUE" && n.activity !== "INVESTIGATE") || !n.targetCharacterId) continue;
    const c = L.curve(n.id, n.targetCharacterId);
    if (!c) continue;
    const hot = n.activity === "PURSUE", m = curveAt(c, 0.7), tagKey = `${n.targetCharacterId}:${n.activity}`;
    const tag = (hot || L.detail) && !labelled.has(tagKey);
    if (tag) labelled.add(tagKey);
    chaseEls.push(
      <g key={`p-${n.id}`}>
        {hot && <path d={curvePath(c)} fill="none" stroke={C.wanted} strokeOpacity={0.2} strokeWidth={8} strokeLinecap="round" />}
        <path className={hot ? "sg-chase" : "sg-probe"} d={curvePath(c)} stroke={hot ? C.wanted : "#ffb347"} strokeWidth={hot ? 2.6 : 1.5} strokeOpacity={hot ? 1 : 0.75} strokeDasharray={hot ? "7 6" : "2 11"} />
        <path d={arrowPath(c, hot ? 9 : 7)} fill={hot ? C.wanted : "#ffb347"} />
        {tag && <g transform={`translate(${r1(m.x)} ${r1(m.y)})`}><Pill text={hot ? "PURSUIT" : "INVESTIGATING"} color={hot ? C.wanted : "#ffb347"} size={10.5} strong={hot} /></g>}
      </g>,
    );
  }

  // header counters
  const live = metrics ? (metrics.decisions.bySource.drex ?? 0) + (metrics.decisions.bySource.meta ?? 0) : null;
  const counters = [
    { key: "msg", label: "MESSAGES", value: metrics ? metrics.messages.sent : null, color: "#ff5fa2", sub: metrics ? `${metrics.messages.delivered} delivered` : "", hint: "Agent-to-agent messages sent in this run" },
    { key: "hop", label: "MAX HOPS", value: metrics ? metrics.messages.maxHops : null, color: "#c77dff", sub: "longest rumor chain", hint: "Longest chain of messages from an eyewitness" },
    { key: "dec", label: "LIVE DECISIONS", value: live, color: "#2ff3e0", sub: metrics ? `of ${metrics.decisions.total} decided` : "", hint: "Decisions resolved by a real model call in this run (decisionSource drex or meta)" },
  ];
  const hy = L.headerH / 2 + 1;
  let cx = vw - pad - 2;
  const counterEls = [...counters].reverse().map((c) => {
    const num = c.value === null ? "—" : String(c.value);
    const lw = Math.max(tw(c.label, 12.5, 700, 0.06), L.detail ? tw(c.sub, 9.5, 600, 0.02) : 0), nw = tw(num, 23, 900);
    const x1 = cx; cx -= lw + nw + 6 + 16;
    const lx = x1 - lw, nx = lx - 6 - nw / 2;
    return (
      <g key={c.key}>
        <title>{c.hint}</title>
        <g transform={`translate(${r1(nx)} ${r1(hy)})`}>
          <text key={num} className={armed.current ? "sg-count" : undefined} textAnchor="middle" dy=".36em" fontSize={23} fontWeight={900} fill={c.color} style={{ filter: `drop-shadow(0 0 5px ${c.color})` }}>{num}</text>
        </g>
        <text x={r1(lx)} y={r1(L.detail && c.sub ? hy - 5.5 : hy)} dy=".36em" fontSize={12.5} fontWeight={700} letterSpacing=".06em" fill={C.soft}>{c.label}</text>
        {L.detail && c.sub && <text x={r1(lx)} y={r1(hy + 8.5)} dy=".36em" fontSize={9.5} fontWeight={600} letterSpacing=".02em" fill={C.dim}>{c.sub}</text>}
      </g>
    );
  });
  const titleW = tw("SWARM GRAPH", 15, 900, 0.16);
  const afterTitle = pad + 15 + titleW + 10;
  const showSub = cx - afterTitle > tw("who told whom, live", 11, 600, 0.03) + 96;
  const chipX = afterTitle + (showSub ? tw("who told whom, live", 11, 600, 0.03) + 12 : 0);
  const showChip = pendingCount > 0 && cx - chipX > 86;

  // legend
  const legendEls: ReactNode[] = [];
  {
    const fs = 12, rowY = (i: number): number => L.legendY + 10.5 + i * 19;
    let x = pad + 2;
    const y1 = rowY(0);
    for (const k of LEGEND_KINDS) {
      const col = KIND_COLOR[k];
      legendEls.push(<g key={`lk-${k}`}><line x1={x} x2={x + 9} y1={y1} y2={y1} stroke={col} strokeOpacity={0.55} strokeWidth={2} strokeLinecap="round" /><circle cx={x + 11} cy={y1} r={3.6} fill={col} /><circle cx={x + 11} cy={y1} r={1.4} fill="#ffffff" />
        <text x={x + 19} y={y1} dy=".36em" fontSize={fs} fontWeight={700} letterSpacing=".05em" fill={C.soft}>{k}</text></g>);
      x += 19 + tw(k, fs, 700, 0.05) + 13;
    }
    const extraW = 11 + tw("BUY", fs, 700, 0.05) + 9 + 11 + tw("SELL", fs, 700, 0.05) + 13;
    if (x + extraW <= vw - pad) {
      legendEls.push(<g key="l-buy"><circle cx={x + 4} cy={y1} r={3.6} fill={C.buy} /><text x={x + 11} y={y1} dy=".36em" fontSize={fs} fontWeight={700} letterSpacing=".05em" fill={C.soft}>BUY</text></g>);
      x += 11 + tw("BUY", fs, 700, 0.05) + 9;
      legendEls.push(<g key="l-sell"><circle cx={x + 4} cy={y1} r={3.6} fill={C.sell} /><text x={x + 11} y={y1} dy=".36em" fontSize={fs} fontWeight={700} letterSpacing=".05em" fill={C.soft}>SELL</text></g>);
      x += 11 + tw("SELL", fs, 700, 0.05) + 13;
    }
    if (x + 22 + tw("PURSUIT", fs, 700, 0.05) <= vw - pad) {
      legendEls.push(<g key="l-chase"><line x1={x} x2={x + 16} y1={y1} y2={y1} stroke={C.wanted} strokeWidth={2.2} strokeDasharray="5 3.5" strokeLinecap="round" /><text x={x + 22} y={y1} dy=".36em" fontSize={fs} fontWeight={700} letterSpacing=".05em" fill={C.soft}>PURSUIT</text></g>);
      x += 22 + tw("PURSUIT", fs, 700, 0.05) + 13;
    }
    // decision sources: second row, or right-aligned on the same row when there is room
    const items = LEGEND_SOURCES.map((s) => srcStyle(s));
    const widths = items.map((s) => 17 + tw(s.word, fs, 700, 0.05) + 12);
    const itemsW = widths.reduce((a, b) => a + b, 0) - 12;
    const prefix = itemsW + tw("DECIDED BY", 10.5, 700, 0.1) + 9 + pad * 2 + 4 <= vw ? "DECIDED BY" : "", pw = prefix ? tw(prefix, 10.5, 700, 0.1) + 9 : 0;
    const total = itemsW + pw;
    const y2 = L.legendRows === 2 ? rowY(1) : y1;
    let sx = L.legendRows === 2 ? pad + 2 : Math.max(x + 8, vw - pad - 2 - total);
    if (prefix) legendEls.push(<text key="l-pre" x={sx} y={y2} dy=".36em" fontSize={10.5} fontWeight={700} letterSpacing=".1em" fill={C.dim}>{prefix}</text>);
    sx += pw;
    items.forEach((s, i) => {
      const dash = s.cls === "scripted" || s.cls === "fallback" ? "2.6 2" : undefined;
      const fill = s.cls === "drex" ? s.color : s.cls === "meta" ? "rgba(165,107,255,.26)" : s.cls === "guard" ? "rgba(255,179,71,.17)" : s.cls === "fallback" ? `url(#${uid}-hatch)` : "none";
      legendEls.push(<g key={`ls-${s.cls}`}><circle cx={sx + 7} cy={y2} r={7} fill={fill} stroke={s.color} strokeWidth={1.3} strokeDasharray={dash} />
        <text x={sx + 7} y={y2} textAnchor="middle" dy=".36em" fontSize={9.5} fontWeight={900} fill={s.text}>{s.letter}</text>
        <text x={sx + 17} y={y2} dy=".36em" fontSize={fs} fontWeight={700} letterSpacing=".05em" fill={C.soft}>{s.word}</text></g>);
      sx += widths[i]!;
    });
  }

  // nodes
  const act = (id: string): void => { const cur = useGame.getState(); cur.selectAgent(cur.selectedAgentId === id ? null : id); };
  const bind = (id: string, onAct?: () => void) => ({
    onPointerEnter: () => setHover(id), onPointerLeave: () => setHover((h) => (h === id ? null : h)),
    onFocus: () => setHover(id), onBlur: () => setHover((h) => (h === id ? null : h)),
    onClick: onAct, onKeyDown: onAct ? (ev: ReactKeyboardEvent) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onAct(); } } : undefined,
  });

  const npcEls = npcs.map((n, idx) => {
    const g = L.nodes.get(n.id);
    if (!g) return null;
    const col = ROLE_COLOR[n.role], pend = isPending(n, E, now), stamp = stampOf(n, E, now), sel = selectedId === n.id;
    const conf = !pend && stamp && stamp.confidence !== null && !stamp.rejected ? clamp(stamp.confidence, 0, 1) : null;
    const rc = g.r + 3.5, circ = 2 * Math.PI * rc, ss = stamp ? srcStyle(stamp.source) : null;
    const stampY = L.inline ? g.r + 31 : -(g.r + 15);
    const said = stamp?.action ? `Last decision: ${stamp.action}, ${ss?.word === "DREX" ? "decided by Drex" : `source ${ss?.word.toLowerCase()}`}.` : "No decision yet.";
    return (
      <g key={n.id} className={`sg-node ${sel || hover === n.id ? "sg-on" : ""}`} transform={`translate(${r1(g.x)} ${r1(g.y)})`} role="button" tabIndex={0}
        aria-pressed={sel} aria-label={`${n.name}, ${n.role.toLowerCase()}. Knows ${n.memories.length} facts. ${pend ? "Deciding now." : said}`} {...bind(n.id, () => act(n.id))}>
        <circle r={g.r + 9} fill="transparent" />
        <circle className="sg-halo sg-breathe" r={r1(g.r * 2.25)} fill={`url(#${haloId(n.role)})`} style={{ animationDelay: `${-idx * 0.47}s` }} />
        {sel && <circle className="sg-sel" r={g.r + 11} strokeWidth={1.7} strokeDasharray="5 6" />}
        {pend && (
          <g>
            <circle r={g.r + 6.5} fill="none" stroke={C.pending} strokeOpacity={0.2} strokeWidth={8} />
            <circle className="sg-think-a" r={g.r + 6.5} stroke={C.pending} strokeWidth={3.2} strokeDasharray={`${r1((g.r + 6.5) * 0.9)} ${r1((g.r + 6.5) * 1.194)}`} />
            <circle className="sg-think-b" r={g.r + 11.5} stroke={C.pending} strokeOpacity={0.7} strokeWidth={1.5} strokeDasharray="2 5" />
            <g className="sg-orbit"><circle cx={g.r + 6.5} r={3.2} fill="#ffffff" /><circle cx={-(g.r + 6.5)} r={2.2} fill="#ffffff" fillOpacity={0.8} /></g>
          </g>
        )}
        {conf !== null && ss && (
          <g transform="rotate(-90)">
            <circle r={rc} fill="none" stroke={ss.color} strokeOpacity={0.17} strokeWidth={2} />
            <circle r={rc} fill="none" stroke={ss.color} strokeOpacity={0.95} strokeWidth={2} strokeLinecap="round" strokeDasharray={`${r1(conf * circ)} ${r1(circ)}`} />
          </g>
        )}
        {shapeOf(n.role, g.r, `url(#${uid}-glass)`, col)}
        {glyphOf(n.role, g.r, col)}
        <text className="sg-ink" y={g.r + 13.5} textAnchor="middle" fontSize={14} fontWeight={700} fill="#ffffff" strokeWidth={3.4}>{shortName(n.name)}</text>
        {L.detail && n.memories.length > 0 && (
          <g transform={`translate(${r1(g.r * 0.82)} ${r1(g.r * 0.7)})`}>
            <circle r={7.6} fill={C.glass} stroke={col} strokeWidth={1.2} />
            <text key={n.memories.length} className={armed.current ? "sg-badge-in" : undefined} textAnchor="middle" dy=".36em" fontSize={9.5} fontWeight={800} fill="#ffffff">{n.memories.length}</text>
          </g>
        )}
        <g transform={`translate(0 ${r1(stampY)})`}>
          {pend ? <ThinkPill offered={offeredFor(n, E)} wide={L.wide} /> : stamp ? <StampPill key={stamp.decisionId} st={stamp} wide={L.wide} hatch={`${uid}-hatch`} /> : null}
        </g>
      </g>
    );
  });

  const outlawEls = outlaws.map((c) => {
    const g = L.nodes.get(c.id);
    if (!g) return null;
    const gone = c.status === "CAPTURED", wanted = c.status === "WANTED", col = gone ? C.captured : C.outlaw;
    const listing = c.ticker ? st.listings[c.ticker] : undefined;
    const heat = clamp(c.heat, 0, 100) / 100, rh = g.r - 0.5, circ = 2 * Math.PI * rh;
    const code = (c.ticker ?? c.nickname).slice(0, 3).toUpperCase();
    const pick = c.ticker ? () => useGame.setState({ selectedTicker: c.ticker }) : undefined;
    return (
      <g key={c.id} className={`sg-node ${hover === c.id ? "sg-on" : ""}`} transform={`translate(${r1(g.x)} ${r1(g.y)})`} role="button" tabIndex={0}
        aria-label={`${c.nickname}, outlaw${c.ticker ? `, ticker ${c.ticker}` : ""}, ${c.status.toLowerCase()}${listing ? `, price ${vd(listing.spotPrice)} fictional credits` : ""}`} {...bind(c.id, pick)}>
        <circle r={g.r + 8} fill="transparent" />
        <circle className={`sg-halo ${wanted ? "sg-wanted" : "sg-breathe"}`} r={r1(g.r * 2.3)} fill={`url(#${haloId(gone ? "captured" : wanted ? "wanted" : "outlaw")})`} />
        {wanted && <circle className="sg-wanted" r={g.r + 5} fill="none" stroke={C.wanted} strokeWidth={2} />}
        <circle className="sg-shape" r={g.r - 4.5} fill={`url(#${uid}-glass)`} stroke={col} strokeWidth={2.2} />
        <circle r={rh} fill="none" stroke={col} strokeOpacity={0.22} strokeWidth={2.4} />
        {!gone && heat > 0 && <circle r={rh} fill="none" stroke={wanted ? C.wanted : C.heat} strokeWidth={2.6} strokeLinecap="round" strokeDasharray={`${r1(heat * circ)} ${r1(circ)}`} transform="rotate(-90)" />}
        <text textAnchor="middle" dy=".36em" fontSize={r1(g.r * 0.5)} fontWeight={900} letterSpacing=".04em" fill={col}>{code}</text>
        {gone && <path d={`M${-g.r + 6} ${-g.r + 6}L${g.r - 6} ${g.r - 6}M${g.r - 6} ${-g.r + 6}L${-g.r + 6} ${g.r - 6}`} stroke={C.wanted} strokeWidth={2.6} strokeLinecap="round" strokeOpacity={0.9} />}
        {wanted && L.outlawLabels && <text className="sg-ink" y={-g.r - 9} textAnchor="middle" dy=".36em" fontSize={10.5} fontWeight={900} letterSpacing=".12em" fill={C.wanted} strokeWidth={3}>WANTED</text>}
        {gone && L.outlawLabels && <text className="sg-ink" y={-g.r - 9} textAnchor="middle" dy=".36em" fontSize={10.5} fontWeight={900} letterSpacing=".12em" fill={C.wanted} strokeWidth={3}>CAPTURED</text>}
        {L.outlawLabels && (
          <>
            <text className="sg-ink" y={g.r + 13.5} textAnchor="middle" fontSize={13.5} fontWeight={800} letterSpacing=".04em" fill={gone ? "#b6b3cc" : "#ffffff"} strokeWidth={3.4}>{c.ticker ?? c.nickname}</text>
            {listing && <text className="sg-ink" y={g.r + 27} textAnchor="middle" fontSize={12} fontWeight={800} fill={gone || listing.status !== "ACTIVE" ? C.sell : C.mint} strokeWidth={3.2}>{listing.status === "ACTIVE" ? vd(listing.spotPrice) : "V$0 · HALTED"}</text>}
          </>
        )}
      </g>
    );
  });

  const exNode = L.nodes.get(EXCHANGE), buNode = L.nodes.get(BULLETIN);
  const board = listings.slice(0, L.boardRows);

  // tooltip
  let tip: ReactNode = null;
  if (hover) {
    const g = L.nodes.get(hover);
    const npc = st.npcs[hover], chr = st.characters[hover];
    if (g) {
      const fsz = clamp(12.5 * L.s, 11, 19), width = clamp(L.cw * 0.54, 190, fsz * 21);
      const px = g.x * L.s, py = g.y * L.s, off = (g.r + (L.inline ? 10 : 16)) * L.s;
      const below = py < L.ch * 0.46;
      const style: CSSProperties = { left: clamp(px, width / 2 + 6, L.cw - width / 2 - 6), top: below ? py + off : py - off, width, fontSize: fsz, transform: below ? "translate(-50%, 0)" : "translate(-50%, -100%)" };
      let body: ReactNode = null;
      if (npc) {
        const pend = isPending(npc, E, now), t = npc.lastDecision, stamp = stampOf(npc, E, now), ss = srcStyle(stamp?.source ?? null);
        const kinds = npc.memories.reduce<Record<string, number>>((acc, m) => { acc[m.sourceKind] = (acc[m.sourceKind] ?? 0) + 1; return acc; }, {});
        const how = [kinds.DIRECT ? `${kinds.DIRECT} first-hand` : "", kinds.MESSAGE ? `${kinds.MESSAGE} told` : "", kinds.BULLETIN ? `${kinds.BULLETIN} bulletin` : "", kinds.DOCUMENT ? `${kinds.DOCUMENT} document` : ""].filter(Boolean).join(" · ");
        const same = t && stamp && t.id === stamp.decisionId ? t : null;
        const probs = same?.probabilities ?? null;
        body = (
          <>
            <div className="sg-tip-h" style={{ color: ROLE_COLOR[npc.role] }}>{npc.name}<span>{npc.role}</span></div>
            <div className="sg-tip-k">Knows</div>
            <div><b>{npc.memories.length}</b> {npc.memories.length === 1 ? "fact" : "facts"} in private memory{how ? <span className="sg-tip-dim"> · {how}</span> : null}</div>
            <div className="sg-tip-k">{pend ? "Deciding now" : "Last decision"}</div>
            {pend ? <div className="sg-tip-row"><span className="sg-tip-src pending">PENDING</span><span className="sg-tip-dim">offered: {offeredFor(npc, E).join(" / ") || "…"}</span></div>
              : stamp ? (
                <>
                  <div className="sg-tip-row"><span className={`sg-tip-src ${ss.cls}`}>{ss.word}</span><span className="sg-tip-act">{(stamp.action ?? "none").toUpperCase()}</span>{stamp.rejected && <span className="sg-tip-dim">rejected, the world moved on</span>}</div>
                  <div className="sg-tip-dim">{ss.note}{stamp.confidence !== null ? ` · confidence ${stamp.confidence.toFixed(2)}` : ""}{same?.latencyMs != null ? ` · ${Math.round(same.latencyMs)} ms` : ""}</div>
                  {probs && <div className="sg-tip-prob">{Object.entries(probs).map(([k, v]) => (<span key={k} style={{ display: "contents" }}><span className={k === stamp.action ? "on" : undefined}>{k}</span><i><b style={{ width: `${Math.round(clamp(v, 0, 1) * 100)}%` }} /></i><span>{(v * 100).toFixed(0)}%</span></span>))}</div>}
                </>
              ) : <div className="sg-tip-dim">No decision yet.</div>}
            <div className="sg-tip-foot">Click to open this agent in the inspector</div>
          </>
        );
      } else if (chr) {
        const l = chr.ticker ? st.listings[chr.ticker] : undefined;
        body = (
          <>
            <div className="sg-tip-h" style={{ color: chr.status === "CAPTURED" ? "#b6b3cc" : C.outlaw }}>{chr.nickname}<span>{chr.ticker ?? "OUTLAW"}</span></div>
            <div>{chr.status === "CAPTURED" ? "Captured. The listing is halted at exactly V$0." : chr.status === "WANTED" ? `Wanted · heat ${Math.round(chr.heat)}/100` : `At large · heat ${Math.round(chr.heat)}/100`}</div>
            {l && <div className="sg-tip-dim">price {l.status === "ACTIVE" ? vd(l.spotPrice) : "V$0"} · reference {vd(l.referenceValue)} · bounty {vd(l.currentBounty)}</div>}
            <div className="sg-tip-dim">jobs {chr.completedMissions} · notoriety {chr.notorietyPoints} · escapes {chr.successfulEscapes}</div>
            <div className="sg-tip-foot">V$ are fictional game credits{chr.ticker ? " · click to select this ticker" : ""}</div>
          </>
        );
      } else if (hover === EXCHANGE) {
        body = (
          <>
            <div className="sg-tip-h" style={{ color: C.exchange }}>The Exchange<span>MARKET</span></div>
            <div>{metrics ? <><b>{metrics.trades.executed}</b> trades settled · NPC {metrics.trades.byNpc} · human {metrics.trades.byHuman}</> : "No trades measured yet."}</div>
            {listings.slice(0, 4).map((l) => <div key={l.ticker} className="sg-tip-dim">{l.ticker} · {l.status === "ACTIVE" ? vd(l.spotPrice) : "HALTED at V$0"}</div>)}
            <div className="sg-tip-foot">Simulated house counterparty · V$ are fictional game credits</div>
          </>
        );
      } else if (hover === BULLETIN) {
        body = (
          <>
            <div className="sg-tip-h" style={{ color: C.bulletin }}>Public bulletin<span>BROADCAST</span></div>
            <div>A report reaching the police, or an officer seeing an incident, publishes a wanted bulletin to every agent.</div>
            <div className="sg-tip-dim">{wantedNow.length ? `Wanted now: ${wantedNow.map((c) => c.ticker ?? c.nickname).join(", ")}` : "Nobody is wanted right now."}</div>
          </>
        );
      }
      if (body) tip = <div className="sg-tip" role="tooltip" style={style}>{body}</div>;
    }
  }

  return (
    <div ref={hostRef} className={`sg ${reduced ? "sg-calm" : ""}`} role="group" aria-label="Swarm graph: live communication, decisions and trades between the agents">
      <svg className="sg-layer sg-base" viewBox={viewBox} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <defs>
          <pattern id={`${uid}-dots`} width={18} height={18} patternUnits="userSpaceOnUse"><circle cx={1} cy={1} r={0.8} fill="#8d92bd" fillOpacity={0.26} /></pattern>
          <pattern id={`${uid}-hatch`} width={4} height={4} patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width={1.7} height={4} fill="#ffb347" fillOpacity={0.6} /></pattern>
          <linearGradient id={`${uid}-main`} x1={0} x2={1} y1={0} y2={0}><stop offset={0} stopColor="#ff4fa3" stopOpacity={0.13} /><stop offset={0.5} stopColor="#a56bff" stopOpacity={0.08} /><stop offset={1} stopColor="#2ff3e0" stopOpacity={0.14} /></linearGradient>
          <linearGradient id={`${uid}-mainS`} x1={0} x2={1} y1={0} y2={0}><stop offset={0} stopColor="#ff4fa3" stopOpacity={0.75} /><stop offset={0.5} stopColor="#a56bff" stopOpacity={0.55} /><stop offset={1} stopColor="#2ff3e0" stopOpacity={0.8} /></linearGradient>
          <linearGradient id={`${uid}-law`} x1={0} x2={1} y1={0} y2={0}><stop offset={0} stopColor="#4cc9f0" stopOpacity={0.13} /><stop offset={1} stopColor="#ffd166" stopOpacity={0.1} /></linearGradient>
          <linearGradient id={`${uid}-lawS`} x1={0} x2={1} y1={0} y2={0}><stop offset={0} stopColor="#4cc9f0" stopOpacity={0.75} /><stop offset={1} stopColor="#ffd166" stopOpacity={0.6} /></linearGradient>
          <linearGradient id={`${uid}-street`} x1={0} x2={0} y1={0} y2={1}><stop offset={0} stopColor="#ff4fa3" stopOpacity={0.17} /><stop offset={1} stopColor="#ff4fa3" stopOpacity={0.04} /></linearGradient>
          <linearGradient id={`${uid}-title`} x1={0} x2={1} y1={0} y2={0}><stop offset={0} stopColor="#ff4fa3" /><stop offset={1} stopColor="#2ff3e0" /></linearGradient>
          <radialGradient id={`${uid}-glass`} cx={0.34} cy={0.28} r={0.95}><stop offset={0} stopColor="#2c3060" /><stop offset={0.55} stopColor="#141733" /><stop offset={1} stopColor="#080915" /></radialGradient>
          {HALOS.map(([k, col]) => (
            <radialGradient key={k} id={haloId(k)}><stop offset={0.28} stopColor={col} stopOpacity={0.55} /><stop offset={0.6} stopColor={col} stopOpacity={0.16} /><stop offset={1} stopColor={col} stopOpacity={0} /></radialGradient>
          ))}
        </defs>
        <rect width={r1(vw)} height={r1(vh)} fill={`url(#${uid}-dots)`} />
        <rect x={r1(L.street.x0)} y={r1(L.street.y0)} width={r1(L.street.x1 - L.street.x0)} height={r1(L.street.y1 - L.street.y0)} rx={11} fill={`url(#${uid}-street)`} stroke={C.outlaw} strokeOpacity={0.5} strokeWidth={1.1} />
        <rect x={r1(L.main.x0)} y={r1(L.main.y0)} width={r1(L.main.x1 - L.main.x0)} height={r1(L.main.y1 - L.main.y0)} rx={11} fill={`url(#${uid}-main)`} stroke={`url(#${uid}-mainS)`} strokeWidth={1.1} />
        <rect x={r1(L.law.x0)} y={r1(L.law.y0)} width={r1(L.law.x1 - L.law.x0)} height={r1(L.law.y1 - L.law.y0)} rx={11} fill={`url(#${uid}-law)`} stroke={`url(#${uid}-lawS)`} strokeWidth={1.1} />
        <text className="sg-lane-h" x={r1((L.street.x0 + L.street.x1) / 2)} y={r1(L.headY)} textAnchor="middle" dy=".36em" fontSize={12} fill={C.outlaw} fillOpacity={0.95}>OUTLAWS</text>
        {L.cols.map((c, i) => {
          const next = L.cols[i + 1]?.x ?? exNode?.x ?? c.x, here = tw(ROLE_PLURAL[c.role], 12, 800, 0.15) / 2;
          const nextHalf = L.cols[i + 1] ? tw(ROLE_PLURAL[L.cols[i + 1]!.role], 12, 800, 0.15) / 2 : (exNode?.r ?? 20);
          const mid = (c.x + here + next - nextHalf) / 2, room = next - nextHalf - (c.x + here);
          return (
            <g key={c.role}>
              <text className="sg-lane-h" x={r1(c.x)} y={r1(L.headY)} textAnchor="middle" dy=".36em" fontSize={12} fill={ROLE_COLOR[c.role]} fillOpacity={0.95}>{ROLE_PLURAL[c.role]}</text>
              {room > 34 && [0, 1, 2].map((k) => (
                <path key={k} className="sg-chev" d={`M${r1(mid - 9 + k * 7)} ${r1(L.headY - 3.6)}l3.8 3.6l-3.8 3.6`} fill="none" stroke="#ffffff" strokeOpacity={0.3} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" style={{ animationDelay: `${k * 0.22 + i * 0.3}s` }} />
              ))}
            </g>
          );
        })}
        <text className="sg-lane-h" x={r1(L.law.x0 + 11)} y={r1(L.inline ? L.lawY : L.law.y0 + 12.5)} dy=".36em" fontSize={12} fill={ROLE_COLOR.POLICE} fillOpacity={0.95}>POLICE</text>
        {L.detail && !L.inline && <text x={r1(L.law.x1 - 11)} y={r1(L.law.y0 + 12.5)} textAnchor="end" dy=".36em" fontSize={10} fontWeight={600} letterSpacing=".04em" fill={C.dim}>a bulletin reaches every agent</text>}
        {edgeEls}
        {chaseEls}
      </svg>
      <canvas ref={canvasRef} className="sg-layer sg-fx" aria-hidden="true" />
      <svg className="sg-layer sg-top" viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
        {/* header */}
        <circle className="sg-live" cx={pad + 5} cy={hy} r={3.8} fill={C.outlaw} />
        <text x={pad + 15} y={r1(hy)} dy=".36em" fontSize={15} fontWeight={900} letterSpacing=".16em" fill={`url(#${uid}-title)`}>SWARM GRAPH</text>
        {showSub && <text x={r1(afterTitle)} y={r1(hy)} dy=".36em" fontSize={11} fontWeight={600} letterSpacing=".03em" fill={C.dim}>who told whom, live</text>}
        {showChip && (
          <g transform={`translate(${r1(chipX + 40)} ${r1(hy)})`}>
            <rect x={-40} y={-9} width={80} height={18} rx={9} fill={C.glass} stroke={C.pending} strokeWidth={1.2} />
            <text textAnchor="middle" dy=".36em" fontSize={10.5} fontWeight={800} letterSpacing=".06em" fill={C.pending}>{pendingCount} DECIDING</text>
          </g>
        )}
        {counterEls}

        {/* exchange */}
        {exNode && (
          <g className={`sg-node ${hover === EXCHANGE ? "sg-on" : ""}`} transform={`translate(${r1(exNode.x)} ${r1(exNode.y)})`} tabIndex={0} role="img" aria-label="The Exchange: every trade settles here" {...bind(EXCHANGE)}>
            <circle r={exNode.r + 6} fill="transparent" />
            <circle className="sg-halo sg-breathe" r={r1(exNode.r * 2.1)} fill={`url(#${haloId("exchange")})`} />
            <path className="sg-shape" d={HEX(exNode.r - 1)} fill={`url(#${uid}-glass)`} stroke={`url(#${uid}-title)`} strokeWidth={2.6} strokeLinejoin="round" />
            <path d={HEX(exNode.r - 6.5)} fill="none" stroke={C.exchange} strokeOpacity={0.28} strokeWidth={1} strokeLinejoin="round" />
            <g transform={`scale(${r1(exNode.r / 28)})`}>
              <path d="M-9 -11V9M0 -13V6M9 -7V11" stroke="#ffffff" strokeOpacity={0.75} strokeWidth={1.3} strokeLinecap="round" />
              <rect x={-11.6} y={-5} width={5.2} height={10} rx={1} fill={C.buy} /><rect x={-2.6} y={-9} width={5.2} height={9} rx={1} fill={C.sell} /><rect x={6.4} y={-2} width={5.2} height={9} rx={1} fill={C.buy} />
            </g>
            <text className="sg-ink" y={exNode.r + 13.5} textAnchor="middle" fontSize={13} fontWeight={900} letterSpacing=".13em" fill={C.exchange} strokeWidth={3.4}>EXCHANGE</text>
            {board.map((l, i) => (
              <text key={l.ticker} className="sg-ink" y={r1(exNode.r + 28 + i * 13.5)} textAnchor="middle" fontSize={12} fontWeight={800} strokeWidth={3.2} fill="#ffffff">
                <tspan fill={l.status === "ACTIVE" ? C.outlaw : "#b6b3cc"}>{l.ticker}</tspan><tspan dx={5} fill={l.status === "ACTIVE" ? "#ffffff" : C.sell}>{l.status === "ACTIVE" ? vd(l.spotPrice) : "V$0"}</tspan>
              </text>
            ))}
          </g>
        )}
        {/* public bulletin */}
        {buNode && (
          <g className={`sg-node ${hover === BULLETIN ? "sg-on" : ""}`} transform={`translate(${r1(buNode.x)} ${r1(buNode.y)})`} tabIndex={0} role="img" aria-label="Public bulletin: reaches every agent" {...bind(BULLETIN)}>
            <circle r={buNode.r + 6} fill="transparent" />
            <circle className={`sg-halo ${wantedNow.length ? "sg-wanted" : "sg-breathe"}`} r={r1(buNode.r * 2.1)} fill={`url(#${haloId("bulletin")})`} />
            <circle className="sg-shape" r={r1(buNode.r * 0.6)} fill={`url(#${uid}-glass)`} stroke={C.bulletin} strokeWidth={2.2} />
            <circle r={r1(buNode.r * 0.2)} fill={C.bulletin} />
            {[0.8, 1].map((k, i) => { const rr = buNode.r * k, a = rr * 0.72, b = rr * 0.69; return (
              <path key={k} d={`M${r1(a)} ${r1(-b)}A${r1(rr)} ${r1(rr)} 0 0 1 ${r1(a)} ${r1(b)}M${r1(-a)} ${r1(-b)}A${r1(rr)} ${r1(rr)} 0 0 0 ${r1(-a)} ${r1(b)}`} fill="none" stroke={C.bulletin} strokeOpacity={i ? 0.45 : 0.85} strokeWidth={1.7} strokeLinecap="round" />
            ); })}
            <text className="sg-ink" y={buNode.r + 12.5} textAnchor="middle" fontSize={12.5} fontWeight={900} letterSpacing=".1em" fill={C.bulletin} strokeWidth={3.4}>PUBLIC BULLETIN</text>
            {wantedNow.length > 0 && <text className="sg-ink" y={buNode.r + 26.5} textAnchor="middle" fontSize={12} fontWeight={800} letterSpacing=".05em" fill={C.wanted} strokeWidth={3.2}>WANTED {wantedNow.map((c) => c.ticker ?? c.nickname).slice(0, 2).join(" · ")}</text>}
          </g>
        )}
        {outlawEls}
        {hiddenOutlaws > 0 && <text x={r1((L.street.x0 + L.street.x1) / 2)} y={r1(L.street.y1 - 9)} textAnchor="middle" fontSize={10.5} fontWeight={700} fill={C.dim}>+{hiddenOutlaws} more</text>}
        {outlaws.length === 0 && <text x={r1((L.street.x0 + L.street.x1) / 2)} y={r1((L.street.y0 + L.street.y1) / 2)} textAnchor="middle" fontSize={11} fontWeight={600} fill={C.dim}>no outlaw yet</text>}
        {npcEls}
        {npcs.length === 0 && <text x={r1((L.main.x0 + L.main.x1) / 2)} y={r1((L.main.y0 + L.main.y1) / 2)} textAnchor="middle" fontSize={14} fontWeight={700} fill={C.dim}>Waiting for the agents to come online…</text>}

        {/* transient labels */}
        {popsRef.current.map((p) => (
          <g key={p.id} transform={`translate(${r1(p.x)} ${r1(p.y)})`}>
            {p.kind === "eye" ? (
              <g className="sg-eye">
                <circle r={p.size} fill={C.glass} stroke="#ffffff" strokeWidth={1.3} />
                <path d={`M${-p.size * 0.66} 0Q0 ${-p.size * 0.62} ${p.size * 0.66} 0Q0 ${p.size * 0.62} ${-p.size * 0.66} 0Z`} fill="none" stroke="#ffffff" strokeWidth={1.2} strokeLinejoin="round" />
                <circle r={p.size * 0.22} fill="#ffffff" />
              </g>
            ) : (
              <g className="sg-pop" style={{ animationDuration: `${p.dur}ms` }}><Pill text={p.text} color={p.color} size={p.size} strong={p.strong} /></g>
            )}
          </g>
        ))}
        {legendEls}
      </svg>
      {tip}
    </div>
  );
}
