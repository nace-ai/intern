// "Watch the swarm think": a stack of live decision cards for the top-right of the city view.
//
// A card appears the moment DECISION_REQUESTED arrives for an NPC and resolves when the matching
// DECISION_RESOLVED arrives. Honesty rules baked in:
//   * probabilities, confidence, model eval time and request id are shown only when the provider reported them;
//   * the DREX LIVE badge is used only for decisionSource === "drex"; guardrail, fallback, scripted, meta and
//     replay decisions each get their own colour, badge shape and stamp;
//   * while a decision is pending the bars stay empty: nothing is animated that could be read as a value.
//
// Mount: <DecisionSpotlight dock="top-right" /> inside the (position: relative) city section. See the props.
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject } from "react";
import { RULES } from "@vice/contracts";
import type { NpcRole } from "@vice/contracts";
import { useGame } from "../../state/gameStore";
import { createDeck, resolutionFromTrace } from "./DecisionSpotlightDeck";
import type { Resolution, SpotCard } from "./DecisionSpotlightDeck";
import { STAMP_AT_MS, createCardFx, onFrame } from "./DecisionSpotlightFx";
import type { CardFx } from "./DecisionSpotlightFx";
import "./DecisionSpotlight.css";

export interface DecisionSpotlightProps {
  /** Most cards on stage at once (1 to 3). Fewer are shown automatically when the city view is too short. */
  maxCards?: number;
  /** Self-position inside the nearest positioned ancestor. Default "none": you position the element. */
  dock?: "none" | "top-right" | "top-left";
  /** How long a resolved card stays, in ms. Default 6000. Hovering or focusing a card pauses the countdown. */
  dwellMs?: number;
  /** The stack may shrink down to this factor to keep `maxCards` on stage in a short city view. Default 0.85; 1 = never shrink. */
  minScale?: number;
  className?: string;
  style?: CSSProperties;
}

// Layout numbers shared with DecisionSpotlight.css (in layout units, 1u = the root font-size).
const CARD_H_U = 11.3;
const GAP_U = 0.55;
const TITLE_U = 1.75;
const CHAMFER_U = 1.05;
const RESERVE_PX = 46;      // kept free under the stack for the HUD chips at the bottom of the city
const EXIT_MS = 460;        // slot collapse in the stylesheet is 440 ms

// Same role colours and silhouettes as the NPC markers in the city, so a card maps to its NPC at a glance.
const ROLE_COLOR: Record<NpcRole, string> = { WITNESS: "#ffd166", INFORMANT: "#c77dff", TRADER: "#5dfdcb", POLICE: "#4cc9f0" };

type Tone = "pending" | "drex" | "guard" | "fallback" | "scripted" | "meta" | "replay" | "lost";
const TONE_COLOR: Record<Tone, string> = {
  pending: "#e6eaff", drex: "#2ff3e0", guard: "#ffe45c", fallback: "#ffb347",
  scripted: "#a9aed4", meta: "#b98cff", replay: "#8fa3d9", lost: "#8085ab",
};
const STALE_COLOR = "#ff5468";

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));
const fmtPct = (p: number) => `${(p * 100).toFixed(1)}%`;
const fmtMs = (n: number) => (n < 10 ? n.toFixed(1) : String(Math.round(n)));
const pretty = (s: string) => s.replace(/_/g, " ");
function shortId(id: string): string {
  return id.length <= 14 ? id : `${id.slice(0, 8)}…${id.slice(-4)}`;
}

function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(() => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduce(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduce;
}

/**
 * Fits the stack between its own top and the bottom of the nearest clipping ancestor (the city view).
 * First it shrinks the layout unit a little (never below `minScale`) so that `max` cards fit; only when that
 * is not enough does it show fewer cards (the rest wait in the queue). The scale is written straight to the
 * element as --dspot-fit, so no React render is needed for it.
 */
function useFit(rootRef: RefObject<HTMLDivElement | null>, max: number, minScale: number): number {
  const [fit, setFit] = useState(max);
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    let clip: HTMLElement | null = null;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p).overflowY;
      if (o === "hidden" || o === "clip" || o === "auto" || o === "scroll") { clip = p; break; }
    }
    const lo = Math.max(0.5, Math.min(1, minScale));
    const measure = () => {
      el.style.setProperty("--dspot-fit", "1");
      const u = parseFloat(getComputedStyle(el).fontSize) || 14;          // the unit at full size
      const top = el.getBoundingClientRect().top;
      const bottom = Math.min(clip ? clip.getBoundingClientRect().bottom : Infinity, window.innerHeight);
      const space = bottom - top - RESERVE_PX;
      const need = (n: number) => (TITLE_U + n * CARD_H_U + (n - 1) * GAP_U) * u;
      let n = max, k = 1;
      for (; n >= 1; n--) {
        k = Math.min(1, Math.floor((space / need(n)) * 100) / 100);
        if (k >= lo || n === 1) break;
      }
      el.style.setProperty("--dspot-fit", String(Math.max(lo, k)));
      setFit(Math.max(1, n));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(clip ?? document.documentElement);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); el.style.removeProperty("--dspot-fit"); };
  }, [rootRef, max, minScale]);
  return fit;
}

// ---------- what a card shows ----------

interface Row { label: string; p: number | null; chosen: boolean; pick: boolean }
interface Cell { k: string; v: string; title?: string }
interface View {
  tone: Tone;
  thinking: boolean;
  stale: boolean;
  badge: string;
  badgeSub: string | null;
  stamp: string | null;
  rows: Row[];
  /** True when the provider reported per-label probabilities. */
  hasProbs: boolean;
  chosen: string | null;
  soloCaption: string;
  clockBig: string | null;
  clockUnit: string;
  clockCap: string;
  cells: Cell[];
  note: string | null;
  lineTag: string;
  lineKind: "knows" | "ok" | "bad" | "idle";
  lineText: string;
  tip: string;
}

function sourceTone(d: Resolution): Tone {
  switch (d.source) {
    case "drex": return "drex";
    case "drex_low_confidence_guardrail": return "guard";
    case "fallback": return "fallback";
    case "meta": return "meta";
    case "replay": return "replay";
    default: return "scripted";
  }
}

function buildView(card: SpotCard): View {
  const trace = card.trace;
  const d: Resolution | null = card.lost ? null : (trace ? resolutionFromTrace(trace) : null) ?? card.res;
  const thinking = card.phase === "thinking" || (!d && !card.lost);
  const labels = card.labels.length ? card.labels : trace ? Object.keys(trace.offeredActions) : [];
  const knows = card.knows;
  const tipHead = `${card.name} · ${card.role}${trace?.question ? `\n${trace.question}` : ""}`;

  if (card.lost) {
    return {
      tone: "lost", thinking: false, stale: false, badge: "NO RESULT", badgeSub: null, stamp: null,
      rows: labels.map((label) => ({ label, p: null, chosen: false, pick: false })), hasProbs: false, chosen: null,
      soloCaption: "the result never reached this screen", clockBig: null, clockUnit: "", clockCap: "NO RESULT",
      cells: [], note: "no DECISION_RESOLVED event was received", lineTag: "KNOWS", lineKind: knows ? "knows" : "idle",
      lineText: knows ?? "nothing was applied on this screen", tip: tipHead,
    };
  }

  if (thinking || !d) {
    const mode = card.mode;
    const deadline = mode === "drex" || mode === "meta" ? RULES.decisions.deadlineMs[mode] : null;
    const cells: Cell[] = [];
    if (mode) cells.push({ k: "room mode", v: mode });
    if (deadline != null) cells.push({ k: "deadline", v: `${(deadline / 1000).toFixed(1)} s` });
    return {
      tone: "pending", thinking: true, stale: false, badge: "PENDING", badgeSub: null, stamp: null,
      rows: labels.map((label) => ({ label, p: null, chosen: false, pick: false })), hasProbs: false, chosen: null,
      soloCaption: "", clockBig: null, clockUnit: "s", clockCap: "THINKING",
      cells, note: null, lineTag: "KNOWS", lineKind: knows ? "knows" : "idle",
      lineText: knows ?? `weighing ${labels.length} possible action${labels.length === 1 ? "" : "s"}`, tip: tipHead,
    };
  }

  const tone = sourceTone(d);
  const stale = d.status === "REJECTED";
  // Provider numbers exist only when an external provider actually answered.
  const real = (d.source === "drex" || d.source === "meta" || d.source === "drex_low_confidence_guardrail") && d.provider != null;
  const probs = real && d.probabilities && typeof d.probabilities === "object" ? d.probabilities : null;
  const pOf = (label: string): number | null => {
    const v = probs ? probs[label] : undefined;
    return typeof v === "number" && Number.isFinite(v) ? clamp01(v) : null;
  };
  const rows: Row[] = labels.map((label) => ({
    label, p: pOf(label), chosen: label === d.chosen,
    pick: d.providerChoice != null && d.providerChoice !== d.chosen && label === d.providerChoice,
  }));
  const hasProbs = rows.some((r) => r.p != null);

  let badge = "SCRIPTED", badgeSub: string | null = null, stamp = "RULE", soloCaption = "no probabilities reported";
  switch (tone) {
    case "drex": badge = "DREX LIVE"; stamp = "CHOSEN"; break;
    case "guard": badge = "DREX UNSURE"; badgeSub = "→ SAFE ACTION"; stamp = "SAFE"; break;
    case "fallback":
      badge = "FALLBACK"; badgeSub = d.errorType ? pretty(d.errorType).toUpperCase() : "SAFE ACTION"; stamp = "FALLBACK";
      soloCaption = "no probabilities reported · safe action applied"; break;
    case "meta": badge = "META LIVE"; stamp = "CHOSEN"; soloCaption = "no probabilities reported by this provider"; break;
    case "replay": badge = "REPLAY"; badgeSub = "RECORDED"; stamp = "RECORDED"; soloCaption = "no probabilities reported · recorded decision"; break;
    default: badge = "SCRIPTED"; stamp = "RULE"; soloCaption = "no probabilities reported · rule-based, no model called"; break;
  }
  if (stale) stamp = "STALE";

  const cells: Cell[] = [];
  let note: string | null = null;
  if (real) {
    if (d.confidence != null) cells.push({ k: "confidence", v: d.confidence.toFixed(3) });
    if (d.evalMs != null) cells.push({ k: "eval", v: `${fmtMs(d.evalMs)} ms`, title: "Provider-reported evaluation time" });
    if (cells.length < 2 && d.model) cells.push({ k: "model", v: d.model, title: d.model });
    if (d.requestId) cells.push({ k: "request id", v: shortId(d.requestId), title: d.requestId });
  } else if (tone === "fallback") {
    note = d.errorType === "timeout" ? "provider timed out · safe action applied" : "provider call failed · safe action applied";
    if (d.errorType === "unconfigured" || d.errorType === "circuit_open" || d.errorType === "budget_exceeded") note = "no provider call was made · safe action applied";
  } else if (tone === "replay") {
    note = "recorded decision, not a live call";
  } else {
    note = "no model was called";
  }

  let clockBig: string | null = null, clockUnit = "", clockCap = "NO MODEL CALL";
  if (d.latencyMs != null && (real || tone === "fallback")) {
    clockBig = fmtMs(d.latencyMs); clockUnit = "ms"; clockCap = real ? "ROUND TRIP" : "WAITED";
  } else if (tone === "fallback") clockCap = "NO ANSWER";
  else if (real) clockCap = "LATENCY N/A";

  let lineTag = "KNOWS", lineKind: View["lineKind"] = knows ? "knows" : "idle", lineText = knows ?? "decision made";
  if (stale) {
    lineTag = "NOT APPLIED"; lineKind = "bad";
    lineText = d.rejectReason ?? (card.outcome && !card.outcome.ok ? card.outcome.text : "the world moved on before the answer arrived");
  } else if (card.outcome) {
    lineTag = card.outcome.ok ? "ACTION" : "NOT APPLIED"; lineKind = card.outcome.ok ? "ok" : "bad"; lineText = card.outcome.text;
  }

  const tipParts = [tipHead, `${badge}${badgeSub ? ` ${badgeSub}` : ""} · applied "${d.chosen ?? "—"}"`];
  if (d.providerChoice && d.providerChoice !== d.chosen) tipParts.push(`provider answered "${d.providerChoice}"`);
  if (real && d.model) tipParts.push(`model ${d.model}`);
  if (real && trace?.usage) tipParts.push(`tokens ${trace.usage.inputTokens ?? "—"} in / ${trace.usage.outputTokens ?? "—"} out`);
  if (real && d.requestId) tipParts.push(`request ${d.requestId}`);
  if (d.errorType) tipParts.push(`error ${d.errorType}${trace?.errorMessage ? `: ${trace.errorMessage}` : ""}`);
  if (d.rejectReason) tipParts.push(`rejected: ${d.rejectReason}`);

  return {
    tone, thinking: false, stale, badge, badgeSub, stamp, rows, hasProbs, chosen: d.chosen, soloCaption,
    clockBig, clockUnit, clockCap, cells, note, lineTag, lineKind, lineText, tip: tipParts.join("\n"),
  };
}

// ---------- original role sigils (same silhouettes as the city markers) ----------

function RoleShape({ role }: { role: NpcRole }) {
  switch (role) {
    case "WITNESS": return <circle className="dspot-shape" cx="24" cy="24" r="13.5" />;
    case "INFORMANT": return <path className="dspot-shape" d="M24 8.5 L39.5 24 L24 39.5 L8.5 24 Z" />;
    case "TRADER": return <rect className="dspot-shape" x="11.5" y="11.5" width="25" height="25" rx="3" />;
    default: return <path className="dspot-shape" d="M24 8 L37.5 13 L34.5 31 L24 40 L13.5 31 L10.5 13 Z" />;
  }
}
function RoleGlyph({ role }: { role: NpcRole }) {
  switch (role) {
    case "WITNESS":
      return (<><path className="dspot-glyph" d="M14.5 24 C18 18.4 30 18.4 33.5 24 C30 29.6 18 29.6 14.5 24 Z" /><circle className="dspot-glyph-fill" cx="24" cy="24" r="3.1" /></>);
    case "INFORMANT":
      return (<><circle className="dspot-glyph-fill" cx="18.6" cy="24" r="2.1" /><path className="dspot-glyph" d="M22.8 20 A5.6 5.6 0 0 1 22.8 28" /><path className="dspot-glyph" d="M26.4 17.2 A9.6 9.6 0 0 1 26.4 30.8" /></>);
    case "TRADER":
      return (<><path className="dspot-glyph thin" d="M17.5 17 V33 M24 14 V30 M30.5 19.5 V33.5" /><path className="dspot-glyph thick" d="M17.5 21 V30 M24 17 V26 M30.5 23 V30.5" /></>);
    default:
      return <path className="dspot-glyph-fill" d="M24 16.3 L25.76 21.07 L30.85 21.28 L26.85 24.43 L28.23 29.33 L24 26.5 L19.77 29.33 L21.15 24.43 L17.15 21.28 L22.24 21.07 Z" />;
  }
}

/** One line of text; when it does not fit it glides to its end and back (plain ellipsis under reduced motion). */
function Ticker({ text }: { text: string }) {
  const wrap = useRef<HTMLSpanElement>(null);
  const inner = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    const w = wrap.current, i = inner.current;
    if (!w || !i) return;
    const measure = () => {
      const over = i.offsetWidth - w.clientWidth;
      setShift(over > 2 ? over : 0);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(w);
    return () => ro.disconnect();
  }, [text]);
  const style = shift ? ({ "--dspot-shift": `${-shift}px`, "--dspot-ticker-ms": `${Math.round(Math.max(2600, shift * 30))}ms` } as CSSProperties) : undefined;
  return <span className={`dspot-line-text${shift ? " is-ticker" : ""}`} ref={wrap}><span ref={inner} style={style}>{text}</span></span>;
}

function offsetWithin(el: HTMLElement, root: HTMLElement): { x: number; y: number } {
  let x = 0, y = 0;
  let cur: HTMLElement | null = el;
  while (cur && cur !== root) {
    x += cur.offsetLeft; y += cur.offsetTop;
    cur = cur.offsetParent as HTMLElement | null;
  }
  return { x, y };
}

// ---------- one card ----------

interface CardProps {
  card: SpotCard;
  selected: boolean;
  reduce: boolean;
  dwellMs: number;
  onSelect(agentId: string): void;
  onHold(id: string, on: boolean): void;
}

const Card = memo(function Card({ card, selected, reduce, dwellMs, onSelect, onHold }: CardProps) {
  const v = useMemo(() => buildView(card), [card]);
  const roleColor = ROLE_COLOR[card.role] ?? "#ffffff";
  const toneColor = TONE_COLOR[v.tone];
  const inkColor = v.stale ? STALE_COLOR : toneColor;

  const plateRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sigilRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<SVGCircleElement>(null);
  const timerRef = useRef<HTMLSpanElement>(null);
  const stampRef = useRef<HTMLSpanElement>(null);
  const trackRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const fxRef = useRef<CardFx | null>(null);
  const [box, setBox] = useState<{ w: number; h: number; c: number }>({ w: 0, h: 0, c: 0 });

  // Cards that arrive already resolved (queued, or the request predates this screen) wait for the entrance.
  const bornResolved = useRef(card.phase !== "thinking").current;
  const [entered, setEntered] = useState(!bornResolved || reduce);
  useEffect(() => {
    if (entered) return;
    const t = window.setTimeout(() => setEntered(true), 320);
    return () => window.clearTimeout(t);
  }, [entered]);
  const revealed = !v.thinking && entered;
  const solo = revealed && !v.hasProbs;

  const labelsKey = v.rows.map((r) => r.label).join("|");
  const measure = useCallback(() => {
    const plate = plateRef.current;
    if (!plate) return;
    const w = plate.offsetWidth, h = plate.offsetHeight;
    if (!w || !h) return;
    // the plate inherits the root font-size, which is exactly one layout unit; the chamfer is 1.05u (--dspot-c)
    const c = CHAMFER_U * (parseFloat(getComputedStyle(plate).fontSize) || h / CARD_H_U);
    setBox((b) => (b.w === w && b.h === h && b.c === c ? b : { w, h, c }));
    const fx = fxRef.current;
    if (!fx) return;
    const sig = sigilRef.current;
    const so = sig ? offsetWithin(sig, plate) : { x: 0, y: 0 };
    const tracks = [];
    for (const el of trackRefs.current) {
      if (!el) continue;
      const o = offsetWithin(el, plate);
      tracks.push({ x0: o.x, x1: o.x + el.offsetWidth, y: o.y + el.offsetHeight / 2, h: el.offsetHeight });
    }
    fx.setGeometry({ w, h, origin: { x: so.x + (sig?.offsetWidth ?? 0) / 2, y: so.y + (sig?.offsetHeight ?? 0) / 2 }, tracks });
  }, []);

  // canvas layer (not created at all under prefers-reduced-motion)
  useEffect(() => {
    if (reduce) return;
    const cv = canvasRef.current;
    if (!cv) return;
    const fx = createCardFx(cv);
    fxRef.current = fx;
    measure();
    return () => { fx.destroy(); fxRef.current = null; };
  }, [reduce, measure]);

  useLayoutEffect(() => {
    measure();
    const plate = plateRef.current;
    if (!plate) return;
    const ro = new ResizeObserver(measure);
    ro.observe(plate);
    return () => ro.disconnect();
  }, [measure, labelsKey]);

  // thinking: thought streams + elapsed timer + deadline ring (all outside React state)
  const thinking = v.thinking && card.phase !== "leaving";
  useEffect(() => {
    if (!thinking) return;
    fxRef.current?.think(roleColor);
    const deadline = card.mode === "drex" || card.mode === "meta" ? RULES.decisions.deadlineMs[card.mode] : null;
    let shown = "";
    const paint = (now: number) => {
      const ms = Math.max(0, now - card.bornAt);
      const txt = reduce || ms >= 9995 ? (ms / 1000).toFixed(1) : (ms / 1000).toFixed(2);
      if (txt !== shown && timerRef.current) { timerRef.current.textContent = txt; shown = txt; }
      if (deadline && ringRef.current) ringRef.current.style.strokeDashoffset = String(100 * (1 - Math.min(1, ms / deadline)));
    };
    paint(performance.now());
    const off = onFrame(paint);
    const ring = ringRef.current;
    return () => { off(); if (ring) ring.style.strokeDashoffset = ""; };
  }, [thinking, card.bornAt, card.mode, roleColor, reduce]);

  // resolve: bars race to the real numbers, comets ride their leading edges, the stamp lands with a shockwave.
  // The percentages are never counted up: every number that is ever on screen is the provider's real number.
  const live = useRef(v);
  live.current = v;
  useEffect(() => {
    if (!revealed || reduce) { if (!thinking) fxRef.current?.stop(); return; }
    const fx = fxRef.current;
    if (!fx) return;
    const cur = live.current;
    measure();
    fx.resolve({ fills: cur.rows.map((r) => r.p), chosen: cur.rows.findIndex((r) => r.chosen), color: TONE_COLOR[cur.tone] });
    const t = window.setTimeout(() => {
      const st = stampRef.current, plate = plateRef.current;
      if (!st || !plate) return;
      const o = offsetWithin(st, plate);
      fx.stamp(o.x + st.offsetWidth / 2, o.y + st.offsetHeight / 2, live.current.stale ? STALE_COLOR : TONE_COLOR[live.current.tone]);
    }, STAMP_AT_MS);
    return () => window.clearTimeout(t);
  }, [revealed, reduce, measure, thinking]);

  const onKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(card.agentId); }
  };

  const { w, h, c } = box;
  const frame = w && h ? `M${c + 1} 1 H${w - 1} V${h - c - 1} L${w - c - 1} ${h - 1} H1 V${c + 1} Z` : "";
  const cls = [
    "dspot-card", `t-${v.tone}`, `r-${card.role.toLowerCase()}`,
    thinking ? "is-thinking" : "is-resolved", revealed ? "is-revealed" : "", solo ? "is-solo" : "",
    v.stale ? "is-stale" : "", selected ? "is-selected" : "",
  ].filter(Boolean).join(" ");
  const style = { "--dspot-role": roleColor, "--dspot-tone": toneColor, "--dspot-ink": inkColor, "--dspot-dwell": `${dwellMs}ms` } as CSSProperties;
  const aria = v.thinking
    ? `${card.name}, ${card.role.toLowerCase()}, is deciding between ${v.rows.map((r) => r.label).join(", ")}. Activate to inspect.`
    : `${card.name}, ${card.role.toLowerCase()}: ${v.chosen ?? "no action"}. ${v.badge}${v.badgeSub ? ` ${v.badgeSub}` : ""}${v.stale ? ", rejected as stale" : ""}. Activate to inspect.`;

  const chosenP = v.rows.find((r) => r.chosen)?.p ?? null;
  const stampEl: ReactNode = revealed && v.stamp ? (
    <span className={`dspot-stamp${chosenP != null && chosenP > 0.5 ? " at-left" : ""}`} ref={stampRef} aria-hidden>
      <svg viewBox="0 0 16 16" className="dspot-stamp-ico">{v.stale
        ? <path d="M4 4 L12 12 M12 4 L4 12" />
        : <path d="M3 8.6 L6.6 12 L13 4.4" />}</svg>
      {v.stamp}
    </span>
  ) : null;

  return (
    <div className={cls} style={style} role="button" tabIndex={card.phase === "leaving" ? -1 : 0} aria-label={aria} title={v.tip}
      onClick={() => onSelect(card.agentId)} onKeyDown={onKey}
      onPointerEnter={() => onHold(card.id, true)} onPointerLeave={() => onHold(card.id, false)}
      onFocus={() => onHold(card.id, true)} onBlur={() => onHold(card.id, false)}>
      <div className="dspot-aura" aria-hidden />
      <div className="dspot-plate" ref={plateRef}>
        {!reduce && <canvas className="dspot-fx" ref={canvasRef} aria-hidden />}
        <div className="dspot-sweep" aria-hidden />

        <div className="dspot-head">
          <div className="dspot-sigil" ref={sigilRef} aria-hidden>
            <svg viewBox="0 0 48 48">
              <circle className="dspot-ring-track" cx="24" cy="24" r="22" />
              <g className="dspot-ring-spin"><circle className="dspot-ring-dash" cx="24" cy="24" r="18.6" /></g>
              <circle className="dspot-ring-prog" ref={ringRef} cx="24" cy="24" r="22" pathLength={100} />
              <RoleShape role={card.role} />
              <RoleGlyph role={card.role} />
            </svg>
          </div>
          <div className="dspot-id">
            <div className="dspot-who"><span className="dspot-name">{card.name}</span><b className="dspot-role">{card.role}</b></div>
            <div className="dspot-sub">{card.why ?? `${card.role.toLowerCase()} is weighing a decision`}</div>
          </div>
          <div className="dspot-clock">
            <div className="dspot-clock-big">
              {v.thinking
                ? <><span key="t" ref={timerRef}>0.00</span><i>s</i></>
                : v.clockBig != null ? <><span key="l">{v.clockBig}</span><i>{v.clockUnit}</i></> : <span key="n" className="dspot-clock-none">—</span>}
            </div>
            <div className="dspot-clock-cap">{v.clockCap}{v.thinking ? <span className="dspot-dots" aria-hidden><i /><i /><i /></span> : null}</div>
          </div>
        </div>

        <div className="dspot-opts">
          <div className="dspot-rows" data-n={v.rows.length}>
            {v.rows.map((r, i) => (
              <div key={r.label} className={`dspot-row${r.chosen && revealed ? " is-chosen" : ""}${r.pick && revealed ? " is-pick" : ""}`} style={{ "--dspot-i": i } as CSSProperties}>
                <span className={`dspot-label${r.label.length > 9 ? " is-long" : ""}`}>{r.label}</span>
                <span className="dspot-track" ref={(el) => { trackRefs.current[i] = el; }}>
                  <i className="dspot-fill" style={{ "--dspot-p": revealed && r.p != null ? r.p : 0 } as CSSProperties} />
                  {r.pick && revealed ? <em className="dspot-pick">PROVIDER PICK</em> : null}
                  {r.chosen && !solo ? stampEl : null}
                </span>
                <span className="dspot-pct">{revealed && v.hasProbs ? (r.p == null ? "—" : fmtPct(r.p)) : ""}</span>
              </div>
            ))}
          </div>
          <div className="dspot-solo" aria-hidden={!solo}>
            <div className="dspot-solo-main">
              <span className="dspot-solo-label">{v.chosen ?? "—"}</span>
              {solo ? stampEl : null}
            </div>
            <div className="dspot-solo-cap">{v.soloCaption}</div>
            <div className="dspot-solo-off">offered: {v.rows.map((r) => r.label).join(" · ")}</div>
          </div>
        </div>

        <div className={`dspot-line k-${v.lineKind}`}>
          <span className="dspot-line-tag">{v.lineTag}</span>
          <Ticker text={v.lineText} />
        </div>

        <div className="dspot-foot">
          <span className="dspot-badge">
            <span className="dspot-badge-main">{v.tone === "drex" || v.tone === "meta" ? <i className="dspot-live-dot" aria-hidden /> : null}{v.badge}</span>
            {v.badgeSub ? <small>{v.badgeSub}</small> : null}
          </span>
          {v.note ? <span className="dspot-note">{v.note}</span> : (
            <span className="dspot-cells">
              {v.cells.map((cell) => (
                <span className="dspot-cell" key={cell.k} title={cell.title}><span className="dspot-k">{cell.k}</span><span className="dspot-v">{cell.v}</span></span>
              ))}
            </span>
          )}
        </div>
        <div className="dspot-drain" aria-hidden />
      </div>
      <svg className="dspot-frame" aria-hidden>
        {frame ? (<>
          <path className="dspot-frame-base" d={frame} />
          <path className="dspot-frame-beam" d={frame} pathLength={100} />
          <path className="dspot-frame-beam b2" d={frame} pathLength={100} />
          <path className="dspot-corner a" d={`M0 ${c * 0.62} V0 H${c * 0.62} Z`} />
          <path className="dspot-corner b" d={`M${w} ${h - c * 0.62} V${h} H${w - c * 0.62} Z`} />
        </>) : null}
      </svg>
    </div>
  );
});

// ---------- the stack ----------

export default function DecisionSpotlight({ maxCards = 3, dock = "none", dwellMs = 6000, minScale = 0.85, className, style }: DecisionSpotlightProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const cap = Math.max(1, Math.min(3, Math.floor(maxCards)));
  const fit = useFit(rootRef, cap, minScale);
  const exitMs = reduce ? 0 : EXIT_MS;

  const deck = useMemo(() => createDeck({ capacity: cap, dwellMs, exitMs: EXIT_MS }), []); // eslint-disable-line react-hooks/exhaustive-deps
  const snap = useSyncExternalStore(deck.subscribe, deck.getSnapshot, deck.getSnapshot);
  // Narrow selector: this component re-renders on selection changes and deck changes only, never on world ticks.
  const selectedAgentId = useGame((s) => s.selectedAgentId);

  useEffect(() => { deck.setCapacity(fit); }, [deck, fit]);
  useEffect(() => { deck.setTimings({ dwellMs, exitMs }); }, [deck, dwellMs, exitMs]);

  useEffect(() => {
    // Everything already in the log when we mount is history: it is recorded as seen and never replayed.
    deck.baseline(useGame.getState());
    const unsub = useGame.subscribe((s, prev) => {
      if (s.events !== prev.events || s.runId !== prev.runId) deck.ingest(s);
      if (s.npcs !== prev.npcs) deck.syncTraces(s);
    });
    const iv = window.setInterval(() => deck.tick(performance.now()), 100);
    return () => { unsub(); window.clearInterval(iv); deck.clear(); };
  }, [deck]);

  const onSelect = useCallback((agentId: string) => useGame.getState().selectAgent(agentId), []);
  const onHold = useCallback((id: string, on: boolean) => deck.hold(id, on), [deck]);

  const liveCount = snap.stage.reduce((n, c) => n + (c.phase === "leaving" ? 0 : 1), 0);
  const cls = ["dspot-root", dock !== "none" ? `is-dock-${dock}` : "", liveCount > 0 ? "is-live" : "", className ?? ""].filter(Boolean).join(" ");

  return (
    <div ref={rootRef} className={cls} style={style} role="region" aria-label="Live agent decisions">
      <div className="dspot-title" aria-hidden={liveCount === 0}>
        <span className="dspot-title-mark" aria-hidden><i /><i /><i /></span>
        <span className="dspot-title-text">WATCH THE SWARM THINK</span>
        <span className="dspot-title-count">
          {snap.thinking > 0 ? <b>{snap.thinking} deciding</b> : snap.queued > 0 ? null : <b className="quiet">settled</b>}
          {snap.queued > 0 ? <em>+{snap.queued} queued</em> : null}
        </span>
      </div>
      {snap.stage.map((card) => (
        <div key={card.id} className={`dspot-slot${card.phase === "leaving" ? " is-leaving" : ""}`}>
          <Card card={card} selected={selectedAgentId === card.agentId} reduce={reduce} dwellMs={dwellMs} onSelect={onSelect} onHold={onHold} />
        </div>
      ))}
    </div>
  );
}
