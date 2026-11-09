"""A track's text and its kind, read from its sidecars by album's own rules."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_kind  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_hearing_log import HeardKind  # noqa: E402 - follows the bootstrap above
from lyrics_sidecar import UNREADABLE  # noqa: E402 - follows the bootstrap above
from sy_types import Label  # noqa: E402 - import follows the sys.path bootstrap above

fixtures = sy_cases.fixtures
LINES = ("A gull on the railing", "", "a kettle in the hold")
#: A provider's timed text: stamped lines and no label.
PROVIDER_LRC = (
    "[00:01.00]A gull on the railing\n[00:02.00]\n[00:03.00]a kettle in the hold\n"
)
#: This tool's own timed text, labeled.
MACHINE_LRC = sy_lrc.lrc_text(Label("0", "0", "0"), LINES, (1.0, 2.0, 3.0))


class KindTest(sy_cases.IsolatedCase):
    def kind(
        self, *, lrc: str | None = None, txt: str | None = None, album: str = "Album"
    ) -> sy_kind.KindedText | None:
        """The kinded text of one invented track, in *album*, holding the sidecars given."""
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=lrc, txt=txt)
        (audio,) = fixtures.build_album(self.tmp / album, (track,))
        return sy_kind.text_kind(audio)

    def test_a_timed_lrc_with_no_label_is_a_providers(self) -> None:
        self.assertEqual(
            self.kind(lrc=PROVIDER_LRC),
            sy_kind.KindedText(HeardKind.PROVIDER_TIMED, LINES),
        )

    def test_a_timed_lrc_this_tool_labeled_is_machine_timed(self) -> None:
        self.assertEqual(
            self.kind(lrc=MACHINE_LRC),
            sy_kind.KindedText(HeardKind.MACHINE_TIMED, LINES),
        )

    def test_an_untimed_text_is_untimed_from_a_txt_or_an_untimed_lrc(self) -> None:
        untimed = sy_kind.KindedText(HeardKind.UNTIMED, LINES)
        self.assertEqual(self.kind(txt="\n".join(LINES), album="Txt"), untimed)
        self.assertEqual(self.kind(lrc="\n".join(LINES), album="Lrc"), untimed)

    def test_a_track_with_no_sidecar_text_has_no_kind(self) -> None:
        self.assertIsNone(self.kind())

    def test_a_sidecar_that_cannot_be_read_raises(self) -> None:
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0)
        (audio,) = fixtures.build_album(self.tmp / "Album", (track,))
        audio.with_suffix(".txt").write_bytes(b"\xc3\x28 not UTF-8\n")
        with self.assertRaises(UNREADABLE):
            sy_kind.text_kind(audio)
