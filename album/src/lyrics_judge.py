"""What the check judges: the lyrics a track now holds, from the frame that served it.

`check_written` reads a track back after its run and passes the check its lyrics and
its backed-up lyrics. A track the run wrote holds frames written under ``--lang``, so
the ``--lang`` frame is judged, as `lyrics_from_frames` picks it. A track served from
its own frames may have been served from another one: a sidecar holding a frame's
lyrics ranks that frame first (`EmbeddedLyrics.favoring`), whatever ``--lang`` ranks.
So the run passes the lyrics that served, and `judged_lyrics` judges the frame equal
to them, in the track and in its backup; a frame changed since falls back to the
``--lang`` frame, so the change is still judged.

A re-check of a finished run (`recheck_written`) does not know which frame served.
For a track the run served from its frames it asks the run's own question again
(`frames_served`): the frame the fetch serves the track from, given the sidecar that
now lies beside it. Usually the run wrote that frame's sidecar, or found it there, so
the re-check names the frame the run did. Not always: a run writes no sidecar a
``.lrc`` could not hold whole, and a ``.txt`` of untimed lyrics beside synced frames
names no synced frame. Then the run and the re-check agree only because both fall back
to the ``--lang`` ranking. A sidecar removed or edited since the run makes the
re-check judge the frame a run would serve now.

A track whose backed-up frames hold no word, its old lyrics in a sidecar alone, is
compared with the untimed sidecar the backup copied: its ``.txt``, else a ``.lrc`` with no
time stamp. A copy that cannot be read (not UTF-8, or past the lyrics cap) makes the
unlike-backup row a SKIP naming why, as does a backed-up frame over the lyrics cap,
the junk a refetch replaced (`lyrics_cap.oversized_reason`). A fresh run's own guard
compares with the frames alone and never comes here.

Nothing here prints. The helpers `judged_lyrics` and `frames_served` are pure.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lyrics_cap import oversized_reason
from lyrics_inventory import embedded_lyrics
from lyrics_model import FrameRecord, Lyrics, TrackTags, lyrics_from_frames
from lyrics_placeholder import unless_placeholder, without_placeholder_frames
from lyrics_records import CheckReason, CheckResult, CheckRule, TrackBefore
from lyrics_sidecar import UNREADABLE
from lyrics_stages import Stages


def judged_lyrics(
    frames: Sequence[FrameRecord], lang: str, served: Lyrics | None
) -> Lyrics | None:
    """The lyrics of *frames* the check judges: the frame equal to *served*.

    Every worded frame is a candidate, converted alone as `embedded_lyrics` converts
    it. With *served* None, or no frame equal to it, the ``--lang`` frame is judged,
    as `lyrics_from_frames` picks it.
    """
    if served is not None:
        candidates = embedded_lyrics(frames, lang).candidates
        match = next((held for held in candidates if held == served), None)
        if match is not None:
            return match
    return lyrics_from_frames(frames, lang)


def frames_served(
    frames: Sequence[FrameRecord], lang: str, sidecar: Lyrics | None
) -> Lyrics | None:
    """The lyrics a run serves from *frames* beside *sidecar*, as the fetch serves them.

    The best synced frame, else the best untimed one, with any frame *sidecar* holds
    ranked first of its kind. None when no frame holds a word.
    """
    return embedded_lyrics(frames, lang).favoring(sidecar).best


def _backup_lyrics(
    stages: Stages, run_dir: Path, before: TrackBefore, lang: str, served: Lyrics | None
) -> tuple[Lyrics | None, CheckReason | None]:
    """The backed-up lyrics the check compares with, or the SKIP saying why there are none.

    The *served* frame of the backup, as `judged_lyrics` takes it; when no backed-up
    frame holds a word, the untimed sidecar the backup copied instead
    (`Stages.load_before_sidecar`). A placeholder is no lyrics to compare with, so
    placeholder frames are set aside first and a placeholder copy is None
    (`lyrics_placeholder`). A copy the sidecar stage could not read is the
    unlike-backup row's SKIP, naming why, never a FLAG; so is a backed-up frame over
    the lyrics cap, which holds no lyrics to compare with.
    """
    held = stages.load_before(run_dir, before)
    oversized = oversized_reason(held)
    if oversized is not None:
        return None, CheckReason(CheckRule.UNLIKE_BACKUP, f"backed-up {oversized}")
    frames = without_placeholder_frames(held)
    if embedded_lyrics(frames, lang).best is not None:
        return judged_lyrics(frames, lang, served), None
    try:
        return unless_placeholder(stages.load_before_sidecar(run_dir, before)), None
    except UNREADABLE as error:
        return None, CheckReason(CheckRule.UNLIKE_BACKUP, f"backed-up {error}")


def _judge(
    stages: Stages,
    tags: TrackTags,
    run_dir: Path,
    before: TrackBefore,
    lang: str,
    served: Lyrics | None,
) -> CheckResult:
    """The check of *tags* read back, against the backup, judging the *served* frame."""
    backup, unread = _backup_lyrics(stages, run_dir, before, lang, served)
    embedded = judged_lyrics(tags.frames, lang, served)
    result = stages.check(embedded, backup, tags.duration_s)
    if unread is None or embedded is None:
        # A track holding no lyrics is the one FLAG: there is nothing to compare.
        return result
    return CheckResult(result.flags, (*result.skips, unread))


def check_written(
    stages: Stages,
    path: Path,
    run_dir: Path,
    before: TrackBefore,
    lang: str,
    served: Lyrics | None = None,
) -> CheckResult:
    """Judge what *path* now holds against its duration and its backed-up lyrics.

    Read back from the file, the authoritative state, not from what was written.
    *served* is the lyrics a track served from its own frames was served with, so
    that frame is judged (`judged_lyrics`); None for a track the run wrote.
    """
    tags = stages.read_tags(path)
    return _judge(stages, tags, run_dir, before, lang, served)


def recheck_written(
    stages: Stages,
    album: Path,
    path: Path,
    run_dir: Path,
    before: TrackBefore,
    lang: str,
    *,
    from_frames: bool,
) -> CheckResult:
    """`check_written` for a finished run, judging the frame the run served from.

    *from_frames* says the run served the track from its own frames: the frame
    judged is the one the fetch would serve beside the sidecar there now
    (`frames_served`), read as a dry run reads it, changing nothing. Otherwise the
    ``--lang`` frame is judged, as in the run.
    """
    if not from_frames:
        return check_written(stages, path, run_dir, before, lang)
    tags = stages.read_tags(path)
    sidecar = stages.normalize_sidecars(path, album=album, apply=False).lyrics
    served = frames_served(tags.frames, lang, sidecar)
    return _judge(stages, tags, run_dir, before, lang, served)
