"""Write La donna è mobile (music21 corpus 'verdi/laDonnaEMobile') as demo/midi/la_donna.mid.

For the score-path A/B: the browser MIDI reader (web/src/orchestra/midi.ts) honors only the
first time-signature and first tempo events, so both must sit at tick 0. The corpus already
carries 3/8 at offset 0; this script replaces every existing MetronomeMark with a single one
at offset 0 of parts[0]'s first measure: eighth note = 150 (75 quarter notes per minute),
matching web/public/scores/la_donna.json (3/8, eighth_bpm 150).
"""

import argparse
import re
from pathlib import Path

import mido
import music21
from music21.midi.base import MidiFile
from trim_midi import trim

OUT = Path(__file__).resolve().parent.parent / "demo" / "midi" / "la_donna.mid"

WANTED = {"SET_TEMPO", "TIME_SIGNATURE", "NOTE_ON"}


def main() -> None:
    score: music21.stream.Score = music21.corpus.parse("verdi/laDonnaEMobile")

    for part in score.parts:
        marks = [el for el in part.recurse() if isinstance(el, music21.tempo.MetronomeMark)]
        for mark in marks:
            mark.activeSite.remove(mark)

    tempo = music21.tempo.MetronomeMark(number=150, referent=music21.duration.Duration(0.5))
    first_measure = score.parts[0].getElementsByClass(music21.stream.Measure)[0]
    first_measure.insert(0, tempo)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    score.write("midi", fp=str(OUT))

    # The music21 MIDI writer expands the repeat, so the file now holds the 35-bar
    # tune twice (70 bars). Trim it back to the first 35 bars (the whole aria, once).
    trim_args = argparse.Namespace(time_sig="3/8", eighth_bpm=150, shift_eighths=0, start_bar=1, bars=35)
    trimmed = trim(mido.MidiFile(str(OUT)), trim_args)
    trimmed.save(str(OUT))
    print(f"trimmed to {trim_args.bars} bars (3/8, {trim_args.eighth_bpm} eighth BPM)")

    # Read the file back raw: the web reader only honors the first time-signature and first
    # tempo events, so report those (with absolute tick) plus the NOTE_ON count.
    mf = MidiFile()
    mf.open(str(OUT))
    mf.read()
    conductor = mf.tracks[0]
    tick = 0
    first_ts = first_tempo = None
    notes = sum(
        1 for tr in mf.tracks for item in tr.events if type(item).__name__ == "MidiEvent" and "NOTE_ON" in str(item)
    )
    for item in conductor.events:
        if type(item).__name__ == "DeltaTime":
            tick += int(item.data or 0)
            continue
        match = re.search(r"MidiEvent (\w+)", str(item))
        name = match.group(1) if match else ""
        if name == "SET_TEMPO":
            data = item.data
            if first_tempo is None and isinstance(data, bytes | bytearray) and len(data) >= 3:
                micro = data[0] << 16 | data[1] << 8 | data[2]
                first_tempo = (tick, round(60_000_000 / micro, 1))
        elif name == "TIME_SIGNATURE":
            data = item.data
            if first_ts is None and isinstance(data, bytes | bytearray) and len(data) >= 2:
                first_ts = (tick, f"{data[0]}/{2 ** data[1]}")
        elif name == "NOTE_ON":
            notes += 1
    print("parts:", len(mf.tracks))
    print("first time signature:", first_ts)
    print("first tempo (quarter bpm) at absolute tick:", first_tempo)
    print("note-on events:", notes)


if __name__ == "__main__":
    main()
