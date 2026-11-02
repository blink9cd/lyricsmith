"""The fix pass at the sweep's level: the child's command, its outcome, a stopped provider.

The fake album tool stands in for the child, as for the run pass; one test runs the real
album tool on an invented album whose only record names a track the fix skips, so no
provider is asked. Every transcript and lyric here is invented for these tests.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fix_text  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_outcome  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_run as sr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import HeardRecord, write_record  # noqa: E402 - bootstrap
from lyrics_provider_states import CoolingState  # noqa: E402 - follows the bootstrap
from sweep_survey import AlbumSurvey  # noqa: E402 - follows the bootstrap

#: How long building the sample album may take.
_CHILD_TIMEOUT_S = 120
_BUILDER = sweep.ALBUM_ENTRY.parent / "samples" / "build_sample_album.py"
#: Every proxy pointed at a closed local port, and no host exempt. Each of the album
#: tool's providers asks through a ``requests`` session that trusts the environment
#: (`lyrics_http.block_netrc` keeps ``trust_env``), so a child that did ask one would
#: fail at once on this machine, never reaching the network.
_DEAD_PROXY = "http://127.0.0.1:9"
_NO_NETWORK = {
    **{
        name: _DEAD_PROXY
        for base in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
        for name in (base, base.lower())
    },
    "NO_PROXY": "",
    "no_proxy": "",
}


def _tally(*outcomes: lr.TrackOutcome) -> sweep_tally.AlbumTally:
    """The tally of a run whose tracks ended as *outcomes*."""
    return sweep_tally.tally_manifest(sf.manifest_of(*outcomes))


class FixArgvTest(sf.IsolatedCase):
    """`album_argv` takes the mode; a fix's command is one the album tool accepts."""

    def test_the_run_pass_command_is_unchanged(self) -> None:
        entry, folder = self.tmp / "album.py", self.library / "A" / "B"
        plain = sweep_child.album_argv(entry, folder, lang=None, offline=False)
        named = sweep_child.album_argv(
            entry, folder, lang=None, offline=False, mode=album.MODE_RUN
        )
        self.assertEqual(plain, named)
        self.assertNotIn("--heard-dir", plain)

    def test_a_fix_command_parses_and_passes_the_album_tools_refusals(self) -> None:
        folder, heard = self.library / "A" / "B", self.tmp / "heard"
        listed = self.tmp / "changed.txt"
        argv = sweep_child.album_argv(
            self.tmp / "album.py",
            folder,
            lang="deu",
            offline=False,
            changed_list=listed,
            skip=tuple(flag for _, flag in sweep_child.SKIP_OPTIONS),
            mode=lyrics_options.MODE_FIX_TEXT,
            heard_dir=heard,
        )
        parser = album.build_parser()
        parser.allow_abbrev = False
        args = parser.parse_args(argv[2:])
        # Exits 2 through the parser when the album tool refuses any option given.
        lyrics_options.refuse_misplaced(parser, args)
        self.assertEqual(args.mode, lyrics_options.MODE_FIX_TEXT)
        self.assertEqual(args.heard_dir, heard)
        self.assertEqual(args.changed_list, listed)
        self.assertIsNone(args.only_providers)


class FixOutcomeTest(sf.IsolatedCase):
    """`outcome_of` for the fix pass: done only when no text was kept."""

    def _ended(
        self, code: int, tally: sweep_tally.AlbumTally | None, *, no_run: bool
    ) -> sl.Outcome:
        result = sweep_child.ChildResult(code)
        return sweep_outcome.outcome_of(
            result, tally, offline=False, fix_text=True, no_run=no_run
        )

    def test_each_finished_fix_ends_as_its_rule_says(self) -> None:
        answered = _tally(*sf.ANSWERED)
        refused = _tally(*sf.REFUSED)
        cases = (
            (album.EXIT_OK, answered, False, sl.Outcome.DONE),
            (album.EXIT_TRACK_ERRORS, answered, False, sl.Outcome.TRACK_ERRORS),
            (album.EXIT_TRACK_ERRORS, refused, False, sl.Outcome.REFUSED),
            (album.EXIT_OK, None, True, sl.Outcome.DONE),
            (album.EXIT_OK, None, False, sl.Outcome.NO_RECORD),
            (album.EXIT_TRACK_ERRORS, None, True, sl.Outcome.NO_RECORD),
        )
        for code, tally, no_run, expected in cases:
            with self.subTest(code=code, no_run=no_run, expected=expected):
                self.assertEqual(self._ended(code, tally, no_run=no_run), expected)

    def test_a_fix_owing_a_provider_is_never_owed(self) -> None:
        # Owed would revisit naming only the debt, leaving every other provider out
        # on purpose, which lets the fix remove a text they never answered for.
        owing = _tally(sf.not_found(sf.refusal()), sf.written())
        self.assertTrue(owing.owed)
        self.assertEqual(
            self._ended(album.EXIT_OK, owing, no_run=False), sl.Outcome.DONE
        )

    def test_the_run_pass_still_owes(self) -> None:
        owing = _tally(sf.not_found(sf.refusal()), sf.written())
        outcome = sweep_outcome.outcome_of(
            sweep_child.ChildResult(album.EXIT_OK), owing, offline=False
        )
        self.assertEqual(outcome, sl.Outcome.OWED)


class WithheldTest(sf.IsolatedCase):
    """A removal a fix withheld on a spoken album: kept, counted apart, done."""

    def test_a_withheld_removal_is_done_and_no_refusal(self) -> None:
        withheld = lr.TrackOutcome(
            lr.OutcomeStatus.KEPT_EXISTING, withheld="spoken word (genre Comedy)"
        )
        # Beside a track every provider refused, the withheld one still shows the
        # providers answering, emptily: the album is not refused.
        tally = _tally(withheld, sf.not_found(sf.refusal()))
        self.assertFalse(tally.refused)
        self.assertEqual(
            (tally.counts.kept, tally.counts.withheld, tally.counts.errors), (1, 1, 0)
        )
        ended = sweep_outcome.outcome_of(
            sweep_child.ChildResult(album.EXIT_OK),
            tally,
            offline=False,
            fix_text=True,
            no_run=False,
        )
        self.assertEqual(ended, sl.Outcome.DONE)

    def test_a_plain_kept_track_is_not_withheld(self) -> None:
        tally = _tally(lr.TrackOutcome(lr.OutcomeStatus.KEPT_EXISTING))
        self.assertEqual((tally.counts.kept, tally.counts.withheld), (1, 0))


class StoppedProviderTest(sf.SweepCase):
    """A provider stopped part-way through one album's fix: no later album is narrowed."""

    def _fix(self) -> sr.SweepReport:
        seams = sr.SweepSeams(
            run_child=self.run_child,
            sleep=self.sleep_fn,
            now=sf.fixed_now,
            progress=self.notes.append,
            entry=self.tmp / "album.py",
        )
        options = replace(sf.OPTIONS, heard_dir=self.tmp / "heard")
        # The store holds no record of these albums, before or after each fake fix,
        # so a done album's digest still agrees and it is passed over.
        empty = sweep_fix_text.records_digest(())
        surveys = [
            AlbumSurvey(album, untimed=1, records_digest=empty)
            for album in self.albums[:3]
        ]
        return sr.sweep(self.library, surveys, self.ledger, options, seams)

    def test_a_stopped_provider_leaves_its_album_to_retry_and_narrows_no_visit(
        self,
    ) -> None:
        # A1's fix met Tekstowo's stop: the album tool kept the text (an error) and
        # Tekstowo's cool-down now holds for every later album's child, which reads
        # it itself and so removes nothing. The sweep must hand no child an option
        # that would leave Tekstowo out on purpose.
        stopped = sf.Script(
            outcomes=(sf.fetch_error(sf.refusal(lm.Source.TEKSTOWO)), sf.written()),
            exit_code=album.EXIT_TRACK_ERRORS,
            tekstowo=CoolingState.STOPPED,
        )
        self.fake.scripts["A1"] = stopped
        self.fake.scripts["A2"] = replace(stopped, tekstowo=CoolingState.COOLING_DOWN)
        report = self._fix()
        self.assertEqual(sr.sweep_exit_code(report), sr.EXIT_ALBUM_ERRORS)
        for argv in self.fake.calls:
            for flag in ("--only-providers", "--offline", "--no-tekstowo"):
                self.assertNotIn(flag, argv)
        outcomes = [v.entry.outcome for v in report.visited]
        self.assertNotIn(sl.Outcome.DONE, outcomes[:2])
        self.assertNotIn(sl.Outcome.OWED, outcomes)
        self.fake.calls.clear()
        self._fix()
        self.assertEqual(self.fake.albums(), ["A1", "A2"])
        for argv in self.fake.calls:
            self.assertNotIn("--only-providers", argv)


class RealChildTest(sf.IsolatedCase):
    """The real album tool, as the fix's child, on an album with nothing to fix."""

    def test_the_real_album_tool_takes_the_fix_command_and_the_album_is_done(
        self,
    ) -> None:
        folder = self.library / "The Invented Quartet" / "Sample Album"
        subprocess.run(  # reliability: ignore[RL005] DEVNULL sinks, no pipe to hold
            [sys.executable, str(_BUILDER), "--out", str(folder)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CHILD_TIMEOUT_S,
        )
        # The record names the timed track: the fix skips it as not untimed only, so
        # it asks no provider and makes no run.
        (timed,) = (lrc.with_suffix(".mp3") for lrc in folder.glob("*.lrc"))
        heard = self.tmp / "heard"
        write_record(heard, HeardRecord(timed, "cd" * 32, "an invented transcript"))
        environ = {**sf.env_without_ffmpeg(self.tmp), **_NO_NETWORK}
        self.enterContext(mock.patch.dict(os.environ, environ))
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["--mode", "fix-text", "--library", str(self.library)]
        argv += ["--heard-dir", str(heard), "--pause-seconds", "0"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(argv)
        self.assertEqual(code, 0, stderr.getvalue())
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        (entry,) = [
            r for r in sl.read_ledger(path).records if isinstance(r, sl.LedgerEntry)
        ]
        self.assertEqual(entry.outcome, sl.Outcome.DONE)
        self.assertEqual(entry.pass_, sl.SweepPass.FIX_TEXT)
        self.assertIsNone(entry.run_id)
        self.assertTrue((heard / next(iter(os.listdir(heard)))).is_file())
