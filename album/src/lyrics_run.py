"""The orchestrator: lock -> back up -> per track: sidecar, fetch, embed, check; or strip.

`run_album` is one run over an album. It takes the album lock, backs up every track
and writes the manifest before anything beside an MP3 changes (the backup barrier),
then takes each backed-up track through its stages inside one error boundary: a
failure is recorded against that track, at the stage it happened in, and the run
moves on. The manifest is replaced as each track finishes, so a run that stops
part-way still records which tracks it changed. A strip is the same run with another
per-track step (`strip_step`), so it shares all of that rather than copying it.
Discovery, the lock and the deferred interrupt are `lyrics_album`'s; a track's state
and the outcome built from it are `lyrics_outcomes`'; the manifest written as each
track finishes is `lyrics_recorder`'s; which frame the check judges is
`lyrics_judge`'s; the dry run, the check of a finished run and undo are `lyrics_modes`'.

A run serves a track from what it already holds, its sidecars and then its lyrics
frames, each kind in its place in the fetch's order (`lyrics_fetch`); an unlabeled
sidecar of the frames' own lyrics counts as the frames. A track served from its frames
is left as it is, but for the sidecar it lacked, written from them
(`_serve_from_frames`). A fresh run (`RunOptions.fresh`) leaves a track's sidecars and
frames out of the lookup, so the providers are always asked, and changes nothing before
an answer is accepted: only then does it strip the old frames and sidecars, in the same
step, before the embed, and only once the answer is one the embed can encode and the
track's tag one it can save. Every run asks the first of those before its embed, fresh
or not. A fresh run also declines an answer the check's unlike-backup row would flag,
and an untimed answer for a track whose lyrics were already synced, before anything is
written, so the track keeps its old lyrics as when nothing is found. A refetch
(`RunOptions.refetch_untimed`) treats each untimed-only track (`lyrics_refetch`) as a
fresh run treats every track, but declines no answer; a track it finds nothing for keeps
its lyrics, but strips its sidecar or its frames, each judged alone, if implausible.
A ``--refetch-implausible`` run (`RunOptions.refetch_implausible`) treats each track
serving implausible lyrics the same way, its implausible side removed when nothing
answers. Every run, fresh, refetch or neither, treats a track whose only lyrics are
placeholders (`lyrics_refetch.placeholder_target`) as a refetch treats an untimed-only
track whose lyrics are implausible: asked afresh, and its placeholders removed when
nothing answers. Every run hands the fetch the track's duration, read with its frames,
so an answer it rules out is declined (`lyrics_accept`).

No write that would leave a file as it was is made: the embed does not save frames the
track already holds, the sidecar writer does not rewrite a file holding its bytes, and
a run replacing old lyrics does not strip a track whose frames already are the
answer's (`lyrics_refetch.strip_if_changing`). A track the run accepted lyrics for and
changed nothing of is `OutcomeStatus.UNCHANGED`, and still checked.

The report carries each provider's tally (`lyrics_tally`): the tracks it was asked
for, and those whose answer the run kept.

Every stage arrives through `lyrics_stages.Stages`, typed by the stage protocols, so
the entry point passes the real functions and a test passes fakes. Nothing here prints:
progress goes to a callback, and the entry point writes it.

An interrupt (Ctrl-C) that arrives while a track's files are being saved, or while its
outcome is being recorded, is held until that write returns, and the run then stops
cleanly; one that arrives during a fetch or a check stops the run at once. Either way
the interrupted track's outcome records what had been done to it, which is what undo
needs. A track's write that fails while the interrupt is held records that failure as
the track's error, and the run still stops. An OS error writing the manifest after the
backup, or a manifest that does not read back as written, stops the run the same way,
and the report says why.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

from lyrics_album import AcquireLock, album_lock
from lyrics_decline import declines
from lyrics_genre import NO_LYRICS_GENRES, no_lyrics_genre
from lyrics_inventory import (
    NO_EMBEDDED_LYRICS,
    embedded_lyrics,
    sidecar_matches,
)
from lyrics_judge import check_written
from lyrics_model import (
    Lyrics,
    ProviderTable,
    SidecarState,
    Source,
    TrackQuery,
    TrackTags,
)
from lyrics_outcomes import TrackState, not_found
from lyrics_provider_states import (
    BandcampState,
    CoolingState,
    SilenceCheck,
    cooling_off,
)
from lyrics_records import (
    ErrorStage,
    GeniusState,
    ManifestTrack,
    OutcomeStatus,
    StageError,
    TrackBefore,
    TrackOutcome,
    run_folder,
)
from lyrics_recorder import RunRecorder
from lyrics_refetch import afresh_target, remove_implausible, strip_if_changing
from lyrics_sidecar import MACHINE_LABELS, sidecar_keeps
from lyrics_stages import Stages
from lyrics_tally import ProviderCount, TrackFetch, tally_providers, track_count

#: The sources that are the track's own sidecar, never rewritten when they serve: a
#: plain sidecar, and each machine-labeled one, whose header lines must survive.
_SERVED_FROM_SIDECAR = frozenset({Source.SIDECAR, *MACHINE_LABELS.values()})

# --- The run's settings and its report -----------------------------------------------


@dataclass(frozen=True)
class RunOptions:
    """A run's settings: the frame language, the per-call fetch deadline, the key state.

    *fresh* asks the providers even where a sidecar would serve the track, and an
    accepted answer replaces the track's old frames and sidecars (`--fresh`).
    *refetch_untimed* does the same for an untimed-only track alone, accepting any
    answer (`--refetch-untimed`); *refetch_implausible* for a track whose serving
    lyrics are implausible (`--refetch-implausible`), the two asking their union.
    *no_lyrics_genres* mark a track whose miss is no lyrics expected (lyrics_genre).
    *bandcamp* is whether the run's table asks Bandcamp, which its manifest records;
    *youtube_music* gives YouTube Music's state, read at every write, so a stop shows at once;
    *tekstowo* gives Tekstowo's, read alike.
    *ffmpeg* measures a missed track for silence (lyrics_silence); None leaves the check off.
    """

    lang: str
    deadline_s: float
    genius: GeniusState
    fresh: bool = False
    refetch_untimed: bool = False
    refetch_implausible: bool = False
    no_lyrics_genres: tuple[str, ...] = NO_LYRICS_GENRES
    bandcamp: BandcampState = BandcampState.OFF
    youtube_music: Callable[[], CoolingState] = cooling_off
    ffmpeg: str | None = None
    tekstowo: Callable[[], CoolingState] = cooling_off


@dataclass(frozen=True)
class RunReport:
    """What a run did: its id, every track with its outcome, and whether it was cut short.

    A track whose ``after`` is None was never reached. A run is cut short by an
    interrupt, or by a manifest write that fails or does not read back (*record_error*).
    """

    run_id: str
    tracks: tuple[ManifestTrack, ...]
    interrupted: bool = False
    #: Why the run's manifest could not be written, when that is what stopped it.
    record_error: str | None = None
    #: Each provider's tracks asked and won, over every track whose fetch finished.
    providers: tuple[ProviderCount, ...] = ()

    @property
    def has_errors(self) -> bool:
        """True when any track's outcome is an error."""
        return any(
            t.after is not None and t.after.status is OutcomeStatus.ERROR
            for t in self.tracks
        )


# --- One track -----------------------------------------------------------------------


def track_query(tags: TrackTags, path: Path) -> TrackQuery:
    """The search query: the artist and title tags, or the file stem when either is missing.

    The album and the length travel either way, for the providers that can use them.
    """
    if tags.artist and tags.title:
        return TrackQuery(
            tags.artist, tags.title, album=tags.album, duration_s=tags.duration_s
        )
    return TrackQuery(
        "", path.stem, from_filename=True, album=tags.album, duration_s=tags.duration_s
    )


@dataclass(frozen=True)
class TrackContext:
    """What every track of one run shares."""

    album: Path
    run_dir: Path
    providers: ProviderTable
    options: RunOptions
    stages: Stages


#: One backed-up track's work inside its error boundary, `_process` for a run and
#: `strip_step` for a strip: it updates the state as it goes, so whatever stops it, the
#: outcome built from that state records what was done.
type TrackStep = Callable[
    [TrackContext, ManifestTrack, TrackBefore, TrackState], TrackOutcome
]


def _asks_afresh(
    ctx: TrackContext, path: Path, tags: TrackTags, state: TrackState
) -> bool:
    """True when the run asks the providers as though the track held no lyrics.

    Every track of a fresh run; in any run, a track whose only lyrics are
    placeholders; each untimed-only track of a refetch; and each track serving
    implausible lyrics under ``--refetch-implausible`` (`lyrics_refetch.afresh_target`).
    *state* records the target, so a miss removes what it names. Its sidecars are
    only planned for that, so nothing on disk changes.
    """
    state.stage = ErrorStage.SIDECAR
    options = ctx.options
    target = afresh_target(
        ctx.stages,
        ctx.album,
        path,
        tags,
        options.lang,
        refetch_untimed=options.refetch_untimed,
        refetch_implausible=options.refetch_implausible,
    )
    if target is not None:
        target.record(state)
    return options.fresh or target is not None


def _sidecar_state(
    ctx: TrackContext, path: Path, state: TrackState, *, afresh: bool
) -> SidecarState:
    """The sidecar state the fetch starts from, normalized on disk unless *afresh*.

    A track asked afresh (`_asks_afresh`) leaves the sidecars out of the lookup, so
    the providers are always asked, and does not normalize them either: that can
    rename or remove one, and nothing changes before an answer is accepted.
    """
    if afresh:
        return SidecarState(None)
    state.stage = ErrorStage.SIDECAR
    with state.held():
        state.sidecar = ctx.stages.normalize_sidecars(path, album=ctx.album, apply=True)
    return state.sidecar


def _serve_from_frames(
    ctx: TrackContext,
    path: Path,
    before: TrackBefore,
    lyrics: Lyrics,
    state: TrackState,
    held: Lyrics | None,
) -> TrackOutcome:
    """A track served from its own lyrics frames: write only the sidecar it lacked.

    The frames keep their place, so they are never written back: an ID3v2.2 tag the
    embed cannot save is no error, an MPEG-frame SYLT keeps its timing, and the
    frames' language and any lyrics frames beside the serving one stay. The sidecar
    is written from the frames, a ``.lrc`` replacing a ``.txt`` as a timed answer's
    does, unless a ``.lrc`` could not hold the lines whole. The check still reads the
    frames back, and judges the one that served, *lyrics*, against the same frame in
    the backup, not the one ``--lang`` ranks first (`check_written`). A sidecar
    already holding the frames' lyrics, *held*, stays as it is. A track whose sidecar
    was not written, or already held those bytes, is unchanged.
    """
    if sidecar_keeps(lyrics) and not sidecar_matches(held, lyrics):
        state.stage = ErrorStage.SIDECAR
        with state.held():
            state.written = ctx.stages.write_sidecar(path, lyrics)
    state.stage = ErrorStage.CHECK
    state.check = check_written(
        ctx.stages, path, ctx.run_dir, before, ctx.options.lang, lyrics
    )
    return state.accepted()


def _process(
    ctx: TrackContext, track: ManifestTrack, before: TrackBefore, state: TrackState
) -> TrackOutcome:
    """Take one backed-up track through sidecar, fetch, embed and check.

    Each write runs with the interrupt deferred, and *state* is updated inside it, so
    an interrupt honored as the write returns still finds the write recorded. A
    track asked afresh (`_asks_afresh`) has its old lyrics stripped between an accepted
    answer and its embed, inside the embed's own held write, so an interrupt never
    leaves the track stripped with nothing new in place; a fresh run first declines an
    answer unlike the old lyrics, or untimed over synced ones
    (`lyrics_decline.declines`), and a refetch declines none. The stage names the step
    under way, so an error is labeled by the step that raised it.
    """
    path, stages, lang = ctx.album / track.path, ctx.stages, ctx.options.lang
    tags = stages.read_tags(path)
    state.no_lyrics_genre = no_lyrics_genre(tags.genres, ctx.options.no_lyrics_genres)
    state.query = track_query(tags, path)
    afresh = _asks_afresh(ctx, path, tags, state)
    sidecar = state.sidecar = _sidecar_state(ctx, path, state, afresh=afresh)
    state.stage, state.in_fetch = ErrorStage.FETCH, True
    state.result = stages.fetch(
        state.query,
        sidecar,
        ctx.providers,
        deadline_s=ctx.options.deadline_s,
        # The frames are read before anything is written, so they are what the backup
        # holds; a track asked afresh is served from none of them.
        embedded=NO_EMBEDDED_LYRICS if afresh else embedded_lyrics(tags.frames, lang),
        # Read with the frames, so every answer is judged against the audio as it is.
        duration_s=tags.duration_s,
    )
    state.in_fetch = False
    lyrics, source = state.result.lyrics, state.result.source
    if lyrics is None:
        if state.implausible is not None and not state.result.every_provider_failed:
            return remove_implausible(stages, path, state)
        # The backup recorded each sidecar present, a file copied or a link by its
        # target, a folder under the name not counted, so its record says whether the
        # track kept one. Without --fresh a sidecar or worded frames would have served
        # the track, so only wordless frames can have been kept; a refetch asked again
        # only a track holding untimed lyrics, which it keeps when they are plausible.
        kept = state.refetch_untimed or (ctx.options.fresh and bool(before.sidecars))
        ffmpeg = ctx.options.ffmpeg
        measure = (
            None if ffmpeg is None else partial(stages.measure_level, path, ffmpeg)
        )
        return not_found(state, tags, lang, kept_sidecar=kept, measure=measure)
    if source is Source.FRAMES:
        return _serve_from_frames(ctx, path, before, lyrics, state, sidecar.lyrics)
    # Refused before anything changes, labeled as the embed would label it, so an
    # answer the embed cannot encode leaves the old lyrics whole.
    state.stage = ErrorStage.EMBED
    stages.check_encodable(lyrics, lang)
    duration_s = tags.duration_s
    if ctx.options.fresh and declines(ctx, path, before, lyrics, duration_s, state):
        return state.outcome(OutcomeStatus.KEPT_EXISTING)
    with state.held():
        if afresh:
            # Likewise a track the embed foreseeably cannot save, before the strip.
            state.stage = ErrorStage.EMBED
            stages.check_writable(path)
            strip_if_changing(stages, path, tags, lyrics, lang, state)
        state.stage = ErrorStage.EMBED
        state.frames_written = stages.write_lyrics(path, lyrics, lang)
        # A sidecar that served the lyrics stays exactly as it was, a machine label's
        # header lines included.
        if source not in _SERVED_FROM_SIDECAR:
            state.written = stages.write_sidecar(path, lyrics)
    state.stage = ErrorStage.CHECK
    state.check = check_written(stages, path, ctx.run_dir, before, lang)
    return state.accepted()


def strip_step(
    ctx: TrackContext, track: ManifestTrack, before: TrackBefore, state: TrackState
) -> TrackOutcome:
    """Strip one backed-up track: its lyrics frames and both sidecars, one held write.

    *before* goes unused: the backup has already recorded it, and undo restores the
    track from there.
    """
    state.stage = ErrorStage.STRIP
    with state.held():
        state.stripped = ctx.stages.strip_track(ctx.album / track.path)
    return state.outcome(OutcomeStatus.STRIPPED)


def _run_track(
    ctx: TrackContext, track: ManifestTrack, step: TrackStep, state: TrackState
) -> tuple[TrackOutcome, bool]:
    """One track's *step* inside its error boundary: its outcome, and whether to stop.

    *state* is the caller's, so the fetch it records outlives the step.
    """
    if track.before is None:
        # No undo source, so the track is never written: the backup's own record of
        # why stands, else a generic one.
        error = StageError(ErrorStage.BACKUP, "BackupError", "the backup failed")
        return track.after or TrackOutcome(OutcomeStatus.ERROR, error=error), False
    try:
        return step(ctx, track, track.before, state), False
    except KeyboardInterrupt as interrupt:
        # The run stops either way; a write's own error, if one was held, is the reason
        # shown for this track rather than the interrupt that masked it.
        return state.failed(state.masked(interrupt) or interrupt), True
    # The per-track boundary: any failure is this track's, and the run moves on.
    except Exception as error:
        return state.failed(error), False


# --- The run -------------------------------------------------------------------------


def _track_fetch(state: TrackState, outcome: TrackOutcome) -> TrackFetch | None:
    """The tally's view of one track: its fetch's attempts and the answer kept, if any.

    None when the track never finished a fetch. A fresh run's declined answer is
    the one kept-existing outcome with a source, and counts as not accepted.
    """
    if state.result is None:
        return None
    kept = outcome.status is not OutcomeStatus.KEPT_EXISTING
    return TrackFetch(state.result.attempts, outcome.source if kept else None)


def _run_tracks(
    ctx: TrackContext, recorder: RunRecorder, step: TrackStep, fetches: list[TrackFetch]
) -> None:
    """Take each backed-up track through *step*, recording each as it finishes.

    Each finished fetch goes into *fetches*, for the providers' tally.

    A Ctrl-C can land outside every held write too: between a track's boundary and
    its record, say. It still ends the run cleanly, and an outcome computed but not yet
    recorded is recorded first, so the manifest says what was done to that track.
    """
    for index, track in enumerate(recorder.manifest.tracks):
        outcome: TrackOutcome | None = None
        try:
            state = TrackState()
            outcome, interrupted = _run_track(ctx, track, step, state)
            fetched = _track_fetch(state, outcome)
            if fetched is not None:
                fetches.append(fetched)
            recorder.interrupted = interrupted
            if recorder.record(index, outcome):
                return
        except KeyboardInterrupt:
            recorder.interrupted = True
            if outcome is not None and not recorder.recorded(index, outcome):
                recorder.record(index, outcome)
            return
    try:
        recorder.complete()
    except KeyboardInterrupt:
        recorder.interrupted = True


def run_album(
    album: Path,
    tracks: Sequence[Path],
    providers: ProviderTable,
    options: RunOptions,
    stages: Stages,
    *,
    acquire_lock: AcquireLock,
    started: datetime,
    progress: Callable[[str], None],
    step: TrackStep = _process,
) -> RunReport:
    """Run *album*: lock, back up every track, then take each through *step*.

    *step* is a run's sidecar, fetch, embed and check unless a strip passes
    `strip_step`; either way it shares this lock, backup barrier, error boundary and
    per-track record. Raise `LockHeldError` when another run holds the album, and an
    `OSError` from the lock or the backup, before any MP3 changed. Nothing beside an
    MP3 changes until the backup has written its manifest. Each finished track is
    recorded in the manifest at once; a run nothing cut short is then marked complete.
    """
    with album_lock(album, acquire=acquire_lock):
        progress(f"backing up {track_count(len(tracks))}")
        manifest = stages.back_up(
            album,
            tracks,
            lang=options.lang,
            genius=options.genius,
            started=started,
            bandcamp=options.bandcamp,
            youtube_music=options.youtube_music(),
            tekstowo=options.tekstowo(),
            silence_check=SilenceCheck.ON
            if options.ffmpeg is not None
            else SilenceCheck.OFF,
        )
        run_dir = run_folder(album, manifest.run_id)
        recorder = RunRecorder(
            run_dir,
            manifest,
            stages,
            progress,
            youtube_music=options.youtube_music,
            tekstowo=options.tekstowo,
        )
        ctx = TrackContext(album, run_dir, providers, options, stages)
        fetches: list[TrackFetch] = []
        _run_tracks(ctx, recorder, step, fetches)
    return RunReport(
        manifest.run_id,
        recorder.manifest.tracks,
        interrupted=recorder.interrupted,
        record_error=recorder.record_error,
        providers=tally_providers({key.source for key in providers}, fetches),
    )
