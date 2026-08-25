"""Tk-level check that the Steam Deck icon fix produces a usable icon.

:mod:`wraithguard.images.ico` is pure and covered hermetically by
tests/test_icon_frame.py; the one piece that module deliberately leaves
untouched is whether Tk itself accepts the bytes it hands back and actually
uses them as the window icon. This is that piece.

Same discipline as tests/test_gui_smoke.py, which this borrows its skip/xvfb
convention from rather than duplicating fixtures wholesale: it **skips**
rather than fails when Tk or a display is missing, so the hermetic suite is
unaffected, and CI runs it under ``xvfb`` alongside the rest of the GUI smoke
job. A skip here means "not checked".

Running it locally, on a machine that has Tk::

    python -m pytest tests/test_gui_icon.py -v
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

tkinter = pytest.importorskip("tkinter", reason="Tk is not installed")

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_png(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    """A real, valid, single-color RGBA PNG -- see tests/test_icon_frame.py
    for why this is built by hand rather than imported from there: tests/
    isn't a package here, and duplicating ~15 lines beats a fragile cross-file
    import for a fixture this small."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b"".join(b"\x00" + bytes(rgba) * width for _row in range(height))
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def build_ico(entries: list[tuple[int, int, bytes]]) -> bytes:
    """Assemble a raw ``.ico`` from ``(width, height, frame_bytes)`` entries."""
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    table = b""
    data = b""
    for width, height, frame in entries:
        table += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(frame), offset)
        data += frame
        offset += len(frame)
    return header + table + data


@pytest.fixture
def tk_root() -> Iterator[Any]:
    """A real, withdrawn Tk root -- enough to own a PhotoImage, no window
    needs to actually appear on screen for this."""
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture
def gui_module() -> Any:
    """The GUI script, imported fresh now that Tk is confirmed available."""
    import wraithguard_toolkit_gui as gui

    return gui


class TestIconPhotoImage:
    """``_icon_photo_image`` end to end: real file on disk, real Tk call."""

    def test_a_valid_ico_becomes_a_photoimage_of_the_right_size(
        self, tk_root: Any, gui_module: Any, tmp_path: Path
    ) -> None:
        png = make_png(24, 24, (10, 20, 30, 255))
        ico_path = tmp_path / "icon.ico"
        ico_path.write_bytes(build_ico([(24, 24, png)]))

        photo = gui_module._icon_photo_image(str(ico_path))

        assert photo is not None
        assert isinstance(photo, tkinter.PhotoImage)
        assert (photo.width(), photo.height()) == (24, 24)

    def test_the_largest_frame_is_the_one_tk_gets(
        self, tk_root: Any, gui_module: Any, tmp_path: Path
    ) -> None:
        small = make_png(16, 16, (255, 0, 0, 255))
        big = make_png(48, 48, (0, 255, 0, 255))
        ico_path = tmp_path / "icon.ico"
        ico_path.write_bytes(build_ico([(16, 16, small), (48, 48, big)]))

        photo = gui_module._icon_photo_image(str(ico_path))

        assert photo is not None
        assert (photo.width(), photo.height()) == (48, 48)

    def test_a_missing_file_returns_none_rather_than_raising(
        self, tk_root: Any, gui_module: Any, tmp_path: Path
    ) -> None:
        assert gui_module._icon_photo_image(str(tmp_path / "does-not-exist.ico")) is None

    def test_a_bmp_only_ico_returns_none_rather_than_raising(
        self, tk_root: Any, gui_module: Any, tmp_path: Path
    ) -> None:
        """An icon old enough to carry no PNG frame at all must not crash
        window setup -- it should just mean no icon, the same as before this
        fix existed."""
        bmp_like = b"\x28\x00\x00\x00" + b"\x00" * 36
        ico_path = tmp_path / "ancient.ico"
        ico_path.write_bytes(build_ico([(16, 16, bmp_like)]))

        assert gui_module._icon_photo_image(str(ico_path)) is None

    def test_garbage_bytes_return_none_rather_than_raising(
        self, tk_root: Any, gui_module: Any, tmp_path: Path
    ) -> None:
        ico_path = tmp_path / "not-an-icon.ico"
        ico_path.write_bytes(b"this is not an icon file at all")

        assert gui_module._icon_photo_image(str(ico_path)) is None


class TestAppUsesIconphotoNotJustIconbitmap:
    """The actual bug: iconbitmap alone is silently a no-op on Linux/X11."""

    def test_the_app_keeps_a_reference_to_its_icon_photo(
        self, tk_root: Any, gui_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PhotoImage with no surviving Python reference gets garbage
        collected even though the window still points at it -- the icon
        would go blank the moment __init__ returned. This is the guard
        against that, checked directly rather than trusting it by inspection.

        The iconphoto path is a FALLBACK: the app tries ``iconbitmap`` first
        and only decodes a PhotoImage when that raises (X11, where .ico loading
        is a silent no-op -- the very bug this class is named for). On Windows
        ``iconbitmap`` succeeds and no PhotoImage is ever made, so to exercise
        the reference-keeping guarantee on every platform we force ``iconbitmap``
        to fail here, standing in for X11.
        """
        import wraithguard.gui as gui_pkg

        app_dir = tmp_path / "appdir"
        app_dir.mkdir()
        gui_pkg._APP_DIR = app_dir

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        png = make_png(20, 20, (1, 2, 3, 255))
        ico_path = assets_dir / "wraithguard_toolkit_icon.ico"
        ico_path.write_bytes(build_ico([(20, 20, png)]))
        monkeypatch.setattr(gui_module, "resource_path", lambda _rel: str(ico_path))

        def _iconbitmap_is_a_noop(*_args: Any, **_kwargs: Any) -> None:
            raise tkinter.TclError("forced: stand in for X11, where .ico is a no-op")

        monkeypatch.setattr(tk_root, "iconbitmap", _iconbitmap_is_a_noop)

        app = gui_module.App(tk_root)

        assert getattr(app, "_icon_photo", None) is not None
        assert isinstance(app._icon_photo, tkinter.PhotoImage)
