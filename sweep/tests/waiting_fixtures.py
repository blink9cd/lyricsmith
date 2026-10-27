"""Shared fixtures for the waiting suites: a provider's hold, written as the album tool does.

`cool` writes a provider's cool-down and `open_breaker` its breaker, each through the
album tool's own writer, into the state folder the environment names, which every
`sweep_fixtures.IsolatedCase` points at its own temporary directory. Each moment is
measured from the sweep's invented clock (`sweep_fixtures.fixed_now`).

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with
this directory on ``sys.path``, after the entry point's bootstrap has put the album
tool's modules there.
"""

from __future__ import annotations

import os
from datetime import timedelta

import lyrics_breaker
import lyrics_cooldown
import lyrics_model as lm
import sweep_fixtures as sf
from lyrics_refusal import Refusal

#: How far from the sweep's clock a breaker's window ends, by default.
MARGIN = timedelta(minutes=30)


def cool(source: lm.Source, *, ago: timedelta = timedelta(hours=1)) -> None:
    """Write *source*'s cool-down, its refusal *ago* before the sweep's clock."""
    path = lyrics_cooldown.cooldown_path(os.environ, source)
    lyrics_cooldown.write_cooldown(path, sf.fixed_now() - ago, "HTTP 429")


def open_breaker(source: lm.Source, *, ends_in: timedelta = MARGIN) -> None:
    """Write *source*'s breaker, opened by a block, its window ending *ends_in* hence."""
    state = lyrics_breaker.BreakerState(
        0, Refusal.BLOCKED, lyrics_breaker.MAX_WINDOW, sf.fixed_now() + ends_in
    )
    lyrics_breaker.write_breaker(lyrics_breaker.breaker_path(os.environ, source), state)
