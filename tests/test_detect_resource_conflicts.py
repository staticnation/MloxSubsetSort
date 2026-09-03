"""detect_resource_conflicts' own error handling and edge cases.

test_lint_and_resource_stages.py exercises this through the CLI stage; these
call it directly to reach the branches that stage never triggers: a data
folder that vanishes or can't be stat'd, a loose plugin file sitting in a
data folder (skipped -- plugins are ordered by content=, not the VFS), a
directory os.walk can't read, and two files in one folder that normalize to
the same relative path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import wraithguard_toolkit as core

if TYPE_CHECKING:
    import pytest


class TestDataFolderItselfIsUnusable:
    def test_a_missing_data_folder_is_skipped_not_raised(self, tmp_path: Path) -> None:
        real = tmp_path / "A"
        real.mkdir()
        (real / "rock.dds").write_bytes(b"x")

        conflicts, stats = core.detect_resource_conflicts(
            [str(tmp_path / "does-not-exist"), str(real)]
        )

        assert conflicts == []
        assert stats["dirs"] == 1  # only the real one counted

    def test_a_data_folder_that_cannot_be_stat_d_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pathlib import Path as RealPath

        real = tmp_path / "A"
        real.mkdir()
        original_is_dir = RealPath.is_dir

        def flaky_is_dir(self: RealPath) -> bool:
            if self.name == "unreadable":
                raise OSError("simulated: dead network share")
            return original_is_dir(self)

        monkeypatch.setattr(RealPath, "is_dir", flaky_is_dir)

        conflicts, stats = core.detect_resource_conflicts([str(tmp_path / "unreadable"), str(real)])

        assert conflicts == []
        assert stats["dirs"] == 1


class TestWalkFailure:
    def test_a_directory_os_walk_cannot_read_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dir_a = tmp_path / "A"
        dir_a.mkdir()
        (dir_a / "rock.dds").write_bytes(b"x")
        dir_b = tmp_path / "B"
        dir_b.mkdir()
        (dir_b / "rock.dds").write_bytes(b"y")
        real_walk = os.walk

        def flaky_walk(top: str, *args: object, **kwargs: object) -> object:
            if str(top) == str(dir_a):
                raise PermissionError("simulated: no read access")
            return real_walk(top, *args, **kwargs)

        monkeypatch.setattr(os, "walk", flaky_walk)

        conflicts, stats = core.detect_resource_conflicts([str(dir_a), str(dir_b)])

        assert conflicts == []  # dir_a never contributed a provider to conflict with
        assert stats["dirs"] == 2  # the folder itself still counted -- only the walk failed


class TestLooseFilesAreFilteredNotConflicted:
    def test_a_stray_plugin_file_in_a_data_folder_is_never_treated_as_a_resource(
        self, tmp_path: Path
    ) -> None:
        """Plugins order by content=, not the VFS -- a shared .esp name is not a VFS conflict."""
        dir_a = tmp_path / "A"
        dir_a.mkdir()
        (dir_a / "Shared.esp").write_bytes(b"x")
        dir_b = tmp_path / "B"
        dir_b.mkdir()
        (dir_b / "Shared.esp").write_bytes(b"y")

        conflicts, _stats = core.detect_resource_conflicts([str(dir_a), str(dir_b)])

        assert conflicts == []

    def test_an_excluded_extension_is_never_treated_as_a_resource(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "A"
        dir_a.mkdir()
        (dir_a / "notes.txt").write_bytes(b"x")
        dir_b = tmp_path / "B"
        dir_b.mkdir()
        (dir_b / "notes.txt").write_bytes(b"y")

        conflicts, _stats = core.detect_resource_conflicts(
            [str(dir_a), str(dir_b)], exclude_exts={".txt"}
        )

        assert conflicts == []


class TestDuplicateWithinOneFolder:
    def test_two_files_that_normalize_to_the_same_relative_path_count_once(
        self, tmp_path: Path
    ) -> None:
        """Case-insensitive VFS folding: 'Rock.dds' and 'rock.DDS' in one folder are one entry."""
        dir_a = tmp_path / "A"
        dir_a.mkdir()
        (dir_a / "Rock.dds").write_bytes(b"x")
        (dir_a / "rock.DDS").write_bytes(b"y")
        dir_b = tmp_path / "B"
        dir_b.mkdir()
        (dir_b / "rock.dds").write_bytes(b"z")

        conflicts, _stats = core.detect_resource_conflicts([str(dir_a), str(dir_b)])

        # A single provider entry for dir_a either way -- still just a two-way conflict.
        assert len(conflicts) == 1
        assert conflicts[0]["providers"] == [str(dir_a), str(dir_b)]

    def test_a_second_dirent_folding_to_an_already_seen_path_is_not_appended_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same scenario as above, without relying on the filesystem to
        actually hold two same-folded names in one directory at once.

        NTFS is case-insensitive but case-*preserving*: writing 'rock.DDS'
        where 'Rock.dds' already exists overwrites that one file rather than
        creating a second dirent, so on Windows the test above never
        actually produces two files for os.walk to find, and the branch it
        means to reach (a folded path seen twice *within the same
        directory*) stays uncovered there even though the test passes.
        Faking os.walk's own yield reproduces the shape directly instead,
        the same way a network share or case-folding bug could -- and
        reaches the branch on every platform, not just case-sensitive ones.
        """
        dir_a = tmp_path / "A"
        dir_a.mkdir()
        (dir_a / "rock.dds").write_bytes(b"x")
        dir_b = tmp_path / "B"
        dir_b.mkdir()
        (dir_b / "rock.dds").write_bytes(b"z")

        real_walk = os.walk

        def fake_walk(top: object, *args: object, **kwargs: object) -> object:
            for root, dirs, files in real_walk(top, *args, **kwargs):
                if Path(root) == dir_a:
                    # Report the one real file twice, as two dirents folding
                    # to the same relative path -- what 'Rock.dds' +
                    # 'rock.DDS' would yield if both could coexist.
                    yield root, dirs, [*files, *files]
                else:
                    yield root, dirs, files

        monkeypatch.setattr(os, "walk", fake_walk)

        conflicts, _stats = core.detect_resource_conflicts([str(dir_a), str(dir_b)])

        assert len(conflicts) == 1
        assert conflicts[0]["providers"] == [str(dir_a), str(dir_b)]
