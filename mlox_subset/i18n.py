"""Internationalisation (i18n) and localisation (l10n) support.

Translation uses the standard library's :mod:`gettext`, so catalogues are
ordinary ``.mo`` files under ``locale/<lang>/LC_MESSAGES/<domain>.mo`` and can
be produced with the usual tooling (``xgettext``/``msgfmt``, Poedit, Weblate).

The public entry point is :func:`gettext`, conventionally imported as ``_``::

    from mlox_subset import _
    print(_("Sorting plugins"))

Marking a string with ``_()`` does two jobs: it looks up a translation at
run time, and it makes the string discoverable by ``xgettext`` when the
catalogue is regenerated. Until a catalogue exists for the active language
every lookup falls back to the original English text, so wrapping strings is
always safe.

Guidance for translatable strings:
    * Use *named* placeholders (``%(count)d``), never positional ``%s``.
      Translators frequently need to reorder them.
    * Use :func:`ngettext` for anything counted -- plural rules vary widely.
    * Never concatenate sentence fragments; translate whole sentences.
"""

from __future__ import annotations

import gettext as _gettext_module
import locale
import os
from pathlib import Path
from typing import Final

#: Name of the translation catalogue (``<domain>.mo``).
DOMAIN: Final[str] = "mlox_subset_sort"

#: Directory holding ``<lang>/LC_MESSAGES/<domain>.mo`` catalogues.
LOCALE_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "locale"

#: Environment variable that forces a language, overriding the system locale.
LANGUAGE_ENV_VAR: Final[str] = "MLOX_LANG"

#: Language used when nothing else is configured. English strings are the
#: source strings, so this needs no catalogue.
DEFAULT_LANGUAGE: Final[str] = "en"

_active_language: str = DEFAULT_LANGUAGE
_translation: _gettext_module.NullTranslations = _gettext_module.NullTranslations()


def _detect_language() -> str:
    """Determine the preferred language from the environment.

    Precedence is ``$MLOX_LANG``, then the standard gettext variables that
    ``gettext.find`` honours (``$LANGUAGE``/``$LC_ALL``/``$LC_MESSAGES``/
    ``$LANG``), then the system locale.

    Returns:
        A language tag such as ``"en"`` or ``"de_DE"``. Falls back to
        :data:`DEFAULT_LANGUAGE` when detection fails.

    """
    for var in (LANGUAGE_ENV_VAR, "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            # "de_DE.UTF-8:en" -> "de_DE"
            return value.split(":")[0].split(".")[0]
    try:
        system_language, _encoding = locale.getlocale(locale.LC_MESSAGES)
    except (AttributeError, ValueError):
        # LC_MESSAGES does not exist on Windows.
        system_language = None
    return system_language or DEFAULT_LANGUAGE


def set_language(language: str | None = None) -> str:
    """Activate a translation catalogue.

    Args:
        language: Language tag such as ``"de"`` or ``"pt_BR"``. When ``None``
            the language is auto-detected from the environment.

    Returns:
        The language tag that was actually activated. If no catalogue exists
        for the requested language the untranslated (English) strings stay in
        use and the requested tag is still reported, so callers can tell what
        was asked for.

    """
    global _active_language, _translation

    resolved = language or _detect_language()
    try:
        _translation = _gettext_module.translation(
            DOMAIN,
            localedir=str(LOCALE_DIR),
            languages=[resolved],
            fallback=True,
        )
    except OSError:
        # An unreadable locale directory must never stop the application.
        _translation = _gettext_module.NullTranslations()
    _active_language = resolved
    return resolved


def get_language() -> str:
    """Return the currently active language tag."""
    return _active_language


def available_languages() -> list[str]:
    """List languages that have a compiled catalogue installed.

    Returns:
        Sorted language tags found under :data:`LOCALE_DIR`. Always includes
        :data:`DEFAULT_LANGUAGE`, whose strings are built in. Returns just the
        default if the locale directory is missing or unreadable.

    """
    found = {DEFAULT_LANGUAGE}
    try:
        for entry in LOCALE_DIR.iterdir():
            if (entry / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file():
                found.add(entry.name)
    except OSError:
        # An unreadable locale directory means "no translations available",
        # which is the same outcome as an empty one. Reporting it would push a
        # packaging detail at a user who cannot act on it.
        pass
    return sorted(found)


def gettext(message: str) -> str:
    """Translate ``message`` into the active language.

    Args:
        message: The English source string.

    Returns:
        The translated string, or ``message`` unchanged when no catalogue
        provides it.

    """
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a counted message, choosing the correct plural form.

    Args:
        singular: English text used when ``n == 1``.
        plural: English text used otherwise.
        n: The count deciding the form. Languages define their own rules, so
            the catalogue -- not this call -- picks the final form.

    Returns:
        The translated string for ``n`` in the active language.

    """
    return _translation.ngettext(singular, plural, n)


# Activate on import so `_()` works without any explicit setup.
set_language()
