"""Shared helpers for the hearing suites: a black-box case running main() on invented tracks.

`HearCliCase` stands in every model with an invented file, and every outside call with
a fake: the child by a runner answering one reply a track and keeping each request it
was handed, so a suite reads what the child was asked. `provider_lrc` writes the timed
text a provider gives, stamped and unlabeled. Every track and text is invented.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sy_cases  # noqa: E402 - puts the tool, src/ and album's fixtures on the path
import sy_models  # noqa: E402 - import follows the sys.path bootstrap above
import sy_modes  # noqa: E402 - import follows the sys.path bootstrap above
import sy_request as rq  # noqa: E402 - import follows the sys.path bootstrap above
import sync  # noqa: E402 - import follows the sys.path bootstrap above
from lyrics_heard import Heard  # noqa: E402 - import follows the sys.path bootstrap above
from sweep_child import ChildResult  # noqa: E402 - import follows the bootstrap above
from sy_cases import DATA, OTHER_SONG, fake_child, fake_opener  # noqa: E402 - bootstrap

fixtures = sy_cases.fixtures
#: Three invented models standing in for sync's pinned ones.
TINY = sy_cases.invented_models("one.bin", "two.bin", "three.bin")
#: The name the dispatcher gives the tool.
DISPATCHED = "lyricsmith sync"
#: An invented text of twenty-two distinct word pairs, so a hearing of it is judged.
LINES = (
    "the morning ferry leaves at nine with seven sleepy riders",
    "a gull is shouting from the mast while the captain hums a tune",
)
#: What a child heard of a sung text, of another song's, and of too little.
SUNG = rq.heard_only(Heard(22, 1.0), " ".join(LINES))
UNSUNG = rq.heard_only(Heard(30, 0.0), OTHER_SONG)
LITTLE = rq.heard_only(Heard(4, 0.5), "a gull is shouting")


def provider_lrc(lines: Sequence[str]) -> str:
    """*lines* as a provider's timed .lrc: one stamp a line, a second apart, no label."""
    return "".join(f"[00:{index:02d}.00]{line}\n" for index, line in enumerate(lines))


def never_embed(argv: Sequence[str], timeout: float) -> ChildResult:
    """The album runner a hearing must never call: it embeds nothing."""
    raise AssertionError(f"a hearing ran album: {argv}")


class RequestingChild:
    """A fake child answering *replies* in turn, keeping each request it was handed.

    A reply of None writes no reply file, as a child that crashed would leave none.
    """

    def __init__(self, *replies: rq.HeardOnly | str | None, code: int = 0) -> None:
        """Answer *replies* in turn, then None; exit *code* each time."""
        self.replies = list(replies)
        self.code = code
        self.requests: list[rq.Request] = []

    def __call__(
        self, argv: Sequence[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Keep the request the argv names, then answer as the child would."""
        text = Path(argv[argv.index("--request") + 1]).read_text(encoding="utf-8")
        self.requests.append(rq.load_request(text))
        reply = self.replies.pop(0) if self.replies else None
        if isinstance(reply, rq.HeardOnly):
            reply = rq.dump_reply(reply)
        return fake_child(reply, self.code)(argv, timeout=timeout)


class HearCliCase(sy_cases.IsolatedCase):
    """main() on invented tracks, every model invented and every outside call faked."""

    def setUp(self) -> None:
        """Invent the models, and name the library, the log, the list and the store."""
        super().setUp()
        self.enterContext(mock.patch.object(sy_models, "MANIFEST", TINY))
        self.models = sy_cases.model_folder(self.tmp / "models", TINY)
        self.whisper = sy_cases.invented_whisper_models(self, self.tmp / "whisper")
        self.library = self.tmp / "Library"
        self.log = self.tmp / "state" / "hearings"
        self.listed = self.tmp / "state" / "by-hand.tsv"
        self.heard = self.tmp / "state" / "heard"
        self.child = RequestingChild()

    def album(self, *tracks: fixtures.TrackSpec) -> list[Path]:
        """*tracks* built in the album ``Artist/Album`` under the library."""
        return fixtures.build_album(self.library / "Artist" / "Album", tracks)

    def seams(self) -> sy_modes.Seams:
        """Every seam faked: the test's child, no album run, a full install."""
        return sy_modes.Seams(
            run=self.child,
            embed=never_embed,
            album_entry=self.tmp / "album.py",
            find_spec=lambda name: object(),
            which=lambda name: f"/usr/bin/{name}",
            version=lambda name: "9.9.9",
        )

    def main(
        self, *argv: str, child: RequestingChild | None = None
    ) -> tuple[int, str, str]:
        """Run the command on *argv* and the models; its code, output and errors."""
        if child is not None:
            self.child = child
        seams = self.seams()
        full = [
            *argv,
            "--model-dir",
            str(self.models),
            "--whisper-model-dir",
            str(self.whisper),
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(
                full, seams=seams, opener=fake_opener(DATA), prog=DISPATCHED
            )
        return code, out.getvalue(), err.getvalue()

    def hear(
        self, *argv: str, child: RequestingChild | None = None
    ) -> tuple[int, str, str]:
        """Run main() in the hearing mode over the library, logging to the test's log."""
        return self.main(
            "--mode",
            "hear",
            "--path",
            str(self.library),
            "--hearing-log",
            str(self.log),
            *argv,
            child=child,
        )
