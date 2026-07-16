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


def set_trace_file(path):
    global _TRACE_PATH
    _TRACE_PATH = str(path) if path else None
    if _TRACE_PATH:
        trace("=== trace start ===")


def trace(msg):
    if not _TRACE_PATH:
        return
    try:
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
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
_re_size_fun = re.compile(r'^\[\s*SIZE\s*(!?)(\d+)\s+(\S.*?\.es[mp]\b)\s*\]$', re.IGNORECASE)
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
                f'data-t="{_html_escape(tip)}" onclick="jump(\'{anchor(gx, gy)}\')"></rect>')
        svg = (f'<svg width="{w*STEP}" height="{h*STEP}" viewBox="0 0 {w*STEP} {h*STEP}" '
               f'xmlns="http://www.w3.org/2000/svg">' + "".join(rects) + "</svg>")
        grid = f'<div class="mapwrap">{svg}</div>'

    ext_rows = []
    for (gx, gy), mods in sorted(ext_ok.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        custom = any(m.lower() in subl for m in mods)
        cls = ' class="cust"' if custom else ""
        ext_rows.append(f'<tr id="{anchor(gx,gy)}"{cls}><td>({gx}, {gy})</td><td>{len(mods)}</td>'
                        f'<td>{_html_escape(", ".join(mods))}</td></tr>')
    int_rows = []
    for name, mods in sorted(inte.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        custom = any(m.lower() in subl for m in mods)
        cls = ' class="cust"' if custom else ""
        int_rows.append(f'<tr{cls}><td>{_html_escape(name)}</td><td>{len(mods)}</td>'
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
 function ff(id){{var q=event.target.value.toLowerCase();
  document.querySelectorAll('#'+id+' tbody tr').forEach(function(r){{
   r.style.display=r.innerText.toLowerCase().indexOf(q)>-1?'':'none';}});}}
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
TOP_RE = re.compile(r"\[\s*(" + "|".join(TOP_KEYWORDS) + r")\b[^\]]*\]", re.IGNORECASE)


def strip_comment(line: str) -> str:
    # mlox comments run from ';' to end of line (outside quotes, which is
    # good enough here since Order/NearStart/NearEnd blocks are just filenames)
    idx = line.find(";")
    return line[:idx] if idx != -1 else line


def parse_mlox_file(path: Path):
    """Returns list of blocks: (keyword, [plugin_pattern, ...])"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = [strip_comment(l) for l in raw.splitlines()]
    text = "\n".join(lines)

    matches = list(TOP_RE.finditer(text))
    blocks = []
    for idx, m in enumerate(matches):
        keyword = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end]
        if keyword.lower() in ("order", "nearstart", "nearend"):
            # plain list of plugin filenames, one (or more) per line, no brackets expected
            names = [tok for tok in re.split(r"\s+", body.strip()) if tok and "[" not in tok and "]" not in tok]
            if names:
                blocks.append((keyword.lower(), names))
    return blocks


def load_rule_blocks(rule_paths):
    """
    Returns list of (names, priority) where names is the ORDERED list of plugin
    patterns in one [Order]/[NearStart]/[NearEnd] block, in the order the rule
    lists them, and priority = index of the file on the command line (later
    files win ties/conflicts).

    Keeping the whole ordered block (rather than pre-zipping it into a<b pairs)
    lets build_and_sort bridge over plugins you don't have: in [Order] A,B,C
    where B isn't installed, chaining A->C directly preserves the A-before-C
    constraint instead of losing it -- matching how the real mlox engine keeps
    a not-installed plugin as a phantom bridge node (see pluggraph). Pre-zipping
    would have produced A->B and B->C, both of which vanish when B expands to
    nothing.
    """
    blocks_out = []
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
                blocks_out.append((names, priority))
            print(f"Loaded {sum(len(n) for _, n in blocks)} plugin refs from {f.name}")
    return blocks_out


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
    func = (r'\[\s*VER\b[^\]]*\]|'
            r'\[\s*SIZE\b[^\]]*\]|'
            r'\[\s*DESC\b[^\]]*\]|'
            r'\[\s*MWSE-LUA\b[^\]]*\]')
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
                chunks.append(f.read_text(encoding="utf-8", errors="replace"))
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

        # Split each line into "logic" (brackets / plugin filenames) vs.
        # free-text message lines, same heuristic mlox itself uses.
        message_lines = []
        logic_text = ""
        for line in body.splitlines():
            line = line.split(";")[0].strip()  # ';' comment stripping
            if not line:
                continue
            if any(c in line for c in "[]") or any(ext in line.lower() for ext in PLUGIN_EXTS):
                logic_text += " " + line
            else:
                message_lines.append(line)

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

    subset = sorted(set(subset), key=str.lower)
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


def generate_customizations_toml(original_data, final_content_order, subset_set,
                                   original_content_values, data_result_tuples=None,
                                   raw_data_inserts=None, replace_dest_names=None,
                                   user_data_values=None, list_name=None,
                                   remove_content=None, remove_data=None):
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
                items = ", ".join(toml_value(x) for x in merged)
                out.append(f"{key} = [ {items} ]")
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
                elif d.get("before"):
                    out.append(f"before = {toml_value(d['before'])}")
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
            out.append("[[Customizations.insert]]")
            out.append(f"insert = {toml_value(value)}")
            if i == 0:
                # sorted to the very start of the load order -- there's no
                # predecessor to anchor "after", so anchor "before" whatever
                # ended up immediately following it instead
                if len(final_content_order) > 1:
                    out.append(f"before = {toml_value(final_content_order[1])}")
                else:
                    out.append("# WARNING: this is the only content= plugin -- no anchor to write")
            else:
                anchor = final_content_order[i - 1]
                out.append(f"after = {toml_value(anchor)}")
            out.append("")

        for rep in block.get("replace", []):
            out.append("[[Customizations.replace]]")
            if "source" in rep:
                out.append(f"source = {toml_value(rep['source'])}")
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


def build_and_sort(base_order_names, subset_names, rule_blocks):
    # Guard against the same plugin becoming two different graph nodes just
    # because of a casing difference (OpenMW's VFS is case-insensitive, so
    # 'NewMod.esp' and 'newmod.esp' are the same file) -- canonicalize any
    # subset entry that matches an existing base entry onto that entry's
    # exact spelling, and drop the resulting case-duplicates. Without this,
    # a subset plugin already in the cfg under different casing would get
    # inserted a second time instead of being repositioned.
    base_lower_map = {n.lower(): n for n in base_order_names}
    canonical_subset_names = []
    seen_lower = set()
    for n in subset_names:
        canon = base_lower_map.get(n.lower(), n)
        if canon.lower() not in seen_lower:
            seen_lower.add(canon.lower())
            canonical_subset_names.append(canon)
    subset_names = canonical_subset_names

    base_index = {name: i for i, name in enumerate(base_order_names)}
    nodes = set(base_order_names) | set(subset_names)
    subset_set = set(subset_names)

    adj = {n: set() for n in nodes}
    indeg = {n: 0 for n in nodes}
    conflicts = []  # mlox rules we couldn't apply without reordering the frozen cfg

    def add_edge(a, b, label):
        if a == b or b in adj.get(a, ()):
            return True
        if would_create_cycle(adj, a, b, nodes):
            conflicts.append((a, b))
            return False
        adj[a].add(b)
        indeg[b] += 1
        return True

    # 1) frozen chain from the existing cfg order
    for a, b in zip(base_order_names, base_order_names[1:]):
        add_edge(a, b, "existing cfg order")

    # 2) mlox ordering edges, but only where they touch a subset plugin (the
    #    frozen base is already ordered by step 1). Within each block we chain
    #    consecutive INSTALLED matches, skipping over patterns that match
    #    nothing you have -- so [Order] A, B, C with B not installed still
    #    yields A -> C directly, preserving the constraint instead of losing it
    #    when B drops out. This is the transitive-bridge behaviour the real mlox
    #    engine gets by keeping a not-installed plugin as a phantom node.
    blocks_sorted = sorted(rule_blocks, key=lambda b: b[1])  # lower priority first, later files last
    for names, priority in blocks_sorted:
        # expand each token to its installed matches; a token that matches
        # nothing is dropped (it becomes an order bridge, not a broken link)
        survivors = [ms for ms in (expand_pattern(tok, nodes) for tok in names) if ms]
        for a_matches, b_matches in zip(survivors, survivors[1:]):
            for a in a_matches:
                for b in b_matches:
                    if a == b:
                        continue
                    if a in subset_set or b in subset_set:
                        add_edge(a, b, f"mlox rule (priority {priority})")

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

    # 3) stable Kahn's topological sort: among ready nodes, prefer original
    #    cfg position; unconstrained subset nodes (no cfg position) sort last
    import heapq
    ready = [(base_index.get(n, float("inf")), n) for n in nodes if indeg[n] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, n = heapq.heappop(ready)
        result.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(ready, (base_index.get(m, float("inf")), m))

    if len(result) != len(nodes):
        remaining = nodes - set(result)
        print(f"WARNING: {len(remaining)} plugin(s) could not be placed due to an "
              f"unresolved cycle and were appended at the end: {sorted(remaining)}")
        result.extend(sorted(remaining, key=str.lower))

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

    subset = sorted(set(subset), key=str.lower)
    if not subset and not data_inserts and not raw_toml_data_inserts:
        raise SystemExit("No subset plugins or data paths found -- nothing to do.")

    lines, content_positions, content_order, data_positions, data_order = read_cfg(args.cfg)
    base_order_names = [name for name, _ in content_order]

    final_order = None
    data_result = None
    predicate_warnings = []

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
        rule_blocks = load_rule_blocks(args.rules)

        final_order = build_and_sort(base_order_names, subset, rule_blocks)

        base_only_after = [n for n in final_order if n in set(base_order_names)]
        if base_order_names != base_only_after:
            print("\n  INTERNAL WARNING: base cfg order drifted -- this shouldn't happen. "
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

    # --- TES3 record-level conflict scan (opt-in, read-only) ----------------
    conflicts = []
    want_conflicts = getattr(args, "check_conflicts", False)
    cell_map_out = getattr(args, "cell_map", None)
    want_resources = getattr(args, "resource_conflicts", False)
    conf_dirs = [v for v in (extract_data_path_value(l) for l in data_order) if v]
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

    if want_resources:
        _section("DATA-PATH RESOURCE (VFS) CONFLICTS (read-only)")
        subset_dirs = [d["value"] for d in (data_inserts or raw_toml_data_inserts or [])]
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
        "original_content_values": original_content_values,
        "original_toml_data": original_toml_data,
        "replace_dest_names": replace_dest_names,
        "raw_toml_data_inserts": raw_toml_data_inserts,
        "data_inserts": data_inserts,
        "base_order_names": base_order_names,
        "conflicts": conflicts,
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
        )
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
