"""Write the pages' JavaScript and CSS as static files, not inline blobs.

The generated pages used to inline every script and stylesheet into one giant
HTML string. That made them effectively undebuggable: a browser's dev tools saw
one anonymous ``<script>`` of thousands of lines, there were no file names to
set breakpoints against, and editing meant hunting through a Python string
literal. The size also made the HTML itself unreadable.

So the shared assets are written **once** into ``<data>/assets/`` and every page
-- the explorer and all the cell pages -- references them with ``<script src>``
and ``<link rel="stylesheet">``. Those tags work from ``file://`` (only
``fetch()`` is blocked there), so nothing about the offline, no-server guarantee
changes; what changes is that ``draw.js`` is now a file called ``draw.js``.

This has **no effect on the packaged binary.** The JavaScript and CSS still live
as string constants inside these modules, so PyInstaller bundles them exactly as
before -- no ``--add-data``, no new data files. They are merely *written out* at
page-generation time instead of pasted into the markup, the same way the data
sidecars already are.

Sharing beats per-page copies here because the data folder already exists and a
10 KB ``draw.js`` duplicated across sixty cell pages would be 600 KB of
identical bytes. One file, one place to edit.
"""

from __future__ import annotations

from pathlib import Path

from mlox_subset.viz.cellpage import CELL_CSS, CELL_JS
from mlox_subset.viz.draw_js import DRAW_JS
from mlox_subset.viz.explorer_js import EXPLORER_CSS, EXPLORER_JS

#: Subfolder of the data directory that holds the shared assets.
ASSETS_DIR = "assets"

#: Filename to content. The page references these by ``ASSETS_DIR/<name>``.
_FILES: dict[str, str] = {
    "draw.js": DRAW_JS,
    "explorer.js": EXPLORER_JS,
    "explorer.css": EXPLORER_CSS,
    "cellpage.js": CELL_JS,
    "cellpage.css": CELL_CSS,
}


def write_assets(data_folder: str | Path) -> Path:
    """Write the shared JS/CSS files into ``<data_folder>/assets``.

    Args:
        data_folder: The sidecar data folder for a page.

    Returns:
        The assets folder.

    Raises:
        OSError: If the files cannot be written. Callers treat this as
            non-fatal and fall back to inlining, so a page is never lost to a
            read-only directory.
    """
    folder = Path(data_folder) / ASSETS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in _FILES.items():
        (folder / name).write_text(content, encoding="utf-8")
    return folder
