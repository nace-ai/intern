import { useEffect, useRef } from "react";
import * as Tone from "tone";
import { MUSICIANS, type Musician, noteToMidi } from "@/orchestra/musicians";
import { type Segment, timeline } from "@/orchestra/timeline";

const byId = new Map<string, Musician>(MUSICIANS.map((m) => [m.id, m]));
const DRUM_COLORS: Record<string, string> = { kick: "#ff8a3d", snare: "#f5e6c8", hats: "#4fd1c5" };
const NOTE_NAME = /^[A-G][#b]?-?\d$/;
const MEMBER_HUE = [0, 14, -14, 28, -28, 42, -42]; // hue offset per member index
const MEMBER_NUDGE = [0, -2, 2, -4, 4]; // px nudge per member index

const FALLBACK = { bgA: "#0e0e14", bgB: "#121219", border: "#26262e", primary: "#ffb347" };
const FUTURE = 2.5; // seconds visible from playhead to the right edge

// AudioContext time WITHOUT lookAhead, so the playhead matches what is heard.
const defaultNow = () => Tone.immediate();

type Theme = { bgA: string; bgB: string; border: string; primary: string };

function readTheme(): Theme {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fb: string) => css.getPropertyValue(name).trim() || fb;
  return {
    bgA: v("--background", FALLBACK.bgA),
    bgB: v("--card", FALLBACK.bgB),
    border: v("--border", FALLBACK.border),
    primary: v("--primary", FALLBACK.primary),
  };
}

export function Lanes({
  musicians,
  rowHeight,
  now,
}: {
  /** INSTRUMENT ids, one per row, in row order. */
  musicians: readonly string[];
  rowHeight: number;
  now?: () => number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const propsRef = useRef({ musicians, rowHeight, now: now as (() => number) | undefined });
  propsRef.current = { musicians, rowHeight, now };
  const height = musicians.length * rowHeight;

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width = 0;
    let dpr = 1;
    let theme = readTheme();

    const resize = () => {
      dpr = window.devicePixelRatio || 1;
      width = container.clientWidth;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(propsRef.current.musicians.length * propsRef.current.rowHeight * dpr));
      theme = readTheme();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(container);

    // when each segment first appeared, for the 150 ms fade-in
    const addedAt = new WeakMap<Segment, number>();
    const mark = () => {
      const tAdd = performance.now();
      for (const seg of timeline.recent(-1e9)) if (!addedAt.has(seg)) addedAt.set(seg, tAdd);
    };
    mark();
    const unsub = timeline.subscribe(mark);

    const noteY = (laneTop: number, h: number, inst: string, label: string): number => {
      const m = byId.get(inst);
      const rowH = propsRef.current.rowHeight;
      if (!m || m.low >= m.high) return laneTop + (rowH - h) / 2;
      const midi = noteToMidi(label);
      const frac = Math.min(1, Math.max(0, (midi - m.low) / (m.high - m.low)));
      const pad = Math.max(1, Math.round(rowH * 0.08));
      return laneTop + pad + (1 - frac) * (rowH - 2 * pad - h);
    };

    const draw = () => {
      if (width < 1) return;
      const { musicians: rows, rowHeight: rh, now: nowProp } = propsRef.current;
      const t = (nowProp ?? defaultNow)();
      const n = rows.length;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      for (let i = 0; i < n; i++) {
        ctx.fillStyle = i % 2 === 0 ? theme.bgA : theme.bgB;
        ctx.fillRect(0, i * rh, width, rh);
      }

      const playheadX = width * 0.2;
      const pxPerSec = (width - playheadX) / FUTURE;
      const x = (s: number) => playheadX + (s - t) * pxPerSec;

      const rr = (x0: number, y0: number, w: number, h0: number, r: number[]) => {
        ctx.beginPath();
        ctx.roundRect(x0, y0, w, h0, r);
      };
      const recent = timeline.recent(t - playheadX / pxPerSec);
      // the last segment per musician, so `hold`s can join to their note
      const last = new Map<string, Segment>();

      const h = Math.max(3, Math.round(rh * 0.2));
      const nudgeScale = Math.round(rh / 56);
      for (const seg of recent) {
        const inst = seg.instrument ?? seg.musician;
        const i = rows.indexOf(inst);
        if (i < 0) continue;
        const before = last.get(seg.musician);
        last.set(seg.musician, seg);
        const laneTop = i * rh;
        const memberIndex = Number(/-(\d+)$/.exec(seg.musician)?.[1] ?? 1);
        const nudge = MEMBER_NUDGE[(memberIndex - 1) % MEMBER_NUDGE.length] * nudgeScale;
        const fadeIn = Math.min(1, (performance.now() - (addedAt.get(seg) ?? 0)) / 150);
        const flashing = t >= seg.start && t <= seg.start + seg.dur;
        const flash = flashing ? (t < seg.start + 0.12 ? 1 : 0.6) : 0;

        if (seg.kind === "hit" || seg.kind === "accent" || byId.get(inst)?.kind === "drums") {
          const accent = seg.kind === "accent";
          const w = accent ? 5 : 3;
          const th = rh * (accent ? 0.75 : 0.45) * (1 + 0.1 * flash);
          const x1 = x(seg.start) + w / 2 + nudge;
          const a = (0.45 + 0.55 * seg.prob) * fadeIn * Math.max(0, Math.min(1, x1 / playheadX));
          if (a <= 0.01) continue;
          const color = DRUM_COLORS[inst] ?? "#ffffff";
          ctx.fillStyle =
            color +
            Math.round(Math.min(1, a + 0.3 * flash) * 255)
              .toString(16)
              .padStart(2, "0");
          if (flash > 0) {
            ctx.shadowBlur = 14 * flash;
            ctx.shadowColor = color;
          }
          ctx.fillRect(x1 - w / 2, laneTop + (rh - th) / 2, w, th);
          ctx.shadowBlur = 0;
          continue;
        }

        if (!NOTE_NAME.test(seg.label)) continue;
        const midi = noteToMidi(seg.label);
        const hue = ((((midi % 12) * 7) % 12) * 30 + MEMBER_HUE[(memberIndex - 1) % MEMBER_HUE.length] + 360) % 360;
        const y = Math.max(laneTop + 1, Math.min(laneTop + rh - h - 1, noteY(laneTop, h, inst, seg.label) + nudge));

        let x0 = x(seg.start);
        let corners: number[] = [3, 3, 3, 3];
        if (seg.kind === "hold") {
          // join into the sounding note: square left edge, no gap, same height
          if (before && before.label === seg.label && Math.abs(x(seg.start) - x(before.start + before.dur)) < 6) {
            x0 = x(before.start + before.dur) - 2;
            corners = [0, 3, 3, 0];
          }
        }
        const x1 = Math.max(x0 + 2, x(seg.start + seg.dur) - 2);
        const fadeOut = Math.max(0, Math.min(1, x1 / playheadX));
        const a = (0.45 + 0.55 * seg.prob) * fadeIn * fadeOut + 0.3 * flash;
        if (a <= 0.01) continue;
        const l = 38 + 30 * seg.prob + 20 * flash;

        ctx.fillStyle = `hsla(${hue}, 85%, ${l}%, ${Math.min(1, a)})`;
        if (flash > 0) {
          ctx.shadowBlur = 14 * flash;
          ctx.shadowColor = `hsla(${hue}, 85%, ${l}%, 1)`;
        }
        const h0 = h * (1 + 0.1 * flash);
        rr(x0, y - (h0 - h) / 2, x1 - x0, h0, corners);
        ctx.fill();
        ctx.shadowBlur = 0;
        if (!seg.isTop) {
          ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
        if (h >= 10 && x1 - x0 > 30) {
          ctx.fillStyle = "rgba(0, 0, 0, 0.8)";
          ctx.font = `${Math.round(Math.min(11, h - 1))}px system-ui, sans-serif`;
          ctx.textBaseline = "middle";
          ctx.globalAlpha = Math.min(1, a);
          ctx.fillText(seg.label, x0 + 5, y + h / 2 + 0.5);
          ctx.globalAlpha = 1;
        }
      }

      ctx.fillStyle = theme.border;
      for (let i = 1; i <= n; i++) ctx.fillRect(0, i * rh - 1, width, 1);

      ctx.save();
      ctx.shadowBlur = 8;
      ctx.shadowColor = theme.primary;
      ctx.fillStyle = theme.primary;
      ctx.fillRect(playheadX - 1, 0, 2, n * rh);
      ctx.restore();
    };

    let raf = 0;
    const frame = () => {
      draw();
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      unsub();
    };
  }, []);

  return (
    <div ref={containerRef} style={{ width: "100%", height, position: "relative" }}>
      <canvas ref={canvasRef} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} />
    </div>
  );
}
