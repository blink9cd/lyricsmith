"""What does not stop Tekstowo for the run: a refused redirect, a marker in a read page.

A redirect off the site fails only that track, so one odd ``Location`` never switches
the source off for a day; a page its reader recognizes is read, whatever words its
lyrics hold, so a submission quoting a challenge marker never stops Tekstowo for every
user. A page not recognized that carries a marker is still a challenge, and a stop.

Every request is answered through `http_fakes.transport` by `tekstowo_fakes.TekstowoServer`;
the clock is a `FakeClock`, and each cool-down file lies under the test's own temporary
directory. Every page and name is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import lyrics_http as lh  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_pages as tp  # noqa: E402 - import follows the sys.path bootstrap above
from bandcamp_fakes import Redirect  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_tekstowo_client import (  # noqa: E402 - import follows the sys.path bootstrap above
    HOST,
    PAGE,
    ROOM_S,
    SEARCH,
    SEARCH_URL,
    _ClientCase,
    _site,
)

#: A lyrics line quoting a challenge marker, as a user's submission might.
MARKED_LINE = "she sang of cf-chl and just a moment"
#: A page no reader recognizes, holding a challenge marker.
CHALLENGE_PAGE = "<html><title>Just a moment...</title></html>"


def _none(text: str) -> None:
    """Read no page as recognized."""
    del text


class RefusedRedirectTest(_ClientCase):
    """A redirect off the site is that track's error alone: no stop, no cool-down."""

    LOCATIONS = (
        "https://example.com/x",
        f"http://{HOST}/x",
        f"https://visitor@{HOST}/x",
        f"https://{HOST}:8443/x",
    )

    def test_a_refused_redirect_fails_the_track_and_stops_nothing(self) -> None:
        for index, location in enumerate(self.LOCATIONS):
            with self.subTest(location=location):
                tmp = self.tmp / str(index)
                routes: dict[tuple[str, str], object] = {
                    (HOST, SEARCH): Redirect(location),
                    (HOST, "/a"): PAGE,
                }
                server, client = self.serve(routes, tmp)
                with self.assertRaises(lp.ProviderError) as raised:
                    self.get(client)
                self.assertEqual(raised.exception.detail, lh.REDIRECT_REFUSED)
                self.assertNotIsInstance(raised.exception, lt.TekstowoStopped)
                self.assertIsNone(client.stop)
                self.assertFalse(tf.state_path(tmp).exists())
                self.assertEqual(self.get(client, _site("/a")), PAGE)
                self.assertEqual(len(server.requests), 2)

    def test_a_redirect_naming_port_443_is_followed(self) -> None:
        routes = {(HOST, SEARCH): Redirect(f"https://{HOST}:443/a"), (HOST, "/a"): PAGE}
        server, client = self.serve(routes)
        self.assertEqual(self.get(client), PAGE)
        self.assertEqual(len(server.requests), 2)
        self.assertIsNone(client.stop)


class MarkedPageTest(_ClientCase):
    """Challenge markers are read only in a page its reader does not recognize."""

    def test_a_recognized_page_holding_a_marker_is_read(self) -> None:
        marked = f"<html><body><p>{MARKED_LINE}</p></body></html>"
        _, client = self.serve({(HOST, SEARCH): marked})
        self.assertEqual(self.get(client), marked)
        self.assertIsNone(client.stop)
        self.assertFalse(tf.state_path(self.tmp).exists())

    def test_a_song_page_holding_a_marker_gives_its_lyrics(self) -> None:
        query_lines = (MARKED_LINE, tp.LINES[1])
        routes: dict[tuple[str, str], object] = {
            (HOST, SEARCH): tp.search_page((tp.HREF, f"{tf.ARTIST} - {tf.TITLE}")),
            (HOST, tp.HREF): tp.song_page(lines=query_lines),
        }
        _, client = self.serve(routes)
        provider = lt.TekstowoProvider(client)
        query = lm.TrackQuery(tf.ARTIST, tf.TITLE)
        self.assertEqual(provider(query), "\n".join(query_lines))
        self.assertIsNone(client.stop)

    def test_an_unrecognized_page_holding_a_marker_is_a_challenge(self) -> None:
        server, client = self.serve({(HOST, SEARCH): CHALLENGE_PAGE})
        with self.assertRaises(lt.TekstowoStopped) as raised:
            client.get(
                SEARCH_URL,
                self.clock() + ROOM_S,
                read=_none,
                unrecognized=lt.SEARCH_NOT_RECOGNIZED,
            )
        self.assertEqual(raised.exception.detail, lt.CHALLENGE)
        assert client.stop is not None
        self.assertEqual(client.stop.what, lt.CHALLENGE)
        self.assertTrue(tf.state_path(self.tmp).exists())
        self.assertEqual(len(server.requests), 1)
