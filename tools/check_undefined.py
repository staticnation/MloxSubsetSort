"""Report names a module loads but neither imports nor defines.

Written during the module split, after relocating code produced a `NameError`
at runtime twice: once for `strip_comment`, once for `datetime`. A test run
reports the *first* missing name; this reports *all* of them, so a relocation
can be fixed in one pass instead of one failure at a time.

Deliberately conservative -- it over-collects locally bound names rather than
risk false positives, since a checker that cries wolf gets ignored. It models
closures and lambda parameters; getting those wrong produced fifteen false
positives on first use, which is exactly how such a tool stops being trusted.

Not a substitute for the linter. Ruff's ``F821`` catches things this misses
(a stale reference in an ``except`` branch, for one) and the two fail in
different directions, so run both.

Usage:
    python tools/check_undefined.py mlox_subset/net/updaters.py [more.py ...]

Exits non-zero if anything is reported.
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names available at module scope: imports, defs, classes, assignments."""
    names = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _bound_in(scope: ast.AST) -> set[str]:
    """Every name bound anywhere inside a function scope.

    Over-collects on purpose: nested comprehensions, walrus targets, ``with``
    aliases and ``except`` names all bind, and missing one would produce a
    false report.
    """
    bound: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            bound |= {a.arg for a in (*args.args, *args.posonlyargs, *args.kwonlyargs)}
            if args.vararg:
                bound.add(args.vararg.arg)
            if args.kwarg:
                bound.add(args.kwarg.arg)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(node.name)  # lambdas are anonymous
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    bound.add(sub.id)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    bound.add(sub.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.withitem) and node.optional_vars:
            for sub in ast.walk(node.optional_vars):
                if isinstance(sub, ast.Name):
                    bound.add(sub.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Global):
            bound |= set(node.names)
    return bound


def undefined_names(path: Path) -> list[str]:
    """Names loaded in ``path`` that are neither bound locally nor available.

    Args:
        path: A Python source file.

    Returns:
        Sorted names, empty when the module is self-consistent.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    available = _module_level_names(tree)
    missing: set[str] = set()

    def visit(scope: ast.AST, enclosing: set[str]) -> None:
        """Check one function scope, carrying names bound by its parents.

        Carrying ``enclosing`` is what makes closures work. Without it a
        nested function referring to a variable from the function around it
        looks undefined -- which produced eleven false positives on the GUI
        before this was added, and a checker that cries wolf gets ignored.
        """
        bound = _bound_in(scope) | enclosing
        for node in ast.iter_child_nodes(scope):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(sub, bound)
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in bound
                and node.id not in available
            ):
                missing.add(node.id)

    # Seed only from *outermost* functions. Using ast.walk here would also
    # re-enter every nested function with an empty enclosing scope, undoing
    # the closure handling above and re-reporting the same false positives.
    def seed(container: ast.AST) -> None:
        """Visit the scopes directly inside a module or class body.

        Lambdas count: a module-level ``f = lambda x: x + typo`` is a scope
        too, and was silently unchecked until this included them.
        """
        for node in ast.iter_child_nodes(container):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node, set())
            elif isinstance(node, ast.ClassDef):
                seed(node)
            else:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Lambda):
                        visit(sub, set())

    seed(tree)
    return sorted(missing)


def main(argv: list[str]) -> int:
    """Check each file named on the command line."""
    if len(argv) < 2:
        print(__doc__)
        return 2
    failed = False
    for name in argv[1:]:
        path = Path(name)
        found = undefined_names(path)
        if found:
            failed = True
            print(f"{path}: {', '.join(found)}")
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
