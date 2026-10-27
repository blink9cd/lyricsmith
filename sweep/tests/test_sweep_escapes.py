"""Everything the sweep prints to stdout shows control and layout characters escaped.

An album or library folder's name is text the sweep did not write: one holding an
escape sequence (a color, an OSC 8 hyperlink, a C1 control), a direction override or a
carriage return would drive the terminal, or make one name read as another. So the
survey, the run report and the resume line each pass through `stdio_utf8.spell_unsafe`,
as the album tool's own reports do. Every name here is INVENTED for these tests.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import stdio_utf8  # noqa: E402 - reachable through the entry point's bootstrap
import sweep_accept  # noqa: E402 - import follows the sys.path bootstrap above
import silence_fixtures as sfx  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_page  # noqa: E402 - import follows the sys.path bootstrap above

#: A color, then the C1 control sequence introducer.
_COLOR_ARTIST = "Red\x1b[31mArtist\x9b0m"
#: An OSC 8 hyperlink wrapped around a word; no slash, which would nest folders.
_LINK_ALBUM = "Link \x1b]8;;https:example.invalid\x07here\x1b]8;;\x07"
#: A right-to-left override and a carriage return.
_TURNED_ALBUM = "Turn‮ed\rBack"
#: Any character `spell_unsafe` escapes, but the newline that ends each printed line.
_RAW = re.compile("[\x00-\x09\x0b-\x1f\x7f-\x9f  ‪-‮]")


class SharedEscapeTest(sf.IsolatedCase):
    """Every module that prints a name escapes it with the one shared helper."""

    def test_each_printing_module_uses_stdio_utf8s_escape(self) -> None:
        printing = (
            sweep,
            sweep_accept,
            sweep_report,
            sweep_silence,
            sweep_silence_page,
        )
        for module in printing:
            with self.subTest(module=module.__name__):
                self.assertIs(module.spell_unsafe, stdio_utf8.spell_unsafe)


class EscapedOutputTest(sf.IsolatedCase):
    """Folder names holding control characters print escaped, in every stdout line."""

    def setUp(self) -> None:
        """A library named with an escape, two albums named with others."""
        super().setUp()
        self.library = self.tmp / "Lib\x1b[2Jrary"
        sf.needy_album(self.library, _COLOR_ARTIST, _LINK_ALBUM)
        sf.needy_album(self.library, _COLOR_ARTIST, _TURNED_ALBUM)

    def main(self, *args: str) -> str:
        """Run `main` with *args* on this library; its stdout."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(sf.FakeAlbumTool(), sf.FakeSleep(), sf.fixed_now)
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            sweep.main(argv, wiring=wiring)
        return stdout.getvalue()

    def assert_escaped(self, text: str) -> None:
        """*text* holds no raw control or layout character, and shows each escaped."""
        self.assertIsNone(_RAW.search(text), repr(text))
        for escape in ("\\x1b[31m", "\\x9b", "\\x1b]8;;", "\\x07", "\\u202e", "\\x0d"):
            self.assertIn(escape, text)

    def test_the_survey_prints_every_name_escaped(self) -> None:
        text = self.main("--mode", "survey")
        self.assert_escaped(text)
        self.assertIn("saved once", text)

    def test_the_run_report_and_resume_line_print_escaped(self) -> None:
        text = self.main("--mode", "run", "--limit", "1")
        resume = text.splitlines()[-1]
        self.assertTrue(resume.startswith("resume: "))
        self.assertIn("Lib\\x1b[2Jrary", resume)
        self.assertNotIn("\x1b", resume)
        # Only one album ran, so the table, its undo line and the other album's name
        # are checked through the second run's report too.
        text += self.main("--mode", "run")
        self.assert_escaped(text)
        self.assertIn("undo:", text)

    def test_a_table_row_still_lines_up_once_escaped(self) -> None:
        text = self.main("--mode", "survey")
        header, *rows = text.splitlines()[:3]
        synced = header.index("synced") + len("synced")
        for row in rows:
            self.assertEqual(row[synced - 1], "1", row)


class SilenceEscapeTest(sfx.SilenceCase):
    """The silence pass's progress, notices, summary and resume line print escaped."""

    def setUp(self) -> None:
        """A library named with an escape, two silent albums named with others."""
        super().setUp()
        self.library = self.tmp / "Lib\x1b[2Jrary"
        sfx.add_track(self.library / _COLOR_ARTIST / _TURNED_ALBUM, "01.mp3")
        sfx.add_track(self.library / _COLOR_ARTIST / _LINK_ALBUM, "01.mp3")
        self.ffmpeg.silent = frozenset({"01.mp3"})

    def test_every_line_on_stdout_and_stderr_prints_escaped(self) -> None:
        result = self.main("--limit", "1")
        self.assertTrue(result.stdout.splitlines()[-1].startswith("resume: "))
        self.assertIn("Lib\\x1b[2Jrary", result.stdout)
        text = result.stdout + result.stderr
        self.assertIsNone(_RAW.search(text), repr(text))
        for escape in ("\\x1b[31m", "\\x9b", "\\x1b]8;;", "\\x07"):
            self.assertIn(escape, result.stderr)
