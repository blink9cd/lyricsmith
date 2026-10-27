"""Whether a sidecar holds a track's own embedded lyrics: the frames then keep serving.

`lyrics_inventory.sidecar_matches` compares a sidecar's lyrics with the frames' lyrics
of the same kind, as the sidecar writer and reader would carry them: a ``.lrc`` holds
each line's text stripped and reads back in time order, and a ``.txt`` reads back with
its line endings made newlines and a leading byte-order mark dropped. A ``.lrc`` or a
``.txt`` written from any worded lyrics therefore matches them, which the property
tests prove through the real writer and reader. `EmbeddedLyrics.serving` and the fetch
then let the frames serve over such a sidecar. Every lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import assume, given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as ls  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_records import MAX_LYRICS_CHARS  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented lines.
_ONE, _TWO = "Chalk birds fly off the pavement", "when the street sweeper sings"


class SyncedMatchTest(fixtures.IsolatedCase):
    """A timed sidecar matches synced frames by its lines' times and stripped text."""

    def test_the_same_lines_match(self) -> None:
        frames = fixtures.synced_lines((1000, _ONE), (4500, _TWO))
        self.assertTrue(li.sidecar_matches(frames, frames))

    def test_text_opening_with_a_line_break_matches_its_stripped_line(self) -> None:
        frames = fixtures.synced_lines((1000, f"\n{_ONE}"), (4500, f"\r\n{_TWO}  "))
        sidecar = fixtures.synced_lines((1000, _ONE), (4500, _TWO))
        self.assertTrue(li.sidecar_matches(sidecar, frames))

    def test_frames_out_of_time_order_match_the_lrc_that_reads_sorted(self) -> None:
        frames = fixtures.synced_lines((4500, _TWO), (1000, _ONE))
        sidecar = fixtures.synced_lines((1000, _ONE), (4500, _TWO))
        self.assertTrue(li.sidecar_matches(sidecar, frames))

    def test_another_time_does_not_match(self) -> None:
        frames = fixtures.synced_lines((1000, _ONE), (4500, _TWO))
        sidecar = fixtures.synced_lines((1000, _ONE), (4600, _TWO))
        self.assertFalse(li.sidecar_matches(sidecar, frames))

    def test_other_words_do_not_match(self) -> None:
        frames = fixtures.synced_lines((1000, _ONE))
        self.assertFalse(
            li.sidecar_matches(fixtures.synced_lines((1000, _TWO)), frames)
        )

    def test_an_interior_line_break_does_not_match_its_first_half(self) -> None:
        frames = fixtures.synced_lines((1000, f"{_ONE}\n{_TWO}"))
        self.assertFalse(
            li.sidecar_matches(fixtures.synced_lines((1000, _ONE)), frames)
        )

    def test_another_kind_never_matches(self) -> None:
        synced = fixtures.synced_lines((1000, _ONE))
        plain = lm.PlainLyrics(_ONE)
        self.assertFalse(li.sidecar_matches(synced, plain))
        self.assertFalse(li.sidecar_matches(plain, synced))


class UntimedMatchTest(fixtures.IsolatedCase):
    """A ``.txt`` matches untimed frames by the text its write and read leave."""

    def test_the_same_text_matches(self) -> None:
        text = f"{_ONE}\n{_TWO}\n"
        self.assertTrue(li.sidecar_matches(lm.PlainLyrics(text), lm.PlainLyrics(text)))

    def test_crlf_and_cr_endings_in_the_frames_match_newlines(self) -> None:
        sidecar = lm.PlainLyrics(f"{_ONE}\n{_TWO}\n\n")
        for frames in (f"{_ONE}\r\n{_TWO}\r\n\r\n", f"{_ONE}\r{_TWO}\r\r"):
            with self.subTest(frames=frames):
                matched = li.sidecar_matches(sidecar, lm.PlainLyrics(frames))
                self.assertTrue(matched)

    def test_a_leading_byte_order_mark_in_the_frames_matches_none(self) -> None:
        sidecar = lm.PlainLyrics(_ONE)
        bom = chr(0xFEFF)
        self.assertTrue(li.sidecar_matches(sidecar, lm.PlainLyrics(f"{bom}{_ONE}")))

    def test_trailing_whitespace_counts(self) -> None:
        sidecar = lm.PlainLyrics(f"{_ONE}\n")
        self.assertFalse(li.sidecar_matches(sidecar, lm.PlainLyrics(f"{_ONE}  \n")))

    def test_other_words_do_not_match(self) -> None:
        self.assertFalse(li.sidecar_matches(lm.PlainLyrics(_ONE), lm.PlainLyrics(_TWO)))

    def test_frames_no_txt_can_hold_match_nothing(self) -> None:
        lone_surrogate = "\udc80"
        frames = lm.PlainLyrics(f"{_ONE}{lone_surrogate}")
        self.assertFalse(li.sidecar_matches(lm.PlainLyrics(_ONE), frames))


class ServingTest(fixtures.IsolatedCase):
    """`EmbeddedLyrics.serving` sets aside a sidecar holding the frames' own lyrics."""

    def test_a_matching_sidecar_gives_way_to_the_frames(self) -> None:
        synced, plain = fixtures.synced_lines((1000, _ONE)), lm.PlainLyrics(_ONE)
        self.assertIsNone(li.EmbeddedLyrics(synced, None).serving(synced))
        self.assertIsNone(li.EmbeddedLyrics(None, plain).serving(plain))

    def test_any_other_sidecar_still_serves(self) -> None:
        synced, plain = fixtures.synced_lines((1000, _ONE)), lm.PlainLyrics(_ONE)
        self.assertIs(li.EmbeddedLyrics(None, plain).serving(synced), synced)
        self.assertIs(li.EmbeddedLyrics(synced, None).serving(plain), plain)
        other = lm.PlainLyrics(_TWO)
        self.assertIs(li.EmbeddedLyrics(synced, plain).serving(other), other)
        self.assertIsNone(li.NO_EMBEDDED_LYRICS.serving(None))


class FetchServesTheFramesTest(fixtures.IsolatedCase):
    """The fetch reports the frames as the source over a sidecar of their lyrics."""

    def test_a_matching_lrc_is_served_as_the_frames(self) -> None:
        frames = fixtures.synced_lines((1000, f"\n{_ONE}"))
        sidecar = lm.SidecarState(fixtures.synced_lines((1000, _ONE)))
        query = lm.TrackQuery("Invented Artist", "Invented Title")
        embedded = li.EmbeddedLyrics(frames, None)
        result = lf.fetch_lyrics(query, sidecar, {}, deadline_s=1.0, embedded=embedded)
        self.assertEqual((result.lyrics, result.source), (frames, lm.Source.FRAMES))

    def test_a_matching_txt_is_served_as_the_frames(self) -> None:
        frames = lm.PlainLyrics(f"{_ONE}\r\n")
        sidecar = lm.SidecarState(lm.PlainLyrics(f"{_ONE}\n"))
        query = lm.TrackQuery("Invented Artist", "Invented Title")
        embedded = li.EmbeddedLyrics(None, frames)
        result = lf.fetch_lyrics(query, sidecar, {}, deadline_s=1.0, embedded=embedded)
        self.assertEqual((result.lyrics, result.source), (frames, lm.Source.FRAMES))


#: Text UTF-8 can encode, so no lone surrogate, with no line break inside it.
_ONE_LINE = st.text(
    alphabet=st.characters(
        codec="utf-8",
        exclude_characters="\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029",
    ),
    max_size=40,
)
#: What may open or close a SYLT entry and is stripped from its ``.lrc`` line.
_EDGE = st.sampled_from(["", " ", "\n", "\r\n", "\t "])
_ENTRY = st.builds(lambda a, t, b: a + t + b, _EDGE, _ONE_LINE, _EDGE)
_LINES = st.lists(
    st.tuples(st.integers(min_value=0, max_value=10_000_000), _ENTRY),
    min_size=1,
    max_size=8,
)
_TEXT = st.text(alphabet=st.characters(codec="utf-8"), max_size=300)


class WrittenSidecarMatchesPropertyTest(fixtures.IsolatedCase):
    """A sidecar written from any worded lyrics reads back as matching them."""

    def written_and_read(self, lyrics: lm.Lyrics) -> lm.Lyrics | None:
        """*lyrics* written as their sidecar by the real writer, then read back."""
        mp3 = self.tmp / "01 Invented.mp3"
        ls.write_sidecar(mp3, lyrics)
        return ls.plan_sidecars(mp3, self.tmp).lyrics

    @given(_LINES)
    def test_a_lrc_written_from_synced_lyrics_matches_them(
        self, lines: list[tuple[int, str]]
    ) -> None:
        lyrics = fixtures.synced_lines(*lines)
        assume(any(text.strip() for _ms, text in lines))
        assume(ls.sidecar_keeps(lyrics))
        read = self.written_and_read(lyrics)
        assert read is not None
        self.assertTrue(li.sidecar_matches(read, lyrics))

    @given(_TEXT)
    def test_a_txt_written_from_untimed_lyrics_matches_them(self, text: str) -> None:
        # A leading byte-order mark is dropped on reading, so it alone is no word.
        assume(text.removeprefix("\ufeff").strip() and len(text) <= MAX_LYRICS_CHARS)
        lyrics = lm.PlainLyrics(text)
        read = self.written_and_read(lyrics)
        assert read is not None
        self.assertTrue(li.sidecar_matches(read, lyrics))
