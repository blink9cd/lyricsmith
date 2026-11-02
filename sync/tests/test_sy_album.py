"""The sync tool's .lrc as album reads it: labeled machine timed, its lines intact.

The sync tool reads the label only through album's `machine_label`: it plans a re-time
from the label `plan_sidecars` reports.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path

import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as lsc  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import text_lines  # noqa: E402 - follows the bootstrap above
from sy_types import MACHINE_BY_LINE, Label  # noqa: E402 - follows the bootstrap

TEXT = sy_lrc.lrc_text(Label("0", "0", "0"), ("A gull", "[Chorus]"), (1.0, 2.0))


class AlbumAgreesTest(sy_cases.IsolatedCase):
    """The file the sync tool writes, as album reads it."""

    def test_album_labels_it_machine_timed(self) -> None:
        self.assertIs(lsc.machine_label(TEXT), lm.Source.MACHINE_TIMED)

    def test_both_tools_compare_the_same_by_line(self) -> None:
        self.assertIn(MACHINE_BY_LINE, lsc.MACHINE_LABELS)

    def test_an_edited_by_line_or_one_after_a_timed_line_is_no_label(self) -> None:
        self.assertIsNone(lsc.machine_label(TEXT.replace("timed", "tuned")))
        padded = TEXT.replace(MACHINE_BY_LINE, f"  {MACHINE_BY_LINE} ")
        self.assertIs(lsc.machine_label(padded), lm.Source.MACHINE_TIMED)
        late = f"[00:01.000]x\n{MACHINE_BY_LINE}\n[00:02.000]y\n"
        self.assertIsNone(lsc.machine_label(late))

    def test_album_embeds_the_original_text_blank_lines_and_all(self) -> None:
        original = "\n  A gull  \nwheels\n\n \n[Chorus]\non the railing\n\n"
        lines = text_lines(original)
        # Each blank line and the header tie with the next sung line, as timed.
        starts = (0.0, 1.0, 3.0, 3.0, 3.0, 4.0)
        lyrics = lm.parse_lrc(sy_lrc.lrc_text(Label("0", "0", "0"), lines, starts))
        expected = "A gull\nwheels\n\n\n[Chorus]\non the railing"
        self.assertEqual(lyrics.plain_text(), expected)
