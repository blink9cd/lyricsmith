"""`--mode run --fresh`: the providers always asked, old lyrics stripped only on an answer.

Each test drives the entry point with its real wiring, the real backup, the real strip
stage and the real undo included, so the manifest and the album on disk are the ones
an operator would get. Only the clock is fixed, so the run id is known, and the
provider table is faked, so nothing reaches the network. A fresh run asks the providers
even where a timed sidecar would have served the track; with an accepted answer it
strips the track's old frames and sidecars, then embeds the answer, and `--mode undo`
puts the old ones back byte for byte. With no answer it changes nothing. The album is
built from silent MP3s under the test's own directory, and every lyric is invented.
"""

from __future__ import annotations

import signal
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_strip  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: The old lyrics a track holds before the run, unlike the answer the providers give.
_OLD_LRC = "[00:01.00]A harbor bell rings twice\n[00:04.00]and the gulls go quiet\n"
_OLD_TXT = "A harbor bell rings twice\nand the gulls go quiet\n"
#: An ``.lrc`` with no time stamp, which a run without --fresh would rename or remove.
_UNTIMED_LRC = "A reed bends low\nand the river hums\n"
#: Old lyrics frames: another transcription of the song the providers answer with, so
#: a fresh run accepts the answer (`test_lyrics_run_fresh_unlike` covers an unlike one).
_FRAMES = (
    tag_builders.uslt(fixtures.LIGHTHOUSE_EDITED),
    tag_builders.sylt(
        *(
            (1000 + 4000 * index, line)
            for index, line in enumerate(fixtures.LIGHTHOUSE_EDITED.splitlines())
            if line
        )
    ),
)
#: A lyrics frame in a language other than the run's: old lyrics all the same, so a
#: fresh run that finds nothing keeps it, and a run without --fresh is served by it (an
#: answer unlike it is declined too: `test_lyrics_run_fresh_unlike`).
_FOREIGN = (tag_builders.uslt("A verse in another tongue\n", lang="fra"),)
RUN_ID = run_fakes.RUN_ID
_SYNCED = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
_PLAIN = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)

#: Frames and a timed ``.lrc`` beside a ``.txt``: the track a timed sidecar would serve.
KEEPER = TrackSpec("01 Keeper.mp3", title="The Keeper", lrc=_OLD_LRC, txt=_OLD_TXT)
#: Nothing at all.
MILL = TrackSpec("02 Mill.mp3", title="The Mill")


class FreshCliCase(cli_fakes.RealWiringCliCase):
    """The real wiring with a fixed clock and a recording provider table."""

    def setUp(self) -> None:
        """No album yet, and a table that answers nothing until a test says otherwise."""
        super().setUp()
        self.answer({})

    def answer(self, synced: Mapping[str, str]) -> None:
        """Answer each title in *synced* with its synced lyrics; nothing for the rest."""
        self.titles = {
            _SYNCED: run_fakes.TitleProvider(synced),
            _PLAIN: run_fakes.TitleProvider({}),
        }
        self.providers = self.titles

    def asked(self) -> list[str]:
        """Every title the synced provider was asked for, in order."""
        return [query.title for query in self.titles[_SYNCED].calls]

    def build(self, *tracks: TrackSpec) -> list[Path]:
        """Build an album of *tracks*, the first one's old frames planted."""
        paths = fixtures.build_album(self.album, tracks)
        tag_builders.plant_frames(paths[0], _FRAMES)
        return paths

    def fresh(self, *extra: str) -> cli_fakes.Result:
        """Run this test's album with --fresh and *extra* options."""
        return self.run_album("--fresh", *extra)

    def afters(self) -> list[lr.TrackOutcome | None]:
        """Each track's outcome in the run's manifest on disk."""
        manifest = lyrics_manifest.read_manifest(lr.run_folder(self.album, RUN_ID))
        return [track.after for track in manifest.tracks]

    def no_run_folder(self) -> bool:
        """True when nothing was written under the album's run folder."""
        return not (self.album / lr.RUN_FOLDER_NAME).exists()


class FreshAnswerTest(FreshCliCase):
    """An accepted answer replaces the old lyrics; an undo puts them back."""

    def setUp(self) -> None:
        """One track with frames and both sidecars; the providers answer it."""
        super().setUp()
        self.paths = self.build(KEEPER)
        self.answer({"The Keeper": fixtures.LIGHTHOUSE_LRC})

    def test_the_providers_are_asked_over_a_timed_sidecar(self) -> None:
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.asked(), ["The Keeper"])
        keeper = self.paths[0]
        self.assertEqual(list(rb.sidecars(keeper)), ["01 Keeper.lrc"])
        self.assertNotEqual(rb.sidecars(keeper)["01 Keeper.lrc"], _OLD_LRC.encode())
        frames = rb.lyrics_frames(keeper)
        self.assertTrue(frames)
        self.assertNotEqual(frames, Counter(_FRAMES))

    def test_the_manifest_lists_what_the_strip_removed(self) -> None:
        self.fresh()
        (after,) = self.afters()
        assert after is not None
        self.assertEqual(
            (after.status, after.source, after.kind),
            (lr.OutcomeStatus.WRITTEN, lm.Source.LRCLIB, lm.LyricsKind.SYNCED),
        )
        self.assertEqual(after.sidecars_removed, ("01 Keeper.lrc", "01 Keeper.txt"))
        self.assertEqual(after.sidecar_written, "01 Keeper.lrc")
        self.assertTrue(after.frames_written)

    def test_undo_puts_back_the_old_frames_and_both_sidecars(self) -> None:
        before = rb.snapshots(self.paths)
        self.fresh()
        self.assertNotEqual(rb.snapshots(self.paths), before)
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertNotIn("displaced", result.stdout)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertEqual(
            rb.sidecars(self.paths[0]),
            {"01 Keeper.lrc": _OLD_LRC.encode(), "01 Keeper.txt": _OLD_TXT.encode()},
        )

    def test_without_fresh_the_timed_sidecar_still_serves_the_track(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.asked(), [])
        (after,) = self.afters()
        assert after is not None
        self.assertEqual(after.source, lm.Source.SIDECAR)


class FreshInterruptTest(FreshCliCase):
    """Ctrl-C during a fresh strip waits for the embed, so no track is left bare."""

    def test_an_interrupt_during_the_strip_is_honored_after_the_embed(self) -> None:
        paths = self.build(KEEPER, MILL)
        self.answer({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        before = rb.snapshots(paths)

        def strip_track(
            mp3: Path, *, frames: bool = True, sidecars: bool = True
        ) -> lr.StripResult:
            stripped = lyrics_strip.strip_track(mp3, frames=frames, sidecars=sidecars)
            signal.raise_signal(signal.SIGINT)
            return stripped

        self.overrides["strip_track"] = strip_track
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_INTERRUPTED)
        self.assertIn(f"ERROR embed: {lyrics_outcomes.INTERRUPTED}", result.stdout)
        keeper, _mill = self.afters()
        assert keeper is not None
        self.assertEqual(
            (keeper.frames_written, keeper.sidecar_written),
            (True, "01 Keeper.lrc"),
        )
        self.assertTrue(rb.lyrics_frames(paths[0]))
        self.assertNotEqual(rb.lyrics_frames(paths[0]), Counter(_FRAMES))
        self.assertEqual(list(rb.sidecars(paths[0])), ["01 Keeper.lrc"])
        self.overrides.clear()
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(paths), before)


class FreshNoAnswerTest(FreshCliCase):
    """With no accepted answer a fresh run changes nothing and says what was kept."""

    def test_a_track_keeps_its_frames_and_sidecars(self) -> None:
        paths = self.build(
            KEEPER,
            MILL,
            TrackSpec("03 Bell.mp3", title="The Bell", lrc=_OLD_LRC),
            TrackSpec("04 Reed.mp3", title="The Reed", lrc=_UNTIMED_LRC, txt=_OLD_TXT),
        )
        before = rb.snapshots(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            self.asked(), ["The Keeper", "The Mill", "The Bell", "The Reed"]
        )
        self.assertEqual(
            [after.status for after in self.afters() if after is not None],
            [
                lr.OutcomeStatus.KEPT_EXISTING,
                lr.OutcomeStatus.NOT_FOUND,
                lr.OutcomeStatus.KEPT_EXISTING,
                lr.OutcomeStatus.KEPT_EXISTING,
            ],
        )
        self.assertEqual(rb.snapshots(paths), before)
        self.assertEqual(
            rb.sidecars(paths[3]),
            {"04 Reed.lrc": _UNTIMED_LRC.encode(), "04 Reed.txt": _OLD_TXT.encode()},
        )
        self.assertIn("kept existing 3", result.stdout)

    def test_frames_in_another_language_are_kept(self) -> None:
        paths = fixtures.build_album(self.album, (MILL,))
        tag_builders.plant_frames(paths[0], _FOREIGN)
        before = rb.snapshots(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (after,) = self.afters()
        assert after is not None
        self.assertEqual(after.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(rb.snapshots(paths), before)

    def test_without_fresh_other_language_frames_serve_the_track(self) -> None:
        paths = fixtures.build_album(self.album, (MILL,))
        tag_builders.plant_frames(paths[0], _FOREIGN)
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (after,) = self.afters()
        assert after is not None
        self.assertEqual(
            (after.status, after.source), (lr.OutcomeStatus.WRITTEN, lm.Source.FRAMES)
        )
        self.assertEqual(
            rb.sidecars(paths[0]), {"02 Mill.txt": str(_FOREIGN[0].payload).encode()}
        )

    def test_every_provider_failing_is_a_fetch_error_and_changes_nothing(self) -> None:
        paths = self.build(KEEPER)
        self.providers = {
            _SYNCED: fixtures.raising(OSError("refused")),
            _PLAIN: fixtures.raising(OSError("refused")),
        }
        before = rb.snapshots(paths)
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR fetch: every provider failed", result.stdout)
        (after,) = self.afters()
        assert after is not None and after.error is not None
        self.assertEqual(after.error.stage, lr.ErrorStage.FETCH)
        self.assertEqual(rb.snapshots(paths), before)


class FreshStripErrorTest(FreshCliCase):
    """A strip that fails is the track's error: nothing embedded, the next track runs."""

    def test_a_folder_under_a_sidecar_name_stops_that_track_alone(self) -> None:
        paths = self.build(TrackSpec("01 Keeper.mp3", title="The Keeper"), MILL)
        txt = paths[0].with_suffix(".txt")
        txt.mkdir()
        paths[0].with_suffix(".lrc").write_text(_OLD_LRC, encoding="utf-8", newline="")
        self.answer(
            {"The Keeper": fixtures.LIGHTHOUSE_LRC, "The Mill": fixtures.LIGHTHOUSE_LRC}
        )
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn(
            "ERROR strip: 01 Keeper.txt is not a file or a link; left as it is",
            result.stdout,
        )
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter(_FRAMES))
        self.assertEqual(rb.sidecars(paths[0]), {"01 Keeper.lrc": _OLD_LRC.encode()})
        self.assertTrue(txt.is_dir())
        keeper, mill = self.afters()
        assert keeper is not None and keeper.error is not None and mill is not None
        self.assertEqual(keeper.error.stage, lr.ErrorStage.STRIP)
        self.assertFalse(keeper.frames_written)
        self.assertEqual(mill.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(rb.lyrics_frames(paths[1]))


class FreshRefusalTest(FreshCliCase):
    """--fresh belongs to a run that asks the providers, and a dry run changes nothing."""

    def setUp(self) -> None:
        """One track with old lyrics, so a write would show."""
        super().setUp()
        self.paths = self.build(KEEPER)

    def test_every_other_mode_refuses_it(self) -> None:
        for mode in (album.MODE_CHECK, album.MODE_UNDO, "strip"):
            with self.subTest(mode=mode):
                result = self.fresh("--mode", mode)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(
                    f"--fresh cannot be used with --mode {mode}", result.stderr
                )
                self.assertTrue(self.no_run_folder())

    def test_offline_refuses_it(self) -> None:
        result = self.fresh("--offline")
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn("--fresh cannot be used with --offline", result.stderr)
        self.assertTrue(self.no_run_folder())
        self.assertEqual(self.provider_calls, [])

    def test_a_dry_run_writes_nothing(self) -> None:
        before = rb.snapshots(self.paths)
        result = self.fresh("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertTrue(self.no_run_folder())

    def test_a_dry_run_plans_a_fetch_over_a_timed_sidecar(self) -> None:
        result = self.fresh("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertTrue(row.endswith(f"  {lyrics_modes.PLAN_FETCH}"), row)
        self.assertNotIn(lyrics_modes.PLAN_SIDECAR_SYNCED, result.stdout)

    def test_a_dry_run_plans_a_fetch_over_an_undecodable_sidecar(self) -> None:
        # A fresh run never reads the sidecars, so bytes it would not decode are no
        # reason to preview an error.
        self.paths[0].with_suffix(".lrc").write_bytes(b"caf\xe9 at the corner\n")
        result = self.fresh("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertTrue(row.endswith(f"  {lyrics_modes.PLAN_FETCH}"), row)
        self.assertNotIn("ERROR", result.stdout)

    def test_a_dry_run_previews_a_folder_under_a_sidecar_name(self) -> None:
        txt = self.paths[0].with_suffix(".txt")
        txt.unlink()
        txt.mkdir()
        result = self.fresh("--dry-run")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn(
            "ERROR strip: 01 Keeper.txt is not a file or a link; left as it is",
            result.stdout,
        )
        self.assertTrue(txt.is_dir())
        self.assertTrue(self.no_run_folder())


class FreshEmbedErrorTest(FreshCliCase):
    """An embed failing after the strip is the track's error, and undo restores it."""

    def test_an_embed_error_after_the_strip_is_recorded_and_undone(self) -> None:
        paths = self.build(KEEPER)
        self.answer({"The Keeper": fixtures.LIGHTHOUSE_LRC})
        before = rb.snapshots(paths)

        def write_lyrics(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            raise OSError("disk full")

        self.overrides["write_lyrics"] = write_lyrics
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR embed: disk full", result.stdout)
        (after,) = self.afters()
        assert after is not None and after.error is not None
        self.assertEqual(after.error.stage, lr.ErrorStage.EMBED)
        self.assertEqual(after.sidecars_removed, ("01 Keeper.lrc", "01 Keeper.txt"))
        self.assertTrue(after.frames_written)
        self.assertEqual(rb.sidecars(paths[0]), {})
        self.assertEqual(rb.lyrics_frames(paths[0]), Counter())
        self.overrides.clear()
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(paths), before)


class FreshUnwritableTagTest(FreshCliCase):
    """A tag the embed cannot save is refused before the strip, so nothing is lost."""

    def setUp(self) -> None:
        """An ID3v2.2 track with an ``.lrc`` and no lyrics frame, then a plain track."""
        super().setUp()
        (mill,) = fixtures.build_album(self.album, (MILL,))
        old = tag_builders.build_v22_mp3(self.album / "01 Old.mp3")
        old.with_suffix(".lrc").write_text(_OLD_LRC, encoding="utf-8", newline="")
        self.paths = [old, mill]
        # With no artist tag the query is the file's stem. The old track is short
        # (tag_builders.SECONDS), so its answer is lines that end inside it.
        self.answer({"01 Old": _OLD_LRC, "The Mill": fixtures.LIGHTHOUSE_LRC})

    def test_the_track_keeps_its_sidecar_and_the_next_track_runs(self) -> None:
        old_bytes = self.paths[0].read_bytes()
        before = rb.snapshots(self.paths[:1])
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR embed: cannot write an ID3v2.2 tag", result.stdout)
        old, mill = self.afters()
        assert old is not None and old.error is not None and mill is not None
        self.assertEqual(
            (old.error.stage, old.error.exc_type),
            (lr.ErrorStage.EMBED, "TagVersionError"),
        )
        self.assertEqual((old.sidecars_removed, old.frames_written), ((), False))
        self.assertEqual(self.paths[0].read_bytes(), old_bytes)
        self.assertEqual(rb.snapshots(self.paths[:1]), before)
        self.assertEqual(mill.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(rb.lyrics_frames(self.paths[1]))

    def test_a_dry_run_previews_the_embed_error(self) -> None:
        before = rb.snapshots(self.paths)
        result = self.fresh("--dry-run")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR embed: cannot write an ID3v2.2 tag", result.stdout)
        (row,) = [line for line in result.stdout.splitlines() if "02 Mill" in line]
        self.assertTrue(row.endswith(f"  {lyrics_modes.PLAN_FETCH}"), row)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertTrue(self.no_run_folder())

    def test_a_dry_run_names_the_error_the_run_would_stop_on(self) -> None:
        # The tag the embed cannot save AND a folder its strip would fail on: the run
        # refuses the embed before it strips, so the dry run must preview that one.
        self.paths[0].with_suffix(".txt").mkdir()
        embed = "ERROR embed: cannot write an ID3v2.2 tag"
        preview = self.fresh("--dry-run")
        self.assertIn(embed, preview.stdout)
        self.assertNotIn("ERROR strip", preview.stdout)
        self.assertIn(embed, self.fresh().stdout)


@tag_builders.needs_binding_modes
class FreshReadOnlyTest(FreshCliCase):
    """A track no write can open is refused before the strip, so nothing is lost."""

    def setUp(self) -> None:
        """A read-only track with an ``.lrc`` and no lyrics frame, then a plain track."""
        super().setUp()
        bell = TrackSpec("01 Bell.mp3", title="The Bell", lrc=_OLD_LRC)
        self.paths = fixtures.build_album(self.album, (bell, MILL))
        tag_builders.make_read_only(self, self.paths[0])
        self.answer(
            {"The Bell": fixtures.LIGHTHOUSE_LRC, "The Mill": fixtures.LIGHTHOUSE_LRC}
        )

    def test_the_track_keeps_its_sidecar_and_the_next_track_runs(self) -> None:
        bell_bytes = self.paths[0].read_bytes()
        before = rb.snapshots(self.paths[:1])
        result = self.fresh()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR embed:", result.stdout)
        self.assertEqual(self.paths[0].read_bytes(), bell_bytes)
        self.assertEqual(rb.sidecars(self.paths[0]), {"01 Bell.lrc": _OLD_LRC.encode()})
        self.assertEqual(rb.snapshots(self.paths[:1]), before)
        bell, mill = self.afters()
        assert bell is not None and bell.error is not None and mill is not None
        self.assertEqual((bell.sidecars_removed, bell.frames_written), ((), False))
        # The check's own refusal, not the save's: nothing reached the save.
        self.assertEqual(
            (bell.error.stage, bell.error.exc_type),
            (lr.ErrorStage.EMBED, "PermissionError"),
        )
        self.assertEqual(mill.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(rb.lyrics_frames(self.paths[1]))

    def test_a_dry_run_previews_the_embed_error(self) -> None:
        before = rb.snapshots(self.paths)
        result = self.fresh("--dry-run")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR embed:", result.stdout)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertTrue(self.no_run_folder())
