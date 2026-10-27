"""The sweep's options, past parsing: the help's epilog, the refusals, the options built.

`EPILOG` is the help's closing text (prerequisites, output, exit codes);
`refuse_misplaced` exits 2 through the parser when the survey is given an option only a
run uses (``--pause-seconds`` and ``--stop-after`` at any value but their default),
accept any option but --library and --apply, silence any but --library, --limit and
--resume, survey or run --apply, any mode but silence --resume, or a refetch is given
with --offline, or --owe-earlier-runs with --restart or --offline; `build_options` turns
the parsed command line into the sweep's `SweepOptions`. The options themselves are
declared in the entry point, beside its parser. The album tool's exit codes come from
its own `lyrics_exit`, not its entry point, so nothing here imports either entry point.
Nothing here prints.
"""

from __future__ import annotations

import argparse

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
import sweep_child
import sweep_report
import sweep_run
from lyrics_exit import EXIT_OK, EXIT_RECORD_NOT_WRITTEN

MODE_SURVEY, MODE_RUN, MODE_ACCEPT = "survey", "run", "accept"
MODE_SILENCE = "silence"
#: The options only a run uses, refused by the survey rather than silently ignored.
_RUN_ONLY = (
    ("offline", "--offline"),
    *sweep_child.SKIP_OPTIONS,
    ("limit", "--limit"),
    ("restart", "--restart"),
    ("changed_list", sweep_child.CHANGED_LIST_OPTION),
)

#: The options accept refuses beside the run-only ones: each is a survey's or a run's,
#: and accept takes every run of every album, whatever the language or pass.
_NOT_ACCEPT = (
    ("lang", "--lang"),
    ("refetch_untimed", "--refetch-untimed"),
    ("refetch_implausible", "--refetch-implausible"),
    ("owe_earlier_runs", "--owe-earlier-runs"),
)
#: The option only the silence pass takes, refused by every other mode.
_SILENCE_ONLY = (("resume", "--resume"),)
#: What accept and silence each refuse: every option but their own. Silence takes
#: --limit, and --apply is accept's.
_REFUSED_BY = {
    MODE_ACCEPT: (*_RUN_ONLY, *_NOT_ACCEPT),
    MODE_SILENCE: (
        *(option for option in _RUN_ONLY if option[0] != "limit"),
        *_NOT_ACCEPT,
        ("apply", "--apply"),
    ),
}
#: The options --owe-earlier-runs is refused with: a restart already asks every
#: provider of every album, and an offline run asks none.
_NOT_OWING = (("restart", "--restart"), ("offline", "--offline"))
#: The run-only options with a real default: every mode but a run refuses any other
#: value. A value equal to the default cannot be told from no option, and is ignored.
_DEFAULTED = (("pause_seconds", "--pause-seconds"), ("stop_after", "--stop-after"))


EPILOG = f"""\
Prerequisites:
  The album tool beside this one, with the project's dependencies installed
  (its pyproject.toml's [project].dependencies); the album tool's own prerequisites
  apply to every album a run visits. The library is a folder of Artist/Album
  folders. A run or accept needs a POSIX system (macOS, Linux); one run or accept
  per library at a time.

Output:
  A survey prints each album's synced, untimed-only and no-lyrics tracks, the totals,
  each track serving implausible lyrics under --refetch-implausible, the albums a run
  would visit, and those the ledger holds as done, which a run passes over; it only
  reads the ledger, writes nothing and contacts nothing. A run prints a table of the
  albums it visited, its totals, each album's undo command, and, when it stopped
  early, the command that resumes it; progress and the album tool's output go to
  stderr. Each album visited is recorded in a ledger, one per library, under
  $XDG_STATE_HOME/lyricsmith/sweep/ or ~/.local/state/lyricsmith/sweep/; a run skips
  done albums unless --restart, and asks an owed album only the providers it owes,
  passing over (and listing apart) one whose whole debt waits on a cool-down or an
  open breaker;
  with --owe-earlier-runs, a done album whose line predates the owed outcome owes
  the refusing providers it names and any its run's cool-downs left owing.
  Accept prints, for each album holding runs, the album tool's accept report (its
  tracks at risk, its runs, what goes), then one summary: albums cleared, refused by
  reason, holding at-risk tracks. Without --apply it removes nothing; with it each
  album's manifests are recorded under $XDG_STATE_HOME/lyricsmith/accepted/ or
  ~/.local/state/lyricsmith/accepted/ and its .lyricsmith folder removed. It takes
  the ledger's lock and writes no ledger line. Run it without --apply first.
  Silence needs ffmpeg on PATH. It measures every track of every album, whatever its
  lyrics, writing nothing in the library, and appends each album's silent and
  unmeasured tracks, judged, to a list under $XDG_STATE_HOME/lyricsmith/silence/ or
  ~/.local/state/lyricsmith/silence/, then writes the whole list as an HTML page
  beside it; it prints counts by verdict, the list's path and the page's, and, when
  albums are left, the command that resumes into the same list.

Exit codes:
  0    finished: every album visited ended done (or offline), the album tool exiting {EXIT_OK};
       accept: every album cleared, holding no run, or skipped as a link;
       silence: every track of every album measured and the page written
  1    finished, but an album did not: track errors, refused or owed providers (an
       owed album passed over, waiting on a cool-down or breaker, too), an album
       locked by another run, the per-album bound of {sweep_child.ALBUM_TIMEOUT_S:.0f} seconds, no run
       recorded, an album the sweep could not run (its run folder unreadable, or
       the album tool not started), a run the album tool made but could not record (its exit {EXIT_RECORD_NOT_WRITTEN};
       the run is still tallied and its undo printed), an exit code the album
       tool never gives, or the album tool left running when it would not stop;
       accept: an album refused, or its removal or records failed;
       silence: a track not measured, an album that could not be read, or the
       page that could not be written
  2    a usage problem, a library that is missing or cannot be listed, a ledger that
       cannot be read or written (a run still prints its report so far), another
       sweep already running on the library or its lock not taken, a run on Windows,
       a --changed-list file whose last write failed, an existing one that cannot be
       read as a list, or its scratch folder not made;
       accept: a Python whose rmtree can follow a link;
       silence: no ffmpeg, a --resume list unreadable, malformed or another
       library's, or the list not opened
  3    stopped after --stop-after consecutive albums whose providers only refused;
       accept: a report that could not be written
  130  interrupted (Ctrl-C); the album in hand is recorded, not done;
       silence: every finished album is kept in the list, the page is written
       and the resume command printed"""


def refuse_misplaced(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through *parser* when a mode is given an option it does not use."""
    if args.mode != MODE_SILENCE:
        _refuse_set(parser, args, _SILENCE_ONLY)
    if args.mode == MODE_SURVEY:
        _refuse_set(parser, args, _RUN_ONLY)
    if args.mode != MODE_RUN:
        _refuse_defaulted(parser, args)
    if args.mode in _REFUSED_BY:
        _refuse_set(parser, args, _REFUSED_BY[args.mode])
    elif args.apply:
        parser.error(f"--apply cannot be used with --mode {args.mode}")
    # The album tool refuses the pair itself, so every album would only fail to run.
    for refetch in sweep_report.refetch_options(
        refetch_untimed=args.refetch_untimed,
        refetch_implausible=args.refetch_implausible,
    ):
        if args.offline:
            parser.error(f"{refetch} cannot be used with --offline")
    for attr, flag in _NOT_OWING:
        if args.owe_earlier_runs and getattr(args, attr):
            parser.error(f"--owe-earlier-runs cannot be used with {flag}")


def _refuse_defaulted(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Exit 2 through *parser* when a run-only option with a default is given another."""
    for attr, flag in _DEFAULTED:
        if getattr(args, attr) != parser.get_default(attr):
            parser.error(f"{flag} cannot be used with --mode {args.mode}")


def _refuse_set(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    options: tuple[tuple[str, str], ...],
) -> None:
    """Exit 2 through *parser* naming the first of *options* set for this mode."""
    for attr, flag in options:
        if getattr(args, attr) not in (None, False):
            parser.error(f"{flag} cannot be used with --mode {args.mode}")


def build_options(args: argparse.Namespace) -> sweep_run.SweepOptions:
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
        owe_earlier_runs=args.owe_earlier_runs,
        skip=tuple(
            flag for attr, flag in sweep_child.SKIP_OPTIONS if getattr(args, attr)
        ),
    )
