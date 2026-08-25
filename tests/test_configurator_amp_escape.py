"""OpenMW's ``&`` escaping in quoted ``data=`` values.

Regression for a reported bug: a folder whose name contains ``&`` was written
twice -- once as ``&`` and once as ``&&`` -- in both ``openmw.cfg`` and the
customisations TOML. Inside double quotes OpenMW uses ``&`` as its escape
character, so a literal ``&`` must be written ``&&``; the tool never escaped or
unescaped it, so an existing (escaped) ``&&`` path never matched a new
single-``&`` insert and a duplicate was added. These pin the escape/unescape
round-trip, the read/write symmetry, and the dedup that the bug slipped past.
"""

from __future__ import annotations

from wraithguard.configurator.cfglines import (
    escape_cfg_value,
    extract_data_path_value,
    format_data_line,
    normalize_data_path,
    unescape_cfg_value,
)
from wraithguard.configurator.datapaths import insert_data_paths

_PATH = r"C:\Mods\Tomb of the Snow Prince & Friends"


class TestEscaping:
    def test_amp_and_quote_round_trip(self) -> None:
        assert escape_cfg_value(_PATH) == r"C:\Mods\Tomb of the Snow Prince && Friends"
        assert unescape_cfg_value(escape_cfg_value(_PATH)) == _PATH

    def test_a_literal_quote_escapes_and_returns(self) -> None:
        value = 'name "quoted" & more'
        assert escape_cfg_value(value) == 'name &"quoted&" && more'
        assert unescape_cfg_value(escape_cfg_value(value)) == value

    def test_escaping_is_stable_under_reescape(self) -> None:
        # A path already containing '&&' must still round-trip.
        weird = "a && b"
        assert unescape_cfg_value(escape_cfg_value(weird)) == weird


class TestReadWriteSymmetry:
    def test_extract_unescapes_a_quoted_value(self) -> None:
        line = f'data="{escape_cfg_value(_PATH)}"'
        assert extract_data_path_value(line) == _PATH

    def test_extract_leaves_a_bare_value_literal(self) -> None:
        # A bare (unquoted) line is literal -- '&' is not an escape there.
        assert extract_data_path_value(f"data={_PATH}") == _PATH

    def test_format_escapes_only_when_quoted(self) -> None:
        assert format_data_line(_PATH, quoted=True) == f'data="{escape_cfg_value(_PATH)}"'
        assert format_data_line(_PATH, quoted=False) == f"data={_PATH}"

    def test_write_then_read_a_quoted_line_is_identity(self) -> None:
        line = format_data_line(_PATH, quoted=True)
        assert extract_data_path_value(line) == _PATH


class TestDedup:
    def test_quoted_and_bare_forms_of_the_same_path_normalise_equal(self) -> None:
        quoted = f'data="{escape_cfg_value(_PATH)}"'
        assert normalize_data_path(extract_data_path_value(quoted)) == normalize_data_path(_PATH)

    def test_insert_is_skipped_when_already_present_as_escaped_line(self) -> None:
        # The bug: an existing escaped '&&' line and a new single-'&' insert.
        existing = [f'data="{escape_cfg_value(_PATH)}"']
        result = insert_data_paths(existing, [{"value": _PATH, "after": None, "before": None}])
        # Exactly one line, the original -- no duplicate added.
        assert [line for line, _is_new, _src in result] == existing
        assert not any(is_new for _line, is_new, _src in result)

    def test_a_genuinely_new_amp_folder_is_still_inserted(self) -> None:
        existing = [r"data=C:\Mods\Something Else"]
        other = r"D:\More\A & B"
        result = insert_data_paths(existing, [{"value": other, "after": None, "before": None}])
        new_lines = [line for line, is_new, _src in result if is_new]
        assert new_lines == [f"data={other}"]  # bare file -> bare, literal '&'


class TestQuotedFileGetsEscapedOutput:
    def test_new_insert_into_a_quoted_cfg_is_escaped(self) -> None:
        # A predominantly-quoted cfg -> new lines are quoted AND escaped.
        existing = [r'data="C:\Games\Data Files"', r'data="C:\Games\Mods\A"']
        result = insert_data_paths(existing, [{"value": _PATH, "after": None, "before": None}])
        new_lines = [line for line, is_new, _src in result if is_new]
        assert new_lines == [f'data="{escape_cfg_value(_PATH)}"']
        assert "&&" in new_lines[0]

    def test_reading_that_escaped_line_recovers_the_real_path(self) -> None:
        existing = [r'data="C:\Games\Data Files"']
        result = insert_data_paths(existing, [{"value": _PATH, "after": None, "before": None}])
        written = next(line for line, is_new, _src in result if is_new)
        assert extract_data_path_value(written) == _PATH


def test_trailing_lone_amp_does_not_crash() -> None:
    # Defensive: a value ending in a lone '&' (malformed escape) drops it, no raise.
    assert unescape_cfg_value("abc&") == "abc"
