"""Long track names: every name the tool creates fits in one file name, 255 bytes.

A track's own name may take nearly the whole limit, yet the backup appends a frame's kind
and number to it, and every confirmed write adds a random tag for its temporary file, so
a track named in about 230 bytes once failed its backup with ``ENAMETOOLONG``. Here a
240-byte name, part of it two-byte UTF-8, is backed up, run, checked and undone. A backup
name is shortened only when it must be, and keeps a short hash of the whole name, so two
long names sharing a head back up apart. Every lyric is invented.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders as tb  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_tags import read_tags  # noqa: E402 - import follows the sys.path bootstrap above

#: The longest file name, in bytes, APFS and ext4 take.
_LIMIT = 255
#: A track named in 240 bytes, its tail two-byte UTF-8.
_LONG = "01 " + "Lanterns over the harbor wall, " * 7 + "ö" * 8 + ".mp3"
#: Another, the same up to its tail.
_TWIN = _LONG.replace("ö", "ü")
#: A track named in the whole limit, so its sidecars are too.
_FULL = "x" * (_LIMIT - len(".mp3")) + ".mp3"
_LYRIC = "Salt on the window, salt on the oar\n"
_OTHER = "A bell across the water rings the hour down\n"
_TXT = "A gull above the harbor\nturns once and is gone\n"
_HAND = b"A verse typed in by hand\nafter the run was over\n"
_EDITED = b"The run's verse, edited\n"
#: A frame its LRC export cannot carry, so it takes the longest backup name: its JSON copy.
_LOSSY = tb.sylt((40, "counted"), time_format=tb.MPEG_FRAMES)


def _size(name: str) -> int:
    """*name*'s length in bytes of UTF-8, the unit a file system's limit counts."""
    return len(name.encode("utf-8"))


class LongNameBackupTest(rb.UndoCase):
    """A long-named track backs up whole, under names that fit, and undoes."""

    def assert_names_fit(self, folder: Path) -> None:
        """Assert every entry under *folder* is named within the limit."""
        for entry in folder.rglob("*"):
            with self.subTest(name=entry.name):
                self.assertLessEqual(_size(entry.name), _LIMIT)

    def test_the_fixture_names_are_as_long_as_their_comments_say(self) -> None:
        self.assertEqual((_size(_LONG), _size(_TWIN), _size(_FULL)), (240, 240, 255))

    def test_a_long_track_backs_up_and_reads_back(self) -> None:
        path = f"CD1/{_LONG}"
        mp3 = self.track(path, (tb.uslt(_LYRIC), _LOSSY), txt=_TXT, lrc=_OTHER)
        manifest = self.back_up(mp3)
        (entry,) = manifest.tracks
        self.assertEqual((entry.path, entry.after), (path, None))
        assert entry.before is not None
        run_dir = lr.run_folder(self.album, manifest.run_id)
        self.assertEqual(
            lyrics_backup.load_before_frames(run_dir, entry.before),
            read_tags(mp3).frames,
        )
        for copy in entry.before.sidecars:
            assert copy.file is not None
            original = mp3.with_name(copy.name).read_bytes()
            self.assertEqual((run_dir / copy.file).read_bytes(), original)
        exports = [export.file for export in entry.before.frames]
        self.assertTrue(all(f.startswith("frames/CD1/01 Lanterns") for f in exports))
        self.assert_names_fit(run_dir)

    def test_two_long_names_sharing_a_head_back_up_apart(self) -> None:
        first = self.track(_LONG, (tb.uslt(_LYRIC),))
        second = self.track(_TWIN, (tb.uslt(_OTHER),))
        manifest = self.back_up(first, second)
        run_dir = lr.run_folder(self.album, manifest.run_id)
        restored = []
        for entry in manifest.tracks:
            assert entry.before is not None
            restored.append(lyrics_backup.load_before_frames(run_dir, entry.before))
        self.assertEqual(restored, [(tb.uslt(_LYRIC),), (tb.uslt(_OTHER),)])

    def test_a_displaced_copy_of_a_full_length_sidecar_fits(self) -> None:
        mp3 = self.track(_FULL)
        manifest = self.back_up(mp3)
        lrc = mp3.with_suffix(".lrc")
        for data in (_HAND, _EDITED):
            rb.simulate_run(self.album, manifest)
            lrc.write_bytes(data)
            report = self.undo()
            self.assertEqual(rb.undo_error(report.tracks[0]), "")
        undo_dir = lr.run_folder(self.album, manifest.run_id) / "undo"
        kept = sorted(p.read_bytes() for p in undo_dir.iterdir())
        self.assertEqual(kept, sorted((_HAND, _EDITED)))
        self.assert_names_fit(undo_dir)


class LongNameWriteTest(fixtures.IsolatedCase):
    """A write or a link whose own name takes the whole limit still lands."""

    def test_a_full_length_file_and_link_are_written(self) -> None:
        target = self.tmp / ("y" * (_LIMIT - len(".txt")) + ".txt")
        lyrics_files.write_confirmed(target, b"words")
        link = self.tmp / ("z" * _LIMIT)
        lyrics_files.replace_with_link(link, target.name)
        self.assertEqual(
            (target.read_bytes(), os.readlink(link)), (b"words", target.name)
        )
        made = {p.name for p in self.tmp.iterdir()} - {"home"}
        self.assertEqual(made, {target.name, link.name})

    def test_a_cut_never_splits_a_character(self) -> None:
        self.assertEqual(lyrics_files.truncate_utf8("aö", 2), "a")
        self.assertEqual(lyrics_files.truncate_utf8("aö", 3), "aö")


class LongNameEndToEndTest(cli_fakes.RealWiringCliCase):
    """Run, check and undo a long-named track through `main`, the providers faked."""

    real_clock = True

    def test_a_long_named_track_runs_checks_and_undoes(self) -> None:
        tracks = (TrackSpec(f"CD1/{_LONG}", title="The Keeper"),)
        paths = fixtures.build_album(self.album, tracks)
        tb.plant_frames(paths[0], [tb.uslt("An older verse about the keeper\n")])
        before = rb.snapshots(paths)
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        run = self.run_album()
        self.assertEqual(run.code, album.EXIT_OK, run.stdout)
        self.assertNotEqual(rb.snapshots(paths), before)
        check = self.run_album("--mode", "check")
        self.assertEqual(check.code, album.EXIT_OK, check.stdout)
        undo = self.run_album("--mode", "undo")
        self.assertEqual((undo.code, undo.stderr), (album.EXIT_OK, ""))
        self.assertEqual(rb.snapshots(paths), before)


if __name__ == "__main__":
    unittest.main()
