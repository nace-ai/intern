// Dev-only page served at /lanes-dev.html: the lane canvas with a fake feed,
// so it can be seen before the real stage exists.

import { StrictMode, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as Tone from "tone";
import { Lanes } from "@/components/Lanes";
import { startFakeFeed } from "@/orchestra/fakeFeed";
import { MUSICIAN_IDS, type MusicianId } from "@/orchestra/musicians";
import "../index.css";

const DUPLICATES: { id: string; instrument: MusicianId }[] = [
  { id: "violin-1", instrument: "violin" },
  { id: "violin-2", instrument: "violin" },
  { id: "violin-3", instrument: "violin" },
  { id: "pad_low-1", instrument: "pad_low" },
  { id: "pad_low-2", instrument: "pad_low" },
  { id: "bass-1", instrument: "bass" },
  { id: "flute-1", instrument: "flute" },
  { id: "flute-2", instrument: "flute" },
  { id: "kick-1", instrument: "kick" },
  { id: "snare-1", instrument: "snare" },
  { id: "bells-1", instrument: "bells" },
];

function Page() {
  const stopRef = useRef<(() => void) | null>(null);
  const [running, setRunning] = useState(false);
  const [dupes, setDupes] = useState(false);
  const [wide, setWide] = useState(false);

  const stop = () => {
    stopRef.current?.();
    stopRef.current = null;
    setRunning(false);
  };

  const startFeed = (useDupes: boolean) => {
    stopRef.current = startFakeFeed(96, useDupes ? DUPLICATES : undefined);
    setRunning(true);
  };

  const toggle = async () => {
    if (running) {
      stop();
      return;
    }
    await Tone.start();
    startFeed(dupes);
  };

  const toggleDupes = () => {
    const next = !dupes;
    setDupes(next);
    if (running) {
      stopRef.current?.();
      stopRef.current = null;
      startFeed(next);
    }
  };

  // one row per instrument present, in MUSICIAN_IDS order
  const rows = MUSICIAN_IDS.filter((id) => (dupes ? DUPLICATES.some((m) => m.instrument === id) : true));

  return (
    <div className="flex min-h-screen flex-col gap-4 bg-background p-6 text-foreground">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={toggle}
          className="rounded-md bg-primary px-6 py-3 text-lg font-semibold text-primary-foreground"
        >
          {running ? "Stop" : "Start"}
        </button>
        <button type="button" onClick={toggleDupes} className="rounded-md border border-border px-4 py-3 font-medium">
          Classic 11 / Duplicates: {dupes ? "Duplicates" : "Classic 11"}
        </button>
        <button
          type="button"
          onClick={() => setWide((w) => !w)}
          className="rounded-md border border-border px-4 py-3 font-medium"
        >
          Width 390 / 1100: {wide ? "1100" : "390"}
        </button>
        <h1 className="text-lg font-medium">Lanes dev — fake feed at 96 bpm</h1>
      </div>
      <div className="overflow-hidden rounded-lg border border-border" style={{ width: wide ? 1100 : 390 }}>
        <Lanes musicians={rows} rowHeight={54} />
      </div>
    </div>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Missing #root element");

createRoot(rootEl).render(
  <StrictMode>
    <Page />
  </StrictMode>,
);
