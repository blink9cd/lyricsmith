"""End to end: the real album tool, offline, swept over a small invented library.

The library is the album tool's own sample album, built by its sample builder: six silent
MP3s with an invented artist and titles, one beside an invented timed sidecar, one beside
a ``.txt`` holding only a placeholder and, of the two tagged stand-up, one beside an
invented untimed sidecar. The sweep runs the real album tool as a child process with
``--offline``, so no provider is asked, then the undo command its report prints is run
and must put the album back byte for byte. An offline visit is recorded but never done,
so a second sweep visits the album again.
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_categories import (  # noqa: E402 - follows the sys.path bootstrap
    RemovedReason,
    WrittenCategory,
)
from sweep_tally import Counts  # noqa: E402 - import follows the sys.path bootstrap above

#: How long building the album, or one undo, may take.
_CHILD_TIMEOUT_S = 120
_BUILDER = sweep.ALBUM_ENTRY.parent / "samples" / "build_sample_album.py"


def _snapshot(folder: Path) -> dict[str, bytes]:
    """Every file under *folder* outside the run folder, by relative path, with bytes."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and ".lyricsmith" not in path.parts
    }


class EndToEndTest(sf.IsolatedCase):
    """A survey, a run and its undo, with nothing faked."""

    def setUp(self) -> None:
        """Build the sample album under its artist's folder in this test's library."""
        super().setUp()
        self.album = self.library / "The Invented Quartet" / "Sample Album"
        subprocess.run(  # reliability: ignore[RL005] DEVNULL sinks, no pipe to hold
            [sys.executable, str(_BUILDER), "--out", str(self.album)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CHILD_TIMEOUT_S,
        )

    def _main(self, *args: str) -> tuple[int, str]:
        # The child inherits this environment: keep the host's ffmpeg out of it.
        self.enterContext(mock.patch.dict(os.environ, sf.env_without_ffmpeg(self.tmp)))
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = sweep.main([*args, "--library", str(self.library)])
        return code, stdout.getvalue()

    def test_a_sweep_runs_the_album_tool_records_it_and_its_undo_works(self) -> None:
        before = _snapshot(self.album)
        code, survey = self._main("--mode", "survey")
        self.assertEqual(code, 0)
        self.assertIn("albums a run would visit: 1", survey)

        listed = self.tmp / "changed.txt"
        code, report = self._main(
            "--mode", "run", "--offline", "--changed-list", str(listed)
        )
        self.assertEqual(code, 0)
        # The two tracks the run embedded lyrics in, each from its own sidecar, as the
        # real album tool wrote them.
        timed, untimed = sorted(map(Path, listed.read_text("utf-8").splitlines()))
        self.assertEqual({timed.parent, untimed.parent}, {self.album})
        self.assertTrue(timed.with_suffix(".lrc").is_file())
        self.assertTrue(untimed.with_suffix(".txt").is_file())
        path = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        (entry,) = sl.read_ledger(path).records
        assert isinstance(entry, sl.LedgerEntry)
        # Offline, no provider was asked: the album is recorded, never done.
        self.assertIs(entry.outcome, sl.Outcome.OFFLINE)
        assert entry.counts is not None
        # The stand-up track holding nothing is no lyrics expected, apart from the two
        # not found and the placeholder removed, which is counted apart, by its
        # reason; the two written embedded their own sidecars, and the one served
        # from its sidecar is flagged.
        self.assertEqual(
            entry.counts,
            Counts(
                written=2,
                not_found=2,
                no_lyrics_expected=1,
                flagged=1,
                removed=1,
                written_by={WrittenCategory.EMBEDDED: 2},
                removed_by={RemovedReason.PLACEHOLDER: 1},
            ),
        )
        self.assertNotEqual(_snapshot(self.album), before)

        undo = next(line for line in report.splitlines() if "--mode undo" in line)
        argv = shlex.split(undo.split("undo: ", 1)[1])
        argv[0] = str(sweep.ALBUM_ENTRY)
        subprocess.run(  # reliability: ignore[RL005] DEVNULL sinks, no pipe to hold
            [sys.executable, *argv],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CHILD_TIMEOUT_S,
        )
        self.assertEqual(_snapshot(self.album), before)

        # An offline visit is never done, so a second sweep visits the album again.
        code, report = self._main("--mode", "run", "--offline")
        self.assertEqual(code, 0)
        self.assertIn("already done: 0", report)
        self.assertEqual(len(sl.read_ledger(path).records), 2)
