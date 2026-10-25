"""The sweep's passes: a normal one, and one re-asking the album tool's untimed-only tracks.

Each pass keeps its own done set in the one ledger of the library: an album done by one
pass is still visited by the other, and a restart forgets only its own pass. The ledger
is read and written for real under the isolated state directory; the album tool is played
by `sweep_fixtures.FakeAlbumTool`, so nothing here reaches a provider.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above

_OPTION = "--refetch-untimed"
_TIME = "2026-10-19T09:30:00+00:00"
_LANG = sl.DEFAULT_LANG
_NORMAL, _REFETCH = sl.SweepPass.NORMAL, sl.SweepPass.REFETCH_UNTIMED
_VISIT_LINE = "albums a run would visit:"
_DONE_LINE = "already done, passed over by a run:"


def _done(album: str, pass_: sl.SweepPass) -> sl.LedgerEntry:
    """A done entry for *album* in the default language, made by *pass_*."""
    return sl.LedgerEntry(album, None, 0, sl.Outcome.DONE, _TIME, pass_=pass_)


class PassRecordTest(sf.IsolatedCase):
    """The pass is on every line, and a line from before it was recorded is normal."""

    def test_an_entry_round_trips_its_pass(self) -> None:
        entry = _done("A/B", _REFETCH)
        line = sl.encode_record(entry)
        self.assertEqual(json.loads(line)["pass"], "refetch-untimed")
        self.assertEqual(sl.parse_record(line), entry)

    def test_a_restart_mark_round_trips_its_pass(self) -> None:
        mark = sl.RestartMark(_TIME, pass_=_REFETCH)
        line = sl.encode_record(mark)
        self.assertEqual(json.loads(line)["pass"], "refetch-untimed")
        self.assertEqual(sl.parse_record(line), mark)

    def test_an_entry_without_a_pass_reads_as_normal(self) -> None:
        document = json.loads(sl.encode_record(_done("A/B", _REFETCH)))
        del document["pass"]
        record = sl.parse_record(json.dumps(document))
        assert isinstance(record, sl.LedgerEntry)
        self.assertIs(record.pass_, _NORMAL)

    def test_a_restart_mark_without_a_pass_reads_as_normal(self) -> None:
        record = sl.parse_record(json.dumps({"kind": "restart", "time": _TIME}))
        self.assertEqual(record, sl.RestartMark(_TIME, pass_=_NORMAL))

    def test_an_unknown_or_untyped_pass_is_refused(self) -> None:
        document = json.loads(sl.encode_record(_done("A/B", _NORMAL)))
        for value in ("sideways", 3, None):
            with self.subTest(value=value), self.assertRaises(sl.LedgerLineError):
                sl.parse_record(json.dumps({**document, "pass": value}))
        with self.assertRaises(sl.LedgerLineError):
            sl.parse_record(json.dumps({"kind": "restart", "time": _TIME, "pass": 1}))


class DoneByPassTest(sf.IsolatedCase):
    """An album is done per pass; a restart forgets only its own pass."""

    def test_an_album_done_in_one_pass_is_not_done_in_the_other(self) -> None:
        records: list[sl.LedgerRecord] = [
            _done("A/Normal", _NORMAL),
            _done("A/Refetch", _REFETCH),
        ]
        self.assertEqual(sl.done_albums(records, _LANG, _NORMAL), {"A/Normal"})
        self.assertEqual(sl.done_albums(records, _LANG, _REFETCH), {"A/Refetch"})

    def test_a_restart_mark_forgets_only_its_own_pass(self) -> None:
        records: list[sl.LedgerRecord] = [
            _done("A/Normal", _NORMAL),
            _done("A/Refetch", _REFETCH),
            sl.RestartMark(_TIME, pass_=_REFETCH),
        ]
        self.assertEqual(sl.done_albums(records, _LANG, _NORMAL), {"A/Normal"})
        self.assertEqual(sl.done_albums(records, _LANG, _REFETCH), frozenset())
        records.append(sl.RestartMark(_TIME))
        self.assertEqual(sl.done_albums(records, _LANG, _NORMAL), frozenset())

    def test_an_old_ledger_counts_for_the_normal_pass(self) -> None:
        old = {"kind": "album", **json.loads(sl.encode_record(_done("A/B", _NORMAL)))}
        del old["pass"]
        records = [sl.parse_record(json.dumps(old))]
        self.assertEqual(sl.done_albums(records, _LANG, _NORMAL), {"A/B"})
        self.assertEqual(sl.done_albums(records, _LANG, _REFETCH), frozenset())


class ArgvTest(sf.IsolatedCase):
    """The refetch pass hands its option to the album tool; a normal one never does."""

    def test_the_option_reaches_the_command_line_only_when_asked(self) -> None:
        def argv(refetch: bool) -> list[str]:
            return sweep_child.album_argv(
                self.tmp / "e.py",
                self.library,
                lang=None,
                offline=False,
                refetch_untimed=refetch,
            )

        self.assertEqual(argv(True)[-1], _OPTION)
        self.assertNotIn(_OPTION, argv(False))


@dataclass(frozen=True)
class _Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class PassCliCase(sf.IsolatedCase):
    """Three needy albums, A1 to A3, run through `main` with a fake album tool."""

    def setUp(self) -> None:
        """Build the albums; the ledger is not made."""
        super().setUp()
        for name in ("A1", "A2", "A3"):
            sf.needy_album(self.library, "Artist", name)
        self.fake = sf.FakeAlbumTool()
        self.ledger = sl.ledger_path(self.library, os.environ)

    def main(self, *args: str) -> _Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return _Result(code, stdout.getvalue(), stderr.getvalue())

    def entries(self) -> list[sl.LedgerEntry]:
        """Every entry in this library's ledger, in order."""
        records = sl.read_ledger(self.ledger).records
        return [r for r in records if isinstance(r, sl.LedgerEntry)]

    @staticmethod
    def listed(stdout: str, heading: str) -> list[str]:
        """The albums listed under the survey line starting *heading*, in order."""
        lines = stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(heading))
        names: list[str] = []
        for line in lines[start + 1 :]:
            if not line.startswith("  "):
                break
            names.append(line.strip())
        return names


class PassRunTest(PassCliCase):
    """A run records its pass, hands the album tool the option, and keeps the passes apart."""

    def test_a_refetch_run_passes_the_option_and_records_its_pass(self) -> None:
        self.main("--mode", "run", _OPTION, "--limit", "1")
        self.assertIn(_OPTION, self.fake.calls[0])
        self.assertEqual([e.pass_ for e in self.entries()], [_REFETCH])

    def test_a_normal_run_records_the_normal_pass_without_the_option(self) -> None:
        self.main("--mode", "run", "--limit", "1")
        self.assertNotIn(_OPTION, self.fake.calls[0])
        self.assertEqual([e.pass_ for e in self.entries()], [_NORMAL])

    def test_albums_a_normal_run_did_are_visited_once_by_the_refetch_pass(self) -> None:
        self.main("--mode", "run")
        self.main("--mode", "run", _OPTION)
        self.assertEqual(self.fake.albums()[3:], ["A1", "A2", "A3"])
        again = self.main("--mode", "run", _OPTION)
        self.assertEqual(len(self.fake.calls), 6)
        self.assertIn("already done: 3\n", again.stdout)

    def test_albums_the_refetch_pass_did_are_visited_by_a_normal_run(self) -> None:
        self.main("--mode", "run", _OPTION)
        self.main("--mode", "run")
        self.assertEqual(self.fake.albums()[3:], ["A1", "A2", "A3"])

    def test_a_restart_of_one_pass_leaves_the_other_done(self) -> None:
        self.main("--mode", "run")
        self.main("--mode", "run", _OPTION)
        self.main("--mode", "run", _OPTION, "--restart")
        self.assertEqual(len(self.fake.calls), 9)
        normal = self.main("--mode", "run")
        self.assertEqual(len(self.fake.calls), 9)
        self.assertIn("already done: 3\n", normal.stdout)
        marks = [
            r
            for r in sl.read_ledger(self.ledger).records
            if isinstance(r, sl.RestartMark)
        ]
        self.assertEqual([m.pass_ for m in marks], [_REFETCH])

    def test_the_resume_line_repeats_the_option(self) -> None:
        result = self.main("--mode", "run", _OPTION, "--limit", "1")
        resume = result.stdout.split("resume: ")[1].splitlines()[0]
        self.assertIn(_OPTION, resume)
        plain = self.main("--mode", "run", "--limit", "1")
        self.assertNotIn(_OPTION, plain.stdout.split("resume: ")[1])

    def test_the_option_is_refused_with_offline(self) -> None:
        result = self.main("--mode", "run", _OPTION, "--offline")
        self.assertEqual(result.code, 2)
        self.assertIn(_OPTION, result.stderr)
        self.assertEqual(self.fake.calls, [])


class PassSurveyTest(PassCliCase):
    """The survey plans with the given pass's done set, as a run of that pass would."""

    def test_the_survey_with_and_without_the_option_agrees_with_a_run(self) -> None:
        self.fake.scripts["A3"] = sf.Script(
            outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS
        )
        self.main("--mode", "run")
        normal = self.main("--mode", "survey")
        refetch = self.main("--mode", "survey", _OPTION)
        self.assertEqual(refetch.code, 0)
        self.assertEqual(self.listed(normal.stdout, _VISIT_LINE), ["Artist/A3"])
        self.assertEqual(
            self.listed(normal.stdout, _DONE_LINE), ["Artist/A1", "Artist/A2"]
        )
        visit = self.listed(refetch.stdout, _VISIT_LINE)
        self.assertEqual(visit, ["Artist/A1", "Artist/A2", "Artist/A3"])
        self.assertNotIn(_DONE_LINE, refetch.stdout)
        calls = len(self.fake.calls)
        self.main("--mode", "run", _OPTION)
        self.assertEqual(["Artist/" + n for n in self.fake.albums()[calls:]], visit)

    def test_the_survey_after_a_refetch_run_passes_its_albums_over(self) -> None:
        self.main("--mode", "run", _OPTION)
        refetch = self.main("--mode", "survey", _OPTION)
        self.assertEqual(len(self.listed(refetch.stdout, _DONE_LINE)), 3)
        normal = self.main("--mode", "survey")
        self.assertEqual(len(self.listed(normal.stdout, _VISIT_LINE)), 3)
        self.assertIn(f"--mode run {_OPTION} --restart", refetch.stdout)
