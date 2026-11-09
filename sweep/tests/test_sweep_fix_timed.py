"""The fix pass visits an all-synced album holding a record; the run pass still does not.

A provider-timed text `sync` heard not sung leaves its record on an album whose every
track is synced, and `album --mode fix-text` now fixes such a text. So the fix pass
visits that album, while a run, which carries no record, passes it over as before.
Every transcript and lyric here is invented for these tests.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fix_text  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard_store import HeardRecord, write_record  # noqa: E402 - bootstrap
from sweep_survey import AlbumSurvey, survey_album  # noqa: E402 - bootstrap

#: An invented fingerprint and transcript: the store checks only their form.
_FINGERPRINT, _HEARD = "ab" * 32, "the kettle sings of harbor lights"


class NeedsVisitTest(sf.IsolatedCase):
    """An all-synced survey needs a visit only when it names a timed record's track."""

    def test_an_all_synced_survey_needs_a_visit_only_with_a_timed_record(self) -> None:
        album = self.library / "Artist" / "A0"
        self.assertFalse(AlbumSurvey(album, synced=1).needs_visit)
        digest = AlbumSurvey(album, synced=1, records_digest="0" * 64)
        self.assertFalse(digest.needs_visit)
        timed = AlbumSurvey(album, synced=1, timed_records=("01 Done.mp3",))
        self.assertTrue(timed.needs_visit)

    def test_an_unreadable_sidecar_is_no_provider_timed_text(self) -> None:
        album = self.library / "Artist" / "A0"
        track = sf.add_track(album, "01 Bad.mp3")
        track.with_suffix(".lrc").write_bytes(b"\xff\xfe not text")
        self.assertFalse(sweep_fix_text.provider_timed(track, album))


class PassesTest(sf.IsolatedCase):
    """The run pass passes over an all-synced album; the fix pass visits it."""

    def setUp(self) -> None:
        """An all-synced album, its one track heard not sung, and a fake album tool."""
        super().setUp()
        self.done = sf.synced_album(self.library, "Artist", "A0")
        self.heard = self.tmp / "heard"
        write_record(
            self.heard, HeardRecord(self.done / "01 Done.mp3", _FINGERPRINT, _HEARD)
        )
        self.fake = sf.FakeAlbumTool()

    def main(self, *args: str) -> int:
        """Run `main` with *args* and ``--library``; return its exit code."""
        argv = [*args, "--library", str(self.library)]
        wiring = sweep.Wiring(self.fake, sf.FakeSleep(), sf.fixed_now)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return sweep.main(argv, wiring=wiring)

    def test_the_run_pass_still_passes_the_album_over(self) -> None:
        self.assertFalse(survey_album(self.done, lang="eng").needs_visit)
        self.assertEqual(self.main("--mode", "run"), 0)
        self.assertEqual(self.fake.calls, [])

    def test_the_fix_pass_visits_it(self) -> None:
        self.assertEqual(
            self.main("--mode", "fix-text", "--heard-dir", str(self.heard)), 0
        )
        self.assertEqual(self.fake.albums(), ["A0"])
