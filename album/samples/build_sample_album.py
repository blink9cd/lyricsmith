"""Build the album tool's sample: six silent MP3s, a timed sidecar and two untimed ones.

Every file is invented, through the same builders the tests use: the MP3s are silent
MPEG frames tagged with an invented artist and titles, two of them with a stand-up
genre, and the ``.lrc`` and the first ``.txt`` hold invented lines; the second
``.txt`` holds only an LRC header tag, the placeholder a provider may send for a track
with no words. Nothing here is a recording or a real song's words, and the album is
built at run time rather than committed. README.md beside this file walks a dry run,
an offline run, a check and an undo on it, then a strip and its undo, then a one-track
strip, dry run and undo, with the report each one prints.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
# The tool's private modules and its test fixture builders, beside this folder.
sys.path[:0] = [str(_TOOL / "src"), str(_TOOL / "tests")]
# The project's manifest reader, in its own folder under the project root: it puts the
# shared libraries the project's pyproject.toml names on sys.path, or fails naming the
# one that is missing (lyricsmith_project/lyricsmith_project.md).
_PROJECT_DIR = Path(__file__).resolve().parents[2]
_READER_DIR = str(_PROJECT_DIR / "lyricsmith_project")
# Moved first on sys.path, so no same-named module can stand in for the reader.
sys.path[:] = [_READER_DIR, *(entry for entry in sys.path if entry != _READER_DIR)]
import lyricsmith_project  # noqa: E402 - import follows the sys.path bootstrap above

# Run as a script, a failure exits 2 naming it; imported, it raises.
lyricsmith_project.add_shared_libraries(exit_on_failure=__name__ == "__main__")

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

_EXIT_OK, _EXIT_USAGE = 0, 2
#: The invented artist every track is tagged with.
ARTIST = "The Invented Quartet"
#: Long enough that the sidecar's last line, at 25.75 s, passes every timing row.
SECONDS = 30.0
#: The genre the two stand-up tracks are tagged with, one of the built-in list's.
STANDUP = ("Stand-up Comedy",)
#: The placeholder the last track's ``.txt`` holds: an LRC header tag and nothing else.
PLACEHOLDER_TXT = "[au:instrumental]\n"
#: The album: one track with an invented timed sidecar, which an offline run embeds,
#: and two with none, which it reports as not found. Two invented stand-up tracks
#: follow: one holding nothing, which an offline run reports as no lyrics expected,
#: and one with an untimed sidecar, which it embeds and flags. The last track's
#: ``.txt`` holds only a placeholder, which an offline run finds nothing to replace,
#: so it removes it.
TRACKS = (
    TrackSpec(
        "01 The Keeper Counts.mp3",
        SECONDS,
        ARTIST,
        "The Keeper Counts",
        lrc=fixtures.LIGHTHOUSE_LRC,
    ),
    TrackSpec("02 Orchard Wall.mp3", SECONDS, ARTIST, "Orchard Wall"),
    TrackSpec("03 The Mill Bell.mp3", SECONDS, ARTIST, "The Mill Bell"),
    TrackSpec("04 The Long Bit.mp3", SECONDS, ARTIST, "The Long Bit", genres=STANDUP),
    TrackSpec(
        "05 The Short Bit.mp3",
        SECONDS,
        ARTIST,
        "The Short Bit",
        txt=fixtures.ORCHARD_PLAIN,
        genres=STANDUP,
    ),
    TrackSpec(
        "06 The Quiet Reel.mp3",
        SECONDS,
        ARTIST,
        "The Quiet Reel",
        txt=PLACEHOLDER_TXT,
    ),
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Build the album tool's sample album of silent, invented tracks.",
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="The album folder to build; it must be missing or empty.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the album and return the exit code: 0 built, 2 ``--out`` not usable."""
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    # A file at --out is refused too: iterdir() on one raises rather than answering.
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        refusal = "--out must be a missing or empty folder"
        print(f"{parser.prog}: {refusal}", file=sys.stderr)
        return _EXIT_USAGE
    fixtures.build_album(args.out, TRACKS)
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point; main() is tested
    sys.exit(main())
