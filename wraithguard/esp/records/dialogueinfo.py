"""The ``INFO`` record -- one dialogue response. A port of ``types/dialogueinfo.rs``.

A single response under a topic: who says it (speaker id, race, class, faction,
cell, sex/rank), the response ``NAME`` text, an optional voice-over sound, a
result script, and the *filters* that decide when it applies. Responses form a
linked list within their topic via ``PNAM``/``NNAM``. A journal entry also
carries a quest state. Each filter is a condition (a function, a comparison and a
value); its value is a float or an integer, told apart by the subrecord that
follows it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import (
    DialogueType,
    FilterComparison,
    FilterFunction,
    FilterType,
    Sex,
)
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.io import EspError
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_DATA_SIZE = 12
_FILTER_FIXED = 5  # index + type + function (2) + comparison, before the id
_DIGIT_ZERO = 0x30
#: The three quest-state markers, and the tag each is written as.
_QUEST_FROM_TAG = {b"QSTN": "name", b"QSTF": "finished", b"QSTR": "restart"}
_QUEST_TO_TAG = {"name": b"QSTN", "finished": b"QSTF", "restart": b"QSTR"}


@dataclass
class DialogueData:
    """The ``DATA`` block: response type, disposition, and speaker/player rank/sex."""

    dialogue_type: DialogueType = field(default_factory=DialogueType.default)
    disposition: int = 0
    speaker_rank: int = 0
    speaker_sex: Sex = field(default_factory=Sex.default)
    player_rank: int = 0

    @classmethod
    def load(cls, reader: Reader) -> DialogueData:
        """Read the 12-byte block (a byte of padding at the end)."""
        dialogue_type = DialogueType(reader.u32())
        disposition = reader.i32()
        speaker_rank = reader.i8()
        speaker_sex = Sex(reader.u8())
        player_rank = reader.i8()
        reader.skip(1)  # padding
        return cls(dialogue_type, disposition, speaker_rank, speaker_sex, player_rank)

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.u32(int(self.dialogue_type))
        writer.i32(self.disposition)
        writer.i8(self.speaker_rank)
        writer.u8(int(self.speaker_sex))
        writer.i8(self.player_rank)
        writer.raw(b"\x00")


@dataclass
class Filter:
    """One dialogue condition (an ``SCVR`` block plus its ``FLTV``/``INTV`` value)."""

    index: int = 0
    filter_type: FilterType = field(default_factory=FilterType.default)
    function: FilterFunction = field(default_factory=FilterFunction.default)
    comparison: FilterComparison = field(default_factory=FilterComparison.default)
    id: str = ""
    value: float | int = 0.0

    @classmethod
    def load(cls, reader: Reader) -> Filter:
        """Read the ``SCVR`` block; the index is stored as an ASCII digit."""
        length = reader.u32()
        index_byte = reader.u8()
        if index_byte < _DIGIT_ZERO:
            raise EspError("INFO: invalid filter index")
        filter_type = FilterType(reader.u8())
        function = FilterFunction(reader.u16())
        comparison = FilterComparison(reader.u8())
        filter_id = reader.string_of(length - _FILTER_FIXED)
        return cls(index_byte - _DIGIT_ZERO, filter_type, function, comparison, filter_id)

    def save(self, writer: Writer) -> None:
        """Write the ``SCVR`` block; its own size word covers the id length."""
        data = writer_encode(self.id)
        writer.u32(len(data) + _FILTER_FIXED)
        writer.u8(self.index + _DIGIT_ZERO)
        writer.u8(int(self.filter_type))
        writer.u16(int(self.function))
        writer.u8(int(self.comparison))
        writer.raw(data)


def writer_encode(value: str) -> bytes:
    """Encode a filter id to Windows-1252 bytes (written raw, no length prefix)."""
    try:
        return value.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise EspError(f"unencodable filter id {value!r}: {exc}") from exc


@register
@dataclass
class DialogueInfo(Record):
    """An ``INFO`` record."""

    TAG: ClassVar[bytes] = b"INFO"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    prev_id: str = ""
    next_id: str = ""
    data: DialogueData = field(default_factory=DialogueData)
    speaker_id: str = ""
    speaker_race: str = ""
    speaker_class: str = ""
    speaker_faction: str = ""
    speaker_cell: str = ""
    player_faction: str = ""
    sound_path: str = ""
    text: str = ""
    quest_state: str | None = None
    filters: list[Filter] = field(default_factory=list)
    script_text: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> DialogueInfo:
        """Read the response's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"INAM":
                self.id = reader.string()
            elif tag == b"PNAM":
                self.prev_id = reader.string()
            elif tag == b"NNAM":
                self.next_id = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "INFO", "DATA", _DATA_SIZE)
                self.data = DialogueData.load(reader)
            elif tag == b"ONAM":
                self.speaker_id = reader.string()
            elif tag == b"RNAM":
                self.speaker_race = reader.string()
            elif tag == b"CNAM":
                self.speaker_class = reader.string()
            elif tag == b"FNAM":
                self.speaker_faction = reader.string()
            elif tag == b"ANAM":
                self.speaker_cell = reader.string()
            elif tag == b"DNAM":
                self.player_faction = reader.string()
            elif tag == b"SNAM":
                self.sound_path = reader.string()
            elif tag == b"NAME":
                self.text = reader.string()
            elif tag in _QUEST_FROM_TAG:
                reader.skip(reader.u32())
                self.quest_state = _QUEST_FROM_TAG[tag]
            elif tag == b"SCVR":
                self.filters.append(Filter.load(reader))
            elif tag == b"FLTV":
                expect_size(reader, "INFO", "FLTV", 4)
                self._last_filter().value = reader.f32()
            elif tag == b"INTV":
                expect_size(reader, "INFO", "INTV", 4)
                self._last_filter().value = reader.i32()
            elif tag == b"BNAM":
                self.script_text = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("INFO", tag)
        return self

    def _last_filter(self) -> Filter:
        """The filter a trailing ``FLTV``/``INTV`` value belongs to."""
        if not self.filters:
            raise EspError("INFO: filter value without a preceding SCVR filter")
        return self.filters[-1]

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"INAM", self.id)
        put_string(writer, b"PNAM", self.prev_id)
        put_string(writer, b"NNAM", self.next_id)
        writer.tag(b"DATA")
        writer.u32(_DATA_SIZE)
        self.data.save(writer)
        put_opt_string(writer, b"ONAM", self.speaker_id)
        put_opt_string(writer, b"RNAM", self.speaker_race)
        put_opt_string(writer, b"CNAM", self.speaker_class)
        put_opt_string(writer, b"FNAM", self.speaker_faction)
        put_opt_string(writer, b"ANAM", self.speaker_cell)
        put_opt_string(writer, b"DNAM", self.player_faction)
        put_opt_string(writer, b"SNAM", self.sound_path)
        if self.text:
            writer.tag(b"NAME")
            writer.string_no_terminator(self.text)  # engine 512-char limit
        if self.quest_state is not None:
            writer.tag(_QUEST_TO_TAG[self.quest_state])
            writer.u32(1)
            writer.u8(1)
        for entry in self.filters:
            writer.tag(b"SCVR")
            entry.save(writer)
            if isinstance(entry.value, float):
                writer.tag(b"FLTV")
                writer.u32(4)
                writer.f32(entry.value)
            else:
                writer.tag(b"INTV")
                writer.u32(4)
                writer.i32(entry.value)
        put_opt_string(writer, b"BNAM", self.script_text)
        write_dele(writer, self.flags)
