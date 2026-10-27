"""The backup stage: the run folder, its run id, the frame exports and sidecar copies.

Built on temporary albums of silent MP3s with planted frames. The round-trip proof gets
a subtest per shape an LRC cannot carry, each of which must leave an exact JSON copy that
reads back as the frame, and the one long-track shape the LRC format does carry. A
backup failure on one track excludes that track and no other. Every lyric here is
invented.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup as lb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above

# Bound to their protocols, so the type checker holds each to the orchestrator's calls.
_BACK_UP: ls.BackUpAlbum = lb.back_up_album
_SELECT: ls.SelectRun = lb.select_run
_LOAD: ls.LoadBeforeFrames = lb.load_before_frames

_STARTED = dt.datetime(2026, 10, 14, 15, 30, 12, tzinfo=dt.UTC)
_RUN_ID = "20261014T153012Z"
_TRACK = "CD1/01 Track.mp3"
_STEM = "CD1/01 Track"
_LYRIC = (
    "Salt on the window, salt on the oar\nwe row to the island and back once more\n"
)
_TIMED = tb.sylt((1000, "Salt on the window"), (4200, "salt on the oar"))


class BackupCase(fixtures.IsolatedCase):
    """Builds albums under the test's temporary directory."""

    def setUp(self) -> None:
        """An empty album folder for this test."""
        super().setUp()
        self.album = self.tmp / "album"
        self.album.mkdir()

    def track(
        self,
        relpath: str = _TRACK,
        frames: tuple[lm.FrameRecord, ...] = (),
        version: tuple[int, int] | None = (2, 4),
    ) -> Path:
        """A silent MP3 at *relpath* under the album, holding *frames*."""
        path = fixtures.build_silent_mp3(
            self.album / relpath, tb.SECONDS, id3_version=version
        )
        if frames:
            tb.plant_frames(path, frames)
        return path

    def back_up(self, *tracks: Path) -> lr.Manifest:
        """Back up *tracks* as a run started at the fixed time."""
        return lb.back_up_album(
            self.album,
            tracks,
            lang="eng",
            genius=lr.GeniusState.ABSENT,
            started=_STARTED,
        )

    def run_dir(self, manifest: lr.Manifest) -> Path:
        """The run folder *manifest* was written to."""
        return lr.run_folder(self.album, manifest.run_id)


class RunIdTest(BackupCase):
    """A run id is the start time in UTC, suffixed when that folder already exists."""

    def test_the_id_is_the_utc_start_time(self) -> None:
        later_zone = dt.timezone(dt.timedelta(hours=2))
        local = dt.datetime(2026, 10, 14, 17, 30, 12, tzinfo=later_zone)
        self.assertEqual(lb.run_id_base(local), _RUN_ID)

    def test_a_collision_takes_the_next_suffix(self) -> None:
        names = [lb.create_run_dir(self.album, _STARTED).name for _ in range(3)]
        self.assertEqual(names, [_RUN_ID, f"{_RUN_ID}-2", f"{_RUN_ID}-3"])
        for name in names:
            self.assertTrue(lr.run_folder(self.album, name).is_dir())


class SelectRunTest(BackupCase):
    """Undo and check read the newest run with a manifest, or the one named."""

    def make_run(self, run_id: str, *, manifest: bool = True) -> Path:
        """A run folder called *run_id*, with an empty manifest unless told otherwise."""
        run_dir = lr.run_folder(self.album, run_id)
        run_dir.mkdir(parents=True)
        if manifest:
            empty = lr.Manifest(
                run_id, "eng", lr.RunState.COMPLETE, lr.GeniusState.ABSENT, ()
            )
            lmf.write_manifest(run_dir, empty)
        return run_dir

    def test_no_run_folder_is_no_run(self) -> None:
        with self.assertRaises(lm.NoRunError):
            lb.select_run(self.album, None)
        (self.album / lr.RUN_FOLDER_NAME).mkdir()
        with self.assertRaises(lm.NoRunError):
            lb.select_run(self.album, None)

    def test_the_newest_is_by_time_then_by_suffix_as_a_number(self) -> None:
        for run_id in (_RUN_ID, f"{_RUN_ID}-2", f"{_RUN_ID}-10", "20261013T235959Z-99"):
            self.make_run(run_id)
        self.assertEqual(lb.select_run(self.album, None).name, f"{_RUN_ID}-10")

    def test_other_entries_and_runs_without_a_manifest_are_not_runs(self) -> None:
        self.make_run(_RUN_ID)
        self.make_run("20261015T000000Z", manifest=False)
        self.make_run("not-a-run")
        (self.album / lr.RUN_FOLDER_NAME / lyrics_album.LOCK_NAME).write_text(
            "1\n", encoding="utf-8", newline=""
        )
        self.assertEqual(lb.select_run(self.album, None).name, _RUN_ID)
        with self.assertRaises(lm.NoRunError):
            lb.select_run(self.album, "20261015T000000Z")

    def test_a_named_run_is_chosen_or_missing(self) -> None:
        self.make_run(_RUN_ID)
        self.make_run(f"{_RUN_ID}-2")
        self.assertEqual(lb.select_run(self.album, _RUN_ID).name, _RUN_ID)
        for missing in ("20200101T000000Z", "../album", ""):
            with self.subTest(run_id=missing), self.assertRaises(lm.NoRunError):
                lb.select_run(self.album, missing)


class BackupLayoutTest(BackupCase):
    """Each frame is exported where a person would look, and the manifest indexes it."""

    def test_frames_are_exported_beside_a_mirror_of_the_album(self) -> None:
        mp3 = self.track(frames=(tb.uslt(_LYRIC), _TIMED))
        manifest = self.back_up(mp3)
        run_dir = self.run_dir(manifest)
        self.assertEqual(run_dir.name, _RUN_ID)
        (entry,) = manifest.tracks
        self.assertEqual(entry.path, _TRACK)
        self.assertEqual(entry.id3_version, (2, 4))
        assert entry.before is not None
        by_kind = {export.kind: export for export in entry.before.frames}
        uslt, sylt = by_kind[lm.FrameKind.USLT], by_kind[lm.FrameKind.SYLT]
        self.assertEqual(uslt.file, f"frames/{_STEM}.uslt.1.txt")
        self.assertEqual(sylt.file, f"frames/{_STEM}.sylt.1.lrc")
        self.assertIsNone(uslt.exact)
        self.assertIsNone(sylt.exact)
        self.assertEqual((sylt.format, sylt.type, sylt.lang), (2, 1, "eng"))
        self.assertEqual((run_dir / uslt.file).read_text(encoding="utf-8"), _LYRIC)
        self.assertEqual(
            (run_dir / sylt.file).read_text(encoding="utf-8"),
            "[00:01.000]Salt on the window\n[00:04.200]salt on the oar\n",
        )

    def test_the_manifest_on_disk_is_the_one_returned_and_backed_up(self) -> None:
        manifest = self.back_up(self.track(frames=(_TIMED,)))
        self.assertIs(manifest.state, lr.RunState.BACKED_UP)
        self.assertEqual(lmf.read_manifest(self.run_dir(manifest)), manifest)

    def test_frames_of_one_kind_are_numbered_in_stored_order(self) -> None:
        frames = (tb.uslt("first words"), tb.uslt("zweite", lang="deu"))
        manifest = self.back_up(self.track(frames=frames))
        before = manifest.tracks[0].before
        assert before is not None
        names = sorted(export.file for export in before.frames)
        self.assertEqual(
            names, [f"frames/{_STEM}.uslt.1.txt", f"frames/{_STEM}.uslt.2.txt"]
        )

    def test_carriage_returns_survive_in_the_text_export(self) -> None:
        text = "first line\r\nsecond line\r\n"
        manifest = self.back_up(self.track(frames=(tb.uslt(text),)))
        before = manifest.tracks[0].before
        assert before is not None
        (export,) = before.frames
        self.assertIsNone(export.exact)
        self.assertEqual(
            (self.run_dir(manifest) / export.file).read_bytes(), text.encode()
        )

    def test_sidecars_are_copied_byte_for_byte_by_name(self) -> None:
        mp3 = self.track()
        mp3.with_suffix(".lrc").write_bytes(b"\xef\xbb\xbf[00:01.00]a bom line\r\n")
        mp3.with_suffix(".txt").write_bytes(b"plain words\n")
        manifest = self.back_up(mp3)
        before = manifest.tracks[0].before
        assert before is not None
        copies = {copy.name: copy.file for copy in before.sidecars}
        self.assertEqual(
            copies,
            {
                "01 Track.lrc": f"sidecars/{_STEM}.lrc",
                "01 Track.txt": f"sidecars/{_STEM}.txt",
            },
        )
        run_dir = self.run_dir(manifest)
        for name, file in copies.items():
            assert file is not None
            self.assertEqual(
                (run_dir / file).read_bytes(), (mp3.parent / name).read_bytes()
            )

    def test_a_track_with_nothing_backs_up_as_nothing(self) -> None:
        manifest = self.back_up(self.track())
        self.assertEqual(manifest.tracks[0].before, lr.TrackBefore((), ()))

    def test_a_track_with_no_id3v2_header_records_no_version(self) -> None:
        manifest = self.back_up(self.track(version=None))
        self.assertIsNone(manifest.tracks[0].id3_version)
        self.assertEqual(manifest.tracks[0].before, lr.TrackBefore((), ()))


class RoundTripProofTest(BackupCase):
    """An export that does not read back as its frame gets an exact JSON copy."""

    def assert_exact(self, frame: lm.FrameRecord, *, expected: bool = True) -> None:
        """Back *frame* up; assert whether it took a JSON copy and that it loads back."""
        manifest = self.back_up(self.track(frames=(frame,)))
        before = manifest.tracks[0].before
        assert before is not None
        (export,) = before.frames
        run_dir = self.run_dir(manifest)
        self.assertTrue((run_dir / export.file).is_file())
        if expected:
            self.assertEqual(export.exact, f"frames/{_STEM}.sylt.1.frames.json")
        else:
            self.assertIsNone(export.exact)
        self.assertEqual(lb.load_before_frames(run_dir, before), (frame,))

    def test_each_shape_an_lrc_cannot_carry_takes_a_json_copy(self) -> None:
        lossy = {
            "frame units": tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES),
            "newline in an entry": tb.sylt((100, "\nleading break"), (900, "next")),
            "out of time order": tb.sylt((900, "late"), (100, "early")),
            "surrounding space": tb.sylt((100, "  padded ")),
            "bracket at the start": tb.sylt((100, "[00:02.000]looks timed")),
        }
        for case, frame in lossy.items():
            with self.subTest(case=case):
                # A fresh album per case, so each backup is the only run in it.
                self.album = self.tmp / case.replace(" ", "-")
                self.album.mkdir()
                self.assert_exact(frame)

    def test_a_stamp_past_ninety_nine_minutes_still_reads_back(self) -> None:
        self.assert_exact(tb.sylt((6_000_000, "a very long track")), expected=False)

    def test_text_utf8_cannot_hold_takes_a_json_copy(self) -> None:
        odd = tb.uslt("a lone \ud800 half")
        tags = lm.TrackTags(None, None, None, (2, 4), (odd,))
        with mock.patch.object(lb, "read_tags", return_value=tags):
            manifest = self.back_up(self.track())
        before = manifest.tracks[0].before
        assert before is not None
        self.assertEqual(before.frames[0].exact, f"frames/{_STEM}.uslt.1.frames.json")
        self.assertEqual(lb.load_before_frames(self.run_dir(manifest), before), (odd,))


class BackupFailureTest(BackupCase):
    """A track whose backup fails is excluded; every other track is backed up."""

    def assert_only_first_failed(self, manifest: lr.Manifest) -> None:
        """Assert the first track's backup failed and the second's did not."""
        failed, kept = manifest.tracks
        self.assertIsNone(failed.before)
        assert failed.after is not None and failed.after.error is not None
        self.assertIs(failed.after.status, lr.OutcomeStatus.ERROR)
        self.assertIs(failed.after.error.stage, lr.ErrorStage.BACKUP)
        self.assertIsNotNone(kept.before)
        self.assertIsNone(kept.after)
        self.assertEqual(lmf.read_manifest(self.run_dir(manifest)), manifest)

    def test_an_unreadable_track_fails_alone(self) -> None:
        gone = self.track("01.mp3")
        kept = self.track("02.mp3", frames=(_TIMED,))
        gone.unlink()
        self.assert_only_first_failed(self.back_up(gone, kept))

    def test_a_sidecar_copy_that_fails_fails_its_track(self) -> None:
        first = self.track("01.mp3")
        first.with_suffix(".txt").write_text("words", encoding="utf-8", newline="")
        second = self.track("02.mp3")
        real = lyrics_files.write_confirmed

        def refuse_sidecars(target: Path, data: bytes) -> None:
            if "sidecars" in target.parts:
                raise OSError("no space left")
            real(target, data)

        with mock.patch.object(lyrics_files, "write_confirmed", refuse_sidecars):
            self.assert_only_first_failed(self.back_up(first, second))

    def test_a_second_track_with_the_same_backup_name_fails_alone(self) -> None:
        first = self.track("Song.mp3", frames=(tb.uslt(_LYRIC),))
        # Where names ignore case this is the same file again; either way, both
        # tracks would back up under ``Song``.
        second = self.album / "Song.MP3"
        second.write_bytes(first.read_bytes())
        third = self.track("Other.mp3")
        manifest = self.back_up(first, second, third)
        kept, failed, other = manifest.tracks
        self.assertIsNotNone(kept.before)
        self.assertIsNotNone(other.before)
        self.assertIsNone(failed.before)
        assert failed.after is not None and failed.after.error is not None
        self.assertIs(failed.after.error.stage, lr.ErrorStage.BACKUP)
        self.assertIn("Song", failed.after.error.message)
        export = kept.before.frames[0] if kept.before else None
        assert export is not None
        self.assertEqual(
            (self.run_dir(manifest) / export.file).read_text(encoding="utf-8"), _LYRIC
        )

    def test_a_json_copy_that_does_not_read_back_fails_its_track(self) -> None:
        lossy = self.track(
            "01.mp3", frames=(tb.sylt((40, "x"), time_format=tb.MPEG_FRAMES),)
        )
        plain = self.track("02.mp3", frames=(tb.uslt(_LYRIC),))
        with mock.patch.object(lmf, "parse_frame", return_value=tb.uslt("other")):
            manifest = self.back_up(lossy, plain)
        self.assert_only_first_failed(manifest)
        assert manifest.tracks[0].after is not None
        assert manifest.tracks[0].after.error is not None
        self.assertEqual(manifest.tracks[0].after.error.exc_type, "ReadBackError")


@unittest.skipIf(not getattr(os, "O_NOFOLLOW", 0), "no O_NOFOLLOW to refuse a link")
class LinkedProofTest(BackupCase):
    """A proof read finding a link under a name it just wrote refuses it, unread."""

    def back_up_linking(self, frame: lm.FrameRecord, suffix: str) -> lr.Manifest:
        """Back *frame* up, each written file ending *suffix* swapped for a link.

        The link leads outside the run folder, to the very bytes the write left, so
        a proof that read through it would pass.
        """
        real = lyrics_files.write_confirmed

        def writing_then_linking(target: Path, data: bytes) -> None:
            real(target, data)
            if target.name.endswith(suffix):
                outside = self.tmp / f"outside-{target.name}"
                target.rename(outside)
                try:
                    target.symlink_to(outside)
                except (OSError, NotImplementedError) as error:
                    self.skipTest(f"symlinks unavailable: {error}")

        with mock.patch.object(lyrics_files, "write_confirmed", writing_then_linking):
            return self.back_up(self.track(frames=(frame,)))

    def assert_refused(self, manifest: lr.Manifest) -> None:
        """The one track's backup failed, at the backup stage."""
        (track,) = manifest.tracks
        self.assertIsNone(track.before)
        assert track.after is not None and track.after.error is not None
        self.assertIs(track.after.error.stage, lr.ErrorStage.BACKUP)

    def test_a_frame_export_swapped_for_a_link_is_refused(self) -> None:
        self.assert_refused(self.back_up_linking(tb.uslt(_LYRIC), ".txt"))

    def test_an_exact_copy_swapped_for_a_link_is_refused(self) -> None:
        lossy = tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES)
        self.assert_refused(self.back_up_linking(lossy, _EXACT))


#: The ending of a frame's exact JSON copy.
_EXACT = ".frames.json"


class LoadBeforeFramesTest(BackupCase):
    """Frames come back from the exports, the JSON copy winning; damage is refused."""

    def backed_up(self, frame: lm.FrameRecord) -> tuple[Path, lr.TrackBefore]:
        """Back up one track holding *frame*; return its run folder and before block."""
        manifest = self.back_up(self.track(frames=(frame,)))
        before = manifest.tracks[0].before
        assert before is not None
        return self.run_dir(manifest), before

    def test_the_json_copy_wins_over_its_export(self) -> None:
        frame = tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES)
        run_dir, before = self.backed_up(frame)
        (run_dir / before.frames[0].file).write_text(
            "damaged", encoding="utf-8", newline=""
        )
        self.assertEqual(lb.load_before_frames(run_dir, before), (frame,))

    def test_an_export_edited_to_hold_no_time_stamp_is_refused(self) -> None:
        run_dir, before = self.backed_up(_TIMED)
        (run_dir / before.frames[0].file).write_text(
            "no stamps", encoding="utf-8", newline=""
        )
        with self.assertRaises(lm.ManifestSchemaError):
            lb.load_before_frames(run_dir, before)

    def test_an_export_that_is_not_utf8_is_refused(self) -> None:
        run_dir, before = self.backed_up(tb.uslt(_LYRIC))
        (run_dir / before.frames[0].file).write_bytes(b"caf\xe9")
        with self.assertRaises(lm.ManifestSchemaError):
            lb.load_before_frames(run_dir, before)

    def test_a_synced_entry_without_its_format_is_refused(self) -> None:
        run_dir, before = self.backed_up(_TIMED)
        export = before.frames[0]
        broken = lr.FrameExport(
            export.kind,
            export.lang,
            export.desc,
            export.encoding,
            None,
            None,
            export.file,
        )
        with self.assertRaises(lm.ManifestSchemaError):
            lb.load_before_frames(run_dir, lr.TrackBefore((broken,), ()))

    def test_a_missing_export_is_an_os_error(self) -> None:
        run_dir, before = self.backed_up(tb.uslt(_LYRIC))
        (run_dir / before.frames[0].file).unlink()
        with self.assertRaises(FileNotFoundError):
            lb.load_before_frames(run_dir, before)


if __name__ == "__main__":
    unittest.main()
