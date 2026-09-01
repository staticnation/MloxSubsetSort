"""Malformed-name guards in the length-free block-type scan.

``scan_block_types`` walks a NIF looking for length-prefixed type names without
parsing the file. A byte run that looks like a name but is preceded by a
nonsensical length prefix -- too small, or larger than the run itself -- must be
rejected, or the scan would report junk as a block type. These drive each such
guard with a hand-built header plus a deliberately bad candidate.
"""

from __future__ import annotations

import struct

from wraithguard.nif.scan import scan_block_types

_HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"
_BASE = _HEADER + struct.pack("<II", 0x04000002, 0)


def test_a_prefix_below_the_minimum_length_is_skipped() -> None:
    """A length prefix smaller than the shortest possible name is not a name."""
    data = _BASE + struct.pack("<I", 1) + b"ABCDE"  # prefix 1 < 3
    assert scan_block_types(data).type_names == []


def test_a_prefix_longer_than_its_run_is_skipped() -> None:
    """A length prefix claiming more bytes than the ASCII run holds is bogus."""
    data = _BASE + struct.pack("<I", 60) + b"ABC"  # prefix 60 > 3-byte run
    assert scan_block_types(data).type_names == []


def test_a_well_formed_candidate_is_found() -> None:
    """A correctly length-prefixed, convention-following name is reported."""
    name = b"NiFooBar"
    data = _BASE + struct.pack("<I", len(name)) + name
    assert "NiFooBar" in scan_block_types(data).type_names
