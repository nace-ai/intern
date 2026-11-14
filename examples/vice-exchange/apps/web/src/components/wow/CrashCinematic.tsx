// CrashCinematic: the full-viewport "ticker goes to V$0" moment.
//
// Mount once, anywhere inside the app tree (it renders through a portal on <body>): <CrashCinematic />
// It listens to the game store for a CAPTURE_SUCCEEDED / LISTING_HALTED pair that arrives LIVE, then plays
// ~4.5 s: flash + shake, the ticker huge, the last price crashing to exactly V$0, a price line falling off a
// cliff, the capture facts, and a stamped "HALTED · V$0" plate. Click or Escape dismisses early.
//
// - Never replays history: events present at mount, and events that arrive inside a snapshot (join, resync,
//   respawn, reset), only move the cursor.
// - Two captures together are queued and play one after the other.
// - prefers-reduced-motion gets a calm variant: one still frame, soft fade, no shake, no flash, no particles.
// - No React re-render while the world ticks: the store is read through a raw subscription that returns
//   immediately unless the event log itself changed, and the animation writes to refs from one rAF loop.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactElement } from "react";
import { createPortal } from "react-dom";
import { CREDITS_NOTICE } from "@vice/contracts";
import type { PricePoint } from "@vice/contracts";
import { useGame } from "../../state/gameStore";
import type { GameState } from "../../state/gameStore";
import { vd } from "../../lib/format";
import {
  TL, buildFx, clamp01, drawFx, easeInCubic, easeOutBack, easeOutCubic, energyAt, flashAt, hashString, noise, priceAt, shakeAt,
} from "./CrashCinematicFx";
import type { FxModel } from "./CrashCinematicFx";
import { highestSequence, isSnapshotUpdate, isStaleEvent, newestSimTime, pickCrashTriggers } from "./CrashCinematicTriggers";
import type { CrashTrigger } from "./CrashCinematicTriggers";
import "./CrashCinematic.css";

export { highestSequence, isSnapshotUpdate, isStaleEvent, newestSimTime, pickCrashTriggers } from "./CrashCinematicTriggers";
export type { CaptureEvent, CrashTrigger, HaltEvent } from "./CrashCinematicTriggers";

/** Length of one cinematic, fade-out included. */
export const CRASH_CINEMATIC_MS = TL.end;
/** How long a capture waits for its halt (or the reverse) before playing with what it has. */
const PAIR_WINDOW_MS = 160;
const DISMISS_MS = 180;
const CALM = { fadeIn: 320, frame: TL.out - 1, out: 4100, end: 4450 } as const;
const GLITCH = "▚▞▓▒░#%";
const fmt = new Intl.NumberFormat("en-US");

/** Everything one cinematic shows. Fields are null when the payload that carries them never arrived. */
export interface CrashShot {
  key: string;
  /** serverSequence of the capture (or of the halt when the capture event is missing). */
  sequence: number;
  ticker: string;
  /** Captured character's nickname, when the store knows it. */
  nickname: string | null;
  /** Spot price just before the halt. */
  lastSpot: number;
  referenceAtCapture: number | null;
  bounty: number | null;
  /** Crew capture: how the bounty was paid out. */
  bountyNote: string | null;
  captorName: string | null;
  /** "POLICE NPC", "HUMAN HUNTER", ... derived from captorKind plus the captor's role in the store. */
  captorLabel: string | null;
  captorIsNpc: boolean | null;
  survivalSeconds: number | null;
  /** Real price history up to the halt; empty when the listing is not in the store. */
  history: PricePoint[];
}

type ShotSource = Pick<GameState, "listings" | "characters" | "npcs" | "players">;

/** Turns a trigger into what the cinematic shows. Reads names and price history from the store, invents nothing. */
export function buildCrashShot(tr: CrashTrigger, s: ShotSource): CrashShot {
  const cap = tr.capture?.payload ?? null;
  const halt = tr.halt?.payload ?? null;
  const listing = s.listings[tr.ticker];
  const history = (listing?.history ?? []).filter((p) => p.spot > 0).slice(-240);
  let captorLabel: string | null = null;
  if (cap) {
    if (cap.captorKind === "NPC") {
      const role = s.npcs[cap.captorId]?.role;
      captorLabel = role ? `${role} NPC` : "NPC";
    } else {
      const role = s.players[cap.captorId]?.role;
      captorLabel = role ? `HUMAN ${role}` : "HUMAN";
    }
  }
  return {
    key: tr.key, sequence: tr.sequence, ticker: tr.ticker,
    nickname: s.characters[tr.characterId]?.nickname ?? listing?.nickname ?? null,
    lastSpot: halt?.finalSpotBeforeHalt ?? cap?.spotBeforeCapture ?? listing?.finalSpotBeforeHalt ?? history[history.length - 1]?.spot ?? 0,
    referenceAtCapture: cap ? cap.referenceAtCapture : null,
    bounty: cap ? cap.bounty : null,
    bountyNote: !cap ? null : cap.doubleCross ? `DOUBLE-CROSS` : cap.shares && cap.shares.length > 1 ? `split ${cap.shares.length} ways` : null,
    captorName: cap ? cap.captorName : null,
    captorLabel, captorIsNpc: cap ? cap.captorKind === "NPC" : null,
    survivalSeconds: cap ? cap.survivalSeconds : null,
    history,
  };
}

function duration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

interface Stat { k: string; v: string; sub: ReactElement | string; long?: boolean }
function statsOf(shot: CrashShot): Stat[] {
  const out: Stat[] = [];
  if (shot.captorName) {
    out.push({
      k: "Captured by", v: shot.captorName, long: shot.captorName.length > 13,
      sub: shot.captorLabel ? <span className={`cc-chip ${shot.captorIsNpc ? "npc" : "human"}`}>{shot.captorLabel}</span> : "",
    });
  }
  if (shot.bounty != null) out.push({ k: "Bounty paid", v: vd(shot.bounty), sub: shot.bountyNote ?? (shot.bounty > 0 ? "credited to the captor" : "none paid") });
  if (shot.survivalSeconds != null) out.push({ k: "Survived", v: duration(shot.survivalSeconds), sub: "spawn to capture" });
  if (shot.referenceAtCapture != null) out.push({ k: "Reference", v: vd(shot.referenceAtCapture), sub: "value at capture" });
  out.push({ k: "Spot before", v: vd(shot.lastSpot), sub: "last price, now V$0" });
  return out;
}

function spoken(shot: CrashShot): string {
  const by = shot.captorName ? ` by ${shot.captorName}${shot.captorLabel ? ` (${shot.captorLabel.toLowerCase()})` : ""}` : "";
  const parts = [`${shot.ticker} captured${by}. Trading halted at V$0.`, `Spot price just before: ${vd(shot.lastSpot)}.`];
  if (shot.referenceAtCapture != null) parts.push(`Reference value at capture: ${vd(shot.referenceAtCapture)}.`);
  if (shot.survivalSeconds != null) parts.push(`Survived ${duration(shot.survivalSeconds)}.`);
  if (shot.bounty != null) parts.push(shot.bounty > 0 ? `Bounty paid: ${vd(shot.bounty)}.` : "No bounty paid.");
  return parts.join(" ");
}

// ------------------------------------------------------------------------------------------------ stage
interface StageProps { shot: CrashShot; calm: boolean; waiting: number; freezeAtMs?: number; onDone(): void }

function CrashStage({ shot, calm, waiting, freezeAtMs, onDone }: StageProps): ReactElement {
  const rootRef = useRef<HTMLDivElement>(null);
  const shakeRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const flashRef = useRef<HTMLDivElement>(null);
  const pulseRef = useRef<HTMLDivElement>(null);
  const pctRef = useRef<HTMLSpanElement>(null);
  const runTopRef = useRef<HTMLDivElement>(null);
  const runBotRef = useRef<HTMLDivElement>(null);
  const kickerRef = useRef<HTMLDivElement>(null);
  const tickerSlotRef = useRef<HTMLDivElement>(null);
  const tickerFxRef = useRef<HTMLDivElement>(null);
  const ghostARef = useRef<HTMLSpanElement>(null);
  const ghostPRef = useRef<HTMLSpanElement>(null);
  const slice1Ref = useRef<HTMLSpanElement>(null);
  const slice2Ref = useRef<HTMLSpanElement>(null);
  const readoutFxRef = useRef<HTMLDivElement>(null);
  const captionRef = useRef<HTMLDivElement>(null);
  const plateSlotRef = useRef<HTMLDivElement>(null);
  const plateRef = useRef<HTMLDivElement>(null);
  const cellRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const dismissRef = useRef<() => void>(() => {});
  const onDoneRef = useRef(onDone);
  useEffect(() => { onDoneRef.current = onDone; }, [onDone]);

  const stats = useMemo(() => statsOf(shot), [shot]);
  const maxChars = `V$${fmt.format(Math.round(shot.lastSpot))}`.length;
  const tapeUnit = (
    <span className="cc-tape-unit">{shot.ticker} HALTED<i>▼</i>V$0<i>▼</i>CAPTURED<i>▼</i>TRADING CLOSED<i>▼</i></span>
  );

  useLayoutEffect(() => {
    const root = rootRef.current, shake = shakeRef.current, canvas = canvasRef.current, flash = flashRef.current;
    const runTop = runTopRef.current, runBot = runBotRef.current, kicker = kickerRef.current;
    const tickerSlot = tickerSlotRef.current, tickerFx = tickerFxRef.current, ghostA = ghostARef.current, ghostP = ghostPRef.current;
    const readoutFx = readoutFxRef.current, caption = captionRef.current, plateSlot = plateSlotRef.current, plate = plateRef.current;
    if (!root || !shake || !canvas || !flash || !runTop || !runBot || !kicker || !tickerSlot || !tickerFx || !ghostA || !ghostP || !readoutFx || !caption || !plateSlot || !plate) return;
    const slices = [slice1Ref.current, slice2Ref.current];
    const pulse = pulseRef.current, pct = pctRef.current;
    let shownPct = "";
    const statEls = Array.from(caption.children) as HTMLElement[];
    const ctx = canvas.getContext("2d");
    const seed = hashString(shot.ticker) | 0;
    const frozen = freezeAtMs != null;
    const shown: string[] = [];
    let model: FxModel | null = null;
    let pixel = 1, unitW = 1, raf = 0, ended = false, dismissAt: number | null = null;
    const started = performance.now();

    const measure = (): void => {
      // Measure with every animated transform cleared so the boxes are the resting layout.
      shake.style.transform = "none"; readoutFx.style.transform = "none";
      const R = root.getBoundingClientRect();
      const w = Math.max(320, R.width), h = Math.max(240, R.height);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      pixel = Math.max(0.5, Math.min(dpr, Math.sqrt(4.2e6 / (w * h)))); // cap the backing store near 4 megapixels
      canvas.width = Math.round(w * pixel); canvas.height = Math.round(h * pixel);
      const tk = tickerSlot.getBoundingClientRect(), rd = readoutFx.getBoundingClientRect(), pl = plateSlot.getBoundingClientRect();
      unitW = Math.max(1, runTop.firstElementChild?.getBoundingClientRect().width ?? 1);
      model = buildFx(
        { ticker: shot.ticker, lastSpot: shot.lastSpot, reference: shot.referenceAtCapture, history: shot.history },
        {
          w, h,
          ticker: { x: tk.left - R.left + tk.width / 2, y: tk.top - R.top },
          readout: { x: rd.left - R.left, y: rd.top - R.top, w: rd.width, h: rd.height },
          plate: { x: pl.left - R.left, y: pl.top - R.top, w: pl.width, h: pl.height },
        },
      );
    };

    const draw = (t: number, alpha: number, zoom: number): void => {
      const m = model;
      root.style.opacity = alpha.toFixed(3);
      if (!m) return;
      const energy = calm ? 0 : energyAt(m, t);

      // screen shake, flash, canvas
      const sh = calm ? { x: 0, y: 0, r: 0 } : shakeAt(m, t);
      shake.style.transform = `translate3d(${sh.x.toFixed(2)}px,${sh.y.toFixed(2)}px,0) rotate(${sh.r.toFixed(3)}deg) scale(${zoom.toFixed(4)})`;
      const fl = calm ? { alpha: 0, rgb: "0,0,0" } : flashAt(t);
      flash.style.opacity = fl.alpha.toFixed(3);
      if (fl.alpha > 0) flash.style.backgroundColor = `rgb(${fl.rgb})`;
      if (ctx) { ctx.setTransform(pixel, 0, 0, pixel, 0, 0); drawFx(ctx, m, t, calm); }
      if (pulse) pulse.style.opacity = Math.min(0.8, energy * 0.024).toFixed(3); // the room throbs with every lurch

      // hazard tape
      const shift = calm ? 0 : (t * 0.34 * m.scale) % unitW;
      runTop.style.transform = `translate3d(${(-shift).toFixed(1)}px,0,0)`;
      runBot.style.transform = `translate3d(${(shift - unitW).toFixed(1)}px,0,0)`;

      // kicker wipes in
      kicker.style.clipPath = calm ? "none" : `inset(0 ${(100 - 100 * easeOutCubic((t - 60) / 300)).toFixed(1)}% 0 0)`;

      // ticker: slam in, chromatic split, glitch slices, then it dies at zero
      tickerFx.style.transform = `scale(${(calm ? 1 : 2.7 - 1.7 * easeOutBack(t / 420)).toFixed(4)})`;
      tickerFx.style.opacity = calm ? "1" : clamp01(t / 60).toFixed(3);
      tickerFx.classList.toggle("is-dead", t >= TL.zero);
      const split = Math.min(0.2, 0.0042 * energy);
      ghostA.style.transform = `translate3d(${(-split).toFixed(4)}em,${(split * 0.25).toFixed(4)}em,0)`;
      ghostP.style.transform = `translate3d(${split.toFixed(4)}em,${(-split * 0.25).toFixed(4)}em,0)`;
      ghostA.style.opacity = ghostP.style.opacity = Math.min(0.95, split * 40).toFixed(3); // fades with the split, never pops
      const win = Math.floor(t / 70), act = clamp01(energy / 12);
      for (let j = 0; j < slices.length; j++) {
        const el = slices[j];
        if (!el) continue;
        if (!(act > 0.04 && noise(win * 2 + j, seed) < 0.2 + 0.7 * act)) { el.style.opacity = "0"; continue; }
        const top = noise(win, seed + 11 + j) * 76, hgt = 7 + noise(win, seed + 23 + j) * 17;
        el.style.opacity = "1";
        el.style.clipPath = `inset(${top.toFixed(1)}% -20% ${(100 - top - hgt).toFixed(1)}% -20%)`;
        el.style.transform = `translate3d(${((noise(win, seed + 37 + j) - 0.5) * 0.36 * (0.35 + act)).toFixed(4)}em,0,0)`;
      }

      // price readout: the real last price falling to exactly V$0
      const price = priceAt(m, t);
      const text = `V$${fmt.format(price)}`;
      const zeroed = t >= TL.zero;
      const moving = !calm && t > TL.fall0 && !zeroed && price !== priceAt(m, t - 34);
      const gi = Math.floor(t / 34);
      for (let i = 0; i < cellRefs.current.length; i++) {
        const el = cellRefs.current[i];
        if (!el) continue;
        let ch = i < text.length ? text[i]! : "";
        let ty = 0;
        if (moving && i >= 2 && ch) {
          const wgt = 0.35 + 0.65 * ((i - 1) / Math.max(1, text.length - 2));
          ty = (noise(gi, seed + i * 7 + 1) - 0.5) * 0.16 * wgt;
          if (noise(gi, seed + i * 13 + 5) < 0.09 * wgt) ch = GLITCH[Math.floor(noise(gi, i + 3) * GLITCH.length)]!;
        }
        if (shown[i] !== ch) { shown[i] = ch; el.textContent = ch; el.style.display = ch ? "" : "none"; el.classList.toggle("sep", ch === ","); }
        el.style.transform = ty ? `translate3d(0,${ty.toFixed(3)}em,0)` : "";
      }
      if (pct) {
        // Derived from the number on screen: it reads -100% only once the price reads exactly V$0.
        const drop = m.last > 0 ? (1 - price / m.last) * 100 : 100;
        const label = t <= TL.fall0 ? "" : zeroed ? "▼ −100%" : `▼ −${Math.min(99.9, drop).toFixed(1)}%`;
        if (label !== shownPct) { shownPct = label; pct.textContent = label; }
      }
      const heat = clamp01((t - TL.fall0) / (TL.zero - TL.fall0));
      readoutFx.classList.toggle("is-zero", zeroed);
      readoutFx.style.color = zeroed ? "" : `rgb(255,${Math.round(255 - 150 * heat)},${Math.round(255 - 130 * heat)})`;
      readoutFx.style.setProperty("--cc-c", Math.min(0.09, 0.0026 * energy).toFixed(4));
      readoutFx.style.transform = `scale(${(zeroed && !calm ? 1 + 0.45 * Math.exp(-(t - TL.zero) / 120) : 1).toFixed(4)})`;
      readoutFx.style.opacity = (1 - clamp01((t - (TL.stamp - 40)) / 120)).toFixed(3);

      // caption cells rise in one after the other
      for (let i = 0; i < statEls.length; i++) {
        const u = calm ? 1 : clamp01((t - (720 + i * 120)) / 280);
        const el = statEls[i]!;
        el.style.opacity = u.toFixed(3);
        el.style.transform = `translate3d(0,${((1 - easeOutCubic(u)) * 1.8).toFixed(3)}vh,0)`;
      }

      // the plate comes down fast and lands exactly on TL.stamp
      const d = t - (TL.stamp - 110);
      if (d < 0) { plate.style.opacity = "0"; }
      else if (d < 110 && !calm) {
        const u = d / 110;
        plate.style.opacity = clamp01(d / 45).toFixed(3);
        plate.style.transform = `rotate(${(-13 + 7 * u).toFixed(2)}deg) scale(${(3.4 - 2.4 * easeInCubic(u)).toFixed(4)})`;
        plate.style.filter = "";
      } else {
        const v = clamp01((d - 110) / 220);
        plate.style.opacity = "1";
        plate.style.transform = `rotate(-6deg) scale(${(1 + 0.07 * Math.sin(Math.PI * v) * (1 - v)).toFixed(4)})`;
        plate.style.filter = calm ? "" : `brightness(${(1 + 0.9 * Math.exp(-(d - 110) / 170)).toFixed(3)})`;
      }
    };

    const finish = (): void => {
      if (ended) return;
      ended = true; cancelAnimationFrame(raf);
      onDoneRef.current();
    };
    const paint = (now: number): void => {
      if (ended) return;
      const real = freezeAtMs ?? now - started;
      const outAt = calm ? CALM.out : TL.out, endAt = calm ? CALM.end : TL.end;
      const out = dismissAt != null ? 1 - clamp01((real - dismissAt) / DISMISS_MS) : 1 - clamp01((real - outAt) / (endAt - outAt));
      if (!frozen && (dismissAt != null ? real >= dismissAt + DISMISS_MS : real >= endAt)) { finish(); return; }
      if (calm) draw(CALM.frame, frozen ? 1 : Math.min(easeOutCubic(real / CALM.fadeIn), out), 1);
      else draw(real, Math.min(clamp01(0.4 + real / 70), out), 1 + 0.04 * easeInCubic(1 - out));
      raf = requestAnimationFrame(paint);
    };
    dismissRef.current = (): void => {
      if (frozen) { finish(); return; }
      if (dismissAt == null) dismissAt = performance.now() - started;
    };
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") dismissRef.current(); };

    measure();
    paint(started); // first frame synchronously, before the browser paints
    window.addEventListener("resize", measure);
    window.addEventListener("keydown", onKey);
    return () => {
      ended = true;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", measure);
      window.removeEventListener("keydown", onKey);
      dismissRef.current = () => {};
    };
  }, [shot, calm, freezeAtMs]);

  const style = { "--cc-len": Math.max(4, shot.ticker.length), "--cc-chars": maxChars } as CSSProperties;
  return (
    <div ref={rootRef} className={`cc-root${calm ? " cc-calm" : ""}`} style={style} role="alert" aria-live="assertive" onClick={() => dismissRef.current()}>
      <div className="cc-bg" aria-hidden />
      <div ref={pulseRef} className="cc-pulse" aria-hidden />
      <div ref={shakeRef} className="cc-shake" aria-hidden>
        <canvas ref={canvasRef} className="cc-fx" />
        <div className="cc-tape top"><div ref={runTopRef} className="cc-tape-run">{tapeUnit}{tapeUnit}{tapeUnit}{tapeUnit}{tapeUnit}{tapeUnit}</div></div>
        <div ref={kickerRef} className="cc-kicker"><b>Captured</b><span>{shot.nickname ?? shot.ticker}</span><i>listing halted</i></div>
        <div ref={tickerSlotRef} className="cc-ticker">
          <div ref={tickerFxRef} className="cc-ticker-fx">
            <span ref={ghostARef} className="cc-tk cc-tk-a">{shot.ticker}</span>
            <span ref={ghostPRef} className="cc-tk cc-tk-p">{shot.ticker}</span>
            <span className="cc-tk cc-tk-main">{shot.ticker}</span>
            <span ref={slice1Ref} className="cc-tk cc-tk-s">{shot.ticker}</span>
            <span ref={slice2Ref} className="cc-tk cc-tk-s">{shot.ticker}</span>
          </div>
        </div>
        <div className="cc-readout">
          <div ref={readoutFxRef} className="cc-readout-fx">
            <span className="cc-readout-k">SPOT PRICE</span>
            <span ref={pctRef} className="cc-readout-pct" />
            {Array.from({ length: maxChars }, (_, i) => (
              <span key={i} ref={(el) => { cellRefs.current[i] = el; }} className={`cc-cell${i < 2 ? " pre" : ""}`} />
            ))}
          </div>
        </div>
        <div ref={captionRef} className={`cc-caption${shot.captorName ? " has-captor" : ""}`}>
          {stats.map((s) => (
            <div key={s.k} className="cc-stat">
              <div className="cc-stat-k">{s.k}</div>
              <div className={`cc-stat-v${s.long ? " long" : ""}`}>{s.v}</div>
              <div className="cc-stat-s">{s.sub}</div>
            </div>
          ))}
        </div>
        <div ref={plateSlotRef} className="cc-plate-slot">
          <div ref={plateRef} className="cc-plate">
            <div className="cc-plate-top">Trading halt</div>
            <div className="cc-plate-main">HALTED<em>·</em>V$0</div>
            <div className="cc-plate-bot">{shot.ticker} · event #{shot.sequence} · history kept</div>
          </div>
        </div>
        {waiting > 0 && <div className="cc-more">+{waiting} MORE CAPTURE{waiting === 1 ? "" : "S"} QUEUED</div>}
        <div className="cc-foot"><span>{CREDITS_NOTICE}</span><span>CLICK OR ESC TO DISMISS</span></div>
        <div className="cc-tape bot"><div ref={runBotRef} className="cc-tape-run">{tapeUnit}{tapeUnit}{tapeUnit}{tapeUnit}{tapeUnit}{tapeUnit}</div></div>
      </div>
      <div className="cc-scan" aria-hidden />
      <div className="cc-vig" aria-hidden />
      <div ref={flashRef} className="cc-flash" aria-hidden />
      <p className="cc-sr">{spoken(shot)}</p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ store wiring
function usePrefersReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const on = (): void => setReduced(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** Queue of crashes that happened live while this hook was mounted. */
function useLiveCrashes(): { shot: CrashShot | null; waiting: number; done(): void } {
  const [shot, setShot] = useState<CrashShot | null>(null);
  const [waiting, setWaiting] = useState(0);
  const queue = useRef<CrashShot[]>([]);
  const playing = useRef(false);

  const advance = useCallback((): void => {
    const next = queue.current.shift() ?? null;
    playing.current = next !== null;
    setShot(next);
    setWaiting(queue.current.length);
  }, []);

  useEffect(() => {
    const first = useGame.getState();
    // Everything already in the log is history: remember where it ends and which crashes it holds.
    let cursor = highestSequence(first.events);
    const seen = new Set<string>(pickCrashTriggers(first.events, Number.NEGATIVE_INFINITY).map((t) => t.key));
    const pending = new Map<string, CrashTrigger>();
    let timer: ReturnType<typeof setTimeout> | null = null;

    const play = (tr: CrashTrigger): void => {
      pending.delete(tr.key);
      if (seen.has(tr.key)) return;
      seen.add(tr.key);
      queue.current.push(buildCrashShot(tr, useGame.getState()));
      if (playing.current) setWaiting(queue.current.length); else advance();
    };
    const settle = (): void => {
      timer = null;
      const st = useGame.getState();
      for (const tr of [...pending.values()]) {
        // The halt is what proves V$0. If its event never showed up, the listing itself must say so.
        if (tr.halt || st.listings[tr.ticker]?.status === "HALTED_CAPTURED") play(tr);
        else pending.delete(tr.key);
      }
    };
    const clear = (): void => {
      pending.clear();
      if (timer) { clearTimeout(timer); timer = null; }
      queue.current = []; playing.current = false;
      setShot(null); setWaiting(0);
    };

    const unsubscribe = useGame.subscribe((st, prev) => {
      if (prev.session && !st.session) clear();                      // left the room
      if (prev.runId && st.runId !== prev.runId) clear();            // seeded reset or another room
      if (st.events === prev.events) return;                         // world ticks and panel state end here
      if (isSnapshotUpdate(st, prev)) {
        // Join, resync, respawn or reset snapshot: whatever it carries is history, even if we never saw it.
        cursor = highestSequence(st.events);
        for (const tr of pickCrashTriggers(st.events, Number.NEGATIVE_INFINITY)) if (!pending.has(tr.key)) seen.add(tr.key);
        return;
      }
      const fresh = pickCrashTriggers(st.events, cursor);
      cursor = Math.max(cursor, highestSequence(st.events));
      if (!fresh.length) return;
      const serverNow = Math.max(st.serverTimeMs, st.lastTick?.serverTimeMs ?? 0);
      const logNow = newestSimTime(st.events);
      for (const tr of fresh) {
        if (seen.has(tr.key)) continue;
        const newest = tr.halt && tr.capture ? (tr.halt.serverSequence > tr.capture.serverSequence ? tr.halt : tr.capture) : (tr.halt ?? tr.capture);
        if (newest && isStaleEvent(newest, logNow, serverNow)) { seen.add(tr.key); pending.delete(tr.key); continue; }
        if (tr.capture && tr.halt) { play(tr); continue; }
        pending.set(tr.key, tr);
        if (!timer) timer = setTimeout(settle, PAIR_WINDOW_MS);
      }
    });
    return () => {
      unsubscribe();
      if (timer) clearTimeout(timer);
      pending.clear();
    };
  }, [advance]);

  return { shot, waiting, done: advance };
}

export interface CrashCinematicProps {
  /** Preview and screenshots only: hold the timeline at this many ms instead of playing it. */
  freezeAtMs?: number;
  /** Preview only: force the calm (true) or the full (false) variant instead of asking the OS. */
  reducedMotion?: boolean;
}

/** Mount once. Takes no props in the app; it reads the game store by itself. */
export default function CrashCinematic({ freezeAtMs, reducedMotion }: CrashCinematicProps = {}): ReactElement | null {
  const { shot, waiting, done } = useLiveCrashes();
  const prefersCalm = usePrefersReducedMotion();
  if (!shot || typeof document === "undefined") return null;
  return createPortal(
    <CrashStage key={shot.key} shot={shot} calm={reducedMotion ?? prefersCalm} waiting={waiting} freezeAtMs={freezeAtMs} onDone={done} />,
    document.body,
  );
}
export { CrashCinematic, CrashStage };
