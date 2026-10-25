"""Which tracks a run asks again as holding nothing, and the strip it makes to replace.

A ``--refetch-untimed`` run asks the providers again for each untimed-only track
(`refetch_target`): one holding untimed lyrics alone, so no SYLT frame with a worded
entry that is no placeholder, in any language or time unit, and no sidecar the sidecar
stage would serve as timed, with words in a USLT frame or a sidecar, or a ``.txt`` that
cannot be read (not UTF-8, or over the lyrics cap). Such a track is asked as though it
held nothing, and any answer the fetch accepts replaces its old lyrics. A synced track, and one with no
lyrics, runs as in any run. `worded_sylt` is the one rule for a worded SYLT, which a
fresh run's guard against untimed lyrics over synced ones asks too. `strip_if_changing`
is the strip a run replacing a track's old lyrics, a fresh run or a refetch, makes
before its embed.

A track no provider answers keeps its old lyrics unless they are implausible. Its
sidecars and its frames are judged apart, each by the lyrics on that side that would
serve it: the sidecar plan's lyrics, and the best-ranked worded untimed frame for the
language, a real one before any placeholder (`served_untimed_frames`, the one rule for
the frame a run serves, which the compare judges too). A ``.txt`` too large to read,
placeholder lyrics (`lyrics_placeholder`), or lyrics past the check's `too many words`
row over the track's readable duration, is implausible. An implausible side goes whole
(`remove_implausible`), through the strip stage as a replacing run strips: both sidecar
names, or every lyrics frame, other languages' and wordless ones included. The backup
and undo bring it back; the other side, when plausible, stays. A ``.txt`` that is not
UTF-8 is no evidence either way, nor is a duration that cannot be read, so either keeps.

A ``--refetch-implausible`` run asks again each track whose serving lyrics are
implausible (`implausible_target`): the lyrics the fetch would serve it, an unlabeled
timed ``.lrc`` before synced frames before a ``.txt`` before untimed frames, run past
the audio's end (synced only) or hold more words a second than any song, by the
check's `past end` and `too many words` rows over the track's readable duration
(`lyrics_check.implausible_flag`), never by `ends short`. Such a track is asked as
though it held nothing, and since every fetch declines an answer by the same rows, the
same wrong lyrics cannot come back; when nothing answers, its sidecar side and its frame
side, each judged apart as above, go when implausible and stay when not. The two
refetch options together ask the union of their tracks (`afresh_target`).
`serving_implausible` is the one rule for whether the serving lyrics are implausible,
which ``sweep``'s survey asks too, to visit an album whose every track is synced.

Every run, not only a refetch, asks again a track whose only lyrics are placeholders
(`placeholder_target`), in its sidecars, its worded frames or both: such a track holds
no lyrics, so it is asked as one holding nothing, and when nothing answers each side
holding a placeholder is removed, as an implausible side is.

Nothing here prints, and only `strip_if_changing` and `remove_implausible` write.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lyrics_check import implausible_flag
from lyrics_frames import new_frames, same_frames
from lyrics_inventory import TrackLyricsKind, embedded_lyrics, lyrics_kind
from lyrics_model import (
    FrameRecord,
    Lyrics,
    SidecarState,
    Source,
    SyncedLyrics,
    TrackTags,
)
from lyrics_outcomes import TrackState
from lyrics_placeholder import (
    is_placeholder_lyrics,
    unless_placeholder,
    without_placeholder_frames,
)
from lyrics_records import CheckReason, ErrorStage, OutcomeStatus, TrackOutcome
from lyrics_sidecar import UNREADABLE, SidecarTooLargeError, unreadable_txt
from lyrics_slots import check_slot
from lyrics_stages import Stages

#: Why a stored placeholder goes when nothing replaces it, and a refetch's old lyrics
#: that are one: they stand in for lyrics rather than hold any.
PLACEHOLDER = "placeholder lyrics"


@dataclass(frozen=True)
class RefetchTarget:
    """A track asked again, and what a run that finds nothing for it does with it.

    *unreadable* says why its ``.txt`` cannot be read, if so; *sidecar_implausible*
    why its sidecars are removed rather than kept, and *frames_implausible* why its
    lyrics frames are, each if it is. *refetch* marks an untimed-only track of
    ``--refetch-untimed``, *refetch_implausible* one ``--refetch-implausible`` picked.
    """

    unreadable: str | None = None
    sidecar_implausible: str | None = None
    frames_implausible: str | None = None
    #: False for a stored placeholder, which every run asks again, refetch or not.
    refetch: bool = True
    #: True for a track whose serving lyrics are implausible (`implausible_target`).
    refetch_implausible: bool = False

    @property
    def implausible(self) -> str | None:
        """Why the old lyrics removed are implausible, each reason once; else None."""
        reasons = (self.sidecar_implausible, self.frames_implausible)
        named = dict.fromkeys(reason for reason in reasons if reason is not None)
        return "; ".join(named) or None

    def record(self, state: TrackState) -> None:
        """Record on *state* that the track is asked again, and what goes unanswered."""
        state.refetch_untimed, state.sidecar_unreadable = self.refetch, self.unreadable
        state.refetch_implausible = self.refetch_implausible
        state.implausible = self.implausible
        state.remove_sidecars = self.sidecar_implausible is not None
        state.remove_frames = self.frames_implausible is not None


def worded_sylt(frames: Sequence[FrameRecord]) -> bool:
    """True when *frames* hold a SYLT with a worded entry, in any language or time unit.

    One of time stamps alone holds no lyrics, as a stamps-only ``.lrc`` holds none.
    """
    # Only a SYLT payload is a tuple of entries (`FrameRecord` holds to that); a
    # USLT's is its text.
    sylts = (f.payload for f in frames if isinstance(f.payload, tuple))
    return any(entry.text.strip() for payload in sylts for entry in payload)


def _implausible_reason(lyrics: Lyrics | None, duration_s: float | None) -> str | None:
    """Why *lyrics* are implausible: a placeholder, or the check's FLAG ruling them out.

    That FLAG is `past end` for synced lyrics, then `too many words`
    (`implausible_flag`), given as its detail; None when they pass. No lyrics at all
    pass: there is nothing there to remove.
    """
    if lyrics is not None and is_placeholder_lyrics(lyrics):
        return PLACEHOLDER
    flag = None if lyrics is None else implausible_flag(lyrics, duration_s)
    return None if flag is None else flag.detail


def served_untimed_frames(frames: Sequence[FrameRecord], lang: str) -> Lyrics | None:
    """The untimed lyrics *frames* serve: the best-ranked worded non-placeholder one.

    Only when no real one holds words is it the best of *frames*: a placeholder, which
    a refetch then judges implausible, so every frame goes when nothing answers. The
    one rule for the frame a run serves and judges, which the compare judges too.
    """
    best = embedded_lyrics(without_placeholder_frames(frames), lang).untimed
    return best if best is not None else embedded_lyrics(frames, lang).untimed


def _unreadable_target(
    path: Path, album: Path, frames_implausible: str | None
) -> RefetchTarget | None:
    """The untimed-only track whose ``.txt`` cannot be read; None for an unreadable ``.lrc``.

    One too large to read is implausible; one that is not UTF-8 may hold a song. Its
    frames are judged alone: *frames_implausible* says why they go, if they do.
    """
    error = unreadable_txt(path, album)
    if error is None:
        return None
    too_large = isinstance(error, SidecarTooLargeError)
    sidecar_implausible = str(error) if too_large else None
    return RefetchTarget(str(error), sidecar_implausible, frames_implausible)


def refetch_target(
    stages: Stages, album: Path, path: Path, tags: TrackTags, lang: str
) -> RefetchTarget | None:
    """The track at *path* as a refetch asks it again, or None when it is not untimed-only.

    *tags* are the track's, read before anything changed: its lyrics frames, which
    *lang* ranks, and its duration, to judge whether its worded sidecar and its
    untimed frames are implausible, each alone. The sidecars are planned, never
    normalized, so nothing on disk changes. A sidecar that cannot be read makes the
    track untimed-only only when it is the ``.txt`` (`unreadable_txt`); an unreadable
    ``.lrc`` might be timed, so the track runs as in any run, where it is the sidecar
    stage's error. A placeholder frame holds no lyrics, so a placeholder SYLT
    (`without_placeholder_frames`) never makes the track synced, and a real untimed
    frame beside a placeholder one is what serves and is judged, however they rank.
    """
    real = without_placeholder_frames(tags.frames)
    if worded_sylt(real):
        return None
    frames = served_untimed_frames(tags.frames, lang)
    frames_implausible = _implausible_reason(frames, tags.duration_s)
    try:
        sidecar = stages.normalize_sidecars(path, album=album, apply=False).lyrics
    except UNREADABLE:
        return _unreadable_target(path, album, frames_implausible)
    # A sidecar holding words serves before the frames, as in the fetch.
    worded = sidecar if lyrics_kind(sidecar) is not TrackLyricsKind.NONE else None
    serving = worded if worded is not None else frames
    if serving is None or lyrics_kind(serving) is not TrackLyricsKind.UNTIMED:
        return None
    sidecar_implausible = _implausible_reason(worded, tags.duration_s)
    return RefetchTarget(None, sidecar_implausible, frames_implausible)


def _served_frames(frames: Sequence[FrameRecord], lang: str) -> Lyrics | None:
    """The lyrics *frames* serve: the best-ranked worded non-placeholder, synced first.

    Only when no real one holds words is it the best of *frames*: a placeholder,
    judged implausible, so every frame goes when nothing answers.
    """
    best = embedded_lyrics(without_placeholder_frames(frames), lang).best
    return best if best is not None else embedded_lyrics(frames, lang).best


def _serving(
    sidecar: Lyrics | None, label: Source | None, frames: Lyrics | None
) -> Lyrics | None:
    """What serves the track, in the fetch's order, from the lyrics each side holds.

    An unlabeled timed sidecar, then synced frames, then any other sidecar, then
    untimed frames. A labeled one is taken as a ``.txt`` is, after synced frames.
    """
    if isinstance(sidecar, SyncedLyrics) and label is None:
        return sidecar
    if isinstance(frames, SyncedLyrics) or sidecar is None:
        return frames
    return sidecar


def _sides(
    planned: SidecarState, tags: TrackTags, lang: str
) -> tuple[Lyrics | None, Lyrics | None]:
    """The lyrics each side holds: the planned sidecar's, if worded, and the frames'.

    The frames' are the best-ranked worded one for *lang* (`_served_frames`).
    """
    worded = planned.lyrics
    sidecar = worded if lyrics_kind(worded) is not TrackLyricsKind.NONE else None
    return sidecar, _served_frames(tags.frames, lang)


def serving_implausible(
    planned: SidecarState, tags: TrackTags, lang: str
) -> CheckReason | None:
    """The check's FLAG ruling out the lyrics serving a track; None when they pass.

    *planned* is the track's sidecar plan, *tags* its tags, read before anything
    changed. The serving lyrics are the fetch's choice of its worded sidecar and its
    frames (`_serving`), placeholders never serving; the FLAG is the check's `past end`
    row (synced only), else its `too many words` row, over the track's readable
    duration (`implausible_flag`), never `ends short`. The one rule a
    ``--refetch-implausible`` run picks its tracks by, which the sweep's survey asks
    too.
    """
    sidecar, frames = _sides(planned, tags, lang)
    serving = _serving(
        unless_placeholder(sidecar), planned.label, unless_placeholder(frames)
    )
    return None if serving is None else implausible_flag(serving, tags.duration_s)


def implausible_target(
    stages: Stages, album: Path, path: Path, tags: TrackTags, lang: str
) -> RefetchTarget | None:
    """The track at *path* when the lyrics serving it are implausible; else None.

    *tags* are the track's, read before anything changed; the serving lyrics are
    judged by `serving_implausible`. Such a track's sidecar side and frame side are
    then judged apart, so a miss removes only the implausible one. The sidecars are
    planned, never normalized; one that cannot be read is no evidence, so the track
    runs as in any run, where it is the sidecar stage's error.
    """
    try:
        planned = stages.normalize_sidecars(path, album=album, apply=False)
    except UNREADABLE:
        return None
    if serving_implausible(planned, tags, lang) is None:
        return None
    sidecar, frames = _sides(planned, tags, lang)
    return RefetchTarget(
        None,
        _implausible_reason(sidecar, tags.duration_s),
        _implausible_reason(frames, tags.duration_s),
        refetch=False,
        refetch_implausible=True,
    )


def _all_placeholders(held: Sequence[Lyrics]) -> bool:
    """True when *held* is not empty and every one of them is a placeholder."""
    return bool(held) and all(is_placeholder_lyrics(lyrics) for lyrics in held)


def placeholder_target(
    stages: Stages, album: Path, path: Path, tags: TrackTags, lang: str
) -> RefetchTarget | None:
    """The track at *path* when the only lyrics it holds are placeholders; else None.

    Its worded frames, in any language, and the lyrics its sidecars would serve are
    what it holds; a blank frame or sidecar holds nothing, so it neither counts nor
    goes. When every one held is a placeholder (`is_placeholder_lyrics`), the track
    holds no lyrics, so any run asks it as though it held none, and when nothing
    answers, each side holding one is removed, as implausible old lyrics are. The
    sidecars are planned, never normalized; one that cannot be read is no evidence,
    so the track runs as in any run, where it is the sidecar stage's error.
    """
    frames = embedded_lyrics(tags.frames, lang).candidates
    try:
        sidecar = stages.normalize_sidecars(path, album=album, apply=False).lyrics
    except UNREADABLE:
        return None
    if not _all_placeholders((*frames, *(() if sidecar is None else (sidecar,)))):
        return None
    sidecar_reason = None if sidecar is None else PLACEHOLDER
    return RefetchTarget(
        None, sidecar_reason, PLACEHOLDER if frames else None, refetch=False
    )


def afresh_target(
    stages: Stages,
    album: Path,
    path: Path,
    tags: TrackTags,
    lang: str,
    *,
    refetch_untimed: bool,
    refetch_implausible: bool,
) -> RefetchTarget | None:
    """The track at *path* as a run asks it afresh, or None when it runs as in any run.

    A stored placeholder first, in every run (`placeholder_target`); then, under
    *refetch_untimed*, an untimed-only track (`refetch_target`); then, under
    *refetch_implausible*, one serving implausible lyrics (`implausible_target`). The
    two options together ask the union of their tracks.
    """
    target = placeholder_target(stages, album, path, tags, lang)
    if target is None and refetch_untimed:
        target = refetch_target(stages, album, path, tags, lang)
    if target is None and refetch_implausible:
        target = implausible_target(stages, album, path, tags, lang)
    return target


def strip_if_changing(
    stages: Stages,
    path: Path,
    tags: TrackTags,
    lyrics: Lyrics,
    lang: str,
    state: TrackState,
) -> None:
    """The strip before a replacing run's embed, unless the track's frames are the answer's.

    The embed replaces every lyrics frame and the sidecar writer the other kind of
    sidecar, so the strip only clears the way; when *tags*, read before anything was
    written, already hold the frames the embed would leave, it would save the MP3
    twice to put back what was there. Its slot check still runs, so a folder, a pipe
    or a device under a sidecar name is refused as the strip would refuse it.
    """
    state.stage = ErrorStage.STRIP
    if same_frames(tags.frames, new_frames(lyrics, lang)):
        check_slot(path)
        return
    state.stripped = stages.strip_track(path)


def remove_implausible(stages: Stages, path: Path, state: TrackState) -> TrackOutcome:
    """Remove the implausible old lyrics of a track no provider answered: `REMOVED`.

    The same write a replacing run makes before its embed, held as one, of only what
    *state* names implausible: when its frames go, the MP3 is first checked as one
    the strip could save; then the strip stage removes its lyrics frames, its
    sidecars, or both. Either refusal leaves the track whole and is the track's strip
    error. The backup holds everything removed, so undo restores it. *state* records
    the strip, so the outcome names what went.
    """
    state.stage = ErrorStage.STRIP
    frames, sidecars = state.remove_frames, state.remove_sidecars
    with state.held():
        if frames:
            stages.check_writable(path)
        state.stripped = stages.strip_track(path, frames=frames, sidecars=sidecars)
    return state.outcome(OutcomeStatus.REMOVED)
