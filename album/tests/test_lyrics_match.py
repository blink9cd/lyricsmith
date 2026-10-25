"""The matcher: a provider's search hit is the track only when title, artist and length agree.

A wrong match embeds another song's words, which nothing later in the run can tell from
the right ones, so most tests here are near-misses that must be refused: the same title
by another artist, a live or remix version, a different length. Every name is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import assume, given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above

#: The invented track every case is matched against, 200 seconds long.
QUERY = lm.TrackQuery(
    "Invented Artist", "The Keeper Counts", album="Lamp Room", duration_s=200.4
)


def _hit(
    title: str = "The Keeper Counts",
    artists: tuple[str, ...] = ("Invented Artist",),
    duration_s: float | None = 200.0,
) -> lmatch.Hit:
    """A hit that matches QUERY unless a field is overridden."""
    return lmatch.Hit(title, artists, duration_s)


class TitleKeyTest(fixtures.IsolatedCase):
    """Case, accents, punctuation, featuring credits and qualifier groups fold away."""

    def test_case_accents_and_punctuation_fold(self) -> None:
        self.assertEqual(lmatch.title_key("Ça, C'est Vrai!"), "cacestvrai")

    def test_bracket_groups_anywhere_are_removed(self) -> None:
        self.assertEqual(
            lmatch.title_key("Lamp (Remastered 2011) Song [Mono] {x}"), "lampsong"
        )

    def test_a_featuring_credit_outside_brackets_is_removed(self) -> None:
        for title in (
            "Lamp Song feat. Someone",
            "Lamp Song ft. Someone",
            "Lamp Song FEATURING Someone",
        ):
            with self.subTest(title=title):
                self.assertEqual(lmatch.title_key(title), "lampsong")

    def test_a_dash_qualifier_is_removed(self) -> None:
        self.assertEqual(lmatch.title_key("Lamp Song - 2011 Remaster"), "lampsong")

    def test_a_bare_feat_or_ft_word_is_the_titles_own(self) -> None:
        # Only a written credit cuts: "feat." or "ft." with the period, or "featuring".
        for title, key in (
            ("A Feat of Clay", "afeatofclay"),
            ("The Feat of Clay", "thefeatofclay"),
            ("Lamp ft Someone", "lampftsomeone"),
            ("Lamp Featured", "lampfeatured"),
        ):
            with self.subTest(title=title):
                self.assertEqual(lmatch.title_key(title), key)

    def test_a_dash_qualifier_is_cut_however_short_the_rest(self) -> None:
        self.assertEqual(lmatch.title_key("Up - 2011 Remaster"), "up")
        self.assertEqual(lmatch.title_key("Go feat. Guest"), "go")

    def test_a_short_title_with_nothing_cut_keeps_its_key(self) -> None:
        self.assertEqual(lmatch.title_key("Up"), "up")

    def test_ampersand_reads_as_and(self) -> None:
        self.assertEqual(
            lmatch.title_key("Salt & Iron"), lmatch.title_key("Salt and Iron")
        )

    def test_a_title_that_is_only_a_group_keeps_its_words(self) -> None:
        self.assertEqual(lmatch.title_key("(Intro)"), "intro")

    def test_a_title_of_punctuation_alone_keeps_its_characters(self) -> None:
        self.assertEqual(lmatch.title_key(" ?! "), "?!")

    def test_blank_is_empty(self) -> None:
        self.assertEqual(lmatch.title_key("  "), "")

    def test_an_oversized_name_is_cut_before_folding(self) -> None:
        key = lmatch.title_key("a" * (lmatch.MAX_NAME_CHARS * 4))
        self.assertEqual(len(key), lmatch.MAX_NAME_CHARS)


class ArtistKeyTest(fixtures.IsolatedCase):
    """An artist also loses a leading "the" and every featured artist."""

    def test_a_leading_the_is_dropped(self) -> None:
        self.assertEqual(lmatch.artist_key("The Lamplighters"), "lamplighters")

    def test_a_featured_artist_is_dropped(self) -> None:
        self.assertEqual(
            lmatch.artist_key("Invented Artist feat. Other"), "inventedartist"
        )

    def test_a_band_called_the_alone_keeps_its_name(self) -> None:
        self.assertEqual(lmatch.artist_key("The"), "the")


class MatchTest(fixtures.IsolatedCase):
    """The rules a hit must pass, each shown by the near-miss it refuses."""

    def test_the_same_track_matches(self) -> None:
        self.assertIsNotNone(lmatch.match_score(QUERY, _hit()))

    def test_spelling_variants_still_match(self) -> None:
        hit = _hit("the keeper counts (feat. Someone)", ("INVENTED ARTIST",))
        self.assertIsNotNone(lmatch.match_score(QUERY, hit))

    def test_the_same_title_by_another_artist_is_refused(self) -> None:
        self.assertIsNone(lmatch.match_score(QUERY, _hit(artists=("Other Singer",))))

    def test_another_title_by_the_same_artist_is_refused(self) -> None:
        self.assertIsNone(lmatch.match_score(QUERY, _hit("The Keeper Sleeps")))

    def test_a_numbered_sequel_is_refused(self) -> None:
        query = lm.TrackQuery("Invented Artist", "Lamp Song 2")
        self.assertIsNone(lmatch.match_score(query, _hit("Lamp Song 3")))

    def test_a_live_version_of_another_length_is_refused(self) -> None:
        self.assertIsNone(lmatch.match_score(QUERY, _hit(duration_s=262.0)))

    def test_a_live_version_is_refused_even_at_the_same_length(self) -> None:
        for title in ("The Keeper Counts (Live)", "The Keeper Counts - Live at Hall"):
            with self.subTest(title=title):
                self.assertIsNone(lmatch.match_score(QUERY, _hit(title)))

    def test_a_version_word_beside_cjk_text_is_still_read(self) -> None:
        self.assertIsNone(lmatch.match_score(QUERY, _hit("The Keeper Counts (Live版)")))

    def test_each_cjk_version_marker_is_refused_even_at_the_same_length(self) -> None:
        for marker in ("伴奏", "现场", "翻唱", "纯音乐"):
            with self.subTest(marker=marker):
                hit = _hit(f"The Keeper Counts ({marker})")
                self.assertIsNone(lmatch.match_score(QUERY, hit))

    def test_a_cjk_marker_the_track_also_has_matches(self) -> None:
        query = lm.TrackQuery("Invented Artist", "The Keeper Counts (伴奏)")
        self.assertIsNotNone(
            lmatch.match_score(query, _hit("The Keeper Counts [伴奏]"))
        )

    def test_a_remix_is_refused_with_no_length_known(self) -> None:
        hit = _hit("The Keeper Counts (Night Remix)", duration_s=None)
        self.assertIsNone(lmatch.match_score(QUERY, hit))

    def test_a_live_track_matches_a_live_hit(self) -> None:
        query = lm.TrackQuery("Invented Artist", "The Keeper Counts (Live)")
        self.assertIsNotNone(
            lmatch.match_score(query, _hit("The Keeper Counts [Live]"))
        )

    def test_a_length_inside_the_tolerance_matches(self) -> None:
        inside = 200.4 + lmatch.DURATION_TOLERANCE_S - 0.01
        self.assertIsNotNone(lmatch.match_score(QUERY, _hit(duration_s=inside)))

    def test_a_length_just_past_the_tolerance_is_refused(self) -> None:
        outside = 200.4 + lmatch.DURATION_TOLERANCE_S + 0.01
        self.assertIsNone(lmatch.match_score(QUERY, _hit(duration_s=outside)))

    def test_an_unknown_or_unusable_length_is_not_compared(self) -> None:
        for duration in (None, 0.0, -5.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                self.assertIsNotNone(
                    lmatch.match_score(QUERY, _hit(duration_s=duration))
                )

    def test_a_query_without_a_length_compares_none(self) -> None:
        query = lm.TrackQuery("Invented Artist", "The Keeper Counts")
        self.assertIsNotNone(lmatch.match_score(query, _hit(duration_s=999.0)))

    def test_a_title_that_is_not_a_featuring_credit_is_not_cut_to_match(self) -> None:
        for title, hit in (("A Feat of Clay", "A"), ("The Feat of Clay", "The")):
            with self.subTest(title=title):
                query = lm.TrackQuery("Invented Artist", title)
                self.assertIsNone(lmatch.match_score(query, _hit(hit)))

    def test_a_short_title_matches_its_qualified_or_credited_hit(self) -> None:
        # A one- or two-letter key left after the cut still matches: no minimum key
        # length applies.
        for title, hit in (
            ("Up", "Up - 2011 Remaster"),
            ("Go", "Go feat. Guest"),
            ("Lamp", "Lamp ft. Guest"),
            ("Lamp", "Lamp featuring Guest"),
        ):
            with self.subTest(hit=hit):
                query = lm.TrackQuery("Invented Artist", title)
                self.assertIsNotNone(lmatch.match_score(query, _hit(hit)))

    def test_a_real_featuring_credit_is_still_cut_to_match(self) -> None:
        query = lm.TrackQuery("Invented Artist", "Lamp feat. Guest Singer")
        self.assertIsNotNone(lmatch.match_score(query, _hit("Lamp")))

    def test_a_short_title_matches_itself(self) -> None:
        query = lm.TrackQuery("Invented Artist", "Up", duration_s=200.0)
        self.assertIsNotNone(lmatch.match_score(query, _hit("Up")))

    def test_the_first_of_several_credited_artists_matches(self) -> None:
        hit = _hit(artists=("Invented Artist", "Guest Singer"))
        self.assertIsNotNone(lmatch.match_score(QUERY, hit))

    def test_a_joint_credit_matches_all_of_its_artists(self) -> None:
        query = lm.TrackQuery("Invented Artist & Guest Singer", "The Keeper Counts")
        hit = _hit(artists=("Invented Artist", "Guest Singer"))
        self.assertIsNotNone(lmatch.match_score(query, hit))

    def test_a_hit_crediting_the_artist_second_is_refused(self) -> None:
        hit = _hit(artists=("Guest Singer", "Invented Artist"))
        self.assertIsNone(lmatch.match_score(QUERY, hit))

    def test_a_hit_with_no_artist_is_refused(self) -> None:
        self.assertIsNone(lmatch.match_score(QUERY, _hit(artists=())))

    def test_a_query_with_no_artist_or_title_matches_nothing(self) -> None:
        for query in (
            lm.TrackQuery("", "The Keeper Counts"),
            lm.TrackQuery("Invented Artist", " "),
        ):
            with self.subTest(query=query):
                self.assertIsNone(lmatch.match_score(query, _hit()))


class QueryFactsTest(fixtures.IsolatedCase):
    """What a query offers the providers: an artist to confirm a hit by, and a length."""

    def test_a_query_has_an_artist_only_when_it_names_one(self) -> None:
        self.assertTrue(lmatch.has_artist(QUERY))
        for blank in ("", " ", "\t\n"):
            with self.subTest(artist=blank):
                self.assertFalse(lmatch.has_artist(lm.TrackQuery(blank, "Lamp")))

    def test_a_usable_length_is_finite_and_positive(self) -> None:
        self.assertEqual(lmatch.usable_length(200.4), 200.4)
        for duration in (None, 0.0, -5.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                self.assertIsNone(lmatch.usable_length(duration))


class RankTest(fixtures.IsolatedCase):
    """Only accepted hits are ranked: the closest names first, then the closest length."""

    def test_refused_hits_are_left_out(self) -> None:
        hits = [_hit(artists=("Other Singer",)), _hit()]
        self.assertEqual(lmatch.rank_hits(QUERY, hits, lambda hit: hit), [hits[1]])

    def test_the_closer_name_ranks_first(self) -> None:
        near, exact = _hit("The Keeper Count"), _hit()
        ranked = lmatch.rank_hits(QUERY, [near, exact], lambda hit: hit)
        self.assertEqual(ranked, [exact, near])

    def test_on_equal_names_the_closer_length_ranks_first(self) -> None:
        far, near = _hit(duration_s=202.0), _hit(duration_s=200.0)
        ranked = lmatch.rank_hits(QUERY, [far, near], lambda hit: hit)
        self.assertEqual(ranked, [near, far])

    def test_an_unknown_length_ranks_after_a_known_one(self) -> None:
        unknown, known = _hit(duration_s=None), _hit(duration_s=202.0)
        ranked = lmatch.rank_hits(QUERY, [unknown, known], lambda hit: hit)
        self.assertEqual(ranked, [known, unknown])

    def test_full_ties_keep_the_providers_order(self) -> None:
        first, second = _hit(), _hit()
        ranked = lmatch.rank_hits(QUERY, [first, second], lambda hit: hit)
        self.assertEqual([id(hit) for hit in ranked], [id(first), id(second)])


#: Names in the shapes catalogs use: words, brackets, credits, dashes, punctuation.
_NAMES = st.lists(
    st.one_of(
        st.text(min_size=1, max_size=12),
        st.sampled_from(
            ["(", ")", "[", "]", "{", "}", " - ", "feat.", "&", "The", "Live"]
        ),
    ),
    max_size=12,
).map(" ".join)


class MatchPropertyTest(fixtures.IsolatedCase):
    """The keys never fail; a hit naming the query exactly is always accepted."""

    @given(st.one_of(st.text(max_size=300), _NAMES))
    def test_fuzz_title_key(self, text: str) -> None:
        key = lmatch.title_key(text)
        self.assertEqual(key, "".join(key.split()))
        self.assertLessEqual(len(key), lmatch.MAX_NAME_CHARS)
        self.assertEqual(key == "", not text[: lmatch.MAX_NAME_CHARS].strip())

    @given(st.one_of(st.text(max_size=300), _NAMES))
    def test_fuzz_artist_key(self, text: str) -> None:
        key = lmatch.artist_key(text)
        self.assertEqual(key, "".join(key.split()))
        self.assertEqual(key == "", not text[: lmatch.MAX_NAME_CHARS].strip())

    @given(_NAMES, _NAMES, st.one_of(st.none(), st.floats(min_value=1, max_value=1e4)))
    def test_fuzz_match_score(
        self, artist: str, title: str, duration: float | None
    ) -> None:
        query = lm.TrackQuery(artist, title, duration_s=duration)
        score = lmatch.match_score(query, lmatch.Hit(title, (artist,), duration))
        named = bool(artist.strip() and title.strip())
        self.assertEqual(score is not None, named)
        other = "someone else entirely"
        theirs = lmatch.similarity(lmatch.artist_key(artist), lmatch.artist_key(other))
        assume(theirs < lmatch.ARTIST_MIN_SIMILARITY)
        hit = lmatch.Hit(f"{title} x", (other,), duration)
        self.assertIsNone(lmatch.match_score(query, hit))


#: Titles that can hold no version word or marker: every version word has a vowel,
#: and every marker is CJK.
_PLAIN_TITLES = st.text(
    alphabet="bcdfghjklnpqrstvwxz ", min_size=1, max_size=20
).filter(str.strip)
#: Every version word and marker the matcher refuses on.
_MARKERS = st.sampled_from(sorted(lmatch.VERSION_WORDS | lmatch.VERSION_MARKERS))
_LENGTHS = st.floats(min_value=1, max_value=1e4)


class RefusalPropertyTest(fixtures.IsolatedCase):
    """Each refusal rule holds whatever the names around it."""

    @given(_PLAIN_TITLES, _MARKERS, st.booleans())
    def test_fuzz_a_version_only_the_hit_names_is_refused(
        self, title: str, marker: str, bracketed: bool
    ) -> None:
        query = lm.TrackQuery("Invented Artist", title)
        named = f"{title} ({marker})" if bracketed else f"{title} - {marker}"
        self.assertIsNone(lmatch.match_score(query, _hit(named, duration_s=None)))
        # Non-vacuous: the version rule alone refused it. Without the marker the hit
        # is accepted, and a track naming the same version accepts it too.
        self.assertIsNotNone(lmatch.match_score(query, _hit(title, duration_s=None)))
        versioned = lm.TrackQuery("Invented Artist", named)
        self.assertIsNotNone(
            lmatch.match_score(versioned, _hit(named, duration_s=None))
        )

    @given(_PLAIN_TITLES, st.integers(0, 10**6), st.integers(0, 10**6))
    def test_fuzz_unequal_unbracketed_numbers_are_refused(
        self, title: str, mine: int, theirs: int
    ) -> None:
        assume(mine != theirs)
        query = lm.TrackQuery("Invented Artist", f"{title} {mine}")
        hit = _hit(f"{title} {theirs}", duration_s=None)
        self.assertIsNone(lmatch.match_score(query, hit))

    @given(_PLAIN_TITLES, _LENGTHS, st.floats(min_value=0.001, max_value=1e4))
    def test_fuzz_a_length_gap_past_the_tolerance_is_refused(
        self, title: str, length: float, extra: float
    ) -> None:
        query = lm.TrackQuery("Invented Artist", title, duration_s=length)
        far = length + lmatch.DURATION_TOLERANCE_S + extra
        self.assertIsNone(lmatch.match_score(query, _hit(title, duration_s=far)))
