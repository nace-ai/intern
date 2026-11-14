# Swarm Orchestra

An orchestra of AI musicians where every note is a live Drex decision.

## At a glance

- Project name: Swarm Orchestra
- Team: Team Darth Vader
- Track: Swarm Intelligence (also a fit for Wildcard)
- Repo: https://github.com/jorge-menjivar/drexathon2026

## Description (150 words)

Swarm Orchestra assembles an orchestra of N musicians (4-40), each its own live Drex agent with a personality, a trait, and a Toon Head face holding its instrument. They listen to each other: what the whole band played on recent beats is in the state Drex reads before every decision. The roster is drawn weighted like a real orchestra and seated where each section sits on a real stage. Each face wears a determinism ring, and a leaderboard ranks the creatures of habit against the wildcards. In improv the band plays one of 13 moods (imperial is the default), or Drex picks a mood from any sentence you type. In score mode it performs La donna è mobile, Für Elise, Eine kleine Nachtmusik, or any MIDI you upload. NDI reads a document you upload and briefs every musician with a direction for each section.

<!-- 144 words -->

## How we used Drex

- Every beat, each musician gets one choice question about what to play; the questions go out in chunks of 16, all chunks at once.
- Drex returns a calibrated probability for every option. The probabilities are shown live in the UI, and the note actually played is sampled from them with the mood's temperature in two levels: first the kind (note, rest or hold), then the note within the notes.
- From our benchmark (README): one Drex round of 11 decisions takes 267 ms at p50, Drex throughput is 41.3 decisions/second at p50 (11 decisions / 0.266 s), and the cost is $0.0000822 per round.
- 52 decisions per second, measured in a QA run with 30 musicians (1,560 decisions in 30 s, 0 timeouts).

## How we used NDI

Upload the document, start an extract job with an inline JSON Schema and instructions, and poll the job. NDI returns the document's sections in document order, each with a summary, a mood, a quote, and a one-sentence direction for every musician. Each section's directions go into the musicians' questions while that section is in play, so the band follows the document as it reads.

## Benchmark: Drex vs an open LLM

Same state and same 11 questions, 16 rounds each:

- Drex (drex-latest): 267 ms at p50 and 1252 ms at p95; $0.0000822 per round.
- LLM baseline (Qwen/Qwen3.8-27B on DeepInfra, temperature 0, json_object, thinking off): 1914 ms at p50 and 6320 ms at p95; $0.0005947 per round (about $0.0476 per minute of music at 80 BPM).
- Drex costs about 7× less per round than the LLM baseline.
- Max tempo with 2-beat lookahead at p95 is 18 BPM for the LLM baseline, versus 95 BPM for Drex.

## How we built it

The code was written by Intern, Nace AI's agent harness, with Qwen3.8-27B on DeepInfra as System 2 doing the work and Drex as System 1 supervising every turn. Several Claude Code agents directed it, coordinating over messages, each in its own git worktree. So Drex both plays the music and supervised the agents that wrote it.

## Credits

- [Drex](https://console.nace.ai) and NDI by Nace AI
- [Tone.js](https://tonejs.github.io) (MIT)
- Instrument samples from [tonejs-instruments](https://github.com/nbrosowsky/tonejs-instruments)
  by Nicholaus Brosowsky, CC-BY 3.0
- Faces: DiceBear toonHead, design by Johan Melin (CC BY 4.0, https://www.johanmelin.com), via [DiceBear](https://www.dicebear.com)
- "La donna è mobile" from the [music21](https://github.com/cuthbertLab/music21) corpus
- "Für Elise" and "Eine kleine Nachtmusik" MIDI from the [Mutopia Project](https://www.mutopiaproject.org)
  (Public Domain; see `demo/midi/README.md`)
- The cached directions for "La donna è mobile" were made by NDI from the Wikipedia article
  ["La donna è mobile"](https://en.wikipedia.org/wiki/La_donna_%C3%A8_mobile) (CC BY-SA 4.0)
- Team: Team Darth Vader
