"""An object reference inside a cell -- a port of ``types/reference.rs``.

One placed instance of an object: which object (``NAME``), where it sits
(``DATA``'s translation and rotation), and a long list of optional overrides --
its owner, lock and key, trap, soul, remaining charge or health, stack count,
scale, a teleport destination, and more. Not a record of its own; a cell holds
many, each framed by an ``FRMR`` index the cell supplies.

The reference's read ends at its ``DATA`` (a live reference) or ``DELE`` (a
deleted one). A couple of fields are written only under conditions that depend on
whether the reference comes from a master file, so the cell sets ``mast_index``
before writing. Non-finite transforms (which the TES construction set can emit)
are zeroed on read, as the crate does, to keep the data sane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wraithguard.esp.records._ai import TravelDestination
from wraithguard.esp.records._common import expect_size

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_ONE = 1
_SCALE_MIN = 0.5
_SCALE_MAX = 2.0
_SCALE_EPSILON = 1e-6
_DATA_SIZE = 24
_DODT_SIZE = 24


@dataclass
class Reference:
    """A placed object reference. ``mast_index``/``refr_index`` come from the cell."""

    mast_index: int = 0
    refr_index: int = 0
    id: str = ""
    temporary: bool = False
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float | None = None
    moved_cell: tuple[int, int] | None = None
    owner: str | None = None
    owner_global: str | None = None
    owner_faction: str | None = None
    owner_faction_rank: int | None = None
    charge_left: int | None = None
    health_left: int | None = None
    object_count: int | None = None
    destination: TravelDestination | None = None
    lock_level: int | None = None
    key: str | None = None
    trap: str | None = None
    soul: str | None = None
    blocked: int | None = None
    deleted: bool | None = None

    @classmethod
    def load(cls, reader: Reader) -> Reference:
        """Read the reference's subrecords, ending at its ``DATA`` or ``DELE``."""
        self = cls()
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"UNAM":
                expect_size(reader, "REFR", "UNAM", 1)
                self.blocked = reader.u8()
            elif tag == b"XSCL":
                expect_size(reader, "REFR", "XSCL", 4)
                self.scale = reader.f32()
            elif tag == b"ANAM":
                self.owner = reader.string()
            elif tag == b"BNAM":
                self.owner_global = reader.string()
            elif tag == b"CNAM":
                self.owner_faction = reader.string()
            elif tag == b"INDX":
                expect_size(reader, "REFR", "INDX", 4)
                self.owner_faction_rank = reader.u32()
            elif tag == b"XSOL":
                self.soul = reader.string()
            elif tag == b"XCHG":
                expect_size(reader, "REFR", "XCHG", 4)
                self.charge_left = reader.u32()
            elif tag == b"INTV":
                expect_size(reader, "REFR", "INTV", 4)
                self.health_left = reader.i32()
            elif tag == b"NAM9":
                expect_size(reader, "REFR", "NAM9", 4)
                self.object_count = reader.u32()
            elif tag == b"DODT":
                expect_size(reader, "REFR", "DODT", _DODT_SIZE)
                self.destination = TravelDestination.load(reader)
            elif tag == b"FLTV":
                expect_size(reader, "REFR", "FLTV", 4)
                self.lock_level = reader.i32()
            elif tag == b"KNAM":
                self.key = reader.string()
            elif tag == b"TNAM":
                self.trap = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "REFR", "DATA", _DATA_SIZE)
                self.translation = (reader.f32(), reader.f32(), reader.f32())
                self.rotation = (reader.f32(), reader.f32(), reader.f32())
                break
            elif tag == b"DELE":
                reader.skip(reader.u32())
                self.deleted = True
                break
            else:
                from wraithguard.esp.io import EspError

                raise EspError(f"Unexpected Tag: REFR::{tag!r}")
        self._make_transforms_finite()
        return self

    def _make_transforms_finite(self) -> None:
        """Zero non-finite transform values and drop a non-finite scale, as the crate."""
        self.translation = tuple(v if math.isfinite(v) else 0.0 for v in self.translation)  # type: ignore[assignment]
        self.rotation = tuple(v if math.isfinite(v) else 0.0 for v in self.rotation)  # type: ignore[assignment]
        if self.scale is not None and not math.isfinite(self.scale):
            self.scale = None

    @property
    def persistent(self) -> bool:
        """Whether the reference is persistent (moved, a teleport door, or flagged)."""
        return self.moved_cell is not None or self.destination is not None or not self.temporary

    def save(self, writer: Writer) -> None:
        """Write the reference's subrecords, ending at its ``DATA`` or ``DELE``."""
        writer.tag(b"NAME")
        writer.string(self.id)
        if self.blocked is not None:
            writer.tag(b"UNAM")
            writer.u32(1)
            writer.u8(self.blocked)
        self._save_scale(writer)
        _put_opt(writer, b"ANAM", self.owner)
        _put_opt(writer, b"BNAM", self.owner_global)
        _put_opt(writer, b"CNAM", self.owner_faction)
        _put_opt_u32(writer, b"INDX", self.owner_faction_rank)
        _put_opt(writer, b"XSOL", self.soul)
        _put_opt_u32(writer, b"XCHG", self.charge_left)
        _put_opt_i32(writer, b"INTV", self.health_left)
        self._save_object_count(writer)
        if self.destination is not None:
            writer.tag(b"DODT")
            writer.u32(_DODT_SIZE)
            self.destination.save(writer)
        _put_opt_i32(writer, b"FLTV", self.lock_level)
        _put_opt(writer, b"KNAM", self.key)
        _put_opt(writer, b"TNAM", self.trap)
        if self.deleted is not None:
            writer.tag(b"DELE")
            writer.u32(4)
            writer.u32(0)
        else:
            writer.tag(b"DATA")
            writer.u32(_DATA_SIZE)
            for value in (*self.translation, *self.rotation):
                writer.f32(value)

    def _save_scale(self, writer: Writer) -> None:
        """Write ``XSCL`` for a non-default (or master-file) scale, clamped."""
        if self.scale is None:
            return
        scale = min(max(self.scale, _SCALE_MIN), _SCALE_MAX)
        if abs(scale - 1.0) >= _SCALE_EPSILON or self.mast_index != 0:
            writer.tag(b"XSCL")
            writer.u32(4)
            writer.f32(scale)

    def _save_object_count(self, writer: Writer) -> None:
        """Write ``NAM9`` for a non-default (or master-file) stack count."""
        if self.object_count is None:
            return
        if self.object_count != _ONE or self.mast_index != 0:
            writer.tag(b"NAM9")
            writer.u32(4)
            writer.u32(self.object_count)


def _put_opt(writer: Writer, tag: bytes, value: str | None) -> None:
    """Write a string subrecord when the optional value is present."""
    if value is not None:
        writer.tag(tag)
        writer.string(value)


def _put_opt_u32(writer: Writer, tag: bytes, value: int | None) -> None:
    """Write a size-4 u32 subrecord when present."""
    if value is not None:
        writer.tag(tag)
        writer.u32(4)
        writer.u32(value)


def _put_opt_i32(writer: Writer, tag: bytes, value: int | None) -> None:
    """Write a size-4 i32 subrecord when present."""
    if value is not None:
        writer.tag(tag)
        writer.u32(4)
        writer.i32(value)
