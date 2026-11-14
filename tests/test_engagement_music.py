"""The intro music: the bundled tracker modules parse, the ProTracker renderer plays them, the cut
is levelled and faded, the WAV is well-formed, and playback is silent when told to be (or when the
machine has no player). No audio is ever produced by these tests."""

from __future__ import annotations

import base64
import struct
import wave
from pathlib import Path

import pytest

from intern.engagement import music


def _mod_bytes(*, volume: int = 64, loop_words: int = 0, note_rows: tuple[int, ...] = (0,)) -> bytes:
    """A minimal 4-channel module: sample 1 is a square wave; the given rows start a note on channel 0."""
    data = bytearray(1084)
    data[0:4] = b"test"
    header = 20  # sample 1's 30-byte header starts after the title
    sample = struct.pack("<64b", *[(1 if i % 2 else -1) * 100 for i in range(64)])
    struct.pack_into(">H", data, header + 22, 32)  # length in words: 64 bytes
    data[header + 24] = 0  # finetune
    data[header + 25] = volume
    struct.pack_into(">H", data, header + 26, 0)  # loop start: the whole sample loops
    struct.pack_into(">H", data, header + 28, loop_words)
    data[950] = 1  # song length
    data[952] = 0  # order 0 plays pattern 0
    data[1080:1084] = b"M.K."
    pattern = bytearray(64 * 4 * 4)
    for row in note_rows:
        at = row * 16
        pattern[at] = 0x01  # low nibble of the period
        pattern[at + 1] = 0xAC  # period 0x1AC = 428 (C-3)
        pattern[at + 2] = 0x10  # sample 1 in the high nibble; effect 0
    return bytes(data + pattern + sample)


# -- parsing --------------------------------------------------------------------------------------------------


def test_bundled_tunes_all_parse_and_are_complete() -> None:
    assert len(music.TUNES) == 8
    for meta in music.TUNES:
        raw = (Path(music.__file__).parent / "assets" / "tunes" / f"{meta.id}.mod.b64.txt").read_text()
        mod = music.parse_mod(base64.b64decode("".join(raw.split())))
        assert mod.channels >= 4, meta.id
        assert mod.orders and mod.patterns, meta.id
        assert sum(1 for s in mod.samples if s.length) >= 1, meta.id


def test_a_bundled_tune_keeps_its_identity() -> None:
    meta = music.TUNES[0]
    mod = music.load_tune(meta)
    assert mod.title == "Neon Techno"
    assert mod.channels == 4
    assert len(mod.orders) == 31


def test_parse_rejects_non_modules() -> None:
    with pytest.raises(music.NotAMod, match="too short"):
        music.parse_mod(b"x" * 100)
    bad = bytearray(1084)
    bad[1080:1084] = b"????"
    with pytest.raises(music.NotAMod, match="format tag"):
        music.parse_mod(bytes(bad))


# -- rendering ------------------------------------------------------------------------------------------------


def test_a_note_renders_audio_and_an_empty_slot_is_silent() -> None:
    loud = music.render_mod(
        music.parse_mod(_mod_bytes(note_rows=(0,))), music.RenderOptions(sample_rate=8000, seconds=1.0)
    )
    silent = music.render_mod(
        music.parse_mod(_mod_bytes(note_rows=())), music.RenderOptions(sample_rate=8000, seconds=1.0)
    )
    assert max(abs(x) for x in loud.left) > 0.01
    assert max((abs(x) for x in silent.left), default=0.0) == 0.0


def test_an_unlooped_sample_stops_after_one_pass() -> None:
    rendered = music.render_mod(
        music.parse_mod(_mod_bytes(loop_words=0)), music.RenderOptions(sample_rate=8000, seconds=2.0)
    )
    early = max(abs(x) for x in rendered.left[:200])
    late = max(abs(x) for x in rendered.left[8000:])  # after the 64-byte sample has played out
    assert early > 0.01 and late == 0.0


def test_a_looped_sample_keeps_sounding() -> None:
    rendered = music.render_mod(
        music.parse_mod(_mod_bytes(loop_words=32)), music.RenderOptions(sample_rate=8000, seconds=2.0)
    )
    late = max(abs(x) for x in rendered.left[8000:])
    assert late > 0.01, "a looped sample keeps repeating its loop"


def test_zero_volume_is_silent() -> None:
    rendered = music.render_mod(
        music.parse_mod(_mod_bytes(volume=0)), music.RenderOptions(sample_rate=8000, seconds=1.0)
    )
    assert max((abs(x) for x in rendered.left), default=0.0) == 0.0


def test_panning_makes_channel_zero_nearer_the_left() -> None:
    rendered = music.render_mod(
        music.parse_mod(_mod_bytes()), music.RenderOptions(sample_rate=8000, seconds=1.0, separation=1.0)
    )
    left = max(abs(x) for x in rendered.left)
    right = max(abs(x) for x in rendered.right)
    assert left > right, "channel 0 plays on the left pair"


# -- cuts -----------------------------------------------------------------------------------------------------


def test_a_cut_is_about_the_target_length_leveled_and_faded() -> None:
    meta = music.TUNES[0]
    left, right, rate = music.cut(meta, sample_rate=8000)
    seconds = len(left) / rate
    assert 4.0 < seconds < 20.0, "a whole number of bars near the target"
    assert max(abs(x) for x in left) <= music.PEAK + 1e-9
    assert max(abs(x) for x in left[-10:]) < 0.01, "the very end is faded to silence"
    assert music.cut(meta, sample_rate=8000) == (left, right, rate), "a cut is built once and reused"


# -- wav ------------------------------------------------------------------------------------------------------


def test_wav_is_16_bit_stereo_and_clips(tmp_path: Path) -> None:
    path = music.write_wav([0.5, 2.0, -2.0], [0.0, -0.5, 0.25], 8000, tmp_path / "cut.wav")
    with wave.open(str(path)) as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (2, 2, 8000)
        assert handle.getnframes() == 3
        frames = handle.readframes(3)
    loud, _right = struct.unpack("<hh", frames[4:8])  # the second frame: (2.0, -0.5)
    assert loud == 32767, "values over full scale clip instead of wrapping"
    low, _right = struct.unpack("<hh", frames[8:12])  # the third frame: (-2.0, 0.25)
    assert low == -32768


# -- selection and playback -----------------------------------------------------------------------------------


def test_pick_tune_understands_ids_and_positions() -> None:
    first = music.pick_tune("air-ride")
    assert first is not None
    meta, at = first
    assert meta.id == "air-ride" and at == meta.from_order
    positioned = music.pick_tune("air-ride@3")
    assert positioned is not None and (positioned[0].id, positioned[1]) == ("air-ride", 3)
    assert music.pick_tune("no-such-tune") is None
    for _ in range(3):
        picked = music.pick_tune(None)
        assert picked is not None and picked[0].id in {t.id for t in music.TUNES}
    assert music.credit(music.TUNES[0]) == "Neon Techno by Drozerix"


def test_play_is_silent_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(music, "_player_command", lambda: ("true",))
    assert music.play(music.TUNES[0], env={"INTERN_NO_MUSIC": "1"}) is None


def test_play_is_silent_without_a_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(music, "_player_command", lambda: None)
    assert music.play(music.TUNES[0], env={}) is None


def test_play_starts_the_player_and_stop_all_ends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(music, "_player_command", lambda: ("sh", "-c", "sleep 30"))
    playback = music.play(music.TUNES[0], env={"INTERN_INTRO_VOLUME": "0"})
    assert playback is None, "volume 0 is silence"

    playback = music.play(music.TUNES[0], env={"INTERN_INTRO_VOLUME": "0.1"})
    assert playback is not None and playback.process.poll() is None
    assert playback.path.exists() and playback.path.suffix == ".wav"
    with wave.open(str(playback.path)) as handle:
        assert handle.getframerate() == music.RATE
    music.stop_all()
    playback.process.wait(timeout=5)
    assert playback.process.poll() is not None


def test_intro_volume_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(music, "_player_command", lambda: ("sh", "-c", "sleep 30"))
    assert music.play(music.TUNES[0], env={"INTERN_INTRO_VOLUME": "-1"}) is None, "negative volume is silence"
    for asked in ("nonsense", "5"):  # the default, and full scale
        playback = music.play(music.TUNES[0], env={"INTERN_INTRO_VOLUME": asked})
        assert playback is not None, asked
        music.stop_all()
