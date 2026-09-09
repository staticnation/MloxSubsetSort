"""Assemble a Morrowind cell for previewing.

Turns a cell's object references plus the sorted load order into an ordered list
of *placements* -- each object's id, its resolved mesh, and its world transform --
together with an audit of what the load order does to the cell (references a
later plugin overrode, deleted or moved; references whose object has no mesh).

The audit is the data behind a "what does this mod change in this cell" view; the
placements feed the 3D scene builder (a later milestone). Everything here is
pure -- no file or mesh IO -- so it is unit-tested against synthetic records; the
caller parses plugins with :mod:`wraithguard.esp.plugin` and hands the records in
load order.
"""

from __future__ import annotations

from wraithguard.scene.build import BuiltScene, build_scene
from wraithguard.scene.cellview import (
    CellChoice,
    CellKey,
    LoadedPlugin,
    cell_key,
    cell_label,
    cell_layers,
    list_cells,
    preview_cell,
)
from wraithguard.scene.resolve import (
    CellAudit,
    ModelIndex,
    Placement,
    build_model_index,
    reference_transform,
    resolve_cell,
)

__all__ = [
    "BuiltScene",
    "CellAudit",
    "CellChoice",
    "CellKey",
    "LoadedPlugin",
    "ModelIndex",
    "Placement",
    "build_model_index",
    "build_scene",
    "cell_key",
    "cell_label",
    "cell_layers",
    "list_cells",
    "preview_cell",
    "reference_transform",
    "resolve_cell",
]
