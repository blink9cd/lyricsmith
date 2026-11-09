"""--mode hear, black-box: main() over invented tracks, every outside call faked.

Each hearing is logged; one not sung is also kept in --heard-dir and listed on the
--by-hand-list, as a timing run keeps it. Nothing is aligned and no sidecar written.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_hear_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_heard_store as hs  # noqa: E402 - import follows the sys.path bootstrap
import lyrics_hearing_log as hl  # noqa: E402 - import follows the sys.path bootstrap
import sy_by_hand as bh  # noqa: E402 - import follows the sys.path bootstrap above
import sy_engine  # noqa: E402 - import follows the sys.path bootstrap above
import sy_hear  # noqa: E402 - import follows the sys.path bootstrap above
import sy_hear_options  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_changed import format_changed_list  # noqa: E402 - follows the bootstrap
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the bootstrap
from sy_cases import OTHER_SONG  # noqa: E402 - import follows the sys.path bootstrap
from sy_hear_cases import (  # noqa: E402 - follows the bootstrap above
    LINES,
    LITTLE,
    SUNG,
    UNSUNG,
    RequestingChild,
    provider_lrc,
)
from sy_types import SetupError  # noqa: E402 - import follows the sys.path bootstrap

fixtures = sy_hear_cases.fixtures
TURBO, LARGE = at_models.WHISPER_MODEL_NAMES
ROW = "Artist/Album/01 A.mp3"


class InterruptingChild(RequestingChild):
    """A fake child answering its first track, then stopped by Ctrl-C on the next."""

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if self.requests:
            raise KeyboardInterrupt
        return super().__call__(argv, timeout=timeout)


class HearCliTest(sy_hear_cases.HearCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.lrc = provider_lrc(LINES)
        (self.audio,) = self.album(
            fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=self.lrc)
        )

    def record(self, model: str = TURBO) -> hl.HearingRecord | None:
        """The log's hearing of the track by *model*."""
        return hl.read_hearing(self.log, str(self.audio), model)

    def kept(self, *argv: str) -> tuple[str, ...]:
        """*argv* with the by-hand list and the transcript store added."""
        return (
            *argv,
            "--by-hand-list",
            str(self.listed),
            "--heard-dir",
            str(self.heard),
        )

    def test_a_sung_text_is_logged_and_nothing_else_is_written(self) -> None:
        code, out, err = self.hear(*self.kept(), child=RequestingChild(SUNG))
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, rf"heard: sung +{ROW}")
        self.assertIn("provider-timed: 1.00 of the 22 word pairs heard", out)
        self.assertEqual(
            self.record(),
            hl.HearingRecord(
                str(self.audio),
                text_fingerprint(LINES),
                hl.HeardKind.PROVIDER_TIMED,
                TURBO,
                22,
                1.0,
                hl.HeardVerdict.SUNG,
                " ".join(LINES),
            ),
        )
        self.assertIn(f"logged 1 hearing(s) in {self.log}", out)
        self.assertEqual(self.audio.with_suffix(".lrc").read_text("utf-8"), self.lrc)
        self.assertFalse(self.listed.exists())
        self.assertEqual(hs.list_records(self.heard).records, ())

    def test_the_child_is_asked_to_hear_only_with_the_turbo(self) -> None:
        self.hear(child=RequestingChild(SUNG))
        (request,) = self.child.requests
        self.assertEqual((request.align, request.whisper_model), (False, TURBO))
        self.assertIsNone(request.transcript)
        self.assertEqual(request.audio, self.audio)

    def test_a_text_not_sung_is_logged_kept_and_listed(self) -> None:
        code, out, _ = self.hear(*self.kept(), child=RequestingChild(UNSUNG))
        self.assertEqual(code, 0)
        self.assertRegex(out, rf"heard: not sung +{ROW}")
        record = self.record()
        assert record is not None
        self.assertEqual(
            (record.verdict, record.transcript), (hl.HeardVerdict.NOT_SUNG, OTHER_SONG)
        )
        kept = hs.read_record(self.heard, self.audio)
        assert kept is not None
        self.assertEqual(
            (kept.fingerprint, kept.transcript), (text_fingerprint(LINES), OTHER_SONG)
        )
        (row,) = bh.read_by_hand_list(self.listed)
        self.assertEqual((row.reason, row.detail), (bh.REASON_NOT_SUNG, "0.00"))
        self.assertIn("listed for timing by hand: 1 row(s) added", out)
        self.assertIn("kept what was heard of 1 track(s) not sung", out)

    def test_a_text_not_sung_without_the_store_is_only_logged(self) -> None:
        code, _, _ = self.hear(child=RequestingChild(UNSUNG))
        self.assertEqual(code, 0)
        self.assertFalse(self.listed.exists() or self.heard.exists())
        record = self.record()
        self.assertEqual(record and record.verdict, hl.HeardVerdict.NOT_SUNG)

    def test_a_text_too_little_heard_is_logged_so(self) -> None:
        code, out, _ = self.hear(*self.kept(), child=RequestingChild(LITTLE))
        self.assertEqual(code, 0)
        self.assertRegex(out, rf"heard: little heard +{ROW}")
        self.assertIn("only 4 word pairs heard, under 20", out)
        record = self.record()
        self.assertEqual(record and record.verdict, hl.HeardVerdict.LITTLE_HEARD)
        self.assertFalse(self.listed.exists())

    def test_a_transcript_too_long_to_keep_logs_the_verdict_without_it(self) -> None:
        unkept = rq.HeardOnly(UNSUNG.verdict, UNSUNG.heard, None)
        code, out, _ = self.hear(*self.kept(), child=RequestingChild(unkept))
        self.assertEqual(code, 0)
        record = self.record()
        assert record is not None
        self.assertEqual((record.verdict, record.transcript), (UNSUNG.verdict, ""))
        self.assertIn("--hearing-log: what was heard is over 64 KB", out)
        self.assertIn("--heard-dir: what was heard is over 64 KB", out)

    def test_a_failed_child_is_an_error_row_and_the_run_goes_on(self) -> None:
        (second,) = self.album(
            fixtures.TrackSpec("02 B.mp3", seconds=1.0, lrc=self.lrc)
        )
        code, out, _ = self.hear(child=RequestingChild(None, SUNG))
        self.assertEqual(code, 1)
        self.assertRegex(out, rf"ERROR hear +{ROW}")
        self.assertIn("unreadable reply", out)
        self.assertIsNone(self.record())
        self.assertIsNotNone(hl.read_hearing(self.log, str(second), TURBO))

    def test_a_rerun_skips_a_track_heard_and_hears_a_changed_text(self) -> None:
        self.hear(child=RequestingChild(SUNG))
        code, out, _ = self.hear(child=RequestingChild())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertRegex(out, rf"skip: heard before +{ROW}")
        self.audio.with_suffix(".lrc").write_text(
            provider_lrc(("a new text",) + LINES), encoding="utf-8", newline="\n"
        )
        code, out, _ = self.hear(child=RequestingChild(UNSUNG))
        self.assertEqual((code, len(self.child.requests)), (0, 1))
        record = self.record()
        self.assertEqual(record and record.verdict, hl.HeardVerdict.NOT_SUNG)

    def test_the_large_model_is_named_to_the_child_and_logged_apart(self) -> None:
        self.hear(child=RequestingChild(SUNG))
        code, _, _ = self.hear("--whisper-model", LARGE, child=RequestingChild(UNSUNG))
        self.assertEqual(code, 0)
        (request,) = self.child.requests
        self.assertEqual(request.whisper_model, LARGE)
        first, second = self.record(TURBO), self.record(LARGE)
        self.assertEqual(first and first.verdict, hl.HeardVerdict.SUNG)
        self.assertEqual(second and second.verdict, hl.HeardVerdict.NOT_SUNG)

    def test_the_tracks_from_list_selects_and_the_kinds_widen(self) -> None:
        (untimed,) = self.album(
            fixtures.TrackSpec("02 B.mp3", seconds=1.0, txt="\n".join(LINES))
        )
        data, _ = format_changed_list([str(untimed)])
        listed = self.tmp / "tracks.txt"
        listed.write_bytes(data)
        code, out, _ = self.hear("--tracks-from", str(listed), child=RequestingChild())
        self.assertEqual(code, 0)
        self.assertRegex(out, r"skip: kind not asked for +Artist/Album/02 B\.mp3")
        self.assertNotIn(ROW, out)
        code, out, _ = self.hear(
            "--tracks-from",
            str(listed),
            "--kinds",
            "untimed,provider-timed",
            child=RequestingChild(SUNG),
        )
        self.assertEqual(code, 0)
        record = hl.read_hearing(self.log, str(untimed), TURBO)
        self.assertEqual(record and record.kind, hl.HeardKind.UNTIMED)

    def test_a_listed_track_is_logged_under_the_lists_path(self) -> None:
        data, _ = format_changed_list([str(self.audio)])
        listed = self.tmp / "tracks.txt"
        listed.write_bytes(data)
        roundabout = self.library / "Artist" / ".." / "Artist"
        argv = ["--mode", "hear", "--path", str(roundabout), "--tracks-from"]
        argv += [str(listed), "--hearing-log", str(self.log)]
        code, _, _ = self.main(*argv, child=RequestingChild(SUNG))
        self.assertEqual((code, len(self.child.requests)), (0, 1))
        record = self.record()
        self.assertEqual(record and record.verdict, hl.HeardVerdict.SUNG)

    def test_a_track_too_long_is_skipped_with_no_child(self) -> None:
        with mock.patch.object(sy_engine, "track_seconds", return_value=7200.0):
            code, out, _ = self.hear(child=RequestingChild())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertRegex(out, rf"skip: track too long +{ROW}")

    def test_a_hearing_the_log_cannot_keep_is_said_and_exits_1(self) -> None:
        refused = OSError("the disk is full")
        with mock.patch.object(hl, "write_state_file", side_effect=refused):
            code, out, _ = self.hear(child=RequestingChild(SUNG))
        self.assertEqual(code, 1)
        self.assertIn(f"--hearing-log: could not keep the hearing in {self.log}", out)
        self.assertNotIn("logged 1", out)

    def test_a_dry_run_prints_the_plan_and_hears_nothing(self) -> None:
        code, out, _ = self.hear("--dry-run", child=RequestingChild())
        self.assertEqual((code, self.child.requests), (0, []))
        self.assertIn("setup: ok", out)
        self.assertRegex(out, rf"hear +{ROW}")
        self.assertFalse(self.log.exists())

    def test_a_dry_run_with_a_setup_problem_or_an_error_row_says_so(self) -> None:
        self.audio.with_suffix(".lrc").write_bytes(b"\xc3\x28\n")
        code, out, _ = self.hear("--dry-run", child=RequestingChild())
        self.assertEqual(code, 1)
        self.assertRegex(out, rf"ERROR text +{ROW}")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_bytes(b"")
        code, out, _ = self.hear("--dry-run", child=RequestingChild())
        self.assertEqual(code, 2)
        self.assertIn(f"setup: --hearing-log is not a folder: {self.log}", out)

    def test_a_setup_problem_stops_the_run_before_any_track(self) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_bytes(b"")
        code, _, err = self.hear(child=RequestingChild(SUNG))
        self.assertEqual((code, self.child.requests), (2, []))
        self.assertIn("--hearing-log is not a folder", err)

    def test_ctrl_c_stops_the_hearing_keeping_what_was_logged(self) -> None:
        (second,) = self.album(
            fixtures.TrackSpec("02 B.mp3", seconds=1.0, lrc=self.lrc)
        )
        code, out, _ = self.hear(child=InterruptingChild(SUNG))
        self.assertEqual(code, 130)
        self.assertIn("interrupted: hearing stopped with 1 track(s) left", out)
        self.assertIn("heard: sung 1", out)
        self.assertIsNotNone(self.record())
        self.assertIsNone(hl.read_hearing(self.log, str(second), TURBO))


class HearOptionsTest(sy_hear_cases.HearCliCase):
    def test_the_hearing_log_is_required(self) -> None:
        code, _, err = self.main("--mode", "hear", "--path", str(self.library))
        self.assertEqual(code, 2)
        self.assertIn("--hearing-log is required in hear mode", err)

    def test_an_unknown_kind_or_no_kind_is_refused(self) -> None:
        for kinds in ("provider-timed,sung", ",", ""):
            with self.subTest(kinds=kinds):
                code, _, err = self.hear("--kinds", kinds)
                self.assertEqual(code, 2)
                self.assertIn("--kinds must name", err)

    def test_a_whisper_model_that_is_not_pinned_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.hear("--whisper-model", "ggml-tiny.bin")
        self.assertEqual(raised.exception.code, 2)

    def test_the_hearing_options_are_for_the_hearing_only(self) -> None:
        listed = self.tmp / "tracks.txt"
        for option, value in (
            ("--hearing-log", str(self.log)),
            ("--kinds", "untimed"),
            ("--tracks-from", str(listed)),
            ("--whisper-model", LARGE),
        ):
            for mode in ("run", "fetch-models"):
                with self.subTest(option=option, mode=mode):
                    code, _, err = self.main(
                        "--mode", mode, "--path", str(self.library), option, value
                    )
                    self.assertEqual(code, 2)
                    self.assertIn(f"{option} is for --mode hear only", err)

    def test_a_hearing_neither_embeds_nor_overwrites(self) -> None:
        for option in ("--embed", "--overwrite"):
            with self.subTest(option=option):
                code, _, err = self.hear(option)
                self.assertEqual(code, 2)
                self.assertIn(f"{option} is not for --mode hear", err)

    def test_a_comparison_hearing_keeps_nothing_but_its_log(self) -> None:
        # large-v3's transcript would replace the turbo's in the store, against which
        # a later run judges a fixed text, and its not-sung row would skip a timing.
        self.album(fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=provider_lrc(LINES)))
        listed, heard = (
            ("--by-hand-list", str(self.listed)),
            ("--heard-dir", str(self.heard)),
        )
        for argv, named in (((*listed, *heard), heard[0]), (listed, listed[0])):
            with self.subTest(named=named):
                code, _, err = self.hear("--whisper-model", LARGE, *argv)
                self.assertEqual((code, self.child.requests), (2, []))
                self.assertIn(f"{named} is not for --whisper-model {LARGE}", err)
        turbo = ("--whisper-model", TURBO, *listed, *heard)
        code, _, _ = self.hear(*turbo, child=RequestingChild(SUNG))
        self.assertEqual(code, 0)

    def test_an_absent_large_model_names_its_own_fetch(self) -> None:
        # The default fetch leaves large-v3 out, so its fetch must name it.
        pinned = sy_hear_cases.sy_cases.invented_models(
            TURBO, at_models.VAD_MODEL_NAME, LARGE
        )
        self.enterContext(mock.patch.object(at_models, "MANIFEST", pinned))
        for model in pinned[:2]:
            (self.whisper / model.name).write_bytes(sy_hear_cases.sy_cases.DATA)
        self.album(fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=provider_lrc(LINES)))
        code, out, _ = self.hear("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("setup: ok", out)
        code, _, err = self.hear("--whisper-model", LARGE, child=RequestingChild(SUNG))
        self.assertEqual((code, self.child.requests), (2, []))
        self.assertIn(f"missing model {LARGE}", err)
        fetch = "lyricsmith transcribe --mode fetch-models --model-dir "
        fetch += f"{shlex.quote(str(self.whisper))} --whisper-model {LARGE}"
        self.assertIn(f"fetch them: {fetch}", err)

    def test_the_store_still_needs_the_list(self) -> None:
        code, _, err = self.hear("--heard-dir", str(self.heard))
        self.assertEqual(code, 2)
        self.assertIn("--heard-dir needs --by-hand-list", err)

    def test_an_unreadable_track_list_is_a_setup_error(self) -> None:
        code, _, err = self.hear("--tracks-from", str(self.tmp / "missing.txt"))
        self.assertEqual(code, 2)
        self.assertIn("--tracks-from", err)

    def test_nothing_found_says_so(self) -> None:
        self.library.mkdir()
        code, out, _ = self.hear()
        self.assertEqual(code, 0)
        self.assertIn("nothing to hear", out)

    def test_a_malformed_by_hand_list_is_a_setup_error(self) -> None:
        self.library.mkdir()
        self.listed.parent.mkdir(parents=True)
        self.listed.write_bytes(b"not a row\n")
        code, _, err = self.hear("--by-hand-list", str(self.listed))
        self.assertEqual(code, 2)
        self.assertIn(f"--by-hand-list {self.listed}: line 1", err)

    def test_an_unreadable_log_file_is_said_and_its_track_heard(self) -> None:
        (audio,) = self.album(
            fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=provider_lrc(LINES))
        )
        bad = hl.hearing_path(self.log, str(audio), TURBO)
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"[]")
        code, out, _ = self.hear(child=RequestingChild(SUNG))
        self.assertEqual(code, 0)
        self.assertIn(f"--hearing-log: {bad}", out)
        record = hl.read_hearing(self.log, str(audio), TURBO)
        self.assertEqual(record and record.verdict, hl.HeardVerdict.SUNG)

    def test_the_mode_needs_a_path_even_when_called_alone(self) -> None:
        options = sync.resolve_options(
            sync.build_parser(self.models, self.whisper).parse_args(
                ["--mode", "fetch-models"]
            )
        )
        hear = sy_hear_options.HearOptions(self.log, sy_hear_options.DEFAULT_KINDS)
        with self.assertRaisesRegex(SetupError, "--path is required in hear mode"):
            sy_hear.hear_mode(options, hear, self.seams(), prog=("sync.py",))


class HeardReplyShapeTest(sy_hear_cases.HearCliCase):
    def test_each_verdict_counts_in_the_footer(self) -> None:
        lrc = provider_lrc(LINES)
        self.album(
            *(
                fixtures.TrackSpec(f"0{n} {n}.mp3", seconds=1.0, lrc=lrc)
                for n in range(1, 4)
            )
        )
        code, out, _ = self.hear(child=RequestingChild(SUNG, UNSUNG, LITTLE))
        self.assertEqual(code, 0)
        self.assertIn("heard: sung 1, heard: not sung 1, heard: little heard 1", out)
