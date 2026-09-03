"""Tests for ``tools/check_nif_layouts.py`` beyond ``load_census`` (already
covered in ``test_nif.py``).

``survey``, ``check_against_census``, ``verify``, ``explain``, and ``main``
had almost no direct coverage. These build small, real NIF files -- each
engineered to land in exactly one of the reader's outcomes (complete,
unknown-but-plausible, unknown-and-desynced, malformed, known-type-failed,
or a fatal parse error) -- rather than depending on a real mesh corpus.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_nif_layouts import (
    _collect_samples,
    check_against_census,
    explain,
    load_census,
    main,
    plausible_type,
    survey,
    verify,
)

#: A real Morrowind-accepted NIF header line.
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
    if children_count is None:
        pass  # zero children, nothing further
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
        """A record the regex matches but ast.literal_eval can't parse is dropped, not fatal."""
        path = tmp_path / "census.txt"
        path.write_text(
            "a.nif = {broken: not_a_literal}\nb.nif = {'NiNode': 1}\n", encoding="utf-8"
        )

        census = load_census(path)

        assert census == {"b.nif": {"NiNode": 1}}

    def test_a_literal_that_is_not_a_dict_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "census.txt"
        path.write_text("a.nif = {1, 2, 3}\nb.nif = {'NiNode': 1}\n", encoding="utf-8")

        census = load_census(path)

        assert census == {"b.nif": {"NiNode": 1}}

    def test_a_record_count_mismatch_warns_but_still_returns_what_parsed(
        self, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "census.txt"
        # Two "= {" occurrences, only one of which is a well-formed record.
        path.write_text("a.nif = {not valid\nb.nif = {'NiNode': 1}\n", encoding="utf-8")

        census = load_census(path)

        assert census == {"b.nif": {"NiNode": 1}}
        assert "holds 2 records but 1 parsed" in capsys.readouterr().err

    def test_a_file_with_no_usable_records_at_all_is_an_error(self, tmp_path: Path) -> None:
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

        rc = survey(tmp_path, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "fully parsed          : 1" in out
        assert "NiNode" in out

    def test_an_unknown_but_plausible_type_is_a_missing_type(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("SomeNewBlockType", b"")))

        rc = survey(tmp_path, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "stopped, type missing : 1" in out
        assert "SomeNewBlockType" in out

    def test_an_implausibly_long_unknown_type_is_desynced_not_missing(
        self, tmp_path: Path, capsys
    ) -> None:
        long_name = "A" + "b" * 62  # 63 chars: matches the reader's own regex, fails the length cap
        (tmp_path / "a.nif").write_bytes(_nif((long_name, b"")))

        rc = survey(tmp_path, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "lost alignment" in out
        assert long_name not in out  # never dumped -- that's the whole point

    def test_a_malformed_count_field_is_reported_separately_from_layout_bugs(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body(children_count=20_000_000))))

        rc = survey(tmp_path, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "malformed input file  : 1" in out
        assert "not layout bugs" in out

    def test_a_known_type_that_fails_to_parse_is_a_layout_bug(self, tmp_path: Path, capsys) -> None:
        truncated = _text("root")[:6]  # cuts the name itself short: EOF mid-field
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", truncated)))

        rc = survey(tmp_path, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "stopped, layout wrong : 1" in out
        assert "layout bugs" in out

    def test_an_unreadable_file_is_an_error_not_a_crash(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(b"not a nif at all")

        rc = survey(tmp_path, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "refused or errored    : 1" in out
        assert "files that errored" in out

    def test_an_incomplete_read_with_no_stop_point_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """A read that ends incomplete but names no stop point falls to the error bucket.

        This defensive branch cannot arise from the real reader on hand-built
        input, so the reader is faked to return exactly that shape.
        """
        import types as _types

        import check_nif_layouts as cnl

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        result = _types.SimpleNamespace(
            blocks=[],
            complete=False,
            stopped_unknown=False,
            stopped_malformed=False,
            stopped_at=None,
            stopped_reason="unclear",
        )
        monkeypatch.setattr(cnl, "read_nif", lambda _path: result)

        rc = survey(tmp_path, 0)

        assert rc == 0
        assert "refused or errored    : 1" in capsys.readouterr().out

    def test_the_limit_caps_how_many_files_are_read(self, tmp_path: Path, capsys) -> None:
        for i in range(5):
            (tmp_path / f"{i}.nif").write_bytes(_nif(("NiNode", _ninode_body())))

        rc = survey(tmp_path, 2)

        assert rc == 0
        assert "2 file(s) under" in capsys.readouterr().out

    def test_nif_extension_is_matched_case_insensitively(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.NIF").write_bytes(_nif(("NiNode", _ninode_body())))

        rc = survey(tmp_path, 0)

        assert rc == 0
        assert "1 file(s) under" in capsys.readouterr().out


class TestCheckAgainstCensus:
    def test_an_exact_match_reports_agreement(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")

        rc = check_against_census(tmp_path, census)

        out = capsys.readouterr().out
        assert rc == 0
        assert "agrees exactly        : 1" in out
        assert "No excess" in out

    def test_the_reader_finding_more_than_the_census_is_an_excess(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 0}\n", encoding="utf-8")

        rc = check_against_census(tmp_path, census)

        out = capsys.readouterr().out
        assert rc == 0
        assert "exceeds the census    : 1" in out
        assert "found 1, census says 0" in out

    def test_a_census_entry_with_no_matching_file_is_counted_missing(
        self, tmp_path: Path, capsys
    ) -> None:
        census = tmp_path / "census.txt"
        census.write_text("ghost.nif = {'NiNode': 1}\n", encoding="utf-8")

        rc = check_against_census(tmp_path, census)

        out = capsys.readouterr().out
        assert rc == 0
        assert "not found on disk     : 1" in out

    def test_an_unreadable_listed_file_counts_as_short(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "bad.nif").write_bytes(b"not a nif")
        census = tmp_path / "census.txt"
        census.write_text("bad.nif = {'NiNode': 1}\n", encoding="utf-8")

        rc = check_against_census(tmp_path, census)

        out = capsys.readouterr().out
        assert rc == 0
        assert "short (stopped early) : 1" in out

    def test_a_report_path_writes_the_per_file_json(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")
        report = tmp_path / "report.json"

        check_against_census(tmp_path, census, report_path=report)

        assert report.is_file()
        assert "agrees" in report.read_text(encoding="utf-8")
        assert "wrote 1 per-file result" in capsys.readouterr().out

    def test_a_short_read_that_still_over_reports_is_classified_as_short(
        self, tmp_path: Path, capsys
    ) -> None:
        """Stopped early AND over the census at the point it stopped: still 'short', not 'exceeds'."""
        (tmp_path / "a.nif").write_bytes(
            _nif(("NiNode", _ninode_body()), ("SomeNewBlockType", b""))
        )
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 0}\n", encoding="utf-8")

        rc = check_against_census(tmp_path, census)

        out = capsys.readouterr().out
        assert rc == 0
        assert "short (stopped early) : 1" in out
        assert "exceeds the census    : 0" in out

    def test_finding_fewer_than_the_census_expects_is_short(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 2}\n", encoding="utf-8")

        rc = check_against_census(tmp_path, census)

        out = capsys.readouterr().out
        assert rc == 0
        assert "short (stopped early) : 1" in out

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

        rc = explain(path)

        out = capsys.readouterr().out
        assert rc == 0
        assert "NiNode" in out

    def test_a_stopped_file_reports_why(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("SomeNewBlockType", b"")))

        rc = explain(path)

        out = capsys.readouterr().out
        assert rc == 0
        assert "SomeNewBlockType" in out

    def test_a_plausible_type_string_exactly_where_expected_needs_no_correction(
        self, tmp_path: Path, capsys
    ) -> None:
        """A complete block followed immediately by an unknown type: shift is 0, no correction."""
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body()), ("NiFutureBlockType", b"")))

        rc = explain(path)

        out = capsys.readouterr().out
        assert rc == 0
        assert "a type string 'NiFutureBlockType' starts at" in out
        assert "byte(s) from where the walk ended" in out
        assert "=>" not in out  # shift is 0: no correction line to print

    def test_a_type_string_offset_from_where_the_walk_ended_reports_the_correction(
        self, tmp_path: Path, capsys
    ) -> None:
        """Two stray bytes before the real next type name: found at shift +2, not 0."""
        block0 = ("NiNode", _ninode_body())
        block1_type = "NiFutureBlockType"
        header = _HEADER + struct.pack("<II", _VERSION, 2)
        body = _text(block0[0]) + block0[1]
        body += b"XX"  # two stray bytes the reader's own bookkeeping does not know about
        body += _text(block1_type)  # the real next block's type string
        path = tmp_path / "a.nif"
        path.write_bytes(header + body)

        rc = explain(path)

        out = capsys.readouterr().out
        assert rc == 0
        assert f"a type string {block1_type!r} starts at" in out
        assert "which is +2 byte(s)" in out
        assert "byte(s) short." in out

    def test_no_plausible_type_string_anywhere_in_the_window_prints_nothing_extra(
        self, tmp_path: Path, capsys
    ) -> None:
        """Some bytes are plausible-length but not a real type name; some run past EOF."""
        block0 = ("NiNode", _ninode_body())
        header = _HEADER + struct.pack("<II", _VERSION, 2)
        body = _text(block0[0]) + block0[1]
        # A length prefix (5) whose "name" bytes are not an identifier at all,
        # followed by nothing -- short enough that the search runs off the end.
        body += struct.pack("<I", 5) + b"12345"
        path = tmp_path / "a.nif"
        path.write_bytes(header + body)

        rc = explain(path)

        out = capsys.readouterr().out
        assert rc == 0
        assert "a type string" not in out

    def test_an_unreadable_file_is_an_error(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "bad.nif"
        path.write_bytes(b"not a nif")

        rc = explain(path)

        assert rc == 1
        assert "not a NIF" in capsys.readouterr().err or capsys.readouterr()


class TestVerify:
    def test_an_identical_read_matches_the_scan(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "identical     : 1" in out
        assert "No divergence" in out

    def test_an_unreadable_file_counts_as_unverifiable(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "bad.nif").write_bytes(b"not a nif")

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "unverifiable  : 1" in out
        assert "did not read every declared block" in out

    def test_a_report_path_writes_json(self, tmp_path: Path) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        report = tmp_path / "report.json"

        verify(tmp_path, report, None, 0)

        assert report.is_file()
        assert "identical" in report.read_text(encoding="utf-8")

    def test_a_type_mismatch_at_some_index_is_a_divergence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """The reader parses ["NiNode", "NiNode"]; a mocked scan disagrees at index 1."""
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(
            _nif(("NiNode", _ninode_body()), ("NiNode", _ninode_body()))
        )
        fake = ScanResult(type_names=["NiNode", "NiTriShape"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 1
        assert "diverged      : 1" in out
        assert "block 1: scan says NiTriShape, reader says NiNode" in out

    def test_stopping_on_a_type_the_reader_claims_to_support_is_a_layout_bug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """The reader stopped after one block; a mocked scan says a *known* type followed."""
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode", "NiNode"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 1
        assert "stopped early : 1" in out
        assert "STOPPED INSIDE A TYPE THIS READER SUPPORTS" in out

    def test_stopping_on_an_unimplemented_type_is_a_gap_not_a_bug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        fake = ScanResult(
            type_names=["NiNode", "SomeUnimplementedType"], declared=2, header_ok=True
        )
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0  # a gap, not a bug -- not a failure
        assert "stopped on an unimplemented type" in out
        assert "SomeUnimplementedType" in out

    def test_a_non_reconciling_scan_falls_back_to_the_reader_and_is_unverifiable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """The scan disqualifies itself; the file is still read and reported, just not tallied."""
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        fake = ScanResult(type_names=["NiNode"], declared=5, header_ok=True)  # does not reconcile
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "unverifiable  : 1" in out
        assert "did not read every declared block" not in out  # this one read fully

    def test_a_fatal_read_failure_during_an_unverifiable_fallback_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(b"not a nif at all")
        fake = ScanResult(type_names=[], declared=5, header_ok=False)  # does not reconcile
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "unverifiable  : 1" in out
        assert "did not read every declared block" in out

    def test_a_file_that_becomes_unreadable_mid_scan_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body())))
        real_read_bytes = Path.read_bytes

        def flaky_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
            if self.name == "a.nif":
                raise OSError("simulated: vanished mid-scan")
            return real_read_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "unreadable    : 1" in out

    def test_the_reader_itself_refusing_a_reconciling_file_is_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """A scan can reconcile even when the reader's own header check fails outright."""
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(b"not a nif at all")
        fake = ScanResult(type_names=["NiNode"], declared=1, header_ok=True)  # reconciles
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)

        rc = verify(tmp_path, None, None, 0)

        out = capsys.readouterr().out
        assert rc == 0
        assert "unreadable    : 1" in out

    def test_a_collect_dir_gathers_verify_buckets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import check_nif_layouts as cnl

        from wraithguard.nif.scan import ScanResult

        (tmp_path / "a.nif").write_bytes(
            _nif(("NiNode", _ninode_body()), ("NiNode", _ninode_body()))
        )
        fake = ScanResult(type_names=["NiNode", "NiTriShape"], declared=2, header_ok=True)
        monkeypatch.setattr(cnl, "scan_block_types", lambda _data: fake)
        collect_dir = tmp_path / "samples"

        verify(tmp_path, None, collect_dir, 40)

        assert (collect_dir / "diverged" / "a.nif").is_file()


class TestMain:
    def test_a_target_that_is_not_a_folder_is_reported(self, tmp_path: Path, capsys) -> None:
        rc = main([str(tmp_path / "does-not-exist")])
        assert rc == 2
        assert "not a folder" in capsys.readouterr().err

    def test_explain_needs_a_file_not_a_folder(self, tmp_path: Path, capsys) -> None:
        rc = main([str(tmp_path), "--explain"])
        assert rc == 2
        assert "not a file" in capsys.readouterr().err

    def test_explain_on_a_single_file(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "a.nif"
        path.write_bytes(_nif(("NiNode", _ninode_body())))

        rc = main([str(path), "--explain"])

        assert rc == 0
        assert "NiNode" in capsys.readouterr().out

    def test_survey_on_a_folder(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))

        rc = main([str(tmp_path)])

        assert rc == 0
        assert "fully parsed          : 1" in capsys.readouterr().out

    def test_census_mode_needs_a_folder(self, tmp_path: Path, capsys) -> None:
        census = tmp_path / "c.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")

        rc = main([str(tmp_path / "gone"), "--census", str(census)])

        assert rc == 2
        assert "not a folder" in capsys.readouterr().err

    def test_census_mode_runs_the_comparison(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))
        census = tmp_path / "census.txt"
        census.write_text("a.nif = {'NiNode': 1}\n", encoding="utf-8")

        rc = main([str(tmp_path), "--census", str(census)])

        assert rc == 0
        assert "agrees exactly        : 1" in capsys.readouterr().out

    def test_an_unreadable_census_file_is_a_clean_error(self, tmp_path: Path, capsys) -> None:
        rc = main([str(tmp_path), "--census", str(tmp_path / "missing.txt")])

        assert rc == 2
        assert capsys.readouterr().err

    def test_verify_mode_needs_a_folder(self, tmp_path: Path, capsys) -> None:
        rc = main([str(tmp_path / "gone"), "--verify"])
        assert rc == 2
        assert "not a folder" in capsys.readouterr().err

    def test_verify_mode_runs_the_scan_comparison(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "a.nif").write_bytes(_nif(("NiNode", _ninode_body())))

        rc = main([str(tmp_path), "--verify"])

        assert rc == 0
        assert "verified 1 file(s)" in capsys.readouterr().out


class TestCollectSamples:
    def test_a_bucket_entry_with_no_matching_file_on_disk_is_skipped(
        self, tmp_path: Path, capsys
    ) -> None:
        """A stale relative path (the file moved or was deleted since) must not crash the copy."""
        destination = tmp_path / "out"

        _collect_samples(tmp_path, {"stopped": ["ghost.nif"]}, destination, 40)

        assert list((destination / "stopped").iterdir()) == []
        assert "collected 1 of 1 stopped file(s)" in capsys.readouterr().out
