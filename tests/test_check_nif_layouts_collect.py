"""Tests for ``tools/check_nif_layouts_collect.py``, the collecting survey variant.

This is the ``--collect`` sibling of ``check_nif_layouts.py``: it surveys a
folder of real ``.nif`` files, classifies how far the reader got in each, and can
copy a stratified sample of the failures into per-category buckets for a human to
look at. The two share most of their code; what differs is that ``survey`` here
files a malformed-count file as a layout bug rather than a separate category, and
that survey/census/verify all feed a collector. As in the sibling's tests, small
hand-built NIFs land each reader outcome deterministically rather than leaning on
a real corpus.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_nif_layouts_collect import (
    _collect_samples,
    check_against_census,
    explain,
    load_census,
    main,
    plausible_type,
    survey,
    verify,
)

_HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"
_VERSION = 0x04000002


def _text(value: str) -> bytes:
    raw = value.encode("cp1252")
    return struct.pack("<I", len(raw)) + raw


def _ninode_body(name: str = "root", children_count: int | None = None) -> bytes:
    """A complete, well-formed NiNode body (no children, by default)."""
    body = _text(name) + struct.pack("<iiH", -1, -1, 0)
    body += struct.pack("<3f", 0.0, 0.0, 0.0)  # translation
    body += struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1)  # rotation
    body += struct.pack("<f", 1.0)  # scale
    body += struct.pack("<3f", 0.0, 0.0, 0.0)  # velocity
    body += struct.pack("<I", 0)  # properties
    body += struct.pack("<I", 0)  # bounding-box flag
    body += struct.pack("<I", 0 if children_count is None else children_count)
    body += struct.pack("<I", 0)  # collision object index
    return body


def _nif(*blocks: tuple[str, bytes]) -> bytes:
    out = [_HEADER, struct.pack("<II", _VERSION, len(blocks))]
    for type_name, body in blocks:
        out.append(_text(type_name))
        out.append(body)
    return b"".join(out)


class TestLoadCensus:
    def test_an_unparseable_literal_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "census.txt"
        path.write_text("a.nif = {broken: x}\nb.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert load_census(path) == {"b.nif": {"NiNode": 1}}

    def test_a_literal_that_is_not_a_dict_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "census.txt"
        path.write_text("a.nif = {1, 2, 3}\nb.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert load_census(path) == {"b.nif": {"NiNode": 1}}

    def test_a_record_count_mismatch_warns_but_returns_what_parsed(
        self, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "census.txt"
        path.write_text("a.nif = {not valid\nb.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert load_census(path) == {"b.nif": {"NiNode": 1}}
        assert "holds 2 records but 1 parsed" in capsys.readouterr().err

    def test_a_file_with_no_usable_records_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "census.txt"
        path.write_text("nothing useful here\n", encoding="utf-8")
        with pytest.raises(ValueError, match="contained no"):
            load_census(path)


class TestPlausibleType:
    def test_an_ordinary_identifier_is_plausible(self) -> None:
        assert plausible_type("NiTriShape") is True

    def test_an_empty_string_is_not_plausible(self) -> None:
        assert plausible_type("") is False

    def test_something_too_long_is_not_plausible(self) -> None:
        assert plausible_type("N" * 41) is False

    def test_punctuation_is_not_plausible(self) -> None:
        assert plausible_type("Not!AnIdentifier") is False


class TestSurvey:
    def test_no_nif_files_is_reported_and_fails(self, tmp_path: Path, capsys) -> None:
        assert survey(tmp_path, 0) == 1
        assert "no .nif files under" in capsys.readouterr().out

    def test_a_complete_file_is_counted_as_fully_parsed(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        assert survey(tmp_path, 0) == 0
        out = capsys.readouterr().out
        assert "fully parsed          : 1" in out
        assert "NiNode" in out

    def test_an_unknown_but_plausible_type_is_a_missing_type(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("SomeNewBlockType", b"")))
        assert survey(tmp_path, 0) == 0
        out = capsys.readouterr().out
        assert "stopped, type missing : 1" in out
        assert "SomeNewBlockType" in out

    def test_an_implausibly_long_unknown_type_is_desynced(self, tmp_path: Path, capsys) -> None:
        long_name = "A" + "b" * 62  # matches the reader's regex, fails the length cap
        (tmp_path / "a.nif").write_bytes(_nif((long_name, b"")))
        assert survey(tmp_path, 0) == 0
        out = capsys.readouterr().out
        assert "lost alignment" in out
        assert long_name not in out

    def test_a_malformed_count_is_a_layout_bug_in_this_variant(
        self, tmp_path: Path, capsys
    ) -> None:
        """Unlike the base survey, this variant has no separate malformed bucket.

        A file whose block stops on a data-plausibility guard is filed with the
        other known-type layout failures rather than counted apart.
        """
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body(children_count=20_000_000))))
        assert survey(tmp_path, 0) == 0
        out = capsys.readouterr().out
        assert "malformed input file" not in out  # the base tool's line is gone
        assert "stopped, layout wrong : 1" in out

    def test_a_known_type_that_fails_to_parse_is_a_layout_bug(self, tmp_path: Path, capsys) -> None:
        truncated = _text("root")[:6]  # EOF mid-field
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", truncated)))
        assert survey(tmp_path, 0) == 0
        out = capsys.readouterr().out
        assert "stopped, layout wrong : 1" in out
        assert "layout bugs" in out

    def test_an_unreadable_file_is_an_error_not_a_crash(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(b"not a nif at all")
        assert survey(tmp_path, 0) == 0
        out = capsys.readouterr().out
        assert "refused or errored    : 1" in out
        assert "files that errored" in out

    def test_the_limit_caps_how_many_files_are_read(self, tmp_path: Path, capsys) -> None:
        for i in range(5):
            (tmp_path / f"{i}.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        assert survey(tmp_path, 2) == 0
        assert "2 file(s) under" in capsys.readouterr().out

    def test_nif_extension_is_matched_case_insensitively(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.NIF").write_bytes(_nif(("NiNode", _ninode_body())))
        assert survey(tmp_path, 0) == 0
        assert "1 file(s) under" in capsys.readouterr().out

    def test_an_incomplete_read_with_no_stop_point_is_an_error(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """A read that ends incomplete but names no stop point falls to the error bucket.

        This defensive branch cannot arise from the real reader on hand-built
        input, so the reader is faked to return exactly that shape.
        """
        import types as _types

        import check_nif_layouts_collect as cnl

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        result = _types.SimpleNamespace(
            blocks=[],
            complete=False,
            stopped_unknown=False,
            stopped_at=None,
            stopped_reason="unclear",
        )
        monkeypatch.setattr(cnl, "read_nif", lambda _path: result)
        assert survey(tmp_path, 0) == 0
        assert "refused or errored    : 1" in capsys.readouterr().out

    def test_a_collect_dir_gathers_survey_failure_buckets(self, tmp_path: Path, capsys) -> None:
        """The distinctive feature: failures are copied into per-category buckets."""
        src = tmp_path / "meshes"
        src.mkdir()
        (src / "missing.nif").write_bytes(_nif(("SomeNewBlockType", b"")))
        (src / "broken.nif").write_bytes(b"not a nif at all")
        collect_dir = tmp_path / "samples"
        assert survey(src, 0, collect_dir=collect_dir) == 0
        assert (collect_dir / "missing_type" / "missing.nif").is_file()
        assert (collect_dir / "unreadable" / "broken.nif").is_file()
        assert "collected" in capsys.readouterr().out


class TestCheckAgainstCensus:
    def test_an_exact_match_reports_agreement(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert check_against_census(tmp_path, census) == 0
        out = capsys.readouterr().out
        assert "agrees exactly        : 1" in out
        assert "No excess" in out

    def test_the_reader_finding_more_than_the_census_is_an_excess(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 0}\n", encoding="utf-8")
        assert check_against_census(tmp_path, census) == 0
        out = capsys.readouterr().out
        assert "exceeds the census    : 1" in out
        assert "found 1, census says 0" in out

    def test_a_census_entry_with_no_matching_file_is_missing(self, tmp_path: Path, capsys) -> None:
        census = tmp_path / "census.txt"
        census.write_text("ghost.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert check_against_census(tmp_path, census) == 0
        assert "not found on disk     : 1" in capsys.readouterr().out

    def test_an_unreadable_listed_file_counts_as_short(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "bad.nif").write_bytes(b"not a nif")
        census = tmp_path / "census.txt"
        census.write_text("bad.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert check_against_census(tmp_path, census) == 0
        assert "short (stopped early) : 1" in capsys.readouterr().out

    def test_a_report_path_writes_the_per_file_json(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")
        report = tmp_path / "report.json"
        check_against_census(tmp_path, census, report_path=report)
        assert report.is_file()
        assert "agrees" in report.read_text(encoding="utf-8")
        assert "wrote 1 per-file result" in capsys.readouterr().out

    def test_a_short_read_that_still_over_reports_is_short(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body()), ("SomeNewBlockType", b"")))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 0}\n", encoding="utf-8")
        assert check_against_census(tmp_path, census) == 0
        out = capsys.readouterr().out
        assert "short (stopped early) : 1" in out
        assert "exceeds the census    : 0" in out

    def test_finding_fewer_than_the_census_expects_is_short(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 2}\n", encoding="utf-8")
        assert check_against_census(tmp_path, census) == 0
        assert "short (stopped early) : 1" in capsys.readouterr().out

    def test_a_collect_dir_gathers_samples_by_bucket(self, tmp_path: Path) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 2}\n", encoding="utf-8")
        collect_dir = tmp_path / "samples"
        check_against_census(tmp_path, census, collect_dir=collect_dir)
        assert (collect_dir / "short" / "a.nif").is_file()


class TestExplain:
    def test_a_complete_file_prints_every_block(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body())))
        assert explain(path) == 0
        assert "NiNode" in capsys.readouterr().out

    def test_a_stopped_file_reports_why(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("SomeNewBlockType", b"")))
        assert explain(path) == 0
        assert "SomeNewBlockType" in capsys.readouterr().out

    def test_a_type_string_exactly_where_expected_needs_no_correction(
        self, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body()), ("NiFutureBlockType", b"")))
        assert explain(path) == 0
        out = capsys.readouterr().out
        assert "a type string 'NiFutureBlockType' starts at" in out
        assert "byte(s) from where the walk ended" in out
        assert "=>" not in out

    def test_a_type_string_offset_reports_the_correction(self, tmp_path: Path, capsys) -> None:
        block0 = ("NiNode", _ninode_body())
        block1_type = "NiFutureBlockType"
        header = _HEADER + struct.pack("<II", _VERSION, 2)
        body = _text(block0[0]) + block0[1] + b"XX" + _text(block1_type)
        path = tmp_path / "a.nif"
        path.write_bytes(header + body)
        assert explain(path) == 0
        out = capsys.readouterr().out
        assert f"a type string {block1_type!r} starts at" in out
        assert "which is +2 byte(s)" in out
        assert "byte(s) short." in out

    def test_no_plausible_type_string_prints_nothing_extra(self, tmp_path: Path, capsys) -> None:
        block0 = ("NiNode", _ninode_body())
        header = _HEADER + struct.pack("<II", _VERSION, 2)
        body = _text(block0[0]) + block0[1] + struct.pack("<I", 5) + b"12345"
        path = tmp_path / "a.nif"
        path.write_bytes(header + body)
        assert explain(path) == 0
        assert "a type string" not in capsys.readouterr().out

    def test_an_unreadable_file_is_an_error(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "bad.nif"
        path.write_bytes(b"not a nif")
        assert explain(path) == 1


class TestVerify:
    def test_an_identical_read_matches_the_scan(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        assert verify(tmp_path, None, None, 0) == 0
        out = capsys.readouterr().out
        assert "identical     : 1" in out
        assert "No divergence" in out

    def test_an_unreadable_file_counts_as_unverifiable(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "bad.nif").write_bytes(b"not a nif")
        assert verify(tmp_path, None, None, 0) == 0
        out = capsys.readouterr().out
        assert "unverifiable  : 1" in out
        assert "did not read every declared block" in out

    def test_a_report_path_writes_json(self, tmp_path: Path) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        report = tmp_path / "report.json"
        verify(tmp_path, report, None, 0)
        assert report.is_file()
        assert "identical" in report.read_text(encoding="utf-8")

    def test_a_type_mismatch_is_a_divergence(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body()), ("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode", "NiTriShape"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        assert verify(tmp_path, None, None, 0) == 1
        out = capsys.readouterr().out
        assert "diverged      : 1" in out
        assert "block 1: scan says NiTriShape, reader says NiNode" in out

    def test_stopping_on_a_supported_type_is_a_layout_bug(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode", "NiNode"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        assert verify(tmp_path, None, None, 0) == 1
        out = capsys.readouterr().out
        assert "stopped early : 1" in out
        assert "STOPPED INSIDE A TYPE THIS READER SUPPORTS" in out

    def test_stopping_on_an_unimplemented_type_is_a_gap(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode", "SomeUnimplementedType"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        assert verify(tmp_path, None, None, 0) == 0
        out = capsys.readouterr().out
        assert "stopped on an unimplemented type" in out
        assert "SomeUnimplementedType" in out

    def test_a_non_reconciling_scan_is_unverifiable(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode"], declared=5, header_ok=True)  # does not reconcile
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        assert verify(tmp_path, None, None, 0) == 0
        out = capsys.readouterr().out
        assert "unverifiable  : 1" in out
        assert "did not read every declared block" not in out

    def test_a_fatal_read_during_an_unverifiable_fallback_is_reported(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(b"not a nif at all")
        fake = ScanResult(type_names=[], declared=5, header_ok=False)  # does not reconcile
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        assert verify(tmp_path, None, None, 0) == 0
        out = capsys.readouterr().out
        assert "unverifiable  : 1" in out
        assert "did not read every declared block" in out

    def test_a_file_that_becomes_unreadable_mid_scan_is_reported(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body())))
        real_read_bytes = Path.read_bytes

        def flaky(self: Path, *args: object, **kwargs: object) -> bytes:
            if self.name == "a.nif":
                raise OSError("simulated: vanished mid-scan")
            return real_read_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", flaky)
        assert verify(tmp_path, None, None, 0) == 0
        assert "unreadable    : 1" in capsys.readouterr().out

    def test_the_reader_refusing_a_reconciling_file_is_unreadable(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(b"not a nif at all")
        fake = ScanResult(type_names=["NiNode"], declared=1, header_ok=True)  # reconciles
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        assert verify(tmp_path, None, None, 0) == 0
        assert "unreadable    : 1" in capsys.readouterr().out

    def test_a_collect_dir_gathers_verify_buckets(self, tmp_path: Path, monkeypatch) -> None:
        import check_nif_layouts_collect as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body()), ("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode", "NiTriShape"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        collect_dir = tmp_path / "samples"
        verify(tmp_path, None, collect_dir, 40)
        assert (collect_dir / "diverged" / "a.nif").is_file()


class TestMain:
    def test_a_target_that_is_not_a_folder_is_reported(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path / "does-not-exist")]) == 2
        assert "not a folder" in capsys.readouterr().err

    def test_explain_needs_a_file_not_a_folder(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path), "--explain"]) == 2
        assert "not a file" in capsys.readouterr().err

    def test_explain_on_a_single_file(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body())))
        assert main([str(path), "--explain"]) == 0
        assert "NiNode" in capsys.readouterr().out

    def test_survey_on_a_folder(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        assert main([str(tmp_path)]) == 0
        assert "fully parsed          : 1" in capsys.readouterr().out

    def test_survey_with_collect_copies_failures(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "meshes"
        src.mkdir()
        (src / "missing.nif").write_bytes(_nif(("SomeNewBlockType", b"")))
        out_dir = tmp_path / "samples"
        assert main([str(src), "--collect", str(out_dir)]) == 0
        assert (out_dir / "missing_type" / "missing.nif").is_file()

    def test_census_mode_needs_a_folder(self, tmp_path: Path, capsys) -> None:
        census = tmp_path / "c.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert main([str(tmp_path / "gone"), "--census", str(census)]) == 2
        assert "not a folder" in capsys.readouterr().err

    def test_census_mode_runs_the_comparison(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")
        assert main([str(tmp_path), "--census", str(census)]) == 0
        assert "agrees exactly        : 1" in capsys.readouterr().out

    def test_an_unreadable_census_file_is_a_clean_error(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path), "--census", str(tmp_path / "missing.txt")]) == 2
        assert capsys.readouterr().err

    def test_verify_mode_needs_a_folder(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path / "gone"), "--verify"]) == 2
        assert "not a folder" in capsys.readouterr().err

    def test_verify_mode_runs_the_scan_comparison(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        assert main([str(tmp_path), "--verify"]) == 0
        assert "verified 1 file(s)" in capsys.readouterr().out


class TestCollectSamples:
    def test_a_bucket_entry_with_no_matching_file_is_skipped(self, tmp_path: Path, capsys) -> None:
        destination = tmp_path / "out"
        _collect_samples(tmp_path, {"stopped": ["ghost.nif"]}, destination, 40)
        assert list((destination / "stopped").iterdir()) == []
        assert "collected 1 of 1 stopped file(s)" in capsys.readouterr().out
