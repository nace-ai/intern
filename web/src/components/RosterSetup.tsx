import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { generateRoster, type Member, seatRoster } from "@/orchestra/roster";

export function RosterSetup({ disabled, onRoster }: { disabled: boolean; onRoster: (members: Member[]) => void }) {
  const [count, setCount] = useState(16);
  const [seed, setSeed] = useState("drexathon");
  // Keep the callback in a ref and never put it in the effect's deps: the
  // parent re-renders every beat, and a changing onRoster must not re-emit.
  const cb = useRef(onRoster);
  cb.current = onRoster;

  useEffect(() => {
    cb.current(seatRoster(generateRoster(count, seed)));
  }, [count, seed]);

  return (
    <div className="flex w-[240px] items-center gap-2">
      <span className="shrink-0 text-xs text-muted-foreground">Musicians</span>
      <Slider
        min={4}
        max={40}
        step={1}
        value={count}
        onValueChange={(v) => setCount(Array.isArray(v) ? v[0] : v)}
        disabled={disabled}
        className="w-28"
      />
      <span className="tabular-nums font-semibold">{count}</span>
      <Button
        variant="ghost"
        size="sm"
        aria-label="Shuffle the orchestra"
        title="Shuffle"
        disabled={disabled}
        onClick={() => setSeed(Math.random().toString(36).slice(2, 8))}
      >
        🎲
      </Button>
    </div>
  );
}
