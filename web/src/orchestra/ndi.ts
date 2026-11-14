import { countApiCall } from "./apiCounter.ts";
import type { MoodName, MusicianId } from "./musicians";

const ROLES: Record<MusicianId, string> = {
  kick: "kick drum: steady heartbeat, lands on beats 1 and 3, rarely fancy",
  snare: "snare drum: answers the kick on 2 and 4, fills before a new phrase",
  hats: "hi-hats: keep time in eighths, follow the mood's energy",
  bass: "bass: anchors the chord root, moves stepwise, locks with the kick",
  pad_low: "low pad: long held chord tones, changes with the chord",
  pad_high: "high pad: long held chord tones, avoids the low pad's note",
  flute: "flute: sparse, plays at least one phrase every bar, answers phrases others start",
  violin: "violin: romantic, long lines, holds notes",
  synth: "synth: slow soft arpeggios when calm, fast busy runs when the energy is high",
  kazoo: "kazoo: the rebel, surprising notes and odd rhythms, but still listens",
  bells: "bells: sparse sparkle on phrase starts",
  viola: "viola: the warm middle voice, fills the harmony between violins and cellos, steady and modest",
  harp: "harp: shimmering arpeggios and sweeping runs on phrase starts, delicate",
  oboe: "oboe: plaintive singing solos, sweet with a touch of melancholy",
  clarinet: "clarinet: agile and mellow, playful runs, answers the flute",
  bassoon: "bassoon: the orchestra's comedian, bouncy staccato bass lines",
  horn: "french horn: noble, round sustained notes, heroic calls on downbeats",
  trumpet: "trumpet: bright fanfares and bold accents on phrase starts",
  trombone: "trombone: big brassy slides and powerful sustained chords",
  tuba: "tuba: oom-pah foundation, chord roots on the strong beats",
  timpani: "timpani: thunderous accents on the downbeat, tuned to the chord root",
  piano: "piano: the accompanist, broken chords and occasional flourishes",
};
const MUSICIAN_ORDER = Object.keys(ROLES) as MusicianId[];
const MOODS: readonly MoodName[] = ["calm", "triumphant", "dark", "dreamy", "funky", "chaos"];

export interface Section {
  name: string;
  summary: string;
  mood: MoodName | null; // null when NDI gave none or something outside MOODS
  quote: string;
  /** Tolerates unknown ids (e.g. future instruments); "" when NDI gave none. */
  directions: Record<string, string>;
}

export interface Directions {
  source: string; // the file name or URL that was read
  sections: Section[]; // in document order, at least one
}

export interface NdiStatus {
  phase: "uploading" | "queued" | "running" | "done";
  message: string; // human readable, e.g. "Uploading notes.pdf", "NDI is reading (12 s)"
  elapsedMs: number; // since extractDirections was called
}

export const DIRECTIONS_SCHEMA: Record<string, unknown> = {
  type: "object",
  properties: {
    sections: {
      type: "array",
      maxItems: 8,
      description: "The document's main sections, in document order. At most 8; merge small ones.",
      items: {
        type: "object",
        properties: {
          name: { type: "string", description: "Short section title, 1 to 4 words." },
          summary: {
            type: "string",
            description: "One sentence: what happens or is said in this section.",
          },
          mood: {
            type: "string",
            enum: [...MOODS],
            description: "The band mood that best fits the feeling of this section.",
          },
          quote: {
            type: "string",
            description: "A short verbatim quote from this section, at most 20 words.",
          },
          directions: {
            type: "object",
            description: "One sentence per musician on how to play during this section.",
            properties: Object.fromEntries(
              MUSICIAN_ORDER.map((id) => [
                id,
                {
                  type: "string",
                  description: `One sentence telling the ${ROLES[id]} how to play this section.`,
                },
              ]),
            ),
            required: MUSICIAN_ORDER,
          },
        },
        required: ["name", "summary", "mood", "quote", "directions"],
      },
    },
  },
  required: ["sections"],
};

export const DIRECTIONS_INSTRUCTIONS: string =
  "You are directing a live band of 22 musicians whose music follows this document. Split the document into " +
  "its main sections, in document order (at most 8; merge short ones). For each section give a short name, a " +
  "one-sentence summary, the mood that fits it best (calm, triumphant, dark, dreamy, funky or chaos), a short " +
  "verbatim quote from it, and for every musician one concrete sentence on how to play during that section. " +
  "Write each direction in musical terms the musician can act on (louder or softer, sparser or busier, higher " +
  "or lower, long or short notes, when to come in or rest) and tie it to what happens in that section of the " +
  "text. Keep each musician in character. The musicians:\n" +
  MUSICIAN_ORDER.map((id) => `- ${id}: ${ROLES[id]}`).join("\n");

const MAX_FILE_BYTES = 32 * 1024 * 1024;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;
const NAME_EXTENSIONS = [".pdf", ".html", ".htm", ".txt", ".md", ".docx"];

interface Grant {
  workspace_id: string;
  upload_url: string;
  token: string;
  expires_at: string;
  max_bytes: number | null;
}

interface UploadedFile {
  workspace_id?: unknown;
  file_id?: unknown;
}

interface NdiJob {
  job_id?: unknown;
  kind?: unknown;
  status?: unknown;
  result?: { data?: unknown; file?: UploadedFile } | null;
  error?: { message?: unknown } | null;
}

function fileNameFromUrl(url: string): string {
  const segment = url.split("/").pop() ?? "";
  const name = decodeURIComponent(segment);
  const lower = name.toLowerCase();
  if (NAME_EXTENSIONS.some((ext) => lower.endsWith(ext))) return name;
  return `${name}.html`;
}

async function ndiError(res: Response): Promise<Error> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { error?: { code?: unknown; message?: unknown } };
    if (body.error && typeof body.error === "object") {
      const code = typeof body.error.code === "string" ? body.error.code : "unknown";
      const message = typeof body.error.message === "string" ? body.error.message : text;
      return new Error(`NDI ${res.status} ${code}: ${message}`);
    }
    return new Error(`NDI ${res.status}: ${text}`);
  } catch {
    return new Error(`NDI ${res.status}: ${text}`);
  }
}

async function requestJson(url: string, init: RequestInit): Promise<unknown> {
  countApiCall();
  const res = await fetch(url, init);
  if (!res.ok) throw await ndiError(res);
  return (await res.json()) as unknown;
}

function normalize(raw: unknown, source: string): Directions {
  const root = typeof raw === "object" && raw !== null ? (raw as { sections?: unknown }) : {};
  const list = Array.isArray(root.sections) ? root.sections : [];
  const sections: Section[] = [];
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const e = entry as {
      name?: unknown;
      summary?: unknown;
      mood?: unknown;
      quote?: unknown;
      directions?: unknown;
    };
    if (typeof e.name !== "string" || e.name.trim() === "") continue;
    const mood = typeof e.mood === "string" ? e.mood.trim().toLowerCase() : "";
    const rawDirections =
      typeof e.directions === "object" && e.directions !== null ? (e.directions as Record<string, unknown>) : {};
    const directions = {} as Record<string, string>;
    for (const id of MUSICIAN_ORDER) {
      const value = rawDirections[id];
      directions[id] = typeof value === "string" ? value.trim() : "";
    }
    sections.push({
      name: e.name.trim(),
      summary: typeof e.summary === "string" ? e.summary.trim() : "",
      mood: MOODS.includes(mood as MoodName) ? (mood as MoodName) : null,
      quote: typeof e.quote === "string" ? e.quote.trim() : "",
      directions,
    });
  }
  if (sections.length === 0) throw new Error("NDI found no sections");
  return { source, sections };
}

export async function extractDirections(
  source: File | { url: string; fileName?: string },
  onStatus?: (status: NdiStatus) => void,
  options?: { base?: string; signal?: AbortSignal },
): Promise<Directions> {
  const base = options?.base ?? "/nace";
  const signal = options?.signal;
  const startedAt = Date.now();
  const report = (phase: NdiStatus["phase"], message: string): void => {
    onStatus?.({ phase, message, elapsedMs: Date.now() - startedAt });
  };

  let name: string;
  let docSource: Record<string, unknown>;
  if (source instanceof File) {
    name = source.name;
    if (source.size > MAX_FILE_BYTES) throw new Error("Document too large (max 32 MB)");
    report("uploading", `Uploading ${name}`);
    const safeName = name.replace(/[^A-Za-z0-9._-]/g, "_");
    const path = `swarm-orchestra/${Date.now()}-${safeName}`;
    const grant = (await requestJson(`${base}/documents/upload-grants`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, ttl_seconds: 600 }),
      signal,
    })) as Grant;
    const form = new FormData();
    form.append("file", source, source.name);
    form.append("metadata", JSON.stringify({ path }));
    countApiCall();
    const res = await fetch(grant.upload_url, {
      method: "POST",
      headers: { "X-Upload-Token": grant.token },
      body: form,
      signal,
    });
    if (!res.ok) throw await ndiError(res);
    const uploadJob = (await res.json()) as NdiJob;
    const file = uploadJob.result?.file;
    if (typeof file?.workspace_id !== "string" || typeof file.file_id !== "string") {
      const status = typeof uploadJob.status === "string" ? uploadJob.status : "unknown";
      throw new Error(`NDI upload gave no file (status: ${status}, HTTP ${res.status})`);
    }
    docSource = { type: "workspace_file", workspace_id: file.workspace_id, file_id: file.file_id };
  } else {
    name = source.fileName ?? fileNameFromUrl(source.url);
    docSource = { type: "url", url: source.url, file_name: name };
    report("queued", `Sending ${name} to NDI`);
  }

  const first = (await requestJson(`${base}/documents/extract`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({
      name: "swarm-orchestra directions",
      source: docSource,
      schema: DIRECTIONS_SCHEMA,
      instructions: DIRECTIONS_INSTRUCTIONS,
      citations: { enabled: false },
      parse_mode: "low",
    }),
    signal,
  })) as NdiJob;
  if (typeof first.job_id !== "string") throw new Error("NDI did not return a job id");

  let job: NdiJob = first;
  while (job.status === "queued" || job.status === "running") {
    if (Date.now() - startedAt >= POLL_TIMEOUT_MS) throw new Error("NDI timed out");
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    report(job.status, `NDI is reading (${seconds} s)`);
    job = (await requestJson(`${base}/documents/jobs/${job.job_id}?wait_seconds=2`, {
      method: "GET",
      signal,
    })) as NdiJob;
  }
  if (job.status === "failed" || job.status === "cancelled") {
    const message =
      typeof job.error?.message === "string" && job.error.message !== "" ? job.error.message : "no reason given";
    throw new Error(`NDI job ${job.status}: ${message}`);
  }
  if (job.status !== "succeeded") throw new Error(`NDI job ended with status ${String(job.status)}`);

  const directions = normalize(job.result?.data, name);
  report("done", `Directions ready: ${directions.sections.length} sections`);
  return directions;
}

export async function loadDefaultDirections(signal?: AbortSignal): Promise<Directions> {
  const res = await fetch("/scores/la_donna_directions.json", { signal });
  if (!res.ok) throw new Error(`Could not load default directions (HTTP ${res.status})`);
  const raw = (await res.json()) as { source?: unknown };
  const source = typeof raw.source === "string" ? raw.source : "la_donna_directions.json";
  return normalize(raw, source);
}
