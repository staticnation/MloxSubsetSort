# Translation catalogues

Translatable strings are marked with `_()` (see `wraithguard/i18n.py`).
`locale/wraithguard_toolkit.pot` is the extracted English template - that is the
file a translator starts from.

## Regenerating the template

```bash
python tools/make_pot.py            # rewrite locale/wraithguard_toolkit.pot
python tools/make_pot.py --check    # CI-style: fail if it is out of date
```

`tools/make_pot.py` needs nothing but the standard library, so it works on
Windows where GNU `xgettext` is not installed. It parses with `ast` rather than
scanning text, so a `_("...")` written inside a docstring is correctly *not*
extracted, and it warns about `_(variable)` calls it cannot read - a marker the
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

Translations live in `locale/translations/<lang>.json` -- a flat map of the
English source string to its translation (a list of forms for a counted
`ngettext` string). `tools/build_locale.py` turns that plus the template into
the `.po` a translator can edit and the `.mo` the app loads. It is standard
library only, so it works on Windows where GNU `msginit`/`msgfmt` are not
installed, and it **refuses to build** a translation that drops a placeholder or
gives a plural the wrong number of forms -- a wrong translation is worse than a
missing one, which just falls back to English.

```bash
# 1. Make sure the template is current
python tools/make_pot.py

# 2. Write locale/translations/de.json  (English source -> translation)
#    Untranslated strings can be left out; they fall back to English.

# 3. Build the .po and .mo, with the safety checks
python tools/build_locale.py de           # one language
python tools/build_locale.py --all        # every translations/<lang>.json
```

The app picks the language up automatically on next launch (it scans for any
`<lang>/LC_MESSAGES/wraithguard_toolkit.mo`), and reports coverage as it builds
so the untranslated gap is visible. Force a language with `MLOX_LANG=de`, and
check what is installed with
`python -c "from wraithguard import available_languages; print(available_languages())"`.

Supported plural rules live in `PLURAL_FORMS` in `tools/build_locale.py`
(currently de, fr, es, it, ru, pl); add a language's rule there before building
it. The generated `.po` is a convenience for editing in Poedit/Weblate -- the
`.json` is the source of truth this repo builds from.

## Notes for translators

* Placeholders are **named** (`%(count)d`) and may be reordered freely. Never
  use positional `%s` - translators frequently need a different word order.
* Counted messages use `ngettext`, so your language's own plural rules apply.
* Plugin names, file paths and mlox rule keywords (`[Order]`, `content=`) are
  data, not prose -- leave them untranslated.
