#!/usr/bin/env python3
"""Time a track's untimed lyrics against its own audio, offline, into a labeled .lrc.

The entry point of the sync tool. Its private modules live in src/ beside it; the album
tool's modules, whose rules decide what a track's sidecars hold, the transcribe tool's,
whose model fetch and bounded runner this tool shares, and the sweep tool's, whose album
runner --embed shares, are put on the path below from their fixed folders under the
project root, with the shared libraries the project's pyproject.toml names. See sync.md.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
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
# The sibling tools, each a fixed folder under the project root, each added once, and
# each checked to be where its entry module loads from before any is imported.
_SIBLINGS = ("album", "transcribe", "sweep")
sys.path += [
    str(_PROJECT_DIR / name)
    for name in _SIBLINGS
    if str(_PROJECT_DIR / name) not in sys.path
]
for _name in _SIBLINGS:
    lyricsmith_project.confirm_from(
        _name, _PROJECT_DIR / _name, exit_on_failure=__name__ == "__main__"
    )

import album  # noqa: E402 - import follows the sys.path bootstrap above

# Imported for its own bootstrap only: it puts its src/, where --embed's album runner
# lives, on the path.
import sweep  # noqa: E402, F401 - follows the bootstrap above; used for its path only

# transcribe.py puts its own src/ on the path, so it comes before the at_ modules.
import transcribe  # noqa: E402 - import follows the sys.path bootstrap above
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
import sy_hear  # noqa: E402 - import follows the sys.path bootstrap above
import sy_hear_options  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
from at_modes import say  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import (  # noqa: E402 - import follows the sys.path bootstrap above
    DEFAULT_EXTENSIONS,
    DEMUXERS,
    EXIT_SETUP,
    LRC_SUFFIX,
    TXT_SUFFIX,
    Options,
    SetupError,
)

#: The album tool's entry point, which --embed runs on each album that gained a .lrc.
ALBUM_ENTRY = Path(album.__file__).resolve()

MODE_RUN, MODE_FETCH = "run", "fetch-models"
MODE_HEAR = sy_hear_options.MODE_HEAR
EXTENSION = re.compile(r"^[a-z0-9]+$")
EPILOG = """\
Prerequisites:
  the sync install: pip install -r requirements-sync.txt (beside this file), into the
  interpreter that runs this tool; ffmpeg on PATH; and the models, fetched once with
  --mode fetch-models. whisper.cpp's whisper-cli on PATH (brew install whisper.cpp)
  and the transcribe tool's two models, fetched once with its own --mode
  fetch-models: each track's vocals are heard before its text is timed. A text the
  check finds not sung is not timed; a track with too few word pairs heard to judge
  is timed, and listed little heard under --by-hand-list. A run never downloads.

Output:
  <track>.lrc beside each timed track, headed by a [re:] line naming the versions and
  the line [by:machine timed]. With --embed, lyricsmith album --offline then runs on
  each album that gained one, embedding into its MP3s only: another format's .lrc
  stays a sidecar. Without --embed, the summary prints that command.
  --mode hear writes no .lrc: it hears each selected track's text and logs the
  verdict, sung, not sung or little heard, in --hearing-log, one file a track and
  whisper model; a text not sung is also kept in --heard-dir and listed on
  --by-hand-list, as a run keeps one.

Exit codes:
  0  every track timed, re-timed, skipped, left low-confidence for timing by hand,
     or left untimed as not sung; under fetch-models, every model present or
     downloaded
  1  a track's text, alignment, hearing or write failed, a --by-hand-list row, a
     --heard-dir record or a --hearing-log record could not be written or removed,
     a folder could not be read, or an --embed album run failed, was
     refused or did not finish, or a written track could not be embedded; under
     fetch-models, any model failed
  2  a setup problem, found before any track was touched, an unreadable or
     malformed --by-hand-list or --tracks-from, or a --heard-dir or
     --hearing-log that is no folder, among them
  130 Ctrl-C while timing: the tracks timed so far keep their .lrc, the footer
     names the album command for each, and --embed is not run; while hearing,
     every hearing logged so far is kept
"""


def build_parser(
    model_dir: Path, whisper_model_dir: Path, prog: str | None = None
) -> argparse.ArgumentParser:
    """The command line, every argument a named option."""
    parser = transcribe.TerminalSafeArgumentParser(
        prog=os.path.basename(sys.argv[0]) if prog is None else prog,
        description="Time a track's untimed lyrics against its own audio, offline, into a .lrc.",
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_RUN, MODE_HEAR, MODE_FETCH),
        default=MODE_RUN,
        help="run times tracks; hear hears their texts without timing them; "
        "fetch-models downloads the pinned models [all modes]",
    )
    parser.add_argument(
        "--path",
        type=Path,
        help="one track, or a folder walked recursively [run, hear; required there]",
    )
    parser.add_argument(
        "--extensions",
        default=DEFAULT_EXTENSIONS,
        help="comma-separated, matched case-insensitively; each one ffmpeg has an audio "
        "demuxer for here, not lrc or txt [run, hear]",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        # None, not False, when omitted, here and on every on/off flag below: the
        # formatter hides a None default, and a flag's help already says what giving
        # it does. Each reader that keeps the value makes it a bool first.
        default=None,
        help="then run lyricsmith album --offline on each album that gained a .lrc [run]",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=None,
        help="re-time a track whose .lrc this tool wrote [run]",
    )
    parser.add_argument(
        "--by-hand-list",
        type=Path,
        help="a list file: each low-confidence or not-sung track, each flagged first "
        "line and each written track too little heard to check is added as its track "
        "ends, and a listed low-confidence or not-sung track is skipped, --overwrite "
        "or not; remove its row to time it again. A hearing lists each text not "
        "sung [run, hear]",
    )
    parser.add_argument(
        "--heard-dir",
        type=Path,
        help="a folder keeping what whisper-cli heard of each track found not sung, "
        "so a track with a record, its text changed or its row removed, is judged "
        "again without hearing it again, and album can replace the text; needs "
        "--by-hand-list [run, hear]",
    )
    parser.add_argument(
        "--hearing-log",
        type=Path,
        help="a folder logging each hearing's verdict and transcript, one file a "
        "track and whisper model; a track logged for its current text is not heard "
        "again [hear; required there]",
    )
    parser.add_argument(
        "--kinds",
        help="comma-separated: which texts to hear, of provider-timed, machine-timed "
        "and untimed; when omitted, provider-timed [hear]",
    )
    parser.add_argument(
        "--tracks-from",
        type=Path,
        help="a list of tracks, one absolute path a line, as album --changed-list "
        "writes it: only these, of the walk of --path, are heard [hear]",
    )
    parser.add_argument(
        "--whisper-model",
        choices=at_models.WHISPER_MODEL_NAMES,
        help="the pinned whisper model to hear with, in --whisper-model-dir; when "
        f"omitted, {at_models.WHISPER_MODEL_NAME}; any other takes neither "
        "--heard-dir nor --by-hand-list [hear]",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="print the setup check and each track's plan; time or hear nothing "
        "[run, hear]",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=model_dir,
        help="where fetch-models writes and a run reads [all modes]",
    )
    parser.add_argument(
        "--whisper-model-dir",
        type=Path,
        default=whisper_model_dir,
        help="the transcribe tool's model folder, where a run reads the whisper "
        "models that hear each track's vocals [run, hear]",
    )
    return parser


def _parse_extensions(text: str) -> frozenset[str]:
    """--extensions as lowercase names without the dot; refused when empty or odd.

    The sidecar suffixes are refused too: a walk would take each .lrc or .txt as a
    track whose own .lrc is the file itself. So is a name `DEMUXERS` does not map,
    since ffmpeg opens a track only with one of the demuxers listed there.
    """
    names = frozenset(part.strip().lower().lstrip(".") for part in text.split(","))
    names -= {""}
    if not names or not all(EXTENSION.fullmatch(name) for name in names):
        raise SetupError(
            f"--extensions must name extensions such as mp3,flac: {text!r}"
        )
    if names & {suffix.lstrip(".") for suffix in (LRC_SUFFIX, TXT_SUFFIX)}:
        raise SetupError(
            f"--extensions {text!r}: lrc and txt are lyrics sidecars, not audio"
        )
    unknown = sorted(names - DEMUXERS.keys())
    if unknown:
        them = "them" if len(unknown) > 1 else "it"
        raise SetupError(
            f"--extensions {', '.join(unknown)}: no audio demuxer is allowed for {them}; "
            f"choose from {', '.join(sorted(DEMUXERS))}"
        )
    return names


def resolve_options(namespace: argparse.Namespace) -> Options:
    """The parsed options; --path is required in run and hear mode.

    --path, both model folders, --by-hand-list and --heard-dir are made absolute
    against the working folder, links and dots kept, as the transcribe tool makes its
    own, so a relative name such as ``http:x.mp3`` or ``pipe:0`` never reaches ffmpeg,
    which would read it as a protocol. An option for another mode is refused
    (`sy_hear_options.refuse_misplaced`).
    """
    if namespace.mode != MODE_FETCH and namespace.path is None:
        raise SetupError(f"--path is required in {namespace.mode} mode")
    sy_hear_options.refuse_misplaced(namespace)
    by_hand = namespace.by_hand_list
    if namespace.mode == MODE_FETCH and by_hand is not None:
        raise SetupError("--by-hand-list is for --mode run or hear only")
    heard = namespace.heard_dir
    if namespace.mode == MODE_FETCH and heard is not None:
        raise SetupError("--heard-dir is for --mode run or hear only")
    if heard is not None and by_hand is None:
        # A not-sung row and its record go together: the row is what skips the track.
        raise SetupError("--heard-dir needs --by-hand-list")
    return Options(
        mode=namespace.mode,
        path=None if namespace.path is None else namespace.path.absolute(),
        extensions=_parse_extensions(namespace.extensions),
        embed=bool(namespace.embed),
        overwrite=bool(namespace.overwrite),
        dry_run=bool(namespace.dry_run),
        model_dir=namespace.model_dir.absolute(),
        whisper_model_dir=namespace.whisper_model_dir.absolute(),
        by_hand_list=None if by_hand is None else by_hand.absolute(),
        heard_dir=None if heard is None else heard.absolute(),
        whisper_model=namespace.whisper_model,
    )


def command_words(display: str, given: str | None) -> tuple[str, ...]:
    """This tool's words in a printed command: the dispatcher's, else this file's name.

    Given, *given* is the dispatcher's ``<dispatcher> sync``; run alone, the entry
    file's own name is used rather than *display*, argv[0]'s, which a test runner or a
    symlink can make something no pasted command would find. The sweep tool names
    itself the same way.
    """
    words = sweep_report.command_words(display, given)
    return sweep_report.resume_prog(words, Path(__file__).name)


def main(
    argv: Sequence[str] | None = None,
    *,
    seams: sy_modes.Seams | None = None,
    opener: at_models.Opener = at_models.https_open,
    prog: str | None = None,
) -> int:
    """Run the command line; the exit code."""
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    default_dir = sy_models.default_model_dir(sys.platform, os.environ, Path.home())
    whisper_dir = at_models.default_model_dir(sys.platform, os.environ, Path.home())
    parser = build_parser(default_dir, whisper_dir, prog)
    try:
        namespace = parser.parse_args(argv)
        options = resolve_options(namespace)
        if options.mode == MODE_FETCH:
            return sy_modes.fetch_mode(options, opener)
        seams = seams or sy_modes.real_seams(ALBUM_ENTRY)
        words = command_words(parser.prog, prog)
        if options.mode == MODE_HEAR:
            hear = sy_hear_options.resolve_hear(namespace)
            return sy_hear.hear_mode(options, hear, seams, prog=words)
        version = lyricsmith_project.project_version()
        return sy_modes.run_mode(options, seams, tool_version=version, prog=words)
    except SetupError as error:
        say(f"{parser.prog}: {error}", sys.stderr)
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover - the CLI entry, run as a subprocess
