"""The ``INDX``/``BNAM``/``CNAM`` biped-object group -- a port of ``bipedobject.rs``.

An armor or clothing item covers one or more body slots; each slot is this
three-subrecord group: an ``INDX`` naming the slot (head, cuirass, left glove,
...), then up to two body-part meshes, a male one under ``BNAM`` and a female one
under ``CNAM``. Not a record of its own -- it is a repeated group inside armor
and clothing.

The read looks ahead: after the slot index it *tries* ``BNAM`` then ``CNAM``,
backing out of either tag that is not there, because a slot may have one mesh,
the other, both, or neither. That is why the reader has :meth:`~Reader.try_tag`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wraithguard.esp.enums import BipedObjectType
from wraithguard.esp.records._common import expect_size

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_INDX_SIZE = 1
_MESH_SLOTS = 2


@dataclass
class BipedObject:
    """One body slot and its male/female meshes (``INDX`` + optional ``BNAM``/``CNAM``)."""

    biped_object_type: BipedObjectType = field(default_factory=BipedObjectType.default)
    male_bodypart: str = ""
    female_bodypart: str = ""

    @classmethod
    def load(cls, reader: Reader) -> BipedObject:
        """Read the slot index, then look ahead for its optional meshes.

        The ``INDX`` tag has already been consumed by the caller; this reads its
        one-byte payload, then tries ``BNAM``/``CNAM`` up to twice so a slot with
        either order, both, one, or no meshes reads correctly.
        """
        expect_size(reader, "BipedObject", "INDX", _INDX_SIZE)
        self = cls(biped_object_type=BipedObjectType(reader.u8()))
        for _ in range(_MESH_SLOTS):
            if reader.try_tag(b"BNAM"):
                self.male_bodypart = reader.string()
            elif reader.try_tag(b"CNAM"):
                self.female_bodypart = reader.string()
        return self

    def save(self, writer: Writer) -> None:
        """Write ``INDX`` and whichever of ``BNAM``/``CNAM`` are present."""
        writer.tag(b"INDX")
        writer.u32(_INDX_SIZE)
        writer.u8(int(self.biped_object_type))
        if self.male_bodypart:
            writer.tag(b"BNAM")
            writer.string(self.male_bodypart)
        if self.female_bodypart:
            writer.tag(b"CNAM")
            writer.string(self.female_bodypart)
