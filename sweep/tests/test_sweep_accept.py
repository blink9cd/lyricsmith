"""--mode accept: the walk, each album refused only for itself, the summary, the lock.

`main` runs with the real album stages and lock unless a test injects its own, with
stdout and stderr captured, HOME and the XDG folders redirected, so every album, run
folder, records folder and ledger lies under the test's own folder. The run folders
are written through the album tool's own manifest writer, and every name is invented.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_lock  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import LyricsError  # noqa: E402 - follows the bootstrap
from lyrics_wiring import real_wiring  # noqa: E402 - follows the bootstrap

#: An invented run id, in the album tool's own format.
_RUN_ID = "20261019T120000Z"
#: The report-only pass's closing hint, as the album tool words it.
_HINT = "report only: nothing was removed; add --apply to remove it"
#: An invented accept time.
_NOW = datetime(2026, 10, 25, 9, 8, 7, tzinfo=UTC)
_DELETE_FAILED = "invented: the delete was refused"


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


def tree_hashes(root: Path) -> dict[str, str]:
    """Every file under *root*, by path relative to it, to its sha256."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def run_album(
    library: Path,
    name: str,
    *,
    state: lr.RunState = lr.RunState.COMPLETE,
) -> Path:
    """An album ``Artist/<name>`` of one track, holding one run of *state*.

    The run found nothing for its track, so its backup holds no copy of anything; its
    lock file is left behind, as a real run leaves it.
    """
    album = library / "Artist" / name
    sf.add_track(album, "01.mp3")
    run_dir = lr.run_folder(album, _RUN_ID)
    run_dir.mkdir(parents=True)
    track = lr.ManifestTrack("01.mp3", (2, 4), lr.TrackBefore((), ()), sf.not_found())
    manifest = lr.Manifest(_RUN_ID, "eng", state, lr.GeniusState.ABSENT, (track,))
    lyrics_manifest.write_manifest(run_dir, manifest)
    lock = run_lock.acquire(album / lr.RUN_FOLDER_NAME / "lock")
    assert lock is not None
    lock.release()
    return album


def set_aside(album: Path) -> None:
    """Put a file in the run's undo folder: a track at risk, set aside."""
    undo = lr.run_folder(album, _RUN_ID) / "undo"
    undo.mkdir()
    (undo / "01.txt").write_text("An invented edit\n", encoding="utf-8", newline="")


def failing_delete(path: Path) -> None:
    """A delete that is always refused, as a busy folder's would be."""
    raise OSError(_DELETE_FAILED)


class AcceptCase(sf.IsolatedCase):
    """Runs `sweep.main` with --mode accept against this test's library."""

    def setUp(self) -> None:
        """The real album seams, the fixed clock, the redirected environment."""
        super().setUp()
        self.seams = sweep.sweep_accept.AcceptSeams(
            acquire_lock=run_lock.acquire,
            stages=real_wiring().stages,
            environ=os.environ,
        )
        self.ledger = sl.ledger_path(self.library.resolve(), os.environ)

    def main(self, *args: str, stdout: io.StringIO | None = None) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(
            sf.FakeAlbumTool(), sf.FakeSleep(), lambda: _NOW, accept=self.seams
        )
        out, err = stdout or io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, out.getvalue(), err.getvalue())

    def accept(self, *extra: str) -> Result:
        """Run accept on the library with *extra* options."""
        return self.main("--mode", "accept", *extra)

    def records(self) -> list[Path]:
        """Each records folder under the redirected state folder."""
        root = self.state / "lyricsmith" / "accepted"
        return sorted(root.iterdir()) if root.is_dir() else []

    def lock_with(self, acquire: Callable[[Path], run_lock.Lock | None]) -> None:
        """Use *acquire* as the album lock from now on."""
        self.seams = replace(self.seams, acquire_lock=acquire)


class WalkTest(AcceptCase):
    """The walk visits the albums `run` would consider, and drops linked ones."""

    def test_every_album_with_a_run_is_reported_in_walk_order(self) -> None:
        run_album(self.library, "B")
        run_album(self.library, "A")
        result = self.accept()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertLess(
            result.stdout.index("Artist/A"), result.stdout.index("Artist/B")
        )
        self.assertIn("sweep accept: 1 run folder under A", result.stdout)

    def test_a_synced_album_a_run_passes_over_is_still_cleared(self) -> None:
        album = run_album(self.library, "Synced")
        (album / "01.lrc").write_text(sf.SYNCED_LRC, encoding="utf-8", newline="")
        result = self.accept("--apply")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertFalse(os.path.lexists(album / lr.RUN_FOLDER_NAME))

    def test_a_linked_album_is_named_and_never_touched(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        target = run_album(elsewhere, "Linked")
        (self.library / "Linker").symlink_to(target.parent, target_is_directory=True)
        before = tree_hashes(elsewhere)
        result = self.accept("--apply")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("skipped album Linker/Linked", result.stderr)
        self.assertEqual(tree_hashes(elsewhere), before)

    def test_an_album_with_no_run_is_only_counted(self) -> None:
        sf.needy_album(self.library, "Artist", "Never")
        result = self.accept()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertNotIn("Artist/Never", result.stdout)
        self.assertIn("no run 1", result.stdout)


class ReportOnlyTest(AcceptCase):
    """Without --apply, no byte changes anywhere, and the hint closes the summary."""

    def test_no_byte_changes_and_nothing_is_recorded(self) -> None:
        run_album(self.library, "A")
        set_aside(run_album(self.library, "B"))
        before = tree_hashes(self.library)
        result = self.accept()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(tree_hashes(self.library), before)
        self.assertEqual(self.records(), [])
        self.assertTrue(result.stdout.endswith(f"{_HINT}\n"))
        # Said once, at the end, not after every album.
        self.assertEqual(result.stdout.count(_HINT), 1)
        self.assertIn("would clear 2", result.stdout)


class ApplyTest(AcceptCase):
    """With --apply, each cleared album loses `.lyricsmith` and nothing else."""

    def test_each_album_loses_only_its_run_folder_and_is_recorded(self) -> None:
        albums = [run_album(self.library, name) for name in ("A", "B")]
        set_aside(albums[1])
        before = {
            name: digest
            for name, digest in tree_hashes(self.library).items()
            if f"/{lr.RUN_FOLDER_NAME}/" not in name
        }
        result = self.accept("--apply")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(tree_hashes(self.library), before)
        for album in albums:
            self.assertFalse(os.path.lexists(album / lr.RUN_FOLDER_NAME))
        self.assertEqual(len(self.records()), 2)
        self.assertIn("cleared 2", result.stdout)
        self.assertNotIn(_HINT, result.stdout)

    def test_a_failed_removal_is_exit_1_and_the_others_still_go(self) -> None:
        failing = run_album(self.library, "A")
        cleared = run_album(self.library, "B")

        def delete(path: Path) -> None:
            if path.parent == failing:
                failing_delete(path)
            shutil.rmtree(path)

        self.seams = replace(self.seams, delete=delete)
        result = self.accept("--apply")
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn(_DELETE_FAILED, result.stdout)
        self.assertFalse(os.path.lexists(cleared / lr.RUN_FOLDER_NAME))
        self.assertIn("errors 1", result.stdout.split("\n\n")[-1])

    def test_an_album_that_failed_holding_at_risk_tracks_is_counted(self) -> None:
        failing = run_album(self.library, "A")
        set_aside(failing)
        self.seams = replace(self.seams, delete=failing_delete)
        result = self.accept("--apply")
        self.assertEqual(result.code, 1, result.stderr)
        summary = result.stdout.split("\n\n")[-1]
        self.assertIn("holding at-risk tracks 1", summary)
        self.assertIn("errors 1", summary)


class RefusalTest(AcceptCase):
    """An album refused, or failing to start, is refused only for itself."""

    def test_a_refused_album_does_not_stop_the_others(self) -> None:
        unfinished = run_album(self.library, "A", state=lr.RunState.BACKED_UP)
        cleared = run_album(self.library, "B")
        before = tree_hashes(unfinished)
        result = self.accept("--apply")
        # Finished, but an album did not: a refused album is exit 1.
        self.assertEqual(result.code, 1, result.stderr)
        self.assertEqual(tree_hashes(unfinished), before)
        self.assertFalse(os.path.lexists(cleared / lr.RUN_FOLDER_NAME))
        self.assertIn("refused: unfinished run", result.stdout)

    def test_an_album_whose_lock_is_held_is_refused_and_named(self) -> None:
        locked = run_album(self.library, "Locked")
        cleared = run_album(self.library, "Open")

        def acquire(path: Path) -> run_lock.Lock | None:
            return None if locked in path.parents else run_lock.acquire(path)

        self.lock_with(acquire)
        result = self.accept("--apply")
        self.assertEqual(result.code, 1, result.stderr)
        self.assertTrue((locked / lr.RUN_FOLDER_NAME).is_dir())
        self.assertFalse(os.path.lexists(cleared / lr.RUN_FOLDER_NAME))
        self.assertIn("refused: album locked: ", result.stdout)
        self.assertIn("held by another run", result.stdout)

    def test_an_os_error_is_that_albums_alone(self) -> None:
        broken = run_album(self.library, "Broken")
        run_album(self.library, "Fine")

        def acquire(path: Path) -> run_lock.Lock | None:
            if broken in path.parents:
                raise PermissionError("invented: the lock cannot be opened")
            return run_lock.acquire(path)

        self.lock_with(acquire)
        result = self.accept()
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn(
            "refused: unreadable: invented: the lock cannot be opened", result.stdout
        )
        self.assertIn("would clear 1", result.stdout)


class SummaryTest(AcceptCase):
    """One summary: albums cleared, refused by reason, holding at-risk tracks."""

    def test_the_counts_by_reason(self) -> None:
        run_album(self.library, "A Cleared")
        set_aside(run_album(self.library, "B At Risk"))
        run_album(self.library, "C Unfinished", state=lr.RunState.BACKED_UP)
        stray = run_album(self.library, "D Stray")
        (stray / lr.RUN_FOLDER_NAME / "notes.txt").write_text(
            "x\n", encoding="utf-8", newline=""
        )
        locked = run_album(self.library, "E Locked")
        run_album(self.library, "F Unfinished", state=lr.RunState.BACKED_UP)
        self.lock_with(
            lambda path: None if locked in path.parents else run_lock.acquire(path)
        )
        result = self.accept()
        self.assertEqual(result.code, 1, result.stderr)
        summary = result.stdout.split("\n\n")[-1]
        self.assertIn(
            "albums 6 · would clear 2 · refused 4 · holding at-risk tracks 1 · "
            "no run 0 · skipped 0 · errors 0",
            summary,
        )
        # Each reason in plain words, a raised one's as much as a judgment's.
        # prose: ignore[entry-tag] a refusal reason counted, not a tracking entry
        expected = "album locked 1 · unfinished run 2 · unknown entry 1"
        self.assertIn(f"refused by reason: {expected}", summary)


class ReasonTest(sf.IsolatedCase):
    """Each error raised before the judgment is counted by a plain reason."""

    def test_each_raised_class_has_its_words(self) -> None:
        errors = {
            "album locked": lyrics_album.LockHeldError("x"),
            "linked run folder": lyrics_album.RunFolderLinkError("x"),
            "unsafe leftover": lar.UnsafeAcceptingError("x"),
            "unreadable": PermissionError("x"),
            "could not start": LyricsError("x"),
        }
        for words, error in errors.items():
            with self.subTest(words=words):
                self.assertEqual(sweep.sweep_accept.refusal_reason(error), words)


class LedgerTest(AcceptCase):
    """The ledger's lock is held for the walk, and no ledger line is written."""

    def test_a_held_ledger_lock_is_exit_2_and_nothing_is_touched(self) -> None:
        run_album(self.library, "A")
        before = tree_hashes(self.library)
        with sweep_lock.held(sweep_lock.lock_path(self.ledger)):
            result = self.accept("--apply")
        self.assertEqual(result.code, 2)
        self.assertIn("another sweep is running", result.stderr)
        self.assertEqual(tree_hashes(self.library), before)
        self.assertEqual(self.records(), [])

    def test_the_lock_is_held_while_each_album_is_accepted(self) -> None:
        run_album(self.library, "A")
        seen: list[bool] = []

        def acquire(path: Path) -> run_lock.Lock | None:
            try:
                with sweep_lock.held(sweep_lock.lock_path(self.ledger)):
                    seen.append(False)
            except sweep_lock.SweepLockError:
                seen.append(True)
            return run_lock.acquire(path)

        self.lock_with(acquire)
        self.assertEqual(self.accept().code, 0)
        self.assertEqual(seen, [True])

    def test_no_ledger_file_is_written(self) -> None:
        run_album(self.library, "A")
        self.assertEqual(self.accept("--apply").code, 0)
        self.assertFalse(self.ledger.exists())

    def test_an_existing_ledger_is_left_as_it_was(self) -> None:
        run_album(self.library, "A")
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_bytes(b"")
        self.assertEqual(self.accept("--apply").code, 0)
        self.assertEqual(self.ledger.read_bytes(), b"")


class OptionsTest(AcceptCase):
    """Accept takes only --library, --mode and --apply; --apply is accept's alone."""

    def test_apply_is_refused_by_survey_and_run(self) -> None:
        for mode in ("survey", "run"):
            with self.subTest(mode=mode):
                result = self.main("--mode", mode, "--apply")
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--apply cannot be used with --mode {mode}", result.stderr
                )

    def test_every_other_option_is_refused_by_accept(self) -> None:
        options = (
            ["--offline"],
            ["--limit", "1"],
            ["--restart"],
            ["--no-genius"],
            ["--changed-list", str(self.tmp / "changed.txt")],
            ["--lang", "eng"],
            ["--refetch-untimed"],
            ["--refetch-implausible"],
            ["--pause-seconds", "1"],
            ["--stop-after", "1"],
        )
        for option in options:
            with self.subTest(option=option):
                result = self.accept(*option)
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"{option[0]} cannot be used with --mode accept", result.stderr
                )


class ReportWriteTest(AcceptCase):
    """A report that cannot be written is exit 3."""

    def test_a_closed_stdout_is_exit_3(self) -> None:
        run_album(self.library, "A")

        class Closed(io.StringIO):
            def write(self, text: str) -> int:
                raise OSError("invented closed stdout")

        result = self.main("--mode", "accept", stdout=Closed())
        self.assertEqual(result.code, 3)


class StartTest(AcceptCase):
    """What accept starts with: the real album seams, and the rmtree guard."""

    def test_no_seams_given_runs_the_album_tools_own(self) -> None:
        album = run_album(self.library, "A")
        wiring = sweep.Wiring(sf.FakeAlbumTool(), sf.FakeSleep(), lambda: _NOW)
        out, err = io.StringIO(), io.StringIO()
        argv = ["--mode", "accept", "--apply", "--library", str(self.library)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sweep.main(argv, wiring=wiring)
        self.assertEqual(code, 0, err.getvalue())
        self.assertFalse(os.path.lexists(album / lr.RUN_FOLDER_NAME))
        self.assertEqual(len(self.records()), 1)

    def test_an_rmtree_that_follows_links_is_exit_2_and_nothing_is_touched(
        self,
    ) -> None:
        run_album(self.library, "A")
        before = tree_hashes(self.library)
        rmtree = lyrics_prune_mode.shutil.rmtree
        with mock.patch.object(rmtree, "avoids_symlink_attacks", False):
            result = self.accept("--apply")
        self.assertEqual(result.code, 2)
        self.assertIn("accept will not run", result.stderr)
        self.assertEqual(tree_hashes(self.library), before)
