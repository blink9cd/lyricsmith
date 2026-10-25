"""Fakes for the Genius step's seams, and the query and fake token its suites share.

For the Genius step: a scripted client, its song, the challenge its status hook raises,
and a clock that moves only when the fake sleeper sleeps, so the pacing and the retry
schedule run in no time. Beside them, the one query, fake token and step key the Genius
suites and the fetch-table suites ask with, and the real provider table built on such a
clock (`instant_table`), so an LRC step's retry waits take no time either. Every value is
invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, never from another suite, so each suite still imports alone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import lyrics_genius as lg
import lyrics_model as lm
import lyrics_providers as lp
import lyrics_table as ltable

#: The query the Genius suites and the fetch-table suites ask with.
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
#: An obviously fake Genius key: the real client is built with it, and never sends it.
FAKE_TOKEN = "fake-token-for-tests"
#: The per-request deadline a Genius step under test is built with: the chain's default.
DEADLINE_S = 30.0


def step_key(source: lm.Source, kind: lm.LyricsKind) -> lm.ProviderKey:
    """A ProviderKey, shorter."""
    return lm.ProviderKey(source, kind)


class FakeClock:
    """A monotonic clock that moves only when the fake sleeper sleeps or a test says so."""

    def __init__(self) -> None:
        """Start at a nonzero time, as a real monotonic clock does."""
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """The current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record the sleep and advance the clock by it."""
        self.sleeps.append(seconds)
        self.now += seconds


def instant_table() -> lm.ProviderTable:
    """The real table with no Genius key, on a fresh `FakeClock`: its waits take no time."""
    clock = FakeClock()
    return ltable.build_provider_table(None, clock=clock, sleeper=clock.sleep)


@dataclass(frozen=True)
class FakeSong:
    """What search_song returns: the lyrics text, and the song's title and credits.

    The title and artist default to `QUERY`'s, so a song built from its lyrics alone
    is the one asked for and passes the step's matcher. *primary_artists* is the
    library's list of credit records, each a mapping with a ``name``; empty, as the
    library leaves it when the reply has none, the matcher falls back to *artist*.
    """

    lyrics: str
    title: str = QUERY.title
    artist: str = QUERY.artist
    primary_artists: tuple[Mapping[str, object], ...] = ()


@dataclass
class FakeGeniusClient:
    """Stands in for lyricsgenius.Genius: plays back a script of answers and errors."""

    script: list[FakeSong | Exception | None]
    clock: Callable[[], float] | None = None
    calls: list[tuple[str | None, str, float | None]] = field(default_factory=list)

    def search_song(
        self, title: str | None = None, artist: str = ""
    ) -> FakeSong | None:
        """Record the call and the time it was made, then play the next scripted step."""
        self.calls.append((title, artist, self.clock() if self.clock else None))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def challenge(status: int) -> lp.HttpStatusError:
    """What the Genius client's status hook raises for *status*."""
    return lp.HttpStatusError(lm.Source.GENIUS, status)


def paced_genius(client: FakeGeniusClient, clock: FakeClock) -> lg.GeniusProvider:
    """A GeniusProvider on *client*, with the fake clock and sleeper."""
    client.clock = clock
    return lg.GeniusProvider(
        client, clock=clock, sleeper=clock.sleep, deadline_s=DEADLINE_S
    )
