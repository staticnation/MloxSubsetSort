"""Assemble a cell's placements into meshes the mesh viewer can draw.

Each placed reference's world transform is *baked* into a copy of its model's
world-space meshes -- ``Transform.apply`` over every vertex -- producing one flat
list of :class:`~wraithguard.nif.geometry.Mesh` that
:func:`~wraithguard.nif.viewer.build_viewer_page` renders exactly as it renders a
single NIF, textures and all. Baking (rather than GPU instancing) keeps the whole
existing viewer, unchanged, and is fine for the bounded reference counts of an
interior cell; instancing is a later optimisation for big exteriors.

Mesh loading is injected as a callable, so this stays pure and unit-tested: the
caller supplies a loader that resolves a model path across the data folders (see
:func:`wraithguard.nif.vfs.read_mesh`) and returns its
:func:`~wraithguard.nif.geometry.world_meshes`, or ``None`` when it cannot be
read -- one unreadable mesh must not sink the whole cell.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from wraithguard.nif.geometry import Mesh
    from wraithguard.scene.resolve import Placement

#: A model loader: model path (e.g. ``"f/flora_tree_01.nif"``) -> its world
#: meshes, or ``None`` when the mesh cannot be found or read.
MeshLoader = "Callable[[str], list[Mesh] | None]"


@dataclass
class BuiltScene:
    """A cell assembled for the viewer.

    Attributes:
        meshes: Every placed object's meshes, transformed into cell space and
            ready for :func:`~wraithguard.nif.viewer.build_viewer_page`.
        drawn: How many placements contributed geometry.
        missing_models: Model paths that could not be loaded, de-duplicated in
            first-seen order -- the "N meshes not found" line.
    """

    meshes: list[Mesh] = field(default_factory=list)
    drawn: int = 0
    missing_models: list[str] = field(default_factory=list)


def build_scene(
    placements: Sequence[Placement],
    load_mesh: Callable[[str], list[Mesh] | None],
) -> BuiltScene:
    """Bake every placed reference into world-space meshes for the viewer.

    Each model is loaded at most once (repeats are common -- a cell reuses the
    same rock or crate many times) and re-baked per placement. Non-drawable
    placements (``no_mesh_record``/``editor_marker``/``actor``) are skipped.

    Args:
        placements: The cell's placements, from
            :func:`wraithguard.scene.resolve.resolve_cell`.
        load_mesh: Resolves a model path to its world meshes, or ``None``.

    Returns:
        A :class:`BuiltScene`.
    """
    cache: dict[str, list[Mesh] | None] = {}
    out: list[Mesh] = []
    drawn = 0
    missing: list[str] = []
    for placement in placements:
        if placement.kind != "placed" or not placement.model:
            continue
        if placement.model not in cache:
            cache[placement.model] = load_mesh(placement.model)
        base = cache[placement.model]
        if base is None:
            if placement.model not in missing:
                missing.append(placement.model)
            continue
        transform = placement.transform
        out.extend(
            replace(mesh, vertices=[transform.apply(v) for v in mesh.vertices]) for mesh in base
        )
        drawn += 1
    return BuiltScene(meshes=out, drawn=drawn, missing_models=missing)
