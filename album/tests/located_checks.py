"""Checks that each private module of a tool imports alone, shared by every tool's suite.

A private module that imports a shared library, or a module of another tool, must first
import its tool's located module, which puts them on ``sys.path``; without it, the module
imports only after something else has run that bootstrap, so it works under one import
order and fails under another. Two checks hold this for one ``src/`` folder: a read of
each module's top-level statements (`late_shared_imports`), and a fresh interpreter per
module with only that ``src/`` folder on the path (`alone_failures`).

Not discovered as a suite (no ``test_`` prefix). The album, sweep and sync suites import
it by bare name with this folder on ``sys.path``; transcribe's loads it by its file. The
located module must already have put the shared libraries on the path.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

import bounded_run
import lyricsmith_project

#: How long one fresh interpreter may take to import one module.
ALONE_TIMEOUT_S: Final = 120
#: How many fresh interpreters run at once.
_WORKERS: Final = 4
#: The locator's own call, which a module may make in place of importing its located one.
_LOCATOR_CALL: Final = "add_shared_libraries"
#: The fresh interpreter's program: the src/ folder alone on the path, then the import.
_ALONE_CHILD: Final = (
    "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
    "importlib.import_module(sys.argv[2])"
)


def shared_library_names() -> frozenset[str]:
    """The module name of every shared library the project's manifest names."""
    return frozenset(
        folder.name for folder in lyricsmith_project.shared_library_folders()
    )


def _imported(statement: ast.stmt) -> Iterator[str]:
    """Each top-level name *statement* imports; nothing for any other statement."""
    if isinstance(statement, ast.Import):
        yield from (alias.name.split(".")[0] for alias in statement.names)
    elif isinstance(statement, ast.ImportFrom) and statement.module:
        yield statement.module.split(".")[0]


def _runs_the_locator(statement: ast.stmt) -> bool:
    """Whether *statement* is a bare call of the locator."""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and statement.value.func.attr == _LOCATOR_CALL
    )


def _first_late_import(file: Path, located: str, shared: frozenset[str]) -> str | None:
    """The first shared library *file* imports before *located*, or None."""
    tree = ast.parse(file.read_text(encoding="utf-8"), str(file))
    for statement in tree.body:
        names = set(_imported(statement))
        if located in names or _runs_the_locator(statement):
            return None
        late = sorted(names & shared)
        if late:
            return late[0]
    return None


def late_shared_imports(src: Path, located: str) -> list[str]:
    """``<file>: <library>`` for each module in *src* importing one before *located*."""
    shared = shared_library_names()
    return [
        f"{file.name}: {library}"
        for file in sorted(src.glob("*.py"))
        if (library := _first_late_import(file, located, shared)) is not None
    ]


def _import_alone(src: Path, name: str, cwd: Path) -> str | None:
    """Why module *name* fails to import in a fresh interpreter, or None."""
    done = bounded_run.run_bounded(
        [sys.executable, "-I", "-B", "-c", _ALONE_CHILD, str(src), name],
        timeout=ALONE_TIMEOUT_S,
        cwd=cwd,
    )
    if done.returncode == 0:
        return None
    lines = done.stderr.strip().splitlines()
    return f"{name}: {lines[-1] if lines else f'exit {done.returncode}'}"


def alone_failures(src: Path, cwd: Path) -> list[str]:
    """``<module>: <its last error line>`` for each module in *src* that fails alone.

    Each runs in its own interpreter, with *cwd* as its working folder.
    """
    names = [file.stem for file in sorted(src.glob("*.py"))]
    with ThreadPoolExecutor(_WORKERS) as pool:
        found = pool.map(lambda name: _import_alone(src, name, cwd), names)
        return [failure for failure in found if failure is not None]
