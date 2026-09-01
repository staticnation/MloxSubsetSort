"""Language detection and the catalogue-loading fallbacks in :mod:`i18n`.

Every failure here has to degrade to untranslated English rather than stop the
application: a missing ``LC_MESSAGES`` (Windows), an unreadable locale
directory, or a locale the platform cannot parse. Those branches are pinned
here; the placeholder-safety tests live in ``test_i18n_placeholders``.
"""

from __future__ import annotations

import locale

import pytest

from wraithguard import i18n


@pytest.fixture(autouse=True)
def _restore_language():
    """Keep these tests from leaking the active language into others."""
    previous = i18n.get_language()
    yield
    i18n.set_language(previous)


def _clear_language_env(monkeypatch) -> None:
    for var in (i18n.LANGUAGE_ENV_VAR, "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)


def test_an_env_var_takes_precedence_and_is_trimmed(monkeypatch) -> None:
    """``de_DE.UTF-8:en`` resolves to the bare ``de_DE`` tag."""
    _clear_language_env(monkeypatch)
    monkeypatch.setenv("LANG", "de_DE.UTF-8:en")
    assert i18n._detect_language() == "de_DE"


def test_the_system_locale_is_used_when_no_env_var_is_set(monkeypatch) -> None:
    """With no override, the platform's LC_MESSAGES locale is consulted."""
    _clear_language_env(monkeypatch)
    # Windows has no locale.LC_MESSAGES; ensure it exists so the getlocale path
    # is reached rather than the AttributeError fallback.
    monkeypatch.setattr(locale, "LC_MESSAGES", getattr(locale, "LC_MESSAGES", 6), raising=False)
    monkeypatch.setattr(locale, "getlocale", lambda _category: ("fr_FR", "UTF-8"))
    assert i18n._detect_language() == "fr_FR"


def test_an_unparseable_locale_falls_back_to_the_default(monkeypatch) -> None:
    """When the platform cannot report a locale, English is the fallback."""
    _clear_language_env(monkeypatch)
    monkeypatch.setattr(locale, "LC_MESSAGES", getattr(locale, "LC_MESSAGES", 6), raising=False)

    def unsupported(_category: object) -> tuple[str | None, str | None]:
        raise ValueError("unknown locale")

    monkeypatch.setattr(locale, "getlocale", unsupported)
    assert i18n._detect_language() == i18n.DEFAULT_LANGUAGE


def test_a_missing_lc_messages_category_falls_back_to_the_default(monkeypatch) -> None:
    """On a platform without LC_MESSAGES (Windows), detection still yields a tag."""
    _clear_language_env(monkeypatch)
    monkeypatch.delattr(locale, "LC_MESSAGES", raising=False)
    assert i18n._detect_language() == i18n.DEFAULT_LANGUAGE


def test_set_language_survives_an_unreadable_catalogue(monkeypatch) -> None:
    """An OSError loading the catalogue leaves untranslated strings in use."""

    def refuse(*_args: object, **_kwargs: object):
        raise OSError("locale dir unreadable")

    monkeypatch.setattr(i18n._gettext_module, "translation", refuse)
    assert i18n.set_language("de") == "de"
    # The fallback is a NullTranslations, so lookups return the source string.
    assert i18n.gettext("Sorting plugins") == "Sorting plugins"


def test_available_languages_survives_an_unreadable_locale_dir(monkeypatch) -> None:
    """An unreadable locale directory yields just the built-in default."""
    from pathlib import Path

    def boom(_self: Path):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "iterdir", boom)
    assert i18n.available_languages() == [i18n.DEFAULT_LANGUAGE]
