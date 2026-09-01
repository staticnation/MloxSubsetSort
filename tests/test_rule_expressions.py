"""The mlox boolean-expression tokeniser and its S-expression parser.

The real predicate evaluation is covered in ``test_predicate_eval``; this pins
the small parser edge that the evaluator never reaches on its own -- an empty
token stream -- and the round trip from source text to a nested AST.
"""

from __future__ import annotations

from pathlib import Path

from wraithguard.rules.expressions import (
    describe_node,
    load_rules_raw_text,
    parse_mlox_lisp,
    tokenize_mlox_logic,
)


def test_parsing_no_tokens_is_an_empty_ast() -> None:
    """An empty token list parses to an empty tree, not an error."""
    assert parse_mlox_lisp([]) == []


def test_a_nested_expression_parses_to_a_nested_list() -> None:
    """Brackets become nesting; the operator leads each group."""
    ast = parse_mlox_lisp(tokenize_mlox_logic("[ANY a.esp [NOT b.esp]]"))
    assert ast == [["ANY", "a.esp", ["NOT", "b.esp"]]]


def test_describe_node_renders_a_group_readably() -> None:
    """A parsed group renders back to a human-readable string."""
    ast = parse_mlox_lisp(tokenize_mlox_logic("[ANY a.esp b.esp]"))
    text = describe_node(ast[0])
    assert "a.esp" in text
    assert "b.esp" in text


def test_load_rules_raw_text_concatenates_readable_files(tmp_path: Path) -> None:
    """Every readable rule file's body is joined in order."""
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    text = load_rules_raw_text([tmp_path])
    assert "first" in text
    assert "second" in text


def test_load_rules_raw_text_skips_an_unreadable_file(tmp_path: Path, monkeypatch) -> None:
    """One unreadable file is logged and skipped, not fatal to the rest."""
    good = tmp_path / "ok.txt"
    good.write_text("kept", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("unreadable", encoding="utf-8")

    real_read = Path.read_text

    def selective(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "bad.txt":
            raise OSError("locked")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", selective)
    text = load_rules_raw_text([tmp_path])
    assert "kept" in text
    assert "unreadable" not in text
