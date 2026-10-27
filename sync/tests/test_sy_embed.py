"""--embed: album's arguments, a real child run of a fake album, and each exit merged.

The album tool saves MP3s in place, so a run past its bound must be stopped the way
Ctrl-C stops it, never killed first. A scripted process plays the waits, so the order
of the signal and the kill is seen without a real child.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_embed  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_child import ChildResult  # noqa: E402 - follows the bootstrap above
from sy_types import EmbedResult, EmbedStop  # noqa: E402 - follows the bootstrap

#: A stand-in for album.py: it logs its arguments and exits as the environment says.
FAKE_ALBUM = """\
import json, os, sys
with open(os.environ["FAKE_ALBUM_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_ALBUM_EXIT", "0")))
"""
#: Outlives the timeout test's bound, and stops on Ctrl-C's signal, quietly, as album does.
SLEEPER = (
    "import signal, sys, time;"
    " signal.signal(signal.SIGINT, lambda *_: sys.exit(130)); time.sleep(5)"
)
#: The exit code a scripted child reports once it has been killed.
KILLED = -9
#: A library: artist and album folders, and a multi-disc album's disc folders below.
LIB = Path("/lib")
DISC_ONE = LIB / "Artist" / "Album" / "CD1" / "01 A.mp3"
DISC_TWO = LIB / "Artist" / "Album" / "CD2" / "01 B.mp3"
OTHER = LIB / "Artist" / "Other" / "01 C.mp3"
LOOSE = LIB / "Artist" / "01 D.mp3"


@dataclass
class ScriptedProcess:
    """A process whose waits play *steps* in order, logging each signal and kill."""

    steps: list[int | BaseException]
    events: list[str] = field(default_factory=list)
    pid: int = 4321

    def wait(self, timeout: float | None = None) -> int:
        """The next step: an exit code returned, or an exception raised."""
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def send_signal(self, sig: int) -> None:
        """Log the signal by name."""
        self.events.append(signal.Signals(sig).name)

    def kill(self) -> None:
        """Log the kill."""
        self.events.append("kill")


class RecordingRunner:
    """A stand-in album runner: records each argv and returns its *results* in turn."""

    def __init__(self, *results: ChildResult) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> ChildResult:
        self.calls.append(list(argv))
        return self.results.pop(0)


class EmbedTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.entry = self.tmp / "album.py"
        self.entry.write_text(FAKE_ALBUM, encoding="utf-8", newline="\n")
        self.log = self.tmp / "calls.jsonl"
        self.enterContext(
            mock.patch.dict(os.environ, {"FAKE_ALBUM_LOG": str(self.log)})
        )

    def calls(self) -> list[list[str]]:
        """Each argv the fake album was run with."""
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def test_album_runs_offline_on_each_folder(self) -> None:
        units = sy_embed.EmbedUnits((self.tmp / "One", self.tmp / "Two"))
        results = sy_embed.embed_folders(units, self.entry)
        self.assertEqual([r.code for r in results], [0, 0])
        expected = [
            ["--album", str(self.tmp / name), "--mode", "run", "--offline"]
            for name in ("One", "Two")
        ]
        self.assertEqual(self.calls(), expected)

    def test_a_refused_album_run_keeps_its_exit(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_ALBUM_EXIT": "2"}):
            units = sy_embed.EmbedUnits((self.tmp,))
            (result,) = sy_embed.embed_folders(units, self.entry)
        self.assertEqual((result.code, result.ok), (2, False))

    def test_a_real_child_past_its_bound_is_stopped_and_not_done(self) -> None:
        child = sy_embed.run_album([sys.executable, "-c", SLEEPER], 0.2)
        self.assertTrue(child.timed_out)
        self.assertIs(sy_embed.embed_result(self.tmp, child).code, EmbedStop.TIMED_OUT)

    def test_an_album_that_cannot_start_is_not_done(self) -> None:
        child = sy_embed.run_album([str(self.tmp / "missing")], 5.0)
        result = sy_embed.embed_result(self.tmp, child)
        self.assertEqual((result.code, result.ok), (EmbedStop.NOT_STARTED, False))

    def test_past_the_bound_album_gets_sigint_before_any_kill(self) -> None:
        timeout = subprocess.TimeoutExpired("album", 1.0)
        process = ScriptedProcess([timeout, timeout, KILLED])

        def popen(argv: Sequence[str], **_kwargs: object) -> ScriptedProcess:
            return process

        child = sy_embed.run_album(["album"], 1.0, popen=popen)
        self.assertEqual(process.events, ["SIGINT", "kill"])
        self.assertIs(sy_embed.embed_result(self.tmp, child).code, EmbedStop.TIMED_OUT)

    def test_a_child_that_stops_on_sigint_is_never_killed(self) -> None:
        timeout = subprocess.TimeoutExpired("album", 1.0)
        process = ScriptedProcess([timeout, 130])

        def popen(argv: Sequence[str], **_kwargs: object) -> ScriptedProcess:
            return process

        sy_embed.run_album(["album"], 1.0, popen=popen)
        self.assertEqual(process.events, ["SIGINT"])

    def test_an_interrupt_stops_the_remaining_folders(self) -> None:
        folders = (self.tmp / "One", self.tmp / "Two", self.tmp / "Three")
        run = RecordingRunner(ChildResult(130, interrupted=True))
        results = sy_embed.embed_folders(sy_embed.EmbedUnits(folders), self.entry, run)
        self.assertEqual(len(run.calls), 1)
        expected = [
            EmbedResult(folders[0], EmbedStop.INTERRUPTED),
            EmbedResult(folders[1], EmbedStop.NOT_RUN),
            EmbedResult(folders[2], EmbedStop.NOT_RUN),
        ]
        self.assertEqual(results, expected)
        self.assertFalse(any(result.ok for result in results))

    def test_ctrl_c_during_a_real_run_marks_it_interrupted(self) -> None:
        process = ScriptedProcess([KeyboardInterrupt(), 130])

        def popen(argv: Sequence[str], **_kwargs: object) -> ScriptedProcess:
            return process

        child = sy_embed.run_album(["album"], 1.0, popen=popen)
        stop = sy_embed.embed_result(self.tmp, child).code
        self.assertIs(stop, EmbedStop.INTERRUPTED)

    def test_each_way_a_run_ends_maps_to_its_result(self) -> None:
        cases = (
            (ChildResult(0), 0),
            (ChildResult(3), 3),
            (ChildResult(0, timed_out=True), EmbedStop.TIMED_OUT),
            (ChildResult(None, timed_out=True, pid=7), EmbedStop.NOT_REAPED),
            (ChildResult(None), EmbedStop.NOT_STARTED),
        )
        for child, code in cases:
            with self.subTest(child=child):
                self.assertEqual(sy_embed.embed_result(self.tmp, child).code, code)

    def test_a_loose_track_is_not_embedded_and_names_itself(self) -> None:
        track = self.tmp / "Loose" / "01 A.mp3"
        units = sy_embed.EmbedUnits((self.tmp / "One",), loose=(track,))
        results = sy_embed.embed_folders(units, self.entry)
        self.assertEqual(len(self.calls()), 1)
        loose = EmbedResult(track.parent, EmbedStop.LOOSE, track=track)
        self.assertEqual(results[1], loose)

    def test_a_ctrl_c_between_runs_stops_the_remaining_folders(self) -> None:
        folders = (self.tmp / "One", self.tmp / "Two", self.tmp / "Three")
        calls: list[Sequence[str]] = []

        def run(argv: Sequence[str], timeout: float) -> ChildResult:
            calls.append(argv)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return ChildResult(0)

        results = sy_embed.embed_folders(sy_embed.EmbedUnits(folders), self.entry, run)
        expected = [
            EmbedResult(folders[0], 0),
            EmbedResult(folders[1], EmbedStop.NOT_RUN),
            EmbedResult(folders[2], EmbedStop.NOT_RUN),
        ]
        self.assertEqual((results, len(calls)), (expected, 2))

    def test_the_argv_is_the_sweeps_offline_run_in_this_interpreter(self) -> None:
        calls: list[Sequence[str]] = []

        def run(argv: Sequence[str], timeout: float) -> ChildResult:
            calls.append(argv)
            return ChildResult(0)

        sy_embed.embed_folders(sy_embed.EmbedUnits((self.tmp,)), self.entry, run)
        expected = sweep_child.album_argv(self.entry, self.tmp, lang=None, offline=True)
        self.assertEqual(calls, [expected])
        # Spelled out too, so a change to the sweep's builder cannot pass here unseen.
        literal = ["--album", str(self.tmp), "--mode", "run", "--offline"]
        self.assertEqual(list(calls[0]), [sys.executable, str(self.entry), *literal])


class EmbedUnitsTest(sy_cases.IsolatedCase):
    """The folder album runs on for each written track, as the sweep walks a library."""

    def test_a_multi_disc_album_in_a_library_is_one_run(self) -> None:
        units = sy_embed.embed_units((DISC_ONE, DISC_TWO), (DISC_ONE, DISC_TWO), LIB)
        self.assertEqual(units, sy_embed.EmbedUnits((LIB / "Artist" / "Album",)))

    def test_a_multi_disc_album_given_alone_is_one_run_on_it(self) -> None:
        album = LIB / "Artist" / "Album"
        units = sy_embed.embed_units((DISC_TWO, DISC_ONE), (DISC_ONE, DISC_TWO), album)
        self.assertEqual(units, sy_embed.EmbedUnits((album,)))

    def test_a_library_runs_each_written_album_once_in_order(self) -> None:
        tracks = (OTHER, DISC_TWO, DISC_ONE)
        units = sy_embed.embed_units(tracks, tracks, LIB)
        expected = (LIB / "Artist" / "Album", LIB / "Artist" / "Other")
        self.assertEqual(units, sy_embed.EmbedUnits(expected))

    def test_a_loose_track_in_a_library_is_left_with_its_folder(self) -> None:
        units = sy_embed.embed_units((LOOSE, OTHER), (LOOSE, OTHER, DISC_ONE), LIB)
        expected = sy_embed.EmbedUnits((LIB / "Artist" / "Other",), loose=(LOOSE,))
        self.assertEqual(units, expected)

    def test_only_an_unwritten_track_makes_a_library(self) -> None:
        units = sy_embed.embed_units((LOOSE,), (LOOSE, DISC_ONE), LIB)
        self.assertEqual(units, sy_embed.EmbedUnits((), loose=(LOOSE,)))

    def test_a_track_at_the_library_root_is_loose_too(self) -> None:
        at_root = LIB / "01 E.mp3"
        units = sy_embed.embed_units((at_root, LOOSE), (at_root, LOOSE, OTHER), LIB)
        self.assertEqual(units, sy_embed.EmbedUnits((), loose=(at_root, LOOSE)))

    def test_a_flat_album_or_a_named_track_is_one_run_on_the_root(self) -> None:
        album = LIB / "Artist" / "Other"
        self.assertEqual(
            sy_embed.embed_units((OTHER,), (OTHER,), album),
            sy_embed.EmbedUnits((album,)),
        )

    def test_nothing_written_runs_nothing(self) -> None:
        self.assertEqual(
            sy_embed.embed_units((), (OTHER, DISC_ONE), LIB), sy_embed.EmbedUnits(())
        )

    def test_a_track_of_another_format_stays_a_sidecar(self) -> None:
        flac = LIB / "Artist" / "Other" / "02 F.FLAC"
        album = flac.parent
        self.assertEqual(
            sy_embed.embed_units((flac,), (flac, OTHER), album),
            sy_embed.EmbedUnits((), sidecars=(flac,)),
        )

    def test_in_a_library_only_the_mp3s_make_album_runs(self) -> None:
        flac = LIB / "Artist" / "Other" / "02 F.flac"
        units = sy_embed.embed_units((flac, DISC_ONE), (flac, DISC_ONE, OTHER), LIB)
        expected = (LIB / "Artist" / "Album",)
        self.assertEqual(units, sy_embed.EmbedUnits(expected, sidecars=(flac,)))
