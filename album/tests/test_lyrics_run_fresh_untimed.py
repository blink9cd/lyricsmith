"""`--fresh` declines an untimed answer for a track whose lyrics are already synced.

A fresh run never trades synced lyrics for untimed ones: when the track's backed-up
state holds a SYLT frame with a worded entry, or a timed ``.lrc`` the tool itself
reads as synced, an untimed answer is not accepted, so the track keeps its old frames
and sidecars, byte for byte, and reports `kept existing` with the reason. A synced
answer, and an untimed one for a track with no synced lyrics, are accepted when the
check calls them like the old lyrics, and a run without `--fresh` serves a track with
a worded SYLT from its frames, asking for no untimed answer at all. Each test drives the entry point with its real
wiring; only the clock and the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above

_SYNCED = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
_PLAIN = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
#: The old timed sidecar: the answer's own song, so only its kind can decline it.
_OLD_LRC = fixtures.LIGHTHOUSE_LRC
#: The old lyrics frames, timed: another transcription of the answer's own song.
_TIMED = tuple(
    (1000 + 4000 * index, line)
    for index, line in enumerate(fixtures.LIGHTHOUSE_EDITED.splitlines())
    if line
)
_SYLT = (tag_builders.sylt(*_TIMED),)
#: The same entries timed in MPEG frames: timed in the file all the same.
_SYLT_MPEG = (tag_builders.sylt(*_TIMED, time_format=1),)
#: Untimed old frames of the answer's own song.
_USLT = (tag_builders.uslt(fixtures.LIGHTHOUSE_EDITED),)
#: Timed old frames of a different song.
_SYLT_ORCHARD = (
    tag_builders.sylt(
        *(
            (1000 * n, line)
            for n, line in enumerate(fixtures.ORCHARD_PLAIN.splitlines())
        )
    ),
)
#: A .lrc of time stamps with no words: read as untimed, so never synced lyrics.
_STAMPS_LRC = "[00:01.00]\n[00:05.00]\n"
_UNSYNCED = lm.LyricsKind.UNSYNCED
#: What a declined track's row and progress line say.
_DECLINED = "kept existing: new answer untimed (existing lyrics synced)"
KEEPER = "01 Keeper.mp3"
MILL = TrackSpec("02 Mill.mp3", title="The Mill")


class FreshUntimedCase(FreshCliCase):
    """The providers answer the keeper untimed, and the mill synced, unless told."""

    def setUp(self) -> None:
        """Untimed lighthouse lyrics for the keeper; synced ones for the mill."""
        super().setUp()
        self.answer_both(
            synced={"The Mill": fixtures.LIGHTHOUSE_LRC},
            plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN},
        )

    def answer_both(
        self, *, synced: Mapping[str, str], plain: Mapping[str, str]
    ) -> None:
        """Answer each title in *synced* synced, and each in *plain* untimed."""
        self.titles = {
            _SYNCED: run_fakes.TitleProvider(synced),
            _PLAIN: run_fakes.TitleProvider(plain),
        }
        self.providers = self.titles

    def keeper(
        self,
        frames: tuple[lm.FrameRecord, ...],
        *,
        lrc: str | None = None,
        txt: str | None = None,
    ) -> list[Path]:
        """The keeper with *frames* and the sidecars given, then the mill with nothing."""
        track = TrackSpec(KEEPER, title="The Keeper", lrc=lrc, txt=txt)
        paths = fixtures.build_album(self.album, (track, MILL))
        if frames:
            tag_builders.plant_frames(paths[0], frames)
        return paths

    def assert_kept(self, paths: list[Path]) -> lr.TrackOutcome:
        """Run fresh: the keeper byte-identical and declined, the mill written."""
        before = raw(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        after = raw(paths)
        for name, data in before.items():
            if name.startswith("01 Keeper"):
                self.assertEqual(after.get(name), data, name)
        self.assertEqual(
            sorted(n for n in after if n.startswith("01 Keeper")),
            sorted(n for n in before if n.startswith("01 Keeper")),
        )
        keeper, mill = self.afters()
        assert keeper is not None and mill is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed, keeper.sidecar_written),
            (False, (), None),
        )
        self.assertEqual(mill.status, lr.OutcomeStatus.WRITTEN)
        return keeper

    def assert_accepted(self, paths: list[Path], kind: lm.LyricsKind) -> None:
        """Run fresh: the keeper's answer written, of *kind*."""
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual((keeper.status, keeper.kind), (lr.OutcomeStatus.WRITTEN, kind))
        self.assertNotIn(_DECLINED, result.stdout)


class DeclinedTest(FreshUntimedCase):
    """An untimed answer over synced lyrics is declined under --fresh."""

    def test_a_sylt_frame_and_a_timed_lrc_keep_the_track(self) -> None:
        keeper = self.assert_kept(self.keeper(_SYLT, lrc=_OLD_LRC))
        assert keeper.check is not None
        self.assertEqual(
            [(f.rule, f.detail) for f in keeper.check.flags],
            [(lr.CheckRule.UNTIMED, "existing lyrics synced")],
        )
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
        )

    def test_a_timed_lrc_alone_keeps_the_track(self) -> None:
        self.assert_kept(self.keeper((), lrc=_OLD_LRC))

    def test_a_sylt_frame_alone_keeps_the_track(self) -> None:
        self.assert_kept(self.keeper(_SYLT))

    def test_a_sylt_frame_timed_in_mpeg_frames_counts_as_synced(self) -> None:
        self.assert_kept(self.keeper(_SYLT_MPEG))

    def test_an_unlike_untimed_answer_names_both_reasons(self) -> None:
        keeper = self.assert_kept(self.keeper(_SYLT_ORCHARD))
        assert keeper.check is not None
        self.assertEqual(
            [f.rule for f in keeper.check.flags],
            [lr.CheckRule.UNLIKE_BACKUP, lr.CheckRule.UNTIMED],
        )

    def test_the_reason_and_the_tally_are_reported(self) -> None:
        self.keeper(_SYLT, lrc=_OLD_LRC)
        result = self.fresh()
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn(_DECLINED, row)
        self.assertIn(f"01 Keeper.mp3: {_DECLINED}", result.stderr)
        self.assertIn("kept existing 1", result.stdout)
        self.assertIn("flagged 0", result.stdout)
        (line,) = [r for r in result.stdout.splitlines() if r.startswith("providers:")]
        # LRCLIB answered both tracks; the declined answer is not counted as kept.
        self.assertIn("LRCLIB 1/2", line)

    def test_an_undecodable_sidecar_is_the_tracks_check_error(self) -> None:
        paths = self.keeper(())
        paths[0].with_suffix(".lrc").write_bytes(b"[00:01.00]\xff\xfe broken\n")
        before = raw(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(keeper.error.stage, lr.ErrorStage.CHECK)
        self.assertEqual(raw(paths)["01 Keeper.lrc"], before["01 Keeper.lrc"])
        self.assertEqual(raw(paths)[KEEPER], before[KEEPER])


class AcceptedTest(FreshUntimedCase):
    """A synced answer, or an untimed one over lyrics that are not synced, is written."""

    def test_an_untimed_answer_over_untimed_lyrics_is_accepted(self) -> None:
        paths = self.keeper(_USLT, txt=fixtures.LIGHTHOUSE_EDITED)
        self.assert_accepted(paths, lm.LyricsKind.UNSYNCED)

    def test_untimed_frames_with_no_sidecar_take_an_untimed_answer(self) -> None:
        self.assert_accepted(self.keeper(_USLT), lm.LyricsKind.UNSYNCED)

    def test_a_synced_answer_over_synced_lyrics_is_accepted(self) -> None:
        self.answer_both(
            synced={"The Keeper": fixtures.LIGHTHOUSE_LRC, "The Mill": _OLD_LRC},
            plain={},
        )
        self.assert_accepted(self.keeper(_SYLT, lrc=_OLD_LRC), lm.LyricsKind.SYNCED)

    def test_an_untimed_lrc_is_no_synced_lyrics(self) -> None:
        paths = self.keeper(_USLT, lrc=fixtures.LIGHTHOUSE_EDITED)
        self.assert_accepted(paths, lm.LyricsKind.UNSYNCED)

    def test_a_timed_lrc_linked_from_outside_the_album_does_not_count(self) -> None:
        # A timed .lrc beside a track of another folder, outside this album.
        elsewhere = TrackSpec("Elsewhere.mp3", lrc=_OLD_LRC)
        (other,) = fixtures.build_album(self.tmp / "elsewhere", (elsewhere,))
        paths = self.keeper(_USLT)
        paths[0].with_suffix(".lrc").symlink_to(other.with_suffix(".lrc"))
        self.assert_accepted(paths, lm.LyricsKind.UNSYNCED)

    def test_without_fresh_a_sylt_frame_serves_the_track_unasked(self) -> None:
        # Embedded synced lyrics serve like a timed sidecar, so no untimed answer is
        # ever asked for, and none can replace them.
        self.keeper(_SYLT)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual(
            (keeper.status, keeper.source, keeper.kind),
            (lr.OutcomeStatus.WRITTEN, lm.Source.FRAMES, lm.LyricsKind.SYNCED),
        )
        self.assertEqual(self.titles[_PLAIN].calls, [])
        self.assertNotIn(_DECLINED, result.stdout)

    def test_a_sylt_frame_of_stamps_alone_is_no_synced_lyrics(self) -> None:
        # Time stamps with no words: no lyrics to protect, as with a stamps-only .lrc.
        stamps = (tag_builders.sylt((1000, ""), (5000, "  ")),)
        self.assert_accepted(self.keeper(stamps), _UNSYNCED)

    def test_a_sylt_frame_with_no_entries_is_no_synced_lyrics(self) -> None:
        self.assert_accepted(self.keeper(_USLT + (tag_builders.sylt(),)), _UNSYNCED)

    def test_a_stamps_only_lrc_is_no_synced_lyrics(self) -> None:
        self.assert_accepted(self.keeper(_USLT, lrc=_STAMPS_LRC), _UNSYNCED)


class DeclinedRecordTest(FreshUntimedCase):
    """A run that declined an untimed answer, read back by check and by undo."""

    def test_a_timed_lrc_linked_from_inside_the_album_counts(self) -> None:
        target = self.album / "Keeper timed.lrc"
        paths = self.keeper(())
        target.write_bytes(_OLD_LRC.encode())
        paths[0].with_suffix(".lrc").symlink_to(target.name)
        self.assert_kept(paths)

    def test_check_does_not_judge_the_declined_track(self) -> None:
        self.keeper(_SYLT, lrc=_OLD_LRC)
        self.fresh()
        result = self.run_album("--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        rows = [line.split() for line in result.stdout.splitlines()]
        self.assertIn(["1", "01", "Keeper.mp3", "-"], rows)
        self.assertIn(["2", "02", "Mill.mp3", "PASS"], rows)
        self.assertIn("passed 1 · flagged 0 · skipped 0 · not checked 1", result.stdout)

    def test_undo_leaves_the_declined_track_byte_identical(self) -> None:
        paths = self.keeper(_SYLT, lrc=_OLD_LRC)
        before = raw(paths)
        self.fresh()
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertNotIn("displaced", result.stdout)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn("undone", row)
        self.assertEqual(raw(paths), before)
