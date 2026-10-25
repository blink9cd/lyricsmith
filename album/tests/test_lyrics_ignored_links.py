"""A sidecar link leading out of the album is ignored, and the reports say so.

Such a link holds no lyrics, as a dangling one does, so the chain fetches; without a
word about it the operator could not tell it from a link that leads nowhere. The
sidecar stage names each ignored link, the track's ``after`` block in the manifest
keeps the names, and the dry run and the run summary print a note naming them. An
older manifest, written before the field existed, still reads. A link inside the
album is read through and earns no note. Every lyric here is invented.
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
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: Invented timed lyrics kept outside the album: they must never be embedded.
_OUTSIDE_LRC = "[00:01.00]A verse that lives outside the album\n"
#: The note a report prints for the track's ignored link.
_NOTE = "sidecar link outside the album ignored: 01 Keeper.lrc"
#: The manifest field that keeps the ignored names.
_FIELD = "sidecar_links_ignored"


class IgnoredLinkCase(cli_fakes.RealWiringCliCase):
    """One track whose ``.lrc`` is a link; the providers answer by its title."""

    def setUp(self) -> None:
        """The track, an outside file, and a provider answering the track's title."""
        super().setUp()
        (self.mp3,) = fixtures.build_album(
            self.album, (TrackSpec("01 Keeper.mp3", title="The Keeper"),)
        )
        (self.tmp / "secret.lrc").write_text(_OUTSIDE_LRC, encoding="utf-8", newline="")
        self.providers = run_fakes.table({"The Keeper": fixtures.LIGHTHOUSE_LRC})

    def link(self, target: str) -> None:
        """Make the track's ``.lrc`` a link storing *target*."""
        self.mp3.with_suffix(".lrc").symlink_to(target)

    def after(self) -> lr.TrackOutcome:
        """The track's ``after`` block in the run's manifest."""
        manifest = lyrics_manifest.read_manifest(
            lr.run_folder(self.album, run_fakes.RUN_ID)
        )
        outcome = manifest.tracks[0].after
        assert outcome is not None  # the run reached its one track
        return outcome


class OutsideLinkNoteTest(IgnoredLinkCase):
    """An outside link is named in the sidecar state, the reports and the manifest."""

    def test_the_sidecar_state_names_the_ignored_link(self) -> None:
        self.link("../secret.lrc")
        state = lyrics_sidecar.normalize_sidecars(
            self.mp3, album=self.album, apply=False
        )
        self.assertEqual(state, lm.SidecarState(None, links_ignored=("01 Keeper.lrc",)))

    def test_the_dry_run_notes_the_ignored_link(self) -> None:
        self.link(str(self.tmp / "secret.lrc"))
        result = self.run_album("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIn(_NOTE, result.stdout)

    def test_the_run_summary_and_the_manifest_note_the_ignored_link(self) -> None:
        self.link("../secret.lrc")
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIn(_NOTE, result.stdout)
        cells = cli_fakes.row_cells(result.stdout, "01 Keeper.mp3")
        self.assertEqual(cells[2:4], ["LRCLIB", "synced"])
        self.assertIs(self.after().source, lm.Source.LRCLIB)
        self.assertEqual(self.after().sidecar_links_ignored, ("01 Keeper.lrc",))


class InsideLinkNoNoteTest(IgnoredLinkCase):
    """A link read through inside the album earns no note anywhere."""

    def setUp(self) -> None:
        """The track's ``.lrc`` links to a file elsewhere in the album."""
        super().setUp()
        shared = self.album / "shared.lrc"
        shared.write_text(fixtures.LIGHTHOUSE_LRC, encoding="utf-8", newline="")
        self.link("shared.lrc")

    def test_the_dry_run_has_no_note(self) -> None:
        result = self.run_album("--dry-run")
        self.assertIn("sidecar (synced)", result.stdout)
        self.assertNotIn("ignored", result.stdout)

    def test_the_run_summary_and_the_manifest_have_no_note(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertNotIn("ignored", result.stdout)
        self.assertEqual(self.after().sidecar_links_ignored, ())


class OlderManifestTest(IgnoredLinkCase):
    """A manifest written before the field existed still reads, with nothing ignored."""

    def test_an_after_block_without_the_field_reads_as_none_ignored(self) -> None:
        self.link("../secret.lrc")
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        path = lr.run_folder(self.album, run_fakes.RUN_ID) / lr.MANIFEST_NAME
        document = json.loads(path.read_bytes())
        del document["tracks"][0]["after"][_FIELD]
        manifest = lyrics_manifest.parse_manifest(json.dumps(document).encode())
        outcome = manifest.tracks[0].after
        assert outcome is not None  # the run reached its one track
        self.assertEqual(outcome.sidecar_links_ignored, ())

    def test_a_field_that_is_not_a_list_of_names_is_refused(self) -> None:
        self.link("../secret.lrc")
        self.assertEqual(self.run_album().code, album.EXIT_OK)
        path = lr.run_folder(self.album, run_fakes.RUN_ID) / lr.MANIFEST_NAME
        document = json.loads(path.read_bytes())
        document["tracks"][0]["after"][_FIELD] = ["../escape.lrc"]
        with self.assertRaises(lm.ManifestSchemaError):
            lyrics_manifest.parse_manifest(json.dumps(document).encode())
