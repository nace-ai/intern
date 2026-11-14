import { useState } from "react";
import { RosterSetup } from "@/components/RosterSetup";
import { Button } from "@/components/ui/button";
import { faceUrl } from "@/orchestra/faces";
import { INSTRUMENTS, type InstrumentId } from "@/orchestra/musicians";
import type { Member } from "@/orchestra/roster";

const SECTION_ORDER: string[] = ["strings", "woodwinds", "brass", "keyboards", "percussion"];

/** Lowercase and pluralize a display name: "Bass drum" → "bass drums", "Double bass" → "double basses". */
function pluralize(name: string): string {
  const n = name.toLowerCase();
  if (n.endsWith("ss")) return `${n}es`;
  if (n.endsWith("s")) return n;
  return `${n}s`;
}

/** The draft roster grouped by section (in SECTION_ORDER), instruments in INSTRUMENTS order. */
function sectionLines(members: Member[]): { section: string; items: string[] }[] {
  const counts: Partial<Record<InstrumentId, number>> = {};
  for (const m of members) counts[m.instrument] = (counts[m.instrument] ?? 0) + 1;
  const order = [
    ...SECTION_ORDER,
    ...new Set(
      Object.values(INSTRUMENTS)
        .map((i) => i.section)
        .filter((s) => !SECTION_ORDER.includes(s)),
    ),
  ];
  return order
    .map((section) => ({
      section,
      items: (Object.keys(INSTRUMENTS) as InstrumentId[]).flatMap((id) => {
        const n = counts[id];
        if (!n || INSTRUMENTS[id].section !== section) return [];
        const name = n === 1 ? INSTRUMENTS[id].name.toLowerCase() : pluralize(INSTRUMENTS[id].name);
        return [`${n} ${name}`];
      }),
    }))
    .filter((line) => line.items.length > 0);
}

export function SetupCard({
  onAssemble,
  ready,
  loading,
}: {
  onAssemble: (members: Member[]) => void;
  ready: boolean;
  loading: { loaded: number; total: number } | null;
}) {
  const [draft, setDraft] = useState<Member[] | null>(null);

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-6">
      <div className="flex w-full max-w-2xl flex-col gap-6 rounded-xl border border-border bg-card p-8">
        <div className="space-y-1">
          <h1 className="text-3xl font-extrabold">Build your orchestra</h1>
          <p className="text-muted-foreground">
            Every musician is its own Drex agent. Pick how many, shuffle until you like the lineup, then assemble.
          </p>
        </div>

        <RosterSetup disabled={false} onRoster={setDraft} />

        {draft && (
          <div className="space-y-1.5">
            <p className="text-sm font-semibold">
              {draft.length} {draft.length === 1 ? "musician" : "musicians"}
            </p>
            {sectionLines(draft).map((line) => (
              <p key={line.section} className="text-sm">
                <span className="mr-2 text-xs font-semibold uppercase tracking-wider text-primary">{line.section}</span>
                {line.items.join(" · ")}
              </p>
            ))}
          </div>
        )}

        {draft && (
          <div className="flex flex-wrap justify-center gap-1">
            {draft.map((m) => (
              <img key={m.id} src={faceUrl(m.faceSeed)} alt={m.name} title={m.name} className="size-7 rounded-full" />
            ))}
          </div>
        )}

        <div className="space-y-2">
          <Button
            className="h-14 w-full text-xl font-extrabold"
            disabled={draft === null}
            onClick={() => {
              if (draft) onAssemble(draft);
            }}
          >
            Assemble the orchestra
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            {ready
              ? "Samples ready"
              : loading
                ? `Loading samples ${loading.loaded}/${loading.total}…`
                : "Loading samples…"}
          </p>
        </div>
      </div>
    </div>
  );
}
