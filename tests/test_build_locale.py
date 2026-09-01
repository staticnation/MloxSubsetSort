"""Tests for the locale builder -- the pot-to-mo pipeline for translations.

The safety checks are the point: a translation that drops a ``%(key)s`` or gives
a plural the wrong number of forms must fail the build, never reach a user and
crash in their language only. The last test compiles a tiny catalogue and reads
it back through the standard library's own ``gettext``, so the ``.mo`` bytes are
proven against the real consumer, not just against this module's idea of them.
"""

from __future__ import annotations

import gettext
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_locale
from build_locale import (
    BuildError,
    _check_placeholders,
    _placeholders,
    collect,
    write_mo,
)


class TestPlaceholders:
    """Finding and comparing printf placeholders."""

    def test_named_and_bare_placeholders_are_found(self) -> None:
        """Named ``%(x)s`` and bare ``%d`` are both extracted, sorted."""
        assert _placeholders("Loaded %(count)d of %(total)d into %s") == [
            "%(count)d",
            "%(total)d",
            "%s",
        ]

    def test_a_matching_translation_passes(self) -> None:
        """Same placeholders, any order, is accepted."""
        _check_placeholders("de", "%(a)s then %(b)s", ["%(b)s dann %(a)s"])

    def test_a_dropped_placeholder_is_refused(self) -> None:
        """Losing a placeholder would be a runtime KeyError; it fails the build."""
        with pytest.raises(BuildError, match="placeholders differ"):
            _check_placeholders("de", "Saved %(path)s", ["Gespeichert"])


class TestCollect:
    """Turning a translations file into catalogue bytes, with its guards."""

    def _entries(self) -> list[dict[str, str]]:
        """A tiny template: one plain string and one plural entry."""
        return [
            {"msgid": "Close"},
            {"msgid": "%(n)d file", "msgid_plural": "%(n)d files"},
        ]

    def _write(self, path: Path, lang: str, table: dict[str, object]) -> None:
        """Write a translations JSON for ``lang`` under a temp locale tree."""
        (path / "translations").mkdir(parents=True, exist_ok=True)
        (path / "translations" / f"{lang}.json").write_text(json.dumps(table), encoding="utf-8")

    def test_a_plural_with_too_few_forms_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Russian needs three plural forms; two is rejected before writing."""
        monkeypatch.setattr(build_locale, "TRANSLATIONS_DIR", tmp_path / "translations")
        self._write(tmp_path, "ru", {"%(n)d file": ["a", "b"]})
        with pytest.raises(BuildError, match="needs 3 forms"):
            collect("ru", self._entries())

    def test_an_untranslated_string_is_a_gap_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing translation is left out (English fallback), not a failure."""
        monkeypatch.setattr(build_locale, "TRANSLATIONS_DIR", tmp_path / "translations")
        self._write(tmp_path, "de", {"Close": "Schließen"})
        catalog, translated = collect("de", self._entries())
        assert translated == 1  # the plural entry was left untranslated
        assert catalog[b"Close"] == "Schließen".encode()


class TestMoRoundTrip:
    """The compiled bytes, proven against the standard library's gettext."""

    def test_gettext_reads_back_singular_and_plural(self, tmp_path: Path) -> None:
        """A written .mo yields the right singular and both plural forms."""
        catalog = {
            b"": b"Content-Type: text/plain; charset=UTF-8\nPlural-Forms: nplurals=2; plural=(n != 1);\n",
            b"Close": "Schließen".encode(),
            b"%(n)d file\x00%(n)d files": b"%(n)d Datei\x00%(n)d Dateien",
        }
        mo_dir = tmp_path / "de" / "LC_MESSAGES"
        mo_dir.mkdir(parents=True)
        write_mo(mo_dir / "wraithguard_toolkit.mo", catalog)

        translation = gettext.translation(
            "wraithguard_toolkit", localedir=str(tmp_path), languages=["de"]
        )
        assert translation.gettext("Close") == "Schließen"

        def one(n: int) -> str:
            return translation.ngettext("%(n)d file", "%(n)d files", n) % {"n": n}

        assert one(1) == "1 Datei"
        assert one(3) == "3 Dateien"
