"""Tests for turning a load order into plugin records.

The failure these pin shipped: a real 27-master load order stopped at
``could not read master distant_seafloor_2.00.esm``, with no way to tell
whether the file was missing, unreadable, or simply somewhere else. Both
halves of that -- searching one folder, and discarding the reason -- are
covered here.
"""

from __future__ import annotations

import json
import subprocess
import types
from array import array
from pathlib import Path

import pytest

from wraithguard.land.diff import LandData
from wraithguard.land.emit import build_landscape_record, build_plugin
from wraithguard.land.landmass import Landmass
from wraithguard.land.merge import ConflictStrategy
from wraithguard.land.meta import (
    LAYER_NAMES,
    META_TYPES,
    STRATEGY_NAMES,
    MergeSettings,
    MetaError,
    PluginMeta,
    load_meta,
    parse_meta,
    render_meta,
    strategy_display_name,
    strategy_from_name,
    write_merged_marker,
    write_meta,
    write_patch_template,
    write_settings,
)
from wraithguard.land.pipeline import MergedCell, MergeOutcome
from wraithguard.land.service import (
    MergeServiceError,
    _build_records,
    _contributors,
    _describe_meta,
    _finish_textures,
    _records_via,
    _split_order,
    _write,
    build_merged_lands,
    resolve_plugin,
)
from wraithguard.land.textures import KnownTextures, vtex_of
from wraithguard.tes3fields.landscape import (
    LAND_SIZE,
    TEXTURE_SIZE,
    decode_vertex_colors,
)


# The four converter-subprocess tests below need a real tes3conv: a hand-rolled
# shell script only stands in for it on POSIX (Windows' CreateProcess cannot
# execute a script directly -- no shebang, and .bat/.py are not valid
# argv[0]s), which made the subprocess boundary untested on Windows. tes3conv
# itself is a real cross-platform binary, so running it directly works
# identically on both -- only its *presence* on a given machine varies, which
# is what this fixture checks and skips on.
@pytest.fixture(scope="session")
def real_tes3conv(core: types.ModuleType) -> str:
    """Path to a real tes3conv executable, or skip the test.

    Resolution matches what the tool itself does at runtime --
    :func:`wraithguard_toolkit.find_tes3conv` -- so point ``MLOX_TES3CONV`` at
    a binary, put one on ``PATH``, or drop it beside ``wraithguard_toolkit.py``.
    """
    found = core.find_tes3conv()
    if not found:
        pytest.skip("needs a real tes3conv executable (set MLOX_TES3CONV or put it on PATH)")
    return found


def _build_esp(converter: str, tmp_path: Path, masters: list[tuple[str, int]]) -> Path:
    """Turn a minimal valid plugin document into a real ``.esp`` via tes3conv.

    Gives the two success-path tests below genuine tes3conv output to read
    back, rather than a hand-rolled stand-in. A header with no landscape
    records round-trips without needing zstd, unlike an actual ``LAND``
    record -- so this stays dependency-free.
    """
    document = build_plugin([], masters)
    as_json = tmp_path / "source.json"
    as_json.write_text(json.dumps(document), encoding="utf-8")
    esp = tmp_path / "source.esp"
    subprocess.run(  # noqa: S603 -- fixed argv, a test-built tes3conv command
        [converter, str(as_json), str(esp), "--overwrite"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return esp


class TestResolvePlugin:
    """A load order spans many data folders, spelled inconsistently."""

    def test_finds_a_plugin_in_a_later_folder(self, tmp_path: Path) -> None:
        """The master may not be in the folder the first plugin was found in."""
        first = tmp_path / "vanilla"
        second = tmp_path / "mod"
        first.mkdir()
        second.mkdir()
        (second / "distant_seafloor.esm").write_bytes(b"")
        found = resolve_plugin("distant_seafloor.esm", [first, second])
        assert found == second / "distant_seafloor.esm"

    def test_matches_case_insensitively(self, tmp_path: Path) -> None:
        """``openmw.cfg`` and the filesystem routinely disagree on case."""
        tmp_path.joinpath("RepopulatedMorrowind.esm").write_bytes(b"")
        assert resolve_plugin("RepopulatedMorrowind.ESM", [tmp_path]) is not None

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        """Not finding it is reported, not guessed at."""
        assert resolve_plugin("nothing.esm", [tmp_path]) is None

    def test_an_unreadable_folder_does_not_stop_the_search(self, tmp_path: Path) -> None:
        """A vanished data folder must not hide a plugin in the next one."""
        good = tmp_path / "good"
        good.mkdir()
        (good / "a.esm").write_bytes(b"")
        assert resolve_plugin("a.esm", [tmp_path / "gone", good]) is not None

    def test_the_last_data_folder_wins(self, tmp_path: Path) -> None:
        """OpenMW resolves a shared name to the latest ``data=`` folder.

        A later ``data=`` line shadows an earlier one, so a mod split across
        ``00 Core`` and ``01 Patch`` -- both shipping ``a.esm``, patch second --
        must resolve to the patch copy. Returning the first match read the
        pre-patch file, which is the load-order bug this pins against.
        """
        core = tmp_path / "00 Core"
        patch = tmp_path / "01 Patch"
        core.mkdir()
        patch.mkdir()
        (core / "a.esm").write_bytes(b"")
        (patch / "a.esm").write_bytes(b"")
        # directories arrive in load order (earliest first); the patch wins.
        assert resolve_plugin("a.esm", [core, patch]) == patch / "a.esm"

    def test_case_insensitive_collision_still_takes_the_later_folder(self, tmp_path: Path) -> None:
        """Last-wins holds even when the two copies differ only in case."""
        core = tmp_path / "00 Core"
        patch = tmp_path / "01 Patch"
        core.mkdir()
        patch.mkdir()
        (core / "Dwemer Airship_Exterior.ESP").write_bytes(b"")
        (patch / "Dwemer Airship_Exterior.esp").write_bytes(b"")
        found = resolve_plugin("Dwemer Airship_Exterior.esp", [core, patch])
        assert found == patch / "Dwemer Airship_Exterior.esp"


class TestRecordsViaReportsWhy:
    """A master that will not convert stops the merge, so it must say why."""

    def test_a_missing_converter_is_named(self, tmp_path: Path) -> None:
        """The reason is returned rather than collapsed into an empty list."""
        records, failure = _records_via(
            str(tmp_path / "no-such-tes3conv"), tmp_path / "a.esm", tmp_path
        )
        assert records == []
        assert "tes3conv" in failure

    def test_a_converter_error_carries_its_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_tes3conv: str
    ) -> None:
        """tes3conv's own complaint is what the user needs to see."""
        import wraithguard.land.service as svc

        # has_landscape is a byte-scan pre-filter that runs before the
        # converter -- our garbage plugin has no LAND/LTEX tag bytes to trip
        # it, so without this it would short-circuit to ([], "") and tes3conv
        # would never run at all.
        monkeypatch.setattr(svc, "has_landscape", lambda *_a, **_k: True)
        bad = tmp_path / "a.esm"
        bad.write_bytes(b"not a real plugin")
        records, failure = _records_via(real_tes3conv, bad, tmp_path)
        assert records == []
        assert failure.startswith("tes3conv exited")
        assert "no message" not in failure

    def test_success_reports_no_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_tes3conv: str
    ) -> None:
        """The happy path returns records and an empty reason."""
        import wraithguard.land.service as svc

        # Same pre-filter as above: a header-only plugin (deliberately, so
        # this needs no zstd) has no LAND/LTEX tag bytes either.
        monkeypatch.setattr(svc, "has_landscape", lambda *_a, **_k: True)
        source = _build_esp(real_tes3conv, tmp_path, masters=[("Morrowind.esm", 1)])
        records, failure = _records_via(real_tes3conv, source, tmp_path)
        assert failure == ""
        assert records == [
            {
                "type": "Header",
                "flags": "",
                "version": 1.3,
                "file_type": "Esp",
                "author": "Wraithguard Toolkit",
                "description": "Merged landscape. Load last.",
                "num_objects": 0,
                "masters": [["Morrowind.esm", 1]],
            }
        ]

    def test_the_converter_call_suppresses_the_console_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A --noconsole build must not flash a window per plugin converted.

        The Merged Lands run shells out to tes3conv once per landscape plugin;
        without the no-window flags each opened a console window. The fix threads
        ``no_window_kwargs()`` into the call -- this pins that it reaches
        ``subprocess.run``.
        """
        import types

        import wraithguard.land.service as svc

        seen: dict[str, object] = {}

        def fake_run(argv: list[str], **kwargs: object) -> types.SimpleNamespace:
            seen.update(kwargs)
            Path(argv[2]).write_text("[]", encoding="utf-8")  # the JSON target
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(svc, "has_landscape", lambda *_a, **_k: True)
        monkeypatch.setattr(svc, "no_window_kwargs", lambda: {"creationflags": 0x08000000})
        monkeypatch.setattr(svc.subprocess, "run", fake_run)

        _records_via("tes3conv", tmp_path / "a.esm", tmp_path)
        assert seen.get("creationflags") == 0x08000000

    def test_a_sidecar_with_no_matching_entry_falls_through_to_conversion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale or absent sidecar entry must not stop the plugin from converting.

        The sidecar folder exists (unlike TestAnAbsentSidecarFolder below), but
        holds nothing for this particular plugin -- the case a merge over a
        partially re-scanned load order hits routinely.
        """
        import wraithguard.land.service as svc

        monkeypatch.setattr(svc, "has_landscape", lambda *_a, **_k: True)
        monkeypatch.setattr(svc, "landscape_records_from_sidecar", lambda *_a, **_k: None)
        monkeypatch.setattr(svc, "read_landscape_records", lambda *_a, **_k: [{"type": "Header"}])
        sidecar_dir = tmp_path / "sidecars"
        sidecar_dir.mkdir()

        records, failure = _records_via(None, tmp_path / "a.esm", tmp_path, sidecar_dir)
        assert failure == ""
        assert records == [{"type": "Header"}]


class TestSplitOrder:
    """Masters are the reference; mods are what gets merged onto it."""

    def test_extensions_decide(self) -> None:
        """``.esm`` and ``.omwgame`` are masters, everything else is a mod."""
        masters, mods = _split_order(["a.esm", "b.esp", "c.omwgame", "d.omwaddon"])
        assert masters == ["a.esm", "c.omwgame"]
        assert mods == ["b.esp", "d.omwaddon"]

    def test_case_does_not_matter(self) -> None:
        """A load order file may spell the extension either way."""
        masters, _mods = _split_order(["A.ESM"])
        assert masters == ["A.ESM"]


class TestMasterFailuresAreExplained:
    """The message a user sees when the merge stops at a master."""

    def test_a_master_in_no_folder_says_so(self, tmp_path: Path) -> None:
        """The old message could not distinguish this from a parse error."""
        with pytest.raises(MergeServiceError, match="not in any of the"):
            build_merged_lands(
                data_files=[tmp_path],
                load_order=["distant_seafloor.esm"],
                converter="tes3conv",
            )

    def test_no_masters_at_all_is_refused(self, tmp_path: Path) -> None:
        """Without a reference every mod looks like a total rewrite."""
        with pytest.raises(MergeServiceError, match="no masters"):
            build_merged_lands(data_files=[tmp_path], load_order=["a.esp"], converter="tes3conv")

    def test_no_data_folder_is_refused(self) -> None:
        """An empty folder list would silently find nothing."""
        with pytest.raises(MergeServiceError, match="no data folder"):
            build_merged_lands(data_files=[], load_order=["a.esm"], converter="tes3conv")


class TestTheOutputIsMarkedAsGenerated:
    """A merged plugin must not be merged again on the next run.

    ``Merged Lands.esp`` is an ``.esp`` that edits every cell it wrote and
    loads last. Without a marker beside it, a second run reads it as a mod and
    reconciles its terrain as one more opinion -- a merge of a merge. Nothing
    fails; the terrain just drifts further from what any author wrote each time.
    """

    def test_the_marker_is_written_and_read_back(self, tmp_path: Path) -> None:
        """The sidecar round-trips through the loader that consumes it."""
        plugin = tmp_path / "Merged Lands.esp"
        plugin.write_bytes(b"")
        marker = write_merged_marker(plugin)
        assert marker.name == "Merged Lands.mergedlands.toml"
        assert load_meta(plugin).is_previous_merge

    def test_a_plugin_without_a_marker_is_not_a_previous_merge(self, tmp_path: Path) -> None:
        """The default must stay permissive; every ordinary mod lacks a sidecar."""
        plugin = tmp_path / "some_mod.esp"
        plugin.write_bytes(b"")
        assert not load_meta(plugin).is_previous_merge

    def test_an_unwritable_location_is_reported(self, tmp_path: Path) -> None:
        """A missing marker is a trap for the next run, so it is not silent."""
        plugin = tmp_path / "gone" / "Merged Lands.esp"
        with pytest.raises(MetaError, match="marks"):
            write_merged_marker(plugin)

    def test_the_marker_is_no_longer_blank_inside(self, tmp_path: Path) -> None:
        """It spells out every layer, so the file is self-describing."""
        plugin = tmp_path / "Merged Lands.esp"
        plugin.write_bytes(b"")
        marker = write_merged_marker(plugin)
        text = marker.read_text(encoding="utf-8")
        for name in LAYER_NAMES:
            assert f"[{name}]" in text
        # Belt-and-braces: a marker excludes every layer.
        meta = load_meta(plugin)
        assert meta.is_previous_merge
        assert meta.allowed_layers() == LandData.NONE  # nothing included


class TestWritingSidecars:
    """render_meta is the inverse of parse_meta, and the writers use it."""

    def test_render_round_trips_through_the_parser(self, tmp_path: Path) -> None:
        """What the writer produces must parse back to the same settings."""
        meta = PluginMeta(
            meta_type="Patch",
            layers={
                "height_map": MergeSettings(included=False),
                "world_map_data": MergeSettings(conflict_strategy=ConflictStrategy.OVERWRITE),
            },
        )
        reparsed = parse_meta(_parse(render_meta(meta), tmp_path))
        assert reparsed.settings_for("height_map").included is False
        assert (
            reparsed.settings_for("world_map_data").conflict_strategy is ConflictStrategy.OVERWRITE
        )

    def test_explicit_writes_every_layer_default_omits_them(self, tmp_path: Path) -> None:
        """Explicit is for humans; skip-default matches Merged Lands' own output."""
        meta = PluginMeta(meta_type="Patch")
        explicit = render_meta(meta, explicit=True)
        minimal = render_meta(meta, explicit=False)
        for name in LAYER_NAMES:
            assert f"[{name}]" in explicit
            assert f"[{name}]" not in minimal

    def test_curvature_is_preserved_our_own_strategy(self, tmp_path: Path) -> None:
        """The strategy we add beyond Merged Lands must survive a round trip."""
        meta = PluginMeta(
            meta_type="Patch",
            layers={"height_map": MergeSettings(conflict_strategy=ConflictStrategy.CURVATURE)},
        )
        assert 'conflict_strategy = "Curvature"' in render_meta(meta)
        reparsed = parse_meta(_parse(render_meta(meta), tmp_path))
        assert reparsed.settings_for("height_map").conflict_strategy is ConflictStrategy.CURVATURE

    def test_patch_template_is_a_full_editable_patch(self, tmp_path: Path) -> None:
        """A template shows every knob at its default and parses cleanly."""
        plugin = tmp_path / "SomeMod.esp"
        plugin.write_bytes(b"")
        sidecar = write_patch_template(plugin)
        assert sidecar.name == "SomeMod.mergedlands.toml"
        text = sidecar.read_text(encoding="utf-8")
        assert "conflict_strategy" in text  # the header lists the options
        for name in LAYER_NAMES:
            assert f"[{name}]" in text
        meta = load_meta(plugin)  # parses without error
        assert meta.meta_type == "Patch"
        assert meta.allowed_layers() != LandData.NONE  # defaults include everything

    def test_write_meta_reports_an_unwritable_path(self, tmp_path: Path) -> None:
        """A write into a missing directory fails loudly."""
        plugin = tmp_path / "gone" / "SomeMod.esp"
        with pytest.raises(MetaError):
            write_meta(plugin, PluginMeta())

    def test_write_settings_round_trips_gui_choices(self, tmp_path: Path) -> None:
        """What the editor writes parses back to the same PluginMeta.

        The GUI editor builds a PluginMeta from its controls and calls
        write_settings; this pins that the chosen strategy and dropped layer
        survive the write/read round trip and that the header is included.
        """
        plugin = tmp_path / "SomeMod.esp"
        plugin.write_bytes(b"")
        meta = PluginMeta(
            meta_type="Patch",
            layers={
                "texture_indices": MergeSettings(included=False),
                "world_map_data": MergeSettings(conflict_strategy=ConflictStrategy.OVERWRITE),
            },
        )
        sidecar = write_settings(plugin, meta)
        assert "conflict_strategy" in sidecar.read_text(encoding="utf-8")  # header present
        reloaded = load_meta(plugin)
        assert reloaded.meta_type == "Patch"
        assert reloaded.settings_for("texture_indices").included is False
        assert (
            reloaded.settings_for("world_map_data").conflict_strategy is ConflictStrategy.OVERWRITE
        )

    def test_public_strategy_and_meta_constants_match_the_schema(self) -> None:
        """The GUI's dropdowns read these, so they must stay in step with the
        parser: every name round-trips through the value and back."""
        assert set(META_TYPES) == {"Auto", "Patch", "MergedLands"}
        assert STRATEGY_NAMES[0] == "Auto"  # the default is offered first
        for name in STRATEGY_NAMES:
            assert strategy_display_name(strategy_from_name(name)) == name

    def test_describe_meta_names_every_layer_and_its_state(self) -> None:
        """The verbose log line spells out on/off and the strategy per layer.

        A troubleshooting log has to show *why* a plugin merged as it did, which
        means naming each layer's setting, not just that a sidecar was present.
        """
        from wraithguard.land.merge import ConflictStrategy

        meta = PluginMeta(
            meta_type="Patch",
            layers={
                "texture_indices": MergeSettings(included=False),
                "world_map_data": MergeSettings(conflict_strategy=ConflictStrategy.OVERWRITE),
            },
        )
        line = _describe_meta(meta)
        assert "meta_type=Patch" in line
        assert "texture_indices: OFF/" in line  # excluded layer flagged
        assert "world_map_data: on/Overwrite" in line  # strategy named
        assert "height_map: on/Auto" in line  # a default layer is still shown


def _parse(text: str, tmp_path: Path) -> dict:
    """Parse rendered TOML back into a document, via a temp file."""
    scratch = tmp_path / "roundtrip.mergedlands.toml"
    scratch.write_text(text, encoding="utf-8")
    from wraithguard.land.meta import _load_toml

    return _load_toml(scratch)


class TestDeclaredMasters:
    """The merged file declares what it read, not the whole load order."""

    def _outcome(self, editors: list[str]) -> MergeOutcome:
        """A one-cell outcome edited by ``editors``."""
        outcome = MergeOutcome()
        outcome.cells[(0, 0)] = MergedCell(coords=(0, 0), editors=list(editors))
        return outcome

    def _reference(self, source: str) -> Landmass:
        """A reference landmass whose only cell came from ``source``."""
        landmass = Landmass(name="reference")
        landmass.sources[(0, 0)] = source
        return landmass

    def test_only_contributing_plugins_are_declared(self) -> None:
        """Twenty-seven masters in the order does not mean twenty-seven here."""
        order = ["Morrowind.esm", "Unused.esm", "a.esp", "b.esp"]
        declared = _contributors(
            self._outcome(["a.esp"]), [], self._reference("Morrowind.esm"), order
        )
        assert declared == ["Morrowind.esm", "a.esp"]

    def test_the_master_behind_the_cell_is_declared(self) -> None:
        """A LAND record overrides one; the file it overrides must be named."""
        declared = _contributors(
            self._outcome(["a.esp"]),
            [],
            self._reference("Bloodmoon.esm"),
            ["Bloodmoon.esm", "a.esp"],
        )
        assert "Bloodmoon.esm" in declared

    def test_texture_sources_are_declared(self) -> None:
        """A texture's file name came from somewhere, and that is a dependency."""
        declared = _contributors(
            self._outcome([]),
            ["Tamriel_Data.esm"],
            self._reference("Morrowind.esm"),
            ["Morrowind.esm", "Tamriel_Data.esm"],
        )
        assert declared == ["Morrowind.esm", "Tamriel_Data.esm"]

    def test_load_order_decides_the_order(self) -> None:
        """Masters are declared in the order they load, not alphabetically."""
        order = ["z.esm", "a.esp"]
        declared = _contributors(self._outcome(["a.esp"]), [], self._reference("z.esm"), order)
        assert declared == ["z.esm", "a.esp"]

    def test_a_contributor_missing_from_the_order_is_still_declared(self) -> None:
        """Omitting a real dependency is worse than declaring one out of order."""
        declared = _contributors(
            self._outcome(["ghost.esp"]), [], self._reference("Morrowind.esm"), ["Morrowind.esm"]
        )
        assert declared == ["Morrowind.esm", "ghost.esp"]

    def test_a_cell_absent_from_the_reference_declares_no_origin(self) -> None:
        """A brand-new cell no master ever defined has no origin to add.

        ``reference.sources`` simply has nothing for its coordinates -- the
        editors are still declared, just without an origin master tagging along.
        """
        empty_reference = Landmass(name="reference")
        declared = _contributors(
            self._outcome(["a.esp"]), [], empty_reference, ["Morrowind.esm", "a.esp"]
        )
        assert declared == ["a.esp"]


class TestNonPluginsAreNotMerged:
    """A load order may name things that are not plugins.

    ``.omwscripts`` is OpenMW's list of Lua scripts to attach. It holds no
    records, it belongs in the load order, and it is not something the merge
    can read. Reporting it under "could not be read" is noise that looks like
    a problem the user needs to fix.
    """

    def test_omwscripts_is_neither_master_nor_mod(self) -> None:
        """It is dropped before either list is built."""
        masters, mods = _split_order(
            ["Morrowind.esm", "a.esp", "Natural Wildlife.omwscripts", "b.omwaddon"]
        )
        assert masters == ["Morrowind.esm"]
        assert mods == ["a.esp", "b.omwaddon"]

    def test_the_case_it_is_written_in_does_not_matter(self) -> None:
        """Load order files are not consistent about case."""
        _masters, mods = _split_order(["A.OMWSCRIPTS"])
        assert mods == []

    def test_real_plugins_are_untouched(self) -> None:
        """The filter must not eat anything that does hold records."""
        masters, mods = _split_order(["a.esm", "b.esp", "c.omwaddon", "d.omwgame"])
        assert masters == ["a.esm", "d.omwgame"]
        assert mods == ["b.esp", "c.omwaddon"]


def _flat_heights(value: int = 0) -> array:
    """A flat 65x65 height grid, uniform, as MergedCell stores it."""
    return array("i", [value] * (LAND_SIZE * LAND_SIZE))


class TestBuildRecords:
    """Turning merged cells into LAND records -- service._build_records.

    This is the seam between the merge pipeline's flat, position-addressed
    grids on :class:`MergedCell` and :func:`build_landscape_record`'s nested
    rows. A reshape bug here does not raise -- it silently paints the wrong
    texture or height at some vertex, which is why several of these decode
    the real encoded record rather than trusting the intermediate shape.
    """

    def _outcome(self, cells: dict[tuple[int, int], MergedCell]) -> MergeOutcome:
        outcome = MergeOutcome()
        outcome.cells.update(cells)
        return outcome

    def test_a_fully_populated_cell_produces_one_record(self) -> None:
        """Heights, textures, and colors together yield a record and a pending grid."""
        cell = MergedCell(
            coords=(1, 2),
            heights=_flat_heights(10),
            textures=[3] * (TEXTURE_SIZE * TEXTURE_SIZE),
            colors=[200] * (LAND_SIZE * LAND_SIZE * 3),
        )
        records, pending, used, clamped = _build_records(
            self._outcome({(1, 2): cell}), lambda _: None
        )
        assert len(records) == 1
        assert records[0]["grid"] == [1, 2]
        assert len(pending) == 1
        assert used == {3}
        assert clamped == 0

    def test_a_cell_without_textures_is_not_pending(self) -> None:
        """Nothing to compact means nothing queued for the texture pass."""
        cell = MergedCell(coords=(0, 0), heights=_flat_heights(5))
        records, pending, used, _clamped = _build_records(
            self._outcome({(0, 0): cell}), lambda _: None
        )
        assert len(records) == 1
        assert pending == []
        assert used == set()

    def test_cells_are_written_in_coordinate_order(self) -> None:
        """Dict iteration order is insertion order, not sorted -- the output must be."""
        cells = {
            (5, 5): MergedCell(coords=(5, 5), heights=_flat_heights(1)),
            (-1, 0): MergedCell(coords=(-1, 0), heights=_flat_heights(1)),
            (0, 0): MergedCell(coords=(0, 0), heights=_flat_heights(1)),
        }
        records, _pending, _used, _clamped = _build_records(self._outcome(cells), lambda _: None)
        assert [tuple(r["grid"]) for r in records] == [(-1, 0), (0, 0), (5, 5)]

    def test_texture_grid_round_trips_through_the_real_encoder(self) -> None:
        """Each flat value must land at its own vertex, not a neighbour's."""
        flat = [(x * 7 + y * 13) % 64 for y in range(TEXTURE_SIZE) for x in range(TEXTURE_SIZE)]
        cell = MergedCell(coords=(0, 0), heights=_flat_heights(0), textures=flat)
        records, pending, _used, _clamped = _build_records(
            self._outcome({(0, 0): cell}), lambda _: None
        )
        record, rows = pending[0]
        assert record is records[0]
        expected = [flat[y * TEXTURE_SIZE : (y + 1) * TEXTURE_SIZE] for y in range(TEXTURE_SIZE)]
        assert rows == expected

    def test_colors_are_clamped_to_the_byte_range_before_encoding(self) -> None:
        """An out-of-range channel must not reach the encoder raw.

        Checked through the real encode/decode round trip, not the
        intermediate tuple -- a clamp that happens in Python but never makes
        it into the packed bytes would still fail to protect the plugin.
        """
        flat = [128] * (LAND_SIZE * LAND_SIZE * 3)
        flat[0], flat[1], flat[2] = -50, 999, 128  # first vertex, r/g/b
        cell = MergedCell(coords=(0, 0), heights=_flat_heights(0), colors=flat)
        records, _pending, _used, _clamped = _build_records(
            self._outcome({(0, 0): cell}), lambda _: None
        )
        colors = decode_vertex_colors(records[0]["vertex_colors"]["data"])
        assert colors[0][0] == (0, 255, 128)

    def test_world_map_and_normals_reshape_to_their_own_vertex(self) -> None:
        """The other two flat-to-nested reshapes get the same treatment as textures.

        Heights are held well above sea level so sink_underwater_land (which
        build_landscape_record applies to any world map once heights are
        given) leaves the supplied map untouched -- otherwise a correct
        reshape and a sea-level rewrite would be indistinguishable here.
        """
        from wraithguard.tes3fields.landscape import (
            WNAM_SIZE,
            decode_vertex_normals,
            decode_world_map,
        )

        world_flat = [(x + y) % 5 - 2 for y in range(WNAM_SIZE) for x in range(WNAM_SIZE)]
        # (x, y, z) int8 triples, distinct per vertex -- a flat "straight up"
        # normal everywhere (what a uniform-height cell would recompute to)
        # would silently pass even a transposed or otherwise broken reshape,
        # so each vertex needs its own value to actually pin the mapping.
        normals_flat: list[int] = []
        for y in range(LAND_SIZE):
            for x in range(LAND_SIZE):
                normals_flat.extend([x % 100, y % 100, 50])
        cell = MergedCell(
            coords=(0, 0),
            heights=_flat_heights(100),
            world_map=world_flat,
            normals=normals_flat,
        )
        records, _pending, _used, _clamped = _build_records(
            self._outcome({(0, 0): cell}), lambda _: None
        )
        decoded_map = decode_world_map(records[0]["world_map_data"]["data"])
        expected_map = [world_flat[y * WNAM_SIZE : (y + 1) * WNAM_SIZE] for y in range(WNAM_SIZE)]
        assert decoded_map == expected_map

        decoded_normals = decode_vertex_normals(records[0]["vertex_normals"]["data"])
        assert decoded_normals[3][7] == (7 % 100, 3 % 100, 50)
        assert decoded_normals[40][12] == (12 % 100, 40 % 100, 50)

    def test_a_cell_with_no_layers_is_skipped_and_reported(self) -> None:
        """build_landscape_record refuses an empty cell; one bad cell must not abort the merge."""
        cell = MergedCell(coords=(9, 9))  # heights, textures, colors all None
        lines: list[str] = []
        records, pending, used, clamped = _build_records(
            self._outcome({(9, 9): cell}), lines.append
        )
        assert records == []
        assert pending == []
        assert used == set()
        assert clamped == 0
        assert any("could not be written" in line for line in lines)

    def test_the_clamp_count_is_summed_and_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_build_records aggregates and reports clamps; it does not decide them.

        Which vertices clamp is build_landscape_record's job (and is pinned in
        test_land_emit.py already) -- this only checks the bookkeeping around
        whatever it returns.
        """
        import wraithguard.land.service as svc

        def fake_build(
            coords: tuple[int, int], **_kw: object
        ) -> tuple[dict[str, object], list[object]]:
            return {"type": "Land", "grid": list(coords)}, [(0, 0), (1, 1)]

        monkeypatch.setattr(svc, "build_landscape_record", fake_build)
        cell = MergedCell(coords=(0, 0), heights=_flat_heights(0))
        lines: list[str] = []
        _records, _pending, _used, clamped = _build_records(
            self._outcome({(0, 0): cell}), lines.append
        )
        assert clamped == 2
        assert any("2 vertex/vertices" in line and "clamped" in line for line in lines)

    def test_no_clamps_means_no_clamp_line(self) -> None:
        """A fully representable cell should not add report noise."""
        cell = MergedCell(coords=(0, 0), heights=_flat_heights(0))
        lines: list[str] = []
        _build_records(self._outcome({(0, 0): cell}), lines.append)
        assert not any("clamped" in line for line in lines)


class TestFinishTextures:
    """Compacting the shared texture table -- service._finish_textures."""

    def _known(self, count: int) -> KnownTextures:
        """A shared table of ``count`` textures, sourced from ``src.esp``."""
        known = KnownTextures()
        known.observe(
            "src.esp",
            [
                {"type": "LandscapeTexture", "id": f"T{i}", "index": i, "file_name": f"t{i}.tga"}
                for i in range(count)
            ],
        )
        return known

    def test_no_pending_returns_nothing_and_reports_nothing(self) -> None:
        """An empty pending list is a real 'no cell painted anything', not an edge case to warn about."""
        lines: list[str] = []
        records, sources = _finish_textures([], {0, 1, 2}, self._known(5), lines.append)
        assert records == []
        assert sources == []
        assert lines == []

    def test_records_get_their_compacted_grid_attached(self) -> None:
        """The point of the function: pending records leave with real texture_indices data."""
        known = self._known(2)
        rows = [[0] * TEXTURE_SIZE for _ in range(TEXTURE_SIZE)]
        record: dict[str, object] = {}
        # `used` lives in VTEX space (vtex_of(ltex_index)), not the raw LTEX
        # index -- compact_textures diffs against painted grid values.
        texture_records, _sources = _finish_textures(
            [(record, rows)], {vtex_of(0)}, known, lambda _: None
        )
        assert "texture_indices" in record
        assert len(texture_records) == 1

    def test_sources_are_deduplicated_and_sorted(self) -> None:
        """Two mods contribute textures; the dependency list names each once, in order."""
        known = KnownTextures()
        known.observe(
            "b.esp", [{"type": "LandscapeTexture", "id": "B", "index": 0, "file_name": "b.tga"}]
        )
        known.observe(
            "a.esp", [{"type": "LandscapeTexture", "id": "A", "index": 1, "file_name": "a.tga"}]
        )
        rows = [[0] * TEXTURE_SIZE for _ in range(TEXTURE_SIZE)]
        _records, sources = _finish_textures(
            [({}, rows)], {vtex_of(0), vtex_of(1)}, known, lambda _: None
        )
        assert sources == ["a.esp", "b.esp"]

    def test_unresolved_with_substitution_mentions_the_fallback(self) -> None:
        """A missing master's index still loads -- the report says it was substituted."""
        known = self._known(2)
        rows = [[0] * TEXTURE_SIZE for _ in range(TEXTURE_SIZE)]
        lines: list[str] = []
        _finish_textures(
            [({}, rows)], {vtex_of(0), vtex_of(999)}, known, lines.append, substitute_unknown=True
        )
        assert any("substituted with the fallback" in line for line in lines)

    def test_unresolved_without_substitution_mentions_a_dangling_index(self) -> None:
        """Opting out keeps the index honest instead of silently rewriting it."""
        known = self._known(2)
        rows = [[0] * TEXTURE_SIZE for _ in range(TEXTURE_SIZE)]
        lines: list[str] = []
        _finish_textures(
            [({}, rows)], {vtex_of(0), vtex_of(999)}, known, lines.append, substitute_unknown=False
        )
        assert any("dangling index" in line for line in lines)

    def test_a_malformed_texture_grid_is_reported_not_raised(self) -> None:
        """One cell's bad grid must not abort a merge that is otherwise fine."""
        known = self._known(1)
        bad_rows = [[0]]  # not 16x16
        lines: list[str] = []
        texture_records, _sources = _finish_textures(
            [({}, bad_rows)], {vtex_of(0)}, known, lines.append
        )
        assert any("texture grid could not be written" in line for line in lines)
        # Compaction itself still ran -- the table is reported regardless.
        assert len(texture_records) == 1


class TestWrite:
    """Serialising and handing off to tes3conv -- service._write."""

    def test_a_missing_master_is_reported(self, tmp_path: Path) -> None:
        """The write must not attempt to measure a master that was never found."""
        with pytest.raises(MergeServiceError, match="cannot find master"):
            _write([], ["ghost.esm"], [tmp_path], tmp_path / "out.esp", "tes3conv")

    def test_an_unmeasurable_master_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A master that resolves but cannot be stat'd is named, not swallowed."""
        import wraithguard.land.service as svc

        class _Unstatable:
            def stat(self) -> None:
                raise OSError("permission denied")

        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: _Unstatable())
        with pytest.raises(MergeServiceError, match="cannot measure master"):
            _write([], ["a.esm"], [tmp_path], tmp_path / "out.esp", "tes3conv")

    def test_a_missing_converter_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No real tes3conv is needed to prove this path -- the binary just has to not exist."""
        import wraithguard.land.service as svc

        (tmp_path / "a.esm").write_bytes(b"x" * 100)
        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: tmp_path / "a.esm")
        with pytest.raises(MergeServiceError, match="could not be run"):
            _write(
                [], ["a.esm"], [tmp_path], tmp_path / "out.esp", str(tmp_path / "no-such-tes3conv")
            )

    def test_a_nonzero_exit_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_tes3conv: str
    ) -> None:
        """tes3conv's own complaint is what the user needs to see, same as _records_via."""
        import wraithguard.land.service as svc

        (tmp_path / "a.esm").write_bytes(b"x" * 100)
        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: tmp_path / "a.esm")
        # A record type tes3conv's own schema does not know -- its rejection
        # is what stands in for a hand-scripted "bad json" here.
        bad_record = {"type": "NotARealRecordType", "flags": ""}
        with pytest.raises(MergeServiceError, match="refused the merged JSON"):
            _write([bad_record], ["a.esm"], [tmp_path], tmp_path / "out.esp", real_tes3conv)

    def test_success_needs_no_return_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_tes3conv: str
    ) -> None:
        """The happy path -- nothing raised, and the output directory is created for it."""
        import wraithguard.land.service as svc

        (tmp_path / "a.esm").write_bytes(b"x" * 100)
        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: tmp_path / "a.esm")
        target = tmp_path / "nested" / "out.esp"
        _write([], ["a.esm"], [tmp_path], target, real_tes3conv)
        assert target.parent.is_dir()
        assert target.is_file()

    def test_the_console_window_is_suppressed_here_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same --noconsole flash _records_via fixes applies to the write side."""
        import types

        import wraithguard.land.service as svc

        seen: dict[str, object] = {}

        def fake_run(argv: list[str], **kwargs: object) -> types.SimpleNamespace:
            seen.update(kwargs)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        (tmp_path / "a.esm").write_bytes(b"x" * 100)
        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: tmp_path / "a.esm")
        monkeypatch.setattr(svc, "no_window_kwargs", lambda: {"creationflags": 0x08000000})
        monkeypatch.setattr(svc.subprocess, "run", fake_run)

        _write([], ["a.esm"], [tmp_path], tmp_path / "out.esp", "tes3conv")
        assert seen.get("creationflags") == 0x08000000

    def test_a_native_encode_failure_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``converter=None`` still has a failure path -- the native encoder can refuse too.

        Mirrors test_a_nonzero_exit_is_reported, but for the in-process writer:
        no tes3conv (real or fake) needed to prove it, since a record type
        neither backend recognises is refused the same way natively.
        """
        import wraithguard.land.service as svc

        (tmp_path / "a.esm").write_bytes(b"x" * 100)
        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: tmp_path / "a.esm")
        bad_record = {"type": "NotARealRecordType", "flags": ""}
        with pytest.raises(MergeServiceError, match="could not encode the merged plugin natively"):
            _write([bad_record], ["a.esm"], [tmp_path], tmp_path / "out.esp", None)

    def test_a_nonzero_tes3conv_exit_is_reported_without_a_real_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same outcome as test_a_nonzero_exit_is_reported, without needing real_tes3conv.

        subprocess.run's return handling is what is under test here, not
        tes3conv's own JSON validation -- a fake with a nonzero returncode
        proves it identically and runs everywhere, tes3conv installed or not.
        """
        import wraithguard.land.service as svc

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="tes3conv says no")

        (tmp_path / "a.esm").write_bytes(b"x" * 100)
        monkeypatch.setattr(svc, "resolve_plugin", lambda *_a, **_k: tmp_path / "a.esm")
        monkeypatch.setattr(svc.subprocess, "run", fake_run)
        with pytest.raises(MergeServiceError, match="refused the merged JSON"):
            _write([], ["a.esm"], [tmp_path], tmp_path / "out.esp", "tes3conv")


class TestBuildMergedLandsHappyPath:
    """End-to-end coverage for build_merged_lands' main body.

    This was the largest untested block in the file: everything from
    "reading masters" through "wrote {marker}" ran only in production. Real
    tes3conv is the one thing not available here, so subprocess.run is
    replaced with a fake that hands back records built through the actual
    build_landscape_record encoder -- everything downstream of that
    (build_reference, merge_landmass, finish, _build_records,
    _finish_textures, _contributors, _write, write_merged_marker) is the real
    code, unmodified. has_landscape is forced True because the plugin
    "files" here are dummy bytes rather than real TES3 headers; its own
    native-format sniffing is covered separately in test_land_native.py.
    """

    def _rig(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        records_by_name: dict[str, list[dict[str, object]]],
    ) -> Path:
        """A Data Files folder with a dummy file per name in records_by_name,
        and tes3conv stubbed to serve exactly those records.
        """
        import wraithguard.land.service as svc

        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        for name in records_by_name:
            # resolve_plugin and _write both need a real, non-empty file: one
            # to find it, the other to measure it for the plugin header.
            (data_dir / name).write_bytes(b"x" * 1000)

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            src = Path(argv[1])
            dst = Path(argv[2])
            if src.name == "merged.json":
                # _write's own conversion call, reading the merge's own
                # output rather than a named plugin -- nothing to look up,
                # just prove something got "converted".
                dst.write_bytes(b"fake esp bytes")
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            records = records_by_name.get(src.name)
            if records is None:
                return types.SimpleNamespace(
                    returncode=1, stdout="", stderr=f"no fixture for {src.name}"
                )
            dst.write_text(json.dumps(records), encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(svc, "has_landscape", lambda *_a, **_k: True)
        monkeypatch.setattr(svc.subprocess, "run", fake_run)
        return data_dir

    def _land(
        self,
        coords: tuple[int, int],
        heights: list[list[float]] | None = None,
        colors: list[list[tuple[int, int, int]]] | None = None,
    ) -> dict[str, object]:
        """A real LAND record, via the same encoder the merge itself uses."""
        record, _clamps = build_landscape_record(coords, heights=heights, colors=colors)
        return record

    def _conflicting_fixture(self) -> dict[str, list[dict[str, object]]]:
        """A cell two mods edit in different, non-overlapping ways.

        Neither mod alone produces the right cell -- ModHeights leaves the
        reference's colors behind, ModColors leaves the reference's heights
        behind -- so this needs an actual merge, not just "last mod wins",
        and cleaning must not drop it as redundant.
        """
        heights_reference = [[100.0] * 65 for _ in range(65)]
        heights_edited = [[150.0] * 65 for _ in range(65)]
        colors_edited = [[(200, 50, 50)] * 65 for _ in range(65)]
        return {
            "Morrowind.esm": [self._land((0, 0), heights=heights_reference)],
            "ModHeights.esp": [self._land((0, 0), heights=heights_edited)],
            "ModColors.esp": [self._land((0, 0), heights=heights_reference, colors=colors_edited)],
        }

    def test_two_mods_editing_different_layers_produce_one_merged_cell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case Merged Lands exists for: reconciling edits, not just picking one."""
        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
        )
        assert result.output == data_dir / "Merged Lands.esp"
        assert result.output.is_file()
        assert result.cells_written == 1
        assert (data_dir / "Merged Lands.mergedlands.toml").is_file()
        assert any("declaring 3 master(s)" in line for line in result.lines)

    def test_a_single_uninvolved_mod_needs_no_merge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One mod editing one cell is exactly what the plain load order already shows.

        Nothing here needs the merge tool at all, and cleaning is supposed to
        say so rather than write a redundant patch.
        """
        records_by_name = {
            "Morrowind.esm": [self._land((0, 0), heights=[[100.0] * 65 for _ in range(65)])],
            "TestMod.esp": [self._land((0, 0), heights=[[150.0] * 65 for _ in range(65)])],
        }
        data_dir = self._rig(tmp_path, monkeypatch, records_by_name)

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "TestMod.esp"],
            converter="tes3conv",
        )
        assert result.output is None
        assert result.cells_written == 0
        assert any("nothing to merge" in line for line in result.lines)
        assert not (data_dir / "Merged Lands.esp").exists()

    def test_dry_run_reports_without_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real conflict that WOULD be written stays unwritten under dry_run."""
        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
            dry_run=True,
        )
        assert result.output is None
        assert result.cells_written == 1  # the merge itself still ran
        assert any("dry run" in line for line in result.lines)
        assert not (data_dir / "Merged Lands.esp").exists()

    def test_a_missing_mod_is_reported_not_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A load order naming an uninstalled mod must not abort the whole merge.

        Unlike a missing master (TestMasterFailuresAreExplained), this is not
        fatal: the load order may simply list something the user removed.
        """
        records_by_name = {
            "Morrowind.esm": [self._land((0, 0), heights=[[100.0] * 65 for _ in range(65)])]
        }
        data_dir = self._rig(tmp_path, monkeypatch, records_by_name)

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ghost.esp"],
            converter="tes3conv",
            verbose=True,
        )
        assert any("ghost.esp (not found)" in line for line in result.lines)

    def test_the_thread_pool_path_produces_the_same_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reading mods in parallel must not change what gets merged.

        _READ_WORKERS is bounded by os.cpu_count(); a single-core sandbox
        would otherwise always take the sequential generator branch and this
        module's ThreadPoolExecutor path would never run in CI at all.
        """
        import wraithguard.land.service as svc

        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())
        monkeypatch.setattr(svc.os, "cpu_count", lambda: 4)

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
        )
        assert result.cells_written == 1

    def test_a_mods_settings_are_named_in_verbose_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A .mergedlands.toml sidecar is real per-plugin detail, not just a flag.

        Written through write_settings and read back through the real
        load_meta -- nothing about metas is mocked here.
        """
        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())
        write_settings(
            data_dir / "ModHeights.esp",
            PluginMeta(layers={"world_map_data": MergeSettings(included=True)}),
        )

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
            verbose=True,
        )
        assert any("carry .mergedlands.toml settings" in line for line in result.lines)
        assert any("ModHeights.esp: meta_type=" in line for line in result.lines)

    def test_an_absent_sidecar_folder_falls_back_without_failing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sidecars is an optimisation; a stale/missing folder must not stop the merge."""
        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
            sidecars=tmp_path / "no-such-sidecar-folder",
        )
        assert result.cells_written == 1

    def test_a_master_that_fails_both_backends_is_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A master neither tes3conv nor the native reader can parse must abort the merge.

        Unlike a missing master (TestMasterFailuresAreExplained), this one
        resolves fine -- the file is right there -- but is unreadable by
        either backend, which has to stop the run rather than build a
        reference from nothing.
        """
        heights = [[100.0] * 65 for _ in range(65)]
        data_dir = self._rig(
            tmp_path, monkeypatch, {"Morrowind.esm": [self._land((0, 0), heights)]}
        )
        # On disk, but absent from every fixture: the fake tes3conv refuses
        # it (no entry to serve), and the native fallback refuses it too --
        # it is dummy bytes, not a real plugin.
        (data_dir / "Bad.esm").write_bytes(b"x" * 1000)

        with pytest.raises(MergeServiceError, match="could not read master Bad"):
            build_merged_lands(
                data_files=[data_dir],
                load_order=["Morrowind.esm", "Bad.esm"],
                converter="tes3conv",
            )

    def test_a_mod_that_fails_both_backends_is_reported_not_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mod (unlike a master) that neither backend can read is unreadable, not fatal.

        Distinct from test_a_missing_mod_is_reported_not_fatal: that mod was
        never found at all. This one resolves and is read for, but both
        backends refuse it -- a real, corrupt file rather than an absent one.
        """
        heights = [[100.0] * 65 for _ in range(65)]
        data_dir = self._rig(
            tmp_path, monkeypatch, {"Morrowind.esm": [self._land((0, 0), heights)]}
        )
        (data_dir / "Corrupt.esp").write_bytes(b"x" * 1000)

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "Corrupt.esp"],
            converter="tes3conv",
            verbose=True,
        )
        assert any("Corrupt.esp (" in line and "not found" not in line for line in result.lines)

    def test_settings_are_named_only_in_verbose_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline 'carries settings' line does not need verbose; the per-plugin detail does.

        Complements test_a_mods_settings_are_named_in_verbose_output, which
        always ran with verbose=True and so never exercised the non-verbose
        half of this same branch.
        """
        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())
        write_settings(
            data_dir / "ModHeights.esp",
            PluginMeta(layers={"world_map_data": MergeSettings(included=True)}),
        )

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
        )
        assert any("carry .mergedlands.toml settings" in line for line in result.lines)
        assert not any("ModHeights.esp: meta_type=" in line for line in result.lines)

    def test_a_plugin_excluded_entirely_is_skipped_and_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An every-layer-excluded plugin is skipped by the merge, and the skip is reported.

        The skip itself is pinned at the pipeline level
        (test_a_plugin_with_every_layer_excluded_is_skipped); this pins that
        build_merged_lands actually says so.
        """
        records_by_name = self._conflicting_fixture()
        data_dir = self._rig(tmp_path, monkeypatch, records_by_name)
        write_settings(
            data_dir / "ModColors.esp",
            PluginMeta(layers={name: MergeSettings(included=False) for name in LAYER_NAMES}),
        )

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
        )
        assert any("skipped ModColors.esp: every layer excluded" in line for line in result.lines)

    def test_include_cells_emits_cell_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """include_cells=True adds CELL records alongside the LAND records."""
        records_by_name = self._conflicting_fixture()
        cell_record = {"type": "Cell", "data": {"grid": [0, 0], "flags": ""}}
        records_by_name["Morrowind.esm"] = [*records_by_name["Morrowind.esm"], cell_record]
        data_dir = self._rig(tmp_path, monkeypatch, records_by_name)

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
            include_cells=True,
        )
        assert result.cell_records == 1
        assert any("CELL record(s)" in line for line in result.lines)

    def test_a_marker_write_failure_after_a_successful_merge_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The plugin can be written and still fail at the very last step: the marker.

        write_merged_marker's own failure mode is pinned directly
        (test_an_unwritable_location_is_reported); this pins that
        build_merged_lands converts it into a MergeServiceError rather than
        leaving a written plugin with no marker and no explanation.
        """
        import wraithguard.land.service as svc

        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())

        def fake_marker(*_a: object, **_k: object) -> None:
            raise MetaError("could not write the marker")

        monkeypatch.setattr(svc, "write_merged_marker", fake_marker)

        with pytest.raises(MergeServiceError, match="could not write the marker"):
            build_merged_lands(
                data_files=[data_dir],
                load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
                converter="tes3conv",
            )
        assert (data_dir / "Merged Lands.esp").is_file()  # the plugin itself was written

    def test_reporting_reflects_borrowed_and_slope_limited_outcomes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The borrowed/slope-adjusted/slope-pinned report lines, when finish() says so.

        finish()'s own decisions about what to borrow and adjust are pinned
        directly against real terrain (test_land_slope.py,
        add_reference_neighbours' own tests); this pins that
        build_merged_lands reports whatever it decided, faithfully, rather
        than re-deriving the same terrain math a second time here.
        """
        import wraithguard.land.service as svc

        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())
        real_finish = svc.finish

        def fake_finish(outcome: MergeOutcome, reference: object, **kwargs: object) -> MergeOutcome:
            real_finish(outcome, reference, **kwargs)
            outcome.borrowed = 2
            outcome.slopes.adjusted = 3
            outcome.slopes.pinned = 1
            return outcome

        monkeypatch.setattr(svc, "finish", fake_finish)

        result = build_merged_lands(
            data_files=[data_dir],
            load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
            converter="tes3conv",
        )
        assert any("brought in 2 untouched cell(s)" in line for line in result.lines)
        assert any("slope limiter moved 3 vertex/vertices" in line for line in result.lines)
        assert any("1 adjustment(s) refused" in line for line in result.lines)

    def test_a_seam_tear_that_survives_repair_aborts_the_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A border seam repair could not close must stop the merge, not ship a visible tear.

        find_tears itself is pinned directly (test_land_seams.py); this pins
        that build_merged_lands refuses to write when finish() reports one.
        """
        import wraithguard.land.service as svc
        from wraithguard.land.seams import Tear

        data_dir = self._rig(tmp_path, monkeypatch, self._conflicting_fixture())
        real_finish = svc.finish

        def fake_finish(outcome: MergeOutcome, reference: object, **kwargs: object) -> MergeOutcome:
            real_finish(outcome, reference, **kwargs)
            outcome.seams.tears = [Tear(left=(0, 0), right=(1, 0), vertices=3, worst=500)]
            return outcome

        monkeypatch.setattr(svc, "finish", fake_finish)

        with pytest.raises(MergeServiceError, match="1 cell border\\(s\\) still disagree"):
            build_merged_lands(
                data_files=[data_dir],
                load_order=["Morrowind.esm", "ModHeights.esp", "ModColors.esp"],
                converter="tes3conv",
            )
        assert not (data_dir / "Merged Lands.esp").exists()
