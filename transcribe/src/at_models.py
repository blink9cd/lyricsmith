"""The two pinned whisper.cpp models and the per-user folder they live in.

The manifest is the one place the model names, URLs, sizes and digests are written;
`--mode fetch-models` downloads exactly these, and a transcribe run's model defaults
point at them inside the folder `default_model_dir` derives. `default_model_dir`
computes that folder without creating it; only fetch-models' download creates it.
The download is checked: every redirect must stay on HTTPS, each file is streamed to
a temporary partial, hashed and bounded by its pinned size and an overall deadline on
the way in, and renamed into place only when its SHA-256 matches the manifest.
"""

from __future__ import annotations

import enum
import hashlib
import http.client
import os
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Protocol, cast

from at_types import EXIT_FILE_FAILED, EXIT_OK


@dataclass(frozen=True)
class ModelFile:
    """One pinned model: its file name, download URL, byte size and SHA-256."""

    name: str
    url: str
    size: int
    sha256: str


WHISPER_MODEL_NAME = "ggml-large-v3-turbo.bin"
VAD_MODEL_NAME = "ggml-silero-v6.2.0.bin"

#: The two pinned models: the Whisper model, then the voice-activity model.
MANIFEST: tuple[ModelFile, ...] = (
    ModelFile(
        WHISPER_MODEL_NAME,
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
        1624555275,
        "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
    ),
    ModelFile(
        VAD_MODEL_NAME,
        "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
        885098,
        "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
    ),
)

#: The folder under the per-user data root that holds this tool's files.
APP_FOLDER = "lyricsmith"
#: The folder under the app folder that holds the models.
_MODELS = "models"


def _data_root(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """The per-user data root: Application Support, LOCALAPPDATA, or XDG_DATA_HOME.

    An empty variable counts as unset, and any platform that is neither macOS nor
    Windows takes the XDG form.
    """
    if platform == "darwin":
        return home / "Library" / "Application Support"
    if platform == "win32":
        local = env.get("LOCALAPPDATA")
        return Path(local) if local else home / "AppData" / "Local"
    data = env.get("XDG_DATA_HOME")
    return Path(data) if data else home / ".local" / "share"


def default_model_dir(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """The per-user model folder, where fetch-models writes: under `APP_FOLDER`."""
    return _data_root(platform, env, home) / APP_FOLDER / _MODELS


# --- fetch-models: the checked download ------------------------------------------

#: The timeout on every socket operation of a download.
NETWORK_TIMEOUT_SECONDS = 60
#: The bound on one file's whole download. The socket timeout bounds only each read,
#: so a server sending a byte a minute would otherwise never be cut off. Six hours
#: lets the largest pinned model (about 1.6 GB) arrive over a line as slow as about
#: 75 kB/s.
DOWNLOAD_DEADLINE_SECONDS = 6 * 60 * 60
#: One read from the response, and one read when hashing a file on disk.
CHUNK_BYTES = 1 << 20
#: The status column's width in fetch-models' rows.
STATUS_WIDTH = 12


#: A monotonic clock in seconds: time.monotonic, or a test's stand-in.
Clock = Callable[[], float]


class Response(Protocol):
    """The part of a urlopen response the download reads."""

    def read1(self, size: int, /) -> bytes:
        """Up to size bytes from at most one socket read; b"" at the end of the body.

        Not read(size): that waits until all size bytes arrive, so no deadline could
        be checked while a slow server trickles them in.
        """
        ...

    def geturl(self) -> str:
        """The URL the response came from, after any redirects."""
        ...

    def __enter__(self) -> Response:
        """Enter the response's context."""
        ...

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Close the response."""
        ...


class Opener(Protocol):
    """Opens a URL with a timeout on every socket operation."""

    def __call__(self, url: str, *, timeout: float) -> Response:
        """Open url, every socket operation bounded by timeout seconds."""
        ...


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib's redirect handler, refusing any hop to a URL that is not HTTPS.

    urllib follows an https-to-http redirect on its own, so a check of the final URL
    alone would let a request travel in the clear mid-chain.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """The base class's next request, or HTTPError (an OSError) for a non-HTTPS hop."""
        if not newurl.startswith("https://"):
            raise urllib.error.HTTPError(
                newurl,
                code,
                f"refused a redirect to a URL that is not HTTPS: {newurl}",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


#: The opener every download goes through: urllib's defaults, the redirect handler
#: replaced by the HTTPS-only one.
HTTPS_OPENER = urllib.request.build_opener(HttpsOnlyRedirectHandler)


def https_open(
    url: str, *, timeout: float
) -> Response:  # pragma: no cover - the real network seam; tests inject an opener
    """Open *url* through `HTTPS_OPENER`; the final URL is checked again afterwards."""
    return cast(Response, HTTPS_OPENER.open(url, timeout=timeout))


class FetchStatus(enum.Enum):
    """What fetch-models did with one file."""

    PRESENT = "present"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


@dataclass(frozen=True)
class FetchResult:
    """One manifest file's row: its name, its status and a failure's reason."""

    name: str
    status: FetchStatus
    detail: str = ""


def file_digest(path: Path) -> str:
    """The SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _Refused(Exception):
    """A download this tool will not accept: not HTTPS, the wrong size, or too slow."""


def _stream_into(
    model_file: ModelFile, partial: IO[bytes], opener: Opener, clock: Clock
) -> str:
    """Copy the download into partial, bounded by size and deadline; return its digest.

    The size check runs before the digest, so a server that streams forever is cut
    off at the pinned size plus one chunk, and one that stops short is named by its
    byte count rather than as a digest mismatch. The deadline is checked after every
    read, each of which the socket timeout bounds, so a server trickling bytes is cut
    off at `DOWNLOAD_DEADLINE_SECONDS` plus at most one read.
    """
    digest = hashlib.sha256()
    total = 0
    deadline = clock() + DOWNLOAD_DEADLINE_SECONDS
    with opener(model_file.url, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        if not response.geturl().startswith("https://"):
            raise _Refused(
                f"the download ended at a URL that is not HTTPS: {response.geturl()}"
            )
        while chunk := response.read1(CHUNK_BYTES):
            total += len(chunk)
            if total > model_file.size:
                raise _Refused(f"more bytes than the pinned {model_file.size}")
            if clock() > deadline:
                raise _Refused(
                    f"passed the {DOWNLOAD_DEADLINE_SECONDS} s download deadline "
                    f"after {total} of the pinned {model_file.size} bytes"
                )
            digest.update(chunk)
            partial.write(chunk)
    if total < model_file.size:
        # Named apart from a digest mismatch: a dropped connection, not a bad file.
        raise _Refused(
            f"the download ended after {total} of the pinned {model_file.size} bytes"
        )
    return digest.hexdigest()


def fetch_one(
    model_file: ModelFile,
    model_dir: Path,
    opener: Opener,
    clock: Clock = time.monotonic,
) -> FetchResult:
    """Present, downloaded, or failed; the target is replaced only on a matching digest.

    Every failure is this file's row, never a crash (the bulk rule): urllib's
    URLError is an OSError, a truncated or garbled response is an HTTPException,
    and an unreadable present file fails inside the same try. The finally removes
    the partial whatever happened.
    """
    target = model_dir / model_file.name
    temp: Path | None = None
    try:
        if target.is_file() and file_digest(target) == model_file.sha256:
            return FetchResult(model_file.name, FetchStatus.PRESENT)
        model_dir.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(
            dir=model_dir, prefix=f".{model_file.name}.", suffix=".part"
        )
        temp = Path(name)
        with os.fdopen(handle, "wb") as partial:
            got = _stream_into(model_file, partial, opener, clock)
        if got != model_file.sha256:
            return FetchResult(
                model_file.name, FetchStatus.FAILED, f"digest mismatch: got {got}"
            )
        os.replace(temp, target)
        return FetchResult(model_file.name, FetchStatus.DOWNLOADED)
    except (_Refused, OSError, http.client.HTTPException) as error:
        return FetchResult(model_file.name, FetchStatus.FAILED, str(error))
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def fetch_models(
    model_dir: Path,
    opener: Opener = https_open,
    manifest: Sequence[ModelFile] = MANIFEST,
    clock: Clock = time.monotonic,
) -> list[FetchResult]:
    """Every manifest file, each judged alone (the bulk rule); *clock* times the deadline."""
    return [fetch_one(model_file, model_dir, opener, clock) for model_file in manifest]


def fetch_lines(results: Sequence[FetchResult]) -> list[str]:
    """One row per file, a failure's reason indented below it."""
    lines = []
    for result in results:
        lines.append(f"{result.status.value:<{STATUS_WIDTH}}{result.name}")
        if result.detail:
            lines.append(f"    {result.detail}")
    return lines


def fetch_exit_code(results: Sequence[FetchResult]) -> int:
    """1 when any file failed, else 0."""
    failed = any(result.status is FetchStatus.FAILED for result in results)
    return EXIT_FILE_FAILED if failed else EXIT_OK
