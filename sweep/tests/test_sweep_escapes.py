"""Everything the sweep prints to stdout shows control and layout characters escaped.

An album or library folder's name is text the sweep did not write: one holding an
escape sequence (a color, an OSC 8 hyperlink, a C1 control), a direction override or a
carriage return would drive the terminal, or make one name read as another. So the
survey, the run report and the resume line each pass through `stdio_utf8.spell_unsafe`,
as the album tool's own reports do. NTFS holds no C0 control in a name, so each check
runs with the attacks in their C1 forms (CSI, OSC and ST as single code points) and a
DEL on every host, and in their C0 forms wherever the host holds them (a probe, not the
platform). Every name here is invented for these tests.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from typing import ClassVar

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

#: A library folder name that clears the screen, and how it prints.
_LIBRARY = "Lib\x1b[2Jrary"
_LIBRARY_SHOWN = "Lib\\x1b[2Jrary"
#: A color, then the C1 control sequence introducer.
_COLOR_ARTIST = "Red\x1b[31mArtist\x9b0m"
#: An OSC 8 hyperlink wrapped around a word; no slash, which would nest folders.
_LINK_ALBUM = "Link \x1b]8;;https:example.invalid\x07here\x1b]8;;\x07"
#: A right-to-left override and a carriage return.
_TURNED_ALBUM = "Turn‮ed\rBack"
#: How each attack above prints, escaped.
_SHOWN = ("\\x1b[31m", "\\x9b", "\\x1b]8;;", "\\x07", "\\u202e", "\\x0d")
#: The same attacks in their C1 forms, with a DEL for the carriage return. NTFS holds
#: these, though no C0 control, and no colon either, so the link names no scheme.
_LIBRARY_C1 = "Lib\x9b2Jrary"
_LIBRARY_C1_SHOWN = "Lib\\x9b2Jrary"
_COLOR_ARTIST_C1 = "Red\x9b31mArtist\x9b0m"
_LINK_ALBUM_C1 = "Link \x9d8;;example.invalid\x9chere\x9d8;;\x9c"
_TURNED_ALBUM_C1 = "Turn‮ed\x7fBack"
_SHOWN_C1 = ("\\x9b31m", "\\x9b", "\\x9d8;;", "\\x9c", "\\u202e", "\\x7f")
#: Any character `spell_unsafe` escapes, but the newline that ends each printed line.
_RAW = re.compile("[\x00-\x09\x0b-\x1f\x7f-\x9f  ‪-‮]")
#: Whether this host's file system holds every C0-form name above.
_HOLDS_C0 = all(
    sfx.album_fixtures.host_holds_name(name)
    for name in (_LIBRARY, _COLOR_ARTIST, _LINK_ALBUM, _TURNED_ALBUM)
)
_NO_C0 = "this file system holds no C0 control character in a name"


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

    def test_the_every_host_names_hold_nothing_ntfs_refuses(self) -> None:
        # NTFS refuses a C0 control and each of these in a name, so the C1 cases,
        # which run on Windows too, must hold none of them.
        refused = re.compile('[\x00-\x1f<>:"/\\\\|?*]')
        for name in (
            _LIBRARY_C1,
            _COLOR_ARTIST_C1,
            _LINK_ALBUM_C1,
            _TURNED_ALBUM_C1,
        ):
            with self.subTest(name=name):
                self.assertIsNone(refused.search(name))


class EscapedOutputTest(sf.IsolatedCase):
    """Folder names holding control characters print escaped, in every stdout line.

    The names hold C1 controls and a DEL, which NTFS holds, so this runs on every host;
    `EscapedC0OutputTest` repeats it with C0 controls where the host holds them.
    """

    library_name: ClassVar[str] = _LIBRARY_C1
    library_shown: ClassVar[str] = _LIBRARY_C1_SHOWN
    artist: ClassVar[str] = _COLOR_ARTIST_C1
    albums: ClassVar[tuple[str, str]] = (_LINK_ALBUM_C1, _TURNED_ALBUM_C1)
    shown: ClassVar[tuple[str, ...]] = _SHOWN_C1

    def setUp(self) -> None:
        """A library named with an escape, two albums named with others."""
        super().setUp()
        self.library = self.tmp / self.library_name
        for name in self.albums:
            sf.needy_album(self.library, self.artist, name)

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
        for escape in self.shown:
            self.assertIn(escape, text)

    def test_the_survey_prints_every_name_escaped(self) -> None:
        text = self.main("--mode", "survey")
        self.assert_escaped(text)
        self.assertIn("saved once", text)

    def test_the_run_report_and_resume_line_print_escaped(self) -> None:
        text = self.main("--mode", "run", "--limit", "1")
        resume = text.splitlines()[-1]
        self.assertTrue(resume.startswith("resume: "))
        self.assertIn(self.library_shown, resume)
        self.assertIsNone(_RAW.search(resume), repr(resume))
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


@unittest.skipUnless(_HOLDS_C0, _NO_C0)
class EscapedC0OutputTest(EscapedOutputTest):
    """The same, the names holding C0 controls, where the host holds them."""

    library_name = _LIBRARY
    library_shown = _LIBRARY_SHOWN
    artist = _COLOR_ARTIST
    albums = (_LINK_ALBUM, _TURNED_ALBUM)
    shown = _SHOWN


class SilenceEscapeTest(sfx.SilenceCase):
    """The silence pass's progress, notices, summary and resume line print escaped.

    C1 forms on every host, as `EscapedOutputTest`; `SilenceC0EscapeTest` the C0 ones.
    """

    library_name: ClassVar[str] = _LIBRARY_C1
    library_shown: ClassVar[str] = _LIBRARY_C1_SHOWN
    artist: ClassVar[str] = _COLOR_ARTIST_C1
    albums: ClassVar[tuple[str, str]] = (_TURNED_ALBUM_C1, _LINK_ALBUM_C1)
    #: What the artist and the link album, whose album is measured first, print as.
    shown: ClassVar[tuple[str, ...]] = _SHOWN_C1[:4]

    def setUp(self) -> None:
        """A library named with an escape, two silent albums named with others."""
        super().setUp()
        self.library = self.tmp / self.library_name
        for name in self.albums:
            sfx.add_track(self.library / self.artist / name, "01.mp3")
        self.ffmpeg.silent = frozenset({"01.mp3"})

    def test_every_line_on_stdout_and_stderr_prints_escaped(self) -> None:
        result = self.main("--limit", "1")
        self.assertTrue(result.stdout.splitlines()[-1].startswith("resume: "))
        self.assertIn(self.library_shown, result.stdout)
        text = result.stdout + result.stderr
        self.assertIsNone(_RAW.search(text), repr(text))
        for escape in self.shown:
            self.assertIn(escape, result.stderr)


@unittest.skipUnless(_HOLDS_C0, _NO_C0)
class SilenceC0EscapeTest(SilenceEscapeTest):
    """The same, the names holding C0 controls, where the host holds them."""

    library_name = _LIBRARY
    library_shown = _LIBRARY_SHOWN
    artist = _COLOR_ARTIST
    albums = (_TURNED_ALBUM, _LINK_ALBUM)
    shown = _SHOWN[:4]
