"""lyricsmith: one command for the lyrics tools -- album, sweep, transcribe and sync.

A thin dispatcher. Its parser knows only the command name and --version. Everything after
the command goes untouched to that tool's main(), whose own parser and companion doc stay
the authority on its options. Each tool still runs alone as <command>/<command>.py. Only
the chosen tool is imported, so `lyricsmith album` never loads transcribe's code.

Importing it puts the shared libraries the project's ``pyproject.toml`` names on
``sys.path``, through the manifest reader in ``lyricsmith_project/``, so it
imports them by bare name wherever the project is moved.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

_HERE = Path(__file__).resolve().parent
# The project's manifest reader, in its own folder beside this file: it puts the shared
# libraries the project's pyproject.toml names on sys.path, or fails naming the
# one that is missing (lyricsmith_project/lyricsmith_project.md).
_READER_DIR = str(_HERE / "lyricsmith_project")
# Moved first on sys.path, so no same-named module can stand in for the reader.
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

# Run as a script, a failure exits 2 naming it; imported, it raises.
lyricsmith_project.add_shared_libraries(exit_on_failure=__name__ == "__main__")

import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above

#: Each command, and the tool folder (and entry module) that answers it.
COMMANDS: Final[Mapping[str, str]] = MappingProxyType(
    {"album": "album", "sweep": "sweep", "transcribe": "transcribe", "sync": "sync"}
)
#: Each command's one line in the dispatcher's help; the tool's own --help says the rest.
_SUMMARIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "album": "find an album's lyrics, embed them and check them; strip them; "
        "undo a run",
        "sweep": "run album over every album of a library holding a track "
        "without synced lyrics",
        "transcribe": "transcribe audio offline into a timed .lrc and a plain .txt",
        "sync": "time a track's untimed lyrics against its audio, offline, into a .lrc",
    }
)
#: The program name the dispatcher hands a tool is this word, then the command's: the
#: project's command name, the documented contract, and not the entry file's name.
_PROG: Final = "lyricsmith"


class ToolMain(Protocol):
    """A tool's main: its arguments and the program name to show; its exit code."""

    def __call__(
        self, argv: Sequence[str] | None = ..., *, prog: str | None = ...
    ) -> int:
        """Run the tool with *argv*, naming itself *prog*; return the exit code."""


def _load_main(command: str) -> ToolMain:
    """Import the tool folder's entry module by its path and return its main."""
    folder = str(_HERE / COMMANDS[command])
    # Inserted once: a second load in one process must not stack copies on sys.path.
    if folder not in sys.path:
        sys.path.insert(0, folder)
    module = importlib.import_module(COMMANDS[command])
    tool: ToolMain = module.main
    return tool


def _parser() -> argparse.ArgumentParser:
    """The dispatcher's own CLI: the command, and --version.

    Its own name is argparse's, argv[0]'s file name, so its usage and epilog read
    true whatever the entry file is called or run as.
    """
    parser = stdio_utf8.EscapingArgumentParser(
        description="Find, check, transcribe and time an album's lyrics.",
        epilog=(
            "Run `%(prog)s COMMAND --help` for a command's own options. "
            "Prerequisites: the Python release that requires-python names, and the "
            "pinned [project].dependencies, both in the pyproject.toml beside this "
            "file; a command's own --help names anything more it needs."
        ),
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {lyricsmith_project.project_version()}",
    )
    # A subcommand rather than a positional argument: the command names a tool, the way
    # `git log` does, and no value is typed into it. Each sub-parser has no options of
    # its own and no --help, so nothing after the command could be read here even if
    # it reached this parser.
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name in COMMANDS:
        commands.add_parser(name, add_help=False, help=_SUMMARIES[name])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the chosen tool with the remaining arguments and return its exit code."""
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    args = list(sys.argv[1:] if argv is None else argv)
    # Only the first argument is the dispatcher's: a tool's own options, --help among
    # them, are never read here, so `lyricsmith album --help` is album's help.
    namespace = _parser().parse_args(args[:1])
    try:
        tool = _load_main(namespace.command)
    except lyricsmith_project.SharedLibraryError as error:
        # Imported, not run, the tool raises rather than exits: exit 2 as it would.
        lyricsmith_project.exit_with(error)
    return tool(args[1:], prog=f"{_PROG} {namespace.command}")


if __name__ == "__main__":  # pragma: no cover - the CLI entry point; main() is tested
    sys.exit(main())
