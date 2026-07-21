# Translation catalogues

Translatable strings are marked with `_()` (see `mlox_subset/i18n.py`).
`locale/mlox_subset_sort.pot` is the extracted English template — that is the
file a translator starts from.

## Regenerating the template

```bash
python tools/make_pot.py            # rewrite locale/mlox_subset_sort.pot
python tools/make_pot.py --check    # CI-style: fail if it is out of date
```

`tools/make_pot.py` needs nothing but the standard library, so it works on
Windows where GNU `xgettext` is not installed. It parses with `ast` rather than
scanning text, so a `_("...")` written inside a docstring is correctly *not*
extracted, and it warns about `_(variable)` calls it cannot read — a marker the
extractor can't see is a string that will never reach a translator.

Run it after adding or changing any `_()` string.

## Coverage status

Marking is **partial**. Plain string literals (buttons, labels, tooltips,
dialogs, menu text) are marked. Messages built with f-strings are **not** yet:
`_(f"Loaded {n} files")` would extract the *evaluated* text, which is useless
to a translator, so each has to become a named-placeholder form first:

```python
print(_("Loaded %(count)d files") % {"count": n})
```

Around 127 such sites remain, mostly report output in `mlox_subset_sort.py`.
Convert them as you touch them, then re-run `tools/make_pot.py`.

## Adding a language

```bash
# 1. Make sure the template is current
python tools/make_pot.py

# 2. Start a language (once per language)
msginit -i locale/mlox_subset_sort.pot -o locale/de/LC_MESSAGES/mlox_subset_sort.po -l de

# 3. Translate the .po (Poedit, Weblate, or any text editor)

# 4. Compile it
msgfmt locale/de/LC_MESSAGES/mlox_subset_sort.po \
    -o locale/de/LC_MESSAGES/mlox_subset_sort.mo
```

The app picks the language up automatically on next launch. Force one with
`MLOX_LANG=de`, and check what is installed with
`python -c "from mlox_subset import available_languages; print(available_languages())"`.

## Notes for translators

* Placeholders are **named** (`%(count)d`) and may be reordered freely. Never
  use positional `%s` — translators frequently need a different word order.
* Counted messages use `ngettext`, so your language's own plural rules apply.
* Plugin names, file paths and mlox rule keywords (`[Order]`, `content=`) are
  data, not prose -- leave them untranslated.
