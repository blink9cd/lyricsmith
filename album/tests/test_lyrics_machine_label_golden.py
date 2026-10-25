"""The two tools agree on a real transcribe output: album reads its label as machine-made.

The transcribe tool's bundled sample is a file it wrote, byte for byte, so it is the
golden input for album's header check. The two tools draw the header's end with their
own stamp rules, which differ on input transcribe never writes; this test pins the
agreement on the files it does write.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar as lsc  # noqa: E402 - import follows the sys.path bootstrap above

#: The transcribe tool's sample .lrc, found from this file: tests/ -> album/ -> project.
_TRANSCRIBE_SAMPLE = _HERE.parent.parent / "transcribe" / "samples" / "jfk.lrc"


class TranscribeSampleLabelTest(fixtures.IsolatedCase):
    """album's machine_label on the .lrc the transcribe tool ships as its sample."""

    def test_the_transcribe_sample_is_a_machine_transcript(self) -> None:
        text = _TRANSCRIBE_SAMPLE.read_text(encoding="utf-8")
        self.assertIs(lsc.machine_label(text), lm.Source.MACHINE_TRANSCRIPT)
