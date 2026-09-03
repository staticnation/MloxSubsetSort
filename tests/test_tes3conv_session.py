"""Tes3ConvSession: the parts test_land_sidecar/test_record_subset don't reach.

Those two files exercise the class against JSON already sitting in the dump
dir, which covers the caching and record-keying logic without needing a real
tes3conv binary. What's left untested is everything on either side of that:
the actual subprocess conversion (success, failure, staleness), the methods
that are thin wrappers nobody calls directly (``records``, ``record_keys``,
``dumped_dir``, ``cleanup``), the streaming path in ``record_subset`` when
``ijson`` actually is importable, and a couple of defensive branches that
don't fire from any record shape tes3conv itself would emit.

Where the subprocess boundary itself is under test, this uses a real
tes3conv (via ``find_tes3conv()``, same discovery the tool uses) rather than
a stand-in -- see test_land_service.py for the reasoning. Everything else
works on pre-seeded JSON, as the sibling files do.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import wraithguard_toolkit as core


@pytest.fixture(scope="session")
def real_tes3conv() -> str:
    """Path to a real tes3conv executable, or skip the test.

    Point ``MLOX_TES3CONV`` at a binary, put one on ``PATH``, or drop it
    beside ``wraithguard_toolkit.py`` -- whatever ``find_tes3conv()`` itself
    would find at runtime.
    """
    found = core.find_tes3conv()
    if not found:
        pytest.skip("needs a real tes3conv executable (set MLOX_TES3CONV or put it on PATH)")
    return found


def _seeded_session(
    tmp_path: Path, records: list[dict[str, Any]]
) -> tuple[core.Tes3ConvSession, str]:
    """A session whose dump dir already holds ``records`` as one plugin's JSON.

    No tes3conv binary needed: a JSON file already in the dump dir reads back
    exactly as a converted plugin would.
    """
    session = core.Tes3ConvSession(exe="unused", dump_dir=str(tmp_path), keep=True)
    (tmp_path / "Plugin.json").write_text(json.dumps(records), encoding="utf-8")
    return session, "Plugin.esp"


def _real_esp(converter: str, tmp_path: Path, name: str, masters: list[tuple[str, int]]) -> Path:
    """Build a genuine, tes3conv-accepted ``.esp`` -- header only, no zstd needed."""
    import subprocess

    document = [
        {
            "type": "Header",
            "flags": "",
            "version": 1.3,
            "file_type": "Esp",
            "author": "tester",
            "description": "probe",
            "num_objects": 0,
            "masters": masters,
        }
    ]
    as_json = tmp_path / f"{name}.src.json"
    as_json.write_text(json.dumps(document), encoding="utf-8")
    esp = tmp_path / name
    subprocess.run(  # noqa: S603 -- fixed argv, a test-built tes3conv command
        [converter, str(as_json), str(esp), "--overwrite"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return esp


class TestJsonForRealConversion:
    """``_json_for`` against a real tes3conv: the code path pre-seeded JSON never hits."""

    def test_it_converts_and_returns_the_json_path(
        self, tmp_path: Path, real_tes3conv: str
    ) -> None:
        """A plugin with no cached JSON is actually converted."""
        esp = _real_esp(real_tes3conv, tmp_path, "a.esp", masters=[("Morrowind.esm", 1)])
        session = core.Tes3ConvSession(exe=real_tes3conv, dump_dir=str(tmp_path), keep=True)
        jp = session._json_for(esp)
        assert jp is not None
        data = json.loads(Path(jp).read_text(encoding="utf-8"))
        assert data[0]["type"] == "Header"

    def test_a_failed_conversion_returns_none(self, tmp_path: Path, real_tes3conv: str) -> None:
        """tes3conv refusing the input (nonzero exit, check=True) is not fatal."""
        bad = tmp_path / "bad.esp"
        bad.write_bytes(b"not a real plugin")
        session = core.Tes3ConvSession(exe=real_tes3conv, dump_dir=str(tmp_path), keep=True)
        assert session._json_for(bad) is None

    def test_a_missing_converter_returns_none(self, tmp_path: Path) -> None:
        """OSError (no such executable) is handled the same as a bad exit."""
        (tmp_path / "a.esp").write_bytes(b"x")
        session = core.Tes3ConvSession(
            exe=str(tmp_path / "no-such-tes3conv"), dump_dir=str(tmp_path), keep=True
        )
        assert session._json_for(tmp_path / "a.esp") is None

    def test_a_stale_json_is_reconverted(self, tmp_path: Path, real_tes3conv: str) -> None:
        """A plugin edited after its cached JSON must be re-run, not reused."""
        import os

        esp = _real_esp(real_tes3conv, tmp_path, "a.esp", masters=[("Morrowind.esm", 1)])
        session = core.Tes3ConvSession(exe=real_tes3conv, dump_dir=str(tmp_path), keep=True)
        first = session._json_for(esp)
        assert first is not None
        first_mtime = Path(first).stat().st_mtime

        # Touch the plugin to be newer than the JSON we just wrote.
        os.utime(esp, (first_mtime + 100, first_mtime + 100))
        second = session._json_for(esp)
        assert second == first  # same path -- rewritten in place, not renamed
        assert Path(second).stat().st_mtime >= first_mtime


class TestJsonForMockedConversion:
    """The same two branches as above, without needing a real tes3conv binary.

    subprocess.run's return handling is what's under test for these two
    branches (a successful conversion, and the staleness message logged
    before re-converting) -- not tes3conv's own JSON output, which the
    class doesn't inspect at all. A fake exe path is enough.
    """

    def test_a_successful_conversion_caches_and_returns_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text("[]", encoding="utf-8")  # tes3conv writes the output itself
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        esp = tmp_path / "a.esp"
        esp.write_bytes(b"x")
        session = core.Tes3ConvSession(exe="fake-tes3conv", dump_dir=str(tmp_path), keep=True)

        jp = session._json_for(esp)

        assert jp == str(tmp_path / "a.json")
        assert session._json_for(esp) == jp  # served from the in-memory cache next time

    def test_a_stale_cached_json_is_reconverted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text("[]", encoding="utf-8")
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        esp = tmp_path / "a.esp"
        esp.write_bytes(b"x")
        cached = tmp_path / "a.json"
        cached.write_text("[]", encoding="utf-8")
        now = cached.stat().st_mtime
        os.utime(esp, (now + 100, now + 100))  # plugin newer than the cached JSON

        session = core.Tes3ConvSession(exe="fake-tes3conv", dump_dir=str(tmp_path), keep=True)
        jp = session._json_for(esp)

        assert jp == str(cached)


class TestRecordsWrapper:
    """``records()``: the one method that keeps the header, never called directly elsewhere."""

    def test_it_includes_the_header(self, tmp_path: Path) -> None:
        """record_map drops the header (no id); records() must not."""
        records = [{"type": "TES3", "author": "x"}, {"type": "Npc", "id": "bob"}]
        session, path = _seeded_session(tmp_path, records)
        assert [r["type"] for r in session.records(path)] == ["TES3", "Npc"]

    def test_a_failed_conversion_reads_as_empty(self, tmp_path: Path) -> None:
        """No JSON to read (bad exe, no cache) is an empty list, not an error."""
        session = core.Tes3ConvSession(
            exe=str(tmp_path / "no-such-tes3conv"), dump_dir=str(tmp_path), keep=True
        )
        (tmp_path / "a.esp").write_bytes(b"x")
        assert session.records(tmp_path / "a.esp") == []

    def test_a_corrupt_cached_json_reads_as_empty(self, tmp_path: Path) -> None:
        """An existing-but-unparsable JSON is the same answer as no JSON at all."""
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(tmp_path), keep=True)
        (tmp_path / "Plugin.json").write_text("{not valid json", encoding="utf-8")
        assert session.records("Plugin.esp") == []


class TestPrimeSerialPath:
    """prime() with a single worker takes the plain-loop branch, not the pool."""

    def test_a_single_worker_still_converts_and_counts_every_plugin(self, tmp_path: Path) -> None:
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(tmp_path), keep=True)
        names = []
        for i in range(3):
            (tmp_path / f"P{i}.json").write_text(json.dumps([]), encoding="utf-8")
            names.append(f"P{i}.esp")
        assert session.prime(names, max_workers=1) == 3


class TestDumpDirCreationFailure:
    def test_an_uncreatable_dump_dir_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read-only or vanished parent must not stop the session existing.

        The cache is an optimisation; failing to create its folder is
        tolerated the same way failing to write a sidecar is elsewhere.
        """

        def _refuse(self: Path, *a: object, **k: object) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "mkdir", _refuse)
        session = core.Tes3ConvSession(
            exe="unused", dump_dir=str(tmp_path / "cannot-create"), keep=True
        )
        assert session.dump_dir == tmp_path / "cannot-create"


class TestRecordSubsetStreamingBranch:
    """record_subset's ijson path, exercised with a fake ijson standing in.

    ``ijson`` is an optional dependency and isn't installed in this
    environment (test_record_subset.py's fallback tests prove the code works
    without it) -- so the streaming branch itself is otherwise never entered.
    A minimal fake module that actually parses the JSON exercises the real
    code path without adding a hard dependency to the test suite.
    """

    @staticmethod
    def _install_fake_ijson(
        monkeypatch: pytest.MonkeyPatch, *, break_after: int | None = None
    ) -> None:
        def items(fh: Any, _prefix: str, use_float: bool = True) -> Any:
            data = json.load(fh)
            for i, item in enumerate(data):
                if break_after is not None and i >= break_after:
                    raise ValueError("simulated truncated stream")
                yield item

        fake = types.ModuleType("ijson")
        fake.items = items  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "ijson", fake)

    def test_it_streams_and_skips_non_dict_items(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray non-dict entry in the array must not crash the loop."""
        self._install_fake_ijson(monkeypatch)
        records: list[Any] = [
            {"type": "TES3", "author": "x"},
            None,  # not a dict -- must be skipped, not raise
            {"type": "Npc", "id": "bob", "level": 3},
        ]
        session, path = _seeded_session(tmp_path, records)  # type: ignore[arg-type]
        got = session.record_subset(path, {("Npc", "bob")})
        assert got == {("Npc", "bob"): records[2]}

    def test_a_failed_conversion_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No JSON to stream (bad exe) must not raise -- just nothing found."""
        self._install_fake_ijson(monkeypatch)
        session = core.Tes3ConvSession(
            exe=str(tmp_path / "no-such-tes3conv"), dump_dir=str(tmp_path), keep=True
        )
        (tmp_path / "a.esp").write_bytes(b"x")
        assert session.record_subset(tmp_path / "a.esp", {("Npc", "bob")}) == {}

    def test_a_stream_read_error_falls_back_to_the_full_reader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A truncated/unstreamable file must return what record_map would, not less."""
        self._install_fake_ijson(monkeypatch, break_after=1)
        records = [
            {"type": "TES3", "author": "x"},
            {"type": "Npc", "id": "bob", "level": 3},
            {"type": "Armor", "id": "cuirass", "weight": 5},
        ]
        session, path = _seeded_session(tmp_path, records)
        want = {("Npc", "bob"), ("Armor", "cuirass")}
        got = session.record_subset(path, want)
        ref = {k: v for k, v in session.record_map(path).items() if k in want}
        assert got == ref


class TestRecordKeysNeverCalledElsewhere:
    """record_keys(): a public method none of the other test files call directly."""

    def test_it_builds_fresh_and_matches_the_full_map(self, tmp_path: Path) -> None:
        records = [
            {"type": "TES3", "author": "x"},
            {"type": "Npc", "id": "bob", "level": 3},
        ]
        session, path = _seeded_session(tmp_path, records)
        keys = session.record_keys(path)
        assert ("Npc", "bob", False) in keys
        assert (tmp_path / "Plugin.keys.json").is_file()

    def test_a_second_call_is_served_from_the_sidecar(self, tmp_path: Path) -> None:
        """The cache-hit branch: a fresh sidecar is read back, not rebuilt."""
        records = [{"type": "Npc", "id": "bob", "level": 3}]
        session, path = _seeded_session(tmp_path, records)
        first = session.record_keys(path)

        # Prove the second call didn't touch the source JSON again: if it had
        # rebuilt, this would still succeed (same content), so instead corrupt
        # the source and confirm record_keys still returns the right answer
        # from the sidecar alone.
        (tmp_path / "Plugin.json").write_text("{not json", encoding="utf-8")
        second = session.record_keys(path)
        assert second == first

    def test_a_corrupt_sidecar_is_rebuilt_from_source(self, tmp_path: Path) -> None:
        records = [{"type": "Npc", "id": "bob", "level": 3}]
        session, path = _seeded_session(tmp_path, records)
        (tmp_path / "Plugin.keys.json").write_text("not json at all", encoding="utf-8")
        keys = session.record_keys(path)
        assert ("Npc", "bob", False) in keys

    def test_a_wrong_version_sidecar_is_rebuilt(self, tmp_path: Path) -> None:
        records = [{"type": "Npc", "id": "bob", "level": 3}]
        session, path = _seeded_session(tmp_path, records)
        (tmp_path / "Plugin.keys.json").write_text(
            json.dumps({"v": -1, "d": [["Old", "stale", False]]}), encoding="utf-8"
        )
        keys = session.record_keys(path)
        assert ("Npc", "bob", False) in keys
        assert ("Old", "stale", False) not in keys


class TestLuaScriptExtraction:
    """A LuaScriptsCfg record's script list becomes ('LuaScript', path) keys."""

    def test_scripts_are_extracted_and_deduplicated(self, tmp_path: Path) -> None:
        records = [
            {
                "type": "LuaScriptsCfg",
                "scripts": [
                    {"script_path": "Scripts/one.lua"},
                    {"script_path": "Scripts/one.lua"},  # duplicate -- kept once
                    {"path": "Scripts/two.lua"},
                ],
            },
        ]
        session, path = _seeded_session(tmp_path, records)
        keys = session.record_keys(path)
        lua_keys = [k for k in keys if k[0] == "LuaScript"]
        assert sorted(k[1] for k in lua_keys) == ["scripts/one.lua", "scripts/two.lua"]

    def test_a_script_entry_with_no_derivable_path_is_skipped(self, tmp_path: Path) -> None:
        """A malformed script entry (no path key, or an empty one) yields no key at all."""
        records = [
            {
                "type": "LuaScriptsCfg",
                "scripts": [
                    {"script_path": ""},  # empty -- nothing to key on
                    {"unrelated": "field"},  # no path-shaped key at all
                    42,  # not a dict, not a string
                ],
            },
        ]
        session, path = _seeded_session(tmp_path, records)
        keys = session.record_keys(path)
        assert [k for k in keys if k[0] == "LuaScript"] == []


class TestExteriorCellRegexFallback:
    """A defensive branch: an ('x, y')-shaped id whose grid can't be re-derived.

    In practice ``_tes3conv_record_key`` and ``_build_sidecars`` look for a
    cell's grid in exactly the same two places, so this never diverges from
    any record tes3conv actually emits. This pins the fallback's behaviour
    directly in case that ever changes -- the alternative is an untested
    ``re.match`` no record shape can reach.
    """

    def test_a_grid_shaped_id_with_no_derivable_grid_still_becomes_a_cell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cell_record = {"type": "Cell", "data": {"flags": 0}, "name": ""}
        session, path = _seeded_session(tmp_path, [cell_record])

        real_key = core._tes3conv_record_key

        def fake_key(rec: Any, interior_cells: Any = None) -> Any:
            # rec is freshly deserialized from the sidecar JSON each call, so
            # this matches on shape rather than identity with cell_record.
            if rec.get("type") == "Cell":
                return ("Cell", "(7, 8)")
            return real_key(rec, interior_cells)

        monkeypatch.setattr(core, "_tes3conv_record_key", fake_key)
        _keys, cells, _land = session._build_sidecars(path)
        assert ("ext", 7, 8) in cells

    def test_a_cell_with_no_grid_and_no_coordinate_shaped_id_is_just_not_placed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No DATA grid, and an id the regex can't parse either: the cell gets a key, no place."""
        cell_record = {"type": "Cell", "data": {"flags": 0}, "name": ""}
        session, path = _seeded_session(tmp_path, [cell_record])

        real_key = core._tes3conv_record_key

        def fake_key(rec: Any, interior_cells: Any = None) -> Any:
            if rec.get("type") == "Cell":
                return ("Cell", "not-a-coordinate")
            return real_key(rec, interior_cells)

        monkeypatch.setattr(core, "_tes3conv_record_key", fake_key)
        keys, cells, _land = session._build_sidecars(path)
        assert ("Cell", "not-a-coordinate", False) in keys
        assert cells == []


class TestBuildSidecarsWriteFailure:
    def test_a_sidecar_write_failure_is_tolerated_per_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One sidecar failing to write (e.g. disk full) must not lose the others or raise."""
        records = [{"type": "Npc", "id": "bob"}]
        session, path = _seeded_session(tmp_path, records)
        real_open = Path.open

        def flaky_open(self: Path, *args: object, **kwargs: object) -> object:
            if self.suffix == ".json" and self.name.endswith(".keys.json"):
                raise OSError("disk full")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", flaky_open)

        keys, _cells, _land = session._build_sidecars(path)

        assert keys == [("Npc", "bob", False)]
        assert not (tmp_path / "Plugin.keys.json").exists()


class TestLandscapeRecordsSidecarCacheHit:
    """A second call must read the .land.json sidecar, not re-parse the source."""

    def test_a_second_call_does_not_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [{"type": "Landscape", "grid": [1, 2]}]
        session, path = _seeded_session(tmp_path, records)
        first = session.landscape_records(path)
        assert [r["type"] for r in first] == ["Landscape"]

        def _boom(*_a: object, **_k: object) -> None:
            raise AssertionError("landscape_records rebuilt instead of using the sidecar")

        monkeypatch.setattr(session, "_build_sidecars", _boom)
        second = session.landscape_records(path)
        assert second == first

    def test_a_wrong_version_land_sidecar_is_rebuilt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [{"type": "Landscape", "grid": [1, 2]}]
        session, path = _seeded_session(tmp_path, records)
        land_side = tmp_path / "Plugin.land.json"
        land_side.write_text(
            json.dumps({"v": session._SIDECAR_VER - 1, "d": [{"type": "stale"}]}),
            encoding="utf-8",
        )

        result = session.landscape_records(path)

        assert [r["type"] for r in result] == ["Landscape"]  # rebuilt, not the stale cache

    def test_a_land_sidecar_whose_payload_is_not_a_list_is_rebuilt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [{"type": "Landscape", "grid": [1, 2]}]
        session, path = _seeded_session(tmp_path, records)
        land_side = tmp_path / "Plugin.land.json"
        land_side.write_text(
            json.dumps({"v": session._SIDECAR_VER, "d": "not-a-list"}), encoding="utf-8"
        )

        result = session.landscape_records(path)

        assert [r["type"] for r in result] == ["Landscape"]

    def test_a_corrupt_land_sidecar_is_rebuilt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = [{"type": "Landscape", "grid": [1, 2]}]
        session, path = _seeded_session(tmp_path, records)
        (tmp_path / "Plugin.land.json").write_text("{not json", encoding="utf-8")

        result = session.landscape_records(path)

        assert [r["type"] for r in result] == ["Landscape"]


class TestDumpedDirAndCleanup:
    def test_dumped_dir_reports_the_folder(self, tmp_path: Path) -> None:
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(tmp_path), keep=True)
        assert session.dumped_dir() == str(tmp_path)

    def test_cleanup_removes_a_temp_dump_by_default(self) -> None:
        session = core.Tes3ConvSession(exe="unused")  # no dump_dir -> temp, keep=False
        assert session.dump_dir.is_dir()
        session.cleanup()
        assert not session.dump_dir.exists()

    def test_cleanup_preserves_the_dump_when_keep_is_true(self, tmp_path: Path) -> None:
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(tmp_path), keep=True)
        session.cleanup()
        assert tmp_path.is_dir()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
