import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { STEPS, makeCtx, type Step, type TourApi } from "./tourSteps";
import "./tour.css";

interface Props {
  api: TourApi;
  onExit: () => void;
}

function stagger(i: number): CSSProperties {
  return { "--i": i } as CSSProperties;
}

function Icon({ name }: { name: "play" | "pause" | "prev" | "next" | "close" }) {
  const common = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  switch (name) {
    case "play":
      return (
        <svg {...common} aria-hidden>
          <path d="M5 3.2v9.6L12.6 8z" fill="currentColor" stroke="none" />
        </svg>
      );
    case "pause":
      return (
        <svg {...common} aria-hidden>
          <path d="M5.2 3.5v9M10.8 3.5v9" />
        </svg>
      );
    case "prev":
      return (
        <svg {...common} aria-hidden>
          <path d="M9.8 3.5 5.3 8l4.5 4.5" />
        </svg>
      );
    case "next":
      return (
        <svg {...common} aria-hidden>
          <path d="M6.2 3.5 10.7 8l-4.5 4.5" />
        </svg>
      );
    default:
      return (
        <svg {...common} aria-hidden>
          <path d="M4 4l8 8M12 4l-8 8" />
        </svg>
      );
  }
}

export default function Tour({ api, onExit }: Props) {
  const ctx = useMemo(() => makeCtx(api), [api.rounds, api.attacks, api.usage, api.demo]); // eslint-disable-line react-hooks/exhaustive-deps
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  // The list of steps is fixed when the tour starts, so live updates cannot shift the index.
  const [steps] = useState<Step[]>(() => STEPS.filter((s) => !s.skip?.(makeCtx(api))));
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [progress, setProgress] = useState(0);
  const elapsed = useRef(0);
  const apiRef = useRef(api);
  apiRef.current = api;

  const step = steps[Math.min(idx, steps.length - 1)];
  const last = idx >= steps.length - 1;

  const go = useCallback(
    (to: number) => setIdx((cur) => Math.max(0, Math.min(steps.length - 1, typeof to === "number" ? to : cur))),
    [steps.length],
  );

  // Enter a step: set the spotlight and run the step's own setup.
  useEffect(() => {
    const app = document.querySelector<HTMLElement>(".app");
    app?.setAttribute("data-focus", step.focus);
    if (step.card) app?.setAttribute("data-card", step.card);
    else app?.removeAttribute("data-card");
    if (window.matchMedia("(max-width: 900px)").matches && step.focus !== "drawer") {
      const target = step.focus === "none" ? "dish" : step.focus;
      document.querySelector(`[data-tour="${target}"]`)?.scrollIntoView({ block: "start", behavior: "auto" });
    }
    elapsed.current = 0;
    setProgress(0);
    const cleanup = step.enter?.(ctxRef.current);
    return () => {
      if (typeof cleanup === "function") cleanup();
    };
  }, [step]);

  useEffect(() => { step.tick?.(ctxRef.current, progress); }, [step, progress]);

  // Leaving the tour restores the normal view.
  useEffect(() => {
    document.querySelector<HTMLElement>(".app")?.setAttribute("data-tour-on", "1");
    return () => {
      const app = document.querySelector<HTMLElement>(".app");
      app?.removeAttribute("data-focus");
      app?.removeAttribute("data-card");
      app?.removeAttribute("data-tour-on");
      apiRef.current.select(null);
      apiRef.current.setShownRound(null);
    };
  }, []);

  // Auto-advance.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let prev = performance.now();
    const frame = (now: number) => {
      elapsed.current += now - prev;
      prev = now;
      const p = Math.min(1, elapsed.current / step.ms);
      setProgress(p);
      if (p >= 1) {
        if (idx < steps.length - 1) setIdx(idx + 1);
        else setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [playing, idx, step, steps.length]);

  // Keyboard: arrows, space, escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(e.target.tagName))) return;
      if (e.key === "ArrowRight") { e.preventDefault(); setIdx((i) => Math.min(steps.length - 1, i + 1)); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
      else if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "Escape") onExit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [steps.length, onExit]);

  if (steps.length === 0) return null;

  return (
    <aside className="tour" role="region" aria-label="Guided demo" aria-live="polite">
      <div className="tour-content" key={step.id}>
        <div className="tour-head tour-in" style={stagger(0)}>
          <span className="tour-count">
            {idx + 1}
            <i>/</i>
            {steps.length}
          </span>
          <h2>{step.title}</h2>
        </div>
        <div className="tour-body tour-in" style={stagger(1)}>
          {step.body(ctx)}
        </div>
        {step.extra && (
          <div className="tour-extra tour-in" style={stagger(2)}>
            {step.extra(ctx, playing)}
          </div>
        )}
      </div>

      <div className="tour-bar">
        <div className="tour-segs" role="group" aria-label="Steps">
          {steps.map((s, i) => (
            <button
              key={s.id}
              aria-current={i === idx ? "step" : undefined}
              aria-label={`${i + 1}. ${s.title}`}
              title={s.title}
              className="tour-seg"
              onClick={() => go(i)}
            >
              <span style={{ transform: `scaleX(${i < idx ? 1 : i === idx ? progress : 0})` }} />
            </button>
          ))}
        </div>
        <div className="tour-ctl">
          <button className="icon-btn" onClick={() => go(idx - 1)} disabled={idx === 0} aria-label="Previous step">
            <Icon name="prev" />
          </button>
          <button
            className="icon-btn primary swap-icon"
            onClick={() => {
              if (last && !playing) {
                elapsed.current = 0;
                setIdx(0);
                setPlaying(true);
              } else setPlaying((p) => !p);
            }}
            data-state={playing ? "playing" : "paused"}
            aria-label={playing ? "Pause" : last ? "Replay from the start" : "Play"}
          >
            <span className="ico ico-play">
              <Icon name="play" />
            </span>
            <span className="ico ico-pause">
              <Icon name="pause" />
            </span>
          </button>
          <button className="icon-btn" onClick={() => go(idx + 1)} disabled={last} aria-label="Next step">
            <Icon name="next" />
          </button>
          {last ? (
            <button className="btn-text primary" onClick={onExit}>
              Explore the data
            </button>
          ) : (
            <button className="icon-btn" onClick={onExit} aria-label="Exit guided demo">
              <Icon name="close" />
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
