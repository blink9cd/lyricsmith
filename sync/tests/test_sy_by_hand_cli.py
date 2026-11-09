"""--by-hand-list, black-box: main() with every outside call faked, offline and isolated."""

from __future__ import annotations

import contextlib
import io
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import sy_by_hand as bh  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import TrackResult  # noqa: E402 - bootstrap
from sy_cases import (  # noqa: E402 - bootstrap
    DATA,
    fake_child,
    fake_opener,
    not_sung_reply,
    reply_for,
)
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from test_sy_cli import (  # noqa: E402 - the black-box suite's own stand-ins
    DISPATCHED,
    TINY,
    VERSION,
    RecordingAlbum,
)

fixtures = sy_cases.fixtures
#: Three lines, so one stretched line stands out against the median of the others.
LINES = ("la la", "lo lo", "li li")
#: A reply the confidence floor refuses.
LOW_REPLY = reply_for(LINES, score=0.05)
#: A reply whose first line stretches back over the intro.
STRETCHED_REPLY = reply_for(LINES, widths=(100, 10, 10))


class CountingChild:
    """A fake child that answers *reply* and counts its runs.

    Given *stop_when*, a run raises Ctrl-C's `KeyboardInterrupt` once it holds.
    """

    def __init__(
        self, reply: str, stop_when: Callable[[], bool] = lambda: False
    ) -> None:
        self.runs = 0
        self.child = fake_child(reply)
        self.stop_when = stop_when

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.runs += 1
        if self.stop_when():
            raise KeyboardInterrupt
        return self.child(argv, timeout=timeout)


class ByHandCliTest(sy_cases.IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.object(sy_models, "MANIFEST", TINY))
        self.models = self.tmp / "models"
        self.models.mkdir()
        for model in TINY:
            (self.models / model.name).write_bytes(DATA)
        self.whisper = sy_cases.invented_whisper_models(self, self.tmp / "whisper")
        self.album = self.tmp / "Album"
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="\n".join(LINES))
        (self.audio,) = fixtures.build_album(self.album, (track,))
        self.listed = self.tmp / "state" / "by-hand.tsv"
        self.child = CountingChild(LOW_REPLY)

    def main(self, *argv: str, reply: str | None = None) -> tuple[int, str, str]:
        """main() on *argv*, the list and the model folder; its code and output."""
        if reply is not None:
            self.child = CountingChild(reply)
        seams = sy_modes.Seams(
            run=self.child,
            embed=RecordingAlbum(),
            album_entry=self.tmp / "album.py",
            find_spec=lambda name: object(),
            which=lambda name: f"/usr/bin/{name}",
            version=lambda name: VERSION,
        )
        full = [
            *argv,
            "--by-hand-list",
            str(self.listed),
            "--model-dir",
            str(self.models),
            "--whisper-model-dir",
            str(self.whisper),
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(
                full, seams=seams, opener=fake_opener(DATA), prog=DISPATCHED
            )
        return code, out.getvalue(), err.getvalue()

    def test_a_low_confidence_track_is_listed_then_skipped_untimed(self) -> None:
        code, out, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 0)
        self.assertEqual(
            bh.read_by_hand_list(self.listed),
            (bh.Row(str(self.audio), bh.REASON_LOW, "0.05"),),
        )
        self.assertIn(
            f"listed for timing by hand: 1 row(s) added to {self.listed}", out
        )
        code, out, _ = self.main("--path", str(self.album), reply=LOW_REPLY)
        self.assertEqual((code, self.child.runs), (0, 0))
        self.assertRegex(out, r"skip: listed for timing by hand +01 A\.mp3")
        self.assertNotIn("listed for timing by hand:", out)

    def test_a_text_not_sung_is_listed_then_skipped_untimed(self) -> None:
        code, out, _ = self.main("--path", str(self.album), reply=not_sung_reply())
        self.assertEqual(code, 0)
        self.assertFalse(self.audio.with_suffix(".lrc").exists())
        self.assertEqual(
            bh.read_by_hand_list(self.listed),
            (bh.Row(str(self.audio), bh.REASON_NOT_SUNG, "0.04"),),
        )
        self.assertRegex(out, r"not sung: left untimed +01 A\.mp3")
        code, out, _ = self.main("--path", str(self.album), reply=not_sung_reply())
        self.assertEqual((code, self.child.runs), (0, 0))
        self.assertRegex(out, r"skip: listed as not sung +01 A\.mp3")
        self.assertNotIn("listed for timing by hand", out)

    def test_a_text_too_little_heard_is_timed_and_listed_as_a_note(self) -> None:
        reply = reply_for(LINES, heard=Heard(12, 0.0))
        code, out, _ = self.main("--path", str(self.album), reply=reply)
        self.assertEqual(code, 0)
        self.assertTrue(self.audio.with_suffix(".lrc").exists())
        self.assertEqual(
            bh.read_by_hand_list(self.listed),
            (bh.Row(str(self.audio), bh.REASON_LITTLE, "12 pairs"),),
        )
        self.assertIn("only 12 word pairs heard", out)

    def test_overwrite_does_not_override_the_list(self) -> None:
        self.main("--path", str(self.album), reply=STRETCHED_REPLY)
        self.assertTrue(self.audio.with_suffix(".lrc").exists())
        self.listed.write_bytes(
            bh.format_rows((bh.Row(str(self.audio), bh.REASON_LOW, "0.05"),))
        )
        code, out, _ = self.main(
            "--path", str(self.album), "--overwrite", reply=LOW_REPLY
        )
        self.assertEqual((code, self.child.runs), (0, 0))
        self.assertRegex(out, r"skip: listed for timing by hand +01 A\.mp3")

    def test_a_flagged_first_line_is_listed_as_a_check(self) -> None:
        code, out, _ = self.main("--path", str(self.album), reply=STRETCHED_REPLY)
        self.assertEqual(code, 0)
        self.assertTrue(self.audio.with_suffix(".lrc").exists())
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual((row.path, row.reason), (str(self.audio), bh.REASON_FIRST))
        self.assertIn(f"{row.detail}", out)
        _, out, _ = self.main("--path", str(self.album))
        self.assertRegex(out, r"skip: already timed +01 A\.mp3")

    def test_a_dry_run_shows_the_skip_and_writes_nothing(self) -> None:
        self.listed.parent.mkdir()
        rows = bh.format_rows((bh.Row(str(self.audio), bh.REASON_LOW, "0.05"),))
        self.listed.write_bytes(rows)
        code, out, _ = self.main("--path", str(self.album), "--dry-run")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"skip: listed for timing by hand +01 A\.mp3")
        self.listed.unlink()
        self.main("--path", str(self.album), "--dry-run")
        self.assertFalse(self.listed.exists())

    def test_a_broken_list_is_a_setup_error_before_any_track(self) -> None:
        self.listed.parent.mkdir()
        self.listed.write_bytes(b"/x.mp3\tlow\t0.1\n")
        code, _, err = self.main("--path", str(self.album))
        self.assertEqual((code, self.child.runs), (2, 0))
        self.assertIn(f"--by-hand-list {self.listed}: line 1:", err)
        code, out, _ = self.main("--path", str(self.album), "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn(f"setup: --by-hand-list {self.listed}: line 1:", out)

    def test_a_failed_save_is_said_and_exits_1(self) -> None:
        with mock.patch.object(
            lyrics_files, "write_confirmed", side_effect=OSError("disk full")
        ):
            code, out, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 1)
        self.assertIn(f"--by-hand-list: could not write {self.listed}: disk full", out)

    def test_ctrl_c_keeps_every_row_already_saved(self) -> None:
        track = fixtures.TrackSpec("02 B.mp3", seconds=1.0, txt="\n".join(LINES))
        fixtures.build_album(self.album, (track,))
        # The first track ends low-confidence; Ctrl-C lands while the second is timed.
        self.child = CountingChild(LOW_REPLY, stop_when=self.listed.exists)
        code, out, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 130)
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.path, str(self.audio))
        self.assertIn(
            f"listed for timing by hand: 1 row(s) added to {self.listed}", out
        )

    def test_ctrl_c_as_a_result_is_kept_still_saves_its_row(self) -> None:
        # The Ctrl-C arrives once the result is kept and before its row is recorded:
        # held until both are done, so the row is saved and the run still stops.
        record = bh.Recorder.record

        def interrupted(recorder: bh.Recorder, result: TrackResult) -> TrackResult:
            signal.raise_signal(signal.SIGINT)
            return record(recorder, result)

        with mock.patch.object(bh.Recorder, "record", interrupted):
            code, _, _ = self.main("--path", str(self.album))
        self.assertEqual(code, 130)
        self.assertEqual(
            bh.read_by_hand_list(self.listed),
            (bh.Row(str(self.audio), bh.REASON_LOW, "0.05"),),
        )

    def test_fetch_models_refuses_the_option(self) -> None:
        code, _, err = self.main("--mode", "fetch-models")
        self.assertEqual(code, 2)
        self.assertIn("--by-hand-list is for --mode run or hear only", err)

    def test_help_names_the_option(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            sync.main(["--help"], prog=DISPATCHED)
        self.assertIn("--by-hand-list", out.getvalue())
