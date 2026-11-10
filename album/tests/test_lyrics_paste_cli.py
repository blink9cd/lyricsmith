"""`--mode paste` end to end: a text copied from a browser, written only when it is sung.

A track sync found not sung keeps its transcript in the store `--heard-dir` names. The
paste reads one text from `--text-from`, cleans it as a Genius page copies, and writes it
in place of the wrong text, backed up for undo and recorded as Genius's, pasted by hand,
only when that transcript sings it. A paste that is not sung, or not lyrics at all,
writes nothing and exits 6; a track the guards refuse, or whose tags or sidecars cannot
be read, writes nothing and exits 4. The frame is written under `--lang`, as a fix
writes one, and every open of the track under the lock must be the file its record was
matched to. No provider is ever asked. Each test drives the entry point with its real wiring; every
title and lyric is invented.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_backup  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_changed  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fix_text as lft  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_paste_mode as pm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    LITTLE,
    QUARRY,
    FixCase,
    song_track,
    words,
)
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above

#: The harbor song as a browser copies it off an invented Genius page: the header, the
#: line Genius puts between verses, and the trailer.
COPIED = (
    "12 ContributorsHarbor Song Lyrics\n[Verse 1]\n"
    + HARBOR.replace("deep,\n", "deep,\nYou might also like\n")
    + "3Embed"
)


class PasteCase(FixCase):
    """`FixCase` with one wrong-text track, heard singing the harbor song."""

    def setUp(self) -> None:
        """Track 1 holds the quarry song, heard as the harbor song; track 2 is sung."""
        super().setUp()
        self.paths = self.build(
            song_track(1, HARBOR, txt=QUARRY), song_track(2, FOUNDRY, txt=FOUNDRY)
        )
        self.hear(self.paths[0], HARBOR)
        self.pasted = self.tmp / "pasted.txt"

    def paste(self, text: str | bytes | None, *extra: str) -> cli_fakes.Result:
        """Paste *text* into track 1 (None: no file at all), with *extra* options."""
        if isinstance(text, bytes):
            self.pasted.write_bytes(text)
        elif text is not None:
            self.pasted.write_text(text, encoding="utf-8", newline="")
        return self.run_album(
            "--mode",
            "paste",
            "--track",
            str(self.paths[0]),
            "--heard-dir",
            str(self.heard),
            "--text-from",
            str(self.pasted),
            *extra,
        )

    def assert_nothing_written(self, result: cli_fakes.Result, code: int) -> None:
        """*result* exited *code*, made no run and left track 1's wrong text."""
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertEqual(lyrics_backup.runs_newest_first(self.album), ())
        self.assertEqual(self.text_of(self.paths[0]), words(QUARRY))
        self.assertEqual(self.provider_calls, [])


class SungPasteTest(PasteCase):
    """A sung paste is written as Genius's, pasted by hand, and undo takes it back."""

    def test_a_sung_paste_is_written_recorded_and_undone(self) -> None:
        before = (rb.snapshots(self.paths), raw(self.paths))
        record = self.record_of(self.paths[0])
        changed = self.tmp / "changed.txt"
        result = self.paste(COPIED, "--changed-list", str(changed))
        self.assertEqual(result.code, pm.EXIT_WRITTEN, result.stdout + result.stderr)
        stored = self.paths[0].with_suffix(".txt").read_text("utf-8")
        self.assertEqual(words(stored), words(HARBOR))
        # The header, the trailer and the clutter line are gone; the section stays.
        self.assertNotIn("Contributors", stored)
        self.assertNotIn("Embed", stored)
        self.assertNotIn("You might also like", stored)
        self.assertTrue(stored.startswith("[Verse 1]\n"))
        outcome = self.outcome(self.paths[0].name)
        self.assertEqual(
            (outcome.status, outcome.source, outcome.kind, outcome.origin),
            (
                lr.OutcomeStatus.WRITTEN,
                lm.Source.GENIUS,
                lm.LyricsKind.UNSYNCED,
                lr.PASTED_BY_HAND,
            ),
        )
        self.assertEqual(
            cli_fakes.row_cells(result.stdout, self.paths[0].name)[2],
            "Genius, pasted by hand",
        )
        # Only the pasted track was backed up and run; no provider was ever built.
        self.assertEqual([t.path for t in self.newest()], [self.paths[0].name])
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.asked(), [])
        self.assertEqual(changed.read_text("utf-8"), f"{self.paths[0]}\n")
        # The record is sync's to judge again, so the paste only reads it.
        self.assertEqual(self.record_of(self.paths[0]), record)
        undone = self.run_album("--mode", "undo")
        self.assertEqual(undone.code, album.EXIT_OK, undone.stdout + undone.stderr)
        self.assertEqual((rb.snapshots(self.paths), raw(self.paths)), before)

    def test_a_second_paste_is_refused_once_the_first_is_written(self) -> None:
        first = self.paste(HARBOR)
        self.assertEqual(first.code, pm.EXIT_WRITTEN, first.stdout + first.stderr)
        again = self.paste(HARBOR)
        self.assertEqual(again.code, pm.EXIT_REFUSED, again.stdout + again.stderr)
        self.assertIn("its text changed since it was heard", again.stderr)
        self.assertEqual(len(lyrics_backup.runs_newest_first(self.album)), 1)


class NotSungTest(PasteCase):
    """A paste the transcript does not sing, or that is no lyrics, writes nothing."""

    def test_an_unsung_paste_says_the_share_and_writes_nothing(self) -> None:
        changed = self.tmp / "changed.txt"
        result = self.paste(FOUNDRY, "--changed-list", str(changed))
        self.assert_nothing_written(result, pm.EXIT_NOT_SUNG)
        self.assertIn("not sung", result.stderr)
        self.assertRegex(result.stderr, r"\d\.\d\d of the \d+ word pairs heard")
        # An empty list, so a caller merging it adds nothing.
        self.assertEqual(changed.read_bytes(), b"")

    def test_the_track_s_own_wrong_text_is_not_sung(self) -> None:
        result = self.paste(QUARRY.upper())
        self.assert_nothing_written(result, pm.EXIT_NOT_SUNG)
        self.assertIn("the track's own wrong text", result.stderr)

    def test_blank_placeholder_and_oversize_pastes_are_refused(self) -> None:
        cases = {
            "nothing found": "  \n\n",
            "placeholder": "♪",
            "too large": "la " * (lr.MAX_LYRICS_CHARS // 3 + 1),
            "header alone": "12 ContributorsHarbor Song Lyrics\n3Embed",
        }
        for name, text in cases.items():
            with self.subTest(name):
                result = self.paste(text)
                self.assert_nothing_written(result, pm.EXIT_NOT_SUNG)
                self.assertIn("not lyrics to write", result.stderr)

    def test_a_file_past_the_byte_bound_is_too_large_unread(self) -> None:
        result = self.paste(b"a" * (pm.MAX_PASTE_BYTES + 1))
        self.assert_nothing_written(result, pm.EXIT_NOT_SUNG)
        self.assertIn("too large", result.stderr)

    def test_a_paste_the_track_s_length_rules_out_is_not_written(self) -> None:
        # Thirty seconds of audio cannot hold the harbor song sung forty times over.
        result = self.paste(HARBOR * 40)
        self.assert_nothing_written(result, pm.EXIT_NOT_SUNG)
        self.assertIn("implausible", result.stderr)


class RefusedTest(PasteCase):
    """A track the guards refuse is never written, whatever the paste."""

    def test_a_track_with_no_record_is_refused(self) -> None:
        self.heard = self.tmp / "empty-store"
        self.heard.mkdir()
        result = self.paste(HARBOR)
        self.assert_nothing_written(result, pm.EXIT_REFUSED)
        self.assertIn("no transcript record", result.stderr)

    def test_a_text_changed_since_it_was_heard_is_refused(self) -> None:
        self.paths[0].with_suffix(".txt").write_text(
            FOUNDRY, encoding="utf-8", newline=""
        )
        result = self.paste(HARBOR)
        self.assertEqual(result.code, pm.EXIT_REFUSED, result.stdout + result.stderr)
        self.assertEqual(lyrics_backup.runs_newest_first(self.album), ())
        self.assertIn("its text changed since it was heard", result.stderr)

    def test_a_track_holding_no_untimed_text_is_refused(self) -> None:
        self.paths[0].with_suffix(".txt").unlink()
        result = self.paste(HARBOR)
        self.assertEqual(result.code, pm.EXIT_REFUSED, result.stdout + result.stderr)
        self.assertIn("its lyrics are not untimed only", result.stderr)

    def test_a_transcript_too_short_to_judge_is_refused(self) -> None:
        self.hear(self.paths[0], LITTLE)
        result = self.paste(HARBOR)
        self.assert_nothing_written(result, pm.EXIT_REFUSED)
        self.assertIn("too little heard to judge", result.stderr)


class CannotRunTest(PasteCase):
    """A text that cannot be read, or an option the mode does not take: exit 2."""

    def test_a_missing_or_undecodable_text_cannot_run(self) -> None:
        for name, text in (("missing", None), ("not UTF-8", b"caf\xe9\n")):
            with self.subTest(name):
                self.pasted.unlink(missing_ok=True)
                result = self.paste(text)
                self.assert_nothing_written(result, album.EXIT_CANNOT_RUN)
                self.assertIn("--text-from", result.stderr)

    def test_paste_needs_its_three_options(self) -> None:
        base = ("--mode", "paste")
        given = {
            "--track": str(self.paths[0]),
            "--heard-dir": str(self.heard),
            "--text-from": str(self.pasted),
        }
        for missing in given:
            with self.subTest(missing):
                rest = [
                    part for k, v in given.items() if k != missing for part in (k, v)
                ]
                result = self.run_album(*base, *rest)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(f"{missing} is required with --mode paste", result.stderr)

    def test_paste_refuses_a_run_s_options(self) -> None:
        for extra in (("--no-genius",), ("--dry-run",)):
            with self.subTest(extra):
                result = self.paste(HARBOR, *extra)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(
                    f"{extra[0]} cannot be used with --mode paste", result.stderr
                )

    def test_text_from_is_refused_by_every_other_mode(self) -> None:
        for mode in ("run", "fix-text", "undo", "check"):
            with self.subTest(mode):
                store = ("--heard-dir", str(self.heard)) if mode == "fix-text" else ()
                text = ("--text-from", str(self.pasted))
                result = self.run_album("--mode", mode, *store, *text)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn("--text-from cannot be used with", result.stderr)


class UnderTheLockTest(PasteCase):
    """The guards are read again under the lock; a list that fails is exit 3."""

    def test_a_text_changed_after_the_check_is_the_run_s_error(self) -> None:
        real = album.real_wiring().stages.back_up

        def back_up_then_edit(*args: object, **kwargs: object) -> object:
            # Another hand edits the text once the paste is judged, before the step.
            manifest = real(*args, **kwargs)  # type: ignore[arg-type] # passed through
            self.paths[0].with_suffix(".txt").write_text(
                FOUNDRY, encoding="utf-8", newline=""
            )
            return manifest

        self.overrides["back_up"] = back_up_then_edit
        result = self.paste(HARBOR)
        self.assertEqual(
            result.code, album.EXIT_PASTE_ERROR, result.stdout + result.stderr
        )
        cell = cli_fakes.row_cells(result.stdout, self.paths[0].name)[-1]
        self.assertIn("its text changed since it was heard", cell)
        self.assertEqual(self.text_of(self.paths[0]), words(FOUNDRY))

    def test_a_track_swapped_for_another_file_after_the_check_is_refused(self) -> None:
        real = album.real_wiring().stages.back_up
        stand_in = self.tmp / "stand-in.mp3"
        shutil.copyfile(self.paths[1], stand_in)

        def back_up_then_swap(*args: object, **kwargs: object) -> object:
            # Another file takes the track's name once the paste is judged; its
            # sidecar still holds the text the record was judged against.
            manifest = real(*args, **kwargs)  # type: ignore[arg-type] # passed through
            os.replace(stand_in, self.paths[0])
            return manifest

        self.overrides["back_up"] = back_up_then_swap
        result = self.paste(HARBOR)
        self.assertEqual(
            result.code, album.EXIT_PASTE_ERROR, result.stdout + result.stderr
        )
        cell = cli_fakes.row_cells(result.stdout, self.paths[0].name)[-1]
        self.assertIn(lft.NOT_THE_HEARD_FILE, cell)
        self.assertEqual(self.text_of(self.paths[0]), words(QUARRY))

    def test_an_empty_list_that_cannot_be_written_is_exit_3(self) -> None:
        # A folder where the list would go: a file cannot replace it.
        unwritable = self.tmp / "a-folder"
        unwritable.mkdir()
        result = self.paste(FOUNDRY, "--changed-list", str(unwritable))
        self.assert_nothing_written(result, album.EXIT_RECORD_NOT_WRITTEN)
        self.assertIn("could not write --changed-list", result.stderr)

    def test_ctrl_c_while_the_empty_list_is_written_is_exit_130(self) -> None:
        late = mock.patch.object(
            lyrics_changed, "write_changed_list", return_value=(None, True)
        )
        with late:
            result = self.paste(FOUNDRY, "--changed-list", str(self.tmp / "c.txt"))
        self.assert_nothing_written(result, album.EXIT_INTERRUPTED)

    def test_a_store_file_that_cannot_be_read_is_a_notice(self) -> None:
        (self.heard / f"{'0' * 64}.json").write_bytes(b"not a record")
        result = self.paste(HARBOR)
        self.assertEqual(result.code, pm.EXIT_WRITTEN, result.stdout + result.stderr)
        self.assertIn("0" * 64, result.stderr)


#: An ID3v2 header of a version no reader takes: mutagen raises its own error, a
#: `ValueError` and no `OSError`, on reading the track's tags.
CORRUPT_TAG = b"ID3\x05\x00\x00\x00\x00\x00\x10" + bytes(64)


class ExitCodeTest(PasteCase):
    """Not sung has a code of its own, so no other failure can be read as it."""

    def test_not_sung_shares_no_code_with_another_end(self) -> None:
        self.assertEqual(album.EXIT_PASTE_NOT_SUNG, pm.EXIT_NOT_SUNG)
        others = {
            album.EXIT_OK,
            album.EXIT_TRACK_ERRORS,
            album.EXIT_CANNOT_RUN,
            album.EXIT_RECORD_NOT_WRITTEN,
            album.EXIT_PASTE_REFUSED,
            album.EXIT_PASTE_ERROR,
            album.EXIT_INTERRUPTED,
        }
        self.assertNotIn(pm.EXIT_NOT_SUNG, others)


class UnreadableTrackTest(PasteCase):
    """A track whose tags or sidecars cannot be read is refused, never a crash."""

    def test_a_corrupt_tag_is_a_refusal(self) -> None:
        # Rewritten in place, so the record still matches the file by its identity.
        self.paths[0].write_bytes(CORRUPT_TAG + self.paths[0].read_bytes())
        result = self.paste(HARBOR)
        self.assertEqual(result.code, pm.EXIT_REFUSED, result.stdout + result.stderr)
        self.assertIn("its tags or sidecars cannot be read", result.stderr)
        self.assertIn("ID3UnsupportedVersionError", result.stderr)
        self.assertEqual(lyrics_backup.runs_newest_first(self.album), ())

    def test_a_sidecar_that_is_not_utf8_is_a_refusal(self) -> None:
        self.paths[0].with_suffix(".txt").write_bytes(b"caf\xe9\n")
        result = self.paste(HARBOR)
        self.assertEqual(result.code, pm.EXIT_REFUSED, result.stdout + result.stderr)
        self.assertIn("its tags or sidecars cannot be read", result.stderr)
        self.assertEqual(lyrics_backup.runs_newest_first(self.album), ())

    def test_the_tags_are_read_once_before_the_run(self) -> None:
        real = album.real_wiring().stages.read_tags
        read: list[Path] = []

        def counted(path: Path) -> lm.TrackTags:
            read.append(path)
            return real(path)

        self.overrides["read_tags"] = counted
        # Not sung, so no run reads them again.
        result = self.paste(FOUNDRY)
        self.assert_nothing_written(result, pm.EXIT_NOT_SUNG)
        self.assertEqual(read, [self.paths[0]])


class LangTest(PasteCase):
    """`--lang` names the frame's language, as it does for a fix."""

    def uslt_langs(self) -> list[str]:
        """The language of each `USLT` frame track 1 holds now."""
        frames = lyrics_tags.read_tags(self.paths[0]).frames
        return [f.lang for f in frames if f.kind is lm.FrameKind.USLT]

    def test_the_frame_is_written_under_lang(self) -> None:
        result = self.paste(HARBOR, "--lang", "fra")
        self.assertEqual(result.code, pm.EXIT_WRITTEN, result.stdout + result.stderr)
        self.assertEqual(self.uslt_langs(), ["fra"])
        self.assertEqual(self.newest_manifest_lang(), "fra")

    def test_without_lang_the_frame_is_english(self) -> None:
        result = self.paste(HARBOR)
        self.assertEqual(result.code, pm.EXIT_WRITTEN, result.stdout + result.stderr)
        self.assertEqual(self.uslt_langs(), ["eng"])

    def newest_manifest_lang(self) -> str:
        """The language the newest run's manifest records."""
        newest = lyrics_backup.runs_newest_first(self.album)[0]
        return lyrics_manifest.read_manifest(newest).lang


class SwappedAtTheOpenTest(PasteCase):
    """A file swapped in after the step's identity check is refused at its own open."""

    def test_a_track_swapped_after_the_check_is_never_written(self) -> None:
        real = album.real_wiring().stages.check_encodable
        stand_in = self.tmp / "stand-in.mp3"
        shutil.copyfile(self.paths[1], stand_in)
        original = self.paths[1].read_bytes()

        def swap_then_check(lyrics: lm.Lyrics, lang: str) -> None:
            # The first call of the write, once the step checked the name: another
            # file takes it, its sidecar still the text the record was judged against.
            os.replace(stand_in, self.paths[0])
            real(lyrics, lang)

        self.overrides["check_encodable"] = swap_then_check
        result = self.paste(HARBOR)
        self.assertEqual(
            result.code, album.EXIT_PASTE_ERROR, result.stdout + result.stderr
        )
        cell = cli_fakes.row_cells(result.stdout, self.paths[0].name)[-1]
        self.assertIn(lft.NOT_THE_HEARD_FILE, cell)
        self.assertEqual(self.paths[0].read_bytes(), original)
        self.assertEqual(self.text_of(self.paths[0]), words(QUARRY))
