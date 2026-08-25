"""Sound, sound generator, start script, and the typed global variable.

The global is the interesting one: its value is always stored as an ``f32`` but
typed by a one-byte ``FNAM``, so a long or short reads through a truncation and a
``NaN`` reads as zero -- exactly as the engine and the crate do. The others are
straightforward, but their unusual tag mappings (the start script's id under
``DATA``) are pinned so they cannot silently swap.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    EspError,
    GlobalVariable,
    Sound,
    SoundGen,
    StartScript,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import GlobalType, SoundGenType
from wraithguard.esp.flags import ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def _string_sub(tag: bytes, text: str) -> bytes:
    return _sub(tag, text.encode("cp1252") + b"\x00")


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


class TestSound:
    def test_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "drip")
            + _string_sub(b"FNAM", "fx\\drip.wav")
            + _sub(b"DATA", struct.pack("<BBB", 200, 20, 80))
        )
        original = _record(b"SOUN", body)
        (sound,) = read_plugin(original)
        assert isinstance(sound, Sound)
        assert sound.sound_path == "fx\\drip.wav"
        assert sound.data.volume == 200
        assert sound.data.range == (20, 80)
        assert write_plugin([sound]) == original

    def test_unexpected_tag_refused_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: SOUN"):
            read_plugin(
                _record(b"SOUN", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = _string_sub(b"NAME", "gone") + _sub(b"DATA", b"\x00\x00\x00")
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"SOUN", body, flags=int(ObjectFlags.DELETED))
        (sound,) = read_plugin(original)
        assert ObjectFlags.DELETED in sound.flags
        assert write_plugin([sound]) == original


class TestSoundGen:
    def test_round_trips_with_creature_and_sound(self) -> None:
        body = (
            _string_sub(b"NAME", "moan_gen")
            + _sub(b"DATA", struct.pack("<I", int(SoundGenType.Moan)))
            + _string_sub(b"CNAM", "rat")
            + _string_sub(b"SNAM", "rat_moan")
        )
        original = _record(b"SNDG", body)
        (sndg,) = read_plugin(original)
        assert isinstance(sndg, SoundGen)
        assert sndg.sound_gen_type is SoundGenType.Moan
        assert sndg.creature == "rat"
        assert sndg.sound == "rat_moan"
        assert write_plugin([sndg]) == original

    def test_unexpected_tag_refused(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: SNDG"):
            read_plugin(
                _record(b"SNDG", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )

    def test_deleted_round_trips(self) -> None:
        body = _string_sub(b"NAME", "gone") + _sub(b"DATA", struct.pack("<I", 0))
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"SNDG", body, flags=int(ObjectFlags.DELETED))
        (sndg,) = read_plugin(original)
        assert ObjectFlags.DELETED in sndg.flags
        assert write_plugin([sndg]) == original


class TestStartScript:
    def test_id_under_data_and_script_under_name(self) -> None:
        body = _string_sub(b"DATA", "myStartScript") + _string_sub(b"NAME", "someScript")
        original = _record(b"SSCR", body)
        (sscr,) = read_plugin(original)
        assert isinstance(sscr, StartScript)
        assert sscr.id == "myStartScript"
        assert sscr.script == "someScript"
        assert write_plugin([sscr]) == original

    def test_unexpected_tag_refused(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: SSCR"):
            read_plugin(
                _record(b"SSCR", _string_sub(b"DATA", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )

    def test_deleted_round_trips(self) -> None:
        body = _string_sub(b"DATA", "gone") + b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"SSCR", body, flags=int(ObjectFlags.DELETED))
        (sscr,) = read_plugin(original)
        assert ObjectFlags.DELETED in sscr.flags
        assert write_plugin([sscr]) == original


def _global(name: str, gtype: GlobalType, raw: float) -> bytes:
    body = (
        _string_sub(b"NAME", name)
        + _sub(b"FNAM", struct.pack("<B", int(gtype)))
        + _sub(b"FLTV", struct.pack("<f", raw))
    )
    return _record(b"GLOB", body)


class TestGlobalVariable:
    def test_float_global_round_trips(self) -> None:
        original = _global("timescale", GlobalType.Float, 30.0)
        (glob,) = read_plugin(original)
        assert isinstance(glob, GlobalVariable)
        assert glob.global_type is GlobalType.Float
        assert glob.value == pytest.approx(30.0)
        assert write_plugin([glob]) == original

    def test_long_global_reads_as_an_int(self) -> None:
        original = _global("dayspassed", GlobalType.Long, 12.0)
        (glob,) = read_plugin(original)
        assert glob.global_type is GlobalType.Long
        assert glob.value == 12
        assert isinstance(glob.value, int)
        assert write_plugin([glob]) == original

    def test_short_global_round_trips(self) -> None:
        original = _global("pcsex", GlobalType.Short, 1.0)
        (glob,) = read_plugin(original)
        assert glob.value == 1
        assert write_plugin([glob]) == original

    def test_long_saturates_out_of_range_values(self) -> None:
        # As the crate's saturating cast does: beyond i32 clamps to its bounds.
        (high,) = read_plugin(_global("big", GlobalType.Long, 1e20))
        assert high.value == 2**31 - 1
        (low,) = read_plugin(_global("small", GlobalType.Long, -1e20))
        assert low.value == -(2**31)

    def test_short_truncates_toward_zero(self) -> None:
        (glob,) = read_plugin(_global("s", GlobalType.Short, 3.9))
        assert glob.value == 3

    def test_nan_reads_as_zero(self) -> None:
        # Morrowind.esm ships a NaN global (ratskilled); it must read as 0.
        (glob,) = read_plugin(_global("ratskilled", GlobalType.Long, float("nan")))
        assert glob.value == 0

    def test_unexpected_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: GLOB"):
            read_plugin(_record(b"GLOB", body))

    def test_missing_fltv_is_refused(self) -> None:
        body = _string_sub(b"NAME", "bad") + _sub(b"FNAM", struct.pack("<B", int(GlobalType.Float)))
        body += b"DELE" + struct.pack("<II", 4, 0)  # something other than FLTV
        with pytest.raises(EspError, match="expected FLTV"):
            read_plugin(_record(b"GLOB", body))

    def test_deleted_global_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"FNAM", struct.pack("<B", int(GlobalType.Float)))
            + _sub(b"FLTV", struct.pack("<f", 0.0))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"GLOB", body, flags=int(ObjectFlags.DELETED))
        (glob,) = read_plugin(original)
        assert ObjectFlags.DELETED in glob.flags
        assert write_plugin([glob]) == original
