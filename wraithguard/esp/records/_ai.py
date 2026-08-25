"""AI data and packages, and travel destinations -- shared by NPCs and creatures.

An actor's behaviour is a small ``AIDT`` block (its fight/flee/alarm thresholds
and the services it offers) plus a list of AI *packages*, one per behaviour it
runs: travel to a point, wander an area, escort or follow a target, or activate
something. Escort and follow packages, and travel destinations, may carry a
trailing cell name in a look-ahead sub-block whose bytes fall *outside* the
package's declared size. NPCs and creatures both use all of this, so it lives
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from wraithguard.esp.flags import ServiceFlags
from wraithguard.esp.records._common import expect_size

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_TARGET_SIZE = 32


@dataclass
class AiData:
    """The ``AIDT`` block: greeting distance, fight/flee/alarm, services (12 bytes)."""

    hello: int = 0
    fight: int = 0
    flee: int = 0
    alarm: int = 0
    services: ServiceFlags = field(default_factory=lambda: ServiceFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> AiData:
        """Read the 12-byte block (three bytes of padding before the services)."""
        hello = reader.i16()
        fight = reader.i8()
        flee = reader.i8()
        alarm = reader.i8()
        reader.skip(3)  # padding
        return cls(hello, fight, flee, alarm, ServiceFlags(reader.u32()))

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.i16(self.hello)
        writer.i8(self.fight)
        writer.i8(self.flee)
        writer.i8(self.alarm)
        writer.raw(b"\x00\x00\x00")
        writer.u32(int(self.services))


@dataclass
class AiTravelPackage:
    """``AI_T`` -- travel to a world point (16 bytes)."""

    TAG: ClassVar[bytes] = b"AI_T"
    SIZE: ClassVar[int] = 16

    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    reset: int = 0

    @classmethod
    def load(cls, reader: Reader) -> AiTravelPackage:
        """Read the location and reset flag (three bytes of padding follow)."""
        location = (reader.f32(), reader.f32(), reader.f32())
        reset = reader.u8()
        reader.skip(3)  # padding
        return cls(location, reset)

    def save(self, writer: Writer) -> None:
        """Write the location, reset flag and padding."""
        for value in self.location:
            writer.f32(value)
        writer.u8(self.reset)
        writer.raw(b"\x00\x00\x00")


@dataclass
class AiWanderPackage:
    """``AI_W`` -- wander within a distance, with idle-animation weights (14 bytes)."""

    TAG: ClassVar[bytes] = b"AI_W"
    SIZE: ClassVar[int] = 14

    distance: int = 0
    duration: int = 0
    game_hour: int = 0
    idles: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0, 0)
    reset: int = 0

    @classmethod
    def load(cls, reader: Reader) -> AiWanderPackage:
        """Read the 14-byte block: distances, hour, eight idle weights, reset."""
        distance = reader.u16()
        duration = reader.u16()
        game_hour = reader.u8()
        idles = tuple(reader.u8() for _ in range(8))
        reset = reader.i8()
        return cls(distance, duration, game_hour, idles, reset)

    def save(self, writer: Writer) -> None:
        """Write the 14-byte block in field order."""
        writer.u16(self.distance)
        writer.u16(self.duration)
        writer.u8(self.game_hour)
        for idle in self.idles:
            writer.u8(idle)
        writer.i8(self.reset)


@dataclass
class _TargetPackage:
    """Shared body of escort and follow: a point, a target, and an optional cell."""

    SIZE: ClassVar[int] = 48

    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    duration: int = 0
    target: str = ""
    reset: int = 0
    cell: str = ""

    @classmethod
    def load(cls, reader: Reader) -> _TargetPackage:
        """Read the 48-byte body, then a trailing ``CNDT`` cell if one follows."""
        location = (reader.f32(), reader.f32(), reader.f32())
        duration = reader.u16()
        target = reader.string_of(_TARGET_SIZE)
        reset = reader.u8()
        reader.skip(1)  # padding
        cell = reader.string() if reader.try_tag(b"CNDT") else ""
        return cls(location, duration, target, reset, cell)

    def save(self, writer: Writer) -> None:
        """Write the 48-byte body, then a ``CNDT`` cell when set."""
        for value in self.location:
            writer.f32(value)
        writer.u16(self.duration)
        writer.fixed_string(self.target, _TARGET_SIZE)
        writer.u8(self.reset)
        writer.raw(b"\x00")
        if self.cell:
            writer.tag(b"CNDT")
            writer.string(self.cell)


@dataclass
class AiEscortPackage(_TargetPackage):
    """``AI_E`` -- escort a target to a point."""

    TAG: ClassVar[bytes] = b"AI_E"


@dataclass
class AiFollowPackage(_TargetPackage):
    """``AI_F`` -- follow a target."""

    TAG: ClassVar[bytes] = b"AI_F"


@dataclass
class AiActivatePackage:
    """``AI_A`` -- activate a named target (33 bytes)."""

    TAG: ClassVar[bytes] = b"AI_A"
    SIZE: ClassVar[int] = 33

    target: str = ""
    reset: int = 0

    @classmethod
    def load(cls, reader: Reader) -> AiActivatePackage:
        """Read the fixed target name and reset flag."""
        target = reader.string_of(_TARGET_SIZE)
        return cls(target, reader.u8())

    def save(self, writer: Writer) -> None:
        """Write the fixed target name and reset flag."""
        writer.fixed_string(self.target, _TARGET_SIZE)
        writer.u8(self.reset)


#: An AI package is any one of the five kinds.
AiPackage = (
    AiTravelPackage | AiWanderPackage | AiEscortPackage | AiFollowPackage | AiActivatePackage
)

#: The subrecord tag each package kind is read from (each knows its own size).
_AI_PACKAGES: dict[bytes, Any] = {
    AiTravelPackage.TAG: AiTravelPackage,
    AiWanderPackage.TAG: AiWanderPackage,
    AiEscortPackage.TAG: AiEscortPackage,
    AiFollowPackage.TAG: AiFollowPackage,
    AiActivatePackage.TAG: AiActivatePackage,
}


def is_ai_tag(tag: bytes) -> bool:
    """Whether ``tag`` starts an AI package subrecord."""
    return tag in _AI_PACKAGES


def load_ai_package(reader: Reader, tag: bytes, record_tag: str) -> AiPackage:
    """Read the AI package a known ``tag`` introduces."""
    cls: Any = _AI_PACKAGES[tag]
    expect_size(reader, record_tag, tag.decode("ascii"), cls.SIZE)
    package: AiPackage = cls.load(reader)
    return package


def save_ai_package(writer: Writer, package: AiPackage) -> None:
    """Write an AI package: its tag, declared (fixed) size, then its body."""
    writer.tag(package.TAG)
    writer.u32(package.SIZE)
    package.save(writer)


@dataclass
class TravelDestination:
    """A ``DODT`` travel target: a transform and an optional destination cell."""

    SIZE: ClassVar[int] = 24

    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cell: str = ""

    @classmethod
    def load(cls, reader: Reader) -> TravelDestination:
        """Read the 24-byte transform, then a trailing ``DNAM`` cell if present."""
        translation = (reader.f32(), reader.f32(), reader.f32())
        rotation = (reader.f32(), reader.f32(), reader.f32())
        cell = reader.string() if reader.try_tag(b"DNAM") else ""
        return cls(translation, rotation, cell)

    def save(self, writer: Writer) -> None:
        """Write the transform, then a ``DNAM`` cell when set."""
        for value in self.translation:
            writer.f32(value)
        for value in self.rotation:
            writer.f32(value)
        if self.cell:
            writer.tag(b"DNAM")
            writer.string(self.cell)
