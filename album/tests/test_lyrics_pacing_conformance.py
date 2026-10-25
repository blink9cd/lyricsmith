"""One pacing contract, held by every paced provider: YouTube Music, Tekstowo, Genius, MusicBrainz.

Each provider is reached through a small lane that makes one request through its real
class on a `FakeClock`, with the existing fakes answering: `youtube_fakes`'s scripted
server, `tekstowo_fakes`'s server, `genius_fakes`'s scripted client and
`bandcamp_fakes`'s MusicBrainz routes. Every check runs per provider as a subtest, so a
provider that drifts from the shared pacer fails by name. The two cooling clients also
hold the stop contract: a refusal stops the client and writes its cool-down under the
test's own temporary directory, and every later call is refused unsent.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import bandcamp_fakes as bf  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import http_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooling_client as lcc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_genius as lg  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_youtube_music as lym  # noqa: E402 - import follows the sys.path bootstrap above
import tekstowo_fakes as tf  # noqa: E402 - import follows the sys.path bootstrap above
import youtube_fakes as yf  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    QUERY,
    FakeClock,
    FakeGeniusClient,
    paced_genius,
)

#: How long past now a request's deadline lies when it is not the point of the test.
ROOM_S = 100.0
#: How many requests a lane's fake answers, more than any check makes.
SCRIPT_LEN = 4
#: The refusing status every cooling lane's fake answers with.
REFUSED = 429
_BODY = lym.search_body("the keeper counts", date(2026, 10, 21))
_PAGE = "<html><body><p>An invented page.</p></body></html>"
_SEARCH = (lt.TEKSTOWO_HOST, tf.search_path(tf.ARTIST, tf.TITLE))
_SEARCH_URL = lt.search_url(tf.ARTIST, tf.TITLE)
#: Each provider's message for a pace wait refused at its deadline.
LATE = {
    "YouTube Music": "the YouTube Music pace outlasts the deadline",
    "Tekstowo": "the Tekstowo pace outlasts the deadline",
    "Genius": "the Genius pace outlasts the deadline",
    "MusicBrainz": "the MusicBrainz lookup ran out of time",
}


@dataclass
class Lane:
    """One provider, reached through its real class: one request per `request` call.

    *times* are the fake clock's readings at each request sent, so their count is how
    many were sent. *cooling* is the client that stops for the run, None for a
    provider that never stops.
    """

    name: str
    interval_s: float
    request: Callable[[float], object]
    times: Callable[[], list[float]]
    cooling: lcc.CoolingClient | None = None


def youtube_lane(stack: ExitStack, tmp: Path, clock: FakeClock, refusing: bool) -> Lane:
    """YouTube Music's client posting a search to its scripted server."""
    first = (REFUSED, b"", {}) if refusing else yf.ok({})
    server = yf.ScriptedServer([first, *[yf.ok({})] * SCRIPT_LEN], clock)
    stack.enter_context(http_fakes.transport(server))
    client = yf.client(tmp, server, clock)
    return Lane(
        "YouTube Music",
        lym.YOUTUBE_MUSIC_MIN_INTERVAL_S,
        lambda give_up_at: client.post(lym.SEARCH, _BODY, give_up_at),
        lambda: server.times,
        client,
    )


def tekstowo_lane(
    stack: ExitStack, tmp: Path, clock: FakeClock, refusing: bool
) -> Lane:
    """Tekstowo's client getting the search page from its fake site."""
    server = tf.TekstowoServer({_SEARCH: REFUSED if refusing else _PAGE}, clock)
    stack.enter_context(http_fakes.transport(server))
    client = tf.client(tmp, clock)

    def request(give_up_at: float) -> object:
        return client.get(
            _SEARCH_URL,
            give_up_at,
            read=lambda text: text,
            unrecognized=lt.SEARCH_NOT_RECOGNIZED,
        )

    return Lane(
        "Tekstowo", lt.TEKSTOWO_MIN_INTERVAL_S, request, lambda: server.times, client
    )


def genius_lane(stack: ExitStack, tmp: Path, clock: FakeClock, refusing: bool) -> Lane:
    """The Genius step's paced search, on its scripted client; it never stops."""
    del stack, tmp, refusing
    client = FakeGeniusClient([None] * SCRIPT_LEN)
    genius = paced_genius(client, clock)
    return Lane(
        "Genius",
        lg.GENIUS_MIN_INTERVAL_S,
        # The step's own deadline is fixed from its clock; its paced search takes one.
        lambda give_up_at: genius._paced_search(QUERY, give_up_at),
        lambda: [when for _, _, when in client.calls if when is not None],
    )


def musicbrainz_lane(
    stack: ExitStack, tmp: Path, clock: FakeClock, refusing: bool
) -> Lane:
    """MusicBrainz's search, answered with no release, so one call is one request."""
    del tmp, refusing
    server = bf.BandcampServer(
        {(bf.MB_HOST, bf.SEARCH_PATH): bf.search_reply()}, clock=clock
    )
    stack.enter_context(http_fakes.transport(server))
    client = lmb.MusicBrainzClient(clock=clock, sleeper=clock.sleep)
    return Lane(
        "MusicBrainz",
        lmb.MUSICBRAINZ_MIN_INTERVAL_S,
        lambda give_up_at: client.find_bandcamp("An Album", "An Artist", give_up_at),
        lambda: server.times,
    )


LaneBuilder = Callable[[ExitStack, Path, FakeClock, bool], Lane]
#: Every paced provider, and the two that stop for the run.
PACED: tuple[LaneBuilder, ...] = (
    youtube_lane,
    tekstowo_lane,
    genius_lane,
    musicbrainz_lane,
)
COOLING: tuple[LaneBuilder, ...] = (youtube_lane, tekstowo_lane)


#: A check run against one lane, on its own clock and folder.
Check = Callable[[Lane, FakeClock, Path], None]


class _ConformanceCase(fixtures.IsolatedCase):
    """Runs a check once per lane, each on a fresh clock, fakes and folder."""

    def each_lane(
        self, builders: tuple[LaneBuilder, ...], check: Check, *, refusing: bool = False
    ) -> None:
        """Run *check* on each built lane inside its own subtest, so a drift names it.

        The check runs inside the subtest's body, not after a yield, so a failing
        assertion is recorded against that provider and the next lane still runs.
        """
        for index, build in enumerate(builders):
            with ExitStack() as stack:
                clock, tmp = FakeClock(), self.tmp / str(index)
                lane = build(stack, tmp, clock, refusing)
                with self.subTest(provider=lane.name):
                    check(lane, clock, tmp)


class PaceConformanceTest(_ConformanceCase):
    """Every provider keeps its interval and never begins a wait it cannot finish."""

    def test_two_requests_in_a_row_are_at_least_the_interval_apart(self) -> None:
        def check(lane: Lane, clock: FakeClock, tmp: Path) -> None:
            del tmp
            lane.request(clock() + ROOM_S)
            lane.request(clock() + ROOM_S)
            first, second = lane.times()
            self.assertGreaterEqual(second - first, lane.interval_s)
            self.assertEqual(clock.sleeps, [lane.interval_s])

        self.each_lane(PACED, check)

    def test_a_pace_ending_past_the_deadline_sends_and_sleeps_nothing(self) -> None:
        def check(lane: Lane, clock: FakeClock, tmp: Path) -> None:
            del tmp
            lane.request(clock() + ROOM_S)
            with self.assertRaises(lp.ProviderTimeout) as late:
                lane.request(clock() + lane.interval_s / 2)
            self.assertEqual((len(lane.times()), clock.sleeps), (1, []))
            # Each provider's own wording, recorded as the attempt's detail, is kept.
            self.assertEqual(str(late.exception), LATE[lane.name])

        self.each_lane(PACED, check)


class StopConformanceTest(_ConformanceCase):
    """A cooling client stops at a refusal, writes its cool-down, and sends no more."""

    def test_a_refusal_stops_writes_and_refuses_the_next_call_unsent(self) -> None:
        def check(lane: Lane, clock: FakeClock, tmp: Path) -> None:
            assert lane.cooling is not None
            with self.assertRaises(lp.ProviderError) as raised:
                lane.request(clock() + ROOM_S)
            stopped = type(raised.exception)
            self.assertIs(stopped, lane.cooling.stopped_error)
            self.assertFalse(lane.cooling.available())
            written = lyc.read_cooldown(lane.cooling.state_path, yf.NOW).cool_down
            assert written is not None
            self.assertEqual(written.what, f"HTTP {REFUSED}")
            self.assertTrue(lane.cooling.state_path.is_relative_to(tmp))
            with self.assertRaises(stopped):
                lane.request(clock() + ROOM_S)
            self.assertEqual(len(lane.times()), 1)

        self.each_lane(COOLING, check, refusing=True)

    def test_a_stopped_client_past_its_deadline_is_a_timeout(self) -> None:
        def check(lane: Lane, clock: FakeClock, tmp: Path) -> None:
            del tmp
            with self.assertRaises(lp.ProviderError):
                lane.request(clock() + ROOM_S)
            with self.assertRaises(lp.ProviderTimeout):
                lane.request(clock())
            self.assertEqual(len(lane.times()), 1)

        self.each_lane(COOLING, check, refusing=True)
