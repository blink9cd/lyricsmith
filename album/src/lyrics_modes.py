"""The other ways through an album: the dry run, the check of a finished run, and undo.

`plan_album` says what a run would do with each track, changing nothing: no lock, no
backup, no run folder. `check_run` re-judges the tracks whose lyrics a run accepted,
reading only. `undo_album` chooses the run, then undoes it holding the album lock. Each
track's failure is recorded against it, as in a run. Nothing here prints.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lyrics_album import AcquireLock, album_lock
from lyrics_genre import NO_LYRICS_GENRES, no_lyrics_genre
from lyrics_inventory import EmbeddedLyrics, TrackLyricsKind, embedded_lyrics
from lyrics_judge import recheck_written
from lyrics_model import (
    Lyrics,
    NoRunError,
    QueryMarker,
    Source,
    SyncedLyrics,
    TrackTags,
    lyrics_from_frames,
)
from lyrics_outcomes import ACCEPTED
from lyrics_records import (
    CheckResult,
    ErrorStage,
    Manifest,
    ManifestTrack,
    StageError,
    UndoReport,
)
from lyrics_placeholder import unless_placeholder
from lyrics_refetch import RefetchTarget, afresh_target
from lyrics_run import track_query
from lyrics_sidecar import read_bounded
from lyrics_slots import Slot, check_slot, sidecar_entry, sidecar_paths
from lyrics_stages import Stages

#: What a dry run says each track would use.
PLAN_SIDECAR_SYNCED = "sidecar (synced)"
PLAN_SIDECAR_UNSYNCED = "sidecar (unsynced)"
PLAN_FETCH_OVER_TXT = "fetch synced, else sidecar (unsynced)"
PLAN_FRAMES_SYNCED = "frames (synced)"
PLAN_FRAMES_UNSYNCED = "frames (unsynced)"
PLAN_FETCH_OVER_FRAMES = "fetch synced, else frames (unsynced)"
#: A machine-labeled .lrc is still asked of the synced steps; offline it serves itself.
#: Each names its label's source, so a new label needs no plan string of its own.
PLAN_LABELED = "{source} (synced), providers asked"
PLAN_LABELED_OFFLINE = "{source} (synced)"
PLAN_FETCH = "fetch"
#: A marked track's plain fetch: its miss ends no lyrics expected (lyrics_genre).
PLAN_FETCH_NO_LYRICS = "fetch (no lyrics expected if none)"
#: A refetch's plan for an untimed-only track: asked again, kept as it is unanswered.
PLAN_REFETCH = "fetch, else keep existing"
#: The plan for one whose implausible old lyrics are removed if nothing answers.
PLAN_REMOVE = "fetch, else remove"
PLAN_NOTHING_OFFLINE = "nothing (offline, no sidecar or frames)"
#: The plan column of a track the dry run could not read.
PLAN_UNKNOWN = "-"


def _stage_error(stage: ErrorStage, error: Exception) -> StageError:
    """*error* recorded against *stage*, its message or else its type's name."""
    name = type(error).__name__
    return StageError(stage, name, str(error) or name)


# --- The dry run ---------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedTrack:
    """What a run would do with one track: its plan, query markers, or why it cannot.

    *links_ignored* names each sidecar link the run would leave unread because it
    leads out of the album.
    """

    path: str
    plan: str
    markers: tuple[QueryMarker, ...] = ()
    error: StageError | None = None
    links_ignored: tuple[str, ...] = ()


def plan_labeled(source: Source, *, offline: bool) -> str:
    """The dry-run plan for a timed sidecar whose header labels it *source*."""
    template = PLAN_LABELED_OFFLINE if offline else PLAN_LABELED
    return template.format(source=source.label)


def _plan_for_label(
    held: Lyrics | None,
    label: Source | None,
    embedded: EmbeddedLyrics,
    *,
    offline: bool,
) -> str | None:
    """The plan for a timed sidecar its header labels, as the fetch takes it; else None."""
    if label is None or not isinstance(held, SyncedLyrics):
        return None
    if embedded.synced_besides(held):
        return PLAN_FRAMES_SYNCED
    return plan_labeled(label, offline=offline)


def _plan_for(
    sidecar: Lyrics | None,
    embedded: EmbeddedLyrics,
    *,
    offline: bool,
    label: Source | None = None,
) -> str:
    """The plan for a track whose sidecars hold *sidecar*, its frames *embedded*.

    In the fetch's order: a timed sidecar, synced frames, a ``.txt``, untimed frames.
    An unlabeled sidecar of the frames' own lyrics counts as the frames, and a
    placeholder held holds no lyrics, as in the fetch. A timed sidecar its header labels
    plans as the fetch takes it, before that rule: frames holding its own lyrics never
    take the track from it.
    """
    sidecar, embedded = unless_placeholder(sidecar), embedded.without_placeholders()
    labeled = _plan_for_label(sidecar, label, embedded, offline=offline)
    if labeled is not None:
        return labeled
    held, frames = embedded.serving(sidecar), embedded.kind
    if isinstance(held, SyncedLyrics):
        return PLAN_SIDECAR_SYNCED
    if frames is TrackLyricsKind.SYNCED:
        return PLAN_FRAMES_SYNCED
    if held is not None:
        return PLAN_SIDECAR_UNSYNCED if offline else PLAN_FETCH_OVER_TXT
    if frames is TrackLyricsKind.UNTIMED:
        return PLAN_FRAMES_UNSYNCED if offline else PLAN_FETCH_OVER_FRAMES
    return PLAN_NOTHING_OFFLINE if offline else PLAN_FETCH


def _afresh_plan(target: RefetchTarget | None) -> str:
    """A track asked afresh: a fresh run's fetch, or a refetch's, kept or removed.

    A refetch that would keep the track names its unreadable ``.txt``, if any; one
    that would remove its old lyrics names why they are implausible.
    """
    if target is None:
        return PLAN_FETCH
    if target.implausible is not None:
        return f"{PLAN_REMOVE} (implausible: {target.implausible})"
    reasons = (PLAN_REFETCH, target.unreadable)
    return "; ".join(reason for reason in reasons if reason is not None)


def _expecting(plan: str, *, marked: bool) -> str:
    """A marked track's plain fetch, which ends no lyrics expected when nothing answers."""
    return PLAN_FETCH_NO_LYRICS if marked and plan == PLAN_FETCH else plan


def _holds_lyrics(path: Path, tags: TrackTags, lang: str) -> bool:
    """Whether a fresh miss would keep *path*'s lyrics, as the run's miss decides it.

    Its frames' lyrics count, or any sidecar the backup would record: a file or a
    link under either name, whatever it holds.
    """
    if lyrics_from_frames(tags.frames, lang) is not None:
        return True
    return any(sidecar_entry(name) is not Slot.ABSENT for name in sidecar_paths(path))


def _refuse_as_the_backup(path: Path) -> None:
    """Refuse, as the backup would, the ``.txt`` file beside *path* past its byte cap.

    The backup copies each sidecar file through `read_bounded`, so a refetch never
    asks again a track whose ``.txt`` is past that cap: the run's error is the
    backup's, and the dry run shows the sidecar error a dry run without the option
    shows. A link is not copied, so it is not refused.
    """
    _lrc, txt = sidecar_paths(path)
    if sidecar_entry(txt) is Slot.FILE:
        read_bounded(txt, txt.name)


def _plan_track(
    album: Path,
    path: Path,
    stages: Stages,
    *,
    offline: bool,
    fresh: bool,
    lang: str,
    refetch_untimed: bool = False,
    refetch_implausible: bool = False,
    no_lyrics_genres: tuple[str, ...] = NO_LYRICS_GENRES,
) -> PlannedTrack:
    """One track's plan, read without changing anything; a failure is its error.

    Every run reads the sidecars and frames to tell a track whose only lyrics are
    placeholders (`lyrics_refetch.placeholder_target`), planned as a fresh run plans
    every track but as one whose miss removes them. Under *fresh* the sidecars are
    otherwise never read, and the frames only for whether a miss would keep them: a
    fresh run reads the sidecars only to judge an untimed answer, which a dry run
    never has. Under *refetch_untimed* they are read to tell an untimed-only track,
    and under *refetch_implausible* a track serving implausible lyrics
    (`lyrics_refetch.afresh_target`), which is then planned as a fresh run plans
    every track, unless its unreadable ``.txt`` is one the backup would refuse, which
    is its sidecar error. For such a track the MP3 is checked for a tag version or a
    write its embed would refuse, then the sidecar names for the folder, pipe or
    device its strip would fail on, in the run's order,
    so a track with both previews the error the run would stop on.
    """
    rel, stage = path.relative_to(album).as_posix(), ErrorStage.FETCH
    try:
        tags = stages.read_tags(path)
        marked = no_lyrics_genre(tags.genres, no_lyrics_genres) is not None
        query = track_query(tags, path)
        stage = ErrorStage.SIDECAR
        target = afresh_target(
            stages,
            album,
            path,
            tags,
            lang,
            refetch_untimed=refetch_untimed,
            refetch_implausible=refetch_implausible,
        )
        if target is not None and target.unreadable is not None:
            _refuse_as_the_backup(path)
        if fresh or target is not None:
            # The run's own order: it refuses an embed it cannot save before it strips.
            stage = ErrorStage.EMBED
            stages.check_writable(path)
            stage = ErrorStage.STRIP
            check_slot(path)
            # A miss keeps what the track holds, so only an empty one expects none.
            marked = marked and not _holds_lyrics(path, tags, lang)
            return PlannedTrack(
                rel, _expecting(_afresh_plan(target), marked=marked), query.markers
            )
        sidecar = stages.normalize_sidecars(path, album=album, apply=False)
        # Inside the boundary: a frame over the cap ends this track's plan alone.
        embedded = embedded_lyrics(tags.frames, lang)
    # The per-track boundary, as in a run: the other tracks are still planned.
    except Exception as error:
        return PlannedTrack(rel, PLAN_UNKNOWN, error=_stage_error(stage, error))
    plan = _expecting(
        _plan_for(sidecar.lyrics, embedded, offline=offline, label=sidecar.label),
        marked=marked,
    )
    return PlannedTrack(rel, plan, query.markers, links_ignored=sidecar.links_ignored)


def plan_album(
    album: Path,
    tracks: Sequence[Path],
    stages: Stages,
    *,
    offline: bool,
    lang: str,
    fresh: bool = False,
    refetch_untimed: bool = False,
    refetch_implausible: bool = False,
    no_lyrics_genres: tuple[str, ...] = NO_LYRICS_GENRES,
) -> tuple[PlannedTrack, ...]:
    """The dry run: what a run would do with each track, with nothing written.

    No lock, no backup and no run folder; sidecars are planned, not normalized, and
    a track's lyrics frames planned the way its sidecars are. Under
    *fresh* every track that can be read plans a fetch, as a fresh run asks the
    providers whatever its sidecars hold; a sidecar name holding a folder, a pipe or
    a device is that track's strip error, and a tag version or an MP3 the embed
    cannot write its embed error, as each would be in the run. Under
    *refetch_untimed* each untimed-only track is planned so too, as asked again and,
    when unanswered, kept, or removed when its old lyrics are implausible; under
    *refetch_implausible* each track serving implausible lyrics is planned so too, and
    removed when unanswered. *lang*
    ranks the frames as a run's does, so it decides whether a sidecar holds their
    lyrics. A track *no_lyrics_genres* marks plans
    ``fetch (no lyrics expected if none)`` where it would plan a plain fetch, unless
    a fresh miss would keep the lyrics frames or sidecar it holds.
    """
    return tuple(
        _plan_track(
            album,
            path,
            stages,
            offline=offline,
            fresh=fresh,
            lang=lang,
            refetch_untimed=refetch_untimed,
            refetch_implausible=refetch_implausible,
            no_lyrics_genres=no_lyrics_genres,
        )
        for path in tracks
    )


# --- The check of a finished run -----------------------------------------------------


def _read_chosen(stages: Stages, album: Path, run_dir: Path) -> Manifest:
    """The manifest of the run chosen in *run_dir*; `NoRunError` once its folder is gone.

    The run is chosen before its manifest is read (for undo, before the lock), so
    another process can remove the folder in between: that run no longer exists.
    """
    try:
        return stages.read_manifest(run_dir)
    except FileNotFoundError:
        if run_dir.is_dir():
            raise
        raise NoRunError(f"no run {run_dir.name!r} under {album}") from None


@dataclass(frozen=True)
class CheckedTrack:
    """One track of a checked run: whether the run wrote it, and the verdict or error."""

    path: str
    written: bool
    check: CheckResult | None = None
    error: StageError | None = None


@dataclass(frozen=True)
class CheckReport:
    """A check of one run's tracks."""

    run_id: str
    tracks: tuple[CheckedTrack, ...]

    @property
    def has_errors(self) -> bool:
        """True when any track's check raised."""
        return any(track.error is not None for track in self.tracks)


def _check_one(
    stages: Stages, album: Path, run_dir: Path, track: ManifestTrack, lang: str
) -> CheckedTrack:
    """Check *track* when the run accepted its lyrics and it is not undone.

    A track the run wrote, or left unchanged because it already held the lyrics, is
    checked; a failure is its error. An undone track holds its backup again, not what
    the run left, so it is not checked, as a track whose lyrics the run never
    accepted is not. A track the run served from its own frames is judged by the
    frame the run would serve it from now (`recheck_written`), as the run judged it.
    """
    after, before = track.after, track.before
    if after is None or after.status not in ACCEPTED or before is None or track.undone:
        return CheckedTrack(track.path, False)
    from_frames = after.source is Source.FRAMES
    try:
        result = recheck_written(
            stages,
            album,
            album / track.path,
            run_dir,
            before,
            lang,
            from_frames=from_frames,
        )
    # The per-track boundary: a track gone since the run fails alone.
    except Exception as error:
        return CheckedTrack(
            track.path, True, error=_stage_error(ErrorStage.CHECK, error)
        )
    return CheckedTrack(track.path, True, result)


def check_run(
    album: Path, *, run_id: str | None, track: str | None, stages: Stages
) -> CheckReport:
    """Re-run the check on the tracks a run accepted lyrics for, newest or *run_id*.

    *track* is an album-relative path. Read only, so no lock is taken. Raise
    `NoRunError` when there is no such run and `TrackNotInRunError` for a track it
    does not list.
    """
    run_dir = stages.select_run(album, run_id)
    manifest = _read_chosen(stages, album, run_dir)
    chosen = manifest.selected(track)
    checked = (_check_one(stages, album, run_dir, t, manifest.lang) for t in chosen)
    return CheckReport(manifest.run_id, tuple(checked))


# --- Undo ----------------------------------------------------------------------------


def undo_album(
    album: Path,
    *,
    run_id: str | None,
    track: str | None,
    stages: Stages,
    acquire_lock: AcquireLock,
) -> UndoReport:
    """Undo the newest run, or *run_id*, holding the album lock while it is undone.

    *track* is an album-relative path. The run is chosen once, through
    `Stages.select_run`, before the lock is taken, so an album with no run is refused
    without one. Its manifest is read, and whether it is the newest run decided, with
    the lock held, so neither can change before undo acts on them. Raise `NoRunError`
    when there is no such run, `ManifestSchemaError` on a manifest this code cannot
    read (before any track changes), and `TrackNotInRunError` for a track the run does
    not list.
    """
    run_dir = stages.select_run(album, run_id)
    with album_lock(album, acquire=acquire_lock):
        manifest = _read_chosen(stages, album, run_dir)
        newest = stages.select_run(album, None) == run_dir
        return stages.undo(album, run_dir, manifest, newest=newest, track=track)
