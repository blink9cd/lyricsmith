"""A paste's rules: the clutter a browser copy picks up, the cleaning, the sung judge.

`lyrics_paste` cleans a text copied off a Genius page as a run cleans Genius's scrape,
then drops the whole lines a browser copy adds that the scrape never held; it judges a
cleaned paste against a track's transcript by the fix's own rule. A pasted text's
outcome names its origin, ``pasted by hand``, which the manifest reads back. Every lyric
here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_paste as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import FOUNDRY, HARBOR, QUARRY, words  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import HeardRecord  # noqa: E402 - import follows the sys.path bootstrap above


def _record(sung: str, held: str = QUARRY) -> HeardRecord:
    """A record of a track holding *held*, heard singing *sung*."""
    path = Path("/invented/01 Harbor.mp3")
    return HeardRecord(path, lyrics_heard.text_fingerprint(held), sung)


class ClutterTest(fixtures.IsolatedCase):
    """Only whole lines a browser copy adds go; every other line stays as it was."""

    def test_a_clutter_line_goes_whole(self) -> None:
        text = "one line\nYou might also like\ntwo line\n"
        self.assertEqual(lp.strip_browser_clutter(text), "one line\ntwo line\n")

    def test_a_clutter_line_with_spaces_or_a_carriage_return_goes(self) -> None:
        text = "one\r\n  You might also like \r\ntwo\r\n"
        self.assertEqual(lp.strip_browser_clutter(text), "one\r\ntwo\r\n")

    def test_the_words_inside_a_line_stay(self) -> None:
        text = "and You might also like the sea\nyou might also like\n"
        self.assertEqual(lp.strip_browser_clutter(text), text)

    def test_a_last_line_without_a_break_is_kept_or_dropped_whole(self) -> None:
        self.assertEqual(lp.strip_browser_clutter("one\nYou might also like"), "one\n")
        self.assertEqual(lp.strip_browser_clutter("one\ntwo"), "one\ntwo")


class CleanTest(fixtures.IsolatedCase):
    """A paste cleaned as Genius's text, then judged against the track's length."""

    def test_header_trailer_and_clutter_go_section_headers_stay(self) -> None:
        text = (
            "7 ContributorsHarbor Song Lyrics\n[Chorus]\nthe gray boats sleep\n"
            "You might also like\nthe gulls dive deep\n12Embed"
        )
        lyrics, detail = lp.clean_paste(text, 200.0)
        assert lyrics is not None
        self.assertEqual(detail, "accepted")
        self.assertEqual(
            lyrics.plain_text(), "[Chorus]\nthe gray boats sleep\nthe gulls dive deep"
        )

    def test_an_oversize_paste_is_refused_untouched(self) -> None:
        text = "You might also like\n" * (lr.MAX_LYRICS_CHARS // 10)
        self.assertEqual(lp.clean_paste(text, 200.0), (None, lp.DETAIL_TOO_LARGE))

    def test_a_paste_of_clutter_alone_is_nothing(self) -> None:
        self.assertEqual(
            lp.clean_paste("You might also like\n", 200.0), (None, "nothing found")
        )

    def test_a_timed_paste_keeps_its_words_alone(self) -> None:
        lyrics, _ = lp.clean_paste("[00:01.00]the gray boats sleep\n", 200.0)
        assert lyrics is not None
        self.assertIsInstance(lyrics, lm.PlainLyrics)
        self.assertEqual(lyrics.plain_text(), "the gray boats sleep")


class SungTest(fixtures.IsolatedCase):
    """The fix's rule: a quarter of the pairs heard, the refused words passed over."""

    def test_a_sung_paste_wins_as_genius_pasted_by_hand(self) -> None:
        verdict = lp.judge_paste(
            lm.PlainLyrics(HARBOR), _record(HARBOR), lm.PlainLyrics(QUARRY)
        )
        assert not isinstance(verdict, str)
        self.assertIs(verdict.source, lm.Source.GENIUS)
        self.assertEqual(verdict.origin, lr.PASTED_BY_HAND)

    def test_an_unsung_paste_says_its_share(self) -> None:
        verdict = lp.judge_paste(
            lm.PlainLyrics(FOUNDRY), _record(HARBOR), lm.PlainLyrics(QUARRY)
        )
        heard = lyrics_heard.judge_words(words(FOUNDRY), HARBOR)
        self.assertEqual(
            verdict,
            f"not sung: {lyrics_heard.shown_share(heard)} of the {heard.pairs} word "
            "pairs heard are the "
            f"pasted text's, under the {lyrics_heard.MIN_HEARD_SHARE} needed",
        )

    def test_the_refused_words_are_never_sung(self) -> None:
        verdict = lp.judge_paste(
            lm.PlainLyrics(QUARRY.upper()), _record(QUARRY), lm.PlainLyrics(QUARRY)
        )
        self.assertEqual(verdict, lp.OWN_WRONG_TEXT)


class OriginTest(fixtures.IsolatedCase):
    """A pasted text's origin is shown after its source, and the manifest reads it."""

    def test_the_row_names_the_paste(self) -> None:
        outcome = lr.TrackOutcome(
            lr.OutcomeStatus.WRITTEN,
            lm.Source.GENIUS,
            lm.LyricsKind.UNSYNCED,
            origin=lr.PASTED_BY_HAND,
        )
        self.assertEqual(
            lyrics_outcomes.shown_source(outcome), "Genius, pasted by hand"
        )

    def test_the_manifest_reads_the_origin_back_and_refuses_other_words(self) -> None:
        after = {"status": "written", "origin": lr.PASTED_BY_HAND}
        read = lyrics_manifest.origin_value(after["origin"], "after.origin")
        self.assertEqual(read, lr.PASTED_BY_HAND)
        self.assertEqual(
            lyrics_manifest.origin_value("02 Mill.mp3", "after.origin"), "02 Mill.mp3"
        )
        with self.assertRaises(lyrics_manifest.ManifestSchemaError):
            lyrics_manifest.origin_value("typed by hand", "after.origin")


class GuardTest(fixtures.IsolatedCase):
    """A track with no record is refused before any of it is read."""

    def test_no_record_is_refused_unread(self) -> None:
        stages = mock.Mock()
        track = Path("/invented/01 Harbor.mp3")
        refusal = lp.paste_refusal(
            cast(lyrics_stages.Stages, stages), track.parent, track, None, "eng"
        )
        self.assertEqual(refusal, lp.NO_RECORD)
        self.assertEqual(stages.mock_calls, [])
