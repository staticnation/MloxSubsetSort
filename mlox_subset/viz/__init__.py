"""Visual representations of load-order conflicts.

Every renderer here is a **pure function from data to an HTML string**: no Tk,
no file I/O, no network. That is what makes them testable in the hermetic
suite, which matters more than usual because the GUI they are reached from has
no automated coverage at all (``REMAINING_WORK.md`` §4). The GUI's job is
reduced to writing the returned string to a file and opening it.

The pages answer questions the text diff cannot:

* :func:`build_conflict_map` -- *where* in the world your mods collide.
* :func:`build_height_delta` -- *how much* terrain a plugin actually moved,
  rather than "these two base64 blobs differ".
* :func:`build_pathgrid_graph` -- *which* navigation edges a mod rewired.
* :func:`build_terrain_3d` -- the cell as a surface, for when a number grid
  still does not convey the shape.

The severity colour language (green fine, yellow minor, red major) follows
``merged_lands`` (MIT), which established it for TES3 land conflicts. Matching
a tool people already read beats inventing a nicer palette. That tool *merges*
land; this one sorts and reports, so these pages answer "where do my mods
collide and who wins" rather than "what did the merge do".
"""

from __future__ import annotations

from mlox_subset.viz.conflictmap import build_conflict_map, cells_with_conflicts
from mlox_subset.viz.heightdelta import build_height_delta
from mlox_subset.viz.pathgrid import build_pathgrid_graph
from mlox_subset.viz.terrain3d import build_terrain_3d

__all__ = [
    "build_conflict_map",
    "build_height_delta",
    "build_pathgrid_graph",
    "build_terrain_3d",
    "cells_with_conflicts",
]
