import { useEffect, useRef, useState } from "react";
import type { ConductorApi } from "@/components/conductorTypes";
import { MoodBar } from "@/components/MoodBar";
import { MusicianPopover } from "@/components/MusicianPopover";
import { type Selection, SelectionPicker } from "@/components/ScorePicker";
import { SectionBanner } from "@/components/SectionBanner";
import { SetupCard } from "@/components/SetupCard";
import { Stage } from "@/components/Stage";
import { StatsStrip } from "@/components/StatsStrip";
import { TopBar } from "@/components/TopBar";
import { UploadBox } from "@/components/UploadBox";
import { panelFor, useConductor } from "@/components/useConductor";
import { fakeConductor } from "@/dev/fakeConductor";
import { conductor as liveConductor } from "@/orchestra/conductor";
import type { Member } from "@/orchestra/roster";
import { loadScore } from "@/orchestra/scores";
import type { ScoreData } from "@/orchestra/scoreTypes";

// The real conductor plays with Drex and Tone.js; add ?fake to the URL to drive the UI with random beats instead.
const conductor: ConductorApi = new URLSearchParams(window.location.search).has("fake") ? fakeConductor : liveConductor;
// The conductor also emits "error" events for the UI.
const report = (e: unknown) => console.error(e);

export default function App() {
  const s = useConductor(conductor);
  const [members, setMembers] = useState<Member[]>([]);
  const [assembled, setAssembled] = useState(false);
  const [selection, setSelection] = useState<Selection>({ kind: "imperial" });
  const [uploaded, setUploaded] = useState<{ id: string; title: string; score: ScoreData }[]>([]);

  const newOrchestra = () => {
    if (s.running) conductor.stop();
    setMembers([]);
    setAssembled(false);
  };

  // Play whatever the picker chose: improv, a manifest piece, or an uploaded one.
  const play = async () => {
    if (selection.kind === "imperial") {
      conductor.setMood("imperial");
      await conductor.start();
    } else if (selection.kind === "improv") {
      await conductor.start();
    } else {
      const sc = selection.score ?? (await loadScore(selection.id));
      conductor.setScore?.(sc);
      await conductor.perform();
    }
  };
  const playTitle =
    selection.kind === "imperial" ? "Imperial" : selection.kind === "improv" ? "Improv" : selection.title;

  // Space toggles pause/resume, or plays the selection when idle; read fresh values through a ref.
  const keysRef = useRef({ running: s.running, paused: s.paused, assembled, ready: s.ready, play });
  keysRef.current = { running: s.running, paused: s.paused, assembled, ready: s.ready, play };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable))
        return;
      e.preventDefault();
      const k = keysRef.current;
      if (k.running) {
        if (k.paused) conductor.resume?.();
        else conductor.pause?.();
      } else if (k.assembled && k.ready) {
        k.play().catch(report);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-full flex-col gap-2 bg-background p-3 text-foreground">
      <TopBar
        ready={s.ready}
        loading={s.loading}
        running={s.running}
        mode={s.mode}
        paused={s.paused}
        onPause={() => conductor.pause?.()}
        onResume={() => conductor.resume?.()}
        count={assembled ? members.length : 0}
        assembled={assembled}
        onNewOrchestra={newOrchestra}
        picker={<SelectionPicker value={selection} onChange={setSelection} uploaded={uploaded} disabled={s.running} />}
        playTitle={playTitle}
        onPlay={() => {
          play().catch(report);
        }}
        onStop={() => conductor.stop()}
      >
        <SectionBanner section={s.section} />
      </TopBar>
      <div className="flex items-center gap-3">
        <MoodBar
          mood={s.mood}
          onMood={(p) => conductor.setMood(p)}
          onMoodText={async (t) => {
            try {
              await conductor.moodFromText(t);
            } catch (e) {
              report(e);
            }
          }}
        />
        <UploadBox
          onDirections={(d) => conductor.setDirections(d)}
          onScore={(sc, title) => {
            const id = `upload-${uploaded.length + 1}`;
            setUploaded([...uploaded, { id, title, score: sc }]);
            setSelection({ kind: "score", id, title, score: sc });
          }}
        />
      </div>
      {assembled ? (
        <Stage
          members={members}
          panels={s.panels}
          wrap={(member, face) => (
            <MusicianPopover member={member} panel={panelFor(s.panels, member)}>
              {face}
            </MusicianPopover>
          )}
        />
      ) : (
        <SetupCard
          ready={s.ready}
          loading={s.loading}
          onAssemble={(ms) => {
            setMembers(ms);
            conductor.setRoster?.(ms); // the live conductor may not have setRoster yet
            setAssembled(true);
          }}
        />
      )}
      <StatsStrip stats={s.stats} />
      {s.error && (
        <div className="fixed top-4 left-1/2 z-50 -translate-x-1/2 rounded-md bg-accent px-5 py-2 text-lg font-bold text-accent-foreground shadow-lg">
          {s.error}
        </div>
      )}
    </div>
  );
}
