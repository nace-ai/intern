import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";
import { faceUrl } from "@/orchestra/faces";
import type { Member } from "@/orchestra/roster";
import { GLYPH_PLACEMENT, type GlyphPlacement, InstrumentGlyph } from "./InstrumentGlyph";
import { shortName } from "./shortName";
import type { PanelState } from "./useConductor";

const DRUM_PATTERN = /^[xX.]+$/;

function drumText(pattern: string): string {
  return pattern
    .split("")
    .map((c) => (c === "x" ? "●" : c === "X" ? "◉" : "·"))
    .join("");
}

export function Face({
  member,
  panel,
  size,
  ...rest
}: { member: Member; panel: PanelState | undefined; size: number } & ComponentProps<"button">) {
  const play = panel?.play ?? "";
  const isDrum = DRUM_PATTERN.test(play);
  const isHold = play === "hold";
  const isNote = play.length > 0 && !isDrum && play !== "rest" && !isHold;
  const resting = play === "rest" || play === ".." || play === "...";
  const showBadge = isNote || isHold || (isDrum && /[xX]/.test(play));
  const r = size / 2 + 3;
  const c = 2 * Math.PI * r;
  const det = panel?.det;
  const pulse = panel?.pulse ?? 0;
  const glyph: GlyphPlacement | undefined = GLYPH_PLACEMENT[member.instrument];
  const glyphOverflow = glyph ? Math.max(0, glyph.y + glyph.h - 1) * size : 0;

  return (
    <button
      type="button"
      {...rest}
      aria-label={member.name}
      title={member.name}
      style={{ width: size + 20 }}
      className={cn(
        "group flex flex-col items-center rounded-md outline-none transition-opacity focus-visible:ring-2 focus-visible:ring-ring",
        resting && "opacity-40",
      )}
    >
      <div key={pulse} className={cn(pulse > 0 && "instrument-bob")}>
        <div className="relative" style={{ width: size, height: size }}>
          <svg aria-hidden="true" className="absolute -inset-[5px] -rotate-90" width={size + 10} height={size + 10}>
            <circle
              cx={(size + 10) / 2}
              cy={(size + 10) / 2}
              r={r}
              strokeWidth={3}
              fill="none"
              className="stroke-muted"
            />
            {det && (
              <circle
                cx={(size + 10) / 2}
                cy={(size + 10) / 2}
                r={r}
                strokeWidth={3}
                fill="none"
                strokeLinecap="round"
                strokeDasharray={`${c * det.repeatRate} ${c}`}
                className="stroke-primary"
              />
            )}
          </svg>
          <img
            src={faceUrl(member.faceSeed)}
            alt=""
            draggable={false}
            className="h-full w-full rounded-full bg-card object-cover"
          />
          {glyph && <InstrumentGlyph instrument={member.instrument} size={size} />}
          {panel && panel.pulse > 0 && (
            <span key={panel.pulse} className="face-pulse pointer-events-none absolute inset-0 rounded-full" />
          )}
          {showBadge && (
            <span
              className={cn(
                "absolute -top-1.5 -right-3 z-20 rounded-full px-1.5 py-px text-[11px] font-extrabold leading-tight tabular-nums shadow",
                isHold ? "bg-primary/50 text-foreground" : "bg-primary text-primary-foreground",
              )}
            >
              {isNote ? play : isHold ? `〜${panel?.lastNote ?? ""}` : drumText(play)}
            </span>
          )}
        </div>
      </div>
      <span
        style={{ marginTop: 6 + glyphOverflow }}
        title={member.name}
        className="max-w-full truncate text-[10px] font-semibold leading-none text-muted-foreground"
      >
        {shortName(member.name)}
      </span>
    </button>
  );
}
