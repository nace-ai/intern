import { midiToScore } from "@/orchestra/midi";
import type { ScoreData } from "@/orchestra/scoreTypes";

export type ScoreEntry = {
  id: string;
  title: string;
  composer: string;
  license: string;
  source_url: string;
  file: string;
  kind: "json" | "midi";
  featured?: boolean;
};

// Vite's dev server answers a missing file with index.html and status 200 (SPA
// fallback), so a probe must check the content type too.
async function localProbe(url: string, wantsJson = false): Promise<boolean> {
  try {
    const res = await fetch(url, { method: "HEAD" });
    const type = res.headers.get("content-type") ?? "";
    if (!res.ok || type.includes("text/html")) return false;
    if (wantsJson && !type.includes("json")) return false;
    return true;
  } catch {
    return false;
  }
}

export async function loadManifest(): Promise<ScoreEntry[]> {
  const res = await fetch("/scores/scores.json");
  if (!res.ok) throw new Error(`Could not load the score list (HTTP ${res.status})`);
  const builtIn = (await res.json()) as ScoreEntry[];

  const manifest: ScoreEntry[] = [];
  if (await localProbe("/scores/local/imperial_march.mid")) {
    manifest.push({
      id: "local-imperial",
      title: "Imperial March (local)",
      composer: "",
      license: "local file, not distributed",
      source_url: "",
      file: "local/imperial_march.mid",
      kind: "midi",
      featured: true,
    });
  }
  manifest.push(...builtIn);
  if (await localProbe("/scores/local/scores.json", true)) {
    try {
      const local = (await (await fetch("/scores/local/scores.json")).json()) as ScoreEntry[];
      manifest.push(...local.map((e) => ({ ...e, file: `local/${e.file}` })));
    } catch {
      // a broken local/scores.json adds nothing
    }
  }
  return manifest;
}

export async function loadScore(entryOrId: ScoreEntry | string): Promise<ScoreData> {
  const entry =
    typeof entryOrId === "string"
      ? await (async () => {
          const manifest = await loadManifest();
          const found = manifest.find((e) => e.id === entryOrId);
          if (!found) throw new Error(`Unknown piece: ${entryOrId}`);
          return found;
        })()
      : entryOrId;
  const res = await fetch(`/scores/${entry.file}`);
  if (!res.ok) throw new Error(`Could not load “${entry.title}” (HTTP ${res.status})`);
  if (entry.kind === "json") return (await res.json()) as ScoreData;
  const file = new File([await res.blob()], entry.file);
  const score = await midiToScore(file);
  return { ...score, title: entry.title, composer: entry.composer };
}
