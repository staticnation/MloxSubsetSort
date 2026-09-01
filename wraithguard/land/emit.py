"""Build the JSON that ``tes3conv`` turns into a merged landscape plugin.

This is the only part of the toolkit that produces a file the game will load,
so it is written to be inspectable rather than clever.

**We do not serialise TES3 ourselves.** Records are emitted as JSON in
``tes3conv``'s schema and ``tes3conv`` performs the binary encoding. That means
the byte-level format is handled by a tool the community already uses, and our
surface is a data structure a person can read. ``tools/check_plugin_roundtrip.py``
established that the conversion converges on real plugins, which is what makes
this route trustworthy.

**zstd is mandatory here, not optional.** ``tes3``'s deserialiser
(``libs/esp/src/features/serde.rs``) runs every base64 field through
``zstd::decode_all`` *unconditionally* -- there is no magic-number check and no
uncompressed fallback. Plain base64 is rejected with "esp deserialize
decompress error" rather than misread, which is the better failure, but it does
mean a merged plugin cannot be produced without a zstd backend: Python 3.14's
``compression.zstd`` or the ``zstandard`` package. ``pyproject.toml`` lists that
as an extra for *reading* script bytecode; for writing it is a requirement, and
:func:`encode_field` says so plainly when it is missing.

**What goes in the plugin**, following Merged Lands: a header, one ``LTEX``
record per land texture in the shared table, and one ``LAND`` record per merged
cell. ``CELL`` records are optional and off by default. References are never
read, compared or written, so the merged plugin carries terrain and nothing
else -- every object, NPC and script in the load order resolves exactly as
before.

**The masters list is the dangerous part.** A merged plugin must declare the
plugins its terrain came from, with the sizes the engine expects, or the game
resolves nothing correctly. That list is the caller's to supply because only
the caller knows the real load order; :func:`build_header` will not invent one.
"""

from __future__ import annotations

import base64
import struct
from typing import TYPE_CHECKING, Any, Final

from wraithguard.land.heights import (
    encode_vertex_heights,
    pack_vertex_normals,
    vertex_normals_from_heights,
)
from wraithguard.tes3fields.landscape import (
    LAND_NUM_VERTS,
    LAND_SIZE,
    NUM_TEXTURES,
    TEXTURE_SIZE,
    WNAM_HEIGHT_SCALE,
    WNAM_SIZE,
    WNAM_STEP,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from wraithguard.land.textures import KnownTexture

#: What tes3conv calls each record type.
HEADER_TYPE: Final = "Header"
LTEX_TYPE: Final = "LandscapeTexture"
LAND_TYPE: Final = "Landscape"

#: The header version every TES3 plugin carries.
PLUGIN_VERSION: Final = 1.3

#: The landscape flags a merged record declares. Heights and normals share one
#: bit; the others are named separately. Written as tes3conv writes them, since
#: that is what it parses back.
FLAG_HEIGHTS_AND_NORMALS: Final = "USES_VERTEX_HEIGHTS_AND_NORMALS"
FLAG_VERTEX_COLORS: Final = "USES_VERTEX_COLORS"
FLAG_TEXTURES: Final = "USES_TEXTURES"

#: There is no world-map flag. ``LandscapeFlags`` has exactly three bits
#: (0x1 heights+normals, 0x2 colours, 0x4 textures); tes3's
#: ``uses_world_map_data()`` is a *derived* predicate that returns true when any
#: of the three is set, and ``WNAM`` is written on that basis. Emitting an
#: invented ``USES_WORLD_MAP_DATA`` name is rejected outright with
#: "unrecognized named flag", which is how this was established.


class EmitError(Exception):
    """Raised when a merged plugin cannot be built."""


def _compress(raw: bytes) -> bytes:
    """Compress a subrecord payload with zstd.

    Args:
        raw: The uncompressed payload.

    Returns:
        A zstd frame.

    Raises:
        EmitError: If no zstd backend is available.
    """
    try:  # Python 3.14+ carries zstd in the standard library.
        from compression import zstd
    except ImportError:
        # Not an error: on an older interpreter the standard library simply has
        # no zstd, and the third-party backend below is the expected route.
        # Only the failure of *both* is worth reporting, which it is.
        pass
    else:
        return zstd.compress(raw)

    try:
        import zstandard
    except ImportError as exc:
        raise EmitError(
            "writing a merged plugin needs zstd, which is not available. "
            "tes3conv's reader decompresses every landscape field without "
            "checking for a zstd header, so uncompressed data is rejected "
            "outright -- there is no fallback to fall back to. Install the "
            "'zstandard' package (pip install zstandard), or run on Python "
            "3.14 or later where zstd is in the standard library."
        ) from exc
    return zstandard.ZstdCompressor().compress(raw)


def encode_field(raw: bytes) -> str:
    """Encode a subrecord payload the way tes3conv expects to read it.

    Args:
        raw: The uncompressed payload.

    Returns:
        Base64 text of a zstd frame.

    Raises:
        EmitError: If no zstd backend is available.
    """
    return base64.b64encode(_compress(raw)).decode("ascii")


def pack_world_map(rows: Sequence[Sequence[int]]) -> bytes:
    """Pack the 9x9 world-map grid.

    Args:
        rows: 9 rows of 9 signed values.

    Returns:
        The 81-byte payload.

    Raises:
        EmitError: If the grid is the wrong shape.
    """
    if len(rows) != WNAM_SIZE or any(len(row) != WNAM_SIZE for row in rows):
        raise EmitError(f"world map must be {WNAM_SIZE}x{WNAM_SIZE}")
    # Saturate to the signed byte the format stores. A merged value can land
    # just outside the range, and ``struct.pack("b", ...)`` raises on that --
    # struct.error, which is not caught in service._build_records, so one such
    # vertex would abort the whole merge. This is the i8-overflow the OpenMW
    # fork hit on large load orders; its RelativeTo::add now clamps for the same
    # reason. Clamping is also the correct answer over the original's wrapping
    # ``as i8`` cast, which would flip a high map value to a negative one.
    flat = [max(-128, min(127, int(value))) for row in rows for value in row]
    return struct.pack(f"<{WNAM_SIZE * WNAM_SIZE}b", *flat)


def world_map_from_heights(heights: Sequence[Sequence[float]]) -> list[list[int]]:
    """Derive the 9x9 world map from a cell's absolute heights.

    The world map is a downsample of the terrain: the engine and the
    Construction Set take every eighth height vertex and divide by
    :data:`WNAM_HEIGHT_SCALE`. We reproduce that here so a merged cell always
    has a map that matches its terrain.

    This exists because a merged cell can arrive with heights but no carried
    ``WNAM`` -- a source plugin edited the terrain without shipping a world map,
    or the reference had none to inherit. Writing zeros in that case paints the
    cell at sea level, which the world map renders as brown coast; an
    underwater cell then shows as land over open water. Deriving from the
    merged heights instead gives negative values for below-sea-level terrain,
    which render as water, and keeps the low-resolution map honest about what
    the full-resolution terrain actually is.

    Args:
        heights: 65 rows of 65 absolute heights in world units.

    Returns:
        A 9x9 grid of signed values, each clamped to the ``int8`` the format
        stores.
    """
    return [
        [
            max(-128, min(127, round(heights[gy * WNAM_STEP][gx * WNAM_STEP] / WNAM_HEIGHT_SCALE)))
            for gx in range(WNAM_SIZE)
        ]
        for gy in range(WNAM_SIZE)
    ]


def sink_underwater_land(
    world_map: Sequence[Sequence[int]], heights: Sequence[Sequence[float]]
) -> list[list[int]]:
    """Force any below-sea-level world-map vertex to read as water.

    The world map paints a vertex with a non-negative value as land, in a brown
    coast colour; only a negative value reads as water. So a vertex sampled from
    terrain that is below sea level must not carry a non-negative value, or it
    paints as a brown speck -- or, over a whole underwater cell, a brown square
    -- on open water. That happens two ways even with a faithful map: a carried
    map can disagree with the merged heights (a deep-water master against a mod's
    edit), and ``round(height / 128)`` rounds terrain in the top 64 units of
    water up to zero.

    The rule is one-directional: a sampled height below zero whose map value is
    at or above zero is dropped to ``-1``, the shallowest value that still reads
    as water. Nothing is ever pushed toward land, so real coastline and a mod's
    intentional deep-water map are both untouched.

    Args:
        world_map: The 9x9 map to correct.
        heights: 65 rows of 65 absolute heights, sampled the same way
            :func:`world_map_from_heights` samples them.

    Returns:
        A 9x9 grid, corrected where the terrain is underwater.
    """
    return [
        [
            (
                -1
                if heights[gy * WNAM_STEP][gx * WNAM_STEP] < 0 and world_map[gy][gx] >= 0
                else int(world_map[gy][gx])
            )
            for gx in range(WNAM_SIZE)
        ]
        for gy in range(WNAM_SIZE)
    ]


def pack_vertex_colors(rows: Sequence[Sequence[tuple[int, int, int]]]) -> bytes:
    """Pack the 65x65 vertex colour grid.

    Args:
        rows: 65 rows of 65 ``(r, g, b)`` triples.

    Returns:
        The 12,675-byte payload.

    Raises:
        EmitError: If the grid is the wrong shape.
    """
    if len(rows) != LAND_SIZE or any(len(row) != LAND_SIZE for row in rows):
        raise EmitError(f"vertex colors must be {LAND_SIZE}x{LAND_SIZE}")
    # Saturate each channel to a byte: ``bytes()`` raises on anything outside
    # 0-255, and a merged colour can land just outside. Same reasoning as
    # pack_world_map -- clamp rather than crash the merge on one vertex.
    return bytes(
        max(0, min(255, component)) for row in rows for triple in row for component in triple
    )


def pack_texture_indices(rows: Sequence[Sequence[int]]) -> bytes:
    """Pack the 16x16 texture grid, restoring the stored 4x4 block order.

    ``VTEX`` is not row-major on disk: the grid is written as sixteen 4x4
    blocks. :func:`~wraithguard.tes3fields.landscape.decode_texture_indices`
    undoes that on the way in, so it has to be redone on the way out. Skipping
    it produces a plugin that loads and paints every cell in a scrambled 4x4
    pattern -- a failure that looks like a merge bug rather than a packing one.

    Args:
        rows: 16 rows of 16 indices, in visual order.

    Returns:
        The 512-byte payload.

    Raises:
        EmitError: If the grid is the wrong shape.
    """
    if len(rows) != TEXTURE_SIZE or any(len(row) != TEXTURE_SIZE for row in rows):
        raise EmitError(f"texture indices must be {TEXTURE_SIZE}x{TEXTURE_SIZE}")
    flat = [0] * NUM_TEXTURES
    for position in range(NUM_TEXTURES):
        x2, y2 = position % 4, (position // 4) % 4
        x1, y1 = (position // 16) % 4, position // 64
        flat[position] = rows[y1 * 4 + y2][x1 * 4 + x2]
    return struct.pack(f"<{NUM_TEXTURES}H", *flat)


def build_header(
    masters: Sequence[tuple[str, int]],
    num_objects: int,
    description: str = "Merged landscape. Load last.",
    author: str = "Wraithguard Toolkit",
) -> dict[str, Any]:
    """Build the plugin header.

    Args:
        masters: Every master this plugin depends on, as ``(name, size in
            bytes)``, in load order. The engine resolves references through
            this list, so it must be right and it must be the caller's -- only
            the caller knows the real load order.
        num_objects: How many records follow the header.
        description: The description shown in launchers.
        author: The author field.

    Returns:
        A header record.

    Raises:
        EmitError: If no masters are given, or a size is missing.
    """
    if not masters:
        raise EmitError(
            "a merged landscape plugin must declare its masters. Without them "
            "the engine cannot resolve the terrain this plugin overrides, and "
            "the result loads but is wrong."
        )
    entries: list[list[Any]] = []
    for name, size in masters:
        if not name or size <= 0:
            raise EmitError(f"master {name!r} has no usable size ({size})")
        entries.append([name, int(size)])

    return {
        "type": HEADER_TYPE,
        "flags": "",
        "version": PLUGIN_VERSION,
        "file_type": "Esp",
        "author": author,
        "description": description,
        "num_objects": int(num_objects),
        "masters": entries,
    }


def build_texture_records(textures: Sequence[KnownTexture]) -> list[dict[str, Any]]:
    """Emit one ``LTEX`` record per land texture the merged plugin uses.

    The merged plugin renumbers textures into its own space, so it must carry
    the table those numbers index: a ``VTEX`` value in a merged cell is
    meaningless without the ``LTEX`` record that names it.

    Pass the *compacted* list from
    :func:`~wraithguard.land.textures.compact_textures`, not the whole shared
    table. The shared table accumulates every texture every surveyed plugin
    declares; a merged plugin paints with a fraction of them, and shipping the
    rest means ``LTEX`` records nothing references.

    Args:
        textures: The textures to emit, already in their final index order.

    Returns:
        The records, in index order.
    """
    records: list[dict[str, Any]] = []
    for texture in textures:
        record: dict[str, Any] = {
            "type": LTEX_TYPE,
            "flags": "",
            "id": texture.identifier,
            "index": texture.index,
        }
        if texture.file_name is not None:
            record["file_name"] = texture.file_name
        records.append(record)
    return records


#: Payload size of each landscape field, in bytes.
#:
#: One copy of the arithmetic. The empty-field branches below used to spell
#: each of these out again -- ``bytes(3 * LAND_NUM_VERTS)`` and so on -- which
#: is two statements of one fact about a wire format, and the kind that stays
#: wrong for a long time, because an empty field of the wrong length still
#: encodes, still writes, and only misreads in the game.
FIELD_SIZES: Final[dict[str, int]] = {
    "vertex_heights": LAND_NUM_VERTS,
    "vertex_normals": 3 * LAND_NUM_VERTS,
    "world_map_data": WNAM_SIZE * WNAM_SIZE,
    "vertex_colors": 3 * LAND_NUM_VERTS,
    "texture_indices": 2 * NUM_TEXTURES,
}


def build_landscape_record(
    coords: tuple[int, int],
    heights: Sequence[Sequence[float]] | None = None,
    normals: Sequence[Sequence[tuple[int, int, int]]] | None = None,
    world_map: Sequence[Sequence[int]] | None = None,
    colors: Sequence[Sequence[tuple[int, int, int]]] | None = None,
    textures: Sequence[Sequence[int]] | None = None,
) -> tuple[dict[str, Any], list[tuple[int, int]]]:
    """Build one merged ``LAND`` record.

    Normals are recomputed from the merged heights when heights are supplied
    and normals are not. That is not a convenience: the engine lights terrain
    from its normals, so a cell whose heights moved but whose normals did not
    is lit as though the old terrain were still there. It looks like a
    rendering bug and is really a merge bug.

    Args:
        coords: The cell's exterior grid coordinates.
        heights: 65 rows of 65 absolute heights in world units.
        normals: 65 rows of 65 signed-byte triples, or ``None`` to recompute.
        world_map: 9 rows of 9 signed values.
        colors: 65 rows of 65 ``(r, g, b)`` triples.
        textures: 16 rows of 16 shared texture indices, in visual order.

    Returns:
        The record, and the vertices whose height gradient had to be clamped
        because the format cannot express them. An empty list means the cell
        round-trips exactly.

    Raises:
        EmitError: If a grid is the wrong shape, or no layer was supplied.
        HeightEncodeError: If the height grid is not 65x65.
    """
    if heights is None and textures is None and colors is None:
        raise EmitError(
            f"cell {coords} was given no layers to write. An empty LAND record "
            "would replace whatever the load order had there with nothing."
        )

    record: dict[str, Any] = {
        "type": LAND_TYPE,
        "flags": "",
        "grid": [int(coords[0]), int(coords[1])],
    }
    declared: list[str] = []
    clamped: list[tuple[int, int]] = []

    # Every grid is written, present or not. tes3 declares all five as plain
    # fields rather than Options, so its deserialiser *requires* each one --
    # omitting world_map_data is rejected with "missing field
    # `world_map_data`", which is how this was found. A layer with no data is
    # therefore written as zeros and simply not declared in the flags: the
    # engine reads the flags to decide what to use, so an undeclared zero grid
    # is inert rather than a black cell.
    if heights is not None:
        rows = [list(row) for row in heights]
        offset, payload, clamped = encode_vertex_heights(rows)
        # payload is the whole VHGT body: the 4-byte offset, the height bytes,
        # then 3 bytes of subrecord padding. The ``data`` field is the heights
        # alone -- tes3conv truncates a longer value to its fixed array, but the
        # native writer takes it literally, so include only the heights.
        heights_data = payload[4 : 4 + FIELD_SIZES["vertex_heights"]]
        record["vertex_heights"] = {"offset": offset, "data": encode_field(heights_data)}
        computed = normals if normals is not None else vertex_normals_from_heights(rows)
        record["vertex_normals"] = {
            "data": encode_field(pack_vertex_normals([list(row) for row in computed]))
        }
        declared.append(FLAG_HEIGHTS_AND_NORMALS)
    else:
        record["vertex_heights"] = {
            "offset": 0.0,
            "data": encode_field(bytes(FIELD_SIZES["vertex_heights"])),
        }
        record["vertex_normals"] = {"data": encode_field(bytes(FIELD_SIZES["vertex_normals"]))}

    # The world map (WNAM). Two corrections, both aimed at one symptom -- brown
    # land squares painted over open water -- and both driven by the merged
    # heights, which are the ground truth a low-resolution map should agree with.
    #
    # 1. A carried map that is *all zeros* is the format's missing-value
    #    sentinel: a source that edited heights without regenerating its map
    #    exports it, and the merge carries the zeros. There is nothing to
    #    preserve, so it is derived from the heights instead. An absent map
    #    (``None``) is derived the same way.
    #
    # 2. Whatever map is then in hand -- carried or derived -- is made
    #    *water-safe*: any sample vertex whose terrain sits below sea level is
    #    forced to read as water. Zero paints as brown coast, and
    #    ``round(height / 128)`` rounds terrain in the top 64 units of water up
    #    to zero, so an underwater vertex could still paint land even from a
    #    faithful downsample. This is what showed up wherever a deep-water master
    #    (distant_seafloor) met a merged cell. The correction only ever pushes a
    #    vertex toward water, never toward land, so real coastline is untouched
    #    and a mod's intentional deep-water (-128) map is left alone.
    if heights is not None and (world_map is None or not any(v for row in world_map for v in row)):
        world_map = world_map_from_heights([list(row) for row in heights])
    if heights is not None and world_map is not None:
        world_map = sink_underwater_land(world_map, [list(row) for row in heights])
    if world_map is not None:
        record["world_map_data"] = {"data": encode_field(pack_world_map(world_map))}
    else:
        record["world_map_data"] = {"data": encode_field(bytes(FIELD_SIZES["world_map_data"]))}

    if colors is not None:
        record["vertex_colors"] = {"data": encode_field(pack_vertex_colors(colors))}
        declared.append(FLAG_VERTEX_COLORS)
    else:
        record["vertex_colors"] = {"data": encode_field(bytes(FIELD_SIZES["vertex_colors"]))}

    if textures is not None:
        record["texture_indices"] = {"data": encode_field(pack_texture_indices(textures))}
        declared.append(FLAG_TEXTURES)
    else:
        record["texture_indices"] = {"data": encode_field(bytes(FIELD_SIZES["texture_indices"]))}

    record["landscape_flags"] = " | ".join(declared)
    return record, clamped


def attach_texture_indices(record: dict[str, Any], rows: Sequence[Sequence[int]]) -> None:
    """Add a texture grid to an already-built ``LAND`` record.

    Texture indices cannot be encoded until every merged cell is known, because
    the shared table is compacted against the set of textures the *whole* merge
    actually paints with. So heights are written in one pass and textures in a
    second, and this is the seam between them.

    The landscape flags are updated at the same time. Writing the payload
    without declaring ``USES_TEXTURES`` produces a record the engine reads as
    having no texture data while carrying 512 bytes of it -- a discrepancy that
    would not raise anywhere.

    Args:
        record: A record from :func:`build_landscape_record`.
        rows: 16 rows of 16 compacted texture indices, in visual order.

    Raises:
        EmitError: If the grid is the wrong shape.
    """
    record["texture_indices"] = {"data": encode_field(pack_texture_indices(rows))}
    declared = [part for part in str(record.get("landscape_flags", "")).split("|") if part.strip()]
    names = [part.strip() for part in declared]
    if FLAG_TEXTURES not in names:
        names.append(FLAG_TEXTURES)
    record["landscape_flags"] = " | ".join(names)


def build_plugin(
    records: Sequence[dict[str, Any]],
    masters: Sequence[tuple[str, int]],
    description: str = "Merged landscape. Load last.",
) -> list[dict[str, Any]]:
    """Assemble a complete plugin document.

    Args:
        records: Every ``LTEX`` and ``LAND`` record, in the order to write.
        masters: The masters, as ``(name, size)``.
        description: The plugin description.

    Returns:
        A record list ready to serialise as JSON and hand to ``tes3conv``.

    Raises:
        EmitError: If the masters are unusable.
    """
    header = build_header(masters, num_objects=len(records), description=description)
    return [header, *records]
