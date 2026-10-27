"""An old manifest's refusal detail read back to the class `classify` gives it live.

A manifest written before a refusal's facts were kept holds only its provider, step and
detail, so its class is read back from the provider and the detail together
(`reason_from_detail`). The provider matters because one detail is a different refusal
from different providers, each as its own error classes it (`lyrics_refusal.classify`):

- Tekstowo: its challenge page and a third redirect in a row stop it, so are blocked
  (`lyrics_tekstowo.stop_refusal`).
- YouTube Music: its two challenge redirects are blocked, and so is any ``HTTP 3xx``
  or client error but ``429``, as its client stops on every status but 200 and classes
  it by `stopping_status_refusal`.
- Genius: ``HTTP 403`` or ``HTTP 429`` after retries is its challenge outlasting them,
  blocked (`lyrics_genius.GeniusChallengeError`); with no retries it is the status's own.
- Every provider: ``HTTP n`` is its status's class (`status_refusal`): ``429`` rate
  limited, any ``5xx`` overloaded, ``401``, ``403``, ``407`` or ``451`` blocked, any
  other ``4xx`` unrecognized, but a redirect is a refused one, so unrecognized too;
  ``timeout`` and a connection error's type are unreachable; a retried failure is read
  by its own part, before ``after``; anything else is unrecognized. A manifest written
  before 4xx and 5xx were told apart reads by these classes, as intended.

The words each provider records come from the leaf module its provider raises them
with (`lyrics_refusal_words`), never retyped, so a reworded stop cannot leave this
reader behind silently, and reading a manifest loads no provider client. It is the only
place a detail's text is read for its class. Nothing here prints.
"""

from __future__ import annotations

import re

from lyrics_http import TOO_MANY_REDIRECTS
from lyrics_model import Source
from lyrics_providers import REDIRECT_STATUSES, TIMEOUT_DETAIL
from lyrics_refusal import UNREACHABLE_ERRORS, Refusal, status_refusal
from lyrics_refusal import stopping_status_refusal
from lyrics_refusal_words import GENIUS_CHALLENGE_STATUSES, TO_CONSENT, TO_SORRY
from lyrics_refusal_words import TEKSTOWO_CHALLENGE

#: The details of a server never reached: a timeout, and each unreachable error's type.
_UNREACHABLE_NAMES = frozenset(
    {TIMEOUT_DETAIL, *(error.__name__ for error in UNREACHABLE_ERRORS)}
)
#: The details a provider records for a refusal it classes as blocked, beyond a status.
_BLOCKED_DETAILS: dict[Source, frozenset[str]] = {
    Source.TEKSTOWO: frozenset({TEKSTOWO_CHALLENGE, TOO_MANY_REDIRECTS}),
    Source.YOUTUBE_MUSIC: frozenset({TO_CONSENT, TO_SORRY}),
}
#: What a retried failure's detail adds after its own: ``HTTP 503 after 3 retries``.
_RETRIES_TAIL = " after "
#: A status detail, ``HTTP`` and three ASCII digits: never handed a run of thousands.
_STATUS_DETAIL = re.compile(r"HTTP ([0-9]{3})", re.ASCII)


def _status_reason(source: Source, status: int, retried: bool) -> Refusal:
    """The class *source* gives a refusal of *status*, *retried* or not."""
    if source is Source.GENIUS and retried and status in GENIUS_CHALLENGE_STATUSES:
        return Refusal.BLOCKED
    if source is Source.YOUTUBE_MUSIC:
        return stopping_status_refusal(status)
    if status in REDIRECT_STATUSES:
        return Refusal.UNRECOGNIZED
    return status_refusal(status)


# fuzz-target
def reason_from_detail(source: Source, detail: str) -> Refusal:
    """The class of the refusal *source* recorded as *detail*, as `classify` gave it."""
    head = detail.split(_RETRIES_TAIL, 1)[0]
    if head in _UNREACHABLE_NAMES:
        return Refusal.UNREACHABLE
    if detail in _BLOCKED_DETAILS.get(source, frozenset()):
        return Refusal.BLOCKED
    match = _STATUS_DETAIL.fullmatch(head)
    if match is None:
        return Refusal.UNRECOGNIZED
    return _status_reason(source, int(match.group(1)), retried=head != detail)
