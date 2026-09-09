"""Tests for ``wraithguard.scene.resolve`` -- cell placement resolution.

Turns a cell's references + the load order into placements and an audit, with no
file or mesh IO: references and cell layers are built by hand, object records use
the real ``Static`` (which carries id + mesh) and a stand-in for actors.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from wraithguard.esp.records.reference import Reference
from wraithguard.esp.records.static_ import Static
from wraithguard.scene.resolve import (
    build_model_index,
    reference_transform,
    resolve_cell,
)


def _layer(name: str, refs: list[Reference], masters: list[str] | None = None):
    """A (plugin_name, masters, cell-with-references) load-order layer."""
    return (name, masters or [], SimpleNamespace(references=refs))


class TestReferenceTransform:
    def test_identity_is_just_translation(self) -> None:
        t = reference_transform((10.0, 20.0, 30.0), (0.0, 0.0, 0.0), None)
        assert t.apply((1.0, 0.0, 0.0)) == (11.0, 20.0, 30.0)

    def test_scale_is_applied_before_translation(self) -> None:
        t = reference_transform((10.0, 0.0, 0.0), (0.0, 0.0, 0.0), 2.0)
        assert t.apply((1.0, 0.0, 0.0)) == (12.0, 0.0, 0.0)

    def test_none_scale_is_one(self) -> None:
        t = reference_transform((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), None)
        assert t.scale == 1.0

    def test_a_z_rotation_is_a_length_preserving_rotation(self) -> None:
        # documents the current convention (Rz(-z)): +X -> -Y for a +90 deg yaw.
        t = reference_transform((0.0, 0.0, 0.0), (0.0, 0.0, math.pi / 2), None)
        x, y, z = t.apply((1.0, 0.0, 0.0))
        assert math.isclose(x, 0.0, abs_tol=1e-9)
        assert math.isclose(y, -1.0, abs_tol=1e-9)
        assert math.isclose(z, 0.0, abs_tol=1e-9)
        assert math.isclose(math.hypot(x, y, z), 1.0, abs_tol=1e-9)


class TestModelIndex:
    def test_last_definition_of_an_id_wins_case_insensitively(self) -> None:
        idx = build_model_index(
            [Static(id="Rock", mesh="a/rock.nif"), Static(id="rock", mesh="b/rock2.nif")]
        )
        assert idx.meshes["rock"] == "b/rock2.nif"

    def test_meshless_and_idless_records_are_skipped(self) -> None:
        idx = build_model_index([Static(id="empty", mesh=""), Static(id="", mesh="x.nif")])
        assert idx.meshes == {}

    def test_actors_are_indexed_separately_not_as_meshes(self) -> None:
        creature = SimpleNamespace(id="rat", mesh="r/rat.nif", TAG=b"CREA")
        idx = build_model_index([creature])
        assert "rat" in idx.actor_ids
        assert "rat" not in idx.meshes


class TestResolveCell:
    _INDEX = build_model_index(
        [
            Static(id="rock", mesh="m/rock.nif"),
            Static(id="marker", mesh="m/editormarker.nif"),
            SimpleNamespace(id="guar", mesh="c/guar.nif", TAG=b"CREA"),
        ]
    )

    def test_a_placed_reference(self) -> None:
        refs = [Reference(id="rock", mast_index=0, refr_index=1)]
        placements, audit = resolve_cell([_layer("Mod.esp", refs)], self._INDEX)
        assert len(placements) == 1
        assert placements[0].kind == "placed"
        assert placements[0].model == "m/rock.nif"
        assert audit.references == 1 and audit.placed == 1

    def test_an_object_with_no_mesh_record(self) -> None:
        refs = [Reference(id="mystery", mast_index=0, refr_index=1)]
        _placements, audit = resolve_cell([_layer("Mod.esp", refs)], self._INDEX)
        assert audit.no_mesh_record == 1 and audit.placed == 0

    def test_an_actor_reference_is_skipped(self) -> None:
        refs = [Reference(id="guar", mast_index=0, refr_index=1)]
        placements, audit = resolve_cell([_layer("Mod.esp", refs)], self._INDEX)
        assert placements[0].kind == "actor" and audit.actors_skipped == 1

    def test_an_editor_marker_is_classified(self) -> None:
        refs = [Reference(id="marker", mast_index=0, refr_index=1)]
        placements, audit = resolve_cell([_layer("Mod.esp", refs)], self._INDEX)
        assert placements[0].kind == "editor_marker" and audit.editor_markers == 1

    def test_a_later_plugin_overrides_a_masters_reference(self) -> None:
        # both edit reference index 5 that Morrowind.esm created -> one survivor.
        early = Reference(id="rock", mast_index=1, refr_index=5, translation=(0.0, 0.0, 0.0))
        late = Reference(id="rock", mast_index=1, refr_index=5, translation=(1.0, 2.0, 3.0))
        layers = [
            _layer("Tribunal.esm", [early], masters=["Morrowind.esm"]),
            _layer("Mod.esp", [late], masters=["Morrowind.esm"]),
        ]
        placements, audit = resolve_cell(layers, self._INDEX)
        assert audit.references == 1
        assert audit.overridden_by_later == 1
        assert placements[0].transform.translation == (1.0, 2.0, 3.0)  # the later wins

    def test_a_later_plugin_deletes_a_reference(self) -> None:
        added = Reference(id="rock", mast_index=1, refr_index=5)
        killed = Reference(id="rock", mast_index=1, refr_index=5, deleted=True)
        layers = [
            _layer("A.esp", [added], masters=["Morrowind.esm"]),
            _layer("B.esp", [killed], masters=["Morrowind.esm"]),
        ]
        placements, audit = resolve_cell(layers, self._INDEX)
        assert placements == []
        assert audit.deleted_by_later == 1 and audit.references == 0

    def test_deleting_a_never_seen_reference_is_a_noop(self) -> None:
        refs = [Reference(id="rock", mast_index=0, refr_index=1, deleted=True)]
        placements, audit = resolve_cell([_layer("Mod.esp", refs)], self._INDEX)
        assert placements == [] and audit.deleted_by_later == 0 and audit.references == 0

    def test_a_new_reference_from_a_later_plugin_is_added(self) -> None:
        a = Reference(id="rock", mast_index=0, refr_index=1)  # A.esp's own ref
        b = Reference(id="rock", mast_index=0, refr_index=1)  # B.esp's own ref (distinct origin)
        layers = [_layer("A.esp", [a]), _layer("B.esp", [b])]
        _placements, audit = resolve_cell(layers, self._INDEX)
        # different origin files -> two distinct references, neither overrides
        assert audit.references == 2 and audit.overridden_by_later == 0

    def test_a_plugin_that_does_not_touch_the_cell_contributes_nothing(self) -> None:
        refs = [Reference(id="rock", mast_index=0, refr_index=1)]
        layers = [("Untouched.esp", [], None), _layer("Mod.esp", refs)]
        _placements, audit = resolve_cell(layers, self._INDEX)
        assert audit.references == 1

    def test_a_moved_reference_is_counted(self) -> None:
        refs = [Reference(id="rock", mast_index=0, refr_index=1, moved_cell=(2, 3))]
        _placements, audit = resolve_cell([_layer("Mod.esp", refs)], self._INDEX)
        assert audit.moved == 1
