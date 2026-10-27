"""`--only-providers`'s names, its value's parse, and the table it leaves (`lyrics_only`).

The names are the providers' option names, one per provider in the chain, each the
same words its `--no-*` option uses; LRCLIB, which has no `--no-*` option, is named
`lrclib`. Pure functions: no test here runs the tool or reaches the network.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_help  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_only  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tally  # noqa: E402 - import follows the sys.path bootstrap above

#: The names the sweep and the docs use, pinned here so a renamed label is caught.
EXPECTED_NAMES = {
    "lrclib": lm.Source.LRCLIB,
    "netease": lm.Source.NETEASE,
    "youtube-music": lm.Source.YOUTUBE_MUSIC,
    "bandcamp": lm.Source.BANDCAMP,
    "genius": lm.Source.GENIUS,
    "tekstowo": lm.Source.TEKSTOWO,
}


class NamesTest(fixtures.IsolatedCase):
    """One name per provider of the chain, the words its `--no-*` option uses."""

    def test_the_names_are_the_rulings(self) -> None:
        self.assertEqual(dict(lyrics_only.PROVIDER_NAMES), EXPECTED_NAMES)

    def test_they_follow_the_chain_order(self) -> None:
        self.assertEqual(
            tuple(lyrics_only.PROVIDER_NAMES.values()), lyrics_tally.PROVIDERS
        )

    def test_each_but_lrclib_has_its_no_option(self) -> None:
        flags = {
            flag
            for action in album.build_parser()._actions
            for flag in action.option_strings
        }
        for name in lyrics_only.PROVIDER_NAMES:
            with self.subTest(name=name):
                expected = name != "lrclib"
                self.assertEqual(f"--no-{name}" in flags, expected)
                self.assertEqual(f"--no-{name}" in lyrics_help.SOURCE_TERMS, expected)

    def test_the_table_is_read_only(self) -> None:
        with self.assertRaises(TypeError):
            lyrics_only.PROVIDER_NAMES["musixmatch"] = lm.Source.MUSIXMATCH  # type: ignore[index] # proving the table refuses a write


class ParseTest(fixtures.IsolatedCase):
    """One comma-separated value of known, distinct, lowercase names."""

    def test_one_name(self) -> None:
        self.assertEqual(
            lyrics_only.parse_only_providers("genius"), frozenset({lm.Source.GENIUS})
        )

    def test_several_names(self) -> None:
        self.assertEqual(
            lyrics_only.parse_only_providers("youtube-music,lrclib"),
            frozenset({lm.Source.YOUTUBE_MUSIC, lm.Source.LRCLIB}),
        )

    def test_every_name(self) -> None:
        self.assertEqual(
            lyrics_only.parse_only_providers(",".join(EXPECTED_NAMES)),
            frozenset(EXPECTED_NAMES.values()),
        )

    def assert_refused(self, text: str, *words: str) -> None:
        """*text* is an argparse type error whose message holds each of *words*."""
        with self.assertRaises(argparse.ArgumentTypeError) as raised:
            lyrics_only.parse_only_providers(text)
        for word in words:
            self.assertIn(word, str(raised.exception))

    def test_an_empty_value_is_refused(self) -> None:
        self.assert_refused("", "names no provider")

    def test_an_empty_name_is_refused(self) -> None:
        for text in ("genius,", ",genius", "genius,,lrclib"):
            with self.subTest(text=text):
                self.assert_refused(text, "an empty name")

    def test_an_unknown_name_is_refused_and_the_names_listed(self) -> None:
        self.assert_refused(
            "genius,spotify",
            "'spotify'",
            "lrclib, netease, youtube-music, bandcamp, genius, tekstowo",
        )

    def test_names_are_case_sensitive(self) -> None:
        self.assert_refused("Genius", "'Genius'")

    def test_a_space_is_part_of_the_name(self) -> None:
        self.assert_refused("lrclib, genius", "' genius'")

    def test_musicbrainz_is_not_a_name(self) -> None:
        self.assert_refused("musicbrainz", "'musicbrainz'")

    def test_a_name_given_twice_is_refused(self) -> None:
        self.assert_refused("genius,lrclib,genius", "'genius' is named twice")


class RestrictTest(fixtures.IsolatedCase):
    """The table keeps the named providers' steps alone; no value keeps every step."""

    def test_no_value_keeps_every_step(self) -> None:
        table = fixtures.chain_table()
        self.assertEqual(lyrics_only.restrict(table, None), table)

    def test_a_value_keeps_its_providers_steps(self) -> None:
        table = fixtures.chain_table()
        kept = lyrics_only.restrict(
            table, frozenset({lm.Source.NETEASE, lm.Source.GENIUS})
        )
        self.assertEqual(
            list(kept),
            [k for k in table if k.source in (lm.Source.NETEASE, lm.Source.GENIUS)],
        )
        self.assertTrue(all(kept[k] is table[k] for k in kept))

    def test_left_out(self) -> None:
        only = frozenset({lm.Source.GENIUS})
        self.assertFalse(lyrics_only.left_out(None, lm.Source.LRCLIB))
        self.assertFalse(lyrics_only.left_out(only, lm.Source.GENIUS))
        self.assertTrue(lyrics_only.left_out(only, lm.Source.LRCLIB))
