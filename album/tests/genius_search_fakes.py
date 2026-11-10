"""Fakes for Genius's keyed search: a scripted client, and the replies it plays back.

`FakeSearchClient` stands in for ``lyricsgenius.Genius`` as far as the keyed search
calls it (`lyrics_genius_search.GeniusSearchClient`): it records each search term and
page size, then plays back the next scripted reply or raises the next scripted error.
`song_result` and `search_reply` build replies in the API's own shape, the parsed body
of ``api.genius.com/search``. `searcher` builds the searcher a suite drives on the fake
clock, and `TERM`, `ONE_SONG`, and `NOW` are the term, reply, and breaker moment the
search suites share. Every title, artist, and URL is invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``; sweep's suites reach it the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import genius_fakes as gf
import lyrics_genius_search as lgs

#: The invented song page a result names unless a test gives another.
SONG_URL = "https://genius.com/Invented-artist-the-keeper-counts-lyrics"
#: A URL on no Genius host, which nothing may ever write.
FOREIGN_URL = "https://elsewhere.example/the-keeper-counts"

#: The term every searcher test asks with.
TERM = "The Keeper Counts Invented Artist"
#: A fixed moment for the breaker's clock.
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

#: What one scripted step of a fake search is: a reply, or an error to raise.
Step = Mapping[str, object] | BaseException


def song_result(
    title: str,
    artist: str,
    *,
    url: str = SONG_URL,
    lyrics_state: str = "complete",
    instrumental: bool = False,
    primary_artists: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """One search hit's song record, as the keyed API gives it.

    *primary_artists*, when given, is the credit list's names in order; otherwise the
    record carries none, as an older reply does, and only ``primary_artist``.
    """
    record: dict[str, object] = {
        "title": title,
        "url": url,
        "lyrics_state": lyrics_state,
        "instrumental": instrumental,
        "primary_artist": {"name": artist},
    }
    if primary_artists is not None:
        record["primary_artists"] = [{"name": name} for name in primary_artists]
    return record


def search_reply(*results: Mapping[str, object]) -> dict[str, object]:
    """The keyed search's parsed reply holding *results*, each a song hit, in order."""
    return {"hits": [{"type": "song", "result": dict(result)} for result in results]}


@dataclass
class FakeSearchClient:
    """Plays back a script of search replies and errors, recording every call.

    *clock*, when given, is read as each call is made, so a test sees when it was sent.
    """

    script: list[Step]
    clock: Callable[[], float] | None = None
    calls: list[tuple[str, int | None]] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    def search_songs(
        self, search_term: str, per_page: int | None = None, page: int | None = None
    ) -> Mapping[str, object]:
        """Record the term and page size, then play the next scripted step."""
        self.calls.append((search_term, per_page))
        if self.clock is not None:
            self.times.append(self.clock())
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


#: A reply holding one complete song, the one `TERM` asks for.
ONE_SONG = search_reply(song_result("The Keeper Counts", "Invented Artist"))


def searcher(client: FakeSearchClient, clock: gf.FakeClock) -> lgs.GeniusSearcher:
    """A searcher on *client*, paced and retried on the fake *clock*."""
    client.clock = clock
    return lgs.GeniusSearcher(
        client, clock=clock, sleeper=clock.sleep, deadline_s=gf.DEADLINE_S
    )
