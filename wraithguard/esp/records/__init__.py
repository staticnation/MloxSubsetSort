"""The ported TES3 record types.

Importing this package registers every ported record with
:data:`wraithguard.esp.record.REGISTRY`, so
:func:`wraithguard.esp.plugin.read_plugin` can dispatch a tag to its class. Each
module here mirrors one ``types/*.rs`` file in the tes3 crate. The set grows as
records are ported; a tag with no module yet is read as an
:class:`~wraithguard.esp.record.UnknownRecord` and preserved.
"""

from __future__ import annotations

from wraithguard.esp.records.activator import Activator
from wraithguard.esp.records.alchemy import Alchemy, AlchemyData
from wraithguard.esp.records.apparatus import Apparatus, ApparatusData
from wraithguard.esp.records.armor import Armor, ArmorData
from wraithguard.esp.records.bipedobject import BipedObject
from wraithguard.esp.records.birthsign import Birthsign
from wraithguard.esp.records.bodypart import Bodypart, BodypartData
from wraithguard.esp.records.book import Book, BookData
from wraithguard.esp.records.cell import AtmosphereData, Cell, CellData
from wraithguard.esp.records.class_ import Class, ClassData
from wraithguard.esp.records.clothing import Clothing, ClothingData
from wraithguard.esp.records.container import Container
from wraithguard.esp.records.creature import Creature, CreatureData
from wraithguard.esp.records.dialogue import Dialogue
from wraithguard.esp.records.dialogueinfo import DialogueData, DialogueInfo, Filter
from wraithguard.esp.records.door import Door
from wraithguard.esp.records.effect import Effect
from wraithguard.esp.records.enchanting import Enchanting, EnchantingData
from wraithguard.esp.records.faction import (
    Faction,
    FactionData,
    FactionReaction,
    FactionRequirement,
)
from wraithguard.esp.records.gamesetting import GameSetting
from wraithguard.esp.records.globalvariable import GlobalVariable
from wraithguard.esp.records.header import Header
from wraithguard.esp.records.ingredient import Ingredient, IngredientData
from wraithguard.esp.records.landscape import Landscape
from wraithguard.esp.records.landscapetexture import LandscapeTexture
from wraithguard.esp.records.leveledcreature import LeveledCreature
from wraithguard.esp.records.leveleditem import LeveledItem
from wraithguard.esp.records.light import Light, LightData
from wraithguard.esp.records.lockpick import Lockpick, LockpickData
from wraithguard.esp.records.magiceffect import MagicEffect, MagicEffectData
from wraithguard.esp.records.miscitem import MiscItem, MiscItemData
from wraithguard.esp.records.npc import Npc, NpcData, NpcStats
from wraithguard.esp.records.pathgrid import PathGrid, PathGridData, PathGridPoint
from wraithguard.esp.records.probe import Probe, ProbeData
from wraithguard.esp.records.race import Race, RaceData
from wraithguard.esp.records.reference import Reference
from wraithguard.esp.records.region import Region, WeatherChances
from wraithguard.esp.records.repairitem import RepairItem, RepairItemData
from wraithguard.esp.records.script import Script, ScriptHeader
from wraithguard.esp.records.skill import Skill, SkillData
from wraithguard.esp.records.sound import Sound, SoundData
from wraithguard.esp.records.soundgen import SoundGen
from wraithguard.esp.records.spell import Spell, SpellData
from wraithguard.esp.records.startscript import StartScript
from wraithguard.esp.records.static_ import Static
from wraithguard.esp.records.weapon import Weapon, WeaponData

__all__ = [
    "Activator",
    "Alchemy",
    "AlchemyData",
    "Apparatus",
    "ApparatusData",
    "Armor",
    "ArmorData",
    "AtmosphereData",
    "BipedObject",
    "Birthsign",
    "Bodypart",
    "BodypartData",
    "Book",
    "BookData",
    "Cell",
    "CellData",
    "Class",
    "ClassData",
    "Clothing",
    "ClothingData",
    "Container",
    "Creature",
    "CreatureData",
    "Dialogue",
    "DialogueData",
    "DialogueInfo",
    "Door",
    "Effect",
    "Enchanting",
    "EnchantingData",
    "Faction",
    "FactionData",
    "FactionReaction",
    "FactionRequirement",
    "Filter",
    "GameSetting",
    "GlobalVariable",
    "Header",
    "Ingredient",
    "IngredientData",
    "Landscape",
    "LandscapeTexture",
    "LeveledCreature",
    "LeveledItem",
    "Light",
    "LightData",
    "Lockpick",
    "LockpickData",
    "MagicEffect",
    "MagicEffectData",
    "MiscItem",
    "MiscItemData",
    "Npc",
    "NpcData",
    "NpcStats",
    "PathGrid",
    "PathGridData",
    "PathGridPoint",
    "Probe",
    "ProbeData",
    "Race",
    "RaceData",
    "Reference",
    "Region",
    "RepairItem",
    "RepairItemData",
    "Script",
    "ScriptHeader",
    "Skill",
    "SkillData",
    "Sound",
    "SoundData",
    "SoundGen",
    "Spell",
    "SpellData",
    "StartScript",
    "Static",
    "Weapon",
    "WeaponData",
    "WeatherChances",
]
