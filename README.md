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

Install optional deps on an externally-managed Python with
`pip install ... --break-system-packages` if needed.

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

**Actions:**

1. **Sort** — runs mlox and fills the order panels. Never writes anything; always
   safe. Highlighted rows are your custom additions.
2. **Export** — writes `openmw.cfg` and/or the corrected TOML using whatever
   order the panels currently show. (Uncheck *Dry run* to actually write.)

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
| `--no-predicate-warnings` | Skip `[Conflict]/[Requires]/[Note]` evaluation. |
| `--no-backup` | Skip timestamped `.bak` copies. |
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

Deliberately different from full mlox (by design):

- **Sorting only repositions the subset** — the existing `content=` order is
  frozen and never reordered.
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
