"""The intro tunes: tracker modules by demoscene musicians (assets/tunes/README.md says where each
comes from and under which licence), rendered to stereo PCM by a ProTracker player: 4–32
channels, 31 samples, the standard effect commands, a silence-before-the-cut start, and
levelling. A cut is written to disk as a WAV and played
by whatever player the system has; with no player, or INTERN_NO_MUSIC=1, the intro runs silent.

Pure Python and stdlib only: parsing, mixing, and the WAV writer need no dependencies.
"""

from __future__ import annotations

import base64
import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from random import Random

ROWS = 64
#: The PAL Amiga's clock: a sample plays at PAULA / period Hz.
PAULA = 3546894.6
MIN_PERIOD, MAX_PERIOD = 113, 856
SINE = (
    0,
    24,
    49,
    74,
    97,
    120,
    141,
    161,
    180,
    197,
    212,
    224,
    235,
    244,
    250,
    253,
    255,
    253,
    250,
    244,
    235,
    224,
    212,
    197,
    180,
    161,
    141,
    120,
    97,
    74,
    49,
    24,
)

RATE = 44_100
#: How long an intro cut aims to be; the actual length is a whole number of bars near it.
TARGET_SECONDS = 11.5
PEAK, FADE_IN, FADE_OUT = 0.93, 0.006, 0.4


def _latin1(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("latin-1").strip()


def _channels_of(tag: str) -> int:
    if tag in ("M.K.", "M!K!", "FLT4", "4CHN"):
        return 4
    if len(tag) == 4 and tag[1:] == "CHN" and tag[0].isdigit() and 2 <= int(tag[0]) <= 9:
        return int(tag[0])
    if len(tag) == 4 and tag[2:] == "CH" and tag[:2].isdigit():
        return int(tag[:2])
    return 0


@dataclass(frozen=True)
class ModSample:
    name: str
    length: int
    finetune: int  # -8..7, eighths of a semitone
    volume: int  # 0..64
    loop_start: int
    loop_length: int  # bytes; 2 or less means the sample does not loop
    data: bytes  # signed 8-bit


@dataclass(frozen=True)
class Mod:
    title: str
    channels: int
    samples: Sequence[ModSample]
    orders: tuple[int, ...]
    patterns: tuple[bytes, ...]


class NotAMod(ValueError):
    """The bytes are not a module this player understands."""


def parse_mod(data: bytes) -> Mod:
    if len(data) < 1084:
        raise NotAMod("not a MOD file: it is too short to hold the header")
    tag = _latin1(data[1080:1084])
    channels = _channels_of(tag)
    if not channels:
        raise NotAMod(f'not a MOD file this player understands (format tag "{tag}")')

    def word(at: int) -> int:
        return (data[at] << 8) | data[at + 1]

    song_length = min(128, data[950])
    table = data[952:1080]
    pattern_count = max(table) + 1
    pattern_bytes = ROWS * channels * 4
    patterns = tuple(data[1084 + i * pattern_bytes : 1084 + (i + 1) * pattern_bytes] for i in range(pattern_count))
    if any(len(p) != pattern_bytes for p in patterns):
        raise NotAMod("not a complete MOD file: pattern data is cut short")
    at = 1084 + pattern_count * pattern_bytes
    samples: list[ModSample] = []
    for i in range(31):
        h = 20 + i * 30
        length = word(h + 22) * 2
        fine = data[h + 24] & 15
        have = max(0, min(length, len(data) - at))  # a truncated file still plays what it has
        samples.append(
            ModSample(
                name=_latin1(data[h : h + 22]),
                length=have,
                finetune=fine - 16 if fine > 7 else fine,
                volume=min(64, data[h + 25]),
                loop_start=word(h + 26) * 2,
                loop_length=word(h + 28) * 2,
                data=data[at : at + have],
            )
        )
        at += length
    return Mod(
        title=_latin1(data[0:20]),
        channels=channels,
        samples=tuple(samples),
        orders=tuple(table[:song_length]),
        patterns=patterns,
    )


@dataclass(frozen=True)
class ModRow:
    time: float  # seconds from the start
    order: int
    row: int
    speed: int
    bpm: int


@dataclass(frozen=True)
class ModRender:
    sample_rate: int
    left: list[float]
    right: list[float]
    rows: list[ModRow]
    ended: bool


def _tuned(period: float, finetune: int) -> float:
    return period * 2 ** (-finetune / 96) if finetune else period


@dataclass
class _Voice:
    sample: int = -1
    playing: int = -1
    pos: float = 0.0
    end: int = 0
    on: bool = False
    period: float = 0.0
    target: float = 0.0
    volume: float = 0.0
    finetune: int = 0
    heard_period: float = 0.0
    heard_volume: float = 0.0
    effect: int = 0
    param: int = 0
    slide: float = 0.0
    vib_pos: int = 0
    vib_speed: int = 0
    vib_depth: int = 0
    trem_pos: int = 0
    trem_speed: int = 0
    trem_depth: int = 0
    offset: int = 0
    delayed: float = 0.0
    loop_row: int = 0
    loop_count: int = 0


@dataclass(frozen=True)
class RenderOptions:
    sample_rate: int
    seconds: float
    from_order: int = 0
    rows: int | None = None
    separation: float = 0.4


def render_mod(mod: Mod, opts: RenderOptions) -> ModRender:
    """Play `mod` into stereo PCM."""
    total = max(0, int(opts.seconds * opts.sample_rate + 0.999))
    left = [0.0] * total
    right = [0.0] * total
    sep = opts.separation
    near, far = (1 + sep) / 2, (1 - sep) / 2
    voices = [_Voice() for _ in range(mod.channels)]
    rows: list[ModRow] = []
    speed, bpm, order, row, tick = 6, 125, 0, 0, 0
    row_ticks = speed
    jump, break_row, loop_to, done, carry = -1, 0, -1, 0, 0.0
    ended = len(mod.orders) == 0
    from_order = max(0, min(len(mod.orders) - 1, opts.from_order))
    max_rows = opts.rows if opts.rows is not None else float("inf")
    silent = from_order > 0
    skipped = 0

    def start(v: _Voice, period: float) -> None:
        v.playing = v.sample
        v.period = period
        v.pos = 0.0
        v.vib_pos = v.trem_pos = 0
        s = mod.samples[v.sample] if 0 <= v.sample < len(mod.samples) else None
        v.end = s.length if s else 0
        v.on = v.end > 0

    def slide_volume(v: _Voice) -> None:
        up = v.param >> 4
        v.volume = min(64, v.volume + up) if up else max(0, v.volume - (v.param & 15))

    def slide_to_note(v: _Voice) -> None:
        if not v.target or not v.period:
            return
        v.period = min(v.target, v.period + v.slide) if v.period < v.target else max(v.target, v.period - v.slide)

    def vibrato(v: _Voice) -> None:
        d = (SINE[v.vib_pos & 31] * v.vib_depth) >> 7
        v.heard_period = v.period + (-d if v.vib_pos & 32 else d)
        v.vib_pos = (v.vib_pos + v.vib_speed) & 63

    def tremolo(v: _Voice) -> None:
        d = (SINE[v.trem_pos & 31] * v.trem_depth) >> 6
        v.heard_volume = max(0.0, min(64.0, v.volume + (-d if v.trem_pos & 32 else d)))
        v.trem_pos = (v.trem_pos + v.trem_speed) & 63

    def wrap(v: _Voice, s: ModSample) -> bool:
        """A looped sample plays through once, then repeats its loop; an unlooped one stops."""
        if s.loop_length <= 2:
            v.on = False
            return False
        loop_end = min(s.length, s.loop_start + s.loop_length)
        span = loop_end - s.loop_start
        if span <= 0:
            v.on = False
            return False
        v.pos = s.loop_start + ((v.pos - v.end) % span)
        v.end = loop_end
        return True

    def advance(v: _Voice, s: ModSample, by: float) -> None:
        v.pos += by
        while v.on and v.pos >= v.end:
            if not wrap(v, s):
                break

    def play_row() -> int:
        nonlocal jump, break_row, loop_to, row_ticks, speed, bpm
        pattern = mod.patterns[mod.orders[order]] if order < len(mod.orders) else None
        delay = 0
        for c in range(mod.channels):
            v = voices[c]
            at = (row * mod.channels + c) * 4
            b0 = pattern[at] if pattern and at < len(pattern) else 0
            b1 = pattern[at + 1] if pattern and at + 1 < len(pattern) else 0
            b2 = pattern[at + 2] if pattern and at + 2 < len(pattern) else 0
            param = pattern[at + 3] if pattern and at + 3 < len(pattern) else 0
            number = (b0 & 0xF0) | (b2 >> 4)
            period = ((b0 & 15) << 8) | b1
            effect = b2 & 15
            x, y = param >> 4, param & 15
            v.effect, v.param, v.delayed = effect, param, 0.0
            if 0 < number <= 31:
                v.sample = number - 1
                s = mod.samples[v.sample]
                v.volume = s.volume
                v.finetune = s.finetune
            if period:
                p = _tuned(period, v.finetune)
                if effect in (3, 5):
                    v.target = p  # slide to this note instead of starting it
                elif effect == 14 and x == 13 and y > 0:
                    v.delayed = p
                else:
                    start(v, p)
                    if effect == 9:
                        if param:
                            v.offset = param
                        v.pos = min(v.offset * 256, v.end)
            if effect == 3:
                if param:
                    v.slide = param
            elif effect == 4:
                if x:
                    v.vib_speed = x
                if y:
                    v.vib_depth = y
            elif effect == 7:
                if x:
                    v.trem_speed = x
                if y:
                    v.trem_depth = y
            elif effect == 11:
                jump, break_row = param, 0
            elif effect == 12:
                v.volume = min(64, param)
            elif effect == 13:
                break_row = x * 10 + y
                if jump < 0:
                    jump = order + 1
            elif effect == 14:
                if x == 1:
                    v.period = max(MIN_PERIOD, v.period - y)
                elif x == 2:
                    v.period = min(MAX_PERIOD, v.period + y)
                elif x == 5:
                    v.finetune = y - 16 if y > 7 else y
                elif x == 6:  # loop part of the pattern
                    if y == 0:
                        v.loop_row = row
                    else:
                        v.loop_count = y if v.loop_count == 0 else v.loop_count - 1
                        if v.loop_count > 0:
                            loop_to = v.loop_row
                elif x == 10:
                    v.volume = min(64, v.volume + y)
                elif x == 11:
                    v.volume = max(0, v.volume - y)
                elif x == 12 and y == 0:
                    v.volume = 0
                elif x == 14:
                    delay = y
            elif effect == 15:
                if param >= 32:
                    bpm = param
                elif param > 0:
                    speed = param
            v.heard_period, v.heard_volume = v.period, v.volume
        row_ticks = speed * (1 + delay)
        if not silent:  # with the tempo this row set, if it set one
            rows.append(ModRow(done / opts.sample_rate, order, row, speed, bpm))
        return delay

    def play_tick() -> None:
        for v in voices:
            x, y = v.param >> 4, v.param & 15
            v.heard_period, v.heard_volume = v.period, v.volume
            if v.effect == 0:
                if v.param and v.period:
                    v.heard_period = v.period * 2 ** (-(0, x, y)[tick % 3] / 12)  # arpeggio
            elif v.effect == 1:
                v.period = max(MIN_PERIOD, v.period - v.param)
                v.heard_period = v.period
            elif v.effect == 2:
                v.period = min(MAX_PERIOD, v.period + v.param)
                v.heard_period = v.period
            elif v.effect == 3:
                slide_to_note(v)
                v.heard_period = v.period
            elif v.effect == 4:
                vibrato(v)
            elif v.effect == 5:
                slide_to_note(v)
                v.heard_period = v.period
                slide_volume(v)
                v.heard_volume = v.volume
            elif v.effect == 6:
                vibrato(v)
                slide_volume(v)
                v.heard_volume = v.volume
            elif v.effect == 7:
                tremolo(v)
            elif v.effect == 10:
                slide_volume(v)
                v.heard_volume = v.volume
            elif v.effect == 14:
                if x == 9 and y and tick % y == 0:
                    v.pos = 0  # retrigger
                elif x == 12 and tick == y:
                    v.volume = 0
                    v.heard_volume = 0
                elif x == 13 and tick == y and v.delayed:
                    start(v, v.delayed)
                    v.delayed = 0.0
                    v.heard_period = v.period

    def mix(start_at: int, end_at: int) -> None:
        for c in range(mod.channels):
            v = voices[c]
            if not (0 <= v.playing < len(mod.samples)):
                continue
            s = mod.samples[v.playing]
            if not v.on or v.heard_period <= 0:
                continue
            if v.pos >= v.end and not wrap(v, s):
                continue  # a note started past the end of its sample
            if v.heard_volume <= 0:
                advance(v, s, ((end_at - start_at) * PAULA) / v.heard_period / opts.sample_rate)
                continue
            step = PAULA / v.heard_period / opts.sample_rate
            gain = v.heard_volume / (64 * 128)
            on_left = (c & 3) in (0, 3)
            gl = gain * (near if on_left else far)
            gr = gain * (far if on_left else near)
            loops = s.loop_length > 2
            data = s.data
            for i in range(start_at, end_at):
                ip = int(v.pos)
                a = data[ip] if ip < len(data) else 0
                nxt = (
                    data[ip + 1]
                    if ip + 1 < v.end and ip + 1 < len(data)
                    else (data[s.loop_start] if loops and s.loop_start < len(data) else 0)
                )
                out = a + (nxt - a) * (v.pos - ip)
                left[i] += out * gl
                right[i] += out * gr
                v.pos += step
                if v.pos >= v.end and not wrap(v, s):
                    break

    while done < total and not ended:
        if silent and tick == 0 and order >= from_order:
            silent = False
        if not silent and tick == 0 and len(rows) >= max_rows:
            break
        if tick == 0:
            play_row()
        else:
            play_tick()
        exact = (opts.sample_rate * 2.5) / bpm + carry  # a tick is 2.5 / bpm seconds
        count = int(exact)
        carry = exact - count
        if silent:
            for v in voices:
                if 0 <= v.playing < len(mod.samples):
                    s = mod.samples[v.playing]
                    if v.on and v.heard_period > 0:
                        advance(v, s, (count * PAULA) / v.heard_period / opts.sample_rate)
            skipped += 1
            if skipped > 2_000_000:
                break  # the start position is never reached (the song jumps around it)
        else:
            end = min(total, done + count)
            mix(done, end)
            done = end
        tick += 1
        if tick >= row_ticks:
            tick = 0
            if loop_to >= 0:
                row = loop_to
                loop_to = -1
            elif jump >= 0:
                order = jump
                row = min(ROWS - 1, break_row)
                jump, break_row = -1, 0
            else:
                row += 1
                if row >= ROWS:
                    row = 0
                    order += 1
            if order >= len(mod.orders):
                ended = True
    return ModRender(opts.sample_rate, left[:done], right[:done], rows, ended)


# -- tunes ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TuneMeta:
    id: str
    title: str
    author: str
    license: str
    source: str
    from_order: int
    rows: int | None = None


MOD_ARCHIVE = "https://modarchive.org/module.php?"
PD, BY = "Public Domain", "CC BY 4.0"

#: Every tune the intro can play (assets/tunes/README.md has the licences).
TUNES: tuple[TuneMeta, ...] = (
    TuneMeta("neon-techno", "Neon Techno", "Drozerix", PD, MOD_ARCHIVE + "178172", 1),
    TuneMeta("air-ride", "Air Ride", "Drozerix", PD, MOD_ARCHIVE + "213325", 1),
    TuneMeta("8bit-castle", "8-bit Castle", "JAM", PD, MOD_ARCHIVE + "195782", 2),
    TuneMeta("stardust-jam", "Stardust Jam", "Drozerix", PD, MOD_ARCHIVE + "201039", 1),
    TuneMeta("actually-chiptune", "actually chiptune", "M4kachu", BY, MOD_ARCHIVE + "212567", 2),
    TuneMeta("silicon-dancer", "Silicon Dancer", "Drozerix", PD, MOD_ARCHIVE + "209692", 1),
    TuneMeta("ai-renaissance", "AI Renaissance", "Drozerix", PD, MOD_ARCHIVE + "200847", 8),
    TuneMeta("hawks-claw", "The Hawk's Claw", "Drozerix", PD, MOD_ARCHIVE + "197917", 0, 128),
)

_mods: dict[str, Mod] = {}
_cuts: dict[tuple[str, int], tuple[list[float], list[float], int]] = {}


def load_tune(meta: TuneMeta) -> Mod:
    """The parsed module, read from the bundled base64 snapshot once per process."""
    mod = _mods.get(meta.id)
    if mod is None:
        raw = (Path(__file__).parent / "assets" / "tunes" / f"{meta.id}.mod.b64.txt").read_text()
        mod = parse_mod(base64.b64decode("".join(raw.split())))
        _mods[meta.id] = mod
    return mod


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) >> 1] if ordered else 0.0


def cut(
    meta: TuneMeta, from_order: int | None = None, *, sample_rate: int = RATE
) -> tuple[list[float], list[float], int]:
    """The intro cut starting at a song position, levelled and faded: (left, right, rate)."""
    mod = load_tune(meta)
    at = max(0, min(len(mod.orders) - 1, int(from_order if from_order is not None else meta.from_order)))
    key = (meta.id, at)
    ready = _cuts.get(key)
    if ready is not None and ready[2] == sample_rate:
        return ready
    # how long a row lasts here, and how many rows make a beat (the multiple nearest 125 bpm)
    probe = render_mod(mod, RenderOptions(sample_rate=8000, seconds=20, from_order=at, rows=65, separation=0))
    row = _median([b.time - a.time for a, b in zip(probe.rows, probe.rows[1:], strict=False)]) or 0.12
    per_beat = 4
    for k in (2, 4, 8, 16):
        if abs(math.log((row * k) / 0.48)) < abs(math.log((row * per_beat) / 0.48)):
            per_beat = k
    bars = max(4, round(TARGET_SECONDS / (4 * per_beat * row)))
    cut_rows = meta.rows if (at == meta.from_order and meta.rows) else bars * 4 * per_beat
    rendered = render_mod(mod, RenderOptions(sample_rate=sample_rate, seconds=30, from_order=at, rows=cut_rows))
    left, right = _finish(rendered.left, rendered.right, sample_rate)
    result = (left, right, sample_rate)
    _cuts[key] = result
    return result


def _finish(left: list[float], right: list[float], rate: int) -> tuple[list[float], list[float]]:
    """Normalize to PEAK, fade the ends in and out."""
    peak = max((abs(x) for pair in zip(left, right, strict=True) for x in pair), default=0.0)
    gain = PEAK / peak if peak > 0 else 1.0
    fade_in = int(FADE_IN * rate)
    fade_out = min(len(left), int(FADE_OUT * rate))
    for i in range(len(left)):
        k = gain * min(1.0, (i + 1) / fade_in if fade_in else 1.0, (len(left) - i) / fade_out if fade_out else 1.0)
        left[i] *= k
        right[i] *= k
    return left, right


def pick_tune(name: str | None, rng: Random | None = None) -> tuple[TuneMeta, int] | None:
    """The tune a name asks for: an id ("air-ride"), or an id and a song position
    ("air-ride@3"). No name, or "random", picks any tune at its usual cut; unknown → None."""
    rng = rng or Random()
    if not name or name.strip().lower() == "random":
        meta = TUNES[rng.randrange(len(TUNES))]
        return meta, meta.from_order
    asked, _, at = name.strip().lower().partition("@")
    for meta in TUNES:
        if meta.id == asked:
            return meta, meta.from_order if not at else max(0, int(at) if at.isdigit() else meta.from_order)
    return None


def credit(meta: TuneMeta) -> str:
    """'Neon Techno by Drozerix' — the line shown while a tune plays."""
    return f"{meta.title} by {meta.author}"


# -- playback ------------------------------------------------------------------------------------------------


@dataclass
class Playback:
    """A player process that can only be waited on and stopped."""

    process: subprocess.Popen[bytes]
    path: Path

    def stop(self) -> None:
        self.process.terminate()

    def wait(self, timeout: float | None = None) -> None:
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.terminate()


PLAYERS = (
    ("afplay",),
    ("pw-play",),
    ("paplay",),
    ("aplay", "-q"),
    ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"),
)
_active: list[Playback] = []


def _player_command() -> tuple[str, ...] | None:
    for command in PLAYERS:
        if shutil.which(command[0]):
            return command
    return None


def write_wav(left: Sequence[float], right: Sequence[float], rate: int, path: Path) -> Path:
    """A 16-bit stereo WAV for the system player."""
    frames = bytearray()
    for stereo_l, stereo_r in zip(left, right, strict=True):
        clip = lambda x: max(-32768, min(32767, int(x * 32767)))  # noqa: E731
        frames += struct.pack("<hh", clip(stereo_l), clip(stereo_r))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def play(
    meta: TuneMeta, from_order: int | None = None, *, volume: float = 0.1, env: dict[str, str] | None = None
) -> Playback | None:
    """Render the tune's cut and hand it to the system player; None when there is none, or the
    music is off (INTERN_NO_MUSIC). Volume is INTERN_INTRO_VOLUME, 0..1 (default 0.1)."""

    environment = dict(os.environ if env is None else {**os.environ, **env})
    if environment.get("INTERN_NO_MUSIC"):
        return None
    command = _player_command()
    if command is None:
        return None
    try:
        volume = max(0.0, min(1.0, float(environment.get("INTERN_INTRO_VOLUME", volume))))
    except ValueError:
        volume = 0.1
    if volume <= 0:
        return None
    left, right, rate = cut(meta, from_order)
    # The cut is levelled to PEAK (full scale); the volume is the fraction of that played.
    left = [x * volume for x in left]
    right = [x * volume for x in right]
    handle, name = tempfile.mkstemp(prefix="intern-intro-", suffix=".wav")
    os.close(handle)
    path = write_wav(left, right, rate, Path(name))
    process = subprocess.Popen(
        [*command, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    playback = Playback(process, path)
    _active.append(playback)
    _prune()
    return playback


def _prune() -> None:
    """Forget finished playbacks and their WAVs; never kill one that is still sounding."""
    global _active
    still = []
    for playback in _active:
        if playback.process.poll() is not None:
            playback.path.unlink(missing_ok=True)
        else:
            still.append(playback)
    _active = still


def stop_all() -> None:
    """Stop every playing tune (the app is closing)."""
    for playback in _active:
        playback.stop()
    _prune()


__all__ = [
    "RATE",
    "TUNES",
    "NotAMod",
    "Playback",
    "RenderOptions",
    "TuneMeta",
    "credit",
    "cut",
    "load_tune",
    "parse_mod",
    "pick_tune",
    "play",
    "render_mod",
    "stop_all",
    "write_wav",
]
