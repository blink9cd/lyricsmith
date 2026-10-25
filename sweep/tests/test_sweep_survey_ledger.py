"""The survey reads the ledger, read-only, and lists the albums a run passes over as done.

`main` runs with a fake album tool, a recording sleep and a fixed clock, with stdout
and stderr captured, so nothing here waits, reaches a provider, or touches a real library
or the real ledger.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import album  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above

#: The line that opens the survey's list of albums a run passes over as done.
_DONE_LINE = "already done, passed over by a run:"
_VISIT_LINE = "albums a run would visit:"
# os.geteuid exists only off Windows, so it is looked up; root can read a mode-000
# file, so the unreadable-ledger test skips under it.
_GETEUID = getattr(os, "geteuid", None)
_IS_ROOT = _GETEUID is not None and _GETEUID() == 0


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class SurveyLedgerCase(sf.IsolatedCase):
    """Three needy albums, A1 to A3, and this library's ledger path."""

    def setUp(self) -> None:
        """Build the albums; the ledger is not made."""
        super().setUp()
        for name in ("A1", "A2", "A3"):
            sf.needy_album(self.library, "Artist", name)
        self.fake = sf.FakeAlbumTool()
        self.ledger = sl.ledger_path(self.library, os.environ)

    def main(self, *args: str) -> Result:
        """Run `main` with *args* and ``--library``; return the code and the output."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sweep.main(argv, wiring=wiring)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def record_done(self, album: str, lang: str = sl.DEFAULT_LANG) -> None:
        """Write a done entry for *album* in *lang* to this library's ledger."""
        entry = sl.LedgerEntry(album, None, 0, sl.Outcome.DONE, "t", lang=lang)
        sl.append_record(self.ledger, entry)

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


class SurveyDoneTest(SurveyLedgerCase):
    """An album the ledger holds as done is passed over, as a run passes it over."""

    def test_an_album_done_in_the_language_is_listed_apart(self) -> None:
        self.record_done("Artist/A2")
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 0)
        self.assertIn(f"{_VISIT_LINE} 2\n", result.stdout)
        self.assertEqual(
            self.listed(result.stdout, _VISIT_LINE), ["Artist/A1", "Artist/A3"]
        )
        self.assertIn(f"{_DONE_LINE} 1\n", result.stdout)
        self.assertEqual(self.listed(result.stdout, _DONE_LINE), ["Artist/A2"])
        self.assertIn("--mode run --restart", result.stdout)
        # The table still describes the tracks: A2 keeps its row.
        self.assertRegex(result.stdout, r"total \(3 albums\)\s+3\s+3\s+3\s+0")

    def test_an_album_done_in_the_given_language_follows_lang(self) -> None:
        self.record_done("Artist/A2", lang="deu")
        result = self.main("--mode", "survey", "--lang", "deu")
        self.assertEqual(self.listed(result.stdout, _DONE_LINE), ["Artist/A2"])

    def test_an_album_done_in_another_language_only_is_still_visited(self) -> None:
        self.record_done("Artist/A2", lang="deu")
        result = self.main("--mode", "survey")
        self.assertEqual(
            self.listed(result.stdout, _VISIT_LINE),
            ["Artist/A1", "Artist/A2", "Artist/A3"],
        )
        self.assertNotIn(_DONE_LINE, result.stdout)

    def test_a_restart_mark_forgets_what_was_done(self) -> None:
        self.record_done("Artist/A2")
        sl.append_record(self.ledger, sl.RestartMark("t"))
        result = self.main("--mode", "survey")
        self.assertEqual(len(self.listed(result.stdout, _VISIT_LINE)), 3)
        self.assertNotIn(_DONE_LINE, result.stdout)

    def test_the_survey_does_not_change_the_ledger(self) -> None:
        self.record_done("Artist/A2")
        before = self.ledger.read_bytes()
        self.main("--mode", "survey")
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(
            sorted(p.name for p in self.ledger.parent.iterdir()), [self.ledger.name]
        )


class SurveyLedgerAbsentOrBadTest(SurveyLedgerCase):
    """No ledger means nothing done; one that cannot be read exits 2."""

    def test_with_no_ledger_nothing_is_done_and_nothing_is_made(self) -> None:
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 0)
        self.assertEqual(len(self.listed(result.stdout, _VISIT_LINE)), 3)
        self.assertNotIn(_DONE_LINE, result.stdout)
        self.assertFalse(self.state.exists())

    @unittest.skipIf(_IS_ROOT, "root can read a mode-000 file")
    def test_a_ledger_that_cannot_be_read_exits_two(self) -> None:
        self.record_done("Artist/A2")
        self.ledger.chmod(0)
        self.addCleanup(self.ledger.chmod, 0o644)
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 2)
        self.assertIn("the ledger cannot be read", result.stderr)
        self.assertIn(str(self.ledger), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_malformed_ledger_line_is_reported_and_skipped(self) -> None:
        self.record_done("Artist/A2")
        bad_line = b"this is not a ledger line\n"
        self.ledger.write_bytes(self.ledger.read_bytes() + bad_line)
        result = self.main("--mode", "survey")
        self.assertEqual(result.code, 0)
        self.assertIn("ledger line 2", result.stderr)
        self.assertEqual(self.listed(result.stdout, _DONE_LINE), ["Artist/A2"])


class RunAgreesWithSurveyTest(SurveyLedgerCase):
    """A run visits exactly the albums the survey before it said it would."""

    def test_a_resumed_run_visits_the_surveys_list(self) -> None:
        self.fake.scripts["A3"] = sf.Script(
            outcomes=sf.REFUSED, exit_code=album.EXIT_TRACK_ERRORS
        )
        self.main("--mode", "run")
        survey = self.main("--mode", "survey")
        visit = self.listed(survey.stdout, _VISIT_LINE)
        done = self.listed(survey.stdout, _DONE_LINE)
        self.assertEqual((visit, done), (["Artist/A3"], ["Artist/A1", "Artist/A2"]))
        calls = len(self.fake.calls)
        run = self.main("--mode", "run")
        self.assertEqual(["Artist/" + n for n in self.fake.albums()[calls:]], visit)
        self.assertIn("1 of 3 albums to visit", run.stderr)
        self.assertIn(f"already done: {len(done)}\n", run.stdout)

    def test_a_restart_counts_done_albums_among_those_to_visit(self) -> None:
        self.main("--mode", "run")
        run = self.main("--mode", "run", "--restart")
        self.assertIn("3 of 3 albums to visit", run.stderr)
        self.assertIn("already done: 0\n", run.stdout)
