#!/usr/bin/env python3
"""Time a track's untimed lyrics against its own audio, offline, into a labeled .lrc.

The entry point of the sync tool. Its private modules live in src/ beside it; the album
tool's modules, whose rules decide what a track's sidecars hold, the transcribe tool's,
whose model fetch and bounded runner this tool shares, and the sweep tool's, whose album
runner --embed shares, are found beside it by the upward search below. See sync.md.
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
# The shared libraries, and the sibling tools, sit directly under the project root,
# this tool folder's parent.
# See stdio_utf8/stdio_utf8.md, "How this project's tools import it", for why
# each is ONE fixed candidate rather than a search up the ancestors, and why a
# missing copy fails its import below by name.
sys.path += [
    str(PROJECT_ROOT.parent / name)
    for name in ("stdio_utf8", "help_formatter", "album", "transcribe", "sweep")
]

import album  # noqa: E402 - import follows the sys.path bootstrap above

# Imported for its own bootstrap only: it puts its src/, where --embed's album runner
# lives, on the path.
import sweep  # noqa: E402, F401 - follows the bootstrap above; used for its path only

# transcribe.py puts its own src/ on the path, so it comes before the at_ modules.
import transcribe  # noqa: E402 - import follows the sys.path bootstrap above
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
from at_modes import say  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import EXIT_SETUP, Options, SetupError  # noqa: E402 - follows the bootstrap

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
  each album that gained one; without it, the summary prints that command.

Exit codes:
  0  every track timed, re-timed, skipped, or left low-confidence for timing by
     hand; under fetch-models, every model present or downloaded
  1  a track's text, alignment or write failed, a folder could not be read, or an
     --embed album run failed, was refused or did not finish, or a written track
     could not be embedded; under fetch-models, any model failed
  2  a setup problem, found before any track was touched
"""


def build_parser(model_dir: Path, prog: str | None = None) -> argparse.ArgumentParser:
    """The command line, every argument a named option."""
    parser = transcribe.TerminalSafeArgumentParser(
        prog=os.path.basename(sys.argv[0]) if prog is None else prog,
        description="Time a track's untimed lyrics against its own audio, offline, into a .lrc.",
        formatter_class=transcribe.HelpFormatter,
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
        default="mp3",
        help="comma-separated, matched case-insensitively [run]",
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
    """--extensions as lowercase names without the dot; refused when empty or odd."""
    names = frozenset(part.strip().lower().lstrip(".") for part in text.split(","))
    names -= {""}
    if not names or not all(EXTENSION.fullmatch(name) for name in names):
        raise SetupError(
            f"--extensions must name extensions such as mp3,flac: {text!r}"
        )
    return names


def resolve_options(namespace: argparse.Namespace) -> Options:
    """The parsed options; --path is required in run mode."""
    if namespace.mode == MODE_RUN and namespace.path is None:
        raise SetupError("--path is required in run mode")
    return Options(
        mode=namespace.mode,
        path=namespace.path,
        extensions=_parse_extensions(namespace.extensions),
        embed=namespace.embed,
        overwrite=namespace.overwrite,
        dry_run=namespace.dry_run,
        model_dir=namespace.model_dir,
    )


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
        version = transcribe.read_tool_version(PROJECT_ROOT.parent)
        return sy_modes.run_mode(
            options, seams or sy_modes.real_seams(ALBUM_ENTRY), tool_version=version
        )
    except SetupError as error:
        say(f"{parser.prog}: {error}", sys.stderr)
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover - the CLI entry, run as a subprocess
