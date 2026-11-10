"""What the Genius check reaches outside itself: the environment, clocks, tags, client.

`real_seams` binds the real ones. The entry point's `sweep_wiring.Wiring` carries a
test's own `CheckSeams` as its *genius*, the way it carries the paste walk's, so no test
patches the check's module. Nothing here prints.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_cooldown import utc_now
from lyrics_genius_search import GeniusSearchClient, build_search_client
from lyrics_model import TrackTags
from lyrics_records import Secret
from lyrics_tags import read_tags


@dataclass(frozen=True)
class CheckSeams:
    """What the check reaches: the environment, the clocks, the client, the tag reader."""

    environ: Mapping[str, str]
    clock: Callable[[], float]
    sleeper: Callable[[float], None]
    #: The breaker's clock: aware UTC.
    now: Callable[[], datetime]
    build_client: Callable[[Secret], GeniusSearchClient]
    #: How a listed track's tags are read; anything it raises is that track's.
    read_tags: Callable[[Path], TrackTags]


def real_seams() -> CheckSeams:
    """The seams a real check runs on."""
    return CheckSeams(
        os.environ, time.monotonic, time.sleep, utc_now, build_search_client, read_tags
    )
