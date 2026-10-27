"""Compare: whether a provider's answer for a track agrees with its untimed lyrics.

`compare_album` judges each untimed-only track, as ``--refetch-untimed`` picks them
(`lyrics_refetch.refetch_target`), by the text a run would serve: its sidecar's words,
else its best-ranked worded untimed frame. LRCLIB, NetEase, Bandcamp, YouTube Music,
Genius and Tekstowo (`COMPARED`, each by its untimed step) are each asked on its own,
never as a chain stopping at the first answer, through the chain's one-step function
(`lyrics_fetch.attempt`), so every answer passes a run's matcher and network rules.
It is not judged against the track's duration, as a run's answers are: the compare
reports what each provider says, and the check's rows judge the lyrics a run wrote.
Answers are scored with the check's word similarity, so one number,
`MIN_SIMILARITY`, means "another song" in the check and here alike; any one answer at
or above it agrees. A track that does not agree gets the weak hints too
(`compare_hints`), never a verdict alone. `ask_each` retries each provider by the
chain's own stripped-title rule (`lyrics_titles.stripped_retry`), applied to that one
provider's answer rather than to a whole pass. Compare writes nothing into the album
(a YouTube Music or Tekstowo refusal still writes its cool-down, through the client),
takes no lock, makes no run folder, and prints nothing itself: a run whose manifest
cannot be read is passed over, and named through the caller's notice.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from lyrics_check import MIN_SIMILARITY, normalize_words, shown, word_similarity
from lyrics_fetch import CHAIN, Heard, attempt, skips
from lyrics_inventory import TrackLyricsKind, lyrics_kind
from lyrics_model import (
    REFUSED_OUTCOMES,
    AttemptOutcome,
    Lyrics,
    LyricsKind,
    ProviderAttempt,
    ProviderKey,
    ProviderTable,
    Source,
    TrackQuery,
    TrackTags,
)
from lyrics_outcomes import every_provider_failed
from lyrics_provenance import recorded_sources
from lyrics_records import ErrorStage, StageError
from lyrics_refetch import refetch_target, served_untimed_frames
from lyrics_run import track_query
from lyrics_stages import Stages
from lyrics_tally import ProviderCount
from lyrics_titles import strip_title_suffixes, stripped_retry

#: The fewest words a second untimed lyrics may hold over the track before compare
#: hints that they may be another, shorter song's: a three-minute song under 18 words,
#: or a song's lyrics on a long stand-up track.
MIN_WORDS_PER_SECOND = 0.1
#: A title word shorter than this (a, of, me) says nothing when absent.
_MIN_TITLE_WORD = 3
#: The providers compare asks: every untimed step of the chain, in its order. An LRC
#: provider is asked by its untimed step, which takes plain text, else timed text
#: without its stamps.
COMPARED: tuple[Source, ...] = tuple(
    key.source for key in CHAIN if key.kind is LyricsKind.UNSYNCED
)
#: The hint for a title none of whose words the lyrics hold, and why a track no
#: provider could be asked for has no second source.
NO_TITLE_WORD, NO_PROVIDER_ASKED = "no title word", "no provider asked"


class CompareVerdict(StrEnum):
    """What compare says of one track."""

    AGREES = "agrees"
    NO_SOURCE_AGREES = "no source agrees"
    NO_SECOND_SOURCE = "no second source"
    NOT_JUDGED = "not judged"


@dataclass(frozen=True)
class ProviderScore:
    """One provider's answer for a track: how the call ended; its similarity, if any."""

    source: Source
    outcome: AttemptOutcome
    similarity: float | None


@dataclass(frozen=True)
class ComparedTrack:
    """One track's row. *verdict* is None for an error row, whose *error* says why.

    *reason* qualifies a `NO_SECOND_SOURCE` verdict when no provider could be asked.
    *own* is the compared provider the track's lyrics came from, set aside and not
    asked; *recorded* is False when no run manifest names that source.
    """

    path: str
    verdict: CompareVerdict | None
    scores: tuple[ProviderScore, ...] = ()
    hints: tuple[str, ...] = ()
    reason: str | None = None
    error: StageError | None = None
    own: Source | None = None
    recorded: bool = True


@dataclass(frozen=True)
class CompareReport:
    """Every track's row, and each compared provider's tally."""

    tracks: tuple[ComparedTrack, ...]
    providers: tuple[ProviderCount, ...]

    @property
    def has_errors(self) -> bool:
        """True when any track's row is an error."""
        return any(track.error is not None for track in self.tracks)


#: One provider's part in `ask_each`'s result: its source, accepted lyrics, the attempt.
type Asked = tuple[Source, Lyrics | None, ProviderAttempt]


def _key(source: Source) -> ProviderKey:
    """The chain step compare asks *source* by: its untimed one."""
    return ProviderKey(source, LyricsKind.UNSYNCED)


def ask_each(
    query: TrackQuery, providers: ProviderTable, deadline_s: float
) -> tuple[Asked, ...]:
    """Each compared provider's answer for *query*, asked on its own, never as a chain.

    A provider absent from the table, or one that would send no request for the query
    (an LRC provider for a query by file name), is left out. One that found nothing is
    asked once more with the title's trailing groups stripped, as the chain retries.
    """
    heard: Heard = {}
    asked: list[Asked] = []
    for source in COMPARED:
        provider = providers.get(_key(source))
        if provider is None or skips(provider, query):
            continue
        lyrics, tried = attempt(_key(source), provider, query, deadline_s, heard)
        again = stripped_retry(query, (tried.outcome,), found=lyrics is not None)
        if again is not None:
            lyrics, tried = attempt(_key(source), provider, again, deadline_s, heard)
        asked.append((source, lyrics, tried))
    return tuple(asked)


def _judged_lyrics(
    stages: Stages, album: Path, path: Path, tags: TrackTags, lang: str
) -> Lyrics | None:
    """The untimed lyrics a run would serve the track from; None when it is not judged.

    Its sidecar's lyrics come first when they hold words, else the untimed frame a run
    serves (`served_untimed_frames`): the best-ranked worded one that is no
    placeholder. The sidecars are planned first, as a run's sidecar stage reads them
    before anything else: one that cannot be read, ``.lrc`` or ``.txt``, raises that
    stage's own error, which the caller records at the sidecar stage. The refetch's
    test would call an unreadable ``.lrc`` not untimed-only and so not judged.
    """
    sidecar = stages.normalize_sidecars(path, album=album, apply=False).lyrics
    if refetch_target(stages, album, path, tags, lang) is None:
        return None
    if lyrics_kind(sidecar) is not TrackLyricsKind.NONE:
        return sidecar
    return served_untimed_frames(tags.frames, lang)


def _few_words(count: int, duration_s: float | None) -> str | None:
    """The `few words` hint, as the check's `too many words` row; None for a usable rate."""
    if duration_s is None or not math.isfinite(duration_s) or duration_s <= 0:
        return None
    rate = count / duration_s
    if rate >= MIN_WORDS_PER_SECOND:
        return None
    measured = f"{shown(rate)} words per second: {count} words in {duration_s:.3f} s"
    return f"few words ({measured})"


# fuzz-target
def compare_hints(
    words: Sequence[str], title: str, duration_s: float | None
) -> tuple[str, ...]:
    """The weak hints beside a verdict: few words for the duration, no title word.

    *words* are the judged text's, normalized (`normalize_words`); so is the title,
    less its trailing bracketed groups. A duration that is missing, not finite or not
    positive gives no `few words`; a title with no word of `_MIN_TITLE_WORD` letters
    or more gives no `no title word`.
    """
    hints = []
    few = _few_words(len(words), duration_s)
    if few is not None:
        hints.append(few)
    title_words = {
        word
        for word in normalize_words(strip_title_suffixes(title))
        if len(word) >= _MIN_TITLE_WORD
    }
    if title_words and title_words.isdisjoint(words):
        hints.append(NO_TITLE_WORD)
    return tuple(hints)


def _score(words: list[str], asked: Asked) -> ProviderScore:
    """One provider's score: the similarity of its answer, if any, to *words*."""
    source, lyrics, tried = asked
    if lyrics is None:
        return ProviderScore(source, tried.outcome, None)
    similarity = word_similarity(words, normalize_words(lyrics.plain_text()))
    return ProviderScore(source, tried.outcome, similarity)


def _agrees(score: ProviderScore) -> bool:
    """True when *score*'s answer is at or above the check's similarity."""
    return score.similarity is not None and score.similarity >= MIN_SIMILARITY


def _verdict(
    path: str, words: list[str], query: TrackQuery, asked: Sequence[Asked]
) -> ComparedTrack:
    """A judged track's row: hints only when it does not agree; all failed is an error."""
    scores = tuple(_score(words, one) for one in asked)
    if any(_agrees(score) for score in scores):
        return ComparedTrack(path, CompareVerdict.AGREES, scores)
    hints = compare_hints(words, query.title, query.duration_s)
    if any(score.outcome is AttemptOutcome.ANSWERED for score in scores):
        return ComparedTrack(path, CompareVerdict.NO_SOURCE_AGREES, scores, hints)
    if not scores:
        verdict = CompareVerdict.NO_SECOND_SOURCE
        return ComparedTrack(path, verdict, hints=hints, reason=NO_PROVIDER_ASKED)
    outcomes = [score.outcome for score in scores]
    if all(outcome in REFUSED_OUTCOMES for outcome in outcomes):
        error = every_provider_failed(outcomes)
        return ComparedTrack(path, None, scores, error=error)
    return ComparedTrack(path, CompareVerdict.NO_SECOND_SOURCE, scores, hints)


def compare_track(
    album: Path,
    path: Path,
    providers: ProviderTable,
    stages: Stages,
    *,
    lang: str,
    deadline_s: float,
    source: Source | None,
) -> ComparedTrack:
    """Compare one track inside a run's per-track error boundary.

    That boundary is `lyrics_run._run_track`. *source* is the provider the run manifests
    record the track's lyrics from, None when unrecorded. A compared one is left out of the
    table, so it is never asked.

    A failure reading the tags, building the query or asking is recorded at the fetch
    stage, as a run's are, and one reading the lyrics at the sidecar stage. One while
    asking shows its type alone, as a run's provider call does: its message may carry a
    token.
    """
    rel = path.relative_to(album).as_posix()
    stage, asking = ErrorStage.FETCH, False
    own = source if source in COMPARED else None
    recorded = source is not None
    try:
        tags = stages.read_tags(path)
        stage = ErrorStage.SIDECAR
        lyrics = _judged_lyrics(stages, album, path, tags, lang)
        if lyrics is None:
            return ComparedTrack(rel, CompareVerdict.NOT_JUDGED)
        stage = ErrorStage.FETCH
        query = track_query(tags, path)
        asking = True
        others = {k: v for k, v in providers.items() if own is None or k != _key(own)}
        asked = ask_each(query, others, deadline_s)
    # The per-track boundary, catching what a run's does: any failure is this track's
    # row, and the album goes on.
    except Exception as error:
        name = type(error).__name__
        message = name if asking else str(error) or name
        failure = StageError(stage, name, message)
        return ComparedTrack(rel, None, error=failure, own=own, recorded=recorded)
    row = _verdict(rel, normalize_words(lyrics.plain_text()), query, asked)
    return dataclasses.replace(row, own=own, recorded=recorded)


def _tally(
    tracks: Sequence[ComparedTrack], table: ProviderTable
) -> tuple[ProviderCount, ...]:
    """Each compared provider in the table: tracks whose answer agreed, over tracks asked.

    A failed call counts as refused; a provider not in the table has no count, as in a run.
    A track whose lyrics came from the provider was not asked it, so it is counted apart
    as its own, unless its row is an error: that track is not counted as set aside.
    """
    counts = []
    for source in COMPARED:
        if _key(source) not in table:
            counts.append(ProviderCount(source, 0, None))
            continue
        scores = [s for track in tracks for s in track.scores if s.source is source]
        agreed = sum(1 for score in scores if _agrees(score))
        refused = sum(1 for score in scores if score.outcome in REFUSED_OUTCOMES)
        own = sum(1 for track in tracks if track.own is source and track.error is None)
        counts.append(ProviderCount(source, agreed, len(scores), refused, own))
    return tuple(counts)


def compare_album(
    album: Path,
    tracks: Sequence[Path],
    providers: ProviderTable,
    stages: Stages,
    *,
    lang: str,
    deadline_s: float,
    notice: Callable[[str], None],
) -> CompareReport:
    """Compare each of *tracks* under *album* with *providers*' answers; write nothing.

    Each track's own source is read once from the album's run manifests. A run whose
    manifest cannot be read is passed over, and given to *notice* as one line, newest
    first, before any provider is asked.
    """
    records = recorded_sources(album, stages.read_manifest)
    for run_dir, kind in records.passed_over:
        shown_run = run_dir.relative_to(album).as_posix()
        notice(f"passed over the run {shown_run}: its manifest cannot be read ({kind})")
    rows = tuple(
        compare_track(
            album,
            path,
            providers,
            stages,
            lang=lang,
            deadline_s=deadline_s,
            source=records.source_of(path.relative_to(album).as_posix()),
        )
        for path in tracks
    )
    return CompareReport(rows, _tally(rows, providers))
