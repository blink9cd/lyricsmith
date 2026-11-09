"""Links at the edges of containment: loops, a swap mid-read, a linked run, a huge copy.

- **A link loop.** Python 3.12, the runtime floor, raises `RuntimeError` from
  `Path.resolve` on a loop (later versions do not), so containment is judged with
  `os.path.realpath`, which never raises on one. A self-linked sidecar holds no
  lyrics and the chain fetches; a self-linked entry in a run folder is refused.
  Each test also patches `Path.resolve` to raise as 3.12's does, so it means the
  same on every interpreter.
- **A swap mid-read.** The file a sidecar link was checked to reach is the file read:
  re-pointing the link after the check must not make the read leave the album.
- **A linked run.** A run-id folder under ``.lyricsmith`` that is a link is no run:
  never listed, never chosen as the newest, and refused by its id.
- **A huge sidecar at the backup.** A run copies each sidecar into its backup before
  the sidecar stage reads it; that copy refuses a sidecar past the lyrics cap too,
  unread, so no mode reads one whole.

Every lyric here is invented.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import host_shapes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented words inside the album, which a sidecar link is meant to reach.
_INSIDE_TXT = "Words the album keeps\n"
#: Invented words outside the album, which must never be read.
_OUTSIDE_TXT = "Words from a folder nobody pointed the run at\n"
#: A later run id than `run_fakes.RUN_ID`, for the linked run-id folder.
_LATER_RUN_ID = "20261015T000000Z"


def _loops(path: Path) -> bool:
    """Whether following *path* goes round a link loop."""
    try:
        os.stat(path)
    except OSError as error:
        return host_shapes.is_link_loop(error)
    return False


@contextmanager
def resolve_raises_on_loops() -> Iterator[None]:
    """Make `Path.resolve` raise on a link loop, as it does on Python 3.12."""
    real = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        if _loops(self):
            raise RuntimeError(f"Symlink loop from {self!r}")
        return real(self, strict=strict)

    with mock.patch.object(Path, "resolve", resolve):
        yield


class LoopTest(fixtures.IsolatedCase):
    """A link loop is no lyrics beside a track, and a refusal inside a run folder."""

    def test_the_stand_in_raises_on_a_loop_as_3_12_does(self) -> None:
        loop, plain = self.tmp / "loop", self.tmp / "plain"
        loop.symlink_to("loop")
        with resolve_raises_on_loops():
            with self.assertRaises(RuntimeError):
                loop.resolve()
            self.assertEqual(plain.resolve(), plain.resolve(strict=False))

    def test_a_self_linked_sidecar_holds_no_lyrics(self) -> None:
        album = self.tmp / "album"
        (mp3,) = fixtures.build_album(album, (TrackSpec("01.mp3"),))
        mp3.with_suffix(".lrc").symlink_to("01.lrc")
        with resolve_raises_on_loops():
            state = lyrics_sidecar.normalize_sidecars(mp3, album=album, apply=False)
        self.assertEqual(state, lm.SidecarState(None))

    def test_a_self_linked_run_folder_entry_is_refused(self) -> None:
        run_dir = self.tmp / "run"
        (run_dir / "frames").mkdir(parents=True)
        (run_dir / "frames" / "01.sylt.1.lrc").symlink_to("01.sylt.1.lrc")
        with resolve_raises_on_loops():
            with self.assertRaisesRegex(lyrics_files.OutsideRunFolderError, "01.sylt"):
                lyrics_files.inside_run_folder(run_dir, "frames/01.sylt.1.lrc")


class SelfLinkedSidecarRunTest(cli_fakes.RealWiringCliCase):
    """A run over a self-linked sidecar fetches, with no error."""

    def test_the_run_fetches_past_a_self_linked_sidecar(self) -> None:
        (mp3,) = fixtures.build_album(
            self.album, (TrackSpec("01 Keeper.mp3", title="The Keeper"),)
        )
        mp3.with_suffix(".lrc").symlink_to("01 Keeper.lrc")
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        with resolve_raises_on_loops():
            result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        cells = cli_fakes.row_cells(result.stdout, "01 Keeper.mp3")
        self.assertEqual(cells[2:4], ["LRCLIB", "synced"])
        fetched = fixtures.synced_lrc(fixtures.LIGHTHOUSE_LRC)
        self.assertEqual(rb.synced_frame_lines(mp3), fetched.lines)
        self.assertNotIn("ERROR", result.stdout)


class SwapAfterCheckTest(fixtures.IsolatedCase):
    """The file a link was checked to reach is the file read, whatever the link does."""

    def test_a_link_re_pointed_after_the_check_still_reads_inside(self) -> None:
        album = self.tmp / "album"
        (mp3,) = fixtures.build_album(album, (TrackSpec("01.mp3"),))
        (album / "shared.txt").write_text(_INSIDE_TXT, encoding="utf-8", newline="")
        (self.tmp / "secret.txt").write_text(_OUTSIDE_TXT, encoding="utf-8", newline="")
        link = mp3.with_suffix(".txt")
        link.symlink_to("shared.txt")
        real_is_file = Path.is_file

        def swap_after(path: Path) -> bool:
            # The check has just looked: re-point the link out of the album now.
            answer = real_is_file(path)
            if path.name == "shared.txt" and os.readlink(link) == "shared.txt":
                link.unlink()
                link.symlink_to("../secret.txt")
            return answer

        with mock.patch.object(Path, "is_file", swap_after):
            state = lyrics_sidecar.normalize_sidecars(mp3, album=album, apply=False)
        self.assertEqual(state.lyrics, lm.PlainLyrics(_INSIDE_TXT))


class LinkedRunFolderTest(cli_fakes.RealWiringCliCase):
    """A run-id folder that is a link is no run, by its id or as the newest."""

    def setUp(self) -> None:
        """One real run, and a copy of it outside the album linked in as a later run."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01.mp3"),))
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        real = lr.run_folder(self.album, run_fakes.RUN_ID)
        elsewhere = self.tmp / "elsewhere"
        shutil.copytree(real, elsewhere)
        lr.run_folder(self.album, _LATER_RUN_ID).symlink_to(elsewhere)
        self.real = real

    def test_the_linked_run_is_refused_by_its_id(self) -> None:
        with self.assertRaises(lm.NoRunError):
            lyrics_backup.select_run(self.album, _LATER_RUN_ID)

    def test_the_newest_run_chosen_is_the_real_one(self) -> None:
        self.assertEqual(lyrics_backup.select_run(self.album, None), self.real)


class BackupSizeCapTest(cli_fakes.RealWiringCliCase):
    """A sidecar past the cap is refused at the backup, unread, and nothing changes."""

    def test_an_oversized_sidecar_is_the_tracks_backup_error(self) -> None:
        (mp3,) = fixtures.build_album(self.album, (TrackSpec("01.mp3"),))
        limit = 4 * lr.MAX_LYRICS_CHARS + 3
        mp3.with_suffix(".txt").write_bytes(b"\xff" * (limit + 1))
        before = rb.sidecars(mp3)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR backup: 01.txt is too large", result.stdout)
        self.assertEqual(rb.sidecars(mp3), before)
        copies = lr.run_folder(self.album, run_fakes.RUN_ID) / "sidecars"
        self.assertFalse(copies.exists())
