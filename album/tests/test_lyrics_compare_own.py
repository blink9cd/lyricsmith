"""Compare sets aside the provider a track's lyrics came from (its own lyrics source).

Each test builds an album of silent MP3s with invented lyrics beside them, writes real
run manifests into its run folders, and calls `compare_album` with the real stages and
the real manifest reader. The set-aside provider is a fake that fails the test when it
is called, so "not asked" is proven by behavior. Every title and lyric is invented.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import cast

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest as lmf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_summary  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

LRCLIB, NETEASE, GENIUS = lm.Source.LRCLIB, lm.Source.NETEASE, lm.Source.GENIUS
MUSIXMATCH = lm.Source.MUSIXMATCH
KEYS = {
    source: lm.ProviderKey(source, lm.LyricsKind.UNSYNCED) for source in lc.COMPARED
}
KEEPER = TrackSpec("01 Keeper.mp3", title="Keeper", txt=fixtures.LIGHTHOUSE_PLAIN)
_RUN = "20261001T000000Z"
PROG = "album.py"


class MustNotAsk:
    """A provider that fails the test when it is called."""

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Fail: compare asked the track's own source."""
        raise AssertionError("compare asked the track's own source")


def entry(path: str, source: lm.Source) -> lr.ManifestTrack:
    """A manifest entry saying *source* wrote lyrics to *path*."""
    after = lr.TrackOutcome(lr.OutcomeStatus.WRITTEN, source=source)
    return lr.ManifestTrack(path, (2, 4), None, after, False)


def write_run(album: Path, run_id: str, *tracks: lr.ManifestTrack) -> None:
    """Write run *run_id* under *album* holding a manifest of *tracks*."""
    run_dir = album / lr.RUN_FOLDER_NAME / run_id
    run_dir.mkdir(parents=True)
    state = lr.RunState.COMPLETE
    manifest = lr.Manifest(run_id, "eng", state, lr.GeniusState.ABSENT, tracks)
    lmf.write_manifest(run_dir, manifest)


def provider(answer: object) -> object:
    """A fake for *answer*: a provider as is, text answering it, an error raised, else nothing."""
    if isinstance(answer, str):
        return fixtures.answering(answer)
    if isinstance(answer, Exception):
        return fixtures.raising(answer)
    return fixtures.empty() if answer is None else answer


def table(
    lrclib: object, netease: object, genius: object, *, only: bool = False
) -> lm.ProviderTable:
    """A table of the given answers or providers; with *only*, just the first."""
    full = {
        KEYS[LRCLIB]: provider(lrclib),
        KEYS[NETEASE]: provider(netease),
        KEYS[GENIUS]: provider(genius),
    }
    return cast(lm.ProviderTable, {KEYS[LRCLIB]: full[KEYS[LRCLIB]]} if only else full)


class OwnCase(fixtures.IsolatedCase):
    """An album compared with the real stages and the real manifest reader."""

    def setUp(self) -> None:
        """Name the album; each test builds its tracks and manifests."""
        super().setUp()
        self.album = self.tmp / "album"

    def compare(
        self, providers: lm.ProviderTable, *tracks: TrackSpec
    ) -> lc.CompareReport:
        """Compare the album built from *tracks*."""
        paths = fixtures.build_album(self.album, tracks)
        stages = dataclasses.replace(
            run_fakes.make_stages(run_fakes.FakeRunFolder()),
            read_manifest=lmf.read_manifest,
        )
        return lc.compare_album(
            self.album,
            paths,
            providers,
            stages,
            lang=run_fakes.LANG,
            deadline_s=run_fakes.DEADLINE_S,
            notice=self.fail,
        )

    def one(
        self, providers: lm.ProviderTable, source: lm.Source | None
    ) -> lc.ComparedTrack:
        """The row of the keeper track whose recorded source is *source*."""
        self.album.mkdir(parents=True, exist_ok=True)
        if source is not None:
            write_run(self.album, _RUN, entry(KEEPER.relpath, source))
        [row] = self.compare(providers, KEEPER).tracks
        return row


class SetAsideTest(OwnCase):
    """The own source is not asked and neither agrees nor flags."""

    def test_a_second_provider_agrees_and_the_source_is_not_asked(self) -> None:
        providers = table(MustNotAsk(), None, fixtures.LIGHTHOUSE_PLAIN)
        row = self.one(providers, LRCLIB)
        self.assertIs(row.verdict, lc.CompareVerdict.AGREES)
        self.assertIs(row.own, LRCLIB)
        self.assertTrue(row.recorded)
        self.assertEqual([s.source for s in row.scores], [NETEASE, GENIUS])

    def test_no_other_answer_is_no_second_source(self) -> None:
        row = self.one(table(MustNotAsk(), None, None), LRCLIB)
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SECOND_SOURCE)
        self.assertIsNone(row.reason)
        self.assertIs(row.own, LRCLIB)

    def test_another_providers_different_text_flags(self) -> None:
        row = self.one(table(MustNotAsk(), None, fixtures.ORCHARD_PLAIN), LRCLIB)
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SOURCE_AGREES)

    def test_the_only_provider_being_the_source_asks_no_one(self) -> None:
        row = self.one(table(MustNotAsk(), None, None, only=True), LRCLIB)
        self.assertIs(row.verdict, lc.CompareVerdict.NO_SECOND_SOURCE)
        self.assertEqual(row.reason, lc.NO_PROVIDER_ASKED)
        self.assertIs(row.own, LRCLIB)


class NothingSetAsideTest(OwnCase):
    """Musixmatch and an unrecorded source set nothing aside."""

    def answering_all(self) -> tuple[lm.ProviderTable, list[fixtures.FakeProvider]]:
        """A table of one fake per compared provider, each finding nothing, and those fakes."""
        fakes = [fixtures.empty() for _ in lc.COMPARED]
        return dict(zip(map(KEYS.get, lc.COMPARED), fakes, strict=True)), fakes  # type: ignore[arg-type] # fakes

    def test_musixmatch_is_recorded_and_every_provider_is_asked(self) -> None:
        providers, fakes = self.answering_all()
        row = self.one(providers, MUSIXMATCH)
        self.assertIsNone(row.own)
        self.assertTrue(row.recorded)
        self.assertEqual([len(f.calls) > 0 for f in fakes], [True] * len(lc.COMPARED))

    def test_no_run_folder_asks_every_provider_and_is_unrecorded(self) -> None:
        providers, fakes = self.answering_all()
        row = self.one(providers, None)
        self.assertIsNone(row.own)
        self.assertFalse(row.recorded)
        self.assertEqual([len(f.calls) > 0 for f in fakes], [True] * len(lc.COMPARED))

    def test_a_track_not_judged_carries_neither(self) -> None:
        synced = TrackSpec("02 Synced.mp3", title="Synced", lrc=fixtures.LIGHTHOUSE_LRC)
        report = self.compare(table(None, None, None), synced)
        [row] = report.tracks
        self.assertIs(row.verdict, lc.CompareVerdict.NOT_JUDGED)
        self.assertEqual((row.own, row.recorded), (None, True))


class TallyOwnTest(OwnCase):
    """A provider's own tracks are noted beside its refusals."""

    def test_own_tracks_are_counted_and_described(self) -> None:
        second = TrackSpec(
            "02 Orchard.mp3", title="Orchard", txt=fixtures.ORCHARD_PLAIN
        )
        third = TrackSpec("03 Mill.mp3", title="Mill", txt=fixtures.LIGHTHOUSE_PLAIN)
        self.album.mkdir(parents=True)
        write_run(
            self.album,
            _RUN,
            entry(KEEPER.relpath, LRCLIB),
            entry(second.relpath, LRCLIB),
        )
        lrclib = fixtures.raising(OSError("refused"))
        providers = table(lrclib, None, fixtures.LIGHTHOUSE_PLAIN)
        report = self.compare(providers, KEEPER, second, third)
        [count] = [c for c in report.providers if c.source is LRCLIB]
        self.assertEqual((count.accepted, count.asked, count.refused), (0, 1, 1))
        self.assertEqual(count.own, 2)
        self.assertEqual(count.describe(), "LRCLIB 0/1 (2 own, 1 refused)")
        self.assertEqual(len(lrclib.calls), 1)

    def test_describe_with_own_alone_and_without_own(self) -> None:
        own = lyrics_tally.ProviderCount(LRCLIB, 1, 3, own=2)
        self.assertEqual(own.describe(), "LRCLIB 1/3 (2 own)")
        run = lyrics_tally.ProviderCount(LRCLIB, 1, 3, 1)
        self.assertEqual(run.describe(), "LRCLIB 1/3 (1 refused)")
        self.assertEqual(
            lyrics_tally.ProviderCount(LRCLIB, 1, 3).describe(), "LRCLIB 1/3"
        )


class FormatOwnTest(OwnCase):
    """The row's own cell, the unrecorded note and the counts line."""

    def text(self, report: lc.CompareReport) -> str:
        """The compare report as the CLI prints it."""
        return lyrics_summary.format_compare(
            report, album=self.album, prog=PROG, command=PROG, genius="present"
        )

    def row_line(self, text: str) -> str:
        """The first table row of *text*."""
        return next(
            line for line in text.splitlines() if line.lstrip().startswith("1 ")
        )

    def test_the_own_cell_sits_in_provider_order(self) -> None:
        self.album.mkdir(parents=True)
        write_run(self.album, _RUN, entry(KEEPER.relpath, LRCLIB))
        providers = table(MustNotAsk(), None, fixtures.LIGHTHOUSE_PLAIN)
        text = self.text(self.compare(providers, KEEPER))
        line = self.row_line(text)
        self.assertIn("LRCLIB own · NetEase - · Genius 1.000", line)
        self.assertNotIn("source unrecorded", line)
        self.assertIn("source unrecorded 0 · genius: present", text)

    def test_an_unrecorded_agreeing_row_is_noted_and_counted(self) -> None:
        providers = table(None, None, fixtures.LIGHTHOUSE_PLAIN)
        self.album.mkdir(parents=True)
        text = self.text(self.compare(providers, KEEPER))
        self.assertTrue(self.row_line(text).endswith("  source unrecorded"))
        self.assertIn("not judged 0 · source unrecorded 1 · genius:", text)

    def test_an_unrecorded_flag_row_leads_its_note_with_it(self) -> None:
        providers = table(None, None, fixtures.ORCHARD_PLAIN)
        self.album.mkdir(parents=True)
        misnamed = TrackSpec("01 Ford.mp3", title="Ford Stones", txt=KEEPER.txt)
        text = self.text(self.compare(providers, misnamed))
        self.assertTrue(
            self.row_line(text).endswith("  source unrecorded; no title word")
        )

    def test_a_not_judged_row_is_not_counted_as_unrecorded(self) -> None:
        synced = TrackSpec("02 Synced.mp3", title="Synced", lrc=fixtures.LIGHTHOUSE_LRC)
        text = self.text(self.compare(table(None, None, None), synced))
        self.assertIn("not judged 1 · source unrecorded 0 · genius:", text)
        self.assertNotIn("source unrecorded\n", text)

    def test_a_musixmatch_row_prints_no_own_cell_and_no_note(self) -> None:
        self.album.mkdir(parents=True)
        write_run(self.album, _RUN, entry(KEEPER.relpath, MUSIXMATCH))
        providers = table(None, None, fixtures.LIGHTHOUSE_PLAIN)
        line = self.row_line(self.text(self.compare(providers, KEEPER)))
        self.assertNotIn("own", line)
        self.assertNotIn("source unrecorded", line)


class ErrorRowOwnTest(OwnCase):
    """A row that failed inside the boundary keeps what the manifests said."""

    def failing_stages(self) -> lyrics_stages.Stages:
        """The real stages, but reading tags raises."""

        def broken(path: Path) -> lyrics_stages.TrackTags:
            raise OSError(f"unreadable tags: {path.name}")

        stages = run_fakes.make_stages(run_fakes.FakeRunFolder())
        return dataclasses.replace(
            stages, read_tags=broken, read_manifest=lmf.read_manifest
        )

    def test_the_error_row_keeps_own_and_recorded_but_is_not_tallied(self) -> None:
        paths = fixtures.build_album(self.album, (KEEPER,))
        write_run(self.album, _RUN, entry(KEEPER.relpath, LRCLIB))
        report = lc.compare_album(
            self.album,
            paths,
            table(MustNotAsk(), None, None),
            self.failing_stages(),
            lang=run_fakes.LANG,
            deadline_s=run_fakes.DEADLINE_S,
            notice=self.fail,
        )
        [row] = report.tracks
        self.assertIsNotNone(row.error)
        self.assertIs(row.own, LRCLIB)
        self.assertTrue(row.recorded)
        [count] = [c for c in report.providers if c.source is LRCLIB]
        self.assertEqual(count.own, 0)


class PassedOverNoticeTest(fixtures.IsolatedCase):
    """Each run compare passes over is one notice, given before any provider is asked."""

    def setUp(self) -> None:
        """An album of one track, a good run and two newer ones that cannot be read."""
        super().setUp()
        self.album = self.tmp / "album"
        self.paths = fixtures.build_album(self.album, (KEEPER,))
        write_run(self.album, _RUN, entry(KEEPER.relpath, GENIUS))
        self.broken = (f"{_RUN}-2", f"{_RUN}-3")
        for run_id in self.broken:
            write_run(self.album, run_id, entry(KEEPER.relpath, NETEASE))
            manifest = self.album / lr.RUN_FOLDER_NAME / run_id / lr.MANIFEST_NAME
            manifest.write_bytes(b"\xff{")
        self.events: list[str] = []

    def asking(self, query: lm.TrackQuery) -> str | None:
        """A provider that records being asked and answers nothing."""
        self.events.append("asked")
        return None

    def test_one_notice_per_run_newest_first_before_any_provider(self) -> None:
        stages = dataclasses.replace(
            run_fakes.make_stages(run_fakes.FakeRunFolder()),
            read_manifest=lmf.read_manifest,
        )
        lc.compare_album(
            self.album,
            self.paths,
            table(self.asking, None, None, only=True),
            stages,
            lang=run_fakes.LANG,
            deadline_s=run_fakes.DEADLINE_S,
            notice=self.events.append,
        )
        expected = [
            f"passed over the run {lr.RUN_FOLDER_NAME}/{run_id}: its manifest cannot "
            "be read (ManifestSchemaError)"
            for run_id in reversed(self.broken)
        ]
        self.assertEqual(self.events[:2], expected)
        self.assertIn("asked", self.events[2:])
