"""lyricsmith sweep: run the album tool over a whole library, one album at a time.

The entry point of the tool folder, and the only module here that writes output: the
survey and the final report go to stdout, progress, notices and the album tool's own
output to stderr. Importing it puts the folder's private modules, under ``src/``, on
``sys.path``, resolved from this file's own location, then the shared libraries and the
sibling tools it uses, the album tool among them, found by searching upward; importing
the album tool puts the album tool's own private modules on the path too.

`main` parses the options, finds the library's albums through the shared walk, surveys
them, and for ``--mode run`` hands the surveys to the sweep, which plans its visits
against the ledger exactly as the survey does (`plan_visits`), with the real child
runner, sleep and clock bound (`real_wiring`); a test passes its own `Wiring` instead.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: The private modules' folder, beside this file.
SRC_DIR = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC_DIR))
# The shared libraries, and the sibling album tool, sit directly under the project
# root, this file's grandparent.
# See stdio_utf8/stdio_utf8.md, "How this project's tools import it", for why
# each is ONE fixed candidate rather than a search up the ancestors, and why a
# missing copy fails its import below by name.
sys.path += [
    str(Path(__file__).resolve().parents[1] / name)
    for name in ("stdio_utf8", "library_walk", "album")
]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_report import Words  # noqa: E402 - follows the bootstrap
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_types import positive_int, seconds  # noqa: E402 - follows the bootstrap
from library_walk import DEFAULT_LIBRARY  # noqa: E402 - follows the bootstrap
from lyrics_outcomes import printable  # noqa: E402 - follows the bootstrap
from sweep_ledger import ledger_path  # noqa: E402 - follows the bootstrap
from sweep_survey import (  # noqa: E402 - follows the bootstrap
    AlbumSurvey,
    LibraryUnlistableError,
    plan_visits,
    survey_library,
)

#: The album tool's entry point, found beside this tool by the bootstrap above.
ALBUM_ENTRY = Path(album.__file__).resolve()
#: The name a pasted resume command runs this tool by, when it runs alone.
_PROG_FILE = Path(__file__).name


MODE_SURVEY, MODE_RUN = "survey", "run"
#: `os.name` on Windows, where a run is refused.
_WINDOWS = "nt"
#: The pause between albums, in seconds. Provisional, and conservative: tuned once the
#: first limited run has measured what the providers tolerate.
DEFAULT_PAUSE_S = 10.0
#: How many consecutive refused albums stop a run. Provisional, like the pause.
DEFAULT_STOP_AFTER = 3
#: The options only a run uses, refused by the survey rather than silently ignored.
_RUN_ONLY = (
    ("offline", "--offline"),
    *sweep_child.SKIP_OPTIONS,
    ("limit", "--limit"),
    ("restart", "--restart"),
    ("changed_list", sweep_child.CHANGED_LIST_OPTION),
)


# --- Wiring --------------------------------------------------------------------------


@dataclass(frozen=True)
class Wiring:
    """What a run runs with: the album tool's child runner, the pause, the clock."""

    run_child: sweep_child.ChildRunner
    sleep: Callable[[float], None]
    now: Callable[[], datetime]


def _utc_now() -> datetime:
    """Now, in UTC, which the ledger's time stamps are written in."""
    return datetime.now(UTC)


def real_wiring() -> Wiring:
    """The real child runner, the real sleep, the UTC clock."""
    return Wiring(sweep_child.run_child, time.sleep, _utc_now)


# --- The options ---------------------------------------------------------------------


_EPILOG = f"""\
Prerequisites:
  The album tool beside this one, with the project's dependencies installed
  (mutagen, requests, urllib3, lyricsgenius); the album tool's own prerequisites
  apply to every album a run visits. The library is a folder of Artist/Album
  folders. A run needs a POSIX system (macOS, Linux); one run per library at a time.

Output:
  A survey prints each album's synced, untimed-only and no-lyrics tracks, the totals,
  each track serving implausible lyrics under --refetch-implausible, the albums a run
  would visit, and those the ledger holds as done, which a run passes
  over; it only reads the ledger, writes nothing and contacts nothing. A run prints
  a table of the albums it visited, each album's undo command, and, when it stopped
  early, the command that resumes it. The album tool's own output, progress and
  notices go to stderr. Each album visited is recorded in a ledger under
  $XDG_STATE_HOME/lyricsmith/sweep/ or ~/.local/state/lyricsmith/sweep/, one per
  library; a run skips the albums the ledger holds as done unless --restart is given.

Exit codes:
  0    finished: every album visited ended done (or offline), the album tool exiting {album.EXIT_OK}
  1    finished, but an album did not: track errors, refused providers, an album
       locked by another run, the per-album bound of {sweep_child.ALBUM_TIMEOUT_S:.0f} seconds, no run
       recorded, an album the sweep could not run (its run folder unreadable, or
       the album tool not started), a run the album tool made but could not record (its exit {album.EXIT_RECORD_NOT_WRITTEN};
       the run is still tallied and its undo printed), an exit code the album
       tool never gives, or the album tool left running when it would not stop
  2    a usage problem, a library that is missing or cannot be listed, a ledger that
       cannot be read or written (a run still prints its report so far), another
       sweep already running on the library or its lock not taken, a run on Windows,
       a --changed-list file whose last write failed, or its scratch folder not made
  3    stopped after --stop-after consecutive albums whose providers only refused
  130  interrupted (Ctrl-C); the album in hand is recorded, not done"""


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """The CLI: every argument a named option, every real default shown; *prog* its name."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the album tool over every album of a library that has a "
        "track without synced lyrics, one album at a time.",
        formatter_class=lyrics_help.HelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--library",
        default=DEFAULT_LIBRARY,
        help="The library: a folder of Artist/Album folders. A leading ~ is expanded.",
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_SURVEY, MODE_RUN),
        default=MODE_SURVEY,
        help="survey: count each album's tracks by the lyrics they hold, writing "
        "nothing. run: run the album tool on each album a survey would visit.",
    )
    parser.add_argument(
        "--lang",
        type=album.iso_639_2,
        default=None,
        help="The ISO 639-2 code the album tool ranks and writes frames in; "
        f"the album tool's own {album.DEFAULT_LANG} when omitted.",
    )
    parser.add_argument(
        "--refetch-untimed",
        action="store_true",
        help="The refetch pass: a run passes --refetch-untimed to the album tool, which "
        "asks the providers again for each track holding only untimed lyrics. It "
        "keeps its own done albums in the ledger, apart from a normal sweep's, and a "
        "survey with it lists the albums this pass would visit. Not with --offline.",
    )
    parser.add_argument(
        "--refetch-implausible",
        action="store_true",
        help="A run passes --refetch-implausible to the album tool, which asks the "
        "providers again for each track serving implausible lyrics, removing them "
        "when none answers; a survey or run with it also visits an album whose "
        "every track is synced when one serves implausible lyrics. It keeps the "
        "pass: the normal one, or the refetch pass beside --refetch-untimed. Not "
        "with --offline.",
    )
    _add_run_options(parser)
    return parser


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    """The options only a run uses."""
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run only: pass --offline to the album tool, which then asks no provider.",
    )
    parser.add_argument(
        "--no-netease",
        action="store_true",
        help="run only: pass --no-netease to the album tool, which then skips both "
        "NetEase steps. " + lyrics_help.terms_note("--no-netease"),
    )
    parser.add_argument(
        "--no-bandcamp",
        action="store_true",
        help="run only: pass --no-bandcamp to the album tool, which then skips its "
        "Bandcamp step. " + lyrics_help.terms_note("--no-bandcamp"),
    )
    parser.add_argument(
        "--no-youtube-music",
        action="store_true",
        help="run only: pass --no-youtube-music to the album tool, which then "
        "skips its YouTube Music steps. "
        + lyrics_help.terms_note("--no-youtube-music"),
    )
    parser.add_argument(
        "--no-genius",
        action="store_true",
        help="run only: pass --no-genius to the album tool, which then skips its "
        "Genius step. " + lyrics_help.terms_note("--no-genius"),
    )
    parser.add_argument(
        "--no-tekstowo",
        action="store_true",
        help="run only: pass --no-tekstowo to the album tool, which then skips its "
        "Tekstowo step. " + lyrics_help.terms_note("--no-tekstowo"),
    )
    parser.add_argument(
        "--pause-seconds",
        type=seconds,
        default=DEFAULT_PAUSE_S,
        help="run only: the pause between one album and the next.",
    )
    parser.add_argument(
        "--stop-after",
        type=positive_int,
        default=DEFAULT_STOP_AFTER,
        help="run only: stop after this many consecutive albums whose providers "
        "only refused.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="run only: visit at most this many albums; every album left when omitted.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="run only: visit albums the ledger holds as done, as well.",
    )
    parser.add_argument(
        "--changed-list",
        type=Path,
        default=None,
        help="run only: add the absolute path of every MP3 the run's albums saved to "
        "this file, one per line, for an app that rereads their tags, rewritten after "
        "each album; --restart starts it empty; no list when omitted.",
    )


def refuse_misplaced(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through *parser* when the survey is given an option only a run uses."""
    if args.mode == MODE_SURVEY:
        for attr, flag in _RUN_ONLY:
            if getattr(args, attr) not in (None, False):
                parser.error(f"{flag} cannot be used with --mode {MODE_SURVEY}")
    # The album tool refuses the pair itself, so every album would only fail to run.
    for refetch in sweep_report.refetch_options(
        refetch_untimed=args.refetch_untimed,
        refetch_implausible=args.refetch_implausible,
    ):
        if args.offline:
            parser.error(f"{refetch} cannot be used with --offline")


def _options(args: argparse.Namespace) -> sweep_run.SweepOptions:
    """The sweep's options from the parsed command line."""
    return sweep_run.SweepOptions(
        lang=args.lang,
        offline=args.offline,
        pause_s=args.pause_seconds,
        stop_after=args.stop_after,
        limit=args.limit,
        restart=args.restart,
        refetch_untimed=args.refetch_untimed,
        refetch_implausible=args.refetch_implausible,
        skip=tuple(
            flag for attr, flag in sweep_child.SKIP_OPTIONS if getattr(args, attr)
        ),
    )


# --- The modes -----------------------------------------------------------------------


def _notice(text: str) -> None:
    """One line of progress or notice, on stderr, its control characters made visible."""
    print(printable(text), file=sys.stderr)


def _skipped(name: str) -> None:
    """The notice for an artist folder the walk could not list."""
    _notice(f"skipped artist folder {name}: it cannot be listed")


def _linked(name: str) -> None:
    """The notice for an album reached through a link, which the sweep drops."""
    _notice(
        f"skipped album {name}: its artist or album folder is a link, "
        "or it lies outside the library"
    )


def _survey(args: argparse.Namespace, library: Path) -> list[AlbumSurvey]:
    """Every album of *library* not reached through a link, surveyed in walk order."""
    lang = sweep_run.lang_of(_options(args))
    return survey_library(
        library,
        lang=lang,
        on_skip=_skipped,
        on_linked=_linked,
        refetch_implausible=args.refetch_implausible,
    )


def _survey_mode(
    args: argparse.Namespace, library: Path, _wiring: Wiring, _words: Words
) -> int:
    """Print the survey; 2 if the ledger exists but cannot be read.

    The ledger is only read, as a run reads it, so the survey lists the albums a run
    would visit and those it would pass over as done; a missing one is not made.
    """
    ledger = ledger_path(library, os.environ)
    try:
        options = _options(args)
        done = sweep_run.read_done(
            ledger, sweep_run.lang_of(options), sweep_run.pass_of(options), _notice
        )
    except OSError as error:
        _notice(f"the ledger cannot be read: {ledger}: {error}")
        return sweep_run.EXIT_USAGE
    surveys = _survey(args, library)
    plan = plan_visits(library, surveys, done, restart=False)
    text = sweep_report.format_survey(
        surveys,
        library,
        plan,
        refetch_untimed=args.refetch_untimed,
        refetch_implausible=args.refetch_implausible,
    )
    print(text, end="")
    return sweep_run.EXIT_OK


def _run_mode(
    args: argparse.Namespace, library: Path, wiring: Wiring, words: Words
) -> int:
    """Hold the library's lock, run the sweep, print the report; 2 if it is held."""
    ledger = ledger_path(library, os.environ)
    try:
        with sweep_lock.held(sweep_lock.lock_path(ledger)):
            return _sweep_held(args, library, ledger, wiring, words)
    # Both are raised before any album is visited, so there is no report to print.
    except (sweep_lock.SweepLockError, sweep_changed.ChangedListError) as error:
        _notice(f"{' '.join(words)}: {error}")
        return sweep_run.EXIT_USAGE


def _sweep_held(
    args: argparse.Namespace, library: Path, ledger: Path, wiring: Wiring, words: Words
) -> int:
    """Visit each album that needs it and is not done, then print the report."""
    surveys = _survey(args, library)
    options = _options(args)
    with sweep_changed.changed_union(
        args.changed_list, _notice, restart=args.restart
    ) as changed:
        seams = sweep_run.SweepSeams(
            wiring.run_child, wiring.sleep, wiring.now, _notice, ALBUM_ENTRY
        )
        seams = dataclasses.replace(seams, changed=changed)
        report = sweep_run.sweep(library, surveys, ledger, options, seams)
    print(
        sweep_report.format_sweep(
            report, library=library, options=options, undo_prog=album_prog(words)
        ),
        end="",
    )
    if report.stop is not sweep_run.StopReason.FINISHED:
        _print_resume(args, library, words)
    if changed is not None and changed.error is not None:
        _notice(f"{' '.join(words)}: {changed.error}")
        return sweep_run.EXIT_USAGE
    return sweep_run.sweep_exit_code(report)


def _print_resume(args: argparse.Namespace, library: Path, words: Words) -> None:
    """Print the command that resumes this run, named by *words* when dispatched."""
    command = sweep_report.resume_command(
        sweep_report.resume_prog(words, _PROG_FILE),
        library,
        _options(args),
        changed_list=args.changed_list,
    )
    # Escaped like the report: a library named with a control character prints as
    # visible escapes, so a pasted command then holds them as text.
    print(printable(f"resume: {command}"))


def album_prog(words: Words) -> Words:
    """The album command's words as the user ran this one: `lyricsmith album`, else album.py."""
    return sweep_report.album_prog(words, ALBUM_ENTRY.name)


_MODES: dict[str, Callable[[argparse.Namespace, Path, Wiring, Words], int]] = {
    MODE_SURVEY: _survey_mode,
    MODE_RUN: _run_mode,
}


def main(
    argv: Sequence[str] | None = None,
    *,
    wiring: Wiring | None = None,
    prog: str | None = None,
) -> int:
    """Parse the options and run the chosen mode; return the exit code.

    *wiring* is the real one unless a test passes its own. *prog*, ``lyricsmith sweep``
    from the lyricsmith command, names the tool in its help, notices and commands.
    """
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser(prog)
    words = sweep_report.command_words(parser.prog, prog)
    args = parser.parse_args(argv)
    refuse_misplaced(parser, args)
    # Checked before any path is built: a run stops the album tool with SIGINT, which
    # only a POSIX system delivers as Ctrl-C's own signal.
    if args.mode == MODE_RUN and os.name == _WINDOWS:
        _notice(f"{parser.prog}: --mode {MODE_RUN} needs a POSIX system (macOS, Linux)")
        return sweep_run.EXIT_USAGE
    library = Path(args.library).expanduser()
    if not library.is_dir():
        _notice(f"{parser.prog}: the library is not a folder: {library}")
        return sweep_run.EXIT_USAGE
    library = library.resolve()
    try:
        return _MODES[args.mode](args, library, wiring or real_wiring(), words)
    # Only the library's own listing: an artist folder the walk cannot list is passed
    # over with a notice, and an album's own error is that album's (`sweep_run`).
    except LibraryUnlistableError as error:
        _notice(f"{parser.prog}: {error}")
        return sweep_run.EXIT_USAGE
    # Ctrl-C while the album tool runs, while its run is read, while the album's entry
    # is made or written to the ledger, or during the pause is caught by the sweep:
    # an entry in hand is written in full, then the report prints. Anywhere else (the
    # survey, reading the ledger or writing a restart mark, taking the lock, or the
    # few instructions either side of making an entry) it ends here: a run prints only
    # the resume line, no report.
    except KeyboardInterrupt:
        _notice(f"{parser.prog}: interrupted")
        if args.mode == MODE_RUN:
            _print_resume(args, library, words)
        return sweep_run.EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover - the CLI entry point; main() is tested
    sys.exit(main())
