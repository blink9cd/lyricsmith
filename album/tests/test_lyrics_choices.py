"""The entry point's choices and exit codes: the Genius key, the genre list.

Every environment here is invented and HOME is the test's own temporary one, so no
real config file or key is ever read.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_choices as lch  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_config as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_exit as lx  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_genre import NO_LYRICS_GENRES  # noqa: E402 - follows the bootstrap


def _args(
    *,
    offline: bool = False,
    no_genius: bool = False,
    only_providers: frozenset[lm.Source] | None = None,
) -> argparse.Namespace:
    """The options the choices read, with no config file named."""
    return argparse.Namespace(
        offline=offline,
        no_genius=no_genius,
        config=None,
        only_providers=only_providers,
    )


@dataclass(frozen=True)
class _Report:
    """A finished report standing in for a run's, a strip's or an undo's."""

    record_error: str | None = None
    interrupted: bool = False
    has_errors: bool = False


class GeniusChoiceTest(fixtures.IsolatedCase):
    """Offline and --no-genius decide alone; otherwise the key is resolved, or absent."""

    def setUp(self) -> None:
        """Collect the notices each choice gives."""
        super().setUp()
        self.notices: list[str] = []

    def test_offline_needs_no_key_and_says_nothing(self) -> None:
        got = lch.genius_choice(_args(offline=True), {}, self.notices.append)
        self.assertEqual((got, self.notices), ((None, "offline"), []))

    def test_no_genius_skips_the_step(self) -> None:
        got = lch.genius_choice(_args(no_genius=True), {}, self.notices.append)
        self.assertEqual((got, self.notices), ((None, "skipped"), []))

    def test_left_out_by_only_providers_skips_it_and_looks_for_no_key(self) -> None:
        only = frozenset({lm.Source.LRCLIB})
        got = lch.genius_choice(_args(only_providers=only), {}, self.notices.append)
        self.assertEqual((got, self.notices), ((None, "skipped"), []))

    def test_named_by_only_providers_looks_for_its_key(self) -> None:
        only = frozenset({lm.Source.GENIUS})
        got = lch.genius_choice(_args(only_providers=only), {}, self.notices.append)
        self.assertEqual(got, (None, "absent"))

    def test_no_key_anywhere_is_absent_with_one_notice(self) -> None:
        got = lch.genius_choice(_args(), {}, self.notices.append)
        self.assertEqual(got, (None, "absent"))
        self.assertEqual(len(self.notices), 1)
        self.assertIn(str(lc.default_config_path({})), self.notices[0])


class NoLyricsChoiceTest(fixtures.IsolatedCase):
    """With no config file the built-in genre list applies."""

    def test_no_config_file_is_the_built_in_list(self) -> None:
        notices: list[str] = []
        got = lch.no_lyrics_choice(_args(), {}, notices.append)
        self.assertEqual((got, notices), (NO_LYRICS_GENRES, []))


class ReportExitTest(fixtures.IsolatedCase):
    """A record that could not be written outranks everything else, and is said once."""

    def test_a_record_error_is_exit_record_not_written(self) -> None:
        notices: list[str] = []
        report = _Report(record_error="cannot write the manifest")
        code = lx.report_exit(report, "album", (None, False), notices.append)
        self.assertEqual(code, lx.EXIT_RECORD_NOT_WRITTEN)
        self.assertEqual(notices, ["album: cannot write the manifest"])
