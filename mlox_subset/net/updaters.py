"""Download rule files and the MOMW plugin order.

Every URL here is user-configurable -- read from a persisted settings file and
overridable by environment variable -- which makes them untrusted input rather
than constants. :func:`fetch_url_bytes` is the single choke point that enforces
that:

* **Scheme allow-list.** Only ``http`` and ``https``. Without this a settings
  file could point at ``file:///etc/passwd`` and the "update rules" button
  would happily read arbitrary local files over the user's own rule database.
  ``file:``, ``data:``, ``ftp:`` and friends are refused by name.
* **A host is required.** ``https://`` with nothing after it is rejected
  rather than passed to ``urlopen``.
* **A size cap.** Responses are read to :data:`MAX_DOWNLOAD_BYTES` + 1 and
  rejected if they exceed it, so a hostile or broken endpoint cannot exhaust
  memory.

The rejection paths are pure -- nothing is fetched when a URL is refused --
and ``tests/test_differential.py`` pins them against a list of hostile URLs,
so a refactor that let one through fails the suite rather than shipping.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from mlox_subset.momw import parse_plugin_order_yml

#: Where MOMW publishes ``plugin-order.yml``. Two URLs because the raw path
#: and the API path have each been the one that works at different times.
PLUGIN_ORDER_URLS: Final = (
    "https://gitlab.com/modding-openmw/modding-openmw.com/-/raw/master/momw/momw/"
    "data_seeds/data/plugin-order.yml?ref_type=heads&inline=false",
    "https://gitlab.com/api/v4/projects/modding-openmw%2Fmodding-openmw.com/repository/files/"
    "momw%2Fmomw%2Fdata_seeds%2Fdata%2Fplugin-order.yml/raw?ref=master",
)

#: Schemes we are willing to download from. Anything else -- notably ``file:``
#: -- is refused, because these URLs come from user-editable configuration.
ALLOWED_URL_SCHEMES: Final = frozenset({"http", "https"})

#: Hard ceiling on a single download. Rule files are ~2 MB; this is generous
#: while still bounding memory if an endpoint misbehaves.
MAX_DOWNLOAD_BYTES: Final = 32 * 1024 * 1024

#: Repository the mlox rule databases are fetched from.
RULES_REPO: Final = "DanaePlays/mlox-rules"

#: Template for one rule file. ``{name}`` is the filename.
RULES_URL_TEMPLATE: Final = "https://raw.githubusercontent.com/" + RULES_REPO + "/main/{name}"


def fetch_url_bytes(url: str, timeout: int = 30, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Download ``url`` and return its bytes.

    Raises:
        ValueError: the URL is malformed or uses a disallowed scheme (see
            :data:`ALLOWED_URL_SCHEMES`), or the response exceeds
            ``max_bytes``.
        OSError: the request itself failed (urllib raises ``URLError``, a
            subclass of ``OSError``).
    """
    # Both imported explicitly. urllib.request happens to pull in
    # urllib.parse today, so `urllib.parse.urlparse` resolved by accident --
    # relying on another module's imports is exactly the kind of thing that
    # breaks silently on a stdlib refactor.
    import urllib.parse
    import urllib.request

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"refusing to download from a {parsed.scheme or 'scheme-less'!r} URL "
            f"-- only {'/'.join(sorted(ALLOWED_URL_SCHEMES))} are allowed"
        )
    if not parsed.netloc:
        raise ValueError("URL has no host")
    # Read one byte more than the cap so an exactly-at-limit body still fits
    # while anything larger is detected rather than silently truncated.
    # S310: the scheme is checked against ALLOWED_URL_SCHEMES immediately above.
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response larger than the {max_bytes:,}-byte limit")
    return data


def update_plugin_order_yml(
    path: str | Path,
    urls: Sequence[str] | None = None,
    timeout: int = 45,
) -> list[str]:
    """Download the current MOMW ``plugin-order.yml`` over the configured file.

    Strictly validated *before* anything on disk is touched: the download must
    parse with :func:`~mlox_subset.momw.parse_plugin_order_yml` and contain a
    plausible number of entries. Without that check a wrong URL or an HTML
    error page would silently overwrite the curated-list source of truth --
    and every plugin it then failed to list would be treated as one of the
    user's own mods and become eligible for reordering.

    The previous file is kept as a timestamped ``.bak``.

    Args:
        path: Destination ``plugin-order.yml``.
        urls: Source URLs to try in order. Defaults to
            :data:`PLUGIN_ORDER_URLS`.
        timeout: Per-request timeout in seconds.

    Returns:
        Human-readable report lines describing what was tried and what
        happened. Failures are reported here rather than raised.
    """
    import tempfile as _tf

    p = Path(path)
    # precedence: explicit urls param (e.g. the GUI's Sources setting) >
    # $MLOX_PLUGIN_ORDER_URL > built-in candidates
    env = os.environ.get("MLOX_PLUGIN_ORDER_URL")
    cand = list(urls) if urls else ([env] if env else list(PLUGIN_ORDER_URLS))
    report = []
    for url in cand:
        try:
            data = fetch_url_bytes(url, timeout=timeout)
        except (OSError, ValueError) as e:
            # fetch_url_bytes' documented contract: ValueError for a bad or
            # disallowed URL / oversized body, OSError for the request itself
            # (urllib's URLError, socket timeouts and ssl errors all subclass it).
            report.append(f"  {url}: {e}")
            continue
        if b"file_name" not in data or b"on_lists" not in data:
            report.append(f"  {url}: response doesn't look like plugin-order.yml")
            continue
        # full validation through the real parser before touching anything.
        # try/finally so a parse failure can't leak the temp file.
        tmp = None
        try:
            with _tf.NamedTemporaryFile("wb", suffix=".yml", delete=False) as tf:
                tf.write(data)
                tmp = Path(tf.name)
            entries = parse_plugin_order_yml(tmp)
        except Exception as e:  # noqa: BLE001 -- validating untrusted download
            # This is the gate that decides whether freshly downloaded bytes are
            # safe to write over the user's file. parse_plugin_order_yml runs a
            # third-party YAML parser (or our fallback) over arbitrary remote
            # content; narrowing risks letting an unanticipated parser error
            # through and installing a corrupt plugin-order.yml.
            report.append(f"  {url}: downloaded but failed to parse ({e})")
            continue
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        if len(entries) < 100:
            report.append(f"  {url}: parsed but only {len(entries)} entries -- refusing")
            continue
        old = p.read_bytes() if p.exists() else b""
        if old == data:
            report.append(f"{p.name}: already up to date ({len(entries)} entries).")
            return report
        if p.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            p.with_name(p.name + f".bak-{stamp}").write_bytes(old)
        p.write_bytes(data)
        try:
            old_entries = None
            if old:
                with _tf.NamedTemporaryFile("wb", suffix=".yml", delete=False) as tf:
                    tf.write(old)
                    tmp = Path(tf.name)
                old_entries = len(parse_plugin_order_yml(tmp))
                tmp.unlink()
        except Exception:  # noqa: BLE001 -- cosmetic count only
            # This block just derives "N -> M entries" for the report. The new
            # file is already written by this point, so any failure re-parsing
            # the *old* one must be invisible rather than fail the update.
            old_entries = None
        frm = f"{old_entries} -> " if old_entries is not None else ""
        report.append(
            f"{p.name}: updated from {url} ({frm}{len(entries)} entries; "
            f"previous version kept as .bak)."
        )
        return report
    report.insert(0, "FAILED: no source produced a valid plugin-order.yml:")
    report.append("  (set $MLOX_PLUGIN_ORDER_URL if MOMW moved the file)")
    return report


def update_rule_files(
    rule_paths: Sequence[str | Path],
    url_template: str | None = None,
    timeout: int = 30,
) -> list[str]:
    """Download the maintained mlox rule databases over the configured files.

    Only filenames the upstream repository actually manages are touched, so a
    personal rules file with another name is left alone rather than
    overwritten. Each replaced file is kept as a timestamped ``.bak``.

    Args:
        rule_paths: Configured rule files. Names the repo does not manage are
            skipped.
        url_template: Overrides the source; must contain ``{name}``, replaced
            per file. Falls back to ``$MLOX_RULES_URL_TEMPLATE`` and then
            :data:`RULES_URL_TEMPLATE`.
        timeout: Per-request timeout in seconds.

    Returns:
        Human-readable report lines. A malformed template or a failed download
        is reported here rather than raised.
    """
    template = url_template or os.environ.get("MLOX_RULES_URL_TEMPLATE") or RULES_URL_TEMPLATE
    if "{name}" not in template:
        return [f"FAILED: rules URL template must contain '{{name}}': {template}"]
    report = []
    for rule_path in rule_paths:
        p = Path(rule_path)
        if p.name.lower() not in ("mlox_base.txt", "mlox_user.txt"):
            report.append(f"skipped {p.name}: not an upstream-managed filename")
            continue
        # A user-supplied template may contain other braces ('{branch}', or an
        # unclosed '{'); format() would raise KeyError/ValueError and crash the
        # update. Report it as a bad template instead.
        try:
            url = template.format(name=p.name)
        except (KeyError, IndexError, ValueError) as e:
            return [
                f"FAILED: bad rules URL template {template!r}: {e}. "
                f"Only the '{{name}}' placeholder is supported."
            ]
        try:
            data = fetch_url_bytes(url, timeout=timeout)
        except (OSError, ValueError) as e:
            # same documented contract as above
            report.append(f"FAILED {p.name}: {e}")
            continue
        if not data or b"[Order]" not in data:
            report.append(f"FAILED {p.name}: download doesn't look like an mlox rules file")
            continue
        old = p.read_bytes() if p.exists() else b""
        if old == data:
            report.append(f"{p.name}: already up to date ({len(data):,} bytes)")
            continue
        if p.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            p.with_name(p.name + f".bak-{stamp}").write_bytes(old)
        p.write_bytes(data)
        report.append(
            f"{p.name}: updated {len(old):,} -> {len(data):,} bytes "
            f"(previous version kept as .bak)"
        )
    return report


def rule_file_ages(rule_paths: Sequence[str | Path]) -> list[tuple[str, int | None]]:
    """Report how stale each rule file is.

    Args:
        rule_paths: The configured rule files.

    Returns:
        ``(filename, age_in_days)`` per file, with ``None`` for a file whose
        modification time could not be read.
    """
    out: list[tuple[str, int | None]] = []
    now = datetime.now().timestamp()
    for raw_path in rule_paths:
        path = Path(raw_path)
        try:
            out.append((path.name, int((now - path.stat().st_mtime) // 86400)))
        except OSError:
            out.append((path.name, None))
    return out
