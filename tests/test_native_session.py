"""The native conflict session (:class:`wraithguard_toolkit.NativeEspSession`).

This is the fallback that makes ``tes3conv`` optional: a
:class:`~wraithguard_toolkit.Tes3ConvSession` that converts plugins in process
with the built-in reader instead of shelling out. Because the parent funnels
every method through ``_json_for``, overriding only that gives a drop-in whose
``record_keys`` / ``cells`` / ``record_map`` / ``landscape_records`` all work --
so these build a plugin with the esp *writer*, then read it back through the
native session with no ``tes3conv`` anywhere, and check each surface the
conflict scan, cell map and Merged Lands depend on.

The parity with a *real* ``tes3conv`` session (identical record keys, cells and
landscape records on multi-thousand-record plugins) was checked during
development against the binary; it cannot run in the hermetic suite, so what is
pinned here is that the native path is self-consistent and wired correctly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import wraithguard.esp as esp_module
import wraithguard_toolkit as core
from wraithguard.esp import (
    Cell,
    CellData,
    GameSetting,
    Header,
    Landscape,
    Weapon,
    write_plugin,
)
from wraithguard.esp.flags import CellFlags

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _plugin(path: Path) -> str:
    """Write a small varied plugin with the esp writer and return its path.

    Args:
        path: The file to write.

    Returns:
        The path as a string.
    """
    records = [
        Header(),
        Weapon(id="the_sword", name="The Sword"),
        GameSetting(id="fJumpBase", value=1.5),
        Cell(data=CellData(cell_flags=CellFlags.HAS_WATER)),
        Landscape(grid=(3, 4)),
    ]
    path.write_bytes(write_plugin(records))
    return str(path)


class TestNativeSession:
    """Reading a plugin back through the native session, without tes3conv."""

    def test_engine_is_named_native(self, tmp_path: Path) -> None:
        """The engine label distinguishes it from tes3conv in scan output."""
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        assert session.engine_name == "native"

    def test_records_include_the_header_and_content(self, tmp_path: Path) -> None:
        """``records`` yields every record as a tes3conv-shaped dict."""
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        records = session.records(_plugin(tmp_path / "mine.esp"))
        types = [r.get("type") for r in records]
        assert types[0] == "Header"
        assert {"Weapon", "GameSetting", "Cell", "Landscape"} <= set(types)

    def test_record_map_keys_by_type_and_id(self, tmp_path: Path) -> None:
        """``record_map`` is what field-level diffing indexes by."""
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        mapping = session.record_map(_plugin(tmp_path / "mine.esp"))
        assert ("Weapon", "the_sword") in mapping
        assert mapping[("Weapon", "the_sword")]["name"] == "The Sword"

    def test_record_keys_drive_conflict_detection(self, tmp_path: Path) -> None:
        """``record_keys`` is the compact list the conflict scan compares."""
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        keys = {(rtype, rid) for rtype, rid, *_ in session.record_keys(_plugin(tmp_path / "m.esp"))}
        assert ("GameSetting", "fJumpBase") in keys

    def test_landscape_records_are_available_for_merging(self, tmp_path: Path) -> None:
        """Merged Lands reads terrain through ``landscape_records``."""
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        land = session.landscape_records(_plugin(tmp_path / "mine.esp"))
        assert any(r.get("type") == "Landscape" for r in land)

    def test_a_cached_json_is_reused(self, tmp_path: Path) -> None:
        """The second read hits the on-disk spool, as the tes3conv session does."""
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        path = _plugin(tmp_path / "mine.esp")
        first = session.records(path)
        assert (tmp_path / "mine.json").is_file()
        assert session.records(path) == first

    def test_a_json_already_on_disk_from_a_prior_run_is_reused_without_reconverting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh session (empty in-memory cache) still finds a valid spool file on disk."""
        path = _plugin(tmp_path / "mine.esp")
        core.NativeEspSession(dump_dir=str(tmp_path), keep=True).records(path)  # writes mine.json
        assert (tmp_path / "mine.json").is_file()

        def _boom(*_a: object, **_k: object) -> None:
            raise AssertionError("re-read the plugin instead of reusing the on-disk JSON")

        monkeypatch.setattr(esp_module, "read_plugin", _boom)
        fresh_session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)
        records = fresh_session.records(path)

        assert any(r.get("type") == "Header" for r in records)

    def test_an_unreadable_plugin_returns_no_records(self, tmp_path: Path) -> None:
        """A file that isn't a valid plugin at all fails read_plugin, not a crash."""
        bad = tmp_path / "corrupt.esp"
        bad.write_bytes(b"not a plugin")
        session = core.NativeEspSession(dump_dir=str(tmp_path), keep=True)

        assert session.records(str(bad)) == []
