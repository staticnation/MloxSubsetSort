"""build_record_patch's orchestration -- the guard rails, the progress lines
each stage contributes, and _write's real subprocess call to tes3conv.

test_patch_merge.py already exercises the merge-vs-selection clash refusal
and dry_run paths as a side effect of testing merging itself. What's new
here: the "nothing selected" guard, collect()'s own PatchError surfacing as
PatchServiceError, the dialogue-risk/position-shift/anchor note lines (never
triggered by the simple two-plugin fixture those tests use), the missing-
master-size guard, EmitError surfacing, and _write end to end -- including
an actual tes3conv invocation, not just dry_run=True.

Reuses test_patch_merge.py's CASTLE/OTHER/PATCH fixture shape rather than
inventing a new one, so both files agree on what a real plugin pair looks
like.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import pytest

from wraithguard.patch import Selection
from wraithguard.patch.service import PatchServiceError, build_record_patch

if TYPE_CHECKING:
    from pathlib import Path

CASTLE: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1]]},
    {
        "type": "Cell",
        "flags": "",
        "name": "",
        "data": {"grid": [7, 22], "flags": "HAS_WATER"},
        "references": [
            {
                "mast_index": 0,
                "refr_index": 1,
                "id": "castle_own_thing",
                "temporary": False,
                "translation": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
            }
        ],
    },
    {"type": "GameSetting", "id": "sCastleName", "value": "Castle"},
]

OTHER: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1]]},
    {
        "type": "Cell",
        "flags": "",
        "name": "",
        "data": {"grid": [7, 22], "flags": "RESTING_IS_ILLEGAL"},
        "references": [
            {
                "mast_index": 0,
                "refr_index": 1,
                "id": "other_own_thing",
                "temporary": False,
                "translation": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
            }
        ],
    },
    {"type": "GameSetting", "id": "sOtherName", "value": "Other"},
]

SOURCES: Final[dict[str, list[dict[str, Any]]]] = {"Castle.esp": CASTLE, "Other.esp": OTHER}
PATCH: Final[list[str]] = ["Morrowind.esm", "Castle.esp", "Other.esp"]
SIZES: Final[dict[str, int]] = dict.fromkeys(PATCH, 1)

#: A previous session's build of the same patch: one record with nothing left
#: to override, and a master (Other.esp) that this session's own selections
#: never touch -- so the only way it gets declared is via `carried`.
OLD_BUILD: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1], ["Other.esp", 1]]},
    {"type": "GameSetting", "id": "sFromLastSession", "value": "Kept"},
]

#: A previous build that also carries the *same* record this session is about
#: to re-decide, to prove the fresh answer wins rather than sitting next to
#: the stale one.
OLD_BUILD_WITH_OVERRIDE: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1]]},
    {"type": "GameSetting", "id": "sCastleName", "value": "Old Value"},
]

#: A dialogue chain where Override.esp repositions r2 to the end, and the
#: patch carries Base.esp's *original* placement of r2 instead -- the one
#: fixture that triggers all three post-sort notes (dialogue_position_risk,
#: dialogue_shifts, position_anchors) at once, since carrying r2 with a
#: different prev_id than what's already resolved is what dialogue.py's
#: topic_order treats as a genuine reposition, not a no-op duplicate.
DIALOGUE_BASE: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1]]},
    {"type": "Dialogue", "id": "Greeting 0"},
    {"type": "DialogueInfo", "id": "r1", "prev_id": "", "next_id": "r2"},
    {"type": "DialogueInfo", "id": "r2", "prev_id": "r1", "next_id": "r3"},
    {"type": "DialogueInfo", "id": "r3", "prev_id": "r2", "next_id": ""},
]
DIALOGUE_OVERRIDE: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1]]},
    {"type": "Dialogue", "id": "Greeting 0"},
    {"type": "DialogueInfo", "id": "r2", "prev_id": "r3", "next_id": ""},
]
DIALOGUE_SOURCES: Final[dict[str, list[dict[str, Any]]]] = {
    "Base.esp": DIALOGUE_BASE,
    "Override.esp": DIALOGUE_OVERRIDE,
}
DIALOGUE_LOAD_ORDER: Final[list[str]] = ["Morrowind.esm", "Base.esp", "Override.esp"]
DIALOGUE_SIZES: Final[dict[str, int]] = dict.fromkeys(DIALOGUE_LOAD_ORDER, 1)


class TestDialogueNotes:
    """The three notes only a dialogue selection can trigger.

    Base.esp defines r1 -> r2 -> r3. Override.esp (loading after) moves r2 to
    the end. Carrying r2 *as Base.esp defined it* undoes that repositioning
    from the patch's point of view -- which is exactly the situation all
    three notes exist to flag.
    """

    def test_all_three_notes_appear_for_the_one_scenario_that_triggers_them(
        self, tmp_path: Path
    ) -> None:
        selections = [
            Selection("Base.esp", "Dialogue", "Greeting 0"),
            Selection("Base.esp", "DialogueInfo", "r2"),
        ]

        result = build_record_patch(
            selections,
            DIALOGUE_SOURCES,
            DIALOGUE_LOAD_ORDER,
            DIALOGUE_SIZES,
            "tes3conv",
            tmp_path / "out.esp",
            dry_run=True,
        )

        risk = [line for line in result.lines if line.strip().startswith("note:")]
        moves = [line for line in result.lines if line.strip().startswith("moves:")]
        anchors = [line for line in result.lines if line.strip().startswith("anchor:")]
        assert risk, "dialogue_position_risk note never appeared"
        assert len(moves) == 2, "expected both r2 and r3 to be reported as moved"
        assert anchors, "position_anchors note never appeared"
        assert "r2" in risk[0]
        assert all("r2" in line or "r3" in line for line in moves)


class TestAppendingToAnExistingPatch:
    """`carried` is what lets a patch be built over several sessions.

    Each case here passes an earlier build's own decoded records back in,
    which is a fundamentally different kind of source than SOURCES: those are
    other plugins being read *from*, this is a previous version of the file
    about to be *overwritten*.
    """

    def test_a_carried_record_alone_is_a_valid_build(self, tmp_path: Path) -> None:
        """Nothing selected or merged this session is fine when something is
        carried -- that is what "nothing changed today, just checking in on
        the patch" looks like.
        """
        result = build_record_patch(
            [],
            SOURCES,
            PATCH,
            SIZES,
            "tes3conv",
            tmp_path / "out.esp",
            carried=OLD_BUILD,
            dry_run=True,
        )
        assert result.records == 1
        assert result.carried == 1

    def test_a_carried_masters_own_dependency_is_declared(self, tmp_path: Path) -> None:
        """Other.esp is required by OLD_BUILD's header, not by anything this
        session selected -- so it only ends up declared if `carried` feeds
        into the master calculation, not just the record list.
        """
        result = build_record_patch(
            [],
            SOURCES,
            PATCH,
            SIZES,
            "tes3conv",
            tmp_path / "out.esp",
            carried=OLD_BUILD,
            dry_run=True,
        )
        assert "Other.esp" in result.masters

    def test_a_fresh_decision_supersedes_the_carried_one(self, tmp_path: Path) -> None:
        """Re-deciding a record this session must replace the old answer, not
        sit beside it and leave the patch's own last-wins to pick.
        """
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]

        result = build_record_patch(
            selections,
            SOURCES,
            PATCH,
            SIZES,
            "tes3conv",
            tmp_path / "out.esp",
            carried=OLD_BUILD_WITH_OVERRIDE,
            dry_run=True,
        )

        assert result.records == 1  # the new answer only, not both
        assert result.carried == 0

    def test_carrying_forward_is_reported_in_the_progress_lines(self, tmp_path: Path) -> None:
        result = build_record_patch(
            [],
            SOURCES,
            PATCH,
            SIZES,
            "tes3conv",
            tmp_path / "out.esp",
            carried=OLD_BUILD,
            dry_run=True,
        )
        assert any("carried forward" in line for line in result.lines)

    def test_no_carried_records_means_a_zero_count_not_an_error(self, tmp_path: Path) -> None:
        """The ordinary fresh-patch or replace path -- `carried` defaults to
        empty, and nothing about that case should change.
        """
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]

        result = build_record_patch(
            selections, SOURCES, PATCH, SIZES, "tes3conv", tmp_path / "out.esp", dry_run=True
        )

        assert result.carried == 0
        assert not any("carried forward" in line for line in result.lines)


class TestGuardRails:
    def test_nothing_selected_or_merged_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PatchServiceError, match="nothing was selected"):
            build_record_patch([], SOURCES, PATCH, SIZES, "tes3conv", tmp_path / "out.esp")

    def test_a_patcherror_from_collect_surfaces_as_a_service_error(self, tmp_path: Path) -> None:
        # A selection naming a record that doesn't exist in that plugin.
        bad = [Selection("Castle.esp", "GameSetting", "sNoSuchSetting")]
        with pytest.raises(PatchServiceError):
            build_record_patch(bad, SOURCES, PATCH, SIZES, "tes3conv", tmp_path / "out.esp")

    def test_a_master_with_no_known_size_is_refused(self, tmp_path: Path) -> None:
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]
        sizes_missing_castle = {"Morrowind.esm": 1}  # Castle.esp itself is unmeasured
        with pytest.raises(PatchServiceError, match="cannot measure"):
            build_record_patch(
                selections,
                SOURCES,
                PATCH,
                sizes_missing_castle,
                "tes3conv",
                tmp_path / "out.esp",
                dry_run=True,
            )

    def test_a_master_present_but_with_an_unusable_size_raises_via_emiterror(
        self, tmp_path: Path
    ) -> None:
        # Present in `sizes` (so the earlier "cannot measure" guard passes)
        # but zero -- build_plugin's own header validation is what actually
        # catches this, and its EmitError must surface as PatchServiceError
        # rather than leaking a foreign exception type.
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]
        sizes_with_zero = {**SIZES, "Castle.esp": 0}
        with pytest.raises(PatchServiceError, match="no usable size"):
            build_record_patch(
                selections,
                SOURCES,
                PATCH,
                sizes_with_zero,
                "tes3conv",
                tmp_path / "out.esp",
                dry_run=True,
            )


class TestProgressReporting:
    def test_report_callback_receives_every_line_dry_run_produces(self, tmp_path: Path) -> None:
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]
        seen: list[str] = []

        result = build_record_patch(
            selections,
            SOURCES,
            PATCH,
            SIZES,
            "tes3conv",
            tmp_path / "out.esp",
            dry_run=True,
            report=seen.append,
        )

        assert seen == result.lines
        assert any("dry run" in line for line in seen)

    def test_a_remapped_reference_record_is_counted_and_reported(self, tmp_path: Path) -> None:
        selections = [Selection("Castle.esp", "Cell", "(7, 22)")]

        result = build_record_patch(
            selections, SOURCES, PATCH, SIZES, "tes3conv", tmp_path / "out.esp", dry_run=True
        )

        assert result.remapped == 1
        assert any("reference" in line and "renumbered" in line for line in result.lines)

    def test_a_record_with_no_references_is_not_counted_as_remapped(self, tmp_path: Path) -> None:
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]

        result = build_record_patch(
            selections, SOURCES, PATCH, SIZES, "tes3conv", tmp_path / "out.esp", dry_run=True
        )

        assert result.remapped == 0
        assert not any("renumbered" in line for line in result.lines)


class TestDryRunVsRealWrite:
    def test_dry_run_writes_nothing_to_disk(self, tmp_path: Path) -> None:
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]
        out = tmp_path / "out.esp"

        result = build_record_patch(
            selections, SOURCES, PATCH, SIZES, "tes3conv", out, dry_run=True
        )

        assert result.output is None
        assert not out.exists()

    def test_a_real_write_produces_a_file_tes3conv_actually_wrote(self, tmp_path: Path) -> None:
        import shutil

        converter = shutil.which("tes3conv")
        if not converter:
            pytest.skip("no tes3conv binary on PATH in this environment")

        selections = [Selection("Castle.esp", "Cell", "(7, 22)")]
        out = tmp_path / "out.esp"

        result = build_record_patch(selections, SOURCES, PATCH, SIZES, converter, out)

        assert result.output == out
        assert out.is_file()
        assert out.stat().st_size > 0
        assert any("wrote" in line for line in result.lines)

    def test_a_missing_converter_binary_is_reported_not_a_traceback(self, tmp_path: Path) -> None:
        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]

        with pytest.raises(PatchServiceError, match="could not be run"):
            build_record_patch(
                selections,
                SOURCES,
                PATCH,
                SIZES,
                str(tmp_path / "no-such-tes3conv-binary"),
                tmp_path / "out.esp",
            )

    def test_writing_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        import shutil

        converter = shutil.which("tes3conv")
        if not converter:
            pytest.skip("no tes3conv binary on PATH in this environment")

        selections = [Selection("Castle.esp", "Cell", "(7, 22)")]
        out = tmp_path / "nested" / "does-not-exist-yet" / "out.esp"

        build_record_patch(selections, SOURCES, PATCH, SIZES, converter, out)

        assert out.is_file()

    def test_a_real_tes3conv_rejection_is_reported_not_a_traceback(self, tmp_path: Path) -> None:
        import shutil

        converter = shutil.which("tes3conv")
        if not converter:
            pytest.skip("no tes3conv binary on PATH in this environment")

        # A reference tes3conv itself will refuse -- missing every field
        # beyond the bare minimum this decoder's own checks require, so the
        # rejection genuinely comes from the real binary, not our own code.
        broken_source = {
            "Broken.esp": [
                {"type": "Header", "masters": [["Morrowind.esm", 1]]},
                {
                    "type": "Cell",
                    "flags": "",
                    "name": "",
                    "data": {"grid": [1, 1], "flags": ""},
                    "references": [{"mast_index": 0, "refr_index": 1, "id": "x"}],
                },
            ]
        }
        selections = [Selection("Broken.esp", "Cell", "(1, 1)")]

        with pytest.raises(PatchServiceError, match="tes3conv refused the patch"):
            build_record_patch(
                selections,
                broken_source,
                ["Morrowind.esm", "Broken.esp"],
                {"Morrowind.esm": 1, "Broken.esp": 1},
                converter,
                tmp_path / "out.esp",
            )

    def test_tes3conv_reporting_success_with_no_file_written_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A genuinely successful tes3conv run always leaves the file behind;
        # this exercises the defensive check for the case it somehow
        # doesn't, without needing a real binary that misbehaves to prove it.
        import subprocess
        import types

        def fake_run(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        selections = [Selection("Castle.esp", "GameSetting", "sCastleName")]
        with pytest.raises(PatchServiceError, match="wrote no file"):
            build_record_patch(selections, SOURCES, PATCH, SIZES, "tes3conv", tmp_path / "out.esp")
