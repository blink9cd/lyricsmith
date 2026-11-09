"""Builders for the draw's suites: invented tracks of each timing, manifests and a ledger.

A track is an empty MP3 (no ID3 tag) with whatever sidecar makes its timing: an
unlabeled timed ``.lrc`` is provider-timed, one labeled ``[by:machine timed]`` is
machine-timed, a ``.txt`` is untimed, and frames holding a timed text with no ``.lrc``
beside them are frames-only. A manifest is written through the album tool's own records
and writer, its tracks named as the test names them, so the draw's reader round-trips
what the album tool really writes. Every name and lyric here is invented.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with this
directory on ``sys.path``, after importing the entry point, which puts the album tool's
own modules on the path.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import lyrics_manifest
import lyrics_model as lm
import lyrics_outcomes
import lyrics_records as lr
import sweep_fixtures as sf
import sweep_ledger as sl

#: An invented timed ``.lrc`` a machine labeled, as ``sync`` labels its own.
MACHINE_LRC = "[by:machine timed]\n[00:01.000]A kite above the harbor wall\n"
#: Invented run ids, oldest first, in the album tool's run id format.
OLD_RUN, NEW_RUN, NEWEST_RUN = (
    "20261001T080000Z",
    "20261002T080000Z",
    "20261003T080000Z",
)


def provider_timed(album: Path, name: str) -> Path:
    """An invented track whose timed ``.lrc`` no machine labeled."""
    return sf.add_track(album, name, lrc=sf.SYNCED_LRC)


def machine_timed(album: Path, name: str) -> Path:
    """An invented track whose timed ``.lrc`` a machine labeled."""
    return sf.add_track(album, name, lrc=MACHINE_LRC)


def frames_only(album: Path, name: str) -> Path:
    """An invented track whose timing is in its frames alone, no ``.lrc`` beside it."""
    mp3 = sf.embed_sidecar(provider_timed(album, name))
    mp3.with_suffix(".lrc").unlink()
    return mp3


def untimed(album: Path, name: str) -> Path:
    """An invented track holding an untimed ``.txt`` alone."""
    return sf.add_track(album, name, txt=sf.UNTIMED_TXT)


def synced(source: lm.Source | None) -> lr.TrackOutcome:
    """A synced write from *source*, as a run records one."""
    return lr.TrackOutcome(
        lr.OutcomeStatus.WRITTEN, source=source, kind=lm.LyricsKind.SYNCED
    )


def unchanged(source: lm.Source) -> lr.TrackOutcome:
    """Synced lyrics from *source* a run found the track already held."""
    return lr.TrackOutcome(
        lr.OutcomeStatus.UNCHANGED, source=source, kind=lm.LyricsKind.SYNCED
    )


def untimed_write(source: lm.Source) -> lr.TrackOutcome:
    """An untimed write from *source*."""
    return lr.TrackOutcome(
        lr.OutcomeStatus.WRITTEN, source=source, kind=lm.LyricsKind.UNSYNCED
    )


def status(value: lr.OutcomeStatus) -> lr.TrackOutcome:
    """An outcome of *value* alone."""
    return lr.TrackOutcome(value)


def wrong_text_removed() -> lr.TrackOutcome:
    """A fix's removal of a wrong text, no sung text found."""
    return lr.TrackOutcome(
        lr.OutcomeStatus.REMOVED, implausible=lyrics_outcomes.WRONG_TEXT
    )


def withheld_removal() -> lr.TrackOutcome:
    """A fix's kept track whose removal it withheld on an album that looks spoken."""
    return lr.TrackOutcome(
        lr.OutcomeStatus.KEPT_EXISTING, withheld="spoken word (genre Comedy)"
    )


def sibling_write(
    origin: str | None, source: lm.Source = lm.Source.ALBUM_TEXT
) -> lr.TrackOutcome:
    """A fix's write of another track's text, *origin* the track it came from.

    *source* is the provider a run recorded for that track, or `Source.ALBUM_TEXT`
    when none did, as the album tool's fix writes it; *origin* None is a manifest
    written before outcomes named the track a text came from.
    """
    return lr.TrackOutcome(
        lr.OutcomeStatus.WRITTEN,
        source=source,
        kind=lm.LyricsKind.UNSYNCED,
        origin=origin,
    )


def entry(
    path: str, after: lr.TrackOutcome | None, *, undone: bool = False
) -> lr.ManifestTrack:
    """One manifest track at album-relative *path* that ended as *after*."""
    return lr.ManifestTrack(path, (2, 4), lr.TrackBefore((), ()), after, undone)


def write_manifest(
    album: Path, run_id: str, tracks: Sequence[lr.ManifestTrack]
) -> Path:
    """Write run *run_id*'s complete manifest of *tracks* under *album*; its folder."""
    run_dir = lr.run_folder(album, run_id)
    run_dir.mkdir(parents=True)
    manifest = lr.Manifest(
        run_id, "eng", lr.RunState.COMPLETE, lr.GeniusState.ABSENT, tuple(tracks)
    )
    lyrics_manifest.write_manifest(run_dir, manifest)
    return run_dir


def ledger_entry(album: str, pass_: sl.SweepPass) -> sl.LedgerEntry:
    """A done visit of *album*, library-relative, in *pass_*."""
    return sl.LedgerEntry(
        album, None, 0, sl.Outcome.DONE, "2026-10-19T09:30:00+00:00", pass_=pass_
    )


def write_ledger(library: Path, *entries: sl.LedgerEntry) -> Path:
    """Append *entries* to *library*'s ledger, where the environment puts it; its path."""
    ledger = sl.ledger_path(library, os.environ)
    for item in entries:
        sf.append_line(ledger, sl.encode_record(item))
    return ledger


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root* by its relative path, with its bytes: a snapshot."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
