"""lyricsmith transcribe: offline audio to a labeled .lrc and .txt through whisper.cpp.

The entry point of the project folder. It runs whisper.cpp's ``whisper-cli`` program on
each audio file and never downloads anything during a transcribe run; only
``--mode fetch-models`` reaches the network, and only for the two pinned models.
Importing it puts the private modules, under ``src/``, on ``sys.path``, resolved from
this file's own location, then the shared libraries the project's ``pyproject.toml``
names, through the manifest reader at the project root (``lyricsmith_project/``), so
everything imports by bare name wherever the project is moved.

This module owns the option layer, `build_parser` and `resolve_options`, and `main`,
which dispatches to the two modes in ``src/at_modes.py`` with the project's version
(`lyricsmith_project.project_version`).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

#: The folder holding this file.
PROJECT_ROOT = Path(__file__).resolve().parent
#: The private modules' folder, beside this file.
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

import at_engine  # noqa: E402 - import follows the sys.path bootstrap above
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import at_modes  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
from at_types import (  # noqa: E402 - import follows the sys.path bootstrap above
    EXIT_SETUP,
    LRC_SUFFIX,
    TOOL_LABEL,
    TXT_SUFFIX,
    Options,
    SetupError,
)

#: A --path naming one of these would be its own output (output_paths keeps the stem).
SIDECAR_SUFFIXES = frozenset({LRC_SUFFIX, TXT_SUFFIX})

MODE_TRANSCRIBE = "transcribe"
MODE_FETCH = "fetch-models"

#: `auto`, or two or three lowercase ASCII letters.
LANGUAGE = re.compile(r"^(auto|[a-z]{2,3})$")
#: One extension, already lowered and stripped of its dot.
EXTENSION = re.compile(r"^[a-z0-9]+$")

EPILOG = f"""\
Prerequisites:
  whisper.cpp installed, so whisper-cli is on PATH (brew install whisper.cpp) or
  named by --whisper-cli; then run --mode fetch-models once to download the two
  pinned models into --model-dir. A transcribe run never touches the network.

Output:
  For each audio file, a .lrc and a .txt with the audio's own name, beside it or,
  with --output-dir, in that folder mirroring the tree under --path. The .lrc opens
  with two label lines, [re:{TOOL_LABEL} <version>; whisper.cpp
  <version>; <model>] and [by:machine transcript]; the .txt is left as whisper.cpp
  wrote it.

Exit codes:
  0  every file transcribed, skipped or refused (fetch-models: every model
     present or downloaded)
  1  any file failed (an output pair another file already claims among them),
     or a folder under --path could not be read (fetch-models: any model failed)
  2  a setup error found before any file is touched: no working whisper-cli, a
     missing or unreadable model, a --path that does not exist, cannot be read,
     is not a folder or a regular file, or names a .lrc or .txt, or an unusable
     option value"""


class _Writable(Protocol):
    """Where argparse writes a message: anything with a text ``write``."""

    def write(self, text: str, /) -> object:
        """Write *text*; argparse ignores the result."""


class TerminalSafeArgumentParser(stdio_utf8.EscapingArgumentParser):
    """An EscapingArgumentParser whose messages also spell control characters as escapes.

    argparse quotes a stray argument raw ("unrecognized arguments: ..."), so a hostile
    file name could steer the terminal. Each line is escaped on its own, because
    `at_modes.terminal_safe` spells a newline as an escape too, and escaping the whole
    message would flatten the usage onto one line.

    argparse's color (Python 3.14 and later, at a terminal or under FORCE_COLOR) is
    turned off: its codes would otherwise be escaped into visible text.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build the parser, then turn argparse's color off."""
        super().__init__(*args, **kwargs)
        # An attribute, not the color= keyword: 3.12 has neither, and the keyword would
        # fail there (and fail mypy at python_version 3.12); the attribute is inert.
        self.color = False

    def _print_message(self, message: str, file: _Writable | None = None) -> None:
        """Escape each line of *message*, then write it as the base class would."""
        lines = message.split("\n")
        safe = "\n".join(at_modes.terminal_safe(line) for line in lines)
        super()._print_message(safe, file)


def build_parser(model_dir: Path, prog: str | None = None) -> argparse.ArgumentParser:
    """The CLI: every argument a named option, every default shown.

    *prog* names the program in the help and every notice; argv[0]'s file name when None.
    """
    parser = TerminalSafeArgumentParser(
        prog=os.path.basename(sys.argv[0]) if prog is None else prog,
        description="Transcribe audio offline into a timed .lrc and a plain .txt "
        "with whisper.cpp's whisper-cli; or fetch its two pinned models.",
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_TRANSCRIBE, MODE_FETCH),
        default=MODE_TRANSCRIBE,
        help="transcribe audio, or download the two pinned models [all modes]",
    )
    parser.add_argument(
        "--path",
        type=Path,
        help="one audio file, or a folder walked recursively [transcribe; required there]",
    )
    parser.add_argument(
        "--extensions",
        default="mp3",
        help="comma-separated, matched case-insensitively, for a folder walk [transcribe]",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write the files into this folder, mirroring the tree under --path; "
        "when omitted, beside each file [transcribe]",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        # None, not False, when omitted, here and on every on/off flag below: the
        # formatter hides a None default, and a flag's help already says what giving
        # it does. Each reader that keeps the value makes it a bool first.
        default=None,
        help="replace a .lrc this tool wrote; never a hand-made or provider one [transcribe]",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="print the setup check and each file's planned action; run nothing [transcribe]",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="a two- or three-letter code, or auto [transcribe]",
    )
    parser.add_argument(
        "--model",
        type=Path,
        help="the Whisper model file; when omitted, "
        f"{at_models.WHISPER_MODEL_NAME} in --model-dir [transcribe]",
    )
    parser.add_argument(
        "--vad-model",
        type=Path,
        help="the voice-activity model file; when omitted, "
        f"{at_models.VAD_MODEL_NAME} in --model-dir [transcribe]",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=model_dir,
        help="where fetch-models writes and the model defaults point [all modes]",
    )
    parser.add_argument(
        "--whisper-cli",
        type=Path,
        help="the whisper-cli executable; when omitted, found on PATH [transcribe]",
    )
    return parser


def _parse_extensions(text: str) -> frozenset[str]:
    """'MP3, .m4a' -> {'mp3', 'm4a'}; an empty or malformed list is a setup error."""
    items = {item.strip().lower().lstrip(".") for item in text.split(",")} - {""}
    if not items or not all(EXTENSION.match(item) for item in items):
        raise SetupError(
            f"--extensions {text!r}: give comma-separated extensions such as mp3"
        )
    if items & {suffix.lstrip(".") for suffix in SIDECAR_SUFFIXES}:
        # A walk would take each sidecar as audio whose outputs are the file itself.
        raise SetupError(
            f"--extensions {text!r}: lrc and txt are this tool's outputs, not audio"
        )
    return frozenset(items)


def _check_path(path: Path | None) -> Path:
    """The transcribe --path: given, existing, a folder or a regular file, not a sidecar.

    A file is judged by what a link at it points to (`is_file` follows it): naming a
    linked file is the operator's choice. A FIFO, socket or device is refused, since
    whisper-cli would block on it for its whole bound or read it without end.
    """
    if path is None:
        raise SetupError("--mode transcribe needs --path")
    try:
        exists = path.exists()
        is_dir = exists and path.is_dir()
        is_file = exists and path.is_file()
    except OSError as error:  # 3.12 raises where a folder above is unsearchable
        raise SetupError(f"--path {path} cannot be read: {error}") from error
    if not exists:
        raise SetupError(f"--path {path} does not exist")
    if not is_dir and not is_file:
        raise SetupError(
            f"--path {path} is not a regular file (a FIFO, socket or device)"
        )
    if is_dir and not os.access(path, os.R_OK | os.X_OK):
        # The walk would fail on its very first folder; that is setup, not one file.
        raise SetupError(f"--path {path} cannot be read: no permission to list it")
    if not is_dir and path.suffix.lower() in SIDECAR_SUFFIXES:
        # Its outputs would be the file itself, which --overwrite could then replace.
        raise SetupError(
            f"--path {path} is a .lrc or .txt: name the audio file it belongs to"
        )
    return path


def _absolute(path: Path | None) -> Path | None:
    """*path* made absolute against the working folder, links and dots kept; None stays."""
    return None if path is None else path.absolute()


def resolve_options(namespace: argparse.Namespace) -> Options:
    """Validate the parsed options and fill the model defaults from --model-dir.

    --path, --output-dir and both models are made absolute, so no path handed to
    whisper-cli can start with "-" and be read as one of its options: pathlib turns
    ./-x into -x.
    """
    path = _absolute(namespace.path)
    extensions: frozenset[str] = frozenset()
    if namespace.mode == MODE_TRANSCRIBE:  # fetch-models reads none of these
        path = _check_path(path)
        if not LANGUAGE.match(namespace.language):
            raise SetupError(
                f"--language {namespace.language!r}: give auto or a two- or three-letter code"
            )
        extensions = _parse_extensions(namespace.extensions)
    model_dir = namespace.model_dir
    return Options(
        mode=namespace.mode,
        path=path,
        extensions=extensions,
        output_dir=_absolute(namespace.output_dir),
        overwrite=bool(namespace.overwrite),
        dry_run=bool(namespace.dry_run),
        language=namespace.language,
        model=(namespace.model or model_dir / at_models.WHISPER_MODEL_NAME).absolute(),
        vad_model=(
            namespace.vad_model or model_dir / at_models.VAD_MODEL_NAME
        ).absolute(),
        model_dir=model_dir,
        whisper_cli=namespace.whisper_cli,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    run: at_engine.Runner = at_engine.run_bounded,
    opener: at_models.Opener = at_models.https_open,
    manifest: Sequence[at_models.ModelFile] = at_models.MANIFEST,
    prog: str | None = None,
) -> int:
    """Parse, validate, run the mode; a setup error is exit 2.

    *run*, *opener* and *manifest* are the seams the tests replace: the process runner,
    the download opener and the model manifest. *prog* is the program name the help and
    the notices show: ``lyricsmith transcribe`` when the lyricsmith command runs this
    tool, argv[0]'s file name when None.
    """
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    default_dir = at_models.default_model_dir(sys.platform, os.environ, Path.home())
    parser = build_parser(default_dir, prog)
    try:
        namespace = parser.parse_args(argv)
        options = resolve_options(namespace)
        if options.mode == MODE_FETCH:
            return at_modes.fetch_mode(options, opener, manifest)
        return at_modes.transcribe_mode(
            options,
            run,
            tool_version=lyricsmith_project.project_version(),
            prog=parser.prog,
        )
    except SetupError as error:
        at_modes.say(f"{parser.prog}: {error}", sys.stderr)
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover - the CLI entry, run as a subprocess
