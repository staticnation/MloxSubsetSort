"""TES3 bitflags by name -- GENERATED from the tes3 crate, do not edit.

See ``tools/gen_esp_types.py``. Each is an ``IntFlag``, which keeps a bit no named
flag covers through a read and write untouched -- the crate's ``from_bits_retain``
-- on every supported Python. ``WIDTH`` is the backing integer's byte width.
"""

from __future__ import annotations

import enum
from typing import Final


class AlchemyFlags(enum.IntFlag):
    AUTO_CALCULATE = 0x1


class BodypartFlags(enum.IntFlag):
    FEMALE = 0x1
    NOT_PLAYABLE = 0x2


class CellFlags(enum.IntFlag):
    IS_INTERIOR = 0x1
    HAS_WATER = 0x2
    RESTING_IS_ILLEGAL = 0x4
    BEHAVES_LIKE_EXTERIOR = 0x80


class ClassFlags(enum.IntFlag):
    PLAYABLE = 0x1


class ContainerFlags(enum.IntFlag):
    ORGANIC = 0x1
    RESPAWNS = 0x2
    IS_BASE = 0x8


class CreatureFlags(enum.IntFlag):
    BIPED = 0x1
    RESPAWN = 0x2
    WEAPON_AND_SHIELD = 0x4
    IS_BASE = 0x8
    SWIMS = 0x10
    FLIES = 0x20
    WALKS = 0x40
    ESSENTIAL = 0x80


class EnchantingFlags(enum.IntFlag):
    AUTO_CALCULATE = 0x1


class FactionFlags(enum.IntFlag):
    HIDDEN_FROM_PC = 0x1


class LandscapeFlags(enum.IntFlag):
    USES_VERTEX_HEIGHTS_AND_NORMALS = 0x1
    USES_VERTEX_COLORS = 0x2
    USES_TEXTURES = 0x4


class LeveledCreatureFlags(enum.IntFlag):
    CALCULATE_FROM_ALL_LEVELS = 0x1


class LeveledItemFlags(enum.IntFlag):
    CALCULATE_FOR_EACH_ITEM = 0x1
    CALCULATE_FROM_ALL_LEVELS = 0x2


class LightFlags(enum.IntFlag):
    DYNAMIC = 0x1
    CAN_CARRY = 0x2
    NEGATIVE = 0x4
    FLICKER = 0x8
    FIRE = 0x10
    OFF_BY_DEFAULT = 0x20
    FLICKER_SLOW = 0x40
    PULSE = 0x80
    PULSE_SLOW = 0x100


class MagicEffectFlags(enum.IntFlag):
    TARGET_SKILL = 0x1
    TARGET_ATTRIBUTE = 0x2
    NO_DURATION = 0x4
    NO_MAGNITUDE = 0x8
    HARMFUL = 0x10
    CONTINUOUS_VFX = 0x20
    CAN_CAST_SELF = 0x40
    CAN_CAST_TOUCH = 0x80
    CAN_CAST_TARGET = 0x100
    ALLOW_SPELLMAKING = 0x200
    ALLOW_ENCHANTING = 0x400
    NEGATIVE_LIGHTING = 0x800
    APPLIED_ONCE = 0x1000
    UNKNOWN_CHAMELEON = 0x2000
    NON_RECASTABLE = 0x4000
    ILLEGAL_DAEDRA = 0x8000
    UNREFLECTABLE = 0x10000
    CASTER_LINKED = 0x20000


class MiscItemFlags(enum.IntFlag):
    KEY = 0x1


class NpcFlags(enum.IntFlag):
    FEMALE = 0x1
    ESSENTIAL = 0x2
    RESPAWN = 0x4
    IS_BASE = 0x8
    AUTO_CALCULATE = 0x10


class ObjectFlags(enum.IntFlag):
    MODIFIED = 0x2
    DELETED = 0x20
    PERSISTENT = 0x400
    IGNORED = 0x1000
    BLOCKED = 0x2000


class RaceFlags(enum.IntFlag):
    PLAYABLE = 0x1
    BEAST_RACE = 0x2


class ServiceFlags(enum.IntFlag):
    BARTERS_WEAPONS = 0x1
    BARTERS_ARMOR = 0x2
    BARTERS_CLOTHING = 0x4
    BARTERS_BOOKS = 0x8
    BARTERS_INGREDIENTS = 0x10
    BARTERS_LOCKPICKS = 0x20
    BARTERS_PROBES = 0x40
    BARTERS_LIGHTS = 0x80
    BARTERS_APPARATUS = 0x100
    BARTERS_REPAIR_ITEMS = 0x200
    BARTERS_MISC_ITEMS = 0x400
    OFFERS_SPELLS = 0x800
    BARTERS_ENCHANTED_ITEMS = 0x1000
    BARTERS_ALCHEMY = 0x2000
    OFFERS_TRAINING = 0x4000
    OFFERS_SPELLMAKING = 0x8000
    OFFERS_ENCHANTING = 0x10000
    OFFERS_REPAIRS = 0x20000


class SpellFlags(enum.IntFlag):
    AUTO_CALCULATE = 0x1
    PC_START_SPELL = 0x2
    ALWAYS_SUCCEEDS = 0x4


class WeaponFlags(enum.IntFlag):
    IGNORES_NORMAL_WEAPON_RESISTANCE = 0x1
    SILVER = 0x2


#: Each flag set's wire width in bytes (its backing integer).
WIDTH: Final[dict[str, int]] = {
    "AlchemyFlags": 4,
    "BodypartFlags": 1,
    "CellFlags": 4,
    "ClassFlags": 4,
    "ContainerFlags": 4,
    "CreatureFlags": 1,
    "EnchantingFlags": 4,
    "FactionFlags": 4,
    "LandscapeFlags": 4,
    "LeveledCreatureFlags": 4,
    "LeveledItemFlags": 4,
    "LightFlags": 4,
    "MagicEffectFlags": 4,
    "MiscItemFlags": 4,
    "NpcFlags": 1,
    "ObjectFlags": 4,
    "RaceFlags": 4,
    "ServiceFlags": 4,
    "SpellFlags": 4,
    "WeaponFlags": 4,
}
