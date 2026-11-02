"""An on/off option left off: no default in --help, and False wherever a run reads it.

Each flag parses as None when omitted, so the shared formatter shows no default beside
it, as it shows none beside an option whose default is None; the flag's help already
says what giving it does. Every reader that keeps the value, rather than testing it for
truth, makes it a bool first, so the run's options, plan and reports hold False, never
None.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_choices  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_prune_mode  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_provider_modes as lpm  # noqa: E402 - import follows the bootstrap above

#: Every on/off option the album tool takes, by its parsed name.
FLAGS = (
    "apply",
    "dry_run",
    "offline",
    "no_genius",
    "no_netease",
    "no_bandcamp",
    "no_youtube_music",
    "no_tekstowo",
    "fresh",
    "refetch_untimed",
    "refetch_implausible",
)


class FlagDefaultTest(fixtures.IsolatedCase):
    """Each flag is None when omitted, hidden in --help, and False once kept."""

    def test_the_help_shows_no_false_default(self) -> None:
        self.assertNotIn("(default: False)", album.build_parser().format_help())

    def test_each_omitted_flag_parses_as_none(self) -> None:
        args = album.build_parser().parse_args(["--album", "a"])
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIsNone(getattr(args, flag))

    def test_each_given_flag_parses_as_true(self) -> None:
        parser = album.build_parser()
        for flag in FLAGS:
            option = "--" + flag.replace("_", "-")
            with self.subTest(flag=flag):
                args = parser.parse_args(["--album", "a", option])
                self.assertIs(getattr(args, flag), True)

    def test_the_run_options_hold_false_for_an_omitted_flag(self) -> None:
        args = album.build_parser().parse_args(["--album", "a"])
        asked = lpm.AskedProviders(
            lpm.GeniusState.ABSENT, "absent", mock.Mock(), table={}
        )
        options = lpm.provider_options(args, asked)
        for name in ("fresh", "refetch_untimed", "refetch_implausible"):
            with self.subTest(option=name):
                self.assertIs(getattr(options, name), False)


class _Stop(Exception):
    """Raised by a patched callee once it has recorded what it was given."""


class KeptValueTest(fixtures.IsolatedCase):
    """Each reader hands its callee exactly False for an omitted flag, never None.

    The callee is replaced by a mock that records its keywords and stops the mode
    there, so each test reads the value the reader passed on, and a reader that
    passed the parsed None along unwrapped fails here.
    """

    def _omitted(self) -> argparse.Namespace:
        return album.build_parser().parse_args(["--album", "a"])

    def test_the_cooling_choices_are_given_offline_false(self) -> None:
        with (
            mock.patch.object(lyrics_choices, "musicbrainz_contact"),
            mock.patch.object(lyrics_choices, "bandcamp_choice"),
            mock.patch.object(
                lyrics_choices, "cooling_choice", side_effect=_Stop
            ) as cooling,
            self.assertRaises(_Stop),
        ):
            lyrics_choices.optional_providers(
                self._omitted(), {}, mock.Mock(), mock.Mock(), genius=False
            )
        self.assertIs(cooling.call_args.kwargs["offline"], False)

    def test_the_dry_run_plan_is_given_false(self) -> None:
        with (
            mock.patch.object(lyrics_choices, "no_lyrics_choice"),
            mock.patch.object(lyrics_choices, "musicbrainz_contact"),
            mock.patch.object(
                lyrics_choices, "genius_choice", return_value=(None, "absent")
            ),
            mock.patch.object(
                lpm.lyrics_modes, "plan_album", side_effect=_Stop
            ) as plan,
            self.assertRaises(_Stop),
        ):
            lpm._dry_run(self._omitted(), Path("a"), (), mock.Mock(), "album", print)
        for name in ("offline", "fresh", "refetch_untimed", "refetch_implausible"):
            with self.subTest(keyword=name):
                self.assertIs(plan.call_args.kwargs[name], False)

    def test_the_prune_is_given_dry_run_false(self) -> None:
        with (
            mock.patch.object(lyrics_album, "resolve_album", return_value=Path("a")),
            mock.patch.object(lyrics_prune_mode, "refuse_unsafe_delete"),
            mock.patch.object(
                lyrics_prune_mode.lyrics_prune, "prune_album", side_effect=_Stop
            ) as prune,
            self.assertRaises(_Stop),
        ):
            lyrics_prune_mode.prune_mode(self._omitted(), mock.Mock(), "album")
        self.assertIs(prune.call_args.kwargs["dry_run"], False)

    def test_the_accept_is_given_apply_false(self) -> None:
        with (
            mock.patch.object(lyrics_album, "resolve_album", return_value=Path("a")),
            mock.patch.object(lyrics_prune_mode, "refuse_unsafe_delete"),
            mock.patch.object(
                lyrics_accept_mode, "accept_album", side_effect=_Stop
            ) as accept,
            self.assertRaises(_Stop),
        ):
            lyrics_accept_mode.accept_mode(self._omitted(), mock.Mock(), "album")
        self.assertIs(accept.call_args.kwargs["apply"], False)
