# Quick Start

Get from "I added some custom mods" to "a corrected `momw-customizations.toml`"
in about five minutes. For the full reference, see [README.md](README.md).

## What you need

- Your **`openmw.cfg`** (the one MOMW Configurator generated).
- mlox rules: **`mlox_base.txt`** and (optionally) **`mlox_user.txt`**.
- Optional but recommended: MOMW's **`plugin-order.yml`** and your list's name
  (e.g. `total-overhaul`).
- Python 3.8+ with tkinter. On Linux: `sudo apt install python3-tk`.

---

## The GUI in 6 steps

Launch it:

```
python mlox_subset_sort_gui.py
```

1. **openmw.cfg** — Browse to your `openmw.cfg`.
2. **Rule files** — Add `mlox_base.txt`, then `mlox_user.txt` (base first).
3. **Get your subset** — either:
   - Browse to an existing `momw-customizations.toml` (**customizations.toml**
     field) or a subset text file, **or**
   - click **Scan...** next to *subset file* and pick your `custom` mods folder
     to generate the list automatically.
4. *(Recommended)* Set **list name** (e.g. `total-overhaul`) and point
   **plugin-order.yml** at MOMW's file. Now the tool tells your curated list
   apart from your true additions and won't touch the curated order.
5. **emit corrected TOML to** — choose where to save the result (a new
   `.toml`), then tick **Sort data= paths too** if your mods add asset folders.
6. Click **1. Sort**, look over the panels and log, then **2. Export**.
   - *Export writes nothing while **Dry run** is checked* (it's on by default).
     Uncheck it when you're happy, then Export for real.

### While reviewing (optional)

- **Reorder**: drag rows, or select + **Move Up/Move Down** (multi-select with
  Ctrl/Cmd- and Shift-click).
- **Opt out**: select row(s) and click **Disable / Enable** (or double-click) to
  leave mods out — handy when not everything you scanned needs to load.
- **Read the log colours**: green = inserted/moved by this sort, orange =
  warnings and rules your cfg order overrode, red = errors.

### Then apply it

Feed the emitted `momw-customizations.toml` back into MOMW Configurator (put it
next to your `openmw.cfg` and re-run the Configurator). Your custom mods now sort
into place on every rebuild, and the curated list stays untouched.

---

## The one-liner (CLI)

Scan a mods folder, use MOMW's yml, and write a corrected TOML in one go:

```
python mlox_subset_sort.py \
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
- **Your curated MOMW order is never reordered** — only your additions move.
- Customizations aren't supported by the MOMW team; this tool helps you place
  and inspect them, not guarantee they're conflict-free.
