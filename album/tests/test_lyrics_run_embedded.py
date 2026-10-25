"""A run without `--fresh` serves a track from its embedded lyrics as from a sidecar.

Embedded synced lyrics (a millisecond SYLT with a worded entry) serve the track with no
provider asked, after a timed ``.lrc`` only, and the missing ``.lrc`` is written from
them. Embedded untimed lyrics give way to a timed answer and to a ``.txt``, and beat
the untimed steps and Genius, a ``.txt`` written from them. `--fresh` still asks the
providers, and `--mode undo` puts a frames-served track back. Each test drives the
entry point with its real wiring; only the clock and the provider table are faked,
and every lyric is invented.
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
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh import FreshCliCase  # noqa: E402 - import follows the sys.path bootstrap above

_SYNCED = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
_PLAIN = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
_GENIUS = lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED)
#: The keeper's embedded timed lines, and the SYLT holding them.
_TIMED = ((1000, "A lamp swings on the landing"), (4500, "and the stairs hold still"))
_SYLT = tag_builders.sylt(*_TIMED)
_FRAME_SYNCED = lm.SyncedLyrics(tuple(lm.SyncedLine(ms, text) for ms, text in _TIMED))
#: The keeper's embedded untimed words.
_UNTIMED_TEXT = "A lamp swings on the landing\nand the stairs hold still\n"
_USLT = tag_builders.uslt(_UNTIMED_TEXT)
#: Sidecars of another transcription, so which source served the track shows.
_OLD_LRC = "[00:02.00]A gull on the railing\n[00:05.00]and the rope gone slack\n"
_OLD_TXT = "A gull on the railing\nand the rope gone slack\n"
KEEPER = "01 Keeper.mp3"
MILL = TrackSpec("02 Mill.mp3", title="The Mill")


class EmbeddedCase(FreshCliCase):
    """Every provider answers the keeper, timed and untimed, unless a test says otherwise."""

    def setUp(self) -> None:
        """A table of LRCLIB's two steps and Genius, each answering the keeper."""
        super().setUp()
        self.answer_steps(
            synced={"The Keeper": fixtures.LIGHTHOUSE_LRC},
            plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN},
        )

    def answer_steps(
        self, *, synced: Mapping[str, str], plain: Mapping[str, str]
    ) -> None:
        """Answer *synced* at the timed step, *plain* at the untimed step and Genius."""
        self.titles = {
            _SYNCED: run_fakes.TitleProvider(synced),
            _PLAIN: run_fakes.TitleProvider(plain),
            _GENIUS: run_fakes.TitleProvider(plain),
        }
        self.providers = self.titles

    def keeper(
        self,
        *frames: lm.FrameRecord,
        lrc: str | None = None,
        txt: str | None = None,
    ) -> list[Path]:
        """The keeper with *frames* and the sidecars given, then the mill with nothing."""
        track = TrackSpec(KEEPER, title="The Keeper", lrc=lrc, txt=txt)
        paths = fixtures.build_album(self.album, (track, MILL))
        tag_builders.plant_frames(paths[0], frames)
        return paths

    def asked_for_keeper(self) -> list[lm.ProviderKey]:
        """Each step whose provider was asked about the keeper."""
        return [
            key
            for key, provider in self.titles.items()
            if any(query.title == "The Keeper" for query in provider.calls)
        ]

    def run_keeper(self, *extra: str) -> lr.TrackOutcome:
        """Run the album with *extra* options; the keeper's outcome, written."""
        result = self.run_album(*extra)
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        return keeper

    def embedded(self, path: Path) -> lm.Lyrics | None:
        """The lyrics *path*'s frames hold now, as the check reads them."""
        return lm.lyrics_from_frames(lyrics_tags.read_tags(path).frames, run_fakes.LANG)


class EmbeddedSyncedTest(EmbeddedCase):
    """Embedded synced lyrics serve the track; only a timed .lrc comes before them."""

    def test_embedded_synced_lyrics_serve_the_track_with_no_provider_asked(
        self,
    ) -> None:
        paths = self.keeper(_SYLT, _USLT)
        keeper = self.run_keeper()
        self.assertEqual(self.asked_for_keeper(), [])
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.FRAMES, lm.LyricsKind.SYNCED)
        )
        self.assertEqual(self.embedded(paths[0]), _FRAME_SYNCED)
        assert keeper.check is not None
        self.assertEqual(keeper.check.flags, ())

    def test_the_missing_lrc_is_written_from_the_frames(self) -> None:
        paths = self.keeper(_SYLT)
        keeper = self.run_keeper()
        self.assertEqual(keeper.sidecar_written, "01 Keeper.lrc")
        self.assertEqual(
            rb.sidecars(paths[0]),
            {"01 Keeper.lrc": lm.format_lrc(_FRAME_SYNCED.lines).encode()},
        )
        self.assertEqual(
            lm.parse_lrc(lm.format_lrc(_FRAME_SYNCED.lines)), _FRAME_SYNCED
        )

    def test_no_lrc_is_written_from_frames_it_cannot_hold(self) -> None:
        # An entry holding a line break would come back from a .lrc cut in two, the
        # second half dropped, so the frames stay the only copy.
        broken = tag_builders.sylt((1000, "A lamp swings\non the landing"))
        paths = self.keeper(broken)
        self.run_album()
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual(
            (keeper.status, keeper.source, keeper.sidecar_written),
            (lr.OutcomeStatus.UNCHANGED, lm.Source.FRAMES, None),
        )
        self.assertEqual(rb.sidecars(paths[0]), {})
        self.assertEqual(
            self.embedded(paths[0]),
            lm.SyncedLyrics((lm.SyncedLine(1000, "A lamp swings\non the landing"),)),
        )

    def test_embedded_synced_lyrics_replace_a_txt_sidecar(self) -> None:
        paths = self.keeper(_SYLT, txt=_OLD_TXT)
        keeper = self.run_keeper()
        self.assertEqual(self.asked_for_keeper(), [])
        self.assertEqual(keeper.source, lm.Source.FRAMES)
        self.assertEqual(keeper.sidecars_removed, ("01 Keeper.txt",))
        self.assertEqual(list(rb.sidecars(paths[0])), ["01 Keeper.lrc"])

    def test_a_timed_lrc_beats_embedded_synced_lyrics(self) -> None:
        paths = self.keeper(_SYLT, lrc=_OLD_LRC)
        keeper = self.run_keeper()
        self.assertEqual(self.asked_for_keeper(), [])
        self.assertEqual(keeper.source, lm.Source.SIDECAR)
        self.assertEqual(rb.sidecars(paths[0]), {"01 Keeper.lrc": _OLD_LRC.encode()})
        self.assertEqual(self.embedded(paths[0]), lm.parse_lrc(_OLD_LRC))

    def test_the_summary_row_names_the_frames(self) -> None:
        self.keeper(_SYLT)
        result = self.run_album()
        (row,) = [line for line in result.stdout.splitlines() if KEEPER in line]
        self.assertEqual(row.split()[3:5], ["frames", "synced"])
        (line,) = [r for r in result.stdout.splitlines() if r.startswith("providers:")]
        # Only the mill was asked of LRCLIB; the keeper's frames are no provider.
        self.assertIn("LRCLIB 0/1", line)

    def test_an_offline_run_serves_embedded_synced_lyrics(self) -> None:
        paths = self.keeper(_SYLT)
        keeper = self.run_keeper("--offline")
        self.assertEqual(keeper.source, lm.Source.FRAMES)
        self.assertIn("01 Keeper.lrc", rb.sidecars(paths[0]))

    def test_fresh_still_asks_the_providers(self) -> None:
        self.keeper(_SYLT)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.asked_for_keeper(), [_SYNCED])
        keeper, _mill = self.afters()
        assert keeper is not None
        # Asked, and then declined as unlike the frames: the fresh run's own guard.
        self.assertEqual(
            (keeper.status, keeper.source),
            (lr.OutcomeStatus.KEPT_EXISTING, lm.Source.LRCLIB),
        )


class EmbeddedUntimedTest(EmbeddedCase):
    """Embedded untimed lyrics give way to a timed answer and a .txt, and beat the rest."""

    def no_timed_answer(self) -> None:
        """The timed step finds nothing; the untimed step and Genius answer the keeper."""
        self.answer_steps(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})

    def test_a_timed_answer_beats_embedded_untimed_lyrics(self) -> None:
        self.keeper(_USLT)
        keeper = self.run_keeper()
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
        )
        self.assertEqual(self.asked_for_keeper(), [_SYNCED])

    def test_embedded_untimed_lyrics_beat_the_untimed_steps_and_genius(self) -> None:
        self.no_timed_answer()
        paths = self.keeper(_USLT)
        keeper = self.run_keeper()
        self.assertEqual(self.asked_for_keeper(), [_SYNCED])
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.FRAMES, lm.LyricsKind.UNSYNCED)
        )
        self.assertEqual(self.embedded(paths[0]), lm.PlainLyrics(_UNTIMED_TEXT))

    def test_the_txt_is_written_from_the_frames(self) -> None:
        self.no_timed_answer()
        paths = self.keeper(_USLT)
        keeper = self.run_keeper()
        self.assertEqual(keeper.sidecar_written, "01 Keeper.txt")
        self.assertEqual(
            rb.sidecars(paths[0]), {"01 Keeper.txt": _UNTIMED_TEXT.encode()}
        )

    def test_a_txt_sidecar_beats_embedded_untimed_lyrics(self) -> None:
        self.no_timed_answer()
        paths = self.keeper(_USLT, txt=_OLD_TXT)
        keeper = self.run_keeper()
        self.assertEqual(self.asked_for_keeper(), [_SYNCED])
        self.assertEqual(keeper.source, lm.Source.SIDECAR)
        self.assertEqual(rb.sidecars(paths[0]), {"01 Keeper.txt": _OLD_TXT.encode()})
        self.assertEqual(self.embedded(paths[0]), lm.PlainLyrics(_OLD_TXT))

    def test_a_sylt_timed_in_mpeg_frames_serves_as_untimed(self) -> None:
        self.no_timed_answer()
        self.keeper(tag_builders.sylt(*_TIMED, time_format=1))
        keeper = self.run_keeper()
        self.assertEqual(
            (keeper.source, keeper.kind), (lm.Source.FRAMES, lm.LyricsKind.UNSYNCED)
        )
        self.assertEqual(self.asked_for_keeper(), [_SYNCED])


class EmbeddedUndoTest(EmbeddedCase):
    """A track served from its frames is put back by undo, the new sidecar removed."""

    def assert_undone(self, paths: list[Path], written: str) -> None:
        """Run, see *written* appear, undo, and find the album as it was."""
        before = rb.snapshots(paths)
        self.run_keeper()
        self.assertIn(written, rb.sidecars(paths[0]))
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(rb.snapshots(paths), before)
        self.assertEqual(rb.sidecars(paths[0]), {})

    def test_undo_puts_back_a_track_served_from_synced_frames(self) -> None:
        self.assert_undone(self.keeper(_SYLT, _USLT), "01 Keeper.lrc")

    def test_undo_puts_back_a_track_served_from_untimed_frames(self) -> None:
        self.answer_steps(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        self.assert_undone(self.keeper(_USLT), "01 Keeper.txt")

    def test_check_passes_a_track_served_from_its_frames(self) -> None:
        self.keeper(_SYLT)
        self.run_keeper()
        result = self.run_album("--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        rows = [line.split() for line in result.stdout.splitlines()]
        self.assertIn(["1", "01", "Keeper.mp3", "PASS"], rows)
