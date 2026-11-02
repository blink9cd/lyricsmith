"""The --by-hand-list file: its rows, its strict reader, and the recorder that keeps it."""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import lyrics_files  # noqa: E402 - import follows the sys.path bootstrap above
import sy_by_hand as bh  # noqa: E402 - import follows the sys.path bootstrap above
import sy_plan  # noqa: E402 - import follows the sys.path bootstrap above
from fifo_fakes import make_pipe, within_deadline  # noqa: E402 - album's test helpers
from sy_types import Action, Outcome, Planned, TrackResult  # noqa: E402 - bootstrap
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the bootstrap
from lyrics_heard_store import HeardRecord  # noqa: E402 - follows the bootstrap

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

LOW = bh.Row("/m/Artist/Album/01 A.mp3", bh.REASON_LOW, "0.08")
FIRST = bh.Row("/m/Artist/Album/02 B.mp3", bh.REASON_FIRST, "[00:00.280]")
UNSUNG = bh.Row("/m/Artist/Album/04 D.mp3", bh.REASON_NOT_SUNG, "0.04")
LITTLE = bh.Row("/m/Artist/Album/05 E.mp3", bh.REASON_LITTLE, "12 pairs")
#: A field's text: anything a row's field can hold, no tab, line break or NUL in it.
FIELD = hs.text(
    alphabet=hs.characters(
        blacklist_categories=["Cs"], blacklist_characters="\t\n\r\x00"
    ),
    max_size=12,
)
ROW = hs.builds(
    bh.Row,
    FIELD.map(lambda text: "/" + text),
    hs.sampled_from(bh.REASONS),
    FIELD,
)


class FormatTest(sy_cases.IsolatedCase):
    """A row is three tab-separated fields on one line ended by a linefeed."""

    def test_rows_are_written_one_per_line(self) -> None:
        self.assertEqual(
            bh.format_rows((LOW, FIRST, UNSUNG, LITTLE)),
            b"/m/Artist/Album/01 A.mp3\tlow confidence\t0.08\n"
            b"/m/Artist/Album/02 B.mp3\tfirst line\t[00:00.280]\n"
            b"/m/Artist/Album/04 D.mp3\tnot sung\t0.04\n"
            b"/m/Artist/Album/05 E.mp3\tlittle heard\t12 pairs\n",
        )

    def test_a_path_the_format_cannot_hold_is_named_with_why(self) -> None:
        cases = (
            ("/a\tb.mp3", bh.REASON_TAB),
            ("/a\rb.mp3", bh.REASON_CR),
            ("/a\nb.mp3", "linefeed"),
            ("/a\x00b.mp3", "NUL"),
            ("/a\udcffb.mp3", "UTF-8"),
            ("a.mp3", "absolute"),
        )
        for path, why in cases:
            with self.subTest(path=path):
                self.assertIn(why, bh.path_refusal(path) or "")
        self.assertIsNone(bh.path_refusal(LOW.path))


class ParseTest(sy_cases.IsolatedCase):
    """The reader takes only what the writer writes, naming the first line it refuses."""

    def test_rows_read_back_and_blank_lines_are_skipped(self) -> None:
        data = bh.format_rows((LOW,)) + b"\n  \n" + bh.format_rows((FIRST,))
        self.assertEqual(bh.parse_by_hand_list(data), (LOW, FIRST))
        self.assertEqual(bh.parse_by_hand_list(b""), ())

    def test_each_broken_line_is_refused_naming_it(self) -> None:
        good = bh.format_rows((LOW,))
        cases = (
            (good + b"/x.mp3\tlow confidence\t0.1", "line 2: no linefeed ends it"),
            (good + b"/x\xff.mp3\tlow confidence\t0.1\n", "line 2: not UTF-8"),
            (b"/x.mp3\tlow confidence\t0.1\r\n", "line 1: a carriage return"),
            (b"/x\x00.mp3\tlow confidence\t0.1\n", "line 1: a NUL"),
            (b"/x.mp3\tlow confidence\n", "line 1: not 3 tab-separated fields"),
            (b"/x.mp3\tlow\t0.1\n", "line 1: not a reason this tool writes"),
            (b"x.mp3\tfirst line\t[00:01.000]\n", "line 1: the path is not"),
        )
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(bh.ByHandListError) as raised:
                    bh.parse_by_hand_list(data)
                self.assertIn(message, str(raised.exception))

    @given(hs.binary(max_size=64))
    def test_fuzz_parse_by_hand_list(self, data: bytes) -> None:
        try:
            rows = bh.parse_by_hand_list(data)
        except bh.ByHandListError:
            return
        self.assertEqual(bh.parse_by_hand_list(bh.format_rows(rows)), rows)

    @given(hs.lists(ROW, max_size=4))
    def test_any_written_rows_read_back_as_themselves(self, rows: list[bh.Row]) -> None:
        self.assertEqual(bh.parse_by_hand_list(bh.format_rows(rows)), tuple(rows))


class ReadTest(sy_cases.IsolatedCase):
    """The file is read whole under a cap, never through a link or from a FIFO."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.tmp / "state" / "by-hand.tsv"

    def test_a_missing_file_is_an_empty_list(self) -> None:
        self.assertEqual(bh.read_by_hand_list(self.target), ())

    def test_a_file_reads_as_its_rows(self) -> None:
        self.target.parent.mkdir()
        self.target.write_bytes(bh.format_rows((LOW, FIRST)))
        self.assertEqual(bh.read_by_hand_list(self.target), (LOW, FIRST))

    def test_a_broken_file_names_itself_and_the_line(self) -> None:
        self.target.parent.mkdir()
        self.target.write_bytes(b"/x.mp3\tlow\t0.1\n")
        with self.assertRaises(bh.ByHandListError) as raised:
            bh.read_by_hand_list(self.target)
        self.assertIn(f"{self.target}: line 1:", str(raised.exception))

    def test_a_link_a_fifo_or_an_oversized_file_is_refused(self) -> None:
        self.target.parent.mkdir()
        real = self.tmp / "real.tsv"
        real.write_bytes(bh.format_rows((LOW,)))
        self.target.symlink_to(real)
        with self.assertRaises(bh.ByHandListError) as raised:
            bh.read_by_hand_list(self.target)
        self.assertIn("cannot be read", str(raised.exception))
        self.target.unlink()
        make_pipe(self.target)
        with self.assertRaises(bh.ByHandListError):
            within_deadline(
                self, self.target, lambda: bh.read_by_hand_list(self.target)
            )
        self.target.unlink()
        self.target.write_bytes(bh.format_rows((LOW,)))
        with mock.patch.object(bh, "MAX_LIST_BYTES", 8):
            with self.assertRaises(bh.ByHandListError) as raised:
                bh.read_by_hand_list(self.target)
        self.assertIn("more than 8 bytes", str(raised.exception))

    def test_only_low_confidence_and_not_sung_rows_skip_each_with_its_own(
        self,
    ) -> None:
        skipped = bh.skipped_paths((LOW, FIRST, UNSUNG, LITTLE))
        self.assertEqual(
            skipped,
            {LOW.path: Action.SKIP_BY_HAND, UNSUNG.path: Action.SKIP_NOT_SUNG},
        )


class RecorderTest(sy_cases.IsolatedCase):
    """Each row is saved as its track ends, the whole file replaced atomically."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.tmp / "new" / "deeper" / "by-hand.tsv"
        self.audio = Path("/m/Artist/Album/03 C.mp3")

    def low(self, audio: Path | None = None) -> TrackResult:
        """A low-confidence result for *audio*, by default the case's own track."""
        return TrackResult(
            audio or self.audio,
            Outcome.LOW_CONFIDENCE,
            ("median line confidence 0.05",),
            confidence="0.05",
        )

    def test_a_low_confidence_track_is_saved_private(self) -> None:
        recorder = bh.Recorder(self.target, (LOW,))
        result = self.low()
        self.assertEqual(recorder.record(result), result)
        rows = bh.read_by_hand_list(self.target)
        self.assertEqual(rows, (LOW, bh.Row(str(self.audio), bh.REASON_LOW, "0.05")))
        self.assertEqual(recorder.added, 1)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        for folder in (self.target.parent, self.target.parent.parent):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_a_flagged_first_line_is_saved_and_replaces_an_older_one(self) -> None:
        old = bh.Row(str(self.audio), bh.REASON_FIRST, "[00:09.000]")
        recorder = bh.Recorder(self.target, (old, FIRST))
        recorder.record(
            TrackResult(self.audio, Outcome.RETIMED, first_line="[00:00.280]")
        )
        new = bh.Row(str(self.audio), bh.REASON_FIRST, "[00:00.280]")
        self.assertEqual(bh.read_by_hand_list(self.target), (FIRST, new))

    def test_a_written_track_with_no_flag_drops_its_stale_check(self) -> None:
        old = bh.Row(str(self.audio), bh.REASON_FIRST, "[00:09.000]")
        recorder = bh.Recorder(self.target, (old, LOW))
        recorder.record(TrackResult(self.audio, Outcome.TIMED))
        self.assertEqual(bh.read_by_hand_list(self.target), (LOW,))
        self.assertEqual(recorder.added, 0)

    def test_a_text_not_sung_is_saved_with_its_share(self) -> None:
        recorder = bh.Recorder(self.target, (LOW,))
        recorder.record(TrackResult(self.audio, Outcome.NOT_SUNG, share="0.04"))
        row = bh.Row(str(self.audio), bh.REASON_NOT_SUNG, "0.04")
        self.assertEqual(bh.read_by_hand_list(self.target), (LOW, row))
        self.assertEqual(recorder.added, 1)

    def test_a_not_sung_track_replaces_only_its_own_older_row(self) -> None:
        path = str(self.audio)
        old = bh.Row(path, bh.REASON_NOT_SUNG, "0.10")
        first = bh.Row(path, bh.REASON_FIRST, "[00:09.000]")
        recorder = bh.Recorder(self.target, (old, first))
        recorder.record(TrackResult(self.audio, Outcome.NOT_SUNG))
        # The old .lrc is still in place, so its first-line check stays true.
        new = bh.Row(path, bh.REASON_NOT_SUNG, bh.UNMEASURED)
        self.assertEqual(bh.read_by_hand_list(self.target), (first, new))

    def test_a_written_track_too_little_heard_is_saved_beside_its_first_line(
        self,
    ) -> None:
        old = bh.Row(str(self.audio), bh.REASON_LITTLE, "3 pairs")
        recorder = bh.Recorder(self.target, (old, LOW))
        result = TrackResult(
            self.audio, Outcome.TIMED, first_line="[00:00.280]", little_heard=12
        )
        recorder.record(result)
        path = str(self.audio)
        self.assertEqual(
            bh.read_by_hand_list(self.target),
            (
                LOW,
                bh.Row(path, bh.REASON_FIRST, "[00:00.280]"),
                bh.Row(path, bh.REASON_LITTLE, "12 pairs"),
            ),
        )
        self.assertEqual(recorder.added, 2)

    def test_a_written_track_drops_its_stale_not_sung_and_low_rows(self) -> None:
        path = str(self.audio)
        unsung = bh.Row(path, bh.REASON_NOT_SUNG, "0.04")
        low = bh.Row(path, bh.REASON_LOW, "0.08")
        for outcome in (Outcome.TIMED, Outcome.RETIMED):
            with self.subTest(outcome=outcome):
                recorder = bh.Recorder(self.target, (LOW, unsung, low))
                recorder.record(TrackResult(self.audio, outcome))
                self.assertEqual(bh.read_by_hand_list(self.target), (LOW,))

    def test_a_low_confidence_or_too_long_track_drops_its_stale_not_sung_row(
        self,
    ) -> None:
        unsung = bh.Row(str(self.audio), bh.REASON_NOT_SUNG, "0.04")
        for outcome in (Outcome.LOW_CONFIDENCE, Outcome.TOO_LONG):
            with self.subTest(outcome=outcome):
                recorder = bh.Recorder(self.target, (LOW, unsung))
                recorder.record(TrackResult(self.audio, outcome))
                rows = bh.read_by_hand_list(self.target)
                self.assertNotIn(unsung, rows)
                self.assertIn(LOW, rows)

    def test_a_written_track_heard_enough_drops_its_stale_little_heard(self) -> None:
        old = bh.Row(str(self.audio), bh.REASON_LITTLE, "3 pairs")
        recorder = bh.Recorder(self.target, (LOW, old))
        recorder.record(TrackResult(self.audio, Outcome.RETIMED))
        self.assertEqual(bh.read_by_hand_list(self.target), (LOW,))

    def test_too_little_heard_on_a_track_left_unwritten_adds_no_row(self) -> None:
        recorder = bh.Recorder(self.target, ())
        low = TrackResult(self.audio, Outcome.LOW_CONFIDENCE, little_heard=12)
        recorder.record(low)
        self.assertEqual(
            bh.read_by_hand_list(self.target),
            (bh.Row(str(self.audio), bh.REASON_LOW, bh.UNMEASURED),),
        )

    def test_a_track_that_lists_nothing_writes_nothing(self) -> None:
        recorder = bh.Recorder(self.target, (LOW,))
        for outcome in (Outcome.TIMED, Outcome.SKIP_TIMED, Outcome.ERROR_ALIGN):
            recorder.record(TrackResult(self.audio, outcome))
        self.assertFalse(self.target.exists())
        bh.Recorder(None, ()).record(self.low())
        self.assertFalse(self.target.parent.exists())

    def test_a_path_the_format_cannot_hold_is_said_in_the_row(self) -> None:
        recorder = bh.Recorder(self.target, ())
        result = recorder.record(self.low(Path("/m/A\tB.mp3")))
        self.assertEqual(
            result.detail[-1], f"--by-hand-list: left out, the path {bh.REASON_TAB}"
        )
        self.assertFalse(self.target.exists())
        self.assertEqual((recorder.added, recorder.failed), (0, False))

    def test_a_non_regular_file_at_the_name_is_refused(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.mkdir()
        recorder = bh.Recorder(self.target, ())
        result = recorder.record(self.low())
        self.assertIn(
            f"--by-hand-list: could not write {self.target}", result.detail[-1]
        )
        self.assertTrue(recorder.failed)
        self.assertTrue(self.target.is_dir())

    def assert_refused_through(self, made: str) -> None:
        """A save onto the *made* entry at the list's name refuses it, said in the row."""
        recorder = bh.Recorder(self.target, ())
        result = recorder.record(self.low())
        self.assertEqual(
            result.detail[-1],
            f"--by-hand-list: could not write {self.target}: "
            f"{self.target.name} is not a regular file",
            made,
        )
        self.assertEqual((recorder.added, recorder.failed), (0, True))

    def test_a_link_at_the_name_is_refused_and_nothing_written_through_it(
        self,
    ) -> None:
        self.target.parent.mkdir(parents=True)
        aimed = self.tmp / "aimed.tsv"
        aimed.write_bytes(b"theirs\n")
        self.target.symlink_to(aimed)
        self.assert_refused_through("link")
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(self.target.readlink(), aimed)
        self.assertEqual(aimed.read_bytes(), b"theirs\n")

    def test_a_fifo_at_the_name_is_refused_and_left_as_it_is(self) -> None:
        self.target.parent.mkdir(parents=True)
        make_pipe(self.target)
        within_deadline(self, self.target, lambda: self.assert_refused_through("FIFO"))
        self.assertTrue(stat.S_ISFIFO(self.target.lstat().st_mode))

    def test_a_low_confidence_result_without_its_median_is_still_listed(
        self,
    ) -> None:
        recorder = bh.Recorder(self.target, ())
        recorder.record(TrackResult(self.audio, Outcome.LOW_CONFIDENCE))
        self.assertEqual(
            bh.read_by_hand_list(self.target),
            (bh.Row(str(self.audio), bh.REASON_LOW, bh.UNMEASURED),),
        )
        self.assertEqual(recorder.added, 1)

    def test_a_failed_write_is_said_in_the_row(self) -> None:
        recorder = bh.Recorder(self.target, ())
        with mock.patch.object(
            lyrics_files, "write_confirmed", side_effect=OSError("disk full")
        ):
            result = recorder.record(self.low())
        self.assertTrue(result.detail[-1].endswith("disk full"), result.detail)
        self.assertEqual((recorder.added, recorder.failed), (0, True))

    def test_the_save_is_made_with_ctrl_c_held(self) -> None:
        with mock.patch.object(bh, "deferred_interrupt") as deferred:
            bh.Recorder(self.target, ()).record(self.low())
        deferred.assert_called_once_with()
        deferred.return_value.__enter__.assert_called_once_with()
        self.assertEqual(len(bh.read_by_hand_list(self.target)), 1)


class PlanTest(sy_cases.IsolatedCase):
    """A listed track to time or re-time plans as a skip; any other plan stands."""

    def test_only_a_listed_time_or_re_time_is_skipped(self) -> None:
        audio = Path("/m/A.mp3")
        listed = {str(audio): Action.SKIP_BY_HAND}
        for action in (Action.TIME, Action.RETIME):
            planned = Planned(audio, action, lines=("la",))
            self.assertEqual(
                sy_plan.listed_by_hand(planned, listed),
                Planned(audio, Action.SKIP_BY_HAND),
            )
        timed = Planned(audio, Action.SKIP_TIMED)
        self.assertIs(sy_plan.listed_by_hand(timed, listed), timed)
        other = Planned(Path("/m/B.mp3"), Action.TIME, lines=("la",))
        self.assertIs(sy_plan.listed_by_hand(other, listed), other)

    def test_a_track_listed_as_not_sung_reads_as_its_own_skip(self) -> None:
        audio = Path("/m/A.mp3")
        planned = Planned(audio, Action.TIME, lines=("la",))
        self.assertEqual(
            sy_plan.listed_by_hand(planned, {str(audio): Action.SKIP_NOT_SUNG}),
            Planned(audio, Action.SKIP_NOT_SUNG),
        )

    def test_a_not_sung_track_whose_text_is_unchanged_stays_skipped(self) -> None:
        audio = Path("/m/A.mp3")
        planned = Planned(audio, Action.TIME, lines=("la la", "lo"))
        kept = HeardRecord(audio, text_fingerprint(("La  la", "lo!")), "heard")
        self.assertEqual(
            sy_plan.listed_by_hand(
                planned, {str(audio): Action.SKIP_NOT_SUNG}, {str(audio): kept}
            ),
            Planned(audio, Action.SKIP_NOT_SUNG),
        )

    def test_a_not_sung_track_whose_text_changed_is_planned_with_its_transcript(
        self,
    ) -> None:
        audio = Path("/m/A.mp3")
        listed = {str(audio): Action.SKIP_NOT_SUNG}
        kept = {str(audio): HeardRecord(audio, text_fingerprint("lo lo"), "heard")}
        for action in (Action.TIME, Action.RETIME):
            planned = Planned(audio, action, lines=("la la",))
            with self.subTest(action=action):
                self.assertEqual(
                    sy_plan.listed_by_hand(planned, listed, kept),
                    Planned(audio, action, lines=("la la",), transcript="heard"),
                )

    def test_a_record_never_turns_a_by_hand_skip(self) -> None:
        audio = Path("/m/A.mp3")
        planned = Planned(audio, Action.TIME, lines=("la la",))
        kept = {str(audio): HeardRecord(audio, text_fingerprint("lo lo"), "heard")}
        self.assertEqual(
            sy_plan.listed_by_hand(planned, {str(audio): Action.SKIP_BY_HAND}, kept),
            Planned(audio, Action.SKIP_BY_HAND),
        )

    def test_an_unlisted_track_with_a_record_is_planned_with_its_transcript(
        self,
    ) -> None:
        audio = Path("/m/A.mp3")
        kept = {str(audio): HeardRecord(audio, text_fingerprint("la la"), "heard")}
        for action in (Action.TIME, Action.RETIME):
            planned = Planned(audio, action, lines=("la la",))
            with self.subTest(action=action):
                self.assertEqual(
                    sy_plan.listed_by_hand(planned, {}, kept),
                    Planned(audio, action, lines=("la la",), transcript="heard"),
                )
