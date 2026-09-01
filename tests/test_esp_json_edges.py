"""Edge and error branches of the tes3conv-JSON (de)serialiser.

The round-trip happy paths are covered across the ``test_esp_*`` record files;
this pins the guards and format branches those never happen to hit -- the zstd
availability fallbacks, the flag-string corners, and the record-level refusals.
"""

from __future__ import annotations

import enum
import sys

import pytest

from wraithguard.esp.json import (
    EspJsonError,
    _bytes_from_json,
    _compress,
    _decompress,
    _flags_from_str,
    _flags_to_str,
    _from_json_value,
    _to_json_value,
    _zstd_available,
    record_from_json,
)


class _Flags(enum.IntFlag):
    A = 0x1
    B = 0x2


def test_zstd_available_is_false_without_the_extra(monkeypatch) -> None:
    """When ``zstandard`` cannot import, the probe reports it absent."""
    monkeypatch.setitem(sys.modules, "zstandard", None)
    assert _zstd_available() is False


def test_compress_passes_raw_through_without_zstd(monkeypatch) -> None:
    """With no backend, compression is a no-op rather than an error."""
    monkeypatch.setattr("wraithguard.esp.json._zstd_available", lambda: False)
    assert _compress(b"hello") == b"hello"


def test_decompress_of_non_zstd_bytes_returns_them(monkeypatch) -> None:
    """Bytes that are not a zstd frame are already raw and pass through."""
    monkeypatch.setattr("wraithguard.esp.json._zstd_available", lambda: False)
    assert _decompress(b"plain bytes, no magic") == b"plain bytes, no magic"


def test_decompress_of_a_zstd_frame_without_the_extra_raises(monkeypatch) -> None:
    """A zstd-magic blob with no backend to read it is an explicit error."""
    monkeypatch.setattr("wraithguard.esp.json._zstd_available", lambda: False)
    with pytest.raises(EspJsonError, match="zstandard extra"):
        _decompress(b"\x28\xb5\x2f\xfd" + b"\x00" * 8)


def test_flags_to_str_keeps_unknown_bits_as_hex() -> None:
    """A bit no enum member names is preserved as ``0x`` rather than dropped."""
    text = _flags_to_str(_Flags(0x1 | 0x8))
    assert "A" in text
    assert "0x8" in text


def test_flags_round_trip_including_unknown_bits() -> None:
    """The hex form parses back to the same value, unknown bit intact."""
    original = _Flags.A | _Flags(0x8)
    assert int(_flags_from_str(_Flags, _flags_to_str(original))) == int(original)


def test_to_json_value_refuses_an_unserialisable_type() -> None:
    """A value with no JSON rule is reported, not silently mangled."""
    with pytest.raises(EspJsonError, match="no JSON rule"):
        _to_json_value(object())


def test_record_from_json_needs_a_type_tag() -> None:
    """A record object with no string ``type`` cannot be dispatched."""
    with pytest.raises(EspJsonError, match="no string 'type'"):
        record_from_json({"id": "x"})


def test_record_from_json_rejects_an_unknown_type() -> None:
    """A ``type`` naming no known record class is refused."""
    with pytest.raises(EspJsonError, match="unknown record type"):
        record_from_json({"type": "NotARealRecord"})


class TestFromJsonValueShapes:
    """``_from_json_value`` reconstructs each annotated field shape."""

    def test_optional_none_stays_none(self) -> None:
        """An ``X | None`` field with a null value stays None."""
        assert _from_json_value(None, int | None) is None

    def test_optional_present_unwraps_the_inner_type(self) -> None:
        """An ``X | None`` field with a value reconstructs the inner type."""
        assert _from_json_value(5, int | None) == 5

    def test_a_list_field_maps_over_its_element_type(self) -> None:
        """A ``list[int]`` reconstructs element by element."""
        assert _from_json_value([1, 2, 3], list[int]) == [1, 2, 3]

    def test_a_variadic_tuple_field(self) -> None:
        """A ``tuple[int, ...]`` reconstructs to a tuple of that element type."""
        assert _from_json_value([1, 2], tuple[int, ...]) == (1, 2)

    def test_a_fixed_tuple_field(self) -> None:
        """A ``tuple[int, str]`` pairs each element with its own type."""
        assert _from_json_value([1, "x"], tuple[int, str]) == (1, "x")

    def test_bytes_from_a_numeric_array(self) -> None:
        """A fixed byte field stored as a numeric array rebuilds to bytes."""
        assert _bytes_from_json([1, 2, 3]) == b"\x01\x02\x03"

    def test_bytes_from_a_wrapped_blob(self) -> None:
        """A ``{"data": <base64>}`` blob object rebuilds to its raw bytes."""
        import base64

        blob = {"data": base64.b64encode(b"payload").decode()}
        assert _bytes_from_json(blob) == b"payload"

    def test_bytes_from_a_bare_base64_string(self) -> None:
        """A bare base64 blob string rebuilds via the zstd decode path."""
        from wraithguard.esp.json import _b64zstd

        assert _bytes_from_json(_b64zstd(b"payload")) == b"payload"

    def test_an_unannotated_value_passes_through_unchanged(self) -> None:
        """A field typed ``Any`` has no reconstruction rule and is kept as-is."""
        import typing

        assert _from_json_value("kept", typing.Any) == "kept"


class TestStructFromJsonAbsentSpecialFields:
    """Each record with a special-cased field must also parse when it's absent."""

    @pytest.mark.parametrize(
        "obj",
        [
            {"type": "Landscape", "grid": [0, 0]},  # no vertex_heights
            {"type": "GlobalVariable", "id": "g"},  # no value/global_type
            {"type": "GameSetting", "id": "s"},  # no value
            {"type": "DialogueInfo", "id": "i"},  # no quest_state
        ],
    )
    def test_a_record_missing_its_special_field_still_parses(self, obj: dict) -> None:
        """The absent-field branch defaults rather than raising."""
        record = record_from_json(obj)
        assert type(record).__name__ == obj["type"]
