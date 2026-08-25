"""TES3 enums by name -- GENERATED from the tes3 crate, do not edit.

See ``tools/gen_esp_types.py``. Each is an ``IntEnum`` whose members are the
crate's variants at their ``#[repr]`` values; an unrecognised wire value coerces
to the ``#[default]`` variant, as the crate's ``unwrap_or_default`` does. ``WIDTH``
is the repr's byte width, so a record reads the right number of bytes.
"""

from __future__ import annotations

import enum
from typing import ClassVar, Final, TypeVar

_E = TypeVar("_E", bound="EspEnum")


class EspEnum(enum.IntEnum):
    """An enum that coerces an unknown wire value to its default variant."""

    __default_name__: ClassVar[str] = ""

    @classmethod
    def _missing_(cls, value: object) -> EspEnum:
        """Any value the crate does not define reads back as the default."""
        return cls[cls.__default_name__]

    @classmethod
    def default(cls: type[_E]) -> _E:
        """The crate's ``#[default]`` variant, for seeding a field."""
        return cls[cls.__default_name__]


class ApparatusType(EspEnum):
    __default_name__ = "MortarAndPestle"

    MortarAndPestle = 0
    Alembic = 1
    Calcinator = 2
    Retort = 3

class ArmorType(EspEnum):
    __default_name__ = "Helmet"

    Helmet = 0
    Cuirass = 1
    LeftPauldron = 2
    RightPauldron = 3
    Greaves = 4
    Boots = 5
    LeftGauntlet = 6
    RightGauntlet = 7
    Shield = 8
    LeftBracer = 9
    RightBracer = 10

class AttributeId(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    Strength = 0
    Intelligence = 1
    Willpower = 2
    Agility = 3
    Speed = 4
    Endurance = 5
    Personality = 6
    Luck = 7

class AttributeId2(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    Strength = 0
    Intelligence = 1
    Willpower = 2
    Agility = 3
    Speed = 4
    Endurance = 5
    Personality = 6
    Luck = 7

class BipedObjectType(EspEnum):
    __default_name__ = "Head"

    Head = 0
    Hair = 1
    Neck = 2
    Chest = 3
    Groin = 4
    Skirt = 5
    RightHand = 6
    LeftHand = 7
    RightWrist = 8
    LeftWrist = 9
    Shield = 10
    RightForearm = 11
    LeftForearm = 12
    RightUpperArm = 13
    LeftUpperArm = 14
    RightFoot = 15
    LeftFoot = 16
    RightAnkle = 17
    LeftAnkle = 18
    RightKnee = 19
    LeftKnee = 20
    RightUpperLeg = 21
    LeftUpperLeg = 22
    RightPauldron = 23
    LeftPauldron = 24
    Weapon = 25
    Tail = 26

class BodypartId(EspEnum):
    __default_name__ = "Head"

    Head = 0
    Hair = 1
    Neck = 2
    Chest = 3
    Groin = 4
    Hand = 5
    Wrist = 6
    Forearm = 7
    UpperArm = 8
    Foot = 9
    Ankle = 10
    Knee = 11
    UpperLeg = 12
    Clavicle = 13
    Tail = 14

class BodypartType(EspEnum):
    __default_name__ = "Skin"

    Skin = 0
    Clothing = 1
    Armor = 2

class BookType(EspEnum):
    __default_name__ = "Book"

    Book = 0
    Scroll = 1

class ClothingType(EspEnum):
    __default_name__ = "Pants"

    Pants = 0
    Shoes = 1
    Shirt = 2
    Belt = 3
    Robe = 4
    RightGlove = 5
    LeftGlove = 6
    Skirt = 7
    Ring = 8
    Amulet = 9

class CreatureType(EspEnum):
    __default_name__ = "Normal"

    Normal = 0
    Daedra = 1
    Undead = 2
    Humanoid = 3

class DialogueType(EspEnum):
    __default_name__ = "Topic"

    Topic = 0
    Voice = 1
    Greeting = 2
    Persuasion = 3
    Journal = 4

class DialogueType2(EspEnum):
    __default_name__ = "Topic"

    Topic = 0
    Voice = 1
    Greeting = 2
    Persuasion = 3
    Journal = 4

class EffectId(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    WaterBreathing = 0
    SwiftSwim = 1
    WaterWalking = 2
    Shield = 3
    FireShield = 4
    LightningShield = 5
    FrostShield = 6
    Burden = 7
    Feather = 8
    Jump = 9
    Levitate = 10
    SlowFall = 11
    Lock = 12
    Open = 13
    FireDamage = 14
    ShockDamage = 15
    FrostDamage = 16
    DrainAttribute = 17
    DrainHealth = 18
    DrainMagicka = 19
    DrainFatigue = 20
    DrainSkill = 21
    DamageAttribute = 22
    DamageHealth = 23
    DamageMagicka = 24
    DamageFatigue = 25
    DamageSkill = 26
    Poison = 27
    WeaknessToFire = 28
    WeaknessToFrost = 29
    WeaknessToShock = 30
    WeaknessToMagicka = 31
    WeaknessToCommonDisease = 32
    WeaknessToBlightDisease = 33
    WeaknessToCorprus = 34
    WeaknessToPoison = 35
    WeaknessToNormalWeapons = 36
    DisintegrateWeapon = 37
    DisintegrateArmor = 38
    Invisibility = 39
    Chameleon = 40
    Light = 41
    Sanctuary = 42
    NightEye = 43
    Charm = 44
    Paralyze = 45
    Silence = 46
    Blind = 47
    Sound = 48
    CalmHumanoid = 49
    CalmCreature = 50
    FrenzyHumanoid = 51
    FrenzyCreature = 52
    DemoralizeHumanoid = 53
    DemoralizeCreature = 54
    RallyHumanoid = 55
    RallyCreature = 56
    Dispel = 57
    SoulTrap = 58
    Telekinesis = 59
    Mark = 60
    Recall = 61
    DivineIntervention = 62
    AlmsiviIntervention = 63
    DetectAnimal = 64
    DetectEnchantment = 65
    DetectKey = 66
    SpellAbsorption = 67
    Reflect = 68
    CureCommonDisease = 69
    CureBlightDisease = 70
    CureCorprus = 71
    CurePoison = 72
    CureParalyzation = 73
    RestoreAttribute = 74
    RestoreHealth = 75
    RestoreMagicka = 76
    RestoreFatigue = 77
    RestoreSkill = 78
    FortifyAttribute = 79
    FortifyHealth = 80
    FortifyMagicka = 81
    FortifyFatigue = 82
    FortifySkill = 83
    FortifyMagickaMultiplier = 84
    AbsorbAttribute = 85
    AbsorbHealth = 86
    AbsorbMagicka = 87
    AbsorbFatigue = 88
    AbsorbSkill = 89
    ResistFire = 90
    ResistFrost = 91
    ResistShock = 92
    ResistMagicka = 93
    ResistCommonDisease = 94
    ResistBlightDisease = 95
    ResistCorprus = 96
    ResistPoison = 97
    ResistNormalWeapons = 98
    ResistParalysis = 99
    RemoveCurse = 100
    TurnUndead = 101
    SummonScamp = 102
    SummonClannfear = 103
    SummonDaedroth = 104
    SummonDremora = 105
    SummonGhost = 106
    SummonSkeleton = 107
    SummonLeastBonewalker = 108
    SummonGreaterBonewalker = 109
    SummonBonelord = 110
    SummonTwilight = 111
    SummonHunger = 112
    SummonGoldenSaint = 113
    SummonFlameAtronach = 114
    SummonFrostAtronach = 115
    SummonStormAtronach = 116
    FortifyAttackBonus = 117
    CommandCreature = 118
    CommandHumanoid = 119
    BoundDagger = 120
    BoundLongsword = 121
    BoundMace = 122
    BoundBattleAxe = 123
    BoundSpear = 124
    BoundLongbow = 125
    ExtraSpell = 126
    BoundCuirass = 127
    BoundHelm = 128
    BoundBoots = 129
    BoundShield = 130
    BoundGloves = 131
    Corprus = 132
    Vampirism = 133
    SummonCenturionSphere = 134
    SunDamage = 135
    StuntedMagicka = 136
    SummonFabricant = 137
    SummonWolf = 138
    SummonBear = 139
    SummonBoneWolf = 140
    Summon04 = 141
    Summon05 = 142

class EffectId2(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    WaterBreathing = 0
    SwiftSwim = 1
    WaterWalking = 2
    Shield = 3
    FireShield = 4
    LightningShield = 5
    FrostShield = 6
    Burden = 7
    Feather = 8
    Jump = 9
    Levitate = 10
    SlowFall = 11
    Lock = 12
    Open = 13
    FireDamage = 14
    ShockDamage = 15
    FrostDamage = 16
    DrainAttribute = 17
    DrainHealth = 18
    DrainMagicka = 19
    DrainFatigue = 20
    DrainSkill = 21
    DamageAttribute = 22
    DamageHealth = 23
    DamageMagicka = 24
    DamageFatigue = 25
    DamageSkill = 26
    Poison = 27
    WeaknessToFire = 28
    WeaknessToFrost = 29
    WeaknessToShock = 30
    WeaknessToMagicka = 31
    WeaknessToCommonDisease = 32
    WeaknessToBlightDisease = 33
    WeaknessToCorprus = 34
    WeaknessToPoison = 35
    WeaknessToNormalWeapons = 36
    DisintegrateWeapon = 37
    DisintegrateArmor = 38
    Invisibility = 39
    Chameleon = 40
    Light = 41
    Sanctuary = 42
    NightEye = 43
    Charm = 44
    Paralyze = 45
    Silence = 46
    Blind = 47
    Sound = 48
    CalmHumanoid = 49
    CalmCreature = 50
    FrenzyHumanoid = 51
    FrenzyCreature = 52
    DemoralizeHumanoid = 53
    DemoralizeCreature = 54
    RallyHumanoid = 55
    RallyCreature = 56
    Dispel = 57
    SoulTrap = 58
    Telekinesis = 59
    Mark = 60
    Recall = 61
    DivineIntervention = 62
    AlmsiviIntervention = 63
    DetectAnimal = 64
    DetectEnchantment = 65
    DetectKey = 66
    SpellAbsorption = 67
    Reflect = 68
    CureCommonDisease = 69
    CureBlightDisease = 70
    CureCorprus = 71
    CurePoison = 72
    CureParalyzation = 73
    RestoreAttribute = 74
    RestoreHealth = 75
    RestoreMagicka = 76
    RestoreFatigue = 77
    RestoreSkill = 78
    FortifyAttribute = 79
    FortifyHealth = 80
    FortifyMagicka = 81
    FortifyFatigue = 82
    FortifySkill = 83
    FortifyMagickaMultiplier = 84
    AbsorbAttribute = 85
    AbsorbHealth = 86
    AbsorbMagicka = 87
    AbsorbFatigue = 88
    AbsorbSkill = 89
    ResistFire = 90
    ResistFrost = 91
    ResistShock = 92
    ResistMagicka = 93
    ResistCommonDisease = 94
    ResistBlightDisease = 95
    ResistCorprus = 96
    ResistPoison = 97
    ResistNormalWeapons = 98
    ResistParalysis = 99
    RemoveCurse = 100
    TurnUndead = 101
    SummonScamp = 102
    SummonClannfear = 103
    SummonDaedroth = 104
    SummonDremora = 105
    SummonGhost = 106
    SummonSkeleton = 107
    SummonLeastBonewalker = 108
    SummonGreaterBonewalker = 109
    SummonBonelord = 110
    SummonTwilight = 111
    SummonHunger = 112
    SummonGoldenSaint = 113
    SummonFlameAtronach = 114
    SummonFrostAtronach = 115
    SummonStormAtronach = 116
    FortifyAttackBonus = 117
    CommandCreature = 118
    CommandHumanoid = 119
    BoundDagger = 120
    BoundLongsword = 121
    BoundMace = 122
    BoundBattleAxe = 123
    BoundSpear = 124
    BoundLongbow = 125
    ExtraSpell = 126
    BoundCuirass = 127
    BoundHelm = 128
    BoundBoots = 129
    BoundShield = 130
    BoundGloves = 131
    Corprus = 132
    Vampirism = 133
    SummonCenturionSphere = 134
    SunDamage = 135
    StuntedMagicka = 136
    SummonFabricant = 137
    SummonWolf = 138
    SummonBear = 139
    SummonBoneWolf = 140
    Summon04 = 141
    Summon05 = 142

class EffectRange(EspEnum):
    __default_name__ = "OnSelf"

    OnSelf = 0
    OnTouch = 1
    OnTarget = 2

class EffectSchool(EspEnum):
    __default_name__ = "Alteration"

    Alteration = 0
    Conjuration = 1
    Destruction = 2
    Illusion = 3
    Mysticism = 4
    Restoration = 5

class EnchantType(EspEnum):
    __default_name__ = "CastOnce"

    CastOnce = 0
    CastOnStrike = 1
    CastWhenUsed = 2
    ConstantEffect = 3

class FileType(EspEnum):
    __default_name__ = "Esp"

    Esp = 0
    Esm = 1
    Ess = 32

class FilterComparison(EspEnum):
    __default_name__ = "Equal"

    Equal = 48
    NotEqual = 49
    Greater = 50
    GreaterEqual = 51
    Less = 52
    LessEqual = 53

class FilterFunction(EspEnum):
    __default_name__ = "ReactionLow"

    ReactionLow = 12336
    ReactionHigh = 12592
    RankRequirement = 12848
    Reputation = 13104
    HealthPercent = 13360
    PcReputation = 13616
    PcLevel = 13872
    PcHealthPercent = 14128
    PcMagicka = 14384
    PcFatigue = 14640
    PcStrength = 12337
    PcBlock = 12593
    PcArmorer = 12849
    PcMediumArmor = 13105
    PcHeavyArmor = 13361
    PcBluntWeapon = 13617
    PcLongBlade = 13873
    PcAxe = 14129
    PcSpear = 14385
    PcAthletics = 14641
    PcEnchant = 12338
    PcDestruction = 12594
    PcAlteration = 12850
    PcIllusion = 13106
    PcConjuration = 13362
    PcMysticism = 13618
    PcRestoration = 13874
    PcAlchemy = 14130
    PcUnarmored = 14386
    PcSecurity = 14642
    PcSneak = 12339
    PcAcrobatics = 12595
    PcLightArmor = 12851
    PcShortBlade = 13107
    PcMarksman = 13363
    PcMercantile = 13619
    PcSpeechcraft = 13875
    PcHandToHand = 14131
    PcSex = 14387
    PcExpelled = 14643
    PcCommonDisease = 12340
    PcBlightDisease = 12596
    PcClothingModifier = 12852
    PcCrimeLevel = 13108
    SameSex = 13364
    SameRace = 13620
    SameFaction = 13876
    FactionRankDifference = 14132
    Detected = 14388
    Alarmed = 14644
    Choice = 12341
    PcIntelligence = 12597
    PcWillpower = 12853
    PcAgility = 13109
    PcSpeed = 13365
    PcEndurance = 13621
    PcPersonality = 13877
    PcLuck = 14133
    PcCorprus = 14389
    Weather = 14645
    PcVampire = 12342
    Level = 12598
    Attacked = 12854
    TalkedToPc = 13110
    PcHealth = 13366
    CreatureTarget = 13622
    FriendHit = 13878
    Fight = 14134
    Hello = 14390
    Alarm = 14646
    Flee = 12343
    ShouldAttack = 12599
    Werewolf = 12855
    WerewolfKills = 13111
    NotClass = 22595
    DeadType = 22596
    NotFaction = 22598
    ItemType = 22601
    JournalType = 22602
    NotCell = 22604
    NotRace = 22610
    NotIdType = 22616
    Global = 22630
    PcGold = 22636
    CompareGlobal = 22578
    CompareLocal = 22579
    VariableCompare = 22643

class FilterType(EspEnum):
    __default_name__ = "None_"

    None_ = 48
    Function = 49
    Global = 50
    Local = 51
    Journal = 52
    Item = 53
    Dead = 54
    NotId = 55
    NotFaction = 56
    NotClass = 57
    NotRace = 65
    NotCell = 66
    NotLocal = 67

class GlobalType(EspEnum):
    __default_name__ = "Float"

    Float = 102
    Long = 108
    Short = 115

class Sex(EspEnum):
    __default_name__ = "Any"

    Any = -1
    Male = 0
    Female = 1

class SkillId(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    Block = 0
    Armorer = 1
    MediumArmor = 2
    HeavyArmor = 3
    BluntWeapon = 4
    LongBlade = 5
    Axe = 6
    Spear = 7
    Athletics = 8
    Enchant = 9
    Destruction = 10
    Alteration = 11
    Illusion = 12
    Conjuration = 13
    Mysticism = 14
    Restoration = 15
    Alchemy = 16
    Unarmored = 17
    Security = 18
    Sneak = 19
    Acrobatics = 20
    LightArmor = 21
    ShortBlade = 22
    Marksman = 23
    Mercantile = 24
    Speechcraft = 25
    HandToHand = 26

class SkillId2(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    Block = 0
    Armorer = 1
    MediumArmor = 2
    HeavyArmor = 3
    BluntWeapon = 4
    LongBlade = 5
    Axe = 6
    Spear = 7
    Athletics = 8
    Enchant = 9
    Destruction = 10
    Alteration = 11
    Illusion = 12
    Conjuration = 13
    Mysticism = 14
    Restoration = 15
    Alchemy = 16
    Unarmored = 17
    Security = 18
    Sneak = 19
    Acrobatics = 20
    LightArmor = 21
    ShortBlade = 22
    Marksman = 23
    Mercantile = 24
    Speechcraft = 25
    HandToHand = 26

class SoundGenType(EspEnum):
    __default_name__ = "LeftFoot"

    LeftFoot = 0
    RightFoot = 1
    SwimLeft = 2
    SwimRight = 3
    Moan = 4
    Roar = 5
    Scream = 6
    Land = 7

class Specialization(EspEnum):
    __default_name__ = "None_"

    None_ = -1
    Combat = 0
    Magic = 1
    Stealth = 2

class SpellType(EspEnum):
    __default_name__ = "Spell"

    Spell = 0
    Ability = 1
    Blight = 2
    Disease = 3
    Curse = 4
    Power = 5

class WeaponType(EspEnum):
    __default_name__ = "ShortBladeOneHand"

    ShortBladeOneHand = 0
    LongBladeOneHand = 1
    LongBladeTwoClose = 2
    BluntOneHand = 3
    BluntTwoClose = 4
    BluntTwoWide = 5
    SpearTwoWide = 6
    AxeOneHand = 7
    AxeTwoHand = 8
    MarksmanBow = 9
    MarksmanCrossbow = 10
    MarksmanThrown = 11
    Arrow = 12
    Bolt = 13


#: Each enum's wire width in bytes (its repr).
WIDTH: Final[dict[str, int]] = {
    "ApparatusType": 4,
    "ArmorType": 4,
    "AttributeId": 4,
    "AttributeId2": 1,
    "BipedObjectType": 1,
    "BodypartId": 1,
    "BodypartType": 1,
    "BookType": 4,
    "ClothingType": 4,
    "CreatureType": 4,
    "DialogueType": 4,
    "DialogueType2": 1,
    "EffectId": 4,
    "EffectId2": 2,
    "EffectRange": 4,
    "EffectSchool": 4,
    "EnchantType": 4,
    "FileType": 4,
    "FilterComparison": 1,
    "FilterFunction": 2,
    "FilterType": 1,
    "GlobalType": 1,
    "Sex": 1,
    "SkillId": 4,
    "SkillId2": 1,
    "SoundGenType": 4,
    "Specialization": 4,
    "SpellType": 4,
    "WeaponType": 2,
}
