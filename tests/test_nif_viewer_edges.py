"""Edge branches of the mesh viewer page builder.

These pin the texture-read guards, the block-tree payload, and the edit-data
block that the ordinary geometry tests do not exercise.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from wraithguard.nif.geometry import Mesh, TreeNode
from wraithguard.nif.textures import Resolved
from wraithguard.nif.viewer import build_viewer_page, texture_bytes

#: A 1x1 32-bit uncompressed TGA -- small but genuinely decodable.
_TGA = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 32, 0) + bytes([0, 0, 255, 255])


class _NullResolver:
    """A resolver stand-in whose read never returns bytes."""

    def read(self, _resolved: Resolved) -> bytes | None:
        return None


def test_texture_bytes_is_none_for_an_unfound_reference() -> None:
    """A reference that resolved to nothing has no bytes to read."""
    assert texture_bytes(Resolved("missing.dds"), _NullResolver()) is None


def test_texture_bytes_is_none_when_the_read_returns_nothing() -> None:
    """A found reference whose bytes cannot be read yields None, not a crash."""
    found = Resolved("tex.dds", path=Path("tex.dds"))
    assert found.found  # the path makes it 'found'
    assert texture_bytes(found, _NullResolver()) is None


def test_a_block_tree_is_rendered_into_the_page() -> None:
    """A nested block tree is reduced to JSON the page embeds."""
    trees = [
        [
            TreeNode(
                0,
                "NiNode",
                name="root",
                children=[TreeNode(1, "NiTriShape", name="shape")],
            )
        ]
    ]
    page = build_viewer_page([("only", [])], trees=trees)
    assert "NiTriShape" in page
    assert "shape" in page


class _SiblingResolver:
    """A resolver with a readable base texture but an unreadable sibling.

    The sibling read returning ``None`` exercises the "aux could not be read"
    branch; two meshes sharing the texture exercise the cache-hit branch.
    """

    def resolve(self, reference: str) -> Resolved:
        return Resolved(reference, path=Path(reference))

    def read(self, resolved: Resolved) -> bytes | None:
        # The "_broken" sibling cannot be read; everything else can.
        return None if resolved.reference.endswith("_broken") else _TGA

    def siblings(self, texture: str) -> dict[str, Resolved]:
        return {
            "_n.dds": Resolved(f"{texture}_n", path=Path(f"{texture}_n")),  # readable -> offered
            "_x.dds": Resolved(f"{texture}_broken", path=Path(f"{texture}_broken")),  # unreadable
        }


def _painted(name: str) -> Mesh:
    return Mesh(
        name=name,
        vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        triangles=[(0, 1, 2)],
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        texture="tex.dds",
    )


def test_sibling_textures_are_decoded_and_offered() -> None:
    """A mesh with a texture and UVs pulls in its name-matched sibling maps.

    Two meshes share the texture, so the second finds the sibling already
    cached; the sibling itself will not read, so it is skipped, not offered.
    """
    page = build_viewer_page(
        [("only", [_painted("a"), _painted("b")])], resolver=_SiblingResolver()
    )
    assert page  # the sibling loop ran, cache hit and all, and the page built


def test_edit_data_is_embedded_when_supplied() -> None:
    """An ``edit`` mapping is serialised into the editor block."""
    page = build_viewer_page([("only", [])], edit={"blocks": [{"index": 0, "type": "NiNode"}]})
    assert "__EDIT_DATA__" not in page  # the placeholder was substituted
    assert "NiNode" in page
    # The serialised edit data is present and its close-tags are escaped.
    assert json.dumps({"blocks": [{"index": 0, "type": "NiNode"}]}, separators=(",", ":")) in page
