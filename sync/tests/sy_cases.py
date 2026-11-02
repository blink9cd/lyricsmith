"""Shared helpers for the sync tool's suites: the path bootstrap, isolation and fakes.

Every sync test case derives from album's `fixtures.IsolatedCase`: HOME and the XDG
folders point at a fresh temporary folder, and a tripwire fails any socket use. The live
suite reuses the transcribe tool's search for whisper.cpp's bundled sample, loaded from
that tool's tests folder by file rather than put on the path: that folder is a package
named `tests`, the name this folder answers to as well. `fake_child` and `reply_for`
stand in for the child process that needs PyTorch, and `not_sung_reply` for one whose
text whisper-cli did not hear; `fake_opener` stands in for the network, and
`invented_models` and `invented_whisper_models` for the pinned models, each holding
`DATA`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import types
from collections.abc import Sequence
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
# os.geteuid exists only off Windows, so it is looked up; root reads a mode-000 file or
# folder, so the unreadable-sidecar and unreadable-folder tests skip under it.
_GETEUID = getattr(os, "geteuid", None)
IS_ROOT = _GETEUID is not None and _GETEUID() == 0


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
