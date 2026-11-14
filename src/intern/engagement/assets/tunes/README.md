# Intro tunes

ProTracker modules (4-channel Amiga `.mod` files) played by the start-up intro, one picked at random
on each start. Each file here is the original module, unmodified, base64-encoded so it can be embedded
in the binary as text. The intro plays an excerpt of about eleven seconds, rendered by
`src/intro/mod.ts`. A module is not MIDI: it holds the instrument recordings as well as the notes.

`bun scripts/sync-tunes.ts` downloads them again from The Mod Archive and checks every file against
the SHA-256 recorded in that script.

| id | Title | Composer | Licence | Source |
|---|---|---|---|---|
| `neon-techno` | Neon Techno | Drozerix | Public Domain | https://modarchive.org/module.php?178172 |
| `air-ride` | Air Ride | Drozerix | Public Domain | https://modarchive.org/module.php?213325 |
| `stardust-jam` | Stardust Jam | Drozerix | Public Domain | https://modarchive.org/module.php?201039 |
| `silicon-dancer` | Silicon Dancer | Drozerix | Public Domain | https://modarchive.org/module.php?209692 |
| `hawks-claw` | The Hawk's Claw | Drozerix | Public Domain | https://modarchive.org/module.php?197917 |
| `ai-renaissance` | AI Renaissance | Drozerix | Public Domain | https://modarchive.org/module.php?200847 |
| `8bit-castle` | 8-bit Castle | JAM | Public Domain | https://modarchive.org/module.php?195782 |
| `actually-chiptune` | actually chiptune | M4kachu | CC BY 4.0 | https://modarchive.org/module.php?212567 |

## Where the licences come from

Each licence is the one shown on the module's page at The Mod Archive, set by the composer, who is
the page's registered artist (checked on 2026-11-11). "Public Domain" there links to the Creative
Commons public domain dedication (https://creativecommons.org/licenses/publicdomain/); the archive's
licensing FAQ says of such modules that "you are free to do with the module whatever you like".
"CC BY 4.0" is https://creativecommons.org/licenses/by/4.0/: reuse is allowed, including commercially,
with credit. The Mod Archive states that it does not guarantee the accuracy of this information.

Credit is given for every tune, public domain or not: in the intro itself ("♪ title by composer"),
in this file, and in the project README.

## What was left out, and why

- Most modules on the archive, including the famous ones, are under the "Mod Archive Distribution
  license": listening and redistributing the unmodified file is fine, bundling it into an application
  needs the composer's permission. None of those are used.
- Modules whose own text contradicts the page (an "All Rights Reserved" notice inside a module
  listed as public domain), or says the samples were taken from someone else's work, are not used.

## Samples

A module carries its instrument samples, and a composer's licence only covers what was theirs to
license. What the modules say about their samples:

- `actually-chiptune`: three hand-made waveforms of 64 bytes each, nothing recorded.
- `air-ride`: "Tracked, Sampled & Composed By: Drozerix".
- `stardust-jam`: recorded on the composer's Yamaha MM8, "except for 2 samples which I had in my
  general folder"; one voice is the composer's own, the other a voice program.
- `silicon-dancer`: "Voice is my own. Samples from my mm8 and some old FL sample".
- `hawks-claw`: "Samples are taken from an OST I did for an Amiga game".
- `neon-techno`, `ai-renaissance`, `8bit-castle`: no statement.
