# I18N_BRIEF — retired

> **This work is DONE (3.0) and this brief is retired.** It was a hand-off note
> for finishing the i18n string marking; keeping 229 lines of "here is what to
> do next" for finished work only misleads the next reader.
>
> **What happened, and the measurements, are in `CODE_REVIEW.md` §17.**
> The short version: `tools/check_placeholders.py` was built first (as this
> brief insisted), then all 127 f-string sites were converted to
> named-placeholder form, the `.pot` went 141 → 312 messages, and the whole
> pipeline was proven against a compiled German catalogue. The brief's
> predicted `_`-shadowing trap fired exactly once, in `build_and_sort`.
>
> Living documentation for translators is **`locale/README.md`**.
> The rules that outlived the brief are enforced, not remembered:
> `tools/check_placeholders.py` and `tools/make_pot.py --check` both gate CI.

This file is kept only so existing references resolve. It can be deleted.
