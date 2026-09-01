"""Reading the structure of Morrowind NIF and KF files.

**Why this is written rather than imported.** The Python NIF library everyone
reaches for is pyFFI, which is LGPL; this project ships as a PyInstaller onefile
binary, and static bundling of an LGPL library carries a relinking obligation
nobody wants to satisfy. The C++ alternatives are no better placed -- ``nifly``
is GPL-3.0, and the ``nif.xml`` format description lives in a GPL-3.0 repository
whose own licence status is disputed upstream, which is worse than a licence
that merely says no. ``niflib`` is BSD-3, which makes it the right thing to
*consult and credit* (see ``CREDITS.md``), the same way this project treats
``tes3conv`` and ``merged_lands``.

**Why it is tractable.** pyFFI's bulk is version coverage: NIF spans Morrowind
to Starfield and the layouts move between releases. Morrowind is
**4.0.0.2 only**, and the block types a mod actually ships are a few dozen. That
is a bounded problem of the same shape as the TES3 record schema already in
``wraithguard/tes3fields``.

**Layouts are data, not code.** Every block type is a tuple of
``(field name, kind)`` pairs read by one generic walker
(:mod:`~wraithguard.nif.blocks`, :mod:`~wraithguard.nif.reader`). That is
deliberate: a NIF block has no length prefix, so the only way to find block
*n+1* is to parse block *n* exactly, and a single wrong field silently
desynchronises everything after it. Keeping the layouts as a table means a
correction is a one-line data edit that the walker's own tests already cover,
rather than a change to parsing code.

**What happens when a block is not understood.** The reader stops and says so,
naming the block index and type. It does not guess, skip ahead or scan for the
next plausible type string: without a length prefix there is no way to resume
that is not a guess, and a structure report that quietly omits half a file is
worse than one that admits where it stopped.
"""

from __future__ import annotations

from wraithguard.nif.analysis import MeshAnalyser, MeshFinding, file_digest
from wraithguard.nif.blocks import BLOCK_LAYOUTS, FieldKind, block_layout
from wraithguard.nif.geometry import Mesh, Transform, bounds, find_roots, world_meshes
from wraithguard.nif.reader import (
    ACCEPTED_VERSIONS,
    NIF_VERSION_MORROWIND,
    Block,
    NifFile,
    NifMalformedError,
    NifParseError,
    read_nif,
    read_nif_bytes,
    write_nif,
)
from wraithguard.nif.report import (
    COLLISION_NODES,
    Difference,
    Shape,
    Structure,
    compare,
    normalise_texture,
    summarise,
)

__all__ = [
    "ACCEPTED_VERSIONS",
    "BLOCK_LAYOUTS",
    "COLLISION_NODES",
    "NIF_VERSION_MORROWIND",
    "Block",
    "Difference",
    "FieldKind",
    "Mesh",
    "MeshAnalyser",
    "MeshFinding",
    "NifFile",
    "NifMalformedError",
    "NifParseError",
    "Shape",
    "Structure",
    "Transform",
    "block_layout",
    "bounds",
    "compare",
    "file_digest",
    "find_roots",
    "normalise_texture",
    "read_nif",
    "read_nif_bytes",
    "summarise",
    "world_meshes",
    "write_nif",
]
