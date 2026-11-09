"""Each track's planned action, read from its sidecars by album's own rules."""

from __future__ import annotations

import errno
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
import sy_plan  # noqa: E402 - import follows the sys.path bootstrap above
from sy_types import Action, Label, Planned  # noqa: E402 - follows the bootstrap

fixtures = sy_cases.fixtures
OURS = sy_lrc.lrc_text(Label("0", "0", "0"), ("A gull on the railing",), (1.0,))


class PlanTest(sy_cases.IsolatedCase):
    def plan(
        self, *, overwrite: bool = False, lrc: str | None = None, txt: str | None = None
    ) -> sy_plan.Planned:
        """The plan for one invented track holding the *lrc* and *txt* sidecars given."""
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=lrc, txt=txt)
        (audio,) = fixtures.build_album(self.tmp, (track,))
        return sy_plan.plan_track(audio, overwrite=overwrite)

    def test_a_txt_alone_is_timed_with_its_lines(self) -> None:
        planned = self.plan(txt="\nla la\n\nlo lo\n\n")
        self.assertEqual(
            (planned.action, planned.lines), (Action.TIME, ("la la", "", "lo lo"))
        )

    def test_our_lrc_keeps_its_blank_lines_through_a_re_time(self) -> None:
        lines = ("A gull", "", "on the railing")
        spaced = sy_lrc.lrc_text(Label("0", "0", "0"), lines, (1.0, 2.0, 2.0))
        planned = self.plan(lrc=spaced, overwrite=True)
        self.assertEqual((planned.action, planned.lines), (Action.RETIME, lines))

    def test_blank_lines_alone_are_still_no_text(self) -> None:
        self.assertIs(self.plan(txt="\n  \n\n").action, Action.SKIP_NO_TEXT)

    def test_a_timed_lrc_is_skipped(self) -> None:
        self.assertIs(self.plan(lrc=fixtures.LIGHTHOUSE_LRC).action, Action.SKIP_TIMED)

    def test_our_lrc_is_skipped_without_overwrite(self) -> None:
        self.assertIs(self.plan(lrc=OURS).action, Action.SKIP_TIMED)

    def test_our_lrc_is_re_timed_from_its_own_lines_under_overwrite(self) -> None:
        planned = self.plan(lrc=OURS, overwrite=True)
        expected = (Action.RETIME, ("A gull on the railing",))
        self.assertEqual((planned.action, planned.lines), expected)

    def test_our_lrc_with_nothing_alignable_is_no_text_under_overwrite(self) -> None:
        bare = sy_lrc.lrc_text(Label("0", "0", "0"), ("4 4", "1 2 3"), (1.0, 2.0))
        planned = self.plan(lrc=bare, overwrite=True)
        self.assertIs(planned.action, Action.SKIP_NO_TEXT)

    def test_someone_elses_lrc_is_never_re_timed(self) -> None:
        edited = OURS.replace("machine timed", "me")
        self.assertIs(self.plan(lrc=edited, overwrite=True).action, Action.SKIP_TIMED)

    def test_an_untimed_lrc_is_left_for_album(self) -> None:
        self.assertIs(self.plan(lrc="just words\n").action, Action.SKIP_UNTIMED_LRC)

    def test_no_sidecar_or_nothing_alignable_is_no_text(self) -> None:
        self.assertIs(self.plan().action, Action.SKIP_NO_TEXT)
        digits = sy_plan.plan_track(self._digits(), overwrite=False)
        self.assertIs(digits.action, Action.SKIP_NO_TEXT)

    def test_a_txt_that_is_not_utf8_is_an_error_naming_it(self) -> None:
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0)
        (audio,) = fixtures.build_album(self.tmp, (track,))
        audio.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa")
        planned = sy_plan.plan_track(audio, overwrite=False)
        self.assertIs(planned.action, Action.ERROR_TEXT)
        self.assertIn("01 A.txt", planned.detail)

    @unittest.skipUnless(sy_cases.FILE_LOCKS, sy_cases.NO_MODE_000_LOCK)
    def test_a_txt_that_cannot_be_opened_is_an_error_naming_it(self) -> None:
        sidecar = self.locked_txt()
        sidecar.chmod(0)
        self.addCleanup(sidecar.chmod, 0o600)
        self.assert_refused_txt(sidecar)

    def test_a_txt_whose_open_is_refused_is_an_error_naming_it(self) -> None:
        # The portable twin of the mode-000 test: the open is refused directly, so the
        # row is proven where chmod cannot lock a file.
        sidecar = self.locked_txt()
        refused = sy_cases.permission_denied(sidecar)
        with mock.patch.object(sy_plan, "plan_sidecars", side_effect=refused):
            self.assert_refused_txt(sidecar)

    def locked_txt(self) -> Path:
        """The .txt of a one-track album, built for a refused-open case."""
        (audio,) = fixtures.build_album(
            self.tmp, (fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="la\n"),)
        )
        return audio.with_suffix(".txt")

    def assert_refused_txt(self, sidecar: Path) -> None:
        """Planning *sidecar*'s track is an error naming it and the refusal."""
        planned = sy_plan.plan_track(sidecar.with_suffix(".mp3"), overwrite=False)
        expected = f"01 A.txt: {os.strerror(errno.EACCES)}"
        self.assertEqual(
            (planned.action, planned.detail), (Action.ERROR_TEXT, expected)
        )

    def test_an_open_error_without_a_file_name_names_the_txt(self) -> None:
        audio = self.tmp / "01 A.mp3"
        refused = OSError(errno.EIO, os.strerror(errno.EIO))
        with mock.patch.object(sy_plan, "plan_sidecars", side_effect=refused):
            planned = sy_plan.plan_track(audio, overwrite=False)
        self.assertEqual(planned.detail, f"01 A.txt: {os.strerror(errno.EIO)}")

    def test_our_lrc_behind_a_link_is_never_re_timed(self) -> None:
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0)
        (audio,) = fixtures.build_album(self.tmp, (track,))
        kept = self.tmp / "kept.lrc"
        kept.write_text(OURS, encoding="utf-8", newline="\n")
        audio.with_suffix(".lrc").symlink_to(kept.name)
        planned = sy_plan.plan_track(audio, overwrite=True)
        self.assertIs(planned.action, Action.SKIP_TIMED)

    def test_a_link_at_the_lrc_name_is_never_timed_over(self) -> None:
        track = fixtures.TrackSpec("01 A.mp3", seconds=1.0, txt="la la\n")
        (audio,) = fixtures.build_album(self.tmp, (track,))
        audio.with_suffix(".lrc").symlink_to("gone.lrc")
        planned = sy_plan.plan_track(audio, overwrite=False)
        expected = (Action.ERROR_TEXT, "01 A.lrc is a link: never replaced")
        self.assertEqual((planned.action, planned.detail), expected)

    def test_names_differing_only_by_case_in_one_folder_share_a_lrc(self) -> None:
        first = Planned(self.tmp / "01 A.mp3", Action.TIME)
        second = Planned(self.tmp / "01 a.flac", Action.TIME)
        kept = sy_plan.claim_lrcs((first, second))
        self.assertEqual(kept[0], first)
        expected = (Action.ERROR_TEXT, "another track already claims 01 a.lrc")
        self.assertEqual((kept[1].action, kept[1].detail), expected)

    def test_folders_differing_only_by_case_are_distinct(self) -> None:
        plans = (
            Planned(self.tmp / "Album" / "01 A.mp3", Action.TIME),
            Planned(self.tmp / "album" / "01 A.mp3", Action.TIME),
        )
        self.assertEqual(sy_plan.claim_lrcs(plans), list(plans))

    def _digits(self) -> Path:
        """A track whose .txt holds digits only, in its own subfolder."""
        folder = self.tmp / "digits"
        track = fixtures.TrackSpec("01 D.mp3", seconds=1.0, txt="1 2 3\n")
        (audio,) = fixtures.build_album(folder, (track,))
        return audio
