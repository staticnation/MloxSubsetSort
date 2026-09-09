# Wraithguard Toolkit

Sort **only your custom mods** into an existing OpenMW `openmw.cfg` using mlox
rules, **without ever reordering the curated [Modding-OpenMW.com](https://modding-openmw.com/)
(MOMW) mod list** you built with `umo` + MOMW Configurator.

The existing `content=` order in `openmw.cfg` is treated as **frozen**. This
tool only works out where *your* additions (the mods under your `custom` folder)
belong relative to that frozen list and to each other, then writes the result
back as a corrected `momw-customizations.toml` - the durable fix that survives
future Configurator rebuilds.

> This is the opposite of running a whole-list sorter like PLOX. MOMW explicitly
> warns against sorting their curated lists; this tool is built specifically so
> you don't have to.

**New here? See [QUICKSTART.md](QUICKSTART.md) for a 5-minute walkthrough.**

**Writing your own rules? See [MLOX_RULES.md](MLOX_RULES.md) for the rule syntax
and the conventions the rule-base follows.** Both open from the **Help** button
inside the program as well.

---

## Contents

- `wraithguard_toolkit.py` - the command-line tool and the sort engine. It imports
  from `wraithguard/` like any other caller; import library names from the
  package they live in, not from here.
- `wraithguard/` - the engine itself, split by concern: rule handling, the
  load-order sort, `openmw.cfg` reading/emitting, plugin metadata, downloads,
  compiled-script decoding, landscape merging (`land/` - the Merged Lands port,
  see [Merged Lands](#merged-lands-landscape-merging)), and the conflict
  visualisations (`viz/`, which
  renders conflicts as self-contained HTML: a world conflict map, terrain
  height differences, path-grid graphs and a rotatable 3D terrain surface with
  relief shading, contours and multidirectional lighting - see
  [The 3D terrain view](#the-3d-terrain-view)).
- `wraithguard_toolkit_gui.py` - a drag-and-drop GUI front-end (imports the engine
  directly; it reimplements no logic).
- `mlox_base.txt`, `mlox_user.txt` - mlox rule databases (download/update with
  mlox or `plox`).
- `plugin-order.yml` - MOMW's source of truth for which plugins belong to which
  curated list (optional but recommended). From the
  [modding-openmw.com repo](https://gitlab.com/modding-openmw/modding-openmw.com/-/blob/master/momw/momw/data_seeds/data/plugin-order.yml).
- `theme_template.json` - a commented starting point for making your own GUI
  theme (see [Theming the app](#theming-the-app)).
- `CREDITS.md` - acknowledgements for the projects this tool ports, references,
  and depends on (mlox, plox, tes3conv, modmapper, OpenMW, MOMW, and more).
- `CHANGELOG.md` - what changed between releases (current: **4.0.1**).
- `CODE_REVIEW.md` - the running engineering log: defects found, and the
  reasoning behind decisions that look odd (including linter suggestions
  deliberately refused because following them would introduce bugs).

---

## Project layout

Everything needed to **build, run and test** the toolkit lives in this folder.
Reference material (the upstream projects whose formats this tool mirrors) and
scratch output are deliberately left outside it.

```
WraithguardToolkit/
├── *.md                          Docs at the top level: README, QUICKSTART,
│                                 CHANGELOG, CREDITS, CODE_REVIEW, MLOX_RULES,
│                                 NIF_PROVENANCE, MERGED_LANDS, SMOKE_TEST.
├── License/                      This project's MIT licence (`LICENSE`), plus
│                                 one file per upstream project whose licence
│                                 travels with code ported or adapted here.
├── wraithguard_toolkit.py        Engine + CLI. No GUI import; runs headless.
├── wraithguard_toolkit_gui.py    Tkinter front-end. Imports the engine.
├── wraithguard/                  Shared foundation package.
│   ├── i18n.py                   gettext translation, the _() marker.
│   ├── logging_setup.py          Levelled logging (stderr) + trace file.
│   ├── gui/                      GUI support (needs Tk): theming, widgets,
│   │                             tes3cmd + conflict-window mixins, app dir.
│   ├── mwscript/                 Compiled-script (SCDT) reading + disassembly.
│   ├── tes3fields/               Decodes binary LAND / PGRD fields for the diff
│   │                             window, plus the generated TES3 record schema.
│   ├── viz/                      Maps and visualisations as self-contained HTML:
│   │                             cell coverage map, conflict map, terrain
│   │                             deltas, path-grid graphs, 3D surface, and the
│   │                             Markdown renderer behind in-app Help. No CDN.
│   ├── images/                   Every texture format the game and mods use,
│   │                             decoded without a dependency (DDS incl. BC7,
│   │                             Targa, bitmap, a zlib-only PNG writer), picked
│   │                             by inspecting bytes, plus texture-role slots.
│   ├── nif/                      Morrowind NIF meshes: block reader, geometry,
│   │                             texture resolution, BSA-aware VFS, 3D viewer.
│   ├── land/                     The Merged Lands port: reference landmass,
│   │                             per-plugin diff, merge strategies, seam repair.
│   ├── patch/                    Building a *new* patch plugin from records
│   │                             chosen in the diff viewer; never writes a
│   │                             source mod. Conflict-status model + roll-ups.
│   ├── rules/                    mlox rule handling: patterns, parser, expr.
│   ├── configurator/             openmw.cfg: read, simulate, emit TOML.
│   ├── momw.py                   MOMW plugin-order.yml (curated lists).
│   ├── net/                      Downloads: rule files, curated order.
│   ├── plugins/                  Plugin location + header metadata.
│   ├── sort/                     Load-order sort: graph primitives + engine.
│   ├── tracing.py                Crash-survival trace logs (main + sort).
│   └── versions.py               Version regex + mlox's canonical form.
├── tools/                        Developer scripts (not shipped): the gate
│                                 checkers, the code generators, make_pot.py
│                                 -- all under the test suite.
├── tests/                        pytest suite: the hermetic set plus a Tk smoke
│                                 set that runs under xvfb in CI.
├── testdata/                     Copies of a real setup, used by the tests.
├── locale/                       wraithguard_toolkit.pot + translator guide.
├── art/                          Icons, banner, Nexus description, AST graphs.
├── build/                        PyInstaller / auto-py-to-exe configuration.
├── pyproject.toml                ruff / black / pytest / mypy configuration.
└── theme_template.json           Commented starting point for a custom theme.
```

Only the standard library is required to run. Optional extras (`tkinterdnd2`,
`PyYAML`, `pywebview`/`tkinterweb`, `tomli` on Python < 3.11) each enable one
feature and degrade gracefully when missing. Kept *outside* this folder because
none of it is needed to build or run: the reference sources read while porting
(credited in `CREDITS.md`), the third-party Perl/`tes3cmd` tools the app drives,
and run output (logs, `cell_map.html`, `resource_conflicts.csv`, the packaged
`.exe`).

---

## Requirements & setup (Windows, Linux, macOS)

Pure Python + tkinter, so it runs on all three platforms.

- **Python 3.10+** (3.11+ gets `tomllib` for free; on 3.10 install `tomli`).
- **tkinter** - bundled with the python.org installers on Windows and macOS. On
  Linux, install it from your package manager, e.g. Debian/Ubuntu:
  `sudo apt install python3-tk`.
- **Optional extras** (the tool degrades gracefully without them):
  - `pip install tkinterdnd2` - drag files into the GUI from your file manager.
    Without it, use the **Browse...** buttons (dragging rows to reorder still
    works regardless).
  - `pip install PyYAML` - faster/robust `plugin-order.yml` parsing. Without it,
    a built-in parser is used automatically.
  - `pip install tomli` - only on Python < 3.11, for reading TOML.
  - `pip install pywebview` - shows the cell map in an in-app window (uses the OS
    webview, so the SVG heatmap and tabs render exactly like a browser).
    `tkinterweb` also works as a lighter fallback; without either, the map opens
    in your default browser.

Install optional deps on an externally-managed Python with
`pip install ... --break-system-packages` if needed.

### Packaging into a standalone `.exe` (PyInstaller / auto-py-to-exe)

The tool runs frozen. It never persists data next to `__file__` (which, when
frozen, is a temp extraction dir that's wiped on exit), so your settings and
outputs survive the build:

- `wraithguard_toolkit_settings.json`, `wraithguard_toolkit_trace.log`, `cell_map.html`,
  and the `tes3conv_json/` spool are written **next to the `.exe`**. If that
  folder isn't writable (e.g. installed under `Program Files`), they fall back to
  a per-user data dir (`%APPDATA%\MloxSubsetSort` on Windows,
  `~/Library/Application Support/MloxSubsetSort` on macOS,
  `~/.config/MloxSubsetSort` on Linux).
- The in-app cell-map viewer re-invokes the same executable with `--show-map`, so
  pywebview works from the frozen build too (no bundled Python interpreter
  needed). **But the library must actually be inside the exe** - if it isn't, the
  map falls back to the browser.

**Bundling the embedded (pywebview) cell-map viewer.** In your build environment,
`pip install pywebview`, then tell PyInstaller / auto-py-to-exe to collect it and
its Windows backend. In auto-py-to-exe, add under *Advanced → hidden-import* and
*--collect-all*, or on the PyInstaller command line:

```
pyinstaller --noconsole --collect-all webview --collect-all clr_loader \
    --hidden-import clr --hidden-import webview.platforms.edgechromium \
    wraithguard_toolkit_gui.py
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

**One data folder must be added by hand: the 3D viewer library.** PyInstaller
follows imports, not data, so the vendored three.js build under
`wraithguard/viz/assets/` (loaded as `assets/three.cjs`) is not collected
automatically. Map it into the build:

```
--add-data "wraithguard/viz/assets;assets"
```

Without it the app runs normally and the **View in 3D** button reports that the
library was not shipped - deliberately a clear message rather than a blank
window. You do **not** need to add `wraithguard/` or `locale/` by hand:
PyInstaller collects the package by following the import graph, and `locale/` is
a developer directory (no `.mo` catalogues ship yet). Verify any build from the
Log panel's first line - a build stamp `Wraithguard Toolkit <version> --
frozen=True built=<timestamp>`; a stale build looks exactly like a code bug. See
`SMOKE_TEST.md` §5a.

Run the GUI:

```
python wraithguard_toolkit_gui.py
```

### Verifying a download (minisign)

Release builds are signed with [minisign](https://jedisct1.github.io/minisign/).
Each artifact on a Release has a matching `.minisig` beside it. To check a
download is authentic and intact, install `minisign` (or the Rust `rsign2`) and
verify against this project's public key:

```
minisign -Vm wraithguard-toolkit-windows-x86_64.exe -p minisign.pub
```

where `minisign.pub` is the public key shipped in this repository. (The
signatures are standard minisign, so you do **not** need Python or this project's
tooling to verify them.)

**Maintainer setup (one-time).** The signing is done in CI by
`tools/sign_release.py` (pure-Python via the `py-minisign` package -- no C or Rust
toolchain on the runner). To enable it:

1. Generate a key pair locally: `minisign -G` -> `minisign.key` (secret) and
   `minisign.pub` (public). Commit `minisign.pub`.
2. In the repository's **Settings -> Secrets and variables -> Actions**, add
   `MINISIGN_SECRET_KEY` (paste the whole `minisign.key` file) and
   `MINISIGN_PASSWORD` (the password you chose).
3. Push a `v*` tag. Each OS build signs its own artifact and attaches the
   `.minisig` to the Release. If the secret is absent the signing step is
   skipped rather than failing, so a release can still go out during setup.

---

## GUI walkthrough

The left half is the drag-to-reorder panels; the right half is inputs, options,
actions, and a colorized log. Drag the **hamburger grips** on the dividers to
resize any panel.

**Inputs (top right):**

- **openmw.cfg** *(required)* - read the current `content=`/`data=` order from
  here, and (optionally) patch it.
- **customizations.toml** - your `momw-customizations.toml`, to pull the
  plugin/data-path subset from automatically.
- **subset file** - a plain-text list (one plugin filename or data folder path
  per line) or a minimal TOML. A `#` starts a comment only at the start of a line
  or after a space, so a `#` inside a name (e.g. `FMI_#NotAllDunmer.ESP`) is kept.
  Combine with an emit target and it's enough on its own to generate a brand-new
  customizations TOML.
  - **Scan...** - generate this subset by walking a mods folder (see below).
- **emit corrected TOML to** - where to write the sorted/re-anchored
  `momw-customizations.toml`. (Or tick *Write directly back...* to overwrite the
  source in place, with a timestamped `.bak`.)
- **list name** - the MOMW list these customizations apply to (e.g.
  `total-overhaul`). Required by the Configurator; also drives the yml features.
- **plugin-order.yml** - enables the curated-vs-custom features below.
- **Rule files** - mlox `.txt` rule databases in priority order (base first,
  your user rules last).

**Options:**

| Option | Effect |
| --- | --- |
| Dry run | Preview only - Export writes nothing (on by default). |
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

1. **Sort** - runs mlox and fills the order panels. Never writes anything; always
   safe. Red rows are your custom additions; **purple** rows have a missing or
   mis-ordered master (see the MASTER CHECK section in the log).
2. **Export** - writes `openmw.cfg` and/or the corrected TOML using whatever
   order the panels currently show. (Uncheck *Dry run* to actually write.) Every
   TOML export runs a **Configurator preview**: the emitted customizations are
   applied to a simulated fresh cfg using a faithful re-implementation of
   momw-configurator's own apply logic, and the result is verified against the
   sorted order - a green `VERIFIED` line means what the Configurator will do is
   exactly what you sorted. Export also warns if `openmw.cfg` changed on disk
   since the Sort (e.g. the Configurator re-ran underneath you).

The **analysis** buttons (Check Conflicts, Cell Map, Resource Conflicts, Lint)
run against the sorted, enabled plugins and never modify anything; the **tools**
row covers tes3cmd (clean / resync / header, VFS-safe), Save Check (verify an
`.omwsave`'s dependencies are still present), and Backups (restore or delete
backups left by this tool, tes3cmd, and the Configurator).

**Resource Conflicts shows meshes in 3D.** Select a conflicting `.nif` and
press **View in 3D**: one viewport with a checkbox per provider, so the camera
never moves as you switch between them - the whole point, since a view that
re-frames itself is comparing two different pictures. A node tree on the left
lists what the render cannot show: collision nodes, controllers, particle
systems, and any block nothing references. Drag to orbit, wheel to zoom, and
**Textures** turns texturing off when you want to compare shape alone.

It opens in the same in-app viewer as the other visualisations (pywebview),
falling back to your browser. It is served from a small local server on `127.0.0.1` - nothing leaves
your machine, the address is not reachable from the network, and every request
carries a one-off token for that session. **Export 3D file...** writes the same
view as a single self-contained HTML file you can keep or send to someone; that
is also what you get automatically if the app cannot open a local port.

**Resource Conflicts reads meshes.** A conflict list can tell you which mod
wins a file; it cannot tell you the winner is a low-poly stand-in with no
collision. So when two mods ship the same `.nif` and the bytes actually differ,
the report says what the winning mesh *costs* you - lost collision, lost
animation, a fraction of the triangles, or textures it references that nobody
ships. Select a row and the panel below reads both meshes then and there and
describes each provider in full.

Nothing is read during the scan except meshes that already conflict **and**
already differ, and results are cached on file contents, so a mesh shipped
identically by four mods is parsed once. A mesh it cannot read - an unfamiliar
NIF version, a truncated download - is reported as unreadable rather than
guessed at, and a partly-read mesh never reports something as *missing*, since
the node may simply be in the part that was not reached.

Every list in the app supports **type-to-jump**: click it and start typing a
name - prefix match first, substring fallback, tap one letter repeatedly to
cycle its matches, Backspace edits, Esc clears. The two sort panels show what
you've typed in their title bar.

### Reordering rows

Drag a row up/down with the mouse, or select it and use **Move Up** / **Move
Down**. Both work with a multi-selection (Ctrl/Cmd-click, Shift-click) - the Move
buttons handle any selection, and dragging moves a contiguous block. This only
overrides where *your* additions land; it's applied at Export time.

### Reading the log

The log color-codes each line so you can scan it quickly:

- **green** - a plugin/path this sort inserted or moved (`<-- inserted`).
- **orange** - a heads-up: an mlox `[Conflict]/[Requires]/[Note]` warning, a
  `plugin-order.yml` note, or a rule *not applied* because your curated cfg order
  already ordered it the other way. Those "not applied" lines are a handy
  diagnostic: if a mod you just added misbehaves, they tell you exactly where
  mlox disagreed with your cfg - you decide whether to nudge it in the panel.
- **blue** - a section header; **red** - an error worth checking.
- **plain** - a frozen base row left untouched.

### Theming the app

The **Theme:** dropdown next to the log panel sets the colors for the *whole
app* - window, buttons, frames, tabs, lists and entries, as well as the log's
own syntax highlighting and the field-diff viewer. Switching re-themes
everything immediately, including windows you already have open, and your
choice is remembered between sessions.

23 themes are built in: Dark (default), Dracula, Monokai, Atom One Dark,
Gruvbox Dark, Monokai Pro, Tokyo Night, Night Owl, Nord, Shades of Purple,
GitHub Dark, Catppuccin Mocha, Ayu Dark, Cobalt2, SynthWave '84, Winter is
Coming, Material Dark, Bluloco Dark, Palenight, Poimandres, Noctis, Panda and
City Lights. (Looking for One Dark Pro? That's the One Dark palette - use
Atom One Dark.) **Import Theme...** adds your own, in either of two formats:

- **Native JSON** - 9 required colors (`background`, `foreground`, `select`,
  `section`, `warn`, `error`, `ok`, `inserted`, `dim`), plus optional
  syntax-token colors and an optional `"chrome"` object if you want to set the
  window colors explicitly. Start from **`theme_template.json`** next to the
  app: it is commented, imports as-is, and reproduces the default palette.
- **base16 schemes** - any `.yaml`/`.yml`/`.json` with `base00`..`base0F`, e.g.
  from the [base16](https://github.com/chriskempson/base16) or
  [atelierbram](https://github.com/atelierbram/syntax-highlighting) scheme
  repos. No PyYAML needed; a small built-in parser reads them.

Any window color you don't specify is derived from the theme's `background` -
lightened for dark themes, darkened for light ones - so a plain 9-color theme
or a bare base16 scheme still themes the entire app coherently. Imported themes
are saved to `log_themes.json` and appear in the dropdown from then on.

### Opting rows out (disable / enable)

Not everything you scanned needs to load. In either order panel, select one or
more rows (Ctrl/Cmd-click and Shift-click for multi-select) and click
**Disable / Enable** - or double-click a single row. Disabled rows are dimmed and
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
any plugins in it as `content=` entries - then stops descending that branch.

- **Create subset text document** ON → you choose where to save the `.txt`, and
  it's loaded into the subset-file field for reuse.
- OFF → the result is held in memory and fed straight to the sort; nothing is
  written to disk.

**Adding what the scan missed.** The recognised-folder rule is deliberately
conservative -- loosening it risks mis-reading another mod's layout -- so a mod
with no standard asset folder and no plugin is skipped. The common case is an
OpenMW Lua mod with a non-standard VFS layout (e.g. `NgardeParrySounds\sounds\`,
where `sounds` isn't a recognised name). For those, add the folder by hand:
**Add data folder...** under the data-path list opens a picker, or drag the
folder onto that list from your file manager. The plugin list has the same pair
-- **Add plugin...** and drag-drop -- for a stray plugin outside a scanned
folder. Either list accepts either kind (a dropped plugin is filed as a plugin,
a folder as a data path). Manual additions merge into the subset on the next
**Sort**, on top of whatever subset file or in-memory scan is already loaded; a
data folder needs **Sort data= paths too** checked to be placed in the load
order (otherwise it is only carried into an emitted TOML). They last for the
session.

---

## plugin-order.yml integration

Point the **plugin-order.yml** field at MOMW's file and set **list name**. The
tool then knows exactly which plugins belong to your curated list versus which
are genuinely your additions, and:

- **Curated-vs-custom split** - plugins on the list are excluded from the sort
  (never reordered - that's the list's job) so only your true custom additions
  are touched and highlighted.
- **Read-only warnings**:
  - `[REDUNDANT]` - a "custom" plugin that's actually already on the list.
  - `[ORPHAN]` - a plugin in your cfg that's neither on the list nor in your
    customizations (e.g. a manually-added or TES3CMD-cleaned file).
  - `[NEEDS CLEANING]` - flagged for TES3CMD in the yml.
  - `[LIST ORDER]` - your base order has drifted from the list's canonical order.

Works with or without PyYAML.

**Keeping it current** - click **Update...** next to the plugin-order.yml field
to download MOMW's latest. The download must parse as plugin-order data with
hundreds of entries before a single byte is written (an error page or moved URL
can never clobber your file), and the old copy is kept as a timestamped `.bak`.
On a fresh setup you can click **Update...** with the field blank: it asks where
to save the file, remembers the choice, and downloads it - no need to Browse to a
file that does not exist yet.

---

## Master check, lint, and watchdogs

Every **Sort** runs a **MASTER CHECK** automatically (read-only): each active
plugin's TES3 header masters are verified against the load order.

- `[MISSING MASTER]` - a required master is absent. Distinguishes "installed but
  not in the load order" (enable it) from "not found in any data folder" (the
  game will refuse to load).
- `[MASTER ORDER]` - a master loads *after* its dependent.
- `[MASTER SIZE]` - the installed master's size differs from what the plugin
  recorded (built against a different version; a recorded size of `0` usually
  means a failed `tes3cmd` sync - the tes3cmd window's in-app resync fixes it).

Plugins with a missing/mis-ordered master are drawn in **purple** in the plugin
panel.

**Lint** (button, or `--lint`) runs tes3lint-style checks over the sorted,
enabled plugins - natively, no perl needed:

- `[EVLGMST]` - the 72 "evil GMSTs", flagged only when name **and** value match
  (a deliberate change is left alone).
- `[FOGBUG]` - an interior cell with fog density 0 (black-void bug).
- `[NO PATHGRID]` - a new interior cell with no pathgrid anywhere in the load
  order (NPCs can't pathfind).
- `[EXP-DEP]` - scripts calling Tribunal/Bloodmoon functions in a plugin that
  doesn't master the expansion.
- `[TWIN]` - an active `.omwaddon`/`.esp` whose `.omwscripts` sibling (or vice
  versa) sits in the same folder but isn't in the load order.
- `[HEADER]` - a custom plugin with a blank author/description.

**Watchdogs** - a `[STALE]` warning fires when a generated artifact
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

- **clean** - removes junk (dup records, junk cells, evil GMSTs). Plugins whose
  masters can't be found are skipped (cleaning without masters gives wrong
  results). Files are cleaned masters-before-dependents in load order. A
  "MOMW needs-cleaning" button queues exactly the plugins `plugin-order.yml`
  flags. **Morrowind/Tribunal/Bloodmoon are never cleaned** - even a careful
  clean rewrites bytes other content depends on.
- **resync master sizes** - done **in-app**, VFS-aware. tes3cmd's own
  `header --synchronize` writes *empty* sizes on a multi-folder OpenMW setup;
  this resolves each master across all data folders and rewrites only the 8-byte
  size fields (one-time `.masterfix.bak`, verified byte-exact).
- **header** - view author/description/masters (read-only).

### Making mlox rules (rule maker)

The rule base is actively maintained at
[github.com/DanaePlays/mlox-rules](https://github.com/DanaePlays/mlox-rules) -
the same source plox uses and mlox 1.1+ auto-updates from. Two buttons on the
rule-files panel keep you current and let you extend it:

- **Update Rules...** downloads the current `mlox_base.txt`/`mlox_user.txt` over
  the matching files in your list (timestamped `.bak` kept; files with other
  names are never touched). With no rule files set yet it asks for a folder, puts
  `mlox_base.txt` and `mlox_user.txt` there, adds them to the list, and downloads
  them - so a fresh install can fetch its rules without hunting one down first.
- **New Rule...** writes your own rule without knowing the syntax. It covers the
  whole vocabulary - `[Order]`, `[NearStart]`, `[NearEnd]`, `[Note]`,
  `[Requires]`, `[Conflict]` and `[Patch]`, the `ALL`/`ANY`/`NOT` expression
  tree, the `[DESC]`/`[SIZE]`/`[VER]` predicates, `(Ref:)` citations, `@Section`
  headings and the `!`/`!!`/`!!!` marks. Grab the selected rows from the plugin
  panel (their order becomes the rule order) or type names, group them into
  `[ANY ...]`, preview, and append.

  Everything is **validated before it can be written**, because mlox discards a
  rule it cannot use *without saying so* - the moment of writing is the only
  chance you get to find out. A **Rule guide** button opens
  [MLOX_RULES.md](MLOX_RULES.md) beside the window.

  Rules go to a personal file that's auto-added **last** in the list so your
  rules win conflicts - `mlox_base.txt`/`mlox_user.txt` are refused as targets
  since "Update Rules..." overwrites them. Consider contributing good rules
  [upstream](https://morrowind-modding.github.io/modding-tools/sorting-plugin-load-order/mlox/mlox-rule-guidelines).
- **Sources...** points both updaters at a fork or mirror if upstream moves. The
  rules field is a URL template containing `{name}`; the plugin-order.yml field
  is a plain URL. Blank = built-in defaults; both persist in settings.

### Save Check and Backups

- **Save Check** - pick an OpenMW `.omwsave` and verify every content file it
  depends on (the save's `DEPE` list) is still in the load order. OpenMW refuses
  to load a save with missing plugins, so this catches it before an export
  orphans a character.
- **Backups** - lists every backup this tool, tes3cmd, and the Configurator
  leave behind (`.preclean.bak`, `.masterfix.bak`, `name~1.esp`, timestamped
  `.bak-*` / `.backup.*`) across the data folders, with restore-over-original
  and delete.
> **Grass mods.** A folder scan takes every plugin it finds, so a shared mods
> folder sweeps grass plugins into the subset along with everything else. Any
> plugin your `openmw.cfg` declares on a `groundcover=` line is held out of
> `content=` automatically (the run tells you which), because loading a grass
> mod as content spawns every blade as a real object. Its `data=` path is still
> written, and the `groundcover=` lines are left alone. Plugins that merely have
> "grass" or "groundcover" in the *name* are not touched - only what your cfg
> actually declares.
>
> For a grass mod you have **just installed**, which nothing declares yet, say so
> once: a `groundcover=Vurt_Grass.esp` line in your subset file, `--groundcover
> Vurt_Grass.esp` on the CLI, or the **Declare as groundcover** field in Options.
> It is then written as `groundcover=` instead of `content=`, in both the cfg and
> the emitted TOML. Name its folder as usual - the `data=` entry is still needed
> for OpenMW to find the file.

- **Help** - opens this Read me or the Quick start as a readable page with a
  contents sidebar, rendered by the app itself. No network, no external viewer,
  and it works from the frozen `.exe` (both documents are bundled into it).

---

## Conflict detection (TES3 records)

Click **Check Conflicts** (after a Sort) - or pass `--check-conflicts` on the CLI
- to scan the active plugins for **record-level conflicts**, the way TES3View /
tes3cmd do: where two or more plugins define or override the *same* record (by
type + editor id), the last one in the load order wins.

- Results appear both as a color-coded report in the log **and** in a dedicated
  **Conflicts window** (sortable table: type, record, how many plugins touch it,
  and the winner). Conflicts that involve **your** custom mods are marked with a
  ★ and listed first - those are the ones your additions caused.
- Double-click a field for the full value per plugin. Where the field can be
  tied to a documented subrecord it is labelled in the file format's own terms
  (`VHGT - Height Data (struct, 4,232 bytes, optional)`), and a **Format
  reference** button shows what the whole record type is supposed to contain:
  every subrecord, whether the game requires it, how wide it is, and the named
  fields inside the struct ones. A diff tells you what changed; that tells you
  what it was.
- Compiled scripts are **disassembled** rather than shown as base64, including
  the 360 MWSE / MW-Enhanced functions - calls to those are marked, because a
  script using one will not run without that runtime installed.
- Save the full list to CSV for later.
- **Plugin view** turns the flat list into your load order as a tree: file ->
  record type -> record, with the record compared across every plugin that
  defines it. A row's colour says what *that* plugin is doing to the record -
  blue defines it first, green changes it and nothing later disagrees, amber
  changes it and still wins, **red** changes it and something later overrides
  that change, grey redefines it without changing anything. The whole tree
  colours itself: opening it judges the order once in the background (carefully,
  so a large order does not run out of memory) and the colours fill in without
  you opening a group. The same visualisers, image/mesh viewers and patch maker
  the flat diff has are wired into it: it carries the full set of patch buttons
  (**Add record to patch**, **Merge field**, **Define value**, **Patch
  Builder**), so it is a second place to build and review a patch, not just to
  look. It also adds **Merge this plugin's fields...** -- pick a plugin, then pick
  which of its fields should win across *every* record it defines, in one step
  instead of hundreds. The picker lists only the fields that plugin would
  actually change, each with how many records it would touch and a preview of the
  plugin's value; the plugin is read in the background so the window stays
  responsive. Selecting a plugin row also highlights, in
  purple, every plugin it conflicts with -- a *lost/broad* toggle chooses between
  only the records where an edit is discarded (the default) and any shared record.
- Read-only and opt-in: it never changes the sort or your files, and it needs the
  plugin files reachable via your cfg's `data=` folders. It can be slow on a big
  list (it parses every active plugin), so it runs in the background.

Handles **`.esp/.esm/.omwaddon/.omwgame`** (all TES3-format) and **`.omwscripts`**
(OpenMW's text Lua-attach config). Lua scripts are surfaced as `LuaScript` records
keyed by their script path - whether declared in an `.omwscripts` file or in an
`.omwaddon`'s `LuaScriptsCfg` - so two mods attaching the same script path show up
as a conflict.

**Two engines, both full-featured:**

- **Built-in (default, no dependencies)** - a native in-process reader that
  parses every TES3 record type and gives you both record-level detection (which
  plugins touch the same record) *and* the field-by-field diff. It reproduces
  `tes3conv`'s exact JSON schema, so the Conflicts window's field comparison,
  the cell map and Merged Lands all work with nothing installed. (Handles scripts
  by name, interior cells by name, exterior cells / landscape by grid, and Lua
  scripts by path.)
- **tes3conv (optional, preferred when present)** - the community's trusted
  converter. When a [`tes3conv`](https://github.com/Greatness7/tes3conv) binary is
  found it is used instead of the native reader, and it is still what does the
  binary *encoding* for Merged Lands. Point the tool at it via the **Set
  tes3conv...** button, the `--tes3conv` CLI flag, `$MLOX_TES3CONV`, your `PATH`,
  or by dropping the binary next to the script. Installing `zstandard` makes the
  native reader's landscape/script output byte-identical to tes3conv's.

The field-by-field comparison shows each plugin's value side by side, differing
fields in red, last column wins - the same JSON approach TES3 Conflictsolver
uses. Depth is record/field level (not a full editable schema like xEdit); use it
to spot overlaps worth a patch and see exactly what differs. Confirm anything
subtle in TES3View if needed.

### Data-path resource (VFS) conflicts

Click **Resource Conflicts** (or `--resource-conflicts`) to scan your `data=`
folders for **loose-file conflicts**: the same relative path (a mesh, texture,
script, icon…) provided by two or more mod folders. In OpenMW's VFS the **later**
`data=` folder wins, so these are decided by data-path order - reorder the
**Data path order** panel to change the winner (this is what MO2's *Data*
conflicts show). Read-only; a window lists each file, how many folders provide
it, and the winner (yours highlighted), with CSV export. Can be slow on a big
install.

### Skipping noisy mods, settings, JSON

- **Exclude field** (Options) - comma-separated glob patterns to skip in the
  Conflict / Cell-map / Resource scans, e.g. `s3lightfixes*, *delta*, *grass*`.
  Great for "touches-everything" mods that swamp the results. Saved with settings.
- **Settings are remembered** - your paths, rule files, options, tes3conv path,
  and exclude patterns are saved to `wraithguard_toolkit_settings.json` on close and
  reloaded next launch.
- **Dump tes3conv JSON** - in the Conflicts window (tes3conv mode), export the
  per-plugin JSON for every scanned plugin to a folder you pick.
- **Keep tes3conv JSON dump** (Options) - tes3conv conversions are always spooled
  to a `tes3conv_json` folder next to the tool and read one plugin at a time
  (bounded memory, even on 900+ plugins). Within a run the spool is reused, so
  **Check Conflicts followed by Cell Map won't re-run tes3conv** - a plugin is
  only re-converted if it changed (checked by modified-time). This box only
  decides what happens on exit: checked = keep the folder (reused next launch
  too); unchecked = delete it on close. CLI: `--json-dump-dir FOLDER` keeps it.
- **Scan caching (fast repeats).** The first Check Conflicts / Cell Map reads each
  plugin's JSON once and writes two tiny per-plugin sidecars next to it -
  `*.keys.json` (record ids, for conflict detection) and `*.cells.json` (cells
  touched, for the map) - in a single pass, so running both features reads each
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
- **Compiled scripts are disassembled, not shown as base64.** Two script fields
  are decoded in that popout rather than displayed raw:
  - **`bytecode`** - the compiled script (`SCDT`), rendered as named
    instructions with their operands. Without this, *any* script edit looks like
    a total rewrite, because the whole base64 blob changes.
  - **`variables`** - the script's local variable names (`SCVR`), in declaration
    order, so you can see *which* locals a mod added rather than just that the
    blob differs.

  The disassembly is deliberately honest about its limits. Morrowind's compiler
  stores expressions (the `x == 1` in an `if`) as semi-textual data rather than
  opcodes, so no table-driven disassembler can decode a whole script. Anything
  unrecognised is printed verbatim as offset/hex/ASCII and the walker resyncs on
  the next known opcode - it never desyncs and never invents an instruction. A
  `; decoded: N%` header tells you how much of the stream was understood.

  The opcode table is generated from MWEdit's `Functions.dat` (MIT). When the
  record carries its source text, that is used to suppress false positives: an
  opcode value that happens to occur inside expression data is only decoded if
  the script really calls that function.
- The **Cell map** is written to `cell_map.html` and shown in an in-app window if
  `pywebview` (best) or `tkinterweb` is installed, otherwise in your browser; the
  window has **Save HTML** / **Open in browser**.

### Cell map (which mods touch which cells)

Click **Cell Map** (after a Sort) - or pass `--cell-map out.html` - to build a
self-contained HTML page (a port of
[modmapper](https://www.nexusmods.com/morrowind/mods/53069)) with three tabs: an
**exterior-cell SVG heatmap** (uniform squares, colored by how many mods edit
that cell; hover for the mod list, click a cell to jump to its list row)
plus filterable **exterior-** and **interior-cell lists**. Cells your custom mods
touch get a gold outline, so you can see exactly where your additions land and
which areas are conflict hotspots.

Colors are **banded**, not a gradient: 1, 2, 3, 4 and 5 mods per cell each get
their own color, then 6-10, 11-15, and so on. The differences that matter are
crowded at the bottom of the range - one, two and three mods in a cell are
different situations, while 23 and 24 are not - so the legend beside the map is
its key, with one swatch per band. It writes `cell_map.html` and opens it in an
in-app window (with `pywebview`/`tkinterweb`) or your browser, changes nothing,
and works with either engine (tes3conv gives the most exact cell identification).

### The 3D terrain view

Opened from a landscape record's field diff. It draws the cell's 65x65 height
grid as a surface you can turn, which is the one view that settles "is that a
ridge or a trench" without reading numbers. Drag to rotate, shift/right-drag to
pan, scroll to zoom; **Isometric** and **Top down** are buttons because neither
angle can be hit accurately by dragging.

**Heights are drawn to the same scale as the ground.** A cell is 8,192 world
units across and heights are in world units, so a slope on screen is the slope
in game - a 45-degree hillside renders at 45 degrees. Use **Vertical** to
exaggerate deliberately when a cell is genuinely almost flat; the readout says
so while it is on.

Everything about the shading is a control:

| Control | What it does |
| --- | --- |
| **Shading** | *Relief* (hillshade with a tint over it) or *Flat facets* (one color per face, which makes the mesh itself visible). The geometry is identical either way. |
| **Hillshade** | The greyscale shading that carries the shape. |
| **Lights** | *Single*, or 3/6-way **multidirectional**: lights spread around the compass, weighted toward the sun azimuth. One light leaves faces in flat black; several fill those shadows without flattening the relief. Overall brightness does not change. |
| **Scales** | *Multiscale* blends slopes measured over three window widths - a narrow window shows texture, a wide one shows landform, and one radius has to choose. |
| **Sun azimuth / altitude** | Where the light comes from, in compass degrees and degrees above the horizon. |
| **Tint** | *Hypsometric* (green valleys to pale summits), *Rainbow*, or *Greyscale*, at any opacity from 0 to 100%. A rainbow resolves small differences far better on nearly flat ground, which is why it is offered - and why it is not the default, since it implies boundaries the terrain does not have. |
| **Contours** | Lines at a round interval chosen to put about a dozen on the cell, with the interval named in the readout. They are dropped where they would crowd close enough to merge, the way a paper map drops them. |

**Reset** restores every control, not just the camera.

---

## Merged Lands (landscape merging)

Conflict detection *tells* you two mods edited the same cell; **Merge Lands**
does something about it. It builds one `Merged Lands.esp` that carries the
combined terrain of your whole load order, so edits from different mods coexist
instead of the last one winning the whole cell, and the seams between them are
reconciled. It is a port of **Merged Lands** (David Von Derau, MIT) and its
OpenMW fork; the function-by-function account of what is ported, checked and
deliberately changed is in [MERGED_LANDS.md](MERGED_LANDS.md).

**Running it.** Click **Merge Lands** (second button row - it is a
file-producing action, not a read-only scan). It needs only a sort so it knows
the load order - the built-in reader and writer handle the terrain and the
binary encoding, so no `tes3conv` is required (it is used for the encoding when
present). It writes
`Merged Lands.esp` to your output folder and a `Merged Lands.mergedlands.toml`
marker beside it; **enable the plugin and load it LAST**. A second run ignores
its own previous output rather than merging a merge.

**How a conflict is settled.** Where two mods both move the same vertex, the
later one in the load order wins it - the same answer the engine gives, applied
per vertex so everything they did *not* both touch still merges. This is a
change from the original tool, which blended (averaged) contested vertices by
default; blending every conflict synthesises a surface neither mod authored and,
at scale, stretches the terrain, so it is now opt-in per layer (see below). The
merge then repairs the seams across every touched cell, conditions any slope too
steep for the format, and drops cells the load order already delivers.

**Per-plugin control: `.mergedlands.toml`.** A plugin can ship a sidecar named
`<plugin-stem>.mergedlands.toml` (e.g. `MyMod.mergedlands.toml` next to
`MyMod.esp` or `MyMod.omwaddon`) that controls how its edits merge. Two knobs per
landscape layer - `height_map` (heights and normals), `vertex_colors`,
`texture_indices`, `world_map_data`:

- `included = false` drops that plugin's edits to the layer entirely.
- `conflict_strategy` decides collisions: `Overwrite` (later wins - the default),
  `Ignore` (earlier wins), `Resolve` (blend the two, the original's behaviour),
  `Curvature` (a structure-weighted blend, heights only), or `Auto` (the
  default policy, which is `Overwrite`).

Not sure which strategy a cell wants? In a landscape field diff, **Compare
strategies** merges that cell under each of `Overwrite`, `Resolve`, `Ignore` and
`Curvature` and opens the 3D terrain view with each plugin's own version and each
strategy's result switchable in place - so you can see the choice on the terrain
before committing to it. (It only differs where two or more plugins contest a
vertex.) Then, rather than hand-write the sidecar, click **Merge Settings**: pick
a plugin, set each layer with a checkbox and a dropdown, watch a live preview,
and it writes the file for you (loading an existing sidecar so you edit rather
than start over).
Master files (`.esm`) are honoured too - a master builds the reference terrain
rather than being diffed, so a layer it excludes is simply left out of the
reference. Merged Lands' own advice holds: no plugin needs a sidecar until a
conflict makes one necessary.

**Choosing a strategy: winner, blend, or yield?** The thing to hold onto is that
the merge only ever decides the vertices two mods *both* moved. Everything either
mod did on its own is kept whatever you pick, so how *much* land a mod edits - its
footprint - barely touches the result; a strategy settles only the overlap, the
seam where two mods reshaped the exact same ground. That reframes the natural "the
mod with the fewest land edits should win" instinct: edit count is a proxy, not
the reason. At a contested vertex the real question is just *whose ground do you
want to stand on there*, and that is almost always the more purpose-built edit (a
road, a dock, a levelled town square, a quest-flattened clearing) over a broad
procedural sweep - which tends to correlate with a small footprint but isn't
caused by it.

So your first lever is **load order, not the sidecar.** `Overwrite` (the default)
means the later of two colliding mods wins the seam, so loading the specific mod
*after* the broad one already hands it the contested vertices - no
`.mergedlands.toml` needed. Reach for a sidecar only when load order cannot say
what you mean:

- **A detail mod over a big overhaul** (the common case): load it last, keep the
  default. It wins the shared vertices; the overhaul keeps everything else.
  Nothing to write.
- **Two overhauls fighting over one region**, both intentional, where a hard seam
  looks wrong: set the later one's `height_map` to `Resolve` to blend them, or
  `Curvature` if what you are protecting is a *structural* feature - a road cut, a
  ditch, a flat pad - that a plain average would wash out against a big bulk raise
  or lower. Blending is opt-in because it invents terrain neither mod authored, so
  use it only where a compromise is genuinely wanted.
- **A foundational mod that must keep its ground** against a later mod that only
  grazes its border: put the sidecar on the *later* mod and set the contested
  layer to `Ignore` (earlier wins), or `included = false` to drop that layer's
  edits entirely.
- **A mod whose edits to one layer you don't want at all** (it recolours the
  ground, or rewrites the world map): `included = false` on just that layer keeps
  everything else it does.

**Which mod carries the sidecar matters, too - pick the cheaper side.** A
`conflict_strategy` is a property of the plugin it is written on: the merge reads
it from whichever mod is being folded, so it fires wherever *that* mod's edits land
on ground an earlier mod already touched - every one of its seams, not the single
pair you had in mind. Change it on a mod that collides with ten others and you have
changed all ten of those overlaps; a mod that collides with two changes only two.
So when a contested region can be settled from either side, write the sidecar on
the **lower-conflict** mod - you fix the seam you meant to and perturb the fewest
other relationships. *This* is the real "smallest impact" rule: it is about a mod's
conflict count, not its land-edit count. The Plugin view makes it visible - select
a plugin and everything it conflicts with lights up, so you can see which side is
the cheaper place to intervene before writing anything.

**Let the terrain decide, not the arithmetic.** Once you have picked a side, find
the cells that actually collide - Check Conflicts, or the Plugin view, where the
mods *losing* work are marked ★ and coloured - open a landscape field diff, and
click **Compare strategies** to see that cell rendered under each of `Overwrite`,
`Resolve`, `Ignore` and `Curvature`, switchable in place on the 3D view. Pick the
one that looks right, then write it with **Merge Settings**. It is faster and surer
than counting edits, and it keeps you honest about the one place the choice
matters: the handful of vertices two mods contest.

**Reading the run.** The log names what happened - how many plugins carry
settings and which, which were skipped and why (`a previous merge`, `every layer
excluded`), how many cells merged, how far seam repair and the slope limiter
moved things, and which masters the output declares. If a cell cannot be written
it is named and skipped rather than aborting the run. Tick **Verbose Merged Lands
log** (Options) to expand that: every settings-carrying plugin is listed with
exactly what each layer is set to (on/off and the strategy), and everything that
could not be read is named in full - for working out *why* a merge did what it
did, rather than inferring it from the counts.

---

## Command-line usage

```
# Preview (default): print the plan, write nothing
python wraithguard_toolkit.py \
    --cfg openmw.cfg \
    --rules mlox_base.txt mlox_user.txt \
    --customizations momw-customizations.toml

# Durable fix: write a corrected customizations TOML (feed back into Configurator)
python wraithguard_toolkit.py --cfg openmw.cfg --rules mlox_base.txt mlox_user.txt \
    --customizations momw-customizations.toml --emit-toml momw-customizations.toml

# One-shot: scan a mods folder, use MOMW's yml, and emit a fresh TOML
python wraithguard_toolkit.py --cfg openmw.cfg --rules mlox_base.txt mlox_user.txt \
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
| `--subset-from-cfg` | Pull the cfg's own unmanaged (orphan) `content=` plugins -- those neither on the list nor in your customizations -- and sort them, in cfg order. Base masters and groundcover are skipped. `data=` paths are left as the cfg has them (they are already in the cfg's data= order, and nothing reliably tells a list-managed path from a hand-added one). |
| `--list-name` | The MOMW list name for the emitted TOML / yml features. |
| `--plugin-order-yml` | Enable curated-vs-custom split and yml warnings. |
| `--emit-toml` | Write the corrected `momw-customizations.toml` (the durable fix). |
| `--write-cfg` | Patch `openmw.cfg` in place instead/also (one-off). |
| `--sort-data-paths` | Also position `data=` folder inserts. |
| `--check-conflicts` | Scan active plugins for TES3 record-level conflicts. |
| `--conflicts-out` | Write the conflict list to a CSV (with `--check-conflicts`). |
| `--conflicts-subset-only` | Only report conflicts involving your custom mods. |
| `--tes3conv` | Path to tes3conv (preferred engine when present; field-level diffs work without it). |
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
  and the `[VER]/[SIZE]/[DESC]` functions - reading real plugin version, file
  size, and header description from your `data=` folders, with a conservative
  fallback when those files aren't reachable. `[MWSE-LUA]` is parsed but treated
  as not-applicable under OpenMW.

Beyond mlox's rule DB, the sort also enforces the two hard load-order rules that
rules alone don't cover for arbitrary custom mods:

- **Header-master dependencies + interleaving.** Each custom plugin's TES3 header
  masters are read (from your `data=` folders and the paths added this run), and
  it's placed **after** every master it declares - and *anchored* right after the
  mod it extends (its latest non-vanilla master), so a patch/addon interleaves
  next to its target. Mods that depend only on the vanilla masters have no
  positioning info and sit at the end. Falls back to rules + ESM-first if the mod
  files aren't reachable.
- **ESM-first.** Master-type plugins (`.esm`/`.omwgame`) tie-break before ordinary
  plugins, so a custom master with no rule floats up into the master block.

Both apply only to your customs; the curated list is never reordered.

Deliberately different from full mlox (by design):

- **Sorting only repositions the subset** - the curated (non-custom) `content=`
  order is frozen and never reordered. Custom mods already in the cfg *are*
  repositioned (that's the point); only the curated list is held fixed.
- `[Requires]/[Conflict]/[Note]` are **read-only warnings** - they never change
  the order or block anything. Treat them as a prompt to go check, not gospel.
- `[NearStart]/[NearEnd]` become ordering chains among the listed plugins, not a
  hard push to the file's absolute start/end.
- `data=` inserts are placed by their `after`/`before` anchor (mlox has no
  data-path order); an unfound anchor appends at the end with a warning.

---

## Advanced guide (every tool, in depth)

The walkthrough above is the happy path. This section is the power-user
reference: what each tool actually does, how the tools combine, the CLI flag
behind every GUI button, and the sharp edges worth knowing. It assumes you have
read the walkthrough and understand the core idea - the curated `content=` order
already in `openmw.cfg` is **frozen**, and this tool only decides where *your*
subset of custom plugins slots into it.

### The mental model

Three decisions define every run, in the GUI and on the command line alike:

1. **What is the subset?** The plugins this run is allowed to move. Everything
   else is frozen context that positions them but never itself reorders.
2. **What gets read for placement?** The mlox rule database, and optionally
   MOMW's `plugin-order.yml` and per-plugin headers.
3. **What gets written, if anything?** Nothing (preview), a corrected
   customizations TOML (durable), or `openmw.cfg` in place (one-off).

Get those three right and every feature below is a variation on them.

### Tool inventory

Every user-facing tool, where it lives in the GUI, the CLI flag behind it, and
whether it can change files. Everything marked read-only is safe to run on a
live setup; it computes and reports, and writes only where you point it.

| Tool | GUI | CLI | Writes? |
|---|---|---|---|
| Subset sort | `1. Sort` / `Preview` | (default) | read-only until you write |
| Write durable fix | `Write .toml` | `--emit-toml FILE` | writes a customizations TOML |
| Patch `openmw.cfg` | `Write openmw.cfg directly` | `--write-cfg` | edits the cfg (backup first) |
| Mods-folder scanner | `Scan` | `--scan-dir DIR --subset-file F` | writes the subset file |
| Pull orphans | `Pull unmanaged (orphan)` | `--subset-from-cfg` | read-only |
| Declare groundcover | `Declare as groundcover` | `--groundcover P...` | via the written output |
| Sort data= paths | `Sort data= paths too` | `--sort-data-paths` | via the written output |
| plugin-order.yml checks | `plugin-order.yml` panel | `--plugin-order-yml F --list-name N` | read-only |
| Record conflicts | `Conflicts` | `--check-conflicts` | read-only (CSV via `--conflicts-out`) |
| Field-level diffs | conflict diff viewer | built-in (`--tes3conv` optional) | read-only |
| Patch Builder | `Patch Builder...` | (GUI only) | writes a new patch plugin |
| Resource (VFS) conflicts | `Resource Conflicts` | `--resource-conflicts` | read-only (CSV via `--resources-out`) |
| Cell map | `Cell Map` | `--cell-map FILE` | writes an HTML file |
| Conflict map | `Conflict Map` | (via `--check-conflicts` + viz) | writes an HTML file |
| Lint | `Lint` | `--lint` | read-only |
| Save Check | `Save Check` | (GUI only) | read-only |
| Master check / resync | `Resync master sizes` | (part of the sort) | edits master sizes in output |
| tes3cmd frontend | `tes3cmd` | (GUI only) | drives tes3cmd (cleaning writes) |
| Merged Lands | `Merged Lands` | `tools/build_merged_lands.py` | writes `Merged Lands.esp` |
| 3D terrain view | `Show in 3D` | (GUI only) | read-only (`Export 3D file` writes) |
| Texture comparison | `Show difference` | (GUI only) | read-only (`Export comparison` writes) |
| Update rules | `Update Rules...` | (manual, or use `plox`) | downloads rule files |
| Rule maker | `New Rule...` | (GUI only) | writes your personal rules file |

### Choosing the subset - five sources, and how they combine

The subset is the heart of a run, and there are five ways to name it. They are
additive: give several and the union is sorted.

- **A customizations TOML** (`--customizations`, or the GUI's file picker) is
  the normal source - the `insert` blocks in a `momw-customizations.toml` are
  exactly the mods you added on top of a curated list.
- **An explicit list** (`--subset A.esp B.esp`) for a quick one-off.
- **A subset file** (`--subset-file`) - one plugin per line, or a minimal
  `subset = [...]` TOML. Shorter to maintain than a full customizations block.
- **The mods-folder scanner** (`--scan-dir`, GUI `Scan`) walks a mods directory,
  turns every folder that holds an asset subfolder or a plugin into a `data=`
  entry and its plugins into `content=`, and writes the result to a subset file.
  This folds in the old `mod_scan.py`; matched branches are not descended into.
- **Orphan pull** (`--subset-from-cfg`, GUI `Pull unmanaged`) reads the cfg
  itself and sorts every `content=` plugin *and* `data=` path that is on neither
  the curated list nor your customizations. The base masters and the game's Data
  Files folder are never pulled. Use this to fold hand-added mods back under
  management. (`data=` orphans are only *repositioned* with `--sort-data-paths`;
  otherwise they are listed but left where they are.)

### Writing the result - durable vs one-off

Preview writes nothing. When you do commit, there are two targets and they are
not equivalent:

- `--emit-toml` (GUI `Write .toml`) rewrites the customizations TOML with your
  plugins re-anchored, preserving every other block (`removeContent`, `replace`,
  `append`). This is the durable fix: feed it back through momw-configurator and
  the order survives the next rebuild. Set `--list-name` so the TOML names the
  curated list it belongs to - momw-configurator requires it.
- `--write-cfg` patches `openmw.cfg` in place. Immediate, but the next
  configurator rebuild overwrites it. Use it for a setup you do not rebuild, or
  to test before emitting the TOML.

A timestamped `.bak-<time>` copy is written before either overwrite, unless you
pass `--no-backup`. **Groundcover** plugins (`--groundcover`, GUI `Declare as
groundcover`) are kept out of `content=` and written as `groundcover=` instead;
their `data=` folder still goes in so OpenMW can find the file, so name that
folder in the subset as usual.

### Data-path anchoring

`--sort-data-paths` is opt-in because mlox has no concept of `data=` order, so a
plain sort never touches it. When on: an explicit `after`/`before` anchor you
wrote in the TOML always wins. For an insert with no anchor, the tool scans the
folder (non-recursively) for plugins; if it holds a plugin that is also in the
sorted `content=` order, the `data=` line is placed next to whichever existing
`data=` path owns the nearest neighbouring plugin. Every failure mode (missing
path, no plugins, plugin not in this sort) falls through to appended-at-the-end
rather than erroring.

### The rule engine and the rule maker

Rules are read in increasing priority - pass `mlox_base.txt` first and
`mlox_user.txt` (or your own file) last, exactly as mlox layers user over base.
Fidelity is covered under Rule-engine fidelity above; the advanced points:

- **`<VER>` and wildcards** (`*`, `?`) match the way mlox's own escaping does,
  so rules copied from mlox behave identically.
- **`[Order]` chains bridge missing plugins** - `[Order] A, B, C` with `B` absent
  still enforces `A` before `C`.
- **Predicate warnings** (`[Requires]`, `[Conflict]`, `[Note]`) are evaluated
  read-only against the final active list, with full `ALL`/`ANY`/`NOT`/`DESC`
  nesting and the `[VER]`/`[SIZE]`/`[DESC]` functions (which read real version,
  size and header description from your `data=` folders, falling back
  conservatively when a file is unreachable). They print; they never reorder or
  block. `--no-predicate-warnings` skips the step.
- **The rule maker** (`New Rule...`) writes to your personal rules file, with a
  picker for each rule type and a syntax guide. This is how you pin a placement
  the database gets wrong, without editing `mlox_user.txt` by hand. `Update
  Rules...` refreshes the downloaded database (or use `plox`).

### plugin-order.yml integration and the four checks

Point the tool at MOMW's `plugin-order.yml` with `--plugin-order-yml` and name
your list with `--list-name`. Curated plugins for that list are then excluded
from the sort - they are never reordered - so only your additions move, and four
read-only sanity checks run: **redundant** (a custom plugin already on the list),
**orphan** (in your cfg but on neither list nor customizations), **needs-cleaning**
(via tes3cmd), and a **base-order drift** check against the list's canonical
order. PyYAML is used if installed, otherwise a built-in parser.

### Conflict detection and the Patch Builder

`--check-conflicts` (GUI `Conflicts`) scans the active plugins for TES3
record-level conflicts - two or more plugins defining the same record, where the
last in load order wins. Two engines back it:

- The **built-in native reader** needs nothing. It parses every record type,
  gives exact record ids, and drives the **field-level diff viewer** on its own -
  it reproduces tes3conv's JSON schema in process, so tes3conv is optional.
- **tes3conv** (`--tes3conv`, or `Set tes3conv...`) is used in preference when
  present (the community's trusted converter), including for Merged Lands'
  binary encoding - though the built-in writer encodes a byte-compatible plugin
  when it is absent, so nothing here requires it. With either backend the diff
  viewer shows the side-by-side of what each plugin sets on a shared record, down
  to compiled script bytecode, landscape fields and path-grid edges.

Scope and cost controls: `--conflicts-subset-only` reports only conflicts that
involve your mods (skips base-vs-base), `--exclude 'pattern*'` drops noisy mods
(grass, light fixes, delta patches) from the scan, and `--conflicts-out` writes
the full list to CSV. Big lists can be slow; the exclude patterns are the lever.

From the diff viewer you build a **patch plugin** without ever writing to a
source mod:

- `Add record to patch` takes the winning (or any chosen) version of a
  conflicting record.
- `Merge this plugin's fields...` lets you pick a plugin and the specific fields
  it should win, composing a record from several sources.
- `Include my mods' non-conflicting records` folds in your uniquely-added
  records too, and `Highlight only conflicts that lose work` filters the list to
  the ones where a plugin's edit is actually being overridden.
- `Write patch...` emits the new plugin. It is a normal load-order member; sort
  it like anything else.

### Resource (VFS) conflicts

`--resource-conflicts` (GUI `Resource Conflicts`) is the loose-file counterpart
to record conflicts: it scans the cfg's `data=` folders for the same relative
path appearing in two or more of them, where the later folder wins - the same
thing MO2's Data tab shows. Read-only; `--resources-out` writes CSV. This is how
you catch a texture or mesh being silently shadowed even when no plugin
conflicts at all.

### Cell map and conflict map

`--cell-map FILE` writes a self-contained, modmapper-style HTML page: an
exterior-cell heatmap (brighter = more mods touch the cell) plus an
interior-cell list, with the cells your custom mods touch highlighted. The
**conflict map** is the same geography weighted by *conflict* rather than
coverage - two mods can edit one cell happily, so coverage and conflict are
different questions. Both are self-contained HTML with no CDN, openable in any
browser or the in-app viewer. `Tidy old HTML views` clears out stale generated
pages.

### Lint, Save Check, and the watchdogs

`--lint` (GUI `Lint`) runs tes3lint-style checks over the active list, VFS-aware:
evil GMSTs, the interior fog-density-0 bug, interior cells with no path-grid,
expansion-function use without the expansion mastered, `omwaddon`/`omwscripts`
twin mismatches, and blank custom headers. **Save Check** analyses an OpenMW save
against the current order to flag mods a save depends on. The **master
check / resync** verifies and can rewrite recorded master file sizes in the
output, which is what stops OpenMW's "wrong master size" load warnings.

### Merged Lands

Landscape edits from different mods overwrite each other cell by cell unless
merged. `Merged Lands` builds a `Merged Lands.esp` that combines them, with a
per-cell strategy: `Overwrite` (last wins), `Resolve` (merge non-conflicting
layers), `Ignore`, or `Curvature` (conditioned on slope). `Compare strategies`
renders each result plus each plugin's own terrain in the 3D view so you can see
the difference before committing. A per-plugin `.mergedlands.toml` pins choices,
and `Verbose Merged Lands log` names every plugin that carries settings and
exactly what each layer is set to. The CLI generator is `tools/build_merged_lands.py`.

### 3D terrain and texture comparison

`Show in 3D` opens a rotatable terrain surface with relief shading, contours and
multidirectional lighting - hand-rolled on a canvas, no library, so it works in
the frozen build. Layers include raw height, baked vertex colours (terrain
lighting), the low-res world-map heightmap, and which land texture paints each
square. `Export 3D file` saves it. `Show difference` compares two textures
side by side (a normal map is never compared against a diffuse - the tool
classifies each texture's role first); `Export comparison` saves that.

### tes3cmd cleaning - and the plugins it must never touch

`Locate tes3cmd` points the frontend at your binary; the tool then drives it for
cleaning. **The three base masters - `Morrowind.esm`, `Tribunal.esm`,
`Bloodmoon.esm` - are never cleaned**, by design (`T3_NEVER_CLEAN`); cleaning
them corrupts the game. The `needs-cleaning` warning from the plugin-order.yml
checks tells you which of *your* mods tes3cmd thinks are dirty.

### Diagnostics and reproducibility

- `--trace [FILE]` writes a debug trace (default `wraithguard_toolkit_trace.log`)
  - the first thing to enable when a run does something you cannot explain.
- `-v` shows progress on stderr, `-vv` per-item detail; the report itself always
  goes to stdout, so `-vv` never pollutes a piped result.
- `--json-dump-dir DIR` keeps the per-plugin tes3conv JSON conversions (normally
  a temp dir wiped on exit) so you can inspect exactly what the conflict engine
  read, or reuse them across runs.

### Where files land

Settings, the trace log, `cell_map.html` and the tes3conv JSON spool are written
next to the executable, falling back to a per-user data dir if that folder is not
writable (see Packaging, above). Backups sit beside the file they copy. Nothing
is written next to a frozen build's temp `__file__`, so your outputs survive an
exe rebuild.

---

## Safety

- Default is preview/dry-run - nothing is written until you say so.
- Timestamped backups are made before overwriting `openmw.cfg` or a
  customizations TOML.
- The curated list order is never modified; only your additions move.
- Customizations aren't supported by the MOMW team - you're responsible for
  making sure your changes don't cause conflicts. This tool helps you *see* and
  *place* them; it doesn't guarantee they're conflict-free.

---

## Developing

Only the standard library is needed to *run* the tool. The checks below need
`ruff`, `black`, `mypy` and `pytest`; `pip install -e .[dev]` installs them at
the exact versions these standards are measured against (see the `dev` extra in
`pyproject.toml`), so the gates don't drift as the tools add new rules.

```bash
python -m pytest                # the full suite (5,500+ tests): no network, no Tk, no real mods
                                # (the GUI smoke set skips without Tk; CI runs it under xvfb)
python -m ruff check .          # PEP 8 style, naming, import order, security, perf
python -m black --check .       # formatting
python -m mypy                  # PEP 484 types; gates all 178 shipped files
python tools/check_undefined.py wraithguard_toolkit_gui.py
python tools/check_placeholders.py   # i18n %(key)s placeholders vs their dicts
python tools/make_pot.py --check     # the .pot template must be current
```

All of these are expected to pass with zero findings (CI runs exactly this
list, plus a `python -m build` that exercises the packaging metadata, and
coverage against a `fail_under` floor). A few things worth knowing before
changing anything:

- **`tests/test_differential.py` is the safety net.** It pins 41 observations
  of the engine's behaviour - the sorted order of a real 687-plugin list, rule
  parsing, all 2,964 predicate bodies, the configurator simulation - to hashes
  in `tests/baselines/`. If a refactor changes an answer, it fails loudly
  rather than shipping a different load order. Regenerate deliberately, never
  reflexively:

  ```bash
  python -m pytest tests/test_differential.py --update-baseline
  ```

- **`tests/test_standards.py` checks PEP conformance mechanically** - the
  PEPs that define a standard for this codebase (including PEP 639 licence
  metadata and the one line of PEP 20 that can be checked: `except: pass`
  must say why).

- **`tools/check_undefined.py`** reports every name a module uses but never
  imports. A test run tells you the first one; this tells you all of them.
  It complements the linter rather than replacing it - the two miss different
  things, so run both.

- **Every shipped file meets one standard** - full annotations, PEP 257
  docstrings, no undocumented silent excepts - enforced by ruff and mypy in
  `pyproject.toml` rather than by convention. The only per-file exemptions left
  are genuinely specific ones, each with a stated reason (Tk's per-widget
  `try`/`except`, the engine's section-grouped imports). The last blanket
  exemption - `F401` on the engine, which the re-export shim had made
  unavoidable - was retired with the shim itself in 3.0.

- **The GUI has no automated coverage** (no Tk in the test environment).
  `SMOKE_TEST.md` is a scripted manual pass with log markers, so a broken
  callback shows up as a *missing log line* rather than something you have to
  notice by eye.

`CODE_REVIEW.md` records the defects found so far and - more usefully - the
reasoning behind decisions that look odd, including several linter suggestions
that were deliberately **refused** because following them would have introduced
bugs.
