"""Tests for ``tools/check_undefined.py``, the relocation-safety checker.

The tool reads a module a name at a time and reports any that are loaded but
never bound or imported -- the ``NameError``-at-runtime that a module split
produces. Its whole value is not crying wolf: closures, comprehensions,
``except`` and ``with`` aliases, lambda parameters and ``global`` all bind, and
missing any one of them turns a real edit into a wall of false positives. So the
tests lean on the negatives (self-consistent code reports nothing) as hard as on
the positives (a genuine typo is caught).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_undefined import main, undefined_names


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_a_self_consistent_module_reports_nothing(tmp_path: Path) -> None:
    """Imports, defs and assignments all count as bound; nothing is missing."""
    src = "import os\n\nHOME = os.getcwd()\n\n\ndef where() -> str:\n    return HOME\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_bare_undefined_name_is_reported(tmp_path: Path) -> None:
    """A name neither imported nor bound anywhere is the case the tool exists for."""
    src = "def f():\n    return strip_comment(x)\n"
    assert undefined_names(_write(tmp_path, src)) == ["strip_comment", "x"]


def test_builtins_are_never_reported(tmp_path: Path) -> None:
    """``len``/``print`` and friends are available without an import."""
    src = "def f(items):\n    print(len(items))\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_closure_over_an_enclosing_variable_is_not_a_false_positive(tmp_path: Path) -> None:
    """A nested function reading its parent's local must not look undefined."""
    src = "def outer():\n    total = 0\n\n    def inner():\n        return total\n\n    return inner\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_comprehension_and_walrus_targets_bind(tmp_path: Path) -> None:
    """Loop, comprehension and walrus targets are all locally bound."""
    src = (
        "def f(rows):\n"
        "    doubled = [n * 2 for n in rows]\n"
        "    if (m := len(doubled)):\n"
        "        return m\n"
        "    return 0\n"
    )
    assert undefined_names(_write(tmp_path, src)) == []


def test_except_and_with_aliases_bind(tmp_path: Path) -> None:
    """An ``except ... as e`` name and a ``with ... as f`` name both count."""
    src = (
        "def f(path):\n"
        "    try:\n"
        "        with open(path) as handle:\n"
        "            return handle.read()\n"
        "    except OSError as err:\n"
        "        return str(err)\n"
    )
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_module_level_lambda_is_checked(tmp_path: Path) -> None:
    """A lambda body is a scope too; a typo inside one is caught."""
    src = "f = lambda x: x + typo\n"
    assert undefined_names(_write(tmp_path, src)) == ["typo"]


def test_a_lambda_parameter_is_bound(tmp_path: Path) -> None:
    """The lambda's own parameter must not be reported as undefined."""
    src = "square = lambda n: n * n\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_global_declaration_binds_the_name(tmp_path: Path) -> None:
    """A ``global`` name is treated as bound within the function."""
    src = "def f():\n    global counter\n    counter = counter + 1\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_module_level_annotated_assignment_binds(tmp_path: Path) -> None:
    """A bare ``NAME: type = value`` at module scope is a definition."""
    src = "COUNT: int = 3\n\n\ndef f() -> int:\n    return COUNT\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_varargs_and_kwargs_parameters_bind(tmp_path: Path) -> None:
    """``*args`` and ``**kwargs`` are parameters, not undefined names."""
    src = "def f(*args, **kwargs):\n    return len(args) + len(kwargs)\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_class_defined_inside_a_function_binds(tmp_path: Path) -> None:
    """A class nested in a function is bound in that scope."""
    src = "def make():\n" "    class Inner:\n" "        value = 1\n\n" "    return Inner\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_an_import_inside_a_function_binds(tmp_path: Path) -> None:
    """A function-local import counts as bound within that function."""
    src = "def f():\n    import json\n\n    return json.dumps({})\n"
    assert undefined_names(_write(tmp_path, src)) == []


def test_a_method_body_inside_a_class_is_checked(tmp_path: Path) -> None:
    """Class bodies are recursed into, so a typo in a method is caught."""
    src = "class C:\n    def m(self):\n        return oops\n"
    assert undefined_names(_write(tmp_path, src)) == ["oops"]


def test_main_prints_ok_and_returns_zero_for_a_clean_file(tmp_path, capsys) -> None:
    """A clean file is reported ``ok`` with a zero exit."""
    path = _write(tmp_path, "import os\n\n\ndef f():\n    return os.name\n")
    assert main(["check_undefined.py", str(path)]) == 0
    assert "ok" in capsys.readouterr().out


def test_main_reports_the_missing_names_and_returns_one(tmp_path, capsys) -> None:
    """A file with an undefined name is named and the exit is non-zero."""
    path = _write(tmp_path, "def f():\n    return nope\n")
    assert main(["check_undefined.py", str(path)]) == 1
    assert "nope" in capsys.readouterr().out


def test_main_with_no_files_prints_usage_and_returns_two(capsys) -> None:
    """Invoked with no arguments, the tool prints its docstring and exits 2."""
    assert main(["check_undefined.py"]) == 2
    assert "Usage" in capsys.readouterr().out
