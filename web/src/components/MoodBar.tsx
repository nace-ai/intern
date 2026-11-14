import { type KeyboardEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MOOD_NAMES, type MoodName } from "@/orchestra/musicians";

const MOOD_LABELS: Partial<Record<MoodName, string>> = {
  calm: "🌙 Calm",
  triumphant: "🏆 Triumphant",
  dark: "🖤 Dark",
  dreamy: "☁️ Dreamy",
  funky: "🕺 Funky",
  chaos: "🌪️ Chaos",
  imperial: "🌑 Imperial",
  jazzy: "🎷 Jazzy",
  lullaby: "🧸 Lullaby",
  cinematic: "🎬 Cinematic",
  spooky: "👻 Spooky",
  march: "🎖️ March",
  tango: "💃 Tango",
};

function moodLabel(preset: MoodName): string {
  const label = MOOD_LABELS[preset];
  if (label !== undefined) return label;
  return `🎵 ${preset.charAt(0).toUpperCase()}${preset.slice(1)}`;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function MoodBar(props: {
  mood: { preset: MoodName; probs?: Record<MoodName, number>; text?: string } | null;
  onMood: (preset: MoodName) => void;
  onMoodText: (text: string) => Promise<void>;
}) {
  const { mood, onMood, onMoodText } = props;
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);

  async function onEnter(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter" || pending) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    setPending(true);
    try {
      await onMoodText(trimmed);
      setText("");
    } finally {
      setPending(false);
    }
  }

  const probs = mood?.probs ?? null;
  const placeholder = pending
    ? "Drex is reading the room…"
    : mood?.text
      ? `“${truncate(mood.text, 12)}” → ${mood.preset}`
      : "Tell the band how to feel...";

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <div className="flex min-w-0 flex-1 flex-wrap gap-1">
        {MOOD_NAMES.map((preset) => (
          <Button
            key={preset}
            size="sm"
            variant={mood?.preset === preset ? "default" : "outline"}
            className={`rounded-full px-2 text-xs font-semibold${probs ? " relative" : ""}`}
            title={probs ? `${Math.round((probs[preset] ?? 0) * 100)}%` : undefined}
            onClick={() => onMood(preset)}
          >
            {moodLabel(preset)}
            {probs && (
              <span
                className="pointer-events-none absolute bottom-0.5 left-2 h-0.5 rounded-full bg-current opacity-70"
                style={{ width: `calc((100% - 1rem) * ${probs[preset] ?? 0})` }}
              />
            )}
          </Button>
        ))}
      </div>

      <Input
        className={`h-7 w-56 shrink-0 text-sm rounded-full${pending ? " animate-pulse" : ""}`}
        placeholder={placeholder}
        title={mood?.text ? `“${mood.text}” → ${mood.preset}` : undefined}
        value={text}
        disabled={pending}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onEnter}
      />
    </div>
  );
}
