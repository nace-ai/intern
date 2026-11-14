# Demo MIDI files

Short, recognizable openings for the MIDI score upload on the stage. The reader
(`web/src/orchestra/midi.ts`) uses the first time-signature and tempo events
(both at tick 0), cuts bars on a grid from tick 0, and plays one bar per Drex
round.

| file | title | composer | instrumentation | bars kept | time signature | eighth_bpm | source | license |
|------|-------|----------|-----------------|-----------|----------------|------------|--------|---------|
| `fur_elise.mid` | Für Elise WoO 59 | Ludwig van Beethoven | piano | 25 (pickup + A theme + its return) | 3/8 | 180 | <https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=931> | Public Domain |
| `eine_kleine_nachtmusik.mid` | Eine kleine Nachtmusik K. 525, 1st movement | Wolfgang Amadeus Mozart | string quartet | 18 (opening theme to phrase end) | 4/4 | 264 | <https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=900> | Public Domain |
| `la_donna.mid` | La donna è mobile (from Rigoletto) | Giuseppe Verdi | piano | 35 (the whole aria) | 3/8 | 150 | music21 corpus (`verdi/laDonnaEMobile`), made by `scripts/export_la_donna_mid.py` | Public Domain |

## How they were made

The Mutopia files were downloaded in full and trimmed with `scripts/trim_midi.py`
(tick-exact editing with mido: it shifts, keeps a bar window moved to tick 0,
closes notes still sounding at the end, and writes the time signature and tempo
at tick 0):

```
uv run python scripts/trim_midi.py fur_elise_original.mid demo/midi/fur_elise.mid \
    --time-sig 3/8 --eighth-bpm 180 --shift-eighths 2 --start-bar 1 --bars 25
uv run python scripts/trim_midi.py eine_kleine_nachtmusik_original.mid demo/midi/eine_kleine_nachtmusik.mid \
    --time-sig 4/4 --eighth-bpm 264 --shift-eighths 0 --start-bar 1 --bars 18
```

The La donna export trims the written MIDI to the first 35 bars, because music21's
MIDI writer expands the repeat and would otherwise hold the tune twice (70 bars).
The Für Elise `--shift-eighths 2` turns its two-sixteenth pickup (E5 D#5) into a
short anacrusis so the bar where the left hand enters on A starts on a beat-1
downbeat; bar 3 of the output is that downbeat. Use `--show` on any file to
print the per-bar melody (highest note at each onset, offset in eighths).
