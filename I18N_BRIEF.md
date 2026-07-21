# Briefing: finish the i18n string marking (the 127 f-string sites)

> **STATUS: OUTSTANDING as of 3.0.** The groundwork shipped (gettext plumbing,
> 141 marked strings, `tools/make_pot.py`, the English `.pot`). The 127
> f-string sites described below did not. Nothing here blocks the 3.0 release;
> it is the next unit of work whenever you want it.

Hand this to a fresh session. Everything below was measured, not remembered.

---

## Where the project is

`MLOXSubsetSort/` — 3.0 is docs-complete and release-ready. All gates pass with
**zero** findings. Re-run them before and after any change:

```bash
python -m pytest                       # 724 passed, 1 skipped
python -m ruff check .                 # style, naming, imports, security, BLE
python -m black --check .              # formatting
python -m mypy                         # PEP 484; gates mlox_subset/
python tools/check_undefined.py mlox_subset_sort_gui.py
python tools/make_pot.py --check       # .pot must be current
```

The one skip is `test_differential.py`'s `--update-baseline` guard. It is
*correct* to stay skipped — it exists to stop the 41 pinned behavioural
observations being silently regenerated. Do not "fix" it.

`zstandard` must be installed or 3 `test_mwscript` tests skip and you will see
721/4 instead of 724/1. That is an environment artefact, not a regression.

---

## What is already done

- `mlox_subset/i18n.py` — gettext lookup, `ngettext`, language auto-detection
  (`$MLOX_LANG` wins, then the usual gettext vars, then system locale). Exported
  as `_` from `mlox_subset`. Working and covered by `tests/test_foundation.py`.
- **141 messages marked** — every *plain string literal* in a user-facing
  position: `text=`/`title=`/`message=`/`label=` keywords, `add_tooltip()`,
  `messagebox.*`, `status_var.set()`, and single-literal `print()`.
- `tools/make_pot.py` — the extractor. Standard library only (works on Windows
  without GNU `xgettext`), AST-based (a `_("...")` inside a docstring is
  correctly *not* extracted), warns on `_(variable)` it cannot read, and has a
  `--check` mode. It is itself covered by `test_standards.py`, which globs
  `tools/**.py` — adding a file there adds 7 parametrised conformance tests.
- `locale/mlox_subset_sort.pot` — the generated English template, 141 entries.
- `locale/README.md` — translator + maintainer docs.

The imports are already in place: `from mlox_subset import _` at the top of
both `mlox_subset_sort.py` and `mlox_subset_sort_gui.py` (the GUI's carries a
documented `# noqa: E402` because it must follow the `sys.path` fix-up).

---

## The task

**127 f-string sites remain unmarked.** `_(f"Loaded {n} files")` is useless —
gettext would receive the *already-interpolated* string (`"Loaded 3 files"`),
which never matches a catalogue entry. Each must become named-placeholder form:

```python
# before
print(f"  Loaded {len(entries)} plugin entries from {path.name}")

# after
print(_("  Loaded %(count)d plugin entries from %(name)s")
      % {"count": len(entries), "name": path.name})
```

Named placeholders, never positional `%s` — translators reorder words, and
`%s` cannot be reordered. This is already stated in `locale/README.md` and in
`i18n.py`'s module docstring.

### Measured distribution

| Where | Sites |
|---|---|
| `mlox_subset_sort.py` (CLI/report output) | 60 |
| `mlox_subset_sort_gui.py` | 54 |
| `mlox_subset/configurator/datapaths.py` | 4 |
| `mlox_subset/rules/parser.py` | 3 |
| `mlox_subset/sort/engine.py` | 3 |
| `mlox_subset/configurator/emit.py` | 2 |
| `mlox_subset/rules/expressions.py` | 1 |
| **Total** | **127** |

Interpolated fields per string: **83** have one, 27 have two, 10 have three,
7 have four.

**Good news, both verified:** there are **zero** format specs (`{x:.2f}`) and
**zero** conversions (`{x!r}`) among them. So no `%`-format spec translation is
needed — every field is a plain substitution. That removes the single most
error-prone part of this job.

By field complexity: **73** interpolate only a bare name/attribute/index
(`{name}`, `{p.stem}`) and are near-mechanical. **54** interpolate a call or a
ternary (`{len(missing)}`, `{Path(path).name}`, `{'s' if n != 1 else ''}`) and
need the expression lifted into the dict by hand.

### Plurals

84 strings in these two files contain a literal `(s)` — "3 backup(s)",
"1 plugin(s)". Those are exactly what `ngettext` is for:

```python
print(ngettext("Restored %(count)d backup", "Restored %(count)d backups", n)
      % {"count": n})
```

Do **not** mechanically preserve `(s)`. Languages have 1–6 plural forms and
`(s)` is untranslatable in most of them. `ngettext` is already exported from
`mlox_subset` and `tools/make_pot.py` already extracts it as `msgid_plural`
(verified against a synthetic file). Use judgement: a string only needs
`ngettext` if a count actually drives its grammar.

---

## The trap that will bite you, and the fix I recommend

A mistyped placeholder key is a **runtime `KeyError`**:

```python
_("Loaded %(count)d files") % {"cont": n}     # KeyError: 'count'
```

Nothing currently catches this. Coverage is uneven and you should know exactly
how uneven:

- **CLI/engine (60 + 13 sites):** partially covered. 24 `capsys`/`capfd`
  assertions across `test_configurator.py`, `test_rule_maker.py`,
  `test_rule_parser.py` and `test_sort.py` assert on *substrings* of printed
  output (e.g. `assert "matches 2 openmw.cfg lines" in out`). Those will catch a
  botched conversion of those specific messages — but most print sites are not
  asserted. Note the differential baseline pins **data structures**, not printed
  text, so it will *not* catch report-wording breakage.
- **GUI (54 sites):** no coverage at all. There is no Tk in the test
  environment. A `KeyError` here surfaces only when a user clicks the thing.

**Strongly recommended first step:** add a static check —
`tools/check_placeholders.py`, in the same AST style as `check_undefined.py` —
that finds every `_( "..." ) % {...}` / `ngettext(...) % {...}` and asserts the
`%(name)s` keys in the format string exactly match the dict literal's keys.
Report both directions (missing key, unused key). Wire it into the gate list
and `test_standards.py`. That converts the whole class of error from
"user finds it" to "linter finds it", and makes the remaining 127 conversions
safe to do quickly. Build this *before* converting, not after.

---

## Other constraints and traps

- **`_` is the gettext marker now, so it can no longer be a throwaway name.**
  This already caused one real bug: `for line, is_new, _ in data_result:` in
  `compute_plan` bound `_` as a function-local and would have made every `_()`
  call earlier in that 540-line function raise `NameError`. Ruff's **`F823`**
  caught it. Two sites were renamed (`_anchor`, `_is_new`).
  A grep for `for _ in` will **not** find these — `_` is often the third element
  of a tuple target. Use AST, or trust `F823`, which is enabled and reliable.
  Comprehension targets (`[x for x, _ in pairs]`) are safe: separate scope in
  Python 3.
- **Do not translate data.** Plugin names, file paths, mlox rule keywords
  (`[Order]`, `[Conflict]`, `content=`, `data=`), `openmw.cfg` and TOML keys are
  data, not prose. Leave them outside the marked string. The same goes for the
  `[SIZE]`/`[DESC]`/`[VER]` function names and trace/log markers like
  `[smoke]` and `[theme]` that the smoke test greps for.
- **`tools/make_pot.py` excludes `mlox_subset/i18n.py`** by design — that module
  *implements* `gettext`/`ngettext`, so its internal delegating calls are not
  markers. Without the exclusion it emits two permanent false warnings.
- **Do not change `T3_NEVER_CLEAN`** or anything in the tes3cmd path.
  Safety-critical: Morrowind.esm / Tribunal.esm / Bloodmoon.esm must never be
  cleaned. Unrelated to i18n.
- **Re-run `python tools/make_pot.py`** after any marking change, and commit the
  regenerated `.pot`. `--check` fails the gate if you forget.

---

## Suggested order

1. Build `tools/check_placeholders.py` and wire it into the gates. Prove it on a
   deliberately broken example (negative control) before trusting it.
2. Convert `mlox_subset/` (13 sites). Smallest surface, strictest lint rules,
   fully test-covered — a good shakedown of the pattern.
3. Convert `mlox_subset_sort.py` (60 sites). Run the 24 `capsys` assertions
   continuously; they are your early warning.
4. Convert `mlox_subset_sort_gui.py` (54 sites). No test coverage — this is
   where the static check earns its keep. Consider adding `[smoke]` markers and
   a SMOKE_TEST section so the user can exercise the dialogs and status lines.
5. Regenerate the `.pot`, confirm the message count jumped by roughly 127, and
   spot-read the template for anything that reads like data rather than prose.
6. Optional: produce a real `de`/`fr` catalogue with a handful of strings and
   run with `MLOX_LANG=de` to prove the whole pipeline end-to-end. Right now
   nothing has ever exercised a non-null catalogue at runtime.

Steps 1–3 are verifiable by the suite and make a clean, reviewable change.
Step 4 is the one that wants a smoke test before it is called done.

---

## Recent context worth knowing

- 3.0 ships two headline changes: the `mlox_subset/` package split, and
  whole-GUI live theming. Both are done and smoke-tested.
- `BLE001` is enforced: 68 `except Exception` sites reviewed, 28 narrowed, 40
  kept broad with `# noqa: BLE001` and a stated reason. If you add a broad
  catch, expect to justify it. Rationale in `CODE_REVIEW.md` §13.
- The compiled `.exe` is built with auto-py-to-exe from
  `build/build subset sort.json`. A stale build cost two debugging rounds; the
  Log panel's first line is now a build stamp (`MLOX Subset Sort 3.0.0 --
  frozen=True built=...`). Check it before believing any exe-only symptom.
- Dead API in this codebase has now signalled a real bug **three** times
  (`PluginFileIndex.usable`, and the `_` shadowing above found the moment the
  marker was introduced). Treat an unused accessor as a lead, not as tidy-up.
