"""Hardening tests: malformed, hostile and unusual real-world input.

Every case here corresponds to a defect found by adversarial probing of the
parsers and writers. The tool consumes files it did not create -- plugins from
the internet, hand-edited cfg/TOML, downloads -- so "does not crash" and "does
not corrupt" are features, not implementation details.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

# Deliberately malformed TES3 byte streams. Each has broken something a naive
# reader would trust: the magic, a declared size, or a subrecord boundary.
MALFORMED_PLUGINS: dict[str, bytes] = {
    "empty": b"",
    "magic_only": b"TES3",
    "partial_size_field": b"TES3\x10\x00",
    "header_no_body": b"TES3" + struct.pack("<III", 100, 0, 0),
    "declared_size_exceeds_file": b"TES3" + struct.pack("<III", 0xFFFFFFFF, 0, 0) + b"AB",
    "zero_length_record": b"TES3" + struct.pack("<III", 0, 0, 0) * 2,
    "subrecord_size_overflow": (
        b"TES3"
        + struct.pack("<III", 20, 0, 0)
        + struct.pack("<4sI", b"MAST", 0xFFFFFFFF)
        + b"x" * 8
    ),
    "zero_length_subrecords": (
        b"TES3"
        + struct.pack("<III", 16, 0, 0)
        + struct.pack("<4sI", b"MAST", 0)
        + struct.pack("<4sI", b"DATA", 0)
    ),
    "truncated_mid_subrecord": (
        b"TES3" + struct.pack("<III", 40, 0, 0) + struct.pack("<4sI", b"MAST", 30) + b"short"
    ),
    "mast_without_data": (
        b"TES3" + struct.pack("<III", 13, 0, 0) + struct.pack("<4sI", b"MAST", 5) + b"a.esm"
    ),
    "data_field_too_short": (
        b"TES3"
        + struct.pack("<III", 21, 0, 0)
        + struct.pack("<4sI", b"MAST", 5)
        + b"a.esm"
        + struct.pack("<4sI", b"DATA", 4)
        + b"\x01\x02\x03\x04"
    ),
    "non_utf8_master_name": (
        b"TES3" + struct.pack("<III", 14, 0, 0) + struct.pack("<4sI", b"MAST", 6) + b"\xff\xfe.esm"
    ),
    "many_empty_records": (
        b"TES3" + struct.pack("<III", 0, 0, 0) + (b"STAT" + struct.pack("<III", 0, 0, 0)) * 500
    ),
}


@pytest.fixture(params=sorted(MALFORMED_PLUGINS), ids=sorted(MALFORMED_PLUGINS))
def malformed_plugin(request, tmp_path: Path) -> Path:
    path = tmp_path / f"{request.param}.esp"
    path.write_bytes(MALFORMED_PLUGINS[request.param])
    return path


class TestBinaryReadersTolerateGarbage:
    """Readers must degrade to "no data", never raise or hang."""

    def test_read_plugin_masters(self, core, malformed_plugin):
        assert isinstance(core.read_plugin_masters(malformed_plugin), list)

    def test_read_plugin_masters_with_sizes(self, core, malformed_plugin):
        assert isinstance(core.read_plugin_masters_with_sizes(malformed_plugin), list)

    def test_parse_tes3_records(self, core, malformed_plugin):
        assert isinstance(list(core.parse_tes3_records(malformed_plugin)), list)

    def test_read_savegame_content_files(self, core, malformed_plugin):
        files, error = core.read_savegame_content_files(malformed_plugin)
        assert files is None or isinstance(files, list)
        assert files is not None or error


class TestResyncNeverCorrupts:
    """sync_plugin_master_sizes writes to the user's plugins -- the blast
    radius of a mistake here is real data loss."""

    def test_malformed_input_is_rejected_without_mutation(self, core, malformed_plugin, tmp_path):
        index = core.PluginFileIndex([str(tmp_path)])
        before = malformed_plugin.read_bytes()

        updated, _unresolved, _error = core.sync_plugin_master_sizes(malformed_plugin, index)

        after = malformed_plugin.read_bytes()
        assert len(after) == len(before), "file size changed"
        if not updated:
            assert after == before, "file mutated without reporting an update"

    def test_magic_only_file_is_rejected_cleanly(self, core, tmp_path):
        """Regression: a 4-byte file passed the magic check then unpacked past
        the end of the buffer, raising struct.error."""
        stub = tmp_path / "Truncated.esp"
        stub.write_bytes(b"TES3")

        updated, unresolved, error = core.sync_plugin_master_sizes(stub, tmp_path)

        assert error is not None and "not a TES3" in error
        assert updated == [] and unresolved == []
        assert stub.read_bytes() == b"TES3"


class TestScannersTolerateGarbage:
    def test_scanners_survive_a_directory_of_broken_plugins(self, core, tmp_path):
        for name, blob in MALFORMED_PLUGINS.items():
            (tmp_path / f"{name}.esp").write_bytes(blob)
        (tmp_path / "plain_text.esp").write_text("this is not a plugin")
        index = core.PluginFileIndex([str(tmp_path)])
        names = sorted(p.name for p in tmp_path.iterdir())

        warnings, stats = core.lint_plugins(names, index, subset_names=names)
        missing, *_rest, problems = core.check_missing_masters(names, index)
        coverage = core.build_cell_coverage(names, index, subset_names=names)

        assert isinstance(warnings, list) and isinstance(stats, dict)
        assert isinstance(missing, list) and isinstance(problems, set)
        assert "exterior" in coverage and "interior" in coverage


class TestCfgEncodingRoundTrip:
    """openmw.cfg may contain bytes that are not valid UTF-8 (a cp1252
    accented mod folder). Losing them rewrites the user's data= path and
    breaks their load order."""

    NON_UTF8 = (
        'data="E:/Mods/Caf\xe9/Data Files"\n' "content=Morrowind.esm\n" "content=Caf\xe9Mod.esp\n"
    ).encode("latin-1")

    def test_round_trip_is_byte_preserving(self, core, tmp_path):
        cfg = tmp_path / "openmw.cfg"
        cfg.write_bytes(self.NON_UTF8)

        lines, _cp, _content, _dp, _data = core.read_cfg(cfg)
        core.write_cfg(cfg, lines, [], dry_run=False, no_backup=True)

        assert cfg.read_bytes() == self.NON_UTF8

    def test_backup_is_byte_identical(self, core, tmp_path):
        """Regression: the backup decoded then re-encoded as UTF-8, which
        raised UnicodeDecodeError and blocked export entirely."""
        cfg = tmp_path / "openmw.cfg"
        cfg.write_bytes(self.NON_UTF8)

        core.backup_file(cfg, no_backup=False)

        backup = next(tmp_path.glob("openmw.cfg.bak-*"))
        assert backup.read_bytes() == self.NON_UTF8

    def test_unicode_content_is_parsed(self, core, tmp_path):
        cfg = tmp_path / "openmw.cfg"
        cfg.write_bytes('data="E:/Mods/日本語"\ncontent=Ünïcode.esp\n'.encode())

        _lines, _cp, content, _dp, data = core.read_cfg(cfg)

        assert [name for name, _ in content] == ["Ünïcode.esp"]
        assert "日本語" in data[0]

    def test_subset_file_with_non_utf8_bytes_does_not_crash(self, core, tmp_path):
        subset = tmp_path / "subset.txt"
        subset.write_bytes("Caf\xe9Mod.esp\nOther.esp\n".encode("latin-1"))

        plugins, _data_paths = core.extract_subset_from_subset_file(subset)

        assert len(plugins) == 2
        assert plugins[1] == "Other.esp"

    def test_non_utf8_toml_reports_clearly(self, core, tmp_path):
        """TOML is spec-mandated UTF-8; the user needs an actionable message,
        not a raw UnicodeDecodeError."""
        toml_file = tmp_path / "customizations.toml"
        toml_file.write_bytes(b'[[Customizations]]\nlistName = "x"\ninsert = "Caf\xe9.esp"\n')

        with pytest.raises(SystemExit, match="not valid UTF-8"):
            core.extract_subset_from_toml(toml_file)


class TestCustomizationTypeSafety:
    """A TOML typo must not silently destroy the load order."""

    CFG = ["content=Alpha.esp", "content=Beta.esp", "content=Gamma.esp", 'data="E:/M/Core"']

    def test_string_instead_of_array_does_not_wipe_the_cfg(self, core):
        """Regression: iterating a string yielded single characters as removal
        patterns, which matched -- and deleted -- almost every line."""
        doc = "[[Customizations]]\nremoveContent = 'Alpha.esp'\n"

        lines, errors, _notes = core.simulate_configurator_apply(self.CFG, doc)

        assert lines == self.CFG, "cfg was modified by a malformed removeContent"
        assert any("must be an array" in e for e in errors)

    def test_non_string_entries_are_skipped_not_applied(self, core):
        doc = "[[Customizations]]\nremoveContent = ['Beta.esp', 123]\n"

        lines, errors, _notes = core.simulate_configurator_apply(self.CFG, doc)

        assert "content=Beta.esp" not in lines
        assert "content=Alpha.esp" in lines
        assert any("not a string" in e for e in errors)

    def test_non_table_customizations_entry_is_reported(self, core):
        lines, errors, _notes = core.simulate_configurator_apply(
            self.CFG, "Customizations = ['oops']\n"
        )

        assert lines == self.CFG
        assert any("not a table" in e for e in errors)

    def test_valid_array_still_removes(self, core):
        lines, errors, _notes = core.simulate_configurator_apply(
            self.CFG, "[[Customizations]]\nremoveContent = ['Beta.esp']\n"
        )

        assert not errors
        assert "content=Beta.esp" not in lines and len(lines) == 3

    @pytest.mark.parametrize(
        "document",
        [
            "this is not toml at all {{{",
            "",
            "[other]\nx = 1\n",
            "[[Customizations]]\n[[Customizations.insert]]\ninsert='X.esp'\n",
            "[[Customizations]]\n[[Customizations.insert]]\nafter='A.esp'\n",
            "[[Customizations]]\n[[Customizations.insert]]\ninsert='X'\nafter='A.esp'\nbefore='B.esp'\n",
            "[[Customizations]]\n[[Customizations.replace]]\nsource='A.esp'\n",
        ],
    )
    def test_malformed_documents_are_handled_not_raised(self, core, document):
        lines, errors, _notes = core.simulate_configurator_apply(self.CFG, document)
        assert lines is None or isinstance(lines, list)
        assert isinstance(errors, list)


class TestPatternMatchingEdgeCases:
    """Real plugin names contain regex metacharacters."""

    NAMES = [
        "Mod (v1.2).esp",
        "Mod [Final].esp",
        "Mod+Plus.esp",
        "A^B.esp",
        "C$D.esp",
        "E|F.esp",
        "G{2}.esp",
        "back\\slash.esp",
        "dot.any.esp",
    ]

    @pytest.mark.parametrize("name", NAMES)
    def test_exact_names_with_metacharacters_match_only_themselves(self, core, name):
        assert core.expand_pattern(name, self.NAMES) == [name]

    @pytest.mark.parametrize(
        "pattern", ["Mod [Final]*.esp", "Mod (v*).esp", "A^*.esp", "G{2,}*.esp", "bad[.esp"]
    )
    def test_wildcard_patterns_with_metacharacters_do_not_raise(self, core, pattern):
        assert isinstance(core.expand_pattern(pattern, self.NAMES), list)


class TestSortDegenerateInputs:
    @pytest.mark.parametrize(
        ("base", "subset", "masters"),
        [
            ([], [], {}),
            ([], ["A.esp"], {"a.esp": []}),
            (["A.esp"], [], {}),
            (["A.esp"], ["A.esp"], {"a.esp": []}),
            (["A.esp"], ["B.esp"], {"b.esp": ["B.esp"]}),  # self-master
            (["A.esp"], ["B.esp"], {"b.esp": ["Ghost.esm"]}),  # missing master
            (["A.esp", "A.esp", "B.esp"], ["C.esp"], {"c.esp": []}),  # duplicate cfg lines
        ],
    )
    def test_degenerate_inputs_do_not_raise(self, core, base, subset, masters):
        assert isinstance(core.build_and_sort(base, subset, [], masters), list)

    def test_deep_transitive_chain_resolves(self, core):
        """600 mods each mastering the previous one -- the anchor resolver must
        not blow the recursion limit."""
        depth = 600
        subset = [f"C{i}.esp" for i in range(depth)]
        masters = {
            f"c{i}.esp": ["Morrowind.esm"] + ([f"C{i - 1}.esp"] if i else []) for i in range(depth)
        }

        result = core.build_and_sort(["Morrowind.esm"], subset, [], masters)

        assert result[1:] == subset


class TestTomlValueEscaping:
    """Emitted TOML must reparse -- a broken quote would corrupt the file the
    Configurator consumes."""

    @pytest.mark.parametrize(
        "name",
        [
            "Uvirith's Legacy_3.53.ESP",
            'Say "Hello".esp',
            "back\\slash.esp",
            "both'and\".esp",
            "triple'''quote.esp",
            "tab\there.esp",
            "unicode_日本語.esp",
            "trailing space .esp",
        ],
    )
    def test_any_name_round_trips(self, core, name):
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            tomllib = pytest.importorskip("tomli")

        assert tomllib.loads(f"x = {core.toml_value(name)}")["x"] == name
