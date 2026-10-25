"""--changed-list in a sweep: the union of every album's list, kept current as it goes.

The album tool is played by `sweep_fixtures.FakeAlbumTool`, which writes the list each
child is handed, so nothing here runs a process or reaches a provider. The format is
the list file contract; the sweep reads each child's list back with the album tool's
strict reader.
"""

from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - its bootstrap puts src/ and the album tool on the path
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run  # noqa: E402 - import follows the sys.path bootstrap above

#: The tracks `sweep_fixtures.needy_album` makes, in album order.
_NEEDY = ("01 Timed.mp3", "02 Plain.mp3", "03 Bare.mp3")


class ChangedCase(sf.IsolatedCase):
    """Two needy albums, a fake album tool, and the sweep's list file."""

    def setUp(self) -> None:
        """Build albums A1 and A2."""
        super().setUp()
        self.a1 = sf.needy_album(self.library, "Artist", "A1")
        self.a2 = sf.needy_album(self.library, "Artist", "A2")
        self.fake = sf.FakeAlbumTool()
        self.listed = self.tmp / "changed.txt"
        self.runner: sweep_child.ChildRunner = self.fake

    def main(self, *args: str) -> tuple[int, str]:
        """Run a sweep with *args* and --changed-list; its exit code and stderr."""
        argv = ["--mode", "run", "--library", str(self.library), *args]
        wiring = sweep.Wiring(self.runner, sf.FakeSleep(), sf.fixed_now)
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return code, stderr.getvalue()

    def sweep(self, *args: str) -> tuple[int, str]:
        """A sweep writing its list to this test's file."""
        return self.main("--changed-list", str(self.listed), *args)

    def changed(self) -> list[Path]:
        """The sweep's list, in order."""
        read = lyrics_changed.parse_changed_list(self.listed.read_bytes())
        assert read is not None
        return [Path(path) for path in read]


class UnionTest(ChangedCase):
    """Each album's list, merged in visit order, each path once."""

    def test_the_list_is_the_union_of_every_albums_list(self) -> None:
        self.fake.scripts["A1"] = sf.Script(changed=("01 Timed.mp3", "03 Bare.mp3"))
        self.fake.scripts["A2"] = sf.Script(changed=("02 Plain.mp3",))
        code, _ = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_OK)
        self.assertEqual(
            self.changed(),
            [
                self.a1 / "01 Timed.mp3",
                self.a1 / "03 Bare.mp3",
                self.a2 / "02 Plain.mp3",
            ],
        )

    def test_each_child_is_handed_its_own_list_in_a_private_folder_removed_after(
        self,
    ) -> None:
        self.sweep()
        handed = [
            Path(sf.option_value(argv, sweep_child.CHANGED_LIST_OPTION))
            for argv in self.fake.calls
        ]
        self.assertEqual(len(set(handed)), 2)
        self.assertEqual(handed[0].parent, handed[1].parent)
        self.assertNotEqual(handed[0].parent, self.listed.parent)
        self.assertFalse(handed[0].parent.exists())

    def test_no_list_is_handed_to_a_child_unless_one_is_named(self) -> None:
        code, _ = self.main()
        self.assertEqual(code, sweep_run.EXIT_OK)
        # Both albums ran, so the check below reads two commands, never none.
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertTrue(
            all(sweep_child.CHANGED_LIST_OPTION not in argv for argv in self.fake.calls)
        )
        self.assertFalse(self.listed.exists())

    def test_a_path_two_lists_name_is_listed_once(self) -> None:
        both = sf.Script(changed=("../A1/01 Timed.mp3",))
        self.fake.scripts["A1"] = sf.Script(changed=("01 Timed.mp3",))
        self.fake.scripts["A2"] = both
        self.sweep()
        self.assertEqual(self.changed(), [self.a1 / "01 Timed.mp3"])

    def test_a_child_whose_run_errored_but_left_a_list_is_taken_at_its_word(
        self,
    ) -> None:
        self.fake.scripts["A1"] = sf.Script(
            exit_code=album.EXIT_TRACK_ERRORS, changed=("02 Plain.mp3",)
        )
        self.fake.scripts["A2"] = sf.Script(changed=())
        self.sweep()
        self.assertEqual(self.changed(), [self.a1 / "02 Plain.mp3"])


class UnknownChangesTest(ChangedCase):
    """A child that left no readable list has every MP3 of its album listed."""

    def test_a_child_that_left_no_list_has_its_album_listed_whole(self) -> None:
        self.fake.scripts["A1"] = sf.Script(exit_code=None, pid=4321, changed=None)
        self.fake.scripts["A2"] = sf.Script(changed=("02 Plain.mp3",))
        _, stderr = self.sweep()
        self.assertEqual(
            self.changed(), [*(self.a1 / n for n in _NEEDY), self.a2 / "02 Plain.mp3"]
        )
        self.assertIn(
            "Artist/A1: every MP3 of the album is listed in --changed-list, as "
            "the album tool left no readable list of what it changed",
            stderr,
        )

    def test_a_list_the_contract_refuses_is_no_readable_list(self) -> None:
        self.fake.scripts["A1"] = sf.Script(changed=b"relative.mp3\n")
        self.fake.scripts["A2"] = sf.Script(changed=())
        self.sweep()
        self.assertEqual(self.changed(), [self.a1 / n for n in _NEEDY])

    def test_an_album_whose_tracks_cannot_be_found_is_said(self) -> None:
        notes: list[str] = []
        folder = self.tmp / "work"
        union = sweep_changed.ChangedUnion(self.listed, folder, notes.append)
        gone = self.library / "Artist" / "Gone"
        union.child_list(gone, "Artist/Gone")
        union.absorb(gone)
        self.assertEqual(self.changed(), [])
        (note,) = notes
        self.assertTrue(
            note.startswith(
                "Artist/Gone: the album tool left no readable list of what it changed, "
                "and the album's MP3s could not be listed: "
            )
        )

    def test_an_mp3_the_list_cannot_hold_is_left_out_and_said(self) -> None:
        odd = sf.add_track(self.a1, "04 Line\nBreak.mp3")
        self.fake.scripts["A1"] = sf.Script(changed=None)
        self.fake.scripts["A2"] = sf.Script(changed=())
        _, stderr = self.sweep()
        self.assertNotIn(odd, self.changed())
        left = lyrics_changed.LeftOut(str(odd), lyrics_changed.REASON_LINEFEED)
        self.assertIn(lyrics_changed.left_out_notice(left), stderr)


class KeptCurrentTest(ChangedCase):
    """The list is rewritten after each album, so a stop at any point leaves the union."""

    def test_the_list_holds_each_finished_album_before_the_next_starts(self) -> None:
        self.fake.scripts["A1"] = sf.Script(changed=("01 Timed.mp3",))
        seen: list[bytes] = []

        def runner(argv: Sequence[str], timeout_s: float) -> sweep_child.ChildResult:
            seen.append(self.listed.read_bytes())
            return self.fake(argv, timeout_s)

        self.runner = runner
        self.sweep()
        self.assertEqual(seen, [b"", f"{self.a1 / '01 Timed.mp3'}\n".encode()])

    def test_an_interrupted_child_leaves_its_list_in_the_union(self) -> None:
        self.fake.scripts["A1"] = sf.Script(
            exit_code=album.EXIT_INTERRUPTED,
            interrupted=True,
            changed=("01 Timed.mp3",),
        )
        code, _ = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_INTERRUPTED)
        self.assertEqual(self.fake.albums(), ["A1"])
        self.assertEqual(self.changed(), [self.a1 / "01 Timed.mp3"])

    def test_a_ctrl_c_that_ends_the_sweep_outside_a_visit_leaves_the_list(
        self,
    ) -> None:
        self.fake.scripts["A1"] = sf.Script(changed=("01 Timed.mp3",))
        real = sweep._notice

        def notice(text: str) -> None:
            if text.startswith("[2/2]"):
                raise KeyboardInterrupt
            real(text)

        with mock.patch.object(sweep, "_notice", notice):
            code, _ = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_INTERRUPTED)
        self.assertEqual(self.changed(), [self.a1 / "01 Timed.mp3"])

    def test_a_ctrl_c_between_a_visit_and_its_absorb_is_still_absorbed(
        self,
    ) -> None:
        # The child has ended and its list is final; the sweep's own handler path
        # still reads it in before the list is last written.
        self.fake.scripts["A1"] = sf.Script(changed=("01 Timed.mp3",))
        real = sweep_changed.ChangedUnion.absorb
        calls: list[Path] = []

        def absorb(union: sweep_changed.ChangedUnion, album: Path) -> None:
            calls.append(album)
            if len(calls) == 1:
                raise KeyboardInterrupt
            real(union, album)

        with mock.patch.object(sweep_changed.ChangedUnion, "absorb", absorb):
            code, _ = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_INTERRUPTED)
        self.assertEqual(self.changed(), [self.a1 / "01 Timed.mp3"])


class RefusalAndFailureTest(ChangedCase):
    """The survey refuses the option, and a list that cannot be written is exit 2."""

    def test_the_survey_refuses_it(self) -> None:
        argv = ["--library", str(self.library), "--changed-list", str(self.listed)]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            sweep.main(argv)
        self.assertEqual(stop.exception.code, sweep_run.EXIT_USAGE)
        self.assertIn(
            "--changed-list cannot be used with --mode survey", stderr.getvalue()
        )

    def test_a_list_that_cannot_be_written_is_exit_2(self) -> None:
        blocker = self.tmp / "a file"
        blocker.write_bytes(b"")
        self.listed = blocker / "changed.txt"
        code, stderr = self.sweep()
        self.assertEqual(code, sweep_run.EXIT_USAGE)
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        self.assertIn(f"could not write --changed-list {self.listed}", stderr)


class ArgvTest(sf.IsolatedCase):
    """The child's command line carries the per-album list when there is one."""

    def test_the_list_follows_the_album_options(self) -> None:
        argv = sweep_child.album_argv(
            Path("/e/album.py"),
            Path("/l/a"),
            lang=None,
            offline=False,
            changed_list=Path("/t/1.list"),
            interpreter="python",
        )
        self.assertEqual(argv[-2:], ["--changed-list", "/t/1.list"])


class UnionMechanicsTest(ChangedCase):
    """Absorbing is idempotent, and a list still pending at the close is absorbed then."""

    def setUp(self) -> None:
        """A union over this test's list file, with a scratch folder of its own."""
        super().setUp()
        self.notes: list[str] = []
        self.folder = self.tmp / "work"
        self.union = sweep_changed.ChangedUnion(
            self.listed, self.folder, self.notes.append
        )

    def test_absorbing_an_album_again_changes_nothing(self) -> None:
        self.union.child_list(self.a1, "Artist/A1").write_bytes(b"")
        self.union.absorb(self.a1)
        self.listed.write_bytes(b"/marker.mp3\n")
        self.union.absorb(self.a1)
        self.assertEqual(self.listed.read_bytes(), b"/marker.mp3\n")
        self.assertEqual(self.notes, [])

    def test_a_list_pending_at_the_close_is_absorbed_and_the_folder_removed(
        self,
    ) -> None:
        handed = self.union.child_list(self.a2, "Artist/A2")
        handed.write_bytes(f"{self.a2 / '03 Bare.mp3'}\n".encode())
        self.union.close()
        self.assertEqual(self.changed(), [self.a2 / "03 Bare.mp3"])
        self.assertFalse(self.folder.exists())

    def test_a_ctrl_c_before_the_absorb_is_held_keeps_the_list_for_the_retry(
        self,
    ) -> None:
        handed = self.union.child_list(self.a1, "Artist/A1")
        handed.write_bytes(f"{self.a1 / '01 Timed.mp3'}\n".encode())
        real = sweep_changed.ChangedUnion._read_child
        calls: list[Path] = []

        def read_child(
            union: sweep_changed.ChangedUnion,
            album: Path,
            pending: sweep_changed._Pending,
            *,
            started: bool,
        ) -> Iterable[str]:
            calls.append(album)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return real(union, album, pending, started=started)

        with (
            mock.patch.object(sweep_changed.ChangedUnion, "_read_child", read_child),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.union.absorb(self.a1)
        with mock.patch.object(sweep_changed.ChangedUnion, "_read_child", read_child):
            self.union.absorb(self.a1)
        self.assertEqual(self.changed(), [self.a1 / "01 Timed.mp3"])
        self.assertEqual(self.notes, [])

    def test_a_child_list_that_cannot_be_removed_is_said_not_raised(self) -> None:
        handed = self.union.child_list(self.a1, "Artist/A1")
        handed.write_bytes(f"{self.a1 / '01 Timed.mp3'}\n".encode())
        denied = PermissionError(13, "Permission denied", str(handed))
        with mock.patch.object(sweep_changed.Path, "unlink", side_effect=denied):
            self.union.absorb(self.a1)
        self.assertEqual(self.changed(), [self.a1 / "01 Timed.mp3"])
        self.assertIsNone(self.union.error)
        (note,) = self.notes
        self.assertIn("Artist/A1", note)
        self.assertIn("could not remove", note)
        self.assertIn("Permission denied", note)
