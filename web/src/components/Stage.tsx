import type { ReactElement, ReactNode } from "react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { DeterminismBoard } from "@/components/DeterminismBoard";
import { Lanes } from "@/components/Lanes";
import { Orchestra } from "@/components/Orchestra";
import { shortName } from "@/components/shortName";
import { INSTRUMENTS, type InstrumentId, MUSICIANS } from "@/orchestra/musicians";
import type { Member } from "@/orchestra/roster";
import type { PanelState } from "./useConductor";

const EMOJI = new Map<InstrumentId, string>(MUSICIANS.map((m) => [m.id, m.emoji]));

export function Stage({
  members,
  panels,
  wrap,
}: {
  members: Member[];
  panels: Record<string, PanelState>;
  wrap?: (member: Member, face: ReactElement) => ReactNode;
}) {
  // one lane per instrument at least one member plays, in INSTRUMENTS key order
  const rows = useMemo(
    () => (Object.keys(INSTRUMENTS) as InstrumentId[]).filter((id) => members.some((m) => m.instrument === id)),
    [members],
  );
  const counts = useMemo(() => {
    const map = new Map<InstrumentId, number>();
    for (const m of members) map.set(m.instrument, (map.get(m.instrument) ?? 0) + 1);
    return map;
  }, [members]);

  const lanesRef = useRef<HTMLDivElement>(null);
  const [rowHeight, setRowHeight] = useState(48);

  useLayoutEffect(() => {
    const el = lanesRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const next = Math.min(64, Math.max(18, Math.floor(entry.contentRect.height / Math.max(rows.length, 1))));
        setRowHeight((prev) => (prev === next ? prev : next));
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [rows.length]);

  return (
    <div className="flex min-h-0 flex-1 gap-2">
      <Orchestra members={members} panels={panels} wrap={wrap} className="flex-[3]" />
      <div className="flex min-h-0 min-w-0 flex-[2] flex-col gap-2">
        <div
          ref={lanesRef}
          className="min-h-0 min-w-0 flex-1 overflow-hidden rounded-lg border border-border bg-card/30"
        >
          <div className="flex items-start">
            <div className="w-32 shrink-0 border-r border-border">
              {rows.map((id) => {
                const count = counts.get(id) ?? 0;
                return (
                  <div
                    key={id}
                    style={{ height: rowHeight }}
                    className="box-border flex items-center gap-1.5 border-b border-border px-2"
                  >
                    <span className="text-lg leading-none">{EMOJI.get(id) ?? "🎵"}</span>
                    <span title={INSTRUMENTS[id].name} className="min-w-0 flex-1 truncate text-xs font-semibold">
                      {shortName(INSTRUMENTS[id].name)}
                    </span>
                    {count > 1 && <span className="text-[11px] font-bold tabular-nums text-primary">×{count}</span>}
                  </div>
                );
              })}
            </div>
            <div className="min-w-0 flex-1">
              <Lanes musicians={rows} rowHeight={rowHeight} />
            </div>
          </div>
        </div>
        <DeterminismBoard members={members} panels={panels} />
      </div>
    </div>
  );
}
