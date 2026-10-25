"""The run over one album: the barrier, the per-track error boundaries, the interrupt.

Each test builds a small album of silent MP3s, runs it through the real tag, sidecar,
fetch and check stages with faked providers and a faked run folder, and reads the
outcome from the manifests the run wrote. Every lyric is invented.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_outcomes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above

TrackSpec = fixtures.TrackSpec

#: Three tracks, each searched by its own title.
TRACKS = (
    TrackSpec("01 Keeper.mp3", title="The Keeper"),
    TrackSpec("02 Orchard.mp3", title="The Orchard"),
    TrackSpec("03 Mill.mp3", title="The Mill"),
)
#: Synced answers for the first track, plain for the second; the third finds nothing.
SYNCED = {"The Keeper": fixtures.LIGHTHOUSE_LRC}
PLAIN = {"The Orchard": fixtures.ORCHARD_PLAIN}


class RunCase(fixtures.IsolatedCase):
    """Builds an album and runs it, collecting the progress lines."""

    def setUp(self) -> None:
        """A run folder fake, and the progress lines a run sends."""
        super().setUp()
        self.album = self.tmp / "album"
        self.folder = run_fakes.FakeRunFolder()
        self.progress: list[str] = []

    def build(self, tracks: tuple[TrackSpec, ...] = TRACKS) -> list[Path]:
        """Build *tracks* under the album."""
        return fixtures.build_album(self.album, tracks)

    def run_album(
        self,
        stages: lyrics_stages.Stages | None = None,
        providers: lm.ProviderTable | None = None,
    ) -> lyrics_run.RunReport:
        """Run the built album with *stages* and *providers*, or the defaults."""
        return lyrics_run.run_album(
            self.album,
            lyrics_album.discover_tracks(self.album).tracks,
            run_fakes.table(SYNCED, PLAIN) if providers is None else providers,
            run_fakes.options(),
            stages or run_fakes.make_stages(self.folder),
            acquire_lock=run_fakes.acquire,
            started=run_fakes.STARTED,
            progress=self.progress.append,
        )

    def outcomes(
        self, report: lyrics_run.RunReport
    ) -> dict[str, lr.TrackOutcome | None]:
        """Each track's outcome, by album-relative path."""
        return {track.path: track.after for track in report.tracks}

    def status(self, outcome: lr.TrackOutcome | None) -> lr.OutcomeStatus | None:
        """*outcome*'s status, or None when the track was never reached."""
        return None if outcome is None else outcome.status


class BarrierTest(RunCase):
    """Nothing beside an MP3 changes until every track is backed up and the lock held."""

    def test_the_first_write_finds_the_manifest_backed_up_under_the_lock(self) -> None:
        self.build()
        seen: list[tuple[str, bool, int]] = []

        def embed(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            locked = run_fakes.lock_held(self.album)
            backed = len(self.folder.backed_up[0][1]) if self.folder.backed_up else 0
            seen.append((self.folder.state_on_disk(self.album), locked, backed))
            return lyrics_tags.write_lyrics(path, lyrics, lang)

        self.run_album(run_fakes.make_stages(self.folder, write_lyrics=embed))
        self.assertEqual(seen[0], (str(lr.RunState.BACKED_UP), True, len(TRACKS)))

    def test_a_sidecar_rename_also_waits_for_the_backup(self) -> None:
        self.build((TrackSpec("01 Keeper.mp3", lrc=fixtures.LIGHTHOUSE_PLAIN),))
        states: list[str] = []

        def normalize(mp3: Path, *, album: Path, apply: bool) -> lm.SidecarState:
            # Only the applied normalize renames; the run also plans one first, to
            # tell a stored placeholder, which changes nothing.
            if apply:
                states.append(self.folder.state_on_disk(self.album))
            return lyrics_sidecar.normalize_sidecars(mp3, album=album, apply=apply)

        self.run_album(run_fakes.make_stages(self.folder, normalize_sidecars=normalize))
        self.assertEqual(states, [str(lr.RunState.BACKED_UP)])

    def test_a_held_lock_stops_the_run_before_any_backup(self) -> None:
        self.build()
        held = run_fakes.acquire(run_fakes.lock_path(self.album))
        assert held is not None
        self.addCleanup(held.release)
        with self.assertRaises(lyrics_album.LockHeldError):
            lyrics_run.run_album(
                self.album,
                lyrics_album.discover_tracks(self.album).tracks,
                {},
                run_fakes.options(),
                run_fakes.make_stages(self.folder),
                acquire_lock=run_fakes.acquire,
                started=run_fakes.STARTED,
                progress=self.progress.append,
            )
        self.assertEqual(self.folder.backed_up, [])

    def test_the_run_ends_complete_and_unlocked(self) -> None:
        self.build()
        report = self.run_album()
        self.assertEqual(self.folder.state_on_disk(self.album), "complete")
        self.assertFalse(run_fakes.lock_held(self.album))
        self.assertEqual((report.run_id, report.interrupted), (run_fakes.RUN_ID, False))

    def test_the_manifest_is_written_as_each_track_finishes(self) -> None:
        self.build()
        self.run_album()
        filled = [
            sum(t.after is not None for t in m.tracks) for m in self.folder.manifests
        ]
        self.assertEqual(filled, [0, 1, 2, 3, 3])
        self.assertEqual(self.folder.manifests[-1].state, lr.RunState.COMPLETE)


class OutcomeTest(RunCase):
    """What each track ends as, and what the summary is told about it."""

    def test_a_mixed_album_ends_written_written_and_not_found(self) -> None:
        self.build()
        outcomes = self.outcomes(self.run_album())
        keeper, orchard, mill = outcomes.values()
        assert keeper is not None and orchard is not None and mill is not None
        self.assertEqual(
            (keeper.status, keeper.source, keeper.kind, keeper.sidecar_written),
            (
                lr.OutcomeStatus.WRITTEN,
                lm.Source.LRCLIB,
                lm.LyricsKind.SYNCED,
                "01 Keeper.lrc",
            ),
        )
        self.assertEqual(
            (orchard.kind, orchard.frames_written), (lm.LyricsKind.UNSYNCED, True)
        )
        self.assertEqual(mill.status, lr.OutcomeStatus.NOT_FOUND)
        assert keeper.check is not None
        self.assertEqual(keeper.check.verdict, lr.CheckVerdict.PASS)

    def test_a_track_with_lyrics_that_finds_nothing_is_served_by_them(self) -> None:
        (mp3,) = self.build((TrackSpec("01 Mill.mp3", title="The Mill"),))
        lyrics_tags.write_lyrics(mp3, lm.PlainLyrics(fixtures.ORCHARD_PLAIN), "eng")
        before = mp3.read_bytes()
        (outcome,) = self.outcomes(self.run_album()).values()
        assert outcome is not None
        self.assertEqual(
            (outcome.status, outcome.source),
            (lr.OutcomeStatus.WRITTEN, lm.Source.FRAMES),
        )
        self.assertEqual(mp3.read_bytes(), before)
        self.assertEqual(
            lm.lyrics_from_frames(lyrics_tags.read_tags(mp3).frames, "eng"),
            lm.PlainLyrics(fixtures.ORCHARD_PLAIN),
        )

    def test_a_timed_sidecar_is_embedded_and_left_as_it_was(self) -> None:
        (mp3,) = self.build((TrackSpec("01 Keeper.mp3", lrc=fixtures.LIGHTHOUSE_LRC),))
        sidecar = mp3.with_suffix(".lrc").read_bytes()
        (outcome,) = self.outcomes(self.run_album(providers={})).values()
        assert outcome is not None
        self.assertEqual(
            (outcome.source, outcome.sidecar_written), (lm.Source.SIDECAR, None)
        )
        self.assertEqual(mp3.with_suffix(".lrc").read_bytes(), sidecar)
        self.assertIsNotNone(
            lm.lyrics_from_frames(lyrics_tags.read_tags(mp3).frames, "eng")
        )

    def test_the_sidecar_changes_are_recorded_for_undo(self) -> None:
        self.build((TrackSpec("01 Keeper.mp3", title="The Keeper", txt="old words"),))
        (outcome,) = self.outcomes(self.run_album()).values()
        assert outcome is not None
        self.assertEqual(
            (outcome.sidecar_written, outcome.sidecars_removed),
            ("01 Keeper.lrc", ("01 Keeper.txt",)),
        )

    def test_a_stripped_query_is_marked_only_when_it_found_lyrics(self) -> None:
        self.build(
            (
                TrackSpec("01 Keeper.mp3", title="The Keeper (Live)"),
                TrackSpec("02 Mill.mp3", title="The Mill (Live)"),
            )
        )
        keeper, mill = self.outcomes(self.run_album()).values()
        assert keeper is not None and mill is not None
        self.assertEqual(keeper.query, (lm.QueryMarker.STRIPPED,))
        self.assertEqual((mill.status, mill.query), (lr.OutcomeStatus.NOT_FOUND, ()))

    def test_a_track_missing_a_tag_is_searched_by_its_file_name(self) -> None:
        self.build((TrackSpec("The Keeper.mp3", artist=None),))
        providers = run_fakes.table(SYNCED)
        (outcome,) = self.outcomes(self.run_album(providers=providers)).values()
        assert outcome is not None
        self.assertEqual(
            (outcome.status, outcome.query),
            (lr.OutcomeStatus.WRITTEN, (lm.QueryMarker.FILENAME,)),
        )

    def test_every_provider_failing_is_the_tracks_fetch_error(self) -> None:
        self.build((TrackSpec("01 Mill.mp3", title="The Mill"),))
        broken = fixtures.raising(OSError("refused"))
        providers = {key: broken for key in run_fakes.table()}
        report = self.run_album(providers=providers)
        (outcome,) = self.outcomes(report).values()
        assert outcome is not None and outcome.error is not None
        self.assertEqual(
            outcome.error.summary(), "ERROR fetch: every provider failed (2 errors)"
        )
        self.assertTrue(report.has_errors)

    def test_a_no_artist_track_whose_only_asked_provider_fails_is_a_fetch_error(
        self,
    ) -> None:
        # The LRC steps skip a query with no artist, so Genius is the only provider
        # asked; its failure is every provider failing, not "not found".
        self.build((TrackSpec("The Mill.mp3", artist=None),))
        providers: dict[lm.ProviderKey, lm.Provider] = {
            **lyrics_table.build_provider_table(None),
            lm.ProviderKey(lm.Source.GENIUS, lm.LyricsKind.UNSYNCED): (
                fixtures.raising(OSError("refused"))
            ),
        }
        sent: list[str] = []

        def answer(request: requests.PreparedRequest) -> requests.Response:
            sent.append(request.url or "")
            return http_fakes.response(request, 404)

        with http_fakes.transport(answer):
            report = self.run_album(providers=providers)
        self.assertEqual(sent, [])
        (outcome,) = self.outcomes(report).values()
        assert outcome is not None and outcome.error is not None
        self.assertEqual(
            outcome.error.summary(), "ERROR fetch: every provider failed (1 error)"
        )

    def test_a_timeout_is_counted_apart_from_an_error(self) -> None:
        self.assertEqual(
            lyrics_outcomes.describe_failures(
                (
                    lm.AttemptOutcome.TIMEOUT,
                    lm.AttemptOutcome.ERROR,
                    lm.AttemptOutcome.ERROR,
                )
            ),
            "every provider failed (1 timeout, 2 errors)",
        )

    def test_a_clean_run_has_no_errors(self) -> None:
        self.build()
        self.assertFalse(self.run_album().has_errors)


class ErrorBoundaryTest(RunCase):
    """One track's failure is recorded against it, and the others still complete."""

    def _failing_on(self, title: str, error: Exception) -> lyrics_stages.Stages:
        """Stages whose fetch raises *error* for *title* and runs for the rest."""

        def fetch(
            query: lm.TrackQuery,
            sidecar: lm.SidecarState,
            providers: lm.ProviderTable,
            # The real fetch's keywords, passed through whatever they are.
            **options: Any,
        ) -> lm.FetchResult:
            if query.title == title:
                raise error
            return lyrics_fetch.fetch_lyrics(query, sidecar, providers, **options)

        return run_fakes.make_stages(self.folder, fetch=fetch)

    def test_a_fetch_raising_fails_only_its_track(self) -> None:
        self.build()
        report = self.run_album(self._failing_on("The Keeper", RuntimeError("boom")))
        keeper, orchard, mill = self.outcomes(report).values()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(
            (keeper.error.stage, keeper.error.exc_type, keeper.error.message),
            (lr.ErrorStage.FETCH, "RuntimeError", "RuntimeError"),
        )
        self.assertEqual(
            [self.status(orchard), self.status(mill)],
            [lr.OutcomeStatus.WRITTEN, lr.OutcomeStatus.NOT_FOUND],
        )

    def test_an_embed_raising_fails_only_its_track(self) -> None:
        self.build()

        def embed(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            if path.name.startswith("01"):
                raise lm.ReadBackError("frames differ after the save")
            return lyrics_tags.write_lyrics(path, lyrics, lang)

        report = self.run_album(run_fakes.make_stages(self.folder, write_lyrics=embed))
        keeper, orchard, _mill = self.outcomes(report).values()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(
            keeper.error.summary(), "ERROR embed: frames differ after the save"
        )
        self.assertFalse(keeper.frames_written)
        self.assertFalse((self.album / "01 Keeper.lrc").exists())
        self.assertEqual(self.status(orchard), lr.OutcomeStatus.WRITTEN)

    def test_an_undecodable_sidecar_is_that_tracks_sidecar_error(self) -> None:
        (mp3, _other) = self.build(
            (
                TrackSpec("01 Keeper.mp3"),
                TrackSpec("02 Orchard.mp3", title="The Orchard"),
            )
        )
        mp3.with_suffix(".txt").write_bytes(b"\xff\xfe\xfa not utf-8")
        keeper, orchard = self.outcomes(self.run_album()).values()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(keeper.error.stage, lr.ErrorStage.SIDECAR)
        self.assertEqual(self.status(orchard), lr.OutcomeStatus.WRITTEN)

    def test_a_check_raising_keeps_what_was_written(self) -> None:
        self.build((TrackSpec("01 Keeper.mp3", title="The Keeper"),))

        def check(
            embedded: lm.Lyrics | None,
            backup: lm.Lyrics | None,
            duration_s: float | None,
        ) -> lr.CheckResult:
            raise ValueError("no duration")

        report = self.run_album(run_fakes.make_stages(self.folder, check=check))
        (outcome,) = self.outcomes(report).values()
        assert outcome is not None and outcome.error is not None
        self.assertEqual(
            (outcome.error.stage, outcome.frames_written), (lr.ErrorStage.CHECK, True)
        )
        self.assertEqual(outcome.source, lm.Source.LRCLIB)

    def test_a_failed_backup_leaves_its_track_untouched(self) -> None:
        paths = self.build()
        self.folder.fail = frozenset({"01 Keeper.mp3"})
        before = paths[0].read_bytes()
        report = self.run_album()
        keeper, orchard, _mill = self.outcomes(report).values()
        assert keeper is not None and keeper.error is not None
        self.assertEqual(keeper.error.stage, lr.ErrorStage.BACKUP)
        self.assertEqual(paths[0].read_bytes(), before)
        self.assertEqual(self.status(orchard), lr.OutcomeStatus.WRITTEN)

    def test_a_failed_backup_keeps_the_error_the_backup_recorded(self) -> None:
        self.build((TrackSpec("01 Keeper.mp3"),))
        recorded = lr.TrackOutcome(
            lr.OutcomeStatus.ERROR,
            error=lr.StageError(lr.ErrorStage.BACKUP, "OSError", "unreadable"),
        )
        real_back_up = self.folder.back_up_album

        def back_up(
            album: Path,
            tracks: Sequence[Path],
            *,
            lang: str,
            genius: lr.GeniusState,
            started: dt.datetime,
            bandcamp: lr.BandcampState = lr.BandcampState.OFF,
            youtube_music: lr.CoolingState = lr.CoolingState.OFF,
            tekstowo: lr.CoolingState = lr.CoolingState.OFF,
            silence_check: lr.SilenceCheck = lr.SilenceCheck.OFF,
        ) -> lr.Manifest:
            manifest = real_back_up(
                album,
                tracks,
                lang=lang,
                genius=genius,
                started=started,
                bandcamp=bandcamp,
                youtube_music=youtube_music,
                tekstowo=tekstowo,
                silence_check=silence_check,
            )
            failed = lr.ManifestTrack("01 Keeper.mp3", None, None, recorded)
            return dataclasses.replace(manifest, tracks=(failed,))

        report = self.run_album(run_fakes.make_stages(self.folder, back_up=back_up))
        self.assertEqual(report.tracks[0].after, recorded)


class InterruptTest(RunCase):
    """Ctrl-C during a save is honored only after the save returns; the run stops cleanly."""

    def test_an_interrupt_inside_a_save_waits_for_it_then_stops_the_run(self) -> None:
        self.build()
        saves: list[str] = []

        def embed(path: Path, lyrics: lm.Lyrics, lang: str) -> bool:
            signal.raise_signal(signal.SIGINT)
            saved = lyrics_tags.write_lyrics(path, lyrics, lang)
            saves.append(path.name)
            return saved

        report = self.run_album(run_fakes.make_stages(self.folder, write_lyrics=embed))
        keeper, orchard, mill = self.outcomes(report).values()
        self.assertTrue(report.interrupted)
        self.assertEqual(saves, ["01 Keeper.mp3"])
        assert keeper is not None
        self.assertTrue(keeper.frames_written)
        self.assertEqual(keeper.sidecar_written, "01 Keeper.lrc")
        self.assertEqual((orchard, mill), (None, None))
        self.assertEqual(self.folder.state_on_disk(self.album), "backed_up")
        self.assertFalse(run_fakes.lock_held(self.album))

    def test_an_interrupt_during_a_fetch_records_the_sidecar_changes_made(self) -> None:
        self.build((TrackSpec("01 Keeper.mp3", lrc=fixtures.LIGHTHOUSE_PLAIN),))

        def fetch(*args: object, **kwargs: object) -> lm.FetchResult:
            raise KeyboardInterrupt

        report = self.run_album(run_fakes.make_stages(self.folder, fetch=fetch))
        (outcome,) = self.outcomes(report).values()
        assert outcome is not None and outcome.error is not None
        self.assertTrue(report.interrupted)
        self.assertEqual(
            outcome.sidecars_renamed,
            (lm.SidecarRename("01 Keeper.lrc", "01 Keeper.txt"),),
        )
        self.assertEqual(
            (outcome.error.stage, outcome.error.exc_type),
            (lr.ErrorStage.FETCH, "KeyboardInterrupt"),
        )


class ProgressTest(RunCase):
    """One "backing up" line, then one progress line per finished track, in order."""

    def test_one_line_per_track_in_order(self) -> None:
        self.build()
        self.run_album()
        self.assertEqual(
            self.progress,
            [
                "backing up 3 tracks",
                "[1/3] 01 Keeper.mp3: synced (LRCLIB)",
                "[2/3] 02 Orchard.mp3: unsynced (LRCLIB)",
                "[3/3] 03 Mill.mp3: not found",
            ],
        )

    def test_a_one_track_run_backs_up_1_track_not_tracks(self) -> None:
        # Regression: the line once used a fixed plural.
        self.build((TrackSpec("01 Keeper.mp3", title="The Keeper"),))
        self.run_album()
        self.assertEqual(self.progress[0], "backing up 1 track")
