"""Tests for the Markdown renderer behind the in-app Help.

Two things matter more than any individual construct rendering correctly.

**Nothing is passed through as HTML.** These documents are ours, but the
renderer is general, and one that emits whatever it is handed cannot safely be
pointed at anything else later.

**The project's own documents render completely.** The subset is deliberately
small and defined by what README, QUICKSTART and the rest actually use, so those
files are the specification -- they are rendered here and checked for the
tell-tale leftovers of a construct the parser walked past.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from wraithguard.gui import HELP_DOCUMENTS
from wraithguard.viz.docs import DOCS_CSS, docs_page, inline, render_markdown

#: The project's own documents, which the renderer exists to display.
DOCS = ("README.md", "QUICKSTART.md", "CHANGELOG.md", "MLOX_RULES.md")

ROOT = Path(__file__).resolve().parent.parent

FIXED = datetime(2026, 7, 27, 9, 30, 15)  # noqa: DTZ001 - local clock, matching the caller


def body_of(text: str) -> str:
    """Render Markdown and return just the body.

    Args:
        text: Markdown source.

    Returns:
        The rendered HTML body.
    """
    body, _headings = render_markdown(text)
    return body


class TestInline:
    """Inline constructs, which is where a Markdown parser usually goes wrong."""

    def test_bold_and_italic(self) -> None:
        """The two most common, and the two most easily confused."""
        assert inline("**bold**") == "<strong>bold</strong>"
        assert inline("*italic*") == "<em>italic</em>"

    def test_bold_inside_italic_does_not_leak_asterisks(self) -> None:
        """The real defect this was written against.

        ``*a **b** c*`` closed the italic on the first asterisk of ``**b``,
        leaving stray asterisks in the rendered page. QUICKSTART has exactly
        this sentence in it.
        """
        assert inline("*a **b** c*") == "<em>a <strong>b</strong> c</em>"
        assert "*" not in inline("*Export writes nothing while **Dry run** is checked*")

    def test_code_spans_are_literal(self) -> None:
        """Markup inside backticks is text, which is the point of backticks."""
        assert inline("`a **b** c`") == "<code>a **b** c</code>"

    def test_underscores_inside_a_word_are_not_emphasis(self) -> None:
        """``snake_case_name`` is a name, not italics."""
        assert inline("snake_case_name") == "snake_case_name"

    def test_strikethrough(self) -> None:
        """Used by CHANGELOG and CODE_REVIEW to mark items as done."""
        assert inline("~~done~~") == "<del>done</del>"

    def test_underscore_emphasis(self) -> None:
        """A whole word wrapped in single underscores is italics."""
        assert inline("_italic_") == "<em>italic</em>"

    def test_an_image_with_a_safe_source_renders_a_tag(self) -> None:
        """An image with an http(s) source becomes an <img> with escaped alt."""
        rendered = inline("![a cat](https://example.com/cat.png)")
        assert '<img src="https://example.com/cat.png"' in rendered
        assert 'alt="a cat"' in rendered

    def test_an_image_with_a_dangerous_source_falls_back_to_alt(self) -> None:
        """An image whose source is a rejected scheme shows only its alt text."""
        rendered = inline("![just text](javascript:alert(1))")
        assert "<img" not in rendered
        assert "just text" in rendered

    def test_links_render(self) -> None:
        """A link that does not work is worse than plain text."""
        assert inline("[text](https://example.com)") == '<a href="https://example.com">text</a>'

    def test_relative_links_are_allowed(self) -> None:
        """The documents link to each other."""
        assert '<a href="QUICKSTART.md">' in inline("[qs](QUICKSTART.md)")

    @pytest.mark.parametrize(
        "url",
        ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html;base64,PHNjcmlwdD4="],
    )
    def test_dangerous_schemes_become_plain_text(self, url: str) -> None:
        """A renderer that emits any URL it is handed cannot be reused safely.

        Args:
            url: The scheme to reject.
        """
        rendered = inline(f"[click]({url})")
        assert "href" not in rendered
        assert "javascript" not in rendered.lower()
        assert "data:" not in rendered

    def test_angle_brackets_are_escaped(self) -> None:
        """``<VER>`` appears in the README as ordinary text."""
        assert inline("a <VER> b") == "a &lt;VER&gt; b"

    def test_ampersand_is_escaped_once(self) -> None:
        """Double-encoding turns ``&`` into ``&amp;`` on screen."""
        assert inline("a & b") == "a &amp; b"
        assert "&amp;amp;" not in inline("a & b")


class TestBlocks:
    """Block-level constructs."""

    def test_headings_get_ids(self) -> None:
        """The contents sidebar links to them."""
        body, headings = render_markdown("# Title\n\n## Section\n")
        assert '<h2 id="section">Section</h2>' in body
        assert headings == [(1, "Title", "title"), (2, "Section", "section")]

    def test_duplicate_headings_get_unique_ids(self) -> None:
        """Two "Notes" sections must not both own ``#notes``."""
        _body, headings = render_markdown("## Notes\n\n## Notes\n")
        assert [h[2] for h in headings] == ["notes", "notes-2"]

    def test_a_paragraph_ends_at_a_fence(self) -> None:
        """A code fence on the next line closes the paragraph before it."""
        body = body_of("a paragraph\n```\ncode\n```\n")
        assert "<p>a paragraph</p>" in body
        assert "<pre>" in body

    def test_a_paragraph_ends_at_a_blockquote(self) -> None:
        """A blockquote marker closes the paragraph above it."""
        body = body_of("a paragraph\n> quoted\n")
        assert "<p>a paragraph</p>" in body
        assert "<blockquote>" in body

    def test_a_paragraph_ends_at_a_table(self) -> None:
        """A table that starts on the next line closes the paragraph above it."""
        body = body_of("a paragraph\n| h1 | h2 |\n| --- | --- |\n| a | b |\n")
        assert "<p>a paragraph</p>" in body
        assert "<table>" in body

    def test_a_paragraph_ends_at_a_bullet_list(self) -> None:
        """A bullet on the next line closes the paragraph above it."""
        body = body_of("a paragraph\n- item\n")
        assert "<p>a paragraph</p>" in body
        assert "<ul>" in body

    def test_switching_marker_type_starts_a_new_list(self) -> None:
        """A numbered item right after a bullet ends the bulleted list and opens
        an ordered one, rather than mixing them into one."""
        body = body_of("- bullet item\n1. numbered item\n")
        assert "<ul>" in body
        assert "<ol>" in body

    def test_fenced_code_is_verbatim(self) -> None:
        """Markup inside a code block is content, not markup."""
        body = body_of("```\n# not a heading\n**not bold**\n```\n")
        assert "<pre><code># not a heading\n**not bold**</code></pre>" in body

    def test_unterminated_fence_still_renders(self) -> None:
        """Most of a document beats refusing to render it."""
        body = body_of("```\nstuck open\n")
        assert "stuck open" in body
        assert body.startswith("<pre>")

    def test_tables_need_a_separator_row(self) -> None:
        """A prose line containing a pipe is not a table."""
        assert "<table" not in body_of("a | b is a choice\n")
        assert "<table" in body_of("| a | b |\n|---|---|\n| 1 | 2 |\n")

    def test_table_cells_render_inline_markup(self) -> None:
        """The README's tables are full of code spans."""
        body = body_of("| a | b |\n|---|---|\n| `x` | **y** |\n")
        assert "<td><code>x</code></td>" in body
        assert "<td><strong>y</strong></td>" in body

    def test_nested_lists_nest(self) -> None:
        """Flattening them loses the structure that made them lists."""
        body = body_of("- one\n  - inner\n- two\n")
        assert body.count("<ul>") == 2
        assert "<li>one<ul><li>inner</li></ul></li>" in body

    def test_ordered_lists_are_ol(self) -> None:
        """QUICKSTART is a numbered procedure."""
        assert body_of("1. first\n2. second\n").startswith("<ol>")

    def test_block_quotes_render_their_contents_as_markdown(self) -> None:
        """The README's one quote contains emphasis."""
        body = body_of("> a **warning**\n")
        assert "<blockquote>" in body
        assert "<strong>warning</strong>" in body

    def test_horizontal_rules(self) -> None:
        """Used as section separators throughout."""
        assert "<hr>" in body_of("a\n\n---\n\nb\n")

    def test_paragraphs_join_wrapped_lines(self) -> None:
        """These files are hard-wrapped; rendering must not be."""
        assert body_of("one\ntwo\n") == "<p>one two</p>"

    def test_crlf_input(self) -> None:
        """A document edited on Windows must render the same."""
        assert body_of("# Title\r\n\r\ntext\r\n") == body_of("# Title\n\ntext\n")


class TestPage:
    """The assembled help page."""

    def test_is_self_contained(self) -> None:
        """The help has to work offline, from a frozen build."""
        page = docs_page("Read me", "# Read me\n\ntext\n", "README.md")
        assert "<script" not in page
        assert "<link" not in page
        assert "cdn" not in page.lower()

    def test_stamped_and_attributed(self) -> None:
        """Which document, rendered when."""
        page = docs_page("Read me", "# T\n", "README.md", generated_at=FIXED)
        assert "README.md" in page
        assert "2026-07-27 09:30:15" in page

    def test_contents_sidebar_appears_for_a_long_document(self) -> None:
        """A 674-line README without navigation is a wall."""
        page = docs_page("T", "## A\n\n## B\n\n## C\n")
        assert "<nav>" in page
        assert page.count('<a href="#') == 3

    def test_no_sidebar_for_a_short_document(self) -> None:
        """A contents list of one entry is furniture, not navigation."""
        assert "<nav>" not in docs_page("T", "## Only\n\ntext\n")

    def test_title_is_escaped(self) -> None:
        """The title reaches both the ``<title>`` and the heading."""
        page = docs_page("<b>x</b>", "text\n")
        assert "<b>x</b>" not in page
        assert "&lt;b&gt;x&lt;/b&gt;" in page

    def test_css_is_present_and_not_a_template(self) -> None:
        """Styling is a constant, so it needs no brace escaping."""
        assert "{{" not in DOCS_CSS
        assert DOCS_CSS in docs_page("T", "text\n")


class TestProjectDocuments:
    """The documents the renderer exists for are its real specification."""

    @pytest.mark.parametrize("name", DOCS)
    def test_document_renders_without_leftover_markup(self, name: str) -> None:
        """A construct the parser walked past shows up as its own marker.

        Args:
            name: The document to render.
        """
        source = ROOT / name
        if not source.is_file():  # pragma: no cover - a trimmed checkout
            pytest.skip(f"{name} is not in this checkout")
        body = body_of(source.read_text(encoding="utf-8"))
        for leftover in ("**", "```", "~~", "|---"):
            assert leftover not in body, f"{name}: unrendered {leftover!r}"

    @pytest.mark.parametrize("name", DOCS)
    def test_document_produces_headings(self, name: str) -> None:
        """No headings means the whole document came out as one blob.

        Args:
            name: The document to render.
        """
        source = ROOT / name
        if not source.is_file():  # pragma: no cover - a trimmed checkout
            pytest.skip(f"{name} is not in this checkout")
        _body, headings = render_markdown(source.read_text(encoding="utf-8"))
        assert len(headings) >= 5

    def test_readme_keeps_its_tables(self) -> None:
        """The README's option tables are most of its reference value."""
        source = ROOT / "README.md"
        if not source.is_file():  # pragma: no cover - a trimmed checkout
            pytest.skip("README.md is not in this checkout")
        body = body_of(source.read_text(encoding="utf-8"))
        assert body.count("<table>") >= 2
        assert re.search(r"<td>.+</td>", body)


class TestHelpMenuMatchesWhatShips:
    """The Help menu is a promise that a document is there to open.

    Two ways to break that promise, and neither shows up until someone clicks:
    listing a document that is not in the source tree, or adding one to the menu
    without adding it to the build manifest -- which works perfectly from a
    checkout and fails only in the frozen build, where nobody is running tests.
    """

    #: The PyInstaller data files, as the packaging front-end records them.
    MANIFEST = ROOT / "build" / "auto-py-to-exe_build.json"

    @pytest.mark.parametrize("name", HELP_DOCUMENTS)
    def test_every_offered_document_exists(self, name: str) -> None:
        """A menu entry with no file behind it is a dead end.

        Args:
            name: The document the Help menu offers.
        """
        assert (ROOT / name).is_file(), f"Help offers {name}, which is not in the tree"

    @pytest.mark.parametrize("name", HELP_DOCUMENTS)
    def test_every_offered_document_is_bundled(self, name: str) -> None:
        """Otherwise Help works in development and is empty in the release.

        Args:
            name: The document the Help menu offers.
        """
        if not self.MANIFEST.is_file():  # pragma: no cover - a trimmed checkout
            pytest.skip("the build manifest is not in this checkout")
        datas = [
            entry.get("value", "")
            for entry in json.loads(self.MANIFEST.read_text(encoding="utf-8"))["pyinstallerOptions"]
            if entry.get("optionDest") == "datas"
        ]
        assert any(
            value.split(";")[0].endswith(f"/{name}") for value in datas
        ), f"{name} is in the Help menu but not in the build manifest"
