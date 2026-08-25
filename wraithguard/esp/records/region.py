"""The ``REGN`` record -- a region. A port of ``types/region.rs``.

A named area of the world with its weather odds, its map colour, the creature
that ambushes a sleeper, and the ambient sounds it plays. The weather block comes
in two sizes -- eight bytes (no snow or blizzard, pre-Bloodmoon) or ten -- and is
always written as ten, matching the crate. Each ``SNAM`` sound is a fixed
thirty-two-byte id and a one-byte chance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

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

_CNAM_SIZE = 4
_SNAM_SIZE = 33
_SOUND_ID_SIZE = 32
_WEAT_SHORT = 8
_WEAT_FULL = 10


@dataclass
class WeatherChances:
    """The ``WEAT`` block: the percentage chance of each weather type."""

    clear: int = 0
    cloudy: int = 0
    foggy: int = 0
    overcast: int = 0
    rain: int = 0
    thunder: int = 0
    ash: int = 0
    blight: int = 0
    snow: int = 0
    blizzard: int = 0

    @classmethod
    def load(cls, reader: Reader) -> WeatherChances:
        """Read the block, whose own size word is 8 (no snow/blizzard) or 10."""
        length = reader.u32()
        if length not in (_WEAT_SHORT, _WEAT_FULL):
            raise EspError(f"REGN::WEAT size {length}, expected 8 or 10")
        clear = reader.u8()
        cloudy = reader.u8()
        foggy = reader.u8()
        overcast = reader.u8()
        rain = reader.u8()
        thunder = reader.u8()
        ash = reader.u8()
        blight = reader.u8()
        snow = reader.u8() if length == _WEAT_FULL else 0
        blizzard = reader.u8() if length == _WEAT_FULL else 0
        return cls(clear, cloudy, foggy, overcast, rain, thunder, ash, blight, snow, blizzard)

    def save(self, writer: Writer) -> None:
        """Write the block, always at the full ten-byte size, as the crate does."""
        writer.u32(_WEAT_FULL)
        writer.u8(self.clear)
        writer.u8(self.cloudy)
        writer.u8(self.foggy)
        writer.u8(self.overcast)
        writer.u8(self.rain)
        writer.u8(self.thunder)
        writer.u8(self.ash)
        writer.u8(self.blight)
        writer.u8(self.snow)
        writer.u8(self.blizzard)


@register
@dataclass
class Region(Record):
    """A ``REGN`` record."""

    TAG: ClassVar[bytes] = b"REGN"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    weather_chances: WeatherChances = field(default_factory=WeatherChances)
    sleep_creature: str = ""
    map_color: bytes = b"\x00\x00\x00\x00"
    sounds: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Region:
        """Read the region's subrecords, collecting each ambient sound."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"WEAT":
                self.weather_chances = WeatherChances.load(reader)
            elif tag == b"BNAM":
                self.sleep_creature = reader.string()
            elif tag == b"CNAM":
                expect_size(reader, "REGN", "CNAM", _CNAM_SIZE)
                self.map_color = reader.raw(_CNAM_SIZE)
            elif tag == b"SNAM":
                expect_size(reader, "REGN", "SNAM", _SNAM_SIZE)
                sound_id = reader.string_of(_SOUND_ID_SIZE)
                self.sounds.append((sound_id, reader.u8()))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("REGN", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; each sound a fixed field."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.name)
        writer.tag(b"WEAT")
        self.weather_chances.save(writer)
        put_opt_string(writer, b"BNAM", self.sleep_creature)
        writer.tag(b"CNAM")
        writer.u32(_CNAM_SIZE)
        writer.raw(self.map_color)
        for sound_id, chance in self.sounds:
            writer.tag(b"SNAM")
            writer.u32(_SNAM_SIZE)
            writer.fixed_string(sound_id, _SOUND_ID_SIZE)
            writer.u8(chance)
        write_dele(writer, self.flags)
