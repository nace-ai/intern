// A compact live "determinism leaderboard" floating over the seating view:
// which musicians keep repeating themselves, and which keep changing.
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { faceUrl } from "@/orchestra/faces";
import { MUSICIANS } from "@/orchestra/musicians";
import type { Member } from "@/orchestra/roster";
import type { Det } from "./conductorTypes";
import { shortName } from "./shortName";
import { type PanelState, panelFor } from "./useConductor";

type Entry = { member: Member; det: Det };

function detail(member: Member, det: Det): string {
  return `${member.name} (${member.trait}): repeats ${Math.round(det.repeatRate * 100)}%, Drex top choice ${Math.round(
    det.meanTop * 100,
  )}% on average, ${det.distinct} distinct notes`;
}

function Row({ entry, stat, statClass }: { entry: Entry; stat: string; statClass: string }) {
  const { member } = entry;
  return (
    <div className="flex h-[18px] items-center gap-1" title={detail(member, entry.det)}>
      <img src={faceUrl(member.faceSeed)} alt="" className="size-4 shrink-0 rounded-full" />
      <span className="min-w-0 truncate font-semibold">{shortName(member.name)}</span>
      <span className="min-w-0 flex-1 truncate text-[10px] text-muted-foreground">{member.trait}</span>
      <span className={cn("shrink-0 text-right font-bold tabular-nums", statClass)}>{stat}</span>
    </div>
  );
}

export function DeterminismBoard({
  members,
  panels,
  className,
}: {
  members: Member[];
  panels: Record<string, PanelState>;
  className?: string;
}) {
  const { habits, wildcards } = useMemo(() => {
    const eligible: Entry[] = [];
    for (const member of members) {
      const kind = MUSICIANS.find((m) => m.id === member.instrument)?.kind;
      if (kind === "drums") continue; // drums repeat patterns by design
      const panel = panelFor(panels, member);
      if (!panel || panel.det === undefined || panel.recent.length < 8) continue;
      eligible.push({ member, det: panel.det });
    }

    const byHabit = [...eligible].sort((a, b) => b.det.repeatRate - a.det.repeatRate || b.det.meanTop - a.det.meanTop);
    const habits = byHabit.slice(0, 3);
    const habitIds = new Set(habits.map((e) => e.member.id));
    const wildcards = eligible
      .filter((e) => !habitIds.has(e.member.id))
      .sort((a, b) => a.det.repeatRate - b.det.repeatRate || b.det.distinct - a.det.distinct)
      .slice(0, 3);
    return { habits, wildcards };
  }, [members, panels]);

  const warmingUp = habits.length + wildcards.length < 2;

  return (
    <div
      className={cn(
        "grid h-20 shrink-0 grid-cols-2 gap-x-4 overflow-hidden rounded-lg border border-border bg-card/30 px-2 py-1 text-xs",
        className,
      )}
    >
      {warmingUp ? (
        <div className="col-span-2 flex items-center justify-center text-muted-foreground">
          🔁 Creatures of habit · 🎲 Wildcards: warming up…
        </div>
      ) : (
        <>
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              🔁 Creatures of habit
            </div>
            {habits.map((e) => (
              <Row
                key={e.member.id}
                entry={e}
                stat={`repeats ${Math.round(e.det.repeatRate * 100)}%`}
                statClass="text-primary"
              />
            ))}
          </div>
          {wildcards.length > 0 && (
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                🎲 Wildcards
              </div>
              {wildcards.map((e) => (
                <Row key={e.member.id} entry={e} stat={`${e.det.distinct} notes`} statClass="text-accent" />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
