"""The dry run, the check of a run and undo: ways through an album other than a run.

The other modes each have a suite of their own.

The dry run must change nothing on disk, so it is compared byte for byte; the check and
undo modes read a run the faked run folder recorded. Every lyric is invented.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above

TrackSpec = fixtures.TrackSpec


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under *root*, hidden ones included, by relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class PlanAlbumTest(fixtures.IsolatedCase):
    """The dry run reports what a run would do and changes nothing."""

    def plan(self, *, offline: bool) -> dict[str, lyrics_modes.PlannedTrack]:
        """The plan for the album under this test, by path."""
        stages = run_fakes.make_stages(run_fakes.FakeRunFolder())
        tracks = lyrics_album.discover_tracks(self.tmp).tracks
        planned = lyrics_modes.plan_album(
            self.tmp, tracks, stages, offline=offline, lang=run_fakes.LANG
        )
        return {track.path: track for track in planned}

    def build(self) -> None:
        """A timed sidecar, an untimed one to be renamed, a plain one, and none."""
        fixtures.build_album(
            self.tmp,
            (
                TrackSpec("01 A.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
                TrackSpec("02 B.mp3", lrc=fixtures.ORCHARD_PLAIN),
                TrackSpec("03 C.mp3", txt=fixtures.ORCHARD_PLAIN),
                TrackSpec("04 D.mp3", artist=None),
            ),
        )

    def test_online_each_track_says_what_it_would_use(self) -> None:
        self.build()
        plans = {path: track.plan for path, track in self.plan(offline=False).items()}
        self.assertEqual(
            plans,
            {
                "01 A.mp3": lyrics_modes.PLAN_SIDECAR_SYNCED,
                "02 B.mp3": lyrics_modes.PLAN_FETCH_OVER_TXT,
                "03 C.mp3": lyrics_modes.PLAN_FETCH_OVER_TXT,
                "04 D.mp3": lyrics_modes.PLAN_FETCH,
            },
        )

    def test_offline_only_sidecars_serve(self) -> None:
        self.build()
        plans = {path: track.plan for path, track in self.plan(offline=True).items()}
        self.assertEqual(
            list(plans.values()),
            [
                lyrics_modes.PLAN_SIDECAR_SYNCED,
                lyrics_modes.PLAN_SIDECAR_UNSYNCED,
                lyrics_modes.PLAN_SIDECAR_UNSYNCED,
                lyrics_modes.PLAN_NOTHING_OFFLINE,
            ],
        )

    def build_embedded(self) -> None:
        """Synced frames, untimed frames, MPEG-timed frames, and synced frames by a .lrc."""
        fixtures.build_album(
            self.tmp,
            (
                TrackSpec("01 A.mp3"),
                TrackSpec("02 B.mp3"),
                TrackSpec("03 C.mp3"),
                TrackSpec("04 D.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
            ),
        )
        timed = ((1000, "A lamp swings on the landing"), (4000, "and holds"))
        planted = {
            "01 A.mp3": tag_builders.sylt(*timed),
            "02 B.mp3": tag_builders.uslt("A lamp swings on the landing\n"),
            "03 C.mp3": tag_builders.sylt(*timed, time_format=1),
            "04 D.mp3": tag_builders.sylt(*timed),
        }
        for name, frame in planted.items():
            tag_builders.plant_frames(self.tmp / name, (frame,))

    def test_online_embedded_lyrics_are_planned_like_sidecars(self) -> None:
        self.build_embedded()
        plans = [track.plan for track in self.plan(offline=False).values()]
        self.assertEqual(
            plans,
            [
                lyrics_modes.PLAN_FRAMES_SYNCED,
                lyrics_modes.PLAN_FETCH_OVER_FRAMES,
                lyrics_modes.PLAN_FETCH_OVER_FRAMES,
                lyrics_modes.PLAN_SIDECAR_SYNCED,
            ],
        )
        self.assertEqual(
            (lyrics_modes.PLAN_FRAMES_SYNCED, lyrics_modes.PLAN_FETCH_OVER_FRAMES),
            ("frames (synced)", "fetch synced, else frames (unsynced)"),
        )

    def test_offline_embedded_lyrics_serve(self) -> None:
        self.build_embedded()
        plans = [track.plan for track in self.plan(offline=True).values()]
        self.assertEqual(
            plans,
            [
                lyrics_modes.PLAN_FRAMES_SYNCED,
                lyrics_modes.PLAN_FRAMES_UNSYNCED,
                lyrics_modes.PLAN_FRAMES_UNSYNCED,
                lyrics_modes.PLAN_SIDECAR_SYNCED,
            ],
        )

    def test_a_txt_sidecar_is_planned_over_untimed_frames(self) -> None:
        fixtures.build_album(
            self.tmp, (TrackSpec("01 A.mp3", txt=fixtures.ORCHARD_PLAIN),)
        )
        tag_builders.plant_frames(
            self.tmp / "01 A.mp3", (tag_builders.uslt("Words\n"),)
        )
        (planned,) = self.plan(offline=False).values()
        self.assertEqual(planned.plan, lyrics_modes.PLAN_FETCH_OVER_TXT)

    def test_embedded_lyrics_change_nothing_on_disk(self) -> None:
        self.build_embedded()
        before = snapshot(self.tmp)
        self.plan(offline=False)
        self.plan(offline=True)
        self.assertEqual(snapshot(self.tmp), before)

    def test_the_query_markers_come_with_the_plan(self) -> None:
        self.build()
        self.assertEqual(
            self.plan(offline=False)["04 D.mp3"].markers, (lm.QueryMarker.FILENAME,)
        )

    def test_nothing_on_disk_changes(self) -> None:
        self.build()
        before = snapshot(self.tmp)
        self.plan(offline=False)
        self.assertEqual(snapshot(self.tmp), before)
        self.assertFalse((self.tmp / lr.RUN_FOLDER_NAME).exists())

    def test_an_unreadable_sidecar_is_that_tracks_error(self) -> None:
        fixtures.build_album(self.tmp, (TrackSpec("01 A.mp3"),))
        (self.tmp / "01 A.txt").write_bytes(b"\xff\xfe\xfa")
        (planned,) = self.plan(offline=False).values()
        assert planned.error is not None
        self.assertEqual(
            (planned.error.stage, planned.plan), (lr.ErrorStage.SIDECAR, "-")
        )

    def test_an_unreadable_tag_is_that_tracks_error(self) -> None:
        (self.tmp / "01 A.mp3").write_bytes(b"ID3\x09\x00 not a tag")
        (planned,) = self.plan(offline=False).values()
        assert planned.error is not None
        self.assertEqual(planned.error.stage, lr.ErrorStage.FETCH)


class CheckRunTest(fixtures.IsolatedCase):
    """The check mode re-judges the tracks a run wrote, and only those."""

    def setUp(self) -> None:
        """Run a two-track album: the first is written, the second finds nothing."""
        super().setUp()
        self.folder = run_fakes.FakeRunFolder()
        self.paths = fixtures.build_album(
            self.tmp,
            (
                TrackSpec("01 A.mp3", title="The Keeper"),
                TrackSpec("02 B.mp3", title="The Mill"),
            ),
        )
        self.stages = run_fakes.make_stages(self.folder)
        lyrics_run.run_album(
            self.tmp,
            tuple(self.paths),
            run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC}),
            run_fakes.options(),
            self.stages,
            acquire_lock=run_fakes.acquire,
            started=run_fakes.STARTED,
            progress=lambda _line: None,
        )

    def check(
        self, track: str | None = None, run_id: str | None = None
    ) -> lyrics_modes.CheckReport:
        """Check this test's album."""
        return lyrics_modes.check_run(
            self.tmp, run_id=run_id, track=track, stages=self.stages
        )

    def test_the_written_track_is_checked_and_the_other_is_not(self) -> None:
        report = self.check()
        first, second = report.tracks
        assert first.check is not None
        self.assertEqual(
            (report.run_id, first.written, first.check.verdict),
            (run_fakes.RUN_ID, True, lr.CheckVerdict.PASS),
        )
        self.assertEqual(
            (second.written, second.check, second.error), (False, None, None)
        )

    def test_lyrics_removed_since_the_run_are_flagged(self) -> None:
        lyrics_tags.strip_lyrics(self.paths[0])
        first = self.check().tracks[0]
        assert first.check is not None
        self.assertEqual(first.check.flags[0].rule, lr.CheckRule.NO_LYRICS)

    def test_one_track_can_be_chosen(self) -> None:
        report = self.check(track="02 B.mp3")
        self.assertEqual([t.path for t in report.tracks], ["02 B.mp3"])

    def test_a_track_the_run_does_not_list_cannot_be_checked(self) -> None:
        with self.assertRaises(lm.TrackNotInRunError):
            self.check(track="03 C.mp3")

    def test_a_run_id_that_does_not_exist_cannot_be_checked(self) -> None:
        with self.assertRaises(lm.NoRunError):
            self.check(run_id="20000101T000000Z")

    def test_a_track_gone_since_the_run_is_its_check_error(self) -> None:
        self.paths[0].unlink()
        first = self.check().tracks[0]
        assert first.error is not None
        self.assertEqual(first.error.stage, lr.ErrorStage.CHECK)
        self.assertIsNone(first.check)

    def test_a_track_undone_since_is_not_checked(self) -> None:
        run_dir = self.stages.select_run(self.tmp, None)
        manifest = self.stages.read_manifest(run_dir)
        undone = dataclasses.replace(manifest.tracks[0], undone=True)
        tracks = (undone, *manifest.tracks[1:])
        self.folder.write_manifest(
            run_dir, dataclasses.replace(manifest, tracks=tracks)
        )
        first = self.check().tracks[0]
        self.assertEqual((first.written, first.check, first.error), (False, None, None))

    def test_the_check_changes_nothing(self) -> None:
        before = snapshot(self.tmp)
        self.check()
        self.assertEqual(snapshot(self.tmp), before)


class UndoAlbumTest(fixtures.IsolatedCase):
    """Undo picks the run first, then holds the lock while the run is undone."""

    def setUp(self) -> None:
        """A fake run folder with one run on disk."""
        super().setUp()
        self.folder = run_fakes.FakeRunFolder()
        run_dir = self.tmp / lr.RUN_FOLDER_NAME / run_fakes.RUN_ID
        run_dir.mkdir(parents=True)
        self.folder.manifests.append(
            lr.Manifest(
                run_fakes.RUN_ID,
                "eng",
                lr.RunState.COMPLETE,
                lr.GeniusState.ABSENT,
                (lr.ManifestTrack("01 A.mp3", (2, 4), lr.TrackBefore((), ())),),
            )
        )

    def test_the_chosen_run_is_undone_under_the_lock(self) -> None:
        locked: list[bool] = []
        real_undo = self.folder.undo_run

        def undo(
            album: Path,
            run_dir: Path,
            manifest: lr.Manifest,
            *,
            newest: bool,
            track: str | None,
        ) -> lr.UndoReport:
            locked.append(run_fakes.lock_held(album))
            return real_undo(album, run_dir, manifest, newest=newest, track=track)

        stages = run_fakes.make_stages(self.folder, undo=undo)
        track = "01 A.mp3"
        report = lyrics_modes.undo_album(
            self.tmp,
            run_id=None,
            track=track,
            stages=stages,
            acquire_lock=run_fakes.acquire,
        )
        self.assertEqual(locked, [True])
        self.assertEqual(self.folder.undo_calls, [(self.tmp, run_fakes.RUN_ID, track)])
        self.assertEqual(report.run_id, run_fakes.RUN_ID)
        self.assertTrue(report.newest)

    def test_the_run_is_chosen_once_and_named_to_undo(self) -> None:
        older = self.tmp / lr.RUN_FOLDER_NAME / "20000101T000000Z"
        older.mkdir()
        chosen: list[str | None] = []
        real_select = self.folder.select_run

        def select(album: Path, run_id: str | None) -> Path:
            chosen.append(run_id)
            return real_select(album, run_id)

        stages = run_fakes.make_stages(self.folder, select_run=select)
        report = lyrics_modes.undo_album(
            self.tmp,
            run_id=older.name,
            track=None,
            stages=stages,
            acquire_lock=run_fakes.acquire,
        )
        # Once to choose the run, once to ask whether it is the newest; never again.
        self.assertEqual(chosen, [older.name, None])
        self.assertEqual(self.folder.undo_calls, [(self.tmp, older.name, None)])
        self.assertFalse(report.newest)

    def test_with_no_run_nothing_is_locked(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(lm.NoRunError):
            lyrics_modes.undo_album(
                empty,
                run_id=None,
                track=None,
                stages=run_fakes.make_stages(self.folder),
                acquire_lock=run_fakes.acquire,
            )
        self.assertFalse((empty / lr.RUN_FOLDER_NAME).exists())
