"""The survey: each album's tracks classified, and the albums a run would visit.

Every album is built from empty MP3s and invented sidecars, so a track's lyrics are
whatever its sidecars hold. Classifying writes nothing, which the first test checks.
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
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_survey  # noqa: E402 - import follows the sys.path bootstrap above

# os.geteuid exists only off Windows, so it is looked up; root can list a mode-000
# folder, so the unreadable-folder test skips under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


def _snapshot(folder: Path) -> dict[str, bytes]:
    """Every file under *folder* by relative path, with its bytes."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


class SurveyAlbumTest(sf.IsolatedCase):
    """`survey_album` counts synced, untimed-only, no-lyrics and unreadable tracks."""

    def test_each_kind_is_counted_and_nothing_is_written(self) -> None:
        album = sf.needy_album(self.library, "Artist", "Mixed")
        before = _snapshot(album)
        survey = sweep_survey.survey_album(album, lang="eng")
        self.assertEqual((survey.synced, survey.untimed, survey.none), (1, 1, 1))
        self.assertEqual(survey.unreadable, ())
        self.assertTrue(survey.has_mp3)
        self.assertEqual(_snapshot(album), before)
        self.assertFalse((album / ".lyricsmith").exists())

    def test_an_album_of_synced_tracks_needs_no_visit(self) -> None:
        album = sf.synced_album(self.library, "Artist", "Done")
        self.assertFalse(sweep_survey.survey_album(album, lang="eng").needs_visit)

    def test_an_untimed_only_track_needs_a_visit(self) -> None:
        album = self.library / "Artist" / "Plain"
        sf.add_track(album, "01.mp3", txt=sf.UNTIMED_TXT)
        self.assertTrue(sweep_survey.survey_album(album, lang="eng").needs_visit)

    def test_a_track_with_no_lyrics_needs_a_visit(self) -> None:
        album = self.library / "Artist" / "Bare"
        sf.add_track(album, "01.mp3")
        self.assertTrue(sweep_survey.survey_album(album, lang="eng").needs_visit)

    def test_an_unreadable_track_is_named_not_fatal_and_needs_a_visit(self) -> None:
        album = self.library / "Artist" / "Broken"
        sf.add_track(album, "01 Fine.mp3", lrc=sf.SYNCED_LRC)
        sf.add_track(album, "Disc 2/02 Bad.mp3", txt=sf.NOT_UTF8)
        survey = sweep_survey.survey_album(album, lang="eng")
        self.assertEqual(survey.unreadable, ("Disc 2/02 Bad.mp3 (SidecarDecodeError)",))
        self.assertEqual((survey.synced, survey.untimed, survey.none), (1, 0, 0))
        self.assertTrue(survey.needs_visit)

    def test_any_error_classifying_a_track_is_named_by_its_type(self) -> None:
        album = self.library / "Artist" / "Odd"
        sf.add_track(album, "01.mp3", lrc=sf.SYNCED_LRC)
        sf.add_track(album, "02.mp3", lrc=sf.SYNCED_LRC)
        real = sweep_survey.classify_track

        def classify(mp3: Path, *, album: Path, lang: str) -> object:
            if mp3.name == "01.mp3":
                raise ValueError("an error no reader names")
            return real(mp3, album=album, lang=lang)

        with mock.patch.object(sweep_survey, "classify_track", classify):
            survey = sweep_survey.survey_album(album, lang="eng")
        self.assertEqual(survey.unreadable, ("01.mp3 (ValueError)",))
        self.assertEqual(survey.synced, 1)

    def test_a_stamp_of_thousands_of_digits_ends_nothing(self) -> None:
        album = self.library / "Artist" / "Long Stamp"
        sf.add_track(album, "01.mp3", lrc=f"[{'9' * 5000}:00]a line\n")
        survey = sweep_survey.survey_album(album, lang="eng")
        self.assertEqual(survey.unreadable, ())
        self.assertEqual(survey.untimed, 1)
        self.assertTrue(survey.needs_visit)

    def test_a_multi_disc_album_counts_every_disc(self) -> None:
        album = self.library / "Artist" / "Double"
        sf.add_track(album, "Disc 1/01.mp3", lrc=sf.SYNCED_LRC)
        sf.add_track(album, "Disc 2/01.mp3")
        survey = sweep_survey.survey_album(album, lang="eng")
        self.assertEqual((survey.synced, survey.none), (1, 1))

    def test_a_folder_with_no_mp3_is_no_album_to_visit(self) -> None:
        album = self.library / "Artist" / "Artwork Only"
        album.mkdir(parents=True)
        (album / "cover.jpg").write_bytes(b"")
        survey = sweep_survey.survey_album(album, lang="eng")
        self.assertFalse(survey.has_mp3)
        self.assertFalse(survey.needs_visit)


class SurveyLibraryTest(sf.IsolatedCase):
    """`survey_library` surveys the walk's albums in walk order."""

    def test_albums_come_back_in_walk_order_with_their_visits(self) -> None:
        sf.needy_album(self.library, "B Artist", "Needs")
        sf.synced_album(self.library, "A Artist", "Done")
        sf.add_track(self.library / ".hidden" / "Album", "01.mp3")
        sf.add_track(self.library / "B Artist", "loose.mp3")
        surveys = sweep_survey.survey_library(self.library, lang="eng")
        names = [s.album.relative_to(self.library).as_posix() for s in surveys]
        self.assertEqual(names, ["A Artist/Done", "B Artist/Needs"])
        self.assertEqual([s.needs_visit for s in surveys], [False, True])

    @unittest.skipIf(_IS_ROOT, "root can list a mode-000 folder")
    def test_an_unreadable_artist_folder_is_reported_by_name(self) -> None:
        sf.needy_album(self.library, "Readable", "Album")
        locked = self.library / "Locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        skipped: list[str] = []
        surveys = sweep_survey.survey_library(
            self.library, lang="eng", on_skip=skipped.append
        )
        self.assertEqual(skipped, ["Locked"])
        self.assertEqual(len(surveys), 1)


class LinkedFolderTest(sf.IsolatedCase):
    """An album reached through a linked Artist or Album folder is dropped, by name.

    The album tool does not refuse a link: it resolves the album folder, so a linked
    album runs wherever the link leads. This drop is the only guard, for a linked
    album folder and a linked artist folder alike, and it keeps every write inside
    the library.
    """

    def setUp(self) -> None:
        """A needy album at home, and another outside the library."""
        super().setUp()
        sf.needy_album(self.library, "Artist", "Home")
        self.outside = self.tmp / "outside"
        sf.needy_album(self.outside, "Far Artist", "Far Album")
        self.dropped: list[str] = []

    def _names(self) -> list[str]:
        surveys = sweep_survey.survey_library(
            self.library, lang="eng", on_linked=self.dropped.append
        )
        return [s.album.relative_to(self.library).as_posix() for s in surveys]

    def test_a_linked_artist_folder_leading_outside_is_dropped(self) -> None:
        (self.library / "Linked Artist").symlink_to(self.outside / "Far Artist")
        self.assertEqual(self._names(), ["Artist/Home"])
        self.assertEqual(self.dropped, ["Linked Artist/Far Album"])

    def test_a_linked_album_folder_leading_outside_is_dropped(self) -> None:
        linked = self.library / "Artist" / "Linked"
        linked.symlink_to(self.outside / "Far Artist" / "Far Album")
        self.assertEqual(self._names(), ["Artist/Home"])
        self.assertEqual(self.dropped, ["Artist/Linked"])

    def test_a_link_to_an_album_inside_the_library_is_dropped_too(self) -> None:
        # The album itself is still surveyed, once, under its own name.
        (self.library / "Artist" / "Again").symlink_to(self.library / "Artist" / "Home")
        self.assertEqual(self._names(), ["Artist/Home"])
        self.assertEqual(self.dropped, ["Artist/Again"])

    def test_by_default_a_dropped_album_is_dropped_silently(self) -> None:
        (self.library / "Linked Artist").symlink_to(self.outside / "Far Artist")
        surveys = sweep_survey.survey_library(self.library, lang="eng")
        self.assertEqual(len(surveys), 1)


class PlanVisitsTest(sf.IsolatedCase):
    """`plan_visits`: the albums a run visits, and those it passes over as done."""

    def setUp(self) -> None:
        """Two needy albums and a synced one, surveyed in walk order."""
        super().setUp()
        sf.needy_album(self.library, "Artist", "A1")
        sf.needy_album(self.library, "Artist", "A2")
        sf.synced_album(self.library, "Artist", "A3")
        self.surveys = sweep_survey.survey_library(self.library, lang="eng")

    def _plan(self, done: frozenset[str], *, restart: bool = False) -> list[list[str]]:
        plan = sweep_survey.plan_visits(
            self.library, self.surveys, done, restart=restart
        )
        return [
            [album.relative_to(self.library).as_posix() for album in albums]
            for albums in (plan.to_visit, plan.done)
        ]

    def test_with_nothing_done_every_needy_album_is_visited(self) -> None:
        self.assertEqual(self._plan(frozenset()), [["Artist/A1", "Artist/A2"], []])

    def test_a_done_album_is_passed_over(self) -> None:
        plan = self._plan(frozenset({"Artist/A1"}))
        self.assertEqual(plan, [["Artist/A2"], ["Artist/A1"]])

    def test_a_done_album_with_every_track_synced_is_in_neither_list(self) -> None:
        self.assertEqual(
            self._plan(frozenset({"Artist/A3"})), [["Artist/A1", "Artist/A2"], []]
        )

    def test_a_restart_visits_done_albums_too(self) -> None:
        plan = self._plan(frozenset({"Artist/A1"}), restart=True)
        self.assertEqual(plan, [["Artist/A1", "Artist/A2"], []])
