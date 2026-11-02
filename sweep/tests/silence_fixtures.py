"""Shared fixtures for the silence pass's suites: a fake ffmpeg, invented tracks, a runner.

`FakeFfmpeg` stands in for ffmpeg through `lyrics_silence.measure_level`'s process seam:
it answers by the track's file name, silent, loud or failing, and can raise Ctrl-C on
one track, so no suite here starts a process. `add_track` writes a silent MP3 through
the album tool's own test builder (its frames are what mutagen reads a length from),
tagged with an invented title and track number. `SilenceCase` runs `sweep.main` with
``--mode silence`` and that fake, on the isolated library and state directory of
`sweep_fixtures.IsolatedCase`. Every name and title here is invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, after importing the entry point.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.id3 import ID3, TRCK

import sweep
import sweep_fixtures as sf
import sweep_silence

# The album tool's test builders, beside its entry point: the pyproject's mypy_path
# names the same folder, so the type checker resolves this import as the runtime does.
sys.path.append(str(sweep.ALBUM_ENTRY.parent / "tests"))

import fixtures as album_fixtures  # noqa: E402 - import follows the sys.path insert above

#: The peaks the fake reports: a loud track, and one far below the silence threshold.
LOUD_DBFS = -3.0
SILENT_DBFS = -91.0
#: The exit code the fake gives a track it fails on.
FAILED_EXIT = 1


def _volumedetect(peak: float) -> str:
    """What volumedetect writes to stderr for a whole-track *peak*."""
    return f"[Parsed_volumedetect_0 @ 0x0] max_volume: {peak:.1f} dB\n"


@dataclass
class FakeFfmpeg:
    """ffmpeg through the run seam: each track answered by its file name.

    A name in *silent* measures silent, in *failing* exits `FAILED_EXIT`, and the name
    *interrupt* raises Ctrl-C; any other measures loud. *calls* names each track asked.
    """

    silent: frozenset[str] = frozenset()
    failing: frozenset[str] = frozenset()
    interrupt: str | None = None
    calls: list[str] = field(default_factory=list)

    def __call__(
        self, argv: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Answer for the track after ``-i`` in *argv*, as the fields above say."""
        name = Path(argv[argv.index("-i") + 1]).name
        self.calls.append(name)
        if name == self.interrupt:
            raise KeyboardInterrupt
        if name in self.failing:
            return subprocess.CompletedProcess(argv, FAILED_EXIT, "", "invented error")
        peak = SILENT_DBFS if name in self.silent else LOUD_DBFS
        return subprocess.CompletedProcess(argv, 0, "", _volumedetect(peak))


def add_track(
    album: Path,
    name: str,
    *,
    seconds: float = 30.0,
    title: str | None = "Invented Title",
    track_no: str | None = None,
) -> Path:
    """A silent MP3 of *seconds* named *name* under *album*, tagged; return its path."""
    path = album_fixtures.build_silent_mp3(
        album / name, seconds, "Invented Artist", title
    )
    if track_no is not None:
        tags = ID3(path)
        tags.add(TRCK(encoding=3, text=[track_no]))
        tags.save(path)
    return path


@dataclass(frozen=True)
class Result:
    """What one call of `main` returned and wrote."""

    code: int
    stdout: str
    stderr: str


class SilenceCase(sf.IsolatedCase):
    """Runs the silence pass with a fake ffmpeg against this test's library."""

    ffmpeg: FakeFfmpeg
    found: str | None

    def setUp(self) -> None:
        """A fake ffmpeg that measures every track loud, found on the path."""
        super().setUp()
        self.ffmpeg = FakeFfmpeg()
        self.found = "ffmpeg"

    def _find(self) -> str | None:
        """The ffmpeg this test's lookup finds, or None."""
        return self.found

    def main(self, *args: str, mode: str = "silence") -> Result:
        """Run `main` in *mode* with *args* and ``--library``; the code and the output."""
        argv = ["--mode", mode, *args, "--library", str(self.library)]
        seams = sweep_silence.SilenceSeams(self._find, self.ffmpeg)
        wiring = sweep.Wiring(
            sf.FakeAlbumTool(), sf.FakeSleep(), sf.fixed_now, silence=seams
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = sweep.main(argv, wiring=wiring)
            except SystemExit as stop:
                code = int(stop.code or 0)
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def lists(self) -> list[Path]:
        """Every silence list in the state directory."""
        return sorted((self.state / "lyricsmith" / "silence").glob("silent-*.tsv"))

    def the_list(self) -> Path:
        """The one silence list in the state directory."""
        (path,) = self.lists()
        return path

    def list_lines(self, path: Path | None = None) -> list[str]:
        """The lines of the list at *path*, the one list when None, header dropped."""
        text = (path or self.the_list()).read_bytes().decode("utf-8")
        return text.splitlines()[1:]
