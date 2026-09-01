"""The native ESP <-> tes3conv-JSON layer (:mod:`wraithguard.esp.json`).

These are hermetic: they need no ``tes3conv`` binary and no real plugins, so
they run everywhere CI does. What they cannot check here -- that the JSON matches
a *real* ``tes3conv`` byte-for-byte -- was checked during development against the
binary on synthetic records and two multi-thousand-record Tamriel Rebuilt
plugins (0 value differences across 8,348 records); these lock in the schema
rules that verification established, so a later edit that breaks one is caught.

The load-bearing property is the *self* round trip: records -> JSON -> records
must return the identical records, because the native conflict/patch backend
reads a plugin, converts to JSON for the pipeline, and (for writing) converts
back. Anything lost in that circuit is a record silently changed.
"""

from __future__ import annotations

import pytest

from wraithguard.esp import (
    Cell,
    CellData,
    Class,
    ClassData,
    DialogueInfo,
    GameSetting,
    GlobalVariable,
    Header,
    Landscape,
    Light,
    LightData,
    Npc,
    NpcData,
    NpcStats,
    PathGrid,
    Reference,
    Script,
    plugin_from_json,
    plugin_to_json,
    record_from_json,
    record_to_json,
)
from wraithguard.esp.enums import FileType, GlobalType, SkillId
from wraithguard.esp.flags import CellFlags, LightFlags, ObjectFlags
from wraithguard.esp.records._ai import AiTravelPackage, AiWanderPackage
from wraithguard.esp.records.dialogueinfo import Filter


class TestTagAndFields:
    """The record tag and the plain field rules."""

    def test_record_is_tagged_by_class_name(self) -> None:
        """A record is ``{"type": <ClassName>, ...}`` -- the crate's variant."""
        assert record_to_json(Light(id="torch"))["type"] == "Light"

    def test_keyword_field_drops_its_underscore(self) -> None:
        """The port's ``class_`` is ``class`` on the wire, as the crate spells it."""
        obj = record_to_json(Npc(id="n", class_="mage"))
        assert obj["class"] == "mage"
        assert "class_" not in obj

    def test_none_option_fields_are_omitted(self) -> None:
        """``NpcData.stats`` is ``Option`` -- absent, not ``null``, when unset."""
        obj = record_to_json(Npc(id="n", data=NpcData(stats=None)))
        assert "stats" not in obj["data"]

    def test_present_option_field_is_an_object(self) -> None:
        """When the option is set it serialises as its struct."""
        obj = record_to_json(Npc(id="n", data=NpcData(stats=NpcStats(health=5))))
        assert obj["data"]["stats"]["health"] == 5


class TestEnumsAndFlags:
    """Enums serialise by name; bitflags as a joined name string."""

    def test_enum_is_its_variant_name(self) -> None:
        """A ``FileType`` enum is ``"Esm"``, not its number."""
        assert record_to_json(Header(file_type=FileType.Esm))["file_type"] == "Esm"

    def test_empty_flags_are_the_empty_string(self) -> None:
        """No bits set renders as ``""``."""
        assert record_to_json(Light(id="l"))["flags"] == ""

    def test_flags_join_set_names_with_a_pipe(self) -> None:
        """Two bits render in declaration order, `` | ``-separated."""
        obj = record_to_json(Light(id="l", flags=ObjectFlags.MODIFIED | ObjectFlags.DELETED))
        assert obj["flags"] == "MODIFIED | DELETED"

    def test_flags_round_trip_through_the_string(self) -> None:
        """The joined string parses back to the same bits."""
        data = LightData(flags=LightFlags.DYNAMIC | LightFlags.FIRE)
        back = record_from_json(record_to_json(Light(id="l", data=data)))
        assert back.data.flags == LightData(flags=LightFlags.DYNAMIC | LightFlags.FIRE).flags


class TestByteFields:
    """The three byte-field shapes: array, wrapped blob, and bare vec blob."""

    def test_fixed_array_is_a_number_list(self) -> None:
        """A ``[u8; 4]`` colour is a JSON array, not base64."""
        obj = record_to_json(Light(id="l", data=LightData(color=b"\x01\x02\x03\x04")))
        assert obj["data"]["color"] == [1, 2, 3, 4]

    def test_landscape_layer_is_a_data_object(self) -> None:
        """A ``Box`` terrain layer is ``{"data": <base64>}``."""
        obj = record_to_json(Landscape(grid=(1, 2)))
        assert set(obj["vertex_normals"]) == {"data"}
        assert isinstance(obj["vertex_normals"]["data"], str)

    def test_vertex_heights_carries_its_offset(self) -> None:
        """The height layer is the one with an ``offset`` beside its ``data``."""
        obj = record_to_json(Landscape(grid=(0, 0), vertex_heights_offset=3.5))
        assert obj["vertex_heights"]["offset"] == pytest.approx(3.5)
        assert "data" in obj["vertex_heights"]

    def test_script_blob_is_a_bare_base64_string(self) -> None:
        """A ``Vec<u8>`` bytecode blob is a string, not a ``{"data"}`` object."""
        obj = record_to_json(Script(id="s", bytecode=b"\x01\x02\x03"))
        assert isinstance(obj["bytecode"], str)


class TestAdjacentEnums:
    """Values the crate models as adjacently-tagged numeric enums."""

    def test_game_setting_value_is_tagged_by_python_type(self) -> None:
        """A GMST value is ``{"type": Float|Integer|String, "data": ...}``."""
        assert record_to_json(GameSetting(id="f", value=1.5))["value"] == {
            "type": "Float",
            "data": 1.5,
        }
        assert record_to_json(GameSetting(id="i", value=7))["value"]["type"] == "Integer"
        assert record_to_json(GameSetting(id="s", value="x"))["value"] == {
            "type": "String",
            "data": "x",
        }

    def test_global_value_is_tagged_by_its_type_field(self) -> None:
        """A global's ``global_type`` becomes the tag; there is no separate key."""
        obj = record_to_json(GlobalVariable(id="g", global_type=GlobalType.Short, value=42))
        assert obj["value"] == {"type": "Short", "data": 42}
        assert "global_type" not in obj

    def test_global_value_round_trips(self) -> None:
        """The tag rebuilds the ``global_type`` and the number rebuilds the value."""
        back = record_from_json(
            record_to_json(GlobalVariable(id="g", global_type=GlobalType.Float, value=1.0)),
        )
        assert back.global_type == GlobalType.Float
        assert back.value == pytest.approx(1.0)


class TestStructuralSpecials:
    """The two records whose fields the port groups differently from the crate."""

    def test_class_skills_expand_to_named_minor_major(self) -> None:
        """The ten-skill tuple becomes ``minor1``/``major1``.../``major5``."""
        skills = tuple(SkillId(i) for i in range(10))
        obj = record_to_json(Class(id="c", data=ClassData(skills=skills)))["data"]
        assert obj["minor1"] == SkillId(0).name
        assert obj["major1"] == SkillId(1).name
        assert obj["major5"] == SkillId(9).name
        assert "skills" not in obj

    def test_class_skills_round_trip_in_order(self) -> None:
        """Rebuilding restores the same ten skills in the same order."""
        skills = tuple(SkillId(i) for i in range(10))
        back = record_from_json(record_to_json(Class(id="c", data=ClassData(skills=skills))))
        assert back.data.skills == skills

    def test_cell_data_flags_uses_the_wire_name(self) -> None:
        """``CellData.cell_flags`` is ``flags`` on the wire, like the record's own."""
        obj = record_to_json(Cell(data=CellData(cell_flags=CellFlags.IS_INTERIOR)))
        assert obj["data"]["flags"] == "IS_INTERIOR"
        assert "cell_flags" not in obj["data"]


class TestActorAndDialogueSpecials:
    """The tagged unions in the actor and dialogue records.

    These are the shapes a full mainland ESM turns up that a landscape mod does
    not: an actor's AI packages, and a dialogue info's quest marker and filters.
    """

    def test_ai_package_is_a_tagged_union(self) -> None:
        """A wander package is ``{"type": "Wander", ...idle2..idle9...}``."""
        npc = Npc(
            id="n", ai_packages=[AiWanderPackage(distance=512, idles=(1, 2, 3, 4, 5, 6, 7, 8))]
        )
        pkg = record_to_json(npc)["ai_packages"][0]
        assert pkg["type"] == "Wander"
        assert pkg["idle2"] == 1
        assert pkg["idle9"] == 8
        assert "idles" not in pkg

    def test_mixed_ai_packages_round_trip(self) -> None:
        """A travel and a wander package rebuild to the same objects."""
        npc = Npc(
            id="n",
            ai_packages=[
                AiTravelPackage(location=(1.0, 2.0, 3.0)),
                AiWanderPackage(idles=(0,) * 8),
            ],
        )
        assert record_from_json(record_to_json(npc)) == npc

    def test_quest_state_is_capitalised(self) -> None:
        """The port's lowercase marker is the crate's enum name on the wire."""
        assert (
            record_to_json(DialogueInfo(id="i", quest_state="finished"))["quest_state"]
            == "Finished"
        )

    def test_quest_state_round_trips(self) -> None:
        """And rebuilds to the lowercase marker the port stores."""
        back = record_from_json(record_to_json(DialogueInfo(id="i", quest_state="restart")))
        assert back.quest_state == "restart"

    def test_filter_value_is_adjacently_tagged(self) -> None:
        """A filter's value is ``{"type": "Integer", "data": N}``, like a GMST."""
        info = DialogueInfo(id="i", filters=[Filter(index=0, value=3)])
        assert record_to_json(info)["filters"][0]["value"] == {"type": "Integer", "data": 3}


class TestRoundTrip:
    """The property everything else serves: records survive the JSON circuit."""

    def _plugin(self) -> list:
        """A plugin touching every special case above.

        Returns:
            A record list to round-trip.
        """
        return [
            Header(masters=[("Morrowind.esm", 999)]),
            GameSetting(id="fJump", value=2.5),
            GlobalVariable(id="g", global_type=GlobalType.Short, value=7),
            Light(id="l", flags=ObjectFlags.MODIFIED, data=LightData(color=b"\x09\x08\x07\x06")),
            Npc(
                id="n",
                class_="knight",
                inventory=[(3, "gold_001")],
                spells=["fireball"],
                ai_packages=[AiWanderPackage(distance=256, idles=(1,) * 8), AiTravelPackage()],
            ),
            DialogueInfo(id="info", quest_state="finished", filters=[Filter(index=0, value=2)]),
            Class(id="c", data=ClassData(skills=tuple(SkillId(i) for i in range(10)))),
            Script(id="s", bytecode=b"\x01\x02", variables=b"a\x00b\x00"),
            PathGrid(cell="Interior", connections=[0, 1, 2]),
            Cell(data=CellData(cell_flags=CellFlags.HAS_WATER), references=[Reference(id="x")]),
            Landscape(grid=(4, 5), vertex_heights_offset=1.25),
        ]

    def test_records_survive_the_json_circuit(self) -> None:
        """records -> JSON -> records returns the identical records."""
        records = self._plugin()
        assert plugin_from_json(plugin_to_json(records)) == records

    def test_json_is_the_same_the_second_time(self) -> None:
        """A second conversion of the rebuilt records is byte-identical JSON."""
        records = self._plugin()
        once = plugin_to_json(records)
        twice = plugin_to_json(plugin_from_json(once))
        assert once == twice
