import type { ReactElement, ReactNode } from "react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { Member } from "@/orchestra/roster";
import { Face } from "./Face";
import { type PanelState, panelFor } from "./useConductor";

export function Orchestra({
  members,
  panels,
  wrap,
  className,
}: {
  members: Member[];
  panels: Record<string, PanelState>;
  wrap?: (member: Member, face: ReactElement) => ReactNode; // e.g. a popover around the face
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ w: 0, h: 0 });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      setDims((prev) => (prev.w === rect.width && prev.h === rect.height ? prev : { w: rect.width, h: rect.height }));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const { w, h } = dims;
  const size = useMemo(() => {
    if (members.length <= 1) return 64;
    const boxW = Math.max(1, w - 100);
    const boxH = Math.max(1, h - 120);
    const near: number[] = [];
    for (let i = 0; i < members.length; i++) {
      let best = Infinity;
      for (let j = 0; j < members.length; j++) {
        if (j === i) continue;
        const dx = (members[i].seat.x - members[j].seat.x) * boxW;
        const dy = ((members[i].seat.y - members[j].seat.y) * boxH) / 1.3;
        const dist = Math.hypot(dx, dy);
        if (dist < best) best = dist;
      }
      near.push(best);
    }
    near.sort((a, b) => a - b);
    const typical = near[Math.floor(0.25 * (members.length - 1))];
    return Math.round(Math.min(64, Math.max(32, typical * 0.8)));
  }, [members, w, h]);

  return (
    <div
      ref={ref}
      className={cn("relative min-h-0 min-w-0 overflow-hidden rounded-lg border border-border", className)}
      style={{ background: "radial-gradient(ellipse 70% 60% at 50% 100%, rgba(255,181,71,0.10), transparent 70%)" }}
    >
      {w > 0 && (
        <>
          <div
            className="absolute"
            style={{ left: size + 14, right: size + 14, top: size / 2 + 12, bottom: size + 44 }}
          >
            {members.map((member) => {
              const face = <Face member={member} panel={panelFor(panels, member)} size={size} />;
              return (
                <div
                  key={member.id}
                  className="absolute -translate-x-1/2 -translate-y-1/2"
                  style={{ left: `${member.seat.x * 100}%`, top: `${member.seat.y * 100}%` }}
                >
                  {wrap ? wrap(member, face) : face}
                </div>
              );
            })}
          </div>
          <div className="pointer-events-none absolute bottom-2 left-1/2 flex -translate-x-1/2 flex-col items-center">
            <span className="text-2xl leading-none">🪄</span>
            <span className="mt-0.5 text-[10px] font-bold uppercase tracking-[0.2em] text-primary">Drex conducts</span>
          </div>
        </>
      )}
    </div>
  );
}
