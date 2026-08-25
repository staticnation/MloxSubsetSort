# Quick Start

Get from "I added some custom mods" to "a corrected `momw-customizations.toml`"
in about five minutes. For the full reference, see [README.md](README.md).

## What you need

- Your **`openmw.cfg`** (the one MOMW Configurator generated).
- mlox rules: **`mlox_base.txt`** and (optionally) **`mlox_user.txt`**.
- Optional but recommended: MOMW's **`plugin-order.yml`** and your list's name
  (e.g. `total-overhaul`).
- Python 3.10+ with tkinter. On Linux: `sudo apt install python3-tk`.

---

## The GUI in 6 steps

Launch it:

```
python wraithguard_toolkit_gui.py
```

1. **openmw.cfg** - Browse to your `openmw.cfg`.
2. **Rule files** - Add `mlox_base.txt`, then `mlox_user.txt` (base first).
3. **Get your subset** - either:
   - Browse to an existing `momw-customizations.toml` (**customizations.toml**
     field) or a subset text file, **or**
   - click **Scan...** next to *subset file* and pick your `custom` mods folder
     to generate the list automatically. If the scan misses a mod with a
     non-standard layout (an OpenMW Lua mod with no `meshes`/`sound`/... folder
     and no plugin), add it by hand: **Add data folder...** / **Add plugin...**
     under the lists on the left, or drag the folder/plugin onto them.
4. *(Recommended)* Set **list name** (e.g. `total-overhaul`) and point
   **plugin-order.yml** at MOMW's file. Now the tool tells your curated list
   apart from your true additions and won't touch the curated order.
5. **emit corrected TOML to** - choose where to save the result (a new
   `.toml`), then tick **Sort data= paths too** if your mods add asset folders.
6. Click **1. Sort**, look over the panels and log, then **2. Export**.
   - *Export writes nothing while **Dry run** is checked* (it's on by default).
     Uncheck it when you're happy, then Export for real.

### While reviewing (optional)

- **Reorder**: drag rows, or select + **Move Up/Move Down** (multi-select with
  Ctrl/Cmd- and Shift-click).
- **Opt out**: select row(s) and click **Disable / Enable** (or double-click) to
  leave mods out - handy when not everything you scanned needs to load.
- **Read the log colors**: green = inserted/moved by this sort, orange =
  warnings and rules your cfg order overrode, red = errors.
- **Check conflicts**: click **Check Conflicts** to scan for TES3 record-level
  conflicts (two plugins editing the same record; last one wins). Results show
  in the log and a dedicated window - ones involving your mods are marked ★.
  For a **field-by-field diff**, point it at a `tes3conv` binary (**Set
  tes3conv...** in that window, or `--tes3conv`); then selecting a record shows
  each plugin's values with differing fields in red.
- **Plugin view**: after a conflict scan, click **Plugin view** for your load
  order as a tree - open a plugin to see what it changes and a record to compare
  it across every plugin. The colours tell you which of your mods are *losing*
  work; they fill in on their own (a background pass judges the order once). You
  can also build a patch from here: the same **Add record / Merge field / Define
  value / Patch Builder** buttons as the conflict window, plus **Merge this
  plugin's fields...** to take chosen fields from one plugin across every record
  it defines.
- **Merge Lands**: build one `Merged Lands.esp` that combines the landscape edits
  of your whole load order and closes the seams between them, instead of the last
  mod winning a whole cell. Needs a `tes3conv` binary; enable the output and load
  it **last**. By default the later mod wins only the vertices two mods *contest*,
  and everything else merges - so most load orders need no tuning. When a specific
  seam looks wrong, a `.mergedlands.toml` sidecar overrides it per plugin and per
  layer (winner / blend / yield / drop). Don't guess: open a landscape field diff,
  click **Compare strategies** to see the cell under each option on the 3D view,
  then write the sidecar with **Merge Settings**. The README's *Merged Lands*
  section walks through choosing a strategy - winner vs. blend vs. smallest-impact
  and when each fits.
- **Cell map**: click **Cell Map** for a modmapper-style SVG heatmap of which
  mods touch which exterior/interior cells (click a cell to jump to its list row).
  The map is written to a timestamped `cell_map` file and shown in an in-app window if
  `pywebview` (best) or `tkinterweb` is installed, otherwise your browser - it is
  never rendered from an in-memory string, so big load orders won't OOM.
- **Big load orders / memory / speed**: conflict + cell-map scans run tes3conv to
  disk, reading one plugin at a time (bounded memory) instead of holding every
  plugin's records in RAM. The first scan also caches a tiny per-plugin sidecar,
  so **repeat Check Conflicts and Cell Map runs are near-instant** (a mod is only
  re-read if it changed). Tick **Keep tes3conv JSON dump** (Options) to keep the
  `tes3conv_json` folder (and its caches) between launches; leave it off to remove
  it on close. (CLI: `--json-dump-dir FOLDER`.)

### Then apply it

Feed the emitted `momw-customizations.toml` back into MOMW Configurator (put it
next to your `openmw.cfg` and re-run the Configurator). Your custom mods now sort
into place on every rebuild, and the curated list stays untouched.

---

## The one-liner (CLI)

Scan a mods folder, use MOMW's yml, and write a corrected TOML in one go:

```
python wraithguard_toolkit.py \
    --cfg openmw.cfg \
    --rules mlox_base.txt mlox_user.txt \
    --scan-dir "E:\OpenMW\Mods\custom" --subset-file mod_scan_results.txt \
    --plugin-order-yml plugin-order.yml --list-name total-overhaul \
    --sort-data-paths --emit-toml momw-customizations.toml
```

Drop `--emit-toml` (or run without it) to just preview the plan and write
nothing. A timestamped `.bak` is made before anything is overwritten.

---

## Golden rules

- **Nothing is written until you say so** (Dry run is on; the CLI previews by
  default).
- **Your curated MOMW order is never reordered** - only your additions move.
- Customizations aren't supported by the MOMW team; this tool helps you place
  and inspect them, not guarantee they're conflict-free.
