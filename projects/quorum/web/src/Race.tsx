import { useEffect, useState } from "react";

export interface Racer {
  name: string;
  ms: number; // measured median time for one decision
  color: string;
}

/**
 * Two counters ticking at the real median decision time of each defender.
 * Counters replay the supplied median decision cadence.
 */
export default function Race({ a, b, playing = true }: { a: Racer; b: Racer; playing?: boolean }) {
  const [t, setT] = useState(0);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let previous = performance.now();
    const frame = (now: number) => {
      const delta = now - previous;
      previous = now;
      setT((elapsed) => elapsed + delta);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  const rows = [a, b].map((r) => ({ ...r, n: r.ms > 0 ? Math.floor(t / r.ms) : 0 }));
  const peak = Math.max(1, ...rows.map((r) => r.n));

  return (
    <div className="race" role="img" aria-label={`${a.name} decides every ${Math.round(a.ms)} ms, ${b.name} every ${Math.round(b.ms)} ms`}>
      {rows.map((r) => (
        <div className="race-row" key={r.name}>
          <div className="race-name" style={{ color: r.color }}>
            <i className="beat" key={r.n} style={{ background: r.color }} />
            {r.name}
          </div>
          <div className="race-track">
            <div className="race-fill" style={{ width: `${(r.n / peak) * 100}%`, background: r.color }} />
          </div>
          <div className="race-num">
            <b>{r.n}</b> decisions
            <span> · one every {r.ms >= 1000 ? `${(r.ms / 1000).toFixed(1)} s` : `${Math.round(r.ms)} ms`}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
