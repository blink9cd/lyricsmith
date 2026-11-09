"""On a Windows CI runner the link proofs must run, never skip.

The Windows-only proofs of the delete and the no-follow reads make real symbolic links
and junctions, and a symbolic-link case skips itself when the host refuses one
("symlinks unavailable"). A gate prints failures and a skip count only, so a runner
that lost the right to make links would turn those proofs into silent skips and the run
would still read green. This guard fails that run instead. Off Windows, or outside CI,
it skips: there the proofs are POSIX's own or a person's local run.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above

#: The variable GitHub Actions, like most CI services, sets to "true" on every runner.
_CI_VARIABLE = "CI"


def links_unavailable(base: Path) -> str | None:
    """Why a symbolic link (and, on Windows, a junction) cannot be made under *base*.

    None when both are made. *base* must be an empty folder the caller owns.
    """
    folder = base / "target"
    folder.mkdir()
    try:
        os.symlink(folder, base / "link", target_is_directory=True)
    except OSError as error:
        return f"a symbolic link cannot be made: {error}"
    if sys.platform == "win32":
        import _winapi  # pragma: only-on win32 - only Windows has it

        try:  # pragma: only-on win32
            _winapi.CreateJunction(str(folder), str(base / "junction"))
        except OSError as error:  # pragma: only-on win32
            return f"a junction cannot be made: {error}"
    return None


def on_windows_ci() -> bool:
    """Whether this run is a Windows CI runner, where the link proofs must run."""
    return sys.platform == "win32" and os.environ.get(_CI_VARIABLE) == "true"


class LinksUnavailableTest(fixtures.IsolatedCase):
    """The probe names what the host refused, and nothing when it refuses nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "probe"
        self.base.mkdir()

    def test_a_host_that_makes_links_has_no_reason(self) -> None:
        self.assertIsNone(links_unavailable(self.base))
        self.assertTrue((self.base / "link").is_symlink())

    def test_a_refused_link_is_named(self) -> None:
        refused = OSError(1314, "A required privilege is not held by the client")
        with mock.patch.object(os, "symlink", side_effect=refused):
            reason = links_unavailable(self.base)
        assert reason is not None
        self.assertIn("a symbolic link cannot be made", reason)
        self.assertIn("required privilege", reason)


class OnWindowsCiTest(fixtures.IsolatedCase):
    """The guard binds on a Windows CI runner alone."""

    def test_windows_with_ci_set_binds(self) -> None:
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict(os.environ, {_CI_VARIABLE: "true"}),
        ):
            self.assertTrue(on_windows_ci())

    def test_windows_outside_ci_does_not_bind(self) -> None:
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict(os.environ, {_CI_VARIABLE: "false"}),
        ):
            self.assertFalse(on_windows_ci())

    def test_another_host_in_ci_does_not_bind(self) -> None:
        with (
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch.dict(os.environ, {_CI_VARIABLE: "true"}),
        ):
            self.assertFalse(on_windows_ci())


class WindowsProofsRunTest(fixtures.IsolatedCase):
    """On a Windows CI runner, links can be made, so no link proof skips."""

    @unittest.skipUnless(on_windows_ci(), "binds on a Windows CI runner only")
    def test_this_runner_makes_links_and_junctions(self) -> None:
        reason = links_unavailable(self.tmp)  # pragma: only-on win32
        self.assertIsNone(reason, reason)  # pragma: only-on win32


if __name__ == "__main__":  # pragma: no cover - entry point for a direct run
    unittest.main()
