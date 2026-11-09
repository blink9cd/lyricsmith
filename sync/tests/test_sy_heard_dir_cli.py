"""--heard-dir, black-box: main() with every outside call faked, offline and isolated."""

from __future__ import annotations

import contextlib
import io
import os
import signal
import stat
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import lyrics_heard_store as hs  # noqa: E402 - import follows the sys.path bootstrap
import sy_by_hand as bh  # noqa: E402 - import follows the sys.path bootstrap above
import sy_heard_dir as hd  # noqa: E402 - import follows the sys.path bootstrap above
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the bootstrap
from sy_cases import (  # noqa: E402 - bootstrap
    DATA,
    HEARD,
    OTHER_SONG,
    fake_child,
    fake_opener,
    not_sung_reply,
    reply_for,
)
from sy_types import Planned, TrackResult  # noqa: E402 - bootstrap
from test_sy_cli import (  # noqa: E402 - the black-box suite's own stand-ins
    DISPATCHED,
    TINY,
    VERSION,
    RecordingAlbum,
)

fixtures = sy_cases.fixtures
LINES = ("la la", "lo lo", "li li")
#: A new text another song's transcript still does not hear.
STILL_WRONG = ("lu lu", "le le")
#: A new text that is what the kept transcript heard.
NOW_RIGHT = (OTHER_SONG,)
#: The note beneath a not-sung row judged on a kept transcript, with no child.
REJUDGED = "judged against what was heard before"
#: The note beneath a not-sung row whose transcript the store would not keep.
UNKEPT = "--heard-dir: what was heard is over 64 KB and was not kept"
#: A child's reply that the text is longer than the track: no spans.
TOO_LONG_REPLY = rq.dump_reply(rq.Reply(rq.STATUS_TOO_LONG, 1, 0.02, (), HEARD))


class RequestingChild:
    """A fake child that answers *reply*, keeping each request it was handed."""

    def __init__(self, reply: str) -> None:
        self.requests: list[rq.Request] = []
        self.child = fake_child(reply)

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        text = Path(argv[argv.index("--request") + 1]).read_text(encoding="utf-8")
        self.requests.append(rq.load_request(text))
        return self.child(argv, timeout=timeout)


class HeardDirCliTest(sy_cases.IsolatedCase):
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
        self.heard = self.tmp / "state" / "heard"
        self.child = RequestingChild(not_sung_reply())

    def main(
        self, *argv: str, reply: str | None = None, listed: bool = True
    ) -> tuple[int, str, str]:
        """main() on *argv*, the store, the list and the models; its code and output."""
        if reply is not None:
            self.child = RequestingChild(reply)
        seams = sy_modes.Seams(
            run=self.child,
            embed=RecordingAlbum(),
            album_entry=self.tmp / "album.py",
            find_spec=lambda name: object(),
            which=lambda name: f"/usr/bin/{name}",
            version=lambda name: VERSION,
        )
        by_hand = ("--by-hand-list", str(self.listed)) if listed else ()
        full = [
            *argv,
            *by_hand,
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

    def run_store(self, *argv: str, reply: str | None = None) -> tuple[int, str]:
        """A run over the album with the store; its code and output."""
        code, out, _ = self.main(
            "--path",
            str(self.album),
            "--heard-dir",
            str(self.heard),
            *argv,
            reply=reply,
        )
        return code, out

    def retext(self, lines: Sequence[str]) -> None:
        """Replace the track's untimed text with *lines*."""
        self.audio.with_suffix(".txt").write_text(
            "\n".join(lines), encoding="utf-8", newline="\n"
        )

    def test_a_text_not_sung_keeps_its_transcript_beside_its_row(self) -> None:
        code, out = self.run_store()
        self.assertEqual(code, 0)
        self.assertEqual(
            hs.read_record(self.heard, self.audio),
            hs.HeardRecord(self.audio, text_fingerprint(LINES), OTHER_SONG),
        )
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.reason, bh.REASON_NOT_SUNG)
        self.assertIn(
            f"kept what was heard of 1 track(s) not sung in {self.heard}", out
        )

    @unittest.skipIf(os.name == "nt", sy_cases.NO_POSIX_MODES)
    def test_a_kept_transcript_is_private(self) -> None:
        self.run_store()
        record = hs.record_path(self.heard, self.audio)
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)

    def test_an_unchanged_text_is_skipped_with_no_child(self) -> None:
        self.run_store()
        code, out = self.run_store(reply=not_sung_reply())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertRegex(out, r"skip: listed as not sung +01 A\.mp3")

    def test_a_changed_text_still_not_sung_is_judged_again_with_no_child(self) -> None:
        self.run_store()
        self.retext(STILL_WRONG)
        code, out = self.run_store(reply=not_sung_reply())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertRegex(out, r"not sung: left untimed +01 A\.mp3")
        self.assertIn(REJUDGED, out)
        self.assertNotIn("the text changed", out)
        record = hs.read_record(self.heard, self.audio)
        assert record is not None  # the record was rewritten, not removed
        self.assertEqual(
            (record.fingerprint, record.transcript),
            (text_fingerprint(STILL_WRONG), OTHER_SONG),
        )
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual((row.reason, row.detail), (bh.REASON_NOT_SUNG, "0.00"))
        code, _ = self.run_store(reply=not_sung_reply())
        self.assertEqual((code, self.child.requests), (0, []))

    def test_a_changed_text_now_heard_is_timed_from_the_kept_transcript(self) -> None:
        self.run_store()
        self.retext(NOW_RIGHT)
        code, out = self.run_store(reply=reply_for(NOW_RIGHT))
        self.assertEqual(code, 0)
        self.assertEqual([r.transcript for r in self.child.requests], [OTHER_SONG])
        self.assertRegex(out, r"timed +01 A\.mp3")
        self.assertTrue(self.audio.with_suffix(".lrc").exists())
        self.assertIsNone(hs.read_record(self.heard, self.audio))
        self.assertEqual(bh.read_by_hand_list(self.listed), ())

    def test_a_transcript_too_long_to_keep_leaves_the_track_not_sung(self) -> None:
        code, out = self.run_store(reply=not_sung_reply(transcript=None))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"not sung: left untimed +01 A\.mp3")
        self.assertIn(UNKEPT, out)
        self.assertIsNone(hs.read_record(self.heard, self.audio))
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.reason, bh.REASON_NOT_SUNG)

    def test_without_the_store_a_long_transcript_is_still_not_sung(self) -> None:
        code, out, _ = self.main(
            "--path", str(self.album), reply=not_sung_reply(transcript=None)
        )
        self.assertEqual(code, 0)
        self.assertRegex(out, r"not sung: left untimed +01 A\.mp3")
        self.assertNotIn("--heard-dir", out)
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.reason, bh.REASON_NOT_SUNG)

    def test_a_changed_text_now_low_confidence_ends_its_not_sung_state(self) -> None:
        self.run_store()
        self.retext(NOW_RIGHT)
        code, out = self.run_store(reply=reply_for(NOW_RIGHT, score=0.05))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"low confidence: time by hand +01 A\.mp3")
        reasons = [row.reason for row in bh.read_by_hand_list(self.listed)]
        self.assertEqual(reasons, [bh.REASON_LOW])
        self.assertIsNone(hs.read_record(self.heard, self.audio))

    def test_a_changed_text_too_long_drops_its_row_and_reuses_its_record(
        self,
    ) -> None:
        self.run_store()
        self.retext(NOW_RIGHT)
        code, out = self.run_store(reply=TOO_LONG_REPLY)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"skip: text longer than the track +01 A\.mp3")
        self.assertEqual(bh.read_by_hand_list(self.listed), ())
        self.assertIsNotNone(hs.read_record(self.heard, self.audio))
        code, _ = self.run_store(reply=reply_for(NOW_RIGHT))
        self.assertEqual(code, 0)
        self.assertEqual([r.transcript for r in self.child.requests], [OTHER_SONG])

    def test_a_record_whose_row_was_removed_is_never_heard_again(self) -> None:
        self.run_store()
        self.listed.unlink()
        code, out = self.run_store(reply=not_sung_reply())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertRegex(out, r"not sung: left untimed +01 A\.mp3")
        self.assertIn(REJUDGED, out)
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.reason, bh.REASON_NOT_SUNG)
        self.assertIsNotNone(hs.read_record(self.heard, self.audio))

    def test_a_record_of_a_track_timed_since_goes_at_the_start(self) -> None:
        code, _, _ = self.main("--path", str(self.album), reply=reply_for(LINES))
        self.assertEqual(code, 0)
        hs.write_record(
            self.heard, hs.HeardRecord(self.audio, text_fingerprint(LINES), "x")
        )
        code, out = self.run_store(reply=reply_for(LINES))
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertIsNone(hs.read_record(self.heard, self.audio))
        self.assertIn(
            f"--heard-dir: removed 1 record(s) of tracks gone or timed from {self.heard}",
            out,
        )

    def test_an_old_list_with_no_record_still_skips(self) -> None:
        self.listed.parent.mkdir()
        row = bh.Row(str(self.audio), bh.REASON_NOT_SUNG, "0.04")
        self.listed.write_bytes(bh.format_rows((row,)))
        code, out = self.run_store(reply=not_sung_reply())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertRegex(out, r"skip: listed as not sung +01 A\.mp3")

    def test_the_start_of_a_run_removes_records_of_tracks_gone_or_timed(self) -> None:
        gone = self.album / "02 B.mp3"
        away = self.tmp / "Elsewhere" / "03 C.mp3"
        for track in (gone, away):
            hs.write_record(self.heard, hs.HeardRecord(track, "0" * 64, "x"))
        code, out = self.run_store(reply=reply_for(LINES))
        self.assertEqual(code, 0)
        self.assertIsNone(hs.read_record(self.heard, gone))
        self.assertIsNotNone(hs.read_record(self.heard, away))
        self.assertIn(
            f"--heard-dir: removed 1 record(s) of tracks gone or timed from {self.heard}",
            out,
        )

    def test_a_problem_file_is_reported_and_left(self) -> None:
        self.heard.mkdir(parents=True)
        bad = hs.record_path(self.heard, self.album / "02 B.mp3")
        bad.write_bytes(b"[]")
        code, out = self.run_store(reply=reply_for(LINES))
        self.assertEqual(code, 0)
        self.assertIn(f"--heard-dir: {bad}: transcript record ignored", out)
        self.assertTrue(bad.exists())

    def test_a_dry_run_reads_the_store_and_writes_nothing(self) -> None:
        self.run_store()
        self.retext(STILL_WRONG)
        gone = self.album / "02 B.mp3"
        hs.write_record(self.heard, hs.HeardRecord(gone, "0" * 64, "x"))
        before = sorted(p.name for p in self.heard.iterdir())
        kept = hs.read_record(self.heard, self.audio)
        code, out = self.run_store("--dry-run")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"time +01 A\.mp3")
        self.assertEqual(sorted(p.name for p in self.heard.iterdir()), before)
        self.assertEqual(hs.read_record(self.heard, self.audio), kept)
        missing = self.tmp / "never"
        self.main("--path", str(self.album), "--heard-dir", str(missing), "--dry-run")
        self.assertFalse(missing.exists())

    def test_a_failed_write_is_said_and_exits_1(self) -> None:
        with mock.patch.object(hd, "write_record", side_effect=OSError("disk full")):
            code, out = self.run_store()
        self.assertEqual(code, 1)
        self.assertIn(
            f"--heard-dir: could not keep what was heard in {self.heard}: disk full",
            out,
        )
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.reason, bh.REASON_NOT_SUNG)

    def test_ctrl_c_as_the_record_is_kept_still_saves_the_row(self) -> None:
        keep = hd.Keeper.keep

        def interrupted(
            keeper: hd.Keeper, planned: Planned, result: TrackResult
        ) -> TrackResult:
            signal.raise_signal(signal.SIGINT)
            return keep(keeper, planned, result)

        with mock.patch.object(hd.Keeper, "keep", interrupted):
            code, _ = self.run_store()
        self.assertEqual(code, 130)
        self.assertIsNotNone(hs.read_record(self.heard, self.audio))
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual(row.reason, bh.REASON_NOT_SUNG)

    def test_a_relative_folder_is_made_absolute(self) -> None:
        self.enterContext(contextlib.chdir(self.tmp))
        code, _, _ = self.main(
            "--path", str(self.album), "--heard-dir", os.path.join("state", "heard")
        )
        self.assertEqual(code, 0)
        self.assertIsNotNone(hs.read_record(self.heard, self.audio))

    def test_a_file_at_the_folder_name_is_a_setup_error(self) -> None:
        self.heard.parent.mkdir()
        self.heard.write_text("x", encoding="utf-8", newline="\n")
        code, _, err = self.main(
            "--path", str(self.album), "--heard-dir", str(self.heard)
        )
        self.assertEqual((code, self.child.requests), (2, []))
        self.assertIn(f"--heard-dir is not a folder: {self.heard}", err)
        code, out = self.run_store("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn(f"setup: --heard-dir is not a folder: {self.heard}", out)

    def test_the_folder_needs_the_by_hand_list(self) -> None:
        code, _, err = self.main(
            "--path", str(self.album), "--heard-dir", str(self.heard), listed=False
        )
        self.assertEqual(code, 2)
        self.assertIn("--heard-dir needs --by-hand-list", err)

    def test_fetch_models_refuses_the_option(self) -> None:
        code, _, err = self.main(
            "--mode", "fetch-models", "--heard-dir", str(self.heard), listed=False
        )
        self.assertEqual(code, 2)
        self.assertIn("--heard-dir is for --mode run or hear only", err)

    def test_help_names_the_option(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            sync.main(["--help"], prog=DISPATCHED)
        self.assertIn("--heard-dir", out.getvalue())
