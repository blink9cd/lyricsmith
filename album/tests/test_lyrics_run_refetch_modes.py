"""`--refetch-untimed` beside the other options and modes: refusals, dry run, check, undo.

The option belongs to a run that asks the providers, so a fresh run, an offline run
and every other mode refuse it. A dry run with it previews which tracks would be asked
again, writing nothing. A refetch run's record needs nothing new from the check or
from undo: the check judges its written tracks as any run's, and undo puts every
track back byte for byte, an undecodable old sidecar included. The manifest names each
track the run asked again, and one written before the field existed reads as asking
none. Every lyric is invented and every provider faked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_manifest  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_modes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_builders as rb  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_refetch import (  # noqa: E402 - import follows the sys.path bootstrap above
    REFETCH,
    SYLT,
    WRONG_TXT,
    WRONG_USLT,
    RefetchCase,
)
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above

RUN_ID = run_fakes.RUN_ID
#: The most bytes the backup copies of a sidecar: four a character, and a byte-order mark.
_BYTE_CAP = 4 * lr.MAX_LYRICS_CHARS + 3


class RefetchRefusalTest(RefetchCase):
    """The option is refused wherever it could not mean what it says."""

    def setUp(self) -> None:
        """An untimed-only keeper, so a write would show."""
        super().setUp()
        self.paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)

    def assert_refused(self, message: str, *extra: str) -> None:
        """The run with the option and *extra* exits 2 with *message*, writing nothing."""
        before = raw(self.paths)
        result = self.run_album(REFETCH, *extra)
        self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
        self.assertIn(message, result.stderr)
        self.assertEqual(raw(self.paths), before)
        self.assertTrue(self.no_run_folder())
        self.assertEqual(self.provider_calls, [])

    def test_fresh_refuses_it(self) -> None:
        self.assert_refused(f"{REFETCH} cannot be used with --fresh", "--fresh")

    def test_offline_refuses_it(self) -> None:
        self.assert_refused(f"{REFETCH} cannot be used with --offline", "--offline")

    def test_every_other_mode_refuses_it(self) -> None:
        for mode in (album.MODE_CHECK, album.MODE_UNDO, "strip"):
            with self.subTest(mode=mode):
                self.assert_refused(
                    f"{REFETCH} cannot be used with --mode {mode}", "--mode", mode
                )


class RefetchDryRunTest(RefetchCase):
    """A dry run previews which tracks a refetch would ask again, and writes nothing."""

    def plan_rows(self, *extra: str) -> list[str]:
        """The dry run's rows, after checking nothing was written."""
        before = rb.snapshots(self.paths)
        result = self.run_album(REFETCH, "--dry-run", *extra)
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertTrue(self.no_run_folder())
        return [line for line in result.stdout.splitlines() if ".mp3" in line]

    def test_an_untimed_only_track_plans_a_refetch(self) -> None:
        self.paths = self.keeper(WRONG_USLT, txt=WRONG_TXT)
        keeper, mill = self.plan_rows()
        self.assertTrue(keeper.endswith(f"  {lyrics_modes.PLAN_REFETCH}"), keeper)
        self.assertTrue(mill.endswith(f"  {lyrics_modes.PLAN_FETCH}"), mill)

    def test_a_synced_track_plans_as_without_it(self) -> None:
        self.paths = self.keeper(SYLT, txt=WRONG_TXT)
        keeper, _mill = self.plan_rows()
        self.assertTrue(keeper.endswith(lyrics_modes.PLAN_FRAMES_SYNCED), keeper)

    def test_an_undecodable_txt_plans_a_refetch_naming_it(self) -> None:
        self.paths = self.keeper()
        self.paths[0].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")
        keeper, _mill = self.plan_rows()
        self.assertIn(
            f"{lyrics_modes.PLAN_REFETCH}; 01 Keeper.txt is not UTF-8", keeper
        )

    def test_a_txt_past_the_backups_byte_cap_previews_its_refusal(self) -> None:
        # The backup refuses it unread, so the run never asks the track again: the
        # dry run shows the sidecar error a dry run without the option shows.
        self.paths = self.keeper()
        self.paths[0].with_suffix(".txt").write_bytes(b"a" * (_BYTE_CAP + 1))
        before = rb.snapshots(self.paths)
        result = self.run_album(REFETCH, "--dry-run")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        self.assertEqual(rb.snapshots(self.paths), before)
        (keeper,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn("ERROR sidecar", keeper)
        self.assertIn(f"too large: over {_BYTE_CAP} bytes", keeper)
        self.assertNotIn(lyrics_modes.PLAN_REFETCH, keeper)

    def test_a_linked_undecodable_txt_still_plans_a_refetch(self) -> None:
        # A link is recorded by its target, never copied, so the backup refuses none.
        self.paths = self.keeper()
        target = self.album / "elsewhere.dat"
        target.write_bytes(b"caf\xe9 by the quay\n")
        self.paths[0].with_suffix(".txt").symlink_to(target)
        keeper, _mill = self.plan_rows()
        self.assertIn(
            f"{lyrics_modes.PLAN_REFETCH}; 01 Keeper.txt is not UTF-8", keeper
        )

    def test_a_txt_past_the_backups_byte_cap_is_the_runs_backup_error(self) -> None:
        self.paths = self.keeper()
        self.paths[0].with_suffix(".txt").write_bytes(b"a" * (_BYTE_CAP + 1))
        result = self.run_album(REFETCH)
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        (keeper,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn("ERROR backup", keeper)

    def test_a_folder_under_a_sidecar_name_is_the_strip_error(self) -> None:
        self.paths = self.keeper(WRONG_USLT)
        self.paths[0].with_suffix(".txt").mkdir()
        result = self.run_album(REFETCH, "--dry-run")
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS)
        self.assertIn("ERROR strip", result.stdout)


class RefetchRecordTest(RefetchCase):
    """A refetch run's record: checked and undone as any run's."""

    def setUp(self) -> None:
        """The keeper answered unlike its old lyrics; an undecodable .txt beside it."""
        super().setUp()
        self.answer_both(synced={}, plain={"The Keeper": fixtures.LIGHTHOUSE_PLAIN})
        self.paths = self.keeper(WRONG_USLT)
        self.paths[0].with_suffix(".txt").write_bytes(b"caf\xe9 by the quay\n")

    def test_undo_restores_the_album_byte_for_byte(self) -> None:
        before, files = rb.snapshots(self.paths), raw(self.paths)
        _row, keeper, _mill = self.refetch()
        self.assertEqual(keeper.status, lr.OutcomeStatus.WRITTEN)
        self.assertNotEqual(raw(self.paths), files)
        result = self.run_album("--mode", "undo")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        self.assertEqual(rb.snapshots(self.paths), before)
        self.assertEqual(raw(self.paths), files)

    def test_check_judges_the_written_track(self) -> None:
        self.refetch()
        result = self.run_album("--mode", "check")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout)
        (row,) = [line for line in result.stdout.splitlines() if "01 Keeper" in line]
        self.assertIn("FLAG: unlike backup (similarity ", row)

    def test_the_manifest_names_the_track_asked_again(self) -> None:
        self.refetch()
        run_dir = lr.run_folder(self.album, RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_text(encoding="utf-8"))
        keeper, mill = (track["after"] for track in document["tracks"])
        self.assertIs(keeper["refetch_untimed"], True)
        self.assertIn("is not UTF-8", keeper["sidecar_unreadable"])
        self.assertIs(mill["refetch_untimed"], False)
        self.assertIsNone(mill["sidecar_unreadable"])

    def test_a_manifest_without_the_fields_asks_no_track_again(self) -> None:
        self.refetch()
        run_dir = lr.run_folder(self.album, RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_text(encoding="utf-8"))
        for track in document["tracks"]:
            del track["after"]["refetch_untimed"], track["after"]["sidecar_unreadable"]
        manifest = lyrics_manifest.parse_manifest(json.dumps(document).encode())
        for track in manifest.tracks:
            assert track.after is not None
            self.assertEqual(
                (track.after.refetch_untimed, track.after.sidecar_unreadable),
                (False, None),
            )

    def test_a_malformed_refetch_field_is_refused(self) -> None:
        self.refetch()
        run_dir = lr.run_folder(self.album, RUN_ID)
        document = json.loads((run_dir / lr.MANIFEST_NAME).read_text(encoding="utf-8"))
        document["tracks"][0]["after"]["refetch_untimed"] = "yes"
        with self.assertRaises(lm.ManifestSchemaError):
            lyrics_manifest.parse_manifest(json.dumps(document).encode())
