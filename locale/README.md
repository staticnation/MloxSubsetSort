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

Marking is **complete** as of 3.0: plain literals (buttons, labels, tooltips,
dialogs, menu text) and the formerly-f-string report/status messages are all
marked, in named-placeholder form:

```python
print(_("Loaded %(count)d files") % {"count": n})
```

Deliberately *not* marked: pure data/decoration output -- `content=` echo
lines, warning-text passthroughs, `=== section ===` headers -- because those
contain no prose to translate. Counted messages use `ngettext`.

Two checkers keep this state from regressing, both in CI and `pytest`:
`tools/make_pot.py --check` fails if the template is stale, and
`tools/check_placeholders.py` fails on a `%(key)s`/dict mismatch (a runtime
`KeyError` otherwise) or a positional `%s` in a marked string.

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
