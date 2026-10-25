"""Test-side stand-ins for the run-folder stages, and a stage bundle built around them.

`FakeRunFolder` plays the backup, manifest, run-selection and undo stages: it creates
the run folder, writes a small JSON state file where the manifest goes (so a test can
read the state from disk, as the barrier test does), and keeps every manifest it was
handed. The other stages are the real ones, so a run over a built album
reads and writes real MP3s. `TitleProvider` answers by the query's title, so one table
can serve several tracks differently, and `failing_table` fails every provider in
it. `acquire` is the real album lock, `run_lock`'s,
reached through the entry point's bootstrap. Every lyric here is invented.

Not discovered as a suite (no ``test_`` prefix).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_album  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_run  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_tags  # noqa: E402 - import follows the sys.path bootstrap above
import run_lock  # noqa: E402 - reachable through the entry point's bootstrap

#: The run id every fake run folder takes.
RUN_ID = "20261014T153012Z"
#: When every fake run started.
STARTED = dt.datetime(2026, 10, 14, 15, 30, 12, tzinfo=dt.UTC)
#: The language every run in these suites writes.
LANG = "eng"
#: The deadline every run in these suites fetches with.
DEADLINE_S = 5.0


@dataclass
class FakeRunFolder:
    """The run-folder stages, in memory, with the state written where the manifest goes.

    *fail* names the album-relative paths whose backup fails; those tracks come back
    with no ``before`` block, as a real failed backup does.
    """

    run_id: str = RUN_ID
    fail: frozenset[str] = frozenset()
    manifests: list[lr.Manifest] = field(default_factory=list)
    backed_up: list[tuple[Path, tuple[Path, ...]]] = field(default_factory=list)
    frames: dict[str, lm.FrameRecord] = field(default_factory=dict)
    undo_calls: list[tuple[Path, str, str | None]] = field(default_factory=list)
    undo_report: lr.UndoReport | None = None

    def _before(self, rel: str, tags: lm.TrackTags) -> lr.TrackBefore:
        """A ``before`` block holding *tags*' frames, each kept by its export name."""
        exports = []
        for index, frame in enumerate(tags.frames, start=1):
            name = f"frames/{rel}.{index}"
            self.frames[name] = frame
            exports.append(
                lr.FrameExport(
                    frame.kind,
                    frame.lang,
                    frame.desc,
                    frame.encoding,
                    frame.format,
                    frame.type,
                    name,
                )
            )
        return lr.TrackBefore(tuple(exports), ())

    def _track(self, album: Path, path: Path) -> lr.ManifestTrack:
        """One track's manifest entry, backed up unless named in *fail*."""
        rel = path.relative_to(album).as_posix()
        if rel in self.fail:
            return lr.ManifestTrack(rel, None, None)
        tags = lyrics_tags.read_tags(path)
        return lr.ManifestTrack(rel, tags.id3_version, self._before(rel, tags))

    def back_up_album(
        self,
        album: Path,
        tracks: Sequence[Path],
        *,
        lang: str,
        genius: lr.GeniusState,
        started: dt.datetime,
        bandcamp: lr.BandcampState = lr.BandcampState.OFF,
        youtube_music: lr.CoolingState = lr.CoolingState.OFF,
        tekstowo: lr.CoolingState = lr.CoolingState.OFF,
        silence_check: lr.SilenceCheck = lr.SilenceCheck.OFF,
    ) -> lr.Manifest:
        """Create the run folder, record each track's frames, write the state."""
        self.backed_up.append((album, tuple(tracks)))
        run_dir = lr.run_folder(album, self.run_id)
        run_dir.mkdir(parents=True)
        entries = tuple(self._track(album, path) for path in tracks)
        state = lr.RunState.BACKED_UP
        manifest = lr.Manifest(
            self.run_id,
            lang,
            state,
            genius,
            entries,
            bandcamp=bandcamp,
            youtube_music=youtube_music,
            tekstowo=tekstowo,
            silence_check=silence_check,
        )
        self.write_manifest(run_dir, manifest)
        return manifest

    def write_manifest(self, run_dir: Path, manifest: lr.Manifest) -> None:
        """Keep *manifest*, and write its state and outcomes where the real one goes."""
        self.manifests.append(manifest)
        state = {
            "state": str(manifest.state),
            "after": [
                None if t.after is None else str(t.after.status)
                for t in manifest.tracks
            ],
        }
        (run_dir / lr.MANIFEST_NAME).write_text(
            json.dumps(state), encoding="utf-8", newline=""
        )

    def select_run(self, album: Path, run_id: str | None) -> Path:
        """The newest run folder under *album*, or the one named *run_id*."""
        base = album / lr.RUN_FOLDER_NAME
        runs = sorted(p for p in base.glob("*") if p.is_dir()) if base.is_dir() else []
        chosen = [run for run in runs if run_id in (None, run.name)]
        if not chosen:
            raise lm.NoRunError(f"no run folder under {album}")
        return chosen[-1]

    def read_manifest(self, run_dir: Path) -> lr.Manifest:
        """The last manifest written."""
        return self.manifests[-1]

    def load_before_frames(
        self, run_dir: Path, before: lr.TrackBefore
    ) -> tuple[lm.FrameRecord, ...]:
        """The frames *before* names, as they were recorded."""
        return tuple(self.frames[export.file] for export in before.frames)

    def undo_run(
        self,
        album: Path,
        run_dir: Path,
        manifest: lr.Manifest,
        *,
        newest: bool,
        track: str | None,
    ) -> lr.UndoReport:
        """Record the call by run id; answer the canned report, or every track undone."""
        self.undo_calls.append((album, run_dir.name, track))
        if self.undo_report is not None:
            return self.undo_report
        undone = (
            lr.UndoTrackResult(t.path, lr.UndoStatus.UNDONE) for t in manifest.tracks
        )
        return lr.UndoReport(manifest.run_id, newest, tuple(undone))

    def _on_disk(self, album: Path) -> dict[str, object]:
        """What the last write left in the run folder."""
        path = lr.run_folder(album, self.run_id) / lr.MANIFEST_NAME
        written: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        return written

    def state_on_disk(self, album: Path) -> str:
        """The state the last write left in the run folder."""
        return str(self._on_disk(album)["state"])

    def afters_on_disk(self, album: Path) -> object:
        """Each track's outcome status the last write left, None for one not reached."""
        return self._on_disk(album)["after"]


def make_stages(folder: FakeRunFolder, **overrides: object) -> lyrics_stages.Stages:
    """The real stages, *folder*'s fakes for the run-folder ones, *overrides* on top.

    Built from the entry point's own real stages, so a stage added there arrives here
    as the real function without being listed twice.
    """
    stages = dataclasses.replace(
        album.real_wiring().stages,
        back_up=folder.back_up_album,
        write_manifest=folder.write_manifest,
        select_run=folder.select_run,
        read_manifest=folder.read_manifest,
        load_before=folder.load_before_frames,
        undo=folder.undo_run,
    )
    return dataclasses.replace(stages, **overrides)  # type: ignore[arg-type] # a test's override of one stage


@dataclass
class TitleProvider:
    """A provider that answers by the query's title, and records every query.

    An answer that is an exception is raised instead of returned, so one provider
    can refuse one title and answer another.
    """

    answers: Mapping[str, str | Exception]
    calls: list[lm.TrackQuery] = field(default_factory=list)

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """The answer for *query*'s title, or nothing; raise it when it is an error."""
        self.calls.append(query)
        answer = self.answers.get(query.title)
        if isinstance(answer, Exception):
            raise answer
        return answer


def table(
    synced: Mapping[str, str] | None = None, plain: Mapping[str, str] | None = None
) -> dict[lm.ProviderKey, lm.Provider]:
    """A one-provider table: LRCLIB answering *synced* and *plain* by title."""
    synced_key = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.SYNCED)
    plain_key = lm.ProviderKey(lm.Source.LRCLIB, lm.LyricsKind.UNSYNCED)
    return {
        synced_key: TitleProvider(synced or {}),
        plain_key: TitleProvider(plain or {}),
    }


def failing_table() -> dict[lm.ProviderKey, lm.Provider]:
    """`table`'s keys, each provider raising, so every provider asked fails."""
    broken = fixtures.raising(OSError("refused"))
    return {key: broken for key in table()}


def options(genius: lr.GeniusState = lr.GeniusState.ABSENT) -> lyrics_run.RunOptions:
    """The run options every suite here uses."""
    return lyrics_run.RunOptions(lang=LANG, deadline_s=DEADLINE_S, genius=genius)


#: The album lock every run in these suites takes: the real one.
acquire = run_lock.acquire


def lock_path(album: Path) -> Path:
    """Where *album*'s lock file lives."""
    return album / lr.RUN_FOLDER_NAME / lyrics_album.LOCK_NAME


def lock_held(album: Path) -> bool:
    """Whether some holder, this process included, has *album*'s lock."""
    return run_lock.is_held(lock_path(album))
