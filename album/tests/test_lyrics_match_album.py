"""The album match: a release's title against the album tag, then its credited artists.

Version words are not refused here, since each track is judged again by the track
matcher. Every title and name is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above

ALBUM = "The Lantern Years"
ARTIST = "Invented Band"


class AlbumTitleScoreTest(fixtures.IsolatedCase):
    """The title rule alone: alike keys and the same digit runs."""

    def test_a_deluxe_edition_passes(self) -> None:
        score = lmatch.album_title_score(ALBUM, "The Lantern Years (Deluxe Edition)")
        self.assertEqual(score, 1.0)

    def test_a_live_album_is_not_refused_for_its_version_word(self) -> None:
        self.assertIsNotNone(
            lmatch.album_title_score(ALBUM, "The Lantern Years (Live)")
        )

    def test_other_digit_runs_are_refused(self) -> None:
        # Arabic digits: Roman numerals are letters to the title rule, so "II" against
        # "III" falls to the similarity alone, which passes it.
        self.assertIsNone(
            lmatch.album_title_score("Lantern Years 2", "Lantern Years 3")
        )

    def test_a_near_miss_is_refused(self) -> None:
        # Two letters off: at 0.9, one changed letter in a key this long still passes.
        self.assertIsNone(lmatch.album_title_score(ALBUM, "The Lantern Wards"))

    def test_a_blank_album_matches_nothing(self) -> None:
        self.assertIsNone(lmatch.album_title_score("  ", "  "))


class AlbumScoreTest(fixtures.IsolatedCase):
    """The title rule, then the artist rule against the first credit or all of them."""

    def test_the_same_album_and_artist(self) -> None:
        self.assertEqual(lmatch.album_score(ALBUM, ARTIST, ALBUM, (ARTIST,)), 2.0)

    def test_another_artist_is_refused(self) -> None:
        score = lmatch.album_score(ALBUM, ARTIST, ALBUM, ("Somebody Unrelated",))
        self.assertIsNone(score)

    def test_a_blank_artist_matches_nothing(self) -> None:
        self.assertIsNone(lmatch.album_score(ALBUM, " ", ALBUM, (ARTIST,)))

    def test_no_credit_matches_nothing(self) -> None:
        self.assertIsNone(lmatch.album_score(ALBUM, ARTIST, ALBUM, ()))

    def test_a_title_refused_is_refused(self) -> None:
        score = lmatch.album_score(ALBUM, ARTIST, "The Lantern Wards", (ARTIST,))
        self.assertIsNone(score)

    def test_credits_matching_only_when_joined(self) -> None:
        credits = ("Ada Inventa", "Bo Madeup")
        artist = "Ada Inventa & Bo Madeup"
        self.assertIsNone(lmatch.album_score(ALBUM, "Bo Madeup", ALBUM, credits))
        self.assertEqual(lmatch.album_score(ALBUM, artist, ALBUM, credits), 2.0)
