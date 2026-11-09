"""The one state-folder rule every record kept outside the library follows.

Every path here is invented; HOME and the XDG folders are redirected under the test's
own folder, so no test reaches the real state folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_accept_records as lar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_state as lst  # noqa: E402 - import follows the sys.path bootstrap above

#: An invented home folder, named by the environment given, never the process's own.
_GIVEN_HOME = "/srv/someone"


class StateRootTest(fixtures.IsolatedCase):
    """XDG_STATE_HOME when absolute, else HOME's from the environment, else the process's."""

    def test_an_absolute_state_home_wins(self) -> None:
        # Rooted at the host's drive: ``/srv/state`` alone is relative on Windows.
        state = fixtures.host_absolute("/srv/state")
        environ = {"XDG_STATE_HOME": str(state), "HOME": _GIVEN_HOME}
        self.assertEqual(lst.state_root(environ), state)

    def test_a_relative_or_empty_state_home_falls_back_under_the_given_home(
        self,
    ) -> None:
        fallback = Path(_GIVEN_HOME) / ".local" / "state"
        for state in ("relative/state", ""):
            with self.subTest(state=state):
                environ = {"XDG_STATE_HOME": state, "HOME": _GIVEN_HOME}
                self.assertEqual(lst.state_root(environ), fallback)

    def test_with_neither_the_process_home_is_used(self) -> None:
        self.assertEqual(lst.state_root({}), Path.home() / ".local" / "state")

    def test_an_empty_home_is_no_home(self) -> None:
        self.assertEqual(lst.state_root({"HOME": ""}), Path.home() / ".local" / "state")


class SharedRuleTest(fixtures.IsolatedCase):
    """The accept records and the cool-down files sit under the one root."""

    def test_both_follow_the_given_home(self) -> None:
        environ = {"HOME": _GIVEN_HOME}
        root = lst.state_root(environ)
        self.assertEqual(lar.records_root(environ).parent.parent, root)
        self.assertEqual(lyc.state_folder(environ).parent, root)
