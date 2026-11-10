"""The Genius check: split a track list by what Genius's keyed API says it has.

`genius_check_mode` is ``sweep --mode genius-check``. It reads ``--tracks-from``, a
plain track list in the ``--changed-list`` format (`lyrics_changed.parse_changed_list`),
read no further than `MAX_TRACKS_FROM_BYTES` and one byte, never through a link
(`sweep_ledger.read_unlinked`). For each track in order it reads the tags, builds the
query as a run does (`lyrics_run.track_query`, the file stem standing in for missing
tags), and asks Genius's keyed search once for the title and the artist
(`lyrics_genius_search.guarded_search`), through Genius's breaker. The hits are judged
(`sweep_genius_judge.judge_hits`) and the three lists written into ``--out``
(`sweep_genius_lists`).

**Keyed only.** The key is the album tool's (`lyrics_config.resolve_genius_key`: the
environment, else the default config file); with none the check stops before anything
is read or asked. Only the keyed API is asked: Cloudflare challenges a script on Genius's
site and its unkeyed search, and the keyed API, which gives no lyrics, still answers.

**Bounded to the library.** A listed path that resolves, links followed, outside
``--library`` is `OUTSIDE_LIBRARY`: never read, never asked, said on stderr and counted
in the summary, as the paste walk passes over a row outside it.

**Unsure without a search.** A track whose file cannot be read, whatever reading it
raises, is `TRACK_UNREADABLE`, never asked, its error named by type on stderr. Once
Genius's breaker is open every track left is `NOT_ASKED_BREAKER`, with no request sent,
and a refused or timed-out search is `REFUSED` with its detail. Ctrl-C ends the loop:
the track in hand and every one after it are `NOT_ASKED_INTERRUPTED`, the lists are
written and the exit is the sweep's interrupted code, as a run keeps what it finished.
A Ctrl-C from the hold set just after the searches, the breaker's notice included, is
held until all three are written (`lyrics_album.deferred_interrupt`); one in the instant
before is raised as it came. Anything else that ends the loop leaves the rest
`NOT_ASKED_STOPPED`, and is raised only once the lists' write has been tried.

**Each path once.** A path the list names twice is checked once, where first named.
An untagged track is searched by its stem without a leading track number
(`lyrics_match.strip_track_number`), the reading the matcher tries too.

**Set up before any search.** Each of these stops the check before its first search:
an ``--out`` inside the library (refused as the draw refuses one,
`sweep_draw.out_inside_library`), the list, the key, and the ``--out`` folder itself,
made here (0o700) and refused when it cannot be made or written to.

It reads the library's tracks and never writes them; ``--library`` bounds the tracks
read and refuses an ``--out`` inside it. The summary goes to stdout and every notice to stderr,
and the key is never printed. Exit 0 once the lists are written and Genius answered
every search asked; `EXIT_UNANSWERED` once they are written but a search was
refused or a track not asked once the breaker opened; 2 for an ``--out``, a list or a
key it cannot use, or a list it cannot write; and 130 after Ctrl-C.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_breakers import BreakerGuard, breaker_setups
from lyrics_album import deferred_interrupt
from lyrics_config import resolve_genius_key
from lyrics_changed import parse_changed_list
from lyrics_genius_search import GeniusSearcher, SearchRefusal, guarded_search
from lyrics_http import PROVIDER_DEADLINE_S
from lyrics_model import ReadBackError, Source, TrackQuery, TrackTags
from lyrics_records import Secret
from lyrics_match import strip_track_number
from lyrics_run import track_query
from lyrics_state import make_state_folder
from stdio_utf8 import spell_unsafe
from sweep_genius_judge import Finding, Judged, judge_hits
from sweep_draw import out_inside_library
from sweep_genius_lists import CheckedTrack, listed_finding, write_lists
from sweep_genius_seams import CheckSeams, real_seams
from sweep_ledger import read_unlinked
from sweep_run import EXIT_ALBUM_ERRORS, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE
from sweep_wiring import Wiring

#: The exit once the lists are written but Genius did not answer every track: the
#: sweep's own code for a run that finished with something left undone.
EXIT_UNANSWERED: Final = EXIT_ALBUM_ERRORS

#: The largest track list read: far past any real list, small enough to hold in memory.
MAX_TRACKS_FROM_BYTES: Final = 64 * 1024 * 1024
#: Why a track is unsure without hits to judge.
TRACK_UNREADABLE: Final = "track unreadable"
OUTSIDE_LIBRARY: Final = "outside the library"
NOT_ASKED_STOPPED: Final = "not asked: check stopped"
NOT_ASKED_BREAKER: Final = "not asked: breaker open"
NOT_ASKED_INTERRUPTED: Final = "not asked: interrupted"
REFUSED: Final = "refused: {detail}"
#: How the summary names each finding, in the order it prints them.
_COUNTED: Final = (
    (Finding.HAS_IT, "has it"),
    (Finding.NOT_ON_GENIUS, "not on Genius"),
    (Finding.UNSURE, "unsure"),
)
_PREFIX: Final = "genius-check"


class CheckSetupError(Exception):
    """What stops the check before any search: its message says why, never the key."""


def _notice(text: str) -> None:
    """One notice line on stderr, its control characters made visible."""
    print(spell_unsafe(f"{_PREFIX}: {text}"), file=sys.stderr)


def read_tracks(path: Path) -> tuple[str, ...]:
    """The tracks the list at *path* names, each once; `CheckSetupError` if unusable."""
    try:
        data = read_unlinked(path, MAX_TRACKS_FROM_BYTES + 1)
    except OSError as error:
        raise CheckSetupError(
            f"--tracks-from cannot be read: {path}: {error}"
        ) from error
    if len(data) > MAX_TRACKS_FROM_BYTES:
        raise CheckSetupError(
            f"--tracks-from is more than {MAX_TRACKS_FROM_BYTES} bytes: {path}"
        )
    tracks = parse_changed_list(data)
    if tracks is None:
        raise CheckSetupError(
            f"--tracks-from is not a track list (one absolute path a line, "
            f"UTF-8, each line ended): {path}"
        )
    # Each path once, where first named: a repeat would be searched and listed twice.
    return tuple(dict.fromkeys(tracks))


def prepare_out(out: Path) -> None:
    """Make the *out* folder, or find it; `CheckSetupError` if it cannot be written to."""
    try:
        make_state_folder(out)
    except OSError as error:
        raise CheckSetupError(f"--out cannot be written: {out}: {error}") from error
    if not os.access(out, os.W_OK | os.X_OK):
        raise CheckSetupError(f"--out cannot be written: {out}: not writable")


def resolve_key(environ: Mapping[str, str]) -> Secret:
    """Genius's key from *environ* or the default config file; `CheckSetupError` if none."""
    resolution = resolve_genius_key(None, environ)
    for warning in resolution.warnings:
        _notice(f"genius: {warning}")
    if resolution.secret is None:
        raise CheckSetupError(
            f"no Genius key found (looked in {resolution.searched}); "
            "the check asks only the keyed API"
        )
    return resolution.secret


def search_term(query: TrackQuery) -> str:
    """What is searched for *query*: its title, then its artist, if any.

    A file stem's title is searched without its leading track number.
    """
    title = strip_track_number(query.title) if query.from_filename else query.title
    return f"{title} {query.artist}".strip()


def _unsure(path: str, reason: str, *, unanswered: bool = False) -> CheckedTrack:
    """*path* unsure for *reason*, with no hit to name; *unanswered* if Genius was not."""
    return CheckedTrack(path, Judged(Finding.UNSURE, reason, None), unanswered)


@dataclass(frozen=True)
class Checker:
    """What each track is checked with: the searcher, Genius's breaker, the tag reader."""

    searcher: GeniusSearcher
    guard: BreakerGuard
    read_tags: Callable[[Path], TrackTags]
    #: The library, resolved: a track whose path resolves outside it is passed over.
    library: Path


def check_track(path: str, checker: Checker) -> CheckedTrack:
    """*path* searched and judged; unsure when outside, unreadable or not answered."""
    try:
        # The resolved path is the one read, which narrows a link swapped after the
        # bound is judged but does not close it: one swapped into a folder of
        # `resolved` before the read is still followed (sweep.md, "The Genius check").
        # The listed path names the rows.
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(checker.library):
            _notice(f"skipped {path}: {OUTSIDE_LIBRARY}")
            return _unsure(path, OUTSIDE_LIBRARY)
        query = track_query(checker.read_tags(resolved), Path(path))
    # The per-track boundary, as the draw's: a file that cannot be opened, or anything
    # a malformed frame raises (a `ValueError` from a field's conversion, say), is this
    # track's, and the check goes on to the next.
    except Exception as error:  # noqa: BLE001 - every read failure is the track's
        _notice(f"could not read {path}: {type(error).__name__}")
        return _unsure(path, TRACK_UNREADABLE)
    found = guarded_search(checker.searcher, checker.guard, search_term(query))
    if isinstance(found, SearchRefusal):
        reason = (
            REFUSED.format(detail=found.detail) if found.sent else NOT_ASKED_BREAKER
        )
        return _unsure(path, reason, unanswered=True)
    return CheckedTrack(path, judge_hits(query, found.page))


def check_tracks(
    tracks: Sequence[str], checker: Checker
) -> tuple[list[CheckedTrack], BaseException | None]:
    """Every track checked in order, and what cut the loop short, if anything.

    After Ctrl-C the track in hand and every one after it are unsure, unasked, and so
    after anything else that ends the loop early, so that the lists are still written.
    """
    checked: list[CheckedTrack] = []
    try:
        for number, path in enumerate(tracks, start=1):
            _notice(f"track {number} of {len(tracks)}")
            checked.append(check_track(path, checker))
    # Held, never swallowed: the caller writes the lists, then raises anything but
    # Ctrl-C, so the searches already made are kept and the end stays loud.
    except BaseException as stop:  # noqa: BLE001 - re-raised once the lists are written
        reason = (
            NOT_ASKED_INTERRUPTED
            if isinstance(stop, KeyboardInterrupt)
            else NOT_ASKED_STOPPED
        )
        checked += (_unsure(path, reason) for path in tracks[len(checked) :])
        return checked, stop
    return checked, None


def format_summary(checked: Sequence[CheckedTrack], written: Sequence[Path]) -> str:
    """The summary: the tracks checked, each finding's count, and each file written.

    Under the unsure count, how many of them are outside the library. Each track is
    counted as the lists write it (`listed_finding`), so a had track whose path no
    has-it row can hold is counted unsure, as its file lists it.
    """
    listed = [listed_finding(track) for track in checked]
    lines = [f"{_PREFIX}: {len(checked)} tracks"]
    for finding, label in _COUNTED:
        count = sum(1 for judged in listed if judged.finding is finding)
        lines.append(f"  {label}: {count}")
    outside = sum(1 for judged in listed if judged.reason == OUTSIDE_LIBRARY)
    lines.append(f"    {OUTSIDE_LIBRARY}: {outside}")
    lines += (spell_unsafe(f"wrote: {path}") for path in written)
    return "".join(line + "\n" for line in lines)


def _set_up(
    args: argparse.Namespace, library: Path, seams: CheckSeams
) -> tuple[tuple[str, ...], Checker]:
    """The tracks and what checks them; `CheckSetupError` before any search."""
    if out_inside_library(args.out, library):
        raise CheckSetupError(
            f"--out is inside the library, which the check never writes: {args.out}"
        )
    tracks = read_tracks(args.tracks_from)
    secret = resolve_key(seams.environ)
    setup = breaker_setups(seams.environ, seams.now, _notice, sources=(Source.GENIUS,))
    prepare_out(args.out)
    searcher = GeniusSearcher(
        seams.build_client(secret),
        clock=seams.clock,
        sleeper=seams.sleeper,
        # The per-request deadline the provider chain holds every call to.
        deadline_s=PROVIDER_DEADLINE_S,
    )
    guard = BreakerGuard(setup[Source.GENIUS])
    return tracks, Checker(searcher, guard, seams.read_tags, library)


def write_said(out: Path, checked: Sequence[CheckedTrack]) -> tuple[Path, ...] | None:
    """The three lists written into *out*; None, said, when one cannot be written."""
    try:
        return write_lists(out, checked)
    except (OSError, ReadBackError) as error:
        _notice(f"--out cannot be written: {out}: {error}")
        return None


def write_held(
    out: Path, checked: Sequence[CheckedTrack], guard: BreakerGuard
) -> tuple[tuple[Path, ...] | None, bool]:
    """`write_said`, a Ctrl-C held until all three lists are written; whether one came.

    The hold is in force before the breaker's write failure is said, so a Ctrl-C from
    the end of the searches on costs none of them. One landing before the hold is
    installed is raised as it came, the lists unwritten: never read as a failed write.
    The held Ctrl-C is raised as the block ends, so an unexpected error the write
    raised would be its context only: that error is raised instead, never hidden.
    """
    written: tuple[Path, ...] | None = None
    in_force = False
    try:
        with deferred_interrupt():
            in_force = True
            if guard.write_error is not None:
                _notice(
                    f"genius: could not write the breaker file: {guard.write_error}"
                )
            written = write_said(out, checked)
    except KeyboardInterrupt as interrupt:
        if interrupt.__context__ is not None:
            raise interrupt.__context__ from None
        if not in_force:
            raise
        return written, True
    return written, False


def exit_code(checked: Sequence[CheckedTrack], *, interrupted: bool, held: bool) -> int:
    """The exit once the lists are written: interrupted, unanswered, or clean.

    *interrupted* is a Ctrl-C during the searches, *held* one during the write.
    """
    if interrupted:
        _notice("interrupted; the tracks left are listed unsure")
        return EXIT_INTERRUPTED
    if held:
        _notice("interrupted while the lists were written; all three are written")
        return EXIT_INTERRUPTED
    if any(track.unanswered for track in checked):
        _notice("Genius did not answer every search; those tracks are listed unsure")
        return EXIT_UNANSWERED
    return EXIT_OK


def genius_check_mode(
    args: argparse.Namespace, library: Path, wiring: object, _words: object
) -> int:
    """``--mode genius-check``: check, write the three lists, print; the exit code."""
    # The registry types every runner's wiring loosely; the entry point passes its own.
    seams = cast(Wiring, wiring).genius or real_seams()
    try:
        tracks, checker = _set_up(args, library, seams)
    except CheckSetupError as error:
        _notice(str(error))
        return EXIT_USAGE
    checked, stop = check_tracks(tracks, checker)
    written, held = write_held(args.out, checked, checker.guard)
    if written is not None:
        print(format_summary(checked, written), end="", file=sys.stdout)
    if stop is not None and not isinstance(stop, KeyboardInterrupt):
        _notice(f"stopped early ({type(stop).__name__}); the tracks left are unsure")
        raise stop
    if written is None:
        return EXIT_USAGE
    return exit_code(checked, interrupted=stop is not None, held=held)
