"""The one check every provider's followed URL passes: https on an allowed host.

Tekstowo and Bandcamp each follow a URL, a redirect's ``Location`` included, only when
this accepts it, so the two allowlists share one rule for everything but the host and
cannot drift apart: the ``https`` scheme, no user info (refused both by the parsed
fields and by any ``@`` left in the network location), and no port but 443, named or
not. The host itself is the caller's predicate, given the host as `urlsplit` reads it,
lowercased. A URL `urlsplit` cannot read is refused. Nothing here sends a request.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

#: The only port an accepted URL may name.
HTTPS_PORT = 443


# fuzz-target
def is_https_on(url: str, is_host: Callable[[str], bool]) -> bool:
    """True for an ``https`` *url* with no user info, port 443 or none, *is_host*'s host.

    *is_host* is asked only of a URL that passes every other rule, with its lowercased
    host.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # an unbalanced IPv6 bracket, or a port that is not a number
        return False
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or "@" in parts.netloc
        or port not in (None, HTTPS_PORT)
        or parts.hostname is None
    ):
        return False
    return is_host(parts.hostname)
