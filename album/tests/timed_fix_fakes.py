"""Provider-timed tracks and the case the timed `--mode fix-text` suites share.

A provider-timed track holds an unlabeled timed ``.lrc`` and the frames a run embeds from
it, a ``SYLT`` and a ``USLT``, written through the album tool's own embed
(`lyrics_tags.write_lyrics`), so what a fix replaces is what a run left. Each ``.lrc``
holds one blank stamped line between verses, which a fix keeps. `TimedFixCase`
answers from a synced provider step as well as an untimed one, and records which was
asked in what order. Every title and lyric is the invented songs of `fix_fakes`.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_sidecar  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    PLAIN_KEY,
    SYNCED_KEY,
    FixCase,
    song_track,
)
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

#: The language the frames are embedded in, the album tool's default.
LANG = "eng"
#: The seconds between two stamped lines: a four-line song ends well inside the
#: thirty-second tracks the fixtures build, so no answer runs past the end.
STEP_S = 4
#: Which line of a song a blank stamped line follows.
BLANK_AFTER = 2
#: The header a machine-timed ``.lrc`` carries.
MACHINE_HEADER = "[by:machine timed]\n"


def _stamp(seconds: int) -> str:
    """An LRC time stamp of *seconds*, to the hundredth."""
    return f"[{seconds // 60:02d}:{seconds % 60:02d}.00]"


def lrc_of(song: str, *, label: str = "") -> str:
    """*song* as a timed ``.lrc``: a stamp a line, one blank stamped line between verses.

    *label* is a header line before the first stamp, none by default.
    """
    lines = song.strip().splitlines()
    stamped = [
        f"{_stamp(STEP_S * index + 1)}{line}" for index, line in enumerate(lines)
    ]
    blank = _stamp(STEP_S * BLANK_AFTER - 1)
    stamped.insert(BLANK_AFTER, blank)
    return label + "\n".join(stamped) + "\n"


def timed_track(index: int, song: str, held: str, *, label: str = "") -> TrackSpec:
    """Track *index* titled for *song*, holding *held* as a timed ``.lrc`` alone."""
    return song_track(index, song, txt=None, lrc=lrc_of(held, label=label))


def embed(path: Path) -> None:
    """Embed *path*'s ``.lrc`` into its frames, as a run serving the sidecar does."""
    lyrics = lrc_lyrics(path)
    lyrics_tags.write_lyrics(path, lyrics, LANG)


def lrc_lyrics(path: Path) -> lm.Lyrics:
    """The lyrics *path*'s ``.lrc`` holds, parsed as the album tool parses it."""
    return lm.parse_lrc(path.with_suffix(".lrc").read_text("utf-8"))


def frame_kinds(path: Path) -> list[lm.FrameKind]:
    """The kinds of *path*'s lyrics frames, sorted."""
    return sorted(frame.kind for frame in lyrics_tags.read_tags(path).frames)


def planned_sidecar(path: Path) -> lm.SidecarState:
    """What *path*'s sidecars hold, by the plan sync's planner reads them through."""
    return lyrics_sidecar.plan_sidecars(path, path.parent)


class TimedFixCase(FixCase):
    """`FixCase` whose provider answers timed and untimed text, the calls in order."""

    def setUp(self) -> None:
        """A table answering nothing, timed or untimed, and no call yet."""
        self.order: list[lm.LyricsKind] = []
        super().setUp()

    def answer_both(
        self, synced: Mapping[str, str], plain: Mapping[str, str | Exception]
    ) -> None:
        """Answer each title in *synced* timed, and in *plain* untimed, from LRCLIB."""
        self.titles = {
            SYNCED_KEY: run_fakes.TitleProvider(synced),
            PLAIN_KEY: run_fakes.TitleProvider(plain),
        }
        self.providers = {
            key: _Ordered(step, self.order, key.kind)
            for key, step in self.titles.items()
        }

    def answer(self, plain: Mapping[str, str | Exception]) -> None:
        """Answer each title in *plain* untimed only, recording the order asked."""
        self.answer_both({}, plain)

    def build_timed(self, *tracks: TrackSpec) -> list[Path]:
        """Build *tracks*, each ``.lrc`` among them embedded as a run embeds it."""
        paths = self.build(*tracks)
        for path in paths:
            if path.with_suffix(".lrc").exists():
                embed(path)
        return paths


class _Ordered:
    """A provider step that notes its kind in a shared list each time it is asked."""

    def __init__(
        self,
        provider: run_fakes.TitleProvider,
        order: list[lm.LyricsKind],
        kind: lm.LyricsKind,
    ) -> None:
        """Wrap *provider*, noting *kind*, the step's, into *order* at each call."""
        self.provider = provider
        self.order = order
        self.kind = kind

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Note the call, then answer as the wrapped provider does."""
        self.order.append(self.kind)
        return self.provider(query)
