"""``sweep --mode hear-report``'s row check over an album whose tracks sit in disc folders.

A multi-disc album keeps its tracks a folder below it (``Disc 1/``), and the draw lists
them so: the report takes such a row, and still refuses one that a link reaches, the
disc folder or the track. Kept apart from `test_sweep_hear_report` by the file-length
bar; its fixture is reused. Every name and word here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album's and the libraries' code on sys.path
import hear_fixtures as hf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_hear_report as hr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_strata as ss  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_hearing_log import HeardVerdict  # noqa: E402 - follows the bootstrap
from test_sweep_hear_report import ReportCase  # noqa: E402 - follows the bootstrap

_ALBUM = "Invented Artist/Kept"


class DiscFolderRowTest(ReportCase):
    """A row in a disc folder of its album is counted; one a link reaches is refused."""

    def _disc_row(self, disc: str, name: str = "01.mp3") -> ss.StrataRow:
        """The NetEase strata row of *disc*'s track *name* on the Kept album."""
        return ss.StrataRow(
            str(self.kept / disc / name), ss.StrataGroup.NETEASE, _ALBUM
        )

    def test_a_track_in_a_disc_folder_is_counted(self) -> None:
        sf.add_track(self.kept / "Disc 1", "01.mp3", lrc=hf.lrc_of(hf.song("wa")))
        row = self._disc_row("Disc 1")
        ss.write_strata(self.strata, 5, (row,))
        hf.log(self.log, hf.hearing(Path(row.path), HeardVerdict.SUNG))
        result = self.report()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertNotIn("outside --library", result.stderr)

    def test_a_disc_folder_or_track_reached_through_a_link_is_refused(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        sf.add_track(elsewhere, "01.mp3", lrc=hf.lrc_of(hf.song("wa")))
        (self.kept / "Disc 2").symlink_to(elsewhere, target_is_directory=True)
        sf.add_track(self.kept / "Disc 3", "02.mp3", lrc=hf.lrc_of(hf.song("ye")))
        (self.kept / "Disc 3" / "01.mp3").symlink_to(elsewhere / "01.mp3")
        self.assert_refused(self._disc_row("Disc 2"), self._disc_row("Disc 3"))

    def assert_refused(self, *rows: ss.StrataRow) -> None:
        """Each of *rows*, alone in the strata file, stops the report before it prints."""
        for row in rows:
            with self.subTest(path=row.path, album=row.album):
                ss.write_strata(self.strata, 5, (row,))
                result = self.report()
                self.assertEqual(result.code, 2)
                self.assertIn("outside --library", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_a_junction_at_a_disc_folder_is_refused(self) -> None:
        # Windows reads a junction as no symbolic link, so the check must ask album's
        # own junction-aware test; macOS cannot make one, so it stands in here.
        sf.add_track(self.kept / "Disc 4", "01.mp3", lrc=hf.lrc_of(hf.song("wa")))
        junction = self.kept / "Disc 4"
        with mock.patch.object(hr, "is_link_or_junction", lambda p: p == junction):
            self.assert_refused(self._disc_row("Disc 4"))

    def test_a_row_not_lexically_normal_is_refused(self) -> None:
        # The OS follows a link before it applies "..", so a check on the normalized
        # path would pass a row whose raw path, as read, goes through the link.
        elsewhere = self.tmp / "elsewhere"
        sf.add_track(elsewhere, "01.mp3", lrc=hf.lrc_of(hf.song("wa")))
        (self.kept / "Linked").symlink_to(elsewhere, target_is_directory=True)
        through = str(self.kept / "Linked" / ".." / "01.mp3")
        group = ss.StrataGroup.NETEASE
        self.assert_refused(
            ss.StrataRow(through, group, _ALBUM),
            ss.StrataRow(str(self.kept / "01.mp3"), group, "Invented Artist/x/../Kept"),
        )

    def test_an_album_too_deep_or_a_track_naming_its_album_is_refused(self) -> None:
        sf.add_track(self.kept / "Disc 1", "01.mp3", lrc=hf.lrc_of(hf.song("wa")))
        disc = self.kept / "Disc 1"
        group = ss.StrataGroup.NETEASE
        self.assert_refused(
            ss.StrataRow(str(disc / "01.mp3"), group, f"{_ALBUM}/Disc 1"),
            ss.StrataRow(str(self.kept), group, _ALBUM),
        )

    def test_a_failed_look_below_the_album_refuses_the_row(self) -> None:
        # A folder the report may not search refuses the row, never a traceback.
        sf.add_track(self.kept / "Disc 1", "01.mp3", lrc=hf.lrc_of(hf.song("wa")))
        refused = PermissionError(13, "Permission denied")
        with mock.patch.object(hr, "is_link_or_junction", side_effect=refused):
            self.assert_refused(self._disc_row("Disc 1"))


class AlbumNormalTest(ReportCase):
    """A strata album is the draw's ``Artist/Album``, POSIX on every host."""

    def test_the_draws_album_form_is_normal_on_every_host(self) -> None:
        self.assertTrue(hr.album_is_normal("Invented Artist/Kept"))
        for album in ("Invented Artist\\Kept", "a/../Kept", "../Kept", "a//Kept", "a/"):
            with self.subTest(album=album):
                self.assertFalse(hr.album_is_normal(album))
