"""The shared types and errors, and the LRC parse / format pair.

Every lyric here is invented for these tests (see fixtures.py). The property tests at
the bottom explore the LRC parser's input space: it reads text from sidecar files and
providers, so it is a parser of untrusted input, and a handful of examples cannot prove
it never crashes or that its own output reads back unchanged.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above


class LyricsTypesTest(fixtures.IsolatedCase):
    """SyncedLine, SyncedLyrics and PlainLyrics."""

    def test_synced_lyrics_report_their_kind_and_plain_text(self) -> None:
        lyrics = fixtures.synced_lines(
            (1000, "First line"), (2000, ""), (3000, "Last line")
        )
        self.assertIs(lyrics.kind, lm.LyricsKind.SYNCED)
        self.assertEqual(lyrics.plain_text(), "First line\n\nLast line")

    def test_plain_lyrics_report_their_kind_and_text(self) -> None:
        lyrics = lm.PlainLyrics("one\r\ntwo")
        self.assertIs(lyrics.kind, lm.LyricsKind.UNSYNCED)
        self.assertEqual(lyrics.plain_text(), "one\r\ntwo")

    def test_a_negative_timestamp_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "negative"):
            lm.SyncedLine(-1, "x")

    def test_synced_lyrics_need_at_least_one_line(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            lm.SyncedLyrics(())

    def test_kind_values_are_the_manifest_words(self) -> None:
        self.assertEqual([kind.value for kind in lm.LyricsKind], ["synced", "unsynced"])


class ExitTwoErrorsTest(fixtures.IsolatedCase):
    """The could-not-run errors are LyricsErrors, so one handler catches them all."""

    def test_each_is_a_lyrics_error(self) -> None:
        for error in (
            lm.NoRunError,
            lm.TrackNotInRunError,
            lm.ManifestSchemaError,
            lm.ConfigError,
        ):
            with self.subTest(error=error.__name__):
                self.assertTrue(issubclass(error, lm.LyricsError))

    def test_the_two_run_errors_are_distinct(self) -> None:
        self.assertFalse(issubclass(lm.NoRunError, lm.TrackNotInRunError))
        self.assertFalse(issubclass(lm.TrackNotInRunError, lm.NoRunError))


class ParseLrcTest(fixtures.IsolatedCase):
    """parse_lrc: what counts as synced, and how each stamp form reads."""

    def test_the_invented_lrc_parses_as_synced(self) -> None:
        lyrics = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC)
        self.assertIsInstance(lyrics, lm.SyncedLyrics)
        assert isinstance(lyrics, lm.SyncedLyrics)
        self.assertEqual(lyrics.plain_text(), fixtures.LIGHTHOUSE_PLAIN)

    def test_each_fraction_width_scales_to_milliseconds(self) -> None:
        text = "[00:01]a\n[00:01.5]b\n[00:01.50]c\n[00:01.500]d\n[01:02:03]e\n"
        self.assertEqual(
            lm.parse_lrc(text),
            fixtures.synced_lines(
                (1000, "a"), (1500, "b"), (1500, "c"), (1500, "d"), (62030, "e")
            ),
        )

    def test_minutes_past_ninety_nine_are_read(self) -> None:
        self.assertEqual(
            lm.parse_lrc("[123:04.005]x"), fixtures.synced_lines((7384005, "x"))
        )

    def test_a_minute_run_past_the_bound_is_no_stamp(self) -> None:
        # Thousands of digits once reached int() and raised past CPython's digit limit,
        # so one such line in a sidecar or a provider's answer ended the read.
        for digits in (lm.MAX_STAMP_MINUTE_DIGITS + 1, 5000):
            with self.subTest(digits=digits):
                text = f"[{'9' * digits}:00]x"
                self.assertEqual(lm.parse_lrc(text), lm.PlainLyrics(text))

    def test_the_latest_time_a_sylt_frame_holds_reads_back(self) -> None:
        latest = lm.SyncedLine(2**32 - 1, "x")
        lines = lm.format_lrc((latest,))
        self.assertEqual(lm.parse_lrc(lines), lm.SyncedLyrics((latest,)))

    def test_a_line_with_several_stamps_yields_one_line_per_stamp(self) -> None:
        self.assertEqual(
            lm.parse_lrc("[00:10.00] [00:30.00]Chorus\n[00:20.00]Verse"),
            fixtures.synced_lines(
                (10000, "Chorus"), (20000, "Verse"), (30000, "Chorus")
            ),
        )

    def test_lines_are_ordered_by_time_keeping_ties_in_file_order(self) -> None:
        self.assertEqual(
            lm.parse_lrc("[00:05.00]late\n[00:01.00]first\n[00:01.00]second"),
            fixtures.synced_lines((1000, "first"), (1000, "second"), (5000, "late")),
        )

    def test_text_is_stripped_and_blank_and_tag_lines_are_skipped(self) -> None:
        text = "[ar:Invented]\n\n  [00:01.00]  spaced out  \r\nno stamp here\n"
        self.assertEqual(
            lm.parse_lrc(text), fixtures.synced_lines((1000, "spaced out"))
        )

    def test_a_stamp_with_empty_text_is_kept(self) -> None:
        self.assertEqual(lm.parse_lrc("[00:09.00]"), fixtures.synced_lines((9000, "")))

    def test_text_without_a_stamp_is_plain_and_verbatim(self) -> None:
        text = "[ti:Invented]\nNo stamps\r\nanywhere [here]\n"
        self.assertEqual(lm.parse_lrc(text), lm.PlainLyrics(text))

    def test_seconds_past_fifty_nine_are_not_a_stamp(self) -> None:
        for stamp in ("[01:60.00]", "[01:75.00]", "[00:99]"):
            with self.subTest(stamp=stamp):
                text = f"{stamp}never timed\n"
                self.assertEqual(lm.parse_lrc(text), lm.PlainLyrics(text))

    def test_a_bad_stamp_line_among_timed_ones_is_skipped(self) -> None:
        text = "[00:01.00]first\n[01:75.00]never timed\n[00:59.99]last"
        self.assertEqual(
            lm.parse_lrc(text), fixtures.synced_lines((1000, "first"), (59990, "last"))
        )

    def test_empty_text_is_plain(self) -> None:
        self.assertEqual(lm.parse_lrc(""), lm.PlainLyrics(""))


class FormatLrcTest(fixtures.IsolatedCase):
    """format_lrc: millisecond stamps, one line each."""

    def test_stamps_are_zero_padded_milliseconds(self) -> None:
        lines = (lm.SyncedLine(0, "start"), lm.SyncedLine(61005, "later"))
        self.assertEqual(lm.format_lrc(lines), "[00:00.000]start\n[01:01.005]later\n")

    def test_minutes_grow_past_two_digits(self) -> None:
        self.assertEqual(
            lm.format_lrc((lm.SyncedLine(7384005, "x"),)), "[123:04.005]x\n"
        )

    def test_no_lines_format_to_nothing(self) -> None:
        self.assertEqual(lm.format_lrc(()), "")


class FrameRecordTest(fixtures.IsolatedCase):
    """FrameRecord: one ID3 lyrics frame verbatim, kind and payload consistent."""

    def test_a_uslt_frame_carries_text_and_no_sylt_fields(self) -> None:
        frame = lm.FrameRecord(lm.FrameKind.USLT, "eng", "", 3, None, None, "words")
        self.assertEqual(frame.payload, "words")

    def test_a_sylt_frame_carries_entries_format_and_type(self) -> None:
        entries = (lm.SyltEntry("\nline", 1500),)
        frame = lm.FrameRecord(lm.FrameKind.SYLT, "eng", "d", 1, 2, 1, entries)
        self.assertEqual(frame.payload, entries)

    def test_inconsistent_frames_are_refused(self) -> None:
        bad: Sequence[
            tuple[lm.FrameKind, int | None, int | None, str | tuple[lm.SyltEntry, ...]]
        ] = (
            (lm.FrameKind.USLT, None, None, (lm.SyltEntry("x", 0),)),
            (lm.FrameKind.USLT, 2, None, "text"),
            (lm.FrameKind.SYLT, 2, 1, "text"),
            (lm.FrameKind.SYLT, None, 1, ()),
            (lm.FrameKind.SYLT, 2, None, ()),
        )
        for kind, time_format, content_type, payload in bad:
            with self.subTest(kind=kind, payload=payload):
                with self.assertRaises(ValueError):
                    lm.FrameRecord(
                        kind, "eng", "", 3, time_format, content_type, payload
                    )

    def test_a_sylt_entry_refuses_a_negative_time(self) -> None:
        self.assertEqual(lm.SyltEntry("start", 0).time, 0)
        with self.assertRaises(ValueError):
            lm.SyltEntry("before the start", -1)

    def test_track_tags_hold_the_frames_in_order(self) -> None:
        frame = lm.FrameRecord(lm.FrameKind.USLT, "eng", "", 1, None, None, "w")
        tags = lm.TrackTags("Artist", "Title", 12.5, (2, 3), (frame,))
        self.assertEqual(tags.frames, (frame,))


class QueryAndFetchTest(fixtures.IsolatedCase):
    """TrackQuery markers, the provider table seam, and FetchResult's derived facts."""

    def test_markers_name_each_way_the_query_left_the_tags(self) -> None:
        self.assertEqual(lm.TrackQuery("A", "T").markers, ())
        self.assertEqual(
            lm.TrackQuery("A", "T", from_filename=True, stripped=True).markers,
            (lm.QueryMarker.FILENAME, lm.QueryMarker.STRIPPED),
        )
        self.assertEqual(lm.QueryMarker.STRIPPED.value, "stripped")
        self.assertEqual(lm.QueryMarker.FILENAME.value, "filename")

    def test_source_values_are_the_provider_names(self) -> None:
        self.assertEqual(
            [source.value for source in lm.Source],
            [
                "sidecar",
                "frames",
                "Musixmatch",
                "Lrclib",
                "NetEase",
                "Genius",
                "Bandcamp",
                "YouTube Music",
                "Tekstowo",
                "machine transcript",
                "machine timed",
            ],
        )

    def test_a_source_displays_its_label_while_keeping_its_value(self) -> None:
        source = lm.Source.LRCLIB
        self.assertEqual(source.label, "LRCLIB")
        self.assertEqual(
            (str(source), f"{source}", f"{source:>7}"), ("LRCLIB",) * 2 + (" LRCLIB",)
        )
        self.assertEqual(source.value, "Lrclib")
        self.assertIs(lm.Source("Lrclib"), source)

    def test_every_other_source_displays_its_value(self) -> None:
        for source in lm.Source:
            if source is not lm.Source.LRCLIB:
                with self.subTest(source=source.value):
                    self.assertEqual((source.label, str(source)), (source.value,) * 2)

    def test_a_fake_table_satisfies_the_provider_seam(self) -> None:
        table: lm.ProviderTable = {
            lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED): fixtures.answering(
                fixtures.LIGHTHOUSE_LRC
            ),
        }
        provider = table[lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)]
        self.assertEqual(provider(lm.TrackQuery("A", "T")), fixtures.LIGHTHOUSE_LRC)

    def _attempt(self, outcome: lm.AttemptOutcome) -> lm.ProviderAttempt:
        key = lm.ProviderKey(lm.Source.NETEASE, lm.LyricsKind.UNSYNCED)
        return lm.ProviderAttempt(key, False, outcome, "detail")

    def test_every_provider_failed_only_when_each_attempt_raised_or_timed_out(
        self,
    ) -> None:
        query = lm.TrackQuery("A", "T")
        failed = (
            self._attempt(lm.AttemptOutcome.ERROR),
            self._attempt(lm.AttemptOutcome.TIMEOUT),
        )
        self.assertTrue(lm.FetchResult(None, None, query, failed).every_provider_failed)
        mixed = (*failed, self._attempt(lm.AttemptOutcome.EMPTY))
        self.assertFalse(lm.FetchResult(None, None, query, mixed).every_provider_failed)
        self.assertFalse(lm.FetchResult(None, None, query, ()).every_provider_failed)
        found = lm.FetchResult(lm.PlainLyrics("w"), lm.Source.GENIUS, query, failed)
        self.assertFalse(found.every_provider_failed)
        self.assertTrue(found.found)

    def test_lyrics_and_source_come_together_or_not_at_all(self) -> None:
        query = lm.TrackQuery("A", "T")
        with self.assertRaises(ValueError):
            lm.FetchResult(lm.PlainLyrics("w"), None, query, ())
        with self.assertRaises(ValueError):
            lm.FetchResult(None, lm.Source.GENIUS, query, ())


# --- Properties --------------------------------------------------------------------

#: A line's text as format_lrc can carry it: one line, no surrounding whitespace, and
#: not opening with "[", which would read back as a stamp or a tag.
_LINE_TEXT = (
    st.text(max_size=30)
    .map(str.strip)
    .filter(
        lambda t: len(t.splitlines()) <= 1 and t == t.strip() and not t.startswith("[")
    )
)
_LINES = st.lists(
    st.builds(lm.SyncedLine, st.integers(min_value=0, max_value=10**9), _LINE_TEXT),
    min_size=1,
    max_size=20,
).map(lambda lines: tuple(sorted(lines, key=lambda line: line.ms)))

#: Text shaped like LRC often enough to reach the stamp branches, mixed with noise.
_STAMP = st.from_regex(r"\[\d{1,3}:\d{1,2}([.:]\d{1,3})?\]", fullmatch=True)
_LRC_ISH = st.lists(
    st.tuples(st.lists(_STAMP, max_size=3), st.text(max_size=20)).map(
        lambda pair: "".join(pair[0]) + pair[1]
    ),
    max_size=10,
).map("\n".join)


class LrcPropertyTest(fixtures.IsolatedCase):
    """parse_lrc never crashes, and its own output reads back unchanged."""

    @given(st.one_of(st.text(max_size=200), _LRC_ISH))
    def test_fuzz_parse_lrc(self, text: str) -> None:
        lyrics = lm.parse_lrc(text)
        if isinstance(lyrics, lm.SyncedLyrics):
            self.assertEqual(lm.parse_lrc(lm.format_lrc(lyrics.lines)), lyrics)
        else:
            self.assertEqual(lyrics, lm.PlainLyrics(text))

    @given(_LINES)
    def test_parse_reads_back_what_format_wrote(
        self, lines: tuple[lm.SyncedLine, ...]
    ) -> None:
        self.assertEqual(lm.parse_lrc(lm.format_lrc(lines)), lm.SyncedLyrics(lines))


if __name__ == "__main__":
    unittest.main()
