"""Test-side builders for the manifest suites: a full manifest and its JSON document.

`FULL` sets every field the codec writes, so a field it drops shows as a difference;
`document` is that manifest as the JSON object the codec writes, for a suite to edit one
value of and `encode` back to bytes. Not discovered as a suite (no ``test_`` prefix).
Every lyric here is invented.
"""

from __future__ import annotations

import json
from typing import cast

import lyrics_manifest as lmf
import lyrics_model as lm
import lyrics_records as lr
import lyrics_refusal as lrf

CHECK = lr.CheckResult(
    flags=(lr.CheckReason(lr.CheckRule.PAST_END, "last line 4:10, track 3:55"),),
    skips=(lr.CheckReason(lr.CheckRule.DURATION_UNREADABLE, "no MPEG frame"),),
)
#: Every ``after`` field set, so a field the codec drops shows as a difference.
FULL_AFTER = lr.TrackOutcome(
    status=lr.OutcomeStatus.WRITTEN,
    source=lm.Source.LRCLIB,
    kind=lm.LyricsKind.SYNCED,
    query=(lm.QueryMarker.FILENAME, lm.QueryMarker.STRIPPED),
    sidecar_written="01 Café.lrc",
    sidecars_removed=("01 Café.txt",),
    sidecars_renamed=(lm.SidecarRename("01 Café.lrc", "01 Café.txt"),),
    frames_written=True,
    sidecar_hashes=(lr.SidecarHash("01 Café.txt", "0123456789abcdef" * 4),),
    check=CHECK,
    error=lr.StageError(lr.ErrorStage.CHECK, "OSError", "could not read it"),
    refusals=(
        lr.ProviderRefusal(
            lm.Source.LRCLIB,
            lm.LyricsKind.UNSYNCED,
            "HTTP 503 after 3 retries",
            lrf.Refusal.OVERLOADED,
            status=503,
            retries=3,
            retry_after_s=120.0,
            server="edge-cache",
        ),
        lr.ProviderRefusal(
            lm.Source.NETEASE,
            lm.LyricsKind.SYNCED,
            "code -460",
            lrf.Refusal.UNRECOGNIZED,
        ),
        lr.ProviderRefusal(
            lm.Source.GENIUS, lm.LyricsKind.UNSYNCED, "timeout", lrf.Refusal.UNREACHABLE
        ),
    ),
    refetch_implausible=True,
    held=lr.HeldBefore.UNTIMED,
    origin="CD1/02 Café.mp3",
)
BEFORE = lr.TrackBefore(
    frames=(
        lr.FrameExport(
            lm.FrameKind.USLT, "eng", "", 3, None, None, "frames/CD1/01.uslt.1.txt"
        ),
        lr.FrameExport(
            lm.FrameKind.SYLT,
            "deu",
            "alt",
            1,
            1,
            1,
            "frames/CD1/01.sylt.1.lrc",
            "frames/CD1/01.sylt.1.frames.json",
        ),
    ),
    sidecars=(lr.SidecarCopy("01 Café.txt", "sidecars/CD1/01 Café.txt"),),
)
FULL = lr.Manifest(
    run_id="20261014T153012Z-2",
    lang="eng",
    state=lr.RunState.COMPLETE,
    genius=lr.GeniusState.CONFIGURED,
    tracks=(
        lr.ManifestTrack(
            "CD1/01 Café.mp3",
            (2, 4),
            BEFORE,
            FULL_AFTER,
            True,
            (".lyricsmith/20261014T153012Z-2/undo/CD1/01 Café.lrc",),
        ),
        lr.ManifestTrack(
            "02.mp3",
            None,
            None,
            lr.TrackOutcome(
                lr.OutcomeStatus.ERROR,
                error=lr.StageError(lr.ErrorStage.BACKUP, "OSError", "gone"),
            ),
        ),
        lr.ManifestTrack("03.mp3", (2, 3), lr.TrackBefore((), ())),
    ),
    bandcamp=lr.BandcampState.ON,
)


def document() -> dict[str, object]:
    """The full manifest as the JSON object the codec writes."""
    return cast(dict[str, object], json.loads(lmf.manifest_bytes(FULL)))


def encode(value: object) -> bytes:
    """*value*, a manifest document, as manifest bytes."""
    return json.dumps(value).encode("utf-8")


def first_track(doc: dict[str, object]) -> dict[str, object]:
    """The first track of manifest document *doc*, to edit in place."""
    return cast(list[dict[str, object]], doc["tracks"])[0]


def sidecar_record(doc: dict[str, object]) -> dict[str, object]:
    """Manifest document *doc*'s first backed-up sidecar record, to edit in place."""
    before = cast(dict[str, list[dict[str, object]]], first_track(doc)["before"])
    return before["sidecars"][0]
