"""The .lrc this tool writes: its label, its stamps, and the write beside a track."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path

import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import MACHINE_BY_LINE, Label  # noqa: E402 - follows the bootstrap

LABEL = Label("0.0.0", "1.1.1", "2.2.2")
LINES = ("A gull on the railing", "[Chorus]")


class LrcTest(sy_cases.IsolatedCase):
    def test_the_header_names_each_version_then_the_by_line(self) -> None:
        head = sy_lrc.lrc_text(LABEL, LINES, (1.0, 4.25)).splitlines()[:2]
        self.assertEqual(
            head,
            [
                "[re:lyricsmith sync 0.0.0; demucs 1.1.1 htdemucs; "
                "torchaudio 2.2.2 WAV2VEC2_ASR_BASE_960H]",
                MACHINE_BY_LINE,
            ],
        )

    def test_it_reads_back_as_the_lines_at_their_starts(self) -> None:
        lyrics = lm.parse_lrc(sy_lrc.lrc_text(LABEL, LINES, (1.0, 4.25)))
        expected = (lm.SyncedLine(1000, LINES[0]), lm.SyncedLine(4250, LINES[1]))
        self.assertEqual(lyrics, lm.SyncedLyrics(expected))

    def test_stamp_is_the_lrc_stamp(self) -> None:
        self.assertEqual(sy_lrc.stamp(61.2346), "[01:01.235]")

    def test_write_lrc_replaces_the_file_beside_the_track(self) -> None:
        folder = self.tmp / "album"
        folder.mkdir()
        audio = folder / "01 A.mp3"
        audio.with_suffix(".lrc").write_text("old", encoding="utf-8", newline="\n")
        sy_lrc.write_lrc(audio, "new\n")
        self.assertEqual(audio.with_suffix(".lrc").read_text(encoding="utf-8"), "new\n")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["01 A.lrc"])
