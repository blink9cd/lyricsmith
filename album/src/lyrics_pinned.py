"""One name held to one file while a step writes it: `pinned`, and the open's check.

A fix or a paste matches a track to its transcript record by file identity, checks the
track's name still names that file, then writes it by name, opening it several times
(the writable check, the strip, the embed, the read-back). Inside `pinned`, every
`lyrics_files.open_regular` of the pinned name asks `refuse_unless_pinned`, which tells
the file it opened by the descriptor's own `fstat`, so another file swapped in under
the name since the check is refused at the open that would have written it
(`PinnedFileError`), and a check by name and the write after it cannot reach two
different files. Any other name opens as it always does. Nothing here prints.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

#: A file's identity: its device and its file number, the same for every spelling of
#: its path.
FileIdentity = tuple[int, int]


class PinnedFileError(OSError):
    """The file opened under a pinned name is not the file the name was pinned to."""


@dataclass(frozen=True)
class _Pin:
    """One name held to one file, and why another file under it is refused."""

    path: Path
    identity: FileIdentity
    why: str


#: The pin the running step holds, if any. A context variable rather than a module
#: global, so a thread the step starts sees none and a pin ends with its block alone.
_PIN: ContextVar[_Pin | None] = ContextVar("lyrics_pin", default=None)


@contextlib.contextmanager
def pinned(path: Path, identity: FileIdentity, why: str) -> Iterator[None]:
    """Hold every `open_regular` of *path* in the block to the file *identity* names.

    An open that finds another file under *path* closes it and raises
    `PinnedFileError` saying *why*. The pin is matched by *path* as given, the
    spelling every stage of one step is passed. It is canonical, the album resolved
    first, so resolving it again keeps it; `test_lyrics_pinned_steps` fails if a stage
    of a paste or a fix opens the pinned file under any other spelling.
    """
    token = _PIN.set(_Pin(path, identity, why))
    try:
        yield
    finally:
        _PIN.reset(token)


def refuse_unless_pinned(descriptor: int, path: Path) -> None:
    """Raise `PinnedFileError` when *path* is pinned to another file than *descriptor*'s.

    Raises what `os.fstat` raises, too. Any error raised here closes *descriptor*
    first, so a refused open never leaks it.
    """
    pin = _PIN.get()
    if pin is None or pin.path != path:
        return
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != pin.identity:
            raise PinnedFileError(pin.why)
    except BaseException:
        os.close(descriptor)
        raise
