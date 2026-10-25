"""The title-suffix strip: trailing bracketed groups go, everything else stays.

Every title here is invented. The property test explores titles of any shape: the strip
reads a tag another tool wrote, so it must never cut into the name itself.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_titles as lt  # noqa: E402 - import follows the sys.path bootstrap above

# A registered profile, as test_lyrics_model registers its own: deadline=None because a
# per-example deadline measures machine load, and too_slow suppressed because a cold
# cache builds Hypothesis's unicode tables inside that health check's window.
settings.register_profile(
    "album_title_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_title_properties")


class StripTitleSuffixesTest(fixtures.IsolatedCase):
    """strip_title_suffixes: trailing bracketed groups only, repeatedly."""

    def test_trailing_groups_are_removed_repeatedly(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song (feat. X) [Live]"), "Song")

    def test_a_group_without_a_space_is_removed(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song(Live)"), "Song")

    def test_nested_groups_go_as_one(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song (Remix (Extended)) "), "Song")

    def test_a_group_mid_title_is_kept(self) -> None:
        self.assertEqual(
            lt.strip_title_suffixes("Song (Part One) Again"), "Song (Part One) Again"
        )

    def test_a_title_that_is_all_brackets_comes_back_unchanged(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("(Untitled)"), "(Untitled)")

    def test_a_title_with_no_group_comes_back_unchanged_spaces_and_all(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song  "), "Song  ")

    def test_an_unbalanced_closer_stops_the_strip(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song (Live) x)"), "Song (Live) x)")
        self.assertEqual(lt.strip_title_suffixes("Song ]"), "Song ]")

    def test_a_mismatched_pair_is_not_a_group(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song (Live]"), "Song (Live]")

    def test_crossed_brackets_leave_the_whole_title_unchanged(self) -> None:
        for title in ("Song ([Live)]", "Song ([Live)] (Remix)", "Song (a [b) c]"):
            with self.subTest(title=title):
                self.assertEqual(lt.strip_title_suffixes(title), title)

    def test_a_stray_opener_left_behind_ends_the_strip_normally(self) -> None:
        self.assertEqual(lt.strip_title_suffixes("Song :( (Live)"), "Song :(")

    @given(st.text(max_size=40))
    def test_the_result_is_a_prefix_and_stripping_again_changes_nothing(
        self, title: str
    ) -> None:
        stripped = lt.strip_title_suffixes(title)
        self.assertTrue(title.startswith(stripped))
        self.assertEqual(lt.strip_title_suffixes(stripped), stripped)


#: An invented query whose title has a trailing group to strip.
_QUERY = lm.TrackQuery("Invented Artist", "Song (Live)")
_EMPTY, _ERROR = lm.AttemptOutcome.EMPTY, lm.AttemptOutcome.ERROR


class StrippedRetryTest(fixtures.IsolatedCase):
    """stripped_retry: the one rule the chain and compare both retry by."""

    def test_nothing_found_by_a_step_that_answered_retries_the_bare_title(
        self,
    ) -> None:
        again = lt.stripped_retry(_QUERY, (_ERROR, _EMPTY), found=False)
        self.assertEqual(again, lm.TrackQuery("Invented Artist", "Song", stripped=True))

    def test_lyrics_found_is_no_retry(self) -> None:
        self.assertIsNone(lt.stripped_retry(_QUERY, (_EMPTY,), found=True))

    def test_every_step_failing_is_no_retry(self) -> None:
        self.assertIsNone(lt.stripped_retry(_QUERY, (_ERROR,), found=False))
        self.assertIsNone(lt.stripped_retry(_QUERY, (), found=False))

    def test_a_title_that_strips_to_itself_is_no_retry(self) -> None:
        bare = lm.TrackQuery("Invented Artist", "Song")
        self.assertIsNone(lt.stripped_retry(bare, (_EMPTY,), found=False))


if __name__ == "__main__":
    unittest.main()
