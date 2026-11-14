import { useEffect, useRef, useState } from "react";
import { FileHelp } from "@/components/FileHelp";
import { Button } from "@/components/ui/button";
import { midiToScore } from "@/orchestra/midi";
import { type Directions, extractDirections, type NdiStatus } from "@/orchestra/ndi";
import type { ScoreData } from "@/orchestra/scoreTypes";

const IDLE_TEXT = "A document gives directions; a MIDI is a piece to perform";

export function UploadBox({
  onDirections,
  onScore,
}: {
  onDirections: (d: Directions) => void;
  onScore?: (score: ScoreData, title: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastStatus = useRef<string | null>(null);
  const dragDepth = useRef(0);

  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedTitle, setLoadedTitle] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => () => abortRef.current?.abort(), []);

  function run(file: File) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    lastStatus.current = null;
    setBusy(true);
    setStatus(null);
    setResult(null);
    setError(null);
    setLoadedTitle(null);

    if (/\.midi?$/i.test(file.name)) {
      setStatus("Reading MIDI…");
      midiToScore(file)
        .then((score) => {
          if (abortRef.current !== controller) return;
          onScore?.(score, score.title);
          setLoadedTitle(score.title);
        })
        .catch((err: unknown) => {
          if (err instanceof Error && err.name === "AbortError") return;
          if (abortRef.current !== controller) return;
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (abortRef.current === controller) setBusy(false);
        });
      return;
    }

    const onStatus = (s: NdiStatus) => {
      lastStatus.current = s.message;
      setStatus(s.message);
    };

    extractDirections(file, onStatus, { signal: controller.signal })
      .then((d) => {
        onDirections(d);
        setResult(lastStatus.current ?? IDLE_TEXT);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (abortRef.current === controller) setBusy(false);
      });
  }

  return (
    <fieldset
      className={`flex min-w-0 shrink-0 items-center gap-2 rounded-full border border-dashed px-3 py-1 max-w-80 ${dragging ? "border-primary" : "border-border"}`}
      onDragOver={(e) => e.preventDefault()}
      onDragEnter={(e) => {
        e.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) run(file);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.html,.txt,.md,.mid,.midi"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) run(file);
        }}
      />
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="text-sm font-semibold"
        disabled={busy}
        title={loadedTitle !== null ? "Upload another document or MIDI" : undefined}
        onClick={() => inputRef.current?.click()}
      >
        {loadedTitle !== null ? "📂" : "📄 Document or 🎼 MIDI"}
      </Button>
      <FileHelp />
      {busy ? (
        <>
          <span className="h-1 w-12 shrink-0 overflow-hidden rounded-full bg-muted">
            <span className="block h-full w-1/3 animate-pulse rounded-full bg-primary" />
          </span>
          {status && (
            <span className="truncate text-xs text-muted-foreground" title={status}>
              {status}
            </span>
          )}
        </>
      ) : error ? (
        <span className="truncate text-xs text-accent" title={error}>
          {error}
        </span>
      ) : loadedTitle !== null ? (
        <span className="flex min-w-0 flex-col text-xs leading-tight">
          <span className="break-words font-semibold text-primary">🎼 {loadedTitle}</span>
          <span className="text-muted-foreground">Selected: press ▶ Play or Space</span>
        </span>
      ) : result ? (
        <span className="truncate text-xs text-primary" title={result}>
          {result}
        </span>
      ) : (
        <span className="truncate text-xs text-muted-foreground">{IDLE_TEXT}</span>
      )}
    </fieldset>
  );
}
