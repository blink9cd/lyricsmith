"""The album-level steps of a run: discovery, the album lock, the deferred interrupt.

`discover_tracks` finds every MP3 under an album, and the links it skipped, through
`walk_album`, which a caller can also ask to raise on a folder it cannot list; `album_lock`
lets one run, or one undo, change an album at a time; and `deferred_interrupt` holds a
Ctrl-C while a file is saved in place, then raises it. The orchestrator in `lyrics_run`
builds on these. Nothing here prints.

The lock is a kernel lock on a file, taken through an injected *acquire* (the entry
point binds the shared ``run_lock`` library's), so the kernel releases it when its
holder dies, however it dies, and no process id is ever guessed at. The lock lives in
the ``.lyricsmith`` run folder. A link at the run folder or at the lock is refused
before anything is created (`refuse_linked_run_folder`, re-exported from
`lyrics_run_folders` with the lock's name and its error), so nothing can land outside
the album.
"""

from __future__ import annotations

import os
import signal
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from lyrics_errors import AlbumUnusableError as AlbumUnusableError
from lyrics_model import LyricsError
from lyrics_records import is_mp3_name
from lyrics_run_folders import LOCK_NAME as LOCK_NAME
from lyrics_run_folders import RUN_FOLDER_NAME, is_link_or_junction
from lyrics_run_folders import RunFolderLinkError as RunFolderLinkError
from lyrics_run_folders import refuse_linked_run_folder as refuse_linked_run_folder


class HeldLock(Protocol):
    """A lock this process holds until it calls `release`."""

    def release(self) -> None:
        """Drop the lock."""


#: Take the lock on a path without waiting: the held lock, or None when another has it.
AcquireLock = Callable[[Path], HeldLock | None]


class LockHeldError(LyricsError):
    """Another run, or an undo, holds the album lock."""


# --- Discovery -----------------------------------------------------------------------


def resolve_album(album: Path) -> Path:
    """*album* resolved once, so a symlinked album works; raise unless it is a folder."""
    resolved = album.resolve()
    if not resolved.is_dir():
        raise AlbumUnusableError(f"{album} is not a directory")
    return resolved


def _is_hidden(name: str) -> bool:
    """True for a name a listing hides, the run folder and macOS ``._`` files among them."""
    return name.startswith(".")


@dataclass(frozen=True)
class Discovery:
    """An album's tracks, and the links under it that were skipped, each sorted."""

    tracks: tuple[Path, ...]
    links: tuple[Path, ...]


def _visible(folder: Path, names: Sequence[str]) -> Iterator[Path]:
    """The paths of *names* in *folder* that a listing shows."""
    return (folder / name for name in names if not _is_hidden(name))


def _is_mp3(path: Path) -> bool:
    """True for a file whose name ends in the MP3 suffix, in any case."""
    return is_mp3_name(path.name)


def _album_order(album: Path, paths: Sequence[Path]) -> tuple[Path, ...]:
    """*paths* sorted by their album-relative POSIX path, for a stable report order."""
    return tuple(sorted(paths, key=lambda path: path.relative_to(album).as_posix()))


def walk_album(
    album: Path, *, onerror: Callable[[OSError], None] | None = None
) -> Discovery:
    """Every MP3 under *album*, recursively, and every link skipped; none is no error.

    Hidden folders are pruned. A linked MP3 or folder is skipped and reported, never
    followed, so neither the run folder nor another album reached through a link
    joins the run. A junction counts as a linked folder: `os.walk` would enter one,
    since it reads as no symbolic link, so it is taken out of the walk here. A folder
    that cannot be listed is passed over unless *onerror* (`os.walk`'s own) raises.
    An entry that cannot be looked at raises its `OSError`, which ends discovery with
    exit 2.
    """
    tracks: list[Path] = []
    links: list[Path] = []
    for folder, dirs, files in os.walk(album, onerror=onerror, followlinks=False):
        here = Path(folder)
        for path in _visible(here, [*dirs, *files]):
            if is_link_or_junction(path) and (path.name in dirs or _is_mp3(path)):
                links.append(path)
            elif path.name in files and _is_mp3(path) and path.is_file():
                tracks.append(path)
        # Pruned after the listing, so a linked folder is still reported above.
        dirs[:] = [
            name
            for name in dirs
            if not _is_hidden(name) and not is_link_or_junction(here / name)
        ]
    return Discovery(_album_order(album, tracks), _album_order(album, links))


def discover_tracks(album: Path) -> Discovery:
    """`walk_album` of *album*; raise `AlbumUnusableError` when it holds no MP3."""
    found = walk_album(album)
    if not found.tracks:
        links = found.links
        skipped = f" ({len(links)} link{'' if len(links) == 1 else 's'} skipped)"
        raise AlbumUnusableError(f"no MP3 under {album}{skipped if links else ''}")
    return found


def album_relative(album: Path, path: Path) -> str:
    """*path*'s album-relative POSIX path, as a manifest names it; refuse one outside."""
    resolved = path.resolve()
    if not resolved.is_relative_to(album):
        raise AlbumUnusableError(f"{path} is not under {album}")
    return resolved.relative_to(album).as_posix()


def track_selection(
    album: Path, found: Discovery, track: Path | None
) -> tuple[Path, ...]:
    """Every track found, or the one --track names; AlbumUnusableError when it is none of them."""
    if track is None:
        return found.tracks
    relative = album_relative(album, track)
    chosen = album / relative
    if chosen not in found.tracks:
        raise AlbumUnusableError(f"--track {relative} is not a track of {album}")
    return (chosen,)


# --- The album lock ------------------------------------------------------------------


@contextmanager
def album_lock(album: Path, *, acquire: AcquireLock) -> Iterator[Path]:
    """Hold *album*'s lock for the block: one run or undo per album at a time.

    Raise `LockHeldError` when another holder has it, `RunFolderLinkError` before
    anything is created when a run folder or the lock is a link, and the `OSError`
    *acquire* raises when the lock cannot be taken at all (a read-only album). A lock
    file left by a holder that died holds no lock, so it is simply taken.
    """
    refuse_linked_run_folder(album)
    path = album / RUN_FOLDER_NAME / LOCK_NAME
    lock = acquire(path)
    if lock is None:
        raise LockHeldError(f"{path} is held by another run of this album")
    try:
        yield path
    finally:
        lock.release()


# --- The deferred interrupt ----------------------------------------------------------


@dataclass
class _Received:
    """Whether a SIGINT arrived while it was deferred."""

    signals: list[int] = field(default_factory=list)

    def __call__(self, signum: int, _frame: object) -> None:
        """Record the signal instead of raising."""
        self.signals.append(signum)


@contextmanager
def deferred_interrupt() -> Iterator[None]:
    """Hold a Ctrl-C until the block ends, then raise `KeyboardInterrupt`.

    For a save in place, which an interrupt part-way would leave half-written. Off the
    main thread, when the current handler was not installed from Python (and so could
    not be put back), or when Ctrl-C is ignored (so there is nothing to raise), nothing
    is deferred.
    """
    previous = signal.getsignal(signal.SIGINT)
    off_main = threading.current_thread() is not threading.main_thread()
    if off_main or previous is None or previous is signal.SIG_IGN:
        yield
        return
    received = _Received()
    signal.signal(signal.SIGINT, received)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if received.signals:
            raise KeyboardInterrupt
