# Briefing: task #43 — apply theming to the whole GUI

> **STATUS: COMPLETED in 3.0.** Kept as a record of the problem statement and
> the measurements it was planned from. All six steps shipped, plus two things
> this brief did not anticipate: the ttk base theme has to be a colour-capable
> one (`clam`/`alt`/`default`/`classic`) or a frozen `.exe` silently ignores
> every colour, and an already-open field-diff viewer needs its syntax *tags*
> re-configured, not just its chrome. See `CHANGELOG.md` 3.0 and `SMOKE_TEST.md`
> §5. For work still outstanding, see `I18N_BRIEF.md`.
>
> Test counts below are from when this was written (717); the suite is now 724.

Hand this to a fresh session. Everything below was measured, not remembered.

---

## Where the project is

`MLOXSubsetSort/` — sorts a user's own Morrowind mods into a frozen MOMW load
order without ever reordering the curated part.

All gates currently pass with **zero** findings. Re-run them before and after
any change:

```bash
python -m pytest                # 717 tests, 1 skipped
python -m ruff check .          # PEP 8 style, naming, import order, security
python -m black --check .       # formatting
python -m mypy                  # PEP 484; gates mlox_subset/
python tools/check_undefined.py mlox_subset_sort_gui.py
```

Read `CODE_REVIEW.md` §11–15 for why several things look the way they do,
including linter suggestions that were deliberately **refused**.

---

## The task

The theme picker (Log panel → **Theme:** dropdown) currently restyles only the
**log panel** and, at construction time, the **field-diff popups**. Everything
else — buttons, frames, lists, tabs, entries — is hardcoded. Make the selected
theme apply to the whole GUI, and make it work when switched at runtime.

---

## What actually exists (measured)

### Two colour systems, currently disconnected

| | `DARK` | `THEME_PRESETS` |
|---|---|---|
| Purpose | app chrome | syntax highlighting |
| Keys | `accent, bg, bg2, border, btn_bg, btn_bg_active, fg, fg_dim, field_bg, select` (10) | `background, foreground, select, dim, error, warn, ok, key, keyword, string, number, punct, section, tag, attr, inserted` (16) |
| User-switchable | **no** — hardcoded | yes: 5 built-ins + imported JSON |
| Referenced at | **106 sites** as `DARK[...]` | `_apply_log_theme`, diff popups |

**The core of the task is bridging these.** A theme currently has no chrome
colours at all, so the 16 highlight keys must either be extended with chrome
keys, or chrome must be derived from them (`background`/`foreground`/`select`
map onto `bg`/`fg`/`select` fairly naturally; `btn_bg`, `border`, `accent`,
`fg_dim` do not and need deriving or defaulting).

Decide that mapping **first** — it determines everything else. Imported custom
themes only carry the 16 highlight keys, so whatever you choose must degrade
sensibly when chrome keys are absent.

### Widget inventory — this settles the "Style vs per-widget" question

I initially framed this as a big architectural choice. It is not; my first
count was wrong (`grep "tk.Button("` also matches `ttk.Button(`). Corrected:

| ttk (reachable by `ttk.Style`) | | classic tk (**not** reachable) | |
|---|---|---|---|
| `ttk.Button` | 49 | `tk.Toplevel` | 9 |
| `ttk.Frame` | 39 | `tk.Listbox` | 2 |
| `ttk.Label` | 26 | `tk.Text` | 2 |
| `ttk.Checkbutton` | 11 | `tk.Canvas` | 1 |
| `ttk.Entry` | 10 | `tk.Label` | 1 |
| `ttk.Scrollbar` | 9 | `scrolledtext.ScrolledText` | 2 |
| `ttk.LabelFrame` | 9 | | |
| `ttk.Treeview` | 3 | | |
| `ttk.Radiobutton` | 2 | | |
| `ttk.Combobox`, `ttk.Notebook` | 1 each | | |
| **~160 total** | | **~17 total** | |

**So: `ttk.Style` handles ~90%, and the classic set is small and enumerable.**
Not an either/or. There are already 24 `style.configure`/`style.map` calls to
extend, and `style_plain_widget()` (line ~321) already exists for the classic
widgets — it just reads `DARK` directly and is only called at construction.

### The real difficulty: runtime re-application

Nothing re-themes **already-open** windows. `_apply_log_theme` touches
`self.log_text` and nothing else; there is no `winfo_children()` walk anywhere.

So switching theme today leaves every open window on the old colours. Whatever
you build needs a re-apply pass over live widgets — most likely a recursive
walk from `self.root` plus each tracked `Toplevel`, dispatching on widget class.
The 9 `tk.Toplevel` windows need `.configure(bg=...)` directly; they are plain
windows, not ttk.

---

## Constraints and traps

- **`RUF012`.** Colour dicts as class attributes must be `ClassVar[dict[str, str]]`
  or ruff fails. Several already are.
- **`PERF203` is exempted for the GUI**, on purpose: per-widget `try`/`except
  tk.TclError` is *required*, because not every widget supports every option
  and a destroyed widget raises. Follow `style_plain_widget()`'s pattern — it
  applies options one at a time for exactly this reason. Do **not** hoist the
  try out of the loop; one unsupported option must not blank a whole panel.
- **`tk.Toplevel` windows are created and destroyed dynamically.** A re-apply
  pass must tolerate widgets that have gone away (`winfo_exists()`).
- **Do not change `T3_NEVER_CLEAN`** or anything in the tes3cmd path. Unrelated
  to theming, and safety-critical: Morrowind.esm / Tribunal.esm / Bloodmoon.esm
  must never be cleaned.

---

## No automated coverage for any of this

There is no Tk in the test environment. `python -m pytest` will **not** catch a
theming regression. What you can still do:

1. `python -m ruff check .`, `black --check`, `mypy`, and
   `tools/check_undefined.py` all work on the GUI file.
2. `python -c "import ast; ast.parse(open('mlox_subset_sort_gui.py').read())"`.
3. Extend `SMOKE_TEST.md`. It already uses `trace_first_fire()` markers so a
   dead callback shows up as a **missing log line** rather than something the
   user has to spot by eye. Add markers for the theme re-apply path and give
   the user a step list; they run it with `--trace` and send the log back.

That last point is the pattern that has worked here: instrument first, then ask
for a run, then read the log. Do not ask for a smoke test of code that emits
nothing.

---

## Suggested order

1. Decide the `DARK` ↔ `THEME_PRESETS` mapping, including the fallback for
   imported themes that lack chrome keys. **Agree this before writing code.**
2. Make `DARK` derive from the active theme rather than being a module
   constant — that alone reaches the 106 existing call sites.
3. Extend the `ttk.Style` configuration to cover every ttk widget class in use.
4. Rework `style_plain_widget()` to take a theme instead of reading `DARK`.
5. Add the recursive re-apply pass; wire it into `_apply_log_theme`.
6. Add smoke-test markers and hand the user a step list.

Suggest doing 1–2 and stopping for a smoke test before 3–6. The GUI is ~4,900
lines with no test coverage; a single reviewable, verifiable change beats a
large one nobody can check.

---

## Recent context worth knowing

- The engine was split into `mlox_subset/` (7 subpackages). `mlox_subset_sort.py`
  is now a re-export shim; `F401`/`E402` are exempted there deliberately.
- `tests/test_differential.py` pins 41 behavioural observations against a real
  687-plugin order. **Theming should not touch it** — if it fails, something
  went badly wrong.
- Last bug found: `[SIZE]`/`[DESC]` rules asserted matches for plugins not on
  disk, because `PluginFileIndex.usable` existed but was never used. Fixed with
  a negative-controlled regression test. Mentioned only as a reminder that dead
  API in this codebase has twice signalled a real bug.
