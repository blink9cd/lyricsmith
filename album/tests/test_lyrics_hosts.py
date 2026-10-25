"""The one https-host check every followed URL passes: https, no user info, port 443 or none.

Each row is a hostile or odd URL shape, asked under Tekstowo's exact-host rule and under
Bandcamp's subdomain rule, so the two providers' allowlists can no longer drift apart.
Every address is built from its parts, so the text holds no address-shaped literal.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_hosts as lhost  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_musicbrainz as lmb  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tekstowo as lt  # noqa: E402 - import follows the sys.path bootstrap above

# The profile test_lyrics_genre registers, under the same name and settings, so the
# properties run alike whichever suite loads first (its comment gives the reasons).
settings.register_profile(
    "album_properties",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("album_properties")

T = lt.TEKSTOWO_HOST
B = "bandcamp.com"
#: A host off both sites, under the reserved ``.example`` top-level domain.
EVIL = "evil.example"
LOGIN = "visitor:invented"

#: Tekstowo's rows: each URL, and whether its exact-host rule accepts it.
TEKSTOWO_ROWS: tuple[tuple[str, bool], ...] = (
    (f"https://{T}/szukaj/x", True),
    (f"https://{T}:443/", True),
    (f"https://{T.upper()}/", True),
    (f"https://{T}:/", True),
    # Not a chosen rule: urlsplit strips tab, CR and LF anywhere in a URL (bpo-43882),
    # so this reads as the bare host. A redirect hop is sanitized the same way, since
    # each Location is joined through urljoin, which splits it so.
    (f"https://{T}\t/", True),
    (f"https://{T}@{EVIL}/", False),
    (f"https://{EVIL}#@{T}", False),
    (f"https://{EVIL}?@{T}", False),
    (f"https://{T}./", False),
    (f"http://{T}/", False),
    (f"https://{T}%2F@{EVIL}", False),
    ("https://[::1]/", False),
    (f"https://{T}\\@{EVIL}/", False),
    (f"https://{LOGIN}@{T}/", False),
    (f"https://@{T}/", False),
    (f"https://{T}:65536/", False),
    (f"https://{T}:8443/", False),
    (f"https://{T}:x/", False),
    (f"//{T}/", False),
    (f"https:///{T}/", False),
    (f"https://{T}.{EVIL}/", False),
    (f"https://xn--{T}/", False),
    ("https://www。tekstowo.pl/", False),
    ("https://ｗｗｗ.tekstowo.pl/", False),
    ("javascript:alert(1)", False),
    (f"https://{T}\n@{EVIL}/", False),
    (f"https://evil.{T}/", False),
)
#: Bandcamp's rows: each URL, and whether its subdomain rule accepts it.
BANDCAMP_ROWS: tuple[tuple[str, bool], ...] = (
    (f"https://{B}/", True),
    (f"https://{B.upper()}/", True),
    (f"https://a.{B}:443/x", True),
    (f"https://a.{B}:/", True),
    (f"https://a.b-c.{B}/", True),
    (f"https://{B}#@{EVIL}/", True),
    (f"https://{B}?@{EVIL}/", True),
    (f"https://{B}.{EVIL}/", False),
    (f"https://evil{B}/", False),
    (f"https://{B}@{EVIL}/", False),
    (f"https://{B}:444/", False),
    (f"https://{B}./", False),
    (f"http://{B}/", False),
    (f"https://{B}%00.{EVIL}/", False),
    (f"https://{B}\\.{EVIL}/", False),
    (f"https://{B}:443@{EVIL}/", False),
    (f"https://{LOGIN}@a.{B}/", False),
    ("https://[::1]/", False),
    ("https://[::1/", False),
    (f"https://a_b.{B}/", False),
    ("https://ｂandcamp.com/", False),
    (f"https://{B}:65536/", False),
    (f"https://{B}\n@{EVIL}/", False),
    (f"ftp://a.{B}/", False),
)


def _is_tekstowo(host: str) -> bool:
    """Tekstowo's exact-host rule."""
    return host == T


def _recording(seen: list[str]) -> Callable[[str], bool]:
    """A host predicate that appends each host it is asked about to *seen*, and refuses."""

    def is_host(host: str) -> bool:
        seen.append(host)
        return False

    return is_host


class HttpsOnTest(fixtures.IsolatedCase):
    """The shared check, under each provider's host rule, and each caller using it."""

    def _check(
        self,
        rows: tuple[tuple[str, bool], ...],
        is_host: Callable[[str], bool],
        caller: Callable[[str], bool],
    ) -> None:
        """Each of *rows* gets its expected answer from the helper and from *caller*."""
        for url, expected in rows:
            with self.subTest(url=url):
                self.assertIs(lhost.is_https_on(url, is_host), expected)
                self.assertIs(caller(url), expected)

    def test_tekstowo_rows(self) -> None:
        self._check(TEKSTOWO_ROWS, _is_tekstowo, lt.on_site)

    def test_bandcamp_rows(self) -> None:
        self._check(BANDCAMP_ROWS, lmb.is_bandcamp_host, lmb.is_bandcamp_url)

    def test_the_predicate_reads_the_lowercased_host(self) -> None:
        seen: list[str] = []
        lhost.is_https_on(f"https://{T.upper()}:443/x", _recording(seen))
        self.assertEqual(seen, [T])

    def test_the_predicate_is_not_asked_of_a_refused_shape(self) -> None:
        seen: list[str] = []
        lhost.is_https_on(f"http://{T}/", _recording(seen))
        self.assertEqual(seen, [])

    @given(url=st.text(max_size=40))
    def test_fuzz_is_https_on(self, url: str) -> None:
        self.assertIs(lhost.is_https_on(url, _is_tekstowo), lt.on_site(url))
