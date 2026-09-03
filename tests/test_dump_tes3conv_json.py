"""dump_tes3conv_json: writing each plugin's tes3conv JSON out to a folder.

Seeded the same way test_tes3conv_session.py and
test_conflict_detection.py's TestScanTouchViaSession seed a session -- a
JSON file dropped straight into the dump dir, keyed by the plugin's stem --
so no real tes3conv binary is needed. Previously untested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import wraithguard_toolkit as core

if TYPE_CHECKING:
    import pytest


def _seed(dump_dir: Path, stem: str, records: list[dict]) -> None:
    (dump_dir / f"{stem}.json").write_text(json.dumps(records), encoding="utf-8")


class TestNoSession:
    def test_returns_zero_and_never_touches_the_filesystem(self, tmp_path: Path) -> None:
        outdir = tmp_path / "dump-out"

        n = core.dump_tes3conv_json(None, ["A.esp"], {"A.esp": str(tmp_path / "A.esp")}, outdir)

        assert n == 0
        assert not outdir.exists()


class TestWithSession:
    def test_each_plugins_json_is_written_under_its_own_stem(self, tmp_path: Path) -> None:
        plugin_a = tmp_path / "A.esp"
        plugin_a.write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        _seed(dump_dir, "A", [{"type": "Armor", "id": "cuirass"}])
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        outdir = tmp_path / "out"

        n = core.dump_tes3conv_json(session, ["A.esp"], {"A.esp": str(plugin_a)}, outdir)

        assert n == 1
        written = json.loads((outdir / "A.json").read_text(encoding="utf-8"))
        assert written == [{"type": "Armor", "id": "cuirass"}]

    def test_multiple_plugins_each_get_their_own_file(self, tmp_path: Path) -> None:
        plugin_a = tmp_path / "A.esp"
        plugin_b = tmp_path / "B.esp"
        plugin_a.write_bytes(b"\x00")
        plugin_b.write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        _seed(dump_dir, "A", [{"type": "Armor", "id": "a1"}])
        _seed(dump_dir, "B", [{"type": "Armor", "id": "b1"}])
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        outdir = tmp_path / "out"

        n = core.dump_tes3conv_json(
            session, ["A.esp", "B.esp"], {"A.esp": str(plugin_a), "B.esp": str(plugin_b)}, outdir
        )

        assert n == 2
        assert (outdir / "A.json").exists()
        assert (outdir / "B.json").exists()

    def test_a_plugin_missing_from_paths_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        plugin_a = tmp_path / "A.esp"
        plugin_a.write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        _seed(dump_dir, "A", [{"type": "Armor", "id": "cuirass"}])
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        outdir = tmp_path / "out"

        n = core.dump_tes3conv_json(
            session, ["A.esp", "Ghost.esp"], {"A.esp": str(plugin_a)}, outdir
        )

        assert n == 1
        assert not (outdir / "Ghost.json").exists()

    def test_outdir_is_created_if_it_does_not_exist(self, tmp_path: Path) -> None:
        plugin_a = tmp_path / "A.esp"
        plugin_a.write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        _seed(dump_dir, "A", [{"type": "Armor", "id": "cuirass"}])
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        outdir = tmp_path / "nested" / "does-not-exist-yet"

        core.dump_tes3conv_json(session, ["A.esp"], {"A.esp": str(plugin_a)}, outdir)

        assert outdir.is_dir()

    def test_a_write_failure_for_one_plugin_does_not_stop_the_others(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_a = tmp_path / "A.esp"
        plugin_b = tmp_path / "B.esp"
        plugin_a.write_bytes(b"\x00")
        plugin_b.write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        _seed(dump_dir, "A", [{"type": "Armor", "id": "a1"}])
        _seed(dump_dir, "B", [{"type": "Armor", "id": "b1"}])
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        outdir = tmp_path / "out"
        real_write_text = Path.write_text

        def flaky_write_text(self: Path, *args: object, **kwargs: object) -> int:
            if self.name == "A.json":
                raise OSError("disk full")
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", flaky_write_text)

        n = core.dump_tes3conv_json(
            session, ["A.esp", "B.esp"], {"A.esp": str(plugin_a), "B.esp": str(plugin_b)}, outdir
        )

        assert n == 1
        assert not (outdir / "A.json").exists()
        assert (outdir / "B.json").exists()
