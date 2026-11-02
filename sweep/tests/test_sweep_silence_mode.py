"""--mode silence: every track measured, the silent ones listed with verdicts, the exits.

The pass measures every track of every album the survey's walk finds, whatever its
lyrics, and appends each album's silent and unmeasured tracks to a list under the state
directory, then a marker naming the album done. ffmpeg is `silence_fixtures.FakeFfmpeg`,
so nothing here starts a process. Every album and title here is invented.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import silence_fixtures as sfx  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_silence_album as ssa  # noqa: E402 - import follows the sys.path bootstrap above

# os.geteuid exists only off Windows, so it is looked up; root can list a mode-000
# folder, so the unreadable-folder tests skip under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0
_STAMP = "silent-20261019T093000Z.tsv"


class MeasureTest(sfx.SilenceCase):
    """Four albums: a gap track, a ripping mistake, a spacer run, and nothing silent."""

    def setUp(self) -> None:
        """Build the four albums, and mark their silent tracks silent."""
        super().setUp()
        gaps = self.library / "Artist A" / "Gaps"
        sfx.add_track(gaps, "01 Song.mp3", title="Paper Boats", track_no="1/2")
        sfx.add_track(gaps, "02 Gap.mp3", seconds=4.0, title="Untitled", track_no="2/2")
        mistake = self.library / "Artist B" / "Mistake"
        sfx.add_track(mistake, "01 Harbor.mp3", title="Harbor Lights", track_no="1")
        spacers = self.library / "Artist C" / "Spacers"
        for number in (1, 2, 3):
            sfx.add_track(
                spacers, f"0{number} Track.mp3", seconds=2.0, title=f"Track {number}"
            )
        sfx.add_track(spacers, "04 Hidden.mp3", title="Hidden Song")
        sfx.add_track(self.library / "Artist D" / "Loud", "01 Loud.mp3")
        self.ffmpeg.silent = frozenset(
            {
                "02 Gap.mp3",
                "01 Harbor.mp3",
                "01 Track.mp3",
                "02 Track.mp3",
                "03 Track.mp3",
            }
        )

    def test_every_track_is_measured_and_the_silent_ones_listed(self) -> None:
        result = self.main()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(len(self.ffmpeg.calls), 8)
        self.assertEqual(self.the_list().name, _STAMP)
        spacer = (
            "likely meant\tspacer run: placeholder title, 2.0 s, 3 silent on the album"
        )
        self.assertEqual(
            self.list_lines(),
            [
                "likely meant\tgap track: placeholder title, 4.0 s\t-91.0\t4.0\t2/2"
                "\tUntitled\tArtist A/Gaps/02 Gap.mp3",
                "#album-done\tArtist A/Gaps",
                "likely a ripping mistake\treal title, 30.0 s\t-91.0\t30.0\t1"
                "\tHarbor Lights\tArtist B/Mistake/01 Harbor.mp3",
                "#album-done\tArtist B/Mistake",
                *(
                    f"{spacer}\t-91.0\t2.0\t\tTrack {n}\tArtist C/Spacers/0{n} Track.mp3"
                    for n in (1, 2, 3)
                ),
                "#album-done\tArtist C/Spacers",
                "#album-done\tArtist D/Loud",
            ],
        )

    def test_the_summary_counts_and_names_the_list(self) -> None:
        result = self.main()
        self.assertEqual(
            result.stdout,
            "already done: 0\n"
            "albums measured: 4\n"
            "tracks measured: 8\n"
            "silent: 5\n"
            "  likely meant: 4\n"
            "  likely a ripping mistake: 1\n"
            "  unsure: 0\n"
            "not measured: 0\n"
            "finished; albums left: 0\n"
            f"list: {self.the_list()}\n"
            f"page: {self.the_list().with_suffix('.html')}\n",
        )

    def test_progress_names_each_album_on_stderr(self) -> None:
        result = self.main()
        for number, name in enumerate(
            ("Artist A/Gaps", "Artist B/Mistake", "Artist C/Spacers", "Artist D/Loud"),
            start=1,
        ):
            self.assertIn(f"[{number}/4] {name}\n", result.stderr)

    def test_a_limit_measures_the_first_albums_and_prints_the_resume(self) -> None:
        result = self.main("--limit", "2")
        self.assertEqual(result.code, 0)
        self.assertIn("[2/2] Artist B/Mistake", result.stderr)
        self.assertNotIn("Artist C/Spacers", self.the_list().read_text("utf-8"))
        self.assertIn("limit reached; albums left: 2\n", result.stdout)
        self.assertIn(
            f"resume: sweep.py --mode silence --library {self.library} "
            f"--resume {self.the_list()}\n",
            result.stdout,
        )


class TrackTest(sfx.SilenceCase):
    """A track that cannot be measured, one with no readable tags, one in a disc folder."""

    def test_a_track_that_cannot_be_measured_is_listed_and_exits_one(self) -> None:
        sfx.add_track(self.library / "Artist" / "Album", "01 Lost.mp3", title="Lost")
        self.ffmpeg.failing = frozenset({"01 Lost.mp3"})
        result = self.main()
        self.assertEqual(result.code, 1)
        self.assertEqual(
            self.list_lines()[0],
            f"not measured\tffmpeg exit {sfx.FAILED_EXIT}\t\t30.0\t\tLost"
            "\tArtist/Album/01 Lost.mp3",
        )
        self.assertIn("not measured: 1\n", result.stdout)

    def test_a_silent_track_with_no_tags_is_unsure_of_its_length(self) -> None:
        sf.add_track(self.library / "Artist" / "Album", "01.mp3")
        self.ffmpeg.silent = frozenset({"01.mp3"})
        self.assertEqual(self.main().code, 0)
        self.assertEqual(
            self.list_lines()[0],
            "unsure\tplaceholder title, length unknown\t-91.0\t\t\t\tArtist/Album/01.mp3",
        )

    def test_a_tag_read_that_fails_leaves_its_fields_empty(self) -> None:
        sfx.add_track(self.library / "Artist" / "Album", "01.mp3", title="Paper Boats")
        self.ffmpeg.silent = frozenset({"01.mp3"})
        with mock.patch.object(ssa, "read_tags", side_effect=OSError("gone")):
            self.assertEqual(self.main().code, 0)
        # No title read is a missing title, and no length an unknown one.
        self.assertTrue(
            self.list_lines()[0].startswith("unsure\tplaceholder title, length unknown")
        )

    def test_a_tag_read_that_fails_names_the_track_on_stderr(self) -> None:
        sfx.add_track(self.library / "Artist" / "Album", "01.mp3", title="Paper Boats")
        self.ffmpeg.silent = frozenset({"01.mp3"})
        with mock.patch.object(ssa, "read_tags", side_effect=OSError("gone")):
            result = self.main()
        self.assertEqual(result.code, 0)
        self.assertIn("unreadable tags: Artist/Album/01.mp3 (OSError)\n", result.stderr)
        # The track keeps its row.
        self.assertTrue(self.list_lines()[0].endswith("\tArtist/Album/01.mp3"))

    def test_a_disc_folder_track_is_named_from_the_album(self) -> None:
        album = self.library / "Artist" / "Album"
        sfx.add_track(album / "Disc 2", "01.mp3", seconds=4.0, title="")
        self.ffmpeg.silent = frozenset({"01.mp3"})
        self.main()
        self.assertTrue(self.list_lines()[0].endswith("\tArtist/Album/Disc 2/01.mp3"))

    def test_a_folder_with_no_mp3_is_marked_done(self) -> None:
        (self.library / "Artist" / "Empty").mkdir(parents=True)
        result = self.main()
        self.assertEqual(result.code, 0)
        self.assertEqual(self.list_lines(), ["#album-done\tArtist/Empty"])

    def test_the_library_root_as_an_album_is_named_dot(self) -> None:
        self.assertEqual(ssa.album_name(self.library, self.library), ".")
        self.assertEqual(ssa.album_name(self.library / "A" / "B", self.library), "A/B")


class RefuseTest(sfx.SilenceCase):
    """No ffmpeg, a library or list folder that cannot be used: exit 2, nothing made."""

    def test_no_ffmpeg_exits_two_before_the_list_is_made(self) -> None:
        sfx.add_track(self.library / "Artist" / "Album", "01.mp3")
        self.found = None
        result = self.main()
        self.assertEqual(result.code, 2)
        self.assertIn("ffmpeg", result.stderr)
        self.assertEqual(self.ffmpeg.calls, [])
        self.assertFalse(self.state.exists())

    def test_a_list_folder_that_cannot_be_made_exits_two(self) -> None:
        sfx.add_track(self.library / "Artist" / "Album", "01.mp3")
        self.state.parent.mkdir(parents=True)
        self.state.write_bytes(b"")  # a file where the state folder should be
        result = self.main()
        self.assertEqual(result.code, 2)
        self.assertIn("the list cannot be opened", result.stderr)
        self.assertEqual(self.ffmpeg.calls, [])

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unlistable_library_exits_two(self) -> None:
        self.library.chmod(0)
        self.addCleanup(self.library.chmod, 0o755)
        result = self.main()
        self.assertEqual(result.code, 2)
        self.assertIn("the library cannot be listed", result.stderr)
        self.assertFalse(self.state.exists())

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unreadable_album_is_named_skipped_and_exits_one(self) -> None:
        locked = self.library / "Artist" / "Locked"
        sfx.add_track(locked, "01.mp3")
        sfx.add_track(self.library / "Artist" / "Open", "01.mp3")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        result = self.main()
        self.assertEqual(result.code, 1)
        self.assertIn("skipped album Artist/Locked: it cannot be read", result.stderr)
        self.assertEqual(self.list_lines(), ["#album-done\tArtist/Open"])
        self.assertIn("albums skipped: 1\n", result.stdout)

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_a_skipped_album_is_left_and_the_resume_is_printed(self) -> None:
        locked = self.library / "Artist" / "Locked"
        sfx.add_track(locked, "01.mp3")
        sfx.add_track(self.library / "Artist" / "Open", "01.mp3")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        result = self.main()
        # A skipped album has no marker, so a resume retries it: it is left.
        self.assertEqual(result.code, 1)
        self.assertIn("finished; albums left: 1\n", result.stdout)
        self.assertIn(
            f"resume: sweep.py --mode silence --library {self.library} "
            f"--resume {self.the_list()}\n",
            result.stdout,
        )

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unlistable_artist_folder_is_named(self) -> None:
        locked = self.library / "Locked Artist"
        sfx.add_track(locked / "Album", "01.mp3")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        result = self.main()
        self.assertEqual(result.code, 0)
        self.assertIn("skipped artist folder Locked Artist", result.stderr)

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unlistable_disc_folder_skips_its_album_unmarked(self) -> None:
        album = self.library / "Artist" / "Album"
        sfx.add_track(album, "01.mp3")
        locked = album / "Disc 2"
        sfx.add_track(locked, "01.mp3")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        result = self.main()
        self.assertEqual(result.code, 1)
        self.assertIn(
            "skipped album Artist/Album: it cannot be read (PermissionError)",
            result.stderr,
        )
        self.assertEqual(self.list_lines(), [])
        self.assertIn("finished; albums left: 1\n", result.stdout)

    def test_a_linked_track_or_folder_is_named(self) -> None:
        album = self.library / "Artist" / "Album"
        sfx.add_track(album, "01.mp3")
        real = sfx.add_track(self.tmp / "elsewhere", "02.mp3")
        (album / "02 Linked.mp3").symlink_to(real)
        (album / "Linked Disc").symlink_to(real.parent)
        result = self.main()
        self.assertEqual(result.code, 0, result.stderr)
        for link in ("Artist/Album/02 Linked.mp3", "Artist/Album/Linked Disc"):
            with self.subTest(link=link):
                self.assertIn(
                    f"skipped link {link}: a link is never followed\n", result.stderr
                )
        self.assertEqual(self.ffmpeg.calls, ["01.mp3"])

    def test_an_album_of_links_alone_names_them_and_is_marked_done(self) -> None:
        album = self.library / "Artist" / "Album"
        album.mkdir(parents=True)
        real = sfx.add_track(self.tmp / "elsewhere", "02.mp3")
        (album / "02.mp3").symlink_to(real)
        result = self.main()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("skipped link Artist/Album/02.mp3", result.stderr)
        self.assertEqual(self.list_lines(), ["#album-done\tArtist/Album"])

    def test_a_linked_album_is_dropped_and_named(self) -> None:
        real = self.tmp / "elsewhere" / "Album"
        sfx.add_track(real, "01.mp3")
        (self.library / "Artist").mkdir()
        (self.library / "Artist" / "Linked").symlink_to(real)
        result = self.main()
        self.assertIn("skipped album Artist/Linked", result.stderr)
        self.assertEqual(self.ffmpeg.calls, [])
