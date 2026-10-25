"""The fetch chain: what the track holds, then three synced steps, then six plain ones.

`fetch_lyrics` runs the chain for one track over an injected `ProviderTable`, so tests pass
fakes and the entry point passes the real one. The chain asks each LRC
provider for synced lyrics first and only then for plain text, because one search for
"either" returns an earlier provider's plain text before a later provider's timed lines.
The first accepted answer stops it. When none is accepted and at least one step answered
"no match" for the exact title, the chain runs once more on the title with its trailing
bracketed groups removed.

What the track already holds serves it like an answer, each kind in its place: a timed
``.lrc`` sidecar, then the track's embedded synced lyrics, serve it with no provider
asked, unless its header labels it machine-made, which is asked for as a
``.txt`` is; untimed lyrics, a ``.txt`` sidecar before the embedded ones, are kept when the
synced steps find nothing, so the plain steps run only for a track holding no lyrics.
An unlabeled sidecar holding the lyrics of any embedded frame of its kind, as one a
run wrote from them does under any ``--lang``, counts as that frame, so it serves and the frames
keep their place.
Placeholder lyrics held (`lyrics_placeholder`), in a sidecar or a frame, are no
lyrics held and never serve; an answer that is one is nothing found. So is an answer
the track's duration rules out, given one that can be read: synced lines whose latest
starts past the end, or more words a second than any song is sung at (`lyrics_accept`,
the check's `past end` and `too many words` rows), its attempt detail naming the row.
A timed answer declined far past the end (`far_past_end`) is another song, so a later
plain answer of that song, in the same fetch, its stripped-title retry included, is
declined too (`SameSong`).
What the track already holds is not judged so; a ``--refetch-implausible`` run picks
that (`lyrics_refetch`).

An LRC provider's reply holds both its timed and its plain text, so within one track's
fetch each is asked at most once per query (`AnswersEveryKind`): its plain step reads
what its synced step was answered, or re-records how that call failed, and sends
nothing. YouTube Music answers both kinds in one call too, so its timed step after
NetEase's and its plain step after Bandcamp's share one set of requests. The
stripped-title retry is another query, and asks again. The Bandcamp step
answers plain text only, and keeps each album's page for the run itself
(`lyrics_bandcamp`), so its later tracks and the retry send nothing. Tekstowo's
untimed step (`lyrics_tekstowo`) is the last, after Genius's.

Every provider call runs under a thread-watchdog deadline, since a provider makes several
requests and their per-request timeouts do not bound the whole. A provider that raises or
times out is an error for that step only; the chain moves on. An answer is cleaned and
parsed after the watchdog returns, so an oversized one is refused unread, and the chain
moves on as for any other answer it does not accept.

The real table, with its LRC adapters and the Bandcamp, YouTube Music, Genius and
Tekstowo steps, is `lyrics_table`'s.
Nothing here prints; every attempt comes back in the `FetchResult` instead.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias, TypeVar, runtime_checkable

# What a step accepts from an answer, and the attempt details saying why, live in
# `lyrics_accept`; re-exported, so the chain's callers and tests read them from here.
from lyrics_accept import DETAIL_ACCEPTED as DETAIL_ACCEPTED
from lyrics_accept import DETAIL_IMPLAUSIBLE as DETAIL_IMPLAUSIBLE
from lyrics_accept import DETAIL_NOTHING as DETAIL_NOTHING
from lyrics_accept import DETAIL_PLACEHOLDER as DETAIL_PLACEHOLDER
from lyrics_accept import DETAIL_SAME_SONG as DETAIL_SAME_SONG
from lyrics_accept import DETAIL_TOO_LARGE as DETAIL_TOO_LARGE
from lyrics_accept import DETAIL_UNTIMED as DETAIL_UNTIMED
from lyrics_accept import FAR_PAST_END_FACTOR as FAR_PAST_END_FACTOR
from lyrics_accept import SameSong as SameSong
from lyrics_accept import clean_genius_text as clean_genius_text
from lyrics_accept import far_past_end as far_past_end
from lyrics_accept import judge_answer as judge_answer
from lyrics_accept import normalize_answer as normalize_answer
from lyrics_answer import LrcAnswer

# PROVIDER_DEADLINE_S, how long one provider call may run before it is abandoned as a
# timeout, lives with the HTTP layer, whose retries must end within it; it is read
# from here too.
from lyrics_http import PROVIDER_DEADLINE_S as PROVIDER_DEADLINE_S
from lyrics_inventory import NO_EMBEDDED_LYRICS, EmbeddedLyrics
from lyrics_model import (
    AttemptOutcome,
    FetchResult,
    Lyrics,
    LyricsKind,
    ProviderAttempt,
    Provider,
    ProviderKey,
    ProviderTable,
    SidecarState,
    Source,
    SyncedLyrics,
    TrackQuery,
)
from lyrics_placeholder import unless_placeholder
from lyrics_providers import ProviderTimeout, error_detail
from lyrics_titles import stripped_retry

_T = TypeVar("_T")

#: The LRC providers, in the order each pass asks them.
#: Musixmatch is not one: its desktop API answers every query with the same unrelated
#: hits, and the only working route forges its request signatures (`album.md`).
LRC_PROVIDERS: tuple[Source, ...] = (Source.LRCLIB, Source.NETEASE)
#: Every step of the chain, in order: each LRC provider synced, YouTube Music synced,
#: each LRC provider plain, Bandcamp, YouTube Music plain, Genius, then Tekstowo.
CHAIN: tuple[ProviderKey, ...] = (
    *(ProviderKey(source, LyricsKind.SYNCED) for source in LRC_PROVIDERS),
    ProviderKey(Source.YOUTUBE_MUSIC, LyricsKind.SYNCED),
    *(ProviderKey(source, LyricsKind.UNSYNCED) for source in LRC_PROVIDERS),
    ProviderKey(Source.BANDCAMP, LyricsKind.UNSYNCED),
    ProviderKey(Source.YOUTUBE_MUSIC, LyricsKind.UNSYNCED),
    ProviderKey(Source.GENIUS, LyricsKind.UNSYNCED),
    ProviderKey(Source.TEKSTOWO, LyricsKind.UNSYNCED),
)

# --- The watchdog ------------------------------------------------------------------------


def run_with_deadline(call: Callable[[], _T], deadline_s: float) -> _T:
    """*call*'s result, or its own error re-raised; `ProviderTimeout` at *deadline_s*.

    The call runs on a daemon thread. One still running at the deadline is abandoned:
    it keeps its thread until it returns, and the caller has already moved on.
    """
    # One-slot boxes the worker fills; a finished call fills exactly one of them.
    values: list[_T] = []
    errors: list[Exception] = []

    def _run() -> None:
        try:
            values.append(call())
        # Any failure is carried back and re-raised on the caller's thread below.
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        raise ProviderTimeout(f"no answer within {deadline_s}s")
    if errors:
        raise errors[0]
    return values[0]


@runtime_checkable
class OwnsDeadline(Protocol):
    """A provider that retries inside one call, and so sets its own whole-call bound.

    The watchdog uses that bound as given, with no input of the chain's, so it cannot
    disagree with the moment the provider itself gives up.
    """

    def step_deadline_s(self) -> float:
        """The bound for one whole call."""


@runtime_checkable
class AsksSelectively(Protocol):
    """A provider that sends no request for some queries, and says which beforehand.

    The chain skips it for those, recording no attempt: an attempt would count the
    provider as asked in the run's tally (`lyrics_tally`) for a track it never asked
    about, and a provider that had stopped working would hide behind those counts.
    """

    def asks(self, query: TrackQuery) -> bool:
        """False when a call for *query* would send no request."""


def skips(provider: Provider, query: TrackQuery) -> bool:
    """True when *provider* says it would send no request for *query*."""
    return isinstance(provider, AsksSelectively) and not provider.asks(query)


@runtime_checkable
class AnswersEveryKind(Protocol):
    """A provider whose one call settles every kind of step for a query.

    The chain asks it once per query within a track's fetch and keeps the answer, or
    the failure, for its other kind's step (`_ask`), so a plain pass sends nothing a
    synced pass already sent. The method's name is one no single-kind provider carries,
    since the protocol check reads only that the attribute exists.
    """

    def answer_all_kinds(self, query: TrackQuery) -> LrcAnswer:
        """The provider's text of each kind for *query*."""


#: What one track's fetch heard from each every-kind provider, by its source and the
#: query asked: the answer, or the failure, which no later step asks again.
Heard: TypeAlias = dict[tuple[Source, TrackQuery], LrcAnswer | Exception]


def _step_deadline_s(provider: Provider, deadline_s: float) -> float:
    """The watchdog's bound for one call to *provider*: its own bound, else *deadline_s*."""
    if isinstance(provider, OwnsDeadline):
        return provider.step_deadline_s()
    return deadline_s


# --- The chain ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pass:
    """One pass over some of the chain's steps: what it accepted, and every attempt."""

    lyrics: Lyrics | None
    source: Source | None
    attempts: tuple[ProviderAttempt, ...]


def _ask(
    key: ProviderKey,
    provider: Provider,
    query: TrackQuery,
    deadline_s: float,
    heard: Heard,
) -> str | None:
    """*provider*'s text for *key*'s step, each call under the watchdog.

    An every-kind provider is called only when *heard* holds nothing from it for
    *query*; what it answered, or how it failed, is kept there and read by its other
    kind's step, which re-raises the same failure.
    """
    bound = _step_deadline_s(provider, deadline_s)
    if not isinstance(provider, AnswersEveryKind):
        return run_with_deadline(lambda: provider(query), bound)
    slot = (key.source, query)
    if slot not in heard:
        try:
            heard[slot] = run_with_deadline(
                lambda: provider.answer_all_kinds(query), bound
            )
        # Kept, not swallowed: it is raised below, for this step and for every later
        # step of the same provider and query.
        except Exception as error:
            heard[slot] = error
    answer = heard[slot]
    if isinstance(answer, Exception):
        raise answer
    return answer.text(key.kind)


def attempt(
    key: ProviderKey,
    provider: Provider,
    query: TrackQuery,
    deadline_s: float,
    heard: Heard,
    *,
    duration_s: float | None = None,
    same_song: SameSong | None = None,
) -> tuple[Lyrics | None, ProviderAttempt]:
    """Ask one step's provider (`_ask`); its accepted lyrics, and the attempt.

    An answer is judged against the track's *duration_s* (`judge_answer`), and against
    the songs *same_song* holds declined in this track's fetch, none when None.
    """
    songs = SameSong() if same_song is None else same_song
    try:
        text = _ask(key, provider, query, deadline_s, heard)
    except ProviderTimeout as timeout:
        return None, ProviderAttempt(
            key, query.stripped, AttemptOutcome.TIMEOUT, str(timeout)
        )
    # Any provider failure is that step's error; the chain moves on either way. Its
    # detail is the error's short form, never its message (`error_detail`).
    except Exception as error:
        detail = error_detail(error)
        return None, ProviderAttempt(key, query.stripped, AttemptOutcome.ERROR, detail)
    lyrics, detail = songs.judge(normalize_answer(text, key), duration_s)
    outcome = AttemptOutcome.EMPTY if lyrics is None else AttemptOutcome.ANSWERED
    return lyrics, ProviderAttempt(key, query.stripped, outcome, detail)


def _run_pass(
    query: TrackQuery,
    providers: ProviderTable,
    keys: Sequence[ProviderKey],
    deadline_s: float,
    heard: Heard,
    duration_s: float | None,
    songs: SameSong,
) -> _Pass:
    """Try each of *keys* the table holds, in order, until one is accepted.

    A provider that says it would send no request for *query* is skipped unrecorded.
    *heard* is the track's record of every-kind providers' answers (`_ask`);
    *duration_s* the track's, each answer judged against it and against the songs
    *songs*, the track's record, holds declined.
    """
    attempts: list[ProviderAttempt] = []
    for key in keys:
        provider = providers.get(key)
        if provider is None or skips(provider, query):
            continue
        lyrics, tried = attempt(
            key,
            provider,
            query,
            deadline_s,
            heard,
            duration_s=duration_s,
            same_song=songs,
        )
        attempts.append(tried)
        if lyrics is not None:
            return _Pass(lyrics, key.source, tuple(attempts))
    return _Pass(None, None, tuple(attempts))


def _held_untimed(
    sidecar: Lyrics | None, embedded: EmbeddedLyrics
) -> tuple[Lyrics, Source] | None:
    """The untimed lyrics the track holds and where from: a ``.txt`` before its frames.

    Called once a timed ``.lrc`` and embedded synced lyrics are ruled out, so the
    *sidecar* lyrics, when there are any, are untimed.
    """
    if sidecar is not None:
        return sidecar, Source.SIDECAR
    if embedded.untimed is not None:
        return embedded.untimed, Source.FRAMES
    return None


def _fetch_over_untimed(
    query: TrackQuery,
    held: tuple[Lyrics, Source],
    providers: ProviderTable,
    deadline_s: float,
    duration_s: float | None,
) -> FetchResult:
    """Lyrics the track holds that a timed answer replaces: the synced steps still run.

    With no timed answer, the *held* lyrics are used, from their own source, and the
    plain steps are skipped. There is no stripped retry here: the track already has
    its words.
    """
    synced = [key for key in CHAIN if key.kind is LyricsKind.SYNCED]
    found = _run_pass(query, providers, synced, deadline_s, {}, duration_s, SameSong())
    if found.lyrics is not None and found.source is not None:
        return FetchResult(found.lyrics, found.source, query, found.attempts)
    lyrics, source = held
    return FetchResult(lyrics, source, query, found.attempts)


def _fetch_over_label(
    query: TrackQuery,
    held: SyncedLyrics,
    label: Source,
    embedded: EmbeddedLyrics,
    providers: ProviderTable,
    deadline_s: float,
    duration_s: float | None,
) -> FetchResult:
    """A timed sidecar its header labels: taken as an untimed one is.

    Synced frames holding other lines serve, as beside a ``.txt``. Frames holding its
    lines, at its times or at others (`synced_besides`), are an earlier embed of it, so
    they never credit the track to the frames. Otherwise the synced steps run, and with
    no timed answer the file serves under its *label*.
    """
    synced = embedded.synced
    if synced is not None and embedded.synced_besides(held):
        return FetchResult(synced, Source.FRAMES, query, ())
    return _fetch_over_untimed(query, (held, label), providers, deadline_s, duration_s)


def fetch_lyrics(
    query: TrackQuery,
    sidecar: SidecarState,
    providers: ProviderTable,
    *,
    deadline_s: float,
    embedded: EmbeddedLyrics = NO_EMBEDDED_LYRICS,
    duration_s: float | None = None,
) -> FetchResult:
    """Run the chain for one track, what it already holds in its place.

    The order: a timed sidecar; else the *embedded* synced lyrics; else the synced
    steps; else a ``.txt`` sidecar; else the embedded untimed lyrics; else the plain
    steps, with the stripped retry. Each provider call is bounded by
    *deadline_s*, or by the bound a provider that retries inside one call sets for
    itself (`OwnsDeadline`). A step whose key is absent from *providers* is skipped.

    An unlabeled sidecar holding the lyrics of any frame of its kind counts as that
    frame (`EmbeddedLyrics.serving`, `EmbeddedLyrics.favoring`), so the track is served
    from it, source frames, and keeps its frames as they are. A timed sidecar its header
    labels (`SidecarState.label`) is taken as an untimed one is: the synced steps still
    run.

    Each provider's answer is judged against the track's *duration_s*, None when it
    cannot be read (`judge_answer`), and a plain one against the songs of timed
    answers declined far past the end (`SameSong`); what the track holds is not.
    """
    # A placeholder held, in a sidecar or a frame, is no lyrics held: it never serves.
    held = unless_placeholder(sidecar.lyrics)
    embedded = embedded.without_placeholders()
    if sidecar.label is not None and isinstance(held, SyncedLyrics):
        return _fetch_over_label(
            query, held, sidecar.label, embedded, providers, deadline_s, duration_s
        )
    embedded = embedded.favoring(held)
    held_sidecar = embedded.serving(held)
    if isinstance(held_sidecar, SyncedLyrics):
        return FetchResult(held_sidecar, Source.SIDECAR, query, ())
    if embedded.synced is not None:
        return FetchResult(embedded.synced, Source.FRAMES, query, ())
    untimed = _held_untimed(held_sidecar, embedded)
    if untimed is not None:
        return _fetch_over_untimed(query, untimed, providers, deadline_s, duration_s)
    heard: Heard = {}
    # One record for both passes, so the retry's plain answers face the songs the
    # first pass declined.
    songs = SameSong()
    first = _run_pass(query, providers, CHAIN, deadline_s, heard, duration_s, songs)
    outcomes = (tried.outcome for tried in first.attempts)
    retry = stripped_retry(query, outcomes, found=first.lyrics is not None)
    if retry is None:
        return FetchResult(first.lyrics, first.source, query, first.attempts)
    second = _run_pass(retry, providers, CHAIN, deadline_s, heard, duration_s, songs)
    attempts = first.attempts + second.attempts
    # The stripped marker explains a found lyric; with nothing found the track's own
    # query is what was looked for, and each attempt still records which pass it was.
    reported = query if second.lyrics is None else retry
    return FetchResult(second.lyrics, second.source, reported, attempts)
