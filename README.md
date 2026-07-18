# MLOX Subset Sort

Sort **only your custom mods** into an existing OpenMW `openmw.cfg` using mlox
rules, **without ever reordering the curated [Modding-OpenMW.com](https://modding-openmw.com/)
(MOMW) mod list** you built with `umo` + MOMW Configurator.

The existing `content=` order in `openmw.cfg` is treated as **frozen**. This
tool only works out where *your* additions (the mods under your `custom` folder)
belong relative to that frozen list and to each other, then writes the result
back as a corrected `momw-customizations.toml` — the durable fix that survives
future Configurator rebuilds.

> This is the opposite of running a whole-list sorter like PLOX. MOMW explicitly
> warns against sorting their curated lists; this tool is built specifically so
> you don't have to.

**New here? See [QUICKSTART.md](QUICKSTART.md) for a 5-minute walkthrough.**

---

## Contents

- `mlox_subset_sort.py` — the engine + command-line tool.
- `mlox_subset_sort_gui.py` — a drag-and-drop GUI front-end (imports the engine
  directly; it reimplements no logic).
- `mlox_base.txt`, `mlox_user.txt` — mlox rule databases (download/update with
  mlox or `plox`).
- `plugin-order.yml` — MOMW's source of truth for which plugins belong to which
  curated list (optional but recommended). From the
  [modding-openmw.com repo](https://gitlab.com/modding-openmw/modding-openmw.com/-/blob/master/momw/momw/data_seeds/data/plugin-order.yml).
- `CREDITS.md` — acknowledgements for the projects this tool ports, references,
  and depends on (mlox, plox, tes3conv, modmapper, OpenMW, MOMW, and more).
- `CHANGELOG.md` — what changed between releases (current: **2.3**).

---

## Requirements & setup (Windows, Linux, macOS)

Pure Python + tkinter, so it runs on all three platforms.

- **Python 3.8+** (3.11+ gets `tomllib` for free; on older versions install
  `tomli`).
- **tkinter** — bundled with the python.org installers on Windows and macOS. On
  Linux, install it from your package manager, e.g. Debian/Ubuntu:
  `sudo apt install python3-tk`.
- **Optional extras** (the tool degrades gracefully without them):
  - `pip install tkinterdnd2` — drag files into the GUI from your file manager.
    Without it, use the **Browse...** buttons (dragging rows to reorder still
    works regardless).
  - `pip install PyYAML` — faster/robust `plugin-order.yml` parsing. Without it,
    a built-in parser is used automatically.
  - `pip install tomli` — only on Python < 3.11, for reading TOML.
  - `pip install pywebview` — shows the cell map in an in-app window (uses the OS
    webview, so the SVG heatmap and tabs render exactly like a browser).
    `tkinterweb` also works as a lighter fallback; without either, the map opens
    in your default browser.

Install optional deps on an externally-managed Python with
`pip install ... --break-system-packages` if needed.

### Packaging into a standalone `.exe` (PyInstaller / auto-py-to-exe)

The tool runs frozen. It never persists data next to `__file__` (which, when
frozen, is a temp extraction dir that's wiped on exit), so your settings and
outputs survive the build:

- `mlox_subset_sort_settings.json`, `mlox_subset_sort_trace.log`, `cell_map.html`,
  and the `tes3conv_json/` spool are written **next to the `.exe`**. If that
  folder isn't writable (e.g. installed under `Program Files`), they fall back to
  a per-user data dir (`%APPDATA%\MloxSubsetSort` on Windows,
  `~/Library/Application Support/MloxSubsetSort` on macOS,
  `~/.config/MloxSubsetSort` on Linux).
- The in-app cell-map viewer re-invokes the same executable with `--show-map`, so
  pywebview works from the frozen build too (no bundled Python interpreter
  needed). **But the library must actually be inside the exe** — if it isn't, the
  map falls back to the browser.

**Bundling the embedded (pywebview) cell-map viewer.** In your build environment,
`pip install pywebview`, then tell PyInstaller / auto-py-to-exe to collect it and
its Windows backend. In auto-py-to-exe, add under *Advanced → hidden-import* and
*--collect-all*, or on the PyInstaller command line:

```
pyinstaller --noconsole --collect-all webview --collect-all clr_loader \
    --hidden-import clr --hidden-import webview.platforms.edgechromium \
    mlox_subset_sort_gui.py
```

pywebview on Windows uses the Edge **WebView2** runtime (present on Windows 11 and
most updated Windows 10; otherwise install the free "Evergreen" runtime from
Microsoft). To confirm what the build actually sees, run the exe with `--trace`
and open the log: the `viewers: ... pywebview=True/False` line and the
`cell map: viewer = ...` line tell you which path it took.

If you'd rather avoid the WebView2 dependency, bundle **tkinterweb** instead
(`pip install tkinterweb`; `--collect-all tkinterweb`). It renders the SVG map in
a real in-app window (the tab buttons need a full browser, so use *Open in
browser* for those). Without either library, the map opens in your browser.

Run the GUI:

```
python mlox_subset_sort_gui.py
```

---

## GUI walkthrough

The left half is the drag-to-reorder panels; the right half is inputs, options,
actions, and a colorized log. Drag the **hamburger grips** on the dividers to
resize any panel.

**Inputs (top right):**

- **openmw.cfg** *(required)* — read the current `content=`/`data=` order from
  here, and (optionally) patch it.
- **customizations.toml** — your `momw-customizations.toml`, to pull the
  plugin/data-path subset from automatically.
- **subset file** — a plain-text list (one plugin filename or data folder path
  per line) or a minimal TOML. A `#` starts a comment only at the start of a line
  or after a space, so a `#` inside a name (e.g. `FMI_#NotAllDunmer.ESP`) is kept.
  Combine with an emit target and it's enough on its own to generate a brand-new
  customizations TOML.
  - **Scan...** — generate this subset by walking a mods folder (see below).
- **emit corrected TOML to** — where to write the sorted/re-anchored
  `momw-customizations.toml`. (Or tick *Write directly back...* to overwrite the
  source in place, with a timestamped `.bak`.)
- **list name** — the MOMW list these customizations apply to (e.g.
  `total-overhaul`). Required by the Configurator; also drives the yml features.
- **plugin-order.yml** — enables the curated-vs-custom features below.
- **Rule files** — mlox `.txt` rule databases in priority order (base first,
  your user rules last).

**Options:**

| Option | Effect |
| --- | --- |
| Dry run | Preview only — Export writes nothing (on by default). |
| Write openmw.cfg directly | Patch `content=`/`data=` in place on Export (`.bak` made first). |
| Sort data= paths too | Also position `data=` folder inserts (mlox has no data-path order, so this is opt-in). |
| Skip .bak backup | Don't back up before overwriting. Not recommended. |
| Skip mlox warnings | Skip evaluating `[Conflict]/[Requires]/[Note]`. |
| Create subset text document | Controls **Scan...**: on = write a `.txt`; off = keep the scan in memory for this session only. |

**Actions** sit in two compact rows below the options. Top row: the core loop
(**Sort**, **Export**) plus the record/cell analysis buttons (**Check
Conflicts**, **Cell Map**, **Resource Conflicts**), with the status text
trailing. Bottom row: the plugin tools (**Lint**, **tes3cmd**, **Save Check**,
**Backups**).

1. **Sort** — runs mlox and fills the order panels. Never writes anything; always
   safe. Red rows are your custom additions; **purple** rows have a missing or
   mis-ordered master (see the MASTER CHECK section in the log).
2. **Export** — writes `openmw.cfg` and/or the corrected TOML using whatever
   order the panels currently show. (Uncheck *Dry run* to actually write.) Every
   TOML export runs a **Configurator preview**: the emitted customizations are
   applied to a simulated fresh cfg using a faithful re-implementation of
   momw-configurator's own apply logic, and the result is verified against the
   sorted order — a green `VERIFIED` line means what the Configurator will do is
   exactly what you sorted. Export also warns if `openmw.cfg` changed on disk
   since the Sort (e.g. the Configurator re-ran underneath you).

The **analysis** buttons (Check Conflicts, Cell Map, Resource Conflicts, Lint)
run against the sorted, enabled plugins and never modify anything; the **tools**
row covers tes3cmd (clean / resync / header, VFS-safe), Save Check (verify an
`.omwsave`'s dependencies are still present), and Backups (restore or delete
backups left by this tool, tes3cmd, and the Configurator).

Every list in the app supports **type-to-jump**: click it and start typing a
name — prefix match first, substring fallback, tap one letter repeatedly to
cycle its matches, Backspace edits, Esc clears. The two sort panels show what
you've typed in their title bar.

### Reordering rows

Drag a row up/down with the mouse, or select it and use **Move Up** / **Move
Down**. Both work with a multi-selection (Ctrl/Cmd-click, Shift-click) — the Move
buttons handle any selection, and dragging moves a contiguous block. This only
overrides where *your* additions land; it's applied at Export time.

### Reading the log

The log colour-codes each line so you can scan it quickly:

- **green** — a plugin/path this sort inserted or moved (`<-- inserted`).
- **orange** — a heads-up: an mlox `[Conflict]/[Requires]/[Note]` warning, a
  `plugin-order.yml` note, or a rule *not applied* because your curated cfg order
  already ordered it the other way. Those "not applied" lines are a handy
  diagnostic: if a mod you just added misbehaves, they tell you exactly where
  mlox disagreed with your cfg — you decide whether to nudge it in the panel.
- **blue** — a section header; **red** — an error worth checking.
- **plain** — a frozen base row left untouched.

### Opting rows out (disable / enable)

Not everything you scanned needs to load. In either order panel, select one or
more rows (Ctrl/Cmd-click and Shift-click for multi-select) and click
**Disable / Enable** — or double-click a single row. Disabled rows are dimmed and
marked with `✗`.

- Disabled rows are left out of **Export**: a brand-new custom item is simply not
  inserted; an item that already exists in your `openmw.cfg` is emitted as a
  `removeContent` (plugin) or `removeData` (data path) in the corrected TOML, so
  the Configurator durably removes it on the next rebuild.
- Your opt-out choices are remembered across a re-**Sort** (and across **Reset**),
  so you can disable a few, re-sort, and build with the omissions.

---

## Scanning a mods folder

**Scan...** (next to the subset-file field) folds in the old `mod_scan.py`. It
walks the folder you pick and, for every directory that directly contains a
recognized asset subfolder (`meshes`, `textures`, `scripts`, `sound`, `icons`,
`music`, `fonts`, `bookart`, `splash`, `video`) **or** a plugin
(`.esp/.esm/.omwaddon/.omwscripts`), records that folder as a `data=` path plus
any plugins in it as `content=` entries — then stops descending that branch.

- **Create subset text document** ON → you choose where to save the `.txt`, and
  it's loaded into the subset-file field for reuse.
- OFF → the result is held in memory and fed straight to the sort; nothing is
  written to disk.

---

## plugin-order.yml integration

Point the **plugin-order.yml** field at MOMW's file and set **list name**. The
tool then knows exactly which plugins belong to your curated list versus which
are genuinely your additions, and:

- **Curated-vs-custom split** — plugins on the list are excluded from the sort
  (never reordered — that's the list's job) so only your true custom additions
  are touched and highlighted.
- **Read-only warnings**:
  - `[REDUNDANT]` — a "custom" plugin that's actually already on the list.
  - `[ORPHAN]` — a plugin in your cfg that's neither on the list nor in your
    customizations (e.g. a manually-added or TES3CMD-cleaned file).
  - `[NEEDS CLEANING]` — flagged for TES3CMD in the yml.
  - `[LIST ORDER]` — your base order has drifted from the list's canonical order.

Works with or without PyYAML.

**Keeping it current** — click **Update...** next to the plugin-order.yml field
to download MOMW's latest. The download must parse as plugin-order data with
hundreds of entries before a single byte is written (an error page or moved URL
can never clobber your file), and the old copy is kept as a timestamped `.bak`.

---

## Master check, lint, and watchdogs

Every **Sort** runs a **MASTER CHECK** automatically (read-only): each active
plugin's TES3 header masters are verified against the load order.

- `[MISSING MASTER]` — a required master is absent. Distinguishes "installed but
  not in the load order" (enable it) from "not found in any data folder" (the
  game will refuse to load).
- `[MASTER ORDER]` — a master loads *after* its dependent.
- `[MASTER SIZE]` — the installed master's size differs from what the plugin
  recorded (built against a different version; a recorded size of `0` usually
  means a failed `tes3cmd` sync — the tes3cmd window's in-app resync fixes it).

Plugins with a missing/mis-ordered master are drawn in **purple** in the plugin
panel.

**Lint** (button, or `--lint`) runs tes3lint-style checks over the sorted,
enabled plugins — natively, no perl needed:

- `[EVLGMST]` — the 72 "evil GMSTs", flagged only when name **and** value match
  (a deliberate change is left alone).
- `[FOGBUG]` — an interior cell with fog density 0 (black-void bug).
- `[NO PATHGRID]` — a new interior cell with no pathgrid anywhere in the load
  order (NPCs can't pathfind).
- `[EXP-DEP]` — scripts calling Tribunal/Bloodmoon functions in a plugin that
  doesn't master the expansion.
- `[TWIN]` — an active `.omwaddon`/`.esp` whose `.omwscripts` sibling (or vice
  versa) sits in the same folder but isn't in the load order.
- `[HEADER]` — a custom plugin with a blank author/description.

**Watchdogs** — a `[STALE]` warning fires when a generated artifact
(`delta-merged.omwaddon`, `deleted_groundcover.omwaddon`, `S3LightFixes.esp`) is
older than active plugins, meaning the merge no longer reflects your load order;
re-run the Configurator.

---

## tes3cmd frontend

The **tes3cmd** button opens a front-end for tes3cmd (from the MOMW Tools Pack;
the compiled `tes3cmd.exe` is preferred, the perl script works if perl is on
`PATH`). Because tes3cmd only understands one flat `Data Files` directory, this
tool **stages** each plugin into a private Morrowind-shaped folder with its
masters (hardlinked, cached across runs) so tes3cmd sees the full VFS:

- **clean** — removes junk (dup records, junk cells, evil GMSTs). Plugins whose
  masters can't be found are skipped (cleaning without masters gives wrong
  results). Files are cleaned masters-before-dependents in load order. A
  "MOMW needs-cleaning" button queues exactly the plugins `plugin-order.yml`
  flags. **Morrowind/Tribunal/Bloodmoon are never cleaned** — even a careful
  clean rewrites bytes other content depends on.
- **resync master sizes** — done **in-app**, VFS-aware. tes3cmd's own
  `header --synchronize` writes *empty* sizes on a multi-folder OpenMW setup;
  this resolves each master across all data folders and rewrites only the 8-byte
  size fields (one-time `.masterfix.bak`, verified byte-exact).
- **header** — view author/description/masters (read-only).

### Making mlox rules (rule maker)

The rule base is actively maintained at
[github.com/DanaePlays/mlox-rules](https://github.com/DanaePlays/mlox-rules) —
the same source plox uses and mlox 1.1+ auto-updates from. Two buttons on the
rule-files panel keep you current and let you extend it:

- **Update Rules...** downloads the current `mlox_base.txt`/`mlox_user.txt` over
  the matching files in your list (timestamped `.bak` kept; files with other
  names are never touched).
- **New Rule...** writes your own `[Order]`/`[NearStart]`/`[NearEnd]` rule
  without knowing the syntax: grab the selected rows from the plugin panel (their
  order becomes the rule order) or type names (wildcards and `<VER>` allowed,
  validated with the same regex the parser uses), preview, and append. Rules go
  to a personal file that's auto-added **last** in the list so your rules win
  conflicts — `mlox_base.txt`/`mlox_user.txt` are refused as targets since
  "Update Rules..." overwrites them. Consider contributing good rules
  [upstream](https://morrowind-modding.github.io/modding-tools/sorting-plugin-load-order/mlox/mlox-rule-guidelines).
- **Sources...** points both updaters at a fork or mirror if upstream moves. The
  rules field is a URL template containing `{name}`; the plugin-order.yml field
  is a plain URL. Blank = built-in defaults; both persist in settings.

### Save Check and Backups

- **Save Check** — pick an OpenMW `.omwsave` and verify every content file it
  depends on (the save's `DEPE` list) is still in the load order. OpenMW refuses
  to load a save with missing plugins, so this catches it before an export
  orphans a character.
- **Backups** — lists every backup this tool, tes3cmd, and the Configurator
  leave behind (`.preclean.bak`, `.masterfix.bak`, `name~1.esp`, timestamped
  `.bak-*` / `.backup.*`) across the data folders, with restore-over-original
  and delete.

---

## Conflict detection (TES3 records)

Click **Check Conflicts** (after a Sort) — or pass `--check-conflicts` on the CLI
— to scan the active plugins for **record-level conflicts**, the way TES3View /
tes3cmd do: where two or more plugins define or override the *same* record (by
type + editor id), the last one in the load order wins.

- Results appear both as a colour-coded report in the log **and** in a dedicated
  **Conflicts window** (sortable table: type, record, how many plugins touch it,
  and the winner). Conflicts that involve **your** custom mods are marked with a
  ★ and listed first — those are the ones your additions caused.
- Save the full list to CSV for later.
- Read-only and opt-in: it never changes the sort or your files, and it needs the
  plugin files reachable via your cfg's `data=` folders. It can be slow on a big
  list (it parses every active plugin), so it runs in the background.

Handles **`.esp/.esm/.omwaddon/.omwgame`** (all TES3-format) and **`.omwscripts`**
(OpenMW's text Lua-attach config). Lua scripts are surfaced as `LuaScript` records
keyed by their script path — whether declared in an `.omwscripts` file or in an
`.omwaddon`'s `LuaScriptsCfg` — so two mods attaching the same script path show up
as a conflict.

**Two engines:**

- **Built-in (default, no dependencies)** — record-level: which plugins touch the
  same record. Handles the common types including scripts (by name), interior
  cells (by name), exterior cells / landscape (by grid coords), and Lua scripts
  (by path, from `.omwscripts` and `.omwaddon`).
- **tes3conv (optional) — adds field-level diffs.** If a
  [`tes3conv`](https://github.com/Greatness7/tes3conv) binary is available, the
  Conflicts window shows a **field-by-field comparison** for the selected record
  (each plugin's value side by side, differing fields in red, last column wins) —
  the same JSON approach TES3 Conflictsolver uses. Point the tool at it via the
  **Set tes3conv...** button, the `--tes3conv` CLI flag, `$MLOX_TES3CONV`, your
  `PATH`, or by dropping the binary next to the script.

Depth is record-level for detection (not a full record schema like xEdit); use it
to spot overlaps worth a patch, and the field diff (with tes3conv) to see exactly
what differs. Confirm anything subtle in TES3View if needed.

### Data-path resource (VFS) conflicts

Click **Resource Conflicts** (or `--resource-conflicts`) to scan your `data=`
folders for **loose-file conflicts**: the same relative path (a mesh, texture,
script, icon…) provided by two or more mod folders. In OpenMW's VFS the **later**
`data=` folder wins, so these are decided by data-path order — reorder the
**Data path order** panel to change the winner (this is what MO2's *Data*
conflicts show). Read-only; a window lists each file, how many folders provide
it, and the winner (yours highlighted), with CSV export. Can be slow on a big
install.

### Skipping noisy mods, settings, JSON

- **Exclude field** (Options) — comma-separated glob patterns to skip in the
  Conflict / Cell-map / Resource scans, e.g. `s3lightfixes*, *delta*, *grass*`.
  Great for "touches-everything" mods that swamp the results. Saved with settings.
- **Settings are remembered** — your paths, rule files, options, tes3conv path,
  and exclude patterns are saved to `mlox_subset_sort_settings.json` on close and
  reloaded next launch.
- **Dump tes3conv JSON** — in the Conflicts window (tes3conv mode), export the
  per-plugin JSON for every scanned plugin to a folder you pick.
- **Keep tes3conv JSON dump** (Options) — tes3conv conversions are always spooled
  to a `tes3conv_json` folder next to the tool and read one plugin at a time
  (bounded memory, even on 900+ plugins). Within a run the spool is reused, so
  **Check Conflicts followed by Cell Map won't re-run tes3conv** — a plugin is
  only re-converted if it changed (checked by modified-time). This box only
  decides what happens on exit: checked = keep the folder (reused next launch
  too); unchecked = delete it on close. CLI: `--json-dump-dir FOLDER` keeps it.
- **Scan caching (fast repeats).** The first Check Conflicts / Cell Map reads each
  plugin's JSON once and writes two tiny per-plugin sidecars next to it —
  `*.keys.json` (record ids, for conflict detection) and `*.cells.json` (cells
  touched, for the map) — in a single pass, so running both features reads each
  big JSON only once per run. Every scan after that reads those few-KB sidecars
  instead of re-parsing the multi-MB JSON, so **repeat Check Conflicts and Cell
  Map runs are near-instant**. Sidecars are mtime-invalidated per plugin (an
  edited mod rebuilds only its own), live in the same `tes3conv_json` folder, and
  follow the same keep/cleanup rule. The on-click field diff still reads the full
  record on demand, so accuracy is unchanged.
- The **field comparison** shows list fields (e.g. `references`) as a count;
  **double-click a field row** to see the full value per plugin, pretty-printed.
  Your custom mods are flagged with a **★** in the column headers (and shown in
  **orange** in the double-click popout, vs grey for curated-list plugins), so you
  can tell at a glance which side of a conflict is yours. The popout has a
  **Word wrap** toggle for long values.
- The **Cell map** is written to `cell_map.html` and shown in an in-app window if
  `pywebview` (best) or `tkinterweb` is installed, otherwise in your browser; the
  window has **Save HTML** / **Open in browser**.

### Cell map (which mods touch which cells)

Click **Cell Map** (after a Sort) — or pass `--cell-map out.html` — to build a
self-contained HTML page (a port of
[modmapper](https://www.nexusmods.com/morrowind/mods/53069)) with three tabs: an
**exterior-cell SVG heatmap** (uniform squares, brighter/hotter = more mods
editing that cell; hover for the mod list, click a cell to jump to its list row)
plus filterable **exterior-** and **interior-cell lists**. Cells your custom mods
touch get a gold outline, so you can see exactly where your additions land and
which areas are conflict hotspots. It writes `cell_map.html` and opens it in an
in-app window (with `pywebview`/`tkinterweb`) or your browser, changes nothing,
and works with either engine (tes3conv gives the most exact cell identification).

---

## Command-line usage

```
# Preview (default): print the plan, write nothing
python mlox_subset_sort.py \
    --cfg openmw.cfg \
    --rules mlox_base.txt mlox_user.txt \
    --customizations momw-customizations.toml

# Durable fix: write a corrected customizations TOML (feed back into Configurator)
python mlox_subset_sort.py --cfg openmw.cfg --rules mlox_base.txt mlox_user.txt \
    --customizations momw-customizations.toml --emit-toml momw-customizations.toml

# One-shot: scan a mods folder, use MOMW's yml, and emit a fresh TOML
python mlox_subset_sort.py --cfg openmw.cfg --rules mlox_base.txt mlox_user.txt \
    --scan-dir "E:\OpenMW\Mods\custom" --subset-file mod_scan_results.txt \
    --plugin-order-yml plugin-order.yml --list-name total-overhaul \
    --sort-data-paths --emit-toml momw-customizations.toml
```

Key flags:

| Flag | Purpose |
| --- | --- |
| `--cfg` | Path to `openmw.cfg` *(required)*. |
| `--rules` | mlox rule file(s)/dirs, increasing priority *(required)*. |
| `--customizations` | Derive the subset from a `momw-customizations.toml`. |
| `--subset` / `--subset-file` | Name plugins/paths directly, or from a file. |
| `--scan-dir` | Scan a mods folder into `--subset-file`, then sort. |
| `--list-name` | The MOMW list name for the emitted TOML / yml features. |
| `--plugin-order-yml` | Enable curated-vs-custom split and yml warnings. |
| `--emit-toml` | Write the corrected `momw-customizations.toml` (the durable fix). |
| `--write-cfg` | Patch `openmw.cfg` in place instead/also (one-off). |
| `--sort-data-paths` | Also position `data=` folder inserts. |
| `--check-conflicts` | Scan active plugins for TES3 record-level conflicts. |
| `--conflicts-out` | Write the conflict list to a CSV (with `--check-conflicts`). |
| `--conflicts-subset-only` | Only report conflicts involving your custom mods. |
| `--tes3conv` | Path to tes3conv (switches to its engine; enables field-level diffs). |
| `--json-dump-dir` | Keep the per-plugin tes3conv JSON spool in this folder (reused between runs). |
| `--resource-conflicts` | Scan `data=` folders for loose-file (VFS) conflicts. |
| `--resources-out` | Write the resource-conflict list to a CSV. |
| `--lint` | tes3lint-style checks (evil GMSTs, fog bug, missing pathgrids, expansion deps, twins, headers). |
| `--exclude` | Glob patterns to skip in conflict/cell-map/resource/lint scans. |
| `--cell-map` | Write an HTML cell-coverage heatmap (which mods touch which cells). |
| `--no-predicate-warnings` | Skip `[Conflict]/[Requires]/[Note]` evaluation. |
| `--no-backup` | Skip timestamped `.bak` copies. |
| `--trace [LOGFILE]` | Write a debug trace log for troubleshooting (off by default). |
| `--dry-run` | Print the plan, write nothing. |

A timestamped `.bak` is written before any file is overwritten (unless
`--no-backup`).

---

## Rule-engine fidelity

Parsing and matching are ported from mlox itself and cross-checked against
`plox`, so several behaviors match the real engine:

- Filename matching handles `*` and `?` wildcards **and** the `<VER>`
  version-number token, with mlox's exact metacharacter escaping.
- `[Order]` chains **bridge over plugins you don't have**: `[Order] A, B, C`
  with `B` not installed still enforces `A` before `C`.
- `[Requires]/[Conflict]/[Note]` warnings understand `ALL/ANY/NOT/DESC` nesting
  and the `[VER]/[SIZE]/[DESC]` functions — reading real plugin version, file
  size, and header description from your `data=` folders, with a conservative
  fallback when those files aren't reachable. `[MWSE-LUA]` is parsed but treated
  as not-applicable under OpenMW.

Beyond mlox's rule DB, the sort also enforces the two hard load-order rules that
rules alone don't cover for arbitrary custom mods:

- **Header-master dependencies + interleaving.** Each custom plugin's TES3 header
  masters are read (from your `data=` folders and the paths added this run), and
  it's placed **after** every master it declares — and *anchored* right after the
  mod it extends (its latest non-vanilla master), so a patch/addon interleaves
  next to its target. Mods that depend only on the vanilla masters have no
  positioning info and sit at the end. Falls back to rules + ESM-first if the mod
  files aren't reachable.
- **ESM-first.** Master-type plugins (`.esm`/`.omwgame`) tie-break before ordinary
  plugins, so a custom master with no rule floats up into the master block.

Both apply only to your customs; the curated list is never reordered.

Deliberately different from full mlox (by design):

- **Sorting only repositions the subset** — the curated (non-custom) `content=`
  order is frozen and never reordered. Custom mods already in the cfg *are*
  repositioned (that's the point); only the curated list is held fixed.
- `[Requires]/[Conflict]/[Note]` are **read-only warnings** — they never change
  the order or block anything. Treat them as a prompt to go check, not gospel.
- `[NearStart]/[NearEnd]` become ordering chains among the listed plugins, not a
  hard push to the file's absolute start/end.
- `data=` inserts are placed by their `after`/`before` anchor (mlox has no
  data-path order); an unfound anchor appends at the end with a warning.

---

## Safety

- Default is preview/dry-run — nothing is written until you say so.
- Timestamped backups are made before overwriting `openmw.cfg` or a
  customizations TOML.
- The curated list order is never modified; only your additions move.
- Customizations aren't supported by the MOMW team — you're responsible for
  making sure your changes don't cause conflicts. This tool helps you *see* and
  *place* them; it doesn't guarantee they're conflict-free.
