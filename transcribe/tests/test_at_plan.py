"""Each file's action from what is at its output location (transcribe.md, What happens to each file)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import at_plan
from unittest import mock

from at_types import MACHINE_BY_LINE, Action, FileResult, Outcome, Planned
from tests._isolation import IsolatedCase

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

OURS = f"[re:lyricsmith transcribe 0.1.0; whisper.cpp 1.9.5; ggml-large-v3-turbo]\n{MACHINE_BY_LINE}\n[00:00.26] An invented example line.\n"
HAND = "[ar:someone]\n[00:01.00] An invented hand-made line.\n"
#: os.geteuid does not exist on Windows; reading it this way keeps mypy's win32 pass clean.
_GETEUID = getattr(os, "geteuid", None)


class ChooseActionTest(unittest.TestCase):
    def test_every_sidecar_state_maps_to_its_action(self) -> None:
        rows = [
            (None, False, False, Action.TRANSCRIBE),
            (None, False, True, Action.TRANSCRIBE),
            (OURS, False, False, Action.SKIPPED),
            (OURS, True, False, Action.SKIPPED),
            (OURS, False, True, Action.OVERWRITE),
            (OURS, True, True, Action.OVERWRITE),
            (HAND, False, False, Action.SKIPPED),
            (HAND, True, True, Action.REFUSED),
            (None, True, False, Action.SKIPPED),
            (None, True, True, Action.REFUSED),
        ]
        for lrc, txt, overwrite, expected in rows:
            with self.subTest(lrc=lrc, txt=txt, overwrite=overwrite):
                self.assertIs(at_plan.choose_action(lrc, txt, overwrite), expected)


class IsOursTest(unittest.TestCase):
    def test_the_line_in_the_header_is_ours(self) -> None:
        self.assertTrue(at_plan.is_ours(OURS))
        self.assertTrue(at_plan.is_ours(f"  {MACHINE_BY_LINE} \r\n[00:00.00] x\n"))

    def test_the_line_after_the_first_timestamp_is_not(self) -> None:
        self.assertFalse(at_plan.is_ours(f"[00:00.00] x\n{MACHINE_BY_LINE}\n"))

    def test_other_by_lines_are_not(self) -> None:
        self.assertFalse(at_plan.is_ours("[by:whisper.cpp]\n[00:00.00] x\n"))
        self.assertFalse(
            at_plan.is_ours("[by:machine transcript, edited]\n[00:00.00] x\n")
        )
        self.assertFalse(at_plan.is_ours(HAND))
        self.assertFalse(at_plan.is_ours(""))

    @given(st.text())
    def test_fuzz_is_ours(self, text: str) -> None:
        self.assertTrue(at_plan.is_ours(f"{MACHINE_BY_LINE}\n{text}"))
        self.assertFalse(at_plan.is_ours(f"[00:00.00] x\n{text}"))


class ReadAndPlanTest(IsolatedCase):
    def test_read_lrc_absent_bom_and_bad_bytes(self) -> None:
        path = self.root / "a.lrc"
        self.assertIsNone(at_plan.read_lrc(path))
        path.write_bytes(b"\xef\xbb\xbf" + MACHINE_BY_LINE.encode() + b"\n\xff\n")
        text = at_plan.read_lrc(path)
        self.assertIsNotNone(text)
        assert text is not None
        self.assertTrue(text.startswith(MACHINE_BY_LINE))
        self.assertIn("�", text)

    @unittest.skipIf(
        _GETEUID is None or _GETEUID() == 0,
        "needs POSIX permissions as a non-root user",
    )
    def test_an_unreadable_lrc_is_not_ours(self) -> None:
        path = self.root / "a.lrc"
        path.write_text(OURS, encoding="utf-8", newline="\n")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        self.assertEqual(at_plan.read_lrc(path), "")

    def test_plan_files_beside_the_audio(self) -> None:
        root = self.root / "lib"
        root.mkdir()
        fresh = root / "fresh.mp3"
        done = root / "done.mp3"
        for audio in (fresh, done):
            audio.write_bytes(b"")
        (root / "done.lrc").write_text(OURS, encoding="utf-8", newline="\n")
        planned = at_plan.plan_files([done, fresh], root, None, overwrite=True)
        self.assertEqual(
            planned,
            [
                Planned(done, root / "done.lrc", root / "done.txt", Action.OVERWRITE),
                Planned(
                    fresh, root / "fresh.lrc", root / "fresh.txt", Action.TRANSCRIBE
                ),
            ],
        )

    def test_plan_files_checks_only_the_mirrored_location(self) -> None:
        root = self.root / "lib"
        root.mkdir()
        audio = root / "one.mp3"
        audio.write_bytes(b"")
        (root / "one.lrc").write_text(HAND, encoding="utf-8", newline="\n")
        out = self.root / "out"
        [planned] = at_plan.plan_files([audio], root, out, overwrite=False)
        self.assertEqual(planned.action, Action.TRANSCRIBE)
        self.assertEqual(planned.lrc, out / "one.lrc")


class ClaimOutputsTest(unittest.TestCase):
    def test_a_later_claimant_of_one_lrc_is_failed_case_folded(self) -> None:
        root = Path("lib")
        first = Planned(
            root / "a" / "one.mp3",
            root / "a" / "one.lrc",
            root / "a" / "one.txt",
            Action.TRANSCRIBE,
        )
        # A case-only difference still claims the same .lrc on a case-insensitive disk.
        second = Planned(
            root / "a" / "One.MP3",
            root / "a" / "One.lrc",
            root / "a" / "One.txt",
            Action.TRANSCRIBE,
        )
        other = Planned(
            root / "b" / "two.mp3",
            root / "b" / "two.lrc",
            root / "b" / "two.txt",
            Action.SKIPPED,
        )
        earlier = FileResult(root / "c", Outcome.FAILED, ("could not read",))
        self.assertEqual(
            at_plan.claim_outputs(
                [earlier, first, other, second], root, case_insensitive=lambda _: True
            ),
            [
                earlier,
                first,
                other,
                FileResult(
                    second.audio,
                    Outcome.FAILED,
                    (
                        f"another audio file, {Path('a') / 'one.mp3'}, "
                        "already writes this .lrc",
                    ),
                ),
            ],
        )

    def test_a_claimant_outside_the_root_is_named_whole(self) -> None:
        first = Planned(
            Path("x/one.mp3"), Path("x/one.lrc"), Path("x/one.txt"), Action.TRANSCRIBE
        )
        second = Planned(
            Path("x/one.m4a"), Path("x/one.lrc"), Path("x/one.txt"), Action.TRANSCRIBE
        )
        [_, failed] = at_plan.claim_outputs([first, second], Path("elsewhere"))
        assert isinstance(failed, FileResult)
        self.assertIn(str(Path("x/one.mp3")), failed.detail[0])


class CaseSensitiveClaimTest(unittest.TestCase):
    def test_a_case_only_twin_keeps_its_pair_on_a_case_sensitive_destination(
        self,
    ) -> None:
        # Regression: the key was always case-folded, so A.mp3 failed beside a.mp3
        # even where A.lrc and a.lrc are two files.
        root = Path("lib")
        first = Planned(
            root / "a.mp3", root / "a.lrc", root / "a.txt", Action.TRANSCRIBE
        )
        second = Planned(
            root / "A.mp3", root / "A.lrc", root / "A.txt", Action.TRANSCRIBE
        )
        probed: list[Path] = []

        def sensitive(folder: Path) -> bool:
            probed.append(folder)
            return False

        self.assertEqual(
            at_plan.claim_outputs([first, second], root, case_insensitive=sensitive),
            [first, second],
        )
        self.assertEqual(probed, [root])

    def test_an_exact_twin_fails_without_a_probe(self) -> None:
        root = Path("lib")
        first = Planned(
            root / "a.mp3", root / "a.lrc", root / "a.txt", Action.TRANSCRIBE
        )
        second = Planned(
            root / "a.m4a", root / "a.lrc", root / "a.txt", Action.TRANSCRIBE
        )

        def never(folder: Path) -> bool:
            raise AssertionError(f"probed {folder}")

        [_, failed] = at_plan.claim_outputs(
            [first, second], root, case_insensitive=never
        )
        self.assertIsInstance(failed, FileResult)


class CaseProbeTest(IsolatedCase):
    """The probe asks os.path.samefile, faked here: this disk's own answer is never used."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.root / "Lib"
        self.folder.mkdir()
        self.calls: list[tuple[Path, Path]] = []

    def answering(self, answer: bool | OSError) -> at_plan.SameFile:
        def samefile(first: Path, second: Path) -> bool:
            self.calls.append((Path(first), Path(second)))
            if isinstance(answer, OSError):
                raise answer
            return answer

        return samefile

    def test_a_folder_that_is_its_own_case_twin_is_case_insensitive(self) -> None:
        probe = self.answering(True)
        self.assertTrue(at_plan.is_case_insensitive(self.folder, samefile=probe))
        self.assertEqual(self.calls, [(self.folder, self.root / "lIB")])

    def test_a_missing_twin_is_case_sensitive(self) -> None:
        missing = FileNotFoundError(2, "No such file or directory")
        probe = self.answering(missing)
        self.assertFalse(at_plan.is_case_insensitive(self.folder, samefile=probe))

    def test_a_distinct_twin_is_case_sensitive(self) -> None:
        probe = self.answering(False)
        self.assertFalse(at_plan.is_case_insensitive(self.folder, samefile=probe))

    def test_an_unreadable_twin_folds_as_the_safe_answer(self) -> None:
        probe = self.answering(PermissionError(13, "Permission denied"))
        self.assertTrue(at_plan.is_case_insensitive(self.folder, samefile=probe))

    def test_a_missing_or_uncased_folder_asks_its_nearest_cased_ancestor(
        self,
    ) -> None:
        # An --output-dir mirror folder is created only later, and a name such as
        # "2024" has no case to swap.
        probe = self.answering(True)
        folder = self.folder / "2024" / "Not Yet"
        (self.folder / "2024").mkdir()
        self.assertTrue(at_plan.is_case_insensitive(folder, samefile=probe))
        self.assertEqual(self.calls, [(self.folder, self.root / "lIB")])

    def test_no_cased_ancestor_folds_as_the_safe_answer(self) -> None:
        probe = self.answering(False)
        self.assertTrue(at_plan.is_case_insensitive(Path("1") / "2", samefile=probe))
        self.assertEqual(self.calls, [])


class PresentLinkTest(IsolatedCase):
    def test_a_dangling_link_is_present_never_planned_as_transcribe(self) -> None:
        root = self.root / "lib"
        root.mkdir()
        audio = root / "one.mp3"
        audio.write_bytes(b"")
        try:
            os.symlink(root / "gone.lrc", root / "one.lrc")
            os.symlink(root / "gone.txt", root / "one.txt")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        [kept] = at_plan.plan_files([audio], root, None, overwrite=False)
        self.assertIs(kept.action, Action.SKIPPED)
        [refused] = at_plan.plan_files([audio], root, None, overwrite=True)
        self.assertIs(refused.action, Action.REFUSED)

    def test_a_folder_named_like_the_lrc_is_present(self) -> None:
        # Reaches the lexists branch of read_lrc wherever symlinks are unavailable.
        root = self.root / "lib"
        (root / "one.lrc").mkdir(parents=True)
        self.assertEqual(at_plan.read_lrc(root / "one.lrc"), "")

    def test_a_non_regular_lrc_is_never_opened(self) -> None:
        # Regression: a FIFO blocks in open() and /dev/zero never ends, so a name
        # that exists but is not a regular file must not be read, even in a dry run.
        lrc = self.root / "one.lrc"
        with (
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(at_plan.os.path, "lexists", return_value=True),
            mock.patch.object(
                Path, "open", side_effect=AssertionError("opened")
            ) as opened,
        ):
            self.assertEqual(at_plan.read_lrc(lrc), "")
        opened.assert_not_called()

    def test_a_txt_that_lexists_only_is_present(self) -> None:
        # Reaches plan_files' lexists branch without a real symlink.
        root = self.root / "lib"
        root.mkdir()
        audio = root / "one.mp3"
        audio.write_bytes(b"")
        txt = root / "one.txt"
        real = os.path.lexists
        with mock.patch.object(
            at_plan.os.path,
            "lexists",
            side_effect=lambda path: Path(path) == txt or real(path),
        ):
            [planned] = at_plan.plan_files([audio], root, None, overwrite=True)
        self.assertIs(planned.action, Action.REFUSED)


class SidecarReadCapTest(IsolatedCase):
    """A planted sidecar is read only as far as the cap, never whole."""

    def sparse_lrc(self, size: int, tail: str) -> Path:
        """A .lrc of *size* NUL bytes, sparse where the disk allows, then *tail*."""
        lrc = self.root / "one.lrc"
        lrc.touch()
        os.truncate(lrc, size)
        with lrc.open("ab") as stream:
            stream.write(tail.encode("utf-8"))
        return lrc

    def test_a_by_line_past_the_cap_is_not_ours(self) -> None:
        lrc = self.sparse_lrc(4 * at_plan.LRC_READ_CAP_BYTES, f"\n{MACHINE_BY_LINE}\n")
        text = at_plan.read_lrc(lrc)
        self.assertIsNotNone(text)
        self.assertLessEqual(len(text or ""), at_plan.LRC_READ_CAP_BYTES)
        self.assertFalse(at_plan.is_ours(text or ""))

    def test_a_long_ours_lrc_is_still_ours(self) -> None:
        body = "[00:00.26] An invented example line.\n" * (
            at_plan.LRC_READ_CAP_BYTES // 20
        )
        lrc = self.root / "one.lrc"
        lrc.write_text(OURS + body, encoding="utf-8", newline="\n")
        self.assertTrue(at_plan.is_ours(at_plan.read_lrc(lrc) or ""))

    def test_a_sidecar_far_past_the_cap_is_planned_as_present_not_ours(
        self,
    ) -> None:
        # 64 times the cap: far past it, yet only 4 MiB on a disk without sparse files.
        self.sparse_lrc(64 * at_plan.LRC_READ_CAP_BYTES, f"\n{MACHINE_BY_LINE}\n")
        audio = self.root / "one.mp3"
        audio.write_bytes(b"")
        [planned] = at_plan.plan_files([audio], self.root, None, overwrite=True)
        self.assertIs(planned.action, Action.REFUSED)
