"""Tests for the generated 3D viewer page.

The page's *behaviour* was verified by executing it: both script blocks were
pulled out of a generated document and run under a stub DOM in node, which
built 4 real ``BufferGeometry`` objects with computed normals, framed the
camera at 3x the object radius and issued one render per pane. That cannot live
here -- node is not a dependency and there is no browser in the test
environment -- so these tests pin down the properties that can be checked from
the text: that the page is self-contained, that the geometry really is in it,
and that the escaping holds against names written by strangers.
"""

from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from typing import TYPE_CHECKING

import pytest

from wraithguard.nif.geometry import Mesh
from wraithguard.nif.textures import TextureResolver

if TYPE_CHECKING:
    from pathlib import Path
from wraithguard.nif.viewer import ViewerError, build_viewer_page, three_source

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

TRIANGLE = Mesh(
    name="tri",
    vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
    triangles=[(0, 1, 2)],
)


def payload(page: str) -> list[dict]:
    """Extract the scene data the page embeds.

    Textures are carried once in a side table and referenced by index (see
    ``_hoist_textures``); this rehydrates each mesh's texture slots back to their
    blobs, so callers see the same shape the payload had before deduplication.

    Args:
        page: The generated document.

    Returns:
        The decoded scene list, with texture slots as blobs (or ``None``).
    """
    match = re.search(r"var scenes = (\[.*?\]);", page, re.S)
    assert match, "the page carries no scene data"
    scenes = json.loads(match.group(1).replace("<\\/", "</"))
    tex_match = re.search(r"var textures = (\[.*?\]);", page, re.S)
    textures = json.loads(tex_match.group(1).replace("<\\/", "</")) if tex_match else []

    def blob(index: object) -> object:
        """The table entry for an index, or ``None`` when the slot was empty."""
        return None if index is None else textures[index]

    for scene in scenes:
        for mesh in scene.get("meshes", []):
            for slot in ("image", "glow", "dark", "detail", "gloss", "bump"):
                if slot in mesh:
                    mesh[slot] = blob(mesh[slot])
            if "decals" in mesh:
                mesh["decals"] = [blob(index) for index in (mesh["decals"] or [])]
            if "extras" in mesh:
                mesh["extras"] = {s: blob(index) for s, index in (mesh["extras"] or {}).items()}
    return scenes


class TestTheLibraryIsThere:
    """Without three.js the page is an empty black rectangle."""

    def test_the_vendored_build_is_readable(self) -> None:
        """It ships inside the package, so a checkout must find it."""
        source = three_source()
        assert "exports.Scene" in source
        assert "REVISION" in source

    def test_it_is_the_commonjs_build_not_the_module_one(self) -> None:
        """The ESM builds cannot work here.

        They are split across two files and ES module scripts do not load from
        ``file://`` -- the origin is ``null`` and the CORS check fails. If this
        ever becomes an ESM build the page will silently render nothing in a
        browser while looking perfectly correct in the source.
        """
        source = three_source()
        assert "exports." in source
        assert 'from"./three.core' not in source
        assert "import{" not in source[:2000]


class TestThePageIsSelfContained:
    """A file a user can move, keep, or send to someone."""

    def test_nothing_is_fetched_from_elsewhere(self) -> None:
        """No script src, no stylesheet link, no CDN.

        The URL check deliberately excludes the vendored library. three.cjs
        contains 115 ``http`` strings -- comments, and XML *namespace
        identifiers* such as ``http://www.w3.org/1999/xhtml`` that
        ``createElementNS`` uses as a name and never fetches. A blanket "no
        http anywhere" assertion fails on those, which makes it a test of the
        wrong property: what matters is that *this page* loads nothing, not
        that a third-party file never spells a URL.
        """
        page = build_viewer_page([("only", [TRIANGLE])])
        assert "<script src=" not in page
        assert "<link" not in page
        assert "<iframe" not in page
        ours = page.replace(three_source(), "")
        assert "http://" not in ours and "https://" not in ours

    def test_the_library_and_the_shim_are_both_present(self) -> None:
        """The shim is what lets a CommonJS bundle run as a classic script."""
        page = build_viewer_page([("only", [TRIANGLE])])
        assert "var module = {exports:{}}, exports = module.exports;" in page
        assert "var THREE = module.exports;" in page


class TestGeometryReachesThePage:
    """The point of the whole exercise."""

    def test_positions_and_indices_are_embedded(self) -> None:
        """Packed as deflated binary, and they must survive the round trip.

        Decoded here exactly as the page does it -- base64, inflate, read as
        the declared type -- so this fails if the packing and the unpacking
        ever disagree about format or byte order.
        """
        scenes = payload(build_viewer_page([("only", [TRIANGLE])]))
        mesh = scenes[0]["meshes"][0]
        positions = struct.unpack(
            "<9f", zlib.decompress(base64.b64decode(mesh["positions"]["b64"]))
        )
        indices = struct.unpack("<3I", zlib.decompress(base64.b64decode(mesh["indices"]["b64"])))
        assert list(positions) == [0, 0, 0, 1, 0, 0, 0, 1, 0]
        assert list(indices) == [0, 1, 2]
        assert mesh["vertexCount"] == 3
        assert mesh["triangleCount"] == 1

    def test_packing_is_smaller_than_the_decimals_it_replaces(self) -> None:
        """The whole reason for the extra step.

        Measured at a third the size on a 204k-triangle mesh; on a triangle the
        margin is small, so this asserts the direction rather than a ratio.
        """
        big = Mesh(
            name="big",
            vertices=[(float(i), float(i) * 1.5, float(i) * 2.25) for i in range(4000)],
            triangles=[(i, i + 1, i + 2) for i in range(3990)],
        )
        mesh = payload(build_viewer_page([("only", [big])]))[0]["meshes"][0]
        as_json = len(json.dumps([round(c, 4) for v in big.vertices for c in v]))
        assert len(mesh["positions"]["b64"]) < as_json / 2

    def test_two_sides_get_two_scenes_in_different_colors(self) -> None:
        """Telling them apart is the reason there are two."""
        scenes = payload(build_viewer_page([("a", [TRIANGLE]), ("b", [TRIANGLE])]))
        assert [s["label"] for s in scenes] == ["a", "b"]
        assert scenes[0]["color"] != scenes[1]["color"]

    def test_a_mesh_with_no_triangles_is_dropped(self) -> None:
        """An empty shape would add a draw call and show nothing."""
        scenes = payload(build_viewer_page([("only", [Mesh(name="empty")])]))
        assert scenes[0]["meshes"] == []

    def test_a_page_with_no_geometry_says_so(self) -> None:
        """Silence would be indistinguishable from a broken viewer.

        And the wording matters: it is a limit of the reader, not a claim about
        the file, which is the same distinction the conflict report makes.
        """
        page = build_viewer_page([("only", [])])
        assert "No geometry could be read" in page
        assert "__EMPTY__" not in page


class TestUntrustedTextCannotBreakOut:
    """Mesh and folder names come from mod archives."""

    def test_a_script_tag_in_a_mesh_name_cannot_close_the_element(self) -> None:
        """``</script>`` inside a JSON string ends the element in HTML parsing.

        The browser does not care that it is inside a quoted string: the tag
        wins. That turns a mesh name into arbitrary markup.
        """
        nasty = Mesh(
            name="</script><img src=x onerror=alert(1)>",
            vertices=TRIANGLE.vertices,
            triangles=TRIANGLE.triangles,
        )
        page = build_viewer_page([("side", [nasty])])
        assert "</script><img" not in page
        # ...and the name survives intact once unescaped, so the escaping is
        # not just deleting the problem.
        assert payload(page)[0]["meshes"][0]["name"] == nasty.name

    def test_a_hostile_title_is_escaped(self) -> None:
        """The title is interpolated into the document head."""
        page = build_viewer_page([("side", [TRIANGLE])], title="<script>bad()</script>")
        assert "<title><script>" not in page
        assert "&lt;script&gt;" in page

    def test_a_hostile_label_does_not_become_markup(self) -> None:
        """Labels are set with textContent, but must survive JSON encoding."""
        page = build_viewer_page([("</script>evil", [TRIANGLE])])
        assert "</script>evil" not in page
        assert payload(page)[0]["label"] == "</script>evil"


class TestMissingLibraryIsReported:
    """A build that shipped without the asset must say so."""

    def test_it_raises_a_named_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rather than producing a page that renders nothing.

        Patched at :mod:`wraithguard.viz.library`, which is where locating the
        vendored build now lives. Patching the old location silently did
        nothing once the function moved -- the test still passed by asserting
        an error that was raised for a different reason.
        """
        monkeypatch.setattr("wraithguard.viz.library._first_readable", lambda _c: None)
        with pytest.raises(ViewerError, match="not shipped"):
            build_viewer_page([("only", [TRIANGLE])])


class TestServedAndStandaloneShareOneBuilder:
    """Two shapes from one template, differing only in how bytes arrive.

    A fallback that shares no code with the primary path is a second
    implementation waiting to rot, so the split is confined to the sink and the
    library URL -- every line of rendering is common.
    """

    def test_a_served_page_carries_urls_not_bytes(self) -> None:
        """Which is what makes it kilobytes instead of megabytes."""
        page = build_viewer_page(
            [("only", [TRIANGLE])],
            sink=lambda blob, _t="": {"url": f"http://127.0.0.1:1/g{len(blob)}.bin"},
            library_url="http://127.0.0.1:1/three.js",
        )
        mesh = payload(page)[0]["meshes"][0]
        assert "url" in mesh["positions"]
        assert "b64" not in mesh["positions"]

    def test_a_served_page_does_not_inline_the_library(self) -> None:
        """The whole size argument depends on this."""
        page = build_viewer_page(
            [("only", [TRIANGLE])],
            sink=lambda _b, _t="": {"url": "http://127.0.0.1:1/g.bin"},
            library_url="http://127.0.0.1:1/three.js",
        )
        assert "exports.Scene" not in page
        assert '<script src="http://127.0.0.1:1/three.js"></script>' in page
        # The library is 2 MB. This bound is not a page-weight budget -- it is
        # the assertion that the library is *absent*, stated in a way that
        # still fails if a future change inlines it. Room is left above the
        # current size so that adding a control does not look like a
        # regression in something this test does not measure.
        assert len(page) < 100_000, "a served page should be kilobytes, not megabytes"

    @pytest.mark.parametrize("served", [False, True])
    def test_the_shim_wraps_the_library_in_both_modes(self, served: bool) -> None:
        """The bug this test exists for: a served page had no shim at all.

        The CommonJS build assigns to ``exports`` and never defines a global,
        so without the two wrapper scripts it runs against ``undefined`` and
        the page reports "THREE is not defined". Inlining happened to include
        them; serving did not.

        Nothing caught it because the other tests sat either side of it -- one
        asserted the library is *not* inlined when served, the other compared
        the two pages only from ``function render(`` onward, which begins after
        the library block. Both were true while the page was broken.
        """
        extra = (
            {
                "sink": lambda _b, _t="": {"url": "http://127.0.0.1:1/g.bin"},
                "library_url": "http://127.0.0.1:1/three.js",
            }
            if served
            else {}
        )
        page = build_viewer_page([("only", [TRIANGLE])], **extra)
        prologue = page.index("var module = {exports:{}}, exports = module.exports;")
        epilogue = page.index("var THREE = module.exports;")
        assert prologue < epilogue, "the shim must open before it closes"
        # ...and the library has to sit between them, or the order is useless.
        marker = "http://127.0.0.1:1/three.js" if served else "exports.Scene"
        assert prologue < page.index(marker) < epilogue

    def test_the_standalone_page_still_inlines_everything(self) -> None:
        """A negative control: the export must not quietly become a stub."""
        page = build_viewer_page([("only", [TRIANGLE])])
        assert "exports.Scene" in page
        assert "<script src=" not in page

    def test_both_modes_render_through_the_same_code(self) -> None:
        """The rendering half of the page must be byte-identical."""
        served = build_viewer_page(
            [("only", [TRIANGLE])],
            sink=lambda _b, _t="": {"url": "http://127.0.0.1:1/g.bin"},
            library_url="http://127.0.0.1:1/three.js",
        )
        standalone = build_viewer_page([("only", [TRIANGLE])])
        marker = "function build()"
        assert served[served.index(marker) :] == standalone[standalone.index(marker) :]


class TestFramingOrder:
    """The rotation must be applied before the bounds are measured.

    A real bug: the page measured the bounding box, subtracted the centre, and
    *then* rotated Z-up to Y-up. three.js composes a local matrix as ``T*R*S``,
    so position is applied after rotation -- which leaves the mesh at
    ``R*v - centre`` instead of ``R*(v - centre)``. The error is
    ``R*centre - centre``: exactly zero for a mesh already on the origin, and
    arbitrarily large for one that is not.

    That is why it survived every test and most meshes. Measured on an offset
    cube of radius 8.7, the old order left it **451.8 units** from the origin
    while the camera sat at 3 radii -- an empty pane reporting a full triangle
    count, which is what the user saw.

    Checked structurally because the maths lives in JavaScript and node is not
    a test dependency. It was verified numerically during development by
    running the page's own scripts under ``vm`` and comparing both orderings.
    """

    def test_the_rotation_is_set_before_the_bounds_are_measured(self) -> None:
        """The ordering *is* the fix; nothing else about it matters."""
        page = build_viewer_page([("only", [TRIANGLE])])
        rotate = page.index("group.rotation.x = -Math.PI / 2")
        measure = page.index("box.expandByObject(g)")
        assert rotate < measure, "bounds measured before the rotation that changes them"

    def test_the_centring_is_applied_to_a_parent(self) -> None:
        """Rotation and translation cannot share one object here.

        If they did, the composition order would reintroduce the bug no matter
        where the rotation is set.
        """
        page = build_viewer_page([("only", [TRIANGLE])])
        assert "pivot.add(group)" in page
        assert "pivot.position.copy(centre).negate()" in page
        assert "group.position.sub(centre)" not in page, "the old, broken centring"

    def test_degenerate_bounds_do_not_place_the_camera_at_infinity(self) -> None:
        """An empty or non-finite box would send the camera somewhere useless."""
        page = build_viewer_page([("only", [TRIANGLE])])
        assert "!isFinite(radius) || box.isEmpty()" in page


class TestOneViewportWithToggles:
    """One camera, one frame, providers switched on and off.

    Separate panes gave each provider its own camera, which is the one thing a
    comparison must not do: two meshes at different scales look identical when
    each is fitted to its own viewport. A shared camera makes the difference
    the thing you actually see.
    """

    def test_every_provider_gets_a_toggle(self) -> None:
        """Otherwise a third provider is simply unreachable."""
        page = build_viewer_page(
            [("Data Files", [TRIANGLE]), ("00 Core", [TRIANGLE]), ("DB refit", [TRIANGLE])]
        )
        assert [s["label"] for s in payload(page)] == ["Data Files", "00 Core", "DB refit"]
        assert 'type = "checkbox"' in page or 'box2.type = "checkbox"' in page

    def test_only_the_first_provider_starts_visible(self) -> None:
        """Everything at once, overlapping, is a worse default than one thing."""
        page = build_viewer_page([("a", [TRIANGLE]), ("b", [TRIANGLE])])
        assert "group.visible = index === 0" in page

    def test_the_frame_covers_every_provider_not_just_the_visible_one(self) -> None:
        """The camera must not move when a toggle changes.

        Framing only what is shown would re-fit the view on every click, so a
        mesh that is half the size of its neighbour would fill the viewport
        just the same -- and the comparison would show nothing.
        """
        page = build_viewer_page([("a", [TRIANGLE]), ("b", [TRIANGLE])])
        assert "groups.forEach(function (g) { box.expandByObject(g); });" in page
        assert "if (!groups[i].visible) return;" in page  # stats do respect it

    def test_the_stats_follow_what_is_shown(self) -> None:
        """A count that ignores the toggles would describe a different picture."""
        page = build_viewer_page([("a", [TRIANGLE]), ("b", [TRIANGLE])])
        assert '"nothing shown"' in page

    def test_there_is_one_renderer_not_one_per_provider(self) -> None:
        """A negative control on the whole change: panes are gone."""
        page = build_viewer_page([("a", [TRIANGLE]), ("b", [TRIANGLE])])
        assert page.count("new THREE.WebGLRenderer") == 1
        assert 'className = "pane"' not in page


class TestTexturesReachThePage:
    """Geometry alone answers "is it different"; a texture answers "how"."""

    @staticmethod
    def _uv_mesh() -> Mesh:
        """A triangle with one UV per vertex and a texture reference.

        Returns:
            The mesh.
        """
        return Mesh(
            name="tri",
            vertices=TRIANGLE.vertices,
            triangles=TRIANGLE.triangles,
            uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            texture="tx_rock.dds",
        )

    def test_uvs_are_sent_when_there_is_one_per_vertex(self) -> None:
        """A partial set would make three.js index past the attribute's end."""
        mesh = payload(build_viewer_page([("only", [self._uv_mesh()])]))[0]["meshes"][0]
        assert mesh["uvs"] is not None
        coords = struct.unpack("<6f", zlib.decompress(base64.b64decode(mesh["uvs"]["b64"])))
        # V is flipped: NIF measures it downward, OpenGL upward, so an
        # untouched copy renders every texture upside down.
        assert list(coords) == [0.0, 1.0, 1.0, 1.0, 0.0, 0.0]

    def test_a_mismatched_uv_count_is_dropped(self) -> None:
        """Better untextured than drawing nothing at all."""
        broken = Mesh(
            name="tri",
            vertices=TRIANGLE.vertices,
            triangles=TRIANGLE.triangles,
            uvs=[(0.0, 0.0)],
            texture="tx.dds",
        )
        assert payload(build_viewer_page([("only", [broken])]))[0]["meshes"][0]["uvs"] is None

    def test_no_resolver_means_no_image_but_still_a_view(self) -> None:
        """Untextured is a complete view, not a degraded one."""
        mesh = payload(build_viewer_page([("only", [self._uv_mesh()])]))[0]["meshes"][0]
        assert mesh["image"] is None
        assert mesh["positions"] is not None

    def test_a_resolved_texture_becomes_a_png_in_the_page(self, tmp_path: Path) -> None:
        """The whole pipeline: reference, VFS lookup, DDS decode, PNG."""
        from tests.test_images import bc1_block, dds

        folder = tmp_path / "Mod"
        target = folder / "textures" / "tx_rock.dds"
        target.parent.mkdir(parents=True)
        target.write_bytes(dds(b"DXT1", 4, 4, bc1_block(0xFFFF, 0xFFFF, 0)))
        page = build_viewer_page([("only", [self._uv_mesh()])], resolver=TextureResolver([folder]))
        image = payload(page)[0]["meshes"][0]["image"]
        assert image is not None
        assert image["url"].startswith("data:image/png;base64,")
        assert base64.b64decode(image["url"].split(",", 1)[1]).startswith(PNG_SIGNATURE)

    def test_an_undecodable_texture_leaves_the_mesh_untextured(self, tmp_path: Path) -> None:
        """BC7 is unsupported and broken files are common; neither may fail the view."""
        folder = tmp_path / "Mod"
        target = folder / "textures" / "tx_rock.dds"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"not a dds at all")
        page = build_viewer_page([("only", [self._uv_mesh()])], resolver=TextureResolver([folder]))
        assert payload(page)[0]["meshes"][0]["image"] is None

    def test_one_texture_shared_by_two_sides_is_decoded_once(self, tmp_path: Path) -> None:
        """A 2048px image decoded per shape would dominate opening a view."""
        from tests.test_images import bc1_block, dds

        folder = tmp_path / "Mod"
        target = folder / "textures" / "tx_rock.dds"
        target.parent.mkdir(parents=True)
        target.write_bytes(dds(b"DXT1", 4, 4, bc1_block(0xFFFF, 0xFFFF, 0)))
        mesh = self._uv_mesh()
        page = build_viewer_page(
            [("a", [mesh, mesh]), ("b", [mesh])], resolver=TextureResolver([folder])
        )
        urls = {
            entry["image"]["url"]
            for scene in payload(page)
            for entry in scene["meshes"]
            if entry["image"]
        }
        assert len(urls) == 1, "the same texture produced more than one payload"

    def test_a_texture_shared_by_many_meshes_is_carried_once_in_the_page(
        self, tmp_path: Path
    ) -> None:
        """A cell reuses a few textures across hundreds of meshes.

        Inlining a copy per mesh would push the page into the gigabytes and hang
        it; the shared table must carry each texture's bytes exactly once no
        matter how many meshes reference it.
        """
        from tests.test_images import bc1_block, dds

        folder = tmp_path / "Mod"
        target = folder / "textures" / "tx_rock.dds"
        target.parent.mkdir(parents=True)
        target.write_bytes(dds(b"DXT1", 4, 4, bc1_block(0xFFFF, 0xFFFF, 0)))
        meshes = [self._uv_mesh() for _ in range(50)]
        page = build_viewer_page([("cell", meshes)], resolver=TextureResolver([folder]))

        image = payload(page)[0]["meshes"][0]["image"]
        assert image is not None
        # The PNG data URL is long and unique; it must appear exactly once in the
        # whole document even though 50 meshes draw it.
        assert page.count(image["url"]) == 1


class TestMaterialsReachThePage:
    """The file's own material, not a viewer-wide guess.

    Before the reader carried these, the page's alpha controls applied one
    global default -- a 0.5 cutoff on everything -- and "off" meant off
    everywhere, silently overriding any file that had asked for a cutout.
    """

    @staticmethod
    def _shape(name: str, **kwargs: object) -> Mesh:
        """A one-triangle mesh with material fields set.

        Args:
            name: The shape's name.
            kwargs: Material attributes to set.

        Returns:
            The mesh.
        """
        return Mesh(
            name=name,
            vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            triangles=[(0, 1, 2)],
            uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            **kwargs,  # type: ignore[arg-type]
        )

    def test_the_cutout_threshold_travels_with_the_shape(self) -> None:
        """A per-shape value, not the page's 0.5 default."""
        page = build_viewer_page(
            [("side", [self._shape("body", alpha_test=True, alpha_threshold=0.25)])]
        )
        mesh = payload(page)[0]["meshes"][0]
        assert mesh["alphaTest"] is True
        assert mesh["alphaThreshold"] == 0.25

    def test_blending_and_cutout_stay_separate(self) -> None:
        """Foliage sets testing without blending; glass does the reverse.

        Conflating them makes every leaf in the game fade instead of cut out,
        which looks like a rendering choice rather than an error.
        """
        page = build_viewer_page(
            [
                (
                    "side",
                    [
                        self._shape("leaf", alpha_test=True),
                        self._shape("glass", alpha_blend=True, opacity=0.4),
                    ],
                )
            ]
        )
        leaf, glass = payload(page)[0]["meshes"]
        assert leaf["alphaTest"] and not leaf["alphaBlend"]
        assert glass["alphaBlend"] and not glass["alphaTest"]
        assert glass["opacity"] == 0.4

    def test_an_undescribed_material_is_null_not_black(self) -> None:
        """A caller that cannot tell them apart renders silhouettes."""
        page = build_viewer_page([("side", [self._shape("plain")])])
        mesh = payload(page)[0]["meshes"][0]
        assert mesh["diffuse"] is None
        assert mesh["emissive"] is None

    def test_vertex_colors_ship_as_a_packed_blob(self) -> None:
        """Not as JSON numbers: 30,000 vertices is 90,000 floats."""
        page = build_viewer_page(
            [
                (
                    "side",
                    [
                        self._shape(
                            "tinted",
                            vertex_colors=[
                                (1.0, 0.0, 0.0, 1.0),
                                (0.0, 1.0, 0.0, 1.0),
                                (0.0, 0.0, 1.0, 1.0),
                            ],
                        )
                    ],
                )
            ]
        )
        mesh = payload(page)[0]["meshes"][0]
        assert mesh["colors"] is not None
        assert "b64" in mesh["colors"] or "url" in mesh["colors"]

    def test_a_shape_without_colors_sends_none(self) -> None:
        """A negative control, so the attribute means something when present."""
        page = build_viewer_page([("side", [self._shape("plain")])])
        assert payload(page)[0]["meshes"][0]["colors"] is None


class TestThePerShapeList:
    """Isolating one shape is what turns "something differs" into "this does"."""

    def test_the_page_carries_a_shape_list_and_a_summary(self) -> None:
        """Visibility per shape, and the material as text beside it.

        Text rather than controls on purpose: a mesh routinely has twenty
        shapes, and a checkbox per material property per shape would be a
        hundred controls answering a question nobody asks.
        """
        page = build_viewer_page([("side", [TRIANGLE])])
        assert "renderShapes" in page
        assert "summaryOf" in page
        assert "click to isolate" in page

    def test_shapes_are_tracked_per_provider(self) -> None:
        """Solo must isolate within one side, not across both.

        Hiding the other provider's shapes as a side effect would silently
        turn a comparison into a single-mesh view.
        """
        page = build_viewer_page([("a", [TRIANGLE]), ("b", [TRIANGLE])])
        assert "group.userData.shapes" in page


class TestTheCompositeOrderIsPinned:
    """Which layer multiplies into which, and in what sequence.

    Morrowind applies **detail before dark**, and a decal composites over the
    result of both rather than multiplying into it. Today that ordering is an
    accident of the order the ``if`` blocks appear in ``attachExtraSlots`` --
    nothing states it and nothing checks it, so a reordering during an
    unrelated edit would change how every multi-slot mesh renders with no test
    going red and no visible error.

    These read the generated shader source. That is a weaker instrument than
    rendering a frame and comparing pixels, and it is the one available here;
    it is enough to catch a reordering, which is the failure being guarded
    against.
    """

    @staticmethod
    def _shader(mesh: Mesh, tmp_path: Path) -> str:
        """Build a page for a mesh with real texture files behind its slots.

        Args:
            mesh: The shape.
            tmp_path: A folder to put texture files in.

        Returns:
            The generated page.
        """
        from tests.test_images import bc1_block, dds

        folder = tmp_path / "Data Files" / "textures"
        folder.mkdir(parents=True, exist_ok=True)
        # Distinct colors per slot. Writing the same bytes everywhere
        # makes every slot resolve to an identical data URL, which would
        # let an ordering test pass while proving nothing about order.
        colors = {
            "base": 0xFFFF,
            "detail": 0xF800,
            "dark": 0x07E0,
            "d0": 0x001F,
            "d1": 0xFFE0,
            "gloss": 0x8410,
        }
        for name, packed in colors.items():
            (folder / f"{name}.dds").write_bytes(dds(b"DXT1", 4, 4, bc1_block(packed, packed, 0)))
        return build_viewer_page(
            [("side", [mesh])],
            resolver=TextureResolver([tmp_path / "Data Files"]),
        )

    def test_detail_uses_multiply_2x_not_a_uv_scale(self, tmp_path: Path) -> None:
        """The 2 belongs to the color, not to the texture coordinates.

        Gamebryo applies detail maps in "multiply 2X" mode -- Direct3D's
        MODULATE2X: base and detail are multiplied together and the *result*
        is doubled. That doubling is what gives the mode a neutral value: a
        detail map at half brightness leaves the base unchanged, so it can
        brighten as well as darken.

        This asserts the negative too, because the bug it replaces was
        ``texture2D(detailMap, vMapUv * 2.0)`` -- the same literal applied to
        the wrong operand. That scaled the coordinates, left the color
        undoubled, and so had no neutral value at all: every texel is at most
        1.0, so the layer could only ever darken every surface it touched.

        The real tiling comes from the mesh's own UVs. Detail maps are wrapped
        far more densely than the base -- 16x to 64x, per the Gamebryo notes --
        which is a property of the file, not a constant belonging here.
        """
        page = self._shader(
            Mesh(
                name="s",
                vertices=TRIANGLE.vertices,
                triangles=TRIANGLE.triangles,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                texture="base.dds",
                detail="detail.dds",
                dark="dark.dds",
            ),
            tmp_path,
        )
        assert "texture2D(detailMap, vMapUv).rgb * 2.0" in page
        # The negative names the whole wrong *call*, not the substring. A bare
        # "vMapUv * 2.0" check matched the source comment that documents the
        # old bug, so the test failed on its own explanation.
        assert "texture2D(detailMap, vMapUv * 2.0)" not in page

    def test_detail_and_dark_are_both_multiplies(self, tmp_path: Path) -> None:
        """Which is why their relative order does not matter.

        An earlier version of this test asserted detail comes before dark.
        That was pinning something unfalsifiable: both are multiplies into the
        same value, multiplication commutes, and no reordering of the two can
        change a single pixel. Decal order *does* matter -- a decal is a mix,
        not a multiply -- and that is tested separately.

        What is worth pinning is that they *are* multiplies, since switching
        either to a mix would change the image and would not be caught by any
        ordering assertion.
        """
        page = self._shader(
            Mesh(
                name="s",
                vertices=TRIANGLE.vertices,
                triangles=TRIANGLE.triangles,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                texture="base.dds",
                detail="detail.dds",
                dark="dark.dds",
            ),
            tmp_path,
        )
        assert "diffuseColor.rgb *= texture2D(detailMap" in page
        assert "diffuseColor.rgb *= texture2D(darkMap" in page

    def test_decals_composite_after_the_multiplies(self, tmp_path: Path) -> None:
        """A decal replaces what is under it, so it goes last.

        Compositing it before the multiplies would let a dark map darken the
        decal, which is the opposite of what a decal is for.
        """
        page = self._shader(
            Mesh(
                name="s",
                vertices=TRIANGLE.vertices,
                triangles=TRIANGLE.triangles,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                texture="base.dds",
                dark="dark.dds",
                decals=["d0.dds"],
            ),
            tmp_path,
        )
        # The uniform name is built at runtime ("decalMap" + slot), so it
        # never appears literally. Anchor on the code that emits it.
        assert page.index("darkMap, vMapUv") < page.index('"decalMap" + slot')

    def test_every_decal_gets_its_own_uniform_in_slot_order(self, tmp_path: Path) -> None:
        """Slot order is paint order; the last declared ends up on top."""
        page = self._shader(
            Mesh(
                name="s",
                vertices=TRIANGLE.vertices,
                triangles=TRIANGLE.triangles,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                texture="base.dds",
                decals=["d0.dds", "d1.dds"],
            ),
            tmp_path,
        )
        mesh = payload(page)[0]["meshes"][0]
        assert len(mesh["decals"]) == 2
        # Both reach the page in slot order, and the shader unrolls one
        # sample per slot -- named at runtime, so the source carries the
        # construction rather than the names.
        assert mesh["decals"][0] != mesh["decals"][1]
        assert '"decalMap" + slot' in page
        assert "decalMaps.forEach" in page

    def test_the_extra_slots_are_guarded_on_the_base_map(self, tmp_path: Path) -> None:
        """Every injected line samples vMapUv, which exists only under USE_MAP.

        The Textures checkbox sets ``material.map = null``, which undefines
        USE_MAP and forces a recompile. Without the guard that combination
        injects a reference to a varying that no longer exists, the shader
        fails to compile, and the mesh renders as nothing -- from two clicks.
        """
        page = self._shader(
            Mesh(
                name="s",
                vertices=TRIANGLE.vertices,
                triangles=TRIANGLE.triangles,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                texture="base.dds",
                dark="dark.dds",
            ),
            tmp_path,
        )
        assert "#ifdef USE_MAP" in page
