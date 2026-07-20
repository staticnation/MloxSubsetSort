"""Shared pytest fixtures and helpers for the MLOX Subset Sort test suite.

The project ships as two top-level scripts rather than an installed package,
so the engine is loaded by path here and exposed as the ``core`` fixture.
Nothing in this suite imports tkinter: the GUI module is deliberately out of
scope so the tests run headless in CI.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = REPO_ROOT / "mlox_subset_sort.py"


def _load_engine() -> types.ModuleType:
    """Import mlox_subset_sort.py by path, without needing it on sys.path."""
    spec = importlib.util.spec_from_file_location("mlox_subset_sort", ENGINE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load engine from {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("mlox_subset_sort", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def core() -> types.ModuleType:
    """The engine module under test."""
    return _load_engine()


# --------------------------------------------------------------------------
# Minimal TES3 plugin builders.
#
# Real .esp/.esm files are just a TES3 header record followed by game records,
# each `tag | u32 size | u32 header1 | u32 flags | body`. Building them by hand
# keeps the tests hermetic -- no fixture binaries to check in, and every field
# under test is explicit at the call site.
# --------------------------------------------------------------------------


def sub(tag: str, data: bytes) -> bytes:
    """One subrecord: 4-byte tag + u32 length + payload."""
    return struct.pack("<4sI", tag.encode(), len(data)) + data


def rec(tag: str, body: bytes) -> bytes:
    """One top-level record wrapping ``body``."""
    return struct.pack("<4sIII", tag.encode(), len(body), 0, 0) + body


def zstr(text: str, pad: int | None = None) -> bytes:
    """NUL-terminated latin-1 string, optionally NUL-padded to ``pad`` bytes."""
    raw = text.encode("latin-1") + b"\x00"
    return raw.ljust(pad, b"\x00") if pad else raw


def tes3_header(
    masters: tuple[str, ...] = (),
    sizes: tuple[int, ...] = (),
    author: str = "tester",
    description: str = "fixture",
) -> bytes:
    """A TES3 header record, optionally declaring master files and their sizes."""
    body = sub(
        "HEDR",
        struct.pack("<fi", 1.3, 0)
        + zstr(author, 32)
        + zstr(description, 256)
        + struct.pack("<i", 1),
    )
    for index, master in enumerate(masters):
        body += sub("MAST", zstr(master))
        size = sizes[index] if index < len(sizes) else 0
        body += sub("DATA", struct.pack("<Q", size))
    return rec("TES3", body)


def write_plugin(
    path: Path,
    masters: tuple[str, ...] = (),
    sizes: tuple[int, ...] = (),
    extra: bytes = b"",
    **header_kwargs: str,
) -> Path:
    """Write a minimal but structurally valid plugin to ``path``."""
    path.write_bytes(tes3_header(masters, sizes, **header_kwargs) + extra)
    return path


def static_record(record_id: str, mesh: str = "x.nif") -> bytes:
    """A STAT record -- the simplest thing that counts as real game content."""
    return rec("STAT", sub("NAME", zstr(record_id)) + sub("MODL", zstr(mesh)))


def interior_cell(name: str, fog: float, flags: int = 1) -> bytes:
    """An interior CELL record with an explicit AMBI fog density."""
    return rec(
        "CELL",
        sub("NAME", zstr(name))
        + sub("DATA", struct.pack("<Iif", flags, 0, 0.5))
        + sub("AMBI", struct.pack("<IIIf", 0, 0, 0, fog)),
    )


def pathgrid(cell_name: str, x: int = 0, y: int = 0) -> bytes:
    """A PGRD record for ``cell_name`` (interiors carry grid 0,0)."""
    return rec(
        "PGRD", sub("NAME", zstr(cell_name)) + sub("DATA", struct.pack("<iihBB", x, y, 0, 0, 0))
    )


def script_record(name: str, text: str) -> bytes:
    """A SCPT record whose SCTX body is ``text``."""
    return rec(
        "SCPT", sub("SCHD", zstr(name, 32) + b"\x00" * 20) + sub("SCTX", text.encode("latin-1"))
    )


@pytest.fixture
def make_plugin(tmp_path: Path):
    """Factory writing a plugin into the test's tmp_path."""

    def _make(name: str, **kwargs) -> Path:
        return write_plugin(tmp_path / name, **kwargs)

    return _make


def pytest_addoption(parser) -> None:
    """Register ``--update-baseline`` for the differential guard.

    Lives here because pytest only honours ``pytest_addoption`` from a
    conftest or a plugin, not from a test module.
    """
    parser.addoption(
        "--update-baseline",
        action="store_true",
        default=False,
        help="Rewrite tests/baselines/ instead of asserting against it.",
    )
