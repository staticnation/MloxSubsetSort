"""Verify conformance to the PEPs that define standards for this codebase.

There are 700+ PEPs. Most are informational (PEP 20's Zen), process documents
(PEP 1), rejected proposals, or *optional* language features -- using
``match`` where ``if``/``elif`` reads better would make the code worse, not
more compliant. So "apply every PEP" is not a checkable claim.

What *is* checkable is the finite set of PEPs that define a standard this
project should conform to. Each is asserted here, mechanically, so the claim
survives future edits instead of resting on a report someone wrote once:

============ ==================================== =========================
PEP          Standard                             Checked by
============ ==================================== =========================
PEP 8        Style, naming, import order          ruff E/W/N/I + black
PEP 257      Docstring conventions                ruff D
PEP 484/526  Type hints, variable annotations     ruff ANN
PEP 563      ``from __future__ import annotations`` this module
PEP 585/604  ``list[str]``, ``X | Y``             ruff UP + this module
PEP 3120     UTF-8 source encoding                this module
PEP 263      No contradictory coding declaration  this module
PEP 3131     ASCII identifiers                    this module
PEP 328      Absolute imports                     this module
PEP 440      Version identifier format            this module
PEP 621      pyproject.toml project metadata      this module
PEP 561      ``py.typed`` marker                  this module
PEP 594      No "dead battery" stdlib modules     this module
PEP 632      No ``distutils``                     this module
PEP 394      ``python3`` in shebangs              this module
============ ==================================== =========================

The ruff-enforced rows are covered by ``python -m ruff check .`` in CI rather
than duplicated here; this module covers what a linter does not check.
"""

from __future__ import annotations

import ast
import re
import sys
import tokenize
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Every first-party source file. Excludes generated code and vendored trees.
SOURCE_FILES = sorted(
    path
    for path in [
        *PROJECT_ROOT.glob("*.py"),
        *(PROJECT_ROOT / "mlox_subset").rglob("*.py"),
        *(PROJECT_ROOT / "tests").rglob("*.py"),
        *(PROJECT_ROOT / "tools").rglob("*.py"),
    ]
    if "opcodes.py" not in path.name  # generated; style is the generator's
)

#: Modules removed from the standard library by PEP 594, plus the PEP 632
#: removal. Importing any of these breaks on a modern interpreter.
DEAD_BATTERIES = frozenset(
    {
        "aifc",
        "asynchat",
        "asyncore",
        "audioop",
        "cgi",
        "cgitb",
        "chunk",
        "crypt",
        "distutils",
        "imghdr",
        "imp",
        "mailcap",
        "msilib",
        "nis",
        "nntplib",
        "ossaudiodev",
        "pipes",
        "smtpd",
        "sndhdr",
        "spwd",
        "sunau",
        "telnetlib",
        "uu",
        "xdrlib",
    }
)


def _pyproject() -> dict:
    """Load ``pyproject.toml``.

    Returns:
        The parsed document.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        tomllib = pytest.importorskip("tomli")
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _parse(path: Path) -> ast.Module:
    """Parse a source file into an AST."""
    return ast.parse(path.read_text(encoding="utf-8"))


def test_source_files_were_discovered() -> None:
    """Guard against the glob silently matching nothing.

    Without this, every parametrised test below would vacuously pass on a
    restructured checkout -- the failure mode where a suite looks green
    precisely because it stopped testing anything.
    """
    assert len(SOURCE_FILES) > 20


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_pep3120_source_is_utf8(path: Path) -> None:
    """PEP 3120: source is UTF-8."""
    path.read_bytes().decode("utf-8")


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_pep263_no_contradictory_encoding_declaration(path: Path) -> None:
    """PEP 263: any coding declaration must agree with UTF-8.

    A stale ``# -*- coding: latin-1 -*-`` would silently change how the file
    is decoded, which is worse than having no declaration at all.
    """
    with path.open("rb") as handle:
        encoding, _lines = tokenize.detect_encoding(handle.readline)
    assert encoding.lower().replace("_", "-") in {"utf-8", "utf-8-sig"}


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_pep3131_identifiers_are_ascii(path: Path) -> None:
    """PEP 3131 permits non-ASCII identifiers; this project does not use them.

    Homoglyphs -- Cyrillic U+0430 against Latin U+0061, say -- make two
    different names look identical in review, so they are excluded by policy.
    String *contents* are unrestricted; the UI text is not ASCII-only.

    (Writing the example characters literally here trips ruff's own RUF002,
    which is a fair demonstration of the problem.)
    """
    offenders = [
        node.id
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Name) and not node.id.isascii()
    ]
    assert not offenders, f"non-ASCII identifiers: {offenders}"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_pep328_no_implicit_relative_imports(path: Path) -> None:
    """PEP 328: relative imports are explicit, or absolute.

    This project uses absolute imports throughout, so any relative import at
    all would be an inconsistency worth catching.
    """
    relative = [
        node.module or "."
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert not relative, f"relative imports: {relative}"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_pep594_and_632_no_removed_stdlib_modules(path: Path) -> None:
    """PEP 594 / PEP 632: no modules removed from the standard library."""
    imported: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert not (imported & DEAD_BATTERIES)


@pytest.mark.parametrize(
    "path",
    [p for p in SOURCE_FILES if p.name != "__init__.py" or p.stat().st_size > 200],
    ids=lambda p: p.name,
)
def test_pep563_future_annotations(path: Path) -> None:
    """PEP 563: every module opts into postponed annotation evaluation.

    Consistency matters more than the individual benefit: with it on
    everywhere, an annotation can name a type that is only imported under
    ``TYPE_CHECKING`` without anyone having to check first.
    """
    tree = _parse(path)
    has_future = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
    has_annotations = any(
        isinstance(node, (ast.AnnAssign, ast.arg)) and getattr(node, "annotation", None)
        for node in ast.walk(tree)
    ) or any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns
        for node in ast.walk(tree)
    )
    if has_annotations:
        assert has_future, "annotated module without `from __future__ import annotations`"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_pep585_604_no_legacy_typing_aliases(path: Path) -> None:
    """PEP 585/604: builtin generics and ``X | Y``, not ``List``/``Optional``.

    ruff's ``UP`` rules cover this, but they are configurable; this pins it
    directly so turning a rule off cannot quietly reintroduce the old spelling.
    """
    legacy = {"List", "Dict", "Set", "FrozenSet", "Tuple", "Type", "Optional", "Union"}
    found: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            found |= {a.name for a in node.names} & legacy
    assert not found, f"legacy typing aliases imported: {sorted(found)}"


def test_pep394_shebangs_specify_python3() -> None:
    """PEP 394: an executable script's shebang names ``python3``, not ``python``.

    On systems where ``python`` still resolves to 2.x, or to nothing, a bare
    ``python`` shebang fails in a way that looks like the tool is broken.
    """
    for path in SOURCE_FILES:
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        if first.startswith("#!"):
            assert "python3" in first, f"{path.name}: {first}"


def test_pep440_version_is_valid() -> None:
    """PEP 440: ``__version__`` is a valid public version identifier."""
    import mlox_subset

    # The canonical public-version regex from PEP 440, appendix B.
    pattern = (
        r"^([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*"
        r"((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?"
        r"(\.dev(0|[1-9][0-9]*))?$"
    )
    assert re.match(pattern, mlox_subset.__version__), mlox_subset.__version__


def test_pep621_metadata_present_and_consistent() -> None:
    """PEP 621: ``[project]`` exists, and its version matches the package.

    Two declarations of the same fact drift apart the moment one is bumped
    and the other is forgotten, so the agreement is asserted rather than
    trusted.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        tomllib = pytest.importorskip("tomli")

    import mlox_subset

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    project = config.get("project")
    assert project is not None, "pyproject.toml has no [project] table"
    for field in ("name", "version", "description", "requires-python"):
        assert project.get(field), f"[project].{field} is missing"
    assert project["version"] == mlox_subset.__version__


def test_pep484_mypy_gate_is_configured() -> None:
    """PEP 484: type checking is enforced, not merely available.

    Asserts the *configuration*, not a mypy run -- the check itself belongs in
    CI where it can take the time. What this catches is the gate being quietly
    weakened: ``files`` narrowed, ``check_untyped_defs`` flipped back off, or
    ``mlox_subset`` dropped from the checked set.

    The distinction matters because annotations without a checker are only
    documentation, and documentation that is never verified drifts. When mypy
    was first pointed at this package it found 22 errors, all of them in
    hand-written annotations that were simply wrong.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        tomllib = pytest.importorskip("tomli")

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        mypy_config = tomllib.load(handle)["tool"]["mypy"]

    assert mypy_config.get("files") == [
        "mlox_subset"
    ], "mypy must check the whole mlox_subset package"
    assert mypy_config.get("check_untyped_defs") is True
    assert mypy_config.get("warn_unused_ignores") is True


@pytest.mark.parametrize(
    "path",
    [p for p in SOURCE_FILES if "mlox_subset" in p.parts],
    ids=lambda p: p.name,
)
def test_pep20_silenced_errors_are_explicitly_silenced(path: Path) -> None:
    """PEP 20: "Errors should never pass silently. Unless explicitly silenced."

    The only line of the Zen that can be checked mechanically, and the one
    worth checking: a bare ``except ...: pass`` is either a deliberate decision
    or a swallowed bug, and the two are indistinguishable from the outside.
    Requiring a comment forces the author to say which.

    It does not judge whether the silence is *correct* -- that is a review
    question. It only insists the reasoning was written down. Two handlers in
    this codebase failed when this was first run.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    unexplained = []
    for handler in ast.walk(ast.parse(source)):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Pass):
            continue
        window = lines[max(0, handler.lineno - 2) : handler.body[0].lineno + 1]
        if not any("#" in line for line in window):
            unexplained.append(handler.lineno)
    assert not unexplained, (
        f"silent `except: pass` with no reason given at line(s) {unexplained} -- "
        f"say why the error is being swallowed"
    )


def test_pep518_build_system_declared() -> None:
    """PEP 518/517: the build requirements and backend are declared.

    Required as soon as ``[project]`` exists. Without it a build tool must
    guess, and the historical guess -- setuptools, implicitly -- is precisely
    what these PEPs exist to eliminate.
    """
    config = _pyproject()
    build_system = config.get("build-system")
    assert build_system is not None, "pyproject.toml has no [build-system] table"
    assert build_system.get("requires"), "[build-system].requires is empty"
    assert build_system.get("build-backend"), "[build-system].build-backend is missing"


def test_pep508_dependency_specifiers_are_valid() -> None:
    """PEP 508: every dependency string parses as a requirement specifier.

    A typo here is silent until someone tries to install the extra.
    """
    requirements = pytest.importorskip("packaging.requirements")
    project = _pyproject()["project"]
    specifiers = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specifiers.extend(extra)
    for spec in specifiers:
        requirements.Requirement(spec)  # raises InvalidRequirement if malformed


def test_pep420_every_package_directory_is_explicit() -> None:
    """PEP 420: no accidental implicit namespace packages.

    A subpackage missing ``__init__.py`` still imports, as a namespace package
    -- until it is bundled by PyInstaller, which does not collect them the same
    way. The failure would appear only in the built binary, which is the worst
    place to find it.
    """
    for directory in (PROJECT_ROOT / "mlox_subset").rglob("*"):
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        assert (directory / "__init__.py").is_file(), f"{directory} has no __init__.py"


def test_declared_packages_match_what_exists() -> None:
    """Every real subpackage is listed for the build, and vice versa.

    Adding a subpackage without declaring it produces a wheel that imports on
    the developer's machine and fails everywhere else.
    """
    config = _pyproject()
    declared = set(config["tool"]["setuptools"]["packages"])
    actual = {"mlox_subset"} | {
        "mlox_subset." + directory.name
        for directory in (PROJECT_ROOT / "mlox_subset").iterdir()
        if directory.is_dir()
        and directory.name != "__pycache__"
        and (directory / "__init__.py").is_file()
    }
    assert declared == actual, f"declared={sorted(declared)} actual={sorted(actual)}"


def test_pep561_py_typed_marker_present() -> None:
    """PEP 561: a package shipping inline types advertises them.

    Without ``py.typed`` a type checker in a consuming project silently
    ignores every annotation in this package -- the annotations would still
    be there, and still be useless.
    """
    assert (PROJECT_ROOT / "mlox_subset" / "py.typed").is_file()


def test_requires_python_matches_the_running_interpreter() -> None:
    """The declared floor is not above the interpreter the tests run on.

    Catches the case where ``requires-python`` is raised without anyone
    checking the toolchain still satisfies it.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        tomllib = pytest.importorskip("tomli")

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        requires = tomllib.load(handle)["project"]["requires-python"]

    floor = tuple(int(part) for part in requires.lstrip(">=").split("."))
    assert (
        sys.version_info[: len(floor)] >= floor
    ), f"running {sys.version_info[:2]} but requires-python is {requires}"
