"""fetch-models: streamed, hashed, size-bounded, renamed only on a matching digest."""

from __future__ import annotations

import hashlib
import http.client
import io
import os
import urllib.error
import urllib.request
from types import TracebackType
from unittest import mock

import at_models
from at_types import EXIT_FILE_FAILED, EXIT_OK
from tests._isolation import IsolatedCase

DATA = b"an invented model file\n" * 1000
URL = "https://example.invalid/tiny.bin"
TINY = at_models.ModelFile("tiny.bin", URL, len(DATA), hashlib.sha256(DATA).hexdigest())


class FakeResponse(io.BytesIO):
    """A urlopen response: read1(n), geturl(), and a context manager.

    read_error, when given, is raised by every read, as a dropped connection does.
    drip, when given, caps each read at that many bytes, as a slow server sends them.
    A plain read(n) fails the test: on a real response it waits until all n bytes
    arrive, so no deadline could be checked between two of them.
    """

    def __init__(
        self,
        data: bytes,
        final_url: str,
        read_error: Exception | None = None,
        drip: int | None = None,
    ) -> None:
        super().__init__(data)
        self.final_url = final_url
        self.read_error = read_error
        self.drip = drip

    def read1(self, size: int | None = -1, /) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        wanted = -1 if size is None else size
        return super().read1(wanted if self.drip is None else min(wanted, self.drip))

    def read(self, size: int | None = -1, /) -> bytes:
        raise AssertionError("read() waits for every byte asked; use read1()")

    def geturl(self) -> str:
        return self.final_url

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class FakeOpener:
    """Serves one body per URL and records each call's timeout."""

    def __init__(
        self,
        data: bytes = DATA,
        final_url: str = URL,
        error: OSError | None = None,
        read_error: Exception | None = None,
        drip: int | None = None,
    ) -> None:
        self.data, self.final_url, self.error = data, final_url, error
        self.read_error, self.drip = read_error, drip
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.data, self.final_url, self.read_error, self.drip)


class FetchTest(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.models = self.root / "models"

    def fetch(self, opener: FakeOpener) -> at_models.FetchResult:
        [result] = at_models.fetch_models(self.models, opener=opener, manifest=(TINY,))
        return result

    def leftovers(self) -> list[str]:
        return (
            sorted(p.name for p in self.models.iterdir())
            if self.models.exists()
            else []
        )

    def test_downloads_a_missing_file(self) -> None:
        opener = FakeOpener()
        result = self.fetch(opener)
        self.assertEqual(result.status, at_models.FetchStatus.DOWNLOADED)
        self.assertEqual((self.models / "tiny.bin").read_bytes(), DATA)
        self.assertEqual(self.leftovers(), ["tiny.bin"])
        self.assertEqual(opener.calls, [(URL, at_models.NETWORK_TIMEOUT_SECONDS)])

    def test_a_present_matching_file_is_not_downloaded(self) -> None:
        self.models.mkdir()
        (self.models / "tiny.bin").write_bytes(DATA)
        opener = FakeOpener()
        self.assertEqual(self.fetch(opener).status, at_models.FetchStatus.PRESENT)
        self.assertEqual(opener.calls, [])

    def test_a_present_mismatched_file_is_replaced(self) -> None:
        self.models.mkdir()
        (self.models / "tiny.bin").write_bytes(b"stale")
        self.assertEqual(
            self.fetch(FakeOpener()).status, at_models.FetchStatus.DOWNLOADED
        )
        self.assertEqual((self.models / "tiny.bin").read_bytes(), DATA)

    def test_failures_leave_no_file_and_no_partial(self) -> None:
        cases = [
            (FakeOpener(data=DATA[:-1] + b"?"), "digest"),
            (
                FakeOpener(data=DATA[:-10]),
                f"{len(DATA) - 10} of the pinned {len(DATA)}",
            ),
            (FakeOpener(data=DATA + b"extra"), "more bytes"),
            (FakeOpener(final_url="http://example.invalid/tiny.bin"), "HTTPS"),
            (FakeOpener(error=OSError("network unreachable")), "network unreachable"),
        ]
        for opener, needle in cases:
            with self.subTest(needle=needle):
                result = self.fetch(opener)
                self.assertEqual(result.status, at_models.FetchStatus.FAILED)
                self.assertIn(needle, result.detail)
                self.assertEqual(self.leftovers(), [])

    def test_an_unwritable_model_folder_is_a_failed_row(self) -> None:
        self.models.write_bytes(b"a file where the folder should be")
        result = self.fetch(FakeOpener())
        self.assertEqual(result.status, at_models.FetchStatus.FAILED)

    def test_a_failed_rename_leaves_no_partial(self) -> None:
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            result = self.fetch(FakeOpener())
        self.assertEqual(result.status, at_models.FetchStatus.FAILED)
        self.assertEqual(self.leftovers(), [])

    def test_a_failed_existing_file_is_kept_when_the_new_one_is_bad(self) -> None:
        self.models.mkdir()
        (self.models / "tiny.bin").write_bytes(b"stale")
        self.fetch(FakeOpener(data=b"wrong"))
        self.assertEqual((self.models / "tiny.bin").read_bytes(), b"stale")

    def test_lines_and_exit_code(self) -> None:
        ok = at_models.FetchResult("a.bin", at_models.FetchStatus.PRESENT)
        bad = at_models.FetchResult(
            "b.bin", at_models.FetchStatus.FAILED, "digest mismatch"
        )
        self.assertEqual(
            at_models.fetch_lines([ok, bad]),
            ["present     a.bin", "failed      b.bin", "    digest mismatch"],
        )
        self.assertEqual(at_models.fetch_exit_code([ok]), EXIT_OK)
        self.assertEqual(at_models.fetch_exit_code([ok, bad]), EXIT_FILE_FAILED)

    def test_an_unreadable_present_file_fails_only_its_own_row(self) -> None:
        # Regression: the present-check ran outside the try, so an unreadable
        # target raised out of fetch_models and stopped every later file.
        other = at_models.ModelFile("other.bin", URL, TINY.size, TINY.sha256)
        self.models.mkdir()
        (self.models / "tiny.bin").write_bytes(DATA)
        denied = OSError("denied")
        with mock.patch.object(at_models, "file_digest", side_effect=denied):
            first, second = at_models.fetch_models(
                self.models, opener=FakeOpener(), manifest=(TINY, other)
            )
        self.assertEqual(first.status, at_models.FetchStatus.FAILED)
        self.assertIn("denied", first.detail)
        self.assertEqual(second.status, at_models.FetchStatus.DOWNLOADED)
        self.assertEqual((self.models / "other.bin").read_bytes(), DATA)

    def test_a_truncated_response_is_a_failed_row_with_no_partial(self) -> None:
        # Regression: http.client's IncompleteRead is not an OSError, so it
        # escaped the except and crashed the run.
        result = self.fetch(FakeOpener(read_error=http.client.IncompleteRead(b"")))
        self.assertEqual(result.status, at_models.FetchStatus.FAILED)
        self.assertIn("IncompleteRead", result.detail)
        self.assertEqual(self.leftovers(), [])


class SlowClock:
    """A monotonic clock that moves *step* seconds every time it is read."""

    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class DeadlineTest(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.models = self.root / "models"

    def test_a_server_dripping_one_byte_a_minute_is_cut_off(self) -> None:
        # Every read beats the 60 s socket timeout, so only an overall deadline ends it.
        clock = SlowClock(step=59.0)
        [result] = at_models.fetch_models(
            self.models, opener=FakeOpener(drip=1), manifest=(TINY,), clock=clock
        )
        self.assertEqual(result.status, at_models.FetchStatus.FAILED)
        self.assertIn("deadline", result.detail)
        self.assertLess(clock.now, at_models.DOWNLOAD_DEADLINE_SECONDS + 2 * clock.step)
        self.assertEqual(sorted(self.models.iterdir()), [])

    def test_a_download_inside_the_deadline_lands(self) -> None:
        [result] = at_models.fetch_models(
            self.models,
            opener=FakeOpener(drip=4096),
            manifest=(TINY,),
            clock=SlowClock(1.0),
        )
        self.assertEqual(result.status, at_models.FetchStatus.DOWNLOADED)

    def test_the_deadline_covers_the_largest_model_at_a_slow_line(self) -> None:
        # Generous: the largest pinned model at 100 kB/s still finishes inside it.
        largest = max(model.size for model in at_models.MANIFEST)
        self.assertGreater(at_models.DOWNLOAD_DEADLINE_SECONDS, largest / 100_000)


class RedirectTest(IsolatedCase):
    """The real opener refuses any redirect that is not HTTPS, mid-chain included."""

    def redirect(self, new_url: str) -> urllib.request.Request | None:
        handler = at_models.HttpsOnlyRedirectHandler()
        request = urllib.request.Request(URL)
        return handler.redirect_request(
            request, io.BytesIO(b""), 302, "Found", http.client.HTTPMessage(), new_url
        )

    def test_an_http_hop_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.redirect("http://example.invalid/hop")
        self.assertIsInstance(caught.exception, OSError)
        self.assertIn("not HTTPS", str(caught.exception))

    def test_an_https_hop_is_followed(self) -> None:
        followed = self.redirect("https://example.invalid/next.bin")
        self.assertIsNotNone(followed)
        self.assertEqual(
            followed.full_url if followed else "", "https://example.invalid/next.bin"
        )

    def test_the_real_opener_carries_the_handler(self) -> None:
        # OpenerDirector keeps its handlers in an attribute the type stubs do not declare.
        handlers = vars(at_models.HTTPS_OPENER)["handlers"]
        self.assertTrue(
            any(isinstance(h, at_models.HttpsOnlyRedirectHandler) for h in handlers)
        )
        self.assertFalse(
            any(type(h) is urllib.request.HTTPRedirectHandler for h in handlers)
        )
