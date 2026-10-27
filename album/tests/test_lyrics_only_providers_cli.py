"""`--only-providers` through the entry point: refusals, composition, the table, the summary.

A run or a compare given it asks the named providers alone: the others are not in the
table the run receives, are never called, record nothing, and show as not asked, as
their `--no-*` option shows them. Each test drives `album.main` with faked wiring and
fake providers (`fixtures`), so no test reaches the network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breakers as lbs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_compare  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_only  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_options  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_retried as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_option_help import (  # noqa: E402 - follows the sys.path bootstrap
    option_help,
)

#: The option under test.
ONLY = "--only-providers"
#: A provider error every left-out fake raises, so a call would record a refusal.
REFUSAL = OSError("connection refused")


class _OnlyCase(cli_fakes.CliCase):
    """A one-track album, and a table of every chain step on fakes that find nothing."""

    def setUp(self) -> None:
        """The album and the table."""
        super().setUp()
        self.new_album("album")

    def new_album(self, name: str) -> None:
        """A one-track album under this test's directory as *name*, and a fresh table.

        A subtest takes its own, as each run of one album at the fixed clock would
        take the same run id.
        """
        self.album = self.tmp / name
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.fakes = fixtures.chain_table()
        self.providers = dict(self.fakes)

    def asked_sources(self) -> set[lm.Source]:
        """The sources whose fakes this test's run called."""
        return {key.source for key, fake in self.fakes.items() if fake.calls}

    def received_sources(self, *extra: str) -> tuple[cli_fakes.Result, set[lm.Source]]:
        """Run with *extra*; the run's result, and the sources of the table it received."""
        with mock.patch.object(
            lyrics_run, "run_album", wraps=lyrics_run.run_album
        ) as spy:
            result = self.run_album(*extra)
        return result, {key.source for key in spy.call_args.args[2]}


class RefusalTest(_OnlyCase):
    """A bad value, a mode that asks no provider and --offline each exit 2."""

    def assert_usage_error(self, result: cli_fakes.Result, *words: str) -> None:
        """*result* is a usage error whose message holds each of *words*."""
        self.assertEqual(result.code, 2, result.stderr)
        for word in words:
            self.assertIn(word, result.stderr)
        self.assertEqual(self.provider_calls, [])

    def test_an_unknown_name(self) -> None:
        result = self.run_album(ONLY, "lrclib,spotify")
        self.assert_usage_error(result, f"argument {ONLY}", "'spotify'")

    def test_an_empty_value(self) -> None:
        self.assert_usage_error(self.run_album(ONLY, ""), f"argument {ONLY}")

    def test_a_name_given_twice(self) -> None:
        result = self.run_album(ONLY, "genius,genius")
        self.assert_usage_error(result, f"argument {ONLY}", "named twice")

    def test_no_value(self) -> None:
        self.assert_usage_error(self.run_album(ONLY), f"argument {ONLY}")

    def test_offline(self) -> None:
        result = self.run_album(ONLY, "lrclib", "--offline")
        self.assert_usage_error(result, f"{ONLY} cannot be used with --offline")

    def test_the_modes_that_ask_no_provider(self) -> None:
        for mode in ("check", "undo", "strip", "prune", "accept"):
            with self.subTest(mode=mode):
                result = self.run_album("--mode", mode, ONLY, "lrclib")
                self.assert_usage_error(
                    result, f"{ONLY} cannot be used with --mode {mode}"
                )


class TableTest(_OnlyCase):
    """The run receives, and asks, the named providers' steps alone."""

    def test_each_provider_alone(self) -> None:
        for name, source in lyrics_only.PROVIDER_NAMES.items():
            with self.subTest(name=name):
                self.new_album(name)
                result, received = self.received_sources(ONLY, name)
                self.assertEqual(result.code, album.EXIT_OK, result.stderr)
                self.assertEqual(received, {source})

    def test_without_it_every_provider(self) -> None:
        result, received = self.received_sources()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(received, set(lyrics_only.PROVIDER_NAMES.values()))

    def test_the_left_out_are_never_called_and_record_nothing(self) -> None:
        named = {lm.Source.GENIUS, lm.Source.TEKSTOWO}
        for key in self.fakes:
            if key.source not in named:
                self.fakes[key] = fixtures.raising(REFUSAL)
        self.providers = dict(self.fakes)
        result = self.run_album(ONLY, "genius,tekstowo")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.asked_sources(), named)
        self.assertNotIn("refused", result.stdout)
        self.assertNotIn("refusals:", result.stdout)

    def test_lrclib_is_asked_only_when_named(self) -> None:
        self.run_album(ONLY, "netease")
        self.assertEqual(self.asked_sources(), {lm.Source.NETEASE})

    def test_named_and_turned_off_is_off(self) -> None:
        # The faked builder answers its table whatever the settings say, so the table
        # the real builder makes from the recorded settings is what proves it: building
        # one sends nothing and reads no file.
        result = self.run_album(
            ONLY, "netease,genius,lrclib,tekstowo", "--no-netease", "--no-genius"
        )
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (settings,) = self.provider_settings
        (key, _deadline) = self.provider_calls[0]
        self.assertIsNone(key)
        real = ltable.build_provider_table(key, settings=settings)
        self.assertEqual(
            {k.source for k in real}, {lm.Source.LRCLIB, lm.Source.TEKSTOWO}
        )
        self.assertIn("genius: skipped", result.stdout)
        self.assertIn("tekstowo: on", result.stdout)

    def test_it_combines_with_the_run_shapes(self) -> None:
        for extra in (
            ("--fresh",),
            ("--refetch-untimed",),
            ("--refetch-implausible",),
            ("--refetch-untimed", "--refetch-implausible"),
        ):
            with self.subTest(extra=extra):
                self.new_album("-".join(extra))
                result, received = self.received_sources(ONLY, "lrclib", *extra)
                self.assertEqual(result.code, album.EXIT_OK, result.stderr)
                self.assertEqual(received, {lm.Source.LRCLIB})

    def test_a_dry_run_takes_it_and_asks_nothing(self) -> None:
        result = self.run_album(ONLY, "lrclib", "--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.asked_sources(), set())

    def test_a_compare_receives_the_named_alone(self) -> None:
        with mock.patch.object(
            lyrics_compare, "compare_album", wraps=lyrics_compare.compare_album
        ) as spy:
            result = self.run_album("--mode", "compare", ONLY, "bandcamp,genius")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        received = {key.source for key in spy.call_args.args[2]}
        self.assertEqual(received, {lm.Source.BANDCAMP, lm.Source.GENIUS})
        self.assertIn("youtube music: off (--only-providers)", result.stdout)


class SettingsTest(_OnlyCase):
    """The left-out optional steps are off, as their `--no-*` options turn them off."""

    def test_the_settings_turn_the_left_out_off(self) -> None:
        # An invented key, so Genius is asked and its breaker file read.
        os.environ[fixtures.GENIUS_TOKEN_ENV] = "fake-token-for-tests"
        result = self.run_album(ONLY, "genius")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        environ, now = self.wiring().environ, self.wiring().now
        guard = lbs.BreakerSetup(
            lbk.breaker_path(environ, lm.Source.GENIUS), lbk.CLOSED, now
        )
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    bandcamp=False,
                    netease=False,
                    youtube_music=None,
                    tekstowo=None,
                    breakers={lm.Source.GENIUS: guard},
                    retried=lrt.RetryTally(),
                )
            ],
        )

    def test_the_left_out_state_files_are_not_read(self) -> None:
        environ = self.wiring().environ
        for path in (
            lbk.breaker_path(environ, lm.Source.LRCLIB),
            lyc.cooldown_path(environ, lm.Source.YOUTUBE_MUSIC),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not json")
        read = self.run_album()
        self.assertIn("lrclib:", read.stderr)
        self.assertIn("youtube music:", read.stderr)
        self.new_album("left-out")
        result = self.run_album(ONLY, "genius")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        self.assertNotIn("lrclib:", result.stderr)
        self.assertNotIn("youtube music:", result.stderr)

    def test_naming_them_keeps_them_on(self) -> None:
        result = self.run_album(ONLY, "bandcamp,netease,youtube-music,tekstowo")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (settings,) = self.provider_settings
        self.assertTrue(settings.bandcamp)
        self.assertTrue(settings.netease)
        self.assertEqual(settings.youtube_music, self.youtube_setup())
        self.assertEqual(settings.tekstowo, self.tekstowo_setup())
        self.assertEqual(
            set(settings.breakers), {lm.Source.BANDCAMP, lm.Source.NETEASE}
        )


class SummaryTest(_OnlyCase):
    """The summary shows a left-out provider as not asked."""

    def test_the_optional_lines_and_the_tally(self) -> None:
        result = self.run_album(ONLY, "netease")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        for line in (
            "bandcamp: off (--only-providers)",
            "youtube music: off (--only-providers)",
            "tekstowo: off (--only-providers)",
            "genius: skipped",
        ):
            self.assertIn(line, result.stdout)
        (tally,) = [
            ln for ln in result.stdout.splitlines() if ln.startswith("providers:")
        ]
        self.assertIn("LRCLIB -", tally)
        self.assertIn("NetEase 0/1", tally)
        self.assertIn("Genius -", tally)

    def test_its_no_option_names_the_line_first(self) -> None:
        result = self.run_album(ONLY, "lrclib", "--no-bandcamp")
        self.assertIn("bandcamp: off (--no-bandcamp)", result.stdout)

    def test_a_named_genius_without_a_key_is_absent(self) -> None:
        result = self.run_album(ONLY, "genius")
        self.assertIn("genius: absent", result.stdout)


class HelpTest(fixtures.IsolatedCase):
    """`--help` shows the option, every name and what leaving it out means."""

    def test_the_parser_declares_the_flag_the_messages_name(self) -> None:
        self.assertEqual(lyrics_options.ONLY_FLAG, ONLY)
        self.assertTrue(option_help(lyrics_options.ONLY_FLAG))

    def test_the_help(self) -> None:
        text = option_help(ONLY)
        self.assertIn(", ".join(lyrics_only.PROVIDER_NAMES), text)
        self.assertIn("every provider when omitted", text)
        self.assertIn("Refused with --offline", text)

    def test_the_rendered_help_shows_no_none_default(self) -> None:
        rendered = album.build_parser("album").format_help()
        self.assertIn(ONLY, rendered)
        self.assertNotIn("(default: None)", rendered)
