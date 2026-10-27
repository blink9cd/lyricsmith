"""The real provider table: every LRC step, and each optional step its settings turn on.

The table holds its keys in `CHAIN` order (`lyrics_fetch`). The LRC adapters ask LRCLIB
(`lyrics_lrclib`) and NetEase (`lyrics_netease`) directly and raise on an error status
(LRCLIB's exact lookup falls to its search on a 404 or a 5xx instead), each retrying a
transient refusal within the call's deadline (`lyrics_http.Retry`); the Genius step
(`lyrics_genius`), there when a key resolved, is paced to a minimum interval with an
escalating retry when a 403 / 429 challenge answers. The Bandcamp step
(`lyrics_bandcamp`), there when `ProviderSettings` turns it on, finds an album's page
through MusicBrainz, whose client is built here with the configured contact and the same
retry. The YouTube Music step (`lyrics_youtube_provider`), there when its settings name a
cool-down file, is one provider under both of its keys, on one paced client. The Tekstowo
step (`lyrics_tekstowo`), there when its settings name a cool-down file, is the chain's
last, on its own paced client. The defaults leave all three out, so a caller that names no
settings gets none of them; the run's wiring turns them on. NetEase's two steps are the
other way round: there by default, and left out only when the settings say so.

Each provider the settings give a breaker setup for (`ProviderSettings.breakers`), of
LRCLIB, NetEase, Genius and Bandcamp, gets one `lyrics_breakers.BreakerGuard`, shared by
its steps; MusicBrainz's requests are Bandcamp's step's, so its guard covers them. The
retrying clients, and the Genius step's challenge retries, tell the settings'
`lyrics_retried.RetryTally`, when given, of each retry they make.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from lyrics_bandcamp import BandcampProvider
from lyrics_breakers import BreakerGuard, BreakerSetup
from lyrics_cooldown import CoolingSetup
from lyrics_fetch import CHAIN, LRC_PROVIDERS
from lyrics_genius import GeniusClient, GeniusProvider, build_genius_client
from lyrics_http import PROVIDER_DEADLINE_S, RetryPolicy
from lyrics_lrclib import LrclibProvider
from lyrics_model import Provider, ProviderKey, ProviderTable, Source
from lyrics_musicbrainz import MusicBrainzClient
from lyrics_netease import NeteaseProvider
from lyrics_records import Secret
from lyrics_retried import RetryTally
from lyrics_tekstowo import TekstowoClient, TekstowoProvider
from lyrics_youtube_music import YouTubeMusicClient
from lyrics_youtube_provider import YouTubeMusicProvider

#: The sources the table builds an LRC step for.
LRC_STEP_SOURCES: tuple[Source, ...] = LRC_PROVIDERS


@dataclass(frozen=True)
class ProviderSettings:
    """Which optional providers a run's table holds, and what they are built with."""

    bandcamp: bool = False
    #: Whether NetEase's two steps are there: they are by default, not under --no-netease.
    netease: bool = True
    musicbrainz_contact: str | None = None
    youtube_music: CoolingSetup | None = None
    tekstowo: CoolingSetup | None = None
    #: Each guarded provider's breaker as the run starts; one left out is unguarded.
    breakers: Mapping[Source, BreakerSetup] = field(default_factory=dict)
    #: Where the retrying clients count their retries; None counts none.
    retried: RetryTally | None = None


def _lrc_step(
    key: ProviderKey,
    retry: RetryPolicy,
    deadline_s: float,
    guards: Mapping[Source, BreakerGuard],
) -> Provider:
    """The adapter for one LRC step: LRCLIB's of the step's kind, else NetEase's.

    Each retries a transient refusal by *retry*, within *deadline_s* of a call's start,
    and carries its source's guard in *guards*, if any.
    """
    guard = guards.get(key.source)
    if key.source is Source.LRCLIB:
        return LrclibProvider(
            key.kind, retry=retry, deadline_s=deadline_s, breaker=guard
        )
    return NeteaseProvider(retry=retry, deadline_s=deadline_s, breaker=guard)


def build_provider_table(
    genius_key: Secret | None,
    *,
    genius_factory: Callable[[Secret], GeniusClient] = build_genius_client,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    deadline_s: float = PROVIDER_DEADLINE_S,
    settings: ProviderSettings = ProviderSettings(),
) -> ProviderTable:
    """The real table: every LRC step, Genius with a key, and each optional step when on.

    *deadline_s* is the per-request deadline the Genius step builds its own whole-call
    bound from, and the bound within which an LRC step's retries end, waiting on
    *clock* and *sleeper* as the Genius step, the MusicBrainz client, the YouTube
    Music client and the Tekstowo client do. Building it sends nothing and reads or
    writes no file.
    """
    tally = settings.retried
    retry = RetryPolicy(clock, sleeper, on_retry=None if tally is None else tally.count)
    guards = {
        source: BreakerGuard(setup) for source, setup in settings.breakers.items()
    }
    optional: dict[Source, Provider] = {}
    if settings.bandcamp:
        client = MusicBrainzClient(
            settings.musicbrainz_contact, clock=clock, sleeper=sleeper, retry=retry
        )
        optional[Source.BANDCAMP] = BandcampProvider(
            client, clock=clock, breaker=guards.get(Source.BANDCAMP)
        )
    if settings.youtube_music is not None:
        setup = settings.youtube_music
        optional[Source.YOUTUBE_MUSIC] = YouTubeMusicProvider(
            YouTubeMusicClient(
                setup.state_path, clock=clock, sleeper=sleeper, now=setup.now
            )
        )
    if settings.tekstowo is not None:
        tekstowo = settings.tekstowo
        optional[Source.TEKSTOWO] = TekstowoProvider(
            TekstowoClient(
                tekstowo.state_path, clock=clock, sleeper=sleeper, now=tekstowo.now
            )
        )
    if genius_key is not None:
        genius = genius_factory(genius_key)
        optional[Source.GENIUS] = GeniusProvider(
            genius,
            clock,
            sleeper,
            deadline_s=deadline_s,
            breaker=guards.get(Source.GENIUS),
            on_retry=retry.on_retry,
        )
    lrc = tuple(
        source
        for source in LRC_STEP_SOURCES
        if settings.netease or source is not Source.NETEASE
    )
    return {
        key: optional.get(key.source) or _lrc_step(key, retry, deadline_s, guards)
        for key in CHAIN
        if key.source in lrc or key.source in optional
    }
