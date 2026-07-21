#!/usr/bin/env python3
"""
mlox_subset_sort.py

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
  python3 mlox_subset_sort.py \
      --cfg "C:\\Games\\OpenMW\\openmw.cfg" \
      --rules ".\\mlox\\mlox_base.txt" ".\\mlox\\mlox_user.txt" \
      --customizations "C:\\Games\\OpenMW\\momw-customizations.toml"

  # Write a corrected customizations TOML (recommended, durable fix)
  python3 mlox_subset_sort.py \
      --cfg openmw.cfg --rules mlox/mlox_base.txt \
      --customizations momw-customizations.toml \
      --emit-toml momw-customizations.toml

  # Patch openmw.cfg directly instead/also (one-off, gets overwritten on next rebuild)
  python3 mlox_subset_sort.py \
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
import fnmatch
import os
import re
import struct
import sys
from datetime import datetime
from itertools import pairwise
from pathlib import Path

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
# Pattern translation now lives in mlox_subset/rules/patterns.py. It is
# re-exported here so every existing caller (and the GUI) keeps working
# unchanged; behaviour is pinned by tests/test_differential.py.
from mlox_subset import _
from mlox_subset.rules import (
    MLOX_VERSION_PATTERN as _MLOX_VER,
    mlox_pattern_to_regex,
    pattern_has_meta,
)

# Tracing now lives in mlox_subset/tracing.py; re-exported so core.trace(),
# core.set_trace_file() etc. keep working for the GUI and CLI unchanged.
# ---------------------------------------------------------------------------
# Re-exports from mlox_subset/.
#
# The split moved the implementation into packages, but this module stays the
# public surface the GUI, the CLI and the test suite import from -- so every
# name they used still resolves as `core.<name>`. The F401 suppressions below
# are therefore load-bearing, not laziness: these imports exist to be re-
# exported, and removing an "unused" one would break a caller.
# ---------------------------------------------------------------------------
from mlox_subset.tracing import (
    set_trace_file,
    sort_trace_begin,
    trace,
    trace_sort,
)

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


def read_plugin_masters(path):
    """The master files a plugin depends on, from its TES3 header (the MAST
    subrecords). These are the ground-truth load-order dependencies: a plugin
    must load AFTER every master it lists. Returns [] for non-TES3 files
    (.omwscripts) or any read problem. Works for .esm/.esp/.omwaddon/.omwgame."""
    try:
        with Path(path).open("rb") as fh:
            if fh.read(4) != b"TES3":
                return []
            data_size = struct.unpack("<I", fh.read(4))[0]
            fh.read(8)  # header1 + flags
            data = fh.read(min(data_size, 1 << 20))  # header is tiny; cap defensively
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
            nm = chunk.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
            if nm:
                masters.append(nm)
    return masters


def read_plugin_masters_with_sizes(path):
    """Like read_plugin_masters, but returns [(master_name, recorded_size)] --
    each MAST subrecord is (per the TES3 format) immediately followed by a DATA
    subrecord holding the master's file size (8 bytes) at the time the plugin
    was saved. tes3cmd uses the same pairing for its master-sync check.
    recorded_size is None when the DATA subrecord is absent/malformed."""
    try:
        with Path(path).open("rb") as fh:
            if fh.read(4) != b"TES3":
                return []
            data_size = struct.unpack("<I", fh.read(4))[0]
            fh.read(8)  # header1 + flags
            data = fh.read(min(data_size, 1 << 20))
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
            nm = chunk.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
            pending = nm or None
        elif tag == b"DATA" and pending is not None:
            size = struct.unpack_from("<Q", chunk, 0)[0] if len(chunk) >= 8 else None
            out.append((pending, size))
            pending = None
    if pending is not None:
        out.append((pending, None))
    return out


def sync_plugin_master_sizes(path, index, make_backup=True):
    """VFS-aware replacement for `tes3cmd header --synchronize`: fix the
    master sizes recorded in a plugin's TES3 header (the DATA subrecord after
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
    A one-time '<name>.masterfix.bak' copy is made before the first write."""
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


def check_missing_masters(active_order, index, subset_origins=None):
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


def _tes3_record_key(rectype: str, blob: bytes):
    """Return (record_id, deleted) for one record's subrecord blob, or
    (None, deleted) if it has no id worth comparing on."""
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


def parse_tes3_records(path):
    """Yield (record_type, record_id, deleted) for each game record in a TES3
    plugin (.esp/.esm/.omwaddon). Best-effort and fully guarded: a truncated or
    non-TES3 file just yields nothing rather than raising."""
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


def _lual_script_paths(blob):
    """Yield the normalized Lua script path from each LUAS subrecord of a LUAL
    (LuaScriptsCfg) record."""
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


def parse_omwscripts(path):
    """Yield ('LuaScript', normalized_path, False) for each declaration in an
    OpenMW .omwscripts file. Text format (per OpenMW's parseOMWScripts): one
    'TAGS: script/path.lua' per line, '#' comments and blank lines skipped."""
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


def parse_plugin_records(path):
    """Dispatch to the right record reader by extension: .omwscripts is OpenMW's
    text Lua-attach config; everything else (.esp/.esm/.omwaddon/.omwgame) is the
    TES3 binary format."""
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


def find_tes3conv(explicit=None, extra_dirs=None):
    """Locate a tes3conv executable. Order: explicit path, $MLOX_TES3CONV, PATH,
    then alongside this script / any extra dirs given. Returns a path or None."""
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


def find_tes3cmd(explicit=None, extra_dirs=None):
    """Locate tes3cmd. Prefers the compiled executable (tes3cmd.exe -- what the
    MOMW Tools Pack distributes and what end users will normally have); the
    pure-perl 'tes3cmd' script is also accepted (it then needs a perl on PATH;
    see tes3cmd_invocation). Order: explicit path, $MLOX_TES3CMD, PATH, then
    alongside this script / any extra dirs given. Returns a path or None."""
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


def tes3cmd_invocation(path):
    """argv prefix to run the given tes3cmd, or (None, why-not).

    The compiled tes3cmd.exe (MOMW Tools Pack) runs directly. If the path is
    the pure-perl script instead, it's run through a perl interpreter from
    PATH -- with a clear error if there isn't one, since end users normally
    have the compiled build and shouldn't need perl."""
    import shutil

    p = Path(path)
    if p.suffix.lower() in (".exe", ".bat", ".cmd"):
        return [str(p)], None
    try:
        head = p.open("rb").read(256)
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


def stage_for_tes3cmd(staging_root, plugin_path, index, quiet=False):
    """Build/refresh a minimal vanilla-Morrowind layout so tes3cmd can work on
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

    def _ensure(src, allow_link):
        dest = df / src.name
        try:
            if dest.exists():
                s, d = src.stat(), dest.stat()
                if d.st_size == s.st_size and int(d.st_mtime) == int(s.st_mtime):
                    return dest  # cached from a previous run
                dest.unlink()
            if allow_link:
                try:
                    os.link(src, dest)  # same-volume: instant, no disk cost
                    return dest
                except OSError:
                    pass  # cross-volume etc. -> copy
            _sh.copy2(src, dest)
            return dest
        except OSError as e:
            if not quiet:
                print(f"  WARNING: couldn't stage '{src.name}': {e}")
            return None

    for m in masters:
        src = index.find(m) if index else None
        if src is None:
            missing.append(m)
            continue
        if _ensure(Path(src), allow_link=True) is not None:
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


def _iter_tes3_records(raw):
    """Yields (tag, body) for each top-level record. Bodies of record types
    the caller doesn't care about are skipped over cheaply by size."""
    n, i = len(raw), 0
    while i + 16 <= n:
        tag = bytes(raw[i : i + 4])
        (sz,) = struct.unpack_from("<I", raw, i + 4)
        yield tag, raw[i + 16 : i + 16 + sz]
        i += 16 + sz


def _iter_subrecords(body):
    n, i = len(body), 0
    while i + 8 <= n:
        tag = bytes(body[i : i + 4])
        (sz,) = struct.unpack_from("<I", body, i + 4)
        yield tag, body[i + 8 : i + 8 + sz]
        i += 8 + sz


def _lint_zstr(b):
    return b.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()


def lint_plugins(active_order, index, subset_names=None, origins=None, progress=None):
    """Runs the ported checks over every active plugin and returns
    (warnings, stats). Checks:

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
    warnings, stats = [], {"scanned": 0, "unreadable": 0}
    interior_first = {}  # cell id lower -> (plugin, display name)
    pathgrids = set()  # interior pathgrid cell ids seen anywhere

    def tagfor(p):
        o = origins.get(str(p).lower())
        return f" [{o}]" if o else ""

    for np, p in enumerate(active_order):
        pl = str(p).lower()
        if pl.endswith(".omwscripts") or pl in _LINT_SKIP:
            continue
        path = index.find(p) if index else None
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
            progress(np, p)
        is_custom = pl in subset_lower
        evil_here = []
        my_masters = set()
        tb_hits, bm_hits = set(), set()
        for tag, body in _iter_tes3_records(raw):
            if is_custom and tag in (b"SCPT", b"INFO"):
                # expansion-function dependency scan (tes3lint !TB-FUN/!BM-FUN)
                want = b"SCTX" if tag == b"SCPT" else b"BNAM"
                for st, sd in _iter_subrecords(body):
                    if st == want and sd:
                        text = sd.decode("latin-1", "replace")
                        for mm in _RE_TB_FUN.finditer(text):
                            tb_hits.add(mm.group(1))
                        for mm in _RE_BM_FUN.finditer(text):
                            bm_hits.add(mm.group(1))
                continue
            if tag == b"TES3":
                for st, sd in _iter_subrecords(body):
                    if st == b"MAST":
                        my_masters.add(_lint_zstr(sd).lower())
                if is_custom:
                    for st, sd in _iter_subrecords(body):
                        if st == b"HEDR" and len(sd) >= 296:
                            auth = _lint_zstr(sd[8:40])
                            desc = _lint_zstr(sd[40:296])
                            missing = [
                                w for w, v in (("author", auth), ("description", desc)) if not v
                            ]
                            if missing:
                                warnings.append(
                                    f"[HEADER] '{p}'{tagfor(p)}: header has no "
                                    f"{' and no '.join(missing)}."
                                )
                            break
            elif tag == b"GMST":
                name, vtag, vdata = None, None, b""
                for st, sd in _iter_subrecords(body):
                    if st == b"NAME":
                        name = _lint_zstr(sd).lower()
                    elif st in (b"STRV", b"INTV", b"FLTV"):
                        vtag, vdata = st.decode(), sd
                ev = _EVIL_GMSTS.get(name)
                if ev and vtag == ev[0] and vdata.rstrip(b"\x00") == ev[1].rstrip(b"\x00"):
                    evil_here.append(name)
            elif tag == b"CELL":
                name, data, ambi = "", None, None
                for st, sd in _iter_subrecords(body):
                    if st == b"NAME":
                        name = _lint_zstr(sd)
                    elif st == b"DATA" and data is None:
                        data = sd
                    elif st == b"AMBI":
                        ambi = sd
                if data is None or len(data) < 12:
                    continue
                (flags,) = struct.unpack_from("<I", data, 0)
                if not flags & 1:
                    continue  # exterior
                cid = name.lower()
                if cid and cid not in _LINT_SKIP_CELLS and cid not in interior_first:
                    interior_first[cid] = (p, name)
                if not flags & 128:  # not behave-like-exterior
                    if ambi is not None and len(ambi) == 16:
                        (fog,) = struct.unpack_from("<f", ambi, 12)
                    else:
                        (fog,) = struct.unpack_from("<f", data, 8)
                    if fog == 0.0:
                        warnings.append(
                            f"[FOGBUG] '{p}'{tagfor(p)}: interior cell '{name}' has fog "
                            f"density 0.0 -- renders as a black void on some GPUs. Fix by "
                            f"setting any nonzero fog density on the cell."
                        )
            elif tag == b"PGRD":
                name, x, y = "", None, None
                for st, sd in _iter_subrecords(body):
                    if st == b"NAME":
                        name = _lint_zstr(sd)
                    elif st == b"DATA" and len(sd) >= 8:
                        x, y = struct.unpack_from("<ii", sd, 0)
                if x == 0 and y == 0 and name:  # interiors carry grid (0,0)
                    pathgrids.add(name.lower())
        if evil_here:
            warnings.append(
                f"[EVLGMST] '{p}'{tagfor(p)}: {len(evil_here)} evil GMST(s): "
                f"{', '.join(sorted(evil_here))} -- stale expansion defaults copied in "
                f"by an old Construction Set; tes3cmd clean removes them."
            )
        if tb_hits and "tribunal.esm" not in my_masters and "bloodmoon.esm" not in my_masters:
            warnings.append(
                f"[EXP-DEP] '{p}'{tagfor(p)}: scripts use Tribunal function(s) "
                f"{', '.join(sorted(tb_hits))} but the plugin doesn't master Tribunal.esm -- "
                f"fragile on non-expansion setups (tes3lint !TB-FUN)."
            )
        if bm_hits and "bloodmoon.esm" not in my_masters:
            warnings.append(
                f"[EXP-DEP] '{p}'{tagfor(p)}: scripts use Bloodmoon function(s) "
                f"{', '.join(sorted(bm_hits))} but the plugin doesn't master Bloodmoon.esm -- "
                f"fragile on non-expansion setups (tes3lint !BM-FUN)."
            )

    for cid, (plug, name) in sorted(interior_first.items()):
        if cid not in pathgrids:
            warnings.append(
                f"[NO PATHGRID] '{plug}'{tagfor(plug)}: new interior cell '{name}' has no "
                f"pathgrid anywhere in the load order -- NPCs can't pathfind there."
            )

    # scripts-twin check (customs only): an active .omwaddon/.esp shipped
    # next to an .omwscripts of the same stem (or vice versa) almost always
    # needs BOTH declared -- a missing twin silently disables the mod's Lua
    # half (or leaves scripts without their content). This was the real cause
    # behind a batch of user-reported ORPHAN confusion.
    active_lower = {str(p).lower() for p in active_order}
    for p in active_order:
        pl = str(p).lower()
        if pl not in subset_lower:
            continue
        path = index.find(p) if index else None
        if path is None:
            continue
        path = Path(path)
        stem = path.name.rsplit(".", 1)[0]
        if pl.endswith((".omwaddon", ".esp")):
            twin = path.with_name(stem + ".omwscripts")
            if twin.exists() and twin.name.lower() not in active_lower:
                warnings.append(
                    f"[TWIN] '{p}'{tagfor(p)}: '{twin.name}' sits in the same folder but "
                    f"isn't in the load order -- the mod's Lua half is disabled. Add it "
                    f"(or confirm it's optional)."
                )
        elif pl.endswith(".omwscripts"):
            for ext in (".omwaddon", ".esp"):
                twin = path.with_name(stem + ext)
                if twin.exists() and twin.name.lower() not in active_lower:
                    warnings.append(
                        f"[TWIN] '{p}'{tagfor(p)}: '{twin.name}' sits in the same folder but "
                        f"isn't in the load order -- scripts may reference content that "
                        f"never loads. Add it (or confirm it's optional)."
                    )
                    break

    stats["warnings"] = len(warnings)
    stats["interior_cells"] = len(interior_first)
    stats["pathgrids"] = len(pathgrids)
    return warnings, stats


def flatten_dict(d, parent_key="", sep="."):
    """Flatten a nested record dict into dotted keys (lists kept as whole
    values) -- ported from TES3 Conflictsolver so field comparison matches it."""
    items = []
    for k, v in d.items():
        nk = f"{parent_key}{sep}{k}" if parent_key else str(k)
        if isinstance(v, dict):
            items.extend(flatten_dict(v, nk, sep=sep).items())
        else:
            items.append((nk, v))
    return dict(items)


def _rec_deleted(rec) -> bool:
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


def _tes3conv_record_key(rec):
    """(type, id) for a tes3conv JSON record. tes3conv (via the tes3 crate)
    emits internally-tagged JSON: {"type": "Npc", "id": ...}. Most records carry
    an 'id' (or 'name'); id-less ones -- exterior cells, Landscape, path grids --
    carry a 'grid' instead, so we key those by their coords (which TES3
    Conflictsolver's plain 'id or name' misses, collapsing them all together).
    Returns None for the file header / anything with no usable id."""
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
        if cell:
            # Cell-scoped records (e.g. PathGrid): INTERIOR pathgrids all carry
            # grid (0,0) and no id, so keying by grid alone collapses every
            # interior's pathgrid across every plugin into one bogus "(0, 0)"
            # conflict. Key by the CELL name instead (plus coords for exteriors).
            rid = f"{cell} ({gx}, {gy})" if (gx or gy) else str(cell)
        elif gx is not None:
            rid = f"({gx}, {gy})"
    if rid is None or str(rid) == "":
        return None
    return (str(rtype), str(rid))


def _no_window_kwargs():
    """subprocess kwargs that stop a console window from flashing up on Windows
    when a windowed (GUI / auto-py-to-exe) build shells out to a console program
    like tes3conv -- otherwise you get one popup per plugin. No-op elsewhere."""
    import subprocess

    if os.name != "nt":
        return {}
    kw = {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
    except AttributeError:
        # STARTUPINFO/STARTF_USESHOWWINDOW are Windows-only attributes of
        # subprocess; if a future//odd build lacks them we just skip the
        # hide-the-console refinement. Nothing else in here can raise.
        pass
    return kw


class Tes3ConvSession:
    """DISK-BACKED tes3conv wrapper. Converts each plugin to a JSON file in a
    dump folder ONCE, and reads it back per-plugin on demand -- it does NOT keep
    every plugin's records in memory (that was multi-GB / OOM on a big list).
    Only the small map of plugin -> json-file-path is held. Peak memory is now
    bounded by a single plugin's JSON, not the whole load order.

    dump_dir: where to write the .json files (a temp dir if None). keep: if True
    (or an explicit dump_dir is given) the files are left in place; otherwise a
    temp dump is removed by cleanup()."""

    # Bump when the sidecar key/cell extraction changes so stale caches are
    # rebuilt (v2: pathgrids keyed by cell, not the shared "(0,0)" grid).
    _SIDECAR_VER = 2

    def __init__(self, exe, dump_dir=None, keep=False):
        import tempfile

        self.exe = exe
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
        self._json_paths = {}  # plugin path(str) -> json file path on disk

    def _json_for(self, path):
        import subprocess

        key = str(path)
        jp = self._json_paths.get(key)
        if jp and Path(jp).exists():
            return jp
        out = self.dump_dir / (Path(path).stem + ".json")
        if out.exists():
            if not self._stale(out, path):  # reuse existing JSON -- don't re-run tes3conv
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
            self._json_paths[key] = str(out)
            return str(out)
        except (OSError, subprocess.SubprocessError):
            # OSError: tes3conv missing/not executable. SubprocessError covers
            # both CalledProcessError (check=True) and TimeoutExpired.
            return None

    def _records(self, path):
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

    def record_map(self, path):
        # Built fresh each call and NOT cached, so only one plugin's records are
        # ever in memory at a time.
        m = {}
        for rec in self._records(path):
            k = _tes3conv_record_key(rec)
            if k and k not in m:
                m[k] = rec
        return m

    @staticmethod
    def _stale(json_path, plugin_path):
        """A cached JSON is stale if the plugin was modified after it was written,
        so a changed plugin re-converts. If either mtime can't be read, treat the
        cache as good (reuse) rather than needlessly re-running tes3conv."""
        try:
            return json_path.stat().st_mtime < Path(plugin_path).stat().st_mtime
        except OSError:
            return False

    def _load_sidecar(self, side, path):
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

    def _build_sidecars(self, path):
        """Read a plugin's full JSON ONCE and extract BOTH the compact record-key
        list (conflicts) and the cell list (map), writing both sidecars. Whichever
        of record_keys()/cells() is called first pays this single read; the other
        then hits its fresh sidecar -- so Check Conflicts + Cell Map together read
        each big JSON once per run, not twice."""
        import json

        keys, cells, seen = [], [], set()
        for rec in self._records(path):  # the single big-JSON read
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
            k = _tes3conv_record_key(rec)
            if not k or k in seen:
                continue
            seen.add(k)
            rtype, rid = k
            keys.append([rtype, rid, _rec_deleted(rec)])
            if str(rtype).lower() == "cell":
                data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
                flags = data.get("flags")
                interior = (
                    bool(flags & 0x01)
                    if isinstance(flags, int)
                    else (flags is not None and "INTERIOR" in str(flags).upper())
                )
                if interior:
                    cells.append(["int", str(rec.get("id") or rec.get("name") or rid), None])
                else:
                    grid = data.get("grid") or rec.get("grid")
                    if isinstance(grid, (list, tuple)) and len(grid) >= 2:
                        cells.append(["ext", int(grid[0]), int(grid[1])])
                    else:
                        mm = re.match(r"^\((-?\d+), (-?\d+)\)$", str(rid))
                        if mm:
                            cells.append(["ext", int(mm.group(1)), int(mm.group(2))])
        stem = Path(path).stem
        for name, payload in ((stem + ".keys.json", keys), (stem + ".cells.json", cells)):
            try:
                with (self.dump_dir / name).open("w", encoding="utf-8") as fh:
                    json.dump({"v": self._SIDECAR_VER, "d": payload}, fh)
            except OSError:  # noqa: PERF203
                # Per-file isolation: failing to write one sidecar must not
                # lose the other. The cache is an optimisation, never required
                # for correctness, so a write failure is silently tolerated.
                pass
        return [tuple(x) for x in keys], [tuple(x) for x in cells]

    def record_keys(self, path):
        """Compact (rectype, rid, deleted) list for every record in a plugin
        (deduped, first-wins, plus .omwaddon Lua scripts as ('LuaScript', p)) --
        all conflict DETECTION needs, a few hundred KB vs the multi-MB JSON. Served
        from a '<stem>.keys.json' sidecar; rebuilt (with the cells sidecar) only if
        the plugin changed. The on-click field diff still reads the full record."""
        cached = self._load_sidecar(self.dump_dir / (Path(path).stem + ".keys.json"), path)
        return cached if cached is not None else self._build_sidecars(path)[0]

    def cells(self, path):
        """Compact ('ext', gx, gy) / ('int', name, None) list of the CELLs a plugin
        touches -- all the cell map needs. Served from a '<stem>.cells.json'
        sidecar; rebuilt (with the keys sidecar) only if the plugin changed."""
        cached = self._load_sidecar(self.dump_dir / (Path(path).stem + ".cells.json"), path)
        return cached if cached is not None else self._build_sidecars(path)[1]

    def dumped_dir(self):
        return str(self.dump_dir)

    def cleanup(self):
        """Remove the dump unless keep is set. Honors keep regardless of whether the
        dump is a temp dir or a stable folder, so 'don't keep' still cleans up a
        stable dump on close."""
        if not self.keep:
            import shutil

            shutil.rmtree(self.dump_dir, ignore_errors=True)

    def lua_scripts(self, path):
        """Lua script paths declared by a LuaScriptsCfg record inside an
        .omwaddon/.omwgame (tes3conv's JSON for the LUAL record), so tes3conv
        mode matches the built-in parser. Field names are probed defensively."""
        out = []
        for rec in self._records(path):
            if not isinstance(rec, dict):
                continue
            if str(rec.get("type", "")).lower().replace("_", "") not in ("luascriptscfg", "lual"):
                continue
            for s in rec.get("scripts") or rec.get("mScripts") or []:
                sp = None
                if isinstance(s, dict):
                    sp = s.get("script_path") or s.get("path") or s.get("mScriptPath")
                elif isinstance(s, str):
                    sp = s
                if sp:
                    out.append(str(sp).replace("\\", "/").lower().lstrip("/"))
        return out


def diff_record_fields(session, conflict, paths):
    """Field-level comparison for one conflicting record across the plugins that
    touch it. Returns (ordered_keys, per_plugin, differing_keys):
      ordered_keys  -- dotted field keys, in first-seen order
      per_plugin    -- {plugin: {key: value}}
      differing_keys-- the subset of keys whose value differs between plugins
                       (the actual field-level conflicts). Empty if identical.
    Needs a Tes3ConvSession; returns ([], {}, set()) without one."""
    if session is None:
        return [], {}, set()
    key = (conflict["type"], conflict["id"])
    per = {}
    for p in conflict["plugins"]:
        rec = session.record_map(paths.get(p, "")).get(key) if paths.get(p) else None
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


def detect_conflicts(active_order, index, subset_names=None, session=None):
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
    touch = {}  # (type, id) -> list of (plugin, deleted)
    unreadable, scanned, rec_count = [], 0, 0
    paths = {}
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
    conflicts.sort(key=lambda c: (not c["involves_subset"], c["type"], c["id"].lower()))
    stats = {
        "scanned": scanned,
        "unreadable": unreadable,
        "records": rec_count,
        "conflicts": len(conflicts),
        "engine": "tes3conv" if session is not None else "builtin",
        "paths": paths,
    }
    return conflicts, stats


def format_conflict_report(conflicts, stats, subset_only=False, limit=None) -> str:
    """Render conflicts as a readable text report. subset_only shows just the
    ones that involve your custom mods; limit caps how many are listed."""
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


def write_conflict_csv(path, conflicts):
    """Write the full conflict list to a CSV (type, id, winner, involves_custom,
    deleted_by, plugins-in-load-order)."""
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


def filter_plugins(active_order, patterns):
    """Return (kept, excluded) filtering plugin names by case-insensitive glob
    patterns (fnmatch); a pattern with no glob chars also matches as a substring.
    Lets you drop 'touches-everything' mods (light fixes, groundcover/grass
    generators, delta/merged patches) from a conflict/cell scan."""
    pats = [p.strip().lower() for p in (patterns or []) if p and p.strip()]
    if not pats:
        return list(active_order), []
    kept, excl = [], []
    for name in active_order:
        low = name.lower()
        hit = any(
            fnmatch.fnmatch(low, p) or (("*" not in p and "?" not in p) and p in low) for p in pats
        )
        (excl if hit else kept).append(name)
    return kept, excl


def dump_tes3conv_json(session, plugins, paths, outdir):
    """Write each plugin's tes3conv JSON (read back from the session's on-disk
    spool) to outdir/<plugin>.json. Returns the number written. Creates outdir if
    needed. Needs a Tes3ConvSession."""
    import json

    if session is None:
        return 0
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
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


def detect_resource_conflicts(data_dirs, subset_dirs=None, exclude_exts=None):
    """data_dirs: the data= folders in load order (winner last). Returns
    (conflicts, stats). conflicts: [{path, providers:[dirs in order], winner,
    involves_subset}] for every relative file path present in 2+ folders. Plugin
    files are skipped (they're ordered by content=, not the VFS)."""
    subset_norm = {str(s).replace("\\", "/").rstrip("/").lower() for s in (subset_dirs or [])}
    exclude_exts = {e.lower() for e in (exclude_exts or [])}
    providers = {}  # rel_path -> [dir_index in order]
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
        conflicts.append(
            {"path": rel, "providers": prov, "winner": prov[-1], "involves_subset": involves}
        )
    conflicts.sort(key=lambda c: (not c["involves_subset"], c["path"]))
    return conflicts, {"dirs": len(dirs), "files": len(providers), "conflicts": len(conflicts)}


def format_resource_report(conflicts, stats, subset_only=False, limit=200):
    shown = [c for c in conflicts if c["involves_subset"] or not subset_only]
    n_sub = sum(1 for c in conflicts if c["involves_subset"])
    lines = [
        f"Scanned {stats['dirs']} data folder(s), {stats['files']} loose file(s): "
        f"{stats['conflicts']} conflicting file(s), {n_sub} involving your custom data paths."
    ]
    for c in (shown[:limit] if limit else shown):
        star = "* " if c["involves_subset"] else "  "
        lines.append(f"{star}{c['path']}   ({len(c['providers'])} providers, wins: {c['winner']})")
    if limit and len(shown) > limit:
        lines.append(f"  ... and {len(shown) - limit} more (save the full report).")
    return "\n".join(lines)


def write_resource_csv(path, conflicts):
    import csv

    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file_path", "providers", "winner", "involves_custom", "provider_folders"])
        for c in conflicts:
            w.writerow(
                [
                    c["path"],
                    len(c["providers"]),
                    c["winner"],
                    "yes" if c["involves_subset"] else "no",
                    " -> ".join(c["providers"]),
                ]
            )


# ---------------------------------------------------------------------------
# cell coverage map ("modmapper"): which mods touch which cells. Exterior cells
# are keyed by grid coords (for a heatmap), interior by name (for a list). Reads
# via either engine; interior/exterior is told apart the way modmapper does
# (interior = the cell's flags bit 0x01, or "INTERIOR" in the flags).
# ---------------------------------------------------------------------------


def _iter_cells(path, session=None):
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
            elif rid.startswith("Interior: "):
                yield ("int", rid[len("Interior: ") :], None)


def build_cell_coverage(active_order, index, subset_names=None, session=None):
    """Returns {"exterior": {(gx,gy):[mods]}, "interior": {name:[mods]},
    "scanned", "unreadable":[...], "subset_lower": set}. Mods are in load order."""
    subset_lower = {s.lower() for s in (subset_names or [])}
    ext, inte, unreadable, scanned = {}, {}, [], 0
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


def _cell_heat(count):
    """Heatmap fill: one mod = cool (coverage), 2+ = warmer/hotter (conflict)."""
    if count <= 1:
        return "#2f4a63"
    return {2: "#7a5a1e", 3: "#9c4a16", 4: "#b83a1a"}.get(count, "#d8342a")


def _html_escape(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_cell_map_html(coverage, title="MLOX Subset Sort — Cell Map"):
    """Self-contained HTML with three tabs: a colour-coded exterior heatmap drawn
    as a compact SVG grid (uniform squares, one per touched cell; brighter/hotter
    = more mods; click a cell to jump to its list entry), an exterior-cell list,
    and an interior-cell list. Cells your custom mods touch get a gold outline. A
    port of modmapper, fed by this tool's load order."""
    ext = coverage["exterior"]
    inte = coverage["interior"]
    subl = coverage.get("subset_lower", set())

    # Exterior grid coords can be bogus/huge (an interior cell whose grid field
    # is garbage, a mis-parse). Drop anything outside sane Morrowind+add-on
    # bounds. The map is drawn as an SVG that only emits a <rect> for each TOUCHED
    # cell (sparse -- bounded by plugin count), so absolute placement gives uniform
    # squares in every column, and there's no dense billion-cell table to OOM on.
    ext_ok = {
        k: v
        for k, v in ext.items()
        if -CELL_GRID_LIMIT <= k[0] <= CELL_GRID_LIMIT
        and -CELL_GRID_LIMIT <= k[1] <= CELL_GRID_LIMIT
    }
    dropped = len(ext) - len(ext_ok)

    def anchor(gx, gy):
        return f"e_{gx}_{gy}".replace("-", "m")

    def modattr(mods):
        # exact-match token list for the focus filter: |a.esp|b.esp|
        return _html_escape("|" + "|".join(m.lower() for m in mods) + "|")

    # every mod that touches any cell, customs first -- for the focus dropdown
    all_mods = {}
    for mods in list(ext.values()) + list(inte.values()):
        for m in mods:
            all_mods.setdefault(m.lower(), m)
    focus_opts = "".join(
        f'<option value="{_html_escape(low)}">{_html_escape(all_mods[low])}'
        f'{" ★" if low in subl else ""}</option>'
        for low in sorted(all_mods, key=lambda x: (x not in subl, x))
    )

    grid = '<p class="sub">No exterior cells touched.</p>'
    if ext_ok:
        xs = [k[0] for k in ext_ok]
        ys = [k[1] for k in ext_ok]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        w, h = (maxx - minx + 1), (maxy - miny + 1)
        trace(f"cell map: {len(ext_ok)} ext cells, bbox {w}x{h}, dropped {dropped}")
        rects = []
        for (gx, gy), mods in ext_ok.items():
            px = (gx - minx) * CELL_MAP_STEP_PX
            py = (maxy - gy) * CELL_MAP_STEP_PX  # north (max y) at the top
            custom = any(m.lower() in subl for m in mods)
            tip = f"({gx}, {gy}) — {len(mods)} mod(s): " + ", ".join(mods)
            stroke = ' stroke="#ffd24a" stroke-width="1.4"' if custom else ""
            rects.append(
                f'<rect x="{px}" y="{py}" width="{CELL_MAP_CELL_PX}" height="{CELL_MAP_CELL_PX}" '
                f'fill="{_cell_heat(len(mods))}"{stroke} class="cell" '
                f'data-t="{_html_escape(tip)}" data-m="{modattr(mods)}" '
                f"onclick=\"jump('{anchor(gx, gy)}')\"></rect>"
            )
        svg = (
            f'<svg width="{w*CELL_MAP_STEP_PX}" height="{h*CELL_MAP_STEP_PX}" viewBox="0 0 {w*CELL_MAP_STEP_PX} {h*CELL_MAP_STEP_PX}" '
            f'xmlns="http://www.w3.org/2000/svg">' + "".join(rects) + "</svg>"
        )
        grid = f'<div class="mapwrap">{svg}</div>'

    ext_rows = []
    for (gx, gy), mods in sorted(ext_ok.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        custom = any(m.lower() in subl for m in mods)
        cls = ' class="cust"' if custom else ""
        ext_rows.append(
            f'<tr id="{anchor(gx,gy)}"{cls} data-m="{modattr(mods)}">'
            f"<td>({gx}, {gy})</td><td>{len(mods)}</td>"
            f'<td>{_html_escape(", ".join(mods))}</td></tr>'
        )
    int_rows = []
    for name, mods in sorted(inte.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        custom = any(m.lower() in subl for m in mods)
        cls = ' class="cust"' if custom else ""
        int_rows.append(
            f'<tr{cls} data-m="{modattr(mods)}"><td>{_html_escape(name)}</td>'
            f"<td>{len(mods)}</td>"
            f'<td>{_html_escape(", ".join(mods))}</td></tr>'
        )
    ext = ext_ok
    n_ext_conf = sum(1 for m in ext.values() if len(m) > 1)
    n_int_conf = sum(1 for m in inte.values() if len(m) > 1)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_html_escape(title)}</title>
<style>
 body{{background:#101013;color:#c8c8c8;font-family:Segoe UI,Arial,sans-serif;margin:16px;}}
 h1{{color:#e8905a;font-size:20px;}} .sub{{color:#8f8f8f;font-size:13px;}}
 .legend{{margin-top:12px;line-height:1.7;}}
 .tabs{{margin-top:24px;margin-bottom:4px;}}
 .tabs button{{background:#20242a;color:#ddd;border:1px solid #3a3a3a;padding:6px 14px;margin-right:4px;cursor:pointer;}}
 .tabs button.on{{background:#8a3a12;color:#fff;}}
 .tab{{display:none;margin-top:10px;}} .tab.on{{display:block;}}
 .legend span{{display:inline-block;padding:2px 8px;margin-right:6px;border-radius:3px;color:#111;font-size:12px;}}
 .mapwrap{{overflow:auto;max-height:74vh;border:1px solid #333;background:#06111c;display:inline-block;max-width:100%;}}
 .mapwrap svg{{display:block;}}
 rect.cell{{cursor:pointer;}} rect.cell:hover{{stroke:#fff;stroke-width:1.4;}}
 #tt{{position:fixed;pointer-events:none;display:none;z-index:99;max-width:440px;
   background:#000;color:#eee;border:1px solid #555;border-radius:3px;padding:3px 7px;font-size:12px;}}
 table.list{{border-collapse:collapse;width:100%;font-size:13px;}}
 .list td,.list th{{border-bottom:1px solid #262626;padding:4px 8px;text-align:left;vertical-align:top;}}
 .list th{{color:#9a9a9a;position:sticky;top:0;background:#101013;}} tr.cust td{{color:#ff9b6b;}}
 tr.hl td{{background:#3a2a10;}}
 input.f{{background:#1c1c22;color:#ddd;border:1px solid #3a3a3a;padding:6px;width:320px;margin:6px 0;}}
 .focusbar{{margin-top:10px;}}
 .focusbar select{{background:#1c1c22;color:#ddd;border:1px solid #3a3a3a;padding:5px;max-width:420px;}}
 .focusbar button{{background:#20242a;color:#ddd;border:1px solid #3a3a3a;padding:5px 10px;margin-left:6px;cursor:pointer;}}
 #focusinfo{{margin-top:4px;max-width:900px;}}
 rect.cell.dim{{opacity:.13;}}
</style></head><body>
<div id="tt"></div>
<h1>{_html_escape(title)}</h1>
<p class="sub">Scanned {coverage['scanned']} plugin(s). Exterior: {len(ext)} cell(s) touched
 ({n_ext_conf} by 2+ mods). Interior: {len(inte)} cell(s) touched ({n_int_conf} by 2+ mods).
 Cells your custom mods touch are highlighted (gold outline / orange text).</p>
<div class="legend">Mods per cell:
 <span style="background:#2f4a63;color:#fff;">1</span><span style="background:#7a5a1e;">2</span>
 <span style="background:#9c4a16;">3</span><span style="background:#b83a1a;color:#fff;">4</span>
 <span style="background:#d8342a;color:#fff;">5+</span> &nbsp;(north up; hover a cell for its mods, click it to jump to the list)</div>
<div class="focusbar">Focus on mod:
 <select id="focus" onchange="setFocus(this.value)"><option value="">— all mods —</option>{focus_opts}</select>
 <button onclick="document.getElementById('focus').value='';setFocus('')">Clear</button>
 <div id="focusinfo" class="sub"></div></div>
<div class="tabs">
 <button id="b0" class="on" onclick="show(0)">Map</button>
 <button id="b1" onclick="show(1)">Exterior list ({len(ext)})</button>
 <button id="b2" onclick="show(2)">Interior list ({len(inte)})</button>
</div>
<div id="t0" class="tab on">{grid}</div>
<div id="t1" class="tab"><input class="f" placeholder="Filter exterior cells / mods..." onkeyup="ff('xt')">
 <table class="list" id="xt"><thead><tr><th>Cell (x, y)</th><th>#</th><th>Mods (load order, last wins)</th></tr></thead>
 <tbody>{''.join(ext_rows) or '<tr><td colspan=3 class=sub>None.</td></tr>'}</tbody></table></div>
<div id="t2" class="tab"><input class="f" placeholder="Filter interior cells / mods..." onkeyup="ff('it')">
 <table class="list" id="it"><thead><tr><th>Cell</th><th>#</th><th>Mods (load order, last wins)</th></tr></thead>
 <tbody>{''.join(int_rows) or '<tr><td colspan=3 class=sub>None.</td></tr>'}</tbody></table></div>
<script>
 function show(n){{for(var i=0;i<3;i++){{document.getElementById('t'+i).className=i==n?'tab on':'tab';
  document.getElementById('b'+i).className=i==n?'on':'';}}}}
 function jump(a){{show(1);var el=document.getElementById(a);
  if(el){{el.scrollIntoView({{block:'center'}});el.classList.add('hl');
   setTimeout(function(){{el.classList.remove('hl');}},2200);}}}}
 (function(){{var tt=document.getElementById('tt');
  document.addEventListener('mouseover',function(e){{var r=e.target;
   if(r&&r.classList&&r.classList.contains('cell')){{tt.textContent=r.getAttribute('data-t');tt.style.display='block';}}}});
  document.addEventListener('mousemove',function(e){{if(tt.style.display=='block'){{
   tt.style.left=(e.clientX+12)+'px';tt.style.top=(e.clientY+12)+'px';}}}});
  document.addEventListener('mouseout',function(e){{var r=e.target;
   if(r&&r.classList&&r.classList.contains('cell')){{tt.style.display='none';}}}});}})();
 var Q={{xt:'',it:''}}, FOCUS='';
 function match(r){{return !FOCUS||(r.getAttribute('data-m')||'').indexOf('|'+FOCUS+'|')>-1;}}
 function apply(id){{document.querySelectorAll('#'+id+' tbody tr').forEach(function(r){{
   var okQ=!Q[id]||r.innerText.toLowerCase().indexOf(Q[id])>-1;
   r.style.display=(okQ&&match(r))?'':'none';}});}}
 function ff(id){{Q[id]=event.target.value.toLowerCase();apply(id);}}
 function setFocus(v){{FOCUS=(v||'').toLowerCase();
  document.querySelectorAll('rect.cell').forEach(function(r){{
   r.classList.toggle('dim',FOCUS&&!match(r));}});
  apply('xt');apply('it');
  var info=document.getElementById('focusinfo');
  if(!FOCUS){{info.textContent='';return;}}
  var nE=0,nI=0,co={{}};
  document.querySelectorAll('#xt tbody tr').forEach(function(r){{if(match(r)){{nE++;countCo(r,co);}}}});
  document.querySelectorAll('#it tbody tr').forEach(function(r){{if(match(r)){{nI++;countCo(r,co);}}}});
  var names=Object.keys(co).sort(function(a,b){{return co[b]-co[a];}});
  var top=names.slice(0,14).map(function(n){{return n+' ('+co[n]+')';}}).join(', ');
  info.textContent='Touches '+nE+' exterior + '+nI+' interior cell(s). '+
   (names.length?'Shares cells with '+names.length+' other mod(s): '+top+
    (names.length>14?', …':''):'No other mod touches these cells.');}}
 function countCo(r,co){{(r.getAttribute('data-m')||'').split('|').forEach(function(m){{
   if(m&&m!=FOCUS){{co[m]=(co[m]||0)+1;}}}});}}
</script>
</body></html>
"""


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


def read_cfg(path: Path):
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


def backup_file(path: Path, no_backup: bool):
    """Writes a timestamped .bak-YYYYMMDD-HHMMSS copy of an existing file
    before it gets overwritten. No-op if no_backup, or if the file doesn't
    exist yet (nothing to back up)."""
    if no_backup or not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak-{stamp}")
    # Copy BYTES, not decoded text: a backup must be byte-identical. Decoding
    # and re-encoding used to raise UnicodeDecodeError on a cfg containing
    # non-UTF-8 bytes (e.g. a cp1252 accented mod folder), which meant the
    # user could not back up -- or export -- at all.
    backup.write_bytes(path.read_bytes())
    print(f"Backup written: {backup}")


def write_cfg(path: Path, lines, segments, dry_run, no_backup):
    """
    segments: list of (positions, new_lines) pairs. Each segment's block of
    original lines gets replaced (at the position of its first line) with
    new_lines; other lines are left completely untouched.
    """
    replace_at = {}
    skip = set()
    trailing_extra = []
    for positions, new_lines in segments:
        if not positions:
            # no anchor lines of this kind existed in the file at all --
            # tack the new lines on at the end instead of silently dropping them
            trailing_extra.extend(new_lines)
            continue
        replace_at[positions[0]] = new_lines
        skip.update(positions)

    new_lines_out = []
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
    path.write_text(
        "\n".join(new_lines_out) + "\n",
        encoding=CFG_WRITE_ENCODING,
        errors=CFG_ERRORS,
    )
    print(f"Wrote updated: {path}")


# ---------------------------------------------------------------------------
# mlox rule parsing (Order / NearStart / NearEnd only)
# ---------------------------------------------------------------------------

# Rule-file parsing now lives in mlox_subset/rules/parser.py, re-exported so
# existing callers keep working. Behaviour pinned by tests/test_differential.py.
from mlox_subset.configurator import (
    REMOVE_KEYS,
    cfg_line_value,
    configurator_remove_matches,
    customization_string_list,
    detect_data_quoting,
    extract_data_path_value,
    find_anchor_index,
    format_data_line,
    generate_customizations_toml,
    infer_data_path_anchors,
    insert_data_paths,
    normalize_data_path,
    preview_configurator_result,
    simulate_configurator_apply,
    toml_value,
)
from mlox_subset.momw import (
    base_order_matches_yml,
    curated_for_list,
    needs_cleaning_set,
    parse_plugin_order_yml,
)
from mlox_subset.net import (
    ALLOWED_URL_SCHEMES,
    MAX_DOWNLOAD_BYTES,
    PLUGIN_ORDER_URLS,
    RULES_URL_TEMPLATE,
    fetch_url_bytes,
    rule_file_ages,
    update_plugin_order_yml,
    update_rule_files,
)
from mlox_subset.plugins import (
    PLUGIN_EXTS,
    TES3_MIN_PLUGIN_SIZE as _TES3_MIN_PLUGIN_SIZE,
    PluginFileIndex,
    list_plugins_in_dir,
    plugin_version as _plugin_version,
    read_plugin_description as _read_plugin_description,
)
from mlox_subset.rules import (
    ORDER_NAME_RE as _RE_ORDER_NAME,
    TOP_KEYWORDS,
    TOP_RE,
    check_predicates,
    describe_node,
    evaluate_node,
    get_triggered_plugins,
    load_rule_blocks,
    load_rules_raw_text,
    parse_mlox_file,
    parse_mlox_lisp,
    strip_comment,
    tokenize_mlox_logic,
)
from mlox_subset.versions import (
    format_version as _format_version,
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


def scan_mod_directories(start_path, output_path=None):
    """Scan start_path for mod data folders. Writes the result subset file to
    output_path if given, and returns (lines, n_folders, n_plugins).

    Each matched folder contributes its absolute path (a data= entry) followed
    by the plugin filenames directly inside it (content= entries), then a blank
    line -- so a mod with both assets and a plugin adds its data path AND its
    plugin, while an assets-only mod adds just its data path."""
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
        f"Scanned '{os.path.abspath(start_path)}': "  # noqa: PTH100
        f"{n_folders} mod folder(s), {n_plugins} plugin(s)."
    )
    if output_path is not None:
        print(f"Wrote subset file: {output_path}")
    return lines, n_folders, n_plugins


# ---------------------------------------------------------------------------
# subset extraction
# ---------------------------------------------------------------------------


def basename_if_plugin(value: str):
    v = value.strip().strip('"').strip("'")
    v = v.replace("\\", "/")
    name = v.rsplit("/", 1)[-1]
    if name.lower().endswith(PLUGIN_EXTS):
        return name
    return None


def extract_subset_from_subset_file(path: Path):
    """
    Accepts either:
      - a plain text file, one entry per line (# comments allowed), OR
      - a minimal TOML file like:
            subset = ["GoHome.esp", "go-home.omwscripts"]
            data = ["mods/SomeModFolder"]

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
            import tomli as tomllib  # type: ignore
        data = tomllib.loads(text)
        plugins, data_inserts = [], []
        for raw in data.get("subset", []):
            _classify_subset_entry(str(raw), plugins, data_inserts, str(path))
        data_inserts.extend(
            {"value": str(raw), "after": None, "before": None} for raw in data.get("data", [])
        )
        return plugins, data_inserts

    return extract_subset_from_lines(text.splitlines(), source=str(path))


def _classify_subset_entry(raw, plugins, data_inserts, source):
    """Classify one raw subset entry: a recognized plugin extension makes it a
    plugin; otherwise a slash/backslash makes it a data= folder path; otherwise
    it's skipped with a warning (nothing safe to guess from a bare word).

    Both `plugins` and `data_inserts` are APPENDED TO IN PLACE and nothing is
    returned -- the caller passes the accumulators it wants filled.
    """
    raw = raw.strip()
    if not raw:
        return
    name = basename_if_plugin(raw)
    if name:
        plugins.append(name)
    elif "/" in raw.replace("\\", "/"):
        data_inserts.append({"value": raw, "after": None, "before": None})
    else:
        print(
            f"WARNING: '{raw}' from {source} doesn't look like a plugin filename or a "
            f"data folder path (no recognized extension, no slash) -- skipping.",
            file=sys.stderr,
        )


def _strip_line_comment(line: str) -> str:
    """Strip a '#' comment from a subset-file line, but ONLY when the '#' begins
    the line (after optional whitespace) or is preceded by whitespace. A '#'
    that's part of a filename or path -- e.g. 'FMI_#NotAllDunmer.ESP' -- has no
    space in front of it, so it's left intact. (Previously a naive split on '#'
    truncated such names to 'FMI_', which then classified as neither a plugin
    nor a path and got dropped.)"""
    if line.lstrip().startswith("#"):
        return ""
    m = re.search(r"\s#", line)
    return line[: m.start()] if m else line


def extract_subset_from_lines(lines, source="subset lines"):
    """Classify a list of raw text lines (one plugin filename or data folder
    path each; a '#' at line start or after whitespace begins a comment) into
    (plugin_names, data_inserts) -- the same plain-text form
    extract_subset_from_subset_file() reads, but from an in-memory list. Used by
    the GUI's 'scan into memory' path so a scan can feed the sort without writing
    a file to disk."""
    plugins, data_inserts = [], []
    for line in lines:
        _classify_subset_entry(_strip_line_comment(line), plugins, data_inserts, source)
    return plugins, data_inserts


def extract_subset_from_toml(toml_path: Path):
    """
    Returns (content_subset, data_inserts, replace_dest_names):
      content_subset      -- plugin filenames to feed into the mlox sort
      data_inserts        -- [{"value","after","before"}] folder paths to anchor
                              directly into the data= list (mlox doesn't cover these)
      replace_dest_names  -- subset of content_subset that came from a "replace"
                              block's "dest", not an "insert" -- included in the
                              mlox sort so drift can be detected, but must NOT get
                              a synthesized insert block in --emit-toml output
                              (see generate_customizations_toml)
      subset_listnames    -- {plugin_name: listName} -- which [[Customizations]]
                              block each subset plugin came from, so predicate
                              warnings can point back at the specific mod entry
                              in the TOML that's responsible (see check_predicates)
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "Need Python 3.11+ (tomllib) or `pip install tomli --break-system-packages` "
                "to parse the customizations TOML."
            ) from exc

    data = tomllib.loads(read_toml_text(toml_path))
    subset = []
    data_inserts = []
    replace_dest_names = set()
    subset_listnames = {}

    def handle_insert(value, listname, after=None, before=None, is_replace=False):
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
            handle_insert(ins.get("insert", ""), listname, ins.get("after"), ins.get("before"))
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
    subset = [s for s in subset if not (s.lower() in _seen or _seen.add(s.lower()))]
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


def read_savegame_content_files(path):
    """Content files an OpenMW .omwsave depends on. Saves are ESM3 files: a
    TES3 header record, then a SAVE record whose DEPE subrecords each carry
    one content filename (components/esm3/savedgame.cpp). Returns
    (files, error): files is None on failure."""
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


def check_savegame_against_order(save_path, active_order):
    """(save_files, missing, error): which of the save's content files are
    absent from the given load order. A missing file means OpenMW will refuse
    to load (or badly degrade) that save."""
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


def scan_backups(dirs, cfg_path=None, max_depth=4):
    """Find backup files this tool (and tes3cmd / the Configurator) leave
    behind: *.preclean.bak, *.masterfix.bak, tes3cmd's 'name~1.ext', and
    timestamped '*.bak-YYYYMMDD-HHMMSS' / Configurator '*.backup.*' copies.
    Returns [(backup_path, original_path_or_None, kind)]."""
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
    ";; Personal mlox rules -- written by you (with help from MLOX Subset Sort's\n"
    ";; rule maker). Keep this file LAST in the rule-files list: later files win\n"
    ";; rule conflicts, so your rules override mlox_base/mlox_user.\n"
    ";; Syntax: https://morrowind-modding.github.io/modding-tools/sorting-plugin-load-order/mlox/mlox-rule-guidelines\n"
)


def order_rule_frozen_conflicts(names, final_order, curated_lower):
    """For a proposed [Order] rule, return the consecutive (earlier, later)
    name pairs that CONTRADICT the frozen curated order: both names are
    curated plugins the sort won't reorder, but the rule wants them opposite
    to how they currently sit. mlox discards such edges as cycles (per the
    rule guidelines: "whenever we encounter a rule that would cause a cycle,
    it is discarded"), so the rule would silently not take effect for those
    pairs. Wildcard/<VER> tokens are skipped -- they don't resolve to one
    position. Purely advisory; used to warn before writing a rule."""
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


def append_user_rule(path, keyword, names, comment=None):
    """Append one mlox ordering rule block to a personal rules file, creating
    the file (with an explanatory header) if it doesn't exist yet.

    keyword: 'order' (the names are a load-order chain, first loads first),
    'nearstart' or 'nearend' (each name is an independent position hint).
    Names may use mlox wildcards (*, ?, <VER>) but must end in a recognized
    plugin extension -- the same validation the rule parser applies, so a rule
    that gets written is a rule that will load. Returns the text written."""
    kw = str(keyword).strip().lower()
    titles = {"order": "Order", "nearstart": "NearStart", "nearend": "NearEnd"}
    if kw not in titles:
        raise ValueError(f"unsupported rule type: {keyword!r}")
    clean = [str(n).strip() for n in names if str(n).strip()]
    if not clean:
        raise ValueError("no plugin names given")
    if kw == "order" and len(clean) < 2:
        raise ValueError("[Order] needs at least two plugin names (first loads first)")
    seen = set()
    for n in clean:
        if any(c in n for c in "[];\n"):
            raise ValueError(f"invalid character in name/pattern: {n!r}")
        m = _RE_ORDER_NAME.match(n)
        if not m or m.group(0) != n:
            raise ValueError(
                f"{n!r} must end in a plugin extension "
                f"(.esp/.esm/.omwaddon/.omwgame/.omwscripts, optionally '*')"
            )
        if n.lower() in seen:
            # a plugin listed twice orders it relative to itself -- a
            # self-cycle mlox would discard; always a mistake
            raise ValueError(
                f"'{n}' is listed more than once -- a plugin can't be "
                f"ordered relative to itself"
            )
        seen.add(n.lower())
    parts = []
    if comment and str(comment).strip():
        parts += [f";; {line}" for line in str(comment).strip().splitlines()]
    parts.append(f"[{titles[kw]}]")
    parts += clean
    text = "\n".join(parts) + "\n"
    p = Path(path)
    if p.exists():
        existing = p.read_text(encoding="utf-8-sig", errors="replace")
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        p.write_text(existing + sep + text, encoding="utf-8")
    else:
        p.write_text(USER_RULES_HEADER + "\n" + text, encoding="utf-8")
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

# Graph primitives now live in mlox_subset/sort/graph.py.
from mlox_subset.sort import (
    build_and_sort,
    expand_pattern,
    is_master_file as _is_master_file,
    would_create_cycle,
)

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


def _section(title: str):
    print(f"\n{'=' * 70}\n {title}\n{'=' * 70}")


def _subsection(title: str):
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
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
        "for the default log (mlox_subset_sort_trace.log), or --trace PATH to choose "
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
    return ap


def all_scan_dirs(data_order, raw_toml_data_inserts=None, data_inserts=None):
    """Every folder a plugin/resource may live in for THIS run: the cfg's
    existing data= folders PLUS the pending custom data-path inserts (from a
    mods-folder scan or a customizations TOML) that aren't in the cfg yet.

    Conflict / cell-map / resource scans must search this combined list, not
    just the cfg's dirs -- otherwise your custom mods are invisible to those
    tools until AFTER the cfg has been written, which defeats the point of
    checking them before committing to an order. (Pending dirs are appended
    after the cfg dirs; that matches where they'd typically land.)"""
    dirs = [v for v in (extract_data_path_value(line) for line in (data_order or [])) if v]
    seen = {str(d).lower() for d in dirs}
    for src in (data_inserts, raw_toml_data_inserts):
        for d in src or []:
            v = d.get("value")
            if v and str(v).lower() not in seen:
                seen.add(str(v).lower())
                dirs.append(v)
    return dirs


def pending_custom_dirs(raw_toml_data_inserts=None, data_inserts=None):
    """Just the pending custom data-path folders (deduped, in declared order)
    -- used to flag which side of a conflict is YOUR mod."""
    out, seen = [], set()
    for src in (data_inserts, raw_toml_data_inserts):
        for d in src or []:
            v = d.get("value")
            if v and str(v).lower() not in seen:
                seen.add(str(v).lower())
                out.append(v)
    return out


def compute_plan(args) -> dict:
    """
    The "read input + run mlox + evaluate warnings" half of a run -- never
    writes anything. Returns a plan dict that write_plan() can act on
    (optionally with a manually-overridden final_order, e.g. from a GUI's
    drag-to-reorder panel, instead of recomputing).
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
    ):
        raise SystemExit(
            "Provide --customizations, --subset, --subset-file, or --scan-dir so there's something to sort."
        )

    _section("READING INPUT")

    subset = list(args.subset)
    data_inserts = []
    raw_toml_data_inserts = (
        []
    )  # captured regardless of --sort-data-paths, for --emit-toml passthrough
    original_content_values = {}
    original_toml_data = {}
    replace_dest_names = set()
    subset_origins = (
        {}
    )  # {plugin_name_lower: "where this came from"} -- for check_predicates' warnings

    if args.subset_file:
        file_plugins, file_data_inserts = extract_subset_from_subset_file(args.subset_file)
        subset.extend(file_plugins)
        raw_toml_data_inserts.extend(file_data_inserts)
        print(
            f"  {args.subset_file}: {len(file_plugins)} plugin(s), {len(file_data_inserts)} data path(s)"
        )
        if args.sort_data_paths:
            data_inserts.extend(file_data_inserts)
        elif file_data_inserts:
            print(
                f"  NOTE: data path insertions found but not sorted (pass --sort-data-paths to "
                f"include them): {', '.join(d['value'] for d in file_data_inserts)}"
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
            f"  in-memory scan: {len(mem_plugins)} plugin(s), {len(mem_data_inserts)} data path(s)"
        )
        if args.sort_data_paths:
            data_inserts.extend(mem_data_inserts)
        elif mem_data_inserts:
            print(
                f"  NOTE: data path insertions found but not sorted (pass --sort-data-paths to "
                f"include them): {len(mem_data_inserts)} path(s)"
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
            f"  {args.customizations}: {len(toml_subset)} content plugin(s), "
            f"{len(toml_data_inserts)} data path(s)"
        )
        if args.sort_data_paths:
            data_inserts.extend(toml_data_inserts)
        elif toml_data_inserts:
            print(
                f"  NOTE: data path insertions found but not sorted (pass --sort-data-paths to "
                f"include them): {', '.join(d['value'] for d in toml_data_inserts)}"
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
                import tomli as tomllib  # type: ignore
            original_toml_data = tomllib.loads(read_toml_text(args.customizations))

    # de-dupe case-insensitively, PRESERVING declaration order (scan order /
    # the order written in the subset file or TOML). Unconstrained mods keep
    # this order at the end of the load, instead of being alphabetized.
    _seen = set()
    subset = [s for s in subset if not (s.lower() in _seen or _seen.add(s.lower()))]
    if not subset and not data_inserts and not raw_toml_data_inserts:
        raise SystemExit("No subset plugins or data paths found -- nothing to do.")

    lines, content_positions, content_order, data_positions, data_order = read_cfg(args.cfg)
    base_order_names = [name for name, _ in content_order]

    final_order = None
    data_result = None
    predicate_warnings = []
    custom_anchors = {}  # {custom_lower: (how, anchor_name)} from build_and_sort

    # --- plugin-order.yml: curated-vs-custom split (opt-in) -----------------
    # With a --list-name, curated plugins (those the yml says belong to that
    # list) are the list's responsibility -- we drop them from the subset so
    # this tool never reorders them, leaving only YOUR true custom additions to
    # sort. Everything here is guarded; a missing/garbled yml just skips the
    # feature rather than failing the run.
    yml_entries, curated_set, curated_order, yml_warnings = [], set(), [], []
    declared_lower = {
        s.lower() for s in subset
    }  # everything you declared, pre-split (for orphan check)
    plugin_order_yml = getattr(args, "plugin_order_yml", None)
    list_name = getattr(args, "list_name", None)
    if plugin_order_yml:
        _section("PLUGIN-ORDER.YML (MOMW source of truth)")
        try:
            yml_entries = parse_plugin_order_yml(Path(plugin_order_yml))
            print(f"  Loaded {len(yml_entries)} plugin entries from {Path(plugin_order_yml).name}")
        except Exception as e:  # noqa: BLE001 -- untrusted YAML, advisory only
            # parse_plugin_order_yml runs PyYAML (or our fallback parser) over a
            # community-maintained file. These are optional cross-checks; any
            # parser failure must downgrade to a warning, never abort the sort.
            print(f"  WARNING: could not read plugin-order.yml ({e}) -- skipping yml checks.")
            yml_entries = []
        if yml_entries and not list_name:
            print(
                "  NOTE: no list name given -- can't separate curated-list plugins from your "
                "custom ones, so curated/redundant/orphan/order checks are skipped "
                "(needs-cleaning notes still work)."
            )
        if yml_entries and list_name:
            curated_set, curated_order = curated_for_list(yml_entries, list_name)
            print(f"  '{list_name}': {len(curated_set)} curated plugin(s) on this list")
            if not curated_set:
                print(
                    f"  WARNING: no plugins found for list '{list_name}' in the yml -- check the "
                    f"list name spelling. Curated-set checks skipped."
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
                    f"  Excluded {len(redundant)} curated plugin(s) from the sort: {', '.join(redundant)}"
                )

    if subset:
        _section(f"SORTING {len(subset)} PLUGIN(S)")
        print(f"  {', '.join(subset)}")

        base_lower = {n.lower() for n in base_order_names}
        already_present = [s for s in subset if s.lower() in base_lower]
        new_plugins = [s for s in subset if s.lower() not in base_lower]
        if already_present:
            print(
                f"\n  Already in cfg, will be repositioned within it: {', '.join(already_present)}"
            )
        if new_plugins:
            print(f"  Not in cfg yet, will be inserted: {', '.join(new_plugins)}")

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
                    f"  Read header masters for {len(masters)} of {len(subset)} custom plugin(s)."
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
                if n.lower() not in curated_set and n.lower() not in declared_lower
            )
            yml_warnings.extend(base_order_matches_yml(base_order_names, curated_order))
        if yml_warnings:
            _section(f"{len(yml_warnings)} PLUGIN-ORDER.YML WARNING(S) -- read-only, not enforced")
            for w in yml_warnings:
                print(f"\n{w}")
        else:
            print(_("\n  No plugin-order.yml warnings."))

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
        print(f"  WARNING: master check failed: {e}", file=sys.stderr)

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
                    f"\n[STALE] '{_artifact}' is older than {len(_newer)} active plugin(s) "
                    f"(e.g. {', '.join(_newer[:3])}{', ...' if len(_newer) > 3 else ''}) -- "
                    f"re-run momw-configurator so it gets rebuilt against the current load order."
                )
    except Exception:  # noqa: BLE001 -- purely cosmetic staleness hint
        # This block only prints an optional "[STALE]" advisory about generated
        # artifacts. It touches mtimes on paths that may not exist; there is no
        # failure here worth showing the user, let alone aborting for.
        pass

    # --- TES3 record-level conflict scan (opt-in, read-only) ----------------
    conflicts = []
    want_conflicts = getattr(args, "check_conflicts", False)
    cell_map_out = getattr(args, "cell_map", None)
    want_resources = getattr(args, "resource_conflicts", False)
    # Search the cfg's data= dirs AND the pending custom data paths, so the
    # scans can see your custom mods BEFORE the cfg is updated.
    conf_dirs = all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts)
    if want_conflicts or cell_map_out:
        active = final_order if final_order else base_order_names
        active, _excl = filter_plugins(active, getattr(args, "exclude", None))
        if _excl:
            print(f"  (excluded {len(_excl)} plugin(s) by --exclude)")
        cindex = PluginFileIndex(conf_dirs)
        conv = find_tes3conv(
            explicit=getattr(args, "tes3conv", None),
            extra_dirs=[str(args.cfg.parent) if args.cfg else None],
        )
        _dump = getattr(args, "json_dump_dir", None)
        csession = (
            Tes3ConvSession(conv, dump_dir=str(_dump) if _dump else None, keep=bool(_dump))
            if conv
            else None
        )  # disk-backed, shared across both scans
        if csession and _dump:
            print(f"  Keeping tes3conv JSON dump in: {csession.dumped_dir()}")

        if want_conflicts:
            _section("TES3 RECORD CONFLICTS (read-only)")
            print(
                f"  Engine: {'tes3conv (' + conv + ')' if conv else 'built-in parser (record-level)'}"
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
                    print(f"\n  Wrote conflict report: {out}")
                except OSError as e:
                    print(f"  WARNING: could not write conflict CSV: {e}", file=sys.stderr)

        if cell_map_out:
            _section("CELL MAP (which mods touch which cells)")
            cov = build_cell_coverage(active, cindex, subset_names=subset, session=csession)
            try:
                Path(cell_map_out).write_text(generate_cell_map_html(cov), encoding="utf-8")
                print(
                    f"  Scanned {cov['scanned']} plugin(s): {len(cov['exterior'])} exterior + "
                    f"{len(cov['interior'])} interior cell(s) touched."
                )
                print(f"  Wrote cell map: {cell_map_out}  (open it in a browser)")
            except OSError as e:
                print(f"  WARNING: could not write cell map: {e}", file=sys.stderr)

        if csession is not None:
            csession.cleanup()  # drop the temp JSON spool (no-op if --json-dump-dir kept it)

    if getattr(args, "lint", False):
        _section("LINT (tes3lint-style checks, read-only)")
        _lactive = final_order if final_order else base_order_names
        _lactive, _lexcl = filter_plugins(_lactive, getattr(args, "exclude", None))
        if _lexcl:
            print(f"  (excluded {len(_lexcl)} plugin(s) by --exclude)")
        _lw, _ls = lint_plugins(
            _lactive, PluginFileIndex(conf_dirs), subset_names=subset, origins=subset_origins
        )
        print(
            f"  Scanned {_ls.get('scanned', 0)} plugin(s); "
            f"{_ls.get('interior_cells', 0)} interior cell(s), "
            f"{_ls.get('pathgrids', 0)} interior pathgrid(s)."
        )
        for _w in _lw:
            print(f"\n{_w}")
        if not _lw:
            print(_("\n  No lint findings."))

    if want_resources:
        _section("DATA-PATH RESOURCE (VFS) CONFLICTS (read-only)")
        subset_dirs = pending_custom_dirs(raw_toml_data_inserts, data_inserts)
        rconf, rstats = detect_resource_conflicts(conf_dirs, subset_dirs=subset_dirs)
        print(format_resource_report(rconf, rstats, limit=200))
        rout = getattr(args, "resources_out", None)
        if rout and rconf:
            try:
                write_resource_csv(rout, rconf)
                print(f"\n  Wrote resource report: {rout}")
            except OSError as e:
                print(f"  WARNING: could not write resource CSV: {e}", file=sys.stderr)

    if data_inserts and not args.sort_data_paths:
        _section(f"{len(data_inserts)} DATA PATH(S) FOUND BUT NOT SORTED")
        print(_("  Pass --sort-data-paths to enable:"))
        for d in data_inserts:
            print(f"  {d['value']}")

    if data_inserts and args.sort_data_paths:
        _section(f"SORTING {len(data_inserts)} DATA PATH(S)")
        infer_data_path_anchors(data_inserts, data_order, final_order, args.cfg)
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
            print(f"  {line}{'  <-- inserted' if is_new else ''}")

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
        "base_order_names": base_order_names,
        "conflicts": conflicts,
        # every folder the scans should search THIS run (cfg data= dirs +
        # pending custom data paths), and just the pending custom folders --
        # so conflict/cell-map/resource scans can see your custom mods before
        # the cfg is written
        "scan_dirs": all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts),
        "custom_data_dirs": pending_custom_dirs(raw_toml_data_inserts, data_inserts),
    }


def write_plan(
    args,
    plan: dict,
    final_order: list | None = None,
    data_order: list | None = None,
    disabled_plugins=None,
    disabled_data=None,
) -> dict:
    """
    The "write it out" half of a run. Uses plan["final_order"]/plan["data_result"]
    (what mlox/anchoring computed) unless a caller passes its own final_order
    and/or data_order -- e.g. a GUI that let the person drag-reorder either
    list before exporting. Nothing else about the plan is affected by that
    override: warnings, etc. were already evaluated against the computed
    order in compute_plan() and aren't recomputed here.

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

    # Work out durable removals: opted-out items that are already in the base
    # openmw.cfg (a brand-new custom item that was opted out just isn't inserted,
    # so it needs no removeContent/removeData).
    base_lower = {n.lower() for n in (plan.get("base_order_names") or [])}
    remove_content = sorted({p for p in (disabled_plugins or []) if p.lower() in base_lower})
    base_data_norms = {
        normalize_data_path(extract_data_path_value(line))
        for line in (plan.get("data_order") or [])
    }
    base_data_norms.discard("")
    remove_data = []
    for line in disabled_data or []:
        val = extract_data_path_value(line) or line
        if normalize_data_path(val) in base_data_norms:
            remove_data.append(val)
    remove_data = sorted(set(remove_data))
    if remove_content or remove_data:
        _subsection("opted-out items already in cfg -> removeContent/removeData")
        for p in remove_content:
            print(f"  removeContent: {p}")
        for d in remove_data:
            print(f"  removeData: {d}")

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
        write_cfg(args.cfg, plan["lines"], segments, args.dry_run, args.no_backup)
        wrote_cfg = not args.dry_run
    else:
        print(_("  openmw.cfg left untouched (pass --write-cfg to patch it directly)"))

    wrote_toml = False
    if args.emit_toml:
        toml_text = generate_customizations_toml(
            plan["original_toml_data"],
            final_order or [],
            set(subset),
            plan["original_content_values"],
            data_result if args.sort_data_paths else None,
            plan["raw_toml_data_inserts"] if not args.sort_data_paths else None,
            plan["replace_dest_names"],
            user_data_values=[d["value"] for d in (plan["data_inserts"] or [])],
            list_name=getattr(args, "list_name", None),
            remove_content=remove_content,
            remove_data=remove_data,
            custom_anchors=plan.get("custom_anchors"),
        )
        # Dry-run the TOML through a faithful simulation of momw-configurator's
        # apply logic and verify the result reproduces the sorted order.
        _subsection("configurator preview (simulated apply)")
        try:
            _user_norms = [
                normalize_data_path(d["value"])
                for d in (plan["data_inserts"] or [])
                if d.get("value")
            ]
            _ok, _rep = preview_configurator_result(
                plan["lines"],
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
            print(f"  WARNING: configurator preview failed: {_e}")
        if args.dry_run:
            print(f"\n  DRY RUN: would write {args.emit_toml}\n{toml_text}")
        else:
            backup_file(args.emit_toml, args.no_backup)
            args.emit_toml.write_text(toml_text, encoding="utf-8")
            print(f"  Wrote corrected customizations: {args.emit_toml}")
            wrote_toml = True

    if not args.write_cfg and not args.emit_toml:
        print(
            "\n  NOTE: nothing was written -- this was a preview. "
            "Pass --write-cfg and/or --emit-toml to save the result."
        )

    _section("SUMMARY")
    print(f"  Plugins sorted:        {len(subset)}")
    print(f"  Data paths inserted:   {len(plan['data_inserts']) if args.sort_data_paths else 0}")
    print(f"  Rule warnings raised:  {len(plan['predicate_warnings'])}")
    print(f"  plugin-order.yml warnings: {len(plan.get('yml_warnings') or [])}")
    print(f"  openmw.cfg written:    {'yes' if wrote_cfg else 'no'}")
    print(f"  customizations.toml written: {'yes' if wrote_toml else 'no'}")

    return {"wrote_cfg": wrote_cfg, "wrote_toml": wrote_toml}


def run_from_args(args) -> dict:
    """
    Does the actual work for a parsed args object (from build_arg_parser(),
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


def main():
    args = build_arg_parser().parse_args()
    tr = getattr(args, "trace", None)
    if tr:
        set_trace_file(tr if isinstance(tr, str) else "mlox_subset_sort_trace.log")
        trace("CLI started")
    run_from_args(args)


if __name__ == "__main__":
    main()
