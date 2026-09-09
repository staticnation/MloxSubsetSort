"""Regression tests for ``CellPreviewMixin._load_order_plugins``.

The loader once consumed :func:`wraithguard_toolkit.plugin_paths` -- which
returns a ``dict`` of name -> path -- as if it were a list, by zipping the load
order against it. ``zip(order, some_dict)`` walks the dict's *keys*, so every
"path" was really a bare plugin name: ``Path(name).read_bytes()`` then failed
with "No such file", and because dict order is not load order the skip messages
were even mis-paired. These tests pin the dict-lookup contract.

The method touches no widgets, so it runs unbound -- but it lives in a module
that imports Tk at import time, so (like ``test_gui_smoke``) the whole file skips
when Tk is missing and runs under ``xvfb`` in CI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("tkinter", reason="Tk is not installed")

from wraithguard.esp.io import EspError
from wraithguard.gui import cellpreview
from wraithguard.gui.cellpreview import CellPreviewMixin

if TYPE_CHECKING:
    from pathlib import Path


def _install_fakes(monkeypatch: pytest.MonkeyPatch, resolved: dict[str, str]) -> None:
    """Point the loader's module-level dependencies at fakes.

    Args:
        monkeypatch: The pytest fixture.
        resolved: The name -> path map ``plugin_paths`` should return.
    """
    monkeypatch.setattr(cellpreview.core, "plugin_paths", lambda order, index: resolved)
    monkeypatch.setattr(cellpreview, "PluginFileIndex", lambda dirs: object())
    monkeypatch.setattr(cellpreview, "read_plugin", lambda data: [data.decode()])
    monkeypatch.setattr(cellpreview, "read_header", lambda data: type("H", (), {"masters": []})())


def test_it_resolves_each_name_through_the_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plugin present in the map is read from its resolved path, not its name.

    Args:
        monkeypatch: The pytest fixture.
        tmp_path: A temporary directory for the fake plugin files.
    """
    a = tmp_path / "A.esp"
    a.write_bytes(b"alpha")
    b = tmp_path / "B.esp"
    b.write_bytes(b"beta")
    _install_fakes(monkeypatch, {"A.esp": str(a), "B.esp": str(b)})

    loaded = CellPreviewMixin._load_order_plugins(object(), ["A.esp", "B.esp"], [str(tmp_path)])

    assert [p.name for p in loaded] == ["A.esp", "B.esp"]
    assert [p.records for p in loaded] == [["alpha"], ["beta"]]


def test_a_name_the_index_cannot_find_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plugin missing from the map is dropped, not read from its bare name.

    Args:
        monkeypatch: The pytest fixture.
        tmp_path: A temporary directory for the one resolvable plugin file.
    """
    a = tmp_path / "A.esp"
    a.write_bytes(b"alpha")
    _install_fakes(monkeypatch, {"A.esp": str(a)})  # "Missing.esp" absent

    loaded = CellPreviewMixin._load_order_plugins(
        object(), ["Missing.esp", "A.esp"], [str(tmp_path)]
    )

    assert [p.name for p in loaded] == ["A.esp"]


def test_an_unreadable_plugin_is_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One corrupt plugin must not sink the rest of the order.

    Args:
        monkeypatch: The pytest fixture.
        tmp_path: A temporary directory for the fake plugin files.
    """
    good = tmp_path / "Good.esp"
    good.write_bytes(b"ok")
    _install_fakes(monkeypatch, {"Bad.esp": str(tmp_path / "gone.esp"), "Good.esp": str(good)})

    def parse(data: bytes) -> list[Any]:
        """Read a plugin's records -- here just its decoded bytes."""
        return [data.decode()]

    monkeypatch.setattr(cellpreview, "read_plugin", parse)

    loaded = CellPreviewMixin._load_order_plugins(
        object(), ["Bad.esp", "Good.esp"], [str(tmp_path)]
    )

    assert [p.name for p in loaded] == ["Good.esp"]


def test_non_content_files_are_skipped_unparsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``.omwscripts`` (a text script list) is not fed to the record parser.

    A real load order lists ``.omwscripts`` files; they hold no records, and
    parsing their text as a record blew up the whole preview.

    Args:
        monkeypatch: The pytest fixture.
        tmp_path: A temporary directory for the fake files.
    """
    script = tmp_path / "Mod.omwscripts"
    script.write_text("Mod.lua\n")
    esp = tmp_path / "Mod.esp"
    esp.write_bytes(b"esp")
    _install_fakes(monkeypatch, {"Mod.omwscripts": str(script), "Mod.esp": str(esp)})

    read: list[bytes] = []
    monkeypatch.setattr(cellpreview, "read_plugin", lambda data: read.append(data) or [])

    loaded = CellPreviewMixin._load_order_plugins(
        object(), ["Mod.omwscripts", "Mod.esp"], [str(tmp_path)]
    )

    assert [p.name for p in loaded] == ["Mod.esp"]
    assert read == [b"esp"]  # the script list was never parsed


def test_an_esp_error_is_caught_not_fatal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A content file the record parser rejects is skipped, not fatal.

    ``EspError`` is not a ``ValueError``; the loop once let it escape and abort
    the whole preview.

    Args:
        monkeypatch: The pytest fixture.
        tmp_path: A temporary directory for the fake plugin files.
    """
    bad = tmp_path / "Bad.esp"
    bad.write_bytes(b"garbage")
    good = tmp_path / "Good.esp"
    good.write_bytes(b"ok")
    _install_fakes(monkeypatch, {"Bad.esp": str(bad), "Good.esp": str(good)})

    def parse(data: bytes) -> list[Any]:
        """Reject the corrupt plugin with an ``EspError``, pass the good one."""
        if data == b"garbage":
            raise EspError("read past end")
        return [data.decode()]

    monkeypatch.setattr(cellpreview, "read_plugin", parse)

    loaded = CellPreviewMixin._load_order_plugins(
        object(), ["Bad.esp", "Good.esp"], [str(tmp_path)]
    )

    assert [p.name for p in loaded] == ["Good.esp"]
