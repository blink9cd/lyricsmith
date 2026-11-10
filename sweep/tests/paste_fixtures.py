"""Shared fixtures for the paste walker's suites: a library, its has-it list, a terminal.

`PasteCase` builds an invented library whose tracks each hold another song's words, a
transcript store hearing each track's own song, and the has-it list naming them, then
runs ``--mode paste`` through the entry point. The terminal is `FakeTerminal`, scripted
answers and clipboard texts, and the album tool's child runs in this process
(`InProcessAlbum`), its real ``--mode paste`` on its real wiring, so no browser,
clipboard, terminal or network is touched. Every title, URL and lyric is invented.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import contextlib
import io
import os
import stat
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402 - puts album and the libraries on sys.path

sys.path.append(str(_HERE.parents[1] / "album" / "tests"))

import album  # noqa: E402 - import follows the sys.path insert above
import fix_fakes  # noqa: E402 - import follows the sys.path insert above
import fixtures  # noqa: E402 - import follows the sys.path insert above
import lyrics_heard  # noqa: E402 - import follows the sys.path insert above
import lyrics_heard_store as store  # noqa: E402 - import follows the sys.path insert above
import sweep_child  # noqa: E402 - import follows the sys.path insert above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path insert above
import sweep_genius_lists as gl  # noqa: E402 - import follows the sys.path insert above
import sweep_paste as sp  # noqa: E402 - import follows the sys.path insert above
from fix_fakes import FOUNDRY, HARBOR, MEADOW, QUARRY  # noqa: E402 - follows the insert

#: An invented Genius song page, one per song.
URLS = {
    HARBOR: "https://genius.com/Invented-artist-harbor-song-lyrics",
    MEADOW: "https://genius.com/Invented-artist-meadow-fence-lyrics",
}
#: Each track's own song, and the wrong one it holds.
SONGS = ((HARBOR, QUARRY), (MEADOW, FOUNDRY))


@dataclass(frozen=True)
class Result:
    """What one walk returned and wrote."""

    code: int
    stdout: str
    stderr: str


@dataclass
class FakeTerminal:
    """The walker's terminal, clipboard and browser, each scripted or recorded.

    Each answer or clipboard text is popped in turn; an exception in either list is
    raised in its place. *why_not* is what `ready` says, None when the walk may start.
    """

    answers: list[str | BaseException] = field(default_factory=list)
    clips: list[str | BaseException] = field(default_factory=list)
    why_not: str | None = None
    opened: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def ready(self) -> str | None:
        """Why the walk cannot prompt; None when it can."""
        return self.why_not

    def ask(self, prompt: str) -> str:
        """The next scripted answer to *prompt*; EOF once none is left."""
        self.prompts.append(prompt)
        return _next(self.answers, EOFError())

    def clipboard(self) -> str:
        """The next scripted clipboard text."""
        return _next(self.clips, AssertionError("the clipboard was read unscripted"))

    def open_url(self, url: str) -> None:
        """Record *url* as opened."""
        self.opened.append(url)

    def seams(self) -> sp.PasteSeams:
        """The walker's seams, all of them this terminal's."""
        return sp.PasteSeams(self.ready, self.ask, self.clipboard, self.open_url)


def _next(items: list[str | BaseException], end: BaseException) -> str:
    """Pop the first of *items*, raising it when it is an exception; *end* when none."""
    item = items.pop(0) if items else end
    if isinstance(item, BaseException):
        raise item
    return item


@dataclass
class InProcessAlbum:
    """The album tool's child, run in this process by its own `main` on its real wiring.

    Each call records its command, its environment, the pasted text and the text
    file's mode; a result
    in *results*, else a code in *codes*, is returned in place of a run, popped in
    turn, and *interrupted* marks every result as Ctrl-C's.
    """

    calls: list[list[str]] = field(default_factory=list)
    envs: list[Mapping[str, str]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    modes: list[int] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    codes: list[int] = field(default_factory=list)
    results: list[sweep_child.ChildResult] = field(default_factory=list)
    interrupted: bool = False

    def __call__(
        self,
        argv: Sequence[str],
        timeout_s: float,
        *,
        env: Mapping[str, str] | None = None,
    ) -> sweep_child.ChildResult:
        """Run *argv*'s album tool here; its result. *env* is recorded, not applied."""
        self.calls.append(list(argv))
        self.envs.append(dict(os.environ if env is None else env))
        text_file = Path(sf.option_value(argv, "--text-from"))
        self.files.append(text_file)
        self.texts.append(text_file.read_text(encoding="utf-8"))
        self.modes.append(stat.S_IMODE(text_file.stat().st_mode))
        if self.interrupted:
            return sweep_child.ChildResult(130, interrupted=True)
        if self.results:
            return self.results.pop(0)
        if self.codes:
            return sweep_child.ChildResult(self.codes.pop(0))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = album.main(list(argv[2:]))
        sys.stderr.write(err.getvalue() + out.getvalue())
        return sweep_child.ChildResult(code)


class PasteCase(sf.IsolatedCase):
    """Two wrong-text tracks of one album, heard, listed in has-it.tsv."""

    def setUp(self) -> None:
        """The album, the store, the list, the terminal and the child."""
        super().setUp()
        folder = self.library / "Invented Artist" / "Lanterns"
        specs = (
            fix_fakes.song_track(1, HARBOR, txt=QUARRY),
            fix_fakes.song_track(2, MEADOW, txt=FOUNDRY),
        )
        self.tracks = fixtures.build_album(folder, specs)
        self.heard = self.tmp / "heard"
        for track, (song, held) in zip(self.tracks, SONGS, strict=True):
            fingerprint = lyrics_heard.text_fingerprint(held)
            store.write_record(self.heard, store.HeardRecord(track, fingerprint, song))
        self.has_it = self.tmp / "has-it.tsv"
        self.write_has_it(
            (track, URLS[song], fix_fakes.TITLES[song])
            for track, (song, _) in zip(self.tracks, SONGS, strict=True)
        )
        self.terminal = FakeTerminal()
        self.child = InProcessAlbum()

    def write_has_it(self, rows: Iterable[tuple[Path | str, str, str]]) -> None:
        """Write the has-it list of *rows*, each a track, a URL and a title."""
        lines = ["\t".join(gl.HAS_IT_COLUMNS)]
        lines += (
            f"{path}\t{url}\t{title}\tInvented Artist" for path, url, title in rows
        )
        self.has_it.write_text(
            "".join(f"{line}\n" for line in lines), "utf-8", newline=""
        )

    def walk(self, *extra: str, child: sweep_child.ChildRunner | None = None) -> Result:
        """Run ``--mode paste`` over the list with this test's seams and *extra*.

        Each album tool child is *child*, this case's in-process one when None.
        """
        argv = [
            "--library",
            str(self.library),
            "--mode",
            "paste",
            "--from",
            str(self.has_it),
            "--heard-dir",
            str(self.heard),
            *extra,
        ]
        wiring = sweep.Wiring(
            child or self.child,
            sf.FakeSleep(),
            sf.fixed_now,
            paste=self.terminal.seams(),
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, out.getvalue(), err.getvalue())

    def words_of(self, track: Path) -> list[str]:
        """The words *track*'s ``.txt`` holds now."""
        return fix_fakes.words(track.with_suffix(".txt").read_text("utf-8"))
