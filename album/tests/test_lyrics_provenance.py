"""The recorded-source walk: which provider a track's lyrics came from, per its manifests.

Built on temporary albums whose run folders hold manifests written by the real codec.
Every track name here is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - follows the bootstrap
import lyrics_backup as lb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_provenance as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above

_OLD = "20261001T000000Z"
_NEW = "20261002T000000Z"
_TRACK = "a.mp3"
_ST = lr.OutcomeStatus
_SRC = lm.Source


def entry(
    path: str,
    status: lr.OutcomeStatus | None,
    source: lm.Source | None = None,
    *,
    undone: bool = False,
) -> lr.ManifestTrack:
    """A manifest entry for *path* with an ``after`` block of *status* and *source*."""
    after = None if status is None else lr.TrackOutcome(status, source=source)
    return lr.ManifestTrack(path, (2, 4), None, after, undone)


def write_run(album: Path, run_id: str, *tracks: lr.ManifestTrack) -> None:
    """Write run *run_id* under *album* holding a manifest of *tracks*."""
    run_dir = album / lr.RUN_FOLDER_NAME / run_id
    run_dir.mkdir(parents=True)
    lmf.write_manifest(
        run_dir,
        lr.Manifest(run_id, "eng", lr.RunState.COMPLETE, lr.GeniusState.ABSENT, tracks),
    )


class RecordedSourceTests(fixtures.IsolatedCase):
    """`recorded_sources` over albums with one or more runs."""

    def setUp(self) -> None:
        """A fresh album inside the test's isolated temporary directory."""
        super().setUp()
        self.album = self.tmp

    def source(self, track: str = _TRACK) -> lm.Source | None:
        """The recorded source of *track*, read with the real manifest reader."""
        return lp.recorded_sources(self.album, lmf.read_manifest).source_of(track)

    def test_no_run_folder_is_unrecorded(self) -> None:
        self.assertIsNone(self.source())

    def test_written_records_the_provider(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_unchanged_records_the_provider(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.UNCHANGED, _SRC.GENIUS))
        self.assertEqual(self.source(), _SRC.GENIUS)

    def test_musixmatch_is_a_provider(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.MUSIXMATCH))
        self.assertEqual(self.source(), _SRC.MUSIXMATCH)

    def test_tekstowo_is_a_provider(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.TEKSTOWO))
        self.assertEqual(self.source(), _SRC.TEKSTOWO)

    def test_bandcamp_and_youtube_music_are_providers(self) -> None:
        for run_id, source in ((_OLD, _SRC.BANDCAMP), (_NEW, _SRC.YOUTUBE_MUSIC)):
            with self.subTest(source=source):
                write_run(self.album, run_id, entry(_TRACK, _ST.WRITTEN, source))
                self.assertEqual(self.source(), source)

    def test_every_compared_provider_can_be_a_source(self) -> None:
        # A provider compare asks must be readable as a track's own source, or its
        # own answer would count as a second source agreeing.
        self.assertLessEqual(set(lc.COMPARED), lp.PROVIDER_SOURCES)

    def test_newer_run_wins(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.NETEASE))
        self.assertEqual(self.source(), _SRC.NETEASE)

    def test_undone_entry_is_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(
            self.album,
            _NEW,
            entry(_TRACK, _ST.WRITTEN, _SRC.NETEASE, undone=True),
        )
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_entry_without_after_is_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, None))
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_kept_existing_is_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.KEPT_EXISTING, _SRC.SIDECAR))
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_write_from_frames_is_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.FRAMES))
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_another_track_s_text_makes_it_unrecorded(self) -> None:
        # The provider that wrote the wrong text no longer decides the track.
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.ALBUM_TEXT))
        records = lp.recorded_sources(self.album, lmf.read_manifest)
        self.assertIsNone(records.source_of(_TRACK))
        self.assertIn(_TRACK, records.decided)

    def test_stripped_and_removed_make_it_unrecorded(self) -> None:
        for status in (_ST.STRIPPED, _ST.REMOVED):
            with self.subTest(status=status):
                album = self.album / status.value
                album.mkdir()
                write_run(album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
                write_run(album, _NEW, entry(_TRACK, status))
                records = lp.recorded_sources(album, lmf.read_manifest)
                self.assertIsNone(records.source_of(_TRACK))
                self.assertIn(_TRACK, records.decided)

    def test_not_found_and_error_are_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.NOT_FOUND))
        write_run(self.album, "20261003T000000Z", entry(_TRACK, _ST.ERROR))
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_malformed_manifest_is_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.NETEASE))
        (self.album / lr.RUN_FOLDER_NAME / _NEW / lr.MANIFEST_NAME).write_bytes(
            b"\xff{"
        )
        self.assertEqual(self.source(), _SRC.LRCLIB)

    def test_unreadable_manifest_is_passed_over(self) -> None:
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.NETEASE))

        def read(run_dir: Path) -> lr.Manifest:
            if run_dir.name == _NEW:
                raise OSError("unreadable")
            return lmf.read_manifest(run_dir)

        records = lp.recorded_sources(self.album, read)
        self.assertEqual(records.source_of(_TRACK), _SRC.LRCLIB)

    def test_suffix_sorts_as_a_number(self) -> None:
        write_run(self.album, f"{_OLD}-2", entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, f"{_OLD}-10", entry(_TRACK, _ST.WRITTEN, _SRC.GENIUS))
        self.assertEqual(self.source(), _SRC.GENIUS)

    def test_untouched_track_is_unrecorded(self) -> None:
        write_run(self.album, _OLD, entry("b.mp3", _ST.WRITTEN, _SRC.LRCLIB))
        self.assertIsNone(self.source())
        self.assertNotIn(
            _TRACK, lp.recorded_sources(self.album, lmf.read_manifest).decided
        )


class RunsNewestFirstTests(fixtures.IsolatedCase):
    """`runs_newest_first` orders, and leaves out what is no run."""

    def test_order_and_exclusions(self) -> None:
        album = self.tmp
        write_run(album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        (album / lr.RUN_FOLDER_NAME / "20261003T000000Z").mkdir()
        (album / lr.RUN_FOLDER_NAME / "20261004T000000Z").symlink_to(
            album / lr.RUN_FOLDER_NAME / _OLD, target_is_directory=True
        )
        self.assertEqual(
            lb.runs_newest_first(album),
            (
                lb.run_folder(album, _NEW),
                lb.run_folder(album, _OLD),
            ),
        )


class PassedOverTest(fixtures.IsolatedCase):
    """`recorded_sources` names each run whose manifest it passed over, and why."""

    def setUp(self) -> None:
        """An album with a good older run and a newer one, both naming one track."""
        super().setUp()
        self.album = self.tmp
        write_run(self.album, _OLD, entry(_TRACK, _ST.WRITTEN, _SRC.LRCLIB))
        write_run(self.album, _NEW, entry(_TRACK, _ST.WRITTEN, _SRC.NETEASE))
        self.newer = self.album / lr.RUN_FOLDER_NAME / _NEW

    def records(self) -> lp.SourceRecords:
        """What the walk with the real manifest reader found."""
        return lp.recorded_sources(self.album, lmf.read_manifest)

    def test_readable_runs_pass_nothing_over(self) -> None:
        self.assertEqual(self.records().passed_over, ())

    def test_a_malformed_newer_run_is_passed_over_by_name(self) -> None:
        (self.newer / lr.MANIFEST_NAME).write_bytes(b"\xff{")
        records = self.records()
        self.assertEqual(records.passed_over, ((self.newer, "ManifestSchemaError"),))
        self.assertEqual(records.source_of(_TRACK), _SRC.LRCLIB)

    def test_a_fifo_manifest_is_passed_over_at_once(self) -> None:
        manifest = self.newer / lr.MANIFEST_NAME
        manifest.unlink()
        make_pipe(manifest)
        records = within_deadline(self, manifest, self.records)
        self.assertEqual(records.passed_over, ((self.newer, "ManifestSchemaError"),))
        self.assertEqual(records.source_of(_TRACK), _SRC.LRCLIB)

    def test_an_os_error_is_named_by_its_type(self) -> None:
        def read(run_dir: Path) -> lr.Manifest:
            if run_dir.name == _NEW:
                raise PermissionError("refused")
            return lmf.read_manifest(run_dir)

        records = lp.recorded_sources(self.album, read)
        self.assertEqual(records.passed_over, ((self.newer, "PermissionError"),))


if __name__ == "__main__":
    unittest.main()
