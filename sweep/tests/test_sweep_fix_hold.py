"""--hold-for-genius in a fix-text sweep: each child's held tracks merged into one list.

The sweep hands each fix child a list of its own (``--hold-for-genius``), merges what it
held into the sweep's list in visit order, and carries the option into the resume
command. A held track ends ``kept existing``, so an album whose open tracks were all
held is done and resets the stop rule's run of refused albums. The album tool is
played by a fake that also writes the hold list, so nothing here runs a process or
reaches a provider; every transcript and lyric is invented for these tests.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_changed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_outcome  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_report  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import HeardRecord, write_record  # noqa: E402 - bootstrap

HOLD = "--hold-for-genius"
#: An invented fingerprint: the store checks only its form.
_FINGERPRINT = "cd" * 32
#: An invented transcript.
_HEARD = "the lantern hums of quiet roads and nobody follows"
#: A track a fix held for Genius.
_HELD = lr.TrackOutcome(
    lr.OutcomeStatus.KEPT_EXISTING, withheld=lyrics_outcomes.HELD_FOR_GENIUS
)


@dataclass
class HoldingAlbumTool(sf.FakeAlbumTool):
    """`sweep_fixtures.FakeAlbumTool`, writing too the list --hold-for-genius names.

    *held* maps an album folder's name to the album-relative names it holds; an
    album named in *no_list* writes no hold list, as a crashed child leaves none.
    """

    held: dict[str, tuple[str, ...]] = field(default_factory=dict)
    no_list: set[str] = field(default_factory=set)

    def __call__(
        self, argv: Sequence[str], timeout_s: float
    ) -> sweep_child.ChildResult:
        """The fake album tool's run, then the hold list it was handed, written."""
        result = super().__call__(argv, timeout_s)
        album_dir = Path(sf.option_value(argv, "--album"))
        if HOLD in argv and album_dir.name not in self.no_list:
            names = self.held.get(album_dir.name, ())
            data, _left = lyrics_changed.format_changed_list(
                str(album_dir / name) for name in names
            )
            Path(sf.option_value(argv, HOLD)).write_bytes(data)
        return result


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class HoldCase(sf.IsolatedCase):
    """Three needy albums, each with a record, a holding fake, the sweep's hold list."""

    def setUp(self) -> None:
        """Albums A1 to A3, each holding a record, and no list yet."""
        super().setUp()
        self.albums = [
            sf.needy_album(self.library, "Artist", f"A{n}") for n in range(1, 4)
        ]
        self.heard = self.tmp / "heard"
        self.heard.mkdir()
        for folder in self.albums:
            track = folder / "02 Plain.mp3"
            write_record(self.heard, HeardRecord(track, _FINGERPRINT, _HEARD))
        self.fake = HoldingAlbumTool()
        self.listed = self.tmp / "held.txt"

    def main(self, *args: str) -> Result:
        """Run a fix sweep with *args*; its exit code and both streams."""
        argv = ["--mode", "fix-text", "--library", str(self.library)]
        argv += ["--heard-dir", str(self.heard), *args]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def hold(self, *args: str) -> Result:
        """A fix sweep writing its hold list to this test's file."""
        return self.main(HOLD, str(self.listed), *args)

    def held_paths(self) -> list[Path]:
        """The sweep's hold list, in order."""
        read = lyrics_changed.parse_changed_list(self.listed.read_bytes())
        assert read is not None
        return [Path(path) for path in read]

    def blocked(self, name: str) -> Path:
        """A list path named *name* that cannot be written: its folder is a file."""
        blocker = self.tmp / f"{name}.blocker"
        blocker.write_bytes(b"")
        return blocker / name

    def entries(self) -> list[sl.LedgerEntry]:
        """Every entry in this library's ledger."""
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        records = sl.read_ledger(path).records
        return [r for r in records if isinstance(r, sl.LedgerEntry)]


class UnionTest(HoldCase):
    """Each child's held tracks, merged in visit order, each once."""

    def test_the_list_is_the_union_of_every_childs_held_tracks(self) -> None:
        self.fake.scripts = {
            name: sf.Script(outcomes=(_HELD,)) for name in ("A1", "A3")
        }
        self.fake.held = {
            "A1": ("02 Plain.mp3",),
            "A3": ("02 Plain.mp3", "03 Bare.mp3"),
        }
        result = self.hold()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(
            self.held_paths(),
            [
                self.albums[0] / "02 Plain.mp3",
                self.albums[2] / "02 Plain.mp3",
                self.albums[2] / "03 Bare.mp3",
            ],
        )

    def test_each_child_is_handed_its_own_list_and_never_no_genius(self) -> None:
        self.hold()
        handed = [Path(sf.option_value(argv, HOLD)) for argv in self.fake.calls]
        self.assertEqual(len(set(handed)), 3)
        self.assertNotEqual(handed[0].parent, self.listed.parent)
        self.assertFalse(handed[0].parent.exists())
        for argv in self.fake.calls:
            self.assertNotIn("--no-genius", argv)

    def test_no_hold_list_is_handed_unless_one_is_named(self) -> None:
        result = self.main()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(len(self.fake.calls), 3)
        for argv in self.fake.calls:
            self.assertNotIn(HOLD, argv)
        self.assertFalse(self.listed.exists())

    def test_a_child_that_left_no_list_adds_nothing_and_says_so(self) -> None:
        self.fake.held = {"A1": ("02 Plain.mp3",), "A2": ("02 Plain.mp3",)}
        self.fake.no_list = {"A2"}
        result = self.hold()
        self.assertEqual(self.held_paths(), [self.albums[0] / "02 Plain.mp3"])
        self.assertIn(
            f"Artist/A2: the album tool left no readable {HOLD}", result.stderr
        )

    def test_an_album_with_nothing_to_fix_adds_nothing_and_says_nothing(self) -> None:
        # The album tool exits 0 having made no run and written no list.
        self.fake.scripts = {"A2": sf.Script(manifest=False, changed=None)}
        self.fake.held = {"A1": ("02 Plain.mp3",)}
        self.fake.no_list = {"A2"}
        result = self.hold()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.held_paths(), [self.albums[0] / "02 Plain.mp3"])
        self.assertNotIn(f"left no readable {HOLD}", result.stderr)
        self.assertEqual(self.entries()[1].outcome, sl.Outcome.DONE)

    def test_a_child_with_no_run_and_no_list_that_failed_still_says_so(self) -> None:
        self.fake.scripts = {
            "A2": sf.Script(manifest=False, exit_code=album.EXIT_TRACK_ERRORS)
        }
        self.fake.no_list = {"A2"}
        result = self.hold()
        self.assertIn(
            f"Artist/A2: the album tool left no readable {HOLD}", result.stderr
        )

    def test_a_run_folder_that_cannot_be_listed_still_says_so(self) -> None:
        # Whether the child made a run cannot be told, so the line is still said.
        self.fake.no_list = {"A1"}
        denied = PermissionError(13, "Permission denied")
        with mock.patch.object(sweep_child, "new_run_ids", side_effect=denied):
            result = self.hold("--limit", "1")
        self.assertIn(
            f"Artist/A1: the album tool left no readable {HOLD}", result.stderr
        )

    def test_an_absorb_told_nothing_says_the_unread_list(self) -> None:
        # The close absorbs what is still pending without knowing how its child
        # ended, so an unread hold list is said.
        notes: list[str] = []
        with sweep_changed.changed_union(
            self.listed, notes.append, kind=sweep_changed.HELD
        ) as union:
            assert union is not None
            union.child_list(self.albums[0], "Artist/A1")
            union.absorb(self.albums[0])
        self.assertEqual(len(notes), 1)
        self.assertIn(f"Artist/A1: the album tool left no readable {HOLD}", notes[0])

    def test_a_resumed_sweep_keeps_the_tracks_already_listed(self) -> None:
        earlier = self.tmp / "elsewhere" / "01 Earlier.mp3"
        self.listed.write_bytes(f"{earlier}\n".encode())
        self.fake.held = {"A2": ("02 Plain.mp3",)}
        result = self.hold()
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(self.held_paths(), [earlier, self.albums[1] / "02 Plain.mp3"])

    def test_a_restart_starts_the_list_empty(self) -> None:
        self.listed.write_bytes(f"{self.tmp / 'old.mp3'}\n".encode())
        self.hold("--restart")
        self.assertEqual(self.held_paths(), [])

    def test_an_existing_list_that_is_no_list_is_a_usage_error(self) -> None:
        self.listed.write_bytes(b"relative.mp3\n")
        result = self.hold()
        self.assertEqual(result.code, 2)
        self.assertIn(f"{HOLD} {self.listed} is not a readable list", result.stderr)
        self.assertEqual(self.listed.read_bytes(), b"relative.mp3\n")
        self.assertEqual(self.fake.calls, [])

    def test_a_list_that_cannot_be_written_is_a_usage_error(self) -> None:
        self.listed = self.blocked("held.txt")
        result = self.hold()
        self.assertEqual(result.code, 2)
        self.assertIn(f"could not write {HOLD} {self.listed}", result.stderr)

    def test_the_lists_errors_name_their_option(self) -> None:
        kinds = (sweep_changed.CHANGED, sweep_changed.HELD)
        self.assertEqual(
            [kind.flag for kind in kinds], [sweep_child.CHANGED_LIST_OPTION, HOLD]
        )


class OutcomeTest(HoldCase):
    """An album whose open tracks were all held is done and resets the stop rule."""

    def test_a_held_album_is_done_and_answered(self) -> None:
        tally = sweep_tally.tally_manifest(sf.manifest_of(_HELD))
        self.assertFalse(tally.refused)
        self.assertEqual((tally.counts.withheld, tally.counts.held), (1, 1))
        ended = sweep_outcome.outcome_of(
            sweep_child.ChildResult(album.EXIT_OK),
            tally,
            offline=False,
            fix_text=True,
            no_run=False,
        )
        self.assertEqual(ended, sl.Outcome.DONE)
        self.assertEqual(sweep_outcome.next_streak(1, ended), 0)

    def test_held_albums_between_refused_ones_never_stop_the_sweep(self) -> None:
        refused = sf.Script(outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS)
        self.fake.scripts = {
            "A1": refused,
            "A2": sf.Script(outcomes=(_HELD,)),
            "A3": refused,
        }
        self.fake.held = {"A2": ("02 Plain.mp3",)}
        result = self.hold("--stop-after", "2")
        self.assertEqual(result.code, 1, result.stderr)
        self.assertEqual(self.fake.albums(), ["A1", "A2", "A3"])
        outcomes = [entry.outcome for entry in self.entries()]
        self.assertEqual(
            outcomes, [sl.Outcome.REFUSED, sl.Outcome.DONE, sl.Outcome.REFUSED]
        )
        self.assertIn("removal withheld: held for Genius 1", result.stdout)
        self.assertEqual(self.entries()[1].counts.held, 1)  # type: ignore[union-attr] # a held album's counts are read

    def test_the_held_count_reads_back_from_the_ledger(self) -> None:
        self.fake.scripts = {"A1": sf.Script(outcomes=(_HELD,))}
        self.hold("--limit", "1")
        (entry,) = self.entries()
        assert entry.counts is not None
        self.assertEqual((entry.counts.withheld, entry.counts.held), (1, 1))


class ResumeTest(HoldCase):
    """A stopped sweep's resume command carries the option and its file."""

    def test_the_resume_command_carries_the_hold_list(self) -> None:
        result = self.hold("--limit", "1")
        (line,) = [ln for ln in result.stdout.splitlines() if ln.startswith("resume:")]
        words = shlex.split(line.removeprefix("resume: "))
        self.assertEqual(sf.option_value(words, HOLD), str(self.listed))

    def test_no_resume_command_names_it_unless_given(self) -> None:
        options = sweep.build_options(
            sweep.build_parser().parse_args(
                ["--mode", "fix-text", "--library", "x", "--heard-dir", "y"]
            )
        )
        command = sweep_report.resume_command(("sweep.py",), self.library, options)
        self.assertNotIn(HOLD, command)


class RefusedTest(HoldCase):
    """The option is the fix pass's alone, and never beside --no-genius."""

    def test_every_other_mode_refuses_the_option(self) -> None:
        for mode in ("survey", "run", "accept", "silence"):
            with self.subTest(mode=mode):
                argv = ["--mode", mode, "--library", str(self.library)]
                argv += [HOLD, str(self.listed)]
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as stop:
                        sweep.main(argv)
                self.assertEqual(stop.exception.code, 2)
                self.assertIn(
                    f"{HOLD} cannot be used with --mode {mode}", stderr.getvalue()
                )

    def test_the_option_beside_no_genius_is_refused(self) -> None:
        result = self.hold("--no-genius")
        self.assertEqual(result.code, 2)
        self.assertIn(f"{HOLD} cannot be used with --no-genius", result.stderr)
        self.assertEqual(self.fake.calls, [])

    def test_a_childs_command_with_the_option_passes_the_album_tools_refusals(
        self,
    ) -> None:
        held = self.tmp / "child-held.txt"
        argv = sweep_child.album_argv(
            self.tmp / "album.py",
            self.albums[0],
            lang=None,
            offline=False,
            mode=lyrics_options.MODE_FIX_TEXT,
            heard_dir=self.heard,
            hold_list=held,
        )
        parser = album.build_parser()
        parser.allow_abbrev = False
        args = parser.parse_args(argv[2:])
        lyrics_options.refuse_misplaced(parser, args)
        self.assertEqual(args.hold_for_genius, held)

    def test_one_file_named_for_both_lists_is_refused(self) -> None:
        self.listed.write_bytes(b"")
        linked = self.tmp / "linked.txt"
        linked.symlink_to(self.listed)
        for case, changed in (("the same path", self.listed), ("a link", linked)):
            with self.subTest(case=case):
                result = self.hold("--changed-list", str(changed))
                self.assertEqual(result.code, 2, result.stderr)
                self.assertIn(
                    f"{HOLD} cannot name the same file as --changed-list",
                    result.stderr,
                )
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.listed.read_bytes(), b"")
