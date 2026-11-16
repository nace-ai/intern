import type { RoundRecord } from "./types";
import { defenderColor } from "./types";
import { totalsFor } from "./stats";

interface Props {
  rounds: RoundRecord[];
  defenders: string[];
}

const W = 340;
const H = 150;
const PAD = { l: 34, r: 8, t: 8, b: 20 };

interface Series {
  label: string;
  color: string;
  dashed?: boolean;
  points: { x: number; y: number }[];
}

function fmtNum(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(Math.round(n));
}

function LineChart({ series, xs, yMax, yFmt }: { series: Series[]; xs: number[]; yMax: number; yFmt: (n: number) => string }) {
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const xMin = xs.length ? Math.min(...xs) : 0;
  const xMax = xs.length ? Math.max(...xs) : 1;
  const sx = (x: number) => PAD.l + (xMax === xMin ? iw / 2 : ((x - xMin) / (xMax - xMin)) * iw);
  const top = yMax > 0 ? yMax : 1;
  const sy = (y: number) => PAD.t + ih - (y / top) * ih;
  const ticks = [0, top / 2, top];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img">
      {ticks.map((t) => (
        <g key={t}>
          <line className="axis" x1={PAD.l} x2={W - PAD.r} y1={sy(t)} y2={sy(t)} strokeDasharray={t === 0 ? undefined : "2 3"} />
          <text x={PAD.l - 4} y={sy(t) + 3} textAnchor="end">
            {yFmt(t)}
          </text>
        </g>
      ))}
      {xs.filter((_, i) => i === xs.length - 1 || i % Math.max(1, Math.ceil(xs.length / 6)) === 0).map((x) => (
        <text key={x} x={sx(x)} y={H - 6} textAnchor="middle">
          {x}
        </text>
      ))}
      {series.map((s) => (
        <g key={s.label}>
          {s.points.length > 1 && (
            <polyline
              fill="none"
              stroke={s.color}
              strokeWidth={s.dashed ? 1.5 : 2}
              strokeDasharray={s.dashed ? "5 4" : undefined}
              points={s.points.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")}
            />
          )}
          {s.points.map((p) => (
            <circle key={p.x} cx={sx(p.x)} cy={sy(p.y)} r={s.dashed ? 2 : 2.5} fill={s.color}>
              <title>{`${s.label} r${p.x}: ${yFmt(p.y)}`}</title>
            </circle>
          ))}
        </g>
      ))}
    </svg>
  );
}

function Legend({ series }: { series: Series[] }) {
  return (
    <div className="chart-legend">
      {series.map((s) => (
        <span key={s.label}>
          <i className={s.dashed ? "dashed" : undefined} style={s.dashed ? { borderColor: s.color } : { background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

export function BreachChart({ rounds, defenders }: Props) {
  const sorted = [...rounds].sort((a, b) => a.round - b.round);
  const xs = sorted.map((r) => r.round);
  const rate = (n: number, d: number) => (d > 0 ? n / d : 0);
  const series: Series[] = defenders.map((d) => ({
    label: d,
    color: defenderColor(d),
    points: sorted
      .filter((r) => r.defenders?.[d])
      .map((r) => ({ x: r.round, y: rate(r.defenders[d].breaches, r.n_attacks) })),
  }));
  if (defenders.includes("drex")) {
    series.push({
      label: "drex raw",
      color: defenderColor("drex"),
      dashed: true,
      points: sorted
        .filter((r) => r.defenders?.drex)
        .map((r) => ({ x: r.round, y: rate(r.defenders.drex.raw_breaches, r.n_attacks) })),
    });
  }
  const yMax = Math.max(0.1, ...series.flatMap((s) => s.points.map((p) => p.y)));
  return (
    <div className="chart">
      <h2>Breach rate by round</h2>
      <LineChart series={series} xs={xs} yMax={Math.min(1, yMax * 1.1)} yFmt={(n) => `${Math.round(n * 100)}%`} />
      <Legend series={series} />
    </div>
  );
}

/** Lowest risk score per round per defender: shows margin even when breaches are zero. */
export function MinRiskChart({ rounds, defenders }: Props) {
  const sorted = [...rounds].sort((a, b) => a.round - b.round);
  const xs = sorted.map((r) => r.round);
  const series: Series[] = defenders
    .map((d) => ({
      label: d,
      color: defenderColor(d),
      points: sorted
        .filter((r) => typeof r.defenders?.[d]?.min_risk === "number")
        .map((r) => ({ x: r.round, y: r.defenders[d].min_risk as number })),
    }))
    .filter((s) => s.points.length > 0);
  const yMax = Math.max(0.1, ...series.flatMap((s) => s.points.map((p) => p.y)));
  return (
    <div className="chart">
      <h2>Lowest risk score by round</h2>
      {series.length === 0 ? (
        <div className="muted-note">no risk scores yet</div>
      ) : (
        <>
          <LineChart series={series} xs={xs} yMax={yMax <= 1 ? 1 : yMax * 1.1} yFmt={(n) => n.toFixed(2)} />
          <Legend series={series} />
        </>
      )}
    </div>
  );
}

export function SpendChart({ rounds, defenders }: Props) {
  const sorted = [...rounds].sort((a, b) => a.round - b.round);
  const xs = sorted.map((r) => r.round);
  const series: Series[] = [
    {
      label: "attacker",
      color: "#fc8181",
      points: sorted.map((r) => ({
        x: r.round,
        y: (r.attacker?.input_tokens ?? 0) + (r.attacker?.output_tokens ?? 0),
      })),
    },
    ...defenders.map((d) => ({
      label: d,
      color: defenderColor(d),
      points: sorted
        .filter((r) => r.defenders?.[d])
        .map((r) => ({ x: r.round, y: (r.defenders[d].input_tokens ?? 0) + (r.defenders[d].output_tokens ?? 0) })),
    })),
  ];
  const yMax = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.y)));
  const cost = (d: string): number | null => {
    let total: number | null = null;
    for (const r of rounds) {
      const c = r.defenders?.[d]?.cost_usd;
      if (c !== null && c !== undefined) total = (total ?? 0) + c;
    }
    return total;
  };
  const attackerCost = rounds.reduce<number | null>((acc, r) => {
    const c = r.attacker?.cost_usd;
    return c !== null && c !== undefined ? (acc ?? 0) + c : acc;
  }, null);
  const costs = [
    ...(attackerCost !== null ? [["attacker", attackerCost] as const] : []),
    ...defenders.map((d) => [d, cost(d)] as const).filter((x): x is readonly [string, number] => x[1] !== null),
  ];
  return (
    <div className="chart">
      <h2>Spend: tokens per round</h2>
      <LineChart series={series} xs={xs} yMax={yMax * 1.1} yFmt={fmtNum} />
      <Legend series={series} />
      {costs.length > 0 && (
        <div className="chart-legend">
          {costs.map(([name, c]) => (
            <span key={name}>
              {name}: ${c.toFixed(4)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function TotalsTable({ rounds, defenders }: Props) {
  const rows = defenders.map((d) => {
    const t = totalsFor(rounds, d);
    return { d, model: t.model, attacks: t.poisoned, clean: t.clean, raw: t.rawBreaches, br: t.breaches,
      fa: t.falseAlarms, fail: t.failures, med: t.medianMs === null ? null : Math.round(t.medianMs) };
  });
  return (
    <div>
      <h2>Totals</h2>
      <div className="table-scroll" role="region" aria-label="Defender totals" tabIndex={0}>
      <table>
        <thead>
          <tr>
            <th>defender</th>
            <th>model</th>
            <th>attacks</th>
            <th>raw br.</th>
            <th>breaches</th>
            <th title="false alarms out of clean invoices">false al. / clean</th>
            <th>failures</th>
            <th title="Median of per-round medians">latency ms*</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.d}>
              <td>
                <span className="dot" style={{ background: defenderColor(r.d), marginRight: 6 }} />
                {r.d}
              </td>
              <td className="model-cell" title={r.model ?? ""}>{r.model ?? "–"}</td>
              <td>{r.attacks}</td>
              <td>{r.raw}</td>
              <td>{r.br}</td>
              <td>
                {r.fa} / {r.clean}
              </td>
              <td>{r.fail}</td>
              <td>{r.med ?? "–"}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} style={{ textAlign: "center", color: "var(--muted)" }}>
                no rounds yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>
      <p className="totals-note">* Median of round medians. Each row covers the rounds where that defender was present; attack counts can differ. Failures are separate from approvals and catches.</p>
    </div>
  );
}
