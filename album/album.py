"""lyricsmith album: find an album's lyrics, embed them, check them, and undo a run.

The entry point of the tool folder. The end summary goes to stdout alone, progress and
notices to stderr: `_notice` here writes every notice, handed to each mode, and each
mode, here or in the module that holds it, prints its own summary. Importing it puts
the folder's private modules, under ``src/``, on ``sys.path``, resolved from this
file's own location, then the shared libraries the project's ``pyproject.toml`` names,
through the manifest reader at the project root (``lyricsmith_project/``), so
everything imports by bare name wherever the project is moved. A private module that
needs a shared library first imports `lyrics_located`, which makes the same call, so
each module imports alone.

`main` parses the options, then hands the chosen mode to the orchestrator with the
real stages bound (`real_wiring`); a test passes its own `Wiring` instead. No logging
handler is installed anywhere: the provider adapters capture the libraries' log
records themselves, and that relies on the root logger keeping its default level.
"""

from __future__ import annotations

import argparse
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

import lyrics_accept_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fix_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_only  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_provider_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_strip_step  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_undo_mode  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_exit import EXIT_CANNOT_RUN as EXIT_CANNOT_RUN  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_exit import EXIT_INTERRUPTED as EXIT_INTERRUPTED  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_exit import EXIT_OK as EXIT_OK  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_exit import EXIT_RECORD_NOT_WRITTEN as EXIT_RECORD_NOT_WRITTEN  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_exit import EXIT_TRACK_ERRORS as EXIT_TRACK_ERRORS  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import LyricsError  # noqa: E402 - follows the bootstrap
from lyrics_records import GeniusState  # noqa: E402 - follows the bootstrap
from lyrics_wiring import Wiring as Wiring  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_wiring import real_wiring as real_wiring  # noqa: E402 - import follows the sys.path bootstrap above

# The modes, the default language and the language-code check live with the options;
# each is named here too, as callers and tests reach them through the entry point.
from lyrics_options import (  # noqa: E402 - follows the bootstrap
    DEFAULT_LANG,
    MODE_ACCEPT,
    MODE_CHECK,
    MODE_COMPARE,
    MODE_FIX_TEXT,
    MODE_PRUNE,
    MODE_RUN,
    MODE_STRIP,
    MODE_UNDO,
)
from lyrics_options import iso_639_2 as iso_639_2  # noqa: E402 - re-exported

#: The per-call deadline every provider call runs under, and the Genius step's own.
#: The public re-export of `lyrics_fetch.PROVIDER_DEADLINE_S`, which the mode
#: modules use directly.
DEADLINE_S = lyrics_fetch.PROVIDER_DEADLINE_S

# --- The options ---------------------------------------------------------------------


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """The CLI: every argument a named option, every default shown.

    *prog* names the program in the help and every notice; argparse's own, the entry
    file's name, when None.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Find an album's lyrics, embed them, check them; strip them; "
        "fix a wrong text; undo a run.",
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=lyrics_help.EPILOG,
    )
    _add_album_options(parser)
    _add_run_options(parser)
    return parser


def _add_album_options(parser: argparse.ArgumentParser) -> None:
    """The options every mode takes, and the ones that pick a run or a track."""
    parser.add_argument(
        "--album",
        required=True,
        type=Path,
        help="The album folder; every MP3 under it is a track.",
    )
    parser.add_argument(
        "--mode",
        choices=(
            MODE_RUN,
            MODE_CHECK,
            MODE_UNDO,
            MODE_STRIP,
            MODE_COMPARE,
            MODE_PRUNE,
            MODE_ACCEPT,
            MODE_FIX_TEXT,
        ),
        default=MODE_RUN,
        help="run: back up, fetch, embed and check. check: re-check a run's tracks. "
        "undo: put a run's backup back. strip: back up, then remove every track's "
        "lyrics frames and sidecars. compare: ask each provider for every "
        "untimed-only track and report whether any agrees with its lyrics; writes "
        "nothing. prune: remove the run folders nothing needs. accept: keep the "
        "album as it is and report what removing every run's backup would lose; "
        "with --apply, remove them. fix-text: for each track the --heard-dir "
        "store holds a transcript of, write the first text that transcript sings, "
        "the album's other untimed texts tried before the providers' answers; with "
        "none, remove the wrong text, listed as wrong text removed.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        # None, not False, when omitted, here and on every on/off flag below: the
        # formatter hides a None default, and a flag's help already says what giving
        # it does. Each reader that keeps the value makes it a bool first.
        default=None,
        help="accept only: record each run's manifest outside the album, then "
        "remove the album's .lyricsmith folder, every backup and undo with it; "
        "a report only when omitted.",
    )
    parser.add_argument(
        "--track",
        type=Path,
        default=None,
        help="run, strip, check, undo, compare and fix-text: one MP3 under --album, "
        "relative to the "
        "working directory; every track when omitted.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="check and undo only: the run to read; the newest when omitted.",
    )
    parser.add_argument(
        "--changed-list",
        type=Path,
        default=None,
        help="run, strip, undo and fix-text: write the absolute path of every MP3 this "
        "saved to "
        "this file, one per line, for a library app or script that rereads those "
        "files' tags; refused with --dry-run; no list when omitted.",
    )
    parser.add_argument(
        "--heard-dir",
        type=Path,
        default=None,
        help="fix-text only, and required there: the transcript store sync "
        "--heard-dir wrote, one record per track whose untimed text it found not "
        "sung; the fix only reads it, and keeps every record.",
    )


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    """The options that shape a run."""
    parser.add_argument(
        "--lang",
        type=iso_639_2,
        default=None,
        help="run, compare and fix-text only: the ISO 639-2 code whose frames rank first among "
        "a track's own, the frames compare may judge included; a run also writes "
        "it into new lyrics frames; "
        f"{DEFAULT_LANG} when omitted.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="run, compare and fix-text only: a config file holding the Genius key, the [no_lyrics] "
        "genre list and the [musicbrainz] contact, the only place any is read, under "
        "--offline and --no-genius too; the environment and the default config paths "
        "when omitted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="run, prune and fix-text only: report what would happen; write nothing "
        "at all.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=None,
        help="run only: no network; only sidecars beside the MP3s are used.",
    )
    parser.add_argument(
        "--no-genius",
        action="store_true",
        default=None,
        help="run, compare and fix-text only: skip the Genius step. "
        + lyrics_help.terms_note("--no-genius"),
    )
    parser.add_argument(
        "--no-netease",
        action="store_true",
        default=None,
        help="run, compare and fix-text only: skip both NetEase steps. "
        + lyrics_help.terms_note("--no-netease"),
    )
    parser.add_argument(
        "--no-bandcamp",
        action="store_true",
        default=None,
        help="run, compare and fix-text only: skip the Bandcamp step, which looks an album up on "
        "MusicBrainz and reads its Bandcamp page. "
        + lyrics_help.terms_note("--no-bandcamp"),
    )
    parser.add_argument(
        "--no-youtube-music",
        action="store_true",
        default=None,
        help="run, compare and fix-text only: skip the YouTube Music steps. "
        + lyrics_help.terms_note("--no-youtube-music"),
    )
    parser.add_argument(
        "--no-tekstowo",
        action="store_true",
        default=None,
        help="run, compare and fix-text only: skip the Tekstowo step, the last untimed source. "
        + lyrics_help.terms_note("--no-tekstowo"),
    )
    parser.add_argument(
        # A literal, as every option here is, so the companion doc's consistency check
        # reads it; `lyrics_options.ONLY_FLAG`, which the messages use, must match it.
        "--only-providers",
        type=lyrics_only.parse_only_providers,
        default=None,
        metavar="NAME[,NAME...]",
        help="run, compare and fix-text only: ask only the providers named, one comma-separated "
        f"value of {', '.join(lyrics_only.PROVIDER_NAMES)}; a provider left out is "
        "not asked and records nothing, and one also turned off by its --no-* "
        "option stays off. MusicBrainz is asked only by the Bandcamp step. Refused "
        "with --offline; every provider when omitted.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        default=None,
        help="run only: ask the providers even where a sidecar would serve a track; "
        "an accepted answer replaces its old frames and sidecars, and a track with "
        "none keeps them. An answer unlike the old frames is not accepted, nor an "
        "untimed one for a track whose lyrics are synced. "
        "Refused with --offline, --refetch-untimed and --refetch-implausible.",
    )
    parser.add_argument(
        "--refetch-untimed",
        action="store_true",
        default=None,
        help="run only: ask the providers again for each track whose lyrics are "
        "untimed only, as though it held none; any answer replaces its old frames "
        "and sidecars, however unlike them, and a track with none keeps them. "
        "Refused with --fresh and --offline.",
    )
    parser.add_argument(
        "--refetch-implausible",
        action="store_true",
        default=None,
        help="run only: ask the providers again for each track whose serving lyrics "
        "are implausible (synced lines past the audio's end, or more words a second "
        "than any song), as though it held none; a plausible answer replaces them, "
        "and a track with none has them removed, backed up for undo. Combines with "
        "--refetch-untimed; refused with --fresh and --offline.",
    )


# --- The modes -----------------------------------------------------------------------


def _notice(text: str) -> None:
    """One line of progress or notice, on stderr, its control characters made visible."""
    print(stdio_utf8.spell_unsafe(text), file=sys.stderr)


def _run_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Run the album, or plan it for a dry run, and print the summary."""
    return lyrics_provider_modes.run_mode(args, wiring, prog, _notice)


def _compare_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Compare each untimed-only track's lyrics with the providers' answers; write nothing."""
    return lyrics_provider_modes.compare_mode(args, wiring, prog, _notice)


def _fix_text_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Replace each wrong untimed text the --heard-dir store names, or remove it."""
    return lyrics_fix_mode.fix_text_mode(args, wiring, prog, _notice)


def _strip_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Back up every track, or the one --track names, strip each, and print the summary.

    No provider is asked.

    The manifest records the default language and no Genius key: a strip uses
    neither, and a manifest holds both.
    """
    album, found = lyrics_provider_modes.discover(args, _notice)
    tracks = lyrics_album.track_selection(album, found, args.track)
    options = lyrics_run.RunOptions(DEFAULT_LANG, DEADLINE_S, GeniusState.ABSENT)
    report = lyrics_run.run_album(
        album,
        tracks,
        {},
        options,
        wiring.stages,
        acquire_lock=wiring.acquire_lock,
        started=wiring.now(),
        progress=_notice,
        step=lyrics_strip_step.strip_step,
    )
    with lyrics_exit.report_writes():
        written = lyrics_changed.write_changed_list(
            args.changed_list,
            lambda: lyrics_changed.run_changed(album, report.tracks),
            _notice,
        )
        command = args.command_prefix
        summary = lyrics_summary.format_strip(
            report, album=album, prog=prog, command=command
        )
        print(summary, end="")
        return lyrics_exit.report_exit(report, prog, written, _notice)


def _check_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Re-check a run's tracks and print the report; read only."""
    album = lyrics_album.resolve_album(args.album)
    track = (
        None if args.track is None else lyrics_album.album_relative(album, args.track)
    )
    report = lyrics_modes.check_run(
        album, run_id=args.run_id, track=track, stages=wiring.stages
    )
    with lyrics_exit.report_writes():
        print(lyrics_summary.format_check(report, album=album, prog=prog), end="")
    return EXIT_TRACK_ERRORS if report.has_errors else EXIT_OK


def _undo_mode(args: argparse.Namespace, wiring: Wiring, prog: str) -> int:
    """Undo a run, or one track of it, and print the report."""
    album = lyrics_album.resolve_album(args.album)
    track = (
        None if args.track is None else lyrics_album.album_relative(album, args.track)
    )
    report = lyrics_modes.undo_album(
        album,
        run_id=args.run_id,
        track=track,
        stages=wiring.stages,
        acquire_lock=wiring.acquire_lock,
    )
    with lyrics_exit.report_writes():
        return lyrics_undo_mode.report_undo(args, wiring, prog, album, report, _notice)


_MODES: dict[str, Callable[[argparse.Namespace, Wiring, str], int]] = {
    MODE_RUN: _run_mode,
    MODE_CHECK: _check_mode,
    MODE_UNDO: _undo_mode,
    MODE_STRIP: _strip_mode,
    MODE_COMPARE: _compare_mode,
    MODE_PRUNE: lyrics_prune_mode.prune_mode,
    MODE_ACCEPT: lyrics_accept_mode.accept_mode,
    MODE_FIX_TEXT: _fix_text_mode,
}


def main(
    argv: Sequence[str] | None = None,
    *,
    wiring: Wiring | None = None,
    prog: str | None = None,
) -> int:
    """Parse the options and run the chosen mode; return the exit code.

    *wiring* is the real one unless a test passes its own. *prog* is the program name
    the help, the report header and the printed commands show: ``lyricsmith album``
    when the lyricsmith command runs this tool, the entry file's name when None.
    """
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    lyrics_options.refuse_misplaced(parser, args)
    # Not an option: how a printed command names this tool, derived once, here.
    args.command_prefix = lyrics_summary.command_prefix(parser.prog, prog)
    try:
        wired = wiring or real_wiring()
        return _MODES[args.mode](args, wired, parser.prog)
    # The could-not-run errors, each raised before anything beside an MP3 changed, and
    # an OS error at an album-level step (the lock, the backup, a manifest read): each
    # track's own OS errors are caught at its boundary and never reach here.
    except (LyricsError, OSError) as error:
        lyrics_exit.last_notice(f"{parser.prog}: {error}", _notice)
        return EXIT_CANNOT_RUN
    # Once the work began, a progress line or the summary that cannot be written: the
    # run was made, and its manifest holds what it changed.
    except lyrics_exit.ReportNotWrittenError as error:
        return lyrics_exit.lost_report_exit(error, parser.prog, _notice)
    except KeyboardInterrupt:
        _notice(f"{parser.prog}: interrupted")
        return EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover - the CLI entry point; main() is tested
    sys.exit(main())
