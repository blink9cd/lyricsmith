"""`--no-bandcamp`: which modes take it, the table it builds, the line and the record it leaves.

Each test drives the entry point, with faked wiring or with the real wiring and a
provider table built by hand, so no test reaches the network. Every title and lyric is
invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_check  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: The line each choice prints after the counts.
ON, SKIPPED, OFFLINE = (
    "bandcamp: on",
    "bandcamp: off (--no-bandcamp)",
    "bandcamp: off (offline)",
)
#: Two untimed-only tracks: the first one's answer agrees, the second one's does not.
UNTIMED = (
    TrackSpec("01 A.mp3", title="Beacon Light", txt=fixtures.LIGHTHOUSE_PLAIN),
    TrackSpec("02 B.mp3", title="Mill Wheel", txt=fixtures.LIGHTHOUSE_PLAIN),
)
#: What the Bandcamp fake answers, by title.
ANSWERS = {
    "Beacon Light": fixtures.LIGHTHOUSE_EDITED,
    "Mill Wheel": fixtures.ORCHARD_PLAIN,
}


def _after_counts(stdout: str) -> str:
    """The line right after the counts line, which ends with the Genius state."""
    lines = stdout.splitlines()
    counts = next(i for i, line in enumerate(lines) if "· genius: " in line)
    return lines[counts + 1]


class RunChoiceTest(cli_fakes.CliCase):
    """A run builds its table with Bandcamp on unless told otherwise, and says which."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))

    def test_by_default_the_table_asks_bandcamp_and_the_summary_says_on(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    bandcamp=True,
                    youtube_music=self.youtube_setup(),
                    tekstowo=self.tekstowo_setup(),
                )
            ],
        )
        self.assertEqual(_after_counts(result.stdout), ON)

    def test_no_bandcamp_builds_the_table_without_it(self) -> None:
        result = self.run_album("--no-bandcamp")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    youtube_music=self.youtube_setup(), tekstowo=self.tekstowo_setup()
                )
            ],
        )
        self.assertEqual(_after_counts(result.stdout), SKIPPED)

    def test_offline_builds_no_table_and_says_so(self) -> None:
        result = self.run_album("--offline")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.provider_settings, [])
        self.assertEqual(_after_counts(result.stdout), OFFLINE)

    def test_offline_is_named_before_no_bandcamp(self) -> None:
        result = self.run_album("--offline", "--no-bandcamp")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(_after_counts(result.stdout), OFFLINE)

    def test_the_modes_that_ask_no_provider_refuse_it(self) -> None:
        for mode in ("check", "undo", "strip"):
            with self.subTest(mode=mode):
                result = self.main(
                    "--album", str(self.album), "--mode", mode, "--no-bandcamp"
                )
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--no-bandcamp cannot be used with --mode {mode}", result.stderr
                )
        self.assertEqual(self.provider_settings, [])


class CompareChoiceTest(cli_fakes.CliCase):
    """Compare takes the option, asks Bandcamp by default, and scores and tallies it."""

    def setUp(self) -> None:
        """The two untimed-only tracks; a table of Bandcamp alone."""
        super().setUp()
        fixtures.build_album(self.album, UNTIMED)
        key = lm.ProviderKey(lm.Source.BANDCAMP, lm.LyricsKind.UNSYNCED)
        self.providers = {key: run_fakes.TitleProvider(ANSWERS)}

    def compare(self, *extra: str) -> cli_fakes.Result:
        """Run compare on this test's album, with *extra* options."""
        return self.main("--album", str(self.album), "--mode", "compare", *extra)

    def test_by_default_the_table_asks_bandcamp_and_the_report_says_on(self) -> None:
        result = self.compare()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    bandcamp=True,
                    youtube_music=self.youtube_setup(),
                    tekstowo=self.tekstowo_setup(),
                )
            ],
        )
        self.assertEqual(_after_counts(result.stdout), ON)

    def test_no_bandcamp_is_accepted_and_builds_the_table_without_it(self) -> None:
        result = self.compare("--no-bandcamp")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    youtube_music=self.youtube_setup(), tekstowo=self.tekstowo_setup()
                )
            ],
        )
        self.assertEqual(_after_counts(result.stdout), SKIPPED)

    def test_a_bandcamp_answer_is_scored_and_tallied(self) -> None:
        result = self.compare()
        words = lyrics_check.normalize_words
        value = lyrics_check.shown(
            lyrics_check.word_similarity(
                words(fixtures.LIGHTHOUSE_PLAIN), words(fixtures.ORCHARD_PLAIN)
            )
        )
        self.assertIn(f"  Bandcamp {value}", result.stdout)
        absent = (lm.Source.LRCLIB, lm.Source.NETEASE)
        tally = lyrics_tally.format_tally(
            (
                *(lyrics_tally.ProviderCount(source, 0, None) for source in absent),
                lyrics_tally.ProviderCount(lm.Source.BANDCAMP, 1, 2),
                lyrics_tally.ProviderCount(lm.Source.YOUTUBE_MUSIC, 0, None),
                lyrics_tally.ProviderCount(lm.Source.GENIUS, 0, None),
                lyrics_tally.ProviderCount(lm.Source.TEKSTOWO, 0, None),
            )
        )
        self.assertEqual(result.stdout.splitlines()[-1], tally)


class RecordTest(cli_fakes.RealWiringCliCase):
    """The manifest a run writes says whether it asked Bandcamp; a strip's says off."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))

    def stored(self) -> object:
        """The run's manifest field as stored on disk."""
        run_dir = lr.run_folder(self.album, run_fakes.RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_bytes())
        return document["bandcamp"]

    def test_a_run_records_on(self) -> None:
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        self.assertEqual(self.stored(), "on")

    def test_a_run_without_bandcamp_records_off(self) -> None:
        self.assertEqual(self.run_album("--no-bandcamp").code, album.EXIT_OK)
        self.assertEqual(self.stored(), "off")

    def test_an_offline_run_records_off(self) -> None:
        self.assertEqual(self.run_album("--offline").code, album.EXIT_OK)
        self.assertEqual(self.stored(), "off")

    def test_a_strip_records_off(self) -> None:
        result = self.main("--album", str(self.album), "--mode", "strip")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertEqual(self.stored(), "off")
