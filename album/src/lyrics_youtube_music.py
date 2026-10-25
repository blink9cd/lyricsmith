"""YouTube Music's client: one paced, cookie-free POST at a time, stopped at the first refusal.

Asking YouTube Music's own web API is a deliberate exception to YouTube's terms, made
for the lyrics of albums already held, and it stands only on these hard rules:

- **No cookie.** Each request goes through a fresh `lyrics_http.cookie_free_session`,
  whose jar refuses every cookie a reply sets, so nothing is stored to be sent back; no
  ``Cookie`` header is ever written, and the home page, which hands one out, is never
  asked.
- **No visitor id.** No ``X-Goog-Visitor-Id`` header and no ``visitorData`` in a body.
- **No redirect followed**, and **no retry**: a refusal is an answer, not a hiccup.
- **The tool's own User-Agent** (`lyrics_http.user_agent`), and no contact.
- **A 5-second pace**: at least `YOUTUBE_MUSIC_MIN_INTERVAL_S` between the starts of
  any two requests, under one lock, so no two overlap and a call the watchdog abandoned
  ends its request before the next begins. The lock, the pace and the stop are the
  shared cooling client's (`lyrics_cooling_client.CoolingClient`).

The stop rule: any status but 200, a ``Location`` naming a ``consent.`` host or a
``/sorry`` path, or a 200 whose body is not JSON, is a refusal (`CoolingClient.refuse`),
which turns YouTube Music off for the rest of the run and writes the 24-hour cool-down
(`lyrics_cooldown`); every later call raises `YouTubeMusicStopped` unsent. Not a stop,
only that track's error: a reply over `lyrics_http.MAX_REPLY_BYTES`, a connection error
and a request timeout. A 200 of an unexpected shape is the reply parsers' error, not
this module's.

The request shapes follow the 2026-10-21 probe, with ``ytmusicapi`` 1.12.3's source as
the reference, read and never imported or copied; each constant cites where. Nothing here
prints.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import ClassVar
from urllib.parse import urlsplit

import requests

from lyrics_cooling_client import CoolingClient, Refusal, status_refusal
from lyrics_http import REPLY_NOT_JSON, REQUEST_TIMEOUT_S, capped_body
from lyrics_model import Source
from lyrics_providers import ProviderError

#: The API's base. ``?alt=json`` and no API key: ytmusicapi 1.12.3 ``constants.py:3``
#: and ``ytmusic.py:155-157``.
YOUTUBE_MUSIC_URL = "https://music.youtube.com/youtubei/v1"
#: The query every request carries, as above.
_PARAMS: Mapping[str, str] = {"alt": "json"}
#: The JSON content type and the ``Origin`` every request sends: ytmusicapi 1.12.3
#: ``helpers.py:26,28``.
JSON_CONTENT_TYPE = "application/json"
ORIGIN = "https://music.youtube.com"
#: The least time between any two YouTube Music requests.
YOUTUBE_MUSIC_MIN_INTERVAL_S = 5.0
#: The endpoints asked: a song search, a track's watch panel, and its lyrics tab.
SEARCH, NEXT, BROWSE = "search", "next", "browse"
#: The web client, whose version is dated (ytmusicapi 1.12.3 ``helpers.py:38``), and the
#: Android client the lyrics browse is asked as, at ytmusicapi 1.12.3 ``ytmusic.py:219``.
WEB_REMIX, ANDROID_MUSIC, ANDROID_MUSIC_VERSION = (
    "WEB_REMIX",
    "ANDROID_MUSIC",
    "7.21.50",
)
#: The probe's songs filter, sent exactly so, %3D and all. ytmusicapi's value
#: decodes to the same filter and differs only in its trailing field-13 list; this one is
#: what the bare client got correct song results with on 2026-10-21.
SONGS_FILTER = "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D"
#: The language the replies are asked in.
_LANGUAGE = "en"
#: The watch panel's playlist prefix, and the track kind the panel is asked for.
_RADIO_PREFIX = "RDAMVM"
_AUDIO_TRACK = "MUSIC_VIDEO_TYPE_ATV"
#: The two redirect forms that are challenges, by the start of the host and of the path.
_CONSENT_HOST, _SORRY_PATH = "consent.", "/sorry"
#: The stop words each challenge redirect records.
TO_CONSENT, TO_SORRY = "redirect to consent", "redirect to sorry"
_CHALLENGES = frozenset({TO_CONSENT, TO_SORRY})
_OK = 200


class YouTubeMusicStopped(ProviderError):
    """YouTube Music refused or challenged a request, or had already; its detail is *what*."""

    def __init__(self, what: str) -> None:
        """Carry *what* as both the message's tail and the recorded detail."""
        super().__init__(f"YouTube Music stopped: {what}", detail=what)


def web_remix_version(today: date) -> str:
    """The web client's dated version for *today*: ``1.<yyyymmdd>.01.00``."""
    return f"1.{today:%Y%m%d}.01.00"


def _context(client_name: str, client_version: str) -> dict[str, object]:
    """A request's context: the client and the language, an empty user, no visitor."""
    client = {"clientName": client_name, "clientVersion": client_version}
    return {"client": {**client, "hl": _LANGUAGE}, "user": {}}


def search_body(term: str, today: date) -> dict[str, object]:
    """The probe's song search for *term*, asked as the web client dated *today*."""
    context = _context(WEB_REMIX, web_remix_version(today))
    return {"context": context, "query": term, "params": SONGS_FILTER}


def next_body(video_id: str, today: date) -> dict[str, object]:
    """The probe's watch panel for *video_id*, which names its lyrics tab.

    Unlike ytmusicapi's, on purpose: no ``enablePersistentPlaylistPanel`` and no
    ``tunerSettingValue``, as the probe sent it.
    """
    config = {"hasPersistentPlaylistPanel": True, "musicVideoType": _AUDIO_TRACK}
    return {
        "context": _context(WEB_REMIX, web_remix_version(today)),
        "videoId": video_id,
        "playlistId": _RADIO_PREFIX + video_id,
        "isAudioOnly": True,
        "watchEndpointMusicSupportedConfigs": {"watchEndpointMusicConfig": config},
    }


def browse_body(browse_id: str) -> dict[str, object]:
    """The probe's browse of a lyrics tab, *browse_id*, asked as the Android client."""
    context = _context(ANDROID_MUSIC, ANDROID_MUSIC_VERSION)
    return {"context": context, "browseId": browse_id}


# fuzz-target
def stop_reason(status: int, location: str | None) -> str | None:
    """Why a reply of *status*, redirecting to *location*, stops YouTube Music; else None.

    A challenge redirect is named first; then any status but 200 is ``HTTP <status>``.
    A ``Location`` that is not a URL at all names no challenge.
    """
    try:
        parts = urlsplit(location or "")
    # urlsplit refuses a malformed bracketed host; such a Location names neither form.
    except ValueError:
        parts = urlsplit("")
    if (parts.hostname or "").startswith(_CONSENT_HOST):
        return TO_CONSENT
    if parts.path.startswith(_SORRY_PATH):
        return TO_SORRY
    return None if status == _OK else f"HTTP {status}"


def _refusal(status: int, reason: str) -> Refusal:
    """The class of the stop `stop_reason` gave as *reason* for a reply of *status*.

    A challenge redirect is a block; any other reason is the status's own refusal.
    """
    return Refusal.BLOCKED if reason in _CHALLENGES else status_refusal(status)


def _send(
    session: requests.Session, endpoint: str, body: Mapping[str, object]
) -> requests.Response:
    """POST *body* to *endpoint*; the response, its body not yet read.

    No status hook, since the stop rule reads the status and ``Location`` itself, and
    no retry. A redirect is never followed.
    """
    return session.post(
        f"{YOUTUBE_MUSIC_URL}/{endpoint}",
        params=dict(_PARAMS),
        data=json.dumps(dict(body), allow_nan=False).encode(),
        headers={"Content-Type": JSON_CONTENT_TYPE, "Origin": ORIGIN},
        timeout=REQUEST_TIMEOUT_S,
        allow_redirects=False,
        stream=True,
    )


@dataclass
class YouTubeMusicClient(CoolingClient):
    """YouTube Music's one client for a run: paced, cookie-free, and stoppable once.

    *state_path* is the cool-down file a stop writes. The clock, sleeper, now and
    session factory are injected so tests run with no time passing and no network.
    """

    NAME: ClassVar[str] = "YouTube Music"
    MIN_INTERVAL_S: ClassVar[float] = YOUTUBE_MUSIC_MIN_INTERVAL_S
    stopped_error: ClassVar[type[ProviderError]] = YouTubeMusicStopped

    def post(
        self, endpoint: str, body: Mapping[str, object], give_up_at: float
    ) -> object:
        """One paced POST to *endpoint*; its decoded JSON, or a stop for the rest of the run.

        In its own turn (`CoolingClient.turn`), so no two requests overlap and an
        abandoned call's request ends before the next begins. Past *give_up_at* it
        raises `ProviderTimeout` unsent. Once stopped, every call raises
        `YouTubeMusicStopped` unsent.
        """
        with self.turn(give_up_at):
            with (
                self.session_factory() as session,
                _send(session, endpoint, body) as response,
            ):
                status = response.status_code
                reason = stop_reason(status, response.headers.get("Location"))
                if reason is None:
                    raw = capped_body(response, Source.YOUTUBE_MUSIC)
                    try:
                        return json.loads(raw)
                    # Bad text: ValueError; nesting past the stack: RecursionError.
                    except (ValueError, RecursionError):
                        reason, kind = REPLY_NOT_JSON, Refusal.UNRECOGNIZED
                else:
                    kind = _refusal(status, reason)
            # Raised once the reply is closed, still in this turn, under the lock.
            raise self.refuse(kind, reason)
