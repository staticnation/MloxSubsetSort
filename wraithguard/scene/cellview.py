"""Turn parsed plugins into a previewable cell -- selection and orchestration.

Ties the pieces together without any IO: given every plugin's parsed records (in
load order), it lists the cells present, finds one cell's per-plugin layers, and
runs :func:`~wraithguard.scene.resolve.resolve_cell` +
:func:`~wraithguard.scene.build.build_scene`. The caller reads plugin bytes
(:func:`wraithguard.esp.plugin.read_plugin` / ``read_header``) and supplies the
mesh loader; everything here is pure and unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wraithguard.esp.flags import CellFlags
from wraithguard.esp.records.cell import Cell
from wraithguard.scene.build import build_scene
from wraithguard.scene.resolve import build_model_index, resolve_cell

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from wraithguard.nif.geometry import Mesh
    from wraithguard.scene.build import BuiltScene
    from wraithguard.scene.resolve import CellAudit, Placement


@dataclass(frozen=True)
class LoadedPlugin:
    """One parsed plugin in the load order.

    Attributes:
        name: The plugin filename (for reference identity and reporting).
        masters: Its master filenames, in order.
        records: Its records, as :func:`wraithguard.esp.plugin.read_plugin`
            returns them.
    """

    name: str
    masters: Sequence[str]
    records: Sequence[object]


@dataclass(frozen=True)
class CellKey:
    """A load-order-wide identity for a cell: an interior name or an exterior grid."""

    interior: str | None = None
    grid: tuple[int, int] | None = None


def cell_key(cell: Cell) -> CellKey:
    """The identity of a cell record: its lower-cased name if interior, else grid."""
    if cell.data.cell_flags & CellFlags.IS_INTERIOR:
        return CellKey(interior=cell.name.lower(), grid=None)
    return CellKey(interior=None, grid=cell.data.grid)


def cell_label(cell: Cell) -> str:
    """A human label: the interior name, or the exterior region/name + grid."""
    if cell.data.cell_flags & CellFlags.IS_INTERIOR:
        return cell.name or "(unnamed interior)"
    named = cell.name or cell.region or "Wilderness"
    return f"{named} ({cell.data.grid[0]}, {cell.data.grid[1]})"


@dataclass
class CellChoice:
    """A cell offered for preview: its identity, a label, and where it appears."""

    key: CellKey
    label: str
    plugins: list[str] = field(default_factory=list)


def list_cells(plugins: Sequence[LoadedPlugin]) -> list[CellChoice]:
    """Every distinct cell across the load order, with the plugins that touch it.

    Interiors first (by name), then exteriors (by grid), so the picker reads in a
    predictable order.

    Args:
        plugins: The parsed load order.

    Returns:
        One :class:`CellChoice` per distinct cell.
    """
    choices: dict[CellKey, CellChoice] = {}
    for plugin in plugins:
        for record in plugin.records:
            if isinstance(record, Cell):
                key = cell_key(record)
                choice = choices.get(key)
                if choice is None:
                    choice = CellChoice(key=key, label=cell_label(record))
                    choices[key] = choice
                if plugin.name not in choice.plugins:
                    choice.plugins.append(plugin.name)
    return sorted(
        choices.values(),
        key=lambda c: (c.key.interior is None, c.key.interior or "", c.key.grid or (0, 0)),
    )


def cell_layers(
    plugins: Sequence[LoadedPlugin], key: CellKey
) -> list[tuple[str, Sequence[str], Cell | None]]:
    """Each plugin's version of one cell (``None`` when it does not touch it).

    Args:
        plugins: The parsed load order.
        key: The cell to collect, from :func:`cell_key`.

    Returns:
        ``(plugin_name, masters, cell_or_None)`` per plugin, in load order --
        the input :func:`~wraithguard.scene.resolve.resolve_cell` expects.
    """
    layers: list[tuple[str, Sequence[str], Cell | None]] = []
    for plugin in plugins:
        match: Cell | None = None
        for record in plugin.records:
            if isinstance(record, Cell) and cell_key(record) == key:
                match = record
                break
        layers.append((plugin.name, plugin.masters, match))
    return layers


def preview_cell(
    plugins: Sequence[LoadedPlugin],
    key: CellKey,
    load_mesh: Callable[[str], list[Mesh] | None],
) -> tuple[list[Placement], CellAudit, BuiltScene]:
    """Resolve a cell and assemble its scene.

    Args:
        plugins: The parsed load order.
        key: The cell to preview.
        load_mesh: Resolves a model path to its world meshes (see
            :func:`wraithguard.scene.build.build_scene`).

    Returns:
        ``(placements, audit, scene)``.
    """
    model_index = build_model_index(record for plugin in plugins for record in plugin.records)
    layers = cell_layers(plugins, key)
    placements, audit = resolve_cell(layers, model_index)
    scene = build_scene(placements, load_mesh)
    return placements, audit, scene
