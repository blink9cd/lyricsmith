"""The program name: run as `lyricsmith album`, the help and the report name that command.

Each test drives the entry point with faked wiring, as the command-line suites do, but
with *prog* given the way the lyricsmith dispatcher gives it. A dry run writes nothing
and asks no provider, so the report's header is read with nothing changed. A strip
asks no provider either, so its closing undo line is read from a real run.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: The program name the lyricsmith dispatcher hands the album tool.
_DISPATCHED = "lyricsmith album"


class DispatchedProgTest(cli_fakes.CliCase):
    """`main` given the dispatcher's program name shows it wherever it names itself."""

    def build(self) -> None:
        """A one-track album with a timed sidecar, which a dry run plans to read."""
        fixtures.build_album(
            self.album, (TrackSpec("01 A.mp3", lrc=fixtures.LIGHTHOUSE_LRC),)
        )

    def test_help_names_the_album_command(self) -> None:
        result = self.main("--help", prog=_DISPATCHED)
        self.assertEqual(result.code, 0)
        self.assertTrue(
            result.stdout.startswith(f"usage: {_DISPATCHED} "), result.stdout
        )

    def test_the_report_header_names_the_album_command(self) -> None:
        self.build()
        result = self.run_album("--dry-run", "--offline", prog=_DISPATCHED)
        self.assertEqual(result.code, 0)
        self.assertEqual(
            result.stdout.splitlines()[0], f"{_DISPATCHED} dry run: 1 track under album"
        )

    def test_a_could_not_run_notice_names_the_album_command(self) -> None:
        missing = str(self.tmp / "missing")
        result = self.main("--album", missing, "--mode", "check", prog=_DISPATCHED)
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertTrue(result.stderr.startswith(f"{_DISPATCHED}: "), result.stderr)

    def test_run_alone_the_header_still_names_the_entry_files_stem(self) -> None:
        self.build()
        result = self.run_album("--dry-run", "--offline")
        self.assertEqual(
            result.stdout.splitlines()[0], "album dry run: 1 track under album"
        )


class UndoFooterTest(cli_fakes.RealWiringCliCase):
    """A strip's closing undo line survives a shell, dispatched and run alone."""

    def setUp(self) -> None:
        """An album under a folder whose name holds a space."""
        super().setUp()
        self.album = self.tmp / "an album"
        fixtures.build_album(self.album, (TrackSpec("01 A.mp3"),))

    def undo_words(self, result: cli_fakes.Result) -> list[str]:
        """The undo command on the summary's last line, split as a shell would."""
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        return shlex.split(result.stdout.splitlines()[-1].split("undo: ", 1)[1])

    def test_through_the_dispatcher_it_names_the_two_words(self) -> None:
        result = self.main(
            "--album", str(self.album), "--mode", "strip", prog=_DISPATCHED
        )
        self.assertEqual(
            self.undo_words(result),
            ["lyricsmith", "album", "--album", str(self.album), "--mode", "undo"],
        )

    def test_run_alone_a_spaced_entry_name_stays_one_word(self) -> None:
        result = self.main(
            "--album", str(self.album), "--mode", "strip", argv0="my album.py"
        )
        self.assertEqual(
            self.undo_words(result),
            ["my album.py", "--album", str(self.album), "--mode", "undo"],
        )
