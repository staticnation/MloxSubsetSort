"""Edge branches of the landscape merge pipeline.

These pin the plugin-skip reasons, the new-land height-overwrite choice, and the
two "cell has no heights / cell not in the reference" guards that the fidelity
suite's fully-populated cells never take.
"""

from __future__ import annotations

import base64
import struct

from wraithguard.land.landmass import PluginRecords, build_reference
from wraithguard.land.meta import LAYER_NAMES, MergeSettings, PluginMeta
from wraithguard.land.pipeline import finish, merge_landmass, resolve_normals
from wraithguard.tes3fields.landscape import LAND_SIZE

VERTICES = LAND_SIZE * LAND_SIZE


def _tex_record(coords: tuple[int, int], fill: int) -> dict[str, object]:
    data = base64.b64encode(struct.pack("<256H", *([fill] * 256))).decode()
    return {
        "type": "Landscape",
        "grid": list(coords),
        "landscape_flags": "USES_TEXTURES",
        "texture_indices": {"data": data},
    }


def _colors_record(coords: tuple[int, int], rgb: tuple[int, int, int]) -> dict[str, object]:
    data = base64.b64encode(bytes(rgb) * VERTICES).decode()
    return {
        "type": "Landscape",
        "grid": list(coords),
        "landscape_flags": "USES_VERTEX_COLORS",
        "vertex_colors": {"data": data},
    }


def _heights_record(coords: tuple[int, int], first_delta: int) -> dict[str, object]:
    vhgt = struct.pack("<f", 0.0) + bytes([first_delta]) + bytes(VERTICES - 1) + b"\x00\x00\x00"
    return {
        "type": "Landscape",
        "grid": list(coords),
        "landscape_flags": "USES_VERTEX_HEIGHTS_AND_NORMALS",
        "vertex_heights": {"data": base64.b64encode(vhgt).decode(), "offset": 0.0},
    }


def test_a_previous_merge_plugin_is_skipped() -> None:
    """A Merged Lands.esp left in the load order is not folded back in."""
    reference, known = build_reference([])
    outcome = merge_landmass(
        reference,
        [PluginRecords("Merged.esp", [_tex_record((0, 0), 5)])],
        known,
        metas={"Merged.esp": PluginMeta(meta_type="MergedLands")},
    )
    assert outcome.skipped_plugins == [("Merged.esp", "a previous merge")]
    assert outcome.cells == {}


def test_a_plugin_with_every_layer_excluded_is_skipped() -> None:
    """A sidecar that turns off every layer leaves the plugin nothing to add."""
    reference, known = build_reference([])
    excluded = {name: MergeSettings(included=False) for name in LAYER_NAMES}
    outcome = merge_landmass(
        reference,
        [PluginRecords("mod.esp", [_tex_record((0, 0), 5)])],
        known,
        metas={"mod.esp": PluginMeta(meta_type="Auto", layers=excluded)},
    )
    assert outcome.skipped_plugins == [("mod.esp", "every layer excluded")]


def test_new_land_heights_are_taken_wholesale() -> None:
    """Heights on terrain the masters never had are new land, taken as-is."""
    reference, known = build_reference([])  # empty masters -> every cell is new
    outcome = merge_landmass(
        reference, [PluginRecords("mod.esp", [_heights_record((9, 9), 50)])], known
    )
    assert (9, 9) in outcome.cells
    assert outcome.cells[(9, 9)].new_land
    assert outcome.cells[(9, 9)].heights is not None


def test_resolve_normals_skips_a_cell_with_no_heights() -> None:
    """A textures-only cell carries no heights, so normals cannot be recomputed."""
    reference, known = build_reference([])
    outcome = merge_landmass(reference, [PluginRecords("mod.esp", [_tex_record((0, 0), 5)])], known)
    assert outcome.cells[(0, 0)].heights is None
    recomputed = resolve_normals(outcome, reference)
    assert recomputed == 0  # nothing had heights to work from


def test_finish_handles_a_cell_absent_from_the_reference() -> None:
    """A new-land cell has no reference entry, so its border check is skipped."""
    reference, known = build_reference([])
    outcome = merge_landmass(
        reference, [PluginRecords("mod.esp", [_heights_record((9, 9), 50)])], known
    )
    # clean=True so finish reaches the reference-digest loop that skips a cell
    # absent from the reference; repair/limit off to keep the run minimal.
    finished = finish(outcome, reference, repair=False, clean=True, limit=False)
    assert finished is not None


def test_a_non_height_layer_uses_auto_not_the_height_overwrite() -> None:
    """A vertex-colour edit takes the AUTO path, never the new-land height rule."""
    reference, known = build_reference(
        [PluginRecords("m.esm", [_colors_record((0, 0), (0, 0, 0))])]
    )
    outcome = merge_landmass(
        reference, [PluginRecords("mod.esp", [_colors_record((0, 0), (255, 0, 0))])], known
    )
    assert outcome.cells[(0, 0)].colors is not None


def test_existing_land_heights_use_the_default_strategy() -> None:
    """A heights edit on a cell the masters already had is not new land.

    That takes the ``chosen = default`` path instead of the new-land overwrite,
    exercising the branch where the vertex-height overwrite does not apply.
    """
    reference, known = build_reference(
        [PluginRecords("m.esm", [_heights_record((9, 9), 10)])]
    )
    outcome = merge_landmass(
        reference, [PluginRecords("mod.esp", [_heights_record((9, 9), 90)])], known
    )
    assert (9, 9) in outcome.cells
    assert outcome.cells[(9, 9)].new_land is False


def test_a_sidecar_strategy_bypasses_the_auto_choice() -> None:
    """When a sidecar states a concrete strategy, the AUTO defaulting is skipped."""
    from wraithguard.land.merge import ConflictStrategy

    reference, known = build_reference(
        [PluginRecords("m.esm", [_heights_record((9, 9), 10)])]
    )
    metas = {
        "mod.esp": PluginMeta(
            meta_type="Patch",
            layers={"height_map": MergeSettings(conflict_strategy=ConflictStrategy.OVERWRITE)},
        )
    }
    outcome = merge_landmass(
        reference, [PluginRecords("mod.esp", [_heights_record((9, 9), 90)])], known, metas=metas
    )
    assert (9, 9) in outcome.cells
