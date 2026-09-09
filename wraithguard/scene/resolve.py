"""Resolve a cell's placed objects across a load order (pure, no IO).

Three steps, each a plain function so they can be tested in isolation:

* :func:`reference_transform` -- a reference's ``(translation, rotation, scale)``
  as a :class:`~wraithguard.nif.geometry.Transform` in the mesh frame, so a
  placed object is ``ref_transform.apply(v)`` over the mesh's world vertices.
* :func:`build_model_index` -- the winning mesh for every object id, and which
  ids are actors, taken from the load order's object records (last plugin wins).
* :func:`resolve_cell` -- merge a cell's references across the plugins that touch
  it (a later plugin adds, overrides or deletes references by their originating
  master + index), classify each surviving reference against the model index, and
  return the placements plus a :class:`CellAudit`.

The Morrowind reference rotation convention -- which Euler order and sign turns
``rot`` into a matrix -- is the one thing that needs calibrating against a real
cell; it is isolated in :func:`reference_transform` behind named constants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wraithguard.nif.geometry import Transform

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from wraithguard.esp.records.cell import Cell
    from wraithguard.esp.records.reference import Reference

#: Record tags that are actors -- they carry a mesh but a cell preview skips them
#: (they are assembled from body parts/animation and are rarely what you are
#: auditing). Mirrors Gardenfell's "live actors skipped".
ACTOR_TAGS: frozenset[bytes] = frozenset({b"NPC_", b"CREA"})

#: Meshes the editor places as pure markers -- never drawn in-game, so never in a
#: preview. Matched on the resolved mesh path (lower-cased, basename).
EDITOR_MARKER_MESHES: frozenset[str] = frozenset(
    {
        "editormarker.nif",
        "marker_error.nif",
        "marker_arrow.nif",
        "marker_north.nif",
        "marker_light.nif",
        "marker_travel.nif",
        "marker_temple.nif",
        "marker_prison.nif",
        "marker_divine.nif",
        "marker_map.nif",
        "marker_sound.nif",
        "marker_x.nif",
    }
)


# --------------------------------------------------------------------------- #
# reference transform
# --------------------------------------------------------------------------- #


def _rot_x(a: float) -> Transform:
    """Rotation of ``a`` radians about +X, as a rotation-only Transform."""
    c, s = math.cos(a), math.sin(a)
    return Transform(rotation=((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c)))


def _rot_y(a: float) -> Transform:
    """Rotation of ``a`` radians about +Y, as a rotation-only Transform."""
    c, s = math.cos(a), math.sin(a)
    return Transform(rotation=((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c)))


def _rot_z(a: float) -> Transform:
    """Rotation of ``a`` radians about +Z, as a rotation-only Transform."""
    c, s = math.cos(a), math.sin(a)
    return Transform(rotation=((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))


def reference_transform(
    translation: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: float | None,
) -> Transform:
    """A reference's world transform in the mesh (NIF) coordinate frame.

    Morrowind stores a reference rotation as three Euler angles in radians. This
    composes them as ``Rz(-z) . Ry(-y) . Rx(-x)`` -- negated angles, X applied
    first -- which is OpenMW's convention, then attaches the uniform scale and
    the translation. Composing three tested rotation Transforms rather than
    hand-writing the 3x3 keeps the one calibratable thing (order/sign) readable.

    Args:
        translation: The reference position ``(x, y, z)``.
        rotation: The Euler angles ``(rx, ry, rz)`` in radians.
        scale: The reference scale, or ``None`` for 1.0.

    Returns:
        A :class:`~wraithguard.nif.geometry.Transform`; ``apply`` it to a mesh's
        world-space vertices to place that object.
    """
    rx, ry, rz = rotation
    rot = _rot_z(-rz).then(_rot_y(-ry)).then(_rot_x(-rx))
    return Transform(
        rotation=rot.rotation,
        scale=scale if scale else 1.0,
        translation=(float(translation[0]), float(translation[1]), float(translation[2])),
    )


# --------------------------------------------------------------------------- #
# model index (id -> mesh) across the load order
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModelIndex:
    """The winning mesh per object id, and which ids are actors.

    Attributes:
        meshes: ``{id_lower: mesh_path}`` for every object that carries a mesh,
            the last plugin in load order winning.
        actor_ids: ``id_lower`` for ``NPC_``/``CREA`` records, whose references a
            preview skips even though they have a mesh.
    """

    meshes: dict[str, str]
    actor_ids: frozenset[str]


def build_model_index(records: Iterable[object]) -> ModelIndex:
    """Index object records to their winning mesh, and note the actors.

    Args:
        records: Every plugin's records, concatenated in load order (so a later
            record for the same id overwrites an earlier one).

    Returns:
        A :class:`ModelIndex`.
    """
    meshes: dict[str, str] = {}
    actor_ids: set[str] = set()
    for record in records:
        rid = getattr(record, "id", "")
        if not rid:
            continue
        key = rid.lower()
        if getattr(record, "TAG", b"") in ACTOR_TAGS:
            actor_ids.add(key)
            continue
        mesh = getattr(record, "mesh", "")
        if mesh:
            meshes[key] = mesh
    return ModelIndex(meshes=meshes, actor_ids=frozenset(actor_ids))


# --------------------------------------------------------------------------- #
# placements + audit
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Placement:
    """One object placed in the cell.

    Attributes:
        ref_id: The object id the reference names.
        model: The resolved mesh path, or ``""`` when there is none.
        transform: The world transform (see :func:`reference_transform`).
        kind: One of ``"placed"`` (has a mesh to draw), ``"no_mesh_record"`` (the
            id resolves to no object, or an object with no mesh), ``"editor_marker"``
            or ``"actor"``.
    """

    ref_id: str
    model: str
    transform: Transform
    kind: str


@dataclass
class CellAudit:
    """What the load order does to a cell -- the numbers behind the preview panel.

    Every count is a plain integer so the panel can print them directly.
    """

    references: int = 0  # distinct references after the merge
    placed: int = 0  # references with a mesh to draw
    no_mesh_record: int = 0  # references whose object has no mesh (or no record)
    editor_markers: int = 0
    actors_skipped: int = 0
    overridden_by_later: int = 0  # a ref an earlier plugin set that a later one re-set
    deleted_by_later: int = 0  # a ref a later plugin deleted
    moved: int = 0  # a ref carried a moved-cell marker


def _ref_key(ref: Reference, plugin_name: str, masters: Sequence[str]) -> tuple[str, int]:
    """The load-order-wide identity of a reference.

    A reference belongs to whichever file first created it: ``mast_index`` 0 is
    the plugin itself, 1..N index its master list. Pairing that file with the
    reference index gives a key that is stable as later plugins modify or delete
    the same reference.

    Args:
        ref: The reference.
        plugin_name: The plugin the reference was read from.
        masters: That plugin's master names, in order.

    Returns:
        ``(origin_file_lower, refr_index)``.
    """
    if ref.mast_index and 1 <= ref.mast_index <= len(masters):
        origin = masters[ref.mast_index - 1]
    else:
        origin = plugin_name
    return origin.lower(), ref.refr_index


@dataclass
class _PluginCell:
    """One plugin's contribution to a cell: its name, masters and cell record."""

    plugin_name: str
    masters: Sequence[str]
    cell: Cell | None


def _merge_references(layers: Sequence[_PluginCell]) -> tuple[list[Reference], CellAudit]:
    """Merge a cell's references across the plugins that touch it, in load order.

    Later plugins override, delete or add references keyed by :func:`_ref_key`.
    Returns the surviving references (in first-seen order) and the override/delete/
    move counts; mesh classification is filled in later by :func:`resolve_cell`.
    """
    audit = CellAudit()
    winning: dict[tuple[str, int], Reference] = {}
    order: list[tuple[str, int]] = []
    for layer in layers:
        if layer.cell is None:
            continue
        for ref in layer.cell.references:
            key = _ref_key(ref, layer.plugin_name, layer.masters)
            seen = key in winning
            if ref.deleted:
                if seen:
                    audit.deleted_by_later += 1
                    winning.pop(key, None)
                continue
            if seen:
                audit.overridden_by_later += 1
            else:
                order.append(key)
            if ref.moved_cell is not None:
                audit.moved += 1
            winning[key] = ref
    survivors = [winning[k] for k in order if k in winning]
    return survivors, audit


def resolve_cell(
    layers: Sequence[tuple[str, Sequence[str], Cell | None]],
    model_index: ModelIndex,
) -> tuple[list[Placement], CellAudit]:
    """Resolve every placement in a cell and audit what the load order did.

    Args:
        layers: One ``(plugin_name, masters, cell_record_or_None)`` per plugin, in
            load order -- the plugin's version of this cell (``None`` if it does
            not touch it). Merging these is how "overridden/deleted by later" is
            counted.
        model_index: The winning mesh per id (see :func:`build_model_index`).

    Returns:
        ``(placements, audit)`` -- the drawable and non-drawable placements in a
        stable order, and the filled-in :class:`CellAudit`.
    """
    survivors, audit = _merge_references([_PluginCell(n, m, c) for n, m, c in layers])
    placements: list[Placement] = []
    for ref in survivors:
        audit.references += 1
        key = ref.id.lower()
        transform = reference_transform(ref.translation, ref.rotation, ref.scale)
        if key in model_index.actor_ids:
            kind, model = "actor", ""
            audit.actors_skipped += 1
        else:
            model = model_index.meshes.get(key, "")
            if not model:
                kind = "no_mesh_record"
                audit.no_mesh_record += 1
            elif model.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower() in EDITOR_MARKER_MESHES:
                kind = "editor_marker"
                audit.editor_markers += 1
            else:
                kind = "placed"
                audit.placed += 1
        placements.append(Placement(ref_id=ref.id, model=model, transform=transform, kind=kind))
    return placements, audit
