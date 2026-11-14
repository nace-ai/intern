# Swarm Orchestra

A hackathon project (Nace AI Drexathon, Swarm Intelligence track). A swarm of musician agents plays live in the
browser. Every note every agent plays is a decision made by Drex, Nace AI's decision model. The musicians listen to
each other: what the band played recently is in the state Drex reads before every decision. Two ways to play:
improv (pick a mood) and score mode (the swarm performs Verdi's "La donna è mobile"). NDI (Nace's document
intelligence) reads a document, uploaded in the page, and gives every musician a direction per section.

Hard deadline: 3:00 PM today. Prefer the simplest thing that works and sounds good. No speculative features.

## Architecture

Everything runs in the browser, in TypeScript. There is no app server, no WebSocket and no Python at runtime.
The page keeps the beat clock, builds Drex's state and questions, calls Drex once per beat (or bar), and plays
the result with Tone.js. The only server is Vite's dev server, whose proxy adds the API key, so the key never
reaches the browser.

## Tooling (follow exactly)

- The app lives in `web/`: Vite + React + TypeScript, Tailwind CSS v4, shadcn/ui components, Tone.js, Biome.
- Package manager: pnpm. Add packages ONLY with `pnpm add <pkg>` (dev tools: `pnpm add -D <pkg>`), so versions
  resolve to the latest. Never write versions in package.json by hand; never use npm or yarn.
- shadcn components: `pnpm dlx shadcn@latest add <component>`; never hand-write files in `src/components/ui/`.
- Lint and format with Biome only (no ESLint, no Prettier).
- Before saying a step is done, all must pass, run in `web/`:
  `pnpm exec biome check .` (fix with `pnpm exec biome check --write .`), `pnpm exec tsc -b`, `pnpm build`.
- Hot reload must keep working: `pnpm dev` serves the app with Vite HMR and React Fast Refresh. Never disable HMR.
- Python is retired except one-time scripts in `scripts/` (uv-managed: `uv add`, `uv run`; ruff and ty must pass).
- Never read, print or write `.env` files. The key reaches the proxy through Vite's `loadEnv`.

## Layout (`web/`)

```
web/vite.config.ts             React + Tailwind plugins, @ alias, the /nace proxy (below)
web/src/orchestra/drex.ts      Drex client: decide(state, questions) through the proxy
web/src/orchestra/band.ts      musicians, moods, scales, chords, state/question builder, sampling, one step
web/src/orchestra/score.ts     score mode: loads the score JSON, renders per-bar choices into note events
web/src/orchestra/audio.ts     Tone.js engine: samplers + synths, plays a beat or a list of note events
web/src/orchestra/ndi.ts       NDI: upload a document, run an extract job, poll, return directions
web/src/orchestra/conductor.ts the clock: runs the band ahead of playback, schedules audio and UI updates
web/src/components/            the stage (musician cards), mood bar, upload box, stats strip (shadcn/ui)
web/public/samples/            instrument samples (tonejs-instruments, CC-BY 3.0); manifest.json
web/public/scores/             la_donna.json (exported once by scripts/export_score.py) and its directions
```

## The proxy

`vite.config.ts` reads `NACE_API_KEY` with `loadEnv(mode, path.resolve(__dirname, '..'), '')` (the `.env` is in the
repo root) and proxies `/nace/*` to `https://console.nace.ai/v1/*`, adding `Authorization: Bearer <key>` and
`changeOrigin: true`. The browser only ever calls `/nace/...`. Nothing with the key is exposed to client code
(no `VITE_` prefix).

## Drex API (through the proxy)

- `POST /nace/systemone`, JSON body:
  `{"model": "drex-latest", "state": "<text>", "questions": {"flute": {"type": "choice", "instructions": "...",
  "criteria": {"E5": "the 5th, strong and open", "rest": "stay silent"}}}}`
- Response: `{"answers": {"flute": {"type": "choice", "choice": "E5", "confidence": 0.58,
  "probabilities": {"E5": 0.69, "rest": 0.31}}}, "usage": {"input_tokens", "output_tokens", "cost"}}`.
  `usage` or `cost` may be missing or null: treat as 0.
- At most 16 questions per request: chunk and send chunks concurrently (`Promise.all`). ~0.3 s per request.
- Always model `drex-latest`. Abort a round after 2 s (AbortController): melodic musicians then hold (or rest if
  nothing is sounding) and drums repeat their last pattern; count it as a timeout.

## NDI (through the proxy)

Endpoints under `/nace/documents/...`: `upload-grants` / `upload-sessions` to upload a file, `extract` to start an
extract job (inline JSON Schema plus `instructions`), `jobs/{id}` to poll. The exact request shapes are in
`https://console.nace.ai/openapi.json`; read it, don't guess. The extract schema:
`{"sections": [{"name", "summary", "mood", "quote", "directions": {"<musician id>": "one sentence"}}]}` in document
order. The default directions for "La donna è mobile" are cached in `web/public/scores/la_donna_directions.json`, so
the demo never waits on NDI.

## The band (port of the Python reference in `orchestra/band.py`, with the fixes below)

- Musicians (id, sound, register, personality):
  - `kick` drums: steady heartbeat, lands on beats 1 and 3, rarely fancy.
  - `snare` drums: answers the kick on 2 and 4, fills before a new phrase.
  - `hats` drums: keeps time in eighths, follows the mood's energy.
  - `bass` octaves 2-3: anchors the chord root, moves stepwise, locks with the kick.
  - `pad_low`, `pad_high` octave 4: long held chord tones, change with the chord, avoid each other's note.
  - `flute` octave 5: sparse but plays at least one phrase every bar, answers phrases others start.
  - `violin` octaves 4-5: romantic, long lines, holds notes.
  - `synth` octave 5: slow soft arpeggios one note per beat in calm moods, fast busy runs when the energy is high.
  - `kazoo` octaves 4-5: plays in every mood; the rebel with surprising notes and odd rhythms, but still listens.
  - `bells` octave 6: sparse sparkle on phrase starts.
- Moods (tempo BPM, scale, temperature, 4-bar chord loop):
  - `calm`: 80, A minor pentatonic, 0.6, Am-F-C-G
  - `triumphant`: 112, C major, 0.5, C-G-Am-F
  - `dark`: 72, A phrygian (A A# C D E F G), 0.7, Am-A#-Am-Gm
  - `dreamy`: 76, C whole-tone, 0.9, Cmaj7-D7-Cmaj7-D7 (chord tones within the scale)
  - `funky`: 104, E dorian, 0.8, Em7-A7-Em7-A7
  - `chaos`: 120, C chromatic, 1.3, C-F#-C-F#
- One Drex round per quarter-note beat. Melodic options: the scale notes in the musician's register, `rest`, and
  `hold` (only offered while a note is sounding). Drum options: `x.`, `xx`, `.x`, `..`; hats also `xX` (accent).
- Option descriptions carry musical meaning: chord role ("root of Am, home", "3rd, sets the color", "5th, strong",
  "color note, adds tension") plus motion from the musician's sounding note ("a step up from D5"); `hold` names the
  note ("keep sounding E5"); `rest` says how long it has rested ("you have rested 3 beats").
- State: "You are deciding bar B beat N of a 4-bar phrase (beat 1 is the downbeat)", the mood and its feel, the
  current chord and its tones, the current NDI section and directions if any, and each musician's last 8 beats
  ("(start)" for beats not played yet). Each musician's question: its personality, its current NDI direction, and
  what it plays on that beat.
- One-beat-delayed listening: the round for beat n+1 sees the band up to beat n-1, so two rounds can be in flight
  at once. The conductor keeps at most 2 beats decided ahead of playback.
- Sampling, in two levels so many note options can't drown in rest: first sample the kind (note / rest / hold) from
  the summed probabilities at the mood's temperature, then the note within notes. Temperature 0 = argmax at both
  levels. Keep Drex's raw probabilities for the UI. A missing answer = hold/rest (melodic) or `..` (drums).
- After a mood change, a held note no longer in the new scale is released.

## Score mode

- `scripts/export_score.py` (music21, run once) writes `web/public/scores/la_donna.json` from
  `music21.corpus.parse("verdi/laDonnaEMobile")` (3/8, 35 bars, 2 piano parts):
  `{"title", "composer", "beats_per_bar": 3, "eighth_bpm", "sections": [{"name", "start_bar", "end_bar"}],
  "bars": [{"index", "chord": "F major (F A C)", "parts": {"<musician>": [{"note": "C5", "offset": 0, "dur": 1}]}}]}`
  (offset and dur in eighths). Every melodic musician gets a written part in its register (melody, inner voices or
  bass line, octave-transposed). Drums get none.
- ScoreData type: `web/src/orchestra/scoreTypes.ts`.
- beats_per_bar counts eighths per bar (4/4 = 8, 3/4 and 6/8 = 6, 3/8 = 3); drum patterns are chosen as 3-eighth cells and tiled across the bar (truncate the last cell).
- Chords use '<root> <kind> (<tones>)' with sharps, root first; kinds go beyond major/minor (e.g. dominant seventh).
- One Drex round per bar. Rendering options per melodic musician: `as_written`, `octave_up`, `octave_down`,
  `ornament` (add a neighbor or passing note), `sparse` (first note only, held), `melody` (the melody line in your
  register), `rest`. Drums choose a 3-eighth pattern: `x..`, `x.x`, `xxx`, `..x`, `...`.
- NDI section i applies to score section i (spread proportionally when counts differ).

## Audio (`audio.ts`)

flute, violin, bass, pad_low, pad_high and bells use `Tone.Sampler` with `public/samples/manifest.json` (Sampler
repitches between the recorded notes). kick MembraneSynth, snare NoiseSynth, hats NoiseSynth through a highpass,
synth a short square Synth, kazoo a sawtooth Synth with Vibrato. One shared Reverb and a Limiter. Exposes:
`init(onProgress)`, `playBeat(beat, time)` (owns hold/rest bookkeeping), `playEvents(events, time)`, `stopAll()`.
Load Tone.js from the `tone` package.

## UI

Dark stage, warm accents, large type readable from the back of a room, all 11 cards visible on a laptop screen
without scrolling. Start/Stop and Perform buttons (Start shows sample-loading progress until audio is ready; the
click calls `Tone.start()`), six mood buttons, a "Tell the band how to feel..." input (Drex picks the mood preset),
an upload box for a document (NDI directions; shows progress while the job runs), one card per musician (emoji,
name, what it just played in large type, up to 4 probability bars with the played option highlighted, a pulse on
notes and hits, dimmed on rest, its current NDI direction), the current section and NDI's quote, and a stats strip
(decisions, decisions/sec, average Drex latency, cost, timeouts, tempo, bar:beat). Card updates are scheduled with
`Tone.getDraw()` at the moment the beat sounds. Footer credit: samples by tonejs-instruments (CC-BY 3.0).

## The stage: lanes (horizontal "Guitar Hero")

The centerpiece is one horizontal lane per musician, all 11 stacked. Time flows right to left: segments enter at
the right edge the moment Drex decides them (up to 2 beats before they sound), scroll left, and light up as they
cross a fixed playhead about 20% from the left; then they fade out to the left.

- `web/src/orchestra/timeline.ts`: the shared feed. Times are Tone.js context seconds (`Tone.now()` clock).
  ```ts
  export type Segment = {
    musician: string;                     // musician id
    kind: "note" | "hold" | "hit" | "accent";
    label: string;                        // "E5", or "x"/"X" for drums
    start: number;                        // Tone time, seconds
    dur: number;                          // seconds
    prob: number;                         // Drex probability of the played option (0..1)
    isTop: boolean;                       // played option == Drex's top choice
  };
  export const timeline: {
    add(segments: Segment[]): void;       // the conductor calls this when a beat/bar is decided
    clear(): void;
    subscribe(fn: () => void): () => void;
    recent(sinceTime: number): Segment[];
  };
  ```
  The conductor adds one segment per musician per beat: `note` when a new note starts, `hold` for a continuation
  (drawn joined to the previous segment), drums one `hit`/`accent` per eighth. Rests add nothing.
- `web/src/components/Lanes.tsx`: draws the lanes on one `<canvas>` with `requestAnimationFrame`, sized to its
  container and devicePixelRatio. Props: `musicians: string[]` (row order) and `rowHeight: number`. Inside a
  lane, a note's vertical position follows its pitch within the musician's register; its color follows the pitch
  class; its brightness follows `prob`; an outline marks `isTop === false` (the swarm went against Drex's top
  pick). Drum hits are ticks, accents bigger. Segments flash as they cross the playhead. Smooth at 60 fps.
- `web/src/components/Stage.tsx` composes the rows: a fixed-width musician panel on the left of each row (emoji,
  name, current note, mini probability bars, NDI direction) and `<Lanes>` filling the rest, with identical row
  heights so panels and lanes line up. All 11 rows fit on a laptop screen without scrolling.

## Shared modules (build against these exact exports)

`web/src/orchestra/musicians.ts` (written first, by Head's intern; everyone imports from it):
```ts
export const MUSICIAN_IDS = ["kick", "snare", "hats", "bass", "pad_low", "pad_high",
  "flute", "violin", "synth", "kazoo", "bells"] as const;
export type MusicianId = (typeof MUSICIAN_IDS)[number];
export type Musician = { id: MusicianId; name: string; emoji: string; kind: "drums" | "melodic";
  low: number; high: number;            // register as MIDI note numbers (drums: 0, 0)
  personality: string };
export const MUSICIANS: readonly Musician[];   // in MUSICIAN_IDS order
export const MOOD_NAMES = ["calm", "triumphant", "dark", "dreamy", "funky", "chaos"] as const;
export type MoodName = (typeof MOOD_NAMES)[number];
export function noteToMidi(note: string): number;   // "A#4" -> 70
```

`web/src/orchestra/conductor.ts` (Head's intern), what the UI calls:
```ts
export type PanelUpdate = { musician: MusicianId; play: string; probs: Record<string, number>; direction?: string };
export type Stats = { decisions: number; decisionsPerSec: number; avgLatencyMs: number; cost: number;
  timeouts: number; tempo: number; bar: number; beat: number };
export type SectionInfo = { name: string; quote: string; summary?: string } | null;
export type ConductorEvent =
  | { type: "beat"; updates: PanelUpdate[]; stats: Stats }      // emitted via Tone.getDraw() at sound time
  | { type: "section"; section: SectionInfo }
  | { type: "mood"; preset: MoodName; probs?: Record<MoodName, number>; text?: string }
  | { type: "loading"; loaded: number; total: number }
  | { type: "state"; running: boolean; mode: "improv" | "score" | null }
  | { type: "error"; message: string };
export const conductor: {
  init(): Promise<void>;                    // loads samples, emits "loading"
  start(): Promise<void>;                   // improv; calls Tone.start()
  perform(): Promise<void>;                 // score mode
  stop(): void;
  setMood(preset: MoodName): void;
  moodFromText(text: string): Promise<{ preset: MoodName; probs: Record<MoodName, number> }>;
  setDirections(d: Directions): void;       // Directions type from ndi.ts
  subscribe(fn: (e: ConductorEvent) => void): () => void;
};
```
The conductor also feeds `timeline.add(...)` (see "The stage: lanes").
Conductor rules agreed with the UI: `init()` is idempotent (a second call returns the same promise; React
StrictMode runs effects twice). `perform()` loads the default directions itself (W3's `loadDefaultDirections()`
from ndi.ts) unless `setDirections()` already supplied a document's; the UI calls `setDirections` only on upload.

## Git workflow: one worktree per agent (mandatory)

Each agent works in its own git worktree on its own branch, never in the primary checkout
`/Users/jorge/drexathon2026` (that one is the integration copy: it only pulls, and it's where Jorge runs the app).
- Set up once: `git -C /Users/jorge/drexathon2026 worktree add /Users/jorge/drexathon2026-<agent> -b wt-<agent> origin/main`,
  then `cd /Users/jorge/drexathon2026-<agent>/web && pnpm install`.
- Run intern from the worktree: `cd /Users/jorge/drexathon2026-<agent> && /Users/jorge/drexathon2026/intern.sh ...`
  (the wrapper loads the keys; Vite's loadEnv also reads them from the environment, so no .env is needed there).
- Commit on your branch, staging explicit paths only. To land work:
  `git fetch origin && git rebase origin/main && <biome check, tsc -b, pnpm build> && git push origin HEAD:main`.
  If the push is rejected because someone landed first, repeat fetch + rebase + checks + push. Never force-push.
- Only ever rebase or reset your own unpushed branch commits, in your own worktree. Never run reset, rebase, stash,
  restore, checkout or clean in the primary checkout. If a commit includes files that aren't yours, report it;
  don't rewrite history.

## MIDI score upload (play any piece)

`web/src/orchestra/midi.ts`: `midiToScore(file: File): Promise<ScoreData>` turns an uploaded `.mid`/`.midi` into the
same ScoreData shape as `public/scores/la_donna.json`, in the browser (no server, no Python), using `@tonejs/midi`
to read the file and `tonal` (`Chord.detect`) to name each bar's chord. Bars come from the file's time signature
and tempo. Parts go to musicians by register: the highest active line to violin, flute and kazoo, the first melody
note per bar to bells, chord tones arpeggiated to synth, inner voices held to the pads, and the lowest line to bass,
all octave-transposed into each musician's register (MUSICIANS low/high). Percussion tracks (MIDI channel 10) are
ignored; drums improvise. Sections: every 8 bars ("Part 1", "Part 2", ...). The upload box sends `.mid`/`.midi` to
`midiToScore` (then Perform plays it) and every other file type to NDI's `extractDirections`.
Branch names use a `wt-` prefix (`wt-w1`, `wt-head`): a branch named `head` clashes with `HEAD` on macOS's
case-insensitive filesystem.

## Orchestra: roster, seating and faces (Jorge's new features)

Before playing, the user picks how many musicians the orchestra has (4-40, default 16). The app draws a roster
semi-randomly, weighted like a real orchestra, seats everyone where their section sits on a real stage, and gives
each musician a face. Several musicians can play the same instrument; each one is its own Drex agent, and the UI
shows how deterministic each one is (does it keep playing the same note?).

### Instruments and musicians
- The 11 ids in `musicians.ts` (`kick` ... `bells`) are now INSTRUMENT ids. Audio, ScoreData parts and the MIDI
  converter stay keyed by instrument. Add to musicians.ts (additive; keep every existing export working):
  `export type InstrumentId = MusicianId;` and an `INSTRUMENTS` catalog with orchestral display names, section and
  orchestra weight:
  | id | display name | section | weight |
  |---|---|---|---|
  | violin | Violin | strings | 30 |
  | pad_low | Cello | strings | 10 |
  | bass | Double bass | strings | 7 |
  | flute | Flute | woodwinds | 7 |
  | kazoo | Kazoo | woodwinds | 3 |
  | pad_high | Harmonium | keyboards | 4 |
  | synth | Synth | keyboards | 4 |
  | kick | Bass drum | percussion | 5 |
  | snare | Snare drum | percussion | 4 |
  | hats | Cymbals | percussion | 4 |
  | bells | Xylophone | percussion | 4 |
- `web/src/orchestra/roster.ts`:
  `export type Member = { id: string; instrument: InstrumentId; name: string; section: Section; trait: string;
  seat: { x: number; y: number }; faceSeed: string }` (id like "violin-2", name like "Violin 2"; seat in 0..1
  stage coordinates, audience view, conductor at bottom center).
  `generateRoster(count: number, seed: string): Member[]`: deterministic for a seed (a small seeded PRNG such as
  mulberry32). For count >= 4 it guarantees at least one violin, one double bass, one flute and one percussion
  (bass drum or snare); the rest are weighted random draws. Each member gets a random trait from: steady, shy, bold,
  dreamy, rebellious, show-off, lazy, precise, so two violins are different agents.
  `classicRoster()`: exactly one of each of the 11 instruments in MUSICIAN_IDS order (the old band; used by default
  until the UI switches).
  `seatRoster(members)`: assigns seats on semicircular arcs around the conductor. Front arcs are strings (violins on
  the left, cellos on the right, double basses at the back right); the middle arc center holds woodwinds; back left
  holds keyboards; the back row center holds percussion. Seats are spread evenly within each section's arc.
- `web/src/orchestra/faces.ts`: DiceBear like github.com/jorge-menjivar/super-agents: `@dicebear/core` and
  `@dicebear/collection`, style `toonHead` (design: Johan Melin, CC BY 4.0), `backgroundColor` = super-agents' BACKGROUNDS list
  (00acc1 039be5 1e88e5 43a047 546e7a 5e35b1 6d4c41 757575 7cb342 8e24aa c0ca33 d81b60 e53935 f4511e fb8c00 fdd835
  ffb300 00897b 3949ab). `faceUrl(seed: string): string` returns a memoized `data:image/svg+xml` URL.

### Band and conductor with a roster
- `conductor.setRoster(members: Member[])` (while stopped). Default: `classicRoster()`.
- One Drex question per member per beat (or bar); chunks of 16 run concurrently. A member's question = its name, its
  trait, its instrument's personality and register, and its current NDI direction. Its options come from its
  instrument. For more than 16 members, the state's history shows the last 4 beats instead of 8, one line per member.
- Audio: one voice set per instrument, shared by all its members (Samplers are polyphonic); hold/rest bookkeeping
  per member id. Simultaneous hits on the same drum merge.
- PanelUpdate and Segment carry the member id in `musician` and add `instrument: InstrumentId`.
- Determinism, tracked per member by the conductor and sent in each PanelUpdate as
  `det: { repeatRate: number; meanTop: number; distinct: number }`: repeatRate = share of its played notes equal to
  its previous played note; meanTop = its mean Drex top-choice probability; distinct = distinct notes played.

### UI
- Setup (W1): before Start/Perform, a "Musicians: N" slider (4-40) with a Shuffle button (new seed) that regenerates
  and re-seats the roster live; it calls conductor.setRoster. Disabled while playing.
- Orchestra stage (W4): the main stage is the seating view: every member's face at its seat (faceUrl), name below,
  the note it just played on a badge, a pulse when it plays, dimmed on rest, and a determinism ring (more filled =
  more repetitive). Clicking a face opens a popover (W1): name, instrument, trait, NDI direction, last 8 notes, and
  determinism stats.
- Lanes (W2): one lane per instrument present in the roster (at most 11 rows). Each member's segments draw in its
  instrument's lane with a small hue offset per member, so duplicates stay distinguishable.
- Fallback: git tag `classic-11` marks the last commit where the fixed 11-musician version works end to end.
