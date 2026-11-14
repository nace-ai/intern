"""Trim a Standard MIDI file to a bar window, for the app's MIDI score upload.

The app's reader (web/src/orchestra/midi.ts) uses only the FIRST time-signature
and FIRST tempo events (they must sit at tick 0), cuts bars on a grid starting
at tick 0, and takes the melody as the highest note at each onset. This script
prepares short, recognizable demo files: it drops all time_signature and
set_tempo messages, optionally shifts everything right by a number of eighth
notes (silence at the start, to align a pickup), keeps only a bar window
moved to begin at tick 0, closes notes still sounding at the window end,
drops notes that started before the window, and writes a fresh track 0 with
the time signature and tempo at tick 0.

--show prints, per bar, the melody (highest note at each onset, with its
offset in eighths) and marks the bar where the lowest track first plays.
"""

from __future__ import annotations

import argparse
import sys

import mido

# MIDI note number -> name with sharps (C=0). We only ever need this up high,
# but the pattern repeats every 12 so any pitch class works.
_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(note: int) -> str:
    """'60' -> 'C4' (MIDI 60 is middle C, octave 4)."""
    return f"{_NAMES[note % 12]}{note // 12 - 1}"


def to_absolute(mid: mido.MidiFile) -> list[list[tuple[int, mido.Message | mido.MetaMessage]]]:
    """Each track as a list of (absolute_tick, message), with time reset to 0."""
    out: list[list[tuple[int, mido.Message | mido.MetaMessage]]] = []
    for track in mid.tracks:
        events: list[tuple[int, mido.Message | mido.MetaMessage]] = []
        tick = 0
        for msg in track:
            tick += msg.time
            events.append((tick, msg))
        out.append(events)
    return out


def to_relative(events: list[tuple[int, mido.Message | mido.MetaMessage]]) -> mido.MidiTrack:
    """Back to a MidiTrack with delta times, from (absolute_tick, message)."""
    track = mido.MidiTrack()
    prev = 0
    for tick, msg in events:
        d = msg.dict()
        attrs = {k: v for k, v in d.items() if k not in ("type", "time")}
        factory = mido.MetaMessage if isinstance(msg, mido.MetaMessage) else mido.Message
        track.append(factory(str(d["type"]), time=tick - prev, **attrs))
        prev = tick
    return track


def time_sig_of(mid: mido.MidiFile) -> tuple[int, int]:
    """The first time_signature anywhere in the file, or 4/4 if none."""
    for track in mid.tracks:
        for msg in track:
            if msg.type == "time_signature":
                return msg.numerator, msg.denominator
    return 4, 4


def show(mid: mido.MidiFile) -> None:
    """Print per bar: melody (highest note at each onset, offset in eighths)."""
    tracks = to_absolute(mid)
    num, den = time_sig_of(mid)
    beats_per_bar = num * 4.0 / den
    bar_ticks = int(round(mid.ticks_per_beat * beats_per_bar))
    eighth_ticks = int(round(bar_ticks / (beats_per_bar * 2)))
    sixteenth_ticks = eighth_ticks // 2
    last_tick = max((tick for track in tracks for tick, _ in track), default=0)
    n_bars = last_tick // bar_ticks + 1
    # the lowest track: smallest note ever played; ties -> earliest first note
    info: list[tuple[int, int, int]] = []  # (min note, first tick, track index)
    for i, track in enumerate(tracks):
        ons: list[tuple[int, int]] = []
        for t, msg in track:
            d = msg.dict()
            if d.get("type") == "note_on" and int(d["velocity"]) > 0:
                ons.append((t, int(d["note"])))
        if ons:
            info.append((min(n for _, n in ons), ons[0][0], i))
    lowest = min(info) if info else None
    onsets: dict[tuple[int, int], int] = {}  # (bar, offset_sixteenths) -> highest note
    for track in tracks:
        for tick, msg in track:
            d = msg.dict()
            if d.get("type") == "note_on" and int(d["velocity"]) > 0:
                bar, off = divmod(tick, bar_ticks)
                off_s = int(round(off / sixteenth_ticks))
                onsets[(bar, off_s)] = max(onsets.get((bar, off_s), 0), int(d["note"]))
    for bar in range(n_bars):
        marks = sorted(e for (b, e) in onsets if b == bar)
        line = " ".join(f"{note_name(onsets[(bar, e)])}@{e / 2:g}" for e in marks)
        if lowest is not None and bar == lowest[1] // bar_ticks:
            line += f"   <-- lowest track (track {lowest[2]}) first plays"
        print(f"bar {bar + 1}: {line or '-'}")


def _parse_sig(sig: str) -> tuple[int, int]:
    num_s, den_s = sig.split("/")
    return int(num_s), int(den_s)


def trim(mid: mido.MidiFile, args: argparse.Namespace) -> mido.MidiFile:
    """Shift right, keep the bar window moved to tick 0, write tempo at 0."""
    num, den = _parse_sig(args.time_sig)
    beats_per_bar = num * 4.0 / den
    bar_ticks = int(round(mid.ticks_per_beat * beats_per_bar))
    eighth_ticks = int(round(bar_ticks / (beats_per_bar * 2)))
    shift = args.shift_eighths * eighth_ticks
    start = (args.start_bar - 1) * bar_ticks
    end = start + args.bars * bar_ticks
    kept: list[list[tuple[int, mido.Message | mido.MetaMessage]]] = []
    for track in to_absolute(mid):
        events: list[tuple[int, mido.Message | mido.MetaMessage]] = []
        pre: set[tuple[int, int]] = set()  # (channel, note) started before window
        open_: set[tuple[int, int]] = set()  # (channel, note) sounding in window
        for tick, msg in track:
            d = msg.dict()
            mtype = d.get("type")
            if mtype in ("time_signature", "set_tempo"):
                continue
            tick += shift
            if mtype == "note_off" or (mtype == "note_on" and int(d["velocity"]) == 0):
                key = (int(d["channel"]), int(d["note"]))
                if key in pre:  # off for a note dropped at the window start
                    pre.discard(key)
                    open_.discard(key)
                elif start <= tick < end:
                    events.append((tick - start, msg))
                    open_.discard(key)
            elif mtype == "note_on":
                key = (int(d["channel"]), int(d["note"]))
                if tick < start:
                    pre.add(key)
                    open_.discard(key)
                elif tick < end:
                    events.append((tick - start, msg))
                    open_.add(key)
            elif start <= tick < end:
                events.append((tick - start, msg))
        for channel, note in open_:
            events.append((end - start, mido.Message("note_off", channel=channel, note=note, velocity=0)))
        kept.append(sorted(events, key=lambda e: e[0]))
    ts = mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0)
    tempo = mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(args.eighth_bpm / 2), time=0)
    out = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
    for i, ev in enumerate(kept):
        out.tracks.append(to_relative([(0, ts), (0, tempo)] + ev if i == 0 else ev))
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="input .mid file")
    parser.add_argument("output", nargs="?", default=None, help="output .mid file (ignored with --show)")
    parser.add_argument("--time-sig", default="4/4", help="time signature, e.g. 3/8")
    parser.add_argument("--eighth-bpm", type=float, default=200.0, help="tempo in eighth-note BPM")
    parser.add_argument("--shift-eighths", type=int, default=0, help="eighths of silence to insert at the start")
    parser.add_argument("--start-bar", type=int, default=1, help="1-based first bar, after shifting")
    parser.add_argument("--bars", type=int, default=16, help="number of bars to keep")
    parser.add_argument("--show", action="store_true", help="print per-bar melody instead of writing")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    mid = mido.MidiFile(args.input)
    if args.show:
        show(mid)
        return 0
    if args.output is None:
        raise SystemExit("output is required unless --show is given")
    out = trim(mid, args)
    out.save(args.output)
    print(f"wrote {args.output}: {args.bars} bars, {args.time_sig}, {args.eighth_bpm} eighth-note BPM")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
