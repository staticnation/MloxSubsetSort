"""Headless smoke tests for the Tk GUI.

The GUI has no other automated coverage: it cannot be imported without Tk, so
it is excluded from the hermetic suite and from mypy, and its verification has
been the manual ``SMOKE_TEST.md`` run. That gap is not theoretical -- the last
two defects to reach a user were both here, and both invisible to ruff, mypy and
1,200 passing tests:

* a window that opened **blank**, because a mistyped palette key raised after
  the ``Toplevel`` was created and before its content was packed;
* two panels **gridded on top of each other**, because a body moved during a
  refactor kept row offsets that were relative to a different base.

Neither needs a human to spot. What they need is a display, so this module
builds the real application on a real (virtual) X server and asserts the things
a person would otherwise have to look for.

It **skips** rather than fails when Tk or a display is missing, so the hermetic
suite is unaffected; CI runs it under ``xvfb`` in a job of its own. A skip here
means "not checked", so the CI job asserts it actually ran.

Running it locally, on a machine that has Tk::

    python -m pytest tests/test_gui_smoke.py -v

Windows flash open and closed while it runs; that is the suite working. If every
test reports ``SKIPPED``, Tk or the display is missing and nothing was checked --
``python -c "import tkinter; tkinter.Tk()"`` will say which.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

tkinter = pytest.importorskip("tkinter", reason="Tk is not installed")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Tk's modal dialogs so a smoke test can never block on one.

    A real ``messagebox``/``ask*`` pops a modal that waits for a click. That is
    a manual "OK" when running locally and a hard hang under headless ``xvfb``
    in CI, where nothing ever clicks it. The informational ones return their
    usual ``"ok"``; the questions default to the conservative "no". A test that
    cares about a specific dialog still overrides these with its own
    ``monkeypatch`` inside the test body, which takes precedence.
    """
    import tkinter.messagebox as mb

    monkeypatch.setattr(mb, "showinfo", lambda *a, **k: "ok", raising=False)
    monkeypatch.setattr(mb, "showwarning", lambda *a, **k: "ok", raising=False)
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: "ok", raising=False)
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(mb, "askyesnocancel", lambda *a, **k: False, raising=False)


@pytest.fixture(scope="module")
def tk_root() -> Iterator[Any]:
    """A real Tk root window, built the way the application builds its own.

    The root type is not incidental. ``main()`` uses ``TkinterDnD.Tk()`` when
    tkinterdnd2 is present, and that is what loads the tkdnd Tcl package into
    the interpreter; a plain ``tkinter.Tk()`` looks identical but cannot carry
    a drop target. Building the wrong one here tested a window the application
    never constructs.

    Yields:
        The root window, withdrawn so nothing flashes on screen.
    """
    from wraithguard.gui import HAVE_DND, TkinterDnD

    try:
        root = TkinterDnD.Tk() if HAVE_DND else tkinter.Tk()
    except tkinter.TclError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture(scope="module")
def app(tk_root: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The real application, built on a scratch app directory.

    Args:
        tk_root: The Tk root.
        tmp_path_factory: Pytest's temp directory factory.

    Returns:
        The constructed ``App``.
    """
    import wraithguard.gui as gui_pkg

    # app_base_dir() caches into this module global; pointing it at a temp
    # directory keeps the settings file and trace log out of the checkout.
    gui_pkg._APP_DIR = tmp_path_factory.mktemp("appdir")
    import wraithguard_toolkit_gui as gui

    return gui.App(tk_root)


@pytest.fixture
def fresh_app(tk_root: Any, tmp_path: Path) -> Iterator[Any]:
    """A second application on its own settings file, for the mutating tests.

    Anything that writes settings or switches theme would otherwise leak into
    every test that runs after it, since ``app`` is module-scoped. This builds a
    separate ``App`` pointed at a per-test directory, and puts the theme back
    afterwards -- the active theme is process-wide, not per-application.

    It shares the one Tk root deliberately rather than creating a second.
    ``tkinter`` keeps a single ``_default_root``, and a second ``Tk()`` does not
    become it, so an application built on the second root would create its
    variables on the *first* interpreter and its widgets on the second -- which
    fails in a way that looks like a bug in the code under test.

    Args:
        tk_root: The Tk root.
        tmp_path: A per-test temp directory.

    Yields:
        A freshly constructed ``App``.
    """
    import wraithguard.gui as gui_pkg
    from wraithguard.gui import theme as theme_mod

    previous_dir = gui_pkg._APP_DIR
    previous_theme = theme_mod._ACTIVE_THEME
    gui_pkg._APP_DIR = tmp_path

    import wraithguard_toolkit_gui as gui

    built = gui.App(tk_root)
    try:
        yield built
    finally:
        gui_pkg._APP_DIR = previous_dir
        theme_mod._ACTIVE_THEME = previous_theme


def grid_cells(widget: Any) -> dict[tuple[int, int], list[str]]:
    """Map every grid cell a container uses to the widgets occupying it.

    Args:
        widget: The container to inspect.

    Returns:
        ``(row, column)`` to the widget class names placed there, expanded
        across each widget's row and column spans.
    """
    used: dict[tuple[int, int], list[str]] = {}
    for child in widget.grid_slaves():
        info = child.grid_info()
        row, column = int(info["row"]), int(info["column"])
        rowspan, columnspan = int(info.get("rowspan", 1)), int(info.get("columnspan", 1))
        for r in range(row, row + rowspan):
            for c in range(column, column + columnspan):
                used.setdefault((r, c), []).append(child.winfo_class())
    return used


class TestApplicationBuilds:
    """The window comes up at all."""

    def test_app_constructs(self, app: Any) -> None:
        """Construction runs every builder; an exception in any is a blank app.

        Args:
            app: The application.
        """
        assert app.root.winfo_exists()

    def test_the_controls_panel_has_widgets(self, app: Any) -> None:
        """A panel that built nothing looks exactly like one that built wrongly.

        Args:
            app: The application.
        """
        assert app.sort_button.winfo_exists()
        assert app.log_text.winfo_exists()


class TestDragAndDropIsOptional:
    """Drag and drop is a convenience, so its absence must not stop the app.

    Found by running this suite for the first time on a real desktop. The
    fixture built a plain ``tkinter.Tk()`` root, which does not load the tkdnd
    Tcl package, and **every test errored** -- not on an assertion but during
    construction, because registering a drop target raised and took the whole
    window build down with it.

    That was a test bug (the application builds a ``TkinterDnD.Tk()`` root) but
    it exposed a real one: the code asked whether the *Python package* imported
    and then assumed the *Tcl package* was loaded. Those come apart on a
    half-installed tkdnd or a frozen build that shipped one side without the
    other, and the result is an application that will not open at all over a
    feature nobody needs to have.
    """

    def test_the_app_builds_when_drag_and_drop_is_unavailable(
        self, tk_root: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression: the window must finish building without tkdnd.

        Simulated by turning the availability flag off rather than by building
        a second root without the Tcl package. A second root is a second Tk
        interpreter, which brings its own failure modes and can make this skip
        -- and a skip here means the one check that covers the reported bug
        quietly did not run.

        Args:
            tk_root: The Tk root.
            tmp_path: A scratch directory for settings.
            monkeypatch: Pytest's patcher.
        """
        import wraithguard.gui as gui_pkg

        monkeypatch.setattr(gui_pkg, "HAVE_DND", False)
        monkeypatch.setattr(gui_pkg, "_APP_DIR", tmp_path)
        import wraithguard_toolkit_gui as gui

        built = gui.App(tk_root)

        assert built.sort_button.winfo_exists(), "the window did not finish building"
        assert built.log_text.winfo_exists()

    def test_the_missing_feature_is_reported_on_screen(
        self, tk_root: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silently offering a dead drop target would be worse than saying so.

        Args:
            tk_root: The Tk root.
            tmp_path: A scratch directory for settings.
            monkeypatch: Pytest's patcher.
        """
        import wraithguard.gui as gui_pkg

        monkeypatch.setattr(gui_pkg, "HAVE_DND", False)
        monkeypatch.setattr(gui_pkg, "_APP_DIR", tmp_path)
        import wraithguard_toolkit_gui as gui

        built = gui.App(tk_root)
        labels = [
            str(child.cget("text"))
            for child in _all_widgets(built.root)
            if child.winfo_class() == "TLabel"
        ]

        # Compared against the translated form of the same literal, not against
        # English: a matched string would otherwise start failing the moment a
        # catalogue is shipped, which is not a regression in anything.
        from wraithguard.i18n import gettext

        expected = gettext(
            "Drag & drop is unavailable (tkinterdnd2 or its tkdnd library "
            "is missing) -- use the Browse buttons below."
        )
        assert expected in labels

    def test_a_registration_that_raises_is_survivable(self) -> None:
        """The guard itself, without needing an interpreter that lacks tkdnd.

        A widget whose registration refuses must yield ``False`` rather than an
        exception, because the caller is halfway through building a window.
        """
        from wraithguard.gui import register_drop_target

        class Refuses:
            """A widget that reports tkdnd present but refuses registration."""

            class _Tk:
                @staticmethod
                def eval(_script: str) -> str:
                    """Report the package as present.

                    Args:
                        _script: Ignored.

                    Returns:
                        A version string.
                    """
                    return "2.9"

            tk = _Tk()

            @staticmethod
            def drop_target_register(*_args: object) -> None:
                """Refuse, the way Tk does when tkdnd is not really usable.

                Args:
                    _args: Ignored.

                Raises:
                    TclError: Always.
                """
                raise tkinter.TclError("invalid command name 'tkdnd::drop_target'")

        assert register_drop_target(Refuses()) is False

    def test_the_real_root_does_support_it(self, tk_root: Any) -> None:
        """Otherwise the checks above would pass by testing nothing.

        Args:
            tk_root: The root the application actually builds on.
        """
        from wraithguard.gui import HAVE_DND, dnd_ready

        if not HAVE_DND:  # pragma: no cover - tkinterdnd2 is optional
            pytest.skip("tkinterdnd2 is not installed")
        assert dnd_ready(tk_root), "the app's own root should support drag and drop"


class TestDragListboxSelectionEscape:
    """Clicking a row must always be able to reduce a multi-selection.

    Reported bug: Ctrl+A (Tk's ``<<SelectAll>>``) selects every row, and then
    nothing but a programmatic reset would clear it. The block-drag handler
    returned ``"break"`` for any press inside a contiguous selection so it could
    drag the whole block -- and once all rows are selected they *are* one
    contiguous block, so every click was swallowed as a drag-grab. A press that
    never moves is a plain click and must collapse to the clicked row.
    """

    @staticmethod
    def _listbox(tk_root: Any) -> Any:
        """A populated listbox whose row-at-y lookup is stubbed.

        ``bbox``/``nearest`` need the widget mapped and drawn, which it is not in
        a headless run, so ``nearest`` is stubbed to return whatever row the test
        aims at. That isolates the selection logic under test from geometry.

        Args:
            tk_root: A live Tk root.

        Returns:
            The listbox, with ``aim_at(row)`` set to point the next press/motion.
        """
        from wraithguard.gui.widgets import DragReorderListbox

        lb = DragReorderListbox(tk_root, selectmode="extended")
        for i in range(6):
            lb.insert("end", f"item{i}")
        lb.aim_at = lambda row: setattr(lb, "nearest", lambda _y: row)  # type: ignore[attr-defined]
        return lb

    def test_clicking_a_row_after_select_all_collapses_to_it(self, tk_root: Any) -> None:
        """The exact reported path: select all, click one row, get one row.

        Args:
            tk_root: A live Tk root.
        """
        lb = self._listbox(tk_root)
        event = type("E", (), {"y": 0})()
        try:
            lb.selection_set(0, "end")  # what Ctrl+A / <<SelectAll>> does
            assert len(lb.curselection()) == 6

            lb.aim_at(3)  # the click lands on row 3
            # A press inside the full-list block grabs it (returns "break")...
            assert lb._on_press(event) == "break"
            # ...but with no motion, release collapses to the clicked row.
            lb._on_release(event)
            assert list(lb.curselection()) == [3]
        finally:
            lb.destroy()

    def test_a_press_that_moves_still_drags_the_block(self, tk_root: Any) -> None:
        """The collapse must not defeat the feature it guards.

        A press that actually moves is a reorder, not a click, so the selection
        must follow the dragged block rather than collapse to one row.

        Args:
            tk_root: A live Tk root.
        """
        lb = self._listbox(tk_root)
        event = type("E", (), {"y": 0})()
        try:
            lb.selection_set(0, 1)  # a two-row contiguous block
            lb.aim_at(0)  # press on the top of the block
            assert lb._on_press(event) == "break"
            lb.aim_at(2)  # drag down past the block's end
            lb._on_motion(event)
            lb._on_release(event)
            # The block moved and stays multi-selected; it did not collapse.
            assert len(lb.curselection()) == 2
            assert lb.get(1) == "item0" and lb.get(2) == "item1"
        finally:
            lb.destroy()


class TestManualSubsetAdditions:
    """Folders and plugins the scan missed can be added by hand.

    The scan recognises a mod by a standard asset subfolder or a plugin file; an
    OpenMW Lua mod with a non-standard layout, or a stray plugin, has neither. A
    button and drag-drop let the user include one anyway; it merges into the
    subset on the next Sort. These pin the routing (plugin vs folder) and the
    de-duplication, without needing the widgets that only carry it in.
    """

    @staticmethod
    def _host() -> Any:
        """An App with just the attributes the manual-add methods touch."""
        import queue as queue_mod

        from wraithguard_toolkit_gui import App

        host = App.__new__(App)
        host._manual_plugins = []  # type: ignore[attr-defined]
        host._manual_data_dirs = []  # type: ignore[attr-defined]
        host.log_queue = queue_mod.Queue()  # type: ignore[attr-defined]
        host.status_var = type("V", (), {"set": lambda _self, _s: None})()  # type: ignore[attr-defined]
        host.sort_data_paths_var = type("B", (), {"get": lambda _self: False})()  # type: ignore[attr-defined]
        return host

    def test_a_plugin_path_is_classified_as_a_plugin(self) -> None:
        """A dropped .esp joins the subset by basename, not as a folder.

        C:/mods/loose does not exist on this (POSIX) test machine, so this
        also happens to pin that a plugin whose folder cannot be verified
        does not add a bogus data path -- see
        test_a_real_plugin_folder_is_captured_too for the folder actually
        being captured when it does exist.
        """
        host = self._host()
        host._add_manual_path("C:/mods/loose/MyMod.esp")
        assert host._manual_plugins == ["MyMod.esp"]
        assert host._manual_data_dirs == []

    def test_a_real_plugin_folder_is_captured_too(self, tmp_path: Path) -> None:
        """The whole point: a plugin is useless to OpenMW without a data=
        entry for wherever it actually lives, and basename_if_plugin()
        throws that location away -- so it must be captured before that,
        not left for the user to separately remember to add.

        Args:
            tmp_path: A real directory to add a plugin from.
        """
        import os.path

        host = self._host()
        folder = tmp_path / "LooseMod"
        folder.mkdir()
        plugin_path = folder / "MyMod.esp"
        plugin_path.write_bytes(b"")
        host._add_manual_path(str(plugin_path))
        assert host._manual_plugins == ["MyMod.esp"]
        assert host._manual_data_dirs == [os.path.abspath(str(folder))]  # noqa: PTH100

    def test_two_plugins_from_the_same_folder_add_it_once(self, tmp_path: Path) -> None:
        """The folder is shared; it must not be duplicated per plugin.

        Args:
            tmp_path: A real directory to add two plugins from.
        """
        import os.path

        host = self._host()
        folder = tmp_path / "LooseMod"
        folder.mkdir()
        host._add_manual_path(str(folder / "First.esp"))
        host._add_manual_path(str(folder / "Second.esp"))
        assert host._manual_plugins == ["First.esp", "Second.esp"]
        assert host._manual_data_dirs == [os.path.abspath(str(folder))]  # noqa: PTH100

    def test_a_bare_plugin_name_adds_no_folder(self) -> None:
        """A folder-less name (e.g. from a subset file's plain plugin line)
        must not be mistaken for 'the current directory is its data path'
        -- Path('MyMod.esp').parent is '.', which always exists.
        """
        host = self._host()
        host._add_manual_path("MyMod.esp")
        assert host._manual_plugins == ["MyMod.esp"]
        assert host._manual_data_dirs == []

    def test_a_directory_is_classified_as_a_data_folder(self, tmp_path: Path) -> None:
        """A dropped folder joins the data paths, absolutised like the scan.

        Args:
            tmp_path: A real directory to add.
        """
        import os.path

        host = self._host()
        folder = tmp_path / "NgardeParrySounds"
        folder.mkdir()
        host._add_manual_path(str(folder))
        # abspath, not resolve: match production, which must not follow symlinks.
        assert host._manual_data_dirs == [os.path.abspath(str(folder))]  # noqa: PTH100
        assert host._manual_plugins == []

    def test_duplicates_are_ignored_case_insensitively(self, tmp_path: Path) -> None:
        """Adding the same plugin or folder twice is a no-op the second time.

        Args:
            tmp_path: A real directory to add twice.
        """
        host = self._host()
        host._add_manual_path("MyMod.esp")
        host._add_manual_path("mymod.ESP")
        assert host._manual_plugins == ["MyMod.esp"]

        import os.path

        folder = tmp_path / "Mod"
        folder.mkdir()
        host._add_manual_path(str(folder))
        host._add_manual_path(str(folder))
        assert host._manual_data_dirs == [os.path.abspath(str(folder))]  # noqa: PTH100

    def test_a_plugin_from_an_already_added_folder_does_not_duplicate_it(
        self, tmp_path: Path
    ) -> None:
        """The common real sequence: add the mod folder, then a loose plugin
        from inside it. The folder must not appear twice.

        Args:
            tmp_path: A real directory added once as a folder, then a plugin
                dropped from inside it.
        """
        import os.path

        host = self._host()
        folder = tmp_path / "Mod"
        folder.mkdir()
        host._add_manual_path(str(folder))
        host._add_manual_path(str(folder / "MyMod.esp"))
        assert host._manual_data_dirs == [os.path.abspath(str(folder))]  # noqa: PTH100
        assert host._manual_plugins == ["MyMod.esp"]

    def test_a_path_that_is_neither_is_ignored(self) -> None:
        """A non-plugin path that is not a real folder adds nothing."""
        host = self._host()
        host._add_manual_path("C:/nope/not-a-real-thing")
        assert host._manual_plugins == []
        assert host._manual_data_dirs == []


class TestClearScanMemory:
    """The 'Clear Memory' button in the pinned utility row next to Help.

    _manual_plugins/_manual_data_dirs (TestManualSubsetAdditions above) and an
    in-memory scan result are the one kind of state this program holds that is
    genuinely invisible: nothing on screen shows what has accumulated, and
    unlike the subset-file field there is no Browse button to just point
    elsewhere. These pin that it asks before discarding anything, correctly
    no-ops when there is nothing to discard, names what it is about to lose,
    and -- as important as what it clears -- leaves the subset-file field and
    everything else alone.
    """

    @staticmethod
    def _host(*, subset_file: str = "") -> Any:
        """An App with just the attributes _clear_scan_memory touches."""
        import queue as queue_mod

        from wraithguard_toolkit_gui import App

        host = App.__new__(App)
        host._manual_plugins = []  # type: ignore[attr-defined]
        host._manual_data_dirs = []  # type: ignore[attr-defined]
        host._scanned_subset_lines = None  # type: ignore[attr-defined]
        host.log_queue = queue_mod.Queue()  # type: ignore[attr-defined]
        statuses: list[str] = []
        host.status_var = type(  # type: ignore[attr-defined]
            "V", (), {"set": lambda _self, s: statuses.append(s)}
        )()
        host._test_statuses = statuses  # type: ignore[attr-defined]
        # Proof this method must never touch it: a distinct sentinel value,
        # asserted unchanged after every clear below.
        host.subset_file_var = type(  # type: ignore[attr-defined]
            "V", (), {"get": lambda _self: subset_file}
        )()
        return host

    def _confirm(self, monkeypatch: Any, *, answer: bool) -> list[tuple[str, str]]:
        """Stub messagebox.askyesno to return ``answer``, capturing each call."""
        import wraithguard_toolkit_gui as gui

        calls: list[tuple[str, str]] = []

        def fake_askyesno(title: str, message: str) -> bool:
            calls.append((title, message))
            return answer

        monkeypatch.setattr(gui.messagebox, "askyesno", fake_askyesno)
        return calls

    def test_nothing_to_clear_does_not_prompt(self, monkeypatch: Any) -> None:
        """An empty-state click should not interrupt with a pointless confirm."""
        host = self._host()
        calls = self._confirm(monkeypatch, answer=True)
        host._clear_scan_memory()
        assert calls == []
        assert host._test_statuses[-1] == "Nothing to clear."

    def test_declining_leaves_everything_untouched(self, monkeypatch: Any) -> None:
        """Saying no must be exactly that -- not a slower yes."""
        host = self._host(subset_file="C:/mods/subset.txt")
        host._manual_plugins.append("MyMod.esp")
        host._manual_data_dirs.append("C:/mods/Loose")
        host._scanned_subset_lines = ["some", "lines"]
        self._confirm(monkeypatch, answer=False)
        host._clear_scan_memory()
        assert host._manual_plugins == ["MyMod.esp"]
        assert host._manual_data_dirs == ["C:/mods/Loose"]
        assert host._scanned_subset_lines == ["some", "lines"]
        assert host.subset_file_var.get() == "C:/mods/subset.txt"

    def test_confirming_clears_manual_adds_and_the_scan_result(self, monkeypatch: Any) -> None:
        """The actual point: all three kinds of accumulated memory go together."""
        host = self._host(subset_file="C:/mods/subset.txt")
        host._manual_plugins.append("MyMod.esp")
        host._manual_data_dirs.append("C:/mods/Loose")
        host._scanned_subset_lines = ["some", "lines"]
        self._confirm(monkeypatch, answer=True)
        host._clear_scan_memory()
        assert host._manual_plugins == []
        assert host._manual_data_dirs == []
        assert host._scanned_subset_lines is None
        # The one thing this must never touch, confirmed even on the path
        # that actually clears something.
        assert host.subset_file_var.get() == "C:/mods/subset.txt"
        assert "Cleared" in host._test_statuses[-1]
        logged = host.log_queue.get_nowait()
        assert "Cleared memory" in logged

    def test_the_prompt_names_what_will_be_lost(self, monkeypatch: Any) -> None:
        """A blind 'are you sure?' would not let anyone catch a mistake."""
        host = self._host()
        host._manual_plugins.extend(["A.esp", "B.esp"])
        host._manual_data_dirs.append("C:/mods/Loose")
        host._scanned_subset_lines = ["line"]
        calls = self._confirm(monkeypatch, answer=True)
        host._clear_scan_memory()
        assert len(calls) == 1
        _title, message = calls[0]
        assert "2 manual plugin(s)" in message
        assert "1 manual data folder(s)" in message
        assert "in-memory scan result" in message

    def test_only_the_scan_result_is_still_reported_on_its_own(self, monkeypatch: Any) -> None:
        """Manual adds and a scan result are independent; either alone must
        still produce a sensible (non-empty, non-crashing) summary.
        """
        host = self._host()
        host._scanned_subset_lines = ["line"]
        calls = self._confirm(monkeypatch, answer=True)
        host._clear_scan_memory()
        assert len(calls) == 1
        assert "manual plugin" not in calls[0][1]
        assert "manual data folder" not in calls[0][1]
        assert host._scanned_subset_lines is None

    def test_the_button_exists_next_to_help_and_is_pinned(self, app: Any) -> None:
        """Clear Memory stays beside Help, outside the scrollable controls area.

        Args:
            app: The application.
        """
        assert app.clear_memory_button.cget("text") == "Clear Memory"
        assert app.help_btn.cget("text") == "Help"
        assert str(app.clear_memory_button.cget("command")).strip()
        assert str(app.help_btn.cget("command")).strip()
        assert str(app.clear_memory_button.cget("state")) != "disabled"
        assert app.clear_memory_button.master is app.help_btn.master
        assert not _is_descendant(app.clear_memory_button, _controls_content(app))


class TestActionButtons:
    """Every button exists, is bound, and starts in the documented state."""

    #: Button attribute -> whether it should start disabled (needs a Sort first).
    BUTTONS = {
        "sort_button": False,
        "export_button": True,
        "conflicts_button": True,
        "cellmap_button": True,
        "resource_button": True,
        # Second row: plugin manipulation / file creation. Merge Lands needs a
        # sorted order (disabled until Sort); Merge Settings writes a
        # .mergedlands.toml for a picked plugin, so it needs no order.
        "mergedlands_button": True,
        "merge_settings_button": False,
        "lint_button": True,
        "tes3cmd_button": False,
        "savecheck_button": False,
        "backups_button": False,
        # Both live in the log toolbar rather than the controls action bar,
        # but they're plain, always-enabled buttons like the rest here.
        "clear_memory_button": False,
        "help_btn": False,
    }

    def test_every_button_exists(self, app: Any) -> None:
        """A renamed or dropped button is otherwise found by clicking around.

        Args:
            app: The application.
        """
        missing = [name for name in self.BUTTONS if not hasattr(app, name)]
        assert not missing, f"buttons missing from the app: {missing}"

    def test_every_button_has_a_command(self, app: Any) -> None:
        """An unbound button is silent on screen and silent in the log.

        Args:
            app: The application.
        """
        unbound = [
            name for name in self.BUTTONS if not str(getattr(app, name).cget("command")).strip()
        ]
        assert not unbound, f"buttons with no command: {unbound}"

    def test_read_only_scans_start_disabled(self, app: Any) -> None:
        """They need a sorted plugin list; offering them earlier is a dead end.

        Args:
            app: The application.
        """
        wrong = {
            name: str(getattr(app, name).cget("state"))
            for name, should_be_disabled in self.BUTTONS.items()
            if (str(getattr(app, name).cget("state")) == "disabled") is not should_be_disabled
        }
        assert not wrong, f"buttons in the wrong initial state: {wrong}"


class TestControlsLayout:
    """No two panels may be gridded into the same cell.

    This is the check that would have caught the output-fields panel landing on
    top of the rules panel, the options box and the action bar.
    """

    def test_no_two_widgets_share_a_grid_cell(self, app: Any) -> None:
        """Overlapping widgets hide each other, silently.

        Args:
            app: The application.
        """
        container = app.sort_button.winfo_toplevel()
        collisions: dict[str, dict[tuple[int, int], list[str]]] = {}
        for frame in _grid_containers(container):
            clashes = {cell: names for cell, names in grid_cells(frame).items() if len(names) > 1}
            if clashes:
                collisions[str(frame)] = clashes
        assert not collisions, f"widgets stacked in one grid cell: {collisions}"

    def test_the_controls_panel_uses_consecutive_rows(self, app: Any) -> None:
        """A gap means a panel went somewhere unintended.

        Anchored on the scrollable frame ``_build_controls`` actually grids
        into (see ``_controls_content``), not the canvas that wraps it.
        Clear Memory and Help live in the log panel's own toolbar, outside
        this stack entirely; everything gridded here is the scrollable
        controls content.

        Args:
            app: The application.
        """
        parent = _controls_content(app)
        rows = sorted({row for row, _column in grid_cells(parent)})
        if not rows:  # pragma: no cover - layout differs from the expected nesting
            pytest.skip("controls frame not found")
        assert rows == list(range(min(rows), max(rows) + 1)), f"gap in the control rows: {rows}"

    def test_run_and_tool_actions_live_inside_scrollable_controls(self, app: Any) -> None:
        """The large action set should scroll with the controls, not pin the bottom."""
        expected = {
            "sort_button",
            "export_button",
            "conflicts_button",
            "cellmap_button",
            "resource_button",
            "mergedlands_button",
            "merge_settings_button",
            "lint_button",
            "tes3cmd_button",
            "savecheck_button",
            "backups_button",
        }
        for name in expected:
            button = getattr(app, name)
            assert _is_descendant(button, _controls_content(app)), name

    def test_only_clear_memory_and_help_are_pinned(self, app: Any) -> None:
        """Clear Memory and Help sit in the log toolbar, not the scrollable controls.

        They're not alone in that row -- Clear Log, Save Log As, the theme
        picker and Import Theme share it too -- so this checks the invariant
        that actually matters: wherever the two of them live, it never ends
        up nested inside the part of the window that scrolls.
        """
        pinned = app.clear_memory_button.master
        assert app.help_btn.master is pinned
        assert not _is_descendant(pinned, _controls_content(app))


class TestControlsFrameScrolls:
    """controls_frame must never clip content again, however tight the pane.

    Written after the Options panel -- the last thing gridded into
    controls_frame -- turned out to be exactly what got clipped next on a
    Steam Deck's 1280x800, following an earlier round of one-off fixes for
    individual clipped sections. A structural check that a canvas exists
    would have passed even if the canvas never actually became scrollable;
    these size the pane down for real and confirm both that the content
    truly would have overflowed it and that scrolling actually reaches the
    rest, the same standard TestThreeDButtonsAreReachable holds itself to.
    """

    def test_controls_frame_wraps_a_scrollable_canvas(self, app: Any) -> None:
        """The structural half: a canvas+scrollbar viewport, not a bare grid.

        Args:
            app: The application.
        """
        children = _controls_scroll_container(app).winfo_children()
        classes = [child.winfo_class() for child in children]
        assert "Canvas" in classes, classes
        assert any(cls in ("TScrollbar", "Scrollbar") for cls in classes), classes

    @staticmethod
    def _sized_at_deck_resolution(fresh_app: Any) -> Any:
        """Give ``fresh_app`` real, on-screen pixel geometry at 1280x800.

        tk_root is built withdrawn deliberately ("so nothing flashes on
        screen" -- see its docstring) and nothing in this file has needed
        real, window-manager-allocated widths/heights before this: a
        withdrawn root's own geometry() request does not reliably propagate
        down to winfo_height() on its children even after update(), which is
        exactly the pixel measurement these tests need. Callers must
        re-withdraw when done, matching what every other test here expects
        of tk_root between tests.

        Args:
            fresh_app: A freshly built, isolated application.

        Returns:
            The controls-panel scrolling canvas.
        """
        fresh_app.root.geometry("1280x800")
        fresh_app.root.deiconify()
        fresh_app.root.update()
        return _controls_canvas(fresh_app)

    def test_content_taller_than_the_pane_stays_reachable_by_scrolling(
        self, fresh_app: Any
    ) -> None:
        """The actual point: nothing gets clipped, it gets scrolled to instead.

        Args:
            fresh_app: A freshly built, isolated application.
        """
        canvas = self._sized_at_deck_resolution(fresh_app)
        try:
            region = [int(n) for n in str(canvas.cget("scrollregion")).split()]
            assert len(region) == 4, f"scrollregion never got set: {canvas.cget('scrollregion')!r}"
            content_height = region[3] - region[1]
            # The regression this guards: on a short-enough screen the pane is
            # squeezed below the content's natural height, which is exactly
            # the situation a plain (non-scrolling) frame would have clipped.
            assert content_height > canvas.winfo_height(), (
                f"content ({content_height}px) fit the pane ({canvas.winfo_height()}px) "
                "outright -- this test needs a screen short enough to force scrolling"
            )
            # The production wheel handler uses ``units``.  Tk silently does
            # nothing for unit scrolling when a canvas has yscrollincrement=0,
            # so verify the helper actually configured a usable unit size.
            assert int(canvas.cget("yscrollincrement")) > 0
            before = canvas.yview()
            canvas.yview_scroll(5, "units")
            fresh_app.root.update()
            after = canvas.yview()
            assert after != before, "scrolling did not move the view at all"
            assert after[1] > before[1], "scrolling moved the wrong way"
        finally:
            fresh_app.root.withdraw()

    def test_a_field_still_fills_the_available_width(self, fresh_app: Any) -> None:
        """The canvas wrapper must not turn column-weighted fields static.

        _build_controls relies on ``top.columnconfigure(1, weight=1)`` for
        entry fields (openmw.cfg, customizations.toml, ...) to stretch with
        the window; the inner frame living inside a canvas item rather than
        being gridded straight into its parent is exactly the kind of change
        that quietly breaks that.

        Args:
            fresh_app: A freshly built, isolated application.
        """
        canvas = self._sized_at_deck_resolution(fresh_app)
        try:
            entry = fresh_app.subset_file_field.entry
            # A few pixels of grid padding aside, the entry should span
            # essentially the same width as the canvas it scrolls inside of
            # -- not the width it would have wanted from its own text alone.
            assert entry.winfo_width() > canvas.winfo_width() * 0.5, (
                entry.winfo_width(),
                canvas.winfo_width(),
            )
        finally:
            fresh_app.root.withdraw()


def _controls_canvas(app: Any) -> Any:
    """The Canvas that scrolls the controls panel.

    ``_build_widgets`` keeps only ``self.controls_window`` -- a canvas *item
    id* (an int), not a widget -- so neither the canvas nor the frames
    around it are reachable as ``App`` attributes. Found structurally
    instead: walk up from a known controls-panel button until a ``Canvas``
    ancestor turns up.

    Args:
        app: The application (or anything sharing its widget tree).

    Returns:
        The controls-panel Canvas.
    """
    widget = app.sort_button
    while widget is not None:
        if widget.winfo_class() == "Canvas":
            return widget
        widget = widget.master
    raise AssertionError("no Canvas found walking up from sort_button")


def _controls_scroll_container(app: Any) -> Any:
    """The frame holding the controls Canvas side by side with its Scrollbar."""
    return _controls_canvas(app).master


def _controls_content(app: Any) -> Any:
    """The scrollable frame ``_build_controls`` actually grids widgets into.

    This is the canvas's one window-item child -- what the docstrings in
    this file call ``controls_inner``, though nothing in the application
    itself uses that name.
    """
    children = _controls_canvas(app).winfo_children()
    assert len(children) == 1, f"expected exactly one canvas window child, got {children}"
    return children[0]


def _is_descendant(widget: Any, ancestor: Any) -> bool:
    """Return whether widget is nested below ancestor in the Tk widget tree."""
    current = widget
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "master", None)
    return False


def _grid_containers(widget: Any) -> Iterator[Any]:
    """Yield every widget in a tree that has grid-managed children.

    Args:
        widget: The root of the tree.

    Yields:
        Containers using the grid geometry manager.
    """
    children = widget.winfo_children()
    if widget.grid_slaves():
        yield widget
    for child in children:
        yield from _grid_containers(child)


class TestOptionFields:
    """The text options that feed the engine.

    Each is only useful if it is both built and *wired*: an entry with no
    variable behind it looks identical on screen to one that works.
    """

    FIELDS = ("exclude_var", "groundcover_var", "cleanup_html_var")

    def test_every_option_variable_exists(self, app: Any) -> None:
        """A renamed variable silently stops being saved or read.

        Args:
            app: The application.
        """
        missing = [name for name in self.FIELDS if not hasattr(app, name)]
        assert not missing, f"option variables missing: {missing}"

    def test_the_groundcover_field_is_empty_by_default(self, app: Any) -> None:
        """The common case needs no help: the cfg declares its own grass.

        Args:
            app: The application.
        """
        assert app.groundcover_var.get() == ""

    def test_a_widget_is_bound_to_each_text_option(self, app: Any) -> None:
        """The variable must actually reach an entry on screen.

        Args:
            app: The application.
        """
        bound = {
            str(child.cget("textvariable"))
            for child in _all_widgets(app.root)
            if child.winfo_class() == "TEntry"
        }
        for name in ("exclude_var", "groundcover_var"):
            assert str(getattr(app, name)) in bound, f"{name} is not on screen"


def _all_widgets(widget: Any) -> Iterator[Any]:
    """Walk every widget in a tree.

    Args:
        widget: The root of the tree.

    Yields:
        Each descendant, and the root itself.
    """
    yield widget
    for child in widget.winfo_children():
        yield from _all_widgets(child)


class TestSecondaryWindows:
    """Every window the app opens must open with content in it.

    A ``Toplevel`` that raises part-way through construction leaves an empty
    window on screen and a traceback on stderr -- which is what "it does
    nothing" looks like from the outside.
    """

    def test_format_reference_window_has_content(self, app: Any) -> None:
        """The exact defect: this opened blank on a bad palette key.

        Args:
            app: The application.
        """
        before = set(app.root.winfo_children())
        app._show_format_reference("Landscape")
        opened = [w for w in app.root.winfo_children() if w not in before]
        assert opened, "no window was created"
        window = opened[-1]
        try:
            assert window.winfo_children(), "window opened with nothing in it"
            text = _first_text_widget(window)
            assert text is not None, "no text widget in the reference window"
            assert "VHGT" in text.get("1.0", "end"), "the layout was not rendered into the window"
        finally:
            window.destroy()

    def test_the_plugin_summary_window_has_rows(self, app: Any) -> None:
        """It is built from a survey, so it can be checked without a scan.

        The counts and the judgement are tested in ``test_patch_summary.py``.
        What only a display can catch is the window opening empty, which is
        what a mistyped column or palette key looks like from the outside.

        Args:
            app: The application.
        """
        from wraithguard.patch.status import ConflictThis
        from wraithguard.patch.summary import PluginTally, Survey

        found = Survey(
            plugins={
                "Loser.esp": PluginTally("Loser.esp", {ConflictThis.CONFLICT_LOSES: 3}),
                "Winner.esp": PluginTally("Winner.esp", {ConflictThis.CONFLICT_WINS: 3}),
            }
        )
        before = set(app.root.winfo_children())
        app._show_plugin_summary(found)
        opened = [w for w in app.root.winfo_children() if w not in before]
        assert opened, "no window was created"
        window = opened[-1]
        try:
            assert window.winfo_children(), "window opened with nothing in it"
            tree = _first_widget_of_class(window, "Treeview")
            assert tree is not None, "no table in the summary window"
            rows = tree.get_children()
            assert len(rows) == 2, "a plugin was dropped from the summary"
            # Worst first: the mod losing work is the one to act on.
            assert "Loser.esp" in tree.item(rows[0], "text")
        finally:
            window.destroy()

    def _scan(self) -> list[dict[str, Any]]:
        """Two records across two plugins, as the scanner reports them."""
        return [
            {
                "type": "Armor",
                "id": "cuirass",
                "plugins": ["Base.esm", "Mod.esp"],
                "winner": "Mod.esp",
                "involves_subset": False,
            },
            {
                "type": "Cell",
                "id": "(1, 2)",
                "plugins": ["Mod.esp"],
                "winner": "Mod.esp",
                "involves_subset": False,
            },
        ]

    def test_the_plugin_view_builds_a_tree_on_demand(self, app: Any) -> None:
        """Plugin, then record type, then record -- built as it is opened.

        The grouping is tested in ``test_patch_summary.py``. What needs a
        display is that the tree nests the right way round *and* that expanding
        a node actually replaces its placeholder -- lazy building is exactly
        the kind of thing that silently leaves "(opening...)" on screen.

        Args:
            app: The application.
        """
        app._shown_conflicts = self._scan()
        app.show_plugin_view()
        window = app._plugin_win
        try:
            nav = app._plugin_nav
            plugins = nav.get_children()
            assert len(plugins) == 2, "one branch per plugin"

            # Nothing below a plugin exists until it is opened.
            placeholder = nav.get_children("Mod.esp")
            assert len(placeholder) == 1
            assert "pending" in nav.item(placeholder[0], "tags")

            nav.focus("Mod.esp")
            app._on_plugin_open()
            groups = nav.get_children("Mod.esp")
            assert {nav.item(g, "text") for g in groups} == {"Armor", "Cell"}

            group = next(g for g in groups if nav.item(g, "text") == "Armor")
            nav.focus(group)
            app._on_plugin_open()
            records = nav.get_children(group)
            assert records, "a record type group with no records under it"
            assert nav.item(records[0], "text") == "cuirass"
        finally:
            window.destroy()

    def test_the_index_colours_plugin_rows_without_opening_a_group(self, app: Any) -> None:
        """Once a summary has judged the order, its verdict index colours every
        plugin row on open -- no group has to be expanded, and no record is read
        again. This is the whole point of the index scan.

        Args:
            app: The application.
        """
        from wraithguard.gui.pluginview import this_tag
        from wraithguard.patch.status import ConflictThis, worst_this
        from wraithguard.patch.summary import Survey

        app._shown_conflicts = self._scan()
        app._conf_survey = Survey(
            verdicts={
                ("Armor", "cuirass"): {
                    "Base.esm": ConflictThis.MASTER,
                    "Mod.esp": ConflictThis.CONFLICT_WINS,
                },
                ("Cell", "(1, 2)"): {"Mod.esp": ConflictThis.OVERRIDE_WINS},
            }
        )
        app.show_plugin_view()
        window = app._plugin_win
        try:
            nav = app._plugin_nav
            # Coloured from the index at open, with no group expanded.
            assert this_tag(ConflictThis.MASTER) in nav.item("Base.esm", "tags")
            expected = this_tag(
                worst_this([ConflictThis.CONFLICT_WINS, ConflictThis.OVERRIDE_WINS])
            )
            assert expected in nav.item("Mod.esp", "tags")

            # Expanding a group colours its records from the index too, not a
            # fresh read -- the record row takes its verdict straight across.
            nav.focus("Mod.esp")
            app._on_plugin_open()
            group = next(g for g in nav.get_children("Mod.esp") if nav.item(g, "text") == "Armor")
            nav.focus(group)
            app._on_plugin_open()
            row = nav.get_children(group)[0]
            assert this_tag(ConflictThis.CONFLICT_WINS) in nav.item(row, "tags")
        finally:
            window.destroy()

    def test_opening_the_view_starts_background_colouring(self, app: Any, monkeypatch: Any) -> None:
        """With no summary yet, the tree kicks a quiet one so colours fill in.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(type(app), "_survey_conflicts", lambda _s, **kw: calls.append(kw))
        app._conf_survey = None
        app._conf_session = object()  # a stand-in; the survey is patched out
        app.worker_running = False

        app._start_background_colouring()

        assert calls == [{"quiet": True}], "no quiet colour pass was started"

    def test_background_colouring_is_skipped_when_a_survey_exists(
        self, app: Any, monkeypatch: Any
    ) -> None:
        """A judged order colours from its index; it must not re-run the survey.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from wraithguard.patch.summary import Survey

        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(type(app), "_survey_conflicts", lambda _s, **kw: calls.append(kw))
        app._conf_survey = Survey()
        app._conf_session = object()
        app.worker_running = False

        app._start_background_colouring()

        assert calls == [], "the survey was re-run despite an existing index"

    def test_link_all_records_pairwise_conflicts(self) -> None:
        """The adjacency builder links every plugin in a record to the others."""
        from wraithguard.gui.pluginview import PluginViewMixin

        adjacency: dict[str, set[str]] = {}
        PluginViewMixin._link_all(adjacency, ["A.esp", "B.esp", "C.esp"])
        assert adjacency["A.esp"] == {"B.esp", "C.esp"}
        assert adjacency["B.esp"] == {"A.esp", "C.esp"}

    def test_selecting_a_plugin_highlights_its_broad_conflicts(self, app: Any) -> None:
        """Broad mode marks every plugin that shares any record with the click.

        Args:
            app: The application.
        """
        from wraithguard.gui.pluginview import CONFLICT_TAG

        app._shown_conflicts = [
            {"type": "Armor", "id": "cuirass", "plugins": ["A.esp", "B.esp"], "winner": "B.esp"},
            {"type": "Weapon", "id": "sword", "plugins": ["A.esp", "C.esp"], "winner": "C.esp"},
            {"type": "Book", "id": "tome", "plugins": ["D.esp"], "winner": "D.esp"},
        ]
        app._plugin_order = ["A.esp", "B.esp", "C.esp", "D.esp"]
        app.show_plugin_view()
        try:
            nav = app._plugin_nav
            app._plugin_lost_only.set(False)  # broad
            nav.selection_set("A.esp")
            app._on_plugin_node()
            assert CONFLICT_TAG in nav.item("B.esp", "tags")
            assert CONFLICT_TAG in nav.item("C.esp", "tags")
            assert CONFLICT_TAG not in nav.item("D.esp", "tags"), "a non-conflicting plugin lit up"
            # The selected plugin does not mark itself.
            assert CONFLICT_TAG not in nav.item("A.esp", "tags")
        finally:
            app._plugin_win.destroy()

    def test_lost_mode_highlights_only_records_that_lose_work(self, app: Any) -> None:
        """Lost mode ignores benign shared records, marking only real losses.

        Args:
            app: The application.
        """
        from wraithguard.gui.pluginview import CONFLICT_TAG
        from wraithguard.patch.status import ConflictThis
        from wraithguard.patch.summary import Survey

        app._shown_conflicts = [
            {"type": "Armor", "id": "cuirass", "plugins": ["A.esp", "B.esp"], "winner": "B.esp"},
            {"type": "Weapon", "id": "sword", "plugins": ["A.esp", "C.esp"], "winner": "C.esp"},
        ]
        app._plugin_order = ["A.esp", "B.esp", "C.esp"]
        # cuirass is benign (nothing lost); sword loses A's edit to C.
        app._conf_survey = Survey(
            verdicts={
                ("Armor", "cuirass"): {
                    "A.esp": ConflictThis.OVERRIDE_WINS,
                    "B.esp": ConflictThis.MASTER,
                },
                ("Weapon", "sword"): {
                    "A.esp": ConflictThis.CONFLICT_LOSES,
                    "C.esp": ConflictThis.CONFLICT_WINS,
                },
            }
        )
        app.show_plugin_view()
        try:
            nav = app._plugin_nav
            app._plugin_lost_only.set(True)  # lost only
            nav.selection_set("A.esp")
            app._on_plugin_node()
            assert CONFLICT_TAG in nav.item("C.esp", "tags"), "the losing record was not marked"
            assert CONFLICT_TAG not in nav.item("B.esp", "tags"), "a benign record was marked"
        finally:
            app._plugin_win.destroy()

    def test_the_highlight_clears_on_a_new_selection(self, app: Any) -> None:
        """Only one plugin's conflicts show at a time.

        Args:
            app: The application.
        """
        from wraithguard.gui.pluginview import CONFLICT_TAG

        app._shown_conflicts = [
            {"type": "Armor", "id": "cuirass", "plugins": ["A.esp", "B.esp"], "winner": "B.esp"},
            {"type": "Weapon", "id": "sword", "plugins": ["C.esp", "D.esp"], "winner": "D.esp"},
        ]
        app._plugin_order = ["A.esp", "B.esp", "C.esp", "D.esp"]
        app.show_plugin_view()
        try:
            nav = app._plugin_nav
            app._plugin_lost_only.set(False)
            nav.selection_set("A.esp")
            app._on_plugin_node()
            assert CONFLICT_TAG in nav.item("B.esp", "tags")
            nav.selection_set("C.esp")
            app._on_plugin_node()
            assert CONFLICT_TAG not in nav.item(
                "B.esp", "tags"
            ), "the old highlight was not cleared"
            assert CONFLICT_TAG in nav.item("D.esp", "tags")
        finally:
            app._plugin_win.destroy()

    def test_double_clicking_a_field_opens_its_full_value(self, app: Any, monkeypatch: Any) -> None:
        """The tree view's detail pane reuses the conflict window's field popup.

        Double-clicking a field row must open its full value across plugins --
        the same rich view the conflict window's field diff gives -- rather than
        leaving the reader with the truncated column text.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        app._shown_conflicts = self._scan()
        app.show_plugin_view()
        window = app._plugin_win
        try:
            detail = app._plugin_detail
            assert detail.bind("<Double-Button-1>"), "the detail pane is not wired for double-click"

            captured: dict[str, Any] = {}
            monkeypatch.setattr(
                app,
                "_show_field_value",
                lambda key, plugins, per, record_type="", record_label="": captured.update(
                    key=key, record_type=record_type, record_label=record_label
                ),
            )
            # Stash what a real _fill_plugin_detail would, then select a field row.
            app._plugin_detail_fd = {
                "plugins": ["Base.esm", "Mod.esp"],
                "per": {},
                "record_type": "Cell",
                "record_label": "(-23, 24)",
            }
            detail.configure(columns=("p0", "p1"))
            row = detail.insert("", "end", text="vertex_heights.data")
            detail.selection_set(row)
            app._on_plugin_detail_double()
            assert captured.get("key") == "vertex_heights.data"
            # The record's own type and label reach the popup, so a visualiser
            # opened from the tree view names the right cell (task #7).
            assert captured.get("record_type") == "Cell"
            assert captured.get("record_label") == "(-23, 24)"
        finally:
            window.destroy()

    def _vhgt(self, bump: tuple[tuple[int, int], float] | None = None) -> tuple[str, float]:
        """A valid ``(vertex_heights.data, offset)`` for a flat cell, one bump.

        Args:
            bump: ``((x, y), height)`` to raise a single vertex, or ``None``.

        Returns:
            The encoded field and its offset, as they appear in a field diff.
        """
        from wraithguard.land.emit import encode_field
        from wraithguard.land.heights import encode_vertex_heights

        grid = [[0.0] * 65 for _ in range(65)]
        if bump is not None:
            (x, y), value = bump
            grid[y][x] = value
        offset, payload, _ = encode_vertex_heights(grid)
        return encode_field(payload[4:]), offset

    def test_compare_strategies_previews_each_on_the_terrain(
        self, app: Any, monkeypatch: Any
    ) -> None:
        """The comparison opens a 3D view carrying a surface for each strategy.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        d0, o0 = self._vhgt()
        d1, o1 = self._vhgt(((10, 10), 800.0))
        d2, o2 = self._vhgt(((10, 10), 40.0))
        per = {
            "Base.esm": {"vertex_heights.data": d0, "vertex_heights.offset": o0},
            "A.esp": {"vertex_heights.data": d1, "vertex_heights.offset": o1},
            "B.esp": {"vertex_heights.data": d2, "vertex_heights.offset": o2},
        }
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            type(app),
            "_open_html_view",
            lambda _s, markup, stem, title="": captured.update(markup=markup, stem=stem),
        )
        app._compare_merge_strategies(["Base.esm", "A.esp", "B.esp"], per, "(0, 0)")
        assert captured.get("stem") == "terrain"
        assert "Merged: Overwrite" in captured["markup"]
        assert "Merged: Resolve" in captured["markup"]

    def test_compare_strategies_needs_two_editors(self, app: Any, monkeypatch: Any) -> None:
        """One plugin editing the terrain means nothing to compare; say so.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from wraithguard.gui import conflicts

        d0, o0 = self._vhgt(((3, 3), 200.0))
        per = {"Only.esp": {"vertex_heights.data": d0, "vertex_heights.offset": o0}}
        told: list[str] = []
        monkeypatch.setattr(
            conflicts.messagebox, "showinfo", lambda title, _msg: told.append(title)
        )
        opened: list[str] = []
        monkeypatch.setattr(
            type(app), "_open_html_view", lambda _s, *a, **k: opened.append("opened")
        )
        app._compare_merge_strategies(["Only.esp"], per, "(0, 0)")
        assert told, "the user was not told there was nothing to compare"
        assert not opened, "a view opened with only one editor"

    def test_a_terrain_field_offers_its_visualiser(self, app: Any, monkeypatch: Any) -> None:
        """A contextual view button appears for a field that has one.

        The tree view reaches the same field-value popup as the conflict
        window, so a landscape or path-grid field gets its 3D/graph button
        there too. This drives the button builder directly and confirms it
        threads the record label a visualiser needs.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from tkinter import ttk

        bar = ttk.Frame(app.root)
        try:
            plugins = ["Base.esm", "Mod.esp"]
            per = {p: {"connections": [[0, 1]], "points": [1, 2, 3]} for p in plugins}
            before = len(bar.winfo_children())
            app._add_field_view_buttons(bar, "connections", plugins, per, "(-23, 24)")
            assert len(bar.winfo_children()) > before, "no visualiser button for a path-grid field"

            captured: dict[str, Any] = {}
            monkeypatch.setattr(
                app,
                "_visualise_field",
                lambda key, plugins, per, record_label=None: captured.update(label=record_label),
            )
            button = next(w for w in bar.winfo_children() if isinstance(w, ttk.Button))
            button.invoke()
            assert captured.get("label") == "(-23, 24)"
        finally:
            bar.destroy()

    def test_a_mesh_field_offers_the_3d_viewer(self, app: Any, monkeypatch: Any) -> None:
        """A record whose field names a mesh gets the resource window's 3D viewer.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from tkinter import ttk

        bar = ttk.Frame(app.root)
        try:
            plugins = ["Base.esm", "Mod.esp"]
            per = {p: {"mesh": "x\\y.nif"} for p in plugins}
            app._add_field_view_buttons(bar, "mesh", plugins, per, "")
            buttons = [w for w in bar.winfo_children() if isinstance(w, ttk.Button)]
            assert buttons, "no View mesh button for a mesh field"

            captured: dict[str, Any] = {}
            monkeypatch.setattr(
                app, "_view_field_mesh", lambda plugins, per, field: captured.update(field=field)
            )
            buttons[0].invoke()
            assert captured.get("field") == "mesh"
        finally:
            bar.destroy()

    def test_an_icon_field_offers_the_image_viewer(self, app: Any, monkeypatch: Any) -> None:
        """A record whose field names an image gets the texture viewer.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from tkinter import ttk

        bar = ttk.Frame(app.root)
        try:
            plugins = ["Base.esm", "Mod.esp"]
            per = {p: {"icon": "m\\x.dds"} for p in plugins}
            app._add_field_view_buttons(bar, "icon", plugins, per, "")
            buttons = [w for w in bar.winfo_children() if isinstance(w, ttk.Button)]
            assert buttons, "no View image button for an icon field"

            captured: dict[str, Any] = {}
            monkeypatch.setattr(
                app, "_view_field_image", lambda plugins, per, field: captured.update(field=field)
            )
            buttons[0].invoke()
            assert captured.get("field") == "icon"
        finally:
            bar.destroy()

    def test_a_field_popup_offers_the_patch_maker(self, app: Any, monkeypatch: Any) -> None:
        """The patch maker is reachable from the shared field popup, hence the tree.

        Both "Add record to patch" and "Take this field" must queue the right
        record, and neither should appear when there is nothing to choose.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from tkinter import ttk

        bar = ttk.Frame(app.root)
        try:
            plugins = ["Base.esm", "Mod.esp"]
            app._add_patch_buttons(bar, "weight", plugins, "Armor", "cuirass")
            labels = [w.cget("text") for w in bar.winfo_children() if isinstance(w, ttk.Button)]
            assert any("record" in t.lower() for t in labels), "no add-record button"
            assert any("field" in t.lower() for t in labels), "no take-field button"

            record: dict[str, Any] = {}
            field: dict[str, Any] = {}
            monkeypatch.setattr(app, "_patch_whole_record", record.update)
            monkeypatch.setattr(app, "_patch_field", lambda c, path: field.update(c, _path=path))
            for w in bar.winfo_children():
                if isinstance(w, ttk.Button):
                    w.invoke()
            assert record.get("id") == "cuirass" and record.get("type") == "Armor"
            assert field.get("_path") == "weight"

            # Nothing to choose (one plugin) means no buttons at all.
            solo = ttk.Frame(app.root)
            app._add_patch_buttons(solo, "weight", ["Mod.esp"], "Armor", "cuirass")
            assert not [w for w in solo.winfo_children() if isinstance(w, ttk.Button)]
            solo.destroy()
        finally:
            bar.destroy()

    def test_a_large_group_is_inserted_in_batches(self, app: Any) -> None:
        """Every record is listed, none is dropped, and the window survives.

        The first version capped a group at 500 rows because inserting more at
        once froze Tk. Batching removes the cap; this pins that the cap is
        actually gone rather than merely raised.

        Args:
            app: The application.
        """
        app._shown_conflicts = [
            {
                "type": "Cell",
                "id": f"({n}, 0)",
                "plugins": ["Mod.esp"],
                "winner": "Mod.esp",
                "involves_subset": False,
            }
            for n in range(1200)
        ]
        app.show_plugin_view()
        window = app._plugin_win
        try:
            nav = app._plugin_nav
            nav.focus("Mod.esp")
            app._on_plugin_open()
            group = nav.get_children("Mod.esp")[0]
            nav.focus(group)
            app._on_plugin_open()
            # Batches are scheduled with after(); let them all run.
            for _ in range(40):
                app.root.update()
            assert len(nav.get_children(group)) == 1200
        finally:
            window.destroy()

    def test_a_judged_row_takes_its_colour_and_rolls_up(self, app: Any) -> None:
        """A verdict must reach the row, its group and its plugin.

        Args:
            app: The application.
        """
        from wraithguard.patch.status import ConflictThis

        app._shown_conflicts = self._scan()
        app.show_plugin_view()
        window = app._plugin_win
        try:
            nav = app._plugin_nav
            nav.focus("Mod.esp")
            app._on_plugin_open()
            group = next(g for g in nav.get_children("Mod.esp") if nav.item(g, "text") == "Armor")
            nav.focus(group)
            app._on_plugin_open()
            app._paint("Mod.esp", {("Armor", "cuirass"): ConflictThis.CONFLICT_LOSES})
            row = nav.get_children(group)[0]
            assert "this-conflict_loses" in nav.item(row, "tags")
            assert "this-conflict_loses" in nav.item(group, "tags")
            assert "this-conflict_loses" in nav.item("Mod.esp", "tags")
        finally:
            window.destroy()

    def test_the_plugin_view_survives_an_empty_scan(self, app: Any) -> None:
        """Opening it before scanning must say so, not raise.

        Args:
            app: The application.
        """
        app._shown_conflicts = []
        app.show_plugin_view()
        window = app._plugin_win
        try:
            assert app._plugin_nav.get_children() == ()
            assert "scan" in app.status_var.get().lower()
        finally:
            window.destroy()

    def test_format_reference_is_not_offered_for_an_unknown_type(self, app: Any) -> None:
        """It must not open an empty window for a type it cannot describe.

        Args:
            app: The application.
        """
        before = set(app.root.winfo_children())
        app._show_format_reference("Nonesuch")
        assert set(app.root.winfo_children()) == before

    def test_help_renders_a_document(self, app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """The Help button must produce a real page, not an empty file.

        The viewer itself is stubbed: launching a browser or an embedded webview
        is not something a CI job should do, and what matters here is that the
        document was found, rendered and handed over.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        opened: list[Path] = []
        monkeypatch.setattr(
            type(app), "open_html_in_app", lambda _self, path, _title: opened.append(Path(path))
        )
        app._open_document("Read me", "README.md")
        assert opened, "the rendered page was never handed to a viewer"
        page = opened[0].read_text(encoding="utf-8")
        assert page.startswith("<!DOCTYPE html>")
        assert "<nav>" in page, "the contents sidebar is missing"


def _first_widget_of_class(widget: Any, name: str) -> Any:
    """Find the first widget of a given Tk class in a tree.

    Args:
        widget: The root of the tree.
        name: The Tk class name, as ``winfo_class`` reports it.

    Returns:
        The widget, or ``None``.
    """
    if widget.winfo_class() == name:
        return widget
    for child in widget.winfo_children():
        found = _first_widget_of_class(child, name)
        if found is not None:
            return found
    return None


def _first_text_widget(widget: Any) -> Any:
    """Find the first Text widget in a tree.

    Args:
        widget: The root of the tree.

    Returns:
        The widget, or ``None``.
    """
    return _first_widget_of_class(widget, "Text")


class TestJournalChainsWindow:
    """The Journal Chains window: resolved stages, their triggers, and every other effect.

    This is exactly the kind of window ``test_gui_smoke.py``'s own module
    docstring warns about: its detail pane is a scrollable frame of label
    pairs built up field by field, not a Treeview whose shape a static
    checker can see. Two real defects were only ever visible on a real
    display -- a standalone Script's own ``Begin``/``End`` lines showing up
    as if they were quest effects, and a shared enclosing condition printed
    once for the triggering Journal call and again for the sibling effects
    right after it, which read as the same line duplicated by mistake.
    """

    #: A quest whose single stage is set from a *standalone* Script, not its
    #: own (typically empty) journal-type entry -- the realistic shape,
    #: since most real quest scripts live outside the journal topic itself.
    QUEST: Final = [
        {"type": "Dialogue", "id": "AATL_Q_VSLDS", "dialogue_type": "Journal"},
        {
            "type": "DialogueInfo",
            "id": "vslds_stage_10",
            "text": "Barrier stage.",
            "quest_state": "",
            "flags": "",
            "data": {"disposition": 10},
            "script_text": "",
        },
    ]

    #: Shaped to exercise both defects at once: a ``Begin``/``End`` pair
    #: framing the whole script, and a three-level nested ``if`` whose
    #: innermost block holds both the ``Cast`` (an ordinary effect) and the
    #: ``Journal`` call (the trigger) side by side -- exactly the case where
    #: the call's own condition and an effect group's condition are the same
    #: text.
    SCRIPT: Final = {
        "type": "Script",
        "id": "AATL_VSLDS_SCR_Barriers",
        "flags": "",
        "text": (
            "Begin AATL_VSLDS_SCR_Barriers\n"
            "if ( GetDisabled )\n"
            "    set state to 1\n"
            "endif\n"
            'if ( state == 2 )\n'
            '    if ( Player->HasItemEquipped "AATL_C_Dae_amu_Vas_u" == 0 )\n'
            '        if ( Player->HasItemEquipped "AATL_C_Dae_amu_Vas" == 0 )\n'
            '            Cast "AATL_VSLDS_SPL_Barrier" Player\n'
            '            Journal "AATL_Q_VSLDS", 10\n'
            "            return\n"
            "        endif\n"
            "    endif\n"
            "endif\n"
            "End\n"
        ),
    }

    def _open_journal_view(self, app: Any, records: list[dict[str, Any]]) -> None:
        """Wire a fake reading session and open the window, waiting for its background fill.

        ``show_journal_view`` reads every plugin and resolves every quest on
        a background thread, then hands the result back via
        ``root.after(0, ...)``. That ``after`` callback is only ever
        delivered while a real ``mainloop`` is pumping events -- polling
        ``root.update()`` in a loop does not satisfy it, since ``after()``
        called from a foreign thread needs the interpreter's own event loop
        actually running to pick it up. So this runs the real loop, with its
        own ``after``-scheduled check breaking out of it once the tree is
        populated or a generous timeout elapses.

        Args:
            app: The application -- a ``fresh_app``, so this test's window
                does not collide with a window an earlier test already
                opened on the shared module-scoped one.
            records: The plugin's decoded records, handed back for any path
                the fake session is asked to read.
        """

        class _FakeSession:
            exe = "tes3conv"

            def records(self, _path: str) -> list[dict[str, Any]]:
                return records

        app._conf_session = _FakeSession()
        app._conf_paths = {"Test.esp": "/fake/path"}
        app._ensure_conflict_session = lambda conv=None: True
        app.order_panel = types.SimpleNamespace(get_enabled=lambda: ["Test.esp"])
        app._apply_exclusions = list

        app.show_journal_view()

        root = app.root
        state = {"timed_out": False}

        def _check() -> None:
            nav = app._journal_nav
            if not nav.exists("__loading__") and nav.get_children():
                root.quit()
            else:
                root.after(50, _check)

        def _give_up() -> None:
            state["timed_out"] = True
            root.quit()

        root.after(50, _check)
        root.after(5000, _give_up)
        root.mainloop()
        if state["timed_out"]:
            pytest.fail("the journal view never finished its background read")

    @staticmethod
    def _detail_rows(app: Any) -> list[tuple[str, str]]:
        """The detail pane's current (label, value) pairs, in display order.

        Args:
            app: The application, with its journal window already open.

        Returns:
            One pair per row -- the pane is a frame of two-label rows, not a
            Treeview, so there is no ``.get_children()``/``.item()`` to read
            this off directly.
        """
        return [
            (row.winfo_children()[0].cget("text"), row.winfo_children()[1].cget("text"))
            for row in app._journal_detail_body.winfo_children()
            if len(row.winfo_children()) == 2
        ]

    def test_begin_and_end_are_not_shown_as_quest_effects(self, fresh_app: Any) -> None:
        """A script's own Begin/End lines are structure, not something it does.

        Args:
            fresh_app: A fresh application, so it gets its own journal window.
        """
        self._open_journal_view(fresh_app, [*self.QUEST, self.SCRIPT])
        window = fresh_app._journal_win
        try:
            nav = fresh_app._journal_nav
            stage_node = nav.get_children("AATL_Q_VSLDS")[0]
            nav.selection_set(stage_node)
            fresh_app._on_journal_node()

            also_happens = [v for label, v in self._detail_rows(fresh_app) if label == "Also happens"]
            assert not any("Begin" in value for value in also_happens), also_happens
            assert "End" not in also_happens, also_happens
        finally:
            window.destroy()

    def test_a_shared_condition_is_shown_once_not_per_statement(self, fresh_app: Any) -> None:
        """Consecutive statements under the same if-block share one condition row.

        Regression test for the exact shape that read as a duplicated line:
        ``Cast`` and the ``Journal`` call sit in the same three-level nested
        ``if``, and showing the call's own condition and then the sibling
        effect group's identical condition right after it looked like a
        mistake rather than two facts that happen to agree.

        Args:
            fresh_app: A fresh application, so it gets its own journal window.
        """
        self._open_journal_view(fresh_app, [*self.QUEST, self.SCRIPT])
        window = fresh_app._journal_win
        try:
            nav = fresh_app._journal_nav
            stage_node = nav.get_children("AATL_Q_VSLDS")[0]
            nav.selection_set(stage_node)
            fresh_app._on_journal_node()

            rows = self._detail_rows(fresh_app)
            shared = "if ( state == 2 )"
            matching = [value for label, value in rows if label == "if" and value.startswith(shared)]
            assert len(matching) == 1, f"expected the shared condition once, got {matching}"

            also_happens = [value for label, value in rows if label == "Also happens"]
            assert any("Cast" in value for value in also_happens)
            assert "return" in also_happens
        finally:
            window.destroy()

    def test_the_stages_own_call_site_appears_under_it_in_the_tree(self, fresh_app: Any) -> None:
        """The standalone Script setting the stage shows as a child of that stage.

        Args:
            fresh_app: A fresh application, so it gets its own journal window.
        """
        self._open_journal_view(fresh_app, [*self.QUEST, self.SCRIPT])
        window = fresh_app._journal_win
        try:
            nav = fresh_app._journal_nav
            stage_node = nav.get_children("AATL_Q_VSLDS")[0]
            children = nav.get_children(stage_node)
            assert len(children) == 1
            assert "AATL_VSLDS_SCR_Barriers" in nav.item(children[0], "text")
        finally:
            window.destroy()


class TestRuleMakerWindow:
    """The rebuilt rule maker.

    It covers every rule the guidelines describe now, so the window has a lot
    more moving parts than the three-radio-button version it replaced. What is
    checked here is that it builds, that every rule type is offered, and that
    the live preview and the write button actually track validity -- the last
    being the whole safety mechanism, since mlox silently discards a rule it
    cannot use.
    """

    def test_the_window_opens_with_content(self, app: Any) -> None:
        """The blank-window failure mode, on the window with the most widgets.

        Args:
            app: The application.
        """
        app.on_rule_maker()
        window = app._rm_win

        try:
            assert window.winfo_exists()
            assert window.winfo_children()
        finally:
            window.destroy()

    def test_every_documented_rule_type_is_offered(self, app: Any) -> None:
        """A rule the guidelines describe but the window cannot write is a gap.

        Args:
            app: The application.
        """
        from wraithguard.rules.authoring import RULE_KINDS

        app.on_rule_maker()
        try:
            offered = {
                str(child.cget("value"))
                for child in _all_widgets(app._rm_win)
                if child.winfo_class() == "TRadiobutton"
            }
            assert set(RULE_KINDS) <= offered, f"missing: {set(RULE_KINDS) - offered}"
        finally:
            app._rm_win.destroy()

    def test_the_preview_renders_the_rule_being_built(self, app: Any) -> None:
        """The preview is how someone checks the rule before writing it.

        Args:
            app: The application.
        """
        app.on_rule_maker()
        try:
            app._rm_kind.set("Order")
            app._rm_list.insert("end", "A.esp")
            app._rm_list.insert("end", "B.esp")
            app._rm_refresh()

            shown = app._rm_preview.get("1.0", "end")
            assert "[Order]" in shown
            assert "A.esp" in shown and "B.esp" in shown
        finally:
            app._rm_win.destroy()

    def test_the_write_button_follows_validity(self, app: Any) -> None:
        """An invalid rule must not be writable; a valid one must be.

        This is the safety mechanism: mlox discards a rule it cannot parse
        without saying so, and this window is the only place a person finds out.

        Args:
            app: The application.
        """
        app.on_rule_maker()
        try:
            app._rm_kind.set("Order")
            app._rm_list.insert("end", "Only.esp")  # an [Order] needs two
            app._rm_refresh()
            assert str(app._rm_write.cget("state")) == "disabled"

            app._rm_list.insert("end", "Second.esp")
            app._rm_refresh()
            assert str(app._rm_write.cget("state")) == "normal"
        finally:
            app._rm_win.destroy()

    def test_problems_are_shown_with_their_severity(self, app: Any) -> None:
        """Warnings and errors read differently and must not look the same.

        Args:
            app: The application.
        """
        app.on_rule_maker()
        try:
            app._rm_kind.set("NearEnd")  # discouraged: a warning, not an error
            app._rm_list.insert("end", "A.esp")
            app._rm_refresh()

            shown = app._rm_problems.get("1.0", "end")
            assert "warning:" in shown
            assert str(app._rm_write.cget("state")) == "normal", "a warning must not block"
        finally:
            app._rm_win.destroy()

    def test_grouping_builds_an_any_expression(self, app: Any) -> None:
        """The shape the guidelines use for "whichever version you installed".

        Args:
            app: The application.
        """
        app.on_rule_maker()
        try:
            app._rm_kind.set("Requires")
            app._rm_list.insert("end", "Mod.esp")
            app._rm_list.insert("end", "DepA.esp")
            app._rm_list.insert("end", "DepB.esp")
            app._rm_list.selection_set(1, 2)
            app._rm_group_any()
            app._rm_refresh()

            assert "[ANY DepA.esp DepB.esp]" in app._rm_preview.get("1.0", "end")
        finally:
            app._rm_win.destroy()

    def test_the_rule_guide_opens_from_the_window(self, app: Any, monkeypatch: Any) -> None:
        """The reference is only useful if it is reachable while writing a rule.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        opened: list[str] = []
        monkeypatch.setattr(
            type(app), "open_html_in_app", lambda _self, path, _title: opened.append(str(path))
        )
        app.on_rule_maker()
        try:
            guide = [
                child
                for child in _all_widgets(app._rm_win)
                if child.winfo_class() == "TButton" and "guide" in str(child.cget("text")).lower()
            ]
            assert guide, "the rule maker offers no way to reach the rule guide"
            guide[0].invoke()
            assert opened, "the guide button rendered nothing"
        finally:
            app._rm_win.destroy()


class TestSettingsRoundTrip:
    """What is saved must come back.

    The failure this exists for is quiet and permanent: a new option added to
    ``_gather_settings`` but not to ``_load_settings`` is written to disk on
    every exit and silently discarded on every start. Nothing errors, the file
    looks right, and the setting simply never sticks.
    """

    #: A distinctive value per option, chosen so a field restored from the wrong
    #: key is visible rather than coincidentally equal.
    STRINGS = {
        "cfg_var": "settings-probe/openmw.cfg",
        "customizations_var": "settings-probe/momw-customizations.toml",
        "subset_file_var": "settings-probe/subset.txt",
        "list_name_var": "total-overhaul",
        "plugin_order_yml_var": "settings-probe/plugin-order.yml",
        "exclude_var": "Excluded One.esp, Excluded Two.esp",
        "groundcover_var": "My Grass.esp",
        "plugin_order_url_var": "https://example.invalid/order.yml",
        "rules_url_var": "https://example.invalid/rules/%s.txt",
    }

    #: Every boolean option, flipped from whatever it defaults to.
    BOOLEANS = (
        "write_toml_inplace_var",
        "dry_run_var",
        "write_cfg_var",
        "sort_data_paths_var",
        "no_backup_var",
        "no_predicate_warnings_var",
        "create_subset_doc_var",
        "keep_json_var",
        "cleanup_html_var",
    )

    def test_text_options_survive_a_save_and_load(self, fresh_app: Any) -> None:
        """Each one, individually, so a failure names the field.

        Args:
            fresh_app: An application on a scratch settings file.
        """
        for name, value in self.STRINGS.items():
            getattr(fresh_app, name).set(value)
        fresh_app._save_settings()

        for name in self.STRINGS:
            getattr(fresh_app, name).set("")
        fresh_app._load_settings()

        lost = {
            name: getattr(fresh_app, name).get()
            for name, value in self.STRINGS.items()
            if getattr(fresh_app, name).get() != value
        }
        assert not lost, f"saved but not restored: {lost}"

    def test_checkboxes_survive_a_save_and_load(self, fresh_app: Any) -> None:
        """A toggle that resets every launch is worse than no toggle.

        Args:
            fresh_app: An application on a scratch settings file.
        """
        flipped = {name: not getattr(fresh_app, name).get() for name in self.BOOLEANS}
        for name, value in flipped.items():
            getattr(fresh_app, name).set(value)
        fresh_app._save_settings()

        for name in self.BOOLEANS:
            getattr(fresh_app, name).set(not flipped[name])
        fresh_app._load_settings()

        lost = {
            name: getattr(fresh_app, name).get()
            for name, value in flipped.items()
            if getattr(fresh_app, name).get() != value
        }
        assert not lost, f"saved but not restored: {lost}"

    def test_the_rules_list_survives_a_save_and_load(self, fresh_app: Any, tmp_path: Path) -> None:
        """The rule files are the most laborious thing to re-enter by hand.

        Order matters as much as membership: the rule files are consulted in
        list order, so a round trip that restored them shuffled would change
        which rule wins without changing anything visible.

        Args:
            fresh_app: An application on a scratch settings file.
            tmp_path: A per-test temp directory, for platform-native paths.
        """
        wanted = [tmp_path / "mlox_base.txt", tmp_path / "my_rules.txt"]
        for path in wanted:
            fresh_app.rules_panel.listbox.insert("end", str(path))
        fresh_app._save_settings()

        fresh_app.rules_panel.listbox.delete(0, "end")
        fresh_app._load_settings()

        assert fresh_app.rules_panel.get_paths() == wanted

    def test_every_saved_key_is_read_back(self, fresh_app: Any) -> None:
        """The general form of the defect, for options added later.

        Saving a key that nothing ever loads is dead weight at best and a
        setting that does not stick at worst, and it is invisible from the
        outside. This compares the two halves directly rather than waiting for
        someone to notice a checkbox forgetting itself.

        Args:
            fresh_app: An application on a scratch settings file.
        """
        import json

        saved = set(fresh_app._gather_settings())
        source = Path(fresh_app._settings_file())
        fresh_app._save_settings()
        on_disk = set(json.loads(source.read_text(encoding="utf-8")))
        assert saved == on_disk, "the settings file does not match what was gathered"

        # A key is "read back" if _load_settings mentions it. Checked against
        # the source because several are restored through paths that do not go
        # via a Tk variable -- the overrides, the rules list, the theme.
        import inspect

        loader = inspect.getsource(type(fresh_app)._load_settings)
        never_read = sorted(key for key in saved if f'"{key}"' not in loader)
        assert not never_read, f"saved but never loaded: {never_read}"


class TestLogThemes:
    """Switching theme must repaint, and must not raise.

    Theme code reaches into a palette by name, and a missing key raises at the
    moment of the switch. That is how the blank Format-reference window
    happened, and the log pane is the widget most palette keys exist for.
    """

    @staticmethod
    def _apply(app_under_test: Any, name: str) -> str | None:
        """Apply one theme and report what went wrong, if anything.

        Args:
            app_under_test: The application.
            name: The theme name.

        Returns:
            A description of the failure, or ``None`` when it applied cleanly.
        """
        try:
            app_under_test._apply_log_theme(name, announce=False)
        except (KeyError, tkinter.TclError) as exc:  # pragma: no cover - the defect
            return f"{type(exc).__name__}: {exc}"
        return None

    def test_every_offered_theme_applies(self, fresh_app: Any) -> None:
        """A theme in the dropdown that cannot be applied is a trap.

        Args:
            fresh_app: An application on a scratch settings file.
        """
        failed = {
            name: problem
            for name in fresh_app._theme_names()
            if (problem := self._apply(fresh_app, name)) is not None
        }
        assert not failed, f"themes that could not be applied: {failed}"

    def test_switching_theme_changes_the_log_colors(self, fresh_app: Any) -> None:
        """Applying without repainting looks exactly like a theme that matches.

        Args:
            fresh_app: An application on a scratch settings file.
        """
        names = fresh_app._theme_names()
        if len(names) < 2:  # pragma: no cover - a build with one preset
            pytest.skip("only one theme is available")

        seen: set[str] = set()
        for name in names:
            fresh_app._apply_log_theme(name, announce=False)
            seen.add(str(fresh_app.log_text.tag_cget("error", "foreground")))
        assert len(seen) > 1, "every theme painted the error tag the same color"

    def test_the_chosen_theme_is_remembered(self, fresh_app: Any) -> None:
        """It is a per-person preference, so it has to outlive the session.

        Args:
            fresh_app: An application on a scratch settings file.
        """
        other = [n for n in fresh_app._theme_names() if n != fresh_app.log_theme_var.get()]
        if not other:  # pragma: no cover - a build with one preset
            pytest.skip("only one theme is available")

        fresh_app._apply_log_theme(other[0], announce=False)
        fresh_app.log_theme_var.set(other[0])
        fresh_app._save_settings()

        assert fresh_app._saved_log_theme_name() == other[0]


class TestHelpMenu:
    """Every shipped document opens, and opens with something in it."""

    def test_every_help_document_renders(self, app: Any, monkeypatch: Any) -> None:
        """One entry that renders nothing is a menu item that does nothing.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from wraithguard.gui import HELP_DOCUMENTS

        opened: list[Path] = []
        monkeypatch.setattr(
            type(app), "open_html_in_app", lambda _self, path, _title: opened.append(Path(path))
        )
        for name in HELP_DOCUMENTS:
            opened.clear()
            app._open_document(name, name)
            assert opened, f"{name} produced no page"
            page = opened[0].read_text(encoding="utf-8")
            assert page.startswith("<!DOCTYPE html>"), f"{name} is not a page"
            assert "<nav>" in page, f"{name} rendered without a contents sidebar"
            assert len(page) > 2000, f"{name} rendered almost empty"

    def test_a_missing_document_does_not_open_an_empty_window(
        self, app: Any, monkeypatch: Any
    ) -> None:
        """It reports the problem instead, which is the recoverable outcome.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        import wraithguard_toolkit_gui as gui

        told: list[str] = []
        monkeypatch.setattr(gui.messagebox, "showinfo", lambda title, _msg: told.append(title))
        monkeypatch.setattr(
            type(app), "open_html_in_app", lambda *_a: pytest.fail("a viewer was opened")
        )
        app._open_document("Nope", "NO_SUCH_DOCUMENT.md")
        assert told, "a missing document failed silently"

    def test_the_rule_guide_covers_the_rule_vocabulary(self, app: Any, monkeypatch: Any) -> None:
        """The reference has to describe the rules the tool can actually write.

        A rule kind the rule maker offers but the guide never mentions leaves
        someone with a button and no explanation of it.

        Args:
            app: The application.
            monkeypatch: Pytest's patcher.
        """
        from wraithguard.rules.authoring import RULE_KINDS

        opened: list[Path] = []
        monkeypatch.setattr(
            type(app), "open_html_in_app", lambda _self, path, _title: opened.append(Path(path))
        )
        app._open_document("Writing mlox rules", "MLOX_RULES.md")
        if not opened:  # pragma: no cover - a trimmed checkout
            pytest.skip("MLOX_RULES.md is not in this checkout")

        page = opened[0].read_text(encoding="utf-8")
        missing = [kind for kind in RULE_KINDS if f"[{kind}]" not in page]
        assert not missing, f"the rule guide never mentions: {missing}"

    def test_open_in_browser_prefers_loopback(
        self, app: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        """The help window's 'Open in browser' serves over loopback, not file://.

        file:// is refused on some platforms (the Steam Deck), so the page is
        served over loopback and the browser gets that URL.

        Args:
            app: The application.
            tmp_path: A scratch directory.
            monkeypatch: Pytest's patcher.
        """
        page = tmp_path / "help.html"
        page.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
        calls: list[tuple[str, Any]] = []
        monkeypatch.setattr(type(app), "_serve_html_file", lambda _s, _p: "http://127.0.0.1:9/x")
        monkeypatch.setattr(
            type(app), "_open_url_in_browser", lambda _s, u: calls.append(("url", u))
        )
        monkeypatch.setattr(
            type(app), "_open_file_in_browser", lambda _s, p: calls.append(("file", p))
        )

        app._open_html_in_browser_loopback(page)

        assert calls == [("url", "http://127.0.0.1:9/x")]

    def test_open_in_browser_falls_back_to_file_when_no_port(
        self, app: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        """Only when no loopback port can be bound does it use file://.

        Args:
            app: The application.
            tmp_path: A scratch directory.
            monkeypatch: Pytest's patcher.
        """
        page = tmp_path / "help.html"
        page.write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
        calls: list[tuple[str, Any]] = []
        monkeypatch.setattr(type(app), "_serve_html_file", lambda _s, _p: None)
        monkeypatch.setattr(
            type(app), "_open_url_in_browser", lambda _s, u: calls.append(("url", u))
        )
        monkeypatch.setattr(
            type(app), "_open_file_in_browser", lambda _s, p: calls.append(("file", p))
        )

        app._open_html_in_browser_loopback(page)

        assert calls == [("file", page)]


class TestBackupsWindow:
    """The one window that reports "nothing found" as a normal outcome."""

    def test_it_opens_with_content_when_there_are_backups(self, app: Any) -> None:
        """The listing is the point; an empty frame is indistinguishable from a bug.

        Args:
            app: The application.
        """
        app._show_backups_window([], "no backups found")
        window = app._bk_win
        try:
            assert window.winfo_exists()
            assert window.winfo_children(), "the backups window opened empty"
            assert app.status_var.get() == "no backups found"
        finally:
            window.destroy()

    def test_opening_it_twice_does_not_leave_two(self, app: Any) -> None:
        """Otherwise the stale one stays on screen showing stale backups.

        Args:
            app: The application.
        """
        app._show_backups_window([], "first")
        first = app._bk_win
        app._show_backups_window([], "second")
        second = app._bk_win
        try:
            assert not first.winfo_exists(), "the previous backups window was left open"
            assert second.winfo_exists()
        finally:
            second.destroy()

    def test_scan_includes_the_rule_and_yml_folders(self, app: Any, tmp_path: Any) -> None:
        """Rule/yml backups live outside the data paths, so the scan has to be
        pointed at their folders or updating rules looks like it kept no backup.

        Args:
            app: The application.
            tmp_path: A scratch directory.
        """
        rules_dir = tmp_path / "mlox"
        yml_dir = tmp_path / "momw"
        rules_dir.mkdir()
        yml_dir.mkdir()
        app.rules_panel.listbox.delete(0, "end")
        app.rules_panel.listbox.insert("end", str(rules_dir / "mlox_user.txt"))
        app.plugin_order_yml_var.set(str(yml_dir / "plugin-order.yml"))

        roots = app._rule_and_yml_dirs()

        assert str(rules_dir) in roots
        assert str(yml_dir) in roots


class TestMergeSettingsEditor:
    """The .mergedlands.toml editor: pick per-layer settings, not hand-edit TOML."""

    def test_it_opens_and_seeds_every_layer(self, app: Any, tmp_path: Any) -> None:
        """The dialog builds a control for every schema layer.

        Args:
            app: The application.
            tmp_path: A scratch directory.
        """
        from wraithguard.land.meta import LAYER_NAMES

        plugin = tmp_path / "SomeMod.esp"
        plugin.write_bytes(b"")
        app._open_merge_settings_editor(plugin)
        win = app._ms_win
        try:
            assert win.winfo_exists()
            assert set(app._ms_included) == set(LAYER_NAMES)
            assert set(app._ms_strategy) == set(LAYER_NAMES)
        finally:
            win.destroy()

    def test_dropping_a_layer_disables_its_strategy(self, app: Any, tmp_path: Any) -> None:
        """A dropped layer has no collision to resolve, so its dropdown greys.

        Args:
            app: The application.
            tmp_path: A scratch directory.
        """
        plugin = tmp_path / "SomeMod.esp"
        plugin.write_bytes(b"")
        app._open_merge_settings_editor(plugin)
        try:
            app._ms_included["texture_indices"].set(False)
            app._ms_on_include("texture_indices")
            assert str(app._ms_strategy_combos["texture_indices"].cget("state")) == "disabled"
        finally:
            app._ms_win.destroy()

    def test_writing_produces_a_sidecar_that_parses(self, app: Any, tmp_path: Any) -> None:
        """The dialog's choices round-trip through the file.

        Args:
            app: The application.
            tmp_path: A scratch directory.
        """
        from wraithguard.land.merge import ConflictStrategy
        from wraithguard.land.meta import load_meta, meta_path_for

        plugin = tmp_path / "SomeMod.esp"
        plugin.write_bytes(b"")
        app._open_merge_settings_editor(plugin)
        app._ms_included["height_map"].set(False)
        app._ms_strategy["world_map_data"].set("Overwrite")
        app._ms_write()

        assert meta_path_for(plugin).is_file()
        meta = load_meta(plugin)
        assert meta.settings_for("height_map").included is False
        assert meta.settings_for("world_map_data").conflict_strategy is ConflictStrategy.OVERWRITE

    def test_it_preloads_an_existing_sidecar(self, app: Any, tmp_path: Any) -> None:
        """Opening the editor on a plugin that already has settings edits them.

        Args:
            app: The application.
            tmp_path: A scratch directory.
        """
        from wraithguard.land.merge import ConflictStrategy
        from wraithguard.land.meta import MergeSettings, PluginMeta, write_settings

        plugin = tmp_path / "SomeMod.esp"
        plugin.write_bytes(b"")
        write_settings(
            plugin,
            PluginMeta(
                meta_type="Patch",
                layers={"vertex_colors": MergeSettings(conflict_strategy=ConflictStrategy.IGNORE)},
            ),
        )
        app._open_merge_settings_editor(plugin)
        try:
            assert app._ms_strategy["vertex_colors"].get() == "Ignore"
        finally:
            app._ms_win.destroy()


class TestFormatReferenceCoversTheSchema:
    """The reference is generated, so it should hold for every record type."""

    RECORDS = ("Landscape", "Cell", "PathGrid", "Npc", "Creature", "Book")

    @pytest.mark.parametrize("record", RECORDS)
    def test_a_reference_window_opens_for_each_record(self, app: Any, record: str) -> None:
        """One record type with a gap in the schema opens a blank window.

        Args:
            app: The application.
            record: The record type to describe.
        """
        before = set(app.root.winfo_children())
        app._show_format_reference(record)
        opened = [w for w in app.root.winfo_children() if w not in before]
        if not opened:
            pytest.skip(f"{record} is not in the generated schema")
        window = opened[-1]
        try:
            text = _first_text_widget(window)
            assert text is not None, f"{record}: no text widget in the window"
            assert text.get("1.0", "end").strip(), f"{record}: the window is empty"
        finally:
            window.destroy()


class TestResourceWindowMeshDetail:
    """Meshes are read when a row is selected, and never before."""

    @staticmethod
    def _conflict(tmp_path: Path, name: str, left: bytes, right: bytes) -> dict[str, Any]:
        """Build a conflict entry backed by two real files.

        Args:
            tmp_path: Temporary directory.
            name: The asset path.
            left: Bytes for the losing provider.
            right: Bytes for the winning provider.

        Returns:
            A conflict entry.
        """
        import struct

        first, second = tmp_path / "ModA", tmp_path / "ModB"
        for folder in (first, second):
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (first / name).write_bytes(left)
        (second / name).write_bytes(right)
        del struct
        return {
            "path": name,
            "providers": [str(first), str(second)],
            "winner": str(second),
            "involves_subset": True,
            "identical": left == right,
        }

    @staticmethod
    def _mesh(blocks: int = 0) -> bytes:
        """A parseable NIF with no blocks.

        Args:
            blocks: The block count to declare.

        Returns:
            The file bytes.
        """
        import struct

        return b"NetImmerse File Format, Version 4.0.0.2\n" + struct.pack("<II", 0x04000002, blocks)

    def test_opening_the_window_parses_no_meshes(self, app: Any, tmp_path: Path) -> None:
        """The whole design rests on this: the scan must stay cheap.

        Args:
            app: The application.
            tmp_path: Temporary directory.
        """
        conflict = self._conflict(tmp_path, "meshes/a.nif", self._mesh(0), self._mesh(1))
        app._show_resource_window([conflict], {"conflicts": 1, "dirs": 2, "files": 2})
        window = app._res_win
        try:
            analyser = getattr(app, "_mesh_analyser", None)
            assert analyser is None or analyser.parsed == 0
        finally:
            window.destroy()

    def test_selecting_a_mesh_row_fills_in_the_detail(self, app: Any, tmp_path: Path) -> None:
        """And only then is anything read.

        Args:
            app: The application.
            tmp_path: Temporary directory.
        """
        conflict = self._conflict(tmp_path, "meshes/a.nif", self._mesh(0), self._mesh(1))
        app._show_resource_window([conflict], {"conflicts": 1, "dirs": 2, "files": 2})
        window = app._res_win
        try:
            tree = app._res_tree
            rows = tree.get_children()
            assert rows, "the resource tree opened empty"
            tree.selection_set(rows[0])
            tree.event_generate("<<TreeviewSelect>>")
            app.root.update_idletasks()
            assert app._mesh_analyser.parsed > 0, "selecting a mesh row read nothing"
        finally:
            window.destroy()

    def test_an_unreadable_mesh_does_not_close_the_window(self, app: Any, tmp_path: Path) -> None:
        """Mod folders hold files that are not meshes at all.

        Args:
            app: The application.
            tmp_path: Temporary directory.
        """
        conflict = self._conflict(tmp_path, "meshes/a.nif", b"junk", self._mesh(0))
        app._show_resource_window([conflict], {"conflicts": 1, "dirs": 2, "files": 2})
        window = app._res_win
        try:
            lines = app._mesh_detail(conflict)
            assert any("could not read" in line for line in lines)
            assert window.winfo_exists()
        finally:
            window.destroy()


class TestResourceWindowShowsFindingsWithoutClicking:
    """The findings must be visible in the list, not only after selecting a row.

    This is the gap that shipped: the engine had the mesh pass, the detail
    panel had it, and the *window* called neither -- so the feature existed and
    was unreachable. Every test passed, because each one exercised a piece
    rather than the path a user takes.
    """

    @staticmethod
    def _entry(path: str, finding: Any) -> dict[str, Any]:
        """A conflict entry carrying a pre-made finding.

        Args:
            path: The asset path.
            finding: The mesh finding to attach, or ``None``.

        Returns:
            A conflict entry.
        """
        entry: dict[str, Any] = {
            "path": path,
            "providers": ["ModA", "ModB"],
            "winner": "ModB",
            "involves_subset": False,
            "identical": False,
        }
        if finding is not None:
            entry["mesh"] = finding
        return entry

    def test_a_mesh_with_a_finding_is_marked_in_the_list(self, app: Any) -> None:
        """A signal you must click every row to see is not triage.

        Args:
            app: The application.
        """
        from wraithguard.nif.analysis import MeshFinding
        from wraithguard.nif.report import Difference

        finding = MeshFinding(
            "meshes/a.nif", difference=Difference(None, True, False, [], [], False)
        )
        app._show_resource_window([self._entry("meshes/a.nif", finding)], {"conflicts": 1})
        window = app._res_win
        try:
            row = app._res_tree.get_children()[0]
            assert "!" in app._res_tree.item(row, "values"), app._res_tree.item(row, "values")
        finally:
            window.destroy()

    def test_an_unreadable_mesh_is_marked_differently(self, app: Any) -> None:
        """ "could not read" and "loses collision" send a user elsewhere.

        Args:
            app: The application.
        """
        from wraithguard.nif.analysis import MeshFinding

        finding = MeshFinding("meshes/a.nif", unreadable="NIF version 0x14020007")
        app._show_resource_window([self._entry("meshes/a.nif", finding)], {"conflicts": 1})
        window = app._res_win
        try:
            values = app._res_tree.item(app._res_tree.get_children()[0], "values")
            assert "?" in values, values
            assert "!" not in values, values
        finally:
            window.destroy()

    def test_a_row_with_no_finding_is_not_marked(self, app: Any) -> None:
        """A negative control: a mark on everything is a mark on nothing.

        Args:
            app: The application.
        """
        app._show_resource_window([self._entry("textures/a.dds", None)], {"conflicts": 1})
        window = app._res_win
        try:
            values = app._res_tree.item(app._res_tree.get_children()[0], "values")
            assert "!" not in values and "?" not in values, values
        finally:
            window.destroy()


class TestThreeDButtonsAreReachable:
    """The buttons must exist, and must enable only for meshes.

    Written because "wired into the app" was claimed once already while the
    feature was unreachable from the GUI. A structural check of the source
    passed then too; only opening the window catches this.
    """

    @staticmethod
    def _entry(path: str) -> dict[str, Any]:
        """A conflict entry for the given asset path.

        Args:
            path: The asset path.

        Returns:
            A conflict entry.
        """
        return {
            "path": path,
            "providers": ["ModA", "ModB"],
            "winner": "ModB",
            "involves_subset": False,
            "identical": False,
        }

    def test_both_buttons_exist_in_the_resource_window(self, app: Any) -> None:
        """A feature nobody can find is indistinguishable from one that is absent.

        Args:
            app: The application.
        """
        app._show_resource_window([self._entry("meshes/a.nif")], {"conflicts": 1})
        window = app._res_win
        try:
            labels = {
                str(child.cget("text"))
                for frame in window.winfo_children()
                for child in frame.winfo_children()
                if child.winfo_class() == "TButton"
            }
            assert any("3D" in label for label in labels), labels
            assert any("Export" in label and "3D" in label for label in labels), labels
        finally:
            window.destroy()

    def test_they_start_disabled(self, app: Any) -> None:
        """Nothing is selected yet, so there is no mesh to show.

        Args:
            app: The application.
        """
        app._show_resource_window([self._entry("meshes/a.nif")], {"conflicts": 1})
        window = app._res_win
        try:
            assert str(app._res_view3d.cget("state")) == "disabled"
            assert str(app._res_export3d.cget("state")) == "disabled"
        finally:
            window.destroy()

    def test_selecting_a_mesh_enables_them(self, app: Any) -> None:
        """The path a user actually takes.

        Args:
            app: The application.
        """
        app._show_resource_window([self._entry("meshes/a.nif")], {"conflicts": 1})
        window = app._res_win
        try:
            tree = app._res_tree
            tree.selection_set(tree.get_children()[0])
            tree.event_generate("<<TreeviewSelect>>")
            app.root.update_idletasks()
            assert str(app._res_view3d.cget("state")) == "normal"
            assert str(app._res_export3d.cget("state")) == "normal"
        finally:
            window.destroy()

    def test_selecting_a_texture_leaves_them_disabled(self, app: Any) -> None:
        """A negative control: enabling them for everything would be no signal.

        Args:
            app: The application.
        """
        app._show_resource_window([self._entry("textures/a.dds")], {"conflicts": 1})
        window = app._res_win
        try:
            tree = app._res_tree
            tree.selection_set(tree.get_children()[0])
            tree.event_generate("<<TreeviewSelect>>")
            app.root.update_idletasks()
            assert str(app._res_view3d.cget("state")) == "disabled"
        finally:
            window.destroy()


class TestConflictListSearch:
    """The search box narrows each list without discarding its data.

    Typing filters what is shown; clearing the box restores the full list. The
    filter itself is unit-tested in test_patch_summary.py; these pin the wiring.
    """

    def test_the_resource_list_filters_as_you_type(self, app: Any) -> None:
        """A query narrows the loose-file list to matching paths.

        Args:
            app: The application.
        """
        entries = [
            {
                "path": "meshes/apple.nif",
                "providers": ["A", "B"],
                "winner": "B",
                "involves_subset": False,
            },
            {
                "path": "textures/banana.dds",
                "providers": ["A", "B"],
                "winner": "B",
                "involves_subset": False,
            },
        ]
        app._show_resource_window(entries, {"conflicts": 2, "dirs": 2, "files": 2})
        window = app._res_win
        try:
            tree = app._res_tree
            assert len(tree.get_children()) == 2
            app._res_search_var.set("banana")
            app._refill_res_tree()
            assert len(tree.get_children()) == 1
            assert app._res_shown[0]["path"] == "textures/banana.dds"
            app._res_search_var.set("")
            app._refill_res_tree()
            assert len(tree.get_children()) == 2
        finally:
            window.destroy()

    def test_the_conflict_list_filters_as_you_type(self, app: Any) -> None:
        """A query narrows the record list to matching type/id/winner.

        Args:
            app: The application.
        """
        conflicts = [
            {
                "type": "Npc",
                "id": "bob",
                "plugins": ["A", "B"],
                "winner": "B",
                "involves_subset": False,
            },
            {
                "type": "Armor",
                "id": "cuirass",
                "plugins": ["A", "B"],
                "winner": "B",
                "involves_subset": False,
            },
        ]
        app._all_conflicts = conflicts
        app._show_conflict_window(conflicts, {"conflicts": 2, "scanned": 2})
        window = app._conflict_win
        try:
            tree = app._conf_tree
            assert len(tree.get_children()) == 2
            app._conf_search_var.set("cuirass")
            app._refill_conflict_tree()
            assert len(tree.get_children()) == 1
            assert app._shown_conflicts[0]["id"] == "cuirass"
            app._conf_search_var.set("")
            app._refill_conflict_tree()
            assert len(tree.get_children()) == 2
        finally:
            window.destroy()

    def test_clicking_a_column_header_sorts_the_list(self, app: Any) -> None:
        """A header click orders by that column; a second click reverses it.

        Args:
            app: The application.
        """
        conflicts = [
            {
                "type": "Weapon",
                "id": "z",
                "plugins": ["A", "B"],
                "winner": "B",
                "involves_subset": False,
            },
            {"type": "Armor", "id": "a", "plugins": ["A"], "winner": "A", "involves_subset": False},
        ]
        app._all_conflicts = conflicts
        app._show_conflict_window(conflicts, {"conflicts": 2, "scanned": 2})
        window = app._conflict_win
        try:
            app._sort_conflict_tree("type")
            assert [c["type"] for c in app._shown_conflicts] == ["Armor", "Weapon"]
            assert "▲" in app._conf_tree.heading("type", "text")  # ascending arrow
            app._sort_conflict_tree("type")  # a second click reverses
            assert [c["type"] for c in app._shown_conflicts] == ["Weapon", "Armor"]
            assert "▼" in app._conf_tree.heading("type", "text")  # descending arrow
        finally:
            window.destroy()


class TestTheViewerChainUnderstandsUrls:
    """Every other visualisation is a file; the 3D viewer is served.

    The chain converted its target with ``Path(...).as_uri()``, which turns a
    URL into a nonsense local path. The failure would look like a broken
    viewer rather than a mangled address, so the distinction is made before
    converting rather than after something fails.
    """

    def test_a_url_is_recognised_and_a_path_is_not(self) -> None:
        """The whole branch depends on telling them apart."""
        from wraithguard_toolkit_gui import is_view_url

        assert is_view_url("http://127.0.0.1:51283/index.html?t=abc")
        assert is_view_url("https://example.invalid/x")
        assert not is_view_url("C:/Users/someone/page.html")
        assert not is_view_url("/home/someone/page.html")

    def test_a_url_survives_with_its_query_string(self) -> None:
        """The token lives in the query string.

        Dropping it would make every request 404, and the page would look
        broken for a reason nothing on screen could explain.
        """
        from wraithguard_toolkit_gui import view_uri

        url = "http://127.0.0.1:51283/index.html?t=SECRET-TOKEN"
        assert view_uri(url) == url

    def test_a_path_becomes_a_file_uri(self, tmp_path: Path) -> None:
        """A negative control: the file case must still work."""
        from wraithguard_toolkit_gui import view_uri

        page = tmp_path / "page.html"
        page.write_text("<html></html>", encoding="utf-8")
        assert view_uri(page).startswith("file://")
        assert view_uri(page).endswith("page.html")

    def test_the_cell_map_file_is_timestamped_and_housekept(self, app: Any) -> None:
        """A stable ``cell_map.html`` would never be pruned; a stamped one is.

        The generated map now shares the timestamped naming the other views
        use, so exit-time housekeeping keeps the newest few and drops the rest.

        Args:
            app: The application.
        """
        from wraithguard.viz.housekeeping import _STAMPED, GENERATED_STEMS

        name = app._cellmap_file()
        assert name.name.startswith("cell_map_")
        match = _STAMPED.match(name.stem)
        assert match is not None and match.group("stem") == "cell_map"
        assert "cell_map" in GENERATED_STEMS

    def test_the_cell_map_is_embedded_over_loopback(self, app: Any, monkeypatch: Any) -> None:
        """A ``file://`` page is refused by some webviews; loopback is not.

        The embedded opener must serve the page and hand pywebview the URL,
        exactly as the general viewer chain does -- never the bare file path.

        Args:
            app: The application.
            monkeypatch: Patcher.
        """
        handed: list[str] = []
        monkeypatch.setattr(
            type(app), "_serve_html_file", lambda _s, _p: "http://127.0.0.1:9/cell_map.html"
        )
        monkeypatch.setattr(
            type(app),
            "_open_cell_map_pywebview",
            lambda _s, target, _t="Cell Map": handed.append(str(target)),
        )
        app._open_cell_map_embedded("C:/some/cell_map_20260810_000000.html")
        assert handed == ["http://127.0.0.1:9/cell_map.html"]

    def test_the_mesh_viewer_goes_through_the_in_app_chain(
        self, app: Any, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Not straight to the browser, which is what it did first.

        Args:
            app: The application.
            tmp_path: Temporary directory.
            monkeypatch: Patcher.
        """
        import struct

        mesh = b"NetImmerse File Format, Version 4.0.0.2\n" + struct.pack("<II", 0x04000002, 0)
        for name in ("ModA", "ModB"):
            target = tmp_path / name / "meshes" / "a.nif"
            target.parent.mkdir(parents=True)
            target.write_bytes(mesh)
        conflict = {
            "path": "meshes/a.nif",
            "providers": [str(tmp_path / "ModA"), str(tmp_path / "ModB")],
            "winner": str(tmp_path / "ModB"),
            "involves_subset": False,
            "identical": False,
        }
        app._show_resource_window([conflict], {"conflicts": 1})
        window = app._res_win
        opened: list[tuple[str, str]] = []
        monkeypatch.setattr(
            app, "open_html_in_app", lambda target, title: opened.append((str(target), title))
        )
        monkeypatch.setattr("webbrowser.open", lambda _u: opened.append(("BROWSER", "")))
        try:
            tree = app._res_tree
            tree.selection_set(tree.get_children()[0])
            tree.event_generate("<<TreeviewSelect>>")
            app.root.update_idletasks()
            app._open_mesh_viewer()
            assert opened, "nothing was opened"
            assert opened[0][0] != "BROWSER", "the viewer bypassed the in-app chain"
        finally:
            window.destroy()
            server = getattr(app, "_mesh_server", None)
            if server is not None:
                server.stop()


class TestConflictWindowAutoColours:
    """Opening the conflict window colours it without a manual step.

    The verdict colours came from a survey that only ran when Plugin summary or
    the Plugin view was opened. The window now starts that same survey quietly on
    open, so the colours fill in on their own. These pin the wrapper that decides
    whether that background survey should start -- run it when the list is not yet
    coloured, skip it when it already is or the window has since closed.
    """

    @staticmethod
    def _host(*, coloured: bool, window_open: bool = True) -> tuple[Any, list[bool]]:
        """A mixin instance with the survey and window state a test needs.

        Args:
            coloured: Whether a survey has already coloured the list.
            window_open: Whether the conflict window still exists.

        Returns:
            The host and a list that records each ``_survey_conflicts`` call's
            ``quiet`` flag, so a test can assert whether (and how) it fired.
        """
        from wraithguard.gui.conflicts import ConflictWindowsMixin
        from wraithguard.patch.summary import Survey

        host = ConflictWindowsMixin.__new__(ConflictWindowsMixin)
        host._conflict_win = type(  # type: ignore[attr-defined]
            "W", (), {"winfo_exists": staticmethod(lambda: window_open)}
        )()
        host._conf_survey = Survey(records={}) if coloured else None  # type: ignore[attr-defined]
        calls: list[bool] = []
        host._survey_conflicts = lambda *, quiet=False: calls.append(quiet)  # type: ignore[attr-defined]
        return host, calls

    def test_it_starts_a_quiet_survey_when_not_yet_coloured(self) -> None:
        """The point of the feature: an uncoloured list surveys itself on open."""
        host, calls = self._host(coloured=False)
        host._auto_survey_conflicts()
        assert calls == [True], "should start exactly one quiet survey"

    def test_it_skips_when_already_coloured(self) -> None:
        """A manual summary that beat it to the list must not trigger a re-read."""
        host, calls = self._host(coloured=True)
        host._auto_survey_conflicts()
        assert calls == [], "a coloured list must not re-survey"

    def test_it_skips_when_the_window_has_closed(self) -> None:
        """A window closed before the scheduled call must not start a read."""
        host, calls = self._host(coloured=False, window_open=False)
        host._auto_survey_conflicts()
        assert calls == [], "a closed window must not start a needless read"


class TestPacedRecolour:
    """The conflict list colours itself a chunk at a time, never one long freeze.

    A full-MOMW summary judges tens of thousands of rows; tagging them in one
    loop froze the window. The recolour now applies RECOLOUR_CHUNK rows, hands
    control back to the event loop, and continues -- so colours fill in over
    time and the window stays responsive. These pin that it (a) still colours
    every judged row, (b) does so in more than one turn, and (c) a superseding
    pass stops the old one.
    """

    @staticmethod
    def _host(tk_root: Any, n: int, after):
        from tkinter import ttk

        from wraithguard.gui.conflicts import ConflictWindowsMixin
        from wraithguard.patch.status import ConflictAll
        from wraithguard.patch.summary import Survey

        tree = ttk.Treeview(tk_root, columns=("id",), show="headings")
        rows = [{"type": "Npc", "id": f"n{i}"} for i in range(n)]
        for i in range(n):
            tree.insert("", "end", iid=str(i), values=(f"n{i}",))
        records = {("Npc", f"n{i}"): ConflictAll.CONFLICT for i in range(n)}

        host = ConflictWindowsMixin.__new__(ConflictWindowsMixin)
        host._conf_tree = tree  # type: ignore[attr-defined]
        host._conf_survey = Survey(records=records)  # type: ignore[attr-defined]
        host._shown_conflicts = rows  # type: ignore[attr-defined]
        host.root = type("R", (), {"after": staticmethod(after)})()  # type: ignore[attr-defined]
        return host, tree

    def test_owned_rows_take_a_saturated_verdict_not_a_flat_star_colour(self, tk_root: Any) -> None:
        """A ★ row must show its verdict, brighter - not a fixed orange.

        The flat ``sub`` orange used to win over the verdict tag, so a record of
        yours read the same whether it was losing work or perfectly benign. Now
        an owned row takes the saturated ``-mine`` variant of a chromatic verdict
        (benign/conflict) and the plain base tag for the neutral ones.

        Args:
            tk_root: A live Tk root.
        """
        from tkinter import ttk

        from wraithguard.gui.conflicts import ConflictWindowsMixin
        from wraithguard.patch.status import ConflictAll
        from wraithguard.patch.summary import Survey

        tree = ttk.Treeview(tk_root, columns=("id",), show="headings")
        # 0: mine + losing, 1: not-mine + losing, 2: mine + benign, 3: mine + agree.
        rows = [
            {"type": "Npc", "id": "a", "involves_subset": True},
            {"type": "Npc", "id": "b", "involves_subset": False},
            {"type": "Npc", "id": "c", "involves_subset": True},
            {"type": "Npc", "id": "d", "involves_subset": True},
        ]
        for i in range(len(rows)):
            tree.insert("", "end", iid=str(i), values=(rows[i]["id"],))
        records = {
            ("Npc", "a"): ConflictAll.CONFLICT,
            ("Npc", "b"): ConflictAll.CONFLICT,
            ("Npc", "c"): ConflictAll.OVERRIDE_BENIGN,
            ("Npc", "d"): ConflictAll.NO_CONFLICT,
        }
        host = ConflictWindowsMixin.__new__(ConflictWindowsMixin)
        host._conf_tree = tree  # type: ignore[attr-defined]
        host._conf_survey = Survey(records=records)  # type: ignore[attr-defined]
        host._shown_conflicts = rows  # type: ignore[attr-defined]
        host.root = type("R", (), {"after": staticmethod(lambda _ms, cb: cb())})()  # type: ignore[attr-defined]
        try:
            host._recolour_conflict_tree()
            assert tree.item("0", "tags") == ("status-conflict-mine",)
            assert tree.item("1", "tags") == ("status-conflict",)
            assert tree.item("2", "tags") == ("status-benign-mine",)
            # Neutral verdict, but still owned -> the brighter grey, not fg_dim.
            assert tree.item("3", "tags") == ("status-agree-mine",)
            # The retired flat-orange tag must be gone from every row.
            assert all("sub" not in tree.item(str(i), "tags") for i in range(len(rows)))
        finally:
            tree.destroy()

    def test_it_colours_every_row_across_several_turns(self, tk_root: Any) -> None:
        from wraithguard.gui.conflicts import RECOLOUR_CHUNK
        from wraithguard.patch.status import ConflictAll
        from wraithguard.patch.summary import ALL_TAGS

        n = RECOLOUR_CHUNK * 3 + 7
        turns = {"n": 0}

        def after(_ms: int, cb) -> None:
            turns["n"] += 1
            cb()  # run now; depth is one per chunk

        host, tree = self._host(tk_root, n, after)
        try:
            host._recolour_conflict_tree()
            tag = ALL_TAGS[ConflictAll.CONFLICT][0]
            assert all(tag in tree.item(str(i), "tags") for i in range(n))
            assert turns["n"] >= 3, "recolour did not pace itself across turns"
        finally:
            tree.destroy()

    def test_a_newer_pass_stops_the_old_one(self, tk_root: Any) -> None:
        from wraithguard.gui.conflicts import RECOLOUR_CHUNK
        from wraithguard.patch.status import ConflictAll
        from wraithguard.patch.summary import ALL_TAGS

        pending: list = []

        def after(_ms: int, cb) -> None:
            pending.append(cb)  # defer, so the test controls the turns

        host, tree = self._host(tk_root, RECOLOUR_CHUNK * 3, after)
        try:
            host._recolour_conflict_tree()  # paints chunk 0, schedules chunk 1
            host._recolour_token = object()  # a newer pass takes over
            while pending:
                pending.pop(0)()  # the stale continuations must now no-op
            tag = ALL_TAGS[ConflictAll.CONFLICT][0]
            coloured = sum(1 for i in range(RECOLOUR_CHUNK * 3) if tag in tree.item(str(i), "tags"))
            assert coloured == RECOLOUR_CHUNK, "a superseded pass kept painting"
        finally:
            tree.destroy()
