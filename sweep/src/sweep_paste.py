"""``sweep --mode paste``: walk has-it.tsv, pasting each track's lyrics in from a browser.

Genius's pages are challenged for a script but open in a person's browser, so the walk
puts the person in the loop. It reads the Genius check's ``has-it.tsv`` (``--from``,
`sweep_genius_lists.read_has_it`) and, for each row in order:

1. **Passes over a row it cannot or need not paste**, with a one-line reason: a URL
   that is not https on a Genius host (`lyrics_genius.genius_url_allowed`), never
   opened; a track outside the library's ``Artist/Album`` folders; one whose artist or
   album folder is a link (`sweep_survey.is_linked`), or that sits under a linked
   folder below the album or is a link itself (`linked_below`), since the album tool
   would follow the one out of the library and refuse the others; and
   a track the album tool's paste would refuse (`lyrics_paste.paste_refusal`): no
   record in ``--heard-dir``, or a text no longer the one the record was judged
   against, which is how a walk resumes, since a pasted track holds the pasted text.
2. **Opens the page** in the person's browser (``open``, `sweep_paste_seams`) and
   prompts `PROMPT`. Enter reads the clipboard (``pbpaste``) into a private file
   (0o600, in a private folder of its own), runs the album tool's ``--mode paste`` on
   the track as a child (`sweep_child.run_child`: its stdin closed, its output on
   this process's stderr, bounded by `PASTE_TIMEOUT_S`, its environment without the
   Genius key, `sweep_paste_seams.keyless_env`), and deletes the file. ``s`` skips
   the row, ``q`` or the end of input ends the walk.
3. **Reads the child's exit.** Written: the next row. Not sung, or not lyrics
   (`lyrics_paste_mode.EXIT_NOT_SUNG`, the share said by the child), and that exit
   alone: the prompt again, and ``s`` then gives the row up. Refused
   (`lyrics_paste_mode.EXIT_REFUSED`): noted, the next row. Any other end, a track
   error or a crash among them, is a failure, noted, the next row: never a prompt to
   copy again, which would ask the person to retry what no copy can fix.

Each child is given the walk's language (``--lang``, the album tool's own
`lyrics_options.DEFAULT_LANG` when omitted), which the walker's own refusal check
judges each record by too.

Ctrl-C at a prompt or in the child ends the walk at once. Every child's
``--changed-list`` is merged into the walk's own as it ends (`sweep_changed`), so a
stopped walk keeps the list of what it wrote. The summary goes to stdout and every
prompt and notice to stderr.

Nothing here asks a host: the browser is the person's, the album tool's paste asks no
provider, and the clipboard is read only on Enter. Setup comes first, each failure exit
2 before any row: the terminal (`PasteSeams.ready`), the list, the store, the changed
list. The walk exits 0 when it ends by its list's end or ``q``, 1 when any child ended
unexpectedly, and 130 after Ctrl-C.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, cast

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import album
from lyrics_genius import genius_url_allowed
from lyrics_heard_store import Listing, list_records, records_for
from lyrics_options import DEFAULT_LANG, MODE_PASTE
from lyrics_paste import paste_refusal
from lyrics_paste_mode import EXIT_NOT_SUNG, EXIT_REFUSED, EXIT_WRITTEN
from lyrics_paste_mode import MAX_PASTE_BYTES as MAX_PASTE_BYTES
from lyrics_run_folders import is_link_or_junction
from lyrics_stages import Stages
from stdio_utf8 import ENCODING, spell_unsafe
from sweep_changed import ChangedListError, ChangedUnion, changed_union
from sweep_child import CHANGED_LIST_OPTION, HEARD_DIR_OPTION, ChildResult, ChildRunner
from sweep_genius_lists import HAS_IT_COLUMNS as HAS_IT_COLUMNS
from sweep_genius_lists import HasItError, HasItRow, read_has_it
from sweep_paste_seams import PasteInputError as PasteInputError
from sweep_paste_seams import PasteSeams as PasteSeams
from sweep_paste_seams import keyless_env, real_seams
from sweep_survey import is_linked
from sweep_run import EXIT_ALBUM_ERRORS, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE
from sweep_wiring import Wiring

#: The album tool's entry point, run as each child.
ALBUM_ENTRY: Final = Path(album.__file__).resolve()
#: The longest one child may take: it asks no provider, so this bounds only a write to
#: a synced folder that stalls.
PASTE_TIMEOUT_S: Final = 600.0
#: What the person is asked at each row.
PROMPT: Final = "Copy the lyrics in the browser, then press Enter (s skip, q quit): "
#: What follows a paste the album tool did not write.
NOT_SUNG_AGAIN: Final = (
    "not written: copy the lyrics again and press Enter, s to give up, q to quit"
)
#: What an answer the prompt does not know is told.
UNKNOWN_ANSWER: Final = "press Enter to paste, s to skip, q to quit"
#: Why a row was passed over without a paste.
SKIPPED_BY_YOU: Final = "skipped by you"
OUTSIDE_LIBRARY: Final = "not a track in an Artist/Album folder of the library"
NOT_GENIUS: Final = "not a Genius https URL, never opened"
LINKED_FOLDER: Final = (
    "its artist or album folder is reached through a link, never opened"
)
LINKED_TRACK: Final = "the track is a link, never opened"
LINKED_SUBFOLDER: Final = (
    "a folder between its album folder and the track is a link, never opened"
)
#: The summary's last line when ``q`` or the end of input ended the walk.
STOPPED: Final = "stopped before the list's end"
#: The answers, as typed.
_PASTE, _SKIP, _QUIT = "", "s", "q"
#: How the walk's own lines open.
_PREFIX: Final = "paste"
#: The private folder's name prefix, so a folder a killed walk left says whose.
_FOLDER_PREFIX: Final = "lyricsmith-paste-"
#: A private text file: read and written by the user who made it, no one else.
_PRIVATE: Final = 0o600
#: The least a track's path holds under the library: an artist, an album, a file.
_ALBUM_DEPTH: Final = 2


class End(StrEnum):
    """How a row ended; the values are the summary's words."""

    REPLACED = "replaced"
    SKIPPED = "skipped"
    REFUSED = "refused"
    GIVEN_UP = "given up"
    FAILED = "failed"


@dataclass(frozen=True)
class RowEnd:
    """One row's end, and why when it was not replaced."""

    track: Path
    end: End
    why: str = ""


class Quit(Exception):
    """``q``, or the end of input: the walk ends before its list's end."""


@dataclass
class Walk:
    """One walk's state: what it runs on, where its files go, and how each row ended."""

    seams: PasteSeams
    run_child: ChildRunner
    stages: Stages
    library: Path
    heard_dir: Path
    listing: Listing
    folder: Path
    union: ChangedUnion | None
    lang: str
    env: Mapping[str, str]
    ends: list[RowEnd] = field(default_factory=list)
    _attempts: int = 0

    def visit(self, number: int, total: int, row: HasItRow) -> None:
        """Walk one row: pass it over, or open it and prompt until it ends."""
        track = Path(os.path.normpath(row.track))
        album_dir = album_of(self.library, track)
        why = self._pass_over(row, track, album_dir)
        if why is not None or album_dir is None:
            _notice(f"skipped {track}: {why}")
            self.ends.append(RowEnd(track, End.SKIPPED, why or OUTSIDE_LIBRARY))
            return
        _notice(f"track {number} of {total}: {track}")
        _notice(f"Genius: {row.title} by {row.artist}: {row.url}")
        try:
            self.seams.open_url(row.url)
        except PasteInputError as error:
            _notice(f"could not open the page ({error}): open it by hand")
        self.ends.append(self._prompt(track, album_dir))

    def _pass_over(
        self, row: HasItRow, track: Path, album_dir: Path | None
    ) -> str | None:
        """Why *row* is passed over unopened, None when it is pasted."""
        if not genius_url_allowed(row.url):
            return NOT_GENIUS
        if album_dir is None:
            return OUTSIDE_LIBRARY
        record = records_for(self.listing, (track,)).get(track)
        try:
            # The album tool resolves the folder it is given, so a linked one would be
            # written outside the library; and it refuses a linked track, so its page
            # would be opened only for the person's copy to be thrown away.
            if is_linked(album_dir, self.library):
                return LINKED_FOLDER
            linked = linked_below(album_dir, track)
            if linked is not None:
                return linked
            taken = paste_refusal(self.stages, album_dir, track, record, self.lang)
        # Any failure reading the track is that row's: it is passed over, said.
        except Exception as error:  # noqa: BLE001 - every read failure is the row's
            return f"it cannot be read: {error}"
        return taken if isinstance(taken, str) else None

    def _prompt(self, track: Path, album_dir: Path) -> RowEnd:
        """Prompt for *track* until a paste ends it, or ``s`` skips it."""
        tried = False
        while True:
            answer = self._answer()
            if answer == _SKIP:
                end = End.GIVEN_UP if tried else End.SKIPPED
                return RowEnd(track, end, "not sung" if tried else SKIPPED_BY_YOU)
            if answer != _PASTE:
                _notice(UNKNOWN_ANSWER)
                continue
            result = self._attempt(track, album_dir)
            if result is None:
                continue
            if result.exit_code == EXIT_NOT_SUNG:
                tried = True
                _notice(NOT_SUNG_AGAIN)
                continue
            return child_end(track, result)

    def _answer(self) -> str:
        """The person's answer, lowercased; `Quit` at ``q`` or the end of input."""
        try:
            answer = self.seams.ask(PROMPT).lower()
        except EOFError:
            answer = _QUIT
        if answer == _QUIT:
            raise Quit
        return answer

    def _attempt(self, track: Path, album_dir: Path) -> ChildResult | None:
        """One paste of the clipboard into *track*; the child's result, None if unread.

        A Ctrl-C the child ended on is raised here, once its list is merged.
        """
        try:
            text = self.seams.clipboard()
        except PasteInputError as error:
            _notice(f"{error}; press Enter to try again")
            return None
        self._attempts += 1
        text_file = self.folder / f"{self._attempts}.txt"
        listed = (
            None
            if self.union is None
            else self.union.child_list(album_dir, album_dir.name)
        )
        try:
            write_private(text_file, text)
            argv = paste_argv(
                album_dir, track, self.heard_dir, text_file, listed, lang=self.lang
            )
            result = self.run_child(argv, PASTE_TIMEOUT_S, env=self.env)
        finally:
            text_file.unlink(missing_ok=True)
            if self.union is not None:
                self.union.absorb(album_dir)
        # Ctrl-C reached this process while the child ran: the person's own stop. A
        # child stopped at its bound exits interrupted too, but that is its failure.
        if result.interrupted:
            raise KeyboardInterrupt
        return result


def album_of(library: Path, track: Path) -> Path | None:
    """*track*'s ``Artist/Album`` folder under *library*, None when it has none."""
    try:
        parts = track.relative_to(library).parts
    except ValueError:
        return None
    if len(parts) <= _ALBUM_DEPTH:
        return None
    return library.joinpath(*parts[:_ALBUM_DEPTH])


def linked_below(album_dir: Path, track: Path) -> str | None:
    """Why *track* is reached through a link below *album_dir*, None when it is not.

    Each folder between the album folder and the track, and the track itself, is looked
    at without following it, by `album`'s own rule (`is_link_or_junction`): its walk
    skips a linked folder and a linked track alike, so either would be refused only
    after the person had copied its lyrics. A failed look is raised, the row's.
    """
    parts = track.relative_to(album_dir).parts
    for depth in range(1, len(parts) + 1):
        if is_link_or_junction(album_dir.joinpath(*parts[:depth])):
            return LINKED_TRACK if depth == len(parts) else LINKED_SUBFOLDER
    return None


def write_private(path: Path, text: str) -> None:
    """Write *text* to the new file *path*, for the user who made it alone."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE)
    with os.fdopen(descriptor, "w", encoding=ENCODING, newline="") as stream:
        stream.write(text)


def paste_argv(
    album_dir: Path,
    track: Path,
    heard_dir: Path,
    text_file: Path,
    changed: Path | None,
    *,
    lang: str,
    interpreter: str = sys.executable,
) -> list[str]:
    """The command running the album tool's paste of *text_file* into *track*."""
    argv = [interpreter, str(ALBUM_ENTRY), "--album", str(album_dir)]
    argv += ["--mode", MODE_PASTE, "--track", str(track), "--lang", lang]
    argv += [HEARD_DIR_OPTION, str(heard_dir), "--text-from", str(text_file)]
    return argv + ([] if changed is None else [CHANGED_LIST_OPTION, str(changed)])


def child_end(track: Path, result: ChildResult) -> RowEnd:
    """How a child's *result* ends *track*'s row, a not-sung one aside."""
    if result.exit_code == EXIT_WRITTEN:
        return RowEnd(track, End.REPLACED)
    if result.exit_code == EXIT_REFUSED:
        return RowEnd(track, End.REFUSED, "the album tool refused the track")
    if result.timed_out:
        return RowEnd(track, End.FAILED, "the album tool ran past its bound")
    if result.exit_code is None:
        return RowEnd(track, End.FAILED, "the album tool would not stop")
    return RowEnd(track, End.FAILED, f"the album tool exited {result.exit_code}")


def format_summary(ends: Sequence[RowEnd], *, stopped: bool) -> str:
    """The walk's summary: each end's count, each row not replaced, and a stop."""
    counts = ", ".join(
        f"{sum(1 for row in ends if row.end is end)} {end}" for end in End
    )
    lines = [f"{_PREFIX}: {counts}"]
    lines += (
        spell_unsafe(f"  {row.end} {row.track}: {row.why}")
        for row in ends
        if row.end is not End.REPLACED
    )
    lines += [STOPPED] if stopped else []
    return "".join(f"{line}\n" for line in lines)


class PasteSetupError(Exception):
    """What stops the walk before its first row; its message says why."""


def _set_up(args: argparse.Namespace, seams: PasteSeams) -> tuple[HasItRow, ...]:
    """The list's rows, once the terminal is ready; `PasteSetupError` if not."""
    why = seams.ready()
    if why is not None:
        raise PasteSetupError(f"cannot walk: {why}")
    try:
        return read_has_it(args.from_list)
    except HasItError as error:
        raise PasteSetupError(f"--from {error}") from error


@contextmanager
def _private_folder() -> Iterator[Path]:
    """A private folder (0o700) for the pasted texts, removed however the walk ends."""
    folder = Path(tempfile.mkdtemp(prefix=_FOLDER_PREFIX))
    try:
        yield folder
    finally:
        # The walk's own scratch folder, holding nothing but its pasted texts.
        shutil.rmtree(folder, ignore_errors=True)


def _walk(walk: Walk, rows: Sequence[HasItRow]) -> tuple[bool, bool]:
    """Visit each row in turn: whether ``q`` stopped it, whether Ctrl-C did."""
    try:
        for number, row in enumerate(rows, start=1):
            walk.visit(number, len(rows), row)
    except Quit:
        return True, False
    except KeyboardInterrupt:
        return False, True
    return False, False


def paste_mode(
    args: argparse.Namespace, library: Path, wiring: object, _words: object
) -> int:
    """``--mode paste``: walk the has-it list, pasting each track in; the exit code."""
    # The registry types every runner's wiring loosely; the entry point passes its own.
    wired = cast(Wiring, wiring)
    seams = wired.paste or real_seams()
    try:
        rows = _set_up(args, seams)
        listing = list_records(args.heard_dir)
    except PasteSetupError as error:
        _notice(str(error))
        return EXIT_USAGE
    except OSError as error:
        _notice(f"--heard-dir cannot be listed: {error}")
        return EXIT_USAGE
    try:
        with (
            changed_union(args.changed_list, _notice) as union,
            _private_folder() as folder,
        ):
            stages = album.real_wiring().stages
            walk = Walk(
                seams,
                wired.run_child,
                stages,
                library,
                args.heard_dir,
                listing,
                folder,
                union,
                args.lang or DEFAULT_LANG,
                keyless_env(),
            )
            stopped, interrupted = _walk(walk, rows)
    except ChangedListError as error:
        _notice(str(error))
        return EXIT_USAGE
    print(format_summary(walk.ends, stopped=stopped), end="", file=sys.stdout)
    if interrupted:
        _notice("interrupted")
        return EXIT_INTERRUPTED
    failed = any(row.end is End.FAILED for row in walk.ends)
    return EXIT_ALBUM_ERRORS if failed else EXIT_OK


def _notice(text: str) -> None:
    """One notice line on stderr, its control characters made visible."""
    print(spell_unsafe(f"{_PREFIX}: {text}"), file=sys.stderr)
