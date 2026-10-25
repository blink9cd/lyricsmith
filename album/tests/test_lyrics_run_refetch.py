"""`--mode run --refetch-untimed`: an untimed-only track is asked of the providers again.

A track holding untimed lyrics alone (a ``.txt`` or a USLT frame, and no worded SYLT
frame or timed ``.lrc``) is not served from them: the providers are asked in the usual
order, and an answer is accepted however unlike the old lyrics it is, so the check's
unlike-backup row is what shows the two differ. With no answer a track whose old lyrics
are plausible is left byte for byte as it was, and says so. A synced track and a track with no lyrics run as
they would without the option. Each test drives the entry point with its real wiring;
only the clock and the provider table are faked, and every lyric is invented.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above

REFETCH = "--refetch-untimed"
SYNCED_KEY = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
PLAIN_KEY = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
#: The wrong lyrics the keeper holds: a different song from every answer.
WRONG_TXT = fixtures.ORCHARD_PLAIN + "\n"
WRONG_USLT = (tag_builders.uslt(fixtures.ORCHARD_PLAIN),)
#: Synced frames holding words: a track already synced.
SYLT = (tag_builders.sylt((1000, "A lantern swings"), (4000, "over the quay")),)
#: The same entries timed in MPEG frames: still a worded SYLT, so not untimed-only.
SYLT_MPEG = (
    tag_builders.sylt(
        (1000, "A lantern swings"), (4000, "over the quay"), time_format=1
    ),
)
#: A timed sidecar holding words.
TIMED_LRC = "[00:01.00]A lantern swings\n[00:04.00]over the quay\n"
#: What a track with no answer to the refetch reports.
NO_ANSWER = "kept existing: no answer to the refetch"
KEEPER = "01 Keeper.mp3"
MILL = TrackSpec("02 Mill.mp3", title="The Mill")


class RefetchCase(FreshCliCase):
    """A keeper with the given lyrics, then a mill with none, and a refetch run."""

    def answer_both(
        self, *, synced: Mapping[str, str], plain: Mapping[str, str]
    ) -> None:
        """Answer each title in *synced* synced, and each in *plain* untimed."""
        self.titles = {
            SYNCED_KEY: run_fakes.TitleProvider(synced),
            PLAIN_KEY: run_fakes.TitleProvider(plain),
        }
        self.providers = self.titles

    def keeper(
        self,
        frames: tuple[lm.FrameRecord, ...] = (),
        *,
        lrc: str | None = None,
        txt: str | None = None,
    ) -> list[Path]:
        """The keeper with *frames* and the sidecars given, then the mill."""
        track = TrackSpec(KEEPER, title="The Keeper", lrc=lrc, txt=txt)
        paths = fixtures.build_album(self.album, (track, MILL))
        if frames:
            tag_builders.plant_frames(paths[0], frames)
        return paths

    def refetch(self, *extra: str) -> tuple[str, lr.TrackOutcome, lr.TrackOutcome]:
        """Run with the option: the keeper's summary row and both outcomes."""
        result = self.run_album(REFETCH, *extra)
        self.assertIn(
            result.code,
            (album.EXIT_OK, album.EXIT_TRACK_ERRORS),
            result.stderr,
        )
        self.stderr = result.stderr
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        keeper, mill = self.afters()
        assert keeper is not None and mill is not None
        return row, keeper, mill

    def keeper_files(self, paths: list[Path]) -> dict[str, bytes]:
        """Every file of the keeper's, by name."""
        return {k: v for k, v in raw(paths).items() if k.startswith("01 Keeper")}


class RefetchAnswerTest(RefetchCase):
    """An untimed-only track takes the providers' answer, however unlike the old one."""

    def test_a_wrong_txt_and_uslt_are_replaced_and_flagged(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        row, keeper, _mill = self.refetch()
        self.assertEqual(
            (keeper.status, keeper.source, keeper.kind),
            (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED),
        )
        self.assertTrue(keeper.refetch_untimed)
        self.assertIn("FLAG: unlike backup (similarity ", row)
        text = paths[0].with_suffix(".txt").read_text(encoding="utf-8")
        self.assertEqual(text, fixtures.LIGHTHOUSE_PLAIN)
        self.assertNotIn(WRONG_USLT[0], rb.lyrics_frames(paths[0]))
        # The timed steps were asked first, then the untimed one.
        self.assertEqual(
            [q.title for q in self.titles[SYNCED_KEY].calls], ["The Keeper", "The Mill"]
        )
        self.assertIn("The Keeper", [q.title for q in self.titles[PLAIN_KEY].calls])

    def test_without_the_option_the_wrong_txt_still_serves(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertFalse(keeper.refetch_untimed)
        self.assertNotEqual(keeper.source, lm.Source.LRCLIB)
        self.assertEqual(
            paths[0].with_suffix(".txt").read_text(encoding="utf-8"), WRONG_TXT
        )

    def test_a_synced_answer_upgrades_the_track(self) -> None:
        self.answer_both(synced={"The Keeper": fixtures.LIGHTHOUSE_LRC}, plain={})
        paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        _row, keeper, _mill = self.refetch()
        self.assertEqual(
            (keeper.status, keeper.kind),
            (lr.OutcomeStatus.WRITTEN, lm.LyricsKind.SYNCED),
        )
        self.assertEqual(
            sorted(self.keeper_files(paths)), ["01 Keeper.lrc", "01 Keeper.mp3"]
        )

    def test_a_txt_alone_is_replaced_and_flagged(self) -> None:
        # No lyrics frames in the backup: the check compares with the backed-up .txt.
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(txt=WRONG_TXT)
        row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertIn("FLAG: unlike backup (similarity ", row)
        text = paths[0].with_suffix(".txt").read_text(encoding="utf-8")
        self.assertEqual(text, fixtures.LIGHTHOUSE_PLAIN)

    def test_an_untimed_lrc_is_untimed_only(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(lrc=WRONG_TXT)
        _row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(
            sorted(self.keeper_files(paths)), ["01 Keeper.mp3", "01 Keeper.txt"]
        )


class RefetchNoAnswerTest(RefetchCase):
    """With no answer an untimed-only track is left exactly as it was."""

    def test_the_track_is_byte_identical_and_says_why(self) -> None:
        paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        before = self.keeper_files(paths)
        row, keeper, mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertTrue(keeper.refetch_untimed)
        self.assertEqual(
            (keeper.frames_written, keeper.sidecars_removed, keeper.sidecar_written),
            (False, (), None),
        )
        self.assertTrue(row.endswith(NO_ANSWER), row)
        self.assertIn(f"01 Keeper.mp3: {NO_ANSWER}", self.stderr)
        self.assertEqual(mill.status, lr.OutcomeStatus.NOT_FOUND)
        self.assertFalse(mill.refetch_untimed)

    def test_untimed_frames_alone_are_kept(self) -> None:
        paths = self.keeper(WRONG_USLT)
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertTrue(row.endswith(NO_ANSWER), row)
        self.assertEqual(keeper.status, lr.OutcomeStatus.KEPT_EXISTING)

    def test_an_untimed_lrc_is_not_renamed(self) -> None:
        paths = self.keeper(lrc=WRONG_TXT)
        before = self.keeper_files(paths)
        _row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertEqual(keeper.sidecars_renamed, ())

    def test_every_provider_failing_is_the_fetch_error(self) -> None:
        failing = fixtures.raising(OSError("refused"))
        self.providers = {SYNCED_KEY: failing, PLAIN_KEY: failing}
        paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        assert keeper.error is not None
        self.assertEqual(keeper.error.stage, lr.ErrorStage.FETCH)
        self.assertIn("ERROR fetch", row)


#: More characters than any sidecar may hold, in bytes a backup still copies.
_OVER_CAP = "la " * (lr.MAX_LYRICS_CHARS // 3 + 1)


class RefetchUnreadableTest(RefetchCase):
    """A .txt the run cannot read is replaced by an answer; one not UTF-8 kept without.

    One too large to read is implausible, and removed without an answer: that is
    `test_lyrics_run_refetch_remove`'s.
    """

    def test_an_over_cap_txt_is_replaced_by_an_answer(self) -> None:
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        paths = self.keeper(txt=_OVER_CAP)
        _row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertIsNotNone(keeper.sidecar_unreadable)
        text = paths[0].with_suffix(".txt").read_text(encoding="utf-8")
        self.assertEqual(text, fixtures.LIGHTHOUSE_PLAIN)

    def test_an_undecodable_txt_with_no_answer_is_kept_and_named(self) -> None:
        paths = self.keeper()
        paths[0].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        before = self.keeper_files(paths)
        row, keeper, _mill = self.refetch()
        self.assertEqual(self.keeper_files(paths), before)
        self.assertIn(f"{NO_ANSWER}; 01 Keeper.txt is not UTF-8", row)
        self.assertEqual(keeper.sidecar_unreadable, row.split("; ", 1)[1])

    def test_an_undecodable_txt_beside_stamps_alone_is_untimed_only(self) -> None:
        paths = self.keeper(lrc="[00:01.00]\n[00:05.00]\n")
        paths[0].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        row, _keeper, _mill = self.refetch()
        self.assertIn(f"{NO_ANSWER}; 01 Keeper.txt is not UTF-8", row)

    def test_an_undecodable_lrc_is_the_sidecar_error_as_ever(self) -> None:
        paths = self.keeper(txt=WRONG_TXT)
        paths[0].with_suffix(".lrc").write_bytes(b"[00:01.00]caf\xe9\n")
        row, keeper, _mill = self.refetch()
        self.assertIn("ERROR sidecar", row)
        self.assertFalse(keeper.refetch_untimed)

    def test_without_the_option_an_undecodable_txt_is_the_sidecar_error(self) -> None:
        paths = self.keeper()
        paths[0].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR sidecar", result.stdout)


class RefetchLeavesOthersTest(RefetchCase):
    """A synced track and a track with no lyrics run as they would without it."""

    def setUp(self) -> None:
        """Every title answered, so a track asked would show it."""
        super().setUp()
        self.answer_both(
            synced={
                "The Keeper": fixtures.LIGHTHOUSE_LRC,
                "The Mill": fixtures.LIGHTHOUSE_LRC,
            },
            plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN},
        )

    def assert_served_from_its_own(self, paths: list[Path], source: lm.Source) -> None:
        """Refetch: the keeper served from *source*, never asked, the mill written."""
        _row, keeper, mill = self.refetch()
        self.assertEqual(keeper.source, source)
        self.assertFalse(keeper.refetch_untimed)
        self.assertEqual([q.title for q in self.titles[SYNCED_KEY].calls], ["The Mill"])
        self.assertEqual(mill.status, lr.OutcomeStatus.WRITTEN)
        self.assertFalse(mill.refetch_untimed)
        self.assertTrue(paths[1].with_suffix(".lrc").is_file())

    def test_a_worded_sylt_beside_a_wrong_txt_is_synced(self) -> None:
        paths = self.keeper(SYLT, txt=WRONG_TXT)
        self.assert_served_from_its_own(paths, lm.Source.FRAMES)

    def test_a_sylt_timed_in_mpeg_frames_is_not_untimed_only(self) -> None:
        # As in any run: the timed steps may upgrade it, but its untimed lyrics are
        # never traded for an untimed answer, so the untimed step is not asked.
        self.keeper(SYLT_MPEG + WRONG_USLT)
        _row, keeper, _mill = self.refetch()
        self.assertFalse(keeper.refetch_untimed)
        self.assertEqual(keeper.kind, lm.LyricsKind.SYNCED)
        self.assertEqual(self.titles[PLAIN_KEY].calls, [])

    def test_a_timed_lrc_is_synced(self) -> None:
        paths = self.keeper(WRONG_USLT, lrc=TIMED_LRC)
        self.assert_served_from_its_own(paths, lm.Source.SIDECAR)
