"""The LRCLIB step, on LRCLIB's documented public API: the exact lookup, then the search.

The exact lookup (``GET /api/get``) asks by the track's title, artist, album and length
in whole seconds, all from its tags; LRCLIB matches the length within about two seconds
itself, and answers 404 when nothing matches, which is "not found" here, never an error.
A server error (5xx) from the lookup is treated the same way, because the lookup can be
overloaded while the search still answers. When the lookup finds no matching record
with text of a step's kind, or the album tag or a usable length is missing, the search
(``GET /api/search``) asks by title and artist, and its hits go through our matcher
(`lyrics_match`). The exact lookup's own answer goes through the matcher too, so a
lookup that matched loosely cannot slip another song in.

That field search can answer a server error for a song LRCLIB does not hold, while the
search by ``q`` (the artist, then the title) answers an empty list. So a field search
still failing with a server error once retried is followed by one search by ``q``,
unretried, only while the call's deadline has room and with its timeout cut to fit
what is left, whose hits go through the same matcher; its empty list is a real "no
match". If it fails too, the field search's error is the search's failure, so the
refusal still reads as overloaded. A server error from the search is never read as
"not found".

A record LRCLIB flags as instrumental is nothing found. A synced step takes only a
record's timed text; a plain step takes its plain text, else its timed text, whose
stamps the chain drops. Every request's status is read by the status hook, and a reply
in any other shape than the documented one is a `ProviderError` naming LRCLIB.

One call settles both kinds (`LrclibProvider.answer_all_kinds`), with only the requests
a synced step needs: any record holding timed text holds text for a plain step too.
The chain keeps that answer for the track's plain step. A search that answers an error
status or a refused reply, or whose request fails, fails only the kinds the lookup left
unsettled, so a plain text the lookup found is still taken. A search that runs past the
watchdog's deadline, or fails any other way, fails the whole call, and the lookup's text
with it.

The table's step retries a transient refusal of either request (`lyrics_http.Retry`),
both requests sharing one call's deadline; a server error from the lookup still falls
through to the search unretried, since the search is the other try, and the search
by ``q`` is likewise the field search's other try.

Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias

import requests

from lyrics_answer import KindAnswer, LrcAnswer
from lyrics_breakers import BreakerGuard
from lyrics_http import (
    PROVIDER_DEADLINE_S,
    REQUEST_TIMEOUT_S,
    RequestTimeout,
    Retry,
    RetryPolicy,
    get_json,
    get_reply,
    new_session,
)
from lyrics_match import Hit, has_artist, rank_hits, usable_length
from lyrics_model import LyricsKind, Source, TrackQuery
from lyrics_providers import ProviderError, RetriedError, http_status
from lyrics_reply import flag_field, number_field, unexpected_reply

#: LRCLIB's public API root.
LRCLIB_API = "https://lrclib.net/api"
LRCLIB_GET_URL = f"{LRCLIB_API}/get"
LRCLIB_SEARCH_URL = f"{LRCLIB_API}/search"
#: The status the exact lookup answers when no record matches.
NOT_FOUND = 404
#: Server errors: the exact lookup can answer one (an overload) while the search still
#: answers, so from the lookup they mean "ask the search" rather than an error.
SERVER_ERRORS = range(500, 600)
#: The exact lookup's statuses that fall through to the search.
LOOKUP_FALLS_THROUGH = frozenset({NOT_FOUND, *SERVER_ERRORS})
#: The part of the call's time left that the search by ``q``'s timeout never spends.
#: The watchdog's clock starts before the call's own deadline is set, and a timed-out
#: request still has to unwind and close its session, so a timeout ending exactly at
#: the deadline would end past the watchdog, reading as a timeout, not an overload.
Q_SEARCH_HEADROOM_S = 1.0
#: The least budget (the time left less `Q_SEARCH_HEADROOM_S`) the search by ``q`` is
#: sent with. Its timeout is cut to the budget in the default's proportions, where
#: connecting has a third, so below this its connect window is under a second.
Q_SEARCH_MIN_BUDGET_S = 3.0


@dataclass(frozen=True)
class LrclibRecord:
    """One LRCLIB record: what the matcher reads, and the lyrics it holds."""

    hit: Hit
    synced: str | None
    plain: str | None
    instrumental: bool


def _unexpected(what: str) -> ProviderError:
    """The error for an LRCLIB reply whose *what* is not in the documented shape."""
    return unexpected_reply(Source.LRCLIB, what)


def _optional_text(record: Mapping[str, object], name: str) -> str | None:
    """Field *name* of *record* as text, or None when absent or null."""
    value = record.get(name)
    if value is None or isinstance(value, str):
        return value
    raise _unexpected(f"{name} is not text")


def _required_text(record: Mapping[str, object], name: str) -> str:
    """Field *name* of *record* as text; absent or null is out of shape."""
    value = record.get(name)
    if isinstance(value, str):
        return value
    raise _unexpected(f"{name} is not text")


# fuzz-target
def parse_record(value: object) -> LrclibRecord:
    """The record *value* holds; raise `ProviderError` for any other shape."""
    if not isinstance(value, dict):
        raise _unexpected("a record is not an object")
    instrumental = flag_field(value, "instrumental", Source.LRCLIB)
    title = _required_text(value, "trackName")
    artist = _required_text(value, "artistName")
    return LrclibRecord(
        Hit(title, (artist,), number_field(value, "duration", Source.LRCLIB)),
        synced=_optional_text(value, "syncedLyrics"),
        plain=_optional_text(value, "plainLyrics"),
        instrumental=instrumental,
    )


# fuzz-target
def parse_records(value: object) -> tuple[LrclibRecord, ...]:
    """The search's records *value* holds; raise `ProviderError` for any other shape."""
    if not isinstance(value, list):
        raise _unexpected("the search answer is not a list")
    return tuple(parse_record(item) for item in value)


def text_of(record: LrclibRecord, kind: LyricsKind) -> str | None:
    """The text a step of *kind* takes from *record*; None for an instrumental.

    An empty string counts as absent.
    """
    if record.instrumental:
        return None
    if kind is LyricsKind.SYNCED:
        return record.synced or None
    return record.plain or record.synced or None


def exact_params(query: TrackQuery) -> dict[str, str | int] | None:
    """The exact lookup's parameters, or None when the album or the length is missing."""
    duration = usable_length(query.duration_s)
    if duration is None:
        return None
    if query.album is None or not query.album.strip():
        return None
    return {
        "track_name": query.title,
        "artist_name": query.artist,
        "album_name": query.album,
        "duration": round(duration),
    }


#: What a settled kind of step gets: its text, or None for none.
_Texts: TypeAlias = dict[LyricsKind, str | None]


def _settled_by(record: LrclibRecord | None) -> _Texts:
    """The kinds the exact lookup's matched *record* settles, each with its text.

    An instrumental settles every kind with no text; otherwise a kind is settled when
    the record holds text a step of that kind takes. An unsettled kind asks the search.
    """
    if record is None:
        return {}
    if record.instrumental:
        return dict.fromkeys(LyricsKind)
    texts = {kind: text_of(record, kind) for kind in LyricsKind}
    return {kind: text for kind, text in texts.items() if text is not None}


def _best_text(ranked: Sequence[LrclibRecord], kind: LyricsKind) -> str | None:
    """The text of *kind* from the best of *ranked* that holds one, or None."""
    for record in ranked:
        text = text_of(record, kind)
        if text is not None:
            return text
    return None


@dataclass(frozen=True)
class LrclibProvider:
    """The LRCLIB step of one kind: the exact lookup, then the search.

    `answer_all_kinds` settles both kinds at once, which the chain keeps for the fetch;
    calling the step returns its own kind's text from that answer. With a *retry*
    policy, each request retries a transient refusal within *deadline_s* of the
    call's start, the watchdog's bound for it; with none, it is asked once.
    """

    kind: LyricsKind
    session_factory: Callable[[], requests.Session] = new_session
    retry: RetryPolicy | None = None
    deadline_s: float = PROVIDER_DEADLINE_S
    #: The run's breaker for this provider, shared by its steps; the fetch layer asks
    #: it before each call (`lyrics_breakers`). None leaves the steps unguarded.
    breaker: BreakerGuard | None = field(
        default=None, kw_only=True, compare=False, repr=False
    )

    def asks(self, query: TrackQuery) -> bool:
        """False for a query with no artist, which the matcher cannot confirm a hit for.

        Such a query is a file stem standing in for a missing artist or title tag; the
        chain reads this to skip the step unrecorded (`lyrics_provider_kinds.AsksSelectively`).
        """
        return has_artist(query)

    def __call__(self, query: TrackQuery) -> str | None:
        """LRCLIB's text of this step's kind for *query*, or None when none matches."""
        return self.answer_all_kinds(query).text(self.kind)

    def answer_all_kinds(self, query: TrackQuery) -> LrcAnswer:
        """LRCLIB's text of both kinds for *query*: each None when none matches.

        Sent requests are only those the synced step needs, since the plain text is
        settled by any record a synced step reads. A query it does not ask (`asks`)
        sends nothing. A fresh session per call, so a call the watchdog abandoned never
        shares one with the next.
        """
        if not self.asks(query):
            return LrcAnswer.same(None)
        retry = None if self.retry is None else self.retry.within(self.deadline_s)
        with self.session_factory() as session:
            settled = _settled_by(self._exact(session, query, retry))
            texts: Mapping[LyricsKind, KindAnswer] = settled
            if len(settled) < len(LyricsKind):
                texts = self._searched(session, query, settled, retry)
        return LrcAnswer(
            synced=texts[LyricsKind.SYNCED], plain=texts[LyricsKind.UNSYNCED]
        )

    def _exact(
        self, session: requests.Session, query: TrackQuery, retry: Retry | None
    ) -> LrclibRecord | None:
        """The exact lookup's record when the matcher accepts it, else None.

        None also when the album or a usable length is missing, so no lookup is sent,
        and when the lookup answers 404 or a server error.
        """
        params = exact_params(query)
        if params is None:
            return None
        reply = get_json(
            session,
            LRCLIB_GET_URL,
            params,
            Source.LRCLIB,
            absent=LOOKUP_FALLS_THROUGH,
            retry=retry,
        )
        if reply is None:
            return None
        matched = rank_hits(query, [parse_record(reply.body)], lambda r: r.hit)
        return matched[0] if matched else None

    def _searched(
        self,
        session: requests.Session,
        query: TrackQuery,
        settled: _Texts,
        retry: Retry | None,
    ) -> dict[LyricsKind, KindAnswer]:
        """*settled*, with each other kind's text from the best matching search hit.

        A field search failing with a server error asks the search by ``q`` once
        (`_q_fallback`), whose hits, none included, are then the search's answer. A
        search answering an error status or a refused reply, or whose request fails,
        with no answering fallback, is each unsettled kind's failure, while the kinds
        the lookup settled keep their text: the plain step still takes what the lookup
        found. Any other failure propagates and fails the whole call.
        """
        params = {"track_name": query.title, "artist_name": query.artist}
        try:
            ranked = _ranked_search(session, query, params, retry)
        # An error status, a refused reply shape, or the transport failing: carried to
        # the steps that needed the search, each of which records it as its error.
        except (ProviderError, requests.RequestException) as error:
            fallback = _q_fallback(session, query, error, retry)
            if fallback is None:
                return {kind: settled.get(kind, error) for kind in LyricsKind}
            ranked = fallback
        return {
            kind: settled.get(kind, _best_text(ranked, kind)) for kind in LyricsKind
        }


def _ranked_search(
    session: requests.Session,
    query: TrackQuery,
    params: Mapping[str, str],
    retry: Retry | None,
    timeout_s: RequestTimeout = REQUEST_TIMEOUT_S,
) -> list[LrclibRecord]:
    """The search's records for *params*, those the matcher accepts for *query*, best first.

    Each request has *timeout_s*. Any error status, a refused reply shape, or the
    transport failing raises.
    """
    reply = get_reply(
        session,
        LRCLIB_SEARCH_URL,
        params,
        Source.LRCLIB,
        retry=retry,
        timeout_s=timeout_s,
    )
    return rank_hits(query, parse_records(reply.body), lambda r: r.hit)


def _overloaded(error: Exception) -> bool:
    """Whether *error* is a server error status, retried or not."""
    last = error.last if isinstance(error, RetriedError) else error
    return http_status(last) in SERVER_ERRORS


def _q_timeout(retry: Retry | None) -> RequestTimeout | None:
    """The search by ``q``'s timeout, fitted to the call's time left; None to not send it.

    The default timeout, scaled down when its two halves together would outlast the
    budget, what is left less `Q_SEARCH_HEADROOM_S`. That bounds a silent server only,
    which then fails inside the call as a transport error rather than past the
    watchdog, where the attempt would read as a timeout, not an overload. A read
    timeout runs between bytes, so a server trickling its reply can still overrun, and
    resolving the name is not bounded at all: the watchdog stays the last bound. None
    when the budget is under `Q_SEARCH_MIN_BUDGET_S`. With no *retry*, the call has no
    clock here, and the watchdog alone bounds it.
    """
    if retry is None:
        return REQUEST_TIMEOUT_S
    budget_s = retry.give_up_at - retry.policy.clock() - Q_SEARCH_HEADROOM_S
    if budget_s < Q_SEARCH_MIN_BUDGET_S:
        return None
    connect_s, read_s = REQUEST_TIMEOUT_S
    scale = min(1.0, budget_s / (connect_s + read_s))
    return (connect_s * scale, read_s * scale)


def _q_fallback(
    session: requests.Session,
    query: TrackQuery,
    error: Exception,
    retry: Retry | None,
) -> list[LrclibRecord] | None:
    """The ranked hits of one search by ``q``, after the field search failed with *error*.

    Sent only when *error* is a server error and the call has time left for it
    (`_q_timeout`): the field search can answer 503 for a song LRCLIB does not hold
    while the search by ``q`` answers an empty list, which is a real "no match". That
    empty list is taken as LRCLIB's answer on the evidence of 2026-11-01, when ``q``
    answered while the field search shed load. One attempt and no retry, since the
    field search already spent the call's retries waiting on the same server. None
    when it is not sent or fails as the field search may, so *error* stays the
    provider's failure: never a "not found", which would let fix-text remove a text
    on an outage.
    """
    timeout_s = _q_timeout(retry)
    if not _overloaded(error) or timeout_s is None:
        return None
    params = {"q": f"{query.artist} {query.title}"}
    try:
        return _ranked_search(session, query, params, None, timeout_s)
    # The fallback failing tells nothing the field search's failure did not.
    except (ProviderError, requests.RequestException):
        return None
