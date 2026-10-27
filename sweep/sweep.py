"""lyricsmith sweep: run the album tool over a whole library, one album at a time.

The entry point of the tool folder, and with the silence pass (`sweep_silence`) the
only module here that writes output: the survey and the final report go to stdout,
progress, notices and the album tool's own output to stderr. Importing it puts the folder's private modules, under ``src/``, on
``sys.path``, resolved from this file's own location, then the shared libraries the
project's ``pyproject.toml`` names (``lyricsmith_project/``) and the sibling album
tool's folder, both from fixed places; importing the album tool puts the album tool's
own private modules on the path too.

`main` parses the options, finds the library's albums through the shared walk, surveys
them, and for ``--mode run`` hands the surveys to the sweep, which plans its visits
against the ledger exactly as the survey does (`plan_visits`), with the real child
runner, sleep and clock bound (`real_wiring`); a test passes its own `Wiring` instead.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

#: The private modules' folder, beside this file.
SRC_DIR = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC_DIR))
# The project's manifest reader, in its own folder under the project root: it puts the
# shared libraries the project's pyproject.toml names on sys.path, or fails naming the
# one that is missing (lyricsmith_project/lyricsmith_project.md).
_PROJECT_DIR = Path(__file__).resolve().parents[1]
_READER_DIR = str(_PROJECT_DIR / "lyricsmith_project")
# Moved first on sys.path, so no same-named module can stand in for the reader.
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

# Run as a script, a failure exits 2 naming it; imported, it raises.
lyricsmith_project.add_shared_libraries(exit_on_failure=__name__ == "__main__")
# The sibling album tool, a fixed folder under the project root, added once, and
# checked to be where `album` loads from before it is imported.
_ALBUM_DIR = _PROJECT_DIR / "album"
sys.path += [] if str(_ALBUM_DIR) in sys.path else [str(_ALBUM_DIR)]
lyricsmith_project.confirm_from(
    "album", _ALBUM_DIR, exit_on_failure=__name__ == "__main__"
)

import album  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune_mode  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_accept  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_report import Words  # noqa: E402 - follows the bootstrap
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_waiting  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_types import positive_int, seconds  # noqa: E402 - follows the bootstrap
from sweep_wiring import Wiring, real_wiring  # noqa: E402 - follows the bootstrap
from library_walk import DEFAULT_LIBRARY  # noqa: E402 - follows the bootstrap
from stdio_utf8 import spell_unsafe  # noqa: E402 - follows the bootstrap
from sweep_ledger import ledger_path, read_records  # noqa: E402 - follows the bootstrap
from sweep_options import (  # noqa: E402 - follows the bootstrap
    EPILOG,
    MODE_ACCEPT,
    MODE_RUN,
    MODE_SILENCE,
    MODE_SURVEY,
    build_options,
    refuse_misplaced,
)
from sweep_survey import (  # noqa: E402 - follows the bootstrap
    AlbumSurvey,
    LibraryUnlistableError,
    contained_albums,
    plan_visits,
    survey_library,
)

#: The album tool's entry point, found beside this tool by the bootstrap above.
ALBUM_ENTRY = Path(album.__file__).resolve()
#: The name a pasted resume command runs this tool by, when it runs alone.
_PROG_FILE = Path(__file__).name


#: `os.name` on Windows, where a run is refused.
_WINDOWS = "nt"
#: The pause between albums, in seconds. Conservative: it has run a whole library
#: unchanged, and has not been tuned.
DEFAULT_PAUSE_S = 10.0
#: How many consecutive refused albums stop a run. Conservative and untuned, like the
#: pause.
DEFAULT_STOP_AFTER = 3


# --- The options ---------------------------------------------------------------------


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """The CLI: every argument a named option, every real default shown; *prog* its name."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the album tool over every album of a library that has a "
        "track without synced lyrics, one album at a time.",
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "--library",
        default=DEFAULT_LIBRARY,
        help="The library: a folder of Artist/Album folders. A leading ~ is expanded.",
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_SURVEY, MODE_RUN, MODE_ACCEPT, MODE_SILENCE),
        default=MODE_SURVEY,
        help="survey: count each album's tracks by the lyrics they hold, writing "
        "nothing. run: run the album tool on each album a survey would visit. "
        "accept: report, by the album tool's accept rules, what dropping each "
        "album's runs and backups would remove; it removes them with --apply. "
        "silence: measure every track's peak and list the silent ones, judged.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="accept only: record each album's manifests outside the library, then "
        "remove its .lyricsmith folder; without it accept only reports.",
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
    parser.add_argument(
        "--owe-earlier-runs",
        action="store_true",
        help="A survey or run also counts as owed an album whose newest ledger line "
        "is done but predates the owed outcome, owing each refusing provider it names "
        "and each provider its run left out by a cool-down that could still answer "
        "a track; a run asks it only those. Not with --restart, --offline or "
        "--mode accept.",
    )
    _add_run_options(parser)
    parser.add_argument("--resume", type=Path, help=sweep_silence.RESUME_HELP)
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
        help="run and silence only: visit at most this many albums; every album "
        "left when omitted.",
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


# --- The modes -----------------------------------------------------------------------


def _notice(text: str) -> None:
    """One line of progress or notice, on stderr, its control characters made visible."""
    print(spell_unsafe(text), file=sys.stderr)


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
    lang = sweep_run.lang_of(build_options(args))
    return survey_library(
        library,
        lang=lang,
        on_skip=_skipped,
        on_linked=_linked,
        refetch_implausible=args.refetch_implausible,
    )


def _survey_mode(
    args: argparse.Namespace, library: Path, wiring: Wiring, _words: Words
) -> int:
    """Print the survey; 2 if the ledger exists but cannot be read.

    The ledger is only read, as a run reads it, so the survey lists the albums a run
    would visit and those it would pass over as done, and with --owe-earlier-runs
    each album an earlier run owes, with its debt; a missing ledger is not made. The
    providers' holds are read once, at the wiring's clock, so an owed album whose
    whole debt is held is listed apart (`sweep_waiting`).
    """
    ledger = ledger_path(library, os.environ)
    try:
        records = read_records(ledger, _notice)
    except OSError as error:
        _notice(f"the ledger cannot be read: {ledger}: {error}")
        return sweep_run.EXIT_USAGE
    surveys = _survey(args, library)
    options = build_options(args)
    read = sweep_run.ledger_plan(records, library, surveys, options, _notice)
    plan = plan_visits(library, surveys, read.done, restart=False, last=read.challenged)
    holds = sweep_waiting.read_holds(os.environ, wiring.now())
    for warning in holds.warnings:
        _notice(warning)
    to_visit, waiting = sweep_waiting.split_waiting(
        library, plan.to_visit, read.owed, holds.held
    )
    plan = dataclasses.replace(plan, to_visit=to_visit)
    # An earlier run's album that waits is listed under the waiting heading alone.
    waits = {passed.name for passed in waiting}
    earlier = {name: d for name, d in read.earlier.items() if name not in waits}
    text = sweep_report.format_survey(
        surveys,
        library,
        plan,
        refetch_untimed=args.refetch_untimed,
        refetch_implausible=args.refetch_implausible,
        earlier=earlier if args.owe_earlier_runs else None,
        waiting=waiting,
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
    options = build_options(args)
    with sweep_changed.changed_union(
        args.changed_list, _notice, restart=args.restart
    ) as changed:
        seams = sweep_run.SweepSeams(
            wiring.run_child, wiring.sleep, wiring.now, _notice, ALBUM_ENTRY
        )

        def holds() -> sweep_waiting.HoldsRead:
            """Every provider's hold, fresh, where and when the child will read them."""
            return sweep_waiting.read_holds(os.environ, wiring.now())

        seams = dataclasses.replace(seams, changed=changed, holds=holds)
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
        build_options(args),
        changed_list=args.changed_list,
    )
    # Escaped like the report: a library named with a control character prints as
    # visible escapes, so a pasted command then holds them as text.
    print(spell_unsafe(f"resume: {command}"))


def _accept_mode(
    args: argparse.Namespace, library: Path, wiring: Wiring, words: Words
) -> int:
    """Hold the library's lock, accept each album, print the summary; 2 if it cannot.

    The ledger's lock keeps it off a library a sweep run is visiting; it writes no
    ledger line. The walk is the albums a run considers, linked ones dropped.
    """
    ledger = ledger_path(library, os.environ)
    run = sweep_accept.AcceptRun(
        library,
        args.apply,
        wiring.accept or sweep_accept.real_seams(),
        wiring.now,
        " ".join(sweep_report.resume_prog(words, _PROG_FILE)),
        lambda text: print(text, end=""),
        _notice,
    )
    try:
        lyrics_prune_mode.refuse_unsafe_delete(MODE_ACCEPT)
        with sweep_lock.held(sweep_lock.lock_path(ledger)):
            albums = contained_albums(library, on_skip=_skipped, on_linked=_linked)
            return sweep_accept.accept_library(run, albums)
    except (sweep_lock.SweepLockError, lyrics_prune_mode.UnsafeDeleteError) as error:
        _notice(f"{' '.join(words)}: {error}")
        return sweep_run.EXIT_USAGE
    except lyrics_exit.ReportNotWrittenError as error:
        return lyrics_exit.lost_report_exit(error, " ".join(words), _notice)


def album_prog(words: Words) -> Words:
    """The album command's words as the user ran this one: `lyricsmith album`, else album.py."""
    return sweep_report.album_prog(words, ALBUM_ENTRY.name)


_MODES: dict[str, Callable[[argparse.Namespace, Path, Wiring, Words], int]] = {
    MODE_SURVEY: _survey_mode,
    MODE_RUN: _run_mode,
    MODE_ACCEPT: _accept_mode,
    MODE_SILENCE: functools.partial(sweep_silence.silence_mode, prog_file=_PROG_FILE),
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
    # is made or written to the ledger, or before the next album (its holds read, the
    # pause, its link check, its album tool set up) is caught by the sweep: an entry in
    # hand is written in full, then the report prints. Anywhere else (the survey,
    # reading the ledger or writing a restart mark, taking the lock, or the few
    # instructions either side of making an entry) it ends here: a run prints only the
    # resume line, no report.
    except KeyboardInterrupt:
        _notice(f"{parser.prog}: interrupted")
        if args.mode == MODE_RUN:
            _print_resume(args, library, words)
        return sweep_run.EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover - the CLI entry point; main() is tested
    sys.exit(main())
