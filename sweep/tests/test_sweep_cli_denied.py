"""The command line's refusals of a folder or a file, proven on every host.

Each real-permission test of `test_sweep_cli` runs only where ``chmod`` binds (root and
Windows are refused nothing it sets), so each has a twin here that raises the same
refusal through a patched call (`denied_fixtures`), and the branch it proves is reached
on Windows too. Every album here is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import denied_fixtures as dn  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_ledger as sl  # noqa: E402 - import follows the sys.path bootstrap above
import test_sweep_cli as cli  # noqa: E402 - import follows the sys.path bootstrap above


class LibraryDeniedTest(cli.CliCase):
    """A library or an artist folder whose listing is refused."""

    def test_a_library_whose_listing_is_refused_exits_two(self) -> None:
        with dn.listing_refused(self.library):
            result = self.main("--mode", "survey")
        self.assertEqual(result.code, 2)
        self.assertIn("cannot be listed", result.stderr)

    def test_an_artist_folder_whose_listing_is_refused_is_noticed_by_name(
        self,
    ) -> None:
        locked = self.library / "Locked"
        locked.mkdir()
        with dn.listing_refused(locked):
            result = self.main("--mode", "survey")
        self.assertIn("Locked", result.stderr)


class LedgerWriteDeniedTest(cli.CliCase):
    """A ledger whose write is refused stops the run, exit 2, its report still printed."""

    def test_a_ledger_write_refused_exits_two_naming_it(self) -> None:
        sf.needy_album(self.library, "Artist", "A1")
        ledger = sl.ledger_path(self.library, {"XDG_STATE_HOME": str(self.state)})
        real_write = sl.write_state_file

        def refuse_the_ledger(path: Path, data: bytes) -> None:
            if path == ledger:
                raise dn.denied(path)
            real_write(path, data)

        with mock.patch.object(sl, "write_state_file", refuse_the_ledger):
            result = self.main("--mode", "run")
        self.assertEqual(result.code, 2)
        self.assertIn(str(ledger), result.stderr)
        self.assertNotIn("cannot be listed", result.stderr)
        self.assertIn("Artist/A1", result.stdout)
        self.assertIn("ledger", result.stdout)


if __name__ == "__main__":  # pragma: no cover - the unittest entry point
    unittest.main()
