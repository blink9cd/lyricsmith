"""Outcome categories: what a track held before, what it got, why its lyrics went.

Built from a `ManifestTrack` alone, so the sweep can count a finished album the same
way. The removed reasons are read from texts the real check and refetch functions
produce, never from retyped words. Every name and lyric is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import album_profile  # noqa: E402, F401 - loads the shared Hypothesis profile
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check as lcheck  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refetch as lrefetch  # noqa: E402 - import follows the sys.path bootstrap above

H, W, R = lc.HeldBefore, lc.WrittenCategory, lc.RemovedReason
S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB = lm.Source.LRCLIB
WRITTEN, REMOVED = lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.REMOVED
#: A track length every invented lyric below is judged against, in seconds.
_TRACK_S = 30.0


def frame(kind: lm.FrameKind) -> lr.FrameExport:
    """A backed-up frame of *kind*; only its kind matters here."""
    suffix = ".lrc" if kind is lm.FrameKind.SYLT else ".txt"
    return lr.FrameExport(kind, "eng", "", 3, 2, 1, f"frames/01.1{suffix}")


def copy(name: str) -> lr.SidecarCopy:
    """A backed-up sidecar copy named *name*."""
    return lr.SidecarCopy(name, f"sidecars/{name}")


def link(name: str) -> lr.SidecarCopy:
    """A sidecar recorded as a link, never copied."""
    return lr.SidecarCopy(name, None, "elsewhere/" + name)


SYLT, USLT = frame(lm.FrameKind.SYLT), frame(lm.FrameKind.USLT)
NOTHING = lr.TrackBefore((), ())
UNTIMED = lr.TrackBefore((USLT,), ())
SYNCED = lr.TrackBefore((SYLT,), ())


def track(
    before: lr.TrackBefore | None,
    kind: lm.LyricsKind | None = S,
    source: lm.Source | None = LRCLIB,
    **fields: object,
) -> lr.ManifestTrack:
    """A written track that held *before* and got *kind* lyrics from *source*."""
    after = lr.TrackOutcome(WRITTEN, source, kind, **fields)  # type: ignore[arg-type]
    return lr.ManifestTrack("01.mp3", (2, 4), before, after)


def removed(implausible: str | None) -> lr.TrackOutcome:
    """A removed track's outcome, its old lyrics gone for *implausible*."""
    return lr.TrackOutcome(REMOVED, implausible=implausible)


def past_end_text() -> str:
    """The past-end detail the check records for lines past the track's end."""
    lyrics = lm.SyncedLyrics((lm.SyncedLine(60_000, "an invented line"),))
    reason = lcheck.past_end(lyrics, _TRACK_S)
    assert reason is not None
    return reason.detail


def too_many_words_text() -> str:
    """The too-many-words detail the check records for a page of words."""
    reason = lcheck.too_many_words(lm.PlainLyrics("word " * 1_000), _TRACK_S)
    assert reason is not None
    return reason.detail


class HeldBeforeTest(fixtures.IsolatedCase):
    """What a track held before: its frames' kinds and its sidecar copies' names."""

    def test_no_before_is_absent(self) -> None:
        self.assertIsNone(lc.held_before(None))

    def test_nothing_held(self) -> None:
        self.assertIs(lc.held_before(NOTHING), H.NOTHING)

    def test_a_uslt_is_untimed(self) -> None:
        self.assertIs(lc.held_before(UNTIMED), H.UNTIMED)

    def test_a_sylt_is_synced_beside_a_uslt(self) -> None:
        self.assertIs(lc.held_before(lr.TrackBefore((USLT, SYLT), ())), H.SYNCED)

    def test_a_txt_copy_is_untimed(self) -> None:
        before = lr.TrackBefore((), (copy("01.txt"),))
        self.assertIs(lc.held_before(before), H.UNTIMED)

    def test_an_lrc_copy_is_synced(self) -> None:
        before = lr.TrackBefore((USLT,), (copy("01.txt"), copy("01.lrc")))
        self.assertIs(lc.held_before(before), H.SYNCED)

    def test_a_link_was_never_copied_so_holds_nothing(self) -> None:
        before = lr.TrackBefore((), (link("01.lrc"), link("01.txt")))
        self.assertIs(lc.held_before(before), H.NOTHING)


class WrittenCategoryTest(fixtures.IsolatedCase):
    """Each written track's category, from what it held and what it got."""

    def test_the_table(self) -> None:
        cases = [
            (NOTHING, S, W.NEW_SYNCED),
            (UNTIMED, S, W.UPGRADED),
            (SYNCED, S, W.SYNCED_REPLACED),
            (SYNCED, U, W.REPLACED_BY_UNTIMED),
            (NOTHING, U, W.NEW_UNTIMED),
            (UNTIMED, U, W.UNTIMED_REPLACED),
        ]
        for before, kind, expected in cases:
            with self.subTest(before=before, kind=kind):
                self.assertIs(lc.written_category(track(before, kind)), expected)

    def test_the_track_s_own_files_are_embedded_whatever_the_kinds(self) -> None:
        own = (
            lm.Source.SIDECAR,
            lm.Source.FRAMES,
            lm.Source.MACHINE_TRANSCRIPT,
            lm.Source.MACHINE_TIMED,
        )
        for source in own:
            for before in (NOTHING, UNTIMED, SYNCED):
                with self.subTest(source=source, before=before):
                    written = track(before, S, source)
                    self.assertIs(lc.written_category(written), W.EMBEDDED)

    def test_an_absent_before_is_before_unknown(self) -> None:
        self.assertIs(lc.written_category(track(None, S)), W.BEFORE_UNKNOWN)

    def test_own_files_are_embedded_even_with_no_before(self) -> None:
        written = track(None, S, lm.Source.SIDECAR)
        self.assertIs(lc.written_category(written), W.EMBEDDED)

    def test_a_written_record_with_no_kind_is_before_unknown(self) -> None:
        self.assertIs(lc.written_category(track(NOTHING, None)), W.BEFORE_UNKNOWN)

    def test_a_placeholder_held_counts_as_untimed(self) -> None:
        placeholder = track(SYNCED, S, implausible=lrefetch.PLACEHOLDER)
        self.assertIs(lc.written_category(placeholder), W.UPGRADED)
        untimed = track(SYNCED, U, implausible=lrefetch.PLACEHOLDER)
        self.assertIs(lc.written_category(untimed), W.UNTIMED_REPLACED)

    def test_a_refetch_s_placeholder_side_leaves_the_other_side_held(self) -> None:
        refetched = track(
            SYNCED, S, implausible=lrefetch.PLACEHOLDER, refetch_implausible=True
        )
        self.assertIs(lc.written_category(refetched), W.SYNCED_REPLACED)


class RefetchUntimedTest(fixtures.IsolatedCase):
    """A ``--refetch-untimed`` track held no real synced lyrics, whatever its backup."""

    def test_each_untimed_only_shape_held_untimed(self) -> None:
        shapes = {
            # A placeholder SYLT beside a real USLT, and beside a real .txt.
            "placeholder SYLT and USLT": lr.TrackBefore((SYLT, USLT), ()),
            "placeholder SYLT and .txt": lr.TrackBefore((SYLT,), (copy("01.txt"),)),
            # A SYLT of time stamps alone.
            "stamps-only SYLT": SYNCED,
            # A .lrc with no time stamp.
            "unstamped .lrc": lr.TrackBefore((), (copy("01.lrc"),)),
        }
        for name, before in shapes.items():
            for kind, expected in ((S, W.UPGRADED), (U, W.UNTIMED_REPLACED)):
                with self.subTest(shape=name, kind=kind):
                    refetched = track(before, kind, refetch_untimed=True)
                    self.assertIs(lc.written_category(refetched), expected)

    def test_without_the_mark_the_same_backup_held_synced(self) -> None:
        self.assertIs(lc.written_category(track(SYNCED, S)), W.SYNCED_REPLACED)


class FrameTimeFormatTest(fixtures.IsolatedCase):
    """A SYLT timed in MPEG frames holds untimed lyrics, as the run reads it."""

    def test_an_mpeg_timed_sylt_is_untimed(self) -> None:
        mpeg = lr.FrameExport(
            lm.FrameKind.SYLT, "eng", "", 3, lm.SYLT_MILLISECONDS - 1, 1, "f/01.lrc"
        )
        before = lr.TrackBefore((mpeg,), ())
        self.assertIs(lc.held_before(before), H.UNTIMED)
        self.assertIs(lc.written_category(track(before, S)), W.UPGRADED)

    def test_an_mpeg_timed_sylt_beside_a_millisecond_one_is_synced(self) -> None:
        mpeg = lr.FrameExport(
            lm.FrameKind.SYLT, "eng", "", 3, lm.SYLT_MILLISECONDS - 1, 1, "f/01.lrc"
        )
        self.assertIs(lc.held_before(lr.TrackBefore((mpeg, SYLT), ())), H.SYNCED)

    def test_only_written_tracks_have_a_category(self) -> None:
        for status in lr.OutcomeStatus:
            if status is WRITTEN:
                continue
            after = lr.TrackOutcome(status, LRCLIB, S)
            entry = lr.ManifestTrack("01.mp3", (2, 4), NOTHING, after)
            with self.subTest(status=status):
                self.assertIsNone(lc.written_category(entry))
        self.assertIsNone(lc.written_category(lr.ManifestTrack("01.mp3", None, None)))


class RemovedReasonTest(fixtures.IsolatedCase):
    """A removed track's reason, read from the text its refetch recorded."""

    def test_a_placeholder(self) -> None:
        self.assertIs(lc.removed_reason(removed(lrefetch.PLACEHOLDER)), R.PLACEHOLDER)

    def test_past_end_from_the_check_s_own_detail(self) -> None:
        self.assertIs(lc.removed_reason(removed(past_end_text())), R.PAST_END)

    def test_too_many_words_from_the_check_s_own_detail(self) -> None:
        reason = lc.removed_reason(removed(too_many_words_text()))
        self.assertIs(reason, R.TOO_MANY_WORDS)

    def test_anything_else_is_other(self) -> None:
        for implausible in ("an invented reason", "", None):
            with self.subTest(implausible=implausible):
                self.assertIs(lc.removed_reason(removed(implausible)), R.OTHER)

    def test_both_sides_read_the_first_named_rule(self) -> None:
        both = lrefetch.RefetchTarget(None, "an invented reason", past_end_text())
        self.assertIsNotNone(both.implausible)
        self.assertIs(lc.removed_reason(removed(both.implausible)), R.PAST_END)
        named = lrefetch.RefetchTarget(None, too_many_words_text(), past_end_text())
        reason = lc.removed_reason(removed(named.implausible))
        self.assertIs(reason, R.TOO_MANY_WORDS)


class CountTest(fixtures.IsolatedCase):
    """Written tracks by category and removed tracks by reason, and their lines."""

    def test_counts(self) -> None:
        tracks = [
            track(UNTIMED, S),
            track(UNTIMED, S),
            track(NOTHING, S),
            lr.ManifestTrack("04.mp3", (2, 4), NOTHING, removed(lrefetch.PLACEHOLDER)),
            lr.ManifestTrack("05.mp3", (2, 4), NOTHING, removed(past_end_text())),
            lr.ManifestTrack("06.mp3", (2, 4), NOTHING, lr.TrackOutcome(WRITTEN)),
            lr.ManifestTrack("07.mp3", None, None),
            lr.ManifestTrack("08.mp3", (2, 4), NOTHING, lr.TrackOutcome(REMOVED)),
        ]
        counts = lc.count_categories(tracks)
        self.assertEqual(
            counts.written,
            {W.UPGRADED: 2, W.NEW_SYNCED: 1, W.BEFORE_UNKNOWN: 1},
        )
        self.assertEqual(counts.removed, {R.PLACEHOLDER: 1, R.PAST_END: 1, R.OTHER: 1})

    def test_the_lines_in_the_documented_order(self) -> None:
        counts = lc.OutcomeCategories(
            {W.BEFORE_UNKNOWN: 1, W.EMBEDDED: 2, W.NEW_SYNCED: 1, W.UPGRADED: 3},
            {R.OTHER: 1, R.PAST_END: 1, R.PLACEHOLDER: 1},
        )
        self.assertEqual(
            lc.format_written(counts),
            "written: upgraded 3, new synced 1, embedded 2, before unknown 1",
        )
        self.assertEqual(
            lc.format_removed(counts), "removed: placeholder 1, past end 1, other 1"
        )

    def test_every_category_has_its_place(self) -> None:
        counts = lc.OutcomeCategories(dict.fromkeys(W, 1), dict.fromkeys(R, 1))
        written = ", ".join(f"{category} 1" for category in lc.WRITTEN_ORDER)
        self.assertEqual(lc.format_written(counts), f"written: {written}")
        self.assertEqual(set(lc.WRITTEN_ORDER), set(W))
        self.assertEqual(len(lc.WRITTEN_ORDER), len(W))

    def test_zero_counts_are_left_out_and_empty_is_no_line(self) -> None:
        counts = lc.OutcomeCategories({W.UPGRADED: 0}, {R.OTHER: 0})
        self.assertIsNone(lc.format_written(counts))
        self.assertIsNone(lc.format_removed(counts))
        self.assertIsNone(lc.format_written(lc.count_categories([])))

    def test_the_values_are_their_words(self) -> None:
        self.assertEqual(str(R.PAST_END), lr.CheckRule.PAST_END.value)
        self.assertEqual(str(R.TOO_MANY_WORDS), lr.CheckRule.TOO_MANY_WORDS.value)


_BEFORES = st.builds(
    lr.TrackBefore,
    st.lists(st.sampled_from((SYLT, USLT)), max_size=3).map(tuple),
    st.lists(
        st.builds(
            lambda name, linked: link(name) if linked else copy(name),
            st.sampled_from(("01.lrc", "01.txt", "01.LRC", "01.other")),
            st.booleans(),
        ),
        max_size=3,
    ).map(tuple),
)
_OUTCOMES = st.builds(
    lr.TrackOutcome,
    st.sampled_from(lr.OutcomeStatus),
    st.none() | st.sampled_from(lm.Source),
    st.none() | st.sampled_from(lm.LyricsKind),
    implausible=st.none() | st.text() | st.just(lrefetch.PLACEHOLDER),
    refetch_implausible=st.booleans(),
)


class PropertyTest(fixtures.IsolatedCase):
    """Classification never fails, and a written track gets exactly one category."""

    @given(st.none() | _BEFORES, _OUTCOMES)
    def test_every_combination_classes(
        self, before: lr.TrackBefore | None, after: lr.TrackOutcome
    ) -> None:
        entry = lr.ManifestTrack("01.mp3", (2, 4), before, after)
        category = lc.written_category(entry)
        if after.status is WRITTEN:
            self.assertIsInstance(category, W)
        else:
            self.assertIsNone(category)
        self.assertIsInstance(lc.removed_reason(after), R)
        counts = lc.count_categories([entry])
        self.assertEqual(sum(counts.written.values()), int(after.status is WRITTEN))
        self.assertEqual(sum(counts.removed.values()), int(after.status is REMOVED))


if __name__ == "__main__":
    unittest.main()
