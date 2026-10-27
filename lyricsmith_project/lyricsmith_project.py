"""The project's own manifest, read once for every tool: its version and its libraries.

Every entry file reaches this module at one fixed place, its own folder directly under
the project root, by putting that folder FIRST on ``sys.path`` from the entry file's
own location (a single ``parents`` index, never a walk), so no same-named module can
stand in for this one; it then calls `add_shared_libraries`.
That appends each folder the project's ``pyproject.toml`` names under
``[tool.lyricsmith] shared_libraries`` to ``sys.path``, each path relative to that
``pyproject.toml``'s own folder, so the one list describes whichever layout the tree
has: the libraries in a shared folder beside the project, or beside the tools
themselves. Nothing searches upward: only the folders the list names are added, and a
named folder that is missing fails at once, naming the folder and the key. They are
appended, and then each library's module must load from its named folder, checked for
every library before any is imported: a same-named module earlier on ``sys.path``
(``PYTHONPATH``, site-packages, the script's own folder) fails the run, naming both,
rather than being run in its place. `confirm_from` makes the same check for a sibling
tool's module. An entry file run as a script exits 2 on any of these failures, printing
the error; anywhere else it is raised.

It imports nothing outside the standard library, because it runs before any shared
library can be imported.
"""

from __future__ import annotations

import functools
import importlib.machinery
import importlib.util
import sys
import tomllib
from pathlib import Path, PureWindowsPath
from typing import Final, NoReturn

#: The project root: the parent of this module's own folder.
PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
#: The manifest every reader here defaults to.
PROJECT_CONFIG: Final = PROJECT_ROOT / "pyproject.toml"
#: The version reported when the manifest cannot be read.
UNKNOWN_VERSION: Final = "unknown"
#: The key naming the shared library folders, as every error spells it.
CONFIG_KEY: Final = "[tool.lyricsmith] shared_libraries"
#: The ``[tool]`` table this project owns, and the key in it.
_TOOL_TABLE: Final = "lyricsmith"
_LIBRARIES_KEY: Final = "shared_libraries"
#: What a read of the manifest may raise. tomllib decodes the bytes itself, so a file
#: that is not UTF-8 raises UnicodeDecodeError rather than TOMLDecodeError.
_READ_ERRORS: Final = (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError)
#: The exit code of an entry file whose libraries cannot be located: the tools' own
#: code for a run that cannot start.
EXIT_CANNOT_LOCATE: Final = 2
#: What a shadowed module is, as its error names it, and where its folder comes from.
_LIBRARY: Final = "shared library"
_SIBLING: Final = "sibling tool module"
_SIBLING_HOME: Final = "its tool's fixed folder under the project root"


class SharedLibraryError(ImportError):
    """The shared libraries the manifest names cannot be put on ``sys.path``.

    An ImportError, because it is raised while an entry file is still importing, in
    place of the import that would otherwise fail on the missing library.
    """


def _load(config: Path) -> dict[str, object]:
    """*config*'s parsed TOML; raises any of `_READ_ERRORS`."""
    with config.open("rb") as stream:
        return tomllib.load(stream)


@functools.cache
def project_version(config: Path = PROJECT_CONFIG) -> str:
    """``[project].version`` from *config*, else `UNKNOWN_VERSION`.

    Cached, so it is read once per process for each *config* path: the User-Agent of
    every provider call carries it, and the file does not change during a run.
    """
    try:
        project = _load(config).get("project")
    except _READ_ERRORS:
        return UNKNOWN_VERSION
    version = project.get("version") if isinstance(project, dict) else None
    return version if isinstance(version, str) else UNKNOWN_VERSION


def _named_entries(config: Path) -> list[str]:
    """The checked entries of *config*'s shared library list, in order."""
    try:
        document = _load(config)
    except _READ_ERRORS as error:
        raise SharedLibraryError(
            f"cannot read {CONFIG_KEY} from {config}: {error}"
        ) from error
    tool = document.get("tool")
    table = tool.get(_TOOL_TABLE) if isinstance(tool, dict) else None
    entries = table.get(_LIBRARIES_KEY) if isinstance(table, dict) else None
    if not isinstance(entries, list):
        raise SharedLibraryError(f"{config} names no {CONFIG_KEY} list")
    return [_checked_entry(entry, config) for entry in entries]


def _checked_entry(entry: object, config: Path) -> str:
    """*entry* when it is a relative folder path, else a `SharedLibraryError`."""
    if not isinstance(entry, str) or not entry.strip():
        raise SharedLibraryError(
            f"{CONFIG_KEY} in {config}: {entry!r} is not a folder path"
        )
    # Windows' reading is the wider one: it gives an anchor to a POSIX absolute path,
    # a drive, a drive-relative path and a UNC share alike, on every platform.
    if PureWindowsPath(entry).anchor:
        raise SharedLibraryError(
            f"{CONFIG_KEY} in {config}: {entry!r} is absolute; name each folder "
            "relative to the pyproject.toml's own folder"
        )
    return entry


def _library_folder(entry: str, config: Path) -> Path:
    """The folder *entry* names, holding its same-named module, or a `SharedLibraryError`."""
    named = config.parent / entry
    module = f"{named.name}.py"
    if not (named / module).is_file():
        raise SharedLibraryError(
            f"shared library {entry!r} is missing: no {module} in {named.resolve()} "
            f"({CONFIG_KEY} in {config})"
        )
    return named.resolve()


def shared_library_folders(config: Path = PROJECT_CONFIG) -> tuple[Path, ...]:
    """Each folder *config* names as a shared library, resolved, in order.

    Every entry is checked before any is returned, so a caller acts on all or none.
    """
    return tuple(_library_folder(entry, config) for entry in _named_entries(config))


def _origin(name: str, search_path: list[str] | None) -> str | None:
    """The file module *name* is, or would be, loaded from; None when it has none.

    A module already imported answers with the origin its import recorded
    (``__spec__``), or its ``__file__`` when it recorded none; otherwise the import
    system is asked without running anything, over *search_path* when given, else over
    ``sys.path``.
    """
    loaded = sys.modules.get(name)
    if loaded is not None:
        record = getattr(loaded, "__spec__", None)
        origin = getattr(loaded, "__file__", None) if record is None else record.origin
        return origin if isinstance(origin, str) else None
    if search_path is None:
        found = importlib.util.find_spec(name)
    else:
        found = importlib.machinery.PathFinder.find_spec(name, search_path)
    return None if found is None or not found.has_location else found.origin


def _check_origin(
    kind: str, name: str, folder: Path, search_path: list[str] | None, context: str
) -> None:
    """Raise a `SharedLibraryError` unless module *name* loads from *folder*.

    *kind* names what the module is, and *context* where its folder comes from.
    """
    origin = _origin(name, search_path)
    if origin is None:
        raise SharedLibraryError(
            f"{kind} {name!r} would load with no file, as a built-in or namespace "
            f"module, not from {folder} ({context}): a same-named one shadows it"
        )
    # The folder the module's file sits in, resolved: a file that is itself a link to
    # one elsewhere still loads from the folder that holds the link.
    if Path(origin).parent.resolve() != folder:
        raise SharedLibraryError(
            f"{kind} {name!r} would load from {origin}, not from {folder} "
            f"({context}): a same-named module earlier on sys.path shadows it"
        )


def _escaped(text: str) -> str:
    """*text* with each character that is not printable spelled as its escape."""
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def exit_with(error: SharedLibraryError) -> NoReturn:
    """Print *error* after the script's name, escaped, to stderr, and exit 2.

    What ``exit_on_failure`` does; the dispatcher calls it too, for an error raised
    while it loads a tool.
    """
    script = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else _TOOL_TABLE
    message = f"{script}: {type(error).__name__}: {error}"
    print(_escaped(message), file=sys.stderr)
    raise SystemExit(EXIT_CANNOT_LOCATE) from error


def add_shared_libraries(
    config: Path = PROJECT_CONFIG,
    search_path: list[str] | None = None,
    *,
    exit_on_failure: bool = False,
) -> tuple[Path, ...]:
    """Append each folder *config* names to *search_path*, ``sys.path`` when None.

    A folder already on it is not added again, so every entry file may call this.
    Then each library's module must load from its own folder, checked for every
    library before any is imported, so a same-named module earlier on the path (from
    ``PYTHONPATH``, say) is refused rather than run. Returns the folders, in the
    manifest's order. A failure raises `SharedLibraryError`, or, with
    *exit_on_failure* (an entry file run as a script), prints it and exits 2.
    """
    try:
        target = sys.path if search_path is None else search_path
        folders = shared_library_folders(config)
        target.extend(
            folder
            for folder in dict.fromkeys(map(str, folders))
            if folder not in target
        )
        context = f"{CONFIG_KEY} in {config}"
        for folder in folders:
            _check_origin(_LIBRARY, folder.name, folder, search_path, context)
    except SharedLibraryError as error:
        if exit_on_failure:
            exit_with(error)
        raise
    return folders


def confirm_from(
    name: str,
    folder: Path,
    *,
    search_path: list[str] | None = None,
    exit_on_failure: bool = False,
) -> None:
    """Fail unless module *name*, a sibling tool's, loads from *folder*.

    Called before the module is imported, so a same-named module earlier on the path
    is refused rather than run. A failure raises `SharedLibraryError`, or, with
    *exit_on_failure*, prints it and exits 2.
    """
    try:
        _check_origin(_SIBLING, name, folder.resolve(), search_path, _SIBLING_HOME)
    except SharedLibraryError as error:
        if exit_on_failure:
            exit_with(error)
        raise
