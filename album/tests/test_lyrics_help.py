"""The --help epilog: every line within its width, and its exit table as full as the doc's.

The epilog is laid out by hand (`help_formatter.RawNoneHidingFormatter` keeps its line
breaks), so nothing rewraps a line that outgrows the rest; and its exit codes restate
the companion doc's table, so a cause the doc names and the epilog leaves out is a
reader of --help told less than the doc says.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above

#: The widest line the epilog may hold: the source's own line bar, since each epilog
#: line is written as one line of the module.
EPILOG_WIDTH = 88
#: An exit-table row's first line: two spaces, the code, then its text.
_EXIT_ROW = re.compile(r"^  (\d+) ", flags=re.MULTILINE)


def exit_rows() -> dict[str, str]:
    """Each exit code the epilog lists, to its text joined onto one line."""
    table = lyrics_help.EPILOG.split("Exit codes:", 1)[1]
    parts = _EXIT_ROW.split(table)[1:]
    return {code: " ".join(text.split()) for code, text in zip(parts[::2], parts[1::2])}


class EpilogWidthTest(fixtures.IsolatedCase):
    """No line of the epilog is wider than `EPILOG_WIDTH`."""

    def test_every_line_fits(self) -> None:
        wide = [
            line for line in lyrics_help.EPILOG.splitlines() if len(line) > EPILOG_WIDTH
        ]
        self.assertEqual(wide, [])


class ExitTableTest(fixtures.IsolatedCase):
    """The exit table names the causes the companion doc's table names."""

    def test_a_paste_s_own_codes_are_listed(self) -> None:
        rows = exit_rows()
        self.assertIn("paste only: the track refused by its guards", rows["4"])
        self.assertIn("paste only: the run was made", rows["5"])

    def test_a_compare_error_row_is_exit_1(self) -> None:
        self.assertIn("a compare with an ERROR row", exit_rows()["1"])

    def test_an_accept_stopped_after_its_rename_is_said_under_130(self) -> None:
        text = exit_rows()["130"]
        self.assertIn("an accept stopped after its rename", text)
        self.assertIn(".lyricsmith.accepting", text)
