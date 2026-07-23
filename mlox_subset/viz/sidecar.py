"""Sidecar data files for the explorer, so the page itself stays small.

Embedding every decoded height grid in the document was measured to be
unworkable: sixty two-plugin cells is roughly 25 MB of JSON, which froze the
app while it was assembled and would not have opened afterwards. A real cell
map is already 5 MB with no terrain in it at all.

**Why script files rather than JSON fetched on demand.** These pages are opened
from ``file://`` -- in a webview, in tkinterweb, or straight off disk -- and
``fetch()`` against ``file://`` is blocked by the same-origin policy in every
current browser. A ``<script src="...">`` tag is not: it predates that policy
and is still allowed. So each sidecar is a tiny JavaScript file that assigns
into a global, and the page loads one by injecting a script tag. That is the
only mechanism that gives genuine lazy loading with no server, no bundler and
no network.

The split follows how the data is actually read:

* ``overview.js`` -- every detailed cell at :data:`~mlox_subset.viz.detail.OVERVIEW_STRIDE`
  sampling. Loaded once, with the page. Enough to judge a cell's shape.
* ``cells/<x>_<y>.js`` -- one cell at full resolution, plus its immediate
  neighbours' heights so a seam can be seen. Loaded only when that cell is
  opened, so cost is paid per click rather than up front.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mlox_subset.viz.geometry import Cell

#: Folder name, appended to the page's stem.
DATA_SUFFIX = "_data"


def cell_key(cell: Cell) -> str:
    """The key a cell is stored under.

    Args:
        cell: The cell.

    Returns:
        ``"x,y"``.
    """
    return f"{cell.x},{cell.y}"


def cell_filename(key: str) -> str:
    """The sidecar filename for a cell key.

    Args:
        key: A ``"x,y"`` cell key.

    Returns:
        A filename safe on every platform -- no commas or minus signs, which
        are awkward in URLs and in shell globs.
    """
    return key.replace(",", "_").replace("-", "m") + ".js"


def write_sidecars(
    page_path: str | Path,
    overview: Mapping[str, Any] | None = None,
    per_cell: Mapping[str, Any] | None = None,
    world: Mapping[str, Any] | None = None,
    cell_pages: Mapping[str, str] | None = None,
) -> Path:
    """Write the overview and per-cell data files beside a page.

    Every argument is optional and **only what is given is written**, so a
    background pass can add per-cell files and pages without clobbering the
    ``overview.js`` an earlier pass already wrote. That is what lets the map
    render from a cheap overview and have full-resolution pages fill in behind.

    Args:
        page_path: The HTML file the sidecars belong to. The folder is named
            after its stem, so several pages can coexist in one directory.
        overview: Downsampled detail for every covered cell, keyed ``"x,y"``.
            Written only when given.
        per_cell: Full-resolution detail keyed ``"x,y"``, written one file
            each and loaded on demand.
        world: The knitted world terrain. Written only when given -- it is
            currently held back with the 3D map, so callers pass nothing.
        cell_pages: Full-resolution standalone HTML pages keyed ``"x,y"``,
            written under ``pages/`` and opened when a cell's local view is
            expanded.

    Returns:
        The data folder.

    Raises:
        OSError: If the files cannot be written. The caller decides whether
            that is fatal; the page still works from the overview alone if the
            per-cell files are missing.
    """
    page = Path(page_path)
    folder = page.parent / (page.stem + DATA_SUFFIX)
    (folder / "cells").mkdir(parents=True, exist_ok=True)
    # `\n` at the end so a truncated write is visible as a syntax error rather
    # than silently defining a partial object.
    if overview is not None:
        (folder / "overview.js").write_text(
            f"window.__vizOverview={json.dumps(overview, separators=(',', ':'))};\n",
            encoding="utf-8",
        )
    if world is not None:
        (folder / "world.js").write_text(
            "window.__vizWorld=" + json.dumps(dict(world), separators=(",", ":")) + ";\n",
            encoding="utf-8",
        )
    for key, payload in (per_cell or {}).items():
        body = json.dumps(payload, separators=(",", ":"))
        (folder / "cells" / cell_filename(key)).write_text(
            f'window.__vizCellLoaded("{key}",{body});\n', encoding="utf-8"
        )
    if cell_pages:
        (folder / "pages").mkdir(parents=True, exist_ok=True)
        for key, markup in cell_pages.items():
            name = cell_filename(key).removesuffix(".js") + ".html"
            (folder / "pages" / name).write_text(markup, encoding="utf-8")
    return folder


def cell_page_href(data_dir: str, key: str) -> str:
    """The relative URL of a cell's full-resolution page.

    Args:
        data_dir: The sidecar folder name.
        key: A ``"x,y"`` cell key.

    Returns:
        A relative path, matching what the client builds.
    """
    return f"{data_dir}/pages/{cell_filename(key).removesuffix('.js')}.html"


def neighbours(cell: Cell) -> list[Cell]:
    """The eight cells touching this one.

    Landscape seams are a real failure mode -- a mod that reshapes one cell
    without matching its neighbour's edge leaves a visible cliff -- so the
    per-cell payload carries them and the local view can show the join.

    Args:
        cell: The centre cell.

    Returns:
        The surrounding cells, excluding the centre.
    """
    return [
        Cell(cell.x + dx, cell.y + dy)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    ]
