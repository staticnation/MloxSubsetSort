"""TES3 enum variants by field name -- GENERATED, do not edit by hand.

See ``tools/gen_tes3_enums.py``. Harvested from tes3conv's output for the
vanilla masters, so the spellings are the crate's own. Best-effort: a field
here is one whose vanilla values form a small closed set of PascalCase names.
"""

from __future__ import annotations

from typing import Final

#: 42 enum fields, keyed by leaf field name.
FIELD_ENUMS: Final[dict[str, tuple[str, ...]]] = {
    'apparatus_type': ('Alembic', 'Calcinator', 'MortarAndPestle', 'Retort'),
    'armor_type': ('Boots', 'Cuirass', 'Greaves', 'Helmet', 'LeftBracer', 'LeftGauntlet', 'LeftPauldron', 'RightBracer', 'RightGauntlet', 'RightPauldron', 'Shield'),
    'attribute': ('Agility', 'Endurance', 'Intelligence', 'Luck', 'None', 'Personality', 'Speed', 'Strength', 'Willpower'),
    'attribute1': ('Agility', 'Endurance', 'Intelligence', 'Luck', 'None', 'Personality', 'Speed', 'Strength', 'Willpower'),
    'attribute2': ('Agility', 'Endurance', 'Intelligence', 'Luck', 'None', 'Personality', 'Speed', 'Strength', 'Willpower'),
    'biped_object_type': ('Chest', 'Groin', 'Hair', 'Head', 'LeftAnkle', 'LeftFoot', 'LeftForearm', 'LeftHand', 'LeftKnee', 'LeftPauldron', 'LeftUpperArm', 'LeftUpperLeg', 'LeftWrist', 'Neck', 'RightAnkle', 'RightFoot', 'RightForearm', 'RightHand', 'RightKnee', 'RightPauldron', 'RightUpperArm', 'RightUpperLeg', 'RightWrist', 'Shield', 'Skirt', 'Tail', 'Weapon'),
    'bodypart_type': ('Armor', 'Clothing', 'Skin'),
    'book_type': ('Book', 'Scroll'),
    'clothing_type': ('Amulet', 'Belt', 'LeftGlove', 'Pants', 'RightGlove', 'Ring', 'Robe', 'Shirt', 'Shoes', 'Skirt'),
    'comparison': ('Equal', 'Greater', 'GreaterEqual', 'Less', 'LessEqual', 'NotEqual'),
    'creature_type': ('Daedra', 'Humanoid', 'Normal', 'Undead'),
    'dialogue_type': ('Greeting', 'Journal', 'Persuasion', 'Topic', 'Voice'),
    'enchant_type': ('CastOnStrike', 'CastOnce', 'CastWhenUsed', 'ConstantEffect'),
    'filter_type': ('Dead', 'Function', 'Global', 'Item', 'Journal', 'Local', 'None', 'NotCell', 'NotClass', 'NotFaction', 'NotId', 'NotLocal', 'NotRace'),
    'major1': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'major2': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'major3': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'major4': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'major5': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'minor1': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'minor2': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'minor3': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'minor4': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'minor5': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'part': ('Ankle', 'Chest', 'Clavicle', 'Foot', 'Forearm', 'Groin', 'Hair', 'Hand', 'Head', 'Knee', 'Neck', 'Tail', 'UpperArm', 'UpperLeg', 'Wrist'),
    'quest_state': ('Finished', 'Name', 'Restart'),
    'range': ('OnSelf', 'OnTarget', 'OnTouch'),
    'school': ('Alteration', 'Conjuration', 'Destruction', 'Illusion', 'Mysticism', 'Restoration'),
    'skill': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_0': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_1': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_2': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_3': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_4': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_5': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_6': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'skill_id': ('Acrobatics', 'Alchemy', 'Alteration', 'Armorer', 'Athletics', 'Axe', 'Block', 'BluntWeapon', 'Conjuration', 'Destruction', 'Enchant', 'HandToHand', 'HeavyArmor', 'Illusion', 'LightArmor', 'LongBlade', 'Marksman', 'MediumArmor', 'Mercantile', 'Mysticism', 'None', 'Restoration', 'Security', 'ShortBlade', 'Sneak', 'Spear', 'Speechcraft', 'Unarmored'),
    'sound_gen_type': ('Land', 'LeftFoot', 'Moan', 'RightFoot', 'Roar', 'Scream', 'SwimLeft', 'SwimRight'),
    'speaker_sex': ('Any', 'Female', 'Male'),
    'specialization': ('Combat', 'Magic', 'None', 'Stealth'),
    'spell_type': ('Ability', 'Blight', 'Curse', 'Disease', 'Power', 'Spell'),
    'weapon_type': ('Arrow', 'AxeOneHand', 'AxeTwoHand', 'BluntOneHand', 'BluntTwoClose', 'BluntTwoWide', 'Bolt', 'LongBladeOneHand', 'LongBladeTwoClose', 'MarksmanBow', 'MarksmanCrossbow', 'MarksmanThrown', 'ShortBladeOneHand', 'SpearTwoWide'),
}
