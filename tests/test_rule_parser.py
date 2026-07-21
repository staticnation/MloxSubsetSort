"""Tests for the mlox rule-file parser.

These lock in the parser bugs found by auditing against mlox's own
``ruleParser.py`` and plox's ``parser.rs`` -- most importantly that plugin
names contain spaces, which an earlier whitespace-splitting implementation
silently destroyed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mlox_subset.plugins import PluginFileIndex
from mlox_subset.rules import check_predicates, load_rule_blocks, parse_mlox_file


def parse(text: str, tmp_path: Path, *, bom: bool = False):
    """Write ``text`` to a rules file and parse it."""
    path = tmp_path / "rules.txt"
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
    return parse_mlox_file(path)


class TestOrderBlocks:
    def test_multi_word_plugin_names_survive(self, tmp_path, capsys):
        """Regression: names were split on whitespace, shattering real names."""
        blocks = parse(
            "[Order]\nFriends & Frens - Vvardenfell.ESP\n" "Friends & Frens - TR.ESP\n",
            tmp_path,
        )
        assert blocks == [
            ("order", ["Friends & Frens - Vvardenfell.ESP", "Friends & Frens - TR.ESP"])
        ]

    def test_comments_and_junk_lines_are_dropped(self, tmp_path, capsys):
        blocks = parse(
            "[Order]\n"
            "A.esp ; trailing comment\n"
            "; whole-line comment\n"
            "a line with no plugin name\n"
            "B.esp\n",
            tmp_path,
        )
        assert blocks == [("order", ["A.esp", "B.esp"])]

    @pytest.mark.parametrize(
        "name",
        ["Wares*.esp", "plugin-?.esp", "Blasphemous <VER>.esp", "Foo.omwaddon", "bar.omwscripts"],
    )
    def test_wildcards_and_openmw_extensions(self, tmp_path, name, capsys):
        blocks = parse(f"[Order]\n{name}\nOther.esp\n", tmp_path)
        assert blocks[0][1][0] == name

    def test_trailing_junk_after_name_is_trimmed(self, tmp_path, capsys):
        blocks = parse("[Order]\nA.esp\nB.esp extra trailing junk\n", tmp_path)
        assert blocks == [("order", ["A.esp", "B.esp"])]

    def test_names_may_share_the_header_line(self, tmp_path, capsys):
        """plox tokenises several extension-delimited names per line."""
        blocks = parse("[Order] A.esp B.esp\nC.esp\n", tmp_path)
        assert blocks == [("order", ["A.esp", "B.esp", "C.esp"])]


class TestBlockDelimiting:
    def test_header_must_start_a_line(self, tmp_path, capsys):
        """A rule name mentioned mid-sentence must not start a new block."""
        blocks = parse(
            "[Note]\n  see the [Order] section for details\n[Order]\nA.esp\nB.esp\n",
            tmp_path,
        )
        assert blocks == [("order", ["A.esp", "B.esp"])]

    def test_nearstart_and_nearend_are_not_order_chains(self, tmp_path, capsys):
        """They are per-plugin position hints; chaining them invents edges."""
        blocks = parse(
            "[NearStart]\nMorrowind.esm\nTribunal.esm\n"
            "[NearEnd]\nMashed Lists.esp\nMerged Objects.esp\n",
            tmp_path,
        )
        assert blocks == [
            ("nearstart", ["Morrowind.esm", "Tribunal.esm"]),
            ("nearend", ["Mashed Lists.esp", "Merged Objects.esp"]),
        ]

    def test_utf8_bom_does_not_hide_the_first_header(self, tmp_path, capsys):
        blocks = parse("[Order]\nA.esp\nB.esp\n", tmp_path, bom=True)
        assert blocks == [("order", ["A.esp", "B.esp"])]

    def test_version_and_warning_blocks_delimit_order_blocks(self, tmp_path, capsys):
        blocks = parse(
            "[Version 2026-01-01]\n[Order]\nA.esp\nB.esp\n"
            "[Conflict]\nX.esp\nY.esp\n[Order]\nC.esp\nD.esp\n",
            tmp_path,
        )
        assert blocks == [("order", ["A.esp", "B.esp"]), ("order", ["C.esp", "D.esp"])]


class TestRulePriority:
    def test_load_rule_blocks_separates_kinds_and_keeps_priority(self, tmp_path, capsys):
        base = tmp_path / "mlox_base.txt"
        user = tmp_path / "mlox_user.txt"
        base.write_text("[Order]\nA.esp\nB.esp\n[NearEnd]\nZ.esp\n", encoding="utf-8")
        user.write_text("[Order]\nC.esp\nD.esp\n[NearStart]\nS.esp\n", encoding="utf-8")

        order, nearstart, nearend = load_rule_blocks([base, user])

        assert ["A.esp", "B.esp"] in [names for names, _ in order]
        assert ["C.esp", "D.esp"] in [names for names, _ in order]
        assert nearstart == ["S.esp"]
        assert nearend == ["Z.esp"]
        # priority == index of the file in the list; later file wins conflicts
        priorities = {tuple(names): prio for names, prio in order}
        assert priorities[("A.esp", "B.esp")] < priorities[("C.esp", "D.esp")]


class TestPredicateMessageSplitting:
    def test_bracket_continuation_is_not_message_text(self):
        """Regression: an [ALL ...] spanning indented lines was truncated, so
        the note fired without its full condition being satisfied."""
        rules = "[Note]\n" " you use A and C\n" "[ALL A.esp\n" "\t [NOT B.esp]\n" "\t C.esm]\n"
        # C.esm missing -> the ALL is false -> no warning
        assert check_predicates(rules, ["A.esp"]) == []
        # all three conditions satisfied -> fires
        fired = check_predicates(rules, ["A.esp", "C.esm"])
        assert len(fired) == 1
        # the condition must not leak into the printed message body (the
        # "[NOTE]" prefix itself legitimately starts with "[NOT")
        message = fired[0].split("]", 1)[1]
        assert "NOT B.esp" not in message
        assert "C.esm]" not in message
        # and the NOT must be honoured
        assert check_predicates(rules, ["A.esp", "B.esp", "C.esm"]) == []


class TestSizeAndDescAgainstMissingPlugins:
    """`[SIZE]`/`[DESC]` must distinguish "no datadir" from "plugin absent".

    mlox gates its "assume true" on ``self.datadir is None`` -- no data
    directory at all, where the test degrades to mere file existence. It does
    *not* apply when the directories are readable and one particular plugin is
    simply not in them.

    Conflating the two made every such predicate assert a size or description
    match for a plugin known not to be on disk, which can fire a warning the
    tool cannot substantiate.
    """

    @staticmethod
    def _predicates():
        """The evaluator module. The `_eval_*` helpers are private to it, so
        they are reached through the module rather than imported by name."""
        from mlox_subset.rules import predicates

        return predicates

    def test_no_datadir_assumes_true(self):
        """With no index at all, mlox errs on the side of existence."""
        assert self._predicates()._eval_size("", 123, "Foo.esp", {"foo.esp"}, None) is True
        assert self._predicates()._eval_desc("", "anything", "Foo.esp", {"foo.esp"}, None) is True

    def test_unusable_index_assumes_true(self):
        """An index over unreadable directories is the same "cannot see" case."""
        index = PluginFileIndex(["/definitely/not/a/real/directory"])
        assert index.usable is False
        assert self._predicates()._eval_size("", 123, "Foo.esp", {"foo.esp"}, index) is True
        assert self._predicates()._eval_desc("", "anything", "Foo.esp", {"foo.esp"}, index) is True

    def test_readable_datadir_missing_plugin_does_not_assume_true(self, tmp_path):
        """The regression: readable dirs + absent plugin must NOT assert a match.

        The directory holds a different plugin, so the index is usable and the
        answer for ``Foo.esp`` is genuinely "not here" rather than "cannot
        tell". Returning True here would substantiate a claim about a file that
        does not exist.
        """
        (tmp_path / "Other.esp").write_bytes(b"TES3" + b"\x00" * 400)
        index = PluginFileIndex([tmp_path])
        assert index.usable is True
        assert self._predicates()._eval_size("", 123, "Foo.esp", {"foo.esp"}, index) is False
        assert self._predicates()._eval_desc("", "anything", "Foo.esp", {"foo.esp"}, index) is False

    def test_readable_datadir_present_plugin_still_compares(self, tmp_path):
        """The fix must not break the case that actually works."""
        plugin = tmp_path / "Foo.esp"
        plugin.write_bytes(b"TES3" + b"\x00" * 400)
        index = PluginFileIndex([tmp_path])
        size = plugin.stat().st_size
        assert self._predicates()._eval_size("", size, "Foo.esp", {"foo.esp"}, index) is True
        assert self._predicates()._eval_size("", size + 1, "Foo.esp", {"foo.esp"}, index) is False
        assert self._predicates()._eval_size("!", size + 1, "Foo.esp", {"foo.esp"}, index) is True
