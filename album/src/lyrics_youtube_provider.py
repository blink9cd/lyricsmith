"""The YouTube Music step: a timed step after NetEase's and an untimed one after Bandcamp's.

One set of requests serves both steps. A call asks the song search, then the chosen
track's watch panel for its lyrics tab, then that tab, through the run's one paced,
cookie-free `lyrics_youtube_music.YouTubeMusicClient`; the answer, timed or plain, is
kept by the chain for the track's plain step
(`lyrics_provider_kinds.AnswersEveryKind`), which sends nothing. The chain's synced
step takes it only with a timed line, and its plain step drops the stamps.

Only a result whose video is the song's audio track is matched, never a music video
or an upload, whose cut and timing are their own. No result matching is nothing found
after the search alone, and no lyrics tab, after two requests. The step is not asked
for a query with no artist, which the matcher cannot confirm a hit for, nor after the
client stopped (`asks`), so the chain records no attempt for either. The stripped-title
retry is another query, and asks three more requests.

Nothing here prints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lyrics_answer import LrcAnswer
from lyrics_cooldown import Stop
from lyrics_match import has_artist, rank_hits
from lyrics_model import (
    LyricsKind,
    SyncedLyrics,
    TrackQuery,
    parse_lrc,
)
from lyrics_records import MAX_LYRICS_CHARS
from lyrics_youtube_music import (
    BROWSE,
    NEXT,
    SEARCH,
    YouTubeMusicClient,
    browse_body,
    next_body,
    search_body,
)
from lyrics_youtube_reply import parse_lyrics, parse_lyrics_tab, parse_search

#: The bound for one call: three requests at least 5 s apart, each with its own timeout.
YOUTUBE_MUSIC_STEP_DEADLINE_S = 75.0
#: The stamp each line of a timed answer opens with: `lyrics_model.format_lrc`'s
#: millisecond form, which `lyrics_youtube_reply` writes.
_TIMED_LINE = re.compile(r"\[[0-9]{2,}:[0-5][0-9]\.[0-9]{3}\]")


def _answer(text: str | None) -> LrcAnswer:
    """The answer for both steps from *text*, the lyrics as `parse_lyrics` gave them.

    A timed answer stamps every line. Untimed text in which some line merely opens with
    a stamp would otherwise read as timed (`parse_lrc`), so its synced step gets
    nothing instead. Text over `MAX_LYRICS_CHARS` is passed on unread, for the chain to
    refuse before it parses anything.
    """
    if text is None or len(text) > MAX_LYRICS_CHARS:
        return LrcAnswer.same(text)
    timed = all(_TIMED_LINE.match(line) for line in text.splitlines())
    if not timed and isinstance(parse_lrc(text), SyncedLyrics):
        return LrcAnswer(synced=None, plain=text)
    return LrcAnswer.same(text)


@dataclass
class YouTubeMusicProvider:
    """The YouTube Music step, of either kind: the search, the lyrics tab, the lyrics.

    The table puts one instance under both of its keys, so both share the client's
    pace, lock and stop.
    """

    client: YouTubeMusicClient

    def asks(self, query: TrackQuery) -> bool:
        """False for a query with no artist, and for every query once the client stopped.

        The chain reads this to skip the step unrecorded
        (`lyrics_provider_kinds.AsksSelectively`).
        """
        return has_artist(query) and self.client.available()

    def step_deadline_s(self) -> float:
        """The watchdog's bound for one call (`lyrics_provider_kinds.OwnsDeadline`)."""
        return YOUTUBE_MUSIC_STEP_DEADLINE_S

    def answer_all_kinds(self, query: TrackQuery) -> LrcAnswer:
        """YouTube Music's lyrics for *query*, for both kinds of step.

        A query with no artist sends nothing and is None. The client raises a refusal
        as `lyrics_youtube_music.YouTubeMusicStopped`, which the chain records.
        """
        if not has_artist(query):
            return LrcAnswer.same(None)
        give_up_at = self.client.clock() + self.step_deadline_s()
        today = self.client.now().date()
        term = f"{query.title} {query.artist}"
        searched = self.client.post(SEARCH, search_body(term, today), give_up_at)
        songs = [song for song in parse_search(searched) if song.audio_track]
        ranked = rank_hits(query, songs, lambda song: song.hit)
        if not ranked:
            return LrcAnswer.same(None)
        panel = self.client.post(NEXT, next_body(ranked[0].video_id, today), give_up_at)
        tab = parse_lyrics_tab(panel)
        if tab is None:
            return LrcAnswer.same(None)
        lyrics = self.client.post(BROWSE, browse_body(tab), give_up_at)
        return _answer(parse_lyrics(lyrics))

    def __call__(self, query: TrackQuery) -> str | None:
        """The plain step's text for *query* (`answer_all_kinds`)."""
        return self.answer_all_kinds(query).text(LyricsKind.UNSYNCED)

    def provider_stop(self) -> Stop | None:
        """What stopped the client for the run, or None while it still runs.

        The table reads it through `lyrics_cooldown.table_stop`.
        """
        return self.client.stop
