"""zstd backend selection and the never-raise backstops in tes3conv rendering.

``_decompress_zstd`` prefers the Python 3.14 standard-library ``compression.zstd``
and falls back to the third-party binding; both arms are emulated here so a
3.10 CI run still exercises the stdlib path. The two field renderers must also
turn any unexpected decoder error into a comment line rather than raise.
"""

from __future__ import annotations

import sys
import types

import pytest

from wraithguard.mwscript import tes3conv
from wraithguard.mwscript.tes3conv import (
    BytecodeDecodeError,
    _decompress_zstd,
    listing_for_bytecode_field,
    variables_text_for_field,
)


def _install_stdlib_zstd(monkeypatch, *, result: bytes) -> None:
    """Emulate the Python 3.14 ``compression.zstd`` standard-library module."""
    compression = types.ModuleType("compression")
    zstd = types.ModuleType("compression.zstd")

    class ZstdError(Exception):
        pass

    zstd.ZstdError = ZstdError  # type: ignore[attr-defined]
    zstd.decompress = lambda _frame: result  # type: ignore[attr-defined]
    compression.zstd = zstd  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "compression", compression)
    monkeypatch.setitem(sys.modules, "compression.zstd", zstd)


def test_the_stdlib_backend_is_used_when_present(monkeypatch) -> None:
    """With ``compression.zstd`` available, it decompresses without the binding."""
    _install_stdlib_zstd(monkeypatch, result=b"SCDT payload")
    assert _decompress_zstd(b"\x28\xb5\x2f\xfd frame") == b"SCDT payload"


def test_a_stdlib_frame_that_yields_nothing_is_reported(monkeypatch) -> None:
    """An empty decompression is corrupt/truncated, not a blank script."""
    _install_stdlib_zstd(monkeypatch, result=b"")
    with pytest.raises(BytecodeDecodeError, match="decompressed to nothing"):
        _decompress_zstd(b"\x28\xb5\x2f\xfd frame")


def test_the_third_party_binding_is_used_when_the_stdlib_is_absent(monkeypatch) -> None:
    """Forcing the stdlib import to fail exercises the ``zstandard`` fallback.

    On Python 3.14 ``compression.zstd`` is always importable, so this is the
    only way to cover the pre-3.14 binding path there.
    """
    zstandard = pytest.importorskip("zstandard")
    monkeypatch.setitem(sys.modules, "compression", None)
    frame = zstandard.ZstdCompressor().compress(b"a real SCDT payload " * 4)
    assert _decompress_zstd(frame) == b"a real SCDT payload " * 4


def test_a_corrupt_frame_is_reported_not_swallowed() -> None:
    """A frame the backend rejects surfaces as a clear corruption error."""
    zstandard = pytest.importorskip("zstandard")
    good = zstandard.ZstdCompressor().compress(b"a real SCDT payload " * 4)
    corrupt = good[:-3] + b"\x00\x00\x00"  # valid header, damaged tail
    with pytest.raises(BytecodeDecodeError, match="corrupt zstd frame"):
        _decompress_zstd(corrupt)


def test_variables_field_contains_an_unexpected_error(monkeypatch) -> None:
    """A non-decode error from the variables decoder becomes a comment line."""

    def explode(_value: object) -> list[str]:
        raise ValueError("unforeseen")

    monkeypatch.setattr(tes3conv, "decode_variables_field", explode)
    out = variables_text_for_field("anything")
    assert out.startswith("; unexpected error decoding this variables field")
    assert "ValueError" in out


def test_bytecode_listing_contains_an_unexpected_error(monkeypatch) -> None:
    """A non-decode error from the disassembler becomes a comment line."""
    monkeypatch.setattr(tes3conv, "decode_bytecode_field", lambda _v: b"\x00\x00")

    def explode(*_args: object, **_kwargs: object):
        raise ValueError("unforeseen")

    monkeypatch.setattr(tes3conv, "disassemble", explode)
    out = listing_for_bytecode_field("anything")
    assert out.startswith("; unexpected error disassembling this bytecode")
    assert "ValueError" in out
