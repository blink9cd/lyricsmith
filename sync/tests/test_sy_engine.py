"""Running one track's child process: success, timeout, crash, an unreadable reply."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_engine  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from sy_cases import fake_child  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above

REQUEST = rq.Request(Path("a.mp3"), (2, 3), Path("models"))


class EngineTest(sy_cases.IsolatedCase):
    def test_a_good_reply_comes_back(self) -> None:
        good = rq.Reply(rq.STATUS_OK, 9, 0.02, (Span(0, 1, 0.5), Span(2, 3, 0.5)))
        aligned = sy_engine.align(
            REQUEST, self.tmp, fake_child(rq.dump_reply(good)), 10.0
        )
        self.assertEqual((aligned.reply, aligned.detail), (good, ()))

    def test_the_request_is_written_where_the_child_reads_it(self) -> None:
        sy_engine.align(REQUEST, self.tmp, fake_child(None, code=1), 10.0)
        written = (self.tmp / sy_engine.REQUEST_NAME).read_text(encoding="utf-8")
        self.assertEqual(rq.load_request(written), REQUEST)

    def test_a_crash_carries_its_exit_and_the_last_error_lines(self) -> None:
        aligned = sy_engine.align(REQUEST, self.tmp, fake_child(None, code=3), 10.0)
        self.assertIsNone(aligned.reply)
        self.assertEqual(aligned.detail[0], "exit 3")
        self.assertIn("last words", aligned.detail)

    def test_a_timeout_and_a_failed_start_are_each_said(self) -> None:
        timeout = subprocess.TimeoutExpired(["x"], 1.0)
        timed_out = sy_engine.align(
            REQUEST, self.tmp, fake_child(None, raises=timeout), 1.0
        )
        self.assertEqual(timed_out.detail, ("timed out",))
        failed = sy_engine.align(
            REQUEST, self.tmp, fake_child(None, raises=OSError("no")), 1.0
        )
        self.assertTrue(failed.detail[0].startswith("could not start"))

    def test_an_unreadable_or_missing_reply_is_said(self) -> None:
        for reply in ("{}", None):
            with self.subTest(reply=reply):
                aligned = sy_engine.align(REQUEST, self.tmp, fake_child(reply), 1.0)
                self.assertTrue(aligned.detail[0].startswith("unreadable reply"))
                (self.tmp / sy_engine.REPLY_NAME).unlink(missing_ok=True)

    def test_the_timeout_is_a_minute_plus_twice_the_length(self) -> None:
        self.assertEqual(sy_engine.child_timeout(100.0), 260.0)
        self.assertEqual(
            sy_engine.child_timeout(None), 60.0 + 2 * sy_engine.UNKNOWN_LENGTH_S
        )

    def test_the_argv_runs_the_child_with_this_interpreter(self) -> None:
        argv = sy_engine.child_argv(self.tmp)
        self.assertEqual((argv[0], Path(argv[1]).name), (sys.executable, "sy_child.py"))

    def test_track_seconds_reads_the_length_or_none(self) -> None:
        track = sy_cases.fixtures.TrackSpec("01 A.mp3", seconds=2.0)
        (audio,) = sy_cases.fixtures.build_album(self.tmp, (track,))
        self.assertAlmostEqual(sy_engine.track_seconds(audio) or 0.0, 2.0, delta=0.2)
        junk = self.tmp / "junk.mp3"
        junk.write_bytes(b"not audio")
        self.assertIsNone(sy_engine.track_seconds(junk))
        self.assertIsNone(sy_engine.track_seconds(self.tmp / "gone.mp3"))
