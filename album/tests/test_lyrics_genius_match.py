"""The Genius step's matcher: its one search result must be the track asked for.

Genius answers a search with a single song, its own best guess, so the step puts that
song through the same title, artist and version rules as every other provider's hits,
with no length to compare. A refused song is nothing found: no error, no refusal. A
query with no artist, a track asked by its file name, is matched by any reading of
that name: a title alone gets the title and version rules, and an artist read before a
dash must match the song's too. No test here reaches the network: the client is a scripted fake. Every name and
lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes as lo  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    QUERY,
    FakeClock,
    FakeGeniusClient,
    FakeSong,
    paced_genius,
)

U = lm.LyricsKind.UNSYNCED
GENIUS = lm.Source.GENIUS
#: Invented lyrics a matching song carries.
WORDS = "the keeper counts the lamps\nand every lamp counts back"
#: A track asked by its file name: no artist, the stem standing in for the title.
BY_FILENAME = lm.TrackQuery("", "The Keeper Counts", from_filename=True)


def _by_stem(stem: str) -> lm.TrackQuery:
    """A track asked by its file *stem*: no artist, the stem whole as the title."""
    return lm.TrackQuery("", stem, from_filename=True)


def _jointly_credited() -> FakeSong:
    """A song Genius credits to two primary artists, its artist string joining them."""
    return FakeSong(
        WORDS,
        artist="Invented Artist & Lamp Choir",
        primary_artists=({"name": "Invented Artist"}, {"name": "Lamp Choir"}),
    )


def _ask(song: FakeSong | None, query: lm.TrackQuery = QUERY) -> str | None:
    """The Genius step's answer for *query* when the search returns *song*."""
    return paced_genius(FakeGeniusClient([song]), FakeClock())(query)


class GeniusMatchTest(fixtures.IsolatedCase):
    """The single search result passes the title, artist and version rules, or is None."""

    def test_the_right_song_is_accepted(self) -> None:
        song = FakeSong(WORDS, title="The Keeper Counts", artist="Invented Artist")
        self.assertEqual(_ask(song), WORDS)

    def test_a_qualifier_on_the_songs_title_still_matches(self) -> None:
        song = FakeSong(WORDS, title="The Keeper Counts (Remastered 2011)")
        self.assertEqual(_ask(song), WORDS)

    def test_a_song_with_another_title_is_refused(self) -> None:
        self.assertIsNone(_ask(FakeSong(WORDS, title="The Weaver Sleeps")))

    def test_a_song_by_another_artist_is_refused(self) -> None:
        self.assertIsNone(_ask(FakeSong(WORDS, artist="Unrelated Ensemble")))

    def test_a_live_or_remix_song_for_a_plain_track_is_refused(self) -> None:
        for title in ("The Keeper Counts (Live)", "The Keeper Counts - Remix"):
            with self.subTest(title=title):
                self.assertIsNone(_ask(FakeSong(WORDS, title=title)))

    def test_a_live_song_for_a_live_track_is_accepted(self) -> None:
        query = lm.TrackQuery("Invented Artist", "The Keeper Counts (Live)")
        song = FakeSong(WORDS, title="The Keeper Counts (Live)")
        self.assertEqual(_ask(song, query), WORDS)


class GeniusFileNameQueryTest(fixtures.IsolatedCase):
    """A query with no artist is matched by any reading of its file name."""

    def test_any_artist_is_accepted_when_the_title_matches(self) -> None:
        song = FakeSong(WORDS, artist="Whoever Sang It")
        self.assertEqual(_ask(song, BY_FILENAME), WORDS)

    def test_another_title_is_still_refused(self) -> None:
        self.assertIsNone(_ask(FakeSong(WORDS, title="The Weaver Sleeps"), BY_FILENAME))

    def test_a_file_name_holding_a_track_number_is_accepted(self) -> None:
        for stem in (
            "03 The Keeper Counts",
            "03 - The Keeper Counts",
            "1-03 The Keeper Counts",
        ):
            with self.subTest(stem=stem):
                self.assertEqual(_ask(FakeSong(WORDS), _by_stem(stem)), WORDS)

    def test_an_artist_in_the_file_name_must_match_the_songs(self) -> None:
        query = _by_stem("03 - Invented Artist - The Keeper Counts")
        self.assertEqual(_ask(FakeSong(WORDS), query), WORDS)
        song = FakeSong(WORDS, artist="Unrelated Ensemble")
        self.assertIsNone(_ask(song, query))

    def test_a_numbered_file_name_with_another_title_is_refused(self) -> None:
        song = FakeSong(WORDS, title="The Weaver Sleeps")
        self.assertIsNone(_ask(song, _by_stem("03 The Keeper Counts")))

    def test_a_numbered_file_name_for_a_live_song_is_refused(self) -> None:
        song = FakeSong(WORDS, title="The Keeper Counts (Live)")
        self.assertIsNone(_ask(song, _by_stem("03 - The Keeper Counts")))

    def test_a_title_starting_with_a_number_matches_whole(self) -> None:
        song = FakeSong(WORDS, title="99 Lanterns")
        self.assertEqual(_ask(song, _by_stem("99 Lanterns")), WORDS)

    def test_a_version_the_title_lacks_is_still_refused(self) -> None:
        song = FakeSong(WORDS, title="The Keeper Counts (Acoustic)")
        self.assertIsNone(_ask(song, BY_FILENAME))


class GeniusSongHitTest(fixtures.IsolatedCase):
    """The song as the matcher reads it: its title, its credits, and no length."""

    def test_a_song_is_a_hit_with_no_length(self) -> None:
        song = FakeSong(WORDS, title="A Title", artist="An Artist")
        self.assertEqual(lg.song_hit(song), lmatch.Hit("A Title", ("An Artist",), None))

    def test_a_joint_credit_lists_every_primary_artist_in_order(self) -> None:
        credits = ("Invented Artist", "Lamp Choir")
        self.assertEqual(lg.song_hit(_jointly_credited()).artists, credits)

    def test_a_joint_credit_matches_its_first_artist(self) -> None:
        self.assertEqual(_ask(_jointly_credited()), WORDS)

    def test_a_primary_artist_without_a_usable_name_is_skipped(self) -> None:
        song = FakeSong(
            WORDS,
            artist="An Artist",
            primary_artists=({"name": 7}, {"id": 1}, {"name": "  "}),
        )
        self.assertEqual(lg.song_hit(song).artists, ("An Artist",))


class TitleScoreTest(fixtures.IsolatedCase):
    """The matcher's title-and-version rules, without the artist."""

    def test_it_ignores_the_artist(self) -> None:
        hit = lmatch.Hit("The Keeper Counts", ("Someone Else",), None)
        self.assertIsNotNone(lmatch.title_score(BY_FILENAME, hit))

    def test_a_blank_title_matches_nothing(self) -> None:
        hit = lmatch.Hit("The Keeper Counts", (), None)
        self.assertIsNone(lmatch.title_score(lm.TrackQuery("", " "), hit))

    def test_a_length_far_off_is_refused_when_both_are_known(self) -> None:
        query = lm.TrackQuery("", "The Keeper Counts", duration_s=200.0)
        hit = lmatch.Hit("The Keeper Counts", (), 260.0)
        self.assertIsNone(lmatch.title_score(query, hit))


class GeniusRefusedIsNothingFoundTest(fixtures.IsolatedCase):
    """A refused song is the step finding nothing, never an error or a refusal."""

    def test_a_refused_song_is_an_empty_attempt(self) -> None:
        genius = paced_genius(
            FakeGeniusClient([FakeSong(WORDS, title="The Weaver Sleeps")] * 2),
            FakeClock(),
        )
        table = {lm.ProviderKey(GENIUS, U): genius}
        result = lf.fetch_lyrics(
            QUERY, lm.SidecarState(None), table, deadline_s=lf.PROVIDER_DEADLINE_S
        )
        self.assertFalse(result.found)
        self.assertEqual(lo.fetch_refusals(result.attempts), ())
        outcomes = {attempt.outcome for attempt in result.attempts}
        self.assertEqual(outcomes, {lm.AttemptOutcome.EMPTY})
