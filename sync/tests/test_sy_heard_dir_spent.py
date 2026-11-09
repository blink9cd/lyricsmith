"""When a --heard-dir record is spent: its track gone, or timed with words not the record's.

A provider's timed text found not sung keeps its record, so a plain sync run between the
hearing and the fix never erases it; once the text is fixed and timed, the record goes.
A text this tool timed, heard sung to be timed, releases its record as before.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_hear_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import lyrics_heard_store as hs  # noqa: E402 - import follows the sys.path bootstrap
import sy_heard_dir as hd  # noqa: E402 - import follows the sys.path bootstrap above
import sy_kind  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the bootstrap
from lyrics_hearing_log import HeardKind  # noqa: E402 - follows the bootstrap
from sy_cases import OTHER_SONG  # noqa: E402 - import follows the sys.path bootstrap
from sy_hear_cases import LINES, provider_lrc  # noqa: E402 - follows the bootstrap
from sy_types import Label  # noqa: E402 - import follows the sys.path bootstrap above

fixtures = sy_hear_cases.fixtures
#: The text a fix put in the wrong one's place.
FIXED = ("an invented replacement verse", "sung where the other one was not")


class SpentTest(sy_hear_cases.HearCliCase):
    def track(self, lrc: str) -> Path:
        """One invented track under the library holding *lrc*, and a record of LINES."""
        (audio,) = self.album(fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=lrc))
        record = hs.HeardRecord(audio, text_fingerprint(LINES), OTHER_SONG)
        hs.write_record(self.heard, record)
        return audio

    def clean(self) -> hd.Cleaned:
        """The store cleaned over the library."""
        listing, _ = hd.read_store(self.heard)
        return hd.clean(self.heard, listing, self.library)

    def test_a_providers_text_heard_not_sung_keeps_its_record(self) -> None:
        audio = self.track(provider_lrc(LINES))
        cleaned = self.clean()
        self.assertEqual([r.path for r in cleaned.listing.records], [audio])
        self.assertEqual(cleaned.lines, ())
        self.assertIsNotNone(hs.read_record(self.heard, audio))

    def test_a_providers_text_fixed_and_timed_releases_its_record(self) -> None:
        audio = self.track(provider_lrc(FIXED))
        cleaned = self.clean()
        self.assertEqual(cleaned.listing.records, ())
        self.assertIsNone(hs.read_record(self.heard, audio))

    def test_a_change_of_case_or_punctuation_is_no_fix(self) -> None:
        shouted = tuple(f"{line.upper()}!" for line in LINES)
        audio = self.track(provider_lrc(shouted))
        self.assertEqual([r.path for r in self.clean().listing.records], [audio])

    def test_a_text_this_tool_timed_releases_its_record_whatever_its_words(
        self,
    ) -> None:
        ours = sy_lrc.lrc_text(Label("0", "0", "0"), LINES, (1.0, 2.0))
        audio = self.track(ours)
        self.assertEqual(self.clean().listing.records, ())
        self.assertIsNone(hs.read_record(self.heard, audio))

    def test_a_plain_run_keeps_the_record_of_a_providers_text_not_sung(self) -> None:
        audio = self.track(provider_lrc(LINES))
        code, out, _ = self.main(
            "--path",
            str(self.library),
            "--by-hand-list",
            str(self.listed),
            "--heard-dir",
            str(self.heard),
        )
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"skip: already timed +Artist/Album/01 A\.mp3")
        self.assertNotIn("removed", out)
        self.assertEqual(self.child.requests, [])
        self.assertIsNotNone(hs.read_record(self.heard, audio))

    def test_a_text_unreadable_or_untimed_on_its_second_read_releases_nothing(
        self,
    ) -> None:
        # The text is read again after the timed check, so one changed or made
        # unreadable between the two reads keeps its record rather than losing it.
        audio = self.track(provider_lrc(FIXED))
        fingerprint = text_fingerprint(LINES)
        untimed = sy_kind.KindedText(HeardKind.UNTIMED, FIXED)
        cases = (
            mock.Mock(side_effect=OSError("gone")),
            mock.Mock(return_value=None),
            mock.Mock(return_value=untimed),
        )
        for seam in cases:
            with self.subTest(seam=seam):
                with mock.patch.object(hd.sy_kind, "text_kind", seam):
                    self.assertFalse(hd._is_released(audio, fingerprint))
