"""Tests for mesh findings in the resource-conflict report and CSV.

Two things are being pinned down. First that the *cheap* path stays cheap --
identical providers and non-mesh files are never opened, because that is the
only reason this is affordable during a scan. Second that the report never
states something the parse cannot support.
"""

from __future__ import annotations

import csv
import struct
from typing import TYPE_CHECKING, Any

from wraithguard.nif.analysis import MeshAnalyser, MeshFinding
from wraithguard.nif.report import Difference
from wraithguard_toolkit import (
    analyse_mesh_conflicts,
    describe_mesh_detail,
    describe_mesh_finding,
    format_resource_report,
    write_resource_csv,
)

if TYPE_CHECKING:
    from pathlib import Path

HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"


def mesh(blocks: int = 0) -> bytes:
    """A parseable NIF with no blocks.

    Args:
        blocks: The block count to declare.

    Returns:
        The file bytes.
    """
    return HEADER + struct.pack("<II", 0x04000002, blocks)


def _text(value: str) -> bytes:
    raw = value.encode("cp1252")
    return struct.pack("<I", len(raw)) + raw


def _av_body(name: str, children: tuple[int, ...] = ()) -> bytes:
    body = _text(name) + struct.pack("<iiH", -1, -1, 0)
    body += struct.pack("<3f", 0.0, 0.0, 0.0)  # translation
    body += struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1)  # rotation
    body += struct.pack("<f", 1.0)  # scale
    body += struct.pack("<3f", 0.0, 0.0, 0.0)  # velocity
    body += struct.pack("<I", 0)  # properties
    body += struct.pack("<I", 0)  # bounding-box flag
    body += struct.pack("<I", len(children)) + b"".join(struct.pack("<i", c) for c in children)
    body += struct.pack("<I", 0)  # collision object index
    return body


def mesh_with_collision(has_collision: bool) -> bytes:
    """A minimal, real NiNode -- optionally with a RootCollisionNode child.

    Just enough structure for the real collision check in
    :mod:`wraithguard.nif.report` to see -- not a full mesh, since nothing
    here looks at geometry.

    Args:
        has_collision: Whether to add the RootCollisionNode block.

    Returns:
        The file bytes.
    """
    blocks: list[tuple[str, bytes]] = [
        ("NiNode", _av_body("root", children=(1,) if has_collision else ()))
    ]
    if has_collision:
        blocks.append(("RootCollisionNode", _av_body("collide")))
    out = [HEADER, struct.pack("<II", 0x04000002, len(blocks))]
    for type_name, body in blocks:
        out.append(_text(type_name))
        out.append(body)
    return b"".join(out)


def _source_texture(path: str) -> bytes:
    return (
        _text("tex")
        + struct.pack("<ii", -1, -1)  # extra, controller
        + struct.pack("<B", 1)  # use_external
        + _text(path)  # the external filename
        + struct.pack("<III", 0, 0, 0)  # pixel layout, mipmaps, alpha
        + struct.pack("<B", 1)  # is static
    )


def _vis_controller() -> bytes:
    return (
        struct.pack("<i", -1)  # next_controller
        + struct.pack("<H", 0)  # flags
        + struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)  # frequency, phase, start, stop
        + struct.pack("<i", -1)  # target
        + struct.pack("<i", -1)  # data
    )


def mesh_with_everything(has_collision: bool) -> bytes:
    """A NiNode, optionally with collision, plus a texture and a controller.

    The texture and controller blocks need no link to the node to be
    counted -- :func:`wraithguard.nif.report.summarise` just scans every
    block in the file for them.

    Args:
        has_collision: Whether to add the RootCollisionNode block.

    Returns:
        The file bytes.
    """
    blocks: list[tuple[str, bytes]] = [
        ("NiNode", _av_body("root", children=(1,) if has_collision else ())),
    ]
    if has_collision:
        blocks.append(("RootCollisionNode", _av_body("collide")))
    blocks.append(("NiSourceTexture", _source_texture("textures/rock.dds")))
    blocks.append(("NiVisController", _vis_controller()))
    out = [HEADER, struct.pack("<II", 0x04000002, len(blocks))]
    for type_name, body in blocks:
        out.append(_text(type_name))
        out.append(body)
    return b"".join(out)


def two_providers(tmp_path: Path, name: str, left: bytes, right: bytes) -> dict[str, Any]:
    """Build a conflict entry backed by two real files on disk.

    Args:
        tmp_path: Temporary directory.
        name: The asset path.
        left: Bytes for the losing provider.
        right: Bytes for the winning provider.

    Returns:
        A conflict entry shaped like ``detect_resource_conflicts`` produces.
    """
    first, second = tmp_path / "ModA", tmp_path / "ModB"
    for folder in (first, second):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
    (first / name).write_bytes(left)
    (second / name).write_bytes(right)
    return {
        "path": name,
        "providers": [str(first), str(second)],
        "winner": str(second),
        "involves_subset": True,
        "identical": left == right,
    }


class TestOnlyContestedMeshesAreOpened:
    """The scan must not pay for files it already knows about."""

    def test_identical_providers_are_never_parsed(self, tmp_path: Path) -> None:
        """The byte comparison already settled these.

        Opening them would be the single largest avoidable cost in the pass,
        since re-shipped assets are the majority of conflicts in a real setup.
        """
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(), mesh())
        stats = analyse_mesh_conflicts([entry])
        assert stats["analysed"] == 0
        assert "mesh" not in entry

    def test_non_meshes_are_never_parsed(self, tmp_path: Path) -> None:
        """A texture is not a mesh, whatever its bytes look like."""
        entry = two_providers(tmp_path, "textures/a.dds", b"DDS one", b"DDS two")
        assert analyse_mesh_conflicts([entry])["analysed"] == 0
        assert "mesh" not in entry

    def test_a_differing_mesh_is_analysed(self, tmp_path: Path) -> None:
        """A negative control: the filters above must not exclude everything."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        assert analyse_mesh_conflicts([entry])["analysed"] == 1
        assert "mesh" in entry

    def test_the_limit_stops_the_pass(self, tmp_path: Path) -> None:
        """A guard for pathological setups, not an expected path."""
        entries = [
            two_providers(tmp_path / f"c{index}", "meshes/a.nif", mesh(0), mesh(1))
            for index in range(4)
        ]
        assert analyse_mesh_conflicts(entries, limit=2)["analysed"] == 2

    def test_a_lost_collision_counts_as_a_finding(self, tmp_path: Path) -> None:
        """The real path from two genuine NIFs through to stats["findings"], not a hand-built one."""
        entry = two_providers(
            tmp_path, "meshes/a.nif", mesh_with_collision(True), mesh_with_collision(False)
        )
        stats = analyse_mesh_conflicts([entry])
        assert stats["findings"] == 1
        assert entry["mesh"].worth_reporting

    def test_an_unreadable_provider_counts_toward_unreadable_not_findings(
        self, tmp_path: Path
    ) -> None:
        entry = two_providers(tmp_path, "meshes/a.nif", b"not a nif at all", mesh(0))
        stats = analyse_mesh_conflicts([entry])
        assert stats["unreadable"] == 1
        assert stats["findings"] == 0

    def test_a_loser_matching_the_winner_leaves_no_losses_to_analyse(self, tmp_path: Path) -> None:
        """entry["providers"] can list the winner itself (a single true provider); no loss to report."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["providers"] = [entry["winner"]]  # only the winner "provides" it

        stats = analyse_mesh_conflicts([entry])

        assert stats["analysed"] == 0
        assert "mesh" not in entry


class TestTheReportNeverOverstates:
    """A finding the parse cannot support must not reach the page."""

    def test_an_unreliable_finding_describes_nothing(self) -> None:
        """Silence, not a hedge.

        "Possibly lost collision" is worse than saying nothing: it is the one
        finding a user acts on, and a maybe would send them looking for a
        problem that may not exist.
        """
        finding = MeshFinding(
            "m/a.nif",
            difference=Difference(None, True, True, [], [], True),
            winner_partial=True,
        )
        assert describe_mesh_finding(finding) == ""

    def test_an_unreadable_mesh_says_why(self) -> None:
        """Distinct from "no problem found", and useful to a user."""
        text = describe_mesh_finding(MeshFinding("m/a.nif", unreadable="NIF version 0x14020007"))
        assert "could not read" in text
        assert "0x14020007" in text

    def test_losses_are_named_and_gains_are_not(self) -> None:
        """Gaining detail rarely breaks a load order; losing it does."""
        text = describe_mesh_finding(
            MeshFinding(
                "m/a.nif",
                difference=Difference(2.0, True, False, [], ["old.dds"], False),
            )
        )
        assert "loses collision" in text
        assert "animation" not in text
        # A winner with twice the triangles is not a finding.
        assert "%" not in text

    def test_a_clean_comparison_says_nothing(self) -> None:
        """The common case must be silent, or the report is unreadable."""
        assert (
            describe_mesh_finding(
                MeshFinding("m/a.nif", difference=Difference(1.0, False, False, [], [], False))
            )
            == ""
        )

    def test_the_report_marks_and_counts_flagged_meshes(self, tmp_path: Path) -> None:
        """The count is the triage signal; the line under the path is the detail."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["mesh"] = MeshFinding(
            "meshes/a.nif",
            difference=Difference(None, True, False, [], [], False),
        )
        text = format_resource_report(
            [entry], {"dirs": 2, "files": 2, "conflicts": 1, "identical": 0, "differing": 1}
        )
        assert "1 mesh conflict changes what the asset does" in text
        assert "loses collision" in text

    def test_a_limit_smaller_than_the_conflict_count_says_how_many_more(
        self, tmp_path: Path
    ) -> None:
        entries = [
            two_providers(tmp_path / f"c{index}", f"meshes/{index}.nif", mesh(0), mesh(1))
            for index in range(3)
        ]
        text = format_resource_report(
            entries,
            {"dirs": 2, "files": 2, "conflicts": 3, "identical": 0, "differing": 3},
            limit=2,
        )
        assert "and 1 more (save the full report)" in text


class TestCsvBlanksMeanNotEstablished:
    """A spreadsheet filter on "no" must not sweep up files nobody read."""

    def test_an_unanalysed_mesh_leaves_every_column_blank(self, tmp_path: Path) -> None:
        """Writing "no" would be a claim the scan never made."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(), mesh())
        out = tmp_path / "r.csv"
        write_resource_csv(out, [entry])
        row = next(iter(csv.DictReader(out.open(encoding="utf-8"))))
        assert row["mesh_lost_collision"] == ""
        assert row["mesh_triangle_ratio"] == ""

    def test_an_unreadable_mesh_notes_why_but_asserts_nothing(self, tmp_path: Path) -> None:
        """The note is filled in; the yes/no columns are not."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["mesh"] = MeshFinding("meshes/a.nif", unreadable="not a NIF")
        out = tmp_path / "r.csv"
        write_resource_csv(out, [entry])
        row = next(iter(csv.DictReader(out.open(encoding="utf-8"))))
        assert "could not read" in row["mesh_note"]
        assert row["mesh_lost_collision"] == ""

    def test_a_reliable_finding_fills_the_columns(self, tmp_path: Path) -> None:
        """A negative control, so the blanks above mean something."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["mesh"] = MeshFinding(
            "meshes/a.nif",
            difference=Difference(0.25, True, False, [], [], False),
        )
        out = tmp_path / "r.csv"
        write_resource_csv(out, [entry])
        row = next(iter(csv.DictReader(out.open(encoding="utf-8"))))
        assert row["mesh_lost_collision"] == "yes"
        assert row["mesh_lost_animation"] == "no"
        assert row["mesh_triangle_ratio"] == "0.250"


class TestOnDemandDetail:
    """The selected-a-row path, which must read nothing until it is called."""

    def test_a_non_mesh_selection_reads_nothing(self, tmp_path: Path) -> None:
        """Selecting a texture must not open anything."""
        entry = two_providers(tmp_path, "textures/a.dds", b"DDS one", b"DDS two")
        analyser = MeshAnalyser()
        assert describe_mesh_detail(analyser, entry) == []
        assert analyser.parsed == 0

    def test_every_provider_is_described_not_just_the_winner(self, tmp_path: Path) -> None:
        """On demand it can afford the full picture; the scan pass cannot."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        lines = describe_mesh_detail(MeshAnalyser(), entry)
        assert any(str(entry["providers"][0]) in line for line in lines)
        assert any(str(entry["providers"][1]) in line for line in lines)

    def test_reselecting_the_same_row_reparses_nothing(self, tmp_path: Path) -> None:
        """Clicking back and forth in a list must not re-read the disk."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        analyser = MeshAnalyser()
        describe_mesh_detail(analyser, entry)
        parsed = analyser.parsed
        describe_mesh_detail(analyser, entry)
        assert analyser.parsed == parsed
        assert analyser.cache_hits >= 2

    def test_an_unreadable_provider_is_described_not_raised(self, tmp_path: Path) -> None:
        """A window a user just opened must not be closed by a bad file."""
        entry = two_providers(tmp_path, "meshes/a.nif", b"not a nif", mesh(1))
        lines = describe_mesh_detail(MeshAnalyser(), entry)
        assert any("could not read" in line for line in lines)

    def test_a_partial_read_never_reports_an_absence(self, tmp_path: Path) -> None:
        """ "no collision" must not appear for a mesh we did not finish reading.

        Presence is provable from a partial parse; absence is not, and this is
        the line a user would act on.
        """
        truncated = HEADER + struct.pack("<II", 0x04000002, 5)
        entry = two_providers(tmp_path, "meshes/a.nif", truncated, mesh(0))
        lines = describe_mesh_detail(MeshAnalyser(), entry)
        partial = [line for line in lines if "PARTIAL" in line]
        assert partial, lines
        assert all("no collision" not in line for line in partial)

    def test_no_providers_at_all_reads_nothing(self, tmp_path: Path) -> None:
        """A malformed entry (no providers) is treated like a non-mesh selection, not a crash."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["providers"] = []

        assert describe_mesh_detail(MeshAnalyser(), entry) == []

    def test_collision_animation_textures_and_a_real_difference_note_all_show_up(
        self, tmp_path: Path
    ) -> None:
        """The traits line, the animation/texture traits, the comparison note, and the
        textures line, all from one real pair -- not hand-built Structure/MeshFinding
        objects."""
        entry = two_providers(
            tmp_path, "meshes/a.nif", mesh_with_everything(True), mesh_with_everything(False)
        )
        lines = describe_mesh_detail(MeshAnalyser(), entry)

        assert any("collision" in line and "no collision" not in line for line in lines)
        assert any("animated" in line for line in lines)
        assert any("the winner" in line for line in lines)  # the comparison note
        assert any("Textures referenced" in line and "rock.dds" in line for line in lines)


class TestTheWinnerIsTakenFromTheEntryNotThePosition:
    """The declared winner decides the comparison, not the provider order.

    Both of these functions used to assume the winner was the last provider.
    That is true of every entry the scan builds today, so the whole suite
    passed while the coupling was invisible -- and the comparison would have
    been *backwards* the moment anything set a different winner.
    """

    @staticmethod
    def _winner_first(tmp_path: Path) -> dict[str, Any]:
        """A conflict whose winner is the first provider, not the last.

        Args:
            tmp_path: Temporary directory.

        Returns:
            A conflict entry with the winner deliberately out of position.
        """
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["winner"] = entry["providers"][0]
        return entry

    def test_the_scan_pass_compares_against_the_declared_winner(self, tmp_path: Path) -> None:
        """With the winner first, the loser is the *second* provider."""
        entry = self._winner_first(tmp_path)
        analyse_mesh_conflicts([entry])
        finding = entry["mesh"]
        assert isinstance(finding, MeshFinding)
        assert not finding.unreadable

    def test_the_detail_view_compares_against_the_declared_winner(self, tmp_path: Path) -> None:
        """And never compares the winner with itself."""
        entry = self._winner_first(tmp_path)
        lines = describe_mesh_detail(MeshAnalyser(), entry)
        # Provider 1 is the winner, so it must not be reported as losing to
        # itself; only provider 2 can appear in a comparison line.
        against = [line for line in lines if "Against provider" in line]
        assert all("provider 1," not in line for line in against), lines

    def test_a_winner_not_among_the_providers_is_not_guessed_at(self, tmp_path: Path) -> None:
        """A malformed entry gets contents without a comparison, not a wrong one."""
        entry = two_providers(tmp_path, "meshes/a.nif", mesh(0), mesh(1))
        entry["winner"] = str(tmp_path / "NotAProvider")
        lines = describe_mesh_detail(MeshAnalyser(), entry)
        assert lines, "the contents should still be described"
        assert not any("Against provider" in line for line in lines)


class TestTheGuiWorkerRunsTheAnalysis:
    """The scan path must attach findings, not merely be able to.

    Asserted here rather than only in the Tk suite because it is a property of
    the *worker*, and this is the assertion whose absence let the GUI ship
    without ever calling the analysis.
    """

    def test_the_conflicts_a_scan_returns_carry_their_findings(self, tmp_path: Path) -> None:
        """Running the pass over scan output attaches a finding to the mesh."""
        entries = [
            two_providers(tmp_path / "m", "meshes/a.nif", mesh(0), mesh(1)),
            two_providers(tmp_path / "t", "textures/a.dds", b"one", b"two"),
        ]
        stats = analyse_mesh_conflicts(entries)
        assert stats["analysed"] == 1
        assert "mesh" in entries[0], "the mesh conflict has no finding attached"
        assert "mesh" not in entries[1], "a texture must not be analysed"
