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

import argparse
import fnmatch
import os
import re
import struct
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

PLUGIN_EXTS = (".esp", ".esm", ".omwaddon", ".omwgame", ".omwscripts")

# --- lightweight trace log ---------------------------------------------------
# Appends timestamped lines to a file with an immediate flush, so if a heavy
# operation OOMs/hangs and the process dies, the last steps are still on disk.
# Off unless set_trace_file() is called (the GUI turns it on at startup).
_TRACE_PATH = None
_TRACE_FH = None
# The sort engine's play-by-play is very chatty and unrelated to the cell-map /
# tes3conv traces, so it gets its OWN file (next to the main trace), truncated at
# the start of each sort -- so a sort log is small, self-contained, and readable.
_SORT_TRACE_PATH = None
_SORT_TRACE_FH = None


def set_trace_file(path):
    global _TRACE_PATH, _TRACE_FH
    if _TRACE_FH is not None:
        try:
            _TRACE_FH.close()
        except Exception:
            pass
        _TRACE_FH = None
    _TRACE_PATH = str(path) if path else None
    if _TRACE_PATH:
        try:
            # truncate per session so the log doesn't grow unbounded across runs
            _TRACE_FH = open(_TRACE_PATH, "w", encoding="utf-8")
        except OSError:
            _TRACE_FH = None
        trace("=== trace start ===")


def trace(msg):
    # Keeps the file handle OPEN (reopening per call crawled once the sort engine
    # started logging thousands of steps). Still flushes each line, so a crash/OOM
    # leaves the last steps on disk.
    global _TRACE_FH
    if not _TRACE_PATH:
        return
    try:
        if _TRACE_FH is None:
            _TRACE_FH = open(_TRACE_PATH, "a", encoding="utf-8")
        _TRACE_FH.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        _TRACE_FH.flush()
    except OSError:
        pass


def sort_trace_begin():
    """Open a FRESH (truncated) sort-trace file next to the main trace. Call once
    at the start of a sort so its steps aren't mixed with map/tes3conv traces and
    don't pile up across runs. No-op unless --trace is on."""
    global _SORT_TRACE_PATH, _SORT_TRACE_FH
    if _SORT_TRACE_FH is not None:
        try:
            _SORT_TRACE_FH.close()
        except Exception:
            pass
        _SORT_TRACE_FH = None
    if not _TRACE_PATH:
        _SORT_TRACE_PATH = None
        return
    _SORT_TRACE_PATH = str(Path(_TRACE_PATH).with_name("mlox_subset_sort_sort_trace.log"))
    try:
        _SORT_TRACE_FH = open(_SORT_TRACE_PATH, "w", encoding="utf-8")
    except OSError:
        _SORT_TRACE_FH = None
    trace(f"[sort] full sort play-by-play -> {_SORT_TRACE_PATH}")   # pointer in the MAIN log


def trace_sort(msg):
    """Write one line to the dedicated sort-trace file (see sort_trace_begin)."""
    global _SORT_TRACE_FH
    if not _TRACE_PATH or _SORT_TRACE_FH is None:
        return
    try:
        _SORT_TRACE_FH.write(f"{datetime.now().strftime('%H:%M:%S')}  {msg}\n")
        _SORT_TRACE_FH.flush()
    except OSError:
        pass

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

_MLOX_VER = r'(\d+(?:[_.-]?\d+)*[a-zA-Z]?)'    # mlox's plugin_version regex
_re_escape_meta = re.compile(r'([()+.])')       # mlox escapes ONLY these
_re_plugin_meta = re.compile(r'([*?])')         # * and ?
_re_plugin_metaver = re.compile(r'(<VER>)', re.IGNORECASE)


def pattern_has_meta(pattern: str) -> bool:
    """True if a pattern needs regex expansion (has *, ?, or a <VER> token).
    Plain filenames skip the regex entirely for speed and exactness."""
    return ("*" in pattern) or ("?" in pattern) or ("<ver>" in pattern.lower())


@lru_cache(maxsize=None)
def mlox_pattern_to_regex(pattern: str):
    """Compile one mlox filename pattern to a case-insensitive anchored regex,
    exactly as mlox's _filename_to_regex does (escape ()+. ; * -> .* ; ? -> .? ;
    <VER> -> version regex). Cached because the same rule patterns recur across
    thousands of edges."""
    pat = "^%s$" % _re_escape_meta.sub(r'\\\1', pattern)
    pat = _re_plugin_meta.sub(r'.\1', pat)          # * -> .*  and  ? -> .?
    pat = _re_plugin_metaver.sub('<VER>', pat)      # normalize any <ver> casing
    pat = pat.replace('<VER>', _MLOX_VER)           # <VER> -> version number
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error:
        # A malformed rule token slipped past parsing (mlox escapes only ()+. ,
        # so a stray bracket etc. could still be invalid). Fall back to a fully
        # literal, anchored match rather than crashing the whole sort.
        return re.compile("^" + re.escape(pattern) + "$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# mlox [VER]/[SIZE]/[DESC] predicate functions (ported from mlox's ruleParser).
# These let [Requires]/[Conflict]/[Note] rules test a plugin's version, file
# size, or header description -- e.g. "[VER < 2.0 SomeMod.esp]". mlox reads the
# actual plugin file for this; we locate it across the cfg's data= directories.
# When the files aren't reachable (e.g. running on a different machine than the
# mods live on), we fall back to mlox's own conservative "no datadir" behaviour
# rather than guessing, so we never invent a warning we can't substantiate.
# ---------------------------------------------------------------------------

_re_ver_delim = re.compile(r'[_.-]')
_re_alpha_tail = re.compile(r'(\d+)([a-zA-Z])', re.IGNORECASE)
_re_filename_version = re.compile(r'\D%s\D*\.es[mp]' % _MLOX_VER, re.IGNORECASE)
_re_header_version = re.compile(r'\b(?:version\b\D+|v(?:er)?\.?\s*)%s' % _MLOX_VER, re.IGNORECASE)
# atomic function forms, matched against a single token produced by the tokenizer
_re_ver_fun = re.compile(r'^\[\s*VER\s*([=<>])\s*%s\s*([^\]]+?)\s*\]$' % _MLOX_VER, re.IGNORECASE)
_re_size_fun = re.compile(
    r'^\[\s*SIZE\s*(!?)(\d+)\s+(\S.*?\.(?:es[mp]|omwaddon|omwgame|omwscripts)\b)\s*\]$',
    re.IGNORECASE)
_re_desc_fun = re.compile(r'^\[\s*DESC\s*(!?)/([^/]+)/\s+([^\]]+?)\s*\]$', re.IGNORECASE)
_re_mwselua_fun = re.compile(r'^\[\s*MWSE-LUA\s*(!?)/([^/]+)/\s+([^\]]+?)\s*\]$', re.IGNORECASE)

_TES3_MIN_PLUGIN_SIZE = 362


class PluginFileIndex:
    """Locates plugin files across the cfg's data= directories so [VER]/[SIZE]/
    [DESC] predicates can read real version/size/description info. Built once,
    lazily; if the directories can't be read (e.g. this is running somewhere the
    mods aren't installed), lookups return None and callers fall back to mlox's
    conservative behaviour."""

    def __init__(self, data_dirs=None):
        self._dirs = list(data_dirs or [])
        self._index = None  # {lower_filename: Path}

    def _build(self):
        idx = {}
        for d in self._dirs:
            try:
                p = Path(d)
                if not p.is_dir():
                    continue
                for entry in p.iterdir():
                    if entry.is_file() and entry.name.lower().endswith(PLUGIN_EXTS):
                        idx.setdefault(entry.name.lower(), entry)
            except (OSError, PermissionError):
                continue
        self._index = idx

    def find(self, plugin_name):
        if self._index is None:
            self._build()
        return self._index.get(plugin_name.lower())

    @property
    def usable(self):
        """True if at least one data= directory was actually readable -- i.e. we
        can trust a 'file not found' to really mean absent, rather than just
        'we can't see the mod folders from here'."""
        if self._index is None:
            self._build()
        return bool(self._index)


def _format_version(ver: str) -> str:
    """Canonicalize a version string into a fixed-width, lexicographically
    comparable form -- a direct port of mlox's format_version."""
    v = _re_ver_delim.split(ver, 3)
    m = _re_alpha_tail.match(v[-1])
    alpha = "_"
    if m:
        v[-1] = m.group(1)
        alpha = m.group(2)
    try:
        v = [int(x) for x in v]
    except ValueError:
        return ""
    while len(v) < 3:
        v.append(0)
    return "%05d.%05d.%05d.%s" % (v[0], v[1], v[2], alpha)


def _read_plugin_description(path) -> str:
    """Read the description field from a TES3 plugin header (OpenMW is TES3
    only). Any read problem yields '' rather than raising."""
    try:
        with open(path, "rb") as fh:
            block = fh.read(4096)
    except (OSError, PermissionError):
        return ""
    if block[:4] == b"TES3":
        if len(block) < _TES3_MIN_PLUGIN_SIZE:
            return ""
        end = block.find(b"\x00", 64)
        raw = block[64:end] if end != -1 else block[64:]
        return raw.decode("latin-1", "replace")
    return ""


def read_plugin_masters(path):
    """The master files a plugin depends on, from its TES3 header (the MAST
    subrecords). These are the ground-truth load-order dependencies: a plugin
    must load AFTER every master it lists. Returns [] for non-TES3 files
    (.omwscripts) or any read problem. Works for .esm/.esp/.omwaddon/.omwgame."""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"TES3":
                return []
            data_size = struct.unpack("<I", fh.read(4))[0]
            fh.read(8)                                  # header1 + flags
            data = fh.read(min(data_size, 1 << 20))     # header is tiny; cap defensively
    except (OSError, struct.error):
        return []
    masters, i = [], 0
    while i + 8 <= len(data):
        tag = data[i:i + 4]
        sz = struct.unpack_from("<I", data, i + 4)[0]
        i += 8
        chunk = data[i:i + sz]
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
        with open(path, "rb") as fh:
            if fh.read(4) != b"TES3":
                return []
            data_size = struct.unpack("<I", fh.read(4))[0]
            fh.read(8)                                  # header1 + flags
            data = fh.read(min(data_size, 1 << 20))
    except (OSError, struct.error):
        return []
    out, i = [], 0
    pending = None  # last MAST name waiting for its DATA size
    while i + 8 <= len(data):
        tag = data[i:i + 4]
        sz = struct.unpack_from("<I", data, i + 4)[0]
        i += 8
        chunk = data[i:i + sz]
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
    if raw[:4] != b"TES3":
        return [], [], "not a TES3 plugin (no TES3 header)"
    (data_size,) = struct.unpack_from("<I", raw, 4)
    end = min(16 + data_size, len(raw))
    i, updated, unresolved = 16, [], []
    pending = None   # master name waiting for its DATA size field
    while i + 8 <= end:
        tag = bytes(raw[i:i + 4])
        (sz,) = struct.unpack_from("<I", raw, i + 4)
        off = i + 8
        if off + sz > end:
            break
        if tag == b"MAST":
            pending = raw[off:off + sz].split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
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
                        f"folder. The game will fail to load with this plugin enabled.")
                else:
                    missing.append(
                        f"[MISSING MASTER] '{p}'{tag} requires '{m}' -- installed but not in "
                        f"the load order. Enable/add it (it must load before '{p}').")
                problem_names.add(p)
                continue
            if pos[ml] > pos[p.lower()]:
                order_problems.append(
                    f"[MASTER ORDER] '{p}'{tag} loads BEFORE its master '{m}' -- "
                    f"'{m}' must come first.")
                problem_names.add(p)
            if rec_size is not None:   # 0 counts: a failed tes3cmd sync zeroes these
                mpath = index.find(m) if index else None
                if mpath is not None:
                    try:
                        actual = mpath.stat().st_size
                    except OSError:
                        actual = None
                    if actual is not None and actual != rec_size:
                        hint = ("header records 0 bytes -- likely damaged by a tes3cmd "
                                "sync that couldn't find the master"
                                if rec_size == 0 else
                                "made against a different version of the master (usually fine)")
                        size_notes.append(
                            f"[MASTER SIZE] '{p}'{tag}: header says '{m}' was {rec_size} bytes, "
                            f"installed copy is {actual} -- {hint}. The tes3cmd window's "
                            f"in-app resync fixes this.")
    return missing, order_problems, size_notes, checked, problem_names


def _plugin_version(plugin_name: str, index: "PluginFileIndex"):
    """Best-effort canonical version for a plugin: from its TES3 header if the
    file is reachable, else from the version number embedded in its filename.
    Returns None if neither yields a version."""
    path = index.find(plugin_name) if index else None
    if path is not None:
        m = _re_header_version.search(_read_plugin_description(path))
        if m:
            return _format_version(m.group(1))
    m = _re_filename_version.search(plugin_name)
    if m:
        return _format_version(m.group(1))
    return None


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
    schd = None      # SCPT script header (name in first 32 bytes)
    intv = None      # LAND grid coords
    deleted = False
    i, n = 0, len(blob)
    while i + 8 <= n:
        tag = blob[i:i + 4]
        sz = struct.unpack_from("<I", blob, i + 4)[0]
        data = blob[i + 8:i + 8 + sz]
        i += 8 + sz
        if tag == b"NAME" and name is None:
            name = data
        elif tag == b"INAM" and rectype == "INFO" and name is None:
            name = data          # dialogue response id
        elif tag == b"DELE":
            deleted = True
        elif tag == b"DATA" and rectype == "CELL" and cell_data is None:
            cell_data = data
        elif tag == b"SCHD" and rectype == "SCPT" and schd is None:
            schd = data          # script: name is the first 32 bytes
        elif tag == b"INTV" and rectype == "LAND" and intv is None:
            intv = data          # landscape: keyed by exterior grid coords
    if rectype == "CELL":
        cname = _tes3_decode(name) if name else ""
        if cell_data is not None and len(cell_data) >= 12:
            flags, gx, gy = struct.unpack_from("<iii", cell_data, 0)
            if flags & 0x01:     # interior
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
        fh = open(path, "rb")
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
        tag = blob[i:i + 4]
        sz = struct.unpack_from("<I", blob, i + 4)[0]
        data = blob[i + 8:i + 8 + sz]
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
        spath = line[pos + 1:].strip().strip('"').strip("'")
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
    for d in (extra_dirs or []):
        if d:
            search_dirs.append(Path(d))
    for d in search_dirs:
        for nm in names:
            cands.append(str(d / nm))
    for c in cands:
        try:
            if c and Path(c).is_file():
                return c
        except OSError:
            continue
    return None


def find_tes3cmd(explicit=None, extra_dirs=None):
    """Locate tes3cmd. Prefers the compiled executable (tes3cmd.exe -- what the
    MOMW Tools Pack distributes and what end users will normally have); the
    pure-perl 'tes3cmd' script is also accepted (it then needs a perl on PATH;
    see tes3cmd_invocation). Order: explicit path, $MLOX_TES3CMD, PATH, then
    alongside this script / any extra dirs given. Returns a path or None."""
    import shutil
    names = ["tes3cmd.exe", "tes3cmd.bat", "tes3cmd"]   # compiled build first
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
    for d in (extra_dirs or []):
        if d:
            search_dirs.append(Path(d))
    for d in search_dirs:
        for nm in names:
            cands.append(str(d / nm))
    for c in cands:
        try:
            if c and Path(c).is_file():
                return c
        except OSError:
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
            return None, (f"'{p.name}' is the pure-perl tes3cmd but no perl interpreter was found "
                          f"on PATH. Point this at the compiled tes3cmd.exe from the MOMW Tools "
                          f"Pack instead (or install perl).")
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
                    return dest            # cached from a previous run
                dest.unlink()
            if allow_link:
                try:
                    os.link(src, dest)     # same-volume: instant, no disk cost
                    return dest
                except OSError:
                    pass                   # cross-volume etc. -> copy
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
    _sh.copy2(p, staged_plugin)            # always a private copy
    staged_names.append(p.name)

    ini = root / "Morrowind.ini"
    ini.write_text("[Game Files]\n" +
                   "".join(f"GameFile{i}={n}\n" for i, n in enumerate(staged_names)),
                   encoding="latin-1", errors="replace")
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
_EVIL_GMSTS = {
    'fcombatdistancewerewolfmod': ('FLTV', b'\x9a\x99\x99>'),
    'ffleedistance': ('FLTV', b'\x00\x80;E'),
    'fwerewolfacrobatics': ('FLTV', b'\x00\x00\x16C'),
    'fwerewolfagility': ('FLTV', b'\x00\x00\x16C'),
    'fwerewolfalchemy': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfalteration': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfarmorer': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfathletics': ('FLTV', b'\x00\x00\x16C'),
    'fwerewolfaxe': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfblock': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfbluntweapon': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfconjuration': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfdestruction': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfenchant': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfendurance': ('FLTV', b'\x00\x00\x16C'),
    'fwerewolffatigue': ('FLTV', b'\x00\x00\xc8C'),
    'fwerewolfhandtohand': ('FLTV', b'\x00\x00\xc8B'),
    'fwerewolfhealth': ('FLTV', b'\x00\x00\x00@'),
    'fwerewolfheavyarmor': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfillusion': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfintellegence': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolflightarmor': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolflongblade': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfluck': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfmagicka': ('FLTV', b'\x00\x00\xc8B'),
    'fwerewolfmarksman': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfmediumarmor': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfmerchantile': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfmysticism': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfpersonality': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfrestoration': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfrunmult': ('FLTV', b'\x00\x00\xc0?'),
    'fwerewolfsecurity': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfshortblade': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfsilverweapondamagemult': ('FLTV', b'\x00\x00\xc0?'),
    'fwerewolfsneak': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfspear': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfspeechcraft': ('FLTV', b'\x00\x00\x80?'),
    'fwerewolfspeed': ('FLTV', b'\x00\x00\x16C'),
    'fwerewolfstrength': ('FLTV', b'\x00\x00\x16C'),
    'fwerewolfunarmored': ('FLTV', b'\x00\x00\xc8B'),
    'fwerewolfwillpower': ('FLTV', b'\x00\x00\x80?'),
    'iwerewolfbounty': ('INTV', b"\x10'\x00\x00"),
    'iwerewolffightmod': ('INTV', b'd\x00\x00\x00'),
    'iwerewolffleemod': ('INTV', b'd\x00\x00\x00'),
    'iwerewolfleveltoattack': ('INTV', b'\x14\x00\x00\x00'),
    'scompanionshare': ('STRV', b'Companion Share'),
    'scompanionwarningbuttonone': ('STRV', b'Let the mercenary quit.'),
    'scompanionwarningbuttontwo': ('STRV', b'Return to Companion Share display.'),
    'scompanionwarningmessage': ('STRV', b'Your mercenary is poorer now than when he contracted with you.  Your mercenary will quit if you do not give him gold or goods to bring his Profit Value to a positive value.'),
    'sdeletenote': ('STRV', b'Delete Note?'),
    'seditnote': ('STRV', b'Edit Note'),
    'seffectsummoncreature01': ('STRV', b'sEffectSummonCreature01'),
    'seffectsummoncreature02': ('STRV', b'sEffectSummonCreature02'),
    'seffectsummoncreature03': ('STRV', b'sEffectSummonCreature03'),
    'seffectsummoncreature04': ('STRV', b'sEffectSummonCreature04'),
    'seffectsummoncreature05': ('STRV', b'sEffectSummonCreature05'),
    'seffectsummonfabricant': ('STRV', b'sEffectSummonFabricant'),
    'slevitatedisabled': ('STRV', b'Levitation magic does not work here.'),
    'smagiccreature01id': ('STRV', b'sMagicCreature01ID'),
    'smagiccreature02id': ('STRV', b'sMagicCreature02ID'),
    'smagiccreature03id': ('STRV', b'sMagicCreature03ID'),
    'smagiccreature04id': ('STRV', b'sMagicCreature04ID'),
    'smagiccreature05id': ('STRV', b'sMagicCreature05ID'),
    'smagicfabricantid': ('STRV', b'Fabricant'),
    'smaxsale': ('STRV', b'Max Sale'),
    'sprofitvalue': ('STRV', b'Profit Value'),
    'steleportdisabled': ('STRV', b'Teleportation magic does not work here.'),
    'swerewolfalarmmessage': ('STRV', b'You have been detected changing from a werewolf state.'),
    'swerewolfpopup': ('STRV', b'Werewolf'),
    'swerewolfrefusal': ('STRV', b'You cannot do this as a werewolf.'),
    'swerewolfrestmessage': ('STRV', b'You cannot rest in werewolf form.'),
}

# Script functions introduced by the expansions (from tes3lint's DATA lists).
# A plugin calling one without listing the expansion as a master is fragile
# on non-expansion setups and usually indicates a truncated master list.
_TRIBUNAL_FUNCS = (
    "AddToLevCreature", "AddToLevItem", "ClearForceJump", "ClearForceMoveJump",
    "ClearForceRun", "DisableLevitation", "EnableLevitation", "ExplodeSpell",
    "ForceJump", "ForceMoveJump", "ForceRun", "GetCollidingActor",
    "GetCollidingPC", "GetForceJump", "GetForceMoveJump", "GetForceRun",
    "GetPCJumping", "GetPCRunning", "GetPCSneaking", "GetScale",
    "GetSpellReadied", "GetSquareRoot", "GetWaterLevel", "GetWeaponDrawn",
    "GetWeaponType", "HasItemEquipped", "ModScale", "ModWaterLevel",
    "PlaceItem", "PlaceItemCell", "RemoveFromLevCreature", "RemoveFromLevItem",
    "SetDelete", "SetScale", "SetWaterLevel")
_BLOODMOON_FUNCS = (
    "BecomeWerewolf", "GetPCInJail", "GetPCTraveling", "GetWerewolfKills",
    "IsWerewolf", "PlaceAtMe", "SetWerewolfAcrobatics", "TurnMoonRed",
    "TurnMoonWhite", "UndoWerewolf")
# mirror tes3lint's per-line matching: ignore comment text after ';'
_RE_TB_FUN = re.compile(r"^[^;\n]*?\b(" + "|".join(_TRIBUNAL_FUNCS) + r")\b",
                        re.IGNORECASE | re.MULTILINE)
_RE_BM_FUN = re.compile(r"^[^;\n]*?\b(" + "|".join(_BLOODMOON_FUNCS) + r")\b",
                        re.IGNORECASE | re.MULTILINE)

_LINT_SKIP = {"morrowind.esm", "tribunal.esm", "bloodmoon.esm",
              "merged objects.esp", "merged lands.esp", "mashed lists.esp",
              "multipatch.esp"}
_LINT_SKIP_CELLS = {"ashlands region (0, 0)"}   # the classic 0,0 exterior exception


def _iter_tes3_records(raw):
    """Yields (tag, body) for each top-level record. Bodies of record types
    the caller doesn't care about are skipped over cheaply by size."""
    n, i = len(raw), 0
    while i + 16 <= n:
        tag = bytes(raw[i:i + 4])
        (sz,) = struct.unpack_from("<I", raw, i + 4)
        yield tag, raw[i + 16:i + 16 + sz]
        i += 16 + sz


def _iter_subrecords(body):
    n, i = len(body), 0
    while i + 8 <= n:
        tag = bytes(body[i:i + 4])
        (sz,) = struct.unpack_from("<I", body, i + 4)
        yield tag, body[i + 8:i + 8 + sz]
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
    interior_first = {}   # cell id lower -> (plugin, display name)
    pathgrids = set()     # interior pathgrid cell ids seen anywhere

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
                            missing = [w for w, v in (("author", auth), ("description", desc)) if not v]
                            if missing:
                                warnings.append(
                                    f"[HEADER] '{p}'{tagfor(p)}: header has no "
                                    f"{' and no '.join(missing)}.")
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
                    continue                      # exterior
                cid = name.lower()
                if cid and cid not in _LINT_SKIP_CELLS and cid not in interior_first:
                    interior_first[cid] = (p, name)
                if not flags & 128:               # not behave-like-exterior
                    if ambi is not None and len(ambi) == 16:
                        (fog,) = struct.unpack_from("<f", ambi, 12)
                    else:
                        (fog,) = struct.unpack_from("<f", data, 8)
                    if fog == 0.0:
                        warnings.append(
                            f"[FOGBUG] '{p}'{tagfor(p)}: interior cell '{name}' has fog "
                            f"density 0.0 -- renders as a black void on some GPUs. Fix by "
                            f"setting any nonzero fog density on the cell.")
            elif tag == b"PGRD":
                name, x, y = "", None, None
                for st, sd in _iter_subrecords(body):
                    if st == b"NAME":
                        name = _lint_zstr(sd)
                    elif st == b"DATA" and len(sd) >= 8:
                        x, y = struct.unpack_from("<ii", sd, 0)
                if x == 0 and y == 0 and name:    # interiors carry grid (0,0)
                    pathgrids.add(name.lower())
        if evil_here:
            warnings.append(
                f"[EVLGMST] '{p}'{tagfor(p)}: {len(evil_here)} evil GMST(s): "
                f"{', '.join(sorted(evil_here))} -- stale expansion defaults copied in "
                f"by an old Construction Set; tes3cmd clean removes them.")
        if tb_hits and "tribunal.esm" not in my_masters and "bloodmoon.esm" not in my_masters:
            warnings.append(
                f"[EXP-DEP] '{p}'{tagfor(p)}: scripts use Tribunal function(s) "
                f"{', '.join(sorted(tb_hits))} but the plugin doesn't master Tribunal.esm -- "
                f"fragile on non-expansion setups (tes3lint !TB-FUN).")
        if bm_hits and "bloodmoon.esm" not in my_masters:
            warnings.append(
                f"[EXP-DEP] '{p}'{tagfor(p)}: scripts use Bloodmoon function(s) "
                f"{', '.join(sorted(bm_hits))} but the plugin doesn't master Bloodmoon.esm -- "
                f"fragile on non-expansion setups (tes3lint !BM-FUN).")

    for cid, (plug, name) in sorted(interior_first.items()):
        if cid not in pathgrids:
            warnings.append(
                f"[NO PATHGRID] '{plug}'{tagfor(plug)}: new interior cell '{name}' has no "
                f"pathgrid anywhere in the load order -- NPCs can't pathfind there.")

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
                    f"(or confirm it's optional).")
        elif pl.endswith(".omwscripts"):
            for ext in (".omwaddon", ".esp"):
                twin = path.with_name(stem + ext)
                if twin.exists() and twin.name.lower() not in active_lower:
                    warnings.append(
                        f"[TWIN] '{p}'{tagfor(p)}: '{twin.name}' sits in the same folder but "
                        f"isn't in the load order -- scripts may reference content that "
                        f"never loads. Add it (or confirm it's optional).")
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
        return bool(flags & 0x20)     # TES3 deleted flag
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
        gx, gy = (grid[0], grid[1]) if isinstance(grid, (list, tuple)) and len(grid) >= 2 else (None, None)
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
    kw = {"creationflags": 0x08000000}          # CREATE_NO_WINDOW
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0                       # SW_HIDE
        kw["startupinfo"] = si
    except Exception:
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
        self.dump_dir = Path(dump_dir) if dump_dir else Path(tempfile.mkdtemp(prefix="mlox_tes3conv_"))
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._json_paths = {}   # plugin path(str) -> json file path on disk

    def _json_for(self, path):
        import subprocess
        key = str(path)
        jp = self._json_paths.get(key)
        if jp and Path(jp).exists():
            return jp
        out = self.dump_dir / (Path(path).stem + ".json")
        if out.exists():
            if not self._stale(out, path):     # reuse existing JSON -- don't re-run tes3conv
                self._json_paths[key] = str(out)
                trace(f"tes3conv: REUSE {out.name}")
                return str(out)
            trace(f"tes3conv: STALE, re-convert {out.name} (plugin newer than json)")
        try:
            trace(f"tes3conv: CONVERT {Path(path).name} -> {out.name}")
            subprocess.run([self.exe, str(path), str(out)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=600, check=True, **_no_window_kwargs())
            self._json_paths[key] = str(out)
            return str(out)
        except Exception:
            return None

    def _records(self, path):
        import json
        jp = self._json_for(path)
        if not jp:
            return []
        try:
            with open(jp, "r", encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
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
                with open(side, "r", encoding="utf-8", errors="replace") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict) and obj.get("v") == self._SIDECAR_VER:
                    return [tuple(x) for x in obj.get("d", [])]
                # older/mismatched cache format -> rebuild
            except Exception:
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
        for rec in self._records(path):          # the single big-JSON read
            if not isinstance(rec, dict):
                continue
            # Lua scripts declared by an .omwaddon LuaScriptsCfg (keyless record)
            if str(rec.get("type", "")).lower().replace("_", "") in ("luascriptscfg", "lual"):
                for s in (rec.get("scripts") or rec.get("mScripts") or []):
                    sp = s.get("script_path") or s.get("path") or s.get("mScriptPath") if isinstance(s, dict) else (s if isinstance(s, str) else None)
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
                interior = bool(flags & 0x01) if isinstance(flags, int) else (
                    flags is not None and "INTERIOR" in str(flags).upper())
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
                with open(self.dump_dir / name, "w", encoding="utf-8") as fh:
                    json.dump({"v": self._SIDECAR_VER, "d": payload}, fh)
            except OSError:
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
            for s in (rec.get("scripts") or rec.get("mScripts") or []):
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
    touch = {}          # (type, id) -> list of (plugin, deleted)
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
        seen_here = set()   # collapse a record the same plugin defines twice
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
        conflicts.append({
            "type": rectype,
            "id": rid,
            "plugins": names,
            "winner": names[-1],
            "involves_subset": any(p.lower() in subset_lower for p in names),
            "deleted_by": [p for p, d in plugs if d],
        })
    conflicts.sort(key=lambda c: (not c["involves_subset"], c["type"], c["id"].lower()))
    stats = {"scanned": scanned, "unreadable": unreadable,
             "records": rec_count, "conflicts": len(conflicts),
             "engine": "tes3conv" if session is not None else "builtin",
             "paths": paths}
    return conflicts, stats


def format_conflict_report(conflicts, stats, subset_only=False, limit=None) -> str:
    """Render conflicts as a readable text report. subset_only shows just the
    ones that involve your custom mods; limit caps how many are listed."""
    shown = [c for c in conflicts if c["involves_subset"] or not subset_only]
    lines = []
    n_sub = sum(1 for c in conflicts if c["involves_subset"])
    lines.append(f"Scanned {stats['scanned']} plugin(s), {stats['records']} record(s): "
                 f"{stats['conflicts']} conflicting record(s), {n_sub} involving your custom mods.")
    if stats["unreadable"]:
        lines.append(f"NOTE: {len(stats['unreadable'])} plugin(s) could not be read "
                     f"(not found on disk / unreadable): {', '.join(stats['unreadable'][:8])}"
                     + (" ..." if len(stats['unreadable']) > 8 else ""))
    capped = shown if not limit else shown[:limit]
    for c in capped:
        star = "* " if c["involves_subset"] else "  "
        lines.append(f"{star}[{c['type']}] {c['id']}")
        lines.append(f"      {'  ->  '.join(c['plugins'])}   (wins: {c['winner']})")
        if c["deleted_by"]:
            lines.append(f"      deleted by: {', '.join(c['deleted_by'])}")
    if limit and len(shown) > limit:
        lines.append(f"  ... and {len(shown) - limit} more (raise the limit or save the full report).")
    if not capped:
        lines.append("  No conflicts to show.")
    return "\n".join(lines)


def write_conflict_csv(path, conflicts):
    """Write the full conflict list to a CSV (type, id, winner, involves_custom,
    deleted_by, plugins-in-load-order)."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["record_type", "record_id", "winner", "involves_custom",
                    "deleted_by", "plugins_load_order"])
        for c in conflicts:
            w.writerow([c["type"], c["id"], c["winner"],
                        "yes" if c["involves_subset"] else "no",
                        "; ".join(c["deleted_by"]), " -> ".join(c["plugins"])])


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
        hit = any(fnmatch.fnmatch(low, p) or (("*" not in p and "?" not in p) and p in low)
                  for p in pats)
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
                json.dumps(recs, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
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
    providers = {}     # rel_path -> [dir_index in order]
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
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in PLUGIN_EXTS or ext in exclude_exts:
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), p).replace("\\", "/").lower()
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
        conflicts.append({"path": rel, "providers": prov, "winner": prov[-1],
                          "involves_subset": involves})
    conflicts.sort(key=lambda c: (not c["involves_subset"], c["path"]))
    return conflicts, {"dirs": len(dirs), "files": len(providers), "conflicts": len(conflicts)}


def format_resource_report(conflicts, stats, subset_only=False, limit=200):
    shown = [c for c in conflicts if c["involves_subset"] or not subset_only]
    n_sub = sum(1 for c in conflicts if c["involves_subset"])
    lines = [f"Scanned {stats['dirs']} data folder(s), {stats['files']} loose file(s): "
             f"{stats['conflicts']} conflicting file(s), {n_sub} involving your custom data paths."]
    for c in (shown[:limit] if limit else shown):
        star = "* " if c["involves_subset"] else "  "
        lines.append(f"{star}{c['path']}   ({len(c['providers'])} providers, wins: {c['winner']})")
    if limit and len(shown) > limit:
        lines.append(f"  ... and {len(shown) - limit} more (save the full report).")
    return "\n".join(lines)


def write_resource_csv(path, conflicts):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file_path", "providers", "winner", "involves_custom", "provider_folders"])
        for c in conflicts:
            w.writerow([c["path"], len(c["providers"]), c["winner"],
                        "yes" if c["involves_subset"] else "no", " -> ".join(c["providers"])])


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
                yield ("int", rid[len("Interior: "):], None)


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
    return {"exterior": ext, "interior": inte, "scanned": scanned,
            "unreadable": unreadable, "subset_lower": subset_lower}


def _cell_heat(count):
    """Heatmap fill: one mod = cool (coverage), 2+ = warmer/hotter (conflict)."""
    if count <= 1:
        return "#2f4a63"
    return {2: "#7a5a1e", 3: "#9c4a16", 4: "#b83a1a"}.get(count, "#d8342a")


def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


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
    SANE = 4096
    ext_ok = {k: v for k, v in ext.items() if -SANE <= k[0] <= SANE and -SANE <= k[1] <= SANE}
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
        for low in sorted(all_mods, key=lambda x: (x not in subl, x)))

    grid = '<p class="sub">No exterior cells touched.</p>'
    if ext_ok:
        xs = [k[0] for k in ext_ok]
        ys = [k[1] for k in ext_ok]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        w, h = (maxx - minx + 1), (maxy - miny + 1)
        trace(f"cell map: {len(ext_ok)} ext cells, bbox {w}x{h}, dropped {dropped}")
        STEP, SIZE = 13, 12                     # 12px square + 1px gutter
        rects = []
        for (gx, gy), mods in ext_ok.items():
            px = (gx - minx) * STEP
            py = (maxy - gy) * STEP             # north (max y) at the top
            custom = any(m.lower() in subl for m in mods)
            tip = f"({gx}, {gy}) — {len(mods)} mod(s): " + ", ".join(mods)
            stroke = ' stroke="#ffd24a" stroke-width="1.4"' if custom else ""
            rects.append(
                f'<rect x="{px}" y="{py}" width="{SIZE}" height="{SIZE}" '
                f'fill="{_cell_heat(len(mods))}"{stroke} class="cell" '
                f'data-t="{_html_escape(tip)}" data-m="{modattr(mods)}" '
                f'onclick="jump(\'{anchor(gx, gy)}\')"></rect>')
        svg = (f'<svg width="{w*STEP}" height="{h*STEP}" viewBox="0 0 {w*STEP} {h*STEP}" '
               f'xmlns="http://www.w3.org/2000/svg">' + "".join(rects) + "</svg>")
        grid = f'<div class="mapwrap">{svg}</div>'

    ext_rows = []
    for (gx, gy), mods in sorted(ext_ok.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        custom = any(m.lower() in subl for m in mods)
        cls = ' class="cust"' if custom else ""
        ext_rows.append(f'<tr id="{anchor(gx,gy)}"{cls} data-m="{modattr(mods)}">'
                        f'<td>({gx}, {gy})</td><td>{len(mods)}</td>'
                        f'<td>{_html_escape(", ".join(mods))}</td></tr>')
    int_rows = []
    for name, mods in sorted(inte.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        custom = any(m.lower() in subl for m in mods)
        cls = ' class="cust"' if custom else ""
        int_rows.append(f'<tr{cls} data-m="{modattr(mods)}"><td>{_html_escape(name)}</td>'
                        f'<td>{len(mods)}</td>'
                        f'<td>{_html_escape(", ".join(mods))}</td></tr>')
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

def read_cfg(path: Path):
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
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
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
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
        print("\n--- DRY RUN: no files written ---")
        return

    backup_file(path, no_backup)
    path.write_text("\n".join(new_lines_out) + "\n", encoding="utf-8")
    print(f"Wrote updated: {path}")


# ---------------------------------------------------------------------------
# mlox rule parsing (Order / NearStart / NearEnd only)
# ---------------------------------------------------------------------------

TOP_KEYWORDS = ("Order", "NearStart", "NearEnd", "Requires", "Conflict", "Patch", "Note", "Version")
# A rule header must sit at the START of a line (mlox: ^\[(order|...) ; plox:
# line.starts_with("[order")). Matching anywhere in the line -- the old
# behaviour -- turned mentions like "see the [Order] section" inside a rule's
# message text into phantom rule starts, silently corrupting block boundaries.
# The header's optional arguments must stay on the same line, and the closing
# bracket is required (same as both reference parsers).
TOP_RE = re.compile(r"^\[(" + "|".join(TOP_KEYWORDS) + r")\b([^\]\n]*)\]",
                    re.IGNORECASE | re.MULTILINE)


def strip_comment(line: str) -> str:
    # mlox comments run from ';' to end of line (outside quotes, which is
    # good enough here since Order/NearStart/NearEnd blocks are just filenames)
    idx = line.find(";")
    return line[:idx] if idx != -1 else line


# One plugin name/pattern on an [Order]/[NearStart]/[NearEnd] body line.
# Follows the reference parsers: a name starts at the first non-space, runs
# non-greedily to a recognized plugin extension (mlox: `^(\S.*?\.es[mp]\b)`,
# here extended with the OpenMW extensions like plox), may carry a trailing
# '*', and must be followed by whitespace or end-of-line. Names routinely
# contain spaces, '&', '-', parens, wildcards (*, ?) and <VER> -- all pass
# through untouched. finditer supports plox-style multiple names per line
# (extension-delimited); trailing junk after a name is dropped like mlox does.
_RE_ORDER_NAME = re.compile(
    r"\S[^\n]*?\.(?:esp|esm|omwaddon|omwgame|omwscripts)\*?(?=\s|$)",
    re.IGNORECASE)


def parse_mlox_file(path: Path):
    """Returns list of blocks: (keyword, [plugin_pattern, ...]) for the
    ordering keywords (order / nearstart / nearend). Keywords are separated --
    an [Order] body is an ordering CHAIN, while [NearStart]/[NearEnd] bodies
    are independent position hints; conflating them creates bogus edges."""
    # utf-8-sig: a BOM would otherwise hide a header on the very first line
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [strip_comment(l) for l in raw.splitlines()]
    text = "\n".join(lines)

    matches = list(TOP_RE.finditer(text))
    blocks = []
    skipped = 0
    for idx, m in enumerate(matches):
        keyword = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end]
        if keyword.lower() in ("order", "nearstart", "nearend"):
            names = []
            for ln in body.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if ln.startswith("["):
                    # conditional/bracketed entry (e.g. "[DESC /.../ Foo.esp]"
                    # qualifiers that appear in a few mlox_base Order blocks)
                    # or a malformed header. mlox treats these as phantom
                    # names that match nothing, so the ordering chain simply
                    # bridges over them -- which is what skipping does here.
                    skipped += 1
                    continue
                found = _RE_ORDER_NAME.findall(ln)
                if found:
                    names.extend(found)
                else:
                    # a non-empty line with no recognizable plugin name --
                    # same phantom treatment as above
                    skipped += 1
            if names:
                blocks.append((keyword.lower(), names))
    if skipped:
        print(f"NOTE: {path.name}: {skipped} conditional/unrecognized line(s) inside "
              f"ordering rules treated as not-installed and bridged over (mlox does the same).")
    return blocks


def load_rule_blocks(rule_paths):
    """
    Returns (order_blocks, nearstart_patterns, nearend_patterns):
      order_blocks       -- [(names, priority)] ordering chains from [Order]
                            blocks, names in rule order; priority = index of
                            the file on the command line (later files win
                            conflicts, like mlox reading mlox_user first)
      nearstart_patterns -- flat pattern list from [NearStart] blocks
      nearend_patterns   -- flat pattern list from [NearEnd] blocks

    NearStart/NearEnd are per-plugin position hints ("as close to the start/
    end as the constraints allow"), NOT ordering chains -- chaining them (the
    old behaviour) invented edges between unrelated plugins (mlox_base's
    [NearEnd] block alone linked Merged Objects.esp -> Mashed Lists.esp -> ...).

    Keeping each whole ordered block (rather than pre-zipping it into a<b
    pairs) lets build_and_sort bridge over plugins you don't have: in
    [Order] A,B,C where B isn't installed, chaining A->C directly preserves
    the A-before-C constraint instead of losing it -- matching how the real
    mlox engine keeps a not-installed plugin as a phantom bridge node (see
    pluggraph). Pre-zipping would have produced A->B and B->C, both of which
    vanish when B expands to nothing.
    """
    blocks_out = []
    nearstart, nearend = [], []
    for priority, p in enumerate(rule_paths):
        p = Path(p)
        files = [p] if p.is_file() else sorted(p.glob("*.txt"))
        for f in files:
            try:
                blocks = parse_mlox_file(f)
            except Exception as e:
                print(f"WARNING: could not parse rule file {f}: {e}", file=sys.stderr)
                continue
            for keyword, names in blocks:
                if keyword == "order":
                    blocks_out.append((names, priority))
                elif keyword == "nearstart":
                    nearstart.extend(names)
                elif keyword == "nearend":
                    nearend.extend(names)
            print(f"Loaded {sum(len(n) for _, n in blocks)} plugin refs from {f.name}")
    return blocks_out, nearstart, nearend


# ---------------------------------------------------------------------------
# mlox predicate evaluation (Requires / Conflict / Note) -- read-only,
# reported as warnings after sorting. This is a best-effort reimplementation
# of mlox's tiny lisp-like logic language (ALL/ANY/NOT/DESC), not the real
# mlox engine -- good enough to flag likely problems, not to be trusted blindly.
# ---------------------------------------------------------------------------

def tokenize_mlox_logic(text: str) -> list:
    """Splits a [Requires]/[Conflict]/[Note] body into brackets, logic
    keywords, /DESC message/ strings, plugin filenames (with wildcards), and
    the atomic [VER]/[SIZE]/[DESC]/[MWSE-LUA] function forms (captured whole so
    they become single leaf nodes the evaluator can dispatch on, rather than
    being split into '[' + garbage + ']')."""
    # DESC/MWSE-LUA carry a /regex/ which may itself contain ']' (e.g.
    # /[Tt]ribunal/), so those forms consume the /.../ part explicitly
    # before looking for the closing bracket; VER/SIZE bodies can't
    # contain brackets.
    func = (r'\[\s*VER\b[^\]\n]*\]|'
            r'\[\s*SIZE\b[^\]\n]*\]|'
            r'\[\s*DESC\s*!?\s*/[^/\n]*/[^\]\n]*\]|'
            r'\[\s*MWSE-LUA\s*!?\s*/[^/\n]*/[^\]\n]*\]')
    pattern = (func + r'|\[|\]|\bALL\b|\bANY\b|\bNOT\b|/[^/]+/|'
               r'[^\[\]\n]+?\.(?:esp|esm|omwaddon|omwgame|omwscripts)\*?')
    tokens = re.findall(pattern, text, re.IGNORECASE)
    return [t.strip() for t in tokens if t.strip()]


# --- [VER]/[SIZE]/[DESC]/[MWSE-LUA] function-token evaluation ---------------

def _eval_ver(op, want_raw, plugin_pat, active_plugins, index) -> bool:
    want = _format_version(want_raw)
    rx = mlox_pattern_to_regex(plugin_pat)
    matched = [p for p in active_plugins if rx.match(p)]
    if not matched:
        return False  # the plugin the rule is about isn't even active
    for p in matched:
        pv = _plugin_version(p, index)
        if pv is None:
            if op == "=":
                return True  # mlox: version unknowable -> assume '=' holds
            continue
        if op == "=" and pv == want:
            return True
        if op == "<" and pv < want:
            return True
        if op == ">" and pv > want:
            return True
    return False


def _eval_size(bang, want_size, plugin_pat, active_plugins, index) -> bool:
    rx = mlox_pattern_to_regex(plugin_pat)
    matched = [p for p in active_plugins if rx.match(p)]
    if not matched:
        return False
    for p in matched:
        path = index.find(p) if index else None
        if path is None:
            return True  # mlox: no datadir -> assume true (mere existence)
        try:
            actual = path.stat().st_size
        except (OSError, PermissionError):
            return True
        b = (actual == want_size)
        if bang == "!":
            b = not b
        if b:
            return True
    return False


def _eval_desc(bang, pat, plugin_pat, active_plugins, index) -> bool:
    rx = mlox_pattern_to_regex(plugin_pat)
    matched = [p for p in active_plugins if rx.match(p)]
    if not matched:
        return False
    for p in matched:
        path = index.find(p) if index else None
        if path is None:
            return True  # mlox: no datadir -> assume true
        desc = _read_plugin_description(path)
        try:
            b = re.search(pat, desc) is not None
        except re.error:
            b = False
        if bang == "!":
            b = not b
        if b:
            return True
    return False


def _eval_func_token(token, active_plugins, index) -> bool:
    """Evaluate one atomic [VER]/[SIZE]/[DESC]/[MWSE-LUA] token."""
    m = _re_ver_fun.match(token)
    if m:
        return _eval_ver(m.group(1), m.group(2), m.group(3).strip(), active_plugins, index)
    m = _re_size_fun.match(token)
    if m:
        return _eval_size(m.group(1), int(m.group(2)), m.group(3).strip(), active_plugins, index)
    m = _re_desc_fun.match(token)
    if m:
        return _eval_desc(m.group(1), m.group(2), m.group(3).strip(), active_plugins, index)
    if _re_mwselua_fun.match(token):
        return False  # MWSE-Lua content doesn't exist under OpenMW
    return False  # unrecognized bracketed token -> can't assert it holds


def _func_token_matches(token, active_plugins) -> set:
    """The active plugins named by a function token's inner plugin pattern --
    used to attribute a warning to a specific plugin."""
    for rx in (_re_ver_fun, _re_size_fun, _re_desc_fun, _re_mwselua_fun):
        m = rx.match(token)
        if m:
            prx = mlox_pattern_to_regex(m.group(3).strip())
            return {p for p in active_plugins if prx.match(p)}
    return set()


def parse_mlox_lisp(tokens: list) -> list:
    """Recursively builds a nested-list AST from bracketed mlox logic tokens."""
    if not tokens:
        return []
    ast = []
    while tokens:
        token = tokens.pop(0)
        if token == "[":
            ast.append(parse_mlox_lisp(tokens))
        elif token == "]":
            return ast
        else:
            ast.append(token)
    return ast


def evaluate_node(node, active_plugins: set, index=None) -> bool:
    """Evaluates one AST node (a plugin pattern, an ALL/ANY/NOT/DESC group, or
    an atomic [VER]/[SIZE]/[DESC]/[MWSE-LUA] function) against the set of
    plugins active in final_order. index (a PluginFileIndex) lets the function
    predicates read real plugin version/size/description; None falls back to
    mlox's conservative behaviour."""
    if isinstance(node, str):
        if node.startswith("["):  # atomic [VER]/[SIZE]/[DESC]/[MWSE-LUA] token
            return _eval_func_token(node, active_plugins, index)
        if node.startswith("/") and node.endswith("/"):
            return True  # /DESC message/ strings carry no truth value
        rx = mlox_pattern_to_regex(node)
        return any(rx.match(p) for p in active_plugins)

    if isinstance(node, list) and node:
        op = node[0].upper() if isinstance(node[0], str) else ""
        if op == "ALL":
            return all(evaluate_node(arg, active_plugins, index) for arg in node[1:])
        elif op == "ANY":
            return any(evaluate_node(arg, active_plugins, index) for arg in node[1:])
        elif op == "NOT":
            return len(node) >= 2 and not evaluate_node(node[1], active_plugins, index)
        elif op == "DESC":
            return evaluate_node(node[-1], active_plugins, index)
        else:
            # a flat list with no leading operator implies ANY in mlox
            return any(evaluate_node(arg, active_plugins, index) for arg in node)
    return False


def get_triggered_plugins(node, active_plugins: set, index=None) -> set:
    """Names of active plugins that actually matched inside an AST node --
    used to make the printed warning say which plugin(s) triggered it."""
    found = set()
    if isinstance(node, str):
        if node.startswith("["):  # atomic function token
            return _func_token_matches(node, active_plugins)
        if node.startswith("/") and node.endswith("/"):
            return found  # /DESC message/ strings match no plugin
        rx = mlox_pattern_to_regex(node)
        for p in active_plugins:
            if rx.match(p):
                found.add(p)
    elif isinstance(node, list) and node:
        for arg in node[1:]:
            found.update(get_triggered_plugins(arg, active_plugins, index))
    return found


def describe_node(node) -> str:
    """Renders an AST node back to a short human-readable string -- used to
    name a MISSING dependency in a [Requires] warning, since a missing
    plugin can't be looked up in active_plugins to get its real name back."""
    if isinstance(node, str):
        return node
    if isinstance(node, list) and node:
        if isinstance(node[0], str) and node[0].upper() in ("ALL", "ANY", "NOT", "DESC"):
            op, rest = node[0].upper(), node[1:]
        else:
            op, rest = "ANY", node
        return f"{op}({', '.join(describe_node(n) for n in rest)})"
    return "?"


def load_rules_raw_text(rule_paths):
    """Concatenates the raw (uncommented, unsplit) text of every rule file,
    in the same file-discovery order as load_rule_edges -- used so the
    predicate evaluator can see the original [Requires]/[Conflict]/[Note]
    bodies, including their DESC message text."""
    chunks = []
    for p in rule_paths:
        p = Path(p)
        files = [p] if p.is_file() else sorted(p.glob("*.txt"))
        for f in files:
            try:
                chunks.append(f.read_text(encoding="utf-8-sig", errors="replace"))
            except Exception as e:
                print(f"WARNING: could not read rule file {f} for predicate checks: {e}", file=sys.stderr)
    return "\n".join(chunks)


def check_predicates(rules_text: str, final_order: list, subset_origins: dict = None,
                     data_dirs=None) -> list:
    """Extracts and evaluates [Conflict], [Requires], and [Note] blocks
    against the final active plugin list. Returns a list of warning strings.

    data_dirs (optional): the cfg's data= directories, used to locate plugin
    files so [VER]/[SIZE]/[DESC] predicates can read real version/size/
    description info. If omitted or unreadable, those predicates fall back to
    mlox's conservative behaviour.
    Purely read-only -- never affects sorting or what gets written.

    subset_origins (optional): {plugin_name_lower: "where this came from"},
    e.g. "customizations.toml -> 'total-overhaul'" or "subset file (foo.txt)"
    -- lets a warning say "NewMod.esp [customizations.toml -> 'total-overhaul']"
    instead of just "NewMod.esp", making it obvious which of YOUR mods (as
    opposed to something already sitting in the frozen openmw.cfg base) is
    the one to go fix. Plugins with no entry here are printed unannotated.
    """
    subset_origins = subset_origins or {}
    active_set = set(final_order)
    index = PluginFileIndex(data_dirs)
    warnings = []

    def annotate(name: str) -> str:
        origin = subset_origins.get(name.lower())
        return f"{name} [{origin}]" if origin else name

    def annotate_all(names) -> str:
        return ", ".join(annotate(n) for n in sorted(names))

    matches = list(TOP_RE.finditer(rules_text))
    for idx, m in enumerate(matches):
        keyword = m.group(1).title()
        if keyword not in ("Conflict", "Requires", "Note"):
            continue

        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(rules_text)
        body = rules_text[start:end]

        # Split lines into "logic" (expressions) vs. message text the way the
        # real mlox does: a line that starts with WHITESPACE is message text
        # (mlox's re_message = ^\s), everything else is an expression line.
        # The first body line is the remainder of the header line itself, so
        # it's always logic. (The old heuristic classified by content --
        # "contains brackets or a plugin extension" -- which turned the
        # thousands of indented message lines in mlox_base that happen to
        # mention a plugin name into phantom logic operands, producing false
        # conflict/note warnings.)
        message_lines = []
        header_arg = (m.group(2) or "").strip()
        if header_arg:
            message_lines.append(header_arg)  # mlox: header args are the message
        logic_text = ""
        depth = 0   # unclosed brackets carried across lines
        for i, raw_line in enumerate(body.splitlines()):
            line = strip_comment(raw_line).strip()
            if not line:
                continue
            # An indented line is message text ONLY when no bracket expression
            # is open: mlox expressions like  [ALL A.esp\n\t[NOT B.esp]\n\tC.esm]
            # continue across indented lines, and treating those continuations
            # as message text truncated the condition -- e.g. the Uvirith's
            # Legacy / Children of Morrowind note fired for people without
            # Children of Morrowind because its [ALL ...] lost two conjuncts.
            if i > 0 and depth == 0 and raw_line[:1] in (" ", "\t"):
                message_lines.append(line)
            else:
                logic_text += " " + line
                depth += line.count("[") - line.count("]")
                if depth < 0:
                    depth = 0

        message = " ".join(message_lines).strip()
        ast = parse_mlox_lisp(tokenize_mlox_logic(logic_text))
        if not ast:
            continue

        if keyword == "Conflict":
            # a [Conflict] block is a flat list of mutually-exclusive
            # items/groups -- warn if more than one is simultaneously active
            true_nodes = [n for n in ast if evaluate_node(n, active_set, index)]
            if len(true_nodes) > 1:
                triggered_by = set()
                for n in true_nodes:
                    triggered_by.update(get_triggered_plugins(n, active_set, index))
                warning_msg = f"[CONFLICT] {message}"
                if triggered_by:
                    warning_msg += f"\n    Caused by: {annotate_all(triggered_by)}"
                warnings.append(warning_msg)

        elif keyword == "Requires":
            # first item is the "target", the rest are its dependencies
            if len(ast) >= 2 and evaluate_node(ast[0], active_set, index):
                target_names = get_triggered_plugins(ast[0], active_set, index)
                missing = [n for n in ast[1:] if not evaluate_node(n, active_set, index)]
                if missing:
                    warning_msg = f"[REQUIRES] {message}"
                    if target_names:
                        warning_msg += f"\n    Needed by: {annotate_all(target_names)}"
                    warning_msg += f"\n    Missing: {', '.join(describe_node(n) for n in missing)}"
                    warnings.append(warning_msg)

        elif keyword == "Note":
            # notes fire when everything listed is simultaneously true
            if all(evaluate_node(n, active_set, index) for n in ast):
                triggered_by = set()
                for n in ast:
                    triggered_by.update(get_triggered_plugins(n, active_set, index))
                warning_msg = f"[NOTE] {message}"
                if triggered_by:
                    warning_msg += f"\n    About: {annotate_all(triggered_by)}"
                warnings.append(warning_msg)

    return warnings


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

def parse_plugin_order_yml(path: Path):
    """Returns a list of {file_name, for_mod, on_lists:[...], needs_cleaning:bool}
    entries, in file order (which is the canonical load order). Prefers PyYAML
    if it's installed (robust), but falls back to a focused line parser for this
    file's very regular structure so the feature works with a stdlib-only Python
    -- MOMW users won't necessarily have PyYAML. The fallback deliberately
    ignores the nested `depends:` blocks some entries carry (their indented
    `- file_name:` items must NOT be mistaken for top-level plugin entries)."""
    text = path.read_text(encoding="utf-8", errors="replace")

    try:
        import yaml  # PyYAML, if available
        raw = yaml.safe_load(text) or []
        entries = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            fn = e.get("file_name")
            if not fn:
                continue
            entries.append({
                "file_name": str(fn),
                "for_mod": e.get("for_mod"),
                "on_lists": [str(x) for x in (e.get("on_lists") or [])],
                "needs_cleaning": bool(e.get("needs_cleaning")),
            })
        return entries
    except ImportError:
        pass  # fall through to the hand parser

    def apply_kv(entry, s):
        if ":" not in s:
            return
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k == "file_name" and entry["file_name"] is None:
            entry["file_name"] = v
        elif k == "for_mod":
            entry["for_mod"] = v
        elif k == "needs_cleaning":
            entry["needs_cleaning"] = v.lower() in ("true", "yes", "1")

    entries, cur, mode = [], None, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):  # top-level list item = new plugin entry
            if cur and cur["file_name"]:
                entries.append(cur)
            cur = {"file_name": None, "for_mod": None, "on_lists": [], "needs_cleaning": False}
            mode = None
            apply_kv(cur, line[2:])
            continue
        if cur is None:
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 2 and ":" in line:
            key = stripped.split(":", 1)[0].strip()
            mode = key if key in ("on_lists", "depends") else None
            if mode is None:
                apply_kv(cur, stripped)
        elif indent >= 4 and stripped.startswith("- ") and mode == "on_lists":
            val = stripped[2:].strip().strip('"').strip("'")
            if val:
                cur["on_lists"].append(val)
        # anything else (incl. nested depends: items) is ignored
    if cur and cur["file_name"]:
        entries.append(cur)
    return entries


def curated_for_list(entries, list_name: str):
    """Returns (curated_lower_set, curated_ordered_names) for one mod list:
    the plugins whose on_lists contains list_name, as a lowercase set (for
    membership tests) and as a file-order list of their real names (the
    canonical load order for that list)."""
    ln = (list_name or "").lower()
    curated_set, curated_order = set(), []
    if not ln:
        return curated_set, curated_order
    for e in entries:
        if any(l.lower() == ln for l in e["on_lists"]):
            curated_set.add(e["file_name"].lower())
            curated_order.append(e["file_name"])
    return curated_set, curated_order


def needs_cleaning_set(entries):
    return {e["file_name"].lower() for e in entries if e["needs_cleaning"]}


def base_order_matches_yml(base_order_names, curated_order):
    """Read-only check: does the relative order of the curated plugins in the
    cfg match the yml's canonical order for the list? Returns a list of
    warning strings (empty if consistent). Only compares plugins present in
    BOTH, so extra custom plugins interleaved in the cfg don't trip it."""
    curated_lower_order = [n.lower() for n in curated_order]
    rank = {n: i for i, n in enumerate(curated_lower_order)}
    cfg_curated = [n for n in base_order_names if n.lower() in rank]
    warnings = []
    prev_rank, prev_name = -1, None
    for name in cfg_curated:
        r = rank[name.lower()]
        if r < prev_rank:
            warnings.append(
                f"[LIST ORDER] '{name}' appears after '{prev_name}' in your cfg, but the "
                f"curated list order has it BEFORE. Your base order may have drifted from the "
                f"canonical list order (or a tool reordered it).")
            break  # one clear report is enough; the rest usually cascade from it
        prev_rank, prev_name = r, name
    return warnings


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

SCAN_ASSET_FOLDERS = frozenset({
    "icons", "meshes", "scripts", "sound", "textures",
    "bookart", "music", "fonts", "splash", "video",
})


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
            (f for f in files if os.path.splitext(f)[1].lower() in PLUGIN_EXTS),
            key=str.lower,
        )
        if has_asset_folder or plugins:
            lines.append(os.path.abspath(root))
            lines.extend(plugins)
            lines.append("")  # blank separator for readability
            n_folders += 1
            n_plugins += len(plugins)
            dirs[:] = []  # matched -> don't descend further into this branch

    text = "\n".join(lines) + ("\n" if lines else "")
    if output_path is not None:
        Path(output_path).write_text(text, encoding="utf-8")
    print(f"Scanned '{os.path.abspath(start_path)}': "
          f"{n_folders} mod folder(s), {n_plugins} plugin(s).")
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
    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore
        data = tomllib.loads(text)
        plugins, data_inserts = [], []
        for raw in data.get("subset", []):
            _classify_subset_entry(str(raw), plugins, data_inserts, str(path))
        for raw in data.get("data", []):
            data_inserts.append({"value": str(raw), "after": None, "before": None})
        return plugins, data_inserts

    return extract_subset_from_lines(text.splitlines(), source=str(path))


def _classify_subset_entry(raw, plugins, data_inserts, source):
    """Classify one raw subset entry: a recognized plugin extension makes it a
    plugin; otherwise a slash/backslash makes it a data= folder path; otherwise
    it's skipped with a warning (nothing safe to guess from a bare word)."""
    raw = raw.strip()
    if not raw:
        return
    name = basename_if_plugin(raw)
    if name:
        plugins.append(name)
    elif "/" in raw.replace("\\", "/"):
        data_inserts.append({"value": raw, "after": None, "before": None})
    else:
        print(f"WARNING: '{raw}' from {source} doesn't look like a plugin filename or a "
              f"data folder path (no recognized extension, no slash) -- skipping.",
              file=sys.stderr)


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
    return line[:m.start()] if m else line


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
        except ModuleNotFoundError:
            raise SystemExit(
                "Need Python 3.11+ (tomllib) or `pip install tomli --break-system-packages` "
                "to parse the customizations TOML."
            )

    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
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
            handle_insert(rep.get("dest", ""), listname, after=None, before=rep.get("source"), is_replace=True)
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

def detect_data_quoting(data_lines) -> bool:
    """Returns True if the existing data= lines in this openmw.cfg predominantly
    wrap their path in double quotes (data="..."), False if they're bare
    (data=...). New/inserted data= lines are then formatted to MATCH whichever
    convention the file already uses, instead of being unconditionally quoted.

    This matters because a cfg written in the classic bare style treats the
    quote characters as literal parts of the path -- so injecting a quoted
    data="C:\\Foo" line into an otherwise-unquoted cfg can make OpenMW look for
    a folder literally named "C:\\Foo" (quotes included) and fail to load the
    mod. Following the file's own convention avoids that and keeps the sorting
    panel's display consistent. Empty/tie/no-data-lines defaults to bare (the
    momw-configurator/umo default on the setups this tool targets)."""
    quoted = unquoted = 0
    for line in data_lines:
        m = re.match(r"^\s*data\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
        if not m:
            continue
        val = m.group(1).strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            quoted += 1
        else:
            unquoted += 1
    return quoted > unquoted


def format_data_line(path_value: str, quoted: bool = False) -> str:
    v = path_value.strip().strip('"').strip("'")
    return f'data="{v}"' if quoted else f"data={v}"


def find_anchor_index(lines, anchor: str):
    anchor_l = anchor.lower()
    for i, l in enumerate(lines):
        if anchor_l in l.lower():
            return i
    return None


def extract_data_path_value(line: str):
    """Pulls the bare path back out of a raw 'data="..."' cfg line. Returns
    None if the line doesn't actually match (nil-guarded -- callers may pass
    arbitrary lines)."""
    m = re.match(r"^\s*data\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def normalize_data_path(value: str) -> str:
    """Normalizes a data= path value for duplicate-detection purposes only
    (never for display/writing) -- case-insensitive, slash-direction-
    insensitive, trailing-slash-insensitive. Two paths that normalize to the
    same string are treated as 'the same data= entry' even if written
    differently (e.g. a Windows path with backslashes vs. one with forward
    slashes, or differing only in case)."""
    if not value:
        return ""
    return value.strip().strip('"').strip("'").replace("\\", "/").rstrip("/").lower()


def list_plugins_in_dir(path_value: str, base_dir: Path = None):
    """
    Looks (non-recursively -- OpenMW/mlox both expect plugins directly inside
    the folder a data= line points at) for .esp/.esm/etc files in the folder
    a data= path points to, and returns their filenames.

    Heavily nil-guarded on purpose: path_value can be almost anything (an
    absolute Windows path pasted into a TOML written on Linux, an MO2
    variable that was never substituted, a typo, a network share that isn't
    mounted right now, ...). Any failure here should just mean "we don't
    know what's in this folder", not crash the whole sort.
    """
    if not path_value:
        return []
    candidates = []
    try:
        raw = path_value.strip().strip('"').strip("'")
        if not raw:
            return []
        p = Path(raw)
        candidates.append(p)
        if base_dir is not None and not p.is_absolute():
            candidates.append(base_dir / p)
    except (TypeError, ValueError, OSError):
        return []

    for p in candidates:
        try:
            if not p.is_dir():
                continue
            return sorted(
                entry.name for entry in p.iterdir()
                if entry.is_file() and entry.name.lower().endswith(PLUGIN_EXTS)
            )
        except (OSError, PermissionError):
            continue
    return []


def infer_data_path_anchors(data_inserts, data_order, final_order, cfg_path: Path):
    """
    For any data_insert that has NO explicit after/before anchor, tries to
    work one out by looking at what's actually sitting in that folder: if it
    contains a plugin that's also somewhere in the mlox-sorted final_order,
    anchor the data= line next to whichever EXISTING (frozen) data= path
    owns the nearest neighboring plugin in that same sorted order.

    Only ever touches inserts that arrived with no anchor at all -- an
    explicit after/before written in the TOML is always left alone, since
    that's the user's stated intent and this is just a best-effort fallback
    for when there wasn't one. Mutates the dicts in data_inserts in place
    and returns nothing; every lookup here is independently nil-guarded so a
    folder that can't be scanned, or a plugin that isn't in final_order,
    just falls through to the existing "no anchor -> append at end" behavior
    in insert_data_paths rather than raising.
    """
    if not final_order:
        return  # no content sort happened this run -- nothing to anchor against

    base_dir = cfg_path.parent if cfg_path else None

    # plugin (lowercased) -> owning EXISTING data= line's path value.
    # Deliberately built from data_order (frozen/base paths) only -- new
    # inserts can't anchor off each other in the same run (see
    # insert_data_paths' docstring), so they're not eligible anchor targets.
    plugin_owner = {}
    for line in data_order:
        val = extract_data_path_value(line)
        if not val:
            continue
        for plugin in list_plugins_in_dir(val, base_dir):
            plugin_owner.setdefault(plugin.lower(), val)

    order_index = {name.lower(): i for i, name in enumerate(final_order)}

    for item in data_inserts:
        if item.get("after") or item.get("before"):
            continue  # explicit anchor already given -- don't second-guess it

        own_plugins = {p.lower() for p in list_plugins_in_dir(item["value"], base_dir)}
        if not own_plugins:
            continue  # empty/unreadable/no-plugin folder -- nothing to infer from

        # find where (if anywhere) this folder's plugins land in the sort
        positions = sorted(order_index[p] for p in own_plugins if p in order_index)
        if not positions:
            continue  # plugins exist but aren't part of this run's sorted set

        lo, hi = positions[0], positions[-1]

        anchor_value, mode = None, None
        # walk backward from the folder's own plugins for an owned neighbor
        for i in range(lo - 1, -1, -1):
            owner = plugin_owner.get(final_order[i].lower())
            if owner:
                anchor_value, mode = owner, "after"
                break
        if not anchor_value:
            # nothing usable behind it -- try forward instead
            for i in range(hi + 1, len(final_order)):
                owner = plugin_owner.get(final_order[i].lower())
                if owner:
                    anchor_value, mode = owner, "before"
                    break

        if anchor_value:
            item[mode] = anchor_value
            via = sorted(own_plugins & order_index.keys())[0]
            print(f"  Inferred anchor for '{item['value']}': {mode} '{anchor_value}' "
                  f"(via plugin {via})")


def insert_data_paths(data_lines, data_inserts):
    """
    data_lines: existing raw data= lines from openmw.cfg, in file order.
    data_inserts: list of {"value": str, "after": str|None, "before": str|None},
    in the order they appeared in momw-customizations.toml.

    Anchors are matched as a case-insensitive substring against the EXISTING
    data_lines only (an insert can't anchor off another new insert in the
    same run -- keep multi-step chains in separate runs if you need that).

    Guarded against duplicates: an insert whose path already matches an
    existing data_lines entry (case/slash-direction/trailing-slash
    insensitive -- see normalize_data_path) is skipped rather than added a
    second time, and so is a second insert in the same run pointing at a
    path another insert already claimed. This only affects whether a NEW
    line gets added -- it never removes or reorders an existing line, so
    dragging existing entries around in the order panel is unaffected.

    Returns a list of (line_text, is_new, source_value_or_None).
    """
    existing_normalized = {normalize_data_path(extract_data_path_value(line)) for line in data_lines}
    existing_normalized.discard("")
    quoted = detect_data_quoting(data_lines)

    anchor_map = {}
    leftover = []
    for item in data_inserts:
        norm_val = normalize_data_path(item["value"])
        if norm_val and norm_val in existing_normalized:
            print(f"NOTE: '{item['value']}' already present in data= list -- skipping duplicate insert.")
            continue
        if norm_val:
            existing_normalized.add(norm_val)  # also guards duplicate NEW inserts within this same run

        new_line = format_data_line(item["value"], quoted)
        anchor = item.get("after") or item.get("before")
        mode = "after" if item.get("after") else ("before" if item.get("before") else None)
        if not anchor:
            print(f"NOTE: '{item['value']}' has no after/before anchor -- appending at end of data= list.")
            leftover.append((new_line, item["value"]))
            continue
        idx = find_anchor_index(data_lines, anchor)
        if idx is None:
            print(f"WARNING: anchor '{anchor}' not found among existing data= paths for "
                  f"'{item['value']}' -- appending at end instead.")
            leftover.append((new_line, item["value"]))
            continue
        anchor_map.setdefault(idx, []).append((mode, new_line, item["value"]))

    result = []
    for i, line in enumerate(data_lines):
        for mode, new_line, val in anchor_map.get(i, []):
            if mode == "before":
                result.append((new_line, True, val))
        result.append((line, False, None))
        for mode, new_line, val in anchor_map.get(i, []):
            if mode == "after":
                result.append((new_line, True, val))
    for new_line, val in leftover:
        result.append((new_line, True, val))
    return result


# ---------------------------------------------------------------------------
# TOML generation -- write a corrected momw-customizations.toml so the fix
# persists across umo/momw-configurator rebuilds, instead of only patching
# openmw.cfg (which a rebuild would just overwrite again)
# ---------------------------------------------------------------------------

def toml_value(v: str) -> str:
    """
    Prefer a single-quoted TOML literal string ('...') for everything --
    plugin names, script names, and especially paths. Literal strings are
    raw (TOML does zero escape processing on their contents), which is
    exactly what a Windows path full of backslashes needs: 'C:\\Games\\...'
    is correct and readable as-is, whereas a double-quoted *basic* string
    would require every backslash doubled ("C:\\\\Games\\\\..."), which is
    not what momw-configurator/umo actually write.

    A literal string can't contain a `'` itself (it would end the string
    early), so a name with an apostrophe -- e.g. "MyMod's.esp" -- gets
    escalated to a triple-single-quoted multi-line literal string instead
    ('''MyMod's.esp'''), which tolerates a lone `'` (just not three in a
    row). In the vanishingly unlikely case a filename contains `'''`
    itself, fall back to a properly escaped double-quoted basic string as
    a last resort.
    """
    if "'''" in v:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if "'" in v:
        return "'''" + v + "'''"
    return "'" + v + "'"


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
            files = [sd.rstrip(b"\x00").decode("utf-8", "replace")
                     for st, sd in _iter_subrecords(body) if st == b"DEPE"]
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
    ".preclean.bak",      # ours: original before a staged tes3cmd clean
    ".masterfix.bak",     # ours: original before a master-size resync
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
                        orig, kind = p.with_name(fn[:-len(suf)]), suf.lstrip(".")
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
                    out.append((p, orig if (orig and orig.exists()) else orig, kind))
    out.sort(key=lambda t: str(t[0]).lower())
    return out


USER_RULES_HEADER = (
    ";; Personal mlox rules -- written by you (with help from MLOX Subset Sort's\n"
    ";; rule maker). Keep this file LAST in the rule-files list: later files win\n"
    ";; rule conflicts, so your rules override mlox_base/mlox_user.\n"
    ";; Syntax: https://morrowind-modding.github.io/modding-tools/sorting-plugin-load-order/mlox/mlox-rule-guidelines\n")


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
    for a, b in zip(names, names[1:]):
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
            raise ValueError(f"{n!r} must end in a plugin extension "
                             f"(.esp/.esm/.omwaddon/.omwgame/.omwscripts, optionally '*')")
        if n.lower() in seen:
            # a plugin listed twice orders it relative to itself -- a
            # self-cycle mlox would discard; always a mistake
            raise ValueError(f"'{n}' is listed more than once -- a plugin can't be "
                             f"ordered relative to itself")
        seen.add(n.lower())
    parts = []
    if comment and str(comment).strip():
        parts += [f";; {l}" for l in str(comment).strip().splitlines()]
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
PLUGIN_ORDER_URLS = (
    "https://gitlab.com/modding-openmw/modding-openmw.com/-/raw/master/momw/momw/"
    "data_seeds/data/plugin-order.yml?ref_type=heads&inline=false",
    "https://gitlab.com/api/v4/projects/modding-openmw%2Fmodding-openmw.com/repository/files/"
    "momw%2Fmomw%2Fdata_seeds%2Fdata%2Fplugin-order.yml/raw?ref=master",
)


def update_plugin_order_yml(path, urls=None, timeout=45):
    """Download the current MOMW plugin-order.yml over the configured file.
    STRICTLY validated before anything is touched: the download must parse
    with parse_plugin_order_yml and contain a sane number of entries, so a
    wrong URL / error page can never clobber the file. The old file is kept
    as a timestamped .bak. Returns report lines."""
    import urllib.request
    import tempfile as _tf
    p = Path(path)
    # precedence: explicit urls param (e.g. the GUI's Sources setting) >
    # $MLOX_PLUGIN_ORDER_URL > built-in candidates
    env = os.environ.get("MLOX_PLUGIN_ORDER_URL")
    cand = list(urls) if urls else ([env] if env else list(PLUGIN_ORDER_URLS))
    report = []
    for url in cand:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = r.read()
        except Exception as e:
            report.append(f"  {url}: {e}")
            continue
        if b"file_name" not in data or b"on_lists" not in data:
            report.append(f"  {url}: response doesn't look like plugin-order.yml")
            continue
        # full validation through the real parser before touching anything
        try:
            with _tf.NamedTemporaryFile("wb", suffix=".yml", delete=False) as tf:
                tf.write(data)
                tmp = Path(tf.name)
            entries = parse_plugin_order_yml(tmp)
            tmp.unlink()
        except Exception as e:
            report.append(f"  {url}: downloaded but failed to parse ({e})")
            continue
        if len(entries) < 100:
            report.append(f"  {url}: parsed but only {len(entries)} entries -- refusing")
            continue
        old = p.read_bytes() if p.exists() else b""
        if old == data:
            report.append(f"{p.name}: already up to date ({len(entries)} entries).")
            return report
        if p.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            p.with_name(p.name + f".bak-{stamp}").write_bytes(old)
        p.write_bytes(data)
        try:
            old_entries = None
            if old:
                with _tf.NamedTemporaryFile("wb", suffix=".yml", delete=False) as tf:
                    tf.write(old)
                    tmp = Path(tf.name)
                old_entries = len(parse_plugin_order_yml(tmp))
                tmp.unlink()
        except Exception:
            old_entries = None
        frm = f"{old_entries} -> " if old_entries is not None else ""
        report.append(f"{p.name}: updated from {url} ({frm}{len(entries)} entries; "
                      f"previous version kept as .bak).")
        return report
    report.insert(0, "FAILED: no source produced a valid plugin-order.yml:")
    report.append("  (set $MLOX_PLUGIN_ORDER_URL if MOMW moved the file)")
    return report


RULES_REPO = "DanaePlays/mlox-rules"   # actively maintained; plox uses it, mlox 1.1+ auto-updates from it
# {name} is replaced with the rule filename (mlox_base.txt / mlox_user.txt).
# Users can point this at a fork/mirror via the GUI's Sources dialog or
# $MLOX_RULES_URL_TEMPLATE.
RULES_URL_TEMPLATE = "https://raw.githubusercontent.com/" + RULES_REPO + "/main/{name}"


def update_rule_files(rule_paths, url_template=None, timeout=30):
    """Download the current mlox_base.txt / mlox_user.txt from the maintained
    rules repo over any configured rule file with a matching filename. The old
    file is kept as a timestamped .bak. Only filenames the repo actually
    manages are touched -- a personal rules file with another name is skipped.

    url_template (or $MLOX_RULES_URL_TEMPLATE) overrides the source; it must
    contain '{name}', which is replaced per file. Returns report lines."""
    import urllib.request
    template = (url_template or os.environ.get("MLOX_RULES_URL_TEMPLATE")
                or RULES_URL_TEMPLATE)
    if "{name}" not in template:
        return [f"FAILED: rules URL template must contain '{{name}}': {template}"]
    report = []
    for p in rule_paths:
        p = Path(p)
        if p.name.lower() not in ("mlox_base.txt", "mlox_user.txt"):
            report.append(f"skipped {p.name}: not an upstream-managed filename")
            continue
        url = template.format(name=p.name)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = r.read()
        except Exception as e:
            report.append(f"FAILED {p.name}: {e}")
            continue
        if not data or b"[Order]" not in data:
            report.append(f"FAILED {p.name}: download doesn't look like an mlox rules file")
            continue
        old = p.read_bytes() if p.exists() else b""
        if old == data:
            report.append(f"{p.name}: already up to date ({len(data):,} bytes)")
            continue
        if p.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            p.with_name(p.name + f".bak-{stamp}").write_bytes(old)
        p.write_bytes(data)
        report.append(f"{p.name}: updated {len(old):,} -> {len(data):,} bytes "
                      f"(previous version kept as .bak)")
    return report


def rule_file_ages(rule_paths):
    """[(name, age_days or None)] for showing how stale the rule files are."""
    out = []
    now = datetime.now().timestamp()
    for p in rule_paths:
        p = Path(p)
        try:
            out.append((p.name, int((now - p.stat().st_mtime) // 86400)))
        except OSError:
            out.append((p.name, None))
    return out


def cfg_line_value(line):
    """The value part of a cfg line, unquoted -- mirror of custom.go's
    cfgLineValue(). Returns None for lines without '='."""
    if "=" not in line:
        return None
    v = line.split("=", 1)[1].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def configurator_remove_matches(val, line):
    """Mirror of custom.go's shouldRemoveLine(): path-like values (containing
    a slash) compare against the line's VALUE (exact or '/'-suffix match);
    everything else is a plain whole-line substring test."""
    if "/" in val or "\\" in val:
        lv = cfg_line_value(line)
        if lv is None:
            return False
        lvn = lv.replace("\\", "/").strip()
        vn = val.replace("\\", "/").strip()
        is_abs = vn.startswith("/") or (len(vn) >= 3 and vn[1] == ":" and vn[2] == "/")
        return lvn == vn or (not is_abs and lvn.endswith("/" + vn))
    return val in line


def simulate_configurator_apply(cfg_lines, toml_text, list_name=None):
    """Faithful re-implementation of momw-configurator's ApplyCustomizations
    (cfg/custom.go), so the emitted TOML can be dry-run against a cfg BEFORE
    anyone runs the real Configurator. Mirrors, per the Go source:

      * insert: after/before matched by whole-line substring; 0 matches ->
        error per insert; >1 matches -> FATAL (the Go code returns a nil cfg);
        after -> target_idx+1, before -> target_idx; prefix copied from the
        matched line; insertBlock lines inserted sequentially.
      * replace: whole-line substring; >1 matches -> error, entry skipped;
        data lines get quoted values.
      * remove: shouldRemoveLine semantics (see configurator_remove_matches);
        EVERY matching line is removed, silently.
      * append: groundcover= lines routed to the groundcover section (after
        the last groundcover= line, or a new section), the rest to an
        '# APPENDED LINES #' section at the end.
      * apply order per block: inserts, replaces, removes, appends.

    Template vars ({{.ModBaseDir}}) and $ENV vars are NOT expanded -- a note
    is returned instead, since the preview can't know the Configurator's
    config. Returns (new_lines_or_None, errors, notes); None means the run
    would abort (ambiguous insert anchor)."""
    try:
        import tomllib as _toml
    except ModuleNotFoundError:
        try:
            import tomli as _toml  # type: ignore
        except ModuleNotFoundError:
            return None, [], ["preview skipped: needs Python 3.11+ (tomllib) or 'pip install tomli'"]
    try:
        data = _toml.loads(toml_text)
    except Exception as e:
        return None, [f"emitted TOML failed to parse: {e}"], []

    lines = list(cfg_lines)
    errs, notes = [], []

    def _check_templates(v):
        if "{{" in v or "$" in v:
            notes.append(f"'{v}': template/env var left unexpanded in the preview")

    for cust in data.get("Customizations") or []:
        if list_name and cust.get("listName") and cust.get("listName") != list_name:
            continue

        # 1) inserts
        for st in cust.get("insert") or []:
            insert, iblock = st.get("insert"), st.get("insertBlock")
            after, before = st.get("after"), st.get("before")
            target = after if after is not None else before
            if (insert is None and iblock is None) or target is None:
                errs.append("insert entry needs insert/insertBlock plus after or before")
                continue
            if after is not None and before is not None:
                errs.append("after and before cannot be used together")
                continue
            matches = [i for i, l in enumerate(lines) if target in l]
            if not matches:
                errs.append(f"target line is not present in openmw.cfg: {target}")
                continue
            if len(matches) > 1:
                errs.append(f"FATAL: multiple matches for anchor '{target}' -- the real "
                            f"Configurator abandons the cfg here")
                return None, errs, notes
            idx = matches[0]
            prefix = lines[idx].split("=")[0]
            dest = idx + 1 if after is not None else idx
            vals = [insert] if insert is not None else \
                   [l.replace("\r", "") for l in iblock.split("\n") if l]
            for v in vals:
                _check_templates(v)
                lines.insert(dest, f"{prefix}={v}")
                dest += 1

        # 2) replaces
        for st in cust.get("replace") or []:
            src, dst = st.get("source"), st.get("dest")
            if src is None or dst is None:
                errs.append("replace entry needs source and dest")
                continue
            matches = [i for i, l in enumerate(lines) if src in l]
            if len(matches) > 1:
                errs.append(f"replace source '{src}' matches more than one line -- skipped")
                continue
            if matches:
                idx = matches[0]
                prefix = lines[idx].split("=")[0]
                _check_templates(dst)
                lines[idx] = (f'{prefix}="{dst}"' if prefix == "data" else f"{prefix}={dst}")

        # 3) removes (every match, silently)
        rm = []
        for key in ("removeData", "removeFallbackArchive", "removeContent",
                    "removeFallback", "removeGroundcover"):
            rm += list(cust.get(key) or [])
        if rm:
            lines = [l for l in lines if not any(configurator_remove_matches(v, l) for v in rm)]

        # 4) appends
        gc, other = [], []
        for st in cust.get("append") or []:
            vals = [st["append"]] if "append" in st else \
                   [l.replace("\r", "") for l in st.get("appendBlock", "").split("\n") if l]
            for v in vals:
                _check_templates(v)
                (gc if v.startswith("groundcover=") else other).append(v)
        if gc:
            last = max((i for i, l in enumerate(lines) if l.startswith("groundcover=")), default=-1)
            if last >= 0:
                for j, v in enumerate(gc):
                    lines.insert(last + 1 + j, v)
            else:
                lines += ["", "#                   #", "# GROUNDCOVER FILES #", "#                   #"] + gc
        if other:
            lines += ["", "#                #", "# APPENDED LINES #", "#                #"] + other

    return lines, errs, notes


def preview_configurator_result(plan_lines, toml_text, expected_content_order,
                                subset_names, user_data_norms=None, list_name=None):
    """Dry-run the emitted TOML and verify the round trip.

    The real Configurator applies customizations to a FRESH curated cfg (no
    customs in it yet), so the simulation base is the current cfg with this
    run's custom content= lines and custom data= paths stripped out.

    Returns (ok, report_lines): ok is True when the simulated content= order
    exactly matches what the sort computed (accounting for removals)."""
    subset_lower = {str(s).lower() for s in subset_names or ()}
    user_norms = {n for n in (user_data_norms or ()) if n}
    base = []
    for l in plan_lines:
        m = re.match(r"^\s*content\s*=\s*(.+?)\s*$", l, re.IGNORECASE)
        if m and m.group(1).lower() in subset_lower:
            continue
        m = re.match(r"^\s*data\s*=\s*(.+?)\s*$", l, re.IGNORECASE)
        if m and normalize_data_path(m.group(1).strip().strip('"')) in user_norms:
            continue
        base.append(l)

    report = []
    sim, errs, notes = simulate_configurator_apply(base, toml_text, list_name=list_name)
    for n in notes:
        report.append(f"  NOTE: {n}")
    for e in errs:
        report.append(f"  WARNING: {e}")
    if sim is None:
        report.append("  PREVIEW ABORTED -- the real Configurator run would fail the same way.")
        return False, report

    sim_content = [m.group(1) for l in sim
                   for m in [re.match(r"^\s*content\s*=\s*(.+?)\s*$", l, re.IGNORECASE)] if m]
    # expected: the computed order, minus anything the TOML's removes catch
    try:
        import tomllib as _toml
    except ModuleNotFoundError:
        import tomli as _toml  # type: ignore
    data = _toml.loads(toml_text)
    rm = []
    for cust in data.get("Customizations") or []:
        for key in ("removeData", "removeFallbackArchive", "removeContent",
                    "removeFallback", "removeGroundcover"):
            rm += list(cust.get(key) or [])
    expected = [n for n in expected_content_order
                if not any(configurator_remove_matches(v, f"content={n}") for v in rm)]

    if sim_content == expected:
        report.append(f"  VERIFIED: simulated apply reproduces the sorted order exactly "
                      f"({len(sim_content)} content= lines).")
        return True, report
    report.append("  MISMATCH: the simulated Configurator result differs from the sorted order!")
    for i, (a, b) in enumerate(zip(sim_content, expected)):
        if a != b:
            report.append(f"    first difference at #{i}: simulated '{a}' vs expected '{b}'")
            break
    if len(sim_content) != len(expected):
        report.append(f"    lengths differ: simulated {len(sim_content)} vs expected {len(expected)}")
    return False, report


def generate_customizations_toml(original_data, final_content_order, subset_set,
                                   original_content_values, data_result_tuples=None,
                                   raw_data_inserts=None, replace_dest_names=None,
                                   user_data_values=None, list_name=None,
                                   remove_content=None, remove_data=None,
                                   custom_anchors=None):
    """
    original_data: parsed dict of the source momw-customizations.toml (listName,
      removeData/removeContent/removeFallback/removeGroundcover, replace, append
      blocks are preserved verbatim).
    final_content_order: full mlox-sorted content= plugin list.
    subset_set: which of those are "ours" (need a regenerated insert block).
    original_content_values: {plugin_name: original insert-value string} so we
      keep whatever the user originally wrote (usually identical to the name).
    data_result_tuples: output of insert_data_paths -- (line, is_new, source_value) --
      used when --sort-data-paths was given, to emit re-anchored data inserts.
    raw_data_inserts: original {"value","after","before"} dicts -- used when
      --sort-data-paths was NOT given, to pass the data inserts through unchanged.
    replace_dest_names: plugin names that came from a [[Customizations.replace]]
      "dest" (rather than an [[insert]]) -- these are already written out via the
      "replace" passthrough below, so they're skipped here to avoid emitting a
      duplicate/conflicting insert block for the same plugin. momw-configurator's
      "replace" has no after/before of its own (it just takes the position of
      "source"), so mlox moving one of these relative to the frozen order can't
      actually be expressed as a replace -- it's reported as a warning instead.
    user_data_values: the raw path strings of every data= insert that came from
      THIS run's customizations/subset (before duplicate-skipping). Needed so a
      data path that momw-configurator already baked into openmw.cfg on a prior
      run -- and which insert_data_paths therefore correctly skips as a live-cfg
      duplicate -- still gets re-emitted as an insert block here. Without this,
      the "durable" regenerated TOML would silently lose every data path that's
      already in the cfg (i.e. all of them, since the cfg was built FROM this
      very TOML), leaving momw-configurator nothing to re-insert on the next
      rebuild. A line is treated as "ours" if it's a genuinely new insert OR its
      normalized path is in this set.
    """
    replace_dest_names = replace_dest_names or set()
    subset_set_lower = {s.lower() for s in subset_set}
    original_data = original_data or {}
    # No existing [[Customizations]] block to attach inserts to (e.g. --subset-file
    # was used with no --customizations at all) -- synthesize one so there's
    # somewhere for the insert/replace/append output below to actually go,
    # instead of the whole loop silently iterating zero times.
    # listName is REQUIRED by momw-configurator (it says which curated list the
    # customizations apply to). Precedence: an explicit list_name passed in
    # (--list-name / GUI field) wins; else keep whatever the source TOML had;
    # else fall back to "generated" so the file is at least valid TOML. The
    # override also covers the --subset-file-only case, which otherwise always
    # emitted the useless placeholder "generated".
    default_name = list_name or "generated"
    blocks = original_data.get("Customizations") or [{"listName": default_name}]

    # extra removals from opted-out items that already exist in the cfg -- added
    # to the FIRST block only, so a multi-block file doesn't repeat them
    extra_removes = {"removeContent": list(remove_content or []),
                     "removeData": list(remove_data or [])}

    out = []
    _anchors = []   # every after=/before=/source= value we emit, for the ambiguity check
    _removes = []   # every remove* value we emit -- removal matching is SILENT
    for bi, block in enumerate(blocks):
        out.append("[[Customizations]]")
        name = list_name or block.get("listName")
        if name:
            out.append(f"listName = {toml_value(name)}")
        for key in ("removeData", "removeContent", "removeFallback", "removeGroundcover"):
            vals = list(block.get(key) or [])
            if bi == 0:
                vals += extra_removes.get(key, [])
            # de-dupe case-insensitively, preserving order
            seen, merged = set(), []
            for x in vals:
                if x.lower() not in seen:
                    seen.add(x.lower())
                    merged.append(x)
            if merged:
                # one entry per line, matching the style of MOMW's own
                # documentation examples -- a 25-entry single line is unreadable
                out.append(f"{key} = [")
                for x in merged:
                    out.append(f"  {toml_value(x)},")
                out.append("]")
                _removes.extend(merged)
        out.append("")

        # 1) DATA INSERTS FIRST (Ensures paths are defined before plugins look for them)
        if data_result_tuples:
            # data path inserts, anchored to whatever immediately precedes them
            # in the final data= order (existing line or an earlier new insert).
            # We emit a block for every line that's OURS -- a genuinely new
            # insert, OR an existing cfg line whose path is one of this run's
            # data paths (i.e. one momw-configurator already applied on a prior
            # run). The latter is why we can't just gate on is_new: after the
            # first rebuild every one of our paths is "already in the cfg", and
            # gating on is_new would drop them all from the regenerated TOML.
            user_norms = {normalize_data_path(v) for v in (user_data_values or [])}
            user_norms.discard("")
            prev_line = None
            for line, is_new, value in data_result_tuples:
                path_val = value if value else extract_data_path_value(line)
                norm = normalize_data_path(path_val) if path_val else ""
                is_ours = is_new or (norm and norm in user_norms)
                if is_ours and path_val:
                    out.append("[[Customizations.insert]]")
                    out.append(f"insert = {toml_value(path_val)}")
                    if prev_line is not None:
                        anchor = extract_data_path_value(prev_line) or prev_line.split("=", 1)[-1].strip().strip('"')
                        out.append(f"after = {toml_value(anchor)}")
                        _anchors.append(anchor)
                    else:
                        out.append("# WARNING: this was the very first data= line -- no predecessor to anchor to")
                    out.append("")
                prev_line = line
        elif raw_data_inserts:
            # --sort-data-paths not given -- pass these through exactly as originally written
            for d in raw_data_inserts:
                out.append("[[Customizations.insert]]")
                out.append(f"insert = {toml_value(d['value'])}")
                if d.get("after"):
                    out.append(f"after = {toml_value(d['after'])}")
                    _anchors.append(d["after"])
                elif d.get("before"):
                    out.append(f"before = {toml_value(d['before'])}")
                    _anchors.append(d["before"])
                out.append("")

        # 2) CONTENT INSERTS SECOND
        # content inserts, in mlox-computed order, each anchored to whatever
        # immediately precedes it (already-existing plugin or an earlier
        # insert block in this same file, which will exist by the time
        # momw-configurator gets to this block)
        for i, name in enumerate(final_content_order):
            if name.lower() not in subset_set_lower or name in replace_dest_names:
                continue
            value = original_content_values.get(name, name)
            # Annotate WHY this mod sits here: the after=/before= below is its
            # chained position (documented Configurator semantics), but the
            # REAL reason comes from the sort -- a dependency/rule target, a
            # NearStart/NearEnd hint, or nothing at all (positional only).
            info = (custom_anchors or {}).get(name.lower())
            if info:
                how, anch = info
                if how == "after":
                    out.append(f"# constraint: must load after {toml_value(anch)}")
                elif how == "before":
                    out.append(f"# constraint: must load before {toml_value(anch)}")
                elif how in ("nearstart", "nearend"):
                    out.append(f"# constraint: mlox [{'NearStart' if how == 'nearstart' else 'NearEnd'}] hint")
                else:
                    out.append("# no ordering constraint -- positional only")
            out.append("[[Customizations.insert]]")
            out.append(f"insert = {toml_value(value)}")
            if i == 0:
                # sorted to the very start of the load order -- there's no
                # predecessor to anchor "after", so anchor "before" whatever
                # ended up immediately following it instead
                if len(final_content_order) > 1:
                    out.append(f"before = {toml_value(final_content_order[1])}")
                    _anchors.append(final_content_order[1])
                else:
                    out.append("# WARNING: this is the only content= plugin -- no anchor to write")
            else:
                anchor = final_content_order[i - 1]
                out.append(f"after = {toml_value(anchor)}")
                _anchors.append(anchor)
            out.append("")

        for rep in block.get("replace", []):
            out.append("[[Customizations.replace]]")
            if "source" in rep:
                out.append(f"source = {toml_value(rep['source'])}")
                _anchors.append(rep["source"])
            if "dest" in rep:
                out.append(f"dest = {toml_value(rep['dest'])}")
            out.append("")

        for ap in block.get("append", []):
            out.append("[[Customizations.append]]")
            if "append" in ap:
                out.append(f"append = {toml_value(ap['append'])}")
            if "appendBlock" in ap:
                out.append(f"appendBlock = {toml_value(ap['appendBlock'])}")
            out.append("")

    # Ambiguity checks (warn-only, output unchanged). Verified against
    # momw-configurator's cfg/custom.go:
    #  * after=/before=/source= values are matched with strings.Contains
    #    against WHOLE cfg lines, and >1 match is a hard error (doInsert even
    #    discards the cfg it was building) -- so a filename nested inside
    #    another ('Incantation.omwscripts' in 'content=Incantation.omwscripts.esp')
    #    breaks the configurator run.
    #  * remove* values match the same way but with NO multi-match error --
    #    doRemove silently deletes EVERY matching line, so a nested filename
    #    would silently remove a mod the user never opted out. (Path-like
    #    values instead match the line's value exactly or by /-suffix.)
    haystack = [f"content={n}" for n in final_content_order]
    if data_result_tuples:
        haystack += [line for line, _, _ in data_result_tuples]

    _line_value = cfg_line_value
    _remove_matches = configurator_remove_matches

    for a in dict.fromkeys(_anchors):        # dedupe, keep order
        hits = [l for l in haystack if a in l]
        if len(hits) > 1:
            print(f"WARNING: anchor '{a}' in the emitted TOML matches "
                  f"{len(hits)} openmw.cfg lines -- momw-configurator errors on "
                  f"ambiguous matches. Colliding lines: "
                  f"{'; '.join(hits[:4])}{' ...' if len(hits) > 4 else ''}")
    for r in dict.fromkeys(_removes):
        hits = [l for l in haystack if _remove_matches(r, l)]
        if len(hits) > 1:
            print(f"WARNING: remove entry '{r}' matches {len(hits)} openmw.cfg "
                  f"lines -- momw-configurator removes ALL of them, silently. "
                  f"Colliding lines: {'; '.join(hits[:4])}{' ...' if len(hits) > 4 else ''}")

    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# graph + stable topological sort
# ---------------------------------------------------------------------------

def expand_pattern(pattern, node_pool):
    if pattern_has_meta(pattern):
        rx = mlox_pattern_to_regex(pattern)
        return [n for n in node_pool if rx.match(n)]
    for n in node_pool:
        if n.lower() == pattern.lower():
            return [n]
    return []


def would_create_cycle(adj, start, target, nodes):
    # DFS from target: can we already reach start? if so, adding start->target closes a cycle
    stack = [target]
    seen = set()
    while stack:
        n = stack.pop()
        if n == start:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, ()))
    return False


def _is_master_file(name):
    """True for master-type plugins that should load before ordinary plugins."""
    return name.lower().endswith((".esm", ".omwgame"))


def build_and_sort(base_order_names, subset_names, rule_blocks, masters=None,
                   nearstart=None, nearend=None, anchor_out=None):
    # Guard against the same plugin becoming two different graph nodes just
    # because of a casing difference (OpenMW's VFS is case-insensitive, so
    # 'NewMod.esp' and 'newmod.esp' are the same file) -- canonicalize any
    # subset entry that matches an existing base entry onto that entry's
    # exact spelling, and drop the resulting case-duplicates. Without this,
    # a subset plugin already in the cfg under different casing would get
    # inserted a second time instead of being repositioned.
    trace_sort(f"[sort] === build_and_sort: {len(base_order_names)} base plugin(s), "
          f"{len(subset_names)} subset (custom) plugin(s), {len(rule_blocks)} mlox rule block(s), "
          f"masters for {len(masters or {})} plugin(s) ===")
    base_lower_map = {n.lower(): n for n in base_order_names}
    canonical_subset_names = []
    seen_lower = set()
    for n in subset_names:
        canon = base_lower_map.get(n.lower(), n)
        if canon.lower() != n.lower():
            trace_sort(f"[sort] canonicalize subset '{n}' -> cfg spelling '{canon}'")
        if canon.lower() not in seen_lower:
            seen_lower.add(canon.lower())
            canonical_subset_names.append(canon)
        else:
            trace_sort(f"[sort] drop duplicate subset entry '{n}' (already have '{canon}')")
    subset_names = canonical_subset_names

    base_index = {name: i for i, name in enumerate(base_order_names)}
    nodes = set(base_order_names) | set(subset_names)
    # Deterministic iteration pool: set order is randomized per process
    # (PYTHONHASHSEED), and using `nodes` directly for rule expansion made
    # edge insertion order -- and through it the final sort -- vary from
    # run to run. Same membership, fixed order.
    node_pool = base_order_names + [n for n in subset_names if n not in base_index]
    subset_set = set(subset_names)
    node_lower = {n.lower(): n for n in nodes}
    in_cfg = [n for n in subset_names if n in base_index]
    new_cust = [n for n in subset_names if n not in base_index]
    trace_sort(f"[sort] {len(in_cfg)} custom(s) already in cfg (will be repositioned), "
          f"{len(new_cust)} brand-new custom(s) to insert")

    adj = {n: set() for n in nodes}
    indeg = {n: 0 for n in nodes}
    conflicts = []  # mlox rules we couldn't apply without reordering the frozen cfg

    def add_edge(a, b, label, quiet=False):
        if a == b or b in adj.get(a, ()):
            return True
        if would_create_cycle(adj, a, b, nodes):
            conflicts.append((a, b))
            if not quiet:
                trace_sort(f"[sort]   edge REJECTED (would cycle): '{a}' -> '{b}'  [{label}]")
            return False
        adj[a].add(b)
        indeg[b] += 1
        if not quiet:
            trace_sort(f"[sort]   edge OK: '{a}' -> '{b}'  [{label}]")
        return True

    # 1) frozen chain from the existing cfg order -- but ONLY the curated (non-
    #    subset) plugins. Your custom mods that are already in the cfg must NOT be
    #    chained in place, or they'd be locked between their current neighbors and
    #    couldn't be re-sorted. Bridging over them chains curated[i] -> curated[i+1]
    #    directly, freezing the curated list while leaving customs free to move.
    frozen_seq = [n for n in base_order_names if n not in subset_set]
    trace_sort(f"[sort] step 1: frozen chain over {len(frozen_seq)} curated plugin(s), "
          f"bridging over {len(in_cfg)} custom(s) in the cfg (chain edges not logged individually)")
    for a, b in zip(frozen_seq, frozen_seq[1:]):
        add_edge(a, b, "existing cfg order", quiet=True)

    # 1b) header-master dependencies: every custom plugin must load AFTER each
    #     master it lists in its TES3 header (the real dependency, which mlox's
    #     rule DB doesn't capture for arbitrary mods). Only added for CUSTOM
    #     dependents so the curated list (already master-correct) is never touched.
    trace_sort(f"[sort] step 1b: header-master dependency edges")
    if masters:
        for p in subset_names:
            ms = masters.get(p.lower(), ())
            if ms:
                trace_sort(f"[sort]  '{p}' header masters: {list(ms)}")
            for m in ms:
                mn = node_lower.get(m.lower())
                if mn and mn != p:
                    add_edge(mn, p, "master (header)")
                elif not mn:
                    trace_sort(f"[sort]   master '{m}' of '{p}' NOT installed -- no edge")
    else:
        trace_sort("[sort]  (no header masters available -- mod files not reachable)")

    # 2) mlox ordering edges, but only where they touch a subset plugin (the
    #    frozen base is already ordered by step 1). Within each block we chain
    #    consecutive INSTALLED matches, skipping over patterns that match
    #    nothing you have -- so [Order] A, B, C with B not installed still
    #    yields A -> C directly, preserving the constraint instead of losing it
    #    when B drops out. This is the transitive-bridge behaviour the real mlox
    #    engine gets by keeping a not-installed plugin as a phantom node.
    trace_sort("[sort] step 2: mlox [Order] rule edges (only those touching a custom plugin)")
    _rule_edge_count = 0
    # Higher priority (later file, e.g. mlox_user.txt) FIRST: since add_edge
    # rejects any edge that would close a cycle, the edges added earliest win
    # a conflict -- exactly how the real mlox gives user rules precedence by
    # reading mlox_user.txt before mlox_base.txt. (Sorting ascending here
    # silently gave the BASE file precedence -- backwards.)
    blocks_sorted = sorted(rule_blocks, key=lambda b: -b[1])
    for names, priority in blocks_sorted:
        # expand each token to its installed matches; a token that matches
        # nothing is dropped (it becomes an order bridge, not a broken link)
        survivors = [ms for ms in (expand_pattern(tok, node_pool) for tok in names) if ms]
        for a_matches, b_matches in zip(survivors, survivors[1:]):
            for a in a_matches:
                for b in b_matches:
                    if a == b:
                        continue
                    if a in subset_set or b in subset_set:
                        add_edge(a, b, f"mlox rule (priority {priority})")
                        _rule_edge_count += 1
    trace_sort(f"[sort]  considered rule edges touching customs: {_rule_edge_count}")

    # Report rules we couldn't apply -- deduped and phrased as info, not alarm.
    # These aren't errors: they happen whenever a curated MOMW cfg order
    # intentionally differs from raw mlox. The frozen cfg order is kept and the
    # affected plugin is still placed as well as the non-conflicting rules allow.
    if conflicts:
        seen, unique = set(), []
        for a, b in conflicts:
            key = (a.lower(), b.lower())
            if key not in seen:
                seen.add(key)
                unique.append((a, b))
        print(f"\n  {len(unique)} mlox ordering rule(s) not applied -- your openmw.cfg already "
              f"orders these the other way, so your (curated) cfg order is kept:")
        for a, b in unique:
            print(f"    - mlox wanted '{a}' before '{b}', but your load order already has "
                  f"'{b}' before '{a}'")

    # 3) stable Kahn's topological sort. Tie-break among ready nodes:
    #    (a) masters (.esm/.omwgame) before ordinary plugins -- ESM-first, so a
    #        custom master with no rule still floats up into the master block;
    #    (b) then original cfg position (curated keep their exact order; a custom
    #        already in the cfg keeps its rough spot; brand-new customs sort after
    #        the plugins they were declared after);
    #    (c) then name, for determinism.
    #    Edges always win over the tie-break, so real dependencies/rules dominate.
    import heapq
    nb = len(base_order_names)
    pos = dict(base_index)
    # Position each custom from ALL of its graph predecessors -- header-master
    # edges AND applied mlox [Order] rule edges -- resolved TRANSITIVELY through
    # custom->custom chains: "place this custom right after the latest-loading
    # thing it must come after", whatever that thing is (a curated plugin or
    # another custom). Master-type predecessors (.esm/.omwgame) are NOT a
    # position signal: they sit in the master block at the very top and half
    # the list depends on them, so anchoring to them would cluster everything
    # at the front (a previous failed attempt). A custom with no non-master
    # predecessor keeps its cfg position (if already in the cfg) or goes to
    # the end, in declared order -- same place the Configurator would append it.
    trace_sort("[sort] step 3: anchoring custom plugins from their graph neighbors "
               "(master edges + applied mlox rule edges, resolved transitively)")
    preds = {}
    succs = {}
    for a, tgts in adj.items():
        for b in tgts:
            if b in subset_set:
                preds.setdefault(b, []).append(a)
            if a in subset_set:
                succs.setdefault(a, []).append(b)
    # adj's edge sets iterate in hash order (randomized per process); sort the
    # neighbor lists so tie-breaks and resolution order -- and therefore the
    # final sort -- are identical run to run.
    for lst in preds.values():
        lst.sort(key=str.lower)
    for lst in succs.values():
        lst.sort(key=str.lower)

    declared_end = {n: nb + j for j, n in enumerate(subset_names)}
    _EPS = 1e-6  # nudge a dependent just past/short of its custom neighbor
    resolved = {}   # custom -> (pos value, anchor name or None, "after"/"before"/"none")
    derives = {}    # custom -> set of customs its position value was derived from
    _resolving = set()  # in-flight nodes (graph is a DAG, but pred/succ lookups interlock)

    def _no_signal_pos(n):
        # no positioning signal: keep cfg pos if already in the cfg, else end
        return float(base_index[n]) if n in base_index else float(declared_end[n])

    def _derives_from(x, n):
        """True if custom x's resolved position was derived (transitively)
        from n's -- using such a value to anchor n would be circular and
        inflate both (e.g. 'A loads before B' must not make B anchor after
        A's fallback-end position)."""
        stack, seen = [x], set()
        while stack:
            c = stack.pop()
            if c == n:
                return True
            if c in seen:
                continue
            seen.add(c)
            stack.extend(derives.get(c, ()))
        return False

    def _final_pos(n):
        """Anchor position for custom n.

        1. "After" signal (preferred): right after the latest-loading NON-master
           thing n must load after -- a curated plugin (rule edge / header
           master) or another custom (resolved transitively). Master-type
           (.esm/.omwgame) predecessors are NOT a signal: they sit in the
           master block at the very top and half the list depends on them, so
           anchoring to them clusters everything at the front.
        2. "Before" signal: otherwise, just before the earliest-loading thing
           n must load BEFORE (mlox [Order] rules mostly constrain customs
           this way). Without this, a before-constrained custom keeps its
           end position, and when the frozen chain reaches its curated
           successor, Kahn's stalls there and dumps every earlier pending
           custom in one big block -- the exact bug being fixed.
        3. Neither: keep cfg position (if already in the cfg) or go to the
           end, in declared order -- where the Configurator would append it.

        Neighbors whose position derives from n (see _derives_from), or that
        are still being resolved, are skipped -- their value comes FROM n's,
        so it can't ground n's. A skip is recorded in `derives` so the round
        loop below can recompute n once the neighbor has settled.
        """
        got = resolved.get(n)
        if got is not None:
            return got[0]
        _resolving.add(n)
        deps = derives.setdefault(n, set())
        try:
            best, best_p = None, None
            for p in preds.get(n, ()):
                if _is_master_file(p):
                    continue              # master block, top of list: no position signal
                if p in subset_set:
                    if p in _resolving:
                        deps.add(p)       # interlock: p's value needs n's -- skip
                        continue
                    bp = _final_pos(p)
                    if _derives_from(p, n):
                        continue          # p's position came from n -- circular
                    bp += _EPS            # right after that custom
                else:
                    bp = base_index[p] + 0.5        # right after that curated plugin
                if best is None or bp > best:
                    best, best_p = bp, p
            if best is not None:
                if best_p in subset_set:
                    deps.add(best_p)
                    deps.update(derives.get(best_p, ()))
                resolved[n] = (best, best_p, "after")
                return best
            low, low_s = None, None
            for s in succs.get(n, ()):
                if s in subset_set:
                    if s in _resolving:
                        deps.add(s)
                        continue
                    bs = _final_pos(s)
                    if _derives_from(s, n):
                        continue
                    bs -= _EPS            # just before that custom
                else:
                    bs = base_index[s] - 0.5 + _EPS  # just before that curated plugin
                if low is None or bs < low:
                    low, low_s = bs, s
            if low is not None:
                if low_s in subset_set:
                    deps.add(low_s)
                    deps.update(derives.get(low_s, ()))
                resolved[n] = (low, low_s, "before")
                return low
            resolved[n] = (_no_signal_pos(n), None, "none")
            return resolved[n][0]
        finally:
            _resolving.discard(n)

    import sys as _sys
    _old_rlimit = _sys.getrecursionlimit()
    _sys.setrecursionlimit(max(_old_rlimit, 10 * len(subset_names) + 1000))
    try:
        # A node resolved while a neighbor was still in flight may hold a
        # degraded value (the interlocked contribution was skipped). Re-run
        # nodes whose value depended on another custom's -- everything else
        # stays memoized -- until the values stop changing (bounded).
        for n in subset_names:
            _final_pos(n)
        for _round in range(len(subset_names) + 1):
            changed = False
            for n in [x for x in subset_names if derives.get(x)]:
                old = resolved.pop(n)
                derives[n] = set()
                _final_pos(n)
                if resolved[n][0] != old[0]:
                    changed = True
            if not changed:
                break
    finally:
        _sys.setrecursionlimit(_old_rlimit)

    n_after = n_before = 0
    for n in subset_names:
        val, anch, how = resolved[n]
        if how == "after":
            pos[n] = val
            n_after += 1
            kind = "custom" if anch in subset_set else "curated"
            trace_sort(f"[sort]  anchor '{n}' -> right after {kind} '{anch}' (pos {val:.6g})")
        elif how == "before":
            pos[n] = val
            n_before += 1
            kind = "custom" if anch in subset_set else "curated"
            trace_sort(f"[sort]  anchor '{n}' -> right before {kind} '{anch}' (pos {val:.6g})")
        else:
            pos.setdefault(n, declared_end[n])
            where = "keeps cfg pos" if n in base_index else "end of load order"
            trace_sort(f"[sort]  '{n}' no non-master neighbor -> {where}")
    trace_sort(f"[sort]  anchored after a dependency: {n_after}, before a successor: {n_before}, "
               f"unanchored (standalone / masters-only): {len(subset_names) - n_after - n_before} "
               f"/ {len(subset_names)} customs")

    if anchor_out is not None:
        # expose WHY each custom sits where it does -- ("after"|"before", anchor
        # name) for real constraints, ("none", None) for positional-only -- so
        # the TOML emitter can annotate its inserts
        for n in subset_names:
            _v, _a, _how = resolved[n]
            anchor_out[n.lower()] = (_how, _a)

    # [NearStart]/[NearEnd] position hints (mlox semantics: pull each matching
    # plugin as close to the start/end as the edges allow -- NOT a chain).
    # Applied to CUSTOMS only (the curated list is frozen), and they override
    # the anchor heuristic above; graph edges still always win.
    for pats, to_start, label in ((nearstart, True, "NearStart"), (nearend, False, "NearEnd")):
        for pat in (pats or ()):
            for n in expand_pattern(pat, node_pool):
                if n not in subset_set:
                    continue
                j = declared_end[n] - nb  # stable tie-break among hinted customs
                pos[n] = (-1.0 + j * _EPS) if to_start else float(2 * nb + len(subset_names) + j)
                if anchor_out is not None:
                    anchor_out[n.lower()] = ("nearstart" if to_start else "nearend", None)
                trace_sort(f"[sort]  [{label}] hint: '{n}' -> {'front' if to_start else 'very end'} "
                           f"(pos {pos[n]:.6g})")

    def rank(n):
        return (0 if _is_master_file(n) else 1, pos.get(n, nb), n.lower())

    trace_sort("[sort] step 4: topological placement (order each plugin is emitted)")
    ready = [(rank(n), n) for n in nodes if indeg[n] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, n = heapq.heappop(ready)
        result.append(n)
        if n in subset_set:       # log only customs to keep the trace readable
            trace_sort(f"[sort]  place #{len(result)}: '{n}'  (CUSTOM, rank={rank(n)})")
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(ready, (rank(m), m))

    if len(result) != len(nodes):
        remaining = nodes - set(result)
        trace_sort(f"[sort] UNPLACED (cycle): {sorted(remaining)}")
        print(f"WARNING: {len(remaining)} plugin(s) could not be placed due to an "
              f"unresolved cycle and were appended at the end: {sorted(remaining)}")
        result.extend(sorted(remaining, key=str.lower))

    trace_sort(f"[sort] === done: {len(result)} plugin(s) placed "
          f"({len(conflicts)} rule edge(s) rejected as cycles) ===")
    return result


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cfg", required=True, type=Path, help="Path to openmw.cfg")
    ap.add_argument("--rules", required=True, nargs="+", type=Path,
                     help="mlox rule file(s) or directories, in increasing priority order "
                          "(pass mlox_base.txt first, mlox_user.txt last)")
    ap.add_argument("--customizations", type=Path,
                     help="momw-customizations.toml to auto-derive the subset from")
    ap.add_argument("--subset", nargs="*", default=[],
                     help="Explicit list of plugin filenames to sort (combined with --customizations if both given)")
    ap.add_argument("--subset-file", type=Path,
                     help="Plain text (one plugin per line) or minimal TOML (subset = [...]) "
                          "file listing plugins to sort -- shorter to maintain than --subset or "
                          "a full momw-customizations.toml block")
    ap.add_argument("--scan-dir", type=Path,
                     help="Scan this mods folder for data folders and plugins and write the result "
                          "to --subset-file (required with this), then sort using it. Folds in the "
                          "old mod_scan.py: each folder containing an asset subfolder or a plugin "
                          "becomes a data= entry (plus its plugins as content=), and matched branches "
                          "aren't descended further.")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing")
    ap.add_argument("--no-backup", action="store_true",
                     help="Skip writing a .bak-<timestamp> copy before overwriting openmw.cfg and/or "
                          "before overwriting an existing --emit-toml target (e.g. when writing back "
                          "to the same file --customizations pointed at).")
    ap.add_argument("--emit-toml", type=Path,
                     help="Write a momw-customizations.toml here (with insert blocks reordered/"
                          "re-anchored per the mlox+anchor results) instead of/alongside patching "
                          "openmw.cfg directly. If --customizations is also given, its other blocks "
                          "(removeContent, replace, append, ...) are preserved and only the sorted "
                          "plugins/paths are regenerated; if not, a brand new single-block TOML is "
                          "generated from --subset/--subset-file alone. This is the durable fix: feed "
                          "the output back into momw-configurator so the correct order survives "
                          "future rebuilds.")
    ap.add_argument("--list-name",
                     help="listName to write into the emitted momw-customizations.toml (the curated "
                          "mod list these customizations apply to, e.g. 'total-overhaul'). Overrides "
                          "the listName from --customizations if both are given. Without this, the "
                          "source TOML's listName is kept, or -- when generating from --subset-file "
                          "alone -- it defaults to the placeholder 'generated'. momw-configurator "
                          "REQUIRES a correct listName, so set this when generating a fresh TOML.")
    ap.add_argument("--plugin-order-yml", type=Path,
                     help="MOMW's plugin-order.yml (source of truth for which plugins belong to which "
                          "curated mod list). With --list-name, curated plugins for that list are "
                          "excluded from the sort (never reordered) so only YOUR custom additions are "
                          "touched, and read-only sanity warnings are emitted: redundant (a custom "
                          "plugin that's already on the list), orphan (in your cfg but neither on the "
                          "list nor in your customizations), needs-cleaning (TES3CMD), and a base-order "
                          "drift check against the list's canonical order. Optional; PyYAML is used if "
                          "installed, else a built-in parser.")
    ap.add_argument("--write-cfg", action="store_true",
                     help="Actually patch openmw.cfg in place. Off by default -- "
                          "prefer --emit-toml for a fix that survives future rebuilds. "
                          "A .bak copy is made first unless --no-backup is also given.")
    ap.add_argument("--sort-data-paths", action="store_true",
                     help="Also position data= (folder path) insertions from the customizations "
                          "TOML: anchored by their after/before field if given, or otherwise "
                          "inferred by scanning the folder for plugin files and anchoring next to "
                          "whichever existing data= path owns the nearest neighboring plugin in the "
                          "mlox-sorted content order. Off by default: mlox has no concept of "
                          "data-path order itself, so this is a separate opt-in feature from the "
                          "mlox-based plugin sort. When off, any data-path insertions found in "
                          "the TOML are left exactly as originally written (in --emit-toml output) "
                          "or ignored entirely (for --write-cfg).")
    ap.add_argument("--no-predicate-warnings", action="store_true",
                     help="Skip evaluating [Requires]/[Conflict]/[Note] rules against the final "
                          "plugin list. On by default; this is read-only and never changes the "
                          "computed sort or what gets written, only what gets printed.")
    ap.add_argument("--check-conflicts", action="store_true",
                     help="After sorting, scan the active plugins for TES3 record-level conflicts "
                          "(where 2+ plugins define/override the same record -- the last in the load "
                          "order wins), like TES3View/tes3cmd. Read-only; needs the plugin files "
                          "reachable via the cfg's data= folders. Can be slow on big lists.")
    ap.add_argument("--conflicts-out", type=Path,
                     help="Write the full conflict list to this CSV (use with --check-conflicts).")
    ap.add_argument("--tes3conv", type=Path,
                     help="Path to a tes3conv executable. With --check-conflicts this switches the "
                          "conflict engine to tes3conv (exact record ids for every type; enables the "
                          "GUI's field-level diffs). Auto-detected from PATH / $MLOX_TES3CONV / next to "
                          "this script if not given; the built-in parser is used if none is found.")
    ap.add_argument("--cell-map", type=Path,
                     help="Write a self-contained HTML 'modmapper'-style cell map here: an exterior-cell "
                          "heatmap (brighter = more mods) plus an interior-cell list, showing which mods "
                          "touch which cells (cells your custom mods touch are highlighted). Read-only; "
                          "open the file in any browser.")
    ap.add_argument("--resource-conflicts", action="store_true",
                     help="Scan the cfg's data= folders for loose-file (VFS) conflicts: the same relative "
                          "path in 2+ folders (later wins), like MO2's Data conflicts. Read-only.")
    ap.add_argument("--lint", action="store_true",
                     help="After sorting, run tes3lint-style checks over the active plugins: evil "
                          "GMSTs, the interior fog-density-0 bug, interior cells with no pathgrid, "
                          "expansion-function use without the expansion mastered, omwaddon/omwscripts "
                          "twin mismatches, and blank custom headers. Read-only; VFS-aware.")
    ap.add_argument("--resources-out", type=Path,
                     help="Write the full resource-conflict list to this CSV (with --resource-conflicts).")
    ap.add_argument("--exclude", nargs="*", default=[],
                     help="Name patterns (glob) to skip in --check-conflicts/--cell-map/"
                          "--resource-conflicts scans, e.g. 's3lightfixes*' '*delta*' '*grass*'.")
    ap.add_argument("--conflicts-subset-only", action="store_true",
                     help="With --check-conflicts, report only conflicts that involve YOUR custom "
                          "mods (skip base-list vs base-list conflicts).")
    ap.add_argument("--trace", nargs="?", const=True, default=None, metavar="LOGFILE",
                     help="Write a debug trace log for troubleshooting (off by default). Use --trace "
                          "for the default log (mlox_subset_sort_trace.log), or --trace PATH to choose "
                          "the file.")
    ap.add_argument("--json-dump-dir", type=Path,
                     help="When using tes3conv for --check-conflicts/--cell-map, write (and KEEP) the "
                          "per-plugin JSON conversions in this folder. tes3conv output is always spooled "
                          "to disk and read one plugin at a time (bounded memory); by default that spool "
                          "is a temp dir removed on exit -- give this to keep it (or to reuse it).")
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
    dirs = [v for v in (extract_data_path_value(l) for l in (data_order or [])) if v]
    seen = {str(d).lower() for d in dirs}
    for src in (data_inserts, raw_toml_data_inserts):
        for d in (src or []):
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
        for d in (src or []):
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

    if (not args.customizations and not args.subset and not args.subset_file
            and not getattr(args, "subset_lines", None)):
        raise SystemExit("Provide --customizations, --subset, --subset-file, or --scan-dir so there's something to sort.")

    _section("READING INPUT")

    subset = list(args.subset)
    data_inserts = []
    raw_toml_data_inserts = []  # captured regardless of --sort-data-paths, for --emit-toml passthrough
    original_content_values = {}
    original_toml_data = {}
    replace_dest_names = set()
    subset_origins = {}  # {plugin_name_lower: "where this came from"} -- for check_predicates' warnings

    if args.subset_file:
        file_plugins, file_data_inserts = extract_subset_from_subset_file(args.subset_file)
        subset.extend(file_plugins)
        raw_toml_data_inserts.extend(file_data_inserts)
        print(f"  {args.subset_file}: {len(file_plugins)} plugin(s), {len(file_data_inserts)} data path(s)")
        if args.sort_data_paths:
            data_inserts.extend(file_data_inserts)
        elif file_data_inserts:
            print(f"  NOTE: data path insertions found but not sorted (pass --sort-data-paths to "
                  f"include them): {', '.join(d['value'] for d in file_data_inserts)}")
        for name in file_plugins:
            original_content_values.setdefault(name, name)
            subset_origins.setdefault(name.lower(), f"subset file ({args.subset_file.name})")

    # In-memory subset lines (e.g. a GUI 'scan into memory' with no file saved).
    # Classified exactly like a plain-text subset file, just never written out.
    if getattr(args, "subset_lines", None):
        mem_plugins, mem_data_inserts = extract_subset_from_lines(args.subset_lines, source="scanned subset")
        subset.extend(mem_plugins)
        raw_toml_data_inserts.extend(mem_data_inserts)
        print(f"  in-memory scan: {len(mem_plugins)} plugin(s), {len(mem_data_inserts)} data path(s)")
        if args.sort_data_paths:
            data_inserts.extend(mem_data_inserts)
        elif mem_data_inserts:
            print(f"  NOTE: data path insertions found but not sorted (pass --sort-data-paths to "
                  f"include them): {len(mem_data_inserts)} path(s)")
        for name in mem_plugins:
            original_content_values.setdefault(name, name)
            subset_origins.setdefault(name.lower(), "scanned subset (in memory)")

    if args.customizations:
        toml_subset, toml_data_inserts, replace_dest_names, subset_listnames = \
            extract_subset_from_toml(args.customizations)
        subset.extend(toml_subset)
        raw_toml_data_inserts.extend(toml_data_inserts)
        print(f"  {args.customizations}: {len(toml_subset)} content plugin(s), "
              f"{len(toml_data_inserts)} data path(s)")
        if args.sort_data_paths:
            data_inserts.extend(toml_data_inserts)
        elif toml_data_inserts:
            print(f"  NOTE: data path insertions found but not sorted (pass --sort-data-paths to "
                  f"include them): {', '.join(d['value'] for d in toml_data_inserts)}")
        for name in toml_subset:
            original_content_values[name] = name
            listname = subset_listnames.get(name)
            subset_origins[name.lower()] = f"customizations.toml -> '{listname}'" if listname else "customizations.toml"
        if args.emit_toml:
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore
            original_toml_data = tomllib.loads(args.customizations.read_text(encoding="utf-8"))

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
    custom_anchors = {}   # {custom_lower: (how, anchor_name)} from build_and_sort

    # --- plugin-order.yml: curated-vs-custom split (opt-in) -----------------
    # With a --list-name, curated plugins (those the yml says belong to that
    # list) are the list's responsibility -- we drop them from the subset so
    # this tool never reorders them, leaving only YOUR true custom additions to
    # sort. Everything here is guarded; a missing/garbled yml just skips the
    # feature rather than failing the run.
    yml_entries, curated_set, curated_order, yml_warnings = [], set(), [], []
    declared_lower = {s.lower() for s in subset}  # everything you declared, pre-split (for orphan check)
    plugin_order_yml = getattr(args, "plugin_order_yml", None)
    list_name = getattr(args, "list_name", None)
    if plugin_order_yml:
        _section("PLUGIN-ORDER.YML (MOMW source of truth)")
        try:
            yml_entries = parse_plugin_order_yml(Path(plugin_order_yml))
            print(f"  Loaded {len(yml_entries)} plugin entries from {Path(plugin_order_yml).name}")
        except Exception as e:
            print(f"  WARNING: could not read plugin-order.yml ({e}) -- skipping yml checks.")
            yml_entries = []
        if yml_entries and not list_name:
            print("  NOTE: no list name given -- can't separate curated-list plugins from your "
                  "custom ones, so curated/redundant/orphan/order checks are skipped "
                  "(needs-cleaning notes still work).")
        if yml_entries and list_name:
            curated_set, curated_order = curated_for_list(yml_entries, list_name)
            print(f"  '{list_name}': {len(curated_set)} curated plugin(s) on this list")
            if not curated_set:
                print(f"  WARNING: no plugins found for list '{list_name}' in the yml -- check the "
                      f"list name spelling. Curated-set checks skipped.")
            redundant = [s for s in subset if s.lower() in curated_set]
            if redundant:
                subset = [s for s in subset if s.lower() not in curated_set]
                for r in redundant:
                    yml_warnings.append(
                        f"[REDUNDANT] '{r}' is already part of the '{list_name}' list -- not sorting "
                        f"it (leaving it to the curated list / configurator).")
                print(f"  Excluded {len(redundant)} curated plugin(s) from the sort: {', '.join(redundant)}")

    if subset:
        _section(f"SORTING {len(subset)} PLUGIN(S)")
        print(f"  {', '.join(subset)}")

        base_lower = {n.lower() for n in base_order_names}
        already_present = [s for s in subset if s.lower() in base_lower]
        new_plugins = [s for s in subset if s.lower() not in base_lower]
        if already_present:
            print(f"\n  Already in cfg, will be repositioned within it: {', '.join(already_present)}")
        if new_plugins:
            print(f"  Not in cfg yet, will be inserted: {', '.join(new_plugins)}")

        _subsection("loading mlox rules")
        rule_blocks, nearstart_pats, nearend_pats = load_rule_blocks(args.rules)

        sort_trace_begin()   # fresh, dedicated sort log for this sort's play-by-play

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
            trace_sort(f"[sort] header-master read: searching {len(sort_dirs)} data folder(s) for "
                  f"{len(subset)} custom plugin(s)")
            _not_found = 0
            for name in subset:
                p = sort_index.find(name)
                if p is None:
                    _not_found += 1
                    trace_sort(f"[sort]  '{name}': file NOT found in data folders -- masters unknown")
                    continue
                ms = read_plugin_masters(p)
                trace_sort(f"[sort]  '{name}': {len(ms)} master(s) {ms}  ({p})")
                if ms:
                    masters[name.lower()] = ms
            if _not_found:
                trace_sort(f"[sort] header-master read: {_not_found} custom file(s) not found in any "
                      f"data folder")
            if masters:
                print(f"  Read header masters for {len(masters)} of {len(subset)} custom plugin(s).")
            else:
                print("  (No header masters read -- mod files not reachable from the cfg's data= "
                      "folders, so ordering uses mlox rules + ESM-first only.)")
        except Exception:
            masters = {}

        final_order = build_and_sort(base_order_names, subset, rule_blocks, masters=masters,
                                     nearstart=nearstart_pats, nearend=nearend_pats,
                                     anchor_out=custom_anchors)

        # Drift check: the CURATED (non-custom) plugins must keep their exact cfg
        # order. Customs that were already in the cfg are expected to move, so
        # they're excluded from this check.
        subset_lower_chk = {s.lower() for s in subset}
        frozen_before = [n for n in base_order_names if n.lower() not in subset_lower_chk]
        base_set = set(base_order_names)
        frozen_after = [n for n in final_order if n in base_set and n.lower() not in subset_lower_chk]
        if frozen_before != frozen_after:
            print("\n  INTERNAL WARNING: curated (frozen) order drifted -- this shouldn't happen. "
                  "Please double check the output before using it.")

        _subsection("final content= order")
        subset_lower = {s.lower() for s in subset}
        for n in final_order:
            tag = "  <-- inserted/moved" if n.lower() in subset_lower else ""
            print(f"  content={n}{tag}")

        if not args.no_predicate_warnings:
            rules_raw_text = load_rules_raw_text(args.rules)
            pred_data_dirs = [v for v in (extract_data_path_value(l) for l in data_order) if v]
            predicate_warnings = check_predicates(rules_raw_text, final_order, subset_origins,
                                                  data_dirs=pred_data_dirs)
            if predicate_warnings:
                _section(f"{len(predicate_warnings)} MLOX RULE WARNING(S) -- read-only, not enforced")
                for w in predicate_warnings:
                    print(f"\n{w}")
            else:
                print("\n  No [Conflict]/[Requires]/[Note] warnings triggered.")

    # --- plugin-order.yml: post-sort sanity warnings (read-only) ------------
    if yml_entries:
        active = final_order if final_order else base_order_names
        nc_set = needs_cleaning_set(yml_entries)
        for n in active:
            if n.lower() in nc_set:
                yml_warnings.append(f"[NEEDS CLEANING] '{n}' should be cleaned with TES3CMD "
                                    f"(flagged in plugin-order.yml).")
        if list_name and curated_set:
            for n in base_order_names:
                if n.lower() not in curated_set and n.lower() not in declared_lower:
                    yml_warnings.append(
                        f"[ORPHAN] '{n}' is in your cfg but not on the '{list_name}' list and not in "
                        f"your customizations -- an unmanaged custom plugin (fine if intentional).")
            yml_warnings.extend(base_order_matches_yml(base_order_names, curated_order))
        if yml_warnings:
            _section(f"{len(yml_warnings)} PLUGIN-ORDER.YML WARNING(S) -- read-only, not enforced")
            for w in yml_warnings:
                print(f"\n{w}")
        else:
            print("\n  No plugin-order.yml warnings.")

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
            _active_for_masters, _mindex, subset_origins)
        master_problem_plugins = sorted(_problem_names, key=str.lower)
        if _checked == 0:
            _section("MASTER CHECK -- skipped")
            print("  (plugin files not reachable from the data folders; can't read headers)")
        else:
            master_warnings = _missing + _order_problems
            n_issues = len(_missing) + len(_order_problems)
            _section(f"MASTER CHECK -- {_checked} plugin(s) read, "
                     f"{n_issues} problem(s), {len(_size_notes)} size note(s)")
            for w in _missing:
                print(f"\n{w}")
            for w in _order_problems:
                print(f"\n{w}")
            if _size_notes:
                _subsection(f"{len(_size_notes)} master size mismatch note(s) (usually benign)")
                for w in _size_notes:
                    print(f"{w}")
            if not n_issues and not _size_notes:
                print("  All masters present and correctly ordered.")
    except Exception as e:
        print(f"  WARNING: master check failed: {e}", file=sys.stderr)

    # --- merged-artifact staleness watchdog (read-only) ---------------------
    # delta-plugin's merged leveled lists (and similar generated artifacts)
    # only reflect the plugins that existed when the Configurator last ran.
    # If newer plugins exist, the merge is stale and quietly wrong.
    try:
        _active_ws = final_order if final_order else base_order_names
        _widx = PluginFileIndex(all_scan_dirs(data_order, raw_toml_data_inserts, data_inserts))
        for _artifact in ("delta-merged.omwaddon", "deleted_groundcover.omwaddon",
                          "S3LightFixes.esp"):
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
                print(f"\n[STALE] '{_artifact}' is older than {len(_newer)} active plugin(s) "
                      f"(e.g. {', '.join(_newer[:3])}{', ...' if len(_newer) > 3 else ''}) -- "
                      f"re-run momw-configurator so it gets rebuilt against the current load order.")
    except Exception:
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
        conv = find_tes3conv(explicit=getattr(args, "tes3conv", None),
                             extra_dirs=[str(args.cfg.parent) if args.cfg else None])
        _dump = getattr(args, "json_dump_dir", None)
        csession = (Tes3ConvSession(conv, dump_dir=str(_dump) if _dump else None, keep=bool(_dump))
                    if conv else None)          # disk-backed, shared across both scans
        if csession and _dump:
            print(f"  Keeping tes3conv JSON dump in: {csession.dumped_dir()}")

        if want_conflicts:
            _section("TES3 RECORD CONFLICTS (read-only)")
            print(f"  Engine: {'tes3conv (' + conv + ')' if conv else 'built-in parser (record-level)'}")
            conflicts, cstats = detect_conflicts(active, cindex, subset_names=subset, session=csession)
            print(format_conflict_report(conflicts, cstats,
                                         subset_only=getattr(args, "conflicts_subset_only", False),
                                         limit=200))
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
                print(f"  Scanned {cov['scanned']} plugin(s): {len(cov['exterior'])} exterior + "
                      f"{len(cov['interior'])} interior cell(s) touched.")
                print(f"  Wrote cell map: {cell_map_out}  (open it in a browser)")
            except OSError as e:
                print(f"  WARNING: could not write cell map: {e}", file=sys.stderr)

        if csession is not None:
            csession.cleanup()   # drop the temp JSON spool (no-op if --json-dump-dir kept it)

    if getattr(args, "lint", False):
        _section("LINT (tes3lint-style checks, read-only)")
        _lactive = final_order if final_order else base_order_names
        _lactive, _lexcl = filter_plugins(_lactive, getattr(args, "exclude", None))
        if _lexcl:
            print(f"  (excluded {len(_lexcl)} plugin(s) by --exclude)")
        _lw, _ls = lint_plugins(_lactive, PluginFileIndex(conf_dirs),
                                subset_names=subset, origins=subset_origins)
        print(f"  Scanned {_ls.get('scanned', 0)} plugin(s); "
              f"{_ls.get('interior_cells', 0)} interior cell(s), "
              f"{_ls.get('pathgrids', 0)} interior pathgrid(s).")
        for _w in _lw:
            print(f"\n{_w}")
        if not _lw:
            print("\n  No lint findings.")

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
        print("  Pass --sort-data-paths to enable:")
        for d in data_inserts:
            print(f"  {d['value']}")

    if data_inserts and args.sort_data_paths:
        _section(f"SORTING {len(data_inserts)} DATA PATH(S)")
        infer_data_path_anchors(data_inserts, data_order, final_order, args.cfg)
        for d in data_inserts:
            anchor = d.get("after") and f"after '{d['after']}'" or d.get("before") and f"before '{d['before']}'" or "no anchor"
            print(f"  {d['value']}  ({anchor})")
        data_result = insert_data_paths(data_order, data_inserts)
        _subsection("final data= order")
        for line, is_new, _ in data_result:
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


def write_plan(args, plan: dict, final_order: list = None, data_order: list = None,
               disabled_plugins=None, disabled_data=None) -> dict:
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
    base_data_norms = {normalize_data_path(extract_data_path_value(l))
                       for l in (plan.get("data_order") or [])}
    base_data_norms.discard("")
    remove_data = []
    for line in (disabled_data or []):
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

    if data_result is not None and [l for l, _, _ in data_result] != [l for l, _, _ in (plan["data_result"] or [])]:
        _subsection("data= order being exported (manually adjusted)")
        for line, _, _ in data_result:
            print(f"  {line}")

    _section("WRITING OUTPUT")
    wrote_cfg = False
    if args.write_cfg:
        write_cfg(args.cfg, plan["lines"], segments, args.dry_run, args.no_backup)
        wrote_cfg = not args.dry_run
    else:
        print("  openmw.cfg left untouched (pass --write-cfg to patch it directly)")

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
            _user_norms = [normalize_data_path(d["value"]) for d in (plan["data_inserts"] or [])
                           if d.get("value")]
            _ok, _rep = preview_configurator_result(
                plan["lines"], toml_text, list(final_order or []),
                subset, user_data_norms=_user_norms,
                list_name=getattr(args, "list_name", None))
            for _l in _rep:
                print(_l)
        except Exception as _e:
            print(f"  WARNING: configurator preview failed: {_e}")
        if args.dry_run:
            print(f"\n  DRY RUN: would write {args.emit_toml}\n{toml_text}")
        else:
            backup_file(args.emit_toml, args.no_backup)
            args.emit_toml.write_text(toml_text, encoding="utf-8")
            print(f"  Wrote corrected customizations: {args.emit_toml}")
            wrote_toml = True

    if not args.write_cfg and not args.emit_toml:
        print("\n  NOTE: nothing was written -- this was a preview. "
              "Pass --write-cfg and/or --emit-toml to save the result.")

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
