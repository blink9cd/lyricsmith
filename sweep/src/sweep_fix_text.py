"""The fix pass's albums: those of the library holding a record in the transcript store.

``sync --heard-dir`` keeps a transcript for each track whose untimed text it found not
sung, and the album tool's ``--mode fix-text`` fixes those texts one album at a time. The
fix pass visits only the albums worth a child: each album of the library's walk, links
dropped as for a run (`contained_albums`), one of whose tracks, found by the album tool's
own discovery, has a record. A record is matched to a track by the album tool's own rule,
by file identity however its path was spelled (`lyrics_heard_store.records_for`), so the
sweep picks exactly the albums whose child would find a record. Each album picked is
surveyed as a run surveys it (`survey_album`), so one holding no track without synced
lyrics is not visited, unless a track holding a record holds a provider's timed text
(`provider_timed`), which the fix takes too: the survey names those tracks
(`AlbumSurvey.timed_records`), set by this pass alone, so the run pass visits what it
did. A record's track already timed by sync, labeled so, is no reason to visit. A track
whose machine-made timed sidecar is not yet embedded (`sweep_survey`) does not count
here: a fix embeds nothing, so that visit is the run pass's. The store's folder must
exist: a missing or unlistable one stops the pass before any album (`HeardDirError`).

The store is only read: every record is kept, and each file it cannot read is one
notice, the rest still used, as the album tool reports it. A record whose track is in no
album of the library is passed over, counted in one notice. Every record's file
identity is read once for the whole library (`lyrics_heard_store.records_by_group`).

**When a done album is visited again.** Each album's survey carries the digest of its
records now (`records_digest`, a SHA-256 over the sorted pairs of each matched record's
path and fingerprint), and a fix entry that ends done carries the digest of its
album's records as the visit left them (`album_digest`, read once its child ended). A
fix-pass album the ledger holds as done stays done only while the two agree
(`unchanged_done`): a record gained, changed or regained since, a text undone and heard
not sung again say, makes it an album to visit again. Nothing here prints.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from lyrics_album import AlbumUnusableError, discover_tracks
from lyrics_model import SyncedLyrics
from lyrics_sidecar import UNREADABLE, plan_sidecars
from lyrics_heard_store import (
    HeardRecord,
    Listing,
    list_records,
    records_by_group,
    records_for,
)
from sweep_ledger import LedgerEntry
from sweep_survey import AlbumSurvey, survey_album


class HeardDirError(Exception):
    """The transcript store's folder is none or unlistable: the fix pass cannot start."""


@dataclass(frozen=True)
class HeardAlbums:
    """The albums holding a record, in walk order, and the records matching none.

    *digests* holds each album's `records_digest`, by its folder, and *timed* each
    album's tracks holding a record and a provider's timed text (`provider_timed`).
    """

    albums: tuple[Path, ...]
    unmatched: int
    digests: Mapping[Path, str] = dataclasses.field(default_factory=dict)
    timed: Mapping[Path, tuple[str, ...]] = dataclasses.field(default_factory=dict)


def records_digest(records: Iterable[HeardRecord]) -> str:
    """The SHA-256 over the sorted (path, fingerprint) pairs of *records*.

    A record gained, lost or changed in fingerprint changes it; the order the records
    were matched in does not. A path is taken as the store keeps it, a lone surrogate
    escaped, so any path digests.
    """
    pairs = sorted((os.fsdecode(record.path), record.fingerprint) for record in records)
    encoded = json.dumps(pairs, ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _listing(heard_dir: Path) -> Listing:
    """Every record in *heard_dir*; `HeardDirError` when it is no folder or unlistable.

    A missing folder is refused too, though the store reads it as empty: a mistyped
    ``--heard-dir`` would otherwise end the pass with nothing visited and exit 0.
    """
    if not heard_dir.is_dir():
        raise HeardDirError(f"the transcript folder is not a folder: {heard_dir}")
    try:
        return list_records(heard_dir)
    except OSError as error:
        raise HeardDirError(
            f"the transcript folder cannot be listed: {heard_dir}: {error}"
        ) from error


def _tracks_by_album(albums: Sequence[Path]) -> dict[Path, tuple[Path, ...]]:
    """Each of *albums* by the tracks the album tool's discovery finds in it.

    An album the discovery finds no MP3 in is no album, and is left out, as the
    survey passes it over.
    """
    tracks: dict[Path, tuple[Path, ...]] = {}
    for album in albums:
        try:
            tracks[album] = tuple(discover_tracks(album).tracks)
        except AlbumUnusableError:
            continue
    return tracks


def provider_timed(track: Path, album: Path) -> bool:
    """True when *track*'s sidecars serve a timed ``.lrc`` no machine labeled.

    Read by the album tool's own sidecar plan, as its fix reads the text it takes. A
    sidecar that cannot be read is False here: the survey counts it unreadable, which
    visits the album on its own.
    """
    try:
        planned = plan_sidecars(track, album)
    except (*UNREADABLE, OSError):
        return False
    return isinstance(planned.lyrics, SyncedLyrics) and planned.label is None


def heard_albums(albums: Sequence[Path], listing: Listing) -> HeardAlbums:
    """Those of *albums* one of whose tracks has a record in *listing*, with digests."""
    found = records_by_group(listing, _tracks_by_album(albums))
    kept = tuple(album for album, records in found.items() if records)
    matched = {record for album in kept for record in found[album].values()}
    unmatched = sum(1 for record in listing.records if record not in matched)
    digests = {album: records_digest(found[album].values()) for album in kept}
    timed = {
        album: tuple(
            track.relative_to(album).as_posix()
            for track in found[album]
            if provider_timed(track, album)
        )
        for album in kept
    }
    return HeardAlbums(kept, unmatched, digests, timed)


def album_digest(album: Path, heard_dir: Path) -> str | None:
    """The `records_digest` of *album*'s records in *heard_dir* now; None when unread.

    Read after the album's fix, so it holds what the fix left. A store or an album
    that cannot be read gives None, which agrees with no survey's digest, so the
    album is visited again rather than passed over on a guess.
    """
    try:
        tracks = discover_tracks(album).tracks
        listing = list_records(heard_dir)
    except (OSError, AlbumUnusableError):
        return None
    return records_digest(records_for(listing, tracks).values())


def unchanged_done(
    done: frozenset[str],
    newest: Mapping[str, LedgerEntry],
    digests: Mapping[str, str | None],
) -> frozenset[str]:
    """Those of *done* whose *newest* entry's digest is the album's in *digests* now.

    *digests* names each album surveyed, as the ledger names it. A done album with
    no digest now, or an entry from before the digest, is visited again.
    """
    return frozenset(
        name
        for name in done
        if name in digests and newest[name].records_digest == digests[name]
    )


def unmatched_notice(count: int) -> str:
    """The notice for *count* records naming no track of the library's albums."""
    named = "record names" if count == 1 else "records name"
    return f"{count} transcript {named} no track of the library's albums; passed over"


def fix_surveys(
    albums: Sequence[Path],
    heard_dir: Path,
    *,
    lang: str,
    notice: Callable[[str], None],
) -> list[AlbumSurvey]:
    """The surveys of those of *albums* holding a record in the store *heard_dir*.

    Each survey carries its album's `records_digest`. *albums* are the library's
    walk, links dropped (`contained_albums`). Each file the store cannot read, and the
    records matching no album, are said through *notice*. Raises `HeardDirError` when the store is no folder or cannot be listed.
    """
    listing = _listing(heard_dir)
    for problem in listing.problems:
        notice(problem)
    found = heard_albums(albums, listing)
    if found.unmatched:
        notice(unmatched_notice(found.unmatched))
    # A fix embeds nothing, so an embed a timed sidecar owes
    # (`AlbumSurvey.unembedded`), labeled or not, is the run pass's alone.
    return [
        dataclasses.replace(
            survey_album(album, lang=lang),
            records_digest=found.digests[album],
            unembedded=(),
            timed_records=found.timed[album],
        )
        for album in found.albums
    ]
