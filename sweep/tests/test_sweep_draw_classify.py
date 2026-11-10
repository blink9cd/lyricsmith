"""The draw's classifier: a track's group, or why it is left out, from what it holds.

Pure functions over a track's timing and its manifest entries, newest first: the
provenance walk ("another source", a removal ending the walk, an undone entry
deciding nothing), the marks that make an album a wrong-text album, the withheld mark
``--withheld`` draws by, and the order in which timing, the album and provenance decide.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import draw_fixtures as df  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_draw_classify as dc  # noqa: E402 - import follows the sys.path bootstrap
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_strata import StrataGroup  # noqa: E402 - follows the sys.path bootstrap

_TRACK = "01.mp3"
_STATUS = lr.OutcomeStatus


def _walk(*outcomes: lr.TrackOutcome | None) -> dc.Stratum:
    """The provenance of a track whose entries, newest first, ended as *outcomes*."""
    return dc.provenance(df.entry(_TRACK, after) for after in outcomes)


class ProvenanceTest(sf.IsolatedCase):
    """The newest deciding entry names the group; older entries are never read."""

    def test_each_recorded_provider_and_the_sidecar_name_their_group(self) -> None:
        cases = {
            lm.Source.NETEASE: StrataGroup.NETEASE,
            lm.Source.YOUTUBE_MUSIC: StrataGroup.YOUTUBE_MUSIC,
            lm.Source.LRCLIB: StrataGroup.LRCLIB,
            lm.Source.SIDECAR: StrataGroup.OLDER_SIDECARS,
        }
        for source, group in cases.items():
            with self.subTest(source=source):
                self.assertIs(_walk(df.synced(source)), group)
                self.assertIs(_walk(df.unchanged(source)), group)

    def test_a_source_in_no_group_is_another_source(self) -> None:
        # The track's own frames, an old provider, a synced write naming none.
        for source in (lm.Source.FRAMES, lm.Source.MUSIXMATCH, lm.Source.GENIUS, None):
            with self.subTest(source=source):
                self.assertIs(_walk(df.synced(source)), dc.LeftOut.ANOTHER_SOURCE)

    def test_the_newest_synced_write_decides(self) -> None:
        older = df.synced(lm.Source.SIDECAR)
        self.assertIs(_walk(df.synced(lm.Source.NETEASE), older), StrataGroup.NETEASE)

    def test_entries_that_decide_nothing_are_passed_over(self) -> None:
        passed = (
            None,
            df.status(_STATUS.KEPT_EXISTING),
            df.status(_STATUS.NOT_FOUND),
            df.status(_STATUS.ERROR),
        )
        for after in passed:
            with self.subTest(after=after):
                self.assertIs(
                    _walk(after, df.synced(lm.Source.LRCLIB)), StrataGroup.LRCLIB
                )

    def test_an_undone_entry_decides_nothing(self) -> None:
        entries = (
            df.entry(_TRACK, df.synced(lm.Source.NETEASE), undone=True),
            df.entry(_TRACK, df.synced(lm.Source.LRCLIB)),
        )
        self.assertIs(dc.provenance(entries), StrataGroup.LRCLIB)

    def test_an_undone_removal_does_not_end_the_walk(self) -> None:
        entries = (
            df.entry(_TRACK, df.status(_STATUS.REMOVED), undone=True),
            df.entry(_TRACK, df.synced(lm.Source.LRCLIB)),
        )
        self.assertIs(dc.provenance(entries), StrataGroup.LRCLIB)

    def test_a_removal_newer_than_a_synced_write_ends_the_walk(self) -> None:
        # Whatever the track holds arrived after the removal, unrecorded.
        for removal in (_STATUS.REMOVED, _STATUS.STRIPPED):
            with self.subTest(removal=removal):
                found = _walk(df.status(removal), df.synced(lm.Source.NETEASE))
                self.assertIs(found, dc.LeftOut.NO_MANIFEST_RECORD)

    def test_an_untimed_write_newer_than_a_synced_write_ends_the_walk(self) -> None:
        # Writing the untimed sidecar unlinks the .lrc, so the .lrc there today
        # arrived after that write, unrecorded.
        found = _walk(df.untimed_write(lm.Source.GENIUS), df.synced(lm.Source.LRCLIB))
        self.assertIs(found, dc.LeftOut.NO_MANIFEST_RECORD)

    def test_an_untimed_unchanged_newer_than_a_synced_write_ends_the_walk(self) -> None:
        # The run found an untimed text and kept it, so the timed .lrc there today
        # arrived after that run, unrecorded, as after an untimed write.
        kept = lr.TrackOutcome(
            _STATUS.UNCHANGED, source=lm.Source.GENIUS, kind=lm.LyricsKind.UNSYNCED
        )
        found = _walk(kept, df.synced(lm.Source.LRCLIB))
        self.assertIs(found, dc.LeftOut.NO_MANIFEST_RECORD)

    def test_no_entry_is_no_manifest_record(self) -> None:
        self.assertIs(_walk(), dc.LeftOut.NO_MANIFEST_RECORD)
        self.assertIs(_walk(None), dc.LeftOut.NO_MANIFEST_RECORD)


class MarksTest(sf.IsolatedCase):
    """What makes an album a wrong-text album, and what --withheld draws by."""

    def test_a_wrong_text_removal_a_withheld_one_and_a_sibling_write_mark(self) -> None:
        for after in (
            df.wrong_text_removed(),
            df.withheld_removal(),
            df.sibling_write("02.mp3"),
            # A sibling's text keeps the provider a run recorded for that sibling.
            df.sibling_write("02.mp3", lm.Source.NETEASE),
            # A manifest older than the origin field: the album text alone marks.
            df.sibling_write(None),
        ):
            with self.subTest(after=after):
                self.assertTrue(dc.marks_wrong_text(df.entry(_TRACK, after)))

    def test_a_paste_s_write_marks(self) -> None:
        # A paste follows only a not-sung finding, so its write is the finding's mark.
        pasted = lr.TrackOutcome(
            _STATUS.WRITTEN,
            source=lm.Source.GENIUS,
            kind=lm.LyricsKind.UNSYNCED,
            origin=lr.PASTED_BY_HAND,
        )
        self.assertTrue(dc.marks_wrong_text(df.entry(_TRACK, pasted)))

    def test_an_undone_mark_still_marks(self) -> None:
        # The finding was made; an undo puts the text back, not the finding away.
        undone = df.entry(_TRACK, df.wrong_text_removed(), undone=True)
        self.assertTrue(dc.marks_wrong_text(undone))

    def test_other_entries_do_not_mark(self) -> None:
        for after in (
            None,
            df.status(_STATUS.REMOVED),
            df.status(_STATUS.KEPT_EXISTING),
            df.synced(lm.Source.NETEASE),
        ):
            with self.subTest(after=after):
                self.assertFalse(dc.marks_wrong_text(df.entry(_TRACK, after)))

    def test_the_withheld_mark_counts_only_on_an_entry_not_undone(self) -> None:
        self.assertTrue(dc.withheld_mark(df.entry(_TRACK, df.withheld_removal())))
        undone = df.entry(_TRACK, df.withheld_removal(), undone=True)
        self.assertFalse(dc.withheld_mark(undone))
        self.assertFalse(dc.withheld_mark(df.entry(_TRACK, None)))
        kept = df.entry(_TRACK, df.status(_STATUS.KEPT_EXISTING))
        self.assertFalse(dc.withheld_mark(kept))


class TrackStratumTest(sf.IsolatedCase):
    """Timing first, then the album, then provenance."""

    _NETEASE = (df.entry(_TRACK, df.synced(lm.Source.NETEASE)),)

    def test_a_track_not_provider_timed_is_left_out_by_its_timing(self) -> None:
        cases = {
            dc.Timing.MACHINE_TIMED: dc.LeftOut.MACHINE_TIMED,
            dc.Timing.FRAMES_ONLY: dc.LeftOut.FRAMES_ONLY,
            dc.Timing.UNTIMED: dc.LeftOut.NOT_TIMED,
            dc.Timing.NOT_TIMED: dc.LeftOut.NOT_TIMED,
            dc.Timing.UNREADABLE: dc.LeftOut.UNREADABLE,
        }
        for timing, reason in cases.items():
            for album in (False, True):
                with self.subTest(timing=timing, wrong_text_album=album):
                    found = dc.track_stratum(
                        timing, self._NETEASE, wrong_text_album=album
                    )
                    self.assertIs(found, reason)

    def test_a_provider_timed_track_of_a_wrong_text_album_is_its_group(self) -> None:
        found = dc.track_stratum(
            dc.Timing.PROVIDER_TIMED, self._NETEASE, wrong_text_album=True
        )
        self.assertIs(found, StrataGroup.WRONG_TEXT_ALBUMS)

    def test_any_other_provider_timed_track_takes_its_provenance(self) -> None:
        found = dc.track_stratum(
            dc.Timing.PROVIDER_TIMED, self._NETEASE, wrong_text_album=False
        )
        self.assertIs(found, StrataGroup.NETEASE)
