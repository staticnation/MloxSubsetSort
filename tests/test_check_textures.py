"""Tests for ``tools/check_textures.py``, the texture-pipeline tracer.

A mesh that renders untextured fails identically whether the data folders were
never passed, no archive opened, the reference cannot be resolved, the bytes
cannot be read, or they decode to nothing. The tool walks the whole path and
names the step it stopped at. Here ``describe_folders`` is driven with real
folders and archives, ``trace`` with a real DXT1 texture for the OK path and a
tiny fake resolver for the unreadable/archived branches, and ``main`` across its
mesh, texture, sampling and exit-code paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_images import WHITE_565, bc1_block, dds
from tests.test_mesh_from_archive import build_bsa
from tools import check_textures
from tools.check_textures import describe_folders, main, trace
from wraithguard.nif.textures import Resolved, TextureResolver

_GOOD_DDS = dds(b"DXT1", 4, 4, bc1_block(WHITE_565, WHITE_565, 0))


def _folder_with_texture(root: Path, ref: str = "textures/tx_test.dds", body: bytes = _GOOD_DDS):
    """A data folder holding one loose texture at ``ref``."""
    target = root / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return root


class TestDescribeFolders:
    def test_a_missing_folder_is_a_problem(self, tmp_path: Path, capsys) -> None:
        problems = describe_folders([tmp_path / "gone"])
        assert problems == 1
        assert "MISSING" in capsys.readouterr().out

    def test_a_folder_with_textures_and_an_archive_is_summarised(
        self, tmp_path: Path, capsys
    ) -> None:
        _folder_with_texture(tmp_path)
        build_bsa(tmp_path / "Textures.bsa", {"textures/a.dds": _GOOD_DDS})
        assert describe_folders([tmp_path]) == 0
        out = capsys.readouterr().out
        assert "loose textures:" in out
        assert "Textures.bsa" in out
        assert "1 under textures/" in out

    def test_an_archive_that_will_not_open_is_a_problem(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "Broken.bsa").write_bytes(b"\x00\x01\x02")  # too short for a header
        assert describe_folders([tmp_path]) == 1
        assert "WILL NOT OPEN" in capsys.readouterr().out

    def test_an_archive_with_no_textures_prefix_is_flagged(self, tmp_path: Path, capsys) -> None:
        build_bsa(tmp_path / "Meshes.bsa", {"meshes/x.nif": b"NetImmerse"})
        describe_folders([tmp_path])
        assert "worth a look" in capsys.readouterr().out


class TestTrace:
    def test_a_real_texture_reaches_the_viewer(self, tmp_path: Path, capsys) -> None:
        _folder_with_texture(tmp_path)
        resolver = TextureResolver([tmp_path])
        assert trace("textures/tx_test.dds", resolver) is True
        assert "OK" in capsys.readouterr().out

    def test_an_unresolvable_reference_is_not_found(self, tmp_path: Path, capsys) -> None:
        resolver = TextureResolver([tmp_path])
        assert trace("textures/missing.dds", resolver) is False
        assert "NOT FOUND" in capsys.readouterr().out

    def test_a_texture_that_will_not_decode_is_reported(self, tmp_path: Path, capsys) -> None:
        _folder_with_texture(tmp_path, "textures/bad.dds", body=b"DDS " + b"\x00" * 200)
        resolver = TextureResolver([tmp_path])
        assert trace("textures/bad.dds", resolver) is False
        assert "NO DECODE" in capsys.readouterr().out

    def test_a_found_but_unreadable_texture_is_reported(self, capsys) -> None:
        """A resolver that finds a file but returns no bytes stops at UNREADABLE."""

        class _Fake:
            def resolve(self, ref: str) -> Resolved:
                return Resolved(reference=ref, path=Path("/nope/x.dds"))

            def read(self, _resolved: Resolved) -> bytes | None:
                return None

        assert trace("textures/x.dds", _Fake()) is False
        assert "UNREADABLE" in capsys.readouterr().out

    def test_an_unreadable_archived_texture_names_the_archive(self, tmp_path: Path, capsys) -> None:
        """A found-but-unreadable archived texture reports its archive location."""
        archive = tmp_path / "T.bsa"

        class _Fake:
            def resolve(self, ref: str) -> Resolved:
                return Resolved(reference=ref, archived_name="textures/a.dds", archive=archive)

            def read(self, _resolved: Resolved) -> bytes | None:
                return None

        assert trace("textures/a.dds", _Fake()) is False
        out = capsys.readouterr().out
        assert "UNREADABLE" in out
        assert "archive T.bsa" in out

    def test_the_quiet_paths_return_the_same_verdict_without_printing(
        self, tmp_path: Path, capsys
    ) -> None:
        """With verbose off, every outcome returns its verdict but prints nothing.

        The real run silences all but the first ``DETAIL`` references, so each
        branch has a quiet twin; calling ``trace`` directly is the cleanest way
        to reach them all.
        """
        good = _folder_with_texture(tmp_path)
        bad = _folder_with_texture(tmp_path, "textures/bad.dds", body=b"DDS " + b"\x00" * 200)
        resolver = TextureResolver([tmp_path])

        class _Unreadable:
            def resolve(self, ref: str) -> Resolved:
                return Resolved(reference=ref, path=Path("/nope/x.dds"))

            def read(self, _resolved: Resolved) -> bytes | None:
                return None

        assert trace("textures/tx_test.dds", resolver, verbose=False) is True  # OK, quiet
        assert trace("textures/missing.dds", resolver, verbose=False) is False  # NOT FOUND, quiet
        assert trace("textures/bad.dds", resolver, verbose=False) is False  # NO DECODE, quiet
        assert trace("textures/x.dds", _Unreadable(), verbose=False) is False  # UNREADABLE, quiet
        assert good and bad  # both folders were written
        assert capsys.readouterr().out == ""  # nothing was printed on any path


class TestMain:
    def test_a_named_texture_is_traced_and_passes(self, tmp_path: Path, capsys) -> None:
        _folder_with_texture(tmp_path)
        assert main([str(tmp_path), "--texture", "textures/tx_test.dds"]) == 0
        assert "1 of 1 texture(s) reached the viewer" in capsys.readouterr().out

    def test_a_missing_named_texture_fails(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path), "--texture", "textures/gone.dds"]) == 1
        assert "The first failing line" in capsys.readouterr().out

    def test_sampling_from_an_archive_when_nothing_is_named(self, tmp_path: Path, capsys) -> None:
        build_bsa(tmp_path / "Textures.bsa", {f"textures/t{i}.dds": _GOOD_DDS for i in range(3)})
        assert main([str(tmp_path)]) == 0
        assert "sampling" in capsys.readouterr().out

    def test_a_missing_mesh_exits_two(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path), "--mesh", "meshes/gone.nif"]) == 2
        assert "could not find mesh" in capsys.readouterr().out

    def test_an_unparseable_mesh_exits_two(self, tmp_path: Path, capsys) -> None:
        mesh = tmp_path / "bad.nif"
        mesh.write_bytes(b"not a nif")
        assert main([str(tmp_path), "--mesh", str(mesh)]) == 2
        assert "could not parse" in capsys.readouterr().out

    def test_a_valid_mesh_contributes_its_texture_references(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """A parseable mesh's shape textures are traced; the reader is faked here."""
        import types as _types

        _folder_with_texture(tmp_path)
        mesh = tmp_path / "m.nif"
        mesh.write_bytes(b"NetImmerse")
        monkeypatch.setattr(check_textures, "read_nif_bytes", lambda *_a, **_k: object())
        monkeypatch.setattr(
            check_textures,
            "world_meshes",
            lambda _parsed: [_types.SimpleNamespace(texture="textures/tx_test.dds")],
        )
        assert main([str(tmp_path), "--mesh", str(mesh)]) == 0
        out = capsys.readouterr().out
        assert "1 shape(s)" in out
        assert "reached the viewer" in out

    def test_a_mesh_named_the_vfs_way_is_found_inside_a_data_folder(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """A --mesh given as a relative VFS path is located by walking the folders."""
        import types as _types

        _folder_with_texture(tmp_path)
        (tmp_path / "meshes").mkdir()
        (tmp_path / "meshes" / "m.nif").write_bytes(b"NetImmerse")
        monkeypatch.setattr(check_textures, "read_nif_bytes", lambda *_a, **_k: object())
        monkeypatch.setattr(
            check_textures,
            "world_meshes",
            lambda _parsed: [_types.SimpleNamespace(texture="textures/tx_test.dds")],
        )
        assert main([str(tmp_path), "--mesh", "meshes/m.nif"]) == 0
        assert "meshes/m.nif: 1 shape(s)" in capsys.readouterr().out

    def test_a_broken_archive_is_skipped_when_sampling(self, tmp_path: Path, capsys) -> None:
        """Sampling steps over an archive that will not open and uses the next one.

        The broken archive is itself a folder-level problem, so the run exits
        non-zero even though the sample it fell back to traced cleanly.
        """
        (tmp_path / "Broken.bsa").write_bytes(b"\x00\x01\x02")  # sorts before Textures.bsa
        build_bsa(tmp_path / "Textures.bsa", {"textures/a.dds": _GOOD_DDS})
        assert main([str(tmp_path)]) == 1  # the unopenable archive is a reported problem
        assert "sampling 1" in capsys.readouterr().out

    def test_sampling_moves_to_the_next_folder_when_the_first_has_nothing(
        self, tmp_path: Path, capsys
    ) -> None:
        """A folder with no usable archive is passed over for one that has textures."""
        empty = tmp_path / "empty"
        empty.mkdir()
        stocked = tmp_path / "stocked"
        stocked.mkdir()
        build_bsa(stocked / "Textures.bsa", {"textures/a.dds": _GOOD_DDS})
        assert main([str(empty), str(stocked)]) == 0
        assert "sampling 1" in capsys.readouterr().out

    def test_nothing_named_and_no_archives_traces_nothing(self, tmp_path: Path, capsys) -> None:
        """With no references and no archives, the run traces zero and still exits clean."""
        assert main([str(tmp_path)]) == 0
        assert "0 of 0 texture(s)" in capsys.readouterr().out

    def test_more_than_the_detail_limit_are_summarised(self, tmp_path: Path, capsys) -> None:
        """Past DETAIL references, the tail is counted rather than printed in full."""
        refs = []
        for i in range(15):
            ref = f"textures/t{i:02d}.dds"
            _folder_with_texture(tmp_path, ref)
            refs += ["--texture", ref]
        assert main([str(tmp_path), *refs]) == 0
        out = capsys.readouterr().out
        assert "15 of 15 texture(s) reached the viewer" in out
        assert "more not shown" in out
