"""The shared page shell for every visualisation.

Two constraints shape this module, and both are inherited deliberately from
``generate_cell_map_html``:

**Self-contained.** No CDN, no external stylesheet, no font download. The
existing cell map holds to this and it is not an aesthetic preference: the tool
is used offline, is shipped as a PyInstaller binary, and a page that silently
loses its script tag when the network is down is worse than one that never had
it. Everything here is inline, which is also why the 3D view is hand-rolled on
a canvas rather than reaching for a library.

**No f-string templates.** The cell map's generator was one 185-line f-string
with ``{{``/``}}`` escaping throughout, effectively uneditable (the rework is in
``CODE_REVIEW.md`` §29). The helpers here take content as arguments and do their
own escaping, so a page is assembled from pieces that can each be tested.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from wraithguard import _

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


def script_json(value: Any) -> str:  # noqa: ANN401 - encodes arbitrary JSON-able data
    r"""Serialise a value for safe embedding inside an inline ``<script>`` tag.

    ``json.dumps`` does not escape ``<``, ``>`` or ``&``, so a plugin named
    ``</script>...`` -- an attacker-controlled filename on disk -- would close
    the script element and inject arbitrary markup. These pages embed
    third-party plugin names in inline JSON, so that is a real hole, not a
    hypothetical one.

    Escaping ``<``/``>``/``&`` to their ``\\u00xx`` forms (and the two Unicode
    line separators, which are raw newlines inside a JS string) makes the JSON
    inert as HTML while remaining byte-identical once parsed. This is the
    standard mitigation; it is applied wherever JSON is inlined.

    Args:
        value: Any JSON-serialisable structure.

    Returns:
        A JSON string safe to drop between ``<script>`` and ``</script>``.
    """
    encoded = json.dumps(value, separators=(",", ":"))
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


#: The shared dark palette, matching the GUI's default theme so a page opened
#: from the diff window does not look like a different application.
_CSS = """
:root{--bg:#1e2127;--panel:#252a32;--ink:#d7dae0;--dim:#8b93a1;--line:#333945;
--mine:#ff9d5c;--link:#7cc5ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 "Segoe UI",system-ui,sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{margin:0;font-size:17px;font-weight:600}
.sub{color:var(--dim);font-size:12.5px;margin-top:3px}
.stamp{color:#6f7784;font-size:11.5px;margin-top:2px}
main{padding:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;
padding:14px;margin-bottom:16px}
.card h2{margin:0 0 10px;font-size:14px;font-weight:600;color:var(--ink)}
.legend{display:flex;gap:14px;align-items:center;flex-wrap:wrap;
color:var(--dim);font-size:12px;margin-top:10px}
.legend i{display:inline-block;width:13px;height:13px;border-radius:2px;
vertical-align:-2px;margin-right:5px}
.mine{color:var(--mine)}
a{color:var(--link)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.empty{color:var(--dim);font-style:italic;padding:8px 0}
svg{display:block;max-width:100%;height:auto}
.grid rect{stroke:#1a1d23;stroke-width:.5}
.grid rect.mine{stroke:var(--mine);stroke-width:1.2}
.mono{font-family:Consolas,"DejaVu Sans Mono",monospace}
"""


def escape(value: object) -> str:
    """HTML-escape a value, including quotes, for use in text or attributes.

    Args:
        value: Anything; stringified first. Plugin names and record ids come
            from third-party files and are treated as untrusted.

    Returns:
        The escaped string.
    """
    return html.escape(str(value), quote=True)


def page(title: str, subtitle: str, body: str, generated_at: datetime | None = None) -> str:
    """Wrap rendered content in the standard self-contained shell.

    Args:
        title: The page heading, shown and used as ``<title>``.
        subtitle: One line of context under the heading.
        body: Already-escaped HTML for the page body.
        generated_at: When the page was built. Stamped into the header so a
            file found on disk can be told from a freshly generated one --
            these pages accumulate, and an hour-old map that looks identical
            to a new one is a real source of confusion. Defaults to now; pass
            a fixed value to make output reproducible.

    Returns:
        A complete HTML document.
    """
    stamped = generated_at or datetime.now()  # noqa: DTZ005 - local clock is what the user reads
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<header><h1>{escape(title)}</h1>"
        f'<div class="sub">{escape(subtitle)}</div>'
        f'<div class="stamp">{escape(_("Generated %(when)s") % {"when": stamped.strftime("%Y-%m-%d %H:%M:%S")})}</div>'
        f"</header>"
        f"<main>{body}</main></body></html>"
    )


def card(heading: str, body: str) -> str:
    """Wrap a section in a titled panel.

    Args:
        heading: The section heading.
        body: Already-escaped HTML.

    Returns:
        The panel markup.
    """
    return f'<div class="card"><h2>{escape(heading)}</h2>{body}</div>'


def table(
    headers: Iterable[str],
    rows: Iterable[Iterable[object]],
    numeric: set[int] | None = None,
    row_attrs: Sequence[Mapping[str, str]] | None = None,
) -> str:
    """Render a simple table, escaping every cell.

    Args:
        headers: Column headings.
        rows: Row values; each cell is stringified and escaped.
        numeric: Indices of columns to right-align as numbers.
        row_attrs: Extra HTML attributes per row (e.g. ``data-*`` hooks for a
            page's own JS), positionally matched to ``rows``. Omit for a plain
            table; a caller that wants some rows tagged and others not can
            still pass ``{}`` for the untagged ones.

    Returns:
        The table markup, or an empty-state note when there are no rows.
    """
    numeric = numeric or set()
    rows = list(rows)
    # Padded rather than zipped short. `zip` would silently stop at whichever
    # list ran out, and the thing that runs out is the attribute list -- so a
    # caller that built one attribute short would lose *table rows*, which on
    # the conflict map means losing conflicts. A row with no data-* hooks is
    # merely unfilterable; a missing row is invisible.
    attrs = list(row_attrs or ())
    attrs += [{}] * (len(rows) - len(attrs))
    body = "".join(
        "<tr"
        + "".join(f' {key}="{escape(value)}"' for key, value in extra.items())
        + ">"
        + "".join(
            f'<td class="num">{escape(cell)}</td>' if i in numeric else f"<td>{escape(cell)}</td>"
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row, extra in zip(rows, attrs)
    )
    if not body:
        return f'<div class="empty">{escape(_("Nothing to show."))}</div>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def legend(entries: Iterable[tuple[str, str]], note: str = "") -> str:
    """Render a color legend.

    Args:
        entries: ``(color, label)`` pairs.
        note: Optional trailing explanation.

    Returns:
        The legend markup.
    """
    swatches = "".join(
        f'<span><i style="background:{escape(color)}"></i>{escape(label)}</span>'
        for color, label in entries
    )
    tail = f"<span>{escape(note)}</span>" if note else ""
    return f'<div class="legend">{swatches}{tail}</div>'


def summary(pairs: Mapping[str, object]) -> str:
    """Render a compact label/value summary line.

    Args:
        pairs: Label to value.

    Returns:
        The summary markup.
    """
    return (
        legend([], "")
        if not pairs
        else (
            '<div class="legend">'
            + "".join(
                f"<span>{escape(label)}: <b>{escape(value)}</b></span>"
                for label, value in pairs.items()
            )
            + "</div>"
        )
    )
