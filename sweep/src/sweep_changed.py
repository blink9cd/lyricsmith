"""A sweep's ``--changed-list``: the union of every album's list, rewritten as it goes.

Each album child is handed a list file of its own, in a private temporary folder
(`child_list`), and writes there every MP3 it saved. Once the child has ended,
`ChangedUnion.absorb` reads that list back with the album tool's own strict reader
(`lyrics_changed.parse_changed_list`, the rules of the list file contract), merges
it into the union in visit order, each path once, and rewrites the sweep's list
atomically with Ctrl-C held. So a Ctrl-C, a crash or a kill at any point leaves the
union of every album absorbed so far; only the album in hand is missing. A child's
list that cannot be removed once absorbed is said through *notice*, never raised.

A child that left no readable list (killed at the per-album bound, crashed, or
stopped before it began) changed an unknown set of tracks, so every MP3 of its album,
found by the album tool's own discovery, is listed instead, and *notice* says so: a
refresh of an unchanged track only rereads its tags, and missing a changed one is the
failure to avoid. An album tool that could not start at all is absorbed the same way
the moment its launch fails (``absorb(started=False)``), its notice saying so. A
child whose run errored but wrote a list is taken at its word.

`changed_union` holds a union for the length of a sweep and closes it on the way out,
however the sweep ends: it absorbs any list still pending, writes the list a last
time, and removes the private folder. A write that fails is kept as *error*, and the
next write tries again; the caller reports the last one's failure.

A list file that already exists is the same pass's (a resumed sweep is the leg after a
stopped one), so the union starts from it, its paths first, unless the sweep is a
``--restart``, which starts a pass and so an empty list. An
existing list that cannot be read, or that the contract refuses, raises
`ExistingListError` and is left as it was: replacing it would drop tracks still owed a
refresh. It is read through the album tool's `lyrics_files.read_regular`, up to
`MAX_CHANGED_LIST_BYTES`: a FIFO pipe or a device is refused at once rather than
waited on, and a larger file is refused too. A link is followed, since the operator
named it. The list is written with the default mode: it is the operator's file, not
the sweep's state. That, and a private folder that cannot be made (`ScratchFolderError`), are
raised before any album is visited, as a `ChangedListError`, so the caller says so as
itself. Nothing here prints.

A fix pass's ``--hold-for-genius`` list is kept the same way, by a union of its own
(`HELD`, through `sweep_lists`): each child is handed a private list, merged in visit
order, and a resumed sweep adds to the list already there. Only a child that left no
readable list differs: none of its album's tracks is added, and *notice* says so. A
held track keeps its text and its transcript record, so the album's retry, or a later
fix, holds it again; listing every MP3 would send tracks no fix held to the later pass.
A fix with nothing to fix exits 0 having made no run and written no list, so it held
nothing: the caller tells the union so (``absorb(made_nothing=...)``), and nothing
is said.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import lyrics_album
import lyrics_changed
import lyrics_files
from lyrics_model import LyricsError

#: The largest existing list read: one absolute path a line, so a whole large
#: library's every track lists in tens of MiB at most; a bound on what a file swapped
#: in can make the sweep hold in memory.
MAX_CHANGED_LIST_BYTES = 64 * 1024 * 1024
#: The private folder's name prefix, so a folder left by a killed sweep says whose.
_FOLDER_PREFIX = "lyricsmith-sweep-changed-"
#: Each child's list's suffix in that folder.
_CHILD_SUFFIX = ".list"
#: Why an album's every MP3 is listed: its child ran but left no readable list.
_NO_LIST = "the album tool left no readable list of what it changed"
#: Why an album's every MP3 is listed: its album tool could not start at all.
_NOT_STARTED = "the album tool could not start"


def made_something() -> bool:
    """`ChangedUnion.absorb`'s default: the child is not known to have made nothing."""
    return False


@dataclass(frozen=True)
class ListKind:
    """Which list a union keeps: its option, and what an album with no list adds.

    *every_mp3* lists an album's every MP3 when its child left no readable list;
    False adds none. *advice* closes the notice of an existing list the contract
    refuses, saying what to do with it.
    """

    flag: str
    every_mp3: bool
    advice: str


#: The list of the MP3s the albums saved, and the list of the tracks a fix held.
CHANGED = ListKind(
    lyrics_changed.CHANGED_FLAG,
    every_mp3=True,
    advice="refresh what it names, then remove it or name another file",
)
HELD = ListKind(
    lyrics_changed.HOLD_FLAG,
    every_mp3=False,
    advice="hold what it names apart, then remove it or name another file",
)


class ChangedListError(Exception):
    """The list could not be set up, so no album is visited."""


class ScratchFolderError(ChangedListError):
    """The private folder the children's lists go in could not be made."""


class ExistingListError(ChangedListError):
    """The list file a resumed sweep would add to could not be read; it is left as it was."""


@dataclass(frozen=True)
class _Pending:
    """A child's list not yet absorbed: its file, and the album's name in a notice."""

    path: Path
    name: str


@dataclass
class ChangedUnion:
    """The union of the albums' lists so far, and where it is written.

    *folder* holds each child's own list; *notice* takes each stderr line; *kind* is
    which list it keeps.
    """

    target: Path
    folder: Path
    notice: Callable[[str], None]
    kind: ListKind = CHANGED
    #: Every path listed so far, in visit order, each once.
    paths: dict[str, None] = field(default_factory=dict)
    #: Why the last write failed, or None when it landed.
    error: str | None = None
    _pending: dict[Path, _Pending] = field(default_factory=dict)
    #: How many child lists were handed out, which numbers the next one.
    _handed: int = 0

    def child_list(self, album: Path, name: str) -> Path:
        """A fresh list file for *album*'s child, named *name* in notices; now pending."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self._handed += 1
        path = self.folder / f"{self._handed}{_CHILD_SUFFIX}"
        self._pending[album] = _Pending(path, name)
        return path

    def absorb(
        self,
        album: Path,
        *,
        started: bool = True,
        made_nothing: Callable[[], bool] = made_something,
    ) -> None:
        """Merge *album*'s child's list, or its every MP3, then rewrite the union.

        *started* is False for a child whose album tool could not start, which its
        notice says. *made_nothing* is asked only when a held list is unread: True
        when the child exited 0 having made no run, so nothing is said. Held against
        Ctrl-C until the write lands. The album stays pending until then, so an absorb
        a Ctrl-C cut short is done whole when tried again; an album no longer pending
        is left alone, so a repeat after it landed changes nothing.
        """
        pending = self._pending.get(album)
        if pending is None:
            return
        with lyrics_album.deferred_interrupt():
            listed = self._read_child(
                album, pending, started=started, made_nothing=made_nothing
            )
            self._add(listed)
            self.write()
            del self._pending[album]
            self._remove_child(pending)

    def _remove_child(self, pending: _Pending) -> None:
        """Remove an absorbed child's list; one that will not go is said, not raised.

        The album is absorbed by now, so a leftover list costs nothing: the close
        removes the whole folder. Raising would record an album that ran as could not
        run.
        """
        try:
            pending.path.unlink(missing_ok=True)
        except OSError as error:
            self.notice(
                f"{pending.name}: could not remove the album tool's list "
                f"{pending.path}: {error}"
            )

    def write(self) -> None:
        """Replace the list file with the union so far; keep why, when it fails."""
        data, _left = lyrics_changed.format_changed_list(self.paths)
        try:
            lyrics_files.replace_atomically(self.target, data)
        except OSError as error:
            self.error = f"could not write {self.kind.flag} {self.target}: {error}"
        else:
            self.error = None

    def close(self) -> None:
        """Absorb every list still pending, write the union, remove the folder."""
        try:
            for album in list(self._pending):
                self.absorb(album)
            with lyrics_album.deferred_interrupt():
                self.write()
        finally:
            # The sweep's own scratch folder, holding nothing but the child lists.
            shutil.rmtree(self.folder, ignore_errors=True)

    def _read_child(
        self,
        album: Path,
        pending: _Pending,
        *,
        started: bool,
        made_nothing: Callable[[], bool],
    ) -> Iterable[str]:
        """The paths *album*'s child listed, or the album's every MP3 when unknown.

        The MP3s are found as the album tool finds them: under the album folder as it
        resolves it, each track's own path never resolved, as the child would list it.
        A child that never *started* wrote no list, so its every MP3 is listed too:
        its notice says why. A held list unread adds nothing, said unless the child
        *made_nothing*.
        """
        try:
            listed = lyrics_changed.parse_changed_list(pending.path.read_bytes())
        except OSError:
            listed = None
        if listed is not None:
            return listed
        why = _NO_LIST if started else _NOT_STARTED
        if not self.kind.every_mp3:
            # A fix with nothing to fix writes no list and holds nothing, so saying
            # none of its tracks is added would only alarm.
            if not (started and made_nothing()):
                self.notice(self._none_added(pending.name, started=started))
            return ()
        try:
            found = lyrics_album.discover_tracks(lyrics_album.resolve_album(album))
        except (LyricsError, OSError) as error:
            self.notice(
                f"{pending.name}: {why}, and the album's MP3s could not be listed: "
                f"{error}"
            )
            return ()
        self.notice(
            f"{pending.name}: every MP3 of the album is listed in {self.kind.flag}, "
            f"as {why}"
        )
        return (str(track) for track in found.tracks)

    def _none_added(self, name: str, *, started: bool) -> str:
        """The notice for album *name*, its child's list unread, adding no track."""
        flag = self.kind.flag
        why = (
            f"the album tool left no readable {flag} list" if started else _NOT_STARTED
        )
        return f"{name}: {why}, so none of the album's tracks is added to {flag}"

    def _add(self, paths: Iterable[str]) -> None:
        """Add each path the list can hold, normalized; say each it cannot.

        Said as it is met, so no later rewrite of the union says it again.
        """
        for path in paths:
            normal = os.path.normpath(path)
            reason = lyrics_changed.refusal(normal)
            if reason is None:
                self.paths.setdefault(normal, None)
            else:
                left = lyrics_changed.LeftOut(path, reason)
                self.notice(lyrics_changed.left_out_notice(left, flag=self.kind.flag))


def existing_paths(target: Path, kind: ListKind = CHANGED) -> tuple[str, ...]:
    """The paths the list file *target* already holds; none when there is no file.

    A folder on its path that is a file is no file either: the write reports that.
    Raises `ExistingListError` for a file that cannot be read, that is not a regular
    file (a FIFO pipe is refused, never waited on), that is past
    `MAX_CHANGED_LIST_BYTES`, or that the list file contract refuses (a last line
    with no linefeed, a relative path), which is never replaced: only the user can
    tell what it was meant to hold. A link is followed, as the operator named it.
    Every message names the list by *kind*'s option.
    """
    flag = kind.flag
    try:
        data = lyrics_files.read_regular(target, MAX_CHANGED_LIST_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError):
        return ()
    except OSError as error:
        raise ExistingListError(f"could not read {flag} {target}: {error}") from error
    if len(data) > MAX_CHANGED_LIST_BYTES:
        raise ExistingListError(
            f"{flag} {target} is more than {MAX_CHANGED_LIST_BYTES} bytes, "
            "so it is left as it was"
        )
    paths = lyrics_changed.parse_changed_list(data)
    if paths is None:
        raise ExistingListError(
            f"{flag} {target} is not a readable list, so it is left as it "
            f"was: {kind.advice}"
        )
    return paths


@contextmanager
def changed_union(
    target: Path | None,
    notice: Callable[[str], None],
    *,
    restart: bool = False,
    kind: ListKind = CHANGED,
) -> Iterator[ChangedUnion | None]:
    """A union writing to *target* for the block, closed however it ends; None for None.

    The union starts from the list *target* already holds (`existing_paths`), or empty
    for a *restart*, and is written at once, so a sweep stopped before its first album
    still leaves a list. A private folder that cannot be made raises
    `ScratchFolderError`, naming the error. *kind* is which list it keeps.
    """
    if target is None:
        yield None
        return
    earlier = () if restart else existing_paths(target, kind)
    try:
        folder = Path(tempfile.mkdtemp(prefix=_FOLDER_PREFIX))
    except OSError as error:
        raise ScratchFolderError(
            f"could not make the {kind.flag} scratch folder: {error}"
        ) from error
    union = ChangedUnion(target, folder, notice, kind)
    union.paths.update(dict.fromkeys(earlier))
    try:
        with lyrics_album.deferred_interrupt():
            union.write()
        yield union
    finally:
        union.close()


@contextmanager
def sweep_lists(
    changed: Path | None,
    held: Path | None,
    notice: Callable[[str], None],
    *,
    restart: bool = False,
) -> Iterator[tuple[ChangedUnion | None, ChangedUnion | None]]:
    """The ``--changed-list`` union and the ``--hold-for-genius`` one, for the block.

    Each is `changed_union`'s, None for a list not asked for; the held one is opened
    second, so an existing changed list that cannot be read is said first.
    """
    with (
        changed_union(changed, notice, restart=restart) as changed_kept,
        changed_union(held, notice, restart=restart, kind=HELD) as held_kept,
    ):
        yield changed_kept, held_kept


def list_errors(*unions: ChangedUnion | None) -> list[str]:
    """Why each of *unions* asked for failed its last write, in their order."""
    return [
        union.error for union in unions if union is not None and union.error is not None
    ]
