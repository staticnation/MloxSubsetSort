#!/usr/bin/env python3
"""Build a language's gettext catalogue from the template and a translation file.

The template ``locale/wraithguard_toolkit.pot`` lists every translatable string.
A translation lives in ``locale/translations/<lang>.json`` as a flat mapping of
English source to translated text (a list of forms for a plural entry). This
turns that pair into the ``.po`` a translator edits and the ``.mo`` the app
loads at ``locale/<lang>/LC_MESSAGES/wraithguard_toolkit.mo``.

Standard library only, like ``tools/make_pot.py`` -- it writes the ``.mo``
binary itself rather than depend on GNU ``msgfmt`` (absent on Windows) or on a
library whose CLDR plural rules disagree with gettext's for Russian and Polish.

Two safety checks run before anything is written, because a wrong translation is
worse than a missing one -- a missing one falls back to English, a wrong one can
crash:

* **Placeholders must match.** ``%(count)d`` in the source must appear in the
  translation, exactly and completely; a dropped or renamed ``%(key)s`` is a
  runtime ``KeyError`` in the user's language only, the worst kind of bug to
  find. A mismatch fails the build.
* **Plural entries need the language's number of forms.** Russian and Polish
  have three; a two-form translation of a counted string is rejected.

Untranslated strings are simply left out: gettext falls back to the English
source, so a partial catalogue is safe and useful, and coverage is reported so
the gap is visible rather than silent.

Usage::

    python tools/build_locale.py de           # one language
    python tools/build_locale.py de fr ru      # several
    python tools/build_locale.py --all         # every translations/<lang>.json
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALE_DIR = ROOT / "locale"
POT = LOCALE_DIR / "wraithguard_toolkit.pot"
TRANSLATIONS_DIR = LOCALE_DIR / "translations"
DOMAIN = "wraithguard_toolkit"

#: gettext ``Plural-Forms`` header, by language: how many forms a counted string
#: must supply, and the expression gettext evaluates to pick one. The Slavic
#: three-form rule (ru, pl) is why those cannot reuse a Western two-form plural.
PLURAL_FORMS: dict[str, str] = {
    "de": "nplurals=2; plural=(n != 1);",
    "fr": "nplurals=2; plural=(n > 1);",
    "es": "nplurals=2; plural=(n != 1);",
    "it": "nplurals=2; plural=(n != 1);",
    "ru": (
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
        "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "pl": (
        "nplurals=3; plural=(n==1 ? 0 : " "n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);"
    ),
    "pt": "nplurals=2; plural=(n != 1);",
    # Arabic distinguishes six categories (zero, one, two, few, many, other),
    # so a counted string supplies six forms.
    "ar": (
        "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
        "n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);"
    ),
    # East Asian languages make no grammatical plural distinction: one form
    # covers every count, so a counted string supplies a single translation.
    "ja": "nplurals=1; plural=0;",
    "ko": "nplurals=1; plural=0;",
    "zh": "nplurals=1; plural=0;",
}

#: Full names, only for the ``Language-Team`` header line.
LANGUAGE_NAMES: dict[str, str] = {
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "ru": "Russian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ar": "Arabic",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese (Simplified)",
}

_PLACEHOLDER = re.compile(r"%(?:\([^)]*\)[sdifgxX]|[sdifgxX%])")


def _placeholders(text: str) -> list[str]:
    """Every printf-style placeholder in ``text``, sorted for comparison.

    Args:
        text: A source or translated string.

    Returns:
        The placeholders it contains -- ``%(name)s``, ``%d``, ``%%`` -- sorted so
        two strings compare regardless of the order they appear in.
    """
    return sorted(_PLACEHOLDER.findall(text))


def _unescape(text: str) -> str:
    r"""Decode the C-style escapes a ``.po`` string literal uses.

    Args:
        text: The raw content between the quotes of a ``.po`` line.

    Returns:
        The real string, with ``\\n``, ``\\t``, ``\\"`` and ``\\\\`` resolved.
    """
    return text.encode("utf-8").decode("unicode_escape")


def _escape(text: str) -> str:
    """Encode a string as the contents of a ``.po`` quoted literal.

    Args:
        text: The real string.

    Returns:
        Its ``.po`` representation, with backslash, quote, tab and newline
        escaped -- the inverse of :func:`_unescape`.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\n", "\\n")


def parse_pot(path: Path) -> list[dict[str, str]]:
    """Read the message template into a list of entries.

    A deliberately small parser rather than a dependency: it needs only each
    entry's ``msgid``/``msgid_plural`` (the template's ``msgstr`` is empty).
    Continuation lines and C escapes are handled; comments and the header entry
    are skipped.

    Args:
        path: The ``.pot`` file.

    Returns:
        One dict per entry, with ``"msgid"`` and optionally ``"msgid_plural"``.
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    key: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            if current.get("msgid"):
                entries.append(current)
            current, key = {}, None
            continue
        if raw.startswith("#"):
            continue
        match = re.match(r'(msgid_plural|msgid|msgstr\[\d\]|msgstr)\s+"(.*)"$', raw)
        if match:
            key = match.group(1)
            current[key] = current.get(key, "") + _unescape(match.group(2))
            continue
        cont = re.match(r'"(.*)"$', raw)
        if cont and key is not None:
            current[key] += _unescape(cont.group(1))
    if current.get("msgid"):
        entries.append(current)
    return entries


class BuildError(Exception):
    """A translation could not be safely turned into a catalogue."""


def _check_placeholders(lang: str, msgid: str, translations: list[str]) -> None:
    """Fail if any translation drops or alters the source's placeholders.

    Args:
        lang: The language, for the message.
        msgid: The English source.
        translations: One string, or the plural forms.

    Raises:
        BuildError: On any mismatch.
    """
    want = _placeholders(msgid)
    for text in translations:
        if _placeholders(text) != want:
            raise BuildError(
                f"{lang}: placeholders differ for {msgid!r}\n"
                f"  source     : {want}\n"
                f"  translation: {_placeholders(text)} in {text!r}"
            )


def collect(lang: str, entries: list[dict[str, str]]) -> tuple[dict[bytes, bytes], int]:
    r"""Turn a language's translations into the (key, value) map a ``.mo`` stores.

    Args:
        lang: The language code.
        entries: Template entries from :func:`parse_pot`.

    Returns:
        ``(catalog, translated)`` -- a mapping of gettext key bytes to value
        bytes (plural keys and values are ``\\0``-joined, the format's own
        convention), including the metadata header entry, and how many source
        strings were translated.

    Raises:
        BuildError: On an unknown language, a placeholder mismatch, or a plural
            entry with the wrong number of forms.
    """
    if lang not in PLURAL_FORMS:
        raise BuildError(f"unknown language {lang!r}; add its plural rule to PLURAL_FORMS")
    source = TRANSLATIONS_DIR / f"{lang}.json"
    if not source.is_file():
        raise BuildError(f"no translations file: {source}")
    table = json.loads(source.read_text(encoding="utf-8"))
    nplurals = int(PLURAL_FORMS[lang].split(";", 1)[0].split("=", 1)[1])

    header = (
        f"Project-Id-Version: {DOMAIN}\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        f"Language: {lang}\n"
        f"Language-Team: {LANGUAGE_NAMES.get(lang, lang)}\n"
        f"Plural-Forms: {PLURAL_FORMS[lang]}\n"
    )
    catalog: dict[bytes, bytes] = {b"": header.encode("utf-8")}
    translated = 0
    for entry in entries:
        msgid = entry["msgid"]
        value = table.get(msgid)
        if value in (None, "", []):
            continue  # untranslated -> English fallback, a counted gap
        if "msgid_plural" in entry:
            forms = value if isinstance(value, list) else [value]
            if len(forms) != nplurals:
                raise BuildError(
                    f"{lang}: plural for {msgid!r} needs {nplurals} forms, got {len(forms)}"
                )
            _check_placeholders(lang, msgid, forms)
            key = (msgid + "\0" + entry["msgid_plural"]).encode("utf-8")
            catalog[key] = "\0".join(forms).encode("utf-8")
        else:
            if not isinstance(value, str):
                raise BuildError(f"{lang}: {msgid!r} is not a plural entry but its value is a list")
            _check_placeholders(lang, msgid, [value])
            catalog[msgid.encode("utf-8")] = value.encode("utf-8")
        translated += 1
    return catalog, translated


def write_mo(path: Path, catalog: dict[bytes, bytes]) -> None:
    r"""Write a ``catalog`` of key/value bytes as a binary ``.mo`` file.

    The MO format is a header, a table of key (offset, length) pairs, a table of
    value pairs, then the strings, keys sorted. gettext splits a plural key on
    ``\\0`` into singular and plural and a plural value into its forms, which is
    why :func:`collect` joins them that way.

    Args:
        path: Where to write.
        catalog: Key bytes to value bytes, including the ``b""`` header entry.
    """
    keys = sorted(catalog)
    offsets: list[tuple[int, int, int, int]] = []
    ids = b""
    strs = b""
    for key in keys:
        value = catalog[key]
        offsets.append((len(key), len(ids), len(value), len(strs)))
        ids += key + b"\0"
        strs += value + b"\0"
    count = len(keys)
    key_table = 7 * 4
    value_table = key_table + count * 8
    ids_start = value_table + count * 8
    strs_start = ids_start + len(ids)
    out = bytearray()
    out += struct.pack("<Iiiiiii", 0x950412DE, 0, count, key_table, value_table, 0, 0)
    for klen, koff, _vlen, _voff in offsets:
        out += struct.pack("<ii", klen, ids_start + koff)
    for _klen, _koff, vlen, voff in offsets:
        out += struct.pack("<ii", vlen, strs_start + voff)
    out += ids
    out += strs
    path.write_bytes(bytes(out))


def write_po(
    path: Path, lang: str, entries: list[dict[str, str]], catalog: dict[bytes, bytes]
) -> None:
    """Write the human-editable ``.po`` beside the compiled ``.mo``.

    Args:
        path: Where to write.
        lang: The language code, for the header.
        entries: Template entries, for source order and plural sources.
        catalog: The same map :func:`write_mo` gets, read back for translations.
    """
    lines = [
        'msgid ""',
        'msgstr ""',
        *(f'"{part}\\n"' for part in catalog[b""].decode("utf-8").rstrip("\n").split("\n")),
        "",
    ]
    for entry in entries:
        msgid = entry["msgid"]
        if "msgid_plural" in entry:
            key = (msgid + "\0" + entry["msgid_plural"]).encode("utf-8")
            forms = catalog.get(key, b"").decode("utf-8").split("\0") if key in catalog else []
            lines.append(f'msgid "{_escape(msgid)}"')
            lines.append(f'msgid_plural "{_escape(entry["msgid_plural"])}"')
            if forms:
                lines += [f'msgstr[{i}] "{_escape(form)}"' for i, form in enumerate(forms)]
            else:
                lines += ['msgstr[0] ""', 'msgstr[1] ""']
        else:
            value = catalog.get(msgid.encode("utf-8"), b"").decode("utf-8")
            lines.append(f'msgid "{_escape(msgid)}"')
            lines.append(f'msgstr "{_escape(value)}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_language(lang: str, entries: list[dict[str, str]]) -> tuple[int, int]:
    """Write the ``.po`` and ``.mo`` for one language.

    Args:
        lang: The language code, e.g. ``"de"``.
        entries: Template entries from :func:`parse_pot`.

    Returns:
        ``(translated, total)`` message counts, for a coverage line.

    Raises:
        BuildError: If the translation is missing or fails a safety check.
    """
    catalog, translated = collect(lang, entries)
    mo_dir = LOCALE_DIR / lang / "LC_MESSAGES"
    mo_dir.mkdir(parents=True, exist_ok=True)
    write_mo(mo_dir / f"{DOMAIN}.mo", catalog)
    write_po(mo_dir / f"{DOMAIN}.po", lang, entries, catalog)
    return translated, len(entries)


def main(argv: list[str]) -> int:
    """Build the languages named on the command line.

    Args:
        argv: Process arguments; ``argv[1:]`` are language codes, or ``--all``.

    Returns:
        A process exit code.
    """
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args == ["--all"]:
        args = sorted(p.stem for p in TRANSLATIONS_DIR.glob("*.json"))
        if not args:
            print(f"no translations under {TRANSLATIONS_DIR}")
            return 1
    entries = parse_pot(POT)
    failed = False
    for lang in args:
        try:
            done, total = build_language(lang, entries)
        except BuildError as exc:
            print(f"FAILED {lang}: {exc}")
            failed = True
            continue
        pct = 100 * done // total if total else 0
        print(f"built {lang}: {done}/{total} strings ({pct}%) -> locale/{lang}/LC_MESSAGES/")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
