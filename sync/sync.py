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
EXTENSION = re.compile(r"^[a-z0-9]+$")
EPILOG = """\
Prerequisites:
  the sync install: pip install -r requirements-sync.txt (beside this file), into the
  interpreter that runs this tool; ffmpeg on PATH; and the models, fetched once with
  --mode fetch-models. A run never downloads.

Output:
  <track>.lrc beside each timed track, headed by a [re:] line naming the versions and
  the line [by:machine timed]. With --embed, lyricsmith album --offline then runs on
  each album that gained one, embedding into its MP3s only: another format's .lrc
  stays a sidecar. Without --embed, the summary prints that command.

Exit codes:
  0  every track timed, re-timed, skipped, or left low-confidence for timing by
     hand; under fetch-models, every model present or downloaded
  1  a track's text, alignment or write failed, a folder could not be read, or an
     --embed album run failed, was refused or did not finish, or a written track
     could not be embedded; under fetch-models, any model failed
  2  a setup problem, found before any track was touched
  130 Ctrl-C while timing: the tracks timed so far keep their .lrc, the footer
     names the album command for each, and --embed is not run
"""


def build_parser(model_dir: Path, prog: str | None = None) -> argparse.ArgumentParser:
    """The command line, every argument a named option."""
    parser = transcribe.TerminalSafeArgumentParser(
        prog=os.path.basename(sys.argv[0]) if prog is None else prog,
        description="Time a track's untimed lyrics against its own audio, offline, into a .lrc.",
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_RUN, MODE_FETCH),
        default=MODE_RUN,
        help="run times tracks; fetch-models downloads the pinned models [all modes]",
    )
    parser.add_argument(
        "--path",
        type=Path,
        help="one track, or a folder walked recursively [run; required there]",
    )
    parser.add_argument(
        "--extensions",
        default=DEFAULT_EXTENSIONS,
        help="comma-separated, matched case-insensitively; each one ffmpeg has an audio "
        "demuxer for here, not lrc or txt [run]",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="then run lyricsmith album --offline on each album that gained a .lrc [run]",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="re-time a track whose .lrc this tool wrote [run]",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the setup check and each track's plan; time nothing [run]",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=model_dir,
        help="where fetch-models writes and a run reads [all modes]",
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
    """The parsed options; --path is required in run mode.

    --path and --model-dir are made absolute against the working folder, links and dots
    kept, as the transcribe tool makes its own, so a relative name such as
    ``http:x.mp3`` or ``pipe:0`` never reaches ffmpeg, which would read it as a protocol.
    """
    if namespace.mode == MODE_RUN and namespace.path is None:
        raise SetupError("--path is required in run mode")
    return Options(
        mode=namespace.mode,
        path=None if namespace.path is None else namespace.path.absolute(),
        extensions=_parse_extensions(namespace.extensions),
        embed=namespace.embed,
        overwrite=namespace.overwrite,
        dry_run=namespace.dry_run,
        model_dir=namespace.model_dir.absolute(),
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
    parser = build_parser(default_dir, prog)
    try:
        options = resolve_options(parser.parse_args(argv))
        if options.mode == MODE_FETCH:
            return sy_modes.fetch_mode(options, opener)
        version = lyricsmith_project.project_version()
        return sy_modes.run_mode(
            options,
            seams or sy_modes.real_seams(ALBUM_ENTRY),
            tool_version=version,
            prog=command_words(parser.prog, prog),
        )
    except SetupError as error:
        say(f"{parser.prog}: {error}", sys.stderr)
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover - the CLI entry, run as a subprocess
