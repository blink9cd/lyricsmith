"""The hearing's plan: each track's kind from its sidecars, the kinds asked, the skips, resume."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_hear_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_hearing_log as hl  # noqa: E402 - import follows the sys.path bootstrap
import sy_hear_plan as hp  # noqa: E402 - import follows the sys.path bootstrap above
import sy_lrc  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_changed import format_changed_list  # noqa: E402 - follows the bootstrap
from lyrics_heard import text_fingerprint  # noqa: E402 - follows the bootstrap
from sy_hear_cases import LINES, provider_lrc  # noqa: E402 - follows the bootstrap
from sy_types import Label, SetupError  # noqa: E402 - follows the bootstrap above

fixtures = sy_hear_cases.fixtures
TURBO, LARGE = at_models.WHISPER_MODEL_NAMES
EVERY_KIND = frozenset(hl.HeardKind)
MACHINE_LRC = sy_lrc.lrc_text(Label("0", "0", "0"), LINES, (1.0, 2.0))


class PlanCase(sy_hear_cases.HearCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.provider, self.machine, self.untimed, self.bare, self.empty = self.album(
            fixtures.TrackSpec("01 A.mp3", seconds=1.0, lrc=provider_lrc(LINES)),
            fixtures.TrackSpec("02 B.mp3", seconds=1.0, lrc=MACHINE_LRC),
            fixtures.TrackSpec("03 C.mp3", seconds=1.0, txt="\n".join(LINES)),
            fixtures.TrackSpec("04 D.mp3", seconds=1.0),
            fixtures.TrackSpec("05 E.mp3", seconds=1.0, txt="[Chorus]\n"),
        )

    def plans(
        self,
        kinds: frozenset[hl.HeardKind] = EVERY_KIND,
        *,
        model: str = TURBO,
        listed: frozenset[str] | None = None,
        path: Path | None = None,
    ) -> dict[Path, hp.HearPlan]:
        """Each track's plan over *path*, by default the library, by its path."""
        heard = hp.read_log(self.log)
        selection = hp.Selection(kinds, model, heard.by_track, listed)
        found = hp.plan_hearings(path or self.library, frozenset({"mp3"}), selection)
        return {plan.audio: plan for plan in found}


class KindsTest(PlanCase):
    def test_each_kind_is_heard_with_its_text(self) -> None:
        plans = self.plans()
        for audio, kind in (
            (self.provider, hl.HeardKind.PROVIDER_TIMED),
            (self.machine, hl.HeardKind.MACHINE_TIMED),
            (self.untimed, hl.HeardKind.UNTIMED),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(
                    plans[audio], hp.HearPlan(audio, hp.HearAction.HEAR, kind, LINES)
                )

    def test_by_default_only_a_providers_timed_text_is_heard(self) -> None:
        plans = self.plans(frozenset({hl.HeardKind.PROVIDER_TIMED}))
        self.assertIs(plans[self.provider].action, hp.HearAction.HEAR)
        for audio, kind in ((self.machine, "machine-timed"), (self.untimed, "untimed")):
            with self.subTest(kind=kind):
                plan = plans[audio]
                self.assertEqual(
                    (plan.action, plan.detail), (hp.HearAction.SKIP_KIND, kind)
                )

    def test_a_track_with_no_sidecar_text_is_skipped_as_frames_only(self) -> None:
        self.assertIs(self.plans()[self.bare].action, hp.HearAction.SKIP_FRAMES_ONLY)

    def test_a_text_with_no_words_is_skipped(self) -> None:
        self.assertIs(self.plans()[self.empty].action, hp.HearAction.SKIP_NO_TEXT)

    def test_a_sidecar_that_cannot_be_read_is_an_error_naming_it(self) -> None:
        self.untimed.with_suffix(".txt").write_bytes(b"\xc3\x28 not UTF-8\n")
        plan = self.plans()[self.untimed]
        self.assertIs(plan.action, hp.HearAction.ERROR_TEXT)
        self.assertIn("03 C.txt", plan.detail)

    def test_a_sidecar_that_cannot_be_opened_is_an_error_naming_it(self) -> None:
        # Raised through the seam, so it holds wherever a mode-000 file can be read.
        txt = self.untimed.with_suffix(".txt")
        for error, named in (
            (PermissionError(13, "Permission denied", str(txt)), "03 C.txt"),
            (OSError("no name given"), "03 C.mp3"),
        ):
            with self.subTest(named=named):
                with mock.patch.object(hp.sy_kind, "text_kind", side_effect=error):
                    plan = self.plans()[self.untimed]
                self.assertIs(plan.action, hp.HearAction.ERROR_TEXT)
                self.assertTrue(plan.detail.startswith(f"{named}: "), plan.detail)

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_a_link_the_walk_found_is_skipped(self) -> None:
        link = self.provider.with_name("06 F.mp3")
        link.symlink_to(self.provider)
        self.assertIs(self.plans()[link].action, hp.HearAction.SKIP_LINKED)

    def test_a_name_that_is_no_regular_file_is_refused_unread(self) -> None:
        # The walk's own judgment stood in, so it holds where no FIFO can be made.
        not_regular = hp.at_discover.Entry.NOT_REGULAR
        with mock.patch.object(
            hp.at_discover, "classify", return_value=not_regular
        ) as judged:
            plan = self.plans()[self.provider]
        self.assertIs(plan.action, hp.HearAction.ERROR_AUDIO)
        self.assertIn(mock.call(self.provider, named=False), judged.call_args_list)

    def test_the_walks_rows_map_to_a_hearings_read_only(self) -> None:
        with self.assertRaises(TypeError):
            hp._ENTRY_ACTIONS[hp.Action.SKIP_LINKED] = hp.HearAction.HEAR  # type: ignore[index]

    def test_an_unreadable_folder_is_an_error_row(self) -> None:
        def refuse(*args: object, **kwargs: object) -> list[Path]:
            errors = args[2]
            assert isinstance(errors, list)
            errors.append(PermissionError(13, "Permission denied", str(self.library)))
            return []

        with mock.patch.object(hp.at_discover, "find_audio", refuse):
            (plan,) = self.plans().values()
        self.assertIs(plan.action, hp.HearAction.ERROR_TEXT)
        self.assertIn("could not read the folder", plan.detail)


class ListTest(PlanCase):
    def write_list(self, *paths: str) -> Path:
        """A --tracks-from file listing *paths*, written by album's own writer."""
        data, left = format_changed_list(paths)
        self.assertEqual(left, ())
        target = self.tmp / "tracks.txt"
        target.write_bytes(data)
        return target

    def test_only_the_listed_tracks_are_planned(self) -> None:
        listed = hp.read_tracks_from(self.write_list(str(self.provider)))
        self.assertEqual(listed, (str(self.provider),))
        plans = self.plans(listed=frozenset(listed))
        self.assertEqual(list(plans), [self.provider])

    def test_a_listed_track_the_walk_does_not_find_is_an_error_row(self) -> None:
        missing = self.library / "Artist" / "Album" / "09 Z.mp3"
        plans = self.plans(listed=frozenset({str(missing), str(self.provider)}))
        self.assertEqual(list(plans), [self.provider, missing])
        self.assertIs(plans[missing].action, hp.HearAction.ERROR_AUDIO)
        self.assertIn("not found under --path", plans[missing].detail)

    def test_a_listed_track_is_logged_by_the_lists_spelling(self) -> None:
        # A --path through a "..": the walk names each track through it, the list
        # names it lexically normal, and the log must key it as the list does.
        roundabout = self.library / "Artist" / ".." / "Artist"
        listed = str(self.provider)
        plans = self.plans(listed=frozenset({listed}), path=roundabout)
        ((walked, plan),) = plans.items()
        self.assertNotEqual(str(walked), listed)
        self.assertEqual(plan.log_key, listed)
        self.assertEqual(self.plans()[self.provider].log_key, str(self.provider))

    def test_a_list_naming_the_resolved_path_matches_a_path_through_a_link(
        self,
    ) -> None:
        # sweep resolves --library, so its draw names each track under the resolved
        # folder; a --path reaching the same folder through a link must still find
        # them, and log each as the list names it, so the report finds the record.
        linked = self.tmp / "linked"
        linked.symlink_to(self.library, target_is_directory=True)
        listed = str(self.provider.resolve())
        plans = self.plans(listed=frozenset({listed}), path=linked)
        ((walked, plan),) = plans.items()
        self.assertIs(plan.action, hp.HearAction.HEAR)
        self.assertNotEqual(str(walked), listed)
        self.assertEqual(plan.log_key, listed)

    def test_a_list_that_breaks_its_format_or_cannot_be_read_is_refused(self) -> None:
        bad = self.tmp / "bad.txt"
        bad.write_bytes(b"relative/path.mp3\n")
        with self.assertRaisesRegex(SetupError, "not a track list"):
            hp.read_tracks_from(bad)
        with self.assertRaisesRegex(SetupError, "cannot be read"):
            hp.read_tracks_from(self.tmp / "missing.txt")
        folder = self.tmp / "folder"
        folder.mkdir()
        with self.assertRaisesRegex(SetupError, "cannot be read"):
            hp.read_tracks_from(folder)

    def test_a_list_over_the_cap_is_refused(self) -> None:
        target = self.write_list(str(self.provider))
        with mock.patch.object(hp, "MAX_TRACKS_FROM_BYTES", 4):
            with self.assertRaisesRegex(SetupError, "more than 4 bytes"):
                hp.read_tracks_from(target)


class ResumeTest(PlanCase):
    def log_hearing(self, audio: Path, lines: tuple[str, ...], model: str) -> None:
        """A record in the log of *audio*'s text *lines* heard by *model*."""
        record = hl.HearingRecord(
            str(audio),
            text_fingerprint(lines),
            hl.HeardKind.PROVIDER_TIMED,
            model,
            22,
            1.0,
            hl.HeardVerdict.SUNG,
            "what was heard",
        )
        hl.write_hearing(self.log, record)

    def test_a_track_heard_with_its_text_by_the_model_is_skipped(self) -> None:
        self.log_hearing(self.provider, LINES, TURBO)
        self.assertIs(self.plans()[self.provider].action, hp.HearAction.SKIP_HEARD)

    def test_a_listed_track_heard_under_the_lists_spelling_is_skipped(self) -> None:
        self.log_hearing(self.provider, LINES, TURBO)
        roundabout = self.library / "Artist" / ".." / "Artist"
        listed = frozenset({str(self.provider)})
        (plan,) = self.plans(listed=listed, path=roundabout).values()
        self.assertIs(plan.action, hp.HearAction.SKIP_HEARD)

    def test_another_models_hearing_does_not_count(self) -> None:
        self.log_hearing(self.provider, LINES, TURBO)
        plan = self.plans(model=LARGE)[self.provider]
        self.assertIs(plan.action, hp.HearAction.HEAR)

    def test_a_text_changed_since_its_hearing_is_heard_again(self) -> None:
        self.log_hearing(self.provider, ("an older text, since replaced",), TURBO)
        self.assertIs(self.plans()[self.provider].action, hp.HearAction.HEAR)

    def test_an_unreadable_log_file_is_a_problem_and_its_track_heard(self) -> None:
        bad = hl.hearing_path(self.log, str(self.provider), TURBO)
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"[]")
        read = hp.read_log(self.log)
        self.assertEqual(read.by_track, {})
        (problem,) = read.problems
        self.assertTrue(problem.startswith(f"--hearing-log: {bad}"), problem)
        self.assertIs(self.plans()[self.provider].action, hp.HearAction.HEAR)

    def test_a_log_that_is_no_folder_is_a_setup_problem(self) -> None:
        self.log.parent.mkdir(parents=True)
        self.log.write_bytes(b"")
        read = hp.read_log(self.log)
        self.assertEqual(read.setup, (f"--hearing-log is not a folder: {self.log}",))

    def test_a_log_whose_listing_fails_is_a_setup_problem(self) -> None:
        refused = PermissionError(13, "Permission denied")
        with mock.patch.object(hp, "list_hearings", side_effect=refused):
            read = hp.read_log(self.log)
        (problem,) = read.setup
        self.assertIn("cannot be read", problem)
