"""Visual representations of load-order conflicts.

Every renderer here is a **pure function from data to an HTML string**: no Tk,
no file I/O, no network. That is what makes them testable in the hermetic
suite, which matters more than usual because the GUI they are reached from has
no automated coverage beyond the headless Tk smoke pass (``SMOKE_TEST.md``). The
GUI's job is reduced to writing the returned string to a file and opening it.

The pages answer questions the text diff cannot:

* :func:`build_conflict_map` -- *where* in the world your mods collide.
* :func:`build_height_delta` -- *how much* terrain each plugin moved, as a
  chain of edits rather than a star of comparisons against the winner.
* :func:`build_pathgrid_graph` -- *which* navigation edges a mod rewired.
* :func:`build_terrain_3d` -- the cell as a surface, for when a number grid
  still does not convey the shape.

The severity color language (green fine, yellow minor, red major) follows
``merged_lands`` (MIT), which established it for TES3 land conflicts. Matching
a tool people already read beats inventing a nicer palette. That tool *merges*
land; this one sorts and reports, so these pages answer "where do my mods
collide and who wins" rather than "what did the merge do".

**The purity guarantee above is about the four renderers, not the package.**
:mod:`~wraithguard.viz.serve` and :mod:`~wraithguard.viz.library` also live
here, and neither one is pure -- ``serve`` opens a loopback socket, and
``library`` reads a file off disk. They earn their place anyway: both exist
to get a generated page in front of a user, which is the same job
``build_conflict_map`` and its siblings do one step earlier, just for the mesh
viewer and the texture comparison rather than for a page built in this
package. Reached directly (``wraithguard.viz.serve``, ``wraithguard.viz.library``)
rather than through this module, the same way :mod:`wraithguard.nif` leaves
:mod:`~wraithguard.nif.viewer` and :mod:`~wraithguard.nif.textures` off its
own curated export list without those modules being any less real.
"""

from __future__ import annotations

from wraithguard.viz.conflictmap import build_conflict_map, cells_with_conflicts
from wraithguard.viz.heightdelta import build_height_delta
from wraithguard.viz.library import ViewerError, three_source
from wraithguard.viz.pathgrid import build_pathgrid_graph
from wraithguard.viz.serve import Payload, PublishSession, ViewerServer, payloads_for
from wraithguard.viz.terrain3d import build_terrain_3d

__all__ = [
    "Payload",
    "PublishSession",
    "ViewerError",
    "ViewerServer",
    "build_conflict_map",
    "build_height_delta",
    "build_pathgrid_graph",
    "build_terrain_3d",
    "cells_with_conflicts",
    "payloads_for",
    "three_source",
]
