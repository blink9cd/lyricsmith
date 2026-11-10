"""``--mode paste``: one text copied from a browser, written only when the track sings it.

The mode takes one track (``--track``), the transcript store sync wrote
(``--heard-dir``), and the text to paste (``--text-from``), and never prompts. In order:

1. **The text.** `read_pasted` reads ``--text-from`` as strict UTF-8, no further than
   `MAX_PASTE_BYTES` and one byte: a file it cannot read, or not UTF-8, is
   `PasteUnreadableError`, a could-not-run (exit 2); a larger one is too large, read no
   further.
2. **The guards.** The track's record is found by file identity
   (`lyrics_heard_store.records_for`), and the track is refused (`EXIT_REFUSED`) unless
   the fix itself would take it (`lyrics_paste.paste_refusal`), its text read under
   ``--lang`` as a fix reads it. Its tags are read once, here; a track whose tags or
   sidecars cannot be read (`_UNREADABLE`) is refused too, never a crash, so no failure
   is read as not sung.
3. **The cleaning.** `lyrics_paste.clean_paste`: Genius's header and trailer and the
   browser's clutter lines go, and blank text, a placeholder, text over the cap or text
   the track's length rules out is not lyrics to write (`EXIT_NOT_SUNG`).
4. **The sung check.** `lyrics_paste.judge_paste`: a paste the transcript does not sing,
   its share of the pairs heard said, is not written either (`EXIT_NOT_SUNG`).
5. **The write.** One run (`lyrics_run.run_album`) of the one track with the paste's
   step (`lyrics_paste_step.paste_step`), its frame under ``--lang``: the album lock,
   the backup, the manifest, a run id, the write a fix makes, the summary a run
   prints, and undo with ``--mode undo --run-id``. A run whose track errored is `EXIT_PASTE_ERROR`.

Nothing in it asks a provider or touches the network: the run's provider table is empty
and its Genius state absent, so the summary's provider tally counts nothing, and the
Genius label says the text was pasted. ``--changed-list`` is written whatever the end
once the guards are read, empty when nothing was written, so a caller merging it adds
nothing; a could-not-run writes none. Every notice goes to *notice*; only the summary of
a write goes to stdout.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path
from typing import Final

from mutagen import MutagenError

import lyrics_album
import lyrics_changed
import lyrics_exit
import lyrics_files
import lyrics_run
import lyrics_summary
from lyrics_exit import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PASTE_ERROR,
    EXIT_PASTE_NOT_SUNG,
    EXIT_PASTE_REFUSED,
    EXIT_TRACK_ERRORS,
)
from lyrics_fetch import PROVIDER_DEADLINE_S
from lyrics_heard_store import HeardRecord, file_identity, list_records, records_for
from lyrics_model import Lyrics, LyricsError, TrackTags
from lyrics_options import DEFAULT_LANG
from lyrics_fix_text import text_of
from lyrics_paste import NO_RECORD, Taken, clean_paste, judge_paste, taken_from
from lyrics_paste_step import PastePlan, paste_step
from lyrics_provider_modes import Notice, discover
from lyrics_records import MAX_LYRICS_CHARS, GeniusState
from lyrics_stages import Stages
from lyrics_wiring import Wiring

#: The exit codes the mode gives, by what each means here: written; not written, the
#: paste not lyrics the transcript sings; the track refused by its guards; a run whose
#: one track errored. Each is `lyrics_exit`'s, where every code of the tool is kept.
EXIT_WRITTEN: Final = EXIT_OK
EXIT_NOT_SUNG: Final = EXIT_PASTE_NOT_SUNG
EXIT_REFUSED: Final = EXIT_PASTE_REFUSED
#: The largest pasted file read, in bytes: every text under the lyrics cap fits, at four
#: bytes a character at most in UTF-8.
MAX_PASTE_BYTES: Final = 4 * MAX_LYRICS_CHARS
#: What the summary's Genius label says: the text came from Genius, by hand.
GENIUS_LABEL: Final = "not asked (pasted by hand)"
#: How a paste the cleaning refused is said, before why.
_NOT_LYRICS: Final = "not lyrics to write"
#: Closes each notice of a paste not written.
_NOTHING: Final = "nothing was written"
#: What reading a track's tags or sidecars raises when it cannot: an OS error,
#: mutagen's own (its ID3 errors are `ValueError`s too), a frame field the conversion
#: refuses, and a sidecar that is not UTF-8 (a `LyricsError`). A refusal, not a crash.
_UNREADABLE: Final = (OSError, ValueError, TypeError, MutagenError, LyricsError)
#: Why a track whose tags or sidecars cannot be read is refused, before what was raised.
UNREADABLE_TRACK: Final = "its tags or sidecars cannot be read"


class PasteUnreadableError(LyricsError):
    """The ``--text-from`` file could not be read as UTF-8: the paste cannot run."""


def read_pasted(path: Path) -> str | None:
    """The text at *path*, or None when it is past `MAX_PASTE_BYTES`, read no further."""
    try:
        data = lyrics_files.read_regular(path, MAX_PASTE_BYTES + 1)
    except OSError as error:
        raise PasteUnreadableError(f"--text-from cannot be read: {error}") from error
    if len(data) > MAX_PASTE_BYTES:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PasteUnreadableError(f"--text-from is not UTF-8: {path}") from error


def _refused(rel: str, why: str, args: argparse.Namespace, notice: Notice) -> int:
    """Say *rel* takes no paste, and why; write the empty list; `EXIT_REFUSED`."""
    notice(f"refused {rel}: {why}; {_NOTHING}")
    return _empty_list(args, notice, EXIT_REFUSED)


def _not_sung(rel: str, why: str, args: argparse.Namespace, notice: Notice) -> int:
    """Say *rel*'s paste is not written, and why; write the empty list; `EXIT_NOT_SUNG`."""
    notice(f"{rel}: {why}; {_NOTHING}")
    return _empty_list(args, notice, EXIT_NOT_SUNG)


def _empty_list(args: argparse.Namespace, notice: Notice, code: int) -> int:
    """Write ``--changed-list`` empty, when given; *code*, unless that write failed."""
    failure, late = lyrics_changed.write_changed_list(
        args.changed_list, lambda: (), notice
    )
    if failure is not None:
        notice(failure)
        return lyrics_exit.EXIT_RECORD_NOT_WRITTEN
    return EXIT_INTERRUPTED if late else code


def _cleaned(text: str | None, duration_s: float | None) -> tuple[Lyrics | None, str]:
    """The pasted *text* cleaned and judged by the track's length, or why not."""
    if text is None:
        return None, f"too large: over {MAX_PASTE_BYTES} bytes"
    return clean_paste(text, duration_s)


def _taken(
    stages: Stages, album: Path, path: Path, record: HeardRecord | None, lang: str
) -> tuple[Taken, TrackTags] | str:
    """The track as a paste takes it, with its tags read once; else why it is refused.

    The fix's guard, as `lyrics_paste.paste_refusal` reads it. The track's reads, its
    tags and its stored text (`text_of`, which reads the sidecars and frames), are inside
    the catch, so anything of an `_UNREADABLE` kind raised there, a fault in `text_of`
    itself included, is a refusal; the judgment after them (`taken_from`) stays a crash.
    """
    if record is None:
        return NO_RECORD
    try:
        tags = stages.read_tags(path)
        stored = text_of(stages, album, path, tags, lang)
    except _UNREADABLE as error:
        return f"{UNREADABLE_TRACK} ({type(error).__name__}: {error})"
    taken = taken_from(stored, record)
    return taken if isinstance(taken, str) else (taken, tags)


def _run(
    wiring: Wiring,
    album: Path,
    path: Path,
    plan: PastePlan,
    lang: str,
    notice: Notice,
) -> lyrics_run.RunReport:
    """One run of the one track, writing *plan*'s text under *lang*; its report."""
    options = lyrics_run.RunOptions(lang, PROVIDER_DEADLINE_S, GeniusState.ABSENT)
    return lyrics_run.run_album(
        album,
        (path,),
        {},
        options,
        wiring.stages,
        acquire_lock=wiring.acquire_lock,
        started=wiring.now(),
        progress=notice,
        step=partial(paste_step, plan),
    )


def _finish(
    args: argparse.Namespace,
    album: Path,
    report: lyrics_run.RunReport,
    prog: str,
    notice: Notice,
) -> int:
    """Write the changed list and the run's summary; the paste's exit code."""
    with lyrics_exit.report_writes():
        written = lyrics_changed.write_changed_list(
            args.changed_list,
            lambda: lyrics_changed.run_changed(album, report.tracks),
            notice,
        )
        summary = lyrics_summary.format_run(
            report,
            album=album,
            prog=prog,
            command=args.command_prefix,
            genius=GENIUS_LABEL,
        )
        print(summary, end="", file=sys.stdout)
        code = lyrics_exit.report_exit(report, prog, written, notice)
    return EXIT_PASTE_ERROR if code == EXIT_TRACK_ERRORS else code


def paste_mode(
    args: argparse.Namespace, wiring: Wiring, prog: str, notice: Notice
) -> int:
    """Paste ``--text-from`` into ``--track`` when its transcript sings it; the exit code."""
    album, found = discover(args, notice)
    lang = args.lang or DEFAULT_LANG
    (path,) = lyrics_album.track_selection(album, found, args.track)
    rel = path.relative_to(album).as_posix()
    text = read_pasted(args.text_from)
    listing = list_records(args.heard_dir.absolute())
    for problem in listing.problems:
        notice(problem)
    # Before the match, so a file swapped in after it is told apart under the lock.
    identity = file_identity(path)
    record = records_for(listing, (path,)).get(path)
    read = _taken(wiring.stages, album, path, record, lang)
    if isinstance(read, str):
        return _refused(rel, read, args, notice)
    taken, tags = read
    lyrics, detail = _cleaned(text, tags.duration_s)
    if lyrics is None:
        return _not_sung(rel, f"{_NOT_LYRICS} ({detail})", args, notice)
    winner = judge_paste(lyrics, taken.record, taken.refused)
    if isinstance(winner, str):
        return _not_sung(rel, winner, args, notice)
    plan = PastePlan(taken.record, winner.lyrics, identity)
    report = _run(wiring, album, path, plan, lang, notice)
    return _finish(args, album, report, prog, notice)
