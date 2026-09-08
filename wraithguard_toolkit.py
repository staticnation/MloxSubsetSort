#!/usr/bin/env python3
r"""wraithguard_toolkit.py.

Sorts ONLY a subset of plugins (your "custom" mods, e.g. the ones you added
via a umo/momw-customizations.toml file) into an existing openmw.cfg,
using an mlox-format rules database (mlox_base.txt / mlox_user.txt / whatever
`plox` downloads) to decide where they belong.

The existing content= order already in openmw.cfg is treated as FROZEN --
this script never reorders plugins that are already correctly sorted (e.g.
by momw-configurator + plox). It only figures out, for the subset plugins,
where they need to slot in relative to the frozen list and to each other.

By default this tool only PRINTS its plan -- it writes nothing. Pass
--write-cfg to patch openmw.cfg directly, and/or --emit-toml to write a
corrected momw-customizations.toml (the durable fix -- feed it back into
momw-configurator/umo so the order survives future rebuilds, instead of
being overwritten by the next rebuild like a direct cfg patch would be).

After sorting, [Requires], [Conflict], and [Note] rules from the same
rule files are also evaluated (read-only) against the final active plugin
list and printed as warnings -- e.g. "you have A and B active, and they
conflict" or "A requires B, which is missing". These are informational
only: nothing is auto-fixed or blocked because of them, and (like the
sorting rules) they are parsed by a best-effort mlox-logic interpreter,
not the real mlox engine, so treat them as a hint to go check things
yourself rather than ground truth. Pass --no-predicate-warnings to skip
this step entirely.

Also by default, only content= plugins are sorted (via mlox). data=
(folder path) insertions from --customizations are found but NOT acted on
unless you pass --sort-data-paths -- since those aren't ordered by mlox at
all, this is opt-in so you don't get surprise data= reordering from a run
that was only meant to sort plugins.

When --sort-data-paths IS given, an explicit after/before anchor you wrote
in the TOML always wins. For any insert with NO anchor, the folder itself
is scanned (non-recursively) for .esp/.esm/etc files; if it contains a
plugin that's also somewhere in the mlox-sorted content order, the data=
line is anchored next to whichever existing data= path owns the nearest
neighboring plugin in that order -- e.g. a mod folder holding NewMod.esp
gets its data= line placed next to the data= path that holds whatever
mlox decided goes right before/after NewMod.esp in content=. Every step of
that (path doesn't exist, folder has no plugins, plugin isn't part of this
run's sort, ...) is guarded to fall through to the old "no anchor ->
appended at the end" behavior rather than fail the run.

------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------

  # Preview only (default) -- prints the plan, writes nothing
  python3 wraithguard_toolkit.py \
      --cfg "C:\\Games\\OpenMW\\openmw.cfg" \
      --rules ".\\mlox\\mlox_base.txt" ".\\mlox\\mlox_user.txt" \
      --customizations "C:\\Games\\OpenMW\\momw-customizations.toml"

  # Write a corrected customizations TOML (recommended, durable fix)
  python3 wraithguard_toolkit.py \
      --cfg openmw.cfg --rules mlox/mlox_base.txt \
      --customizations momw-customizations.toml \
      --emit-toml momw-customizations.toml

  # Patch openmw.cfg directly instead/also (one-off, gets overwritten on next rebuild)
  python3 wraithguard_toolkit.py \
      --cfg openmw.cfg --rules mlox/mlox_base.txt \
      --customizations momw-customizations.toml --write-cfg

A timestamped backup of openmw.cfg is written next to the original before
--write-cfg makes any change (unless --no-backup is also given).

Rule files passed later on the command line are treated as higher priority
(mirrors mlox's own mlox_user.txt-overrides-mlox_base.txt behaviour) --
so pass mlox_base.txt first, mlox_user.txt last.

------------------------------------------------------------------------
RULE-ENGINE FIDELITY (how close this is to real mlox)
------------------------------------------------------------------------
The rule parsing and matching are ported from mlox itself (and cross-checked
against plox), so several things behave exactly like the real engine:
- Filename matching handles '*' and '?' wildcards AND the <VER> version-number
  token, with the same metacharacter escaping mlox uses.
- [Order] chains bridge over plugins you don't have: [Order] A, B, C with B
  not installed still enforces A before C (mlox keeps a phantom node; we chain
  the surviving neighbours -- same effect on your installed plugins).
- [Requires]/[Conflict]/[Note] warnings understand ALL/ANY/NOT/DESC nesting
  and the [VER]/[SIZE]/[DESC] functions (reading real plugin version/size/
  header description from the cfg's data= folders; conservative fallback when
  those files aren't reachable). [MWSE-LUA] is parsed but treated as N/A under
  OpenMW. [Patch]/[Version] blocks are parsed only well enough to skip cleanly.

Still deliberately different from full mlox (by design):
- SORTING only ever repositions the subset; the existing content= order in
  openmw.cfg is treated as FROZEN and never reordered. This is the whole point
  (don't disturb a curated MOMW list), not a shortcoming.
- [Requires]/[Conflict]/[Note] are read-only: they print warnings, never change
  the order or block anything. Treat warnings as a prompt to go check, not gospel.
- [NearStart]/[NearEnd] become ordering chains among the listed plugins, not a
  hard push to the absolute start/end of the file.
- data= (folder path) insertions are placed by their after/before anchor (mlox
  has no concept of data-path order). An anchor must point at an EXISTING data=
  line; if not found, the path is appended at the end with a warning.

See README.md for the full feature tour (plugin-order.yml integration, the
mods-folder scanner, and the GUI).
"""

# PEP 563: annotations are strings, so a hint may name a type that is
# only imported for type checking, and no annotation costs import time.
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import os
import re
import struct
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

# ---------------------------------------------------------------------------
# mlox-exact plugin filename matching (ported from mlox's
# ruleParser._filename_to_regex) so this tool's Order/predicate pattern
# matching behaves like the real engine. mlox filenames may contain three
# special tokens:
#     *      -> any run of characters
#     ?      -> any single character
#     <VER>  -> a version number (e.g. matches the "1.2" in "Foo 1.2.esp")
# and a handful of regex metacharacters that can legally appear in real plugin
# names are escaped exactly the way mlox escapes them (only ()+. ). Previously
# this tool used fnmatch, which has no concept of <VER> -- so the ~578 rule
# lines in mlox_base.txt/mlox_user.txt that use <VER> silently failed to match
# any installed plugin, and every ordering edge those rules would have created
# was lost. This restores them.
# ---------------------------------------------------------------------------
# Pattern translation now lives in wraithguard/rules/patterns.py; behaviour is
# pinned by tests/test_differential.py. Only the names this module actually
# calls are imported -- callers import from wraithguard/ themselves (§23).
from wraithguard import _, get_logger, ngettext, setup_logging
from wraithguard.esp import EspError, Header, read_header
from wraithguard.nif.analysis import MeshAnalyser, MeshFinding
from wraithguard.nif.report import Structure, compare as compare_structures
from wraithguard.rules import authoring, pattern_has_meta

#: Diagnostics about the run (not the user's report) go through here. The
#: report itself stays on print()/stdout -- see wraithguard/logging_setup.py.
_LOG = get_logger(__name__)

# ---------------------------------------------------------------------------
# Imports from wraithguard/.
#
# The split moved the implementation into packages. This module is the engine
# and CLI -- one caller of those packages among several, not a facade in front
# of them. Until 3.0 it also re-exported 36 names it never called, purely so
# `core.<name>` resolved for the GUI and the tests; those callers now import
# from wraithguard/ directly, and the re-exports are gone (CODE_REVIEW.md §23).
#
# So every import below is one this module *uses*. F401 is enforced here again
# rather than suppressed, which is what makes that claim checkable: an unused
# import in this file is now a lint failure, not the house style.
# ---------------------------------------------------------------------------
from wraithguard.tracing import (
    set_trace_file,
    sort_trace_begin,
    trace,
    trace_sort,
)

if TYPE_CHECKING:
    from collections.abc import (
        Callable,
        Collection,
        Iterable,
        Iterator,
        Mapping,
        MutableMapping,
        Sequence,
        Set as AbstractSet,
    )
    from contextlib import AbstractContextManager

# ---------------------------------------------------------------------------
# mlox [VER]/[SIZE]/[DESC] predicate functions (ported from mlox's ruleParser).
# These let [Requires]/[Conflict]/[Note] rules test a plugin's version, file
# size, or header description -- e.g. "[VER < 2.0 SomeMod.esp]". mlox reads the
# actual plugin file for this; we locate it across the cfg's data= directories.
# When the files aren't reachable (e.g. running on a different machine than the
# mods live on), we fall back to mlox's own conservative "no datadir" behaviour
# rather than guessing, so we never invent a warning we can't substantiate.
# ---------------------------------------------------------------------------

# atomic function forms, matched against a single token produced by the tokenizer


#: Defensive cap on how much of a plugin to read for its header: the TES3
#: header record is tiny, so a larger declared size means a corrupt file, and
#: reading a gigabyte to look for masters helps nobody.
_HEADER_READ_CAP = 1 << 20


def _read_header_record(path: str | Path) -> Header | None:
    """Read a plugin's ``TES3`` header via the in-process ESP reader, or ``None``.

    Reads only the first record -- its declared size then that many bytes -- so a
    huge plugin is not slurped to find its masters, and (because nothing past the
    header is parsed) a file whose later records ``tes3conv`` would refuse still
    yields its header. Never raises: any read or parse problem returns ``None``,
    and the byte-level callers fall back to their own tolerant scan.

    Returns:
        The parsed :class:`~wraithguard.esp.records.Header`, or ``None`` for a
        non-TES3 file, a truncated header, or any decode/parse failure.
    """
    try:
        with Path(path).open("rb") as fh:
            prefix = fh.read(16)  # tag + size + padding + flags
            if len(prefix) < 16 or prefix[:4] != b"TES3":
                return None
            (data_size,) = struct.unpack_from("<I", prefix, 4)
            body = fh.read(min(data_size, _HEADER_READ_CAP))
    except (OSError, struct.error):
        return None
    try:
        return read_header(prefix + body)
    except EspError:
        return None


def read_plugin_masters(path: str | Path) -> list[str]:
    """Return the master files a plugin depends on, from its TES3 header.

    These are the ground-truth load-order dependencies: a plugin must load AFTER every
    master it lists. Returns [] for non-TES3 files (.omwscripts) or any read problem.
    Works for .esm/.esp/.omwaddon/.omwgame.

    Reads the header with the in-process ESP reader (correct cp1252 decoding, the
    format's own MAST/DATA framing); a header the strict reader cannot parse falls
    back to the tolerant byte scan so nothing regresses on an odd file.
    """
    header = _read_header_record(path)
    if header is not None:
        return [name for name, _size in header.masters if name.strip()]
    return _read_plugin_masters_scan(path)


def read_plugin_masters_with_sizes(path: str | Path) -> list[tuple[str, int | None]]:
    """Return [(master_name, recorded_size)] pairs from the TES3 header.

    Like :func:`read_plugin_masters`, but paired with sizes --
    each MAST subrecord is (per the TES3 format) immediately followed by a DATA
    subrecord holding the master's file size (8 bytes) at the time the plugin
    was saved. tes3cmd uses the same pairing for its master-sync check.
    recorded_size is None when the DATA subrecord is absent/malformed.

    Read via the in-process ESP header, which pairs each MAST with its DATA; a
    header too malformed for the strict reader (a MAST with no DATA, say) falls
    back to the byte scan, which tolerates it and reports that size as ``None``.
    """
    header = _read_header_record(path)
    if header is not None:
        return [(name, size) for name, size in header.masters if name.strip()]
    return _read_plugin_masters_with_sizes_scan(path)


def _read_plugin_masters_scan(path: str | Path) -> list[str]:
    """Byte-level MAST scan -- the fallback when the ESP header will not parse."""
    try:
        with Path(path).open("rb") as fh:
            if fh.read(4) != b"TES3":
                return []
            data_size = struct.unpack("<I", fh.read(4))[0]
            fh.read(8)  # header1 + flags
            data = fh.read(min(data_size, _HEADER_READ_CAP))  # header is tiny; cap defensively
    except (OSError, struct.error):
        return []
    masters, i = [], 0
    while i + 8 <= len(data):
        tag = data[i : i + 4]
        sz = struct.unpack_from("<I", data, i + 4)[0]
        i += 8
        chunk = data[i : i + sz]
        i += sz
        if tag == b"MAST":
            nm = chunk.split(b"\x00", 1)[0].decode("cp1252", "replace").strip()
            if nm:
                masters.append(nm)
    return masters


def _read_plugin_masters_with_sizes_scan(path: str | Path) -> list[tuple[str, int | None]]:
    """Byte-level MAST/DATA scan -- the tolerant fallback for a malformed header."""
    try:
        with Path(path).open("rb") as fh:
            if fh.read(4) != b"TES3":
                return []
            data_size = struct.unpack("<I", fh.read(4))[0]
            fh.read(8)  # header1 + flags
            data = fh.read(min(data_size, _HEADER_READ_CAP))
    except (OSError, struct.error):
        return []
    out, i = [], 0
    pending = None  # last MAST name waiting for its DATA size
    while i + 8 <= len(data):
        tag = data[i : i + 4]
        sz = struct.unpack_from("<I", data, i + 4)[0]
        i += 8
        chunk = data[i : i + sz]
        i += sz
        if tag == b"MAST":
            if pending is not None:
                out.append((pending, None))
            nm = chunk.split(b"\x00", 1)[0].decode("cp1252", "replace").strip()
            pending = nm or None
        elif tag == b"DATA" and pending is not None:
            size = struct.unpack_from("<Q", chunk, 0)[0] if len(chunk) >= 8 else None
            out.append((pending, size))
            pending = None
    if pending is not None:
        out.append((pending, None))
    return out


def sync_plugin_master_sizes(
    path: str | Path, index: PluginFileIndex, make_backup: bool = True
) -> tuple[list[tuple[str, int | None, int]], list[str], str | None]:
    """Fix the master sizes recorded in a plugin's TES3 header.

    A VFS-aware replacement for ``tes3cmd header --synchronize``:

    fix the master sizes recorded in a plugin's TES3 header (the DATA subrecord after
    each MAST) to match the installed masters.

        Why not tes3cmd: it assumes a single flat 'Data Files' directory. In an
        OpenMW multi-folder layout the masters usually aren't next to the plugin,
        so tes3cmd resolves them to nothing and writes EMPTY sizes into the
        header (observed in the wild -- 'Synchronized ... length: 79837557 --> ').
        This resolves each master across ALL data folders via the index and
        rewrites only the 8-byte size fields; nothing else in the file changes.

        Returns (updated, unresolved, error):
          updated    -- [(master, old_size, new_size)] fields actually rewritten
          unresolved -- masters whose file couldn't be found (left untouched)
          error      -- message if the file isn't a TES3 plugin / can't be read
        A one-time '<name>.masterfix.bak' copy is made before the first write.
    """
    p = Path(path)
    try:
        raw = bytearray(p.read_bytes())
    except OSError as e:
        return [], [], f"can't read: {e}"
    # A TES3 record header is 16 bytes (tag + size + header1 + flags). A file
    # that starts with the magic but is shorter than that is truncated or
    # corrupt -- reject it rather than unpacking past the buffer.
    if len(raw) < 16 or raw[:4] != b"TES3":
        return [], [], "not a TES3 plugin (no TES3 header)"
    (data_size,) = struct.unpack_from("<I", raw, 4)
    end = min(16 + data_size, len(raw))
    i, updated, unresolved = 16, [], []
    pending = None  # master name waiting for its DATA size field
    while i + 8 <= end:
        tag = bytes(raw[i : i + 4])
        (sz,) = struct.unpack_from("<I", raw, i + 4)
        off = i + 8
        if off + sz > end:
            break
        if tag == b"MAST":
            pending = raw[off : off + sz].split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
        elif tag == b"DATA" and pending:
            if sz >= 8:
                mpath = index.find(pending) if index else None
                if mpath is None:
                    unresolved.append(pending)
                else:
                    try:
                        actual = mpath.stat().st_size
                    except OSError:
                        actual = None
                    (old,) = struct.unpack_from("<Q", raw, off)
                    if actual is not None and old != actual:
                        struct.pack_into("<Q", raw, off, actual)
                        updated.append((pending, old, actual))
            pending = None
        i = off + sz
    if updated:
        if make_backup:
            import shutil as _sh

            bak = p.with_name(p.name + ".masterfix.bak")
            if not bak.exists():
                try:
                    _sh.copy2(p, bak)
                except OSError as e:
                    return [], unresolved, f"couldn't write backup ({e}) -- plugin NOT modified"
        try:
            p.write_bytes(bytes(raw))
        except OSError as e:
            return [], unresolved, f"couldn't write plugin: {e}"
    return updated, unresolved, None


def check_missing_masters(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_origins: Mapping[str, str] | None = None,
) -> tuple[list[str], list[str], list[str], int, set[str]]:
    """Verify every active plugin's TES3 header masters against the load order.

    Returns (missing, order_problems, size_notes, checked):
      missing        -- '[MISSING MASTER]' warnings: a required master is not in
                        the load order. Distinguishes 'installed but not
                        enabled' from 'not found in any data folder' (the
                        latter fails hard at game launch).
      order_problems -- '[MASTER ORDER]' warnings: the master is active but
                        loads AFTER its dependent.
      size_notes     -- '[MASTER SIZE]' notes (tes3cmd-style sync check): the
                        installed master's size differs from the size recorded
                        in the plugin's header -- the plugin was made against a
                        different version of that master. Usually benign.
      checked        -- how many plugin files were actually readable/checked
                        (0 means the mod files aren't reachable; nothing to say).
      problem_names  -- the plugin names behind `missing`/`order_problems`,
                        for UI highlighting.
    """
    origins = subset_origins or {}
    pos = {p.lower(): i for i, p in enumerate(active_order)}
    missing, order_problems, size_notes = [], [], []
    problem_names = set()
    checked = 0
    for p in active_order:
        path = index.find(p) if index else None
        if path is None:
            continue
        pairs = read_plugin_masters_with_sizes(path)
        if not pairs and not str(p).lower().endswith((".esp", ".esm", ".omwaddon", ".omwgame")):
            continue
        checked += 1
        origin = origins.get(p.lower())
        tag = f" [{origin}]" if origin else ""
        for m, rec_size in pairs:
            ml = m.lower()
            if ml not in pos:
                mpath = index.find(m) if index else None
                if mpath is None:
                    missing.append(
                        f"[MISSING MASTER] '{p}'{tag} requires '{m}' -- NOT FOUND in any data "
                        f"folder. The game will fail to load with this plugin enabled."
                    )
                else:
                    missing.append(
                        f"[MISSING MASTER] '{p}'{tag} requires '{m}' -- installed but not in "
                        f"the load order. Enable/add it (it must load before '{p}')."
                    )
                problem_names.add(p)
                continue
            if pos[ml] > pos[p.lower()]:
                order_problems.append(
                    f"[MASTER ORDER] '{p}'{tag} loads BEFORE its master '{m}' -- "
                    f"'{m}' must come first."
                )
                problem_names.add(p)
            if rec_size is not None:  # 0 counts: a failed tes3cmd sync zeroes these
                mpath = index.find(m) if index else None
                if mpath is not None:
                    try:
                        actual = mpath.stat().st_size
                    except OSError:
                        actual = None
                    if actual is not None and actual != rec_size:
                        hint = (
                            "header records 0 bytes -- likely damaged by a tes3cmd "
                            "sync that couldn't find the master"
                            if rec_size == 0
                            else "made against a different version of the master (usually fine)"
                        )
                        size_notes.append(
                            f"[MASTER SIZE] '{p}'{tag}: header says '{m}' was {rec_size} bytes, "
                            f"installed copy is {actual} -- {hint}. The tes3cmd window's "
                            f"in-app resync fixes this."
                        )
    return missing, order_problems, size_notes, checked, problem_names


# ---------------------------------------------------------------------------
# TES3 record-level conflict detection (a la TES3View / tes3cmd / TESPCD).
# Parses each active plugin's records and reports where two or more plugins
# define/override the SAME record -- the last one in the load order wins. This
# is read-only and diagnostic: it never changes the sort or the cfg. Depth is
# record-level (record type + editor id), which catches the vast majority of
# real conflicts; it does not diff individual fields (that's xEdit territory).
#
# TES3 binary layout (little-endian):
#   record  = type[4] + dataSize[4] + header1[4] + flags[4] + data[dataSize]
#   subrec  = tag[4]  + dataSize[4] + data[dataSize]
# The editor id is the NAME subrecord for most record types; CELL is special
# (interior = its NAME, exterior = its DATA grid coords). A DELE subrecord marks
# a deletion. Strings are cp1252, null-terminated.
# ---------------------------------------------------------------------------

# record types with no meaningful "editor id" to compare on -- skipped
_TES3_SKIP_TYPES = frozenset({"TES3"})


def _tes3_decode(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("cp1252", "replace").strip()


def _tes3_record_key(rectype: str, blob: bytes) -> tuple[str | None, bool]:
    """Return (record_id, deleted) for one record's subrecord blob.

    Yields:
    ------
    (None, deleted) if it has no id worth comparing on.

    """
    name = None
    cell_data = None
    schd = None  # SCPT script header (name in first 32 bytes)
    intv = None  # LAND grid coords
    deleted = False
    i, n = 0, len(blob)
    while i + 8 <= n:
        tag = blob[i : i + 4]
        sz = struct.unpack_from("<I", blob, i + 4)[0]
        data = blob[i + 8 : i + 8 + sz]
        i += 8 + sz
        if tag == b"NAME" and name is None:
            name = data
        elif tag == b"INAM" and rectype == "INFO" and name is None:
            name = data  # dialogue response id
        elif tag == b"DELE":
            deleted = True
        elif tag == b"DATA" and rectype == "CELL" and cell_data is None:
            cell_data = data
        elif tag == b"SCHD" and rectype == "SCPT" and schd is None:
            schd = data  # script: name is the first 32 bytes
        elif tag == b"INTV" and rectype == "LAND" and intv is None:
            intv = data  # landscape: keyed by exterior grid coords
    if rectype == "CELL":
        cname = _tes3_decode(name) if name else ""
        if cell_data is not None and len(cell_data) >= 12:
            flags, gx, gy = struct.unpack_from("<iii", cell_data, 0)
            if flags & 0x01:  # interior
                return (f"Interior: {cname}", deleted)
            return (f"Exterior ({gx}, {gy})" + (f" [{cname}]" if cname else ""), deleted)
        return (f"Interior: {cname}", deleted)
    if rectype == "SCPT" and schd is not None:
        return (_tes3_decode(schd[:32]) or None, deleted)
    if rectype == "LAND" and intv is not None and len(intv) >= 8:
        gx, gy = struct.unpack_from("<ii", intv, 0)
        return (f"Land ({gx}, {gy})", deleted)
    if name is not None:
        rid = _tes3_decode(name)
        return (rid or None, deleted)
    return (None, deleted)


def parse_tes3_records(path: str | Path) -> Iterator[tuple[str, str, bool]]:
    """Yield (record_type, record_id, deleted) for each game record.

    Reads a TES3
    plugin (.esp/.esm/.omwaddon). Best-effort and fully guarded: a truncated or
    non-TES3 file just yields nothing rather than raising.
    """
    try:
        fh = Path(path).open("rb")  # closed by the caller, not a context manager
    except (OSError, PermissionError):
        return
    with fh:
        head = fh.read(16)
        if len(head) < 16 or head[:4] != b"TES3":
            return
        fh.seek(struct.unpack_from("<I", head, 4)[0], 1)  # skip TES3 header record
        while True:
            rh = fh.read(16)
            if len(rh) < 16:
                break
            rectype = rh[:4].decode("ascii", "replace")
            size = struct.unpack_from("<I", rh, 4)[0]
            blob = fh.read(size)
            if len(blob) < size:
                break
            if rectype in _TES3_SKIP_TYPES:
                continue
            if rectype == "LUAL":
                # OpenMW LuaScriptsCfg record (in .omwaddon/.omwgame): one entry
                # per LUAS subrecord (a Lua script path). Surface each as a
                # LuaScript record so it lines up with .omwscripts declarations.
                for sp in _lual_script_paths(blob):
                    yield "LuaScript", sp, False
                continue
            rid, deleted = _tes3_record_key(rectype, blob)
            if rid is not None:
                yield rectype, rid, deleted


def _lual_script_paths(blob: bytes) -> Iterator[str]:
    """Yield the normalized Lua script path from each LUAS subrecord.

    Reads the LUAL
    (LuaScriptsCfg) record.
    """
    i, n = 0, len(blob)
    while i + 8 <= n:
        tag = blob[i : i + 4]
        sz = struct.unpack_from("<I", blob, i + 4)[0]
        data = blob[i + 8 : i + 8 + sz]
        i += 8 + sz
        if tag == b"LUAS":
            p = data.split(b"\x00", 1)[0].decode("cp1252", "replace").strip()
            if p:
                yield p.replace("\\", "/").lower().lstrip("/")


def parse_omwscripts(path: str | Path) -> Iterator[tuple[str, str, bool]]:
    """Yield ('LuaScript', normalized_path, False) for each declaration.

    Reads an
    OpenMW .omwscripts file. Text format (per OpenMW's parseOMWScripts): one
    'TAGS: script/path.lua' per line, '#' comments and blank lines skipped.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pos = line.find(":")
        if pos == -1:
            continue
        spath = line[pos + 1 :].strip().strip('"').strip("'")
        if not spath.lower().endswith(".lua"):
            continue
        yield "LuaScript", spath.replace("\\", "/").lower().lstrip("/"), False


def parse_plugin_records(path: str | Path) -> Iterator[tuple[str, str, bool]]:
    """Dispatch to the right record reader for this file's extension.

    Namely:

    .omwscripts is OpenMW's text Lua-attach config; everything else
    (.esp/.esm/.omwaddon/.omwgame) is the TES3 binary format.
    """
    if str(path).lower().endswith(".omwscripts"):
        yield from parse_omwscripts(path)
    else:
        yield from parse_tes3_records(path)


# --- optional tes3conv backend (enables field-level diffing) ----------------
# tes3conv (the tes3 ecosystem's plugin<->JSON converter, also used by TES3
# Conflictsolver) gives clean JSON for every record type. With it we can key
# records exactly and, crucially, DIFF individual fields between conflicting
# plugins. Without it, the built-in binary parser still does record-level
# detection -- just no field-level breakdown.


def find_tes3conv(
    explicit: str | None = None, extra_dirs: Sequence[str | None] | None = None
) -> str | None:
    """Locate a tes3conv executable.

    Order: explicit path, $MLOX_TES3CONV, PATH, then alongside this script / any extra
    dirs given. Returns a path or None.
    """
    import shutil

    names = ["tes3conv", "tes3conv.exe"]
    cands = []
    if explicit:
        cands.append(str(explicit))
    env = os.environ.get("MLOX_TES3CONV")
    if env:
        cands.append(env)
    for nm in names:
        found = shutil.which(nm)
        if found:
            cands.append(found)
    search_dirs = [Path(__file__).resolve().parent]
    search_dirs.extend(Path(d) for d in (extra_dirs or []) if d)
    cands.extend(str(d / nm) for d in search_dirs for nm in names)
    for c in cands:
        try:
            if c and Path(c).is_file():
                return c
        except OSError:  # noqa: PERF203
            # Per-candidate isolation is the point: one unreadable path (a
            # dead network share, a permission-denied dir) must not stop the
            # search for the others.
            continue
    return None


def find_tes3cmd(
    explicit: str | None = None, extra_dirs: Sequence[str | None] | None = None
) -> str | None:
    """Locate tes3cmd.

    Prefers the compiled executable (tes3cmd.exe -- what the MOMW Tools Pack distributes
    and what end users will normally have); the pure-perl 'tes3cmd' script is also
    accepted (it then needs a perl on PATH; see tes3cmd_invocation). Order: explicit
    path, $MLOX_TES3CMD, PATH, then alongside this script / any extra dirs given.
    Returns a path or None.
    """
    import shutil

    names = ["tes3cmd.exe", "tes3cmd.bat", "tes3cmd"]  # compiled build first
    cands = []
    if explicit:
        cands.append(str(explicit))
    env = os.environ.get("MLOX_TES3CMD")
    if env:
        cands.append(env)
    for nm in names:
        found = shutil.which(nm)
        if found:
            cands.append(found)
    search_dirs = [Path(__file__).resolve().parent]
    search_dirs.extend(Path(d) for d in (extra_dirs or []) if d)
    cands.extend(str(d / nm) for d in search_dirs for nm in names)
    for c in cands:
        try:
            if c and Path(c).is_file():
                return c
        except OSError:  # noqa: PERF203
            # Per-candidate isolation is the point: one unreadable path (a
            # dead network share, a permission-denied dir) must not stop the
            # search for the others.
            continue
    return None


def tes3cmd_invocation(path: str | Path) -> tuple[list[str] | None, str | None]:
    """Argv prefix to run the given tes3cmd, or (None, why-not).

    The compiled tes3cmd.exe (MOMW Tools Pack) runs directly. If the path is
    the pure-perl script instead, it's run through a perl interpreter from
    PATH -- with a clear error if there isn't one, since end users normally
    have the compiled build and shouldn't need perl.
    """
    import shutil

    p = Path(path)
    if p.suffix.lower() in (".exe", ".bat", ".cmd"):
        return [str(p)], None
    try:
        # Context-managed: the handle was previously left to the garbage
        # collector, which CPython happens to close promptly and other
        # interpreters do not.
        with p.open("rb") as handle:
            head = handle.read(256)
    except OSError as e:
        return None, f"can't read '{p}': {e}"
    if head.startswith(b"#!") or b"perl" in head.lower():
        perl = shutil.which("perl")
        if not perl:
            return None, (
                f"'{p.name}' is the pure-perl tes3cmd but no perl interpreter was found "
                f"on PATH. Point this at the compiled tes3cmd.exe from the MOMW Tools "
                f"Pack instead (or install perl)."
            )
        return [perl, str(p)], None
    return [str(p)], None


def stage_for_tes3cmd(
    staging_root: str | Path,
    plugin_path: str | Path,
    index: PluginFileIndex | None,
    quiet: bool = False,
) -> tuple[Path | None, list[str]]:
    """Build or refresh a minimal vanilla-Morrowind layout for tes3cmd.

    Lets tes3cmd work on
    ONE plugin from an OpenMW multi-folder setup.

    tes3cmd walks up from its cwd until it finds a directory holding BOTH a
    'Morrowind.ini' and a 'Data Files' folder; masters are then resolved
    inside that single Data Files dir. OpenMW's VFS spreads mods over many
    folders, so run against a mod folder directly tes3cmd can't see the
    masters -- clean silently degrades and header --synchronize corrupts.

    This creates:  <staging_root>/Morrowind.ini      minimal [Game Files]
                   <staging_root>/Data Files/        masters + the plugin
    Masters are HARDLINKED when possible (same volume; read-only use, and a
    hardlink shares the original's timestamp) with copy fallback, and reused
    across runs when size+mtime still match -- so the 100MB+ masters aren't
    recopied per plugin. The target plugin is always a fresh private COPY
    (tes3cmd rewrites it; the original is never touched here).

    Returns (staged_plugin_path, missing_masters). A non-empty
    missing_masters means the caller should SKIP cleaning this plugin --
    tes3cmd compares records against the masters, and cleaning without them
    gives wrong results (the classic batch files refuse too).
    """
    import shutil as _sh

    root = Path(staging_root)
    df = root / "Data Files"
    df.mkdir(parents=True, exist_ok=True)
    p = Path(plugin_path)
    masters = read_plugin_masters(p)
    staged_names, missing = [], []

    def _ensure(src: Path) -> Path | None:
        dest = df / src.name
        try:
            if dest.exists():
                s, d = src.stat(), dest.stat()
                if d.st_size == s.st_size and int(d.st_mtime) == int(s.st_mtime):
                    return dest  # cached from a previous run
                dest.unlink()
            try:
                os.link(src, dest)  # same-volume: instant, no disk cost
                return dest
            except OSError:
                pass  # cross-volume etc. -> copy
            _sh.copy2(src, dest)
            return dest
        except OSError as e:
            if not quiet:
                _LOG.warning(
                    _("couldn't stage '%(name)s': %(error)s"),
                    {"name": src.name, "error": e},
                )
            return None

    for m in masters:
        src = index.find(m) if index else None
        if src is None:
            missing.append(m)
            continue
        if _ensure(Path(src)) is not None:
            staged_names.append(Path(src).name)
        else:
            missing.append(m)

    staged_plugin = df / p.name
    if staged_plugin.exists():
        staged_plugin.unlink()
    _sh.copy2(p, staged_plugin)  # always a private copy
    staged_names.append(p.name)

    ini = root / "Morrowind.ini"
    ini.write_text(
        "[Game Files]\n" + "".join(f"GameFile{i}={n}\n" for i, n in enumerate(staged_names)),
        encoding="latin-1",
        errors="replace",
    )
    return staged_plugin, missing


# ---------------------------------------------------------------------------
# native lint checks -- ports of the useful tes3lint (mlox project) and
# missing_pathgrids.pl diagnostics, working directly on the plugin binaries
# so they see the full OpenMW multi-folder VFS (no perl, no tes3cmd needed).
# ---------------------------------------------------------------------------

# The "72 evil GMSTs": settings copied into a plugin by an old Construction
# Set whenever it was used without both expansions loaded. They are only
# legitimate inside Tribunal.esm/Bloodmoon.esm; in any other plugin they
# override expansion behaviour with stale defaults. A GMST is only flagged
# when BOTH the name and the recorded value match tes3lint's table (a mod
# deliberately changing one of these settings to a new value is fine).
#
# The 72 name/value pairs below are reproduced from tes3lint:
#
#   tes3lint - a diagnostic tool for TES3/Morrowind plugins
#   Copyright 2009 by John Moonsugar
#   Distributed as part of the mlox project under the MIT License.
#
# Retained here to satisfy the MIT notice requirement: this table is research
# (which values the buggy CS actually wrote), not something we rediscovered.
_EVIL_GMSTS = {
    "fcombatdistancewerewolfmod": ("FLTV", b"\x9a\x99\x99>"),
    "ffleedistance": ("FLTV", b"\x00\x80;E"),
    "fwerewolfacrobatics": ("FLTV", b"\x00\x00\x16C"),
    "fwerewolfagility": ("FLTV", b"\x00\x00\x16C"),
    "fwerewolfalchemy": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfalteration": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfarmorer": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfathletics": ("FLTV", b"\x00\x00\x16C"),
    "fwerewolfaxe": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfblock": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfbluntweapon": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfconjuration": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfdestruction": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfenchant": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfendurance": ("FLTV", b"\x00\x00\x16C"),
    "fwerewolffatigue": ("FLTV", b"\x00\x00\xc8C"),
    "fwerewolfhandtohand": ("FLTV", b"\x00\x00\xc8B"),
    "fwerewolfhealth": ("FLTV", b"\x00\x00\x00@"),
    "fwerewolfheavyarmor": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfillusion": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfintellegence": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolflightarmor": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolflongblade": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfluck": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfmagicka": ("FLTV", b"\x00\x00\xc8B"),
    "fwerewolfmarksman": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfmediumarmor": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfmerchantile": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfmysticism": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfpersonality": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfrestoration": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfrunmult": ("FLTV", b"\x00\x00\xc0?"),
    "fwerewolfsecurity": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfshortblade": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfsilverweapondamagemult": ("FLTV", b"\x00\x00\xc0?"),
    "fwerewolfsneak": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfspear": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfspeechcraft": ("FLTV", b"\x00\x00\x80?"),
    "fwerewolfspeed": ("FLTV", b"\x00\x00\x16C"),
    "fwerewolfstrength": ("FLTV", b"\x00\x00\x16C"),
    "fwerewolfunarmored": ("FLTV", b"\x00\x00\xc8B"),
    "fwerewolfwillpower": ("FLTV", b"\x00\x00\x80?"),
    "iwerewolfbounty": ("INTV", b"\x10'\x00\x00"),
    "iwerewolffightmod": ("INTV", b"d\x00\x00\x00"),
    "iwerewolffleemod": ("INTV", b"d\x00\x00\x00"),
    "iwerewolfleveltoattack": ("INTV", b"\x14\x00\x00\x00"),
    "scompanionshare": ("STRV", b"Companion Share"),
    "scompanionwarningbuttonone": ("STRV", b"Let the mercenary quit."),
    "scompanionwarningbuttontwo": ("STRV", b"Return to Companion Share display."),
    "scompanionwarningmessage": (
        "STRV",
        b"Your mercenary is poorer now than when he contracted with you.  Your mercenary will quit if you do not give him gold or goods to bring his Profit Value to a positive value.",
    ),
    "sdeletenote": ("STRV", b"Delete Note?"),
    "seditnote": ("STRV", b"Edit Note"),
    "seffectsummoncreature01": ("STRV", b"sEffectSummonCreature01"),
    "seffectsummoncreature02": ("STRV", b"sEffectSummonCreature02"),
    "seffectsummoncreature03": ("STRV", b"sEffectSummonCreature03"),
    "seffectsummoncreature04": ("STRV", b"sEffectSummonCreature04"),
    "seffectsummoncreature05": ("STRV", b"sEffectSummonCreature05"),
    "seffectsummonfabricant": ("STRV", b"sEffectSummonFabricant"),
    "slevitatedisabled": ("STRV", b"Levitation magic does not work here."),
    "smagiccreature01id": ("STRV", b"sMagicCreature01ID"),
    "smagiccreature02id": ("STRV", b"sMagicCreature02ID"),
    "smagiccreature03id": ("STRV", b"sMagicCreature03ID"),
    "smagiccreature04id": ("STRV", b"sMagicCreature04ID"),
    "smagiccreature05id": ("STRV", b"sMagicCreature05ID"),
    "smagicfabricantid": ("STRV", b"Fabricant"),
    "smaxsale": ("STRV", b"Max Sale"),
    "sprofitvalue": ("STRV", b"Profit Value"),
    "steleportdisabled": ("STRV", b"Teleportation magic does not work here."),
    "swerewolfalarmmessage": ("STRV", b"You have been detected changing from a werewolf state."),
    "swerewolfpopup": ("STRV", b"Werewolf"),
    "swerewolfrefusal": ("STRV", b"You cannot do this as a werewolf."),
    "swerewolfrestmessage": ("STRV", b"You cannot rest in werewolf form."),
}

# Script functions introduced by the expansions (from tes3lint's DATA lists).
# A plugin calling one without listing the expansion as a master is fragile
# on non-expansion setups and usually indicates a truncated master list.
_TRIBUNAL_FUNCS = (
    "AddToLevCreature",
    "AddToLevItem",
    "ClearForceJump",
    "ClearForceMoveJump",
    "ClearForceRun",
    "DisableLevitation",
    "EnableLevitation",
    "ExplodeSpell",
    "ForceJump",
    "ForceMoveJump",
    "ForceRun",
    "GetCollidingActor",
    "GetCollidingPC",
    "GetForceJump",
    "GetForceMoveJump",
    "GetForceRun",
    "GetPCJumping",
    "GetPCRunning",
    "GetPCSneaking",
    "GetScale",
    "GetSpellReadied",
    "GetSquareRoot",
    "GetWaterLevel",
    "GetWeaponDrawn",
    "GetWeaponType",
    "HasItemEquipped",
    "ModScale",
    "ModWaterLevel",
    "PlaceItem",
    "PlaceItemCell",
    "RemoveFromLevCreature",
    "RemoveFromLevItem",
    "SetDelete",
    "SetScale",
    "SetWaterLevel",
)
_BLOODMOON_FUNCS = (
    "BecomeWerewolf",
    "GetPCInJail",
    "GetPCTraveling",
    "GetWerewolfKills",
    "IsWerewolf",
    "PlaceAtMe",
    "SetWerewolfAcrobatics",
    "TurnMoonRed",
    "TurnMoonWhite",
    "UndoWerewolf",
)
# mirror tes3lint's per-line matching: ignore comment text after ';'
_RE_TB_FUN = re.compile(
    r"^[^;\n]*?\b(" + "|".join(_TRIBUNAL_FUNCS) + r")\b", re.IGNORECASE | re.MULTILINE
)
_RE_BM_FUN = re.compile(
    r"^[^;\n]*?\b(" + "|".join(_BLOODMOON_FUNCS) + r")\b", re.IGNORECASE | re.MULTILINE
)

_LINT_SKIP = {
    "morrowind.esm",
    "tribunal.esm",
    "bloodmoon.esm",
    "merged objects.esp",
    "merged lands.esp",
    "mashed lists.esp",
    "multipatch.esp",
}
_LINT_SKIP_CELLS = {"ashlands region (0, 0)"}  # the classic 0,0 exterior exception


def _iter_tes3_records(raw: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield (tag, body) for each top-level record.

    Bodies of record types the caller doesn't care about are skipped over cheaply by
    size.
    """
    n, i = len(raw), 0
    while i + 16 <= n:
        tag = bytes(raw[i : i + 4])
        (sz,) = struct.unpack_from("<I", raw, i + 4)
        yield tag, raw[i + 16 : i + 16 + sz]
        i += 16 + sz


def _iter_subrecords(body: bytes) -> Iterator[tuple[bytes, bytes]]:
    n, i = len(body), 0
    while i + 8 <= n:
        tag = bytes(body[i : i + 4])
        (sz,) = struct.unpack_from("<I", body, i + 4)
        yield tag, body[i + 8 : i + 8 + sz]
        i += 8 + sz


def _lint_zstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()


class _CellFacts(NamedTuple):
    """What one ``CELL`` record contributes to the lint.

    Attributes:
        name: The cell's display name, as written.
        cell_id: Its lower-cased name, or ``""`` when it is unnamed or on the
            skip list -- meaning it is not a candidate for the pathgrid check.
        fog_bug: Whether it trips the black-void bug: interior, not
            behave-like-exterior, fog density exactly zero.
    """

    name: str
    cell_id: str
    fog_bug: bool


def _lint_expansion_calls(body: bytes, tag: bytes) -> tuple[set[str], set[str]]:
    """Find Tribunal and Bloodmoon function calls in one script or dialogue result.

    Args:
        body: The record body.
        tag: Its record tag. ``SCPT`` carries source text in ``SCTX``, ``INFO``
            in ``BNAM``.

    Returns:
        The Tribunal and Bloodmoon function names found.
    """
    want = b"SCTX" if tag == b"SCPT" else b"BNAM"
    tribunal: set[str] = set()
    bloodmoon: set[str] = set()
    for subtag, data in _iter_subrecords(body):
        if subtag == want and data:
            text = data.decode("latin-1", "replace")
            tribunal.update(match.group(1) for match in _RE_TB_FUN.finditer(text))
            bloodmoon.update(match.group(1) for match in _RE_BM_FUN.finditer(text))
    return tribunal, bloodmoon


def _lint_masters(body: bytes) -> set[str]:
    """Read a plugin header's declared masters.

    Args:
        body: The ``TES3`` record body.

    Returns:
        Lower-cased master filenames.
    """
    return {
        _lint_zstr(data).lower() for subtag, data in _iter_subrecords(body) if subtag == b"MAST"
    }


def _lint_header_gaps(body: bytes) -> list[str]:
    """Report which of author and description a header leaves blank.

    Args:
        body: The ``TES3`` record body.

    Returns:
        The missing field names in report order. Only the first ``HEDR`` is
        considered, since the format allows exactly one.
    """
    for subtag, data in _iter_subrecords(body):
        if subtag == b"HEDR" and len(data) >= 296:
            fields = (("author", _lint_zstr(data[8:40])), ("description", _lint_zstr(data[40:296])))
            return [word for word, value in fields if not value]
    return []


def _lint_evil_gmst(body: bytes) -> str | None:
    """Identify an evil GMST: one whose name *and* value match the known table.

    Both must match. A plugin that deliberately changes a game setting is doing
    its job; what this catches is a stale expansion default copied in wholesale
    by an old Construction Set.

    Args:
        body: The ``GMST`` record body.

    Returns:
        The setting's lower-cased name, or ``None``.
    """
    name: str | None = None
    value_tag: str | None = None
    value = b""
    for subtag, data in _iter_subrecords(body):
        if subtag == b"NAME":
            name = _lint_zstr(data).lower()
        elif subtag in (b"STRV", b"INTV", b"FLTV"):
            value_tag, value = subtag.decode(), data
    known = _EVIL_GMSTS.get(name) if name else None
    if known and value_tag == known[0] and value.rstrip(b"\x00") == known[1].rstrip(b"\x00"):
        return name
    return None


def _lint_cell(body: bytes) -> _CellFacts | None:
    """Extract the interior-cell facts the lint needs.

    Args:
        body: The ``CELL`` record body.

    Returns:
        The facts, or ``None`` for an exterior cell or one whose ``DATA`` is
        too short to read -- neither is a finding.
    """
    name = ""
    data: bytes | None = None
    ambience: bytes | None = None
    for subtag, payload in _iter_subrecords(body):
        if subtag == b"NAME":
            name = _lint_zstr(payload)
        elif subtag == b"DATA" and data is None:
            data = payload
        elif subtag == b"AMBI":
            ambience = payload
    if data is None or len(data) < 12:
        return None
    (flags,) = struct.unpack_from("<I", data, 0)
    if not flags & 1:
        return None  # exterior
    cell_id = name.lower()
    fog_bug = False
    if not flags & 128:  # not behave-like-exterior
        if ambience is not None and len(ambience) == 16:
            (fog,) = struct.unpack_from("<f", ambience, 12)
        else:
            (fog,) = struct.unpack_from("<f", data, 8)
        fog_bug = fog == 0.0
    return _CellFacts(
        name=name,
        cell_id="" if cell_id in _LINT_SKIP_CELLS else cell_id,
        fog_bug=fog_bug,
    )


def _lint_interior_pathgrid(body: bytes) -> str | None:
    """Identify the interior cell a path grid belongs to.

    Args:
        body: The ``PGRD`` record body.

    Returns:
        The cell's lower-cased name, or ``None`` for an exterior path grid.
        Interiors are the ones carrying grid ``(0, 0)``.
    """
    name = ""
    grid_x: int | None = None
    grid_y: int | None = None
    for subtag, data in _iter_subrecords(body):
        if subtag == b"NAME":
            name = _lint_zstr(data)
        elif subtag == b"DATA" and len(data) >= 8:
            grid_x, grid_y = struct.unpack_from("<ii", data, 0)
    if grid_x == 0 and grid_y == 0 and name:  # interiors carry grid (0,0)
        return name.lower()
    return None


def _lint_twin_warnings(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_lower: set[str],
    tagfor: Callable[[str], str],
) -> list[str]:
    """Report custom plugins whose .omwscripts / content twin is not loaded.

    An active ``.omwaddon``/``.esp`` sitting next to an ``.omwscripts`` of the
    same stem (or the reverse) almost always needs both declared: a missing twin
    silently disables the mod's Lua half, or leaves scripts referencing content
    that never loads. This was the real cause behind a batch of user-reported
    ORPHAN confusion.

    Args:
        active_order: The active plugins, in load order.
        index: Resolves a plugin filename to a path on disk.
        subset_lower: Lower-cased filenames of the user's own mods. Only those
            are checked; curated files are the list's business.
        tagfor: Renders a plugin's provenance tag for the warning text.

    Returns:
        One warning per missing twin.
    """
    warnings: list[str] = []
    active_lower = {str(plugin).lower() for plugin in active_order}
    for plugin in active_order:
        lowered = str(plugin).lower()
        if lowered not in subset_lower:
            continue
        found = index.find(plugin) if index else None
        if found is None:
            continue
        path = Path(found)
        stem = path.name.rsplit(".", 1)[0]
        if lowered.endswith((".omwaddon", ".esp")):
            twin = path.with_name(stem + ".omwscripts")
            if twin.exists() and twin.name.lower() not in active_lower:
                warnings.append(
                    f"[TWIN] '{plugin}'{tagfor(plugin)}: '{twin.name}' sits in the same folder but "
                    f"isn't in the load order -- the mod's Lua half is disabled. Add it "
                    f"(or confirm it's optional)."
                )
        elif lowered.endswith(".omwscripts"):
            for ext in (".omwaddon", ".esp"):
                twin = path.with_name(stem + ext)
                if twin.exists() and twin.name.lower() not in active_lower:
                    warnings.append(
                        f"[TWIN] '{plugin}'{tagfor(plugin)}: '{twin.name}' sits in the same folder "
                        f"but isn't in the load order -- scripts may reference content that "
                        f"never loads. Add it (or confirm it's optional)."
                    )
                    break
    return warnings


def lint_plugins(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_names: Sequence[str] | None = None,
    origins: Mapping[str, str] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Run the ported lint checks over every active plugin.

    Checks:

        [EVLGMST]     evil GMSTs (name AND value match; see table above) -- fixed
                      by cleaning the plugin (tes3cmd clean removes them).
        [FOGBUG]      interior cell, not behave-like-exterior, AMBI fog density
                      exactly 0.0 -- renders as a black void on some GPUs
                      (tes3lint's FOGBUG; exact port of its DATA/AMBI logic).
        [NO PATHGRID] an interior cell introduced by the load order has no PGRD
                      record anywhere in it -- NPCs can't pathfind there
                      (missing_pathgrids.pl, minus its false positives: the
                      pathgrid may come from ANY plugin, not just earlier ones).
        [HEADER]      custom plugin with a blank author and/or description
                      (tes3lint MISSAUT/MISSDSC; customs only -- curated files
                      are the list's business).

        Vanilla masters and merged/multipatch artifacts are skipped, like the
        reference scripts do. origins maps plugin_lower -> provenance for the
        warning text.
    """
    subset_lower = {str(s).lower() for s in (subset_names or ())}
    origins = origins or {}
    warnings: list[str] = []
    stats: dict[str, Any] = {"scanned": 0, "unreadable": 0}
    interior_first: dict[str, tuple[str, str]] = {}  # cell id lower -> (plugin, display name)
    pathgrids: set[str] = set()  # interior pathgrid cell ids seen anywhere

    def tagfor(plugin: str) -> str:
        """Render a plugin's provenance tag for a warning line.

        Args:
            plugin: The plugin filename.

        Returns:
            ``" [origin]"``, or an empty string when the origin is unknown.
        """
        origin = origins.get(str(plugin).lower())
        return f" [{origin}]" if origin else ""

    for position, plugin in enumerate(active_order):
        lowered = str(plugin).lower()
        if lowered.endswith(".omwscripts") or lowered in _LINT_SKIP:
            continue
        path = index.find(plugin) if index else None
        if path is None:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            stats["unreadable"] += 1
            continue
        if raw[:4] != b"TES3":
            continue
        stats["scanned"] += 1
        if progress:
            progress(position, plugin)
        warnings.extend(
            _lint_one_plugin(
                raw,
                plugin,
                is_custom=lowered in subset_lower,
                tagfor=tagfor,
                interior_first=interior_first,
                pathgrids=pathgrids,
            )
        )

    for cell_id, (plugin, name) in sorted(interior_first.items()):
        if cell_id not in pathgrids:
            warnings.append(
                f"[NO PATHGRID] '{plugin}'{tagfor(plugin)}: new interior cell '{name}' has no "
                f"pathgrid anywhere in the load order -- NPCs can't pathfind there."
            )

    warnings.extend(_lint_twin_warnings(active_order, index, subset_lower, tagfor))

    stats["warnings"] = len(warnings)
    stats["interior_cells"] = len(interior_first)
    stats["pathgrids"] = len(pathgrids)
    return warnings, stats


def _lint_one_plugin(
    raw: bytes,
    plugin: str,
    *,
    is_custom: bool,
    tagfor: Callable[[str], str],
    interior_first: dict[str, tuple[str, str]],
    pathgrids: set[str],
) -> list[str]:
    """Run the per-record checks over one plugin.

    Two of the checks are load-order-wide rather than per-plugin -- whether an
    interior cell has a path grid *anywhere*, and which plugin introduced it --
    so those two accumulators are passed in and updated here. Everything else
    this function decides on its own.

    Args:
        raw: The plugin file's bytes.
        plugin: Its filename, for the warning text.
        is_custom: Whether it is one of the user's own mods. The header and
            expansion-dependency checks apply only to those: a curated file's
            metadata is the list's business, not this tool's.
        tagfor: Renders a plugin's provenance tag.
        interior_first: Cell id to the plugin that first introduced it, updated
            in place. The first writer wins, which is load order.
        pathgrids: Interior cell ids that have a path grid, updated in place.

    Returns:
        The warnings this plugin earns on its own.
    """
    warnings: list[str] = []
    evil_gmsts: list[str] = []
    masters: set[str] = set()
    tribunal: set[str] = set()
    bloodmoon: set[str] = set()

    for tag, body in _iter_tes3_records(raw):
        if is_custom and tag in (b"SCPT", b"INFO"):
            found_tribunal, found_bloodmoon = _lint_expansion_calls(body, tag)
            tribunal |= found_tribunal
            bloodmoon |= found_bloodmoon
        elif tag == b"TES3":
            masters |= _lint_masters(body)
            missing = _lint_header_gaps(body) if is_custom else []
            if missing:
                warnings.append(
                    f"[HEADER] '{plugin}'{tagfor(plugin)}: header has no "
                    f"{' and no '.join(missing)}."
                )
        elif tag == b"GMST":
            evil = _lint_evil_gmst(body)
            if evil:
                evil_gmsts.append(evil)
        elif tag == b"CELL":
            cell = _lint_cell(body)
            if cell is None:
                continue
            if cell.cell_id and cell.cell_id not in interior_first:
                interior_first[cell.cell_id] = (plugin, cell.name)
            if cell.fog_bug:
                warnings.append(
                    f"[FOGBUG] '{plugin}'{tagfor(plugin)}: interior cell '{cell.name}' has fog "
                    f"density 0.0 -- renders as a black void on some GPUs. Fix by "
                    f"setting any nonzero fog density on the cell."
                )
        elif tag == b"PGRD":
            interior = _lint_interior_pathgrid(body)
            if interior:
                pathgrids.add(interior)

    if evil_gmsts:
        warnings.append(
            f"[EVLGMST] '{plugin}'{tagfor(plugin)}: {len(evil_gmsts)} evil GMST(s): "
            f"{', '.join(sorted(evil_gmsts))} -- stale expansion defaults copied in "
            f"by an old Construction Set; tes3cmd clean removes them."
        )
    if tribunal and "tribunal.esm" not in masters and "bloodmoon.esm" not in masters:
        warnings.append(
            f"[EXP-DEP] '{plugin}'{tagfor(plugin)}: scripts use Tribunal function(s) "
            f"{', '.join(sorted(tribunal))} but the plugin doesn't master Tribunal.esm -- "
            f"fragile on non-expansion setups (tes3lint !TB-FUN)."
        )
    if bloodmoon and "bloodmoon.esm" not in masters:
        warnings.append(
            f"[EXP-DEP] '{plugin}'{tagfor(plugin)}: scripts use Bloodmoon function(s) "
            f"{', '.join(sorted(bloodmoon))} but the plugin doesn't master Bloodmoon.esm -- "
            f"fragile on non-expansion setups (tes3lint !BM-FUN)."
        )
    return warnings


def flatten_dict(d: Mapping[str, Any], parent_key: str = "", sep: str = ".") -> dict[str, Any]:
    """Flatten a nested record dict into dotted keys.

    Lists are kept as whole
    values) -- ported from TES3 Conflictsolver so field comparison matches it.
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        nk = f"{parent_key}{sep}{k}" if parent_key else str(k)
        if isinstance(v, dict):
            items.extend(flatten_dict(v, nk, sep=sep).items())
        else:
            items.append((nk, v))
    return dict(items)


def _rec_deleted(rec: Mapping[str, Any]) -> bool:
    if not isinstance(rec, dict):
        return False
    flags = rec.get("flags")
    if isinstance(flags, (list, tuple)):
        return any("delet" in str(f).lower() for f in flags)
    if isinstance(flags, str):
        return "delet" in flags.lower()
    if isinstance(flags, int):
        return bool(flags & 0x20)  # TES3 deleted flag
    return bool(rec.get("deleted"))


def _interior_cell_names(records: Iterable[Mapping[str, Any]]) -> set[str]:
    """Collect the lower-cased names of every interior CELL record.

    Path grids don't carry the interior flag themselves -- only their parent
    CELL record does (DATA.flags bit 0x01) -- so anything that needs to tell
    an interior path grid from an unnamed exterior one has to look this up
    first. This is deliberately NOT "is the grid (0, 0)": interior path grids
    do store (0, 0) by convention, but a real exterior cell can legitimately
    sit at world origin too (Ashlands Region), so grid alone is ambiguous and
    the flag is the only signal that isn't.

    Args:
        records: One plugin's tes3conv records.

    Returns:
        Lower-cased interior cell names.
    """
    out: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("type") or "").lower() != "cell":
            continue
        raw_data = rec.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        flags = data.get("flags")
        interior = (
            bool(flags & 0x01)
            if isinstance(flags, int)
            else (flags is not None and "INTERIOR" in str(flags).upper())
        )
        if not interior:
            continue
        name = rec.get("id") or rec.get("name")
        if name:
            out.add(str(name).lower())
    return out


def _tes3conv_record_key(
    rec: Mapping[str, Any], interior_cells: AbstractSet[str] | None = None
) -> tuple[str, str] | None:
    """(type, id) for a tes3conv JSON record.

    tes3conv (via the tes3 crate) emits internally-tagged JSON: {"type": "Npc", "id":
    ...}. Most records carry an 'id' (or 'name'); id-less ones -- exterior cells,
    Landscape, path grids -- carry a 'grid' instead, so we key those by their coords
    (which TES3 Conflictsolver's plain 'id or name' misses, collapsing them all
    together). Returns None for the file header / anything with no usable id.

    Args:
        rec: One tes3conv JSON record.
        interior_cells: Lower-cased names of interior cells in this same
            plugin, from :func:`_interior_cell_names`. Used to key a
            cell-scoped record (path grids) by name alone when its cell is
            genuinely interior, vs. by name-plus-coordinates for a named
            exterior cell. Without it, falls back to the old grid-is-zero
            heuristic, which is only ambiguous exactly at world origin.
    """
    if not isinstance(rec, dict):
        return None
    rtype = rec.get("type")
    if not rtype or str(rtype).lower() in ("header", "tes3"):
        return None
    rid = rec.get("id") or rec.get("name")
    if rid is None or str(rid) == "":
        grid = rec.get("grid")
        if grid is None and isinstance(rec.get("data"), dict):
            grid = rec["data"].get("grid")
        gx, gy = (
            (grid[0], grid[1])
            if isinstance(grid, (list, tuple)) and len(grid) >= 2
            else (None, None)
        )
        cell = rec.get("cell") or rec.get("cell_name")
        if cell is None and isinstance(rec.get("data"), dict):
            cell = rec["data"].get("cell") or rec["data"].get("cell_name")
        if cell:
            # Cell-scoped records (e.g. PathGrid): keying by grid alone
            # collapses every interior's pathgrid across every plugin into
            # one bogus "(0, 0)" conflict, since interiors all store that
            # grid by convention. Key by the CELL name instead for
            # interiors, and by name-plus-coordinates for named exteriors.
            is_interior = (
                str(cell).lower() in interior_cells
                if interior_cells is not None
                else not (gx or gy)  # no flag lookup available: old heuristic
            )
            rid = str(cell) if is_interior else f"{cell} ({gx}, {gy})"
        elif gx is not None:
            rid = f"({gx}, {gy})"
    if rid is None or str(rid) == "":
        return None
    return (str(rtype), str(rid))


def _no_window_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs that suppress the Windows console flash.

    On Windows, when a windowed (GUI / auto-py-to-exe / ``--noconsole``) build
    shells out to a console program like tes3conv -- otherwise you get one popup
    per plugin. No-op elsewhere.

    Delegates to :func:`wraithguard.proc.no_window_kwargs`, the one
    implementation the package modules also use, so the merge and patch services
    and this script cannot drift apart on it.
    """
    from wraithguard.proc import no_window_kwargs

    return no_window_kwargs()


class Tes3ConvSession:
    """DISK-BACKED tes3conv wrapper.

    Converts each plugin to a JSON file in a dump folder ONCE, and reads it back per-
    plugin on demand -- it does NOT keep every plugin's records in memory (that was
    multi-GB / OOM on a big list). Only the small map of plugin -> json-file-path is
    held. Peak memory is now bounded by a single plugin's JSON, not the whole load
    order.

        dump_dir: where to write the .json files (a temp dir if None). keep: if True
        (or an explicit dump_dir is given) the files are left in place; otherwise a
        temp dump is removed by cleanup().
    """

    # Bump when the sidecar key/cell extraction changes so stale caches are
    # rebuilt (v2: pathgrids keyed by cell, not the shared "(0,0)" grid;
    # v3: interior determination uses the CELL record's own flag bit instead
    # of a grid==(0,0) heuristic, which was ambiguous -- a real exterior cell
    # can legitimately sit at world origin too).
    _SIDECAR_VER = 3

    #: Which reader produced this session's JSON, for the engine label a scan
    #: prints and records in its stats. Overridden by the native subclass.
    engine_name: str = "tes3conv"

    def __init__(self, exe: str, dump_dir: str | None = None, keep: bool = False) -> None:
        """Open a session backed by ``exe``, spooling JSON to ``dump_dir``."""
        import tempfile
        import threading

        self.exe = exe
        # Guards _json_paths so prime() can convert plugins from several threads
        # at once. Only the dict is locked, never the subprocess -- the whole
        # point is to let the tes3conv processes run in parallel.
        self._json_lock = threading.Lock()
        # keep = leave the dump on disk when cleanup() runs. Location and lifetime
        # are independent: a session can dump to a STABLE folder (so its JSON is
        # reused by later scans in the same run) yet still be cleaned up on close
        # when keep is False.
        self.keep = bool(keep)
        self._temp = dump_dir is None
        self.dump_dir = (
            Path(dump_dir) if dump_dir else Path(tempfile.mkdtemp(prefix="mlox_tes3conv_"))
        )
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._json_paths: dict[str, str] = {}  # plugin path(str) -> json path on disk

    def _json_for(self, path: str | Path) -> str | None:
        import subprocess

        key = str(path)
        with self._json_lock:
            jp = self._json_paths.get(key)
        if jp and Path(jp).exists():
            return jp
        out = self.dump_dir / (Path(path).stem + ".json")
        if out.exists():
            if not self._stale(out, path):  # reuse existing JSON -- don't re-run tes3conv
                with self._json_lock:
                    self._json_paths[key] = str(out)
                trace(f"tes3conv: REUSE {out.name}")
                return str(out)
            trace(f"tes3conv: STALE, re-convert {out.name} (plugin newer than json)")
        try:
            trace(f"tes3conv: CONVERT {Path(path).name} -> {out.name}")
            # S603: the argument list is built entirely here -- a resolved
            # executable path plus two paths we constructed. No shell, no user
            # string interpolation, so there is nothing to inject through.
            subprocess.run(  # noqa: S603
                [self.exe, str(path), str(out)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
                check=True,
                **_no_window_kwargs(),
            )
            with self._json_lock:
                self._json_paths[key] = str(out)
            return str(out)
        except (OSError, subprocess.SubprocessError):
            # OSError: tes3conv missing/not executable. SubprocessError covers
            # both CalledProcessError (check=True) and TimeoutExpired.
            return None

    #: Default ceiling on concurrent tes3conv processes. Each worker is a live
    #: converter plus its output JSON, so the cap is what keeps peak memory
    #: bounded; four is plenty to hide the per-process startup and I/O without
    #: stacking several large conversions at once.
    _PRIME_WORKERS: int = 4

    def prime(self, paths: Iterable[str | Path], max_workers: int | None = None) -> int:
        """Convert many plugins to on-disk JSON at once, warming the cache.

        tes3conv runs as a separate process per plugin, so a bounded thread pool
        converts several on different cores at the same time without the GIL
        getting in the way -- the read phase of a merge or a scan spends most of
        its wall-clock here. Each plugin writes its own ``<stem>.json`` (a unique
        name, so parallel writes never collide) and is skipped when a fresh JSON
        already exists, exactly as :meth:`_json_for` does one at a time.

        **Bounded on purpose.** Every worker is a live tes3conv process and its
        output held in memory; an unbounded pool would multiply peak memory by
        the plugin count, which is how the original tool reached tens of GB. The
        cap defaults to ``min(cpu_count, _PRIME_WORKERS)``.

        Args:
            paths: The plugin files to convert. Duplicates are collapsed.
            max_workers: Concurrent conversions, or ``None`` for the default cap.
                Values below 1 are treated as 1 (serial).

        Returns:
            How many of the plugins now have a usable JSON on disk.
        """
        from concurrent.futures import ThreadPoolExecutor

        unique = list(dict.fromkeys(str(p) for p in paths))
        if not unique:
            return 0
        cap = max_workers if max_workers else min((os.cpu_count() or 1), self._PRIME_WORKERS)
        workers = max(1, min(cap, len(unique)))
        if workers == 1:
            return sum(1 for p in unique if self._json_for(p))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return sum(1 for result in pool.map(self._json_for, unique) if result)

    def _records(self, path: str | Path) -> list[Any]:
        import json

        jp = self._json_for(path)
        if not jp:
            return []
        try:
            with Path(jp).open(encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            # OSError: unreadable/vanished file. ValueError: JSONDecodeError and
            # UnicodeDecodeError both subclass it.
            return []

    def records(self, path: str | Path) -> list[Any]:
        """Return every record in one plugin, header included.

        The header is the point: it carries the master list, and a record
        patch cannot renumber a cell's references without it. `record_map`
        cannot serve this because the header has no id to key it by.

        Args:
            path: The plugin file.

        Returns:
            The decoded records, or an empty list if it could not be read.
        """
        return self._records(path)

    def record_map(self, path: str | Path) -> dict[tuple[str, str], Any]:
        """Return {(rectype, rid): record} for one plugin, from cached JSON."""
        # Built fresh each call and NOT cached, so only one plugin's records are
        # ever in memory at a time. self._records() returns an in-memory list
        # (already read once), so iterating it twice -- once for the interior
        # lookup, once to key every record -- costs no extra I/O.
        records = self._records(path)
        interior_cells = _interior_cell_names(records)
        m = {}
        for rec in records:
            k = _tes3conv_record_key(rec, interior_cells)
            if k and k not in m:
                m[k] = rec
        return m

    def record_subset(
        self, path: str | Path, wanted: AbstractSet[tuple[str, str]]
    ) -> dict[tuple[str, str], Any]:
        """Return ``{(rectype, rid): record}`` for just the ``wanted`` keys.

        Like :meth:`record_map`, but *streams* the plugin's JSON and keeps only
        the records asked for, so peak memory is one record plus the kept subset
        rather than the whole decoded file. Decoding a plugin's JSON costs about
        3.5x its size in RAM, so on a 600 MB landmass plugin this is the
        difference between ~2 GB and tens of MB. :func:`batch_record_fields` and
        the on-click :func:`diff_record_fields` both want only a handful of a
        plugin's records, and are the callers that need it.

        Streaming needs the ``ijson`` parser (optional). Without it, this falls
        back to :meth:`record_map` filtered to ``wanted`` -- the same result, at
        the old peak memory. So installing ``ijson`` only changes the memory
        profile, never the records returned.

        Args:
            path: The plugin file.
            wanted: The ``(rectype, rid)`` keys to keep.

        Returns:
            Those of the ``wanted`` records that exist, keyed exactly as
            :func:`_tes3conv_record_key` keys them. Absent keys are simply not
            in the result.
        """
        want = set(wanted)
        if not want:
            return {}

        try:
            import ijson
        except ImportError:
            # No streaming parser: same answer, whole-file peak. Optional dep,
            # so a missing one degrades memory rather than breaking the read.
            return {k: v for k, v in self.record_map(path).items() if k in want}

        jp = self._json_for(path)
        if not jp:
            return {}

        # Cell-scoped, id-less records (path grids) are keyed by their parent
        # cell, and whether that cell is interior decides the key shape.
        # record_map learns this from a full pre-pass; the cells sidecar already
        # holds it for a few KB, so streaming needs no second parse of the file.
        interior = {
            str(name).lower() for kind, name, *_ in self.cells(path) if kind == "int" and name
        }

        out: dict[tuple[str, str], Any] = {}
        remaining = set(want)
        try:
            with Path(jp).open("rb") as fh:
                # use_float=True: decode reals as float and integers as int,
                # exactly as json.load does. Without it ijson yields Decimal,
                # which would compare and repr() differently from every other
                # read path and quietly change what the differ calls a conflict.
                for rec in ijson.items(fh, "item", use_float=True):
                    if not isinstance(rec, dict):
                        continue
                    key = _tes3conv_record_key(rec, interior)
                    if key in remaining:
                        out[key] = rec
                        remaining.discard(key)
                        if not remaining:
                            # Everything asked for is in hand; stop parsing the
                            # rest of the file entirely.
                            break
        except (OSError, ValueError):
            # A truncated or otherwise unstreamable file must never silently
            # return less than record_map would: fall back to the whole read.
            return {k: v for k, v in self.record_map(path).items() if k in want}
        return out

    @staticmethod
    def _stale(json_path: Path, plugin_path: str | Path) -> bool:
        """Report whether a cached JSON predates its plugin.

        It is stale when the plugin was modified after the JSON was written,
        so a changed plugin re-converts. If either mtime can't be read, treat the
        cache as good (reuse) rather than needlessly re-running tes3conv.
        """
        try:
            return json_path.stat().st_mtime < Path(plugin_path).stat().st_mtime
        except OSError:
            return False

    def _load_sidecar(self, side: Path, path: str | Path) -> list[tuple[Any, ...]] | None:
        import json

        if side.exists() and not self._stale(side, path):
            try:
                with Path(side).open(encoding="utf-8", errors="replace") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict) and obj.get("v") == self._SIDECAR_VER:
                    return [tuple(x) for x in obj.get("d", [])]
                # older/mismatched cache format -> rebuild
            except (OSError, ValueError, TypeError):
                # OSError: unreadable sidecar. ValueError: JSON/decode errors.
                # TypeError: a corrupt cache whose "d" entries aren't iterable,
                # which tuple() would reject. Any of them just means "rebuild".
                pass
        return None

    def _build_sidecars(
        self, path: str | Path
    ) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[Any]]:
        """Read a plugin's full JSON once and build every sidecar cache.

        Extracts the compact record-key
        list (conflicts), the cell list (map), and the landscape records (Merged
        Lands), writing all three sidecars. Whichever of record_keys()/cells()/
        landscape_records() is called first pays this single read; the others
        then hit their fresh sidecars -- so Check Conflicts, Cell Map and Merged
        Lands together read each big JSON once per run, not three times.
        """
        import json

        records = self._records(path)  # the single big-JSON read
        # The Landscape and LandscapeTexture records, kept whole: this is all
        # Merged Lands needs (heights, normals, colours, texture indices, world
        # map, and the LTEX id/name records), a few hundred KB of a plugin that
        # may run to hundreds of MB. Written to a '<stem>.land.json' sidecar so a
        # merge reads terrain from the cache instead of re-running the converter.
        land = [
            rec
            for rec in records
            if isinstance(rec, dict) and rec.get("type") in ("Landscape", "LandscapeTexture")
        ]
        # A cheap first pass over the already-in-memory list (no extra I/O):
        # path grids need to know which cells are interior before they can be
        # keyed correctly, and a cell's own record can appear anywhere in the
        # file relative to its path grid's.
        interior_cells = _interior_cell_names(records)

        keys: list[list[Any]] = []
        cells: list[list[Any]] = []
        seen: set[Any] = set()
        for rec in records:
            if not isinstance(rec, dict):
                continue
            # Lua scripts declared by an .omwaddon LuaScriptsCfg (keyless record)
            if str(rec.get("type", "")).lower().replace("_", "") in ("luascriptscfg", "lual"):
                for s in rec.get("scripts") or rec.get("mScripts") or []:
                    sp = (
                        s.get("script_path") or s.get("path") or s.get("mScriptPath")
                        if isinstance(s, dict)
                        else (s if isinstance(s, str) else None)
                    )
                    if sp:
                        lk = ("LuaScript", str(sp).replace("\\", "/").lower().lstrip("/"))
                        if lk not in seen:
                            seen.add(lk)
                            keys.append([lk[0], lk[1], False])
            k = _tes3conv_record_key(rec, interior_cells)
            if not k or k in seen:
                continue
            seen.add(k)
            rtype, rid = k
            keys.append([rtype, rid, _rec_deleted(rec)])
            if str(rtype).lower() == "cell":
                name = str(rec.get("id") or rec.get("name") or rid)
                if name.lower() in interior_cells:
                    cells.append(["int", name, None])
                else:
                    _raw_data = rec.get("data")
                    data = _raw_data if isinstance(_raw_data, dict) else {}
                    grid = data.get("grid") or rec.get("grid")
                    if isinstance(grid, (list, tuple)) and len(grid) >= 2:
                        cells.append(["ext", int(grid[0]), int(grid[1])])
                    else:
                        mm = re.match(r"^\((-?\d+), (-?\d+)\)$", str(rid))
                        if mm:
                            cells.append(["ext", int(mm.group(1)), int(mm.group(2))])
        stem = Path(path).stem
        for name, payload in (
            (stem + ".keys.json", keys),
            (stem + ".cells.json", cells),
            (stem + ".land.json", land),
        ):
            try:
                with (self.dump_dir / name).open("w", encoding="utf-8") as fh:
                    json.dump({"v": self._SIDECAR_VER, "d": payload}, fh)
            except OSError:  # noqa: PERF203
                # Per-file isolation: failing to write one sidecar must not
                # lose the others. The cache is an optimisation, never required
                # for correctness, so a write failure is silently tolerated.
                pass
        return [tuple(x) for x in keys], [tuple(x) for x in cells], land

    def record_keys(self, path: str | Path) -> list[tuple[Any, ...]]:
        """Return a compact (rectype, rid, deleted) list for every record.

        For one plugin
        (deduped, first-wins, plus .omwaddon Lua scripts as ('LuaScript', p)) --
        all conflict DETECTION needs, a few hundred KB vs the multi-MB JSON. Served
        from a '<stem>.keys.json' sidecar; rebuilt (with the cells sidecar) only if
        the plugin changed. The on-click field diff still reads the full record.
        """
        cached = self._load_sidecar(self.dump_dir / (Path(path).stem + ".keys.json"), path)
        return cached if cached is not None else self._build_sidecars(path)[0]

    def cells(self, path: str | Path) -> list[tuple[Any, ...]]:
        """Return a compact list of the CELLs a plugin touches.

        Entries are ('ext', gx, gy) / ('int', name, None) for the cells a plugin
        touches -- all the cell map needs. Served from a '<stem>.cells.json'
        sidecar; rebuilt (with the keys sidecar) only if the plugin changed.
        """
        cached = self._load_sidecar(self.dump_dir / (Path(path).stem + ".cells.json"), path)
        return cached if cached is not None else self._build_sidecars(path)[1]

    def landscape_records(self, path: str | Path) -> list[Any]:
        """Return the Landscape and LandscapeTexture records for one plugin.

        The whole records, not the compact keys -- everything Merged Lands needs
        to merge terrain -- served from a '<stem>.land.json' sidecar and rebuilt
        (with the other sidecars) only when the plugin changed. A merge over a
        load order the conflict scanner has already seen therefore reads terrain
        straight from the cache instead of re-running the converter per plugin.
        """
        import json

        side = self.dump_dir / (Path(path).stem + ".land.json")
        if side.exists() and not self._stale(side, path):
            try:
                with side.open(encoding="utf-8", errors="replace") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict) and obj.get("v") == self._SIDECAR_VER:
                    data = obj.get("d")
                    if isinstance(data, list):
                        return data
            except (OSError, ValueError):
                # Unreadable or corrupt cache -> rebuild it below.
                pass
        return self._build_sidecars(path)[2]

    def dumped_dir(self) -> str:
        """Return the folder the JSON spool was written to."""
        return str(self.dump_dir)

    def cleanup(self) -> None:
        """Remove the dump unless keep is set.

        Honors keep regardless of whether the dump is a temp dir or a stable folder, so
        'don't keep' still cleans up a stable dump on close.
        """
        if not self.keep:
            import shutil

            shutil.rmtree(self.dump_dir, ignore_errors=True)


class NativeEspSession(Tes3ConvSession):
    """A :class:`Tes3ConvSession` that converts plugins in-process, without tes3conv.

    The parent is built entirely on one primitive -- :meth:`_json_for`, which
    turns a plugin into an on-disk JSON file -- so a native backend overrides only
    that: it reads the plugin with :mod:`wraithguard.esp` and writes the same
    ``tes3conv``-schema JSON (:func:`wraithguard.esp.plugin_to_json`, whose output
    is verified byte-for-byte against ``tes3conv``). Every higher method --
    ``record_map``, ``record_subset``, ``cells``, ``landscape_records``, the
    sidecars, the ``ijson`` streaming -- then works unchanged, reading that file.

    This is the fallback that makes ``tes3conv`` optional: with it, record- and
    field-level conflict detection, the cell map and Merged Lands all run on the
    built-in reader alone. ``tes3conv`` stays the default when it is present -- it
    is the community's trusted converter and its zstd framing is canonical -- but
    its absence no longer reduces conflict detection to bare record counts.
    """

    engine_name = "native"

    def __init__(self, dump_dir: str | None = None, keep: bool = False) -> None:
        """Open a native session, spooling JSON to ``dump_dir`` (a temp dir if None).

        Args:
            dump_dir: Where to write the ``.json`` spool, or ``None`` for a temp
                dir removed on :meth:`cleanup`.
            keep: Leave the dump in place on cleanup even if it is a temp dir.
        """
        super().__init__(exe="", dump_dir=dump_dir, keep=keep)

    def _json_for(self, path: str | Path) -> str | None:
        """Convert one plugin to on-disk JSON in process, reusing a fresh cache.

        Mirrors the parent's cache and staleness handling exactly; only the
        conversion differs -- :func:`wraithguard.esp.read_plugin` plus
        :func:`wraithguard.esp.record_to_json` in place of a subprocess. Records
        the built-in reader does not model (OpenMW-only tags, which come back as
        ``UnknownRecord``) are skipped rather than failing the file, so a plugin
        ``tes3conv`` would refuse outright still yields its ordinary records.

        Args:
            path: The plugin file to convert.

        Returns:
            The path to the JSON on disk, or ``None`` if the plugin could not be
            read.
        """
        import json as _json

        from wraithguard.esp import EspError, UnknownRecord, read_plugin, record_to_json

        key = str(path)
        with self._json_lock:
            jp = self._json_paths.get(key)
        if jp and Path(jp).exists():
            return jp
        out = self.dump_dir / (Path(path).stem + ".json")
        if out.exists() and not self._stale(out, path):
            with self._json_lock:
                self._json_paths[key] = str(out)
            trace(f"native esp: REUSE {out.name}")
            return str(out)
        try:
            trace(f"native esp: CONVERT {Path(path).name} -> {out.name}")
            records = read_plugin(Path(path).read_bytes())
            objs = [record_to_json(r) for r in records if not isinstance(r, UnknownRecord)]
            with out.open("w", encoding="utf-8") as fh:
                _json.dump(objs, fh)
        except (OSError, EspError, ValueError):
            # OSError: unreadable/vanished plugin or dump. EspError: malformed
            # plugin bytes. ValueError covers EspJsonError (a record with no JSON
            # form). Any of them means "no JSON for this plugin", as a failed
            # tes3conv run returns None.
            return None
        with self._json_lock:
            self._json_paths[key] = str(out)
        return str(out)


def diff_record_fields(
    session: Tes3ConvSession | None, conflict: Mapping[str, Any], paths: Mapping[str, str]
) -> tuple[list[str], dict[str, dict[str, Any]], set[str]]:
    """Field-level comparison for one conflicting record across the plugins that touch it.

    Returns (ordered_keys, per_plugin, differing_keys): ordered_keys  -- dotted field
    keys, in first-seen order per_plugin    -- {plugin: {key: value}} differing_keys--
    the subset of keys whose value differs between plugins (the actual field-level
    conflicts). Empty if identical. Needs a Tes3ConvSession; returns ([], {}, set())
    without one.
    """
    if session is None:
        return [], {}, set()
    key = (conflict["type"], conflict["id"])
    per = {}
    for p in conflict["plugins"]:
        # Stream out just this one record rather than decoding the whole
        # plugin: the on-click diff of a record a 600 MB landmass defines used
        # to materialise ~2 GB to read one entry (see record_subset).
        rec = session.record_subset(paths.get(p, ""), {key}).get(key) if paths.get(p) else None
        per[p] = flatten_dict(rec) if isinstance(rec, dict) else {}
    ordered, seen = [], set()
    for p in conflict["plugins"]:
        for k in per[p]:
            if k not in seen:
                seen.add(k)
                ordered.append(k)
    differing = set()
    for k in ordered:
        vals = {repr(per[p].get(k)) for p in conflict["plugins"] if k in per[p]}
        present = sum(1 for p in conflict["plugins"] if k in per[p])
        if len(vals) > 1 or present != len(conflict["plugins"]):
            differing.add(k)
    return ordered, per, differing


def batch_record_fields(
    session: Tes3ConvSession | None,
    conflicts: Sequence[Mapping[str, Any]],
    paths: Mapping[str, str],
    report: Callable[[int, int], None] | None = None,
    digest: bool = False,
    lock: AbstractContextManager[Any] | None = None,
) -> dict[tuple[str, str], tuple[list[str], dict[str, dict[str, Any]]]]:
    """Field values for many records, with one JSON parse per plugin.

    :func:`diff_record_fields` answers for one record, and calls
    :meth:`Tes3ConvSession.record_map` once per plugin defining it. That method
    deliberately caches nothing -- holding every plugin's records was multi-GB --
    so each call re-reads and re-parses that plugin's entire JSON.

    For one record on demand that is right. For a batch it is quadratic in the
    worst way: judging 2,000 records that Morrowind.esm defines re-parses its
    183 MB JSON 2,000 times, and one parse is 14 seconds cold. The sidecar
    already saves re-running tes3conv; nothing was saving the parse.

    So this inverts the loops. Every plugin is read once, only the wanted
    records are kept from it, and the parse is thrown away before the next.
    With ``ijson`` installed the read is a streaming one
    (:meth:`Tes3ConvSession.record_subset`), so peak memory is one record plus
    the wanted subset rather than the whole plugin's JSON -- the difference
    between tens of MB and ~2 GB on a big landmass plugin. Without ``ijson`` it
    falls back to a whole-file parse per plugin: same records, old peak.

    Args:
        session: The conversion session, or ``None`` for no field data.
        conflicts: The records to read, as the scanner reports them.
        paths: Plugin name to its file path.
        report: Called with ``(plugins done, plugins total)`` as it goes.
        digest: Keep a hash of each value rather than the value. A caller that
            only compares values for equality -- which is all judging does --
            does not need to hold them, and holding them is what makes a whole
            load order run out of memory: a landscape's height and normal
            fields are tens of kilobytes of base64 each, and there are 50,000
            conflicting records. Hashing bounds it to 16 bytes a field. Use it
            for judging; leave it off when the values will be shown.
        lock: Held around *each plugin read*, not around the whole call. The
            tes3conv session is one process on one pipe, so concurrent readers
            must take turns -- but holding it for a whole batch blocks every
            other reader for minutes, and a caller waiting minutes is an
            application the operating system reports as not responding.

    Returns:
        ``(type, id)`` to ``(ordered field keys, per-plugin values)`` -- the
        same shape :func:`diff_record_fields` returns, minus the differing set,
        which callers judging with :mod:`wraithguard.patch.status` do not use.
    """
    import hashlib

    # Any in and out: a decoded field is whatever tes3conv produced, and what
    # comes back is either that value unchanged or a hash of it.
    def keep(value: Any) -> Any:  # noqa: ANN401
        if not digest:
            return value
        return hashlib.blake2b(repr(value).encode("utf-8", "replace"), digest_size=16).digest()

    if session is None:
        return {}

    wanted: dict[str, set[tuple[str, str]]] = {}
    for conflict in conflicts:
        key = (conflict["type"], conflict["id"])
        for plugin in conflict["plugins"]:
            wanted.setdefault(plugin, set()).add(key)

    held: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for done, (plugin, keys) in enumerate(wanted.items(), start=1):
        path = paths.get(plugin, "")
        if path:
            with lock if lock is not None else contextlib.nullcontext():
                found = session.record_subset(path, keys)
            held[plugin] = {
                key: {name: keep(value) for name, value in flatten_dict(found[key]).items()}
                for key in keys
                if isinstance(found.get(key), dict)
            }
            del found  # before the next plugin, not after
        else:
            held[plugin] = {}
        if report is not None:
            report(done, len(wanted))

    out: dict[tuple[str, str], tuple[list[str], dict[str, dict[str, Any]]]] = {}
    for conflict in conflicts:
        key = (conflict["type"], conflict["id"])
        plugins = list(conflict["plugins"])
        per = {plugin: held.get(plugin, {}).get(key, {}) for plugin in plugins}
        ordered: list[str] = []
        seen: set[str] = set()
        for plugin in plugins:
            for field in per[plugin]:
                if field not in seen:
                    seen.add(field)
                    ordered.append(field)
        out[key] = (ordered, per)
    return out


def plugin_paths(order: Sequence[str], index: PluginFileIndex) -> dict[str, str]:
    """Resolve each plugin in a load order to its file path.

    The plugin-name to path map the conflict window's field diff, tree view,
    patch builder and plugin view all read. The conflict scan produces it as a
    side effect, but it needs no scan: it is only a last-wins lookup per name
    (see :class:`~wraithguard.plugins.PluginFileIndex`, which resolves a name a
    later ``data=`` folder shadows to that folder). Building it on demand is
    what lets those features open without a Check Conflicts run first -- the
    tes3conv JSON is still generated lazily, one plugin at a time, when a field
    is actually read.

    Args:
        order: Plugin file names, in load order.
        index: The data-folder index to resolve names against.

    Returns:
        Plugin name to its resolved path, omitting any name the index cannot
        find (a plugin the data folders do not hold).
    """
    resolved: dict[str, str] = {}
    for name in order:
        found = index.find(name)
        if found is not None:
            resolved[name] = str(found)
    return resolved


def _scan_touch(
    active_order: Sequence[str],
    index: PluginFileIndex,
    session: Tes3ConvSession | None,
) -> tuple[dict[tuple[str, str], list[tuple[str, bool]]], list[str], int, int, dict[str, str]]:
    """Scan the active load order and tally which plugins touch each record.

    The shared, expensive part of :func:`detect_conflicts` and
    :func:`list_subset_singles`: both need "every record, and which plugins
    touch it" -- they only disagree about which counts are worth keeping (2+
    plugins vs. exactly 1 of your own) -- so reading every plugin happens
    once here rather than once per caller.

    Args:
        active_order: Plugin filenames in load order (winner last).
        index: A PluginFileIndex to locate the files on disk.
        session: An optional Tes3ConvSession; when given, records are read
            from tes3conv JSON, else the built-in binary parser is used.

    Returns:
        ``(touch, unreadable, scanned, rec_count, paths)`` -- ``touch`` maps
        ``(type, id)`` to every ``(plugin, deleted)`` hit, in load order.
    """
    touch: dict[tuple[str, str], list[tuple[str, bool]]] = {}
    unreadable, scanned, rec_count = [], 0, 0
    paths: dict[str, str] = {}
    for plugin in active_order:
        path = index.find(plugin) if index else None
        if path is None:
            unreadable.append(plugin)
            continue
        scanned += 1
        paths[plugin] = str(path)
        is_omwscripts = str(path).lower().endswith(".omwscripts")
        seen_here = set()  # collapse a record the same plugin defines twice
        if session is not None and not is_omwscripts:
            # tes3conv for TES3 records, plus any Lua scripts declared in an
            # .omwaddon's LuaScriptsCfg (so they line up with .omwscripts).
            # record_keys() is sidecar-cached (compact keys, not full records),
            # so re-scans don't re-read the whole tes3conv dump.
            for rectype, rid, deleted in session.record_keys(path):
                key = (rectype, rid)
                if key in seen_here:
                    continue
                seen_here.add(key)
                rec_count += 1
                touch.setdefault(key, []).append((plugin, deleted))
        else:
            # built-in engine: handles .omwscripts (text) AND TES3 records incl.
            # .omwaddon LUAL scripts, all via parse_plugin_records.
            for rectype, rid, deleted in parse_plugin_records(path):
                rec_count += 1
                key = (rectype, rid)
                if key in seen_here:
                    continue
                seen_here.add(key)
                touch.setdefault(key, []).append((plugin, deleted))
    return touch, unreadable, scanned, rec_count, paths


def detect_conflicts(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_names: Sequence[str] | None = None,
    session: Tes3ConvSession | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan the active load order for record-level conflicts.

    active_order : plugin filenames in load order (winner last).
    index        : a PluginFileIndex to locate the files on disk.
    subset_names : your custom plugins, so conflicts involving them can be flagged.

    session : an optional Tes3ConvSession. When given, records are read from
    tes3conv JSON (exact ids for every record type, and field-level diffing is
    then possible via diff_record_fields); when None, the built-in binary parser
    is used (record-level only).

    Returns (conflicts, stats). conflicts is a list of dicts sorted with your
    custom-involved ones first:
      {type, id, plugins:[load order], winner, involves_subset, deleted_by:[...]}
    stats = {"scanned", "unreadable":[...], "records", "conflicts",
             "engine": "tes3conv"|"builtin", "paths": {plugin: path}}.
    """
    subset_lower = {s.lower() for s in (subset_names or [])}
    touch, unreadable, scanned, rec_count, paths = _scan_touch(active_order, index, session)

    conflicts = []
    for (rectype, rid), plugs in touch.items():
        if len(plugs) < 2:
            continue
        names = [p for p, _ in plugs]
        conflicts.append(
            {
                "type": rectype,
                "id": rid,
                "plugins": names,
                "winner": names[-1],
                "involves_subset": any(p.lower() in subset_lower for p in names),
                "deleted_by": [p for p, d in plugs if d],
            }
        )
    conflicts.sort(key=lambda c: (not c["involves_subset"], c["type"], str(c["id"]).lower()))
    stats: dict[str, Any] = {
        "scanned": scanned,
        "unreadable": unreadable,
        "records": rec_count,
        "conflicts": len(conflicts),
        "engine": session.engine_name if session is not None else "builtin",
        "paths": paths,
    }
    return conflicts, stats


def _list_singles(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_names: Sequence[str],
    session: Tes3ConvSession | None,
    *,
    want_subset: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Shared core of :func:`list_subset_singles` and :func:`list_other_singles`.

    Both want the same thing -- records with exactly one contributor -- and
    only disagree about which side of ``subset_names`` that contributor has
    to fall on, so the filter condition is the one parameter that differs.

    Args:
        active_order: Plugin filenames in load order (winner last).
        index: A PluginFileIndex to locate the files on disk.
        subset_names: Your custom plugins.
        session: An optional Tes3ConvSession; same meaning as in
            :func:`detect_conflicts`.
        want_subset: ``True`` keeps a single-contributor record when that
            lone plugin IS one of ``subset_names`` (your mods); ``False``
            keeps the complement -- a momw dependency, the game's own
            masters, anything active that isn't yours.

    Returns:
        ``(records, stats)``, matching :func:`detect_conflicts`'s shape:
        each record is ``{type, id, plugins: [the one plugin], winner,
        involves_subset, deleted_by}``, sorted by type then id. ``stats``
        carries a ``"singles"`` count in place of ``"conflicts"``.
    """
    subset_lower = {s.lower() for s in subset_names}
    touch, unreadable, scanned, rec_count, paths = _scan_touch(active_order, index, session)

    records = []
    for (rectype, rid), plugs in touch.items():
        if len(plugs) != 1:
            continue
        plugin, deleted = plugs[0]
        is_subset = plugin.lower() in subset_lower
        if is_subset != want_subset:
            continue
        records.append(
            {
                "type": rectype,
                "id": rid,
                "plugins": [plugin],
                "winner": plugin,
                "involves_subset": is_subset,
                "deleted_by": [plugin] if deleted else [],
            }
        )
    records.sort(key=lambda c: (c["type"], str(c["id"]).lower()))
    stats: dict[str, Any] = {
        "scanned": scanned,
        "unreadable": unreadable,
        "records": rec_count,
        "singles": len(records),
        "engine": session.engine_name if session is not None else "builtin",
        "paths": paths,
    }
    return records, stats


def list_subset_singles(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_names: Sequence[str],
    session: Tes3ConvSession | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """List records that only your own mods touch -- no conflict, just yours.

    A sister process to :func:`detect_conflicts`, not a variant of it: a
    record with exactly one contributor isn't a conflict, and folding it into
    the conflict list would misrepresent both the conflict count and the
    conflict map/table, which are specifically about collisions between
    plugins. This scans the same load order -- paying for the read only once,
    via :func:`_scan_touch` -- but keeps the single-plugin entries
    :func:`detect_conflicts` discards, filtered to ones where that lone
    plugin is one of ``subset_names``.

    The point is parity, not a new UI: the shape matches
    :func:`detect_conflicts`'s output exactly (a "conflict" with one plugin
    is just a record with one column), so the same field-diff panel and the
    same graph/difference/3D buttons already work on it -- "Show
    difference..." simply won't offer itself, correctly, since there's
    nothing to diff against.

    Args:
        active_order: Plugin filenames in load order (winner last).
        index: A PluginFileIndex to locate the files on disk.
        subset_names: Your custom plugins. Unlike ``detect_conflicts``, this
            is meant to be non-empty here -- there is no meaningful "single
            plugin, not mine" entry for this function to return.
        session: An optional Tes3ConvSession; same meaning as in
            :func:`detect_conflicts`.

    Returns:
        ``(records, stats)`` -- see :func:`_list_singles`. ``involves_subset``
        is always ``True`` here.
    """
    return _list_singles(active_order, index, subset_names, session, want_subset=True)


def list_other_singles(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_names: Sequence[str],
    session: Tes3ConvSession | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """List records that only ONE non-custom plugin touches.

    The complement of :func:`list_subset_singles`: everything active that
    isn't yours and isn't conflicting with anything either -- a momw-listed
    dependency's own untouched records, Morrowind.esm/Tribunal.esm/
    Bloodmoon.esm's own base content, any other plugin you didn't add as a
    custom. Useful for pulling a record up in the field-diff/visualisation
    views to just look at it, with nothing to compare it against.

    This can be a much longer list than :func:`list_subset_singles` -- the
    base masters alone define enormous numbers of records -- which is exactly
    why it's a separate, deliberately-not-default function: callers gate it
    behind its own opt-in control rather than fetching it automatically
    alongside a conflict scan.

    Args:
        active_order: Plugin filenames in load order (winner last).
        index: A PluginFileIndex to locate the files on disk.
        subset_names: Your custom plugins, EXCLUDED here. Can be empty --
            then this returns every plugin's single-contributor records.
        session: An optional Tes3ConvSession; same meaning as in
            :func:`detect_conflicts`.

    Returns:
        ``(records, stats)`` -- see :func:`_list_singles`. ``involves_subset``
        is always ``False`` here.
    """
    return _list_singles(active_order, index, subset_names, session, want_subset=False)


def format_conflict_report(
    conflicts: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any],
    subset_only: bool = False,
    limit: int | None = None,
) -> str:
    """Render conflicts as a readable text report.

    subset_only shows just the ones that involve your custom mods; limit caps how many
    are listed.
    """
    shown = [c for c in conflicts if c["involves_subset"] or not subset_only]
    lines = []
    n_sub = sum(1 for c in conflicts if c["involves_subset"])
    lines.append(
        f"Scanned {stats['scanned']} plugin(s), {stats['records']} record(s): "
        f"{stats['conflicts']} conflicting record(s), {n_sub} involving your custom mods."
    )
    if stats["unreadable"]:
        lines.append(
            f"NOTE: {len(stats['unreadable'])} plugin(s) could not be read "
            f"(not found on disk / unreadable): {', '.join(stats['unreadable'][:8])}"
            + (" ..." if len(stats["unreadable"]) > 8 else "")
        )
    capped = shown if not limit else shown[:limit]
    for c in capped:
        star = "* " if c["involves_subset"] else "  "
        lines.append(f"{star}[{c['type']}] {c['id']}")
        lines.append(f"      {'  ->  '.join(c['plugins'])}   (wins: {c['winner']})")
        if c["deleted_by"]:
            lines.append(f"      deleted by: {', '.join(c['deleted_by'])}")
    if limit and len(shown) > limit:
        lines.append(
            f"  ... and {len(shown) - limit} more (raise the limit or save the full report)."
        )
    if not capped:
        lines.append("  No conflicts to show.")
    return "\n".join(lines)


def write_conflict_csv(path: str | Path, conflicts: Sequence[Mapping[str, Any]]) -> None:
    """Write the full conflict list to a CSV.

    Columns are (type, id, winner, involves_custom,
    deleted_by, plugins-in-load-order).
    """
    import csv

    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "record_type",
                "record_id",
                "winner",
                "involves_custom",
                "deleted_by",
                "plugins_load_order",
            ]
        )
        for c in conflicts:
            w.writerow(
                [
                    c["type"],
                    c["id"],
                    c["winner"],
                    "yes" if c["involves_subset"] else "no",
                    "; ".join(c["deleted_by"]),
                    " -> ".join(c["plugins"]),
                ]
            )


def filter_plugins(
    active_order: Sequence[str], patterns: Sequence[str] | None
) -> tuple[list[str], list[str]]:
    """Return (kept, excluded) after filtering plugin names by glob.

    Matching is case-insensitive
    patterns (fnmatch); a pattern with no glob chars also matches as a substring.
    Lets you drop 'touches-everything' mods (light fixes, groundcover/grass
    generators, delta/merged patches) from a conflict/cell scan.
    """
    pats = [p.strip().lower() for p in (patterns or []) if p and p.strip()]
    if not pats:
        return list(active_order), []
    kept: list[str] = []
    excl: list[str] = []
    for name in active_order:
        low = name.lower()
        hit = any(
            fnmatch.fnmatch(low, p) or (("*" not in p and "?" not in p) and p in low) for p in pats
        )
        (excl if hit else kept).append(name)
    return kept, excl


def dump_tes3conv_json(
    session: Tes3ConvSession | None,
    plugins: Sequence[str],
    paths: Mapping[str, str],
    outdir: str | Path,
) -> int:
    """Write each plugin's tes3conv JSON to a folder.

    Read back from the session's on-disk
    spool) to outdir/<plugin>.json. Returns the number written. Creates outdir if
    needed. Needs a Tes3ConvSession.
    """
    import json

    if session is None:
        return 0
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # Convert everything in parallel first: prime() runs the tes3conv processes
    # concurrently (bounded), so the per-plugin loop below only reads fresh JSON
    # instead of waiting on one conversion at a time.
    session.prime([path for p in plugins if (path := paths.get(p))])
    n = 0
    for p in plugins:
        path = paths.get(p)
        if not path:
            continue
        try:
            recs = session._records(path)
            (outdir / (Path(p).stem + ".json")).write_text(
                json.dumps(recs, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
            )
            n += 1
        except OSError:
            continue
    return n


# ---------------------------------------------------------------------------
# data-path resource (VFS) conflicts: the same loose file (relative path) living
# in two or more data= folders. In OpenMW's VFS the LATER data= folder wins, so
# these overlaps are decided by data-path order -- reorder the data panel to
# change the winner. This is what MO2's "Data" conflicts show for a mod list.
# ---------------------------------------------------------------------------


def detect_resource_conflicts(
    data_dirs: Sequence[str],
    subset_dirs: Sequence[str] | None = None,
    exclude_exts: Collection[str] | None = None,
    compare_contents: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Detect loose-file (VFS) conflicts across the data folders.

    A path present in two or more data folders is a *candidate*; whether it is
    a conflict anyone has to think about depends on whether the bytes differ.
    Mods routinely re-ship assets they did not change -- an unedited vanilla
    texture, an asset a patch simply re-includes -- and reporting those beside
    genuine overrides is how a list of thousands stops being read at all. Each
    entry therefore carries ``identical``, and the differing ones sort first.

    Args:
        data_dirs: The ``data=`` folders in load order, winner last.
        subset_dirs: The folders belonging to the user's own mods, used to flag
            conflicts that involve them.
        exclude_exts: Extensions to skip entirely.
        compare_contents: Whether to compare bytes. Left on by default because
            the size check makes it cheap, but exposed so a scan over a slow or
            network filesystem can skip it; entries then carry no ``identical``
            key rather than a guess.

    Returns:
        ``(conflicts, stats)``. Each conflict is
        ``{path, providers, winner, involves_subset[, identical]}`` for every
        relative path present in 2+ folders. Plugin files are skipped: they are
        ordered by ``content=``, not by the VFS.
    """
    subset_norm = {str(s).replace("\\", "/").rstrip("/").lower() for s in (subset_dirs or [])}
    exclude_exts = {e.lower() for e in (exclude_exts or [])}
    providers: dict[str, list[int]] = {}  # rel_path -> [dir_index in order]
    dirs = []
    for d in data_dirs:
        try:
            p = Path(d)
            if not p.is_dir():
                continue
        except OSError:
            continue
        dirs.append(str(d))
        di = len(dirs) - 1
        try:
            for root, _sub, files in os.walk(p):
                for fn in files:
                    ext = Path(fn).suffix.lower()
                    if ext in PLUGIN_EXTS or ext in exclude_exts:
                        continue
                    # os.path.relpath has no pathlib equivalent that tolerates
                    # a non-subpath (Path.relative_to raises), so the join stays
                    # os.path too rather than mixing idioms mid-expression.
                    joined = os.path.join(root, fn)  # noqa: PTH118
                    rel = os.path.relpath(joined, p).replace("\\", "/").lower()
                    lst = providers.get(rel)
                    if lst is None:
                        providers[rel] = [di]
                    elif lst[-1] != di:
                        lst.append(di)
        except (OSError, PermissionError):
            continue
    conflicts = []
    for rel, idxs in providers.items():
        if len(idxs) < 2:
            continue
        prov = [dirs[i] for i in idxs]
        involves = any(pv.replace("\\", "/").rstrip("/").lower() in subset_norm for pv in prov)
        entry: dict[str, Any] = {
            "path": rel,
            "providers": prov,
            "winner": prov[-1],
            "involves_subset": involves,
        }
        if compare_contents:
            entry["identical"] = _providers_are_identical(rel, prov)
        conflicts.append(entry)
    # Files that genuinely differ first: a path present twice with the same
    # bytes is not a decision anybody has to make, and burying the real
    # overrides among them is how a list of thousands stops being read.
    conflicts.sort(key=lambda c: (bool(c.get("identical")), not c["involves_subset"], c["path"]))
    identical = sum(1 for c in conflicts if c.get("identical"))
    return conflicts, {
        "dirs": len(dirs),
        "files": len(providers),
        "conflicts": len(conflicts),
        "identical": identical,
        "differing": len(conflicts) - identical,
    }


def _providers_are_identical(rel: str, provider_dirs: Sequence[str]) -> bool:
    """Whether every provider ships byte-identical content for one path.

    Sizes are compared first and the hash is only reached when they agree, which
    is what makes this affordable: a re-shipped asset and a real override almost
    always differ in length, so the expensive path runs on the minority.

    A file that cannot be read counts as *not* identical. Claiming two files
    match when one of them could not be opened would quietly retire a genuine
    conflict from the list, and an unreadable file is itself worth surfacing.

    Args:
        rel: The path relative to each data folder.
        provider_dirs: The data folders providing it.

    Returns:
        ``True`` when all providers hold the same bytes.
    """
    import hashlib

    sizes: set[int] = set()
    paths = [Path(d) / rel for d in provider_dirs]
    for candidate in paths:
        try:
            sizes.add(candidate.stat().st_size)
        except OSError:
            return False
        if len(sizes) > 1:
            return False
    digests: set[str] = set()
    for candidate in paths:
        # blake2b rather than a cryptographic-strength choice: this compares
        # files that already have the same length and the same name, so the
        # question is accidental collision, not an adversary.
        digest = hashlib.blake2b(digest_size=16)
        try:
            with candidate.open("rb") as fh:
                # Chunked: a data folder can hold a 200 MB texture pack and
                # reading one whole into memory per provider is avoidable.
                while chunk := fh.read(1 << 20):
                    digest.update(chunk)
        except OSError:
            return False
        digests.add(digest.hexdigest())
        if len(digests) > 1:
            return False
    return True


#: Below this ratio of winner triangles to loser triangles, the winning mesh is
#: simple enough that the swap is worth mentioning. A retexture that also
#: replaces the mesh with a low-poly stand-in is the case this catches.
_MESH_SIMPLER_RATIO = 0.5

#: Logger for the mesh analysis pass, separate so it can be turned up without
#: turning up the whole sorter.
LOG_MESH = get_logger("mesh")


def analyse_mesh_conflicts(
    conflicts: Sequence[MutableMapping[str, Any]],
    limit: int = 0,
) -> dict[str, int]:
    """Read the meshes behind conflicts and record what the winner changes.

    Only paths that are meshes **and** already known to differ are opened. That
    is the whole reason this is affordable: parsing runs at roughly 120 files a
    second, so opening every mesh in a large setup would cost minutes, while
    opening only the contested ones costs a second or two. Identical providers
    are skipped because there is nothing to compare -- the bytes are the same.

    Findings are attached to the conflict entries in place under ``"mesh"``.
    Absent means "not looked at", which is deliberately distinct from "looked
    at and found nothing".

    Args:
        conflicts: The scan's conflicts, modified in place.
        limit: Stop after analysing this many meshes; 0 for no limit. A guard
            for pathological setups, not an expected path.

    Returns:
        Counters: how many were analysed, how many had something worth saying,
        and how many could not be read.
    """
    analyser = MeshAnalyser()
    stats = {"analysed": 0, "findings": 0, "unreadable": 0}
    for entry in conflicts:
        path = str(entry.get("path", ""))
        if not path.lower().endswith(".nif"):
            continue
        if entry.get("identical"):
            continue
        if limit and stats["analysed"] >= limit:
            break
        winner_dir = str(entry["winner"])
        winner = Path(winner_dir) / path
        # The losers are every provider that is not the winner, found by
        # comparing against the declared winner rather than by assuming it is
        # last. The two happen to coincide today; taking the position instead
        # of the value couples this to a detail of the scan that nothing
        # promises, and gets the comparison backwards if it ever changes.
        losses: list[MeshFinding] = [
            analyser.compare_providers(path, Path(str(provider)) / path, winner)
            for provider in entry["providers"]
            if str(provider) != winner_dir
        ]
        if not losses:
            continue
        stats["analysed"] += 1
        # The worst finding wins the summary. A mesh overridden by three mods
        # is one decision, not three, and the reason to look is whatever the
        # winner loses against *any* of them.
        reportable = next((f for f in losses if f.worth_reporting), None)
        chosen = reportable or next((f for f in losses if f.reliable), None) or losses[0]
        if chosen.unreadable:
            stats["unreadable"] += 1
        if reportable is not None:
            stats["findings"] += 1
        entry["mesh"] = chosen
    LOG_MESH.debug(
        "analysed %d mesh conflict(s): %d finding(s), %d unreadable, %d parse(s), %d cache hit(s)",
        stats["analysed"],
        stats["findings"],
        stats["unreadable"],
        analyser.parsed,
        analyser.cache_hits,
    )
    return stats


def describe_mesh_finding(finding: MeshFinding) -> str:
    """Say what a mesh conflict costs, in one line, or nothing at all.

    Only *losses* are named. A winner that adds detail is not a problem anybody
    needs to be told about, and a report that lists every difference equally is
    one nobody reads.

    Args:
        finding: The comparison result.

    Returns:
        A short description, or ``""`` when there is nothing worth saying.
    """
    if finding.unreadable:
        return _("could not read the mesh: %(reason)s") % {"reason": finding.unreadable}
    if not finding.reliable or finding.difference is None:
        # Silence rather than a caveat. A partial read cannot prove an absence,
        # so it has nothing to report, and saying "possibly lost collision"
        # would be worse than saying nothing.
        return ""
    parts: list[str] = []
    difference = finding.difference
    if difference.lost_collision:
        parts.append(_("loses collision"))
    if difference.lost_animation:
        parts.append(_("loses animation"))
    if difference.added_textures:
        parts.append(
            ngettext(
                "needs %(count)d texture the other does not ship",
                "needs %(count)d textures the other does not ship",
                len(difference.added_textures),
            )
            % {"count": len(difference.added_textures)}
        )
    ratio = difference.triangle_ratio
    if ratio is not None and ratio < _MESH_SIMPLER_RATIO:
        parts.append(_("%(percent)d%% of the triangles") % {"percent": round(ratio * 100)})
    return ", ".join(parts)


def describe_mesh_detail(
    analyser: MeshAnalyser,
    conflict: Mapping[str, Any],
) -> list[str]:
    """Describe every provider of one mesh, reading them on demand.

    This is the *selected a row* path, not the scan path. Nothing is parsed
    until a user asks about a specific file, which is why it can afford to
    describe every provider rather than only the winner: one mesh is
    milliseconds, and the largest in the corpus is under two seconds.

    Args:
        analyser: The cache to read through, so reselecting a row is free.
        conflict: One conflict entry.

    Returns:
        Lines for a detail panel, empty when the path is not a mesh.
    """
    path = str(conflict.get("path", ""))
    if not path.lower().endswith(".nif"):
        return []
    providers = [str(p) for p in conflict.get("providers", [])]
    if not providers:
        return []
    lines = [_("Mesh contents (read just now):")]
    structures: list[Structure | None] = []
    for index, provider in enumerate(providers, start=1):
        outcome = analyser.structure(Path(provider) / path)
        if isinstance(outcome, str):
            structures.append(None)
            lines.append(
                _("  %(n)d. %(dir)s - could not read: %(why)s")
                % {"n": index, "dir": provider, "why": outcome}
            )
            continue
        structures.append(outcome)
        traits = [
            ngettext("%(count)d shape", "%(count)d shapes", len(outcome.shapes))
            % {"count": len(outcome.shapes)},
            ngettext("%(count)d triangle", "%(count)d triangles", outcome.total_triangles)
            % {"count": outcome.total_triangles},
            ngettext("%(count)d texture", "%(count)d textures", len(outcome.textures))
            % {"count": len(outcome.textures)},
        ]
        # Presence is stated; absence only when the read was complete, because
        # a partial parse cannot prove a node is not there.
        if outcome.has_collision:
            traits.append(_("collision"))
        elif not outcome.partial:
            traits.append(_("no collision"))
        if outcome.has_animation:
            traits.append(_("animated"))
        if outcome.partial:
            traits.append(
                _("PARTIAL: %(read)d of %(declared)d blocks")
                % {"read": outcome.blocks_read, "declared": outcome.blocks_declared}
            )
        lines.append(f"  {index}. {provider} - " + ", ".join(traits))
    winner_dir = str(conflict.get("winner", providers[-1]))
    try:
        winner = structures[providers.index(winner_dir)]
    except ValueError:
        # The declared winner is not among the providers, which is a bug in
        # whoever built the entry. Reporting the contents without a comparison
        # is more useful than guessing which one won.
        return lines
    for index, (provider, loser) in enumerate(zip(providers, structures), start=1):
        if loser is None or winner is None or provider == winner_dir:
            continue
        note = describe_mesh_finding(
            MeshFinding(
                path,
                difference=compare_structures(loser, winner),
                loser_partial=loser.partial,
                winner_partial=winner.partial,
            )
        )
        if note:
            lines.append(
                _("  Against provider %(n)d, the winner %(note)s.") % {"n": index, "note": note}
            )
    for structure in structures:
        if structure is not None and structure.textures:
            lines.append(
                _("  Textures referenced: %(list)s") % {"list": ", ".join(structure.textures[:8])}
            )
            break
    return lines


def _mesh_note(conflict: Mapping[str, Any]) -> str:
    """The one-line mesh finding for a conflict, or ``""``.

    Args:
        conflict: One conflict entry.

    Returns:
        The description, empty when the mesh was not analysed or had nothing
        worth saying.
    """
    finding = conflict.get("mesh")
    return describe_mesh_finding(finding) if isinstance(finding, MeshFinding) else ""


def format_resource_report(
    conflicts: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any],
    subset_only: bool = False,
    limit: int = 200,
) -> str:
    """Render the loose-file conflict list as a readable report.

    Args:
        conflicts: The scan's conflicts.
        stats: The scan's counters.
        subset_only: Show only conflicts involving the user's own data paths.
        limit: Maximum rows to list; 0 for all.

    Returns:
        The report text.
    """
    shown = [c for c in conflicts if c["involves_subset"] or not subset_only]
    n_sub = sum(1 for c in conflicts if c["involves_subset"])
    lines = [
        f"Scanned {stats['dirs']} data folder(s), {stats['files']} loose file(s): "
        f"{stats['conflicts']} conflicting file(s), {n_sub} involving your custom data paths."
    ]
    if stats.get("identical"):
        # Said as a count rather than by hiding them: "3,912 of these are the
        # same file shipped twice" is the sentence that makes the rest legible,
        # and a reader who wants them can still scroll.
        lines.append(
            f"  {stats['differing']} differ; {stats['identical']} are byte-identical "
            f"across every provider and need no decision."
        )
    flagged = sum(1 for c in conflicts if _mesh_note(c))
    if flagged:
        lines.append(
            ngettext(
                "  %(count)d mesh conflict changes what the asset does; "
                "those lines are marked below.",
                "  %(count)d mesh conflicts change what the asset does; "
                "those lines are marked below.",
                flagged,
            )
            % {"count": flagged}
        )
    for c in (shown[:limit] if limit else shown):
        star = "* " if c["involves_subset"] else "  "
        same = "  [identical]" if c.get("identical") else ""
        lines.append(
            f"{star}{c['path']}   ({len(c['providers'])} providers, wins: {c['winner']}){same}"
        )
        note = _mesh_note(c)
        if note:
            # Indented under its file rather than as a column: the finding is
            # a sentence, and squeezing it onto the path line pushes the path
            # off the screen in exactly the cases worth reading.
            lines.append(f"      -> {note}")
    if limit and len(shown) > limit:
        lines.append(f"  ... and {len(shown) - limit} more (save the full report).")
    return "\n".join(lines)


def _mesh_columns(conflict: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """The mesh columns for one CSV row.

    Every column is blank when the mesh was not analysed or the read was not
    reliable. Blank means "not established", which a spreadsheet filter treats
    differently from "no" -- and writing "no" for a mesh nobody could read
    would be a claim the scan cannot support.

    Args:
        conflict: One conflict entry.

    Returns:
        Note, lost-collision, lost-animation and triangle-ratio cells.
    """
    finding = conflict.get("mesh")
    if not isinstance(finding, MeshFinding):
        return ("", "", "", "")
    note = describe_mesh_finding(finding)
    if not finding.reliable or finding.difference is None:
        return (note, "", "", "")
    ratio = finding.difference.triangle_ratio
    return (
        note,
        "yes" if finding.difference.lost_collision else "no",
        "yes" if finding.difference.lost_animation else "no",
        "" if ratio is None else f"{ratio:.3f}",
    )


def write_resource_csv(path: str | Path, conflicts: Sequence[Mapping[str, Any]]) -> None:
    """Write the loose-file conflict list to a CSV.

    Args:
        path: Where to write.
        conflicts: The scan's conflicts.
    """
    import csv

    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "file_path",
                "providers",
                "winner",
                "involves_custom",
                "identical",
                "provider_folders",
                "mesh_note",
                "mesh_lost_collision",
                "mesh_lost_animation",
                "mesh_triangle_ratio",
            ]
        )
        for c in conflicts:
            # "" rather than "no" when the scan did not compare: a spreadsheet
            # filter on "no" must not sweep up files nobody looked at.
            identical = c.get("identical")
            w.writerow(
                [
                    c["path"],
                    len(c["providers"]),
                    c["winner"],
                    "yes" if c["involves_subset"] else "no",
                    "" if identical is None else ("yes" if identical else "no"),
                    " -> ".join(c["providers"]),
                    *_mesh_columns(c),
                ]
            )


# ---------------------------------------------------------------------------
# cell coverage map ("modmapper"): which mods touch which cells. Exterior cells
# are keyed by grid coords (for a heatmap), interior by name (for a list). Reads
# via either engine; interior/exterior is told apart the way modmapper does
# (interior = the cell's flags bit 0x01, or "INTERIOR" in the flags).
# ---------------------------------------------------------------------------


def _iter_cells(
    path: str | Path, session: Tes3ConvSession | None = None
) -> Iterator[Sequence[Any]]:
    """Yield ('ext', gx, gy) or ('int', name, None) for each CELL in a plugin."""
    if session is not None:
        # session.cells() is sidecar-cached, so map rebuilds skip the big JSON.
        yield from session.cells(path)
    else:
        for rtype, rid, _deleted in parse_tes3_records(path):
            if rtype != "CELL":
                continue
            mm = re.match(r"^Exterior \((-?\d+), (-?\d+)\)", rid)
            if mm:
                yield ("ext", int(mm.group(1)), int(mm.group(2)))
            else:
                # A CELL's rid from _tes3_record_key is always one of these
                # two shapes (see its CELL branch); the interior case is the
                # only one left once the exterior regex hasn't matched.
                yield ("int", rid[len("Interior: ") :], None)


def build_cell_coverage(
    active_order: Sequence[str],
    index: PluginFileIndex,
    subset_names: Sequence[str] | None = None,
    session: Tes3ConvSession | None = None,
) -> dict[str, Any]:
    """Build the per-cell coverage map for the active order.

    Returns {"exterior":

    {(gx,gy):[mods]}, "interior": {name:[mods]}, "scanned", "unreadable":[...],
    "subset_lower": set}. Mods are in load order.
    """
    subset_lower = {s.lower() for s in (subset_names or [])}
    ext: dict[tuple[int, int], list[str]] = {}
    inte: dict[str, list[str]] = {}
    unreadable: list[str] = []
    scanned = 0
    for plugin in active_order:
        path = index.find(plugin) if index else None
        if path is None:
            unreadable.append(plugin)
            continue
        scanned += 1
        se, si = set(), set()
        for cell in _iter_cells(path, session):
            if cell[0] == "ext":
                key = (cell[1], cell[2])
                if key not in se:
                    se.add(key)
                    ext.setdefault(key, []).append(plugin)
            else:
                nm = cell[1]
                if nm not in si:
                    si.add(nm)
                    inte.setdefault(nm, []).append(plugin)
    return {
        "exterior": ext,
        "interior": inte,
        "scanned": scanned,
        "unreadable": unreadable,
        "subset_lower": subset_lower,
    }


def _html_escape(s: object) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_cell_map_html(
    coverage: Mapping[str, Any],
    title: str = "Wraithguard Toolkit - Cell Map",
) -> str:
    """Render the cell map as a self-contained HTML page.

    The rendering itself lives in :mod:`wraithguard.viz.cellmap` -- 216 lines of
    HTML, CSS and JavaScript had no business sitting in the sort engine, and as
    one f-string it could not be tested in pieces. This stays as the engine's
    public entry point so existing callers and the CLI are unaffected.

    Args:
        coverage: The result of :func:`build_cell_coverage`.
        title: The page title.

    Returns:
        A complete, self-contained HTML document.
    """
    from wraithguard.viz.cellmap import generate_cell_map_html as _render

    html = _render(coverage, title)
    trace(f"cell map: rendered {len(html)} bytes via viz.cellmap")
    return html


# ---------------------------------------------------------------------------
# openmw.cfg handling
# ---------------------------------------------------------------------------

#: openmw.cfg is normally UTF-8, but it can legitimately contain bytes that
#: are not (a cp1252 accented mod folder, a hand edit in Notepad). Decoding
#: with ``surrogateescape`` maps those bytes to lone surrogates and encoding
#: with it restores them exactly, so a read/write round-trip is byte-
#: preserving. ``errors="replace"`` used to destroy them permanently, breaking
#: the user's data= path.
#: Largest exterior cell coordinate treated as real. Beyond this a plugin is
#: almost certainly corrupt, and plotting it would stretch the map to nothing.
CELL_GRID_LIMIT = 4096

#: Cell-map geometry: a 12px square on a 13px pitch, leaving a 1px gutter.
CELL_MAP_CELL_PX = 12
CELL_MAP_STEP_PX = 13

CFG_READ_ENCODING = "utf-8-sig"
CFG_WRITE_ENCODING = "utf-8"
CFG_ERRORS = "surrogateescape"


def read_user_text(path: Path, encoding: str = "utf-8-sig") -> str:
    """Read a user-supplied text file without losing undecodable bytes.

    Plugin names and mod paths can contain bytes that are not valid UTF-8
    (a cp1252 accented folder, a hand-edited file). ``surrogateescape``
    preserves them, which also matches how Python decodes filenames from the
    filesystem -- so a name read here still compares equal to the same name
    listed from disk.
    """
    return Path(path).read_text(encoding=encoding, errors=CFG_ERRORS)


def read_toml_text(path: Path) -> str:
    """Read a TOML file, which the spec requires to be UTF-8.

    Raises:
        SystemExit: with an actionable message if it is not valid UTF-8,
            rather than surfacing a raw UnicodeDecodeError.

    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"{path} is not valid UTF-8 (byte 0x{exc.object[exc.start]:02x} at "
            f"position {exc.start}). TOML files must be UTF-8 -- re-save the "
            f"file as UTF-8 in your editor."
        ) from exc


def read_cfg(
    path: Path,
) -> tuple[list[str], list[int], list[tuple[str, str]], list[int], list[str]]:
    """Read openmw.cfg into its lines plus the content= / data= segments."""
    lines = path.read_text(encoding=CFG_READ_ENCODING, errors=CFG_ERRORS).splitlines()
    content_positions, content_order = [], []
    data_positions, data_order = [], []
    for i, line in enumerate(lines):
        m = re.match(r"^\s*content\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
        if m:
            content_positions.append(i)
            content_order.append((m.group(1), line))
            continue
        m = re.match(r"^\s*data\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
        if m:
            data_positions.append(i)
            data_order.append(line)
    return lines, content_positions, content_order, data_positions, data_order


def read_groundcover_names(lines: Sequence[str]) -> list[str]:
    """Read the plugins declared on ``groundcover=`` lines.

    Grass plugins are declared to OpenMW on their own ``groundcover=`` lines,
    never as ``content=``: the engine loads them through the groundcover system,
    which instances the grass rather than spawning every blade as a real object.
    Declaring one as ``content=`` as well loads it *both* ways, and the object
    copy is a severe, silent performance cliff.

    Read separately from :func:`read_cfg` rather than added to its return value,
    which would change a five-value signature that several callers unpack
    positionally. The lines are already in hand, so there is nothing to re-read.

    Args:
        lines: The cfg's lines, as :func:`read_cfg` returned them.

    Returns:
        The plugin filenames, in file order. Commented lines are skipped, the
        same way they are for ``content=``.
    """
    found: list[str] = []
    for line in lines:
        match = re.match(r"^\s*groundcover\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
        if match:
            name = basename_if_plugin(match.group(1)) or match.group(1).strip().strip("\"'")
            if name:
                found.append(name)
    return found


def hold_back_groundcover(
    subset: Sequence[str], groundcover_names: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Drop plugins the cfg already declares as groundcover from the subset.

    A folder scan cannot tell a grass mod from any other mod -- it walks a
    directory and takes every plugin it finds -- so a shared mods folder puts
    grass plugins into the subset alongside genuinely new content. Inserting
    those as ``content=`` is what this prevents.

    The decision is made on **what the user's own cfg says**, not on the
    filename: a plugin is held back only if that same cfg declares it on a
    ``groundcover=`` line. Nothing is guessed from names like ``*grass*``, and
    the existing ``groundcover=`` lines are never touched -- the mod stays
    enabled as grass, which is what it was.

    Its *data path* is deliberately still emitted. OpenMW has to be able to find
    the file for the ``groundcover=`` line to work, so removing the data= entry
    would break the very mod this is protecting.

    Args:
        subset: The plugin filenames destined for insertion.
        groundcover_names: Plugins declared as groundcover in the cfg.

    Returns:
        The subset to use, and the names held back (in the order they appeared
        in the subset), so the caller can say what it did and why.
    """
    blocked = {str(name).lower() for name in groundcover_names}
    if not blocked:
        return list(subset), []
    kept = [name for name in subset if str(name).lower() not in blocked]
    held = [name for name in subset if str(name).lower() in blocked]
    return kept, held


def plugins_needing_removal(
    disabled: Collection[str],
    curated_set: Collection[str],
    base_order_names: Sequence[str],
) -> list[str]:
    """Decide which opted-out plugins need a durable ``removeContent``.

    A removal is only needed for something this tool does **not** put in the
    file itself. momw-configurator rebuilds openmw.cfg from the curated list
    plus these customizations, so a plugin we simply stop inserting is already
    absent from the result; emitting a ``removeContent`` for it as well is noise
    in a file people hand-edit.

    The discriminator is therefore "does the curated list own this plugin", not
    "is it currently in openmw.cfg". The latter was the old test, and it catches
    the user's own mods the moment they have exported once -- which is what made
    disabling a custom mod add a block that did nothing.

    Args:
        disabled: Plugins the user opted out of.
        curated_set: Lower-cased plugins the curated list owns. **Empty means
            unknown**, not "nothing is curated": without a ``plugin-order.yml``
            there is no curated set at all.
        base_order_names: Plugins already in openmw.cfg, used as the fallback
            signal when the curated set is unknown. Guessing wrong in that
            direction would leave a plugin enabled that the user asked to
            disable, so presence is preferred over silence there.

    Returns:
        The plugin names to emit, sorted.
    """
    curated_lower = {str(name).lower() for name in curated_set}
    base_lower = {str(name).lower() for name in base_order_names}
    owned_elsewhere = curated_lower or base_lower
    return sorted({p for p in disabled if str(p).lower() in owned_elsewhere})


def data_paths_needing_removal(
    disabled_data: Iterable[str],
    our_values: Collection[str],
    base_data_norms: Collection[str],
) -> list[str]:
    """Decide which opted-out data paths need a durable ``removeData``.

    The plugin rule (:func:`plugins_needing_removal`) applied to folders. A path
    this run would have inserted is one we can simply stop inserting: the
    Configurator rebuilds from the curated list plus these customizations, so
    omitting it is enough and a ``removeData`` would be noise.

    A path we do **not** own -- one the curated list brings in -- still needs the
    removal, because nothing else will take it out.

    Args:
        disabled_data: Raw ``data=...`` lines the user opted out of.
        our_values: Normalised paths this run inserts.
        base_data_norms: Normalised paths already in openmw.cfg.

    Returns:
        The path values to emit, sorted and de-duplicated.
    """
    removals: list[str] = []
    for line in disabled_data:
        value = extract_data_path_value(line) or line
        norm = normalize_data_path(value)
        if norm in base_data_norms and norm not in our_values:
            removals.append(value)
    return sorted(set(removals))


def backup_file(path: Path, no_backup: bool) -> None:
    """Write a timestamped .bak-YYYYMMDD-HHMMSS copy of an existing file.

    Made
    before it gets overwritten. No-op if no_backup, or if the file doesn't
    exist yet (nothing to back up).
    """
    if no_backup or not path.exists():
        return
    # Local clock: goes into a .bak filename the user reads.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005
    backup = path.with_suffix(path.suffix + f".bak-{stamp}")
    # Copy BYTES, not decoded text: a backup must be byte-identical. Decoding
    # and re-encoding used to raise UnicodeDecodeError on a cfg containing
    # non-UTF-8 bytes (e.g. a cp1252 accented mod folder), which meant the
    # user could not back up -- or export -- at all.
    backup.write_bytes(path.read_bytes())
    print(_("Backup written: %(path)s") % {"path": backup})


def write_cfg(
    path: Path,
    lines: Sequence[str],
    segments: Sequence[tuple[Sequence[int], Sequence[str]]],
    dry_run: bool,
    no_backup: bool,
) -> None:
    """Write the cfg back with the rebuilt content= / data= segments.

    segments:

    list of (positions, new_lines) pairs. Each segment's block of original lines gets
    replaced (at the position of its first line) with new_lines; other lines are left
    completely untouched.
    """
    replace_at: dict[int, Sequence[str]] = {}
    skip: set[int] = set()
    trailing_extra: list[str] = []
    for positions, new_lines in segments:
        if not positions:
            # no anchor lines of this kind existed in the file at all --
            # tack the new lines on at the end instead of silently dropping them
            trailing_extra.extend(new_lines)
            continue
        replace_at[positions[0]] = new_lines
        skip.update(positions)

    new_lines_out: list[str] = []
    for i, line in enumerate(lines):
        if i in replace_at:
            new_lines_out.extend(replace_at[i])
        if i in skip:
            continue
        new_lines_out.append(line)
    new_lines_out.extend(trailing_extra)

    if dry_run:
        print(_("\n--- DRY RUN: no files written ---"))
        return

    backup_file(path, no_backup)
    # newline="" disables the text-mode newline translation that would rewrite
    # every "\n" to "\r\n" on Windows -- read_cfg already stripped line endings
    # via splitlines(), so the join is authoritative and must reach disk byte
    # for byte. Without this, writing back a cfg on Windows silently changed its
    # line endings (and broke the byte-preservation contract this promises).
    path.write_text(
        "\n".join(new_lines_out) + "\n",
        encoding=CFG_WRITE_ENCODING,
        errors=CFG_ERRORS,
        newline="",
    )
    print(_("Wrote updated: %(path)s") % {"path": path})


# ---------------------------------------------------------------------------
# mlox rule parsing (Order / NearStart / NearEnd only)
# ---------------------------------------------------------------------------

# Rule-file parsing now lives in wraithguard/rules/parser.py. Behaviour pinned
# by tests/test_differential.py; callers import it from there directly.
from wraithguard.configurator import (
    curated_covers,
    extract_data_path_value,
    generate_customizations_toml,
    infer_data_path_anchors,
    insert_data_paths,
    normalize_data_path,
    orphan_cfg_entries,
    preview_configurator_result,
)
from wraithguard.momw import (
    base_order_matches_yml,
    curated_for_list,
    needs_cleaning_set,
    parse_plugin_order_yml,
)
from wraithguard.momw_datapaths import (
    managed_cfg_data_path_norms,
    parse_data_path_order_yml,
    reconcile_list_against_cfg,
)
from wraithguard.plugins import PLUGIN_EXTS, PluginFileIndex
from wraithguard.rules import (
    check_predicates,
    load_rule_blocks,
    load_rules_raw_text,
)

# ---------------------------------------------------------------------------
# mlox predicate evaluation (Requires / Conflict / Note) -- read-only,
# reported as warnings after sorting. This is a best-effort reimplementation
# of mlox's tiny lisp-like logic language (ALL/ANY/NOT/DESC), not the real
# mlox engine -- good enough to flag likely problems, not to be trusted blindly.
# ---------------------------------------------------------------------------


# --- [VER]/[SIZE]/[DESC]/[MWSE-LUA] function-token evaluation ---------------


# ---------------------------------------------------------------------------
# plugin-order.yml -- MOMW's source of truth for which plugins belong to which
# curated mod list (and their canonical order). Used, when a --list-name is
# given, to tell the curated base list apart from YOUR custom additions:
#   * curated plugins are excluded from the sort (never reordered -- they're
#     the list's job, not ours) and never highlighted as custom
#   * everything else is a true custom addition
# plus read-only sanity warnings (redundant / orphan / needs-cleaning) and a
# base-order drift check against the yml's canonical order for the list. All of
# this is opt-in and additive: with no --plugin-order-yml, behavior is exactly
# as before.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# mod folder scan -- generate a subset file directly from a mods directory
# (folded in from the standalone mod_scan.py). Walks a folder tree and, for
# each directory that looks like a mod's data folder -- i.e. it directly
# contains a recognized asset subfolder (meshes/textures/scripts/...) OR a
# plugin file (.esp/.esm/.omwaddon/.omwscripts) -- records that folder's path
# and any plugins in it, then stops descending that branch (OpenMW/mlox expect
# plugins directly inside a data= folder, so there's no need to go deeper). The
# result is exactly the mixed "one data path or plugin per line" format that
# extract_subset_from_subset_file already consumes.
# ---------------------------------------------------------------------------

SCAN_ASSET_FOLDERS = frozenset(
    {
        "icons",
        "meshes",
        "scripts",
        "sound",
        "textures",
        "bookart",
        "music",
        "fonts",
        "splash",
        "video",
    }
)


def scan_mod_directories(
    start_path: str | Path, output_path: str | Path | None = None
) -> tuple[list[str], int, int]:
    """Scan start_path for mod data folders.

    Writes the result subset file to output_path if given, and returns (lines,
    n_folders, n_plugins).

        Each matched folder contributes its absolute path (a data= entry) followed
        by the plugin filenames directly inside it (content= entries), then a blank
        line -- so a mod with both assets and a plugin adds its data path AND its
        plugin, while an assets-only mod adds just its data path.
    """
    start_path = str(start_path)
    lines = []
    n_folders = n_plugins = 0
    for root, dirs, files in os.walk(start_path):
        lower_dirs = {d.lower() for d in dirs}
        has_asset_folder = any(f in lower_dirs for f in SCAN_ASSET_FOLDERS)
        plugins = sorted(
            (f for f in files if Path(f).suffix.lower() in PLUGIN_EXTS),
            key=str.lower,
        )
        if has_asset_folder or plugins:
            # abspath normalizes WITHOUT resolving symlinks. Path.resolve()
            # follows them, which would rewrite the displayed path of every
            # MO2 junction / symlinked mod folder -- common in Morrowind
            # setups. Not equivalent, so this stays os.path.
            lines.append(os.path.abspath(root))  # noqa: PTH100
            lines.extend(plugins)
            lines.append("")  # blank separator for readability
            n_folders += 1
            n_plugins += len(plugins)
            dirs[:] = []  # matched -> don't descend further into this branch

    text = "\n".join(lines) + ("\n" if lines else "")
    if output_path is not None:
        Path(output_path).write_text(text, encoding="utf-8")
    # See the PTH100 note above: abspath must not become resolve() here.
    print(
        # Two independent counts share this sentence, so ngettext (which
        # handles one) cannot apply; the "(s)" style is kept deliberately.
        _("Scanned '%(path)s': %(folders)d mod folder(s), %(plugins)d plugin(s).")
        % {
            "path": os.path.abspath(start_path),  # noqa: PTH100
            "folders": n_folders,
            "plugins": n_plugins,
        }
    )
    if output_path is not None:
        print(_("Wrote subset file: %(path)s") % {"path": output_path})
    return lines, n_folders, n_plugins


# ---------------------------------------------------------------------------
# subset extraction
# ---------------------------------------------------------------------------


def basename_if_plugin(value: str) -> str | None:
    """Return the bare filename if ``value`` names a plugin, else None."""
    v = value.strip().strip('"').strip("'")
    v = v.replace("\\", "/")
    name = v.rsplit("/", 1)[-1]
    if name.lower().endswith(PLUGIN_EXTS):
        return name
    return None


def extract_subset_from_subset_file(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Read the subset from a subset file.

    Accepts either:

    - a plain text file, one entry per line (# comments allowed), OR - a minimal TOML
    file like: subset = ["GoHome.esp", "go-home.omwscripts"] data =
    ["mods/SomeModFolder"].

        Plugin filenames (.esp/.esm/etc) and data folder paths can be freely
        mixed in the plain-text form -- each line is classified automatically
        the same way extract_subset_from_toml() classifies TOML insert values:
        a recognized plugin extension makes it a plugin; otherwise, if it
        contains a slash or backslash it's treated as a data= folder path;
        otherwise it's skipped with a warning (nothing safe to guess from a
        bare word with neither).

        Returns (plugin_names, data_inserts) -- data_inserts is
        [{"value","after","before"}] with after/before always None, since this
        format has no anchor syntax. --sort-data-paths can still work out an
        anchor automatically by scanning each folder for plugins and anchoring
        next to their neighbors in the mlox-sorted order (see
        infer_data_path_anchors) -- or leave --sort-data-paths off and they'll
        just get appended at the end, same as any other anchor-less insert.

        Much shorter to maintain than repeating --subset on the CLI or writing
        a full momw-customizations.toml block just to name plugins/paths --
        and, combined with --emit-toml, is enough on its own (no existing
        momw-customizations.toml required) to generate a brand new one.
    """
    text = read_user_text(path, encoding="utf-8")

    if path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
        data = tomllib.loads(text)
        plugins: list[str] = []
        data_inserts: list[dict[str, Any]] = []
        for raw in data.get("subset", []):
            _classify_subset_entry(str(raw), plugins, data_inserts, str(path))
        data_inserts.extend(
            {"value": str(raw), "after": None, "before": None} for raw in data.get("data", [])
        )
        return plugins, data_inserts

    return extract_subset_from_lines(text.splitlines(), source=str(path))


#: ``groundcover=Vurt_Grass.esp`` in a subset file. Deliberately the same
#: spelling openmw.cfg uses, so the line means what it looks like.
_GROUNDCOVER_DECL = re.compile(r"^\s*groundcover\s*=\s*(?P<name>.+?)\s*$", re.IGNORECASE)


def extract_groundcover_declarations(lines: Iterable[str], source: str = "subset") -> list[str]:
    """Read the plugins a subset file declares as grass.

    Grass belongs on a ``groundcover=`` line and nowhere else, but a plugin the
    user has only just installed is not in openmw.cfg yet, so there is nothing
    to read the fact off. Declaring it here says so once, and the declaration
    then drives both outputs: the plugin is kept out of ``content=`` and its
    ``groundcover=`` line is written for it.

    The mod's **data path is a separate line, as it always was** -- a folder
    line in the same subset file. That is deliberate: OpenMW has to find the
    file for the groundcover line to mean anything, and the data path is not
    grass-specific, so it goes through the ordinary data-insert path.

    Args:
        lines: Raw subset-file lines.
        source: Where they came from, for the warning text.

    Returns:
        The declared plugin filenames, in order, de-duplicated case-insensitively.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = _GROUNDCOVER_DECL.match(_strip_line_comment(line))
        if match is None:
            continue
        name = basename_if_plugin(match.group("name")) or match.group("name").strip()
        if not name:
            continue
        if not name.lower().endswith(PLUGIN_EXTS):
            _LOG.warning(
                _(
                    "'%(entry)s' from %(source)s is declared as groundcover but is not a "
                    "plugin filename -- skipping."
                ),
                {"entry": name, "source": source},
            )
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            found.append(name)
    return found


def _classify_subset_entry(
    raw: str, plugins: list[str], data_inserts: list[dict[str, Any]], source: str
) -> None:
    """Classify one raw subset entry as a plugin or a data path.

    Namely:

    a recognized plugin extension makes it a plugin; otherwise a slash/backslash makes
    it a data= folder path; otherwise it's skipped with a warning (nothing safe to guess
    from a bare word).

        Both `plugins` and `data_inserts` are APPENDED TO IN PLACE and nothing is
        returned -- the caller passes the accumulators it wants filled.
    """
    raw = raw.strip()
    if not raw:
        return
    if _GROUNDCOVER_DECL.match(raw):
        # Declared as grass, and read by extract_groundcover_declarations. It
        # must not also become a content plugin -- that is the whole point.
        return
    name = basename_if_plugin(raw)
    if name:
        plugins.append(name)
    elif "/" in raw.replace("\\", "/"):
        data_inserts.append({"value": raw, "after": None, "before": None})
    else:
        _LOG.warning(
            _(
                "'%(entry)s' from %(source)s doesn't look like a plugin filename or a "
                "data folder path (no recognized extension, no slash) -- skipping."
            ),
            {"entry": raw, "source": source},
        )


def _strip_line_comment(line: str) -> str:
    """Strip a '#' comment from a subset-file line.

    Only when the '#' begins
    the line (after optional whitespace) or is preceded by whitespace. A '#'
    that's part of a filename or path -- e.g. 'FMI_#NotAllDunmer.ESP' -- has no
    space in front of it, so it's left intact. (Previously a naive split on '#'
    truncated such names to 'FMI_', which then classified as neither a plugin
    nor a path and got dropped.).
    """
    if line.lstrip().startswith("#"):
        return ""
    m = re.search(r"\s#", line)
    return line[: m.start()] if m else line


def extract_subset_from_lines(
    lines: Iterable[str], source: str = "subset lines"
) -> tuple[list[str], list[dict[str, Any]]]:
    """Classify a list of raw subset text lines.

    One plugin filename or data folder
    path each; a '#' at line start or after whitespace begins a comment) into
    (plugin_names, data_inserts) -- the same plain-text form
    extract_subset_from_subset_file() reads, but from an in-memory list. Used by
    the GUI's 'scan into memory' path so a scan can feed the sort without writing
    a file to disk.
    """
    plugins: list[str] = []
    data_inserts: list[dict[str, Any]] = []
    for line in lines:
        _classify_subset_entry(_strip_line_comment(line), plugins, data_inserts, source)
    return plugins, data_inserts


def extract_subset_from_toml(
    toml_path: Path,
) -> tuple[list[str], list[dict[str, Any]], set[str], dict[str, str]]:
    """Read the customisations TOML into its component parts.

    Returns (content_subset, data_inserts, replace_dest_names):

    content_subset      -- plugin filenames to feed into the mlox sort data_inserts
    -- [{"value","after","before"}] folder paths to anchor directly into the data= list
    (mlox doesn't cover these) replace_dest_names  -- subset of content_subset that came
    from a "replace" block's "dest", not an "insert" -- included in the mlox sort so
    drift can be detected, but must NOT get a synthesized insert block in --emit-toml
    output (see generate_customizations_toml) subset_listnames    -- {plugin_name:
    listName} -- which [[Customizations]] block each subset plugin came from, so
    predicate warnings can point back at the specific mod entry in the TOML that's
    responsible (see check_predicates).
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "Need Python 3.11+ (tomllib) or `pip install tomli --break-system-packages` "
                "to parse the customizations TOML."
            ) from exc

    data = tomllib.loads(read_toml_text(toml_path))
    subset = []
    data_inserts = []
    replace_dest_names: set[str] = set()
    subset_listnames = {}

    def handle_insert(
        value: str,
        listname: str | None,
        after: str | None = None,
        before: str | None = None,
        is_replace: bool = False,
    ) -> None:
        if not isinstance(value, str) or not value:
            return
        name = basename_if_plugin(value)
        if name:
            subset.append(name)
            if is_replace:
                replace_dest_names.add(name)
            if listname:
                subset_listnames[name] = listname
        elif "/" in value.replace("\\", "/"):
            data_inserts.append({"value": value, "after": after, "before": before})

    for block in data.get("Customizations", []):
        listname = block.get("listName")
        for ins in block.get("insert", []):
            single, iblock = ins.get("insert"), ins.get("insertBlock")
            values = (
                [single]
                if single is not None
                else [line.replace("\r", "") for line in (iblock or "").split("\n") if line]
            )
            for value in values:
                handle_insert(value, listname, ins.get("after"), ins.get("before"))
        for rep in block.get("replace", []):
            # replace has no after/before of its own -- it takes the position of
            # what it replaces, so it's fed into the mlox sort anchored at that
            # spot (via source) purely to detect drift; generate_customizations_toml
            # skips it when emitting insert blocks (see replace_dest_names)
            handle_insert(
                rep.get("dest", ""), listname, after=None, before=rep.get("source"), is_replace=True
            )
        for ap in block.get("append", []):
            text = ap.get("append") or ap.get("appendBlock") or ""
            for m in re.finditer(r"^\s*content\s*=\s*(\S+)", text, re.MULTILINE):
                subset.append(m.group(1))
                if listname:
                    subset_listnames[m.group(1)] = listname

    # de-dupe case-insensitively but PRESERVE the file's declaration order --
    # for mods no rule or dependency constrains, "where it appears in my file"
    # is the order the user chose, and alphabetizing it away was a bug
    _seen = set()
    # `or _seen.add(...)` is the standard dedupe idiom: add() returns None, so
    # the `or` always falls through to False and the entry is kept once.
    subset = [
        s for s in subset if not (s.lower() in _seen or _seen.add(s.lower()))  # type: ignore[func-returns-value]
    ]
    return subset, data_inserts, replace_dest_names, subset_listnames


# ---------------------------------------------------------------------------
# data= (folder path) insertion -- positioned by after/before anchor, since
# mlox has no concept of ordering data paths, only plugins
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TOML generation -- write a corrected momw-customizations.toml so the fix
# persists across umo/momw-configurator rebuilds, instead of only patching
# openmw.cfg (which a rebuild would just overwrite again)
# ---------------------------------------------------------------------------


def read_savegame_content_files(path: str | Path) -> tuple[list[str] | None, str | None]:
    """Content files an OpenMW .omwsave depends on.

    Saves are ESM3 files: a TES3 header record, then a SAVE record whose DEPE subrecords
    each carry one content filename (components/esm3/savedgame.cpp). Returns (files,
    error): files is None on failure.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, f"can't read save: {e}"
    if raw[:4] != b"TES3":
        return None, "not an OpenMW save (no TES3 header)"
    for tag, body in _iter_tes3_records(raw):
        if tag == b"SAVE":
            files = [
                sd.rstrip(b"\x00").decode("utf-8", "replace")
                for st, sd in _iter_subrecords(body)
                if st == b"DEPE"
            ]
            return files, None
    return None, "no SAVE record found -- not a savegame?"


def check_savegame_against_order(
    save_path: str | Path, active_order: Sequence[str]
) -> tuple[list[str] | None, list[str] | None, str | None]:
    """Check a savegame's content files against the active order.

    Returns (save_files, missing, error):

    which of the save's content files are absent from the given load order. A missing
    file means OpenMW will refuse to load (or badly degrade) that save.
    """
    files, err = read_savegame_content_files(save_path)
    if files is None:
        return None, None, err
    active_lower = {str(n).lower() for n in active_order}
    missing = [f for f in files if f.lower() not in active_lower]
    return files, missing, None


BACKUP_PATTERNS = (
    ".preclean.bak",  # ours: original before a staged tes3cmd clean
    ".masterfix.bak",  # ours: original before a master-size resync
)


def scan_backups(
    dirs: Sequence[str], cfg_path: str | Path | None = None, max_depth: int = 4
) -> list[tuple[Path, Path | None, str]]:
    """Find backup files this tool and the tools it drives leave behind.

    Namely:

    *.preclean.bak, *.masterfix.bak, tes3cmd's 'name~1.ext', and timestamped '*.bak-
    YYYYMMDD-HHMMSS' / Configurator '*.backup.*' copies. Returns [(backup_path,
    original_path_or_None, kind)].
    """
    import re as _re

    out, seen = [], set()
    re_tilde = _re.compile(r"^(.+)~\d+(\.[^.]+)$", _re.IGNORECASE)
    re_stamp = _re.compile(r"^(.+)\.bak-\d{8}-\d{6}$", _re.IGNORECASE)
    re_cfgbk = _re.compile(r"^(.+)\.backup\.", _re.IGNORECASE)

    roots = [Path(d) for d in dirs if d]
    if cfg_path:
        roots.append(Path(cfg_path).parent)
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).parts) - base_depth >= max_depth:
                dirnames[:] = []
            for fn in filenames:
                p = Path(dirpath) / fn
                key = str(p).lower()
                if key in seen:
                    continue
                orig, kind = None, None
                for suf in BACKUP_PATTERNS:
                    if fn.lower().endswith(suf):
                        orig, kind = p.with_name(fn[: -len(suf)]), suf.lstrip(".")
                        break
                if kind is None:
                    m = re_tilde.match(fn)
                    if m:
                        orig, kind = p.with_name(m.group(1) + m.group(2)), "tes3cmd ~N"
                    else:
                        m = re_stamp.match(fn)
                        if m:
                            orig, kind = p.with_name(m.group(1)), "timestamped .bak"
                        else:
                            m = re_cfgbk.match(fn)
                            if m:
                                orig, kind = p.with_name(m.group(1)), "configurator .backup"
                if kind is not None:
                    seen.add(key)
                    # The original is reported even when it no longer exists:
                    # restoring a backup whose original was deleted is a valid
                    # recovery, and the caller shows its own "original missing"
                    # marker based on the path.
                    out.append((p, orig, kind))
    out.sort(key=lambda t: str(t[0]).lower())
    return out


USER_RULES_HEADER = (
    ";; Personal mlox rules -- written by you (with help from Wraithguard Toolkit's\n"
    ";; rule maker). Keep this file LAST in the rule-files list: later files win\n"
    ";; rule conflicts, so your rules override mlox_base/mlox_user.\n"
    ";; Syntax: https://morrowind-modding.github.io/modding-tools/sorting-plugin-load-order/mlox/mlox-rule-guidelines\n"
)


def order_rule_frozen_conflicts(
    names: Sequence[str], final_order: Sequence[str], curated_lower: Collection[str]
) -> list[tuple[str, str]]:
    """Return the pairs a proposed [Order] rule would fight the cfg over.

    For consecutive (earlier, later)
    name pairs that CONTRADICT the frozen curated order: both names are
    curated plugins the sort won't reorder, but the rule wants them opposite
    to how they currently sit. mlox discards such edges as cycles (per the
    rule guidelines: "whenever we encounter a rule that would cause a cycle,
    it is discarded"), so the rule would silently not take effect for those
    pairs. Wildcard/<VER> tokens are skipped -- they don't resolve to one
    position. Purely advisory; used to warn before writing a rule.
    """
    pos = {str(n).lower(): i for i, n in enumerate(final_order)}
    cl = {str(c).lower() for c in curated_lower}
    out = []
    for a, b in pairwise(names):
        al, bl = a.lower(), b.lower()
        if pattern_has_meta(a) or pattern_has_meta(b):
            continue
        if al in cl and bl in cl and al in pos and bl in pos and pos[al] > pos[bl]:
            out.append((a, b))
    return out


def append_user_rule(
    path: str | Path, keyword: str, names: Sequence[str], comment: str | None = None
) -> str:
    """Append one mlox *ordering* rule to a personal rules file.

    The narrow form kept for callers that only ever write an ordering rule.
    It delegates to :func:`append_authored_rule`, so there is one validator and
    one renderer rather than two that can drift -- which matters here, because
    the two used to disagree about what a valid plugin name looks like.

    Args:
        path: The personal rules file, created with a header if new.
        keyword: ``order``, ``nearstart`` or ``nearend``, in any case.
        names: The plugin filenames. mlox wildcards are allowed.
        comment: An optional ``;`` comment written above the rule.

    Returns:
        The text written.

    Raises:
        ValueError: If the keyword is not an ordering rule, or the rule does
            not pass validation.
    """
    kinds = {"order": "Order", "nearstart": "NearStart", "nearend": "NearEnd"}
    kind = kinds.get(str(keyword).strip().lower())
    if kind is None:
        message = f"unsupported rule type: {keyword!r}"
        raise ValueError(message)
    cleaned = [str(name).strip() for name in names if str(name).strip()]
    if not cleaned:
        message = "no plugin names given"
        raise ValueError(message)
    rule = authoring.Rule(kind=kind, plugins=cleaned, comment=(comment or "").strip())
    return append_authored_rule(path, rule)


def append_authored_rule(path: str | Path, rule: authoring.Rule) -> str:
    """Append any mlox rule to a personal rules file.

    The general form of :func:`append_user_rule`, which only ever knew the three
    ordering kinds. Validation is the authoring module's, so what gets written
    is a rule that both parses *and* meets the rule-base guidelines -- errors
    refuse, warnings do not.

    Args:
        path: The personal rules file. Created with an explanatory header when
            it does not exist yet.
        rule: The rule to write.

    Returns:
        The text appended.

    Raises:
        ValueError: If the rule has errors, listing every one of them. A rule
            file is read by a tool that silently discards what it cannot use, so
            refusing here is the only place a person finds out.
    """
    problems = authoring.errors(authoring.validate(rule))
    if problems:
        message = "; ".join(problem.describe() for problem in problems)
        raise ValueError(message)
    text = authoring.render_rule(rule) + "\n"
    target = Path(path)
    if target.exists():
        existing = target.read_text(encoding="utf-8-sig", errors="replace")
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        target.write_text(existing + sep + text, encoding="utf-8")
    else:
        target.write_text(USER_RULES_HEADER + "\n" + text, encoding="utf-8")
    return text


# Candidate sources for MOMW's plugin-order.yml (first that yields a valid
# file wins). The canonical copy lives in the website repo at
# momw/momw/data_seeds/data/plugin-order.yml; the GitLab API raw endpoint is
# tried too since it sidesteps the web UI's occasional auth funkiness, then
# MOMW's own Gitea mirror. Overridable via $MLOX_PLUGIN_ORDER_URL.


#: Schemes we are willing to download from. Anything else (notably ``file:``,
#: ``ftp:`` and ``data:``) is rejected: these URLs come from a persisted
#: settings file and from environment variables, so a tampered value must not
#: be able to make an "update" button read an arbitrary local file and write
#: it over the user's rules.

#: Hard cap on a single download (bytes). The real files are ~250 KB; this
#: stops a hostile or misconfigured endpoint from exhausting memory.


RULES_REPO = (
    "DanaePlays/mlox-rules"  # actively maintained; plox uses it, mlox 1.1+ auto-updates from it
)
# {name} is replaced with the rule filename (mlox_base.txt / mlox_user.txt).
# Users can point this at a fork/mirror via the GUI's Sources dialog or
# $MLOX_RULES_URL_TEMPLATE.


# ---------------------------------------------------------------------------
# graph + stable topological sort
# ---------------------------------------------------------------------------

# Graph primitives now live in wraithguard/sort/graph.py.
from wraithguard.sort import build_and_sort

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# console UX helpers -- small, dependency-free "sections" so a run's output
# reads as a few clearly separated stages instead of one long scroll.
# stdout is what the GUI captures/redirects too, so keep this plain text
# (no ANSI codes) -- the GUI does its own colorizing by scanning for tags
# like [CONFLICT] / [REQUIRES] / WARNING: / NOTE: on each line.
# ---------------------------------------------------------------------------


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n {title}\n{'=' * 70}")


def _subsection(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cfg", required=True, type=Path, help="Path to openmw.cfg")
    ap.add_argument(
        "--rules",
        required=True,
        nargs="+",
        type=Path,
        help="mlox rule file(s) or directories, in increasing priority order "
        "(pass mlox_base.txt first, mlox_user.txt last)",
    )
    ap.add_argument(
        "--customizations",
        type=Path,
        help="momw-customizations.toml to auto-derive the subset from",
    )
    ap.add_argument(
        "--subset",
        nargs="*",
        default=[],
        help="Explicit list of plugin filenames to sort (combined with --customizations if both given)",
    )
    ap.add_argument(
        "--groundcover",
        nargs="*",
        default=[],
        metavar="PLUGIN",
        help="Plugin filenames to declare as grass. They are kept out of content= "
        "and written as groundcover= instead. Their data= folders still go in "
        "normally (OpenMW must be able to find the file), so name the folder with "
        "--subset or in the subset file as usual",
    )
    ap.add_argument(
        "--subset-file",
        type=Path,
        help="Plain text (one plugin per line) or minimal TOML (subset = [...]) "
        "file listing plugins to sort -- shorter to maintain than --subset or "
        "a full momw-customizations.toml block",
    )
    ap.add_argument(
        "--scan-dir",
        type=Path,
        help="Scan this mods folder for data folders and plugins and write the result "
        "to --subset-file (required with this), then sort using it. Folds in the "
        "old mod_scan.py: each folder containing an asset subfolder or a plugin "
        "becomes a data= entry (plus its plugins as content=), and matched branches "
        "aren't descended further.",
    )
    ap.add_argument(
        "--subset-from-cfg",
        action="store_true",
        help="Pull UNMANAGED (orphan) entries out of the --cfg itself and sort them: "
        "every content= plugin and data= path already in openmw.cfg that is neither on "
        "the curated list (--plugin-order-yml + --list-name) nor declared in your "
        "--customizations. Their existing cfg order is kept as the frozen starting "
        "order (until the sort repositions them). Base masters (Morrowind/Tribunal/"
        "Bloodmoon.esm) and the base game's Data Files folder are never pulled. Can be "
        "the only subset source, or combined with --customizations/--subset/--subset-file "
        "to sort those plus the orphans. data= orphans are classified only when a "
        "--data-path-order-yml and --list-name are given (that yml is the 'managed' "
        "signal a folder can match); they are reported, not reordered, since they already "
        "sit in the cfg's data= order.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing")
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing a .bak-<timestamp> copy before overwriting openmw.cfg and/or "
        "before overwriting an existing --emit-toml target (e.g. when writing back "
        "to the same file --customizations pointed at).",
    )
    ap.add_argument(
        "--emit-toml",
        type=Path,
        help="Write a momw-customizations.toml here (with insert blocks reordered/"
        "re-anchored per the mlox+anchor results) instead of/alongside patching "
        "openmw.cfg directly. If --customizations is also given, its other blocks "
        "(removeContent, replace, append, ...) are preserved and only the sorted "
        "plugins/paths are regenerated; if not, a brand new single-block TOML is "
        "generated from --subset/--subset-file alone. This is the durable fix: feed "
        "the output back into momw-configurator so the correct order survives "
        "future rebuilds.",
    )
    ap.add_argument(
        "--list-name",
        help="listName to write into the emitted momw-customizations.toml (the curated "
        "mod list these customizations apply to, e.g. 'total-overhaul'). Overrides "
        "the listName from --customizations if both are given. Without this, the "
        "source TOML's listName is kept, or -- when generating from --subset-file "
        "alone -- it defaults to the placeholder 'generated'. momw-configurator "
        "REQUIRES a correct listName, so set this when generating a fresh TOML.",
    )
    ap.add_argument(
        "--plugin-order-yml",
        type=Path,
        help="MOMW's plugin-order.yml (source of truth for which plugins belong to which "
        "curated mod list). With --list-name, curated plugins for that list are "
        "excluded from the sort (never reordered) so only YOUR custom additions are "
        "touched, and read-only sanity warnings are emitted: redundant (a custom "
        "plugin that's already on the list), orphan (in your cfg but neither on the "
        "list nor in your customizations), needs-cleaning (TES3CMD), and a base-order "
        "drift check against the list's canonical order. Optional; PyYAML is used if "
        "installed, else a built-in parser.",
    )
    ap.add_argument(
        "--data-path-order-yml",
        type=Path,
        help="MOMW's data-path-order.yml (the per-list order each mod's data directories "
        "should be added in). With --list-name, read-only warnings are emitted: mods on "
        "the list whose data= path is missing from openmw.cfg, and data= paths whose cfg "
        "order contradicts the curated one. Optional; PyYAML used if installed, else a "
        "built-in parser.",
    )
    ap.add_argument(
        "--write-cfg",
        action="store_true",
        help="Actually patch openmw.cfg in place. Off by default -- "
        "prefer --emit-toml for a fix that survives future rebuilds. "
        "A .bak copy is made first unless --no-backup is also given.",
    )
    ap.add_argument(
        "--sort-data-paths",
        action="store_true",
        help="Also position data= (folder path) insertions from the customizations "
        "TOML: anchored by their after/before field if given, or otherwise "
        "inferred by scanning the folder for plugin files and anchoring next to "
        "whichever existing data= path owns the nearest neighboring plugin in the "
        "mlox-sorted content order. Off by default: mlox has no concept of "
        "data-path order itself, so this is a separate opt-in feature from the "
        "mlox-based plugin sort. When off, any data-path insertions found in "
        "the TOML are left exactly as originally written (in --emit-toml output) "
        "or ignored entirely (for --write-cfg).",
    )
    ap.add_argument(
        "--no-predicate-warnings",
        action="store_true",
        help="Skip evaluating [Requires]/[Conflict]/[Note] rules against the final "
        "plugin list. On by default; this is read-only and never changes the "
        "computed sort or what gets written, only what gets printed.",
    )
    ap.add_argument(
        "--check-conflicts",
        action="store_true",
        help="After sorting, scan the active plugins for TES3 record-level conflicts "
        "(where 2+ plugins define/override the same record -- the last in the load "
        "order wins), like TES3View/tes3cmd. Read-only; needs the plugin files "
        "reachable via the cfg's data= folders. Can be slow on big lists.",
    )
    ap.add_argument(
        "--conflicts-out",
        type=Path,
        help="Write the full conflict list to this CSV (use with --check-conflicts).",
    )
    ap.add_argument(
        "--tes3conv",
        type=Path,
        help="Path to a tes3conv executable. With --check-conflicts this switches the "
        "conflict engine to tes3conv (exact record ids for every type; enables the "
        "GUI's field-level diffs). Auto-detected from PATH / $MLOX_TES3CONV / next to "
        "this script if not given; the built-in parser is used if none is found.",
    )
    ap.add_argument(
        "--cell-map",
        type=Path,
        help="Write a self-contained HTML 'modmapper'-style cell map here: an exterior-cell "
        "heatmap (brighter = more mods) plus an interior-cell list, showing which mods "
        "touch which cells (cells your custom mods touch are highlighted). Read-only; "
        "open the file in any browser.",
    )
    ap.add_argument(
        "--resource-conflicts",
        action="store_true",
        help="Scan the cfg's data= folders for loose-file (VFS) conflicts: the same relative "
        "path in 2+ folders (later wins), like MO2's Data conflicts. Read-only.",
    )
    ap.add_argument(
        "--lint",
        action="store_true",
        help="After sorting, run tes3lint-style checks over the active plugins: evil "
        "GMSTs, the interior fog-density-0 bug, interior cells with no pathgrid, "
        "expansion-function use without the expansion mastered, omwaddon/omwscripts "
        "twin mismatches, and blank custom headers. Read-only; VFS-aware.",
    )
    ap.add_argument(
        "--resources-out",
        type=Path,
        help="Write the full resource-conflict list to this CSV (with --resource-conflicts).",
    )
    ap.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Name patterns (glob) to skip in --check-conflicts/--cell-map/"
        "--resource-conflicts scans, e.g. 's3lightfixes*' '*delta*' '*grass*'.",
    )
    ap.add_argument(
        "--conflicts-subset-only",
        action="store_true",
        help="With --check-conflicts, report only conflicts that involve YOUR custom "
        "mods (skip base-list vs base-list conflicts).",
    )
    ap.add_argument(
        "--trace",
        nargs="?",
        const=True,
        default=None,
        metavar="LOGFILE",
        help="Write a debug trace log for troubleshooting (off by default). Use --trace "
        "for the default log (wraithguard_toolkit_trace.log), or --trace PATH to choose "
        "the file.",
    )
    ap.add_argument(
        "--json-dump-dir",
        type=Path,
        help="When using tes3conv for --check-conflicts/--cell-map, write (and KEEP) the "
        "per-plugin JSON conversions in this folder. tes3conv output is always spooled "
        "to disk and read one plugin at a time (bounded memory); by default that spool "
        "is a temp dir removed on exit -- give this to keep it (or to reuse it).",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Show run diagnostics on stderr (-v: progress, -vv: per-item detail). "
        "The report itself always goes to stdout regardless.",
    )
    return ap


def all_scan_dirs(
    data_order: Sequence[str] | None,
    raw_toml_data_inserts: Sequence[Mapping[str, Any]] | None = None,
    data_inserts: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return every folder a plugin or resource may live in for this run.

    Namely:

    the cfg's existing data= folders PLUS the pending custom data-path inserts (from a
    mods-folder scan or a customizations TOML) that aren't in the cfg yet.

        Conflict / cell-map / resource scans must search this combined list, not
        just the cfg's dirs -- otherwise your custom mods are invisible to those
        tools until AFTER the cfg has been written, which defeats the point of
        checking them before committing to an order. (Pending dirs are appended
        after the cfg dirs; that matches where they'd typically land.)
    """
    dirs = [v for v in (extract_data_path_value(line) for line in (data_order or [])) if v]
    seen = {str(d).lower() for d in dirs}
    for src in (data_inserts, raw_toml_data_inserts):
        for d in src or []:
            v = d.get("value")
            if v and str(v).lower() not in seen:
                seen.add(str(v).lower())
                dirs.append(v)
    return dirs


def pending_custom_dirs(
    raw_toml_data_inserts: Sequence[Mapping[str, Any]] | None = None,
    data_inserts: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return just the pending custom data-path folders.

    Deduped, in declared order,
    -- used to flag which side of a conflict is YOUR mod.
    """
    out, seen = [], set()
    for src in (data_inserts, raw_toml_data_inserts):
        for d in src or []:
            v = d.get("value")
            if v and str(v).lower() not in seen:
                seen.add(str(v).lower())
                out.append(v)
    return out


def declared_groundcover(args: argparse.Namespace) -> list[str]:
    """Collect every plugin this run declares as grass.

    Three sources, all optional and additive: ``--groundcover`` on the command
    line, ``groundcover=`` lines in a plain-text subset file, and a
    ``groundcover = [...]`` key in the minimal TOML form. The GUI's in-memory
    scan lines are read the same way as a file, so a scan can carry the
    declaration too.

    Args:
        args: The parsed arguments.

    Returns:
        Plugin filenames, in the order encountered, de-duplicated
        case-insensitively.
    """
    found: list[str] = []
    found.extend(str(name) for name in (getattr(args, "groundcover", None) or []))

    path = getattr(args, "subset_file", None)
    if path:
        try:
            text = read_user_text(path, encoding="utf-8")
        except OSError:
            text = ""  # the subset reader reports this properly; do not double-warn
        if str(path).lower().endswith(".toml"):
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib
            try:
                found.extend(str(v) for v in (tomllib.loads(text).get("groundcover") or []))
            except (ValueError, TypeError):
                pass  # a malformed TOML is reported by the subset reader
        else:
            found.extend(extract_groundcover_declarations(text.splitlines(), str(path)))

    found.extend(
        extract_groundcover_declarations(
            getattr(args, "subset_lines", None) or [], "scanned subset"
        )
    )

    seen: set[str] = set()
    unique: list[str] = []
    for name in found:
        if name.lower() not in seen:
            seen.add(name.lower())
            unique.append(name)
    return unique


def _read_subset_inputs(
    args: argparse.Namespace,
) -> tuple[
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
    dict[str, Any],
    set[str],
    dict[str, str],
]:
    """Gather the subset to sort -- the READING INPUT stage of compute_plan.

    Runs the optional mods-folder scan, then reads every declared source
    (--subset, --subset-file, in-memory subset lines, --customizations),
    de-dupes case-insensitively preserving declaration order, and refuses to
    proceed with nothing to do. Extracted verbatim from compute_plan during
    the §16 decomposition; the report output is unchanged.

    Returns:
        ``(subset, data_inserts, raw_toml_data_inserts,
        original_content_values, original_toml_data, replace_dest_names,
        subset_origins)``.

    Raises:
        SystemExit: No input source was given, or nothing usable was found.

    """
    if getattr(args, "scan_dir", None):
        if not args.subset_file:
            raise SystemExit("--scan-dir requires --subset-file (where to write the scanned list).")
        _section("SCANNING MODS FOLDER")
        scan_mod_directories(args.scan_dir, args.subset_file)

    if (
        not args.customizations
        and not args.subset
        and not args.subset_file
        and not getattr(args, "subset_lines", None)
        and not getattr(args, "subset_from_cfg", False)
        and not getattr(args, "allow_empty_sort", False)
    ):
        raise SystemExit(
            "Provide --customizations, --subset, --subset-file, --scan-dir, or "
            "--subset-from-cfg so there's something to sort."
        )

    _section("READING INPUT")

    subset = list(args.subset)
    data_inserts = []
    raw_toml_data_inserts = (
        []
    )  # captured regardless of --sort-data-paths, for --emit-toml passthrough
    original_content_values: dict[str, str] = {}
    original_toml_data: dict[str, Any] = {}
    replace_dest_names: set[str] = set()
    # {plugin_name_lower: "where this came from"} -- for check_predicates' warnings
    subset_origins: dict[str, str] = {}

    if args.subset_file:
        file_plugins, file_data_inserts = extract_subset_from_subset_file(args.subset_file)
        subset.extend(file_plugins)
        raw_toml_data_inserts.extend(file_data_inserts)
        print(
            # Two counts in one line; "(s)" kept -- ngettext handles one count.
            _("  %(file)s: %(plugins)d plugin(s), %(paths)d data path(s)")
            % {
                "file": args.subset_file,
                "plugins": len(file_plugins),
                "paths": len(file_data_inserts),
            }
        )
        if args.sort_data_paths:
            data_inserts.extend(file_data_inserts)
        elif file_data_inserts:
            print(
                _(
                    "  NOTE: data path insertions found but not sorted (pass "
                    "--sort-data-paths to include them): %(paths)s"
                )
                % {"paths": ", ".join(d["value"] for d in file_data_inserts)}
            )
        for name in file_plugins:
            original_content_values.setdefault(name, name)
            subset_origins.setdefault(name.lower(), f"subset file ({args.subset_file.name})")

    # In-memory subset lines (e.g. a GUI 'scan into memory' with no file saved).
    # Classified exactly like a plain-text subset file, just never written out.
    if getattr(args, "subset_lines", None):
        mem_plugins, mem_data_inserts = extract_subset_from_lines(
            args.subset_lines, source="scanned subset"
        )
        subset.extend(mem_plugins)
        raw_toml_data_inserts.extend(mem_data_inserts)
        print(
            _("  in-memory scan: %(plugins)d plugin(s), %(paths)d data path(s)")
            % {"plugins": len(mem_plugins), "paths": len(mem_data_inserts)}
        )
        if args.sort_data_paths:
            data_inserts.extend(mem_data_inserts)
        elif mem_data_inserts:
            print(
                ngettext(
                    "  NOTE: data path insertion found but not sorted (pass "
                    "--sort-data-paths to include it): %(count)d path",
                    "  NOTE: data path insertions found but not sorted (pass "
                    "--sort-data-paths to include them): %(count)d paths",
                    len(mem_data_inserts),
                )
                % {"count": len(mem_data_inserts)}
            )
        for name in mem_plugins:
            original_content_values.setdefault(name, name)
            subset_origins.setdefault(name.lower(), "scanned subset (in memory)")

    if args.customizations:
        toml_subset, toml_data_inserts, replace_dest_names, subset_listnames = (
            extract_subset_from_toml(args.customizations)
        )
        subset.extend(toml_subset)
        raw_toml_data_inserts.extend(toml_data_inserts)
        print(
            _("  %(file)s: %(plugins)d content plugin(s), %(paths)d data path(s)")
            % {
                "file": args.customizations,
                "plugins": len(toml_subset),
                "paths": len(toml_data_inserts),
            }
        )
        if args.sort_data_paths:
            data_inserts.extend(toml_data_inserts)
        elif toml_data_inserts:
            print(
                _(
                    "  NOTE: data path insertions found but not sorted (pass "
                    "--sort-data-paths to include them): %(paths)s"
                )
                % {"paths": ", ".join(d["value"] for d in toml_data_inserts)}
            )
        for name in toml_subset:
            original_content_values[name] = name
            listname = subset_listnames.get(name)
            subset_origins[name.lower()] = (
                f"customizations.toml -> '{listname}'" if listname else "customizations.toml"
            )
        if args.emit_toml:
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib
            original_toml_data = tomllib.loads(read_toml_text(args.customizations))

    # de-dupe case-insensitively, PRESERVING declaration order (scan order /
    # the order written in the subset file or TOML). Unconstrained mods keep
    # this order at the end of the load, instead of being alphabetized.
    _seen: set[str] = set()
    subset = [
        s for s in subset if not (s.lower() in _seen or _seen.add(s.lower()))  # type: ignore[func-returns-value]
    ]
    if (
        not subset
        and not data_inserts
        and not raw_toml_data_inserts
        and not getattr(args, "subset_from_cfg", False)
        and not getattr(args, "allow_empty_sort", False)
    ):
        # With --subset-from-cfg the subset is filled later, in compute_plan, from
        # the cfg's own orphaned entries -- so an otherwise-empty set is not "nothing
        # to do" here. allow_empty_sort is the GUI's opt-out: it wants the run to
        # proceed and hand back the cfg's current order so the user can edit it
        # (remove entries) even with nothing to sort.
        raise SystemExit("No subset plugins or data paths found -- nothing to do.")

    return (
        subset,
        data_inserts,
        raw_toml_data_inserts,
        original_content_values,
        original_toml_data,
        replace_dest_names,
        subset_origins,
    )


def _apply_plugin_order_yml(
    args: argparse.Namespace, subset: list[str]
) -> tuple[list[str], list[Any], set[str], list[str], list[str], set[str], str | None]:
    """Split curated from custom -- the plugin-order.yml stage of compute_plan.

    With a --list-name, curated plugins (those the yml says belong to that
    list) are the list's responsibility -- they are dropped from the subset so
    this tool never reorders them, leaving only YOUR true custom additions to
    sort. Everything here is guarded; a missing/garbled yml just skips the
    feature rather than failing the run. Extracted verbatim from compute_plan
    during the §16 decomposition; the report output is unchanged.

    Returns:
        ``(subset, yml_entries, curated_set, curated_order, yml_warnings,
        declared_lower, list_name)`` -- ``subset`` possibly narrowed,
        ``declared_lower`` the pre-split declarations (for the orphan check).

    """
    yml_entries: list[Any] = []
    curated_set: set[str] = set()
    curated_order: list[str] = []
    yml_warnings: list[str] = []
    declared_lower = {
        s.lower() for s in subset
    }  # everything you declared, pre-split (for orphan check)
    plugin_order_yml = getattr(args, "plugin_order_yml", None)
    list_name = getattr(args, "list_name", None)
    if plugin_order_yml:
        _section("PLUGIN-ORDER.YML (MOMW source of truth)")
        try:
            yml_entries = parse_plugin_order_yml(Path(plugin_order_yml))
            print(
                ngettext(
                    "  Loaded %(count)d plugin entry from %(file)s",
                    "  Loaded %(count)d plugin entries from %(file)s",
                    len(yml_entries),
                )
                % {"count": len(yml_entries), "file": Path(plugin_order_yml).name}
            )
        except Exception as e:  # noqa: BLE001 -- untrusted YAML, advisory only
            # parse_plugin_order_yml runs PyYAML (or our fallback parser) over a
            # community-maintained file. These are optional cross-checks; any
            # parser failure must downgrade to a warning, never abort the sort.
            print(
                _("  WARNING: could not read plugin-order.yml (%(error)s) -- skipping yml checks.")
                % {"error": e}
            )
            yml_entries = []
        if yml_entries and not list_name:
            print(
                "  NOTE: no list name given -- can't separate curated-list plugins from your "
                "custom ones, so curated/redundant/orphan/order checks are skipped "
                "(needs-cleaning notes still work)."
            )
        if yml_entries and list_name:
            curated_set, curated_order = curated_for_list(yml_entries, list_name)
            print(
                ngettext(
                    "  '%(list)s': %(count)d curated plugin on this list",
                    "  '%(list)s': %(count)d curated plugins on this list",
                    len(curated_set),
                )
                % {"list": list_name, "count": len(curated_set)}
            )
            if not curated_set:
                print(
                    _(
                        "  WARNING: no plugins found for list '%(list)s' in the yml -- check "
                        "the list name spelling. Curated-set checks skipped."
                    )
                    % {"list": list_name}
                )
            redundant = [s for s in subset if s.lower() in curated_set]
            if redundant:
                subset = [s for s in subset if s.lower() not in curated_set]
                yml_warnings.extend(
                    f"[REDUNDANT] '{r}' is already part of the '{list_name}' list -- not "
                    f"sorting it (leaving it to the curated list / configurator)."
                    for r in redundant
                )
                print(
                    ngettext(
                        "  Excluded %(count)d curated plugin from the sort: %(names)s",
                        "  Excluded %(count)d curated plugins from the sort: %(names)s",
                        len(redundant),
                    )
                    % {"count": len(redundant), "names": ", ".join(redundant)}
                )
    return subset, yml_entries, curated_set, curated_order, yml_warnings, declared_lower, list_name


def _sort_subset(
    args: argparse.Namespace,
    subset: Sequence[str],
    base_order_names: list[str],
    data_order: Sequence[str],
    raw_toml_data_inserts: Sequence[Mapping[str, Any]],
    data_inserts: Sequence[Mapping[str, Any]],
    subset_origins: Mapping[str, str],
    custom_anchors: MutableMapping[str, tuple[str, str | None]],
) -> tuple[list[str] | None, list[str]]:
    """Run the sort -- rules, masters, mlox order and warnings.

    Loads the mlox rule blocks, reads header masters for the custom plugins
    (best-effort), runs :func:`build_and_sort`, verifies the curated order did
    not drift, prints the final ``content=`` order, and evaluates the
    read-only predicate warnings. Extracted verbatim from compute_plan during
    the §16 decomposition; the report output is unchanged.

    Args:
        args: The parsed CLI/GUI arguments (rule files, flags).
        subset: The user's own plugins to place.
        base_order_names: The curated ``content=`` order, treated as frozen.
        data_order: Existing ``data=`` lines, used to locate the plugin files.
        raw_toml_data_inserts: Pending data-path inserts from the TOML.
        data_inserts: Pending data-path inserts for this run.
        subset_origins: ``{plugin_lower: where it came from}``, for warnings.
        custom_anchors: **Mutated in place** by build_and_sort with
            ``{custom_lower: (how, anchor_name)}``.

    Returns:
        ``(final_order, predicate_warnings)`` -- ``(None, [])`` when the
        subset is empty and there is nothing to sort.

    """
    final_order = None
    predicate_warnings = []
    if not subset and getattr(args, "allow_empty_sort", False):
        _section("NOTHING TO SORT")
        print(
            _(
                "  No plugins to sort. The current openmw.cfg load order is shown above "
                "for editing -- remove entries and Export, or add plugins and Sort again."
            )
        )
    if subset:
        _section(f"SORTING {len(subset)} PLUGIN(S)")
        print(f"  {', '.join(subset)}")

        base_lower = {n.lower() for n in base_order_names}
        already_present = [s for s in subset if s.lower() in base_lower]
        new_plugins = [s for s in subset if s.lower() not in base_lower]
        if already_present:
            print(
                _("\n  Already in cfg, will be repositioned within it: %(names)s")
                % {"names": ", ".join(already_present)}
            )
        if new_plugins:
            print(
                _("  Not in cfg yet, will be inserted: %(names)s")
                % {"names": ", ".join(new_plugins)}
            )

        _subsection("loading mlox rules")
        rule_blocks, nearstart_pats, nearend_pats = load_rule_blocks(args.rules)

        sort_trace_begin()  # fresh, dedicated sort log for this sort's play-by-play

        # header-master dependencies for the custom plugins (best-effort: needs
        # the mod files reachable via the cfg's data= folders). These force each
        # custom to load after the masters it declares -- the real dependency the
        # mlox rule DB doesn't cover for arbitrary mods.
        masters = {}
        try:
            # Look for the custom plugin files in BOTH the cfg's existing data=
            # folders AND the data paths being added by this run (the custom mods
            # usually live in folders not yet in the cfg -- e.g. the umo custom
            # dirs -- so without these their headers can't be read).
            sort_dirs = all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts)
            sort_index = PluginFileIndex(sort_dirs)
            trace_sort(
                f"[sort] header-master read: searching {len(sort_dirs)} data folder(s) for "
                f"{len(subset)} custom plugin(s)"
            )
            _not_found = 0
            for name in subset:
                p = sort_index.find(name)
                if p is None:
                    _not_found += 1
                    trace_sort(
                        f"[sort]  '{name}': file NOT found in data folders -- masters unknown"
                    )
                    continue
                ms = read_plugin_masters(p)
                trace_sort(f"[sort]  '{name}': {len(ms)} master(s) {ms}  ({p})")
                if ms:
                    masters[name.lower()] = ms
            if _not_found:
                trace_sort(
                    f"[sort] header-master read: {_not_found} custom file(s) not found in any "
                    f"data folder"
                )
            if masters:
                print(
                    _("  Read header masters for %(have)d of %(total)d custom plugin(s).")
                    % {"have": len(masters), "total": len(subset)}
                )
            else:
                print(
                    "  (No header masters read -- mod files not reachable from the cfg's data= "
                    "folders, so ordering uses mlox rules + ESM-first only.)"
                )
        except Exception:  # noqa: BLE001 -- binary plugin headers, advisory only
            # Reads TES3 headers straight out of arbitrary .esp/.esm files, so a
            # truncated or non-standard record can surface almost any error.
            # Falling back to "no masters known" degrades the ordering hints;
            # failing here would take out an otherwise fine sort.
            masters = {}

        final_order = build_and_sort(
            base_order_names,
            subset,
            rule_blocks,
            masters=masters,
            nearstart=nearstart_pats,
            nearend=nearend_pats,
            anchor_out=custom_anchors,
        )

        # Drift check: the CURATED (non-custom) plugins must keep their exact cfg
        # order. Customs that were already in the cfg are expected to move, so
        # they're excluded from this check.
        subset_lower_chk = {s.lower() for s in subset}
        frozen_before = [n for n in base_order_names if n.lower() not in subset_lower_chk]
        base_set = set(base_order_names)
        frozen_after = [
            n for n in final_order if n in base_set and n.lower() not in subset_lower_chk
        ]
        if frozen_before != frozen_after:
            print(
                "\n  INTERNAL WARNING: curated (frozen) order drifted -- this shouldn't happen. "
                "Please double check the output before using it."
            )

        _subsection("final content= order")
        subset_lower = {s.lower() for s in subset}
        for n in final_order:
            tag = "  <-- inserted/moved" if n.lower() in subset_lower else ""
            print(f"  content={n}{tag}")

        if not args.no_predicate_warnings:
            rules_raw_text = load_rules_raw_text(args.rules)
            pred_data_dirs = [
                v for v in (extract_data_path_value(line) for line in data_order) if v
            ]
            predicate_warnings = check_predicates(
                rules_raw_text, final_order, subset_origins, data_dirs=pred_data_dirs
            )
            if predicate_warnings:
                _section(
                    f"{len(predicate_warnings)} MLOX RULE WARNING(S) -- read-only, not enforced"
                )
                for w in predicate_warnings:
                    print(f"\n{w}")
            else:
                print(_("\n  No [Conflict]/[Requires]/[Note] warnings triggered."))
    return final_order, predicate_warnings


def _yml_post_sort_warnings(
    final_order: Sequence[str] | None,
    base_order_names: Sequence[str],
    yml_entries: Sequence[Any],
    yml_warnings: list[str],
    curated_set: Collection[str],
    curated_order: Sequence[str],
    declared_lower: Collection[str],
    list_name: str | None,
) -> None:
    """Emit the plugin-order.yml post-sort sanity warnings (read-only).

    Needs-cleaning notes, orphan detection and curated-order drift against the
    yml. Extracted verbatim from compute_plan during the §16 decomposition;
    the report output is unchanged.

    Args:
        final_order: The sorted order, or None when no sort ran.
        base_order_names: The curated ``content=`` order from the cfg.
        yml_entries: Parsed plugin-order.yml entries.
        yml_warnings: **Extended in place** with any findings.
        curated_set: Lowercased names the yml assigns to this list.
        curated_order: The curated order the yml declares, for drift checks.
        declared_lower: Everything the user declared, pre-split.
        list_name: The MOMW list name, or None when not given.

    """
    # --- plugin-order.yml: post-sort sanity warnings (read-only) ------------
    if yml_entries:
        active = final_order if final_order else base_order_names
        nc_set = needs_cleaning_set(yml_entries)
        yml_warnings.extend(
            f"[NEEDS CLEANING] '{n}' should be cleaned with TES3CMD "
            f"(flagged in plugin-order.yml)."
            for n in active
            if n.lower() in nc_set
        )
        if list_name and curated_set:
            yml_warnings.extend(
                f"[ORPHAN] '{n}' is in your cfg but not on the '{list_name}' list and "
                f"not in your customizations -- an unmanaged custom plugin (fine if "
                f"intentional)."
                for n in base_order_names
                if not curated_covers(n, curated_set, nc_set) and n.lower() not in declared_lower
            )
            yml_warnings.extend(base_order_matches_yml(base_order_names, curated_order))
        if yml_warnings:
            _section(f"{len(yml_warnings)} PLUGIN-ORDER.YML WARNING(S) -- read-only, not enforced")
            for w in yml_warnings:
                print(f"\n{w}")
        else:
            print(_("\n  No plugin-order.yml warnings."))


def _data_path_order_warnings(
    args: argparse.Namespace,
    list_name: str | None,
    data_order: Sequence[str],
) -> None:
    """Emit data-path-order.yml reconcile warnings (read-only).

    When ``--data-path-order-yml`` and a list name are given, check the mods
    that list expects against the ``data=`` paths already in ``openmw.cfg``:
    which are missing, and whether their cfg order contradicts the curated
    data-path order. Reports only; never rewrites the cfg. A parse failure is
    surfaced as one line rather than aborting the run.

    Args:
        args: The run arguments; ``data_path_order_yml`` is read from it.
        list_name: The MOMW list name, or ``None`` when not given.
        data_order: The cfg's raw ``data=`` lines.
    """
    dpo = getattr(args, "data_path_order_yml", None)
    if not dpo or not list_name:
        return
    try:
        entries = parse_data_path_order_yml(Path(dpo))
    except (OSError, ValueError) as exc:
        _section("1 DATA-PATH-ORDER.YML WARNING(S) -- read-only, not enforced")
        print(f"\n[DATA PATH] could not read {Path(dpo).name}: {exc}")
        return
    cfg_paths = [v for v in (extract_data_path_value(line) for line in data_order) if v]
    warnings = reconcile_list_against_cfg(
        entries,
        list_name,
        cfg_paths,
        report_missing=bool(getattr(args, "verbose", 0)),
    )
    if warnings:
        _section(f"{len(warnings)} DATA-PATH-ORDER.YML WARNING(S) -- read-only, not enforced")
        for w in warnings:
            print(f"\n{w}")
    else:
        print(_("\n  No data-path-order.yml warnings."))


def _check_masters(
    final_order: Sequence[str] | None,
    base_order_names: Sequence[str],
    data_order: Sequence[str],
    raw_toml_data_inserts: Sequence[Mapping[str, Any]],
    data_inserts: Sequence[Mapping[str, Any]],
    subset_origins: Mapping[str, str],
) -> tuple[list[str], list[str]]:
    """Check for missing and out-of-order masters (always on, read-only).

    Every active plugin's TES3 header masters must be present and load before
    it -- a missing master fails hard at game launch. Checked against the
    final (sorted) order when there is one, using the combined dirs (cfg +
    pending custom paths) so custom mods are checked BEFORE the cfg is
    written. Extracted verbatim from compute_plan during the §16
    decomposition; the report output is unchanged.

    Returns:
        ``(master_warnings, master_problem_plugins)``.

    """
    master_warnings: list[str] = []
    master_problem_plugins: list[str] = []
    # --- missing / out-of-order master check (always on, read-only) ---------
    # Every active plugin's TES3 header masters must be present and load
    # before it -- a missing master fails hard at game launch, so this is
    # checked on every run, against the final (sorted) order when there is
    # one. Uses the combined dirs (cfg + pending custom paths) so custom mods
    # are checked BEFORE the cfg is written.
    master_warnings = []
    master_problem_plugins = []
    try:
        _active_for_masters = final_order if final_order else base_order_names
        _mindex = PluginFileIndex(all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts))
        _missing, _order_problems, _size_notes, _checked, _problem_names = check_missing_masters(
            _active_for_masters, _mindex, subset_origins
        )
        master_problem_plugins = sorted(_problem_names, key=str.lower)
        if _checked == 0:
            _section("MASTER CHECK -- skipped")
            print(_("  (plugin files not reachable from the data folders; can't read headers)"))
        else:
            master_warnings = _missing + _order_problems
            n_issues = len(_missing) + len(_order_problems)
            _section(
                f"MASTER CHECK -- {_checked} plugin(s) read, "
                f"{n_issues} problem(s), {len(_size_notes)} size note(s)"
            )
            for w in _missing:
                print(f"\n{w}")
            for w in _order_problems:
                print(f"\n{w}")
            if _size_notes:
                _subsection(f"{len(_size_notes)} master size mismatch note(s) (usually benign)")
                for w in _size_notes:
                    print(f"{w}")
            if not n_issues and not _size_notes:
                print(_("  All masters present and correctly ordered."))
    except Exception as e:  # noqa: BLE001 -- advisory whole-section guard
        # Wraps the entire master-consistency report, which walks plugin headers
        # from disk. It is diagnostic output: if any part of it fails the user
        # should still get their sort, with a note that this check didn't run.
        _LOG.warning(_("master check failed: %(error)s"), {"error": e})
    return master_warnings, master_problem_plugins


def _staleness_watchdog(
    final_order: Sequence[str] | None,
    base_order_names: Sequence[str],
    data_order: Sequence[str],
    raw_toml_data_inserts: Sequence[Mapping[str, Any]],
    data_inserts: Sequence[Mapping[str, Any]],
) -> None:
    """Warn when a generated merged artefact has gone stale (read-only).

    delta-plugin's merged leveled lists (and similar generated artifacts) only
    reflect the plugins that existed when the Configurator last ran; if newer
    plugins exist the merge is stale and quietly wrong. Extracted verbatim
    from compute_plan during the §16 decomposition; the report output is
    unchanged.
    """
    # --- merged-artifact staleness watchdog (read-only) ---------------------
    # delta-plugin's merged leveled lists (and similar generated artifacts)
    # only reflect the plugins that existed when the Configurator last ran.
    # If newer plugins exist, the merge is stale and quietly wrong.
    try:
        _active_ws = final_order if final_order else base_order_names
        _widx = PluginFileIndex(all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts))
        for _artifact in (
            "delta-merged.omwaddon",
            "deleted_groundcover.omwaddon",
            "S3LightFixes.esp",
        ):
            _ap = _widx.find(_artifact)
            if _ap is None or _artifact.lower() not in {n.lower() for n in _active_ws}:
                continue
            _amtime = _ap.stat().st_mtime
            _newer = []
            for _n in _active_ws:
                if _n.lower() == _artifact.lower() or _n.lower().endswith(".omwscripts"):
                    continue
                _np = _widx.find(_n)
                try:
                    if _np is not None and _np.stat().st_mtime > _amtime:
                        _newer.append(_n)
                except OSError:
                    pass
            if _newer:
                print(
                    ngettext(
                        "\n[STALE] '%(artifact)s' is older than %(count)d active plugin "
                        "(e.g. %(examples)s%(more)s) -- re-run momw-configurator so it "
                        "gets rebuilt against the current load order.",
                        "\n[STALE] '%(artifact)s' is older than %(count)d active plugins "
                        "(e.g. %(examples)s%(more)s) -- re-run momw-configurator so it "
                        "gets rebuilt against the current load order.",
                        len(_newer),
                    )
                    % {
                        "artifact": _artifact,
                        "count": len(_newer),
                        "examples": ", ".join(_newer[:3]),
                        "more": ", ..." if len(_newer) > 3 else "",
                    }
                )
    except Exception:  # noqa: BLE001 -- purely cosmetic staleness hint
        # This block only prints an optional "[STALE]" advisory about generated
        # artifacts. It touches mtimes on paths that may not exist; there is no
        # failure here worth showing the user, let alone aborting for.
        pass


def _conflict_and_cellmap_scans(
    args: argparse.Namespace,
    subset: Sequence[str],
    final_order: Sequence[str] | None,
    base_order_names: Sequence[str],
    conf_dirs: Sequence[str],
) -> list[dict[str, Any]]:
    """Run the opt-in TES3 record-conflict and cell-map scans.

    Extracted verbatim from compute_plan during the §16 decomposition; the
    report output is unchanged.

    Returns:
        The conflict list (empty when the scans were not requested).

    """
    conflicts: list[dict[str, Any]] = []
    want_conflicts = getattr(args, "check_conflicts", False)
    cell_map_out = getattr(args, "cell_map", None)
    if want_conflicts or cell_map_out:
        active = final_order if final_order else base_order_names
        active, _excl = filter_plugins(active, getattr(args, "exclude", None))
        if _excl:
            print(
                ngettext(
                    "  (excluded %(count)d plugin by --exclude)",
                    "  (excluded %(count)d plugins by --exclude)",
                    len(_excl),
                )
                % {"count": len(_excl)}
            )
        cindex = PluginFileIndex(conf_dirs)
        conv = find_tes3conv(
            explicit=getattr(args, "tes3conv", None),
            extra_dirs=[str(args.cfg.parent) if args.cfg else None],
        )
        _dump = getattr(args, "json_dump_dir", None)
        csession = (
            Tes3ConvSession(conv, dump_dir=str(_dump) if _dump else None, keep=bool(_dump))
            if conv
            # No tes3conv on the system: the built-in reader converts in process,
            # so field-level conflicts, the cell map and Merged Lands still work.
            else NativeEspSession(dump_dir=str(_dump) if _dump else None, keep=bool(_dump))
        )  # disk-backed, shared across both scans
        if csession and _dump:
            print(_("  Keeping tes3conv JSON dump in: %(path)s") % {"path": csession.dumped_dir()})

        if want_conflicts:
            _section("TES3 RECORD CONFLICTS (read-only)")
            print(
                _("  Engine: %(engine)s")
                % {
                    "engine": (
                        f"tes3conv ({conv})" if conv else _("native esp reader (field-level)")
                    )
                }
            )
            conflicts, cstats = detect_conflicts(
                active, cindex, subset_names=subset, session=csession
            )
            print(
                format_conflict_report(
                    conflicts,
                    cstats,
                    subset_only=getattr(args, "conflicts_subset_only", False),
                    limit=200,
                )
            )
            out = getattr(args, "conflicts_out", None)
            if out and conflicts:
                try:
                    write_conflict_csv(out, conflicts)
                    print(_("\n  Wrote conflict report: %(path)s") % {"path": out})
                except OSError as e:
                    _LOG.error(_("could not write conflict CSV: %(error)s"), {"error": e})

        if cell_map_out:
            _section("CELL MAP (which mods touch which cells)")
            cov = build_cell_coverage(active, cindex, subset_names=subset, session=csession)
            try:
                Path(cell_map_out).write_text(generate_cell_map_html(cov), encoding="utf-8")
                print(
                    _(
                        "  Scanned %(scanned)d plugin(s): %(exterior)d exterior + "
                        "%(interior)d interior cell(s) touched."
                    )
                    % {
                        "scanned": cov["scanned"],
                        "exterior": len(cov["exterior"]),
                        "interior": len(cov["interior"]),
                    }
                )
                print(
                    _("  Wrote cell map: %(path)s  (open it in a browser)") % {"path": cell_map_out}
                )
            except OSError as e:
                _LOG.error(_("could not write cell map: %(error)s"), {"error": e})

        csession.cleanup()  # drop the temp JSON spool (no-op if --json-dump-dir kept it)
    return conflicts


def _lint_stage(
    args: argparse.Namespace,
    subset: Sequence[str],
    final_order: Sequence[str] | None,
    base_order_names: Sequence[str],
    conf_dirs: Sequence[str],
    subset_origins: Mapping[str, str],
) -> None:
    """Run the opt-in tes3lint-style checks (read-only).

    Extracted verbatim from compute_plan during the §16 decomposition; the
    report output is unchanged.
    """
    if getattr(args, "lint", False):
        _section("LINT (tes3lint-style checks, read-only)")
        _lactive = final_order if final_order else base_order_names
        _lactive, _lexcl = filter_plugins(_lactive, getattr(args, "exclude", None))
        if _lexcl:
            print(
                ngettext(
                    "  (excluded %(count)d plugin by --exclude)",
                    "  (excluded %(count)d plugins by --exclude)",
                    len(_lexcl),
                )
                % {"count": len(_lexcl)}
            )
        _lw, _ls = lint_plugins(
            _lactive, PluginFileIndex(conf_dirs), subset_names=subset, origins=subset_origins
        )
        print(
            _(
                "  Scanned %(scanned)d plugin(s); %(cells)d interior cell(s), "
                "%(grids)d interior pathgrid(s)."
            )
            % {
                "scanned": _ls.get("scanned", 0),
                "cells": _ls.get("interior_cells", 0),
                "grids": _ls.get("pathgrids", 0),
            }
        )
        for _w in _lw:
            print(f"\n{_w}")
        if not _lw:
            print(_("\n  No lint findings."))


def _resource_stage(
    args: argparse.Namespace,
    raw_toml_data_inserts: Sequence[Mapping[str, Any]],
    data_inserts: Sequence[Mapping[str, Any]],
    conf_dirs: Sequence[str],
) -> None:
    """Run the opt-in data-path (VFS) resource-conflict scan.

    Extracted verbatim from compute_plan during the §16 decomposition; the
    report output is unchanged.
    """
    want_resources = getattr(args, "resource_conflicts", False)
    if want_resources:
        _section("DATA-PATH RESOURCE (VFS) CONFLICTS (read-only)")
        subset_dirs = pending_custom_dirs(raw_toml_data_inserts, data_inserts)
        rconf, rstats = detect_resource_conflicts(conf_dirs, subset_dirs=subset_dirs)
        # Meshes that conflict *and* differ get read. Everything else is left
        # closed: the point of the byte comparison above is that it already
        # told us which files nobody has to think about.
        mesh_stats = analyse_mesh_conflicts(rconf)
        rstats = {**rstats, **{f"mesh_{k}": v for k, v in mesh_stats.items()}}
        print(format_resource_report(rconf, rstats, limit=200))
        rout = getattr(args, "resources_out", None)
        if rout and rconf:
            try:
                write_resource_csv(rout, rconf)
                print(_("\n  Wrote resource report: %(path)s") % {"path": rout})
            except OSError as e:
                _LOG.error(_("could not write resource CSV: %(error)s"), {"error": e})


def _plan_data_paths(
    args: argparse.Namespace,
    data_inserts: list[dict[str, Any]],
    data_order: list[str],
    final_order: Sequence[str] | None,
) -> list[tuple[str, bool, str | None]] | None:
    """Plan the data= path order for this run.

    Reports unsorted paths, or (with --sort-data-paths) infers anchors and
    computes the final data= order. Extracted verbatim from compute_plan
    during the §16 decomposition; the report output is unchanged.

    Returns:
        insert_data_paths()'s result when paths were sorted, else ``None``.

    """
    data_result = None
    # data_inserts is only ever populated (in _read_subset_inputs) when
    # args.sort_data_paths is already True -- every source that would add to
    # it prints its own "found but not sorted" note and skips the insert
    # instead, whenever the flag is off. So reaching here with a non-empty
    # data_inserts implies the flag is on.
    if data_inserts and args.sort_data_paths:
        _section(f"SORTING {len(data_inserts)} DATA PATH(S)")
        infer_data_path_anchors(data_inserts, data_order, list(final_order or []), args.cfg)
        for d in data_inserts:
            anchor = (
                (d.get("after") and f"after '{d['after']}'")
                or (d.get("before") and f"before '{d['before']}'")
                or "no anchor"
            )
            print(f"  {d['value']}  ({anchor})")
        data_result = insert_data_paths(data_order, data_inserts)
        _subsection("final data= order")
        # NB: not `_` for the unused anchor -- `_` is the gettext marker, and
        # binding it here would make it a function-local and turn every _()
        # call earlier in this function into a NameError (ruff F823).
        for line, is_new, _anchor in data_result:
            print(f"  {line}{_('  <-- inserted') if is_new else ''}")
    return data_result


def _pull_cfg_orphans(
    args: argparse.Namespace,
    subset: list[str],
    data_inserts: list[dict[str, Any]],
    raw_toml_data_inserts: list[dict[str, Any]],
    original_content_values: dict[str, str],
    subset_origins: dict[str, str],
    base_order_names: Sequence[str],
    data_order: Sequence[str],
    curated_set: Collection[str],
    needs_cleaning_lower: Collection[str],
    declared_lower: Collection[str],
    groundcover_lower: Collection[str],
) -> tuple[list[str], list[str]]:
    """Add the cfg's own orphaned ``content=`` plugins (and surface data paths).

    No-op unless ``--subset-from-cfg`` is set. Otherwise pulls every ``content=``
    plugin already in openmw.cfg that is neither curated (``plugin-order.yml``)
    nor declared in the customizations, in cfg order, and hands them to the
    sorter as ``already_present`` entries it repositions within the frozen order.
    Base masters and any plugin the cfg declares as groundcover are excluded.

    ``data=`` orphans are also identified, but only when a ``data-path-order.yml``
    and a list name are given -- that yml is the "managed" signal a data folder
    can match. Before it, a data path was already in the cfg's own ``data=`` order
    with no way to tell a curated folder from a hand-added one, so none were
    surfaced. :func:`~wraithguard.momw_datapaths.managed_cfg_data_path_norms`
    now says which cfg ``data=`` paths the list accounts for; anything else (and
    not declared, not the base game) is an orphan. They are *reported and
    returned* so the caller/GUI can highlight them; they are not re-inserted,
    since they already sit in ``data=`` and :func:`insert_data_paths` never
    reorders an existing line.

    Args:
        args: The parsed CLI/GUI arguments.
        subset: The subset so far. **Appended to** with the orphan plugins.
        data_inserts: This run's data-path inserts, read to skip already-declared
            folders when classifying data orphans.
        raw_toml_data_inserts: The TOML data-path inserts, read for the same.
        original_content_values: ``{name: cfg spelling}``. **Updated** for the
            orphan plugins.
        subset_origins: ``{plugin_lower: source}``. **Updated** for the orphans.
        base_order_names: The cfg's ``content=`` order.
        data_order: The cfg's raw ``data=`` lines (read only, to classify).
        curated_set: Lower-cased plugins the curated list owns (may be empty).
        needs_cleaning_lower: Lower-cased plugins the list flags for cleaning,
            for the ``clean_`` alias match (see :func:`curated_covers`).
        declared_lower: Lower-cased plugins already declared this run.
        groundcover_lower: Lower-cased plugins the cfg declares as groundcover.

    Returns:
        ``(subset, orphan_data_paths)`` -- the subset with orphan plugins
        appended (in cfg order), and the orphaned ``data=`` path values (empty
        unless a data-path-order.yml + list name let them be classified).
    """
    if not getattr(args, "subset_from_cfg", False):
        return subset, []

    _section(_("UNMANAGED (ORPHAN) ENTRIES FROM openmw.cfg"))

    # data= orphans need a filter to tell a curated folder from a hand-added one.
    # data-path-order.yml + a list name are that filter: managed_cfg_data_path_
    # norms() reports the cfg data= paths the list accounts for. With no yml we
    # can't classify data paths, so declared_data_norms stays "everything is
    # managed" (an empty orphan set) -- the pre-filter behavior.
    list_name = getattr(args, "list_name", None)
    dpo = getattr(args, "data_path_order_yml", None)
    pull_data = bool(dpo and list_name)
    managed_data_norms: set[str] = set()
    if pull_data:
        cfg_data_values = [v for v in (extract_data_path_value(x) for x in data_order) if v]
        try:
            dp_entries = parse_data_path_order_yml(Path(str(dpo)))
        except (OSError, ValueError):
            dp_entries = []
        managed_data_norms = managed_cfg_data_path_norms(
            dp_entries, str(list_name), cfg_data_values
        )
        declared_data_norms = {
            normalize_data_path(d["value"]) for d in (*data_inserts, *raw_toml_data_inserts)
        }
        declared_data_norms.discard("")
        managed_data_norms |= declared_data_norms

    orphan_plugins, orphan_data = orphan_cfg_entries(
        base_order_names,
        data_order,
        curated_lower={str(c).lower() for c in curated_set},
        needs_cleaning_lower={str(c).lower() for c in needs_cleaning_lower},
        declared_plugins_lower={str(d).lower() for d in declared_lower},
        # Without the yml filter every cfg data= path looks unmanaged, so treat
        # them all as "managed" (no orphans) rather than flooding the report.
        declared_data_norms=(
            managed_data_norms
            if pull_data
            else {
                normalize_data_path(extract_data_path_value(x) or "")
                for x in data_order
                if extract_data_path_value(x)
            }
        ),
    )

    # A plugin the cfg also declares as groundcover belongs on a groundcover=
    # line; adding it to content= too would load the grass twice.
    if groundcover_lower:
        orphan_plugins = [p for p in orphan_plugins if p.lower() not in groundcover_lower]

    # Skip anything already declared explicitly this run (the later de-dupe would
    # drop it anyway; not counting it keeps the report honest).
    already = {s.lower() for s in subset}
    new_plugins = [p for p in orphan_plugins if p.lower() not in already]
    for name in new_plugins:
        subset.append(name)
        original_content_values.setdefault(name, name)
        subset_origins.setdefault(name.lower(), "openmw.cfg (orphan)")

    print(
        ngettext(
            "  %(count)d orphan plugin pulled from openmw.cfg",
            "  %(count)d orphan plugins pulled from openmw.cfg",
            len(new_plugins),
        )
        % {"count": len(new_plugins)}
    )
    for name in new_plugins:
        print(f"    content={name}")

    if not new_plugins:
        print(
            _(
                "  No unmanaged plugins found -- every content= plugin in the cfg is on "
                "the list or in your customizations."
            )
        )

    # data= orphans: reported and returned (for GUI highlighting), not
    # re-inserted -- they already sit in the cfg's data= order.
    if pull_data:
        print(
            ngettext(
                "  %(count)d orphan data= path found in openmw.cfg",
                "  %(count)d orphan data= paths found in openmw.cfg",
                len(orphan_data),
            )
            % {"count": len(orphan_data)}
        )
        for value in orphan_data:
            print(f"    data={value}")
        if not orphan_data:
            print(
                _(
                    "  No unmanaged data= paths found -- every data= path in the cfg is on "
                    "the '%(list)s' data-path order or in your customizations."
                )
                % {"list": list_name}
            )
    else:
        orphan_data = []
        print(
            _(
                "  data= paths not classified -- set a data-path-order.yml and list name to "
                "surface unmanaged (orphan) data= paths too."
            )
        )
    return subset, orphan_data


def compute_plan(args: argparse.Namespace) -> dict:
    """Run the "read input, sort, evaluate warnings" half of a run.

    Never
    writes anything. Returns a plan dict that write_plan() can act on
    (optionally with a manually-overridden final_order, e.g. from a GUI's
    drag-to-reorder panel, instead of recomputing).

    Decomposed into per-stage helpers (the _read_subset_inputs /
    _apply_plugin_order_yml / _sort_subset / ... functions above and below)
    during the §16 pass; each stage's body and report output moved verbatim,
    and the whole pipeline stays pinned by tests/test_differential.py.
    """
    (
        subset,
        data_inserts,
        raw_toml_data_inserts,
        original_content_values,
        original_toml_data,
        replace_dest_names,
        subset_origins,
    ) = _read_subset_inputs(args)

    lines, content_positions, content_order, data_positions, data_order = read_cfg(args.cfg)
    base_order_names = [name for name, _ in content_order]

    # A grass plugin belongs on a groundcover= line and nowhere else. A folder
    # scan cannot tell one apart -- it takes every plugin under the tree -- so
    # one lands in the subset whenever the scanned folder is a shared mods
    # directory. Inserting it as content= as well loads the grass twice, once
    # instanced and once as real objects, which is a silent and severe
    # performance cliff. The cfg already says which plugins those are.
    # Grass the cfg already declares, plus grass this run declares. The second
    # is how a newly installed grass mod gets in: it is not in the cfg yet, so
    # there is nothing to read the fact off, and the user says so once instead.
    cfg_groundcover = read_groundcover_names(lines)
    user_groundcover = declared_groundcover(args)
    known_lower = {name.lower() for name in cfg_groundcover}
    new_groundcover = [name for name in user_groundcover if name.lower() not in known_lower]
    subset, held_back_groundcover = hold_back_groundcover(
        subset, [*cfg_groundcover, *user_groundcover]
    )
    if new_groundcover:
        _subsection(_("groundcover declared by this run"))
        for name in new_groundcover:
            print(f"  groundcover={name}")
        print(
            _(
                "  These are written as groundcover= rather than content=. Their data= "
                "folders are inserted normally -- OpenMW has to find the file."
            )
        )
    if held_back_groundcover:
        _subsection(_("groundcover plugins held back"))
        print(
            ngettext(
                "%(count)d plugin in your subset is already declared as groundcover in "
                "openmw.cfg, so it was NOT added to content=:",
                "%(count)d plugins in your subset are already declared as groundcover in "
                "openmw.cfg, so they were NOT added to content=:",
                len(held_back_groundcover),
            )
            % {"count": len(held_back_groundcover)}
        )
        for name in held_back_groundcover:
            print(f"  {name}")
        print(
            _(
                "  Their groundcover= lines are untouched and their data= paths are still "
                "written, so the grass stays enabled -- as grass. Loading one as content= "
                "too would spawn every blade as a real object."
            )
        )

    custom_anchors: dict[str, tuple[str, str | None]] = {}  # from build_and_sort

    subset, yml_entries, curated_set, curated_order, yml_warnings, declared_lower, list_name = (
        _apply_plugin_order_yml(args, subset)
    )

    # Optionally pull the cfg's own unmanaged (orphan) content=/data= into the
    # subset, in cfg order, so they get sorted too. Runs after the yml stage so
    # the curated set is known and after groundcover detection so a grass plugin
    # is never pulled into content=.
    subset, orphan_data_paths = _pull_cfg_orphans(
        args,
        subset,
        data_inserts,
        raw_toml_data_inserts,
        original_content_values,
        subset_origins,
        base_order_names,
        data_order,
        curated_set,
        needs_cleaning_set(yml_entries),
        declared_lower,
        {name.lower() for name in cfg_groundcover},
    )

    final_order, predicate_warnings = _sort_subset(
        args,
        subset,
        base_order_names,
        data_order,
        raw_toml_data_inserts,
        data_inserts,
        subset_origins,
        custom_anchors,
    )

    _yml_post_sort_warnings(
        final_order,
        base_order_names,
        yml_entries,
        yml_warnings,
        curated_set,
        curated_order,
        declared_lower,
        list_name,
    )

    _data_path_order_warnings(args, list_name, data_order)

    master_warnings, master_problem_plugins = _check_masters(
        final_order,
        base_order_names,
        data_order,
        raw_toml_data_inserts,
        data_inserts,
        subset_origins,
    )

    _staleness_watchdog(
        final_order, base_order_names, data_order, raw_toml_data_inserts, data_inserts
    )

    # Search the cfg's data= dirs AND the pending custom data paths, so the
    # scans can see your custom mods BEFORE the cfg is updated.
    conf_dirs = all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts)
    conflicts = _conflict_and_cellmap_scans(args, subset, final_order, base_order_names, conf_dirs)

    _lint_stage(args, subset, final_order, base_order_names, conf_dirs, subset_origins)

    _resource_stage(args, raw_toml_data_inserts, data_inserts, conf_dirs)

    data_result = _plan_data_paths(args, data_inserts, data_order, final_order)

    return {
        "args": args,
        "subset": subset,
        "lines": lines,
        "content_positions": content_positions,
        "data_positions": data_positions,
        "data_order": data_order,
        "final_order": final_order,
        "data_result": data_result,
        "predicate_warnings": predicate_warnings,
        "yml_warnings": yml_warnings,
        "master_warnings": master_warnings,
        "master_problem_plugins": master_problem_plugins,
        "custom_anchors": custom_anchors,
        "original_content_values": original_content_values,
        "original_toml_data": original_toml_data,
        "replace_dest_names": replace_dest_names,
        "raw_toml_data_inserts": raw_toml_data_inserts,
        "data_inserts": data_inserts,
        # Unmanaged data= paths pulled from the cfg (values, cfg order). Empty
        # unless --subset-from-cfg ran with a data-path-order.yml + list name.
        # Reported, not re-inserted; the GUI highlights them in the data panel.
        "orphan_data_paths": orphan_data_paths,
        "base_order_names": base_order_names,
        # Which plugins the curated list owns. Empty when no plugin-order.yml
        # was given -- and "empty" must be read as "unknown", not "none".
        "curated_set": curated_set,
        # Grass this run declares that the cfg does not already have. Written
        # as groundcover= lines (direct write) and append blocks (TOML).
        "new_groundcover": new_groundcover,
        "conflicts": conflicts,
        # every folder the scans should search THIS run (cfg data= dirs +
        # pending custom data paths), and just the pending custom folders --
        # so conflict/cell-map/resource scans can see your custom mods before
        # the cfg is written
        "scan_dirs": all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts),
        "custom_data_dirs": pending_custom_dirs(raw_toml_data_inserts, data_inserts),
    }


def write_plan(
    args: argparse.Namespace,
    plan: dict,
    final_order: list | None = None,
    data_order: list | None = None,
    disabled_plugins: Collection[str] | None = None,
    disabled_data: Collection[str] | None = None,
) -> dict:
    """Write out a computed plan -- the second half of a run.

    Uses plan["final_order"]/plan["data_result"] (what mlox/anchoring computed) unless a
    caller passes its own final_order and/or data_order -- e.g. a GUI that let the
    person drag-reorder either list before exporting. Nothing else about the plan is
    affected by that override: warnings, etc. were already evaluated against the
    computed order in compute_plan() and aren't recomputed here.

        data_order, if given, is a plain list of raw data= line strings (a
        permutation of the lines in plan["data_result"] -- not a list of bare
        path values) in the order to write them. Each line's is_new/source-value
        metadata is looked up from plan["data_result"] by exact line-text match,
        so reordering never has to know or guess which lines were new inserts.

        disabled_plugins / disabled_data (optional): items the user opted out of in
        the GUI. The caller passes ENABLED-only final_order/data_order, so opted-out
        items are already gone from what's written/inserted. Additionally, any
        opted-out item that ALREADY EXISTS in openmw.cfg is emitted as a
        removeContent (plugin) / removeData (data path) in the corrected TOML, so
        momw-configurator durably removes it on the next rebuild instead of just not
        re-inserting it. disabled_data holds raw 'data=...' lines.
    """
    final_order = final_order if final_order is not None else plan["final_order"]
    subset = plan["subset"]
    data_result = plan["data_result"]

    # Work out durable removals. A removal is only needed for something this
    # tool does NOT put in the file itself: momw-configurator rebuilds the cfg
    # from the curated list plus these customizations, so a plugin we simply
    # stop inserting is already absent from the result. Emitting a
    # removeContent for it as well is noise in a file people hand-edit.
    #
    # The discriminator is therefore "does the curated list own this plugin",
    # not "is it currently in openmw.cfg" -- which was the old test, and which
    # catches the user's own mods the moment they have exported once.
    #
    # Without a plugin-order.yml there is no curated set, and an empty one means
    # "unknown" rather than "nothing is curated". In that case the presence test
    # is the only signal available, so it stays as the fallback.
    remove_content = plugins_needing_removal(
        disabled_plugins or [],
        plan.get("curated_set") or (),
        plan.get("base_order_names") or [],
    )
    base_data_norms = {
        normalize_data_path(value)
        for value in (extract_data_path_value(line) for line in (plan.get("data_order") or []))
        if value
    }
    base_data_norms.discard("")
    # The same rule as for plugins, for the same reason: a data path this run
    # inserts is one we can simply stop inserting, so it needs no removeData.
    # "Ours" is anything in this run's inserts -- from the subset OR from the
    # source TOML, since the emitted file replaces that one wholesale.
    ours = {
        normalize_data_path(str(entry.get("value")))
        for entry in [*(plan.get("data_inserts") or []), *(plan.get("raw_toml_data_inserts") or [])]
        if entry.get("value")
    }
    ours.discard("")
    remove_data = data_paths_needing_removal(disabled_data or [], ours, base_data_norms)
    if remove_content or remove_data:
        _subsection("opted-out items already in cfg -> removeContent/removeData")
        for p in remove_content:
            print(f"  removeContent: {p}")
        for d in remove_data:
            print(f"  removeData: {d}")

    # New groundcover declarations go in as their own lines. Appended to the
    # end rather than spliced into an existing groundcover section: appending
    # cannot shift any index, and every content=/data= position in `segments`
    # is an index into these same lines. Placement does not matter to OpenMW --
    # only the order of groundcover lines relative to each other does, and
    # appending preserves that.
    new_groundcover = list(plan.get("new_groundcover") or [])
    plan_lines = list(plan["lines"])
    if new_groundcover:
        existing = {name.lower() for name in read_groundcover_names(plan_lines)}
        additions = [f"groundcover={n}" for n in new_groundcover if n.lower() not in existing]
        if additions:
            plan_lines.extend(additions)

    if data_order is not None and data_result is not None:
        lookup = {line: (is_new, value) for line, is_new, value in data_result}
        data_result = [(line, *lookup.get(line, (False, None))) for line in data_order]

    segments = []
    if final_order:
        segments.append((plan["content_positions"], [f"content={n}" for n in final_order]))
    if data_result is not None:
        segments.append((plan["data_positions"], [line for line, _, _ in data_result]))

    if final_order and final_order != plan["final_order"]:
        _subsection("content= order being exported (manually adjusted)")
        for n in final_order:
            print(f"  content={n}")

    if data_result is not None and [line for line, _, _ in data_result] != [
        line for line, _, _ in (plan["data_result"] or [])
    ]:
        _subsection("data= order being exported (manually adjusted)")
        for line, _is_new, _anchor in data_result:
            print(f"  {line}")

    _section("WRITING OUTPUT")
    wrote_cfg = False
    if args.write_cfg:
        write_cfg(args.cfg, plan_lines, segments, args.dry_run, args.no_backup)
        wrote_cfg = not args.dry_run
    else:
        print(_("  openmw.cfg left untouched (pass --write-cfg to patch it directly)"))

    wrote_toml = False
    if args.emit_toml:
        # Decide what data= inserts the TOML carries. The goal of --subset-from-cfg
        # is that everything unmanaged in the cfg lands in the customizations so a
        # momw-configurator rebuild re-creates it; a data= path only reaches the
        # emitted TOML if it is classified "ours" (in user_data_values) against a
        # data_result of the cfg's data= lines. Three cases:
        #   * --sort-data-paths on: data_result already holds the (re)anchored
        #     order; ours = this run's declared inserts + the cfg orphans.
        #   * orphans present, sort off: build a data_result from the existing cfg
        #     data= order so the orphan (and any already-applied TOML) paths are
        #     there to re-emit, anchored to the curated (frozen) neighbours. Any
        #     brand-new TOML insert not yet in the cfg is folded in with its own
        #     anchor so switching off the raw passthrough loses nothing.
        #   * neither: unchanged -- pass the raw TOML inserts through verbatim.
        orphan_data = list(plan.get("orphan_data_paths") or [])
        declared_vals = [d["value"] for d in (plan["data_inserts"] or [])]
        raw_inserts = list(plan["raw_toml_data_inserts"] or [])
        if args.sort_data_paths and data_result is not None:
            # Data paths were sorted: data_result holds the (re)anchored order.
            emit_data_result = data_result
            emit_raw = None
            emit_user_vals = declared_vals + orphan_data
        elif orphan_data:
            # Orphans to capture but no sorted data_result (e.g. --subset-from-cfg
            # with no data inserts, whether or not --sort-data-paths was passed).
            # Build a data_result from the existing cfg data= order so the orphan
            # (and any already-applied TOML) paths are present to re-emit, anchored
            # to the curated (frozen) neighbours; fold in any brand-new TOML insert
            # not yet in the cfg so nothing the raw passthrough carried is lost.
            src_lines = data_order if data_order is not None else list(plan.get("data_order") or [])
            src_norms = {
                normalize_data_path(extract_data_path_value(line) or "") for line in src_lines
            }
            src_norms.discard("")
            brand_new = [
                d for d in raw_inserts if normalize_data_path(d.get("value", "")) not in src_norms
            ]
            emit_data_result = insert_data_paths(list(src_lines), brand_new)
            emit_raw = None
            emit_user_vals = orphan_data + [d["value"] for d in raw_inserts]
        else:
            # No orphans: unchanged either/or -- sorted result, or raw passthrough.
            emit_data_result = data_result if args.sort_data_paths else None
            emit_raw = raw_inserts if not args.sort_data_paths else None
            emit_user_vals = declared_vals
        toml_text = generate_customizations_toml(
            plan["original_toml_data"],
            final_order or [],
            set(subset),
            plan["original_content_values"],
            emit_data_result,
            emit_raw,
            plan["replace_dest_names"],
            user_data_values=emit_user_vals,
            list_name=getattr(args, "list_name", None),
            remove_content=remove_content,
            new_groundcover=new_groundcover,
            remove_data=remove_data,
            custom_anchors=plan.get("custom_anchors"),
        )
        # Dry-run the TOML through a faithful simulation of momw-configurator's
        # apply logic and verify the result reproduces the sorted order.
        _subsection("configurator preview (simulated apply)")
        try:
            _user_norms = [normalize_data_path(v) for v in emit_user_vals if v]
            _ok, _rep = preview_configurator_result(
                plan_lines,
                toml_text,
                list(final_order or []),
                subset,
                user_data_norms=_user_norms,
                list_name=getattr(args, "list_name", None),
            )
            for _l in _rep:
                print(_l)
        except Exception as _e:  # noqa: BLE001 -- preview must never block export
            # A read-only preview of what the configurator would emit. It parses
            # TOML and cfg text that the user may have hand-edited; if the
            # preview can't be produced, the actual export still must proceed.
            print(_("  WARNING: configurator preview failed: %(error)s") % {"error": _e})
        if args.dry_run:
            print(
                _("\n  DRY RUN: would write %(path)s\n%(toml)s")
                % {"path": args.emit_toml, "toml": toml_text}
            )
        else:
            backup_file(args.emit_toml, args.no_backup)
            args.emit_toml.write_text(toml_text, encoding="utf-8")
            print(_("  Wrote corrected customizations: %(path)s") % {"path": args.emit_toml})
            wrote_toml = True

    if not args.write_cfg and not args.emit_toml:
        print(
            "\n  NOTE: nothing was written -- this was a preview. "
            "Pass --write-cfg and/or --emit-toml to save the result."
        )

    _section("SUMMARY")
    print(_("  Plugins sorted:        %(count)d") % {"count": len(subset)})
    print(
        _("  Data paths inserted:   %(count)d")
        % {"count": len(plan["data_inserts"]) if args.sort_data_paths else 0}
    )
    print(_("  Rule warnings raised:  %(count)d") % {"count": len(plan["predicate_warnings"])})
    print(
        _("  plugin-order.yml warnings: %(count)d") % {"count": len(plan.get("yml_warnings") or [])}
    )
    print(_("  openmw.cfg written:    %(answer)s") % {"answer": _("yes") if wrote_cfg else _("no")})
    print(
        _("  customizations.toml written: %(answer)s")
        % {"answer": _("yes") if wrote_toml else _("no")}
    )

    return {"wrote_cfg": wrote_cfg, "wrote_toml": wrote_toml}


def run_from_args(args: argparse.Namespace) -> dict:
    """Do the actual work for a parsed args object.

    Comes from build_arg_parser(),
    or an equivalent argparse.Namespace/SimpleNamespace built by a caller
    such as the GUI). All progress/results are printed to stdout -- callers
    that want to capture them (e.g. the GUI) should redirect sys.stdout
    around this call rather than expect a return value with the log in it.

    This is just compute_plan() + write_plan() back to back, for callers
    (the CLI) that don't need to inspect/adjust the plan in between. A GUI
    that wants a manual-reorder step in between should call compute_plan()
    and write_plan() itself instead.

    Returns a small summary dict for programmatic callers:
      {"final_order": [...] or None, "predicate_warnings": [...],
       "data_result": [...] or None, "wrote_cfg": bool, "wrote_toml": bool}
    """
    plan = compute_plan(args)
    result = write_plan(args, plan)
    return {
        "final_order": plan["final_order"],
        "predicate_warnings": plan["predicate_warnings"],
        "data_result": plan["data_result"],
        "wrote_cfg": result["wrote_cfg"],
        "wrote_toml": result["wrote_toml"],
    }


def main() -> None:
    """Parse arguments, configure logging and tracing, and run."""
    args = build_arg_parser().parse_args()
    # Diagnostics go to stderr via logging (WARNING by default, more with -v);
    # the report the user asked for stays on stdout. See logging_setup.py.
    setup_logging(verbosity=getattr(args, "verbose", 0))
    tr = getattr(args, "trace", None)
    if tr:
        set_trace_file(tr if isinstance(tr, str) else "wraithguard_toolkit_trace.log")
        trace("CLI started")
    run_from_args(args)


if __name__ == "__main__":
    main()
