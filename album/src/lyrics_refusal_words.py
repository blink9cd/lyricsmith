"""The words a provider records for a refusal: a leaf, importing none of the tool.

Each provider raises its challenge with these, and the old-manifest detail reader
(`lyrics_refusal_detail`) reads a detail's class back by them, so the two cannot drift
apart. The words naming which Genius request a refusal came from, by its host, are here
too: the Genius session's allowed hosts are their keys (`lyrics_genius.GENIUS_HOSTS`),
and the summary names a refusal's request by them (`lyrics_refusal_tally`). They live
here, not in the providers, so reading a manifest (undo, check, prune, the sweep) loads
no provider client and not Genius's library. Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: The HTTP statuses Genius's front end answers a challenge with.
GENIUS_CHALLENGE_STATUSES = frozenset({403, 429})
#: The stop words a Tekstowo challenge page records.
TEKSTOWO_CHALLENGE = "challenge page"
#: The stop words each YouTube Music challenge redirect records.
TO_CONSENT, TO_SORRY = "redirect to consent", "redirect to sorry"
#: Each Genius host, and the request a refusal from it was, in the order the summary
#: lists them. The Genius step's song record request goes to ``api.genius.com`` with
#: the key, and so does the Genius check's keyed search (`lyrics_genius_search`); the
#: step's own search and the lyrics page go to ``genius.com``, with none. So the host
#: tells a refused key from a refused site, though not a search from a record or a page.
REQUEST_LABELS: Mapping[str, str] = MappingProxyType(
    {"api.genius.com": "keyed API", "genius.com": "unkeyed site"}
)


def request_label(host: str | None) -> str | None:
    """The request a refusal from *host* was, or None for no host or an unlabeled one."""
    return None if host is None else REQUEST_LABELS.get(host)
