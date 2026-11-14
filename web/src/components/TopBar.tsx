import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";

export function TopBar(props: {
  ready: boolean;
  loading: { loaded: number; total: number } | null;
  running: boolean;
  paused: boolean;
  onPause: () => void;
  onResume: () => void;
  mode: "improv" | "score" | null;
  count: number;
  assembled: boolean;
  children?: ReactNode;
  picker?: ReactNode;
  playTitle: string;
  onPlay: () => void;
  onNewOrchestra: () => void;
  onStop: () => void;
}) {
  const { ready, loading, running, count, assembled, children, picker, playTitle, onPlay, onNewOrchestra, onStop } =
    props;
  const { paused, onPause, onResume } = props;

  const stoppedLabel = !ready
    ? loading
      ? `Loading samples ${loading.loaded}/${loading.total}…`
      : "Loading samples…"
    : `▶ Play ${playTitle}`;

  const kbd = <kbd className="ml-2 rounded border px-1 text-[10px] opacity-80">Space</kbd>;

  return (
    <div className="flex items-center gap-4">
      <div className="shrink-0">
        <h1 className="text-3xl font-extrabold tracking-tight">Swarm Orchestra</h1>
        <p className="text-sm text-muted-foreground">
          {assembled ? `${count} musicians · every note is a live Drex decision` : "every note is a live Drex decision"}
        </p>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-3">{children}</div>

      {assembled && (
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="lg"
            variant="outline"
            className="text-base font-bold"
            disabled={running}
            onClick={onNewOrchestra}
          >
            New orchestra
          </Button>
          {picker}
          <Button
            size="lg"
            className="text-base font-bold"
            disabled={!running && !ready}
            onClick={running ? (paused ? onResume : onPause) : onPlay}
          >
            {running ? (
              <>
                {paused ? "▶ Resume" : "⏸ Pause"}
                {kbd}
              </>
            ) : (
              <>
                <span className="max-w-[14rem] truncate">{stoppedLabel}</span>
                {kbd}
              </>
            )}
          </Button>
          {running && (
            <Button
              size="lg"
              className="bg-accent text-accent-foreground hover:bg-accent/90 text-base font-bold"
              onClick={onStop}
            >
              ■ Stop
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
