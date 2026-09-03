"""Tests for ``tools/diff_roundtrip_json.py``, the *what-changed* round-trip diff.

Where ``check_plugin_roundtrip`` says whether a plugin survives an
esp->json->esp trip, this says which fields moved and how often, flattening list
indices so the same field in ten thousand records tallies as one finding. The
structural diff (``_walk``), the record-typing (``_kind``), the loader's error
handling and the ranked report (``compare``) are all pure once the JSON exists,
so they are driven directly; ``main``'s conversion pipeline is exercised with the
converter faked, the same way the other tool tests avoid needing a real tes3conv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import diff_roundtrip_json as diff
from tools.diff_roundtrip_json import _abbreviate, _kind, _load, _walk, compare, main


class TestAbbreviate:
    def test_a_short_value_is_shown_whole(self) -> None:
        assert _abbreviate([1, 2]) == "[1, 2]"

    def test_a_long_value_is_truncated_with_an_ellipsis(self) -> None:
        out = _abbreviate("x" * 200)
        assert out.endswith("...")
        assert len(out) == diff._WIDTH


class TestKind:
    def test_a_type_field_names_the_record(self) -> None:
        assert _kind({"type": "Cell"}) == "Cell"

    def test_an_alternate_capitalisation_is_accepted(self) -> None:
        assert _kind({"Type": "Npc"}) == "Npc"

    def test_a_record_without_a_type_is_a_question_mark(self) -> None:
        assert _kind({"grid": [0, 0]}) == "?"

    def test_a_non_dict_record_is_a_question_mark(self) -> None:
        assert _kind([1, 2, 3]) == "?"


class TestWalk:
    def test_identical_values_record_nothing(self) -> None:
        found: dict = {}
        _walk({"a": 1}, {"a": 1}, "R", found)
        assert found == {}

    def test_a_scalar_difference_is_recorded_at_its_path(self) -> None:
        found: dict = {}
        _walk({"scale": 1.0}, {"scale": 1.5}, "Cell", found)
        assert "Cell.scale" in found
        assert found["Cell.scale"] == [("1.0", "1.5")]

    def test_a_key_only_in_the_second_pass_is_added(self) -> None:
        found: dict = {}
        _walk({}, {"extra": 9}, "R", found)
        assert "R.extra (added)" in found

    def test_a_key_missing_from_the_second_pass_is_lost(self) -> None:
        found: dict = {}
        _walk({"script": "x"}, {}, "R", found)
        assert "R.script (LOST)" in found

    def test_a_list_length_change_is_named(self) -> None:
        found: dict = {}
        _walk([1, 2], [1], "R.items", found)
        assert "R.items[] (length)" in found

    def test_list_indices_are_flattened_so_one_field_is_one_finding(self) -> None:
        """The same field differing in every element tallies once, not per index."""
        found: dict = {}
        before = [{"scale": 1.0}, {"scale": 1.0}, {"scale": 1.0}]
        after = [{"scale": 2.0}, {"scale": 2.0}, {"scale": 2.0}]
        _walk(before, after, "Cell.refs", found)
        assert list(found) == ["Cell.refs[].scale"]
        assert len(found["Cell.refs[].scale"]) == 3  # three examples under one path

    def test_a_difference_below_max_depth_is_grouped_at_its_ancestor(self) -> None:
        """Past the depth cap the whole differing subtree is recorded at one path."""
        found: dict = {}
        deep_before: object = 0
        deep_after: object = 1
        for _ in range(diff._MAX_DEPTH + 2):
            deep_before = {"k": deep_before}
            deep_after = {"k": deep_after}
        _walk(deep_before, deep_after, "R", found)
        assert found  # recorded once, truncated, rather than recursing forever


class TestLoad:
    def test_a_record_list_is_returned(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text(json.dumps([{"type": "Cell"}]), encoding="utf-8")
        assert _load(path) == [{"type": "Cell"}]

    def test_a_non_list_document_is_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        with pytest.raises(SystemExit, match="not a record list"):
            _load(path)

    def test_unparseable_json_is_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="could not read"):
            _load(path)

    def test_running_out_of_memory_is_a_clean_exit(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "d.json"
        path.write_text("[]", encoding="utf-8")

        def oom(*_a: object, **_k: object) -> object:
            raise MemoryError

        monkeypatch.setattr(diff.json, "loads", oom)
        with pytest.raises(SystemExit, match="ran out of memory"):
            _load(path)


class TestCompare:
    def _pair(self, tmp_path: Path, before: list, after: list) -> tuple[Path, Path]:
        first, second = tmp_path / "first.json", tmp_path / "second.json"
        first.write_text(json.dumps(before), encoding="utf-8")
        second.write_text(json.dumps(after), encoding="utf-8")
        return first, second

    def test_identical_conversions_pass(self, tmp_path: Path, capsys) -> None:
        recs = [{"type": "Cell", "scale": 1.0}]
        first, second = self._pair(tmp_path, recs, recs)
        assert compare(first, second, 3) == 0
        assert "Nothing was lost" in capsys.readouterr().out

    def test_a_changed_record_is_reported_with_a_uniform_tally(
        self, tmp_path: Path, capsys
    ) -> None:
        """A field that lost the same value everywhere prints the one-value summary."""
        before = [{"type": "Cell", "scale": 1.0} for _ in range(4)]
        after = [{"type": "Cell", "scale": 2.0} for _ in range(4)]
        first, second = self._pair(tmp_path, before, after)
        assert compare(first, second, 3) == 1
        out = capsys.readouterr().out
        assert "4 record(s) changed" in out
        assert "every one of the 4 was 1.0" in out

    def test_multiple_distinct_before_values_are_ranked_and_truncated(
        self, tmp_path: Path, capsys
    ) -> None:
        """More distinct before-values than ``examples`` prints an ``and N more``."""
        before = [{"type": "Cell", "scale": float(i)} for i in range(6)]
        after = [{"type": "Cell", "scale": 99.0} for _ in range(6)]
        first, second = self._pair(tmp_path, before, after)
        assert compare(first, second, 2) == 1
        out = capsys.readouterr().out
        assert "distinct value(s) before" in out
        assert "... and" in out  # six values, only two shown

    def test_a_few_distinct_values_are_all_shown_without_a_more_tail(
        self, tmp_path: Path, capsys
    ) -> None:
        """Fewer distinct before-values than ``examples`` fit, so no truncation note."""
        before = [{"type": "Cell", "scale": 1.0}, {"type": "Cell", "scale": 2.0}]
        after = [{"type": "Cell", "scale": 9.0}, {"type": "Cell", "scale": 9.0}]
        first, second = self._pair(tmp_path, before, after)
        assert compare(first, second, 3) == 1  # 2 distinct values, examples=3
        out = capsys.readouterr().out
        assert "distinct value(s) before" in out
        assert "... and" not in out  # both values fit, nothing truncated

    def test_a_record_count_change_is_flagged(self, tmp_path: Path, capsys) -> None:
        before = [{"type": "Cell"}, {"type": "Npc"}]
        after = [{"type": "Cell"}]
        first, second = self._pair(tmp_path, before, after)
        assert compare(first, second, 3) == 1
        assert "RECORD COUNT CHANGED" in capsys.readouterr().out


class TestMain:
    def test_a_missing_converter_exits_two(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setattr(diff, "find_tes3conv", lambda _arg: None)
        plugin = tmp_path / "P.esp"
        plugin.write_bytes(b"TES3")
        assert main([str(plugin)]) == 2
        assert "tes3conv was not found" in capsys.readouterr().err

    def test_a_missing_plugin_exits_two(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setattr(diff, "find_tes3conv", lambda _arg: "tes3conv")
        assert main([str(tmp_path / "gone.esp")]) == 2
        assert "no such plugin" in capsys.readouterr().err

    def test_a_conversion_failure_exits_two(self, tmp_path: Path, capsys, monkeypatch) -> None:
        plugin = tmp_path / "P.esp"
        plugin.write_bytes(b"TES3")
        monkeypatch.setattr(diff, "find_tes3conv", lambda _arg: "tes3conv")
        monkeypatch.setattr(diff, "convert", lambda *_a, **_k: "converter blew up")
        assert main([str(plugin)]) == 2
        assert "conversion failed" in capsys.readouterr().err

    def test_a_full_run_with_a_faked_converter_diffs_the_two_passes(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """The whole pipeline, with ``convert`` writing canned JSON, ends in a diff."""
        plugin = tmp_path / "P.esp"
        plugin.write_bytes(b"TES3")
        monkeypatch.setattr(diff, "find_tes3conv", lambda _arg: "tes3conv")

        def fake_convert(_tool: str, source: Path, target: Path) -> str:
            # first.json and second.json get differing scales; back.esp gets bytes.
            if target.suffix == ".json" and target.name == "first.json":
                target.write_text(json.dumps([{"type": "Cell", "scale": 1.0}]), encoding="utf-8")
            elif target.suffix == ".json":
                target.write_text(json.dumps([{"type": "Cell", "scale": 2.0}]), encoding="utf-8")
            else:
                target.write_bytes(b"TES3 back")
            return ""

        monkeypatch.setattr(diff, "convert", fake_convert)
        assert main([str(plugin), "--examples", "2"]) == 1
        assert "1 record(s) changed" in capsys.readouterr().out
