import type * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { faceUrl } from "@/orchestra/faces";
import type { Member } from "@/orchestra/roster";

export type PanelInfo = {
  play: string;
  direction: string;
  recent: string[];
  det?: { repeatRate: number; meanTop: number; distinct: number };
};

const DRUM_PATTERN = /^[xX.]+$/;

function chipText(play: string): string {
  if (play === "rest") return "—";
  if (play === "hold") return "〜";
  if (DRUM_PATTERN.test(play)) {
    return play
      .split("")
      .map((c) => (c === "x" ? "●" : c === "X" ? "◉" : "·"))
      .join(" ");
  }
  return play;
}

function BarRow({ label, value, pct }: { label: string; value: string; pct: number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline justify-between">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums font-semibold">{value}</span>
      </div>
      <div className="h-1 rounded-full bg-muted">
        <div className="h-1 rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, pct * 100))}%` }} />
      </div>
    </div>
  );
}

export function MusicianPopover({
  member,
  panel,
  children,
}: {
  member: Member;
  panel?: PanelInfo;
  children: React.ReactElement;
}) {
  const instrument = member.name.replace(/\s+\d+$/, "");
  const recent = (panel?.recent ?? []).slice(-8);
  const det = panel?.det;
  const verdict = det
    ? det.repeatRate >= 0.6
      ? "Very repetitive"
      : det.repeatRate >= 0.3
        ? "Settled"
        : "Exploring"
    : null;

  return (
    <Popover>
      <PopoverTrigger render={children} />
      <PopoverContent className="w-72" side="top">
        <div className="flex items-center gap-3">
          <img src={faceUrl(member.faceSeed)} alt="" className="size-12 shrink-0 rounded-full" />
          <div className="min-w-0">
            <div className="truncate font-semibold">{member.name}</div>
            <div className="truncate text-xs text-muted-foreground">
              {instrument} · {member.section}
            </div>
          </div>
        </div>

        <Badge variant="secondary">{member.trait}</Badge>

        <div>
          <div className="text-xs text-muted-foreground">NDI direction</div>
          {panel?.direction ? (
            <div className="text-xs italic">{panel.direction}</div>
          ) : (
            <div className="text-xs text-muted-foreground">No direction yet</div>
          )}
        </div>

        <div>
          <div className="text-xs text-muted-foreground">Last notes</div>
          {recent.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {recent.map((play, i) => (
                <span
                  /* biome-ignore lint/suspicious/noArrayIndexKey: chips are a positional history; repeated notes are expected */
                  key={`${i}-${play}`}
                  className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                    i === recent.length - 1 ? "bg-primary text-primary-foreground" : "bg-muted"
                  }`}
                >
                  {chipText(play)}
                </span>
              ))}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">Nothing played yet</div>
          )}
        </div>

        {det && verdict && (
          <div className="flex flex-col gap-1.5">
            <div className="text-xs text-muted-foreground">How deterministic</div>
            <BarRow label="Repeats its last note" value={`${Math.round(det.repeatRate * 100)}%`} pct={det.repeatRate} />
            <BarRow
              label="Drex's top choice, on average"
              value={`${Math.round(det.meanTop * 100)}%`}
              pct={det.meanTop}
            />
            <div className="flex items-baseline justify-between">
              <span className="text-muted-foreground">Distinct notes</span>
              <span className="tabular-nums font-semibold">{det.distinct}</span>
            </div>
            <div className="text-xs italic">{verdict}</div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
