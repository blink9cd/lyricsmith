"""Shared helpers for the sync tool's suites: the path bootstrap, isolation and fakes.

Every sync test case derives from album's `fixtures.IsolatedCase`: HOME and the XDG
folders point at a fresh temporary folder, and a tripwire fails any socket use. The live
suite reuses the transcribe tool's search for whisper.cpp's bundled sample, loaded from
that tool's tests folder by file rather than put on the path: that folder is a package
named `tests`, the name this folder answers to as well. `fake_child` and `reply_for`
stand in for the child process that needs PyTorch, and `not_sung_reply` for one whose
text whisper-cli did not hear; `fake_opener` stands in for the network, and
`invented_models` and `invented_whisper_models` for the pinned models, each holding
`DATA`. `host_absolute` roots a made-up track at the host's drive, so it is absolute on
Windows too, and `FOLDER_LOCKS` and `FILE_LOCKS` say whether ``chmod(0)`` locks
anything here, for the mode-000 tests and their portable twins.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

_TOOL = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_TOOL), str(_TOOL / "src")]

import sync  # noqa: E402 - import follows the sys.path bootstrap above

sys.path.insert(0, str(sync.ALBUM_ENTRY.parent / "tests"))

import at_engine  # noqa: E402 - import follows the sys.path bootstrap above
import at_models  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - album's test fixtures, found beside album.py
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
from sy_lines import Span  # noqa: E402 - import follows the sys.path bootstrap above
from sy_text import NO_LINE, tokenize  # noqa: E402 - follows the bootstrap above

IsolatedCase = fixtures.IsolatedCase
#: The seconds each frame of a made-up reply stands for: wav2vec2's own frame length.
FRAME_SECONDS = 0.02
#: The error stream a fake child leaves, whose last line a crash row must carry.
CHILD_STDERR = "line one\nlast words\n"
#: The address a fake download ends at: HTTPS, as the fetch requires.
FAKE_URL = "https://example.invalid/model.bin"
#: The bytes every invented model holds, and their SHA-256, as a manifest pins them.
DATA = b"an invented model file\n" * 100
DIGEST = hashlib.sha256(DATA).hexdigest()
#: What whisper-cli heard of a faked track: enough pairs to judge, and the text sung.
HEARD = Heard(40, 0.9)
#: What it heard of a faked track whose text is another song's.
UNSUNG = Heard(30, 0.04)
#: An invented transcript of another song: thirty distinct word pairs, none in a text
#: of la, lo and li, so any such text is judged not sung against it.
OTHER_SONG = (
    "red kites over the harbor wall seven lanterns in the rain a quiet engine hums"
    " all night and nobody comes home again under the bridge the river keeps its"
    " silver promise"
)
#: The root of the drive the suites run on, album's `fixtures.HOST_ROOT`. A made-up
#: track path must be absolute on the host, as the walk names one, and ``/music`` has
#: no drive on Windows, so it is not absolute there.
HOST_ROOT = fixtures.HOST_ROOT
#: Why a POSIX mode check skips on Windows: album's `fixtures.NO_POSIX_MODES`.
NO_POSIX_MODES = fixtures.NO_POSIX_MODES
#: Why a real mode-000 test skips where the lock does not hold.
NO_MODE_000_LOCK = "chmod(0) does not stop a read here (root, or Windows)"


#: The absolute POSIX path given, rooted at `HOST_ROOT`: album's `fixtures.host_absolute`.
host_absolute = fixtures.host_absolute


def _mode_000_locks(
    make: Callable[[Path], object], look: Callable[[Path], object]
) -> bool:
    """True when ``chmod(0)`` stops this process doing *look* to what *make* made.

    Root reads a mode-000 file or folder, and on Windows ``chmod`` sets only the
    read-only attribute, which stops no read. Probed rather
    than inferred from the platform, so the real-permission tests run wherever they can;
    each has a twin raising the refusal through a seam, which runs everywhere.
    """
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "probe"
        make(probe)
        probe.chmod(0)
        try:
            look(probe)
        except PermissionError:
            return True
        finally:
            probe.chmod(0o700)
    return False


#: Whether a mode-000 folder cannot be listed here; a mode that stops a listing
#: stops a look at its entries too, for any user the bits bind.
FOLDER_LOCKS = _mode_000_locks(Path.mkdir, lambda folder: list(folder.iterdir()))
#: Whether a mode-000 file cannot be read here.
FILE_LOCKS = _mode_000_locks(lambda file: file.write_bytes(b""), Path.read_bytes)


def permission_denied(path: Path) -> PermissionError:
    """The refusal a mode-000 *path* gets, as the OS raises it, for a twin to raise."""
    return PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))


def transcribe_test_module(name: str) -> types.ModuleType:
    """The transcribe tool's test module *name*, loaded from its file."""
    folder = Path(sync.transcribe.__file__).resolve().parent / "tests"
    # A name of its own, registered for the import only: a dataclass in the file reads
    # sys.modules[cls.__module__] while its body runs, and a bare name could be a real one.
    unique = f"_sy_transcribe_{name}"
    found = importlib.util.spec_from_file_location(unique, folder / f"{name}.py")
    assert found is not None and found.loader is not None  # a .py file always has one
    module = importlib.util.module_from_spec(found)
    sys.modules[unique] = module
    try:
        found.loader.exec_module(module)
    finally:
        del sys.modules[unique]
    return module


def invented_models(*names: str) -> tuple[at_models.ModelFile, ...]:
    """One invented model a name, each holding `DATA`, standing in for the pinned ones."""
    return tuple(
        at_models.ModelFile(name, f"https://example.invalid/{name}", len(DATA), DIGEST)
        for name in names
    )


#: Two invented models standing in for the transcribe tool's pinned whisper models.
WHISPER_TINY = invented_models("whisper.bin", "vad.bin")


class FakeResponse(io.BytesIO):
    """A download response serving *data* from an HTTPS address."""

    def geturl(self) -> str:
        """The address the download ended at."""
        return FAKE_URL


def fake_opener(data: bytes) -> at_models.Opener:
    """An opener that serves *data* for every address, touching no network."""

    def open_url(url: str, *, timeout: float) -> FakeResponse:
        return FakeResponse(data)

    return open_url


def fake_child(
    reply: str | None, code: int = 0, raises: BaseException | None = None
) -> at_engine.Runner:
    """A runner that writes *reply* where the argv says, as the child would, then exits *code*."""

    def run(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if raises is not None:
            raise raises
        if reply is not None:
            Path(argv[argv.index("--reply") + 1]).write_text(
                reply, encoding="utf-8", newline="\n"
            )
        return subprocess.CompletedProcess(list(argv), code, "", CHILD_STDERR)

    return run


def reply_for(
    lines: Sequence[str],
    *,
    score: float = 0.9,
    widths: Sequence[int] = (),
    heard: Heard = HEARD,
) -> str:
    """A good reply for *lines*: one span per token, back to back, each at *score*.

    A letter of line ``i`` spans ``widths[i]`` frames, one frame when *widths* is short;
    a separator spans one frame. Whisper-cli heard *heard*, by default the text sung.
    """
    tokens = tokenize(lines)
    spans: list[Span] = []
    frame = 0
    for line in tokens.line_of:
        width = widths[line] if line != NO_LINE and line < len(widths) else 1
        spans.append(Span(frame, frame + width, score))
        frame += width
    reply = rq.Reply(rq.STATUS_OK, frame + 1, FRAME_SECONDS, tuple(spans), heard)
    return rq.dump_reply(reply)


def not_sung_reply(heard: Heard = UNSUNG, transcript: str | None = OTHER_SONG) -> str:
    """The reply of a child whose track's text whisper-cli did not hear sung.

    whisper-cli heard *transcript*, by default `OTHER_SONG`; None is one too long to keep.
    """
    return rq.dump_reply(rq.NotSung(heard, transcript))


def invented_whisper_models(case: IsolatedCase, folder: Path) -> Path:
    """*folder*, made, holding invented whisper models that stand in for the pinned two.

    The transcribe tool's manifest is patched for *case*'s length, so a run's setup
    check finds them as it would the real ones.
    """
    case.enterContext(mock.patch.object(at_models, "MANIFEST", WHISPER_TINY))
    return model_folder(folder, WHISPER_TINY)


def model_folder(folder: Path, models: Sequence[at_models.ModelFile]) -> Path:
    """*folder*, made, holding each of the invented *models*."""
    folder.mkdir()
    for model in models:
        (folder / model.name).write_bytes(DATA)
    return folder
