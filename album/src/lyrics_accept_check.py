"""`--mode accept`'s check: what ``--mode check`` says of each track's current lyrics.

Check reads a run's backup, so accept runs it before anything is deleted. A track's
runs are walked newest first, over each readable run whose entry for it holds an
outcome and is not undone, and each of those entries is checked, or not, exactly as
`check_run` would (`check_tracks`). The walk stops after the first run that changed
the track's lyrics (`changed_lyrics`): what that run left is what the track holds, so
no older run's lyrics are current. A newer run that left them alone, finding nothing,
keeping what the track had, or failing before it wrote, does not hide an older run's
written lyrics; and a newer UNCHANGED is checked beside the older WRITTEN it found in
place, since only the older backup holds the track's original. A FLAG is listed with
check's own summary, and a check that could not run is listed as `CHECK_FAILED`, since
an unknown is shown, never silent. SKIP and PASS list nothing. A run whose manifest did
not read is skipped here: the judgment already refuses the album for it.

Pure apart from what *stages* reads; nothing is written.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from lyrics_accept_judge import AtRisk, AtRiskReason, Contents, RunEntry
from lyrics_modes import CheckedTrack, check_tracks
from lyrics_outcomes import changed_lyrics
from lyrics_records import CheckVerdict, Manifest, ManifestTrack
from lyrics_run_folders import run_folder
from lyrics_stages import Stages

#: One run's entry for one track: the run, its manifest and the track's entry.
_Entry = tuple[RunEntry, Manifest, ManifestTrack]


def check_flags(album: Path, contents: Contents, stages: Stages) -> tuple[AtRisk, ...]:
    """Each track of *album* whose current lyrics' check flags or errors, by track."""
    walked = [entry for entries in _entries_by_track(contents) for entry in entries]
    flagged: list[AtRisk] = []
    for run_id, manifest, tracks in _by_run(walked):
        run_dir = run_folder(album, run_id)
        checked = check_tracks(album, run_dir, manifest, tracks, stages)
        flagged.extend(_flagged(run_id, checked))
    # Stable: one track's runs stay in run order, oldest first.
    flagged.sort(key=lambda risk: risk.track)
    return tuple(flagged)


def _entries_by_track(contents: Contents) -> Iterator[tuple[_Entry, ...]]:
    """Each track's entries to check: newest first, through the first that changed it."""
    for entries in _candidates(contents).values():
        yield tuple(_walk(entries))


def _candidates(contents: Contents) -> dict[str, list[_Entry]]:
    """Each track's readable entries holding an outcome and not undone, newest first."""
    by_track: dict[str, list[_Entry]] = {}
    for run in sorted(contents.runs, key=lambda r: r.order, reverse=True):
        manifest = run.manifest
        if manifest is None:
            continue
        for track in manifest.tracks:
            if track.after is not None and not track.undone:
                by_track.setdefault(track.path, []).append((run, manifest, track))
    return by_track


def _walk(entries: list[_Entry]) -> Iterator[_Entry]:
    """*entries*, newest first, up to and including the first that changed the lyrics."""
    for entry in entries:
        yield entry
        after = entry[2].after
        # _candidates keeps only entries with an outcome.
        assert after is not None
        if changed_lyrics(after):
            return


def _by_run(
    walked: list[_Entry],
) -> Iterator[tuple[str, Manifest, tuple[ManifestTrack, ...]]]:
    """*walked*'s tracks grouped by run, runs in run order, tracks in manifest order."""
    runs = {run.name: (run, manifest) for run, manifest, _ in walked}
    for name, (_, manifest) in sorted(runs.items(), key=lambda kv: kv[1][0].order):
        picked = {track.path for run, _, track in walked if run.name == name}
        yield name, manifest, tuple(t for t in manifest.tracks if t.path in picked)


def _flagged(run_id: str, checked: tuple[CheckedTrack, ...]) -> Iterator[AtRisk]:
    """Each of *checked* whose check flagged, or raised, as an at-risk track."""
    for track in checked:
        if track.error is not None:
            message = track.error.message
            yield AtRisk(track.path, run_id, AtRiskReason.CHECK_FAILED, message)
        elif track.check is not None and track.check.verdict is CheckVerdict.FLAG:
            summary = track.check.summary()
            yield AtRisk(track.path, run_id, AtRiskReason.CHECK_FLAG, summary)
