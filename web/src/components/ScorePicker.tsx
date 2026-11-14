import { useEffect, useRef, useState } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { loadManifest, type ScoreEntry } from "@/orchestra/scores";
import type { ScoreData } from "@/orchestra/scoreTypes";

const IMPERIAL_LABEL = "🌑 Imperial: Team Darth Vader";

export type Selection =
  | { kind: "imperial" }
  | { kind: "improv" }
  | { kind: "score"; id: string; title: string; score?: ScoreData };

export function SelectionPicker({
  value,
  onChange,
  uploaded,
  disabled,
}: {
  value: Selection;
  onChange: (s: Selection) => void;
  uploaded: { id: string; title: string; score: ScoreData }[];
  disabled: boolean;
}) {
  const [entries, setEntries] = useState<ScoreEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const touchedRef = useRef(false);
  const defaultedRef = useRef(false);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    let on = true;
    loadManifest()
      .then((list) => {
        if (on) {
          setEntries(list);
          setLoaded(true);
        }
        if (on && !touchedRef.current && !defaultedRef.current && list.some((e) => e.id === "local-imperial")) {
          defaultedRef.current = true;
          onChangeRef.current({ kind: "score", id: "local-imperial", title: "Imperial March (local)" });
        }
      })
      .catch((err) => {
        if (on) {
          setError(err instanceof Error ? err.message : String(err));
          setLoaded(true);
        }
      });
    return () => {
      on = false;
    };
  }, []);

  function choose(id: string) {
    touchedRef.current = true;
    if (id === "imperial") {
      onChange({ kind: "imperial" });
      return;
    }
    if (id === "improv") {
      onChange({ kind: "improv" });
      return;
    }
    const up = uploaded.find((u) => u.id === id);
    if (up) {
      onChange({ kind: "score", id, title: up.title, score: up.score });
      return;
    }
    const entry = entries.find((e) => e.id === id);
    if (entry) onChange({ kind: "score", id, title: entry.title });
  }

  const stringValue = value.kind === "score" ? value.id : value.kind;
  const title = value.kind === "imperial" ? IMPERIAL_LABEL : value.kind === "improv" ? "Improv (moods)" : value.title;

  return (
    <div className="relative">
      <Select value={stringValue} onValueChange={(v) => v && choose(v)} disabled={disabled}>
        <SelectTrigger className="h-10 w-[200px]" disabled={disabled || !loaded}>
          <span className="min-w-0 flex-1 truncate text-sm">{loaded ? title : "loading…"}</span>
        </SelectTrigger>
        <SelectContent>
          {entries
            .filter((e) => e.featured)
            .map((e) => (
              <SelectItem key={e.id} value={e.id}>
                <span className="truncate">{e.title}</span>
                <span className="truncate text-xs text-muted-foreground">{e.composer}</span>
              </SelectItem>
            ))}
          <SelectItem value="imperial">
            <span className="truncate">{IMPERIAL_LABEL}</span>
          </SelectItem>
          <SelectItem value="improv">
            <span className="truncate">Improv (moods)</span>
          </SelectItem>
          {entries
            .filter((e) => !e.featured)
            .map((e) => (
              <SelectItem key={e.id} value={e.id}>
                <span className="truncate">{e.title}</span>
                <span className="truncate text-xs text-muted-foreground">{e.composer}</span>
              </SelectItem>
            ))}
          {uploaded.map((u) => (
            <SelectItem key={u.id} value={u.id}>
              <span className="truncate">Uploaded: {u.title}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <p className="absolute left-0 top-full z-50 mt-1 w-[200px] text-xs text-red-500">{error}</p>}
    </div>
  );
}
