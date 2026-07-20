# Translation catalogues

Translatable strings are marked with `_()` (see `mlox_subset/i18n.py`).

## Adding a language

```bash
# 1. Extract marked strings into a template
xgettext -o locale/mlox_subset_sort.pot \
         --keyword=_ --keyword=ngettext:1,2 \
         --from-code=UTF-8 \
         mlox_subset/*.py mlox_subset_sort.py mlox_subset_sort_gui.py

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

* Placeholders are **named** (`%(count)d`) and may be reordered freely.
* Counted messages use `ngettext`, so your language's own plural rules apply.
* Plugin names, file paths and mlox rule keywords (`[Order]`, `content=`) are
  data, not prose -- leave them untranslated.
