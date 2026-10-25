"""YouTube Music's reply parsers: search results, the lyrics tab, and the lyrics.

Pure functions over decoded JSON, so every test hands a reply in directly and nothing
reaches the network. A reply in an unexpected shape must be YouTube Music's error,
never "nothing found", and the property tests hold that over arbitrary JSON keyed by
the replies' own field names. Every title, artist and lyric is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_match as lmatch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_reply as ly  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_replies as yr  # noqa: E402 - import follows the sys.path bootstrap above

# The shared profile, as test_lyrics_genre explains: registered, never a decorator.
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")

#: JSON-like values keyed by the names YouTube Music's replies use.
_JSON = http_fakes.json_values(
    (
        "contents",
        "tabbedSearchResultsRenderer",
        "tabs",
        "tabRenderer",
        "content",
        "sectionListRenderer",
        "musicShelfRenderer",
        "musicResponsiveListItemRenderer",
        "flexColumns",
        "musicResponsiveListItemFlexColumnRenderer",
        "text",
        "runs",
        "overlay",
        "navigationEndpoint",
        "watchEndpoint",
        "videoId",
        "musicVideoType",
        "browseEndpoint",
        "browseId",
        "pageType",
        "endpoint",
        "singleColumnMusicWatchNextResultsRenderer",
        "tabbedRenderer",
        "watchNextTabbedResultsRenderer",
        "elementRenderer",
        "timedLyricsData",
        "lyricLine",
        "cueRange",
        "startTimeMilliseconds",
        "musicDescriptionShelfRenderer",
        "description",
    ),
    max_size=6,
    max_leaves=20,
)


def _unexpected(test: fixtures.IsolatedCase, value: object, parse: object) -> None:
    """Assert that *parse* refuses *value* as an unexpected reply."""
    assert callable(parse)
    with test.assertRaises(lp.ProviderError) as caught:
        parse(value)
    test.assertEqual(caught.exception.detail, "unexpected reply")


class ConstantTest(fixtures.IsolatedCase):
    def test_builders_use_the_parsers_names(self) -> None:
        self.assertEqual(
            (ly.AUDIO_TRACK, ly.LYRICS_PAGE), (yr.AUDIO_TRACK, yr.LYRICS_PAGE)
        )


class SearchTest(fixtures.IsolatedCase):
    def test_three_kinds_of_result(self) -> None:
        reply = yr.search_reply(
            yr.song_item("The Keeper Counts", ("Invented Artist", "A Second Artist")),
            yr.song_item(
                "The Keeper Counts",
                ("Invented Artist",),
                video_id="vid00000002",
                video_type=yr.MUSIC_VIDEO,
            ),
            yr.song_item(
                "The Keeper Counts",
                ("Someone",),
                video_id="vid00000003",
                video_type=yr.UPLOAD,
            ),
        )
        first, second, third = ly.parse_search(reply)
        self.assertEqual(
            first,
            ly.SongResult(
                "vid00000001",
                True,
                lmatch.Hit(
                    "The Keeper Counts", ("Invented Artist", "A Second Artist"), 201.0
                ),
            ),
        )
        self.assertEqual((second.video_id, second.audio_track), ("vid00000002", False))
        self.assertEqual((third.video_id, third.audio_track), ("vid00000003", False))

    def test_hour_length(self) -> None:
        (found,) = ly.parse_search(
            yr.search_reply(yr.song_item("Long", ("A",), length="1:02:03"))
        )
        self.assertEqual(found.hit.duration_s, 3723.0)

    def test_length_run_that_is_not_a_length(self) -> None:
        (found,) = ly.parse_search(
            yr.search_reply(yr.song_item("Short", ("A",), length="Song"))
        )
        self.assertIsNone(found.hit.duration_s)

    def test_overlong_length_run_is_no_length(self) -> None:
        # Thousands of digits pass a digits-only pattern yet exceed int()'s limit.
        run = "9" * 5000 + ":00"
        (found,) = ly.parse_search(
            yr.search_reply(yr.song_item("Long", ("A",), length=run))
        )
        self.assertIsNone(found.hit.duration_s)

    def test_no_artist_run_is_an_empty_credit(self) -> None:
        (found,) = ly.parse_search(yr.search_reply(yr.song_item("Alone", ())))
        self.assertEqual(found.hit.artists, ())

    def test_empty_search(self) -> None:
        self.assertEqual(ly.parse_search(yr.empty_search_reply()), ())

    def test_no_contents_is_no_results(self) -> None:
        self.assertEqual(ly.parse_search({"responseContext": {}}), ())

    def test_section_list_without_tabs(self) -> None:
        tabbed = yr.search_reply(
            yr.song_item("The Keeper Counts", ("Invented Artist",))
        )
        contents = tabbed["contents"]
        assert isinstance(contents, dict)
        tabs = contents["tabbedSearchResultsRenderer"]["tabs"]
        assert isinstance(tabs, list)
        bare = {"contents": tabs[0]["tabRenderer"]["content"]}
        self.assertEqual(ly.parse_search(bare), ly.parse_search(tabbed))

    def test_contents_of_neither_form(self) -> None:
        _unexpected(self, {"contents": {"somethingElse": {}}}, ly.parse_search)

    def test_tabs_not_a_list(self) -> None:
        _unexpected(
            self,
            {"contents": {"tabbedSearchResultsRenderer": {"tabs": {}}}},
            ly.parse_search,
        )

    def test_reply_not_an_object(self) -> None:
        _unexpected(self, [], ly.parse_search)

    def test_contents_not_an_object(self) -> None:
        _unexpected(self, {"contents": []}, ly.parse_search)

    def test_tab_without_a_section_list(self) -> None:
        tabbed: dict[str, object] = {"tabbedSearchResultsRenderer": {"tabs": []}}
        _unexpected(self, {"contents": tabbed}, ly.parse_search)

    def test_item_with_no_subtitle(self) -> None:
        item = yr.song_item("Bare", ("A",))
        renderer = item["musicResponsiveListItemRenderer"]
        assert isinstance(renderer, dict)
        columns = renderer["flexColumns"]
        assert isinstance(columns, list)
        renderer["flexColumns"] = columns[:1]
        (found,) = ly.parse_search(yr.search_reply(item))
        self.assertEqual(found.hit, lmatch.Hit("Bare", (), None))

    def test_item_without_video_id_is_skipped(self) -> None:
        reply = yr.search_reply(
            yr.song_item("No Button", ("A",), video_id=None),
            yr.song_item("Kept", ("A",)),
        )
        self.assertEqual(
            [found.hit.title for found in ly.parse_search(reply)], ["Kept"]
        )

    def test_item_without_title_is_skipped(self) -> None:
        reply = yr.search_reply(yr.song_item("  ", ("A",)), {"notAnItem": {}}, "text")
        self.assertEqual(ly.parse_search(reply), ())


class LyricsTabTest(fixtures.IsolatedCase):
    def test_lyrics_tab_gives_its_browse_id(self) -> None:
        self.assertEqual(
            ly.parse_lyrics_tab(yr.next_reply("MPLYt_invented01")), "MPLYt_invented01"
        )

    def test_no_lyrics_tab(self) -> None:
        self.assertIsNone(ly.parse_lyrics_tab(yr.next_reply(None)))

    def test_another_shape(self) -> None:
        _unexpected(self, yr.search_reply(), ly.parse_lyrics_tab)


class TimedLyricsTest(fixtures.IsolatedCase):
    def test_timed_lines_are_lrc(self) -> None:
        reply = yr.timed_browse_reply(
            [("the keeper counts", 1230), ("the lamps", 61005)]
        )
        self.assertEqual(
            ly.parse_lyrics(reply),
            "[00:01.230]the keeper counts\n[01:01.005]the lamps\n",
        )

    def test_a_start_keeps_its_milliseconds(self) -> None:
        reply = yr.timed_browse_reply([("the keeper counts", 1234)])
        timed = lm.SyncedLine(1234, "the keeper counts")
        self.assertEqual(
            lm.parse_lrc(ly.parse_lyrics(reply) or ""), lm.SyncedLyrics((timed,))
        )

    def test_whole_number_starts(self) -> None:
        reply = yr.timed_browse_reply([("the keeper counts", 1239)], as_text=False)
        self.assertEqual(ly.parse_lyrics(reply), "[00:01.239]the keeper counts\n")

    def test_minutes_widen_past_99(self) -> None:
        reply = yr.timed_browse_reply([("a long night", 100 * 60_000 + 5_000)])
        self.assertEqual(ly.parse_lyrics(reply), "[100:05.000]a long night\n")

    def test_one_untimed_line_makes_the_answer_untimed(self) -> None:
        reply = yr.timed_browse_reply(
            [("the keeper counts", 1230), ("the lamps", None)]
        )
        self.assertEqual(ly.parse_lyrics(reply), "the keeper counts\nthe lamps\n")

    def test_start_not_a_whole_number_is_untimed(self) -> None:
        for start in ("1.5", 1.5, True, -1):
            with self.subTest(start=start):
                cue = {"startTimeMilliseconds": start}
                line = {"lyricLine": "the keeper counts", "cueRange": cue}
                reply = yr.timed_entries_reply([line])
                self.assertEqual(ly.parse_lyrics(reply), "the keeper counts\n")

    def test_entry_without_a_text_is_passed_over(self) -> None:
        kept = {"lyricLine": "the lamps", "cueRange": {"startTimeMilliseconds": "0"}}
        reply = yr.timed_entries_reply([{"cueRange": {}}, "loose", kept])
        self.assertEqual(ly.parse_lyrics(reply), "[00:00.000]the lamps\n")

    def test_line_break_in_a_line_stays_one_line(self) -> None:
        reply = yr.timed_browse_reply([("the keeper\ncounts", 1000)])
        self.assertEqual(ly.parse_lyrics(reply), "[00:01.000]the keeper counts\n")

    def test_no_word_is_none(self) -> None:
        self.assertIsNone(
            ly.parse_lyrics(yr.timed_browse_reply([("", 1000), ("  ", 2000)]))
        )


class PlainLyricsTest(fixtures.IsolatedCase):
    def test_plain_text_without_its_credit(self) -> None:
        text = "the keeper counts\nthe lamps"
        self.assertEqual(ly.parse_lyrics(yr.plain_browse_reply(text)), text)

    def test_empty_text_is_none(self) -> None:
        self.assertIsNone(ly.parse_lyrics(yr.plain_browse_reply("")))

    def test_contents_absent(self) -> None:
        _unexpected(self, {"responseContext": {}}, ly.parse_lyrics)

    def test_contents_of_neither_form(self) -> None:
        self.assertIsNone(ly.parse_lyrics({"contents": {"somethingElse": {}}}))


class PropertyTest(fixtures.IsolatedCase):
    @given(_JSON)
    def test_fuzz_parse_search(self, value: object) -> None:
        try:
            parsed = ly.parse_search(value)
        except lp.ProviderError:
            return
        for found in parsed:
            self.assertTrue(found.video_id.strip())
            self.assertTrue(found.hit.title.strip())

    @given(_JSON)
    def test_fuzz_parse_lyrics_tab(self, value: object) -> None:
        try:
            parsed = ly.parse_lyrics_tab(value)
        except lp.ProviderError:
            return
        self.assertTrue(parsed is None or isinstance(parsed, str))

    @given(_JSON)
    def test_fuzz_parse_lyrics(self, value: object) -> None:
        try:
            parsed = ly.parse_lyrics(value)
        except lp.ProviderError:
            return
        self.assertTrue(parsed is None or any(char.isalnum() for char in parsed))

    @given(
        st.lists(
            st.tuples(st.text(), st.integers(min_value=0, max_value=10**10)),
            min_size=1,
            max_size=5,
        )
    )
    def test_timed_lines_read_back_as_timed(self, lines: list[tuple[str, int]]) -> None:
        parsed = ly.parse_lyrics(yr.timed_browse_reply(lines))
        if parsed is None:
            return
        given_lines = parsed.splitlines()
        self.assertEqual(len(given_lines), len(lines))
        for line, (_, ms) in zip(given_lines, lines):
            read = lm.parse_lrc(line)
            assert isinstance(read, lm.SyncedLyrics), line
            # A line's own text may open with a stamp of its own, so look for ours,
            # to the millisecond.
            self.assertIn(ms, [timed.ms for timed in read.lines])
