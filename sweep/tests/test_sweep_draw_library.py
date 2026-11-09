"""The draw's library read: each track's timing, each album's manifests, the ledger's fixes.

An invented library of empty MP3s and sidecars, with manifests written through the album
tool's own writer and a ledger in the redirected state folder: every timing, every way an
album becomes a wrong-text album (a wrong-text removal, a withheld one, a sibling's
text, a fix-text visit in the ledger), a manifest that cannot be read, and a folder
holding no MP3. Every name and lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import draw_fixtures as df  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_draw_classify as dc  # noqa: E402 - import follows the sys.path bootstrap
import sweep_draw_library as dl  # noqa: E402 - import follows the sys.path bootstrap
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_strata import StrataGroup  # noqa: E402 - follows the sys.path bootstrap

_TIMING = dc.Timing


class TrackTimingTest(sf.IsolatedCase):
    """A track's timing, read from its sidecars and its frames, as sync's planner reads it."""

    def setUp(self) -> None:
        """An album folder to put each track in."""
        super().setUp()
        self.album = self.library / "Invented Artist" / "Timings"

    def _timing(self, mp3: Path) -> dc.Timing:
        return dl.track_timing(mp3, self.album)

    def test_an_unlabeled_timed_lrc_is_provider_timed(self) -> None:
        self.assertIs(
            self._timing(df.provider_timed(self.album, "01.mp3")),
            _TIMING.PROVIDER_TIMED,
        )

    def test_a_labeled_timed_lrc_is_machine_timed(self) -> None:
        self.assertIs(
            self._timing(df.machine_timed(self.album, "01.mp3")), _TIMING.MACHINE_TIMED
        )

    def test_timed_frames_with_no_lrc_are_frames_only(self) -> None:
        self.assertIs(
            self._timing(df.frames_only(self.album, "01.mp3")), _TIMING.FRAMES_ONLY
        )

    def test_an_untimed_txt_is_untimed(self) -> None:
        self.assertIs(self._timing(df.untimed(self.album, "01.mp3")), _TIMING.UNTIMED)

    def test_no_lyrics_and_a_placeholder_lrc_are_not_timed(self) -> None:
        bare = sf.add_track(self.album, "01.mp3")
        note = sf.add_track(self.album, "02.mp3", lrc="[00:01.00]♪\n")
        self.assertIs(self._timing(bare), _TIMING.NOT_TIMED)
        self.assertIs(self._timing(note), _TIMING.NOT_TIMED)

    def test_a_sidecar_that_cannot_be_read_is_unreadable(self) -> None:
        mp3 = sf.add_track(self.album, "01.mp3", txt=sf.NOT_UTF8)
        self.assertIs(self._timing(mp3), _TIMING.UNREADABLE)


class AlbumRunsTest(sf.IsolatedCase):
    """An album's manifests, newest first, and whether any marks a wrong text."""

    def setUp(self) -> None:
        """An album with two tracks."""
        super().setUp()
        self.album = self.library / "Invented Artist" / "Runs"
        df.provider_timed(self.album, "01.mp3")
        df.provider_timed(self.album, "02.mp3")
        self.notices: list[str] = []

    def _runs(self) -> dl.AlbumRuns:
        return dl.read_album_runs(self.album, self.notices.append)

    def test_no_run_folder_holds_no_entries_and_no_mark(self) -> None:
        runs = self._runs()
        self.assertEqual(dict(runs.entries), {})
        self.assertFalse(runs.marked)

    def test_entries_are_kept_newest_first_per_track(self) -> None:
        old = df.entry("01.mp3", df.synced(lm.Source.SIDECAR))
        new = df.entry("01.mp3", df.synced(lm.Source.NETEASE))
        df.write_manifest(self.album, df.OLD_RUN, (old,))
        df.write_manifest(self.album, df.NEW_RUN, (new,))
        self.assertEqual(self._runs().entries["01.mp3"], (new, old))

    def test_a_mark_on_any_track_of_any_run_marks_the_album(self) -> None:
        df.write_manifest(
            self.album, df.OLD_RUN, (df.entry("02.mp3", df.wrong_text_removed()),)
        )
        df.write_manifest(
            self.album, df.NEW_RUN, (df.entry("01.mp3", df.synced(lm.Source.LRCLIB)),)
        )
        self.assertTrue(self._runs().marked)

    def test_a_manifest_that_cannot_be_read_is_passed_over_and_said(self) -> None:
        run_dir = df.write_manifest(
            self.album, df.NEW_RUN, (df.entry("01.mp3", df.wrong_text_removed()),)
        )
        (run_dir / "manifest.json").write_bytes(b"{")
        good = df.entry("01.mp3", df.synced(lm.Source.LRCLIB))
        df.write_manifest(self.album, df.OLD_RUN, (good,))
        runs = self._runs()
        self.assertEqual(runs.entries["01.mp3"], (good,))
        self.assertFalse(runs.marked)
        (notice,) = self.notices
        self.assertIn(df.NEW_RUN, notice)
        self.assertIn("ManifestSchemaError", notice)

    def test_a_run_folder_that_cannot_be_listed_is_said(self) -> None:
        df.write_manifest(self.album, df.OLD_RUN, ())
        refused = PermissionError("not listable")
        with mock.patch.object(Path, "iterdir", side_effect=refused):
            runs = self._runs()
        self.assertEqual(dict(runs.entries), {})
        (notice,) = self.notices
        self.assertIn("PermissionError", notice)


class FixTextAlbumsTest(sf.IsolatedCase):
    """Every album with a fix-text pass entry in the ledger."""

    def test_only_fix_text_entries_name_albums(self) -> None:
        records = (
            df.ledger_entry("A/Fixed", sl.SweepPass.FIX_TEXT),
            df.ledger_entry("A/Run", sl.SweepPass.NORMAL),
            df.ledger_entry("A/Refetched", sl.SweepPass.REFETCH_UNTIMED),
            sl.RestartMark("2026-10-19T09:30:00+00:00", sl.SweepPass.FIX_TEXT),
        )
        self.assertEqual(dl.fix_text_albums(records), frozenset({"A/Fixed"}))


class ClassifyLibraryTest(sf.IsolatedCase):
    """Every track of the library, with its album, timing, group or reason, and mark."""

    def _classify(
        self, fixed: frozenset[str] = frozenset()
    ) -> dict[str, dl.ClassifiedTrack]:
        found = dl.classify_library(self.library, fixed, notice=self.fail)
        return {
            track.path.relative_to(self.library).as_posix(): track for track in found
        }

    def test_each_album_and_track_is_classified(self) -> None:
        plain = self.library / "Artist" / "Plain"
        df.provider_timed(plain, "01.mp3")
        df.machine_timed(plain, "02.mp3")
        df.untimed(plain, "03.mp3")
        df.write_manifest(
            plain,
            df.OLD_RUN,
            (
                df.entry("01.mp3", df.synced(lm.Source.YOUTUBE_MUSIC)),
                df.entry("03.mp3", df.withheld_removal(), undone=True),
            ),
        )
        found = self._classify()
        first = found["Artist/Plain/01.mp3"]
        self.assertEqual(first.album, "Artist/Plain")
        self.assertIs(first.timing, _TIMING.PROVIDER_TIMED)
        self.assertIs(first.stratum, StrataGroup.WRONG_TEXT_ALBUMS)
        self.assertIs(found["Artist/Plain/02.mp3"].stratum, dc.LeftOut.MACHINE_TIMED)
        self.assertFalse(found["Artist/Plain/03.mp3"].withheld)
        self.assertTrue(first.path.is_absolute())

    def test_an_unmarked_album_takes_provenance(self) -> None:
        album = self.library / "Artist" / "Clean"
        df.provider_timed(album, "01.mp3")
        df.provider_timed(album, "02.mp3")
        df.write_manifest(
            album, df.OLD_RUN, (df.entry("01.mp3", df.synced(lm.Source.NETEASE)),)
        )
        found = self._classify()
        self.assertIs(found["Artist/Clean/01.mp3"].stratum, StrataGroup.NETEASE)
        self.assertIs(
            found["Artist/Clean/02.mp3"].stratum, dc.LeftOut.NO_MANIFEST_RECORD
        )

    def test_a_ledger_fix_marks_an_album_no_manifest_marks(self) -> None:
        album = self.library / "Artist" / "Fixed"
        df.provider_timed(album, "01.mp3")
        df.write_manifest(
            album, df.OLD_RUN, (df.entry("01.mp3", df.synced(lm.Source.NETEASE)),)
        )
        found = self._classify(frozenset({"Artist/Fixed"}))
        self.assertIs(
            found["Artist/Fixed/01.mp3"].stratum, StrataGroup.WRONG_TEXT_ALBUMS
        )

    def test_a_live_withheld_mark_is_kept_on_the_track(self) -> None:
        album = self.library / "Artist" / "Spoken"
        df.untimed(album, "01.mp3")
        df.write_manifest(
            album, df.OLD_RUN, (df.entry("01.mp3", df.withheld_removal()),)
        )
        (track,) = self._classify().values()
        self.assertTrue(track.withheld)
        self.assertIs(track.timing, _TIMING.UNTIMED)

    def test_a_folder_holding_no_mp3_is_no_album(self) -> None:
        (self.library / "Artist" / "Empty").mkdir(parents=True)
        self.assertEqual(self._classify(), {})
