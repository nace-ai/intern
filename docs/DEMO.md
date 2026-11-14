# 5-Minute Stage Demo: Swarm Orchestra

Team Darth Vader. Pitch: the Swarm Intelligence track. A whole orchestra of agents coordinates in real time, and
every one of their decisions is made by Drex. It also fits Wildcard in one line: the weirdest thing Drex is doing
right now is deciding whether the kazoo makes a noise.

The page: http://localhost:5173 (start it with `cd web && pnpm dev`). Everything runs in the browser: the
orchestra, the beat clock and the Drex calls; the dev server only proxies the API.

Before the demo:

- Have a short document ready for the NDI beat (a page or two with clear sections, as PDF or text).
- Have `demo/midi/eine_kleine_nachtmusik.mid` ready for the MIDI upload (backup: `demo/midi/fur_elise.mid`).
  Both are short Public Domain openings the room will recognize in seconds.
- Optional: if you have an Imperial March MIDI you're licensed to use, save it as
  `web/public/scores/local/imperial_march.mid` (that folder is gitignored, so it's never committed); the dropdown
  then offers it first.
- Open the page and keep the setup card on screen. Make sure the speakers are on.

The controls: one dropdown picks what to play (improv in a mood, a piece, or an uploaded MIDI), one button plays
it (**▶ Play**, then **⏸ Pause** / **▶ Resume**; the Space bar toggles too), and **■ Stop** resets. To switch
pieces: Stop, pick, Play.

## 0:00–0:30: Assemble the orchestra

The setup card is on screen. Say:

> "This orchestra has no human playing it and no plan. You choose how big it is. Let's have sixteen musicians."

Set the slider to about 16 and click **Assemble the orchestra**. Say:

> "The roster is drawn like a real orchestra: mostly strings, a few winds, percussion at the back, and everyone
> sits where their section sits on a real stage. Every one of these faces is its own AI agent, with its own
> personality: a shy violin, a show-off violin, a rebellious kazoo."

## 0:30–1:15: Play Imperial

Leave the dropdown on its default, **Imperial**, click **▶ Play** and wait for the samples to load. Imperial is
our own dark march style. Say:

> "Every note you hear is a decision made by Drex a moment before it sounds. Every beat, every musician gets its
> own question: here is what the whole orchestra just played, here is the chord, what do you play next? That's
> the swarm: sixteen agents, one shared state, and they listen to each other through it."

Point at the faces pulsing and at the lanes:

> "The lanes on the side show every decision arriving before it sounds, and lighting up as it plays."

## 1:15–1:45: Click a face: determinism

Click a face, ideally one with a very full ring and then one with an empty ring. Say:

> "That ring is how predictable this musician is. A full ring means it keeps repeating itself; an empty one
> means it keeps surprising us. Here are its last eight notes, its trait, and how confident Drex was in its
> choices. Same model, different personalities, different behavior."

## 1:45–2:15: Type a mood

Type in the box: **a funeral for a goldfish**. Send it. When the mood probabilities show up, read out the mood
with the biggest bar. Say:

> "Or the conductor can just say something. Drex read that sentence and picked a mood itself. Those bars are its
> probabilities, and it picked <mood>. The whole mood of the room was a single Drex call."

## 2:15–3:00: Upload a document

Upload the prepared document. While it runs (about 15 to 17 seconds), keep talking over the music:

> "Now I'm giving the orchestra a document. NDI, Nace's document intelligence, is reading it right now and
> briefing each musician: it splits the document into sections and writes every instrument a one-line direction
> for each section. The orchestra keeps playing while it reads."

When the directions appear:

> "There: every musician now has a direction from the document, and it goes straight into the question Drex
> answers for it. Same orchestra, now playing toward the text."

## 3:00–4:00: Play a piece

Click **■ Stop**, pick "La donna è mobile" in the dropdown, and click **▶ Play**. Say:

> "That was improvisation. Now the orchestra plays a real piece: Verdi. There's a written part for every
> instrument, but nobody plays it literally: every bar, Drex decides for each musician how to render its part:
> as written, an octave up or down, ornamented, thinned out, or silence. The same swarm is now reading sheet
> music and improvising the details."

Then click **■ Stop**, drop `demo/midi/eine_kleine_nachtmusik.mid` in the upload box (it shows "Loaded: eine kleine
nachtmusik, 18 bars"), make sure it's the piece selected in the dropdown, and click **▶ Play** (backup:
`demo/midi/fur_elise.mid`, or pick "Eine kleine Nachtmusik" straight from the dropdown). Say:

> "And it plays any MIDI file. The browser just turned this file into parts for the whole orchestra, and now the
> swarm plays Mozart instead of Verdi, with Drex choosing every musician's rendering bar by bar."

Let it run for the rest of the minute.

## 4:00–4:30: The numbers

Point at the stats strip. Say:

> "Why Drex and not a big language model? Speed and price. Drex answers a beat's worth of decisions in about 267
> milliseconds; the same questions to an open LLM, Qwen 27B, take about 1.9 seconds. With two beats of lookahead,
> that's a top tempo of about 95 BPM for Drex against 18 for the LLM, which is no band at all. And Drex is about
> 7 times cheaper per beat. Plus it hands back a real probability for every option, which is what the orchestra
> samples from and what you see on screen."

## 4:30–5:00: Close

Let the music keep playing under your voice. Say:

> "An orchestra of agents, one model, and they listen to each other. Every note you heard was a decision made a
> moment before it happened, by Drex: the same Drex that supervised the coding agents who wrote this app. That's
> Swarm Orchestra, from Team Darth Vader. Thank you."

Click **■ Stop** after the final line.

## If something breaks

- Need a breath: press the Space bar to pause; press it again to resume where the orchestra left off.
- No sound: check the speakers and that ▶ Play was clicked. If audio is truly broken, narrate with the sound off:
  the faces, badges and lanes still show every decision as it happens.
- Drex is slow: pick a slower mood (`dark`, 72 BPM) so the rounds catch up; a late round makes the musicians hold
  their notes, it doesn't break the music. A smaller orchestra (fewer musicians) also means fewer questions per
  beat.
- NDI is slow or fails: skip it and go straight to a piece; "La donna è mobile" has cached directions.
- The orchestra version misbehaves: the git tag `classic-11` is the last version with the fixed band of 11
  musicians. Prepare it before the demo in a separate folder, so the main checkout stays untouched:
  `git worktree add ../drexathon2026-classic classic-11`, then `cd ../drexathon2026-classic/web && pnpm install`
  and, if needed on stage, `pnpm dev`.
- No WiFi: talk over a recording.
