"""The unlike-backup row of a track whose old lyrics were in an untimed sidecar alone.

When the backup holds no worded lyrics frame, the check compares the track's lyrics with
the untimed sidecar the backup copied instead: its ``.txt``, else a ``.lrc`` with no time
stamp. A refetch replacing such a track therefore shows how unlike its old lyrics the
answer is, a normal run served from its ``.txt`` passes, and a backed-up copy that cannot
be read is a SKIP naming why. A fresh run still declines nothing for want of frames, and
``--mode check`` judges as the run did. Every lyric is invented and every provider faked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_refetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    WRONG_TXT,
    RefetchCase,
)

#: The unlike-backup FLAG with its measured similarity, as the summary prints it.
_UNLIKE = re.compile(r"FLAG: unlike backup \(similarity [0-9.]+ < 0\.6\)")
#: More characters than any sidecar may hold, in bytes a backup still copies.
_OVER_CAP = "la " * (lr.MAX_LYRICS_CHARS // 3 + 1)
#: A .txt whose bytes are not UTF-8.
_UNDECODABLE = b"caf\xe9 by the quay\n"


class SidecarBackupCase(RefetchCase):
    """The keeper answered with lyrics unlike its old ones."""

    def setUp(self) -> None:
        """The keeper's title answered untimed, a different song from WRONG_TXT."""
        super().setUp()
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})

    def unlike(self, row: str) -> str:
        """The unlike-backup FLAG in *row*, failing when there is none."""
        found = _UNLIKE.search(row)
        assert found is not None, row
        return found.group(0)


class RefetchAgainstSidecarTest(SidecarBackupCase):
    """A refetch of a sidecar-only track is judged against the backed-up sidecar."""

    def test_a_txt_alone_flags_the_answer_with_its_similarity(self) -> None:
        self.keeper(txt=WRONG_TXT)
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.unlike(row)

    def test_an_untimed_lrc_alone_flags_the_answer(self) -> None:
        self.keeper(lrc=WRONG_TXT)
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.unlike(row)

    def test_check_mode_agrees_with_the_run(self) -> None:
        self.keeper(txt=WRONG_TXT)
        row, _keeper, _mill = self.refetch()
        result = self.run_album("--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        (again,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertEqual(self.unlike(again), self.unlike(row))

    def test_an_undecodable_txt_is_a_skip_naming_why(self) -> None:
        paths = self.keeper()
        paths[0].with_suffix(".txt").write_bytes(_UNDECODABLE)
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertIn(
            "SKIP: unlike backup (backed-up 01 Keeper.txt is not UTF-8: ", row
        )
        self.assertNotIn("FLAG", row)
        assert keeper.check is not None
        self.assertEqual(keeper.check.verdict, lr.CheckVerdict.SKIP)

    def test_an_over_cap_txt_is_a_skip_naming_why(self) -> None:
        self.keeper(txt=_OVER_CAP)
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertIn(
            "SKIP: unlike backup (backed-up 01 Keeper.txt is too large: over "
            f"{lr.MAX_LYRICS_CHARS} characters)",
            row,
        )
        self.assertNotIn("FLAG", row)


class OtherRunsAgainstSidecarTest(SidecarBackupCase):
    """A normal run and a fresh run of a sidecar-only track."""

    def test_a_normal_run_served_from_its_txt_passes(self) -> None:
        self.answer_both(synced={}, plain={})
        self.keeper(txt=WRONG_TXT)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        keeper, _mill = self.afters()
        assert keeper is not None and keeper.check is not None
        self.assertEqual(keeper.source, lm.Source.SIDECAR)
        self.assertEqual(keeper.check.verdict, lr.CheckVerdict.PASS, row)

    def test_a_fresh_run_still_accepts_the_answer_then_flags_it(self) -> None:
        # The fresh run's decline compares with the backed-up frames alone, as ever;
        # the check of the written track then compares with the backed-up .txt.
        paths = self.keeper(txt=WRONG_TXT)
        result = self.run_album("--fresh")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.unlike(row)
        text = paths[0].with_suffix(".txt").read_text(encoding="utf-8")
        self.assertEqual(text, fixtures.LIGHTHOUSE_PLAIN)
