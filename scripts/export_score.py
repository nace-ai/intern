"""Export Verdi's "La donna è mobile" (music21 corpus) as the score-mode JSON.

Run once:  uv run python scripts/export_score.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import TypedDict

import music21
from music21 import chord as m21_chord
from music21 import note as m21_note
from music21 import stream as m21_stream
from music21 import tempo as m21_tempo

TITLE = "La donna è mobile"
COMPOSER = "Giuseppe Verdi"
EIGHTHS_PER_BAR = 3
EXPECTED_BARS = 35
SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KEY_ROOT = 10  # A# major (B-flat major), fallback for silent bars
CHORD_KINDS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("major", (0, 4, 7)),
    ("minor", (0, 3, 7)),
    ("diminished", (0, 3, 6)),
    ("dominant 7th", (0, 4, 7, 10)),
)
# musician id -> (lowest octave, highest octave)
REGISTERS: dict[str, tuple[int, int]] = {
    "violin": (4, 5),
    "flute": (5, 5),
    "kazoo": (4, 5),
    "bass": (2, 3),
    "synth": (5, 5),
    "pad_low": (4, 4),
    "pad_high": (4, 4),
    "bells": (6, 6),
}


class NoteEvent(TypedDict):
    note: str
    offset: int | float
    dur: int | float


class BarData(TypedDict):
    index: int
    chord: str
    parts: dict[str, list[NoteEvent]]


class SectionData(TypedDict):
    name: str
    start_bar: int
    end_bar: int


class ScoreData(TypedDict):
    title: str
    composer: str
    beats_per_bar: int
    eighth_bpm: int
    sections: list[SectionData]
    bars: list[BarData]


NOTE_RE = re.compile(r"([A-G])(#)?([0-9])")
CHORD_RE = re.compile(r"[A-G](#)? (major|minor|diminished|dominant 7th) \(([^()]*\))")


def octave_of(midi: int) -> int:
    return midi // 12 - 1


def note_name(midi: int) -> str:
    return f"{SHARP_NAMES[midi % 12]}{octave_of(midi)}"


def eighths(value: float) -> int | float:
    """Round a length/offset in eighths to the nearest 16th (0.5), int when whole."""
    r = int(value * 2 + 0.5) / 2
    return int(r) if r.is_integer() else r


def sounds_in(measure: m21_stream.Measure) -> list[tuple[int, float, float]]:
    """(midi, offset, dur) in eighths for every sounding note/chord tone in the measure."""
    out: list[tuple[int, float, float]] = []
    elements = list(measure.getElementsByClass(m21_note.Note)) + list(measure.getElementsByClass(m21_chord.Chord))
    for el in elements:
        if el.duration.quarterLength <= 0:  # rests were skipped; this drops grace notes
            continue
        offset = float(el.getOffsetInHierarchy(measure) * 2 + measure.paddingLeft)
        dur = float(el.duration.quarterLength * 2)
        midis = [int(el.pitch.midi)] if isinstance(el, m21_note.Note) else [int(n.pitch.midi) for n in el]
        for midi in midis:
            out.append((midi, offset, dur))
    return out


def extreme_line(events: list[tuple[int, float, float]], top: bool) -> list[tuple[float, int, float]]:
    """At each onset, the highest (top) or lowest pitch, as (offset, midi, dur)."""
    by_offset: dict[float, list[tuple[int, float]]] = {}
    for midi, offset, dur in events:
        by_offset.setdefault(offset, []).append((midi, dur))
    out: list[tuple[float, int, float]] = []
    for offset in sorted(by_offset):
        midi, dur = (max if top else min)(by_offset[offset])
        out.append((offset, midi, dur))
    return out


def fit_register(line: list[tuple[float, int, float]], lo: int, hi: int) -> list[NoteEvent]:
    """Shift by the octave that puts the most notes in range, then fold outliers in by octaves."""
    best_shift = 0
    best_count = -1
    for k in (-2, -1, 0, 1, 2):
        count = sum(1 for _, midi, _ in line if lo <= octave_of(midi + 12 * k) <= hi)
        if count > best_count:
            best_shift, best_count = k, count
    notes: list[NoteEvent] = []
    for offset, midi, dur in line:
        midi += 12 * best_shift
        while octave_of(midi) < lo:
            midi += 12
        while octave_of(midi) > hi:
            midi -= 12
        assert lo <= octave_of(midi) <= hi
        start = eighths(offset)
        length = eighths(dur)
        if start + length > EIGHTHS_PER_BAR:
            length = max(0, EIGHTHS_PER_BAR - start)
        if length == 0:  # note fell exactly on the bar boundary after rounding
            continue
        notes.append({"note": note_name(midi), "offset": start, "dur": length})
    return notes


def chord_text(events: list[tuple[int, float, float]], lowest_bass: int | float | None) -> str:
    """Name the bar's chord: weighted pitch classes, best triad or dominant 7th."""
    weights: dict[int, float] = {}
    for midi, _, dur in events:
        weights[midi % 12] = weights.get(midi % 12, 0.0) + dur
    if not weights:
        root, kind = KEY_ROOT, "major"
        intervals = (0, 4, 7)
    else:
        best = -1.0
        root, kind, intervals = KEY_ROOT, "major", (0, 4, 7)
        for r in range(12):
            for kind_name, ives in CHORD_KINDS:
                score = sum(weights.get((r + i) % 12, 0.0) for i in ives)
                if lowest_bass is not None and lowest_bass % 12 == r:
                    score += 1.5
                if score > best:
                    best = score
                    root, kind, intervals = r, kind_name, ives
    tones = " ".join(SHARP_NAMES[(root + i) % 12] for i in intervals)
    return f"{SHARP_NAMES[root]} {kind} ({tones})"


def held_note(pitch_class: int, octave: int) -> NoteEvent:
    midi = 12 * (octave + 1) + pitch_class
    return {"note": note_name(midi), "offset": 0, "dur": EIGHTHS_PER_BAR}


def eighth_bpm(score: m21_stream.base.Score) -> int:
    for part in score.parts:
        for measure in part.getElementsByClass(m21_stream.Measure):
            for el in measure:
                if isinstance(el, m21_tempo.MetronomeMark) and el.quarterLength:
                    # quarter-note BPM of the reference value, converted to eighth notes per minute
                    return int(round(el.getQuarterBPM() * 0.5 / el.quarterLength))
    return 150


def build_bar(index: int, rh: list[tuple[int, float, float]], lh: list[tuple[int, float, float]]) -> BarData:
    melody = extreme_line(rh, top=True)
    bass = extreme_line(lh, top=False)
    lowest_bass = min((midi for _, midi, _ in lh), default=None)
    chord = chord_text(rh + lh, lowest_bass)
    parts: dict[str, list[NoteEvent]] = {}
    if melody:
        parts["violin"] = fit_register(melody, *REGISTERS["violin"])
        parts["flute"] = fit_register(melody, *REGISTERS["flute"])
        parts["kazoo"] = fit_register(melody, *REGISTERS["kazoo"])
        first = melody[0]
        parts["bells"] = fit_register([(first[0], first[1], 1.0)], *REGISTERS["bells"])
    if bass:
        parts["bass"] = fit_register(bass, *REGISTERS["bass"])
    root_pc, third_pc, fifth_pc = detect_tones(chord)
    parts["pad_low"] = [held_note(root_pc, 4)]
    parts["pad_high"] = [held_note(third_pc if third_pc != root_pc else fifth_pc, 4)]
    parts["synth"] = [
        {"note": note_name(12 * 6 + pc), "offset": i, "dur": 1} for i, pc in enumerate((root_pc, third_pc, fifth_pc))
    ]
    return {"index": index, "chord": chord, "parts": parts}


def detect_tones(chord: str) -> tuple[int, int, int]:
    """Root, 3rd and 5th pitch classes parsed back out of the chord text."""
    inner = chord.split("(")[1]
    names = inner.split(")")
    pcs: list[int] = []
    for name in names[0].split(" "):
        for i, sharp in enumerate(SHARP_NAMES):
            if sharp == name:
                pcs.append(i)
    assert len(pcs) >= 3, chord
    return pcs[0], pcs[1], pcs[2]


def build_score() -> ScoreData:
    score = music21.corpus.parse("verdi/laDonnaEMobile")
    parts = list(score.parts)
    right = [m for m in parts[0].getElementsByClass(m21_stream.Measure)]
    left = [m for m in parts[1].getElementsByClass(m21_stream.Measure)]
    right.sort(key=lambda m: m.number)
    left.sort(key=lambda m: m.number)
    assert len(right) == len(left) == EXPECTED_BARS
    bars: list[BarData] = []
    for index, (rm, lm) in enumerate(zip(right, left, strict=True)):
        assert rm.number == lm.number == index + 1
        bars.append(build_bar(index, sounds_in(rm), sounds_in(lm)))
    sections: list[SectionData] = [
        {"name": "Introduction (ritornello)", "start_bar": 0, "end_bar": 0},
        {"name": "Refrain", "start_bar": 1, "end_bar": 8},
        {"name": "Refrain, repeated", "start_bar": 9, "end_bar": 16},
        {"name": "Verse (variation)", "start_bar": 17, "end_bar": 24},
        {"name": "Cadenza (tremolo flourish)", "start_bar": 25, "end_bar": 32},
        {"name": "Coda", "start_bar": 33, "end_bar": 34},
    ]
    return {
        "title": TITLE,
        "composer": COMPOSER,
        "beats_per_bar": EIGHTHS_PER_BAR,
        "eighth_bpm": eighth_bpm(score),
        "sections": sections,
        "bars": bars,
    }


def validate(score_data: ScoreData) -> None:
    assert set(score_data) == {"title", "composer", "beats_per_bar", "eighth_bpm", "sections", "bars"}
    assert score_data["beats_per_bar"] == EIGHTHS_PER_BAR
    assert 100 <= score_data["eighth_bpm"] <= 170
    bars = score_data["bars"]
    assert len(bars) == EXPECTED_BARS
    for i, bar in enumerate(bars):
        assert bar["index"] == i
        assert "-" not in bar["chord"] and CHORD_RE.fullmatch(bar["chord"]), bar["chord"]
        for musician, notes in bar["parts"].items():
            lo, hi = REGISTERS[musician]
            for n in notes:
                m = NOTE_RE.fullmatch(n["note"])
                assert m, n["note"]
                assert "-" not in n["note"] and "b" not in n["note"]
                assert lo <= int(m.group(3)) <= hi, (musician, n)
                assert n["offset"] >= 0 and n["offset"] + n["dur"] <= EIGHTHS_PER_BAR, (musician, n)
    sections = score_data["sections"]
    assert sections and sections[0]["start_bar"] == 0
    for prev, cur in zip(sections, sections[1:], strict=False):
        assert cur["start_bar"] == prev["end_bar"] + 1
    assert sections[-1]["end_bar"] == EXPECTED_BARS - 1


def note_midi(name: str) -> int:
    m = NOTE_RE.fullmatch(name)
    assert m, name
    return 12 * (int(m.group(3)) + 1) + "CDEFGAB".index(m.group(1)) + (1 if m.group(2) else 0)


def summarize(score_data: ScoreData) -> None:
    bars = score_data["bars"]
    print(f"{score_data['title']} — {len(bars)} bars, eighth_bpm {score_data['eighth_bpm']}")
    for sec in score_data["sections"]:
        print(f"  {sec['name']}: bars {sec['start_bar']}-{sec['end_bar']}")
    for musician in REGISTERS:
        midis = [note_midi(n["note"]) for bar in bars for n in bar["parts"].get(musician, [])]
        rng = f"{note_name(min(midis))}-{note_name(max(midis))}" if midis else "(none)"
        print(f"  {musician}: {len(midis)} notes, {rng}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("web/public/scores/la_donna.json"))
    args = parser.parse_args()
    score_data = build_score()
    validate(score_data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(score_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    summarize(score_data)


if __name__ == "__main__":
    main()
