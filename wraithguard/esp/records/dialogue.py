"""The ``DIAL`` record -- a dialogue topic. A port of ``types/dialogue.rs``.

The heading a run of ``INFO`` responses belongs to: a topic, a greeting, a voice
line, a persuasion result, or a journal (quest). Just an id and a one-byte type;
the responses that follow it in the file are separate ``INFO`` records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import DialogueType2
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import put_string, read_dele, unexpected, write_dele

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_TYPE_SIZE = 1


@register
@dataclass
class Dialogue(Record):
    """A ``DIAL`` record."""

    TAG: ClassVar[bytes] = b"DIAL"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    dialogue_type: DialogueType2 = field(default_factory=DialogueType2.default)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Dialogue:
        """Read the topic's subrecords.

        A live topic's ``DATA`` is one byte -- the type. A deleted one sometimes
        carries a four-byte ``DATA`` instead, which is skipped, matching the
        crate.
        """
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"DATA":
                size = reader.u32()
                if size == _TYPE_SIZE:
                    self.dialogue_type = DialogueType2(reader.u8())
                else:
                    reader.skip(size)
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("DIAL", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the id and the one-byte type, then a deletion marker if set."""
        put_string(writer, b"NAME", self.id)
        writer.tag(b"DATA")
        writer.u32(_TYPE_SIZE)
        writer.u8(int(self.dialogue_type))
        write_dele(writer, self.flags)
