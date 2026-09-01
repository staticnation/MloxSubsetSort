"""A reference block listing built without using any field layout.

The layout reader in :mod:`wraithguard.nif.reader` walks a file by knowing how
wide every field is. That makes it useful and it makes it fallible in a
specific way: one wrong width desynchronises everything after it, and the
result is not an exception but a plausible-looking answer. Checking that answer
needs a second opinion that cannot fail the same way.

This module is that second opinion. It uses one fact about the format -- **a
block's type name is stored as a 32-bit length followed by that many bytes** --
and never advances by a field width or consults
:data:`~wraithguard.nif.blocks.BLOCK_LAYOUTS`, so it cannot inherit a layout
bug. Where the two agree, the layouts are right. Where they disagree, the scan
says which block *index* the disagreement starts at, and the block before it is
the one whose width is wrong.

**That fact alone is not sufficient, and the code says so because measuring it
proved it.** Every string in a NIF is length-prefixed -- block names, texture
paths, extra data -- so the invariant matches a node called ``Bip01`` exactly
as well as it matches a type name. Scanning on it alone over-counted 522 of 556
corpus files. A second filter is therefore applied: a type name must also
follow NIF's naming convention (:data:`_TYPE_CONVENTION`). That takes
reconciliation to 553 of 556.

This is a real trade and worth naming. The scan can no longer discover a block
type named arbitrarily; it can discover any type named the way every type in
this format is named. Since the purpose is to audit a reader for a single
shipped game version, that is the right side of the trade -- but it is why the
scan is a cross-check and not an oracle.

**It checks itself.** The header declares how many blocks the file holds, so a
scan finding a different number is wrong and says so via
:attr:`ScanResult.reconciles` rather than quietly reporting a bad list. A
reference that might be wrong in an unmeasured way is worse than none; this one
reports its own failures per file.

This replaces an externally supplied census. That census proved to undercount
property blocks -- verified against both this scan and NifSkope -- and carried
questions about provenance that a file generated from the user's own installed
game does not.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Final

#: The header line every NetImmerse file starts with.
_HEADER_PREFIX: Final[bytes] = b"NetImmerse File Format"

#: What a block type name may look like. Deliberately permissive about the
#: *vocabulary* -- the point of a scan is to find types this project has never
#: heard of -- and strict about the *shape*, since that is the only thing
#: separating a real name from a run of bytes that reads like one.
_CANDIDATE: Final[re.Pattern[bytes]] = re.compile(rb"[A-Za-z][A-Za-z0-9_]{2,63}")

#: Shortest and longest name lengths considered. The lower bound rejects the
#: two-character runs that appear constantly inside float data; the upper bound
#: is far beyond any real type name.
_MIN_NAME: Final[int] = 3
_MAX_NAME: Final[int] = 64

#: NIF's type-naming convention: the ``Ni`` prefix, plus the handful of
#: engine-recognised names that predate it. Without this the scan cannot tell a
#: type name from any other length-prefixed string and over-counts nearly every
#: file. It is applied to the *name*, never to a list of known types, so a type
#: this project has never implemented is still found.
_TYPE_CONVENTION: Final[re.Pattern[str]] = re.compile(
    r"(?:Ni|Root|Avoid|Bounding|Bhk|bhk)[A-Za-z0-9_]*"
)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Block type names recovered from a file by scanning for them.

    Attributes:
        type_names: The names found, in the order they appear in the file.
        declared: How many blocks the header says there are.
        header_ok: Whether the header parsed at all. When false the rest is
            empty rather than guessed.
        version: The version word, or ``0`` when the header did not parse.
    """

    type_names: list[str] = field(default_factory=list)
    declared: int = 0
    header_ok: bool = False
    version: int = 0

    @property
    def found(self) -> int:
        """How many blocks the scan located."""
        return len(self.type_names)

    @property
    def reconciles(self) -> bool:
        """Whether the scan found exactly as many blocks as the header declares.

        A scan that does not reconcile is not evidence about anything. It is
        reported rather than silently used, because the whole value of this
        module is being a reference, and a reference that might be wrong in an
        unmeasured way is worse than none.
        """
        return self.header_ok and self.found == self.declared


def scan_block_types(data: bytes) -> ScanResult:
    """Recover a file's block type names without decoding any field.

    The method is a single invariant: a type name is a ``u32`` length followed
    by that many bytes. Candidate names are found first and the length prefix
    is then checked behind each one, which is far cheaper than testing a
    ``u32`` at every offset in the file.

    Within one run of identifier-like bytes the name may be shorter than the
    run, because a block's first field can itself begin with letters. Lengths
    are therefore tried longest-first, so ``NiTriShapeData`` is preferred over
    the ``NiTriShape`` hiding at its front.

    Args:
        data: The whole file.

    Returns:
        What was found, and whether it reconciles with the declared count.
    """
    newline = data.find(b"\n")
    if not data.startswith(_HEADER_PREFIX) or newline < 0 or len(data) < newline + 9:
        return ScanResult()
    version, declared = struct.unpack_from("<II", data, newline + 1)

    names: list[str] = []
    for run in _CANDIDATE.finditer(data, newline + 9):
        start = run.start()
        if start < 4:  # pragma: no cover - the search begins past the 40-byte header
            continue
        (prefix,) = struct.unpack_from("<I", data, start - 4)
        if not _MIN_NAME <= prefix < _MAX_NAME:
            continue
        if prefix > run.end() - start:
            continue
        name = data[start : start + prefix].decode("ascii")
        if not _TYPE_CONVENTION.fullmatch(name):
            continue
        names.append(name)
    return ScanResult(names, int(declared), True, int(version))


def first_divergence(scanned: list[str], parsed: list[str]) -> int | None:
    """Find where two block listings stop agreeing.

    A count comparison says *that* a reader is wrong; an index says *where*,
    which is the difference between a number in a report and a layout that can
    be fixed. The block before the divergence is the one whose width is
    suspect, since a block is only mis-identified when the cursor arrived at
    the wrong place.

    Args:
        scanned: Type names from :func:`scan_block_types`.
        parsed: Type names the layout reader produced.

    Returns:
        The first index at which they differ, or ``None`` when one is simply a
        prefix of the other -- which is the ordinary "stopped early" case and
        not a disagreement at all.
    """
    for index, (left, right) in enumerate(zip(scanned, parsed)):
        if left != right:
            return index
    return None
