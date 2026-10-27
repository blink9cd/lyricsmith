"""The placeholder rule: what stands in for lyrics, in an answer or in what a track holds.

A placeholder is NetEase's instrumental notice, a run of music-note glyphs, text whose
every non-blank line is an LRC header tag (``[au:...]``), or synced lyrics with no word
in them. Blank text is no placeholder: it is simply nothing. The fetch's cleanup turns
each placeholder answer into nothing found, and holds a stored one as no lyrics, so a
held placeholder never serves a track. The property tests explore the rule's input
space, since it reads text that came from providers, sidecars and frames. Every lyric
here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_inventory as li  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_placeholder as ph  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB_SYNCED = lm.ProviderKey(lm.Source.LRCLIB, S)
LRCLIB_PLAIN = lm.ProviderKey(lm.Source.LRCLIB, U)
DEADLINE_S = 5.0
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
PLAIN_ANSWER = "The tide turns in the harbor\nand waits\n"
TAG_ONLY = lm.PlainLyrics("[au:comedy]\n")
REAL_UNTIMED = lm.PlainLyrics("A lamp swings on the landing\nand holds\n")


def plain(text: str) -> bool:
    """Whether *text*, held as untimed lyrics, is a placeholder."""
    return ph.is_placeholder_lyrics(lm.PlainLyrics(text))


class InstrumentalTest(fixtures.IsolatedCase):
    """Instrumental placeholders: the NetEase text and lone music-note glyphs."""

    def test_the_netease_placeholder_and_note_glyphs_are_placeholders(self) -> None:
        for text in sorted(ph.INSTRUMENTAL_PLACEHOLDERS):
            with self.subTest(text=text.encode("unicode_escape")):
                self.assertTrue(ph.is_instrumental_placeholder(f"  {text}\n"))
                self.assertTrue(plain(f"  {text}\n"))

    def test_a_run_of_note_glyphs_is_a_placeholder(self) -> None:
        self.assertTrue(ph.is_instrumental_placeholder("♪ ♫\n♪"))
        self.assertTrue(plain("♪ ♫\n♪"))

    def test_lyrics_with_a_note_glyph_are_not(self) -> None:
        self.assertFalse(ph.is_instrumental_placeholder("♪ the keeper sings"))
        self.assertFalse(plain("♪ the keeper sings"))

    def test_blank_text_is_not_a_placeholder(self) -> None:
        self.assertFalse(ph.is_instrumental_placeholder(" \n"))
        self.assertFalse(plain(" \n"))
        self.assertFalse(plain(""))

    def test_the_longer_netease_placeholder_is_one_too(self) -> None:
        # "This song is pure music with no lyrics written, please enjoy."
        longer = "此歌曲为没有填词的纯音乐，请您欣赏"
        self.assertTrue(ph.is_instrumental_placeholder(longer + "\n"))


class HeaderTagTest(fixtures.IsolatedCase):
    """Text whose every non-blank line is an LRC header tag is a placeholder."""

    def test_a_lone_author_tag_is_a_placeholder(self) -> None:
        for text in ("[au:comedy]", "[au:instrumental]\n", "  [au:comedy]  \n\n"):
            with self.subTest(text=text):
                self.assertTrue(plain(text))

    def test_several_header_lines_are_one(self) -> None:
        text = "[ar:Invented Artist]\n\n[ti:The Keeper Counts]\n[offset:+120]\n"
        self.assertTrue(plain(text))

    def test_two_tags_on_one_line_are_one(self) -> None:
        self.assertTrue(plain("[ar:Invented Artist][ti:The Keeper Counts]"))

    def test_a_value_may_hold_a_colon_and_be_empty(self) -> None:
        self.assertTrue(plain("[length: 03:20]\n[by:]"))

    def test_a_tag_beside_a_lyric_line_is_not(self) -> None:
        self.assertFalse(plain("[au:comedy]\nThe keeper climbs the stair\n"))

    def test_words_outside_the_brackets_are_not(self) -> None:
        self.assertFalse(plain("[au:comedy] the keeper climbs"))
        self.assertFalse(plain("the keeper [au:comedy]"))

    def test_a_section_header_without_a_colon_is_not(self) -> None:
        self.assertFalse(plain("[Chorus]"))

    def test_a_time_stamp_is_no_header_tag(self) -> None:
        # The name must open with a letter, so a stamp held as text never matches.
        self.assertFalse(plain("[00:01.00]"))
        self.assertFalse(plain("[1a:comedy]"))

    def test_an_unclosed_bracket_is_not(self) -> None:
        self.assertFalse(plain("[au:comedy"))


class WordlessSyncedTest(fixtures.IsolatedCase):
    """Synced lyrics with no word in them are a placeholder."""

    def test_a_lone_stamp_with_no_text_is_a_placeholder(self) -> None:
        self.assertTrue(ph.is_placeholder_lyrics(lm.parse_lrc("[00:01.00]\n")))

    def test_stamps_with_blank_text_are_a_placeholder(self) -> None:
        lyrics = lm.parse_lrc("[00:00.00]  \n[00:05.00]\n")
        self.assertIsInstance(lyrics, lm.SyncedLyrics)
        self.assertTrue(ph.is_placeholder_lyrics(lyrics))

    def test_a_timed_note_glyph_is_a_placeholder(self) -> None:
        self.assertTrue(ph.is_placeholder_lyrics(lm.parse_lrc("[00:00.00]♪\n")))

    def test_a_timed_header_tag_is_a_placeholder(self) -> None:
        self.assertTrue(ph.is_placeholder_lyrics(lm.parse_lrc("[00:00.00][au:x]\n")))

    def test_one_worded_line_makes_real_lyrics(self) -> None:
        lyrics = lm.parse_lrc("[00:00.00]\n[00:05.00]The keeper climbs\n")
        self.assertFalse(ph.is_placeholder_lyrics(lyrics))

    def test_unless_placeholder_keeps_real_lyrics_and_drops_the_rest(self) -> None:
        self.assertIs(ph.unless_placeholder(REAL_UNTIMED), REAL_UNTIMED)
        self.assertIsNone(ph.unless_placeholder(TAG_ONLY))
        self.assertIsNone(ph.unless_placeholder(None))


class AnswerTest(fixtures.IsolatedCase):
    """A placeholder answer is nothing found, at either kind of step."""

    def test_lrclibs_author_tag_answer_is_nothing_found(self) -> None:
        # Regression: LRCLIB's plain answer `[au:comedy]` was accepted as lyrics.
        lyrics, detail = lf.normalize_answer("[au:comedy]", LRCLIB_PLAIN)
        self.assertEqual((lyrics, detail), (None, lf.DETAIL_PLACEHOLDER))

    def test_header_tags_alone_are_nothing_found_at_a_synced_step(self) -> None:
        lyrics, detail = lf.normalize_answer("[ar:x]\n[ti:y]\n", LRCLIB_SYNCED)
        self.assertEqual((lyrics, detail), (None, lf.DETAIL_PLACEHOLDER))

    def test_a_stamps_only_answer_is_nothing_found(self) -> None:
        for key in (LRCLIB_SYNCED, LRCLIB_PLAIN):
            with self.subTest(kind=key.kind):
                lyrics, detail = lf.normalize_answer("[00:00.00]\n[00:04.00]\n", key)
                self.assertEqual((lyrics, detail), (None, lf.DETAIL_PLACEHOLDER))

    def test_a_tag_beside_lyrics_is_accepted(self) -> None:
        lyrics, detail = lf.normalize_answer(
            "[au:comedy]\nThe keeper climbs\n", LRCLIB_PLAIN
        )
        self.assertEqual(detail, lf.DETAIL_ACCEPTED)
        self.assertIsNotNone(lyrics)


class HeldPlaceholderTest(fixtures.IsolatedCase):
    """A placeholder a track holds reads as no lyrics held, so it never serves."""

    def table(self) -> dict[lm.ProviderKey, fixtures.FakeProvider]:
        """Every step finding nothing but the untimed ones, which answer."""
        return {
            key: fixtures.empty() if key.kind is S else fixtures.answering(PLAIN_ANSWER)
            for key in lf.CHAIN
        }

    def test_a_placeholder_txt_lets_the_untimed_steps_answer(self) -> None:
        result = lf.fetch_lyrics(
            QUERY, lm.SidecarState(TAG_ONLY), self.table(), deadline_s=DEADLINE_S
        )
        self.assertEqual(result.source, lm.Source.LRCLIB)
        self.assertEqual(result.lyrics, lm.PlainLyrics(PLAIN_ANSWER.strip()))

    def test_a_placeholder_txt_gives_way_to_worded_frames(self) -> None:
        embedded = li.EmbeddedLyrics(None, REAL_UNTIMED, (REAL_UNTIMED,))
        result = lf.fetch_lyrics(
            QUERY,
            lm.SidecarState(TAG_ONLY),
            {key: fixtures.empty() for key in lf.CHAIN},
            deadline_s=DEADLINE_S,
            embedded=embedded,
        )
        self.assertEqual(
            (result.source, result.lyrics), (lm.Source.FRAMES, REAL_UNTIMED)
        )

    def test_a_wordless_timed_sidecar_does_not_serve(self) -> None:
        stamps = lm.parse_lrc("[00:00.00]♪\n")
        result = lf.fetch_lyrics(
            QUERY, lm.SidecarState(stamps), self.table(), deadline_s=DEADLINE_S
        )
        self.assertEqual(result.source, lm.Source.LRCLIB)

    def test_placeholder_frames_give_way_to_a_worded_frame_of_their_kind(self) -> None:
        embedded = li.EmbeddedLyrics(None, TAG_ONLY, (TAG_ONLY, REAL_UNTIMED))
        bare = embedded.without_placeholders()
        self.assertEqual(bare, li.EmbeddedLyrics(None, REAL_UNTIMED, (REAL_UNTIMED,)))

    def test_placeholder_synced_frames_alone_hold_nothing(self) -> None:
        glyph = lm.parse_lrc("[00:00.00]♪\n")
        assert isinstance(glyph, lm.SyncedLyrics)
        bare = li.EmbeddedLyrics(glyph, TAG_ONLY, (glyph, TAG_ONLY))
        self.assertEqual(bare.without_placeholders(), li.NO_EMBEDDED_LYRICS)

    def test_frames_holding_no_placeholder_are_left_as_they_are(self) -> None:
        embedded = li.EmbeddedLyrics(None, REAL_UNTIMED)
        self.assertEqual(embedded.without_placeholders(), embedded)

    def test_placeholder_frame_records_are_set_aside(self) -> None:
        # A glyph SYLT, a header-tag USLT and a stamps-only SYLT go; a blank USLT,
        # which converts to nothing, and a worded USLT stay.
        glyph = tag_builders.sylt((1000, "♪"))
        tag = tag_builders.uslt("[au:instrumental]")
        stamps = tag_builders.sylt((1000, ""), (2000, " "))
        blank = tag_builders.uslt("  ")
        real = tag_builders.uslt("A lantern swings")
        frames = (glyph, blank, tag, stamps, real)
        self.assertEqual(ph.without_placeholder_frames(frames), (blank, real))


# --- Properties ----------------------------------------------------------------------

#: Every character that ends a line for `str.splitlines`, kept out of generated lines.
_LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85  "
#: Characters a tag's name may continue with, and its value may hold.
_NAME_REST = st.text(
    st.characters(blacklist_characters="[]:" + _LINE_BREAKS, codec="utf-8"),
    max_size=8,
)
_VALUE = st.text(
    st.characters(blacklist_characters="[]" + _LINE_BREAKS, codec="utf-8"),
    max_size=12,
)
_HEADER = st.builds(
    lambda first, rest, value: f"[{first}{rest}:{value}]",
    st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    _NAME_REST,
    _VALUE,
)


class PlaceholderPropertyTest(fixtures.IsolatedCase):
    """The rule never raises, and header lines alone are always a placeholder."""

    @given(st.text(max_size=200))
    def test_fuzz_is_placeholder_lyrics(self, text: str) -> None:
        for lyrics in (lm.PlainLyrics(text), lm.parse_lrc(text)):
            self.assertIsInstance(ph.is_placeholder_lyrics(lyrics), bool)

    @given(st.lists(_HEADER, min_size=1, max_size=6))
    def test_header_lines_alone_are_a_placeholder(self, headers: list[str]) -> None:
        text = "\n".join(headers)
        self.assertTrue(plain(text))
        self.assertTrue(ph.is_placeholder_lyrics(lm.parse_lrc(text)))

    @given(
        st.lists(_HEADER, min_size=1, max_size=6),
        st.text(st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=1, max_size=9),
    )
    def test_a_worded_line_makes_them_lyrics(
        self, headers: list[str], word: str
    ) -> None:
        self.assertFalse(plain("\n".join([*headers, word])))
