"""A sidecar holding a track's own embedded lyrics leaves the track as it is.

A first run serves a track from its frames and writes the sidecar it lacked from them.
On the next run that sidecar comes first in the order, but it holds the frames' own
lyrics, so the frames keep serving: the MP3 is not saved, the sidecar stays as it is,
and the track reads `unchanged`, source `frames`. A sidecar whose lyrics differ from
the frames, edited by hand say, still serves and is embedded. Each test drives the
entry point with its real wiring; only the clock and the provider table are faked, and
every lyric is invented.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_embedded import KEEPER  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_unchanged import (  # noqa: E402 - import follows the sys.path bootstrap above
    UnchangedCase,
    age_track_files,
    track_files,
)

#: Timed lines whose entries open with a line break, as many taggers write them.
_BROKEN_OPEN = tag_builders.sylt(
    (1000, "\nA lamp swings on the landing"), (4500, "\nand the stairs hold still")
)
#: The same timed lines, plainly written.
_TIMED = ((1000, "A lamp swings on the landing"), (4500, "and the stairs hold still"))
#: Their words as one untimed text.
_WORDS = "A lamp swings on the landing\nand the stairs hold still"
#: The timed lines in French, and invented German ones, as two SYLTs.
_FRA_SYLT = tag_builders.sylt(*_TIMED, lang="fra")
_DEU_SYLT = tag_builders.sylt(
    (1000, "Ein Licht im Flur"), (4500, "und die Treppe schweigt"), lang="deu"
)
#: The same, untimed, as two USLTs.
_FRA_USLT = tag_builders.uslt(_WORDS, lang="fra")
_DEU_USLT = tag_builders.uslt("Ein Licht im Flur\nund die Treppe schweigt", lang="deu")
#: A .lrc of other words, as a person might edit the one a run wrote.
_EDITED_LRC = "[00:01.000]A gull on the railing\n[00:04.500]and the rope gone slack\n"


class SecondRunFramesKeptCase(UnchangedCase):
    """Runs a keeper once, then again, comparing what the second run left."""

    def second_run(self, mp3: Path, *extra: str) -> lr.TrackOutcome:
        """Age *mp3* and its sidecars, run again with *extra*, and return the keeper's.

        Asserts the second run left every file's bytes and modification time, and
        that the keeper reads unchanged, served from its frames.
        """
        before = age_track_files(mp3)
        self.run_ok(*extra)
        self.assertEqual(track_files(mp3), before)
        keeper = self.keeper_after()
        self.assert_unchanged(keeper)
        self.assertIs(keeper.source, lm.Source.FRAMES)
        return keeper

    def first_run(self, *frames: lm.FrameRecord, **sidecars: str) -> Path:
        """The keeper with *frames* and *sidecars*, run once; its MP3."""
        mp3 = self.keeper(*frames, **sidecars)[0]
        self.run_ok()
        return mp3


class SecondRunKeepsTheFramesTest(SecondRunFramesKeptCase):
    """The sidecar a first run wrote from the frames does not displace them."""

    def test_an_id3v2_2_track_is_left_byte_for_byte(self) -> None:
        fixtures.build_album(self.album, ())
        mp3 = tag_builders.build_v22_mp3(self.album / KEEPER, lyrics=_WORDS + "\n")
        before = mp3.read_bytes()
        self.run_ok()
        self.assertIn("01 Keeper.txt", rb.sidecars(mp3))
        keeper = self.second_run(mp3)
        self.assertEqual(mp3.read_bytes(), before)
        self.assertIs(keeper.kind, lm.LyricsKind.UNSYNCED)

    def test_an_mpeg_frame_sylt_keeps_its_timing(self) -> None:
        mpeg = tag_builders.sylt(*_TIMED, time_format=tag_builders.MPEG_FRAMES)
        mp3 = self.first_run(mpeg)
        self.assertIn("01 Keeper.txt", rb.sidecars(mp3))
        self.second_run(mp3)
        self.assertEqual(lyrics_tags.read_tags(mp3).frames, (mpeg,))

    def test_entries_opening_with_a_line_break_are_not_saved_again(self) -> None:
        mp3 = self.first_run(_BROKEN_OPEN)
        self.assertIn("01 Keeper.lrc", rb.sidecars(mp3))
        keeper = self.second_run(mp3)
        self.assertIs(keeper.kind, lm.LyricsKind.SYNCED)
        self.assertEqual(lyrics_tags.read_tags(mp3).frames, (_BROKEN_OPEN,))

    def test_frames_in_another_language_are_not_saved_again(self) -> None:
        sylt = tag_builders.sylt(*_TIMED, lang="deu")
        uslt = tag_builders.uslt(_WORDS, lang="deu", desc="Invented")
        mp3 = self.first_run(sylt, uslt)
        self.second_run(mp3)
        self.assertEqual(rb.lyrics_frames(mp3), Counter((sylt, uslt)))

    def test_crlf_untimed_frames_keep_their_txt(self) -> None:
        crlf = _WORDS.replace("\n", "\r\n")
        mp3 = self.first_run(tag_builders.uslt(crlf))
        self.assertEqual(rb.sidecars(mp3), {"01 Keeper.txt": crlf.encode()})
        self.second_run(mp3)

    def test_a_hand_written_lrc_of_the_frames_lyrics_stays_as_it_is(self) -> None:
        lrc = "[00:01.00]A lamp swings on the landing\n[00:04.50]and the stairs hold still\n"
        mp3 = self.keeper(tag_builders.sylt(*_TIMED), lrc=lrc)[0]
        keeper = self.second_run(mp3)
        self.assertEqual(rb.sidecars(mp3)["01 Keeper.lrc"], lrc.encode())
        self.assertIs(keeper.kind, lm.LyricsKind.SYNCED)

    def test_a_hand_written_txt_with_crlf_endings_stays_as_it_is(self) -> None:
        txt = _WORDS.replace("\n", "\r\n") + "\r\n"
        mp3 = self.keeper(tag_builders.uslt(_WORDS + "\n"), txt=txt)[0]
        self.second_run(mp3)
        self.assertEqual(rb.sidecars(mp3)["01 Keeper.txt"], txt.encode())

    def test_undo_of_the_second_run_changes_nothing(self) -> None:
        mp3 = self.first_run(_BROKEN_OPEN)
        self.run_ok()
        before = age_track_files(mp3)
        result = self.run_ok("--mode", "undo")
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(track_files(mp3), before)


class AnotherLanguageKeepsTheFramesTest(SecondRunFramesKeptCase):
    """A run under another --lang keeps the frames whose lyrics the sidecar holds."""

    def test_a_lrc_of_the_fra_sylt_leaves_both_sylts_under_deu(self) -> None:
        mp3 = self.keeper(_FRA_SYLT, _DEU_SYLT)[0]
        self.run_ok("--lang", "fra")
        self.assertIn("01 Keeper.lrc", rb.sidecars(mp3))
        keeper = self.second_run(mp3, "--lang", "deu")
        self.assertIs(keeper.kind, lm.LyricsKind.SYNCED)
        self.assertEqual(rb.lyrics_frames(mp3), Counter((_FRA_SYLT, _DEU_SYLT)))

    def test_a_txt_of_the_fra_uslt_leaves_both_uslts_under_deu(self) -> None:
        mp3 = self.keeper(_FRA_USLT, _DEU_USLT)[0]
        self.run_ok("--lang", "fra")
        self.assertIn("01 Keeper.txt", rb.sidecars(mp3))
        keeper = self.second_run(mp3, "--lang", "deu")
        self.assertIs(keeper.kind, lm.LyricsKind.UNSYNCED)
        self.assertEqual(rb.lyrics_frames(mp3), Counter((_FRA_USLT, _DEU_USLT)))

    def test_a_lrc_matching_none_of_several_sylts_is_embedded(self) -> None:
        mp3 = self.keeper(_FRA_SYLT, _DEU_SYLT, lrc=_EDITED_LRC)[0]
        self.run_ok("--lang", "deu")
        keeper = self.keeper_after()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(keeper.frames_written)
        self.assertIs(keeper.source, lm.Source.SIDECAR)
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(_EDITED_LRC))


class DifferentSidecarStillServesTest(SecondRunFramesKeptCase):
    """A sidecar whose lyrics are not the frames' own serves and is embedded."""

    def test_an_edited_lrc_is_embedded(self) -> None:
        mp3 = self.first_run(_BROKEN_OPEN)
        mp3.with_suffix(".lrc").write_text(_EDITED_LRC, encoding="utf-8", newline="")
        before = age_track_files(mp3)
        self.run_ok()
        keeper = self.keeper_after()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(keeper.frames_written)
        self.assertIs(keeper.source, lm.Source.SIDECAR)
        self.assertNotEqual(mp3.read_bytes(), before["01 Keeper.mp3"][0])
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(_EDITED_LRC))

    def test_a_timed_lrc_beside_untimed_frames_is_embedded(self) -> None:
        lrc = "[00:01.000]A lamp swings on the landing\n"
        mp3 = self.keeper(tag_builders.uslt("A lamp swings on the landing\n"), lrc=lrc)[
            0
        ]
        self.run_ok()
        keeper = self.keeper_after()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.SIDECAR, lm.LyricsKind.SYNCED)
        )
        self.assertEqual(self.embedded(mp3), lm.parse_lrc(lrc))

    def test_a_txt_of_the_words_beside_synced_frames_gives_way_to_a_lrc(self) -> None:
        sylt, uslt = tag_builders.sylt(*_TIMED), tag_builders.uslt(_WORDS)
        mp3 = self.keeper(sylt, uslt, txt=_WORDS)[0]
        self.run_ok()
        keeper = self.keeper_after()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.FRAMES, lm.LyricsKind.SYNCED)
        )
        self.assertEqual(list(rb.sidecars(mp3)), ["01 Keeper.lrc"])
        self.assertFalse(keeper.frames_written)


class DryRunAgreesTest(SecondRunFramesKeptCase):
    """The dry run plans the frames for a track whose sidecar holds their lyrics."""

    def plan(self, *extra: str) -> str:
        """The keeper's plan in a dry run with *extra* options."""
        result = self.run_album("--dry-run", *extra)
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        (row,) = [line for line in result.stdout.splitlines() if KEEPER in line]
        return row

    def test_a_lrc_of_the_frames_lyrics_plans_the_frames(self) -> None:
        self.first_run(_BROKEN_OPEN)
        self.assertIn(lyrics_modes.PLAN_FRAMES_SYNCED, self.plan())

    def test_a_txt_of_the_frames_words_plans_the_frames(self) -> None:
        self.first_run(tag_builders.uslt(_WORDS))
        self.assertIn(lyrics_modes.PLAN_FETCH_OVER_FRAMES, self.plan())
        self.assertIn(lyrics_modes.PLAN_FRAMES_UNSYNCED, self.plan("--offline"))

    def test_a_lrc_of_frames_in_another_language_plans_the_frames(self) -> None:
        self.keeper(_FRA_SYLT, _DEU_SYLT)
        self.run_ok("--lang", "fra")
        self.assertIn(lyrics_modes.PLAN_FRAMES_SYNCED, self.plan("--lang", "deu"))
        self.assertIn(lyrics_modes.PLAN_FRAMES_SYNCED, self.plan("--lang", "fra"))

    def test_a_txt_of_frames_in_another_language_plans_the_frames(self) -> None:
        self.keeper(_FRA_USLT, _DEU_USLT)
        self.run_ok("--lang", "fra")
        plan = self.plan("--lang", "deu", "--offline")
        self.assertIn(lyrics_modes.PLAN_FRAMES_UNSYNCED, plan)

    def test_an_edited_lrc_plans_the_sidecar(self) -> None:
        mp3 = self.first_run(_BROKEN_OPEN)
        mp3.with_suffix(".lrc").write_text(_EDITED_LRC, encoding="utf-8", newline="")
        self.assertIn(lyrics_modes.PLAN_SIDECAR_SYNCED, self.plan())
