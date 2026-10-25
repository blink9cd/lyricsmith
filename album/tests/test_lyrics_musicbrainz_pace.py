"""MusicBrainz counts its one-a-second interval from when a request ends, not begins.

Its requests run under Bandcamp's lock, retries included, so the next turn waits a whole
interval after the last reply. The fake server spends half an interval answering each
request, which a pace counted from when a request begins would subtract from its wait.
Every name and reply is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import requests

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import genius_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above

ALBUM, ARTIST = "The Lantern Years", "Invented Band"
#: How long past now the call's deadline lies: room for every request and wait.
ROOM_S = 30.0


class EndCountedPaceTest(fixtures.IsolatedCase):
    """The wait before each request is the whole interval since the last one ended."""

    def test_the_interval_counts_from_when_a_request_ends(self) -> None:
        clock = genius_fakes.FakeClock()
        client = lmb.MusicBrainzClient(clock=clock, sleeper=clock.sleep)
        no_links = bf.relations_reply()
        server = bf.BandcampServer(
            {
                (bf.MB_HOST, bf.SEARCH_PATH): bf.search_reply(
                    bf.release(bf.RELEASE_1, ALBUM, ARTIST)
                ),
                (bf.MB_HOST, bf.release_path(bf.RELEASE_1)): no_links,
                (bf.MB_HOST, bf.artist_path(bf.ARTIST_1)): no_links,
            },
            clock=clock,
        )
        spent = lmb.MUSICBRAINZ_MIN_INTERVAL_S / 2

        def slow(request: requests.PreparedRequest) -> requests.Response:
            answer = server(request)
            clock.now += spent
            return answer

        with http_fakes.transport(slow):
            client.find_bandcamp(ALBUM, ARTIST, clock() + ROOM_S)
        self.assertEqual(len(server.times), 3)
        self.assertEqual(clock.sleeps, [lmb.MUSICBRAINZ_MIN_INTERVAL_S] * 2)


if __name__ == "__main__":
    unittest.main()
