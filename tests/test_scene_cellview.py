"""Tests for ``wraithguard.scene.cellview`` -- cell selection + orchestration.

Cells and plugins are built from the real records (``Cell``/``CellData``/
``Static``/``Reference``); the mesh loader is a fake, so no IO.
"""

from __future__ import annotations

from wraithguard.esp.flags import CellFlags
from wraithguard.esp.records.cell import Cell, CellData
from wraithguard.esp.records.reference import Reference
from wraithguard.esp.records.static_ import Static
from wraithguard.nif.geometry import Mesh
from wraithguard.scene.cellview import (
    CellKey,
    LoadedPlugin,
    cell_key,
    cell_label,
    cell_layers,
    list_cells,
    preview_cell,
)


def _interior(name: str, refs: list[Reference] | None = None) -> Cell:
    return Cell(
        name=name,
        data=CellData(cell_flags=CellFlags.IS_INTERIOR),
        references=refs or [],
    )


def _exterior(grid: tuple[int, int], region: str = "", refs: list[Reference] | None = None) -> Cell:
    return Cell(name="", region=region, data=CellData(grid=grid), references=refs or [])


class TestKeyAndLabel:
    def test_interior_key_is_the_lowercased_name(self) -> None:
        assert cell_key(_interior("Balmora, Guar Shack")) == CellKey(interior="balmora, guar shack")

    def test_exterior_key_is_the_grid(self) -> None:
        assert cell_key(_exterior((-4, -2))) == CellKey(grid=(-4, -2))

    def test_interior_label_is_the_name(self) -> None:
        assert cell_label(_interior("Balmora, Guar Shack")) == "Balmora, Guar Shack"

    def test_exterior_label_carries_region_and_grid(self) -> None:
        assert cell_label(_exterior((-4, -2), region="Ascadian Isles")) == "Ascadian Isles (-4, -2)"


class TestListCells:
    def test_it_lists_distinct_cells_with_the_plugins_that_touch_them(self) -> None:
        p1 = LoadedPlugin("A.esp", [], [_interior("Cave"), _exterior((1, 1))])
        p2 = LoadedPlugin("B.esp", [], [_interior("Cave")])  # also touches Cave
        choices = list_cells([p1, p2])
        by_label = {c.label: c for c in choices}
        assert set(by_label) == {"Cave", "Wilderness (1, 1)"}
        assert by_label["Cave"].plugins == ["A.esp", "B.esp"]

    def test_interiors_sort_before_exteriors(self) -> None:
        p = LoadedPlugin("A.esp", [], [_exterior((5, 5)), _interior("Zzz"), _interior("Aaa")])
        labels = [c.label for c in list_cells([p])]
        assert labels == ["Aaa", "Zzz", "Wilderness (5, 5)"]


class TestCellLayers:
    def test_it_returns_the_matching_cell_or_none_per_plugin(self) -> None:
        cave_a = _interior("Cave")
        cave_b = _interior("Cave")
        plugins = [
            LoadedPlugin("A.esp", [], [cave_a]),
            LoadedPlugin("B.esp", [], [_interior("Other")]),  # different cell
            LoadedPlugin("C.esp", [], [cave_b]),
        ]
        layers = cell_layers(plugins, CellKey(interior="cave"))
        assert [name for name, _m, _c in layers] == ["A.esp", "B.esp", "C.esp"]
        assert [c for _n, _m, c in layers] == [cave_a, None, cave_b]


class TestPreviewCell:
    def test_end_to_end_resolves_and_builds(self) -> None:
        # A plugin defines a static with a mesh and places one reference to it.
        cell = _interior("Cave", refs=[Reference(id="rock", mast_index=0, refr_index=1)])
        plugin = LoadedPlugin("A.esp", [], [Static(id="rock", mesh="rock.nif"), cell])
        loaded: list[str] = []

        def load_mesh(path: str) -> list[Mesh]:
            loaded.append(path)
            return [Mesh(name="s", vertices=[(0.0, 0.0, 0.0)], triangles=[(0, 0, 0)])]

        placements, audit, scene = preview_cell([plugin], CellKey(interior="cave"), load_mesh)
        assert audit.references == 1 and audit.placed == 1
        assert [p.kind for p in placements] == ["placed"]
        assert loaded == ["rock.nif"] and scene.drawn == 1
