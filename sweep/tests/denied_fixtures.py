"""Refused listings, reads and writes, played by `chmod` where it binds and patched where not.

A POSIX host refuses a mode-000 folder's listing, a mode-000 file's read and a file made
in a 0o500 folder. Root is refused none of them, and neither is anyone on Windows, where
``chmod`` sets only the read-only attribute. So each real-permission test runs only where
its probe here says ``chmod`` binds, and a portable twin raises the same refusal through
a patched call (`listing_refused`, `reading_refused`), so the product's branch is reached
on every host. Each probe runs once, at import, in a temporary folder of its own.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

#: The reason a mode-000 folder test skips where the probe found a listing still works.
NO_FOLDER_LOCK = "chmod(0) does not stop a listing here (root, or Windows)"
#: The reason a mode-000 file test skips where the probe found a read still works.
NO_FILE_LOCK = "chmod(0) does not stop a read here (root, or Windows)"
#: The reason a read-only folder test skips where the probe could still make a file.
NO_READ_ONLY = "a file can still be made in a read-only folder here (root, or Windows)"
#: The mode a probe gives back to what it locked, so its temporary folder can go.
_OPEN_MODE = 0o700
#: A read-only folder's mode: listed and entered, never written.
_READ_ONLY_FOLDER = 0o500


def _probe(lock: Callable[[Path], Path], attempt: Callable[[Path], object]) -> bool:
    """True when *attempt* on what *lock* made and locked is refused with EACCES."""
    with TemporaryDirectory() as scratch:
        locked = lock(Path(scratch))
        try:
            attempt(locked)
        except PermissionError:
            return True
        finally:
            locked.chmod(_OPEN_MODE)
    return False


def _locked_folder(scratch: Path) -> Path:
    """A mode-000 folder in *scratch*."""
    folder = scratch / "probe"
    folder.mkdir()
    folder.chmod(0)
    return folder


def _locked_file(scratch: Path) -> Path:
    """A mode-000 file in *scratch*."""
    path = scratch / "probe.txt"
    path.write_bytes(b"probe\n")
    path.chmod(0)
    return path


def _read_only_folder(scratch: Path) -> Path:
    """A 0o500 folder in *scratch*."""
    folder = scratch / "probe"
    folder.mkdir()
    folder.chmod(_READ_ONLY_FOLDER)
    return folder


#: Whether ``chmod(0)`` stops this process listing a folder.
MODE_000_LOCKS_A_FOLDER = _probe(_locked_folder, lambda path: list(path.iterdir()))
#: Whether ``chmod(0)`` stops this process reading a file.
MODE_000_LOCKS_A_FILE = _probe(_locked_file, Path.read_bytes)
#: Whether a 0o500 folder stops this process making a file in it.
READ_ONLY_STOPS_A_FILE = _probe(_read_only_folder, lambda path: (path / "file").touch())


def denied(path: object) -> PermissionError:
    """The refusal a POSIX host gives for *path*, its text as the OS's own."""
    return PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))


def _refusing(
    real: Callable[..., object], refused: frozenset[str]
) -> Callable[..., object]:
    """*real*, refusing with `denied` when its first argument names one of *refused*."""

    def call(*args: object, **kwargs: object) -> object:
        if args and isinstance(args[0], (str, os.PathLike)):
            if os.fspath(args[0]) in refused:
                raise denied(args[0])
        return real(*args, **kwargs)

    return call


def _names(paths: tuple[Path, ...]) -> frozenset[str]:
    """Each of *paths* as the string an `os` call is handed."""
    return frozenset(os.fspath(path) for path in paths)


@contextmanager
def listing_refused(*folders: Path) -> Iterator[None]:
    """Each of *folders* refuses its listing, as a mode-000 folder does on POSIX.

    Both `os.scandir` and `os.listdir` refuse, so `Path.iterdir` (whichever it calls
    on this Python) and `os.walk` meet the refusal at the listing itself.
    """
    refused = _names(folders)
    with ExitStack() as stack:
        for name in ("scandir", "listdir"):
            real = getattr(os, name)
            stack.enter_context(mock.patch.object(os, name, _refusing(real, refused)))
        yield


@contextmanager
def reading_refused(*files: Path) -> Iterator[None]:
    """Each of *files* refuses its open, as a mode-000 file does on POSIX."""
    with mock.patch.object(os, "open", _refusing(os.open, _names(files))):
        yield
