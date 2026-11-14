import type { Stats } from "@/components/conductorTypes";

const LABELS = [
  "decisions",
  "api calls",
  "decisions/sec",
  "avg Drex latency",
  "cost",
  "timeouts",
  "tempo",
  "bar:beat",
] as const;

function values(stats: Stats | null): string[] {
  if (stats === null) return LABELS.map(() => "–");
  return [
    stats.decisions.toLocaleString(),
    String(stats.apiCalls ?? 0),
    stats.decisionsPerSec.toFixed(1),
    `${Math.round(stats.avgLatencyMs)} ms`,
    `$${stats.cost.toFixed(4)}`,
    String(stats.timeouts),
    `${stats.tempo} BPM`,
    `${stats.bar}:${stats.beat}`,
  ];
}

export function StatsStrip({ stats }: { stats: Stats | null }) {
  const vals = values(stats);

  return (
    <div className="flex items-stretch gap-2">
      {LABELS.map((label, i) => (
        <div
          key={label}
          className="flex flex-1 flex-col items-center justify-center rounded-md border border-border bg-card py-1"
        >
          <span className="text-2xl font-extrabold tabular-nums text-primary leading-tight">{vals[i]}</span>
          <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">{label}</span>
        </div>
      ))}
      <span className="shrink-0 self-end text-right text-[10px] leading-tight text-muted-foreground/70">
        <span className="block">
          Samples by{" "}
          <a
            href="https://github.com/nbrosowsky/tonejs-instruments"
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            tonejs-instruments
          </a>{" "}
          (CC-BY 3.0)
        </span>
        <span className="block">
          Faces: Toon Head by Johan Melin (CC BY 4.0) via{" "}
          <a
            href="https://www.dicebear.com/styles/toon-head/"
            target="_blank"
            rel="noreferrer"
            className="underline-offset-2 hover:underline"
          >
            DiceBear
          </a>
        </span>
      </span>
    </div>
  );
}
