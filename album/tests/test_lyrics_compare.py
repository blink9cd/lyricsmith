"""compare_album: each untimed-only track's lyrics against every provider's answer.

Each test builds an album of silent MP3s with invented lyrics beside them and calls
`compare_album` with the real stages (`run_fakes.make_stages`) and a provider table
built by hand, so no test reaches the network. Every title and lyric is invented.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_answer  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

LRCLIB, NETEASE, GENIUS = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS
ANSWERED, EMPTY = lm.AttemptOutcome.ANSWERED, lm.AttemptOutcome.EMPTY
ERROR = lm.AttemptOutcome.ERROR
SYNCED_KEY = lm.ProviderKey(LRCLIB, lm.LyricsKind.SYNCED)
#: Each compared step's key, by source.
KEYS = {
    source: lm.ProviderKey(source, lm.LyricsKind.UNSYNCED) for source in lc.COMPARED
}
#: A track holding untimed lyrics alone, in a ``.txt``; its title is in no lyric here.
KEEPER = TrackSpec("01 Keeper.mp3", txt=fixtures.LIGHTHOUSE_PLAIN)
NO_TITLE_WORD = ("no title word",)
#: What a provider answers: text, nothing (None), or an error it raises.
Answer = str | None | Exception


def provider(answer: Answer) -> fixtures.FakeProvider:
    """A plain fake answering *answer*: its text, nothing, or the error raised."""
    if isinstance(answer, Exception):
        return fixtures.raising(answer)
    return fixtures.answering(answer) if answer is not None else fixtures.empty()


def three(lrclib: Answer, netease: Answer, genius: Answer) -> lm.ProviderTable:
    """A table answering every compared step with fixed answers."""
    return {
        KEYS[LRCLIB]: provider(lrclib),
        KEYS[NETEASE]: provider(netease),
        KEYS[GENIUS]: provider(genius),
    }


@dataclass
class EveryKind:
    """An LRC-shaped provider: one call answers every kind, and it skips artistless queries.

    *asks_error*, when set, is raised by `asks`.
    """

    answer: lyrics_answer.LrcAnswer
    asks_error: Exception | None = None
    calls: list[lm.TrackQuery] = field(default_factory=list)

    def asks(self, query: lm.TrackQuery) -> bool:
        """False for a query with no artist, as the real LRC adapters say."""
        if self.asks_error is not None:
            raise self.asks_error
        return bool(query.artist)

    def answer_all_kinds(self, query: lm.TrackQuery) -> lyrics_answer.LrcAnswer:
        """Record *query*; the fixed answer."""
        self.calls.append(query)
        return self.answer

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Never called: the chain asks an every-kind provider through `answer_all_kinds`."""
        raise AssertionError("an every-kind provider was asked as a plain one")


class CompareCase(fixtures.IsolatedCase):
    """An album under the test's directory, compared with the real stages."""

    def setUp(self) -> None:
        """Name the album; each test builds its own tracks."""
        super().setUp()
        self.album = self.tmp / "album"

    def build(self, *tracks: TrackSpec) -> list[Path]:
        """Build *tracks* under the album; their MP3s, sorted."""
        return fixtures.build_album(self.album, tracks)

    def compare(
        self,
        table: lm.ProviderTable,
        tracks: list[Path],
        stages: lyrics_stages.Stages | None = None,
    ) -> lc.CompareReport:
        """Compare *tracks* against *table*'s answers."""
        return lc.compare_album(
            self.album,
            tracks,
            table,
            stages or run_fakes.make_stages(run_fakes.FakeRunFolder()),
            lang=run_fakes.LANG,
            deadline_s=run_fakes.DEADLINE_S,
            notice=self.fail,
        )

    def one(
        self, table: lm.ProviderTable, track: TrackSpec = KEEPER
    ) -> lc.ComparedTrack:
        """The one row of an album holding *track* alone."""
        report = self.compare(table, self.build(track))
        self.assertEqual(len(report.tracks), 1)
        return report.tracks[0]


class VerdictTest(CompareCase):
    """Any one answer at or above the check's similarity agrees; the rest are told apart."""

    def test_one_agreeing_answer_agrees_and_hides_the_hints(self) -> None:
        orchard = fixtures.ORCHARD_PLAIN
        row = self.one(three(orchard, fixtures.LIGHTHOUSE_EDITED, orchard))
        self.assertEqual(row.path, "01 Keeper.mp3")
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)
        self.assertEqual([s.source for s in row.scores], [LRCLIB, NETEASE, GENIUS])
        similarities = [s.similarity or 0.0 for s in row.scores]
        self.assertGreaterEqual(similarities[1], 0.6)
        self.assertLess(max(similarities[0], similarities[2]), 0.6)
        self.assertEqual((row.hints, row.reason, row.error), ((), None, None))

    def test_every_answer_another_song_is_no_source_agrees(self) -> None:
        orchard = fixtures.ORCHARD_PLAIN
        row = self.one(three(orchard, orchard, orchard))
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SOURCE_AGREES)
        self.assertTrue(all(s.outcome is ANSWERED for s in row.scores))
        self.assertTrue(all((s.similarity or 1.0) < 0.6 for s in row.scores))
        self.assertEqual(row.hints, NO_TITLE_WORD)

    def test_no_answer_is_no_second_source(self) -> None:
        row = self.one(three(None, None, None))
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SECOND_SOURCE)
        self.assertIsNone(row.reason)
        self.assertEqual([s.outcome for s in row.scores], [EMPTY] * 3)
        self.assertEqual([s.similarity for s in row.scores], [None] * 3)
        self.assertEqual(row.hints, NO_TITLE_WORD)

    def test_a_failure_beside_nothing_found_is_no_second_source(self) -> None:
        row = self.one(three(OSError("refused"), None, None))
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SECOND_SOURCE)
        self.assertIsNone(row.error)

    def test_a_track_no_provider_is_asked_for_says_so(self) -> None:
        answer = lyrics_answer.LrcAnswer.same(fixtures.LIGHTHOUSE_PLAIN)
        lrclib, netease = EveryKind(answer), EveryKind(answer)
        table = {KEYS[LRCLIB]: lrclib, KEYS[NETEASE]: netease}
        report = self.compare(
            table,
            self.build(
                TrackSpec("01 Keeper.mp3", artist="", txt=fixtures.LIGHTHOUSE_PLAIN)
            ),
        )
        row = report.tracks[0]
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SECOND_SOURCE)
        self.assertEqual((row.reason, row.scores), ("no provider asked", ()))
        self.assertEqual(lrclib.calls + netease.calls, [])
        self.assertEqual(
            lyrics_tally.format_tally(report.providers),
            "providers: LRCLIB 0/0 · NetEase 0/0 · Bandcamp - · YouTube Music - · Genius - "
            "· Tekstowo -",
        )

    def test_genius_alone_asked_for_an_artistless_track_judges_it(self) -> None:
        artistless = TrackSpec(
            "01 Keeper.mp3", artist="", txt=fixtures.LIGHTHOUSE_PLAIN
        )
        skipped = lyrics_answer.LrcAnswer.same(fixtures.ORCHARD_PLAIN)
        for genius, verdict in (
            (fixtures.LIGHTHOUSE_EDITED, lc.CompareVerdict.AGREES),
            (fixtures.ORCHARD_PLAIN, lc.CompareVerdict.NO_SOURCE_AGREES),
        ):
            with self.subTest(verdict=verdict):
                lrclib, netease = EveryKind(skipped), EveryKind(skipped)
                table: lm.ProviderTable = {
                    KEYS[LRCLIB]: lrclib,
                    KEYS[NETEASE]: netease,
                    KEYS[GENIUS]: provider(genius),
                }
                row = self.compare(table, self.build(artistless)).tracks[0]
                self.assertEqual(lrclib.calls + netease.calls, [])
                self.assertEqual([s.source for s in row.scores], [GENIUS])
                self.assertIs(row.verdict, verdict)

    def test_every_provider_failing_is_the_fetch_error(self) -> None:
        slow = lyrics_providers.ProviderTimeout("no answer")
        report = self.compare(
            three(OSError("a"), slow, TimeoutError("c")), self.build(KEEPER)
        )
        row = report.tracks[0]
        self.assertIsNone(row.verdict)
        # Worded as a run words its own fetch error (`lyrics_outcomes`).
        message = "every provider failed (1 timeout, 2 errors)"
        error = lr.StageError(lr.ErrorStage.FETCH, "FetchError", message)
        self.assertEqual(row.error, error)
        timeout = lm.AttemptOutcome.TIMEOUT
        self.assertEqual([s.outcome for s in row.scores], [ERROR, timeout, ERROR])
        self.assertTrue(report.has_errors)

    def test_an_interrupt_a_call_raises_ends_the_compare(self) -> None:
        """A `KeyboardInterrupt` from a provider's own code is no row: it ends compare."""
        stopped = fixtures.raising(KeyboardInterrupt())
        table: lm.ProviderTable = {**three(None, None, None), KEYS[LRCLIB]: stopped}
        with self.assertRaises(KeyboardInterrupt):
            self.compare(table, self.build(KEEPER))

    def test_one_failing_beside_one_agreeing_agrees(self) -> None:
        report = self.compare(
            three(OSError("refused"), fixtures.LIGHTHOUSE_EDITED, None),
            self.build(KEEPER),
        )
        row = report.tracks[0]
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)
        self.assertEqual(
            (row.scores[0].outcome, row.scores[0].similarity), (ERROR, None)
        )
        self.assertFalse(report.has_errors)


class AskingTest(CompareCase):
    """Each provider is asked on its own, with the stripped retry, its every-kind call once."""

    def test_a_title_found_only_stripped_is_judged_by_that_answer(self) -> None:
        table = run_fakes.table(plain={"The Mill": fixtures.LIGHTHOUSE_EDITED})
        mill = TrackSpec(
            "01 Mill.mp3", title="The Mill (Live)", txt=fixtures.LIGHTHOUSE_PLAIN
        )
        row = self.one(table, mill)
        plain = table[KEYS[LRCLIB]]
        assert isinstance(plain, run_fakes.TitleProvider)
        self.assertEqual(
            [q.title for q in plain.calls], ["The Mill (Live)", "The Mill"]
        )
        self.assertEqual([q.stripped for q in plain.calls], [False, True])
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)
        self.assertEqual(row.scores[0].outcome, ANSWERED)
        synced = table[SYNCED_KEY]
        assert isinstance(synced, run_fakes.TitleProvider)
        self.assertEqual(synced.calls, [])

    def test_an_every_kind_provider_is_called_once_for_its_untimed_step(self) -> None:
        lrclib = EveryKind(
            lyrics_answer.LrcAnswer(synced=None, plain=fixtures.LIGHTHOUSE_EDITED)
        )
        row = self.one({KEYS[LRCLIB]: lrclib})
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)
        self.assertEqual(len(lrclib.calls), 1)


class JudgedTextTest(CompareCase):
    """Only untimed-only tracks are judged, by the text a run would serve."""

    def test_a_synced_track_and_a_bare_track_are_not_judged(self) -> None:
        table = three(fixtures.LIGHTHOUSE_PLAIN, None, None)
        report = self.compare(
            table,
            self.build(
                TrackSpec("01 Synced.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
                TrackSpec("02 Bare.mp3"),
            ),
        )
        verdicts = [(t.path, t.verdict, t.scores) for t in report.tracks]
        not_judged = lc.CompareVerdict.NOT_JUDGED
        self.assertEqual(
            verdicts,
            [("01 Synced.mp3", not_judged, ()), ("02 Bare.mp3", not_judged, ())],
        )
        self.assertEqual([p.calls for p in table.values()], [[], [], []])  # type: ignore[attr-defined] # the table's fakes record their calls

    def test_a_txt_that_is_not_utf8_is_the_sidecar_error(self) -> None:
        [mp3] = self.build(TrackSpec("01 Keeper.mp3"))
        mp3.with_suffix(".txt").write_bytes(b"\xff\xfe")
        report = self.compare(three(None, None, None), [mp3])
        row = report.tracks[0]
        assert row.error is not None
        self.assertIs(row.error.stage, lr.ErrorStage.SIDECAR)
        self.assertIsNone(row.verdict)
        self.assertTrue(report.has_errors)

    def assert_run_sidecar_error(self, mp3: Path) -> None:
        """Compare's row for *mp3* is the sidecar error a run's sidecar stage raises."""
        with self.assertRaises(lyrics_sidecar.UNREADABLE) as raised:
            lyrics_sidecar.plan_sidecars(mp3, self.album)
        error = raised.exception
        row = self.compare(three(None, None, None), [mp3]).tracks[0]
        expected = lr.StageError(
            lr.ErrorStage.SIDECAR, type(error).__name__, str(error)
        )
        self.assertEqual((row.verdict, row.error), (None, expected))

    def test_an_lrc_that_is_not_utf8_is_the_sidecar_error(self) -> None:
        [mp3] = self.build(TrackSpec("01 Keeper.mp3"))
        mp3.with_suffix(".lrc").write_bytes(b"\xff\xfe")
        self.assert_run_sidecar_error(mp3)

    def test_a_txt_past_the_lyrics_cap_is_the_sidecar_error(self) -> None:
        too_large = "la " * (lr.MAX_LYRICS_CHARS // 3 + 1)
        [mp3] = self.build(TrackSpec("01 Keeper.mp3", txt=too_large))
        self.assert_run_sidecar_error(mp3)

    def test_the_txt_is_judged_before_the_frames(self) -> None:
        [mp3] = self.build(KEEPER)
        tag_builders.plant_frames(mp3, [tag_builders.uslt(fixtures.ORCHARD_PLAIN)])
        row = self.compare(three(fixtures.LIGHTHOUSE_EDITED, None, None), [mp3]).tracks[
            0
        ]
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)

    def test_untimed_frames_alone_are_judged(self) -> None:
        [mp3] = self.build(TrackSpec("01 Orchard.mp3"))
        tag_builders.plant_frames(mp3, [tag_builders.uslt(fixtures.ORCHARD_PLAIN)])
        row = self.compare(three(fixtures.ORCHARD_PLAIN, None, None), [mp3]).tracks[0]
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)

    def test_a_real_uslt_ranked_below_a_placeholder_is_judged(self) -> None:
        # The run serves the real frame, so it is the text judged, not the notice
        # ("pure music, please enjoy") that ranks above it.
        [mp3] = self.build(TrackSpec("01 Orchard.mp3"))
        notice = tag_builders.uslt("\u7eaf\u97f3\u4e50\uff0c\u8bf7\u6b23\u8d4f")
        real = tag_builders.uslt(fixtures.ORCHARD_PLAIN, desc="alt")
        tag_builders.plant_frames(mp3, [notice, real])
        row = self.compare(three(fixtures.ORCHARD_PLAIN, None, None), [mp3]).tracks[0]
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)


class TrackErrorTest(CompareCase):
    """A failure is that track's row, at the stage it happened in; the next track runs."""

    def test_a_tag_read_failure_is_the_tracks_error_and_the_album_goes_on(self) -> None:
        def unreadable(path: Path) -> lm.TrackTags:
            raise lm.TagVersionError(f"no tag in {path.name}")

        stages = run_fakes.make_stages(run_fakes.FakeRunFolder(), read_tags=unreadable)
        tracks = self.build(KEEPER, TrackSpec("02 Bare.mp3"))
        report = self.compare(three(None, None, None), tracks, stages)
        first = report.tracks[0].error
        self.assertEqual(
            first,
            lr.StageError(
                lr.ErrorStage.FETCH, "TagVersionError", "no tag in 01 Keeper.mp3"
            ),
        )
        self.assertEqual(len(report.tracks), 2)

    def test_a_sidecar_stage_failure_is_the_sidecar_error(self) -> None:
        def broken(mp3: Path, *, album: Path, apply: bool) -> lm.SidecarState:
            raise OSError("disk gone")

        stages = run_fakes.make_stages(
            run_fakes.FakeRunFolder(), normalize_sidecars=broken
        )
        row = self.compare(three(None, None, None), self.build(KEEPER), stages).tracks[
            0
        ]
        self.assertEqual(
            row.error, lr.StageError(lr.ErrorStage.SIDECAR, "OSError", "disk gone")
        )

    def test_a_failure_building_the_query_is_a_fetch_error(self) -> None:
        failing = mock.patch.object(lc, "track_query", side_effect=ValueError("bad"))
        with failing:
            row = self.one(three(None, None, None))
        self.assertEqual(
            row.error, lr.StageError(lr.ErrorStage.FETCH, "ValueError", "bad")
        )

    def test_a_failure_while_asking_shows_its_type_alone(self) -> None:
        lrclib = EveryKind(
            lyrics_answer.LrcAnswer.same(None), ValueError("token=secret")
        )
        row = self.one({KEYS[LRCLIB]: lrclib})
        self.assertEqual(
            row.error, lr.StageError(lr.ErrorStage.FETCH, "ValueError", "ValueError")
        )


class TallyTest(CompareCase):
    """Each provider's tracks whose answer agreed, over the tracks it was asked for."""

    def test_agreed_over_asked_with_failures_refused(self) -> None:
        lrclib = run_fakes.TitleProvider(
            {"Keeper": fixtures.LIGHTHOUSE_EDITED, "Orchard": fixtures.LIGHTHOUSE_PLAIN}
        )
        netease = run_fakes.TitleProvider({"Keeper": OSError("refused")})
        tracks = self.build(
            TrackSpec("01 Keeper.mp3", title="Keeper", txt=fixtures.LIGHTHOUSE_PLAIN),
            TrackSpec("02 Orchard.mp3", title="Orchard", txt=fixtures.ORCHARD_PLAIN),
        )
        report = self.compare({KEYS[LRCLIB]: lrclib, KEYS[NETEASE]: netease}, tracks)
        self.assertEqual(
            lyrics_tally.format_tally(report.providers),
            "providers: LRCLIB 1/2 · NetEase 0/2 (1 refused) · Bandcamp - · "
            "YouTube Music - · Genius - · Tekstowo -",
        )
        self.assertEqual(
            [t.verdict for t in report.tracks],
            [lc.CompareVerdict.AGREES, lc.CompareVerdict.NO_SOURCE_AGREES],
        )


class WritesNothingTest(CompareCase):
    """Compare leaves every file as it was and makes no run folder."""

    def test_every_file_is_byte_identical_afterwards(self) -> None:
        tracks = self.build(
            KEEPER,
            TrackSpec("02 Synced.mp3", lrc=fixtures.LIGHTHOUSE_LRC),
            TrackSpec("03 Bare.mp3"),
        )
        tag_builders.plant_frames(
            tracks[2], [tag_builders.uslt(fixtures.ORCHARD_PLAIN)]
        )
        before = {p: p.read_bytes() for p in self.album.rglob("*") if p.is_file()}
        self.compare(
            three(fixtures.LIGHTHOUSE_EDITED, None, fixtures.ORCHARD_PLAIN), tracks
        )
        after = {p: p.read_bytes() for p in self.album.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.album / lr.RUN_FOLDER_NAME).exists())
