"""Turning a parsed NIF into triangles positioned in world space.

:mod:`wraithguard.nif.reader` reads blocks; it does not assemble them. A mesh
is a *tree*: every shape carries its own transform, every node above it carries
another, and a shape's vertices mean nothing until those are composed down the
chain. A viewer that skipped that would draw every part of a mesh piled on the
origin.

This module does the composition and nothing else. It takes a file parsed with
``geometry=True`` and returns flat triangle soup with world-space coordinates,
which is the form both a renderer and a bounding-box calculation want.

**Roots are found, not assumed.** Block 0 is conventionally the root, and
conventionally is not good enough for files written by twenty years of
exporters. A root here is any node no other node claims as a child, which is
derived from the child links rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from wraithguard.logging_setup import get_logger
from wraithguard.nif.report import COLLISION_NODES as COLLISION_HINT, normalise_texture

if TYPE_CHECKING:
    from wraithguard.nif.reader import Block, NifFile

LOG = get_logger(__name__)

#: Block types that hold children and therefore continue the walk.
_NODE_TYPES: frozenset[str] = frozenset(
    {
        "NiNode",
        "RootCollisionNode",
        "AvoidNode",
        "NiBSParticleNode",
        "NiBSAnimationNode",
        "NiBillboardNode",
        "NiSwitchNode",
        "NiLODNode",
    }
)

#: Block types that carry drawable geometry.
_SHAPE_TYPES: frozenset[str] = frozenset({"NiTriShape"})

#: How deep the walk may go before it concludes the graph has a cycle. NIF
#: children are links by index and nothing in the format forbids a loop, so a
#: hostile or broken file could otherwise spin forever.
_MAX_DEPTH: int = 64


@dataclass(frozen=True, slots=True)
class Transform:
    """A rotation, a uniform scale and a translation, applied in that order.

    Attributes:
        rotation: Three rows of three, as the file stores it.
        scale: Uniform scale factor.
        translation: Offset applied after rotating and scaling.
    """

    rotation: tuple[tuple[float, float, float], ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    scale: float = 1.0
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def apply(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        """Move one point into this transform's frame.

        Args:
            point: The point to move.

        Returns:
            The transformed point.
        """
        x, y, z = point
        rows = self.rotation
        return (
            (rows[0][0] * x + rows[0][1] * y + rows[0][2] * z) * self.scale + self.translation[0],
            (rows[1][0] * x + rows[1][1] * y + rows[1][2] * z) * self.scale + self.translation[1],
            (rows[2][0] * x + rows[2][1] * y + rows[2][2] * z) * self.scale + self.translation[2],
        )

    def then(self, child: Transform) -> Transform:
        """Compose a child's transform with this one.

        The child's rotation and scale are expressed in *this* frame, so the
        child's translation has to be carried through this transform rather
        than simply added -- adding them is the mistake that leaves a rotated
        parent's children in the wrong place.

        Args:
            child: The transform below this one in the tree.

        Returns:
            The combined transform.
        """
        rows = self.rotation
        other = child.rotation
        combined: tuple[tuple[float, float, float], ...] = tuple(
            (
                sum(rows[i][k] * other[k][0] for k in range(3)),
                sum(rows[i][k] * other[k][1] for k in range(3)),
                sum(rows[i][k] * other[k][2] for k in range(3)),
            )
            for i in range(3)
        )
        return Transform(
            rotation=combined,
            scale=self.scale * child.scale,
            translation=self.apply(child.translation),
        )


#: Bit in a ``NiAlphaProperty``'s flags that enables blending. Taken from
#: ``tes3``'s ``flag_props!`` block, which spells the same masks out.
_ALPHA_BLEND_MASK: Final[int] = 0x0001

#: Bit that enables alpha testing -- a cutout rather than a fade. Independent
#: of blending, and the distinction matters: foliage sets this one alone.
_ALPHA_TEST_MASK: Final[int] = 0x0200


@dataclass(frozen=True, slots=True)
class Mesh:
    """One drawable shape, in world coordinates.

    Attributes:
        name: The shape's name, as the exporter wrote it.
        vertices: World-space positions.
        triangles: Index triples into :attr:`vertices`.
        uvs: Texture coordinates, one per vertex, empty when the shape has
            none. Only the first UV set is kept: Morrowind draws from it, and
            the rest exist for tools rather than for the game.
        texture: The base texture path, normalised, or ``""`` when untextured.
        glow: The self-illumination texture path, normalised, or ``""`` when
            the shape has none. Distinct from the normal and specular maps a
            viewer finds by filename: this one is a real NIF texture slot,
            the same way ``texture`` is.
        dark: The dark-map texture path, normalised, or ``""`` when the shape
            has none. Multiplied into the base color; the closest built-in
            equivalent a renderer has is an ambient-occlusion map.
        decals: Every decal texture path the shape names, normalised, in slot
            order -- ``decal_0`` first. Empty when it has none.

            A list rather than a single path because the format genuinely
            allows several and vanilla content uses them: ``7decals.NIF`` is
            named for the fact. The reader has always parsed past the first
            (see :func:`~wraithguard.nif.reader._slot_name`); only this layer
            was throwing the rest away.

            Order is load-bearing. Decals composite over one another, so
            slot order is paint order, and reversing it puts the wrong one
            on top.
        detail: The detail-map texture path, normalised, or ``""`` when the
            shape has none. Also multiplied into the base color, at a 2x UV
            tile -- a second multiply alongside ``dark``, not a substitute for
            it.
        gloss: The gloss-map texture path, normalised, or ``""`` when the
            shape has none. A single-channel specular *mask* -- brighter is
            shinier -- and distinct from the specular *map* an OpenMW-style
            ``_spec`` sibling provides, which carries color.
        bump: The bump-slot texture path, normalised, or ``""`` when the shape
            has none. What this means is convention-dependent and this module
            does not decide it: vanilla Morrowind ignores the slot outright,
            while MGE-XE and NifSkope repurpose it to carry tangent-space
            normals. See :func:`~wraithguard.images.roles.classify`.
        vertex_colors: One RGBA tuple per vertex, each channel 0-1, empty when
            the shape has none *or* when the count does not match the vertex
            count. That is the same all-or-nothing rule :attr:`uvs` uses and
            for the same reason: a partial set makes a renderer index past the
            end of an attribute and draw nothing, which is worse than drawing
            it uncoloured.
        diffuse: The material's diffuse color, or ``None`` when the shape has
            no ``NiMaterialProperty``. Multiplies with the base texture.
            ``None`` means *undescribed*, not *black* -- a caller with neither
            texture nor color must fall back to white, or every unmaterialed
            shape renders as a silhouette.
        emissive: The material's emissive color, or ``None`` when there is no
            material property. Combines with :attr:`glow` the way
            :attr:`diffuse` combines with :attr:`texture` -- multiplied -- so
            a renderer that already multiplies an emissive map by an emissive
            color is correct by construction, with no glow-specific case.
        opacity: The material's own alpha, ``1.0`` when there is no material
            property. Independent of :attr:`alpha_blend`: this is *how*
            transparent, blending is *whether the renderer looks at all*.
        alpha_blend: Whether the shape's ``NiAlphaProperty`` enables blending.
            ``False`` when the shape has no such property, which reads
            correctly: nothing describing transparency means opaque.
        alpha_test: Whether that property enables alpha testing -- a cutout,
            not a fade. Independent of :attr:`alpha_blend`; foliage commonly
            sets this and not that.
        alpha_threshold: The cutout reference, normalised to 0-1 from the
            byte the file stores. Only meaningful when :attr:`alpha_test`.
    """

    name: str
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    texture: str = ""
    glow: str = ""
    dark: str = ""
    decals: list[str] = field(default_factory=list)
    detail: str = ""
    gloss: str = ""
    bump: str = ""
    collision: bool = False
    vertex_colors: list[tuple[float, float, float, float]] = field(default_factory=list)
    diffuse: tuple[float, float, float] | None = None
    emissive: tuple[float, float, float] | None = None
    opacity: float = 1.0
    alpha_blend: bool = False
    alpha_test: bool = False
    alpha_threshold: float = 0.0


def _transform_of(block: Block) -> Transform:
    """Read a block's own transform.

    Args:
        block: A scene object.

    Returns:
        Its transform, defaulting to identity when the fields are absent --
        which happens for a block read without ``geometry=True``.
    """
    rotation = block.fields.get("rotation_m3")
    translation = block.fields.get("translation_xyz")
    scale = block.fields.get("scale", 1.0)
    rows: tuple[tuple[float, float, float], ...] = (
        tuple((float(r[0]), float(r[1]), float(r[2])) for r in rotation)
        if rotation
        else Transform().rotation
    )
    return Transform(
        rotation=rows,
        scale=float(scale) if isinstance(scale, (int, float)) else 1.0,
        translation=(
            (float(translation[0]), float(translation[1]), float(translation[2]))
            if translation
            else (0.0, 0.0, 0.0)
        ),
    )


def find_roots(parsed: NifFile) -> list[int]:
    """Find the blocks that nothing claims as a child.

    Args:
        parsed: A parsed file.

    Returns:
        Root block indices, in file order.
    """
    claimed: set[int] = set()
    for block in parsed.blocks:
        for child in block.fields.get("children_links") or []:
            if child >= 0:
                claimed.add(int(child))
    return [b.index for b in parsed.blocks if b.index not in claimed]


def world_meshes(parsed: NifFile) -> list[Mesh]:
    """Collect every drawable shape, with vertices in world space.

    Args:
        parsed: A file parsed with ``geometry=True``. Parsed without it the
            vertex arrays are absent and the result is empty, which is honest:
            the data was never read.

    Returns:
        One :class:`Mesh` per shape that has geometry.
    """
    by_index = {block.index: block for block in parsed.blocks}
    meshes: list[Mesh] = []
    seen: set[int] = set()

    def walk(index: int, parent: Transform, depth: int, collision: bool) -> None:
        """Visit one block and its children, accumulating world transforms.

        A NIF is a graph rather than a tree: a block can be referenced twice,
        and a malformed file can reference itself. ``seen`` and ``_MAX_DEPTH``
        are what stop this recursing forever on a file that is merely wrong
        rather than malicious.

        Args:
            index: The block to visit.
            parent: The accumulated transform of everything above it.
            depth: How far down the graph this is, for the depth guard.
            collision: Whether an ancestor marked this branch as collision
                geometry, which is not drawn.
        """
        block = by_index.get(index)
        if block is None or depth > _MAX_DEPTH or index in seen:
            if depth > _MAX_DEPTH:
                LOG.warning("scene graph deeper than %d at block %d; stopping", _MAX_DEPTH, index)
            return
        seen.add(index)
        here = parent.then(_transform_of(block))
        # RootCollisionNode marks everything under it as physics-only geometry
        # -- never drawn by the game, whatever a NiTriShape inside it looks
        # like. Once set, it stays set for the rest of the branch: a shape
        # three levels under a collision node is still collision geometry.
        collision = collision or block.type_name in COLLISION_HINT
        if block.type_name in _SHAPE_TYPES:
            mesh = _shape_to_mesh(block, here, by_index, collision)
            if mesh is not None:
                meshes.append(mesh)
        if block.type_name in _NODE_TYPES:
            for child in block.fields.get("children_links") or []:
                if child >= 0:
                    walk(int(child), here, depth + 1, collision)

    for root in find_roots(parsed):
        walk(root, Transform(), 0, False)
    return meshes


def _shape_to_mesh(
    block: Block, world: Transform, by_index: dict[int, Block], collision: bool = False
) -> Mesh | None:
    """Build one mesh from a shape and its data block.

    Args:
        block: The ``NiTriShape``.
        world: Its composed world transform.
        by_index: Every parsed block, by index.
        collision: Whether this shape sits under a ``RootCollisionNode``.

    Returns:
        The mesh, or ``None`` when its data block was not reached or carries no
        retained vertices.
    """
    data = by_index.get(block.link("data"))
    if data is None:
        return None
    vertices = data.fields.get("vertices_xyz")
    triangles = data.fields.get("triangles_indices")
    if not vertices or not triangles:
        return None
    # A shape can declare several UV sets. Only the first is Morrowind's, and
    # taking more would mean guessing which one a texture belongs to.
    uvs = (data.fields.get("uv_sets_uv") or [])[: len(vertices)]
    diffuse, emissive, opacity = _material(block, by_index)
    blend, test, threshold = _alpha(block, by_index)
    return Mesh(
        name=_name_of(block),
        vertices=[world.apply(v) for v in vertices],
        triangles=list(triangles),
        uvs=[(float(u), float(v)) for u, v in uvs],
        texture=_texture_slot(block, by_index, "base"),
        glow=_texture_slot(block, by_index, "glow"),
        dark=_texture_slot(block, by_index, "dark"),
        decals=_decal_slots(block, by_index),
        detail=_texture_slot(block, by_index, "detail"),
        gloss=_texture_slot(block, by_index, "gloss"),
        bump=_texture_slot(block, by_index, "bump"),
        collision=collision,
        vertex_colors=_vertex_colors(data, len(vertices)),
        diffuse=diffuse,
        emissive=emissive,
        opacity=opacity,
        alpha_blend=blend,
        alpha_test=test,
        alpha_threshold=threshold,
    )


def _vertex_colors(data: Block, vertices: int) -> list[tuple[float, float, float, float]]:
    """Read a shape's per-vertex colors, if it has a usable set.

    Reads ``vertex_colors_rgba`` -- the *decoded* companion -- rather than
    ``vertex_colors``, which holds only the count the array gate produced. An
    earlier draft of this read the latter, found an integer where it expected a
    list, and silently produced no colors at all: the field existed and held a
    number, so nothing looked wrong.

    Args:
        data: The shape's geometry data block.
        vertices: How many vertices the shape has.

    Returns:
        One RGBA tuple per vertex, or empty when there are none or the count
        disagrees with the vertex count.
    """
    colors = data.fields.get("vertex_colors_rgba")
    if not isinstance(colors, list) or len(colors) != vertices:
        return []
    return [(float(r), float(g), float(b), float(a)) for r, g, b, a in colors]


def _material(
    block: Block, by_index: dict[int, Block]
) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None, float]:
    """Read a shape's ``NiMaterialProperty``.

    The layout is confirmed against Greatness7's ``tes3`` (MIT): ambient,
    diffuse, specular and emissive colors, then shine, then alpha. Only the
    three a renderer here needs are returned; specular and shine are read past
    rather than dropped by guessing at their width.

    Args:
        block: The shape.
        by_index: Every parsed block, by index.

    Returns:
        Diffuse color, emissive color and opacity. The colors are ``None`` when
        the shape has no material property -- *undescribed*, not black -- and
        opacity is ``1.0``.
    """
    for link in block.fields.get("properties_links") or []:
        prop = by_index.get(int(link))
        if prop is None or prop.type_name != "NiMaterialProperty":
            continue
        diffuse = prop.fields.get("diffuse_xyz")
        emissive = prop.fields.get("emissive_xyz")
        alpha = prop.fields.get("alpha", 1.0)
        return (
            _triple(diffuse),
            _triple(emissive),
            float(alpha) if isinstance(alpha, (int, float)) else 1.0,
        )
    return (None, None, 1.0)


def _triple(value: object) -> tuple[float, float, float] | None:
    """Coerce a retained vector to a color, or ``None``.

    Args:
        value: The decoded ``vector3``, or whatever was in its place.

    Returns:
        Three floats, or ``None`` when the field was absent.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _alpha(block: Block, by_index: dict[int, Block]) -> tuple[bool, bool, float]:
    """Read a shape's ``NiAlphaProperty``.

    Blending and testing live in the *property flags*, not in a field of their
    own -- confirmed against ``tes3``, which exposes them as masks over the
    same ``u16``: blending at ``0x0001`` and testing at ``0x0200``. The byte
    after the flags is the test reference.

    The two are independent, and conflating them is the mistake worth avoiding:
    foliage routinely sets testing without blending, and treating "has an alpha
    property" as "is translucent" makes every leaf in the game fade.

    Args:
        block: The shape.
        by_index: Every parsed block, by index.

    Returns:
        Whether blending is on, whether testing is on, and the test reference
        normalised to 0-1.
    """
    for link in block.fields.get("properties_links") or []:
        prop = by_index.get(int(link))
        if prop is None or prop.type_name != "NiAlphaProperty":
            continue
        flags = prop.fields.get("flags", 0)
        flags = int(flags) if isinstance(flags, int) else 0
        threshold = prop.fields.get("threshold", 0)
        threshold = int(threshold) if isinstance(threshold, int) else 0
        return (
            bool(flags & _ALPHA_BLEND_MASK),
            bool(flags & _ALPHA_TEST_MASK),
            threshold / 255.0,
        )
    return (False, False, 0.0)


def _decal_slots(block: Block, by_index: dict[int, Block]) -> list[str]:
    """Collect every decal a shape names, in slot order.

    The reader numbers them ``decal_0``, ``decal_1`` and upward for as many as
    the property declares, so this walks until one is missing rather than
    stopping at a fixed count -- the format sets no limit, and ``7decals.NIF``
    is in the vanilla corpus specifically because it exceeds any guess.

    Stops at the first gap rather than scanning to some ceiling: the slots are
    written consecutively, so a missing one means the end. Scanning past it
    would be looking for something the format cannot produce.

    Args:
        block: The shape.
        by_index: Every parsed block, by index.

    Returns:
        The decal texture paths, first slot first. Empty when there are none.
    """
    found: list[str] = []
    index = 0
    while True:
        path = _texture_slot(block, by_index, f"decal_{index}")
        if not path:
            return found
        found.append(path)
        index += 1


def _texture_slot(block: Block, by_index: dict[int, Block], slot: str) -> str:
    """Find the texture a shape's ``NiTexturingProperty`` names in one slot.

    Args:
        block: The shape.
        by_index: Every parsed block, by index.
        slot: Which slot to read, matching the names
            :func:`~wraithguard.nif.reader._slot_name` assigns -- ``"base"``
            for the diffuse texture, ``"glow"`` for the self-illumination map,
            ``"dark"``, ``"decal_0"``, and so on. All of these are real NIF
            texture slots the shape names directly, unlike the normal and
            specular maps a viewer finds by filename convention instead.

    Returns:
        The normalised texture path, or ``""`` when the shape has no
        ``NiTexturingProperty``, or that property has nothing in this slot.
    """
    for prop_index in block.fields.get("properties_links") or []:
        prop = by_index.get(int(prop_index))
        if prop is None or prop.type_name != "NiTexturingProperty":
            continue
        slots = prop.fields.get("textures")
        # A parsed NiTexturingProperty always carries its texture_slots as a dict;
        # this guards a property whose field never parsed (a truncated block), which
        # the geometry path never reaches, so the guarded skip is dead.
        if not isinstance(slots, dict):  # pragma: no cover
            continue  # pragma: no cover
        source = by_index.get(int(slots.get(slot, -1)))
        if source is None:
            continue
        reference = source.fields.get("external_or_internal")
        if isinstance(reference, str) and reference.strip():
            return normalise_texture(reference)
    return ""


@dataclass(frozen=True, slots=True)
class TreeNode:
    """One entry in a mesh's block hierarchy.

    Attributes:
        index: The block index, so a reader can match it against a survey.
        type_name: The block's type.
        name: Its name, empty for blocks that have none.
        note: A short summary of what it carries, when that is worth saying.
        children: Nested entries.
    """

    index: int
    type_name: str
    name: str = ""
    note: str = ""
    children: list[TreeNode] = field(default_factory=list)


#: Blocks worth listing under their parent even though they are not children in
#: the scene-graph sense. These are exactly the things the 3D view cannot show:
#: a property is not geometry, a controller draws nothing, and a collision node
#: is invisible in a render but decides whether you can walk through the thing.
_ATTACHMENT_LINKS: tuple[str, ...] = ("properties_links", "data", "controller", "skin_instance")


def _name_of(block: Block) -> str:
    """A block's name, or empty when it has none.

    Args:
        block: Any block.

    Returns:
        The name as text.
    """
    name = block.fields.get("name")
    return name if isinstance(name, str) else ""


def block_tree(parsed: NifFile) -> list[TreeNode]:
    """Describe a file's block hierarchy.

    A structural companion to :func:`world_meshes`: that returns what can be
    drawn, this returns everything, including the blocks that never appear in
    a render and are often the reason a conflict matters.

    Args:
        parsed: A parsed file. Child links need ``geometry=True``; without them
            the result is a flat list of roots, which is honest rather than
            wrong.

    Returns:
        One entry per root, with children nested.
    """
    by_index = {block.index: block for block in parsed.blocks}
    seen: set[int] = set()

    def describe(block: Block) -> str:
        """A short summary of what a block holds, for its tree row.

        Args:
            block: The block to describe.

        Returns:
            A few words, or an empty string for block types with nothing worth
            summarising -- the row still shows the type name.
        """
        if block.type_name == "NiTriShapeData":
            return f"{block.fields.get('num_vertices', 0)} verts, {block.fields.get('num_triangles', 0)} tris"
        if block.type_name == "NiSourceTexture":
            reference = block.fields.get("external_or_internal")
            return normalise_texture(reference) if isinstance(reference, str) else "embedded"
        if block.type_name in COLLISION_HINT:
            return "collision"
        if block.type_name.endswith("Controller"):
            return "animated"
        if block.type_name == "NiSkinInstance":
            return "skinned"
        return ""

    def build(index: int, depth: int) -> TreeNode | None:
        """Build the tree node for one block and everything under it.

        Args:
            index: The block to build from.
            depth: How far down the graph this is, for the depth guard.

        Returns:
            The node, or ``None`` when the block is missing, already placed, or
            deeper than the guard allows.
        """
        block = by_index.get(index)
        if block is None or index in seen or depth > _MAX_DEPTH:
            return None
        seen.add(index)
        node = TreeNode(
            index=block.index,
            type_name=block.type_name,
            name=_name_of(block),
            note=describe(block),
        )
        for key in _ATTACHMENT_LINKS:
            value = block.fields.get(key)
            targets = value if isinstance(value, list) else [value]
            for target in targets:
                if isinstance(target, int) and target >= 0:
                    child = build(int(target), depth + 1)
                    if child is not None:
                        node.children.append(child)
        for child_index in block.fields.get("children_links") or []:
            if child_index >= 0:
                child = build(int(child_index), depth + 1)
                if child is not None:
                    node.children.append(child)
        return node

    roots = [build(index, 0) for index in find_roots(parsed)]
    trees = [node for node in roots if node is not None]
    # Anything the walk never reached is still in the file and still worth
    # showing -- an orphaned block is exactly the sort of thing a person
    # opening this view wants to know about.
    trees.extend(
        TreeNode(b.index, b.type_name, _name_of(b), describe(b))
        for b in parsed.blocks
        if b.index not in seen
    )
    return trees


def bounds(meshes: list[Mesh]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Axis-aligned bounds of a whole mesh set.

    A viewer needs this before it can frame anything, and it is cheap once the
    vertices are already in world space.

    Args:
        meshes: The meshes to measure.

    Returns:
        Minimum and maximum corners. Both are the origin when there is nothing
        to measure, so a caller never has to special-case an empty file.
    """
    points = [v for mesh in meshes for v in mesh.vertices]
    if not points:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return (
        (min(p[0] for p in points), min(p[1] for p in points), min(p[2] for p in points)),
        (max(p[0] for p in points), max(p[1] for p in points), max(p[2] for p in points)),
    )
