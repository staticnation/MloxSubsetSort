# GUI smoke test

The GUI has no automated coverage — there is no Tk in the test environment —
and five callbacks were rewritten during the PEP conformance pass. This script
exercises exactly those, and only those.

**Why a script rather than "click around":** the rewrites changed
`command=lambda: self.m()` into `command=self.m`. If one of those is wrong, the
callback simply never runs. Nothing errors, nothing looks broken on screen — the
button just quietly does nothing. So each step below has a **log line that must
appear**; a missing line is the failure signal, not a visible glitch.

---

## 1. Turn tracing on

Tracing is **off by default**. Either works:

```powershell
python mlox_subset_sort_gui.py --trace
```

or set `MLOX_SUBSET_TRACE=1` before launching. Both write to:

```
mlox_subset_sort_trace.log      (next to the app)
```

Delete any existing log first so the run is clean — the file is truncated per
session anyway, but starting from nothing removes all doubt.

Confirm the header appears before going further:

```
GUI started
viewers: frozen=... pywebview=... 
```

If that line is missing, tracing did not start and nothing below will be
recorded. Stop and check the flag.

---

## 2. The five steps

Do them in order. After each, the named line should be in the log.

| # | Action | Log line to expect |
|---|---|---|
| 1 | Load a cfg and **drag a plugin** to a new position in the main plugin list | `[smoke] callback fired for the first time: listbox drag-reorder -> on_reorder`<br>`[smoke] drag-reorder committed: N row(s) now listed` |
| 2 | Same list — **drag a multi-row selection** (select 3+ contiguous rows, drag the block) | `[smoke] drag-reorder committed:` again, with the same N |
| 3 | Open **MLOX user rules maker** → click **Browse…** → pick a filename, then repeat and **Cancel** | `[smoke] callback fired for the first time: rules-maker Browse...`<br>`[smoke] rules-maker Browse: chose <path>` then `[smoke] rules-maker Browse: cancelled` |
| 4 | In the rules maker, click each **rule-type radio** ([Order] / [NearStart] / [NearEnd]) and drag a row in its plugin list | `[smoke] callback fired for the first time: rules-maker refresh (radio / reorder)` |
| 5 | Open the **tes3cmd** window, add some plugins, select one or more, click **Remove selected** | `[smoke] callback fired for the first time: tes3cmd Remove selected`<br>`[smoke] tes3cmd Remove selected: N -> M file(s)` |

Also expect, early on and once only:

```
[smoke] callback fired for the first time: plugin list restyle
```

---

## 3. What each result means

**All lines present** → every rewritten binding is live. The rewrites are good.

**A "first time" line missing** → that callback never fired. This is the exact
failure the rewrite risked: the binding is broken and the control is inert.
Tell me which label is missing; it maps to one specific line I changed.

**Line present but the UI misbehaved** → the binding works and the bug is in the
logic, not the wiring. Note what you saw versus expected.

**`drag-reorder committed` shows a changed row count** → rows were lost or
duplicated by the drag. That would be a real bug; send the two counts.

---

## 4. Send me

- `mlox_subset_sort_trace.log`
- `mlox_subset_sort_sort_trace.log` (written next to it, if you ran a sort)
- Which of the five steps you did, and anything that looked wrong on screen

Screenshots are useful for visual problems but the log is what identifies
*which* rewrite is at fault.

---

## 5. Theming (task #43 — ships in 3.0)

The theme picker (Log panel → Theme:) now drives the whole GUI: chrome
(window, buttons, frames, tabs, entries, lists, scrollbars, tooltips, grips,
console-style panes) plus the existing syntax highlighting, and switching
re-themes **every open window immediately**.

| # | Action | What to expect |
|---|---|---|
| 1 | Launch with `--trace`, theme on **Dark (default)** | Log lines: `[smoke] callback fired for the first time: theme -> chrome palette update (set_active_chrome)`, `[smoke] callback fired for the first time: theme runtime re-apply walk (restyle_widget_tree)`, `[theme] chrome palette now follows: Dark (default)`, and `[theme] re-applied chrome to N plain-tk widgets`. N counts only the widgets the walk must fix by hand — Toplevels, lists, text panes, canvases, scrollbars, the combobox — so expect roughly **10–15** for the bare main window, more with extra windows open; the ~160 ttk widgets are restyled wholesale by `ttk.Style` and aren't counted. The GUI must look **visually identical** to 2.3 — the only intentional deltas are invisible ones: selected-tab text `#ffffff`→`#e6e6e6` and disabled-row text off by 1/255. |
| 2 | Open the **tes3cmd** window and leave it open, then switch the Theme dropdown to **Dracula** | New `[theme] chrome palette now follows: Dracula` and `[theme] re-applied chrome to N plain-tk widgets` lines, with N higher than in step 1 (the open window's widgets join the walk). **Both** windows — main and tes3cmd — recolour immediately: buttons, frames, tabs, lists, log, everything. No stale dark-grey islands. |
| 3 | Still on Dracula: hover a button (tooltip), open the Theme dropdown itself, drag a pane grip | Tooltip, the dropdown's list, and the grip are all in Dracula colours — these are the three widgets ttk theming can't reach and the walk handles specially. |
| 4 | Load a cfg and sort, so the plugin list has coloured rows; switch themes again | Normal rows follow the new field colour; amber "touched", purple "problem" and red-highlight rows keep their semantic colours (deliberate — see the comment on `ReorderPanel`). |
| 5 | Close the app (saves settings), relaunch with `--trace` | The whole GUI comes up in Dracula from the first frame — no dark flash, no half-themed panels. `[theme] chrome palette now follows: Dracula` appears at startup. |
| 6 | Briefly repeat 2 with **Monokai**, **Atom One Dark**, **Gruvbox Dark** | Everything readable; no black-on-black or white-on-white anywhere. |
| 6a | Spot-check a few of the 18 themes added in the 3.0 theme pack — at least **Tokyo Night**, **Nord**, **GitHub Dark**, **SynthWave '84**, **Cobalt2** (the extremes: darkest, coolest, highest-saturation) | Same bar as step 6: chrome follows the scheme (not default grey), all six log roles distinguishable, tooltips/dropdown/grips themed. Each new theme ships hand-filled chrome from its published UI palette, so none of them should fall back to derived colours. |
| 7 | (Optional) **Import Theme…** a base16 `.yaml` scheme and repeat 2 | Same behaviour; chrome comes from the scheme's base00–base04 UI slots. There is also `theme_template.json` next to the app — a commented native-format starting point. |
| 8 | Open a field diff (**Check Conflicts** → double-click a field), leave it open, switch themes | The diff window's **syntax token colours** (keys/strings/tags) change with the theme, not just its background — this was a real bug found in smoke testing. |

**A `[theme]` line missing after a dropdown switch** → the combobox binding is
broken; that is the failure signal.

**`re-applied chrome to N` present but a widget stayed on the old colours** →
the walk misses that widget class. Say exactly which widget and where.

**Wrong or unreadable colours** (with the lines present) → the wiring works
and the chrome *mapping* is at fault. Say which theme and which widget.

### 5a. Compiled .exe: check you're running the build you think you are

**Do this before investigating anything else.** A stale `.exe` presents exactly
like a theming bug — new source on disk, old behaviour on screen — and it has
already cost two debugging rounds here.

**No flag needed:** the first line in the **Log panel** on every start is now

```
MLOX Subset Sort 3.0.0 -- frozen=True built=2026-07-20 17:20:11 path=mlox_subset_sort.exe
```

Read it off the screen. If it doesn't say `3.0.0`, or `built=` predates your
last source edit, you are looking at an old build and nothing below applies.

> **Where the exe's trace file actually is.** For a frozen build,
> `app_base_dir()` resolves to the folder **containing the .exe** — typically
> `output\`. It is *not* the `mlox_subset_sort_trace.log` sitting in the source
> tree; that one is left over from running the `.py`. Collecting the wrong copy
> looks exactly like "the new code didn't run". The `[trace] writing debug
> trace to: …` line in the Log panel now prints the absolute path — use that.

With `--trace` on, the same stamp is also written to the log:

```
build: version=3.0.0 frozen=True built=2026-07-20 17:20:11 path=mlox_subset_sort.exe
```

- `version=` below `3.0.0`, or a `built=` timestamp older than your last edit
  to `mlox_subset_sort_gui.py` → **the exe predates the change. Rebuild.**
- The three `[theme] …` lines below are also unconditional on every startup.
  If the log has `GUI started` but **no `[theme]` lines at all**, the build does
  not contain the theming code, regardless of what the source says.

`clean_build` is on in `build/auto-py-to-exe_build.json`, so PyInstaller should
not reuse a cached analysis. If a rebuild still shows an old stamp, the Script
Location is pointing at a different copy of the source — check that field before
blaming the cache. (That was the real cause here: a stale tree on the Desktop.)

### 5b. Compiled .exe: the ttk base theme must load

ttk widgets (buttons, frames, tabs, entries) only obey our colours when a
*colour-capable* base theme is active — `clam`, `alt`, `default` or `classic`.
The Windows-native themes (`vista`/`xpnative`/`winnative`) draw with the OS
renderer and **silently ignore** colour options. Plain-tk widgets (the log,
lists, text panes) are coloured directly and are unaffected either way — which
is why a broken build shows the classic signature: **the log recolours but the
main window stays default grey.**

Every launch now traces which base theme it got. In the log, near the top:

```
[theme] ttk base theme: using 'clam'
```

If instead you see:

```
[theme] WARNING: no colour-capable ttk theme available (have [...]);
        staying on 'vista' -- ttk widget colours will NOT apply. If this is a
        frozen .exe, the Tcl/tk ttk theme files were not bundled.
```

then the build didn't ship Tk's ttk theme files, and no runtime code can fix
it — confirm PyInstaller bundled the `tcl/` + `tk/` library trees (they include
`tk/ttk/*.tcl`). A standard tkinter build does this automatically, so this
warning should be rare.

**If you see *neither* line, go back to 5a — the build is stale.** That has
been the actual cause every time so far, and it is worth exhausting before
suspecting anything about Tk.

Re-run the section 5 steps against the rebuilt exe: the main window and every
open window should now re-theme on switch exactly as they do from source.

---

## Note on the instrumentation

`trace_first_fire()` records each callback **once per session**, deliberately.
`_restyle` and `_rm_refresh` run on every re-render; logging each call would
bury the trace in noise and make the rest of it unreadable. One line proves the
binding is live, which is the only question here.

These lines are cheap and harmless to keep — they are inert unless `--trace` is
on — but if you would rather they went once the GUI is verified, say so and I
will strip them.

---

## 6. The GUI split (3.0 — §16 pass): re-verify the moved windows

Roughly 2,600 lines moved out of `mlox_subset_sort_gui.py` into
`mlox_subset/gui/` (theming, widgets, and the tes3cmd + conflict windows as
mixins). Bodies are verbatim and every name is re-imported, so **nothing
above should behave differently** — but the moved code is exactly the code
sections 2 and 5 exercise, so a re-run of both is the verification:

| # | What | Why it covers the split |
|---|---|---|
| 1 | Section 2, steps 1–5 | Drag-reorder (widgets.py), rules maker, and **step 5 is the tes3cmd window** (t3.py) |
| 2 | Section 5, steps 1–8 | The whole theming path now lives in theme.py; step 8's field diff is conflicts.py |
| 3 | **Check Conflicts** and **Resource conflicts**, save a CSV from each window | conflicts.py's workers, windows and exports |
| 4 | Confirm the settings file, trace log and `cell_map.html` land in the same folder as before | `app_base_dir()` moved to the package and now derives the source-run folder from its own location |

Also new in this pass, worth 30 seconds from a terminal:

```
python mlox_subset_sort.py --cfg <your cfg> --rules mlox_base.txt -v
```

`-v` is new: diagnostics (WARNING and worse always; `-v` adds progress) now
print to **stderr** with a level prefix, while the report stays on stdout.
Piping stdout to a file should capture a clean report with no log lines in it.

If any window above misbehaves, the first suspect is an import the static
checks could not see (a name resolved dynamically). Say which window and
which action; each maps to one module.
