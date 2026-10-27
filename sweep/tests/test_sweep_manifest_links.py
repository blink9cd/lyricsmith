"""The sweep on a run whose ``manifest.json`` is a link: a run, read as no record.

The album tool counts a folder whose manifest is a link, dangling or leading out of
the run folder, as a run (`lyrics_run_folders.manifest_present`), so the sweep's
before-and-after listing does too. Such a run is never read: `read_run` gives None, and
an album whose child left one is recorded as no record, never done and never a
traceback. A run like that from before the child ran is no new run of its. The album
tool is played by `sweep_fixtures.FakeAlbumTool`; nothing here reaches a provider.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_child  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above

#: A generous bound for a fake child, which never waits.
_GENEROUS_S = 60.0


def _manifests(album: Path) -> list[Path]:
    """Every run manifest under *album*, oldest run first."""
    return sorted(album.glob(f"{lr.RUN_FOLDER_NAME}/*/{lr.MANIFEST_NAME}"))


def dangle(manifest: Path) -> None:
    """Make *manifest* a link to a name beside it that does not exist."""
    manifest.unlink()
    manifest.symlink_to(manifest.parent / "missing.json")


def link_out(manifest: Path, outside: Path) -> None:
    """Move *manifest* to *outside*, leaving a link to it in its place."""
    shutil.move(manifest, outside)
    manifest.symlink_to(outside)


class LinkedManifestRunTest(sf.IsolatedCase):
    """The listing takes a linked manifest's folder as a run; reading it gives None."""

    def setUp(self) -> None:
        """An album run once by the fake album tool."""
        super().setUp()
        self.album = sf.needy_album(self.library, "Artist", "Album")
        sf.FakeAlbumTool()(["--album", str(self.album)], _GENEROUS_S)
        (self.manifest,) = _manifests(self.album)
        self.run_id = self.manifest.parent.name

    def test_a_dangling_manifest_is_a_run_read_as_none(self) -> None:
        dangle(self.manifest)
        self.assertEqual(sweep_child.run_ids(self.album), frozenset({self.run_id}))
        self.assertIsNone(sweep_child.read_run(self.album, self.run_id))

    def test_a_manifest_linked_out_is_a_run_read_as_none(self) -> None:
        link_out(self.manifest, self.tmp / "outside.json")
        self.assertEqual(sweep_child.run_ids(self.album), frozenset({self.run_id}))
        self.assertIsNone(sweep_child.read_run(self.album, self.run_id))


class _LinkingAlbumTool(sf.FakeAlbumTool):
    """The fake album tool, leaving the manifest of each run it makes a dangling link."""

    def __call__(
        self, argv: Sequence[str], timeout_s: float
    ) -> sweep_child.ChildResult:
        """Make the run as the fake does, then dangle its manifest."""
        result = super().__call__(argv, timeout_s)
        album = Path(sf.option_value(argv, "--album"))
        dangle(_manifests(album)[-1])
        return result


class SweepOutcomeTest(sf.SweepCase):
    """How the sweep records an album whose run's manifest is a link."""

    def test_a_new_run_with_a_dangling_manifest_is_no_record(self) -> None:
        self.run_child = _LinkingAlbumTool()
        self.sweep_one()
        (entry,) = self._ledger()
        self.assertIs(entry.outcome, sl.Outcome.NO_RECORD)
        self.assertIsNone(entry.run_id)

    def test_an_earlier_run_with_a_linked_manifest_is_not_the_new_one(self) -> None:
        album = self.albums[0]
        self.fake(["--album", str(album)], _GENEROUS_S)
        dangle(_manifests(album)[0])
        self.sweep_one()
        (entry,) = self._ledger()
        self.assertIs(entry.outcome, sl.Outcome.DONE)
        self.assertEqual(entry.run_id, _manifests(album)[-1].parent.name)

    def sweep_one(self) -> None:
        """Sweep the first album alone."""
        self._sweep(limit=1)
