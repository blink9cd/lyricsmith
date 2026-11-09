"""Names and messages reach the terminal with their control characters made visible.

An MP3's name, an album's folder name, and any message carrying one are text the tool
did not write: a name holding an escape sequence (a clipboard write, a
hyperlink, a cursor move) would otherwise drive the operator's terminal when a progress
line, a report or a notice prints it. Every such character, C0 (tab included), DEL and
C1, prints as a visible ``\\xNN`` escape instead; every other character, accented, CJK
or emoji, prints as it is. The escape is `stdio_utf8.spell_unsafe`, whose own suite
holds its cases code point by code point; these hold that the album tool uses it. The
manifest keeps the true names. NTFS holds no C0 control in a name, so each mode runs
with the attacks in their C1 forms on every host, and in their C0 forms wherever the
host holds them (a probe, not the platform). The album is built from silent MP3s under the test's own
directory, and the providers answer nothing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import ClassVar

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_recorder  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - reachable through the album fixtures' bootstrap
from lyrics_summary import table as summary_table  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: A clipboard-writing OSC 52 sequence, then a newline that would forge a second line.
_HOSTILE = "01 \x1b]52;c;aGk=\x07\nBell.mp3"
#: The same name as it must print.
_HOSTILE_SHOWN = "01 \\x1b]52;c;aGk=\\x07\\x0aBell.mp3"
#: An album folder name that clears the screen.
_ALBUM = "album\x1b[2J"
_ALBUM_SHOWN = "album\\x1b[2J"
#: The C0 characters above a terminal acts on: any of them in the output means a leak.
_RAW = ("\x1b", "\x07")
#: The same attacks in their C1 forms (OSC, ST and CSI as single code points), with a
#: DEL: NTFS refuses a C0 character in a name but holds these, so they run everywhere.
_HOSTILE_C1 = "01 \x9d52;c;aGk=\x9c\x7fBell.mp3"
_HOSTILE_C1_SHOWN = "01 \\x9d52;c;aGk=\\x9c\\x7fBell.mp3"
_ALBUM_C1 = "album\x9b2J"
_ALBUM_C1_SHOWN = "album\\x9b2J"
_RAW_C1 = ("\x9b", "\x9c", "\x9d", "\x7f")
#: A name no terminal acts on, which prints exactly as it is.
_ORDINARY = "02 Fähre 灯台 🌊.mp3"


class PrintableTest(fixtures.IsolatedCase):
    """The one helper every printed name, path and message passes through."""

    def test_each_printing_module_uses_stdio_utf8s_escape(self) -> None:
        for module in (lyrics_recorder, lyrics_summary):
            with self.subTest(module=module.__name__):
                self.assertIs(module.spell_unsafe, stdio_utf8.spell_unsafe)
        self.assertFalse(hasattr(lyrics_outcomes, "printable"))

    def test_a_right_to_left_override_in_a_name_prints_escaped(self) -> None:
        # "01 ‮gpj.mp3" displays as "01 3pm.jpg": the escape shows the truth.
        line = summary_table(("#", "track"), [("01 ‮gpj.mp3",)], [""])
        self.assertIn("01 \\u202egpj.mp3", "\n".join(line))
        self.assertNotIn("‮", "\n".join(line))


class PrintedNamesTest(cli_fakes.RealWiringCliCase):
    """Every mode's output escapes a hostile name and keeps an ordinary one.

    The names here hold C1 controls and a DEL, which NTFS holds, so this runs on every
    host; `PrintedC0NamesTest` repeats it with C0 controls where the host holds them.
    """

    hostile: ClassVar[str] = _HOSTILE_C1
    hostile_shown: ClassVar[str] = _HOSTILE_C1_SHOWN
    album_name: ClassVar[str] = _ALBUM_C1
    album_shown: ClassVar[str] = _ALBUM_C1_SHOWN
    raw: ClassVar[tuple[str, ...]] = _RAW_C1

    def setUp(self) -> None:
        """An album with a hostile folder name, one hostile track and one ordinary."""
        super().setUp()
        self.album = self.tmp / self.album_name
        tracks = (TrackSpec(self.hostile), TrackSpec(_ORDINARY))
        fixtures.build_album(self.album, tracks)

    def assert_escaped(self, result: cli_fakes.Result, *streams: str) -> None:
        """No raw control character in either stream; *streams* name the hostile one."""
        for raw in self.raw:
            self.assertNotIn(raw, result.stdout + result.stderr)
        output = {"stdout": result.stdout, "stderr": result.stderr}
        for stream in streams:
            self.assertIn(self.hostile_shown, output[stream])
            self.assertIn(_ORDINARY, output[stream])

    def test_the_progress_lines_and_the_run_summary(self) -> None:
        result = self.run_album()
        self.assert_escaped(result, "stdout", "stderr")
        self.assertIn(f"under {self.album_shown}", result.stdout)
        manifest = lyrics_manifest.read_manifest(
            lr.run_folder(self.album, run_fakes.RUN_ID)
        )
        self.assertIn(self.hostile, [track.path for track in manifest.tracks])

    def test_the_dry_run(self) -> None:
        self.assert_escaped(self.run_album("--dry-run"), "stdout")

    def test_the_check_and_undo_reports(self) -> None:
        self.run_album()
        self.assert_escaped(self.run_album("--mode", "check"), "stdout")
        self.assert_escaped(self.run_album("--mode", "undo"), "stdout")

    def test_the_strip_summary(self) -> None:
        self.assert_escaped(self.run_album("--mode", "strip"), "stdout", "stderr")

    def test_a_notice_naming_a_skipped_link(self) -> None:
        (self.album / f"link {self.hostile}").symlink_to(self.album / _ORDINARY)
        result = self.run_album("--dry-run")
        self.assert_escaped(result)
        self.assertIn(f"skipped link {self.hostile_shown}", result.stderr)

    def test_a_notice_carrying_an_error_message(self) -> None:
        self.album = self.tmp / f"missing {self.album_name}"
        result = self.run_album()
        self.assert_escaped(result)
        self.assertIn(f"missing {self.album_shown}", result.stderr)


@unittest.skipUnless(
    fixtures.host_holds_name(_HOSTILE) and fixtures.host_holds_name(_ALBUM),
    "this file system holds no C0 control character in a name",
)
class PrintedC0NamesTest(PrintedNamesTest):
    """The same, the names holding C0 controls and a newline, where the host holds them."""

    hostile = _HOSTILE
    hostile_shown = _HOSTILE_SHOWN
    album_name = _ALBUM
    album_shown = _ALBUM_SHOWN
    raw = _RAW


if __name__ == "__main__":
    unittest.main()
