"""The Genius check's judging: a track's search hits read as has it, not on Genius, or unsure.

Pure: each test builds a query and hits in the keyed search's shape
(`genius_search_fakes`) and reads the finding. Every title and artist is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - puts album and the libraries on sys.path

sys.path.append(str(_HERE.parents[1] / "album" / "tests"))

import genius_search_fakes as gsf  # noqa: E402 - import follows the sys.path insert above
import lyrics_genius_search as lgs  # noqa: E402 - import follows the sys.path insert above
import lyrics_match  # noqa: E402 - import follows the sys.path insert above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path insert above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path insert above
import sweep_genius_judge as gj  # noqa: E402 - import follows the sys.path insert above

#: The track every test asks about, by its tags.
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
#: The same track asked by a file stem, its tags missing.
STEM = lm.TrackQuery("", "03 - Invented Artist - The Keeper Counts", from_filename=True)


#: A song far from the track, by title and artist both.
FAR = gsf.song_result("Paper Boats", "Someone Else")
#: Hits of shapes a drifted reply might hold, none read as a song.
DRIFTED: tuple[dict[str, object], ...] = (
    {"type": "Song", "result": gsf.song_result("The Keeper Counts", "Invented Artist")},
    {"type": "song", "song": gsf.song_result("The Keeper Counts", "Invented Artist")},
    {"type": "song", "result": {"full_title": "The Keeper Counts by Invented Artist"}},
)


def _hits(*results: dict[str, object]) -> lgs.SearchPage:
    """*results* read as the searcher reads a reply holding them."""
    return lgs.parse_search_reply(gsf.search_reply(*results))


def _with_drifted(
    drifted: dict[str, object], *results: dict[str, object]
) -> lgs.SearchPage:
    """A reply holding *results*, then the one *drifted* hit, as the searcher reads it."""
    reply = gsf.search_reply(*results)
    hits = reply["hits"]
    assert isinstance(hits, list)
    return lgs.parse_search_reply({"hits": [*hits, drifted]})


class JudgeTest(sf.IsolatedCase):
    """Each finding, and which hit it names."""

    def test_a_complete_match_has_it(self) -> None:
        """A hit matching at the normal bar, with complete lyrics, is has it."""
        hits = _hits(
            gsf.song_result("Another Song", "Someone Else"),
            gsf.song_result("The Keeper Counts", "Invented Artist"),
        )
        judged = gj.judge_hits(QUERY, hits)
        self.assertIs(judged.finding, gj.Finding.HAS_IT)
        self.assertIs(judged.hit, hits.hits[1])
        self.assertIsNone(judged.reason)

    def test_a_complete_match_wins_over_an_earlier_incomplete_one(self) -> None:
        """Of two matches, the one with lyrics is named."""
        hits = _hits(
            gsf.song_result(
                "The Keeper Counts", "Invented Artist", lyrics_state="unreleased"
            ),
            gsf.song_result("The Keeper Counts", "Invented Artist"),
        )
        judged = gj.judge_hits(QUERY, hits)
        self.assertIs(judged.finding, gj.Finding.HAS_IT)
        self.assertIs(judged.hit, hits.hits[1])

    def test_a_match_without_complete_lyrics_is_unsure(self) -> None:
        """A matched song whose lyrics are not complete is unsure, naming it."""
        hits = _hits(
            gsf.song_result(
                "The Keeper Counts", "Invented Artist", lyrics_state="unreleased"
            )
        )
        judged = gj.judge_hits(QUERY, hits)
        self.assertIs(judged.finding, gj.Finding.UNSURE)
        self.assertEqual(judged.reason, gj.NOT_COMPLETE)
        self.assertIs(judged.hit, hits.hits[0])

    def test_an_instrumental_match_is_unsure(self) -> None:
        """A matched song Genius marks instrumental is unsure."""
        hits = _hits(
            gsf.song_result("The Keeper Counts", "Invented Artist", instrumental=True)
        )
        judged = gj.judge_hits(QUERY, hits)
        self.assertEqual(judged.reason, gj.INSTRUMENTAL)

    def test_no_close_hit_is_not_on_genius(self) -> None:
        """Hits far from the track, or none, are not on Genius."""
        for hits in (lgs.SearchPage((), 0), _hits(FAR)):
            with self.subTest(hits=hits):
                judged = gj.judge_hits(QUERY, hits)
                self.assertIs(judged.finding, gj.Finding.NOT_ON_GENIUS)
                self.assertIsNone(judged.hit)

    def test_the_title_matching_with_another_artist_is_a_near_miss(self) -> None:
        """The title at the bar but the artist missed is unsure, naming the hit."""
        hits = _hits(gsf.song_result("The Keeper Counts", "Someone Else"))
        judged = gj.judge_hits(QUERY, hits)
        self.assertIs(judged.finding, gj.Finding.UNSURE)
        self.assertEqual(judged.reason, gj.NEAR_MISS)
        self.assertIs(judged.hit, hits.hits[0])

    def test_the_artist_matching_with_a_near_title_is_a_near_miss(self) -> None:
        """The artist at the bar and the title past the lower bound is a near miss."""
        hits = _hits(gsf.song_result("The Keeper Waits", "Invented Artist"))
        self.assertEqual(gj.judge_hits(QUERY, hits).reason, gj.NEAR_MISS)

    def test_the_artist_matching_with_a_far_title_is_unsure(self) -> None:
        """The artist alone, its title below the lower bound, is not close, but doubt.

        Genius knowing the artist and not this title is as likely a title tag that
        misnames the song, so the track is unsure, naming the artist's first hit.
        """
        hits = _hits(FAR, gsf.song_result("Paper Boats", "Invented Artist"))
        judged = gj.judge_hits(QUERY, hits)
        self.assertIs(judged.finding, gj.Finding.UNSURE)
        self.assertEqual(judged.reason, gj.ARTIST_FOUND)
        self.assertIs(judged.hit, hits.hits[1])

    def test_a_stem_query_matches_by_its_readings(self) -> None:
        """A file stem matches when one of its readings does."""
        hits = _hits(gsf.song_result("The Keeper Counts", "Invented Artist"))
        self.assertIs(gj.judge_hits(STEM, hits).finding, gj.Finding.HAS_IT)

    def test_a_stem_query_with_a_near_title_is_a_near_miss(self) -> None:
        """A stem reading's title past the lower bound is a near miss."""
        stem = lm.TrackQuery("", "Keeper Counting", from_filename=True)
        hits = _hits(gsf.song_result("The Keeper Counts", "Someone Else"))
        self.assertEqual(gj.judge_hits(stem, hits).reason, gj.NEAR_MISS)

    def test_a_stem_query_far_from_every_hit_is_unsure(self) -> None:
        """A stem, a guess at the title, far from every hit is unsure, never lacking."""
        stem = lm.TrackQuery("", "Paper Boats", from_filename=True)
        hits = _hits(gsf.song_result("The Keeper Counts", "Someone Else"))
        judged = gj.judge_hits(stem, hits)
        self.assertIs(judged.finding, gj.Finding.UNSURE)
        self.assertEqual(judged.reason, gj.BY_FILE_NAME)
        self.assertIsNone(judged.hit)


class DoubtTest(sf.IsolatedCase):
    """A search that found nothing close, but whose answer is not clear, is unsure."""

    def test_a_drifted_hit_beside_far_ones_is_unsure(self) -> None:
        """A hit not read as a song may be the track: unsure, never not on Genius."""
        for drifted in DRIFTED:
            with self.subTest(drifted=drifted):
                judged = gj.judge_hits(QUERY, _with_drifted(drifted, FAR))
                self.assertIs(judged.finding, gj.Finding.UNSURE)
                self.assertEqual(judged.reason, gj.HIT_UNREADABLE)

    def test_a_drifted_hit_beside_a_match_still_has_it(self) -> None:
        """A match read is a match, whatever else the page held."""
        match = gsf.song_result("The Keeper Counts", "Invented Artist")
        page = _with_drifted(DRIFTED[0], match)
        self.assertIs(gj.judge_hits(QUERY, page).finding, gj.Finding.HAS_IT)

    def test_a_full_page_with_nothing_close_is_unsure(self) -> None:
        """A page as full as a search gives may have the track past it: unsure."""
        page = _hits(*[FAR] * lgs.SEARCH_PER_PAGE)
        judged = gj.judge_hits(QUERY, page)
        self.assertIs(judged.finding, gj.Finding.UNSURE)
        self.assertEqual(judged.reason, gj.FULL_PAGE)

    def test_a_page_one_short_of_full_is_not_on_genius(self) -> None:
        """Fewer hits than a page holds is every hit there is: a clear answer."""
        page = _hits(*[FAR] * (lgs.SEARCH_PER_PAGE - 1))
        self.assertIs(gj.judge_hits(QUERY, page).finding, gj.Finding.NOT_ON_GENIUS)

    def test_the_lower_bound_sits_below_the_bar(self) -> None:
        """The near-miss bound is a lower bar than the match's own."""
        self.assertLess(gj.NEAR_TITLE_MIN_SIMILARITY, lyrics_match.TITLE_MIN_SIMILARITY)
