"""The provider-asking modes' cool-down notices follow the sources that keep a cool-down.

When a run or a compare raises, each source with a cool-down file gets its failed-write
notice said. Which sources those are is `lyrics_cooldown.STATE_FILES`, the one list of
them, so a source added there is noticed with no second list to update. The label
writer itself is replaced by a recorder: only which sources were asked is under test.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import MappingProxyType
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402, F401 - puts the shared libraries on sys.path first
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_provider_modes as lpm  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_model import Source  # noqa: E402 - import follows the sys.path bootstrap above


class CoolingOnFailureTest(fixtures.IsolatedCase):
    """Every source in the cool-down table is reported, and only those."""

    def reported(self) -> list[Source]:
        """The sources `_report_cooling_on_failure` asked for a label."""
        asked: list[Source] = []

        def record(source: Source, *_rest: object) -> str:
            asked.append(source)
            return ""

        with mock.patch.object(lpm, "_cooling_report", record):
            lpm._report_cooling_on_failure(mock.Mock(), {}, lambda _text: None)
        return asked

    def test_each_source_with_a_cool_down_file(self) -> None:
        self.assertEqual(self.reported(), list(lyrics_cooldown.STATE_FILES))

    def test_the_table_decides_which_sources(self) -> None:
        one = MappingProxyType({Source.TEKSTOWO: "tekstowo.json"})
        with mock.patch.object(lyrics_cooldown, "STATE_FILES", one):
            self.assertEqual(self.reported(), [Source.TEKSTOWO])

    def test_a_lost_notice_never_skips_the_next_source(self) -> None:
        asked: list[Source] = []

        def closed(source: Source, *_rest: object) -> str:
            asked.append(source)
            raise OSError("invented closed stderr")

        with mock.patch.object(lpm, "_cooling_report", closed):
            lpm._report_cooling_on_failure(mock.Mock(), {}, lambda _text: None)
        self.assertEqual(asked, list(lyrics_cooldown.STATE_FILES))


class DiscoverTest(fixtures.IsolatedCase):
    """A skipped link is noticed by its path in the album and why it was skipped."""

    def test_a_skipped_link_is_noticed_as_not_followed(self) -> None:
        folder = self.tmp / "album"
        folder.mkdir()
        found = lyrics_album.Discovery(tracks=(), links=(folder.resolve() / "linked",))
        notices: list[str] = []
        with mock.patch.object(lyrics_album, "discover_tracks", return_value=found):
            lpm.discover(argparse.Namespace(album=folder), notices.append)
        self.assertEqual(len(notices), 1)
        self.assertIn("a symbolic link or junction is not followed", notices[0])
