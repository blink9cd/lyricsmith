"""The cookie-free session: its jar refuses every cookie a reply sets.

No test here reaches the network: the request and the reply are stand-ins handed to
the jar directly, as `requests` itself hands them over after a send.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above

#: The page the stand-in reply answers; never requested.
URL = "https://musicbrainz.org/ws/2/release"
#: The cookie the stand-in reply sets, for the whole domain.
SET_COOKIE = "session=x; Domain=.musicbrainz.org"


def _extract(session: requests.Session) -> int:
    """How many cookies *session*'s jar holds after the stand-in reply sets one."""
    return http_fakes.cookies_kept(
        session, method="GET", url=URL, set_cookie=SET_COOKIE
    )


class CookieFreeSessionTest(fixtures.IsolatedCase):
    """A reply's cookie is never stored, so none can be sent back."""

    def test_the_jar_stays_empty(self) -> None:
        self.assertEqual(_extract(lh.cookie_free_session()), 0)

    def test_a_plain_session_would_have_stored_it(self) -> None:
        self.assertEqual(_extract(requests.Session()), 1)

    def test_it_is_still_a_new_session(self) -> None:
        session = lh.cookie_free_session()
        self.assertEqual(session.headers["User-Agent"], lh.user_agent())
