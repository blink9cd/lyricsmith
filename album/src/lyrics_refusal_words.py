"""The words and statuses a provider records for a challenge: a leaf, importing nothing.

Each provider raises its challenge with these, and the old-manifest detail reader
(`lyrics_refusal_detail`) reads a detail's class back by them, so the two cannot drift
apart. They live here, not in the providers, so reading a manifest (undo, check, prune,
the sweep) loads no provider client and not Genius's library. Nothing here prints.
"""

from __future__ import annotations

#: The HTTP statuses Genius's front end answers a challenge with.
GENIUS_CHALLENGE_STATUSES = frozenset({403, 429})
#: The stop words a Tekstowo challenge page records.
TEKSTOWO_CHALLENGE = "challenge page"
#: The stop words each YouTube Music challenge redirect records.
TO_CONSENT, TO_SORRY = "redirect to consent", "redirect to sorry"
