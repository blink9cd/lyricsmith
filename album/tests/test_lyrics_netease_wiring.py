"""`--no-netease`, and the terms each `--no-*` option's help names.

`--no-netease` leaves both NetEase steps out of the table: which modes take it, the
settings it builds and the table those settings give. Each source a `--no-*` option
leaves out is used under its own terms, which the option's help and the `Providers:`
epilog name in a few words, pointing to the section that sets them out. Each test
drives the entry point with faked wiring, or builds the table by hand, so no test
reaches the network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_option_help import (  # noqa: E402 - follows the sys.path bootstrap
    option_help,
)

#: The project's own doc, which holds the terms section the help points to.
PROJECT_DOC = _HERE.parent.parent / "README.md"
#: Each `--no-*` option, and the words of its help that name its source's terms.
TERMS = {
    "--no-netease": "NetEase publishes no terms",
    "--no-bandcamp": "Bandcamp's terms allow personal, non-commercial use",
    "--no-youtube-music": "YouTube's terms bar automated access",
    "--no-genius": "Genius's terms bar scraping",
    "--no-tekstowo": "Tekstowo's terms allow home use only",
}
#: The steps of the chain that ask NetEase.
NETEASE_STEPS = frozenset(
    lm.ProviderKey(lm.Source.NETEASE, kind)
    for kind in (lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED)
)


class RunChoiceTest(cli_fakes.CliCase):
    """A run asks NetEase unless told otherwise; the modes that ask none refuse it."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))

    def test_by_default_the_settings_ask_netease(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual([s.netease for s in self.provider_settings], [True])

    def test_no_netease_builds_the_settings_without_it(self) -> None:
        result = self.run_album("--no-netease")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(
            self.provider_settings,
            [
                self.chosen_settings(
                    ltable.ProviderSettings(
                        bandcamp=True,
                        netease=False,
                        youtube_music=self.youtube_setup(),
                        tekstowo=self.tekstowo_setup(),
                    )
                )
            ],
        )

    def test_an_offline_run_builds_no_table_under_it_either(self) -> None:
        result = self.run_album("--offline", "--no-netease")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.provider_settings, [])

    def test_the_modes_that_ask_no_provider_refuse_it(self) -> None:
        for mode in ("check", "undo", "strip", "prune", "accept"):
            with self.subTest(mode=mode):
                result = self.main(
                    "--album", str(self.album), "--mode", mode, "--no-netease"
                )
                self.assertEqual(result.code, 2)
                self.assertIn(
                    f"--no-netease cannot be used with --mode {mode}", result.stderr
                )
        self.assertEqual(self.provider_settings, [])


class CompareChoiceTest(cli_fakes.CliCase):
    """Compare takes the option and builds its settings without NetEase."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))

    def test_no_netease_is_accepted_and_builds_the_settings_without_it(self) -> None:
        result = self.main(
            "--album", str(self.album), "--mode", "compare", "--no-netease"
        )
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual([s.netease for s in self.provider_settings], [False])


class TableTest(fixtures.IsolatedCase):
    """The settings decide whether the real table holds NetEase's two steps."""

    def test_the_default_table_holds_both_netease_steps(self) -> None:
        table = ltable.build_provider_table(None)
        self.assertLessEqual(NETEASE_STEPS, set(table))

    def test_netease_off_leaves_both_steps_out_and_keeps_lrclib(self) -> None:
        settings = ltable.ProviderSettings(netease=False)
        table = ltable.build_provider_table(None, settings=settings)
        self.assertEqual(
            list(table),
            [
                lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED),
                lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED),
            ],
        )


class TermsHelpTest(fixtures.IsolatedCase):
    """Each `--no-*` option's help, and the epilog, name the terms and where they are."""

    def test_each_option_names_its_sources_terms_and_the_section(self) -> None:
        for flag, terms in TERMS.items():
            with self.subTest(flag=flag):
                text = option_help(flag)
                self.assertIn(terms, text)
                self.assertIn(lyrics_help.TERMS_POINTER, text)

    def test_the_epilog_names_no_netease_and_the_section(self) -> None:
        providers = lyrics_help.EPILOG.split("Providers:")[1].split("Output:")[0]
        self.assertIn("--no-netease", providers)
        self.assertIn(lyrics_help.TERMS_POINTER, " ".join(providers.split()))

    def test_the_section_pointed_to_is_in_the_project_doc(self) -> None:
        heading = "## " + lyrics_help.TERMS_SECTION
        lines = PROJECT_DOC.read_text(encoding="utf-8").splitlines()
        self.assertIn(heading, lines)
        self.assertIn(lyrics_help.TERMS_SECTION, lyrics_help.TERMS_POINTER)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
