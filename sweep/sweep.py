"""lyricsmith sweep: run the album tool over a whole library, one album at a time.

The entry point of the tool folder, and with the silence pass (`sweep_silence`) the
only module here that writes output: the survey and the final report go to stdout,
progress, notices and the album tool's own output to stderr. Importing it puts the folder's private modules, under ``src/``, on
``sys.path``, resolved from this file's own location, then the shared libraries the
project's ``pyproject.toml`` names (``lyricsmith_project/``) and the sibling album
tool's folder, both from fixed places; importing the album tool puts the album tool's
own private modules on the path too.

`main` parses the options, finds the library's albums through the shared walk, surveys
them (for ``--mode fix-text`` only those holding a transcript record, `sweep_fix_text`),
and for ``--mode run`` or ``fix-text`` hands the surveys to the sweep, which plans its visits
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
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_accept  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fix_text  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_more_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_report import Words  # noqa: E402 - follows the bootstrap
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_waiting  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_types import absolute_path, positive_int, seconds  # noqa: E402 - bootstrap
from sweep_wiring import Wiring, real_wiring  # noqa: E402 - follows the bootstrap
from library_walk import DEFAULT_LIBRARY  # noqa: E402 - follows the bootstrap
from stdio_utf8 import spell_unsafe  # noqa: E402 - follows the bootstrap
from sweep_ledger import ledger_path, read_records  # noqa: E402 - follows the bootstrap
from sweep_options import (  # noqa: E402 - follows the bootstrap
    CHILD_MODES,
    EPILOG,
    FLAG_HELP,
    HOLD_FOR_GENIUS,
    MODE_ACCEPT,
    MODE_FIX_TEXT,
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


#: The pause between albums, in seconds. Conservative: it has run a whole library
#: unchanged, and has not been tuned.
DEFAULT_PAUSE_S = 10.0
#: Consecutive refused albums that stop a run; conservative and untuned, like the pause.
DEFAULT_STOP_AFTER = 3
#: How the help of an option both a run and the fix take opens.
_BOTH = "run and fix-text only:"


# --- The options ---------------------------------------------------------------------


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """The CLI: every argument a named option, every real default shown; *prog* its name."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the album tool over every album of a library that has a "
        "track without synced lyrics, one album at a time.",
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=sweep_more_modes.epilog(EPILOG),
    )
    parser.add_argument(
        "--library",
        default=DEFAULT_LIBRARY,
        help="The library: a folder of Artist/Album folders. A leading ~ is expanded.",
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_SURVEY, MODE_RUN, MODE_ACCEPT, MODE_SILENCE, MODE_FIX_TEXT)
        + sweep_more_modes.MODE_NAMES,
        default=MODE_SURVEY,
        help="survey: count each album's tracks by the lyrics they hold, writing "
        "nothing. run: run the album tool on each album a survey would visit. "
        "accept: report, by the album tool's accept rules, what dropping each "
        "album's runs and backups would remove; it removes them with --apply. "
        "silence: measure every track's peak and list the silent ones, judged. "
        "fix-text: run the album tool's fix on each album with a --heard-dir record."
        + sweep_more_modes.MODE_HELP,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        # None, not False, when omitted, on every on/off flag here: --help then shows
        # no default, and each reader that keeps the value makes it a bool first.
        default=None,
        help="accept only: record each album's manifests outside the library, then "
        "remove its .lyricsmith folder; without it accept only reports.",
    )
    parser.add_argument(
        "--lang",
        type=album.iso_639_2,
        default=None,
        help="The ISO 639-2 code the album tool ranks and writes frames in; "
        f"the album tool's own {album.DEFAULT_LANG} when omitted. Paste passes it "
        "to every track's paste and judges each record by it.",
    )
    parser.add_argument(
        "--refetch-untimed",
        action="store_true",
        default=None,
        help=FLAG_HELP["--refetch-untimed"],
    )
    parser.add_argument(
        "--refetch-implausible",
        action="store_true",
        default=None,
        help=FLAG_HELP["--refetch-implausible"],
    )
    parser.add_argument(
        "--owe-earlier-runs",
        action="store_true",
        default=None,
        help=FLAG_HELP["--owe-earlier-runs"],
    )
    _add_run_options(parser)
    parser.add_argument("--seed", **sweep_more_modes.SEED)
    parser.add_argument("--out", **sweep_more_modes.OUT)
    parser.add_argument("--withheld", **sweep_more_modes.WITHHELD)
    parser.add_argument("--hearing-log", **sweep_more_modes.HEARING_LOG)
    parser.add_argument("--strata", **sweep_more_modes.STRATA)
    parser.add_argument("--model", **sweep_more_modes.MODEL)
    parser.add_argument("--compare-model", **sweep_more_modes.COMPARE_MODEL)
    parser.add_argument("--tracks-from", **sweep_more_modes.TRACKS_FROM)
    parser.add_argument("--from", dest="from_list", **sweep_more_modes.FROM)
    parser.add_argument("--resume", type=Path, help=sweep_silence.RESUME_HELP)
    return parser


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    """The options a run, the fix, or the paste walk uses: each says which take it."""
    parser.add_argument(
        "--heard-dir",
        type=absolute_path,
        help="fix-text and paste only, and required by each: the transcript folder "
        "sync --heard-dir wrote, passed to each album's fix or each track's paste, "
        "which only reads it.",
    )
    parser.add_argument("--hold-for-genius", **HOLD_FOR_GENIUS)
    parser.add_argument(
        "--offline",
        action="store_true",
        default=None,
        help="run only: pass --offline to the album tool, which then asks no provider.",
    )
    parser.add_argument(
        "--no-netease",
        action="store_true",
        default=None,
        help=f"{_BOTH} pass --no-netease to the album tool, which then skips both "
        "NetEase steps. " + lyrics_help.terms_note("--no-netease"),
    )
    parser.add_argument(
        "--no-bandcamp",
        action="store_true",
        default=None,
        help=f"{_BOTH} pass --no-bandcamp to the album tool, which then skips its "
        "Bandcamp step. " + lyrics_help.terms_note("--no-bandcamp"),
    )
    parser.add_argument(
        "--no-youtube-music",
        action="store_true",
        default=None,
        help=f"{_BOTH} pass --no-youtube-music to the album tool, which then "
        "skips its YouTube Music steps. "
        + lyrics_help.terms_note("--no-youtube-music"),
    )
    parser.add_argument(
        "--no-genius",
        action="store_true",
        default=None,
        help=f"{_BOTH} pass --no-genius to the album tool, which then skips its "
        "Genius step. " + lyrics_help.terms_note("--no-genius"),
    )
    parser.add_argument(
        "--no-tekstowo",
        action="store_true",
        default=None,
        help=f"{_BOTH} pass --no-tekstowo to the album tool, which then skips its "
        "Tekstowo step. " + lyrics_help.terms_note("--no-tekstowo"),
    )
    parser.add_argument(
        "--pause-seconds",
        type=seconds,
        default=DEFAULT_PAUSE_S,
        help=f"{_BOTH} the pause between one album and the next.",
    )
    parser.add_argument(
        "--stop-after",
        type=positive_int,
        default=DEFAULT_STOP_AFTER,
        help=f"{_BOTH} stop after this many consecutive albums whose providers "
        "only refused.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="run, fix-text and silence only: visit at most this many albums; "
        "every album left when omitted.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        default=None,
        help=f"{_BOTH} visit albums the ledger holds as done, as well.",
    )
    parser.add_argument(
        "--changed-list",
        type=Path,
        default=None,
        help="run, fix-text and paste only: add the absolute path of every MP3 saved "
        "to this file, one per line, for an app that rereads their tags, rewritten "
        "after each album or paste; a run's or fix's --restart starts it empty; no "
        "list when omitted.",
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
        refetch_untimed=bool(args.refetch_untimed),
        refetch_implausible=bool(args.refetch_implausible),
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
        refetch_untimed=bool(args.refetch_untimed),
        refetch_implausible=bool(args.refetch_implausible),
        earlier=earlier if args.owe_earlier_runs else None,
        waiting=waiting,
    )
    print(text, end="")
    return sweep_run.EXIT_OK


def _child_surveys(args: argparse.Namespace, library: Path) -> list[AlbumSurvey]:
    """A run's albums surveyed, or the fix's: those holding a record in --heard-dir."""
    if args.heard_dir is None:
        return _survey(args, library)
    albums = contained_albums(library, on_skip=_skipped, on_linked=_linked)
    lang = sweep_run.lang_of(build_options(args))
    return sweep_fix_text.fix_surveys(albums, args.heard_dir, lang=lang, notice=_notice)


def _run_mode(
    args: argparse.Namespace, library: Path, wiring: Wiring, words: Words
) -> int:
    """Hold the library's lock, run the sweep (a run or the fix), print the report."""
    ledger = ledger_path(library, os.environ)
    try:
        with sweep_lock.held(sweep_lock.lock_path(ledger)):
            return _sweep_held(args, library, ledger, wiring, words)
    # Each is raised before any album is visited, so there is no report to print.
    except (
        sweep_lock.SweepLockError,
        sweep_changed.ChangedListError,
        sweep_fix_text.HeardDirError,
    ) as error:
        _notice(f"{' '.join(words)}: {error}")
        return sweep_run.EXIT_USAGE


def _sweep_held(
    args: argparse.Namespace, library: Path, ledger: Path, wiring: Wiring, words: Words
) -> int:
    """Visit each album that needs it and is not done, then print the report."""
    surveys = _child_surveys(args, library)
    options = build_options(args)
    with sweep_changed.sweep_lists(
        args.changed_list, args.hold_for_genius, _notice, restart=bool(args.restart)
    ) as (changed, held):
        seams = sweep_run.SweepSeams(
            wiring.run_child, wiring.sleep, wiring.now, _notice, ALBUM_ENTRY
        )

        def holds() -> sweep_waiting.HoldsRead:
            """Every provider's hold, fresh, where and when the child will read them."""
            return sweep_waiting.read_holds(os.environ, wiring.now())

        seams = dataclasses.replace(seams, changed=changed, held=held, holds=holds)
        report = sweep_run.sweep(library, surveys, ledger, options, seams)
    print(
        sweep_report.format_sweep(
            report, library=library, options=options, undo_prog=album_prog(words)
        ),
        end="",
    )
    if report.stop is not sweep_run.StopReason.FINISHED:
        _print_resume(args, library, words)
    errors = sweep_changed.list_errors(changed, held)
    for error in errors:
        _notice(f"{' '.join(words)}: {error}")
    if errors:
        return sweep_run.EXIT_USAGE
    return sweep_run.sweep_exit_code(report)


def _print_resume(args: argparse.Namespace, library: Path, words: Words) -> None:
    """Print the command that resumes this run, named by *words* when dispatched."""
    prog = sweep_report.resume_prog(words, _PROG_FILE)
    print(
        sweep_report.resume_line(prog, library, build_options(args), args.changed_list)
    )


def album_prog(words: Words) -> Words:
    """The album command's words as the user ran this one: `lyricsmith album`, else album.py."""
    return sweep_report.album_prog(words, ALBUM_ENTRY.name)


_MODES: dict[str, Callable[[argparse.Namespace, Path, Wiring, Words], int]] = {
    MODE_SURVEY: _survey_mode,
    MODE_RUN: _run_mode,
    MODE_ACCEPT: functools.partial(
        sweep_accept.accept_mode,
        prog_file=_PROG_FILE,
        notice=_notice,
        walk=functools.partial(contained_albums, on_skip=_skipped, on_linked=_linked),
    ),
    MODE_SILENCE: functools.partial(sweep_silence.silence_mode, prog_file=_PROG_FILE),
    MODE_FIX_TEXT: _run_mode,
    **sweep_more_modes.RUNNERS,
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
    sweep_more_modes.refuse(parser, args)
    refuse_misplaced(parser, args)
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
        if args.mode in CHILD_MODES:
            _print_resume(args, library, words)
        return sweep_run.EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover - the CLI entry point; main() is tested
    sys.exit(main())
