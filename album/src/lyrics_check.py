"""The check: judge a track's embedded lyrics against its duration and its backup.

`check_track` binds to the `CheckTrack` stage protocol. It reads nothing and changes
nothing: the caller reads the frames back from the file and from the run's backup, takes
from each the lyrics of the frame that served the track (`lyrics_judge`), and passes
them in with the duration.

Seven rows, each a FLAG with its measured values beside it:

- **past end** (synced): the latest line starts after the audio ends, beyond a tolerance.
- **ends short** (synced): the latest line starts before half the track AND more than a
  minute before its end.
- **out of order** (synced): a line starts earlier than the one stored before it.
- **too many words** (any): more words a second than any song is sung at, such as a
  saved web page.
- **unlike backup** (any): the words resemble the backed-up lyrics too little, comparing
  the opening `MAX_COMPARED_WORDS` words of each.
- **no lyrics embedded** (any): a track the run wrote now holds no lyrics at all, lost
  since (removed by hand, or by another tool).
- **placeholder lyrics** (any): the embedded lyrics are a stand-in, not lyrics
  (`lyrics_placeholder`): a header tag such as ``[au:comedy]``, an instrumental notice,
  or timed lines with no words. Running the album again asks the providers afresh.

A duration that cannot be read makes the two duration rows and the word-rate row one
SKIP, never a FLAG.

Every threshold is an initial estimate, named below with its reasoning, to be re-tuned
against real runs; the measured value printed beside each FLAG is the data for that.

Pure: no I/O, no printing, standard library only. The runtime floor is Python 3.12.
"""

from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher

from lyrics_model import Lyrics, SyncedLyrics
from lyrics_placeholder import is_placeholder_lyrics
from lyrics_records import CheckReason, CheckResult, CheckRule

#: Seconds a line may start after the audio's reported end. Absorbs encoder padding and
#: the duration's rounding; nothing legitimate is sung after the audio stops.
PAST_END_TOLERANCE_S = 2.0
#: "Ends short" needs the last line before this fraction of the track...
SHORT_FRACTION = 0.5
#: ...AND more than this many seconds before its end. Both conditions, so a long
#: instrumental outro (the last line at 60-70% of a seven-minute track) is not flagged,
#: while the lyrics of a different, shorter song are.
SHORT_MIN_GAP_S = 60.0
#: The least word-sequence similarity to the backed-up lyrics that passes. Two
#: transcriptions of one song differ in punctuation, contractions and repeat-marking and
#: score well above it; two different songs share only common words and score below.
MIN_SIMILARITY = 0.6
#: The most words of each side the similarity compares, from the start. With the junk
#: heuristic off, the comparison's cost grows with the square of a text's repeats, so a
#: text of thousands of one word took seconds; a song's lyrics run to a few hundred
#: words, so its opening two thousand are the whole song.
MAX_COMPARED_WORDS = 2_000
#: The most words a second that passes, over the whole track. Above any sung song: a
#: fast rap verse sustains about 4 to 5 words a second and a whole song averages 1 to
#: 2, while a saved web page or another album's lyrics in one file runs to dozens.
MAX_WORDS_PER_SECOND = 6.0

_MS_PER_SECOND = 1000
#: Decimal places of the similarity, and of the word rate, shown in a FLAG.
_SHOWN_PLACES = 3
#: The SKIP's detail when the duration is unreadable, for synced and for untimed lyrics.
_SYNCED_ROWS_SKIPPED = "timing and too-many-words rows not checked"
_UNTIMED_ROWS_SKIPPED = "too-many-words row not checked"
#: A bracketed run (an LRC time stamp or tag, or a section header such as "[Chorus]"),
#: or an enhanced-LRC inline word stamp such as "<00:01.80>".
_STAMPS_AND_TAGS = re.compile(r"\[[^\]]*\]|<\d+:\d+(?:[.:]\d+)?>")
#: Anything that is neither a word character nor whitespace.
_NON_WORD = re.compile(r"[^\w\s]")
#: Straight and curly apostrophes: deleted, so a contraction and its apostrophe-less
#: spelling normalize alike.
_APOSTROPHES = frozenset("'\u2019")


def _drop_or_space(match: re.Match[str]) -> str:
    """Nothing for an apostrophe or a combining mark, else a space.

    A dash, slash or comma between two words separates them ("night-day" is the two
    words another transcription writes with a space), while an apostrophe or a
    combining mark sits inside one word and must not split it.
    """
    char = match.group()
    if char in _APOSTROPHES or unicodedata.category(char).startswith("M"):
        return ""
    return " "


# fuzz-target
def normalize_words(text: str) -> list[str]:
    """The words of *text* as the similarity row compares them.

    Casefolded, with bracketed runs (time stamps, tags, section headers) and inline word
    stamps removed, apostrophes and combining marks deleted, other punctuation turned
    into a space, and whitespace collapsed. Casefolding goes first, since it can yield
    characters (a combining mark, say) that the later steps then handle.
    """
    folded = text.casefold()
    unstamped = _STAMPS_AND_TAGS.sub(" ", folded)
    return _NON_WORD.sub(_drop_or_space, unstamped).split()


def shown(value: float) -> str:
    """*value* truncated to a few places, never rounded.

    Rounding could show a similarity under the minimum as the minimum itself (0.5996
    as "0.600"), so the FLAG would read "0.600 < 0.6"; truncating never shows more
    than was measured.
    """
    scale = 10**_SHOWN_PLACES
    return f"{math.floor(value * scale) / scale:.{_SHOWN_PLACES}f}"


def word_similarity(first: list[str], second: list[str]) -> float:
    """`difflib.SequenceMatcher`'s ratio between the openings of two word lists, 0 to 1.

    Only the first `MAX_COMPARED_WORDS` words of each list are compared: two lists
    differing only past them score as alike. The automatic junk heuristic is off: it
    discards words frequent in a long text, and a chorus repeats exactly the words
    that make two transcriptions alike.
    """
    opening, other = first[:MAX_COMPARED_WORDS], second[:MAX_COMPARED_WORDS]
    return SequenceMatcher(None, opening, other, autojunk=False).ratio()


def _seconds(ms: int) -> float:
    """*ms* in seconds."""
    return ms / _MS_PER_SECOND


def _usable_duration(duration_s: float | None) -> float | None:
    """*duration_s* when it is a finite positive length, else None (unreadable)."""
    if duration_s is None or not math.isfinite(duration_s) or duration_s <= 0:
        return None
    return duration_s


def _past_end(last_s: float, duration_s: float) -> CheckReason | None:
    """The past-end FLAG when the latest line starts beyond the tolerance."""
    if last_s <= duration_s + PAST_END_TOLERANCE_S:
        return None
    detail = f"last line {last_s:.3f} s, track {duration_s:.3f} s"
    return CheckReason(CheckRule.PAST_END, detail)


def _ends_short(last_s: float, duration_s: float) -> CheckReason | None:
    """The ends-short FLAG when the latest line is both early and far from the end."""
    early = last_s < duration_s * SHORT_FRACTION
    if not early or duration_s - last_s <= SHORT_MIN_GAP_S:
        return None
    detail = f"last line {last_s:.3f} s, track {duration_s:.3f} s"
    return CheckReason(CheckRule.ENDS_SHORT, detail)


def _out_of_order(lyrics: SyncedLyrics) -> CheckReason | None:
    """The out-of-order FLAG at the first line that starts before the one stored before it."""
    times = [line.ms for line in lyrics.lines]
    for index, (before, after) in enumerate(zip(times, times[1:]), start=1):
        if after < before:
            late, early = _seconds(before), _seconds(after)
            detail = f"line {index + 1} at {early:.3f} s follows {late:.3f} s"
            return CheckReason(CheckRule.OUT_OF_ORDER, detail)
    return None


def latest_line_s(lyrics: SyncedLyrics) -> float:
    """When the latest line of *lyrics* starts, in seconds: not the last stored.

    A disordered frame could otherwise hide a line past the end behind an earlier one.
    A fetch reads it too, to tell a far-past answer (`lyrics_accept.far_past_end`).
    """
    return _seconds(max(line.ms for line in lyrics.lines))


def past_end(lyrics: SyncedLyrics, duration_s: float | None) -> CheckReason | None:
    """The `past end` row alone: its FLAG for *lyrics*, as `check_track` gives it.

    None when the latest line starts within the tolerance, and when *duration_s*
    cannot be read, where the check would SKIP the row.
    """
    usable = _usable_duration(duration_s)
    return None if usable is None else _past_end(latest_line_s(lyrics), usable)


def _timing_rows(
    lyrics: SyncedLyrics, usable: float | None
) -> list[CheckReason | None]:
    """The synced rows' findings, in the check's row order, None for each that passed.

    *usable* None (an unreadable duration) leaves the order row alone. The latest line
    (`latest_line_s`), not the last stored, is judged against the duration.
    """
    if usable is None:
        return [_out_of_order(lyrics)]
    last_s = latest_line_s(lyrics)
    return [
        _past_end(last_s, usable),
        _ends_short(last_s, usable),
        _out_of_order(lyrics),
    ]


def _too_many_words(lyrics: Lyrics, usable: float | None) -> CheckReason | None:
    """The too-many-words FLAG when the words a second exceed the most a song sings.

    Words are counted as the similarity row counts them (`normalize_words`), so
    time stamps, tags and section headers are none. *usable* None passes: the caller
    records that SKIP.
    """
    if usable is None:
        return None
    count = len(normalize_words(lyrics.plain_text()))
    rate = count / usable
    if rate <= MAX_WORDS_PER_SECOND:
        return None
    detail = f"{shown(rate)} words per second: {count} words in {usable:.3f} s"
    return CheckReason(CheckRule.TOO_MANY_WORDS, detail)


def too_many_words(lyrics: Lyrics, duration_s: float | None) -> CheckReason | None:
    """The `too many words` row alone: its FLAG for *lyrics*, as `check_track` gives it.

    None when the rate passes, and when *duration_s* cannot be read, where the check
    would SKIP the row. A fetch and a refetch judge lyrics by it too
    (`implausible_flag`), so the rule lives here once.
    """
    return _too_many_words(lyrics, _usable_duration(duration_s))


def implausible_flag(lyrics: Lyrics, duration_s: float | None) -> CheckReason | None:
    """Why *lyrics* cannot be this track's: `past end` (synced only), else `too many words`.

    The two rows that say lyrics belong to another recording, so a fetch declines an
    answer by them and a refetch picks old lyrics by them. `ends short` is not one: a
    long outro sings nothing. None when both pass or *duration_s* cannot be read.
    """
    late = past_end(lyrics, duration_s) if isinstance(lyrics, SyncedLyrics) else None
    return late if late is not None else too_many_words(lyrics, duration_s)


def _unreadable_skip(lyrics: Lyrics) -> CheckReason:
    """The one SKIP an unreadable duration makes, naming the rows it left unchecked."""
    synced = isinstance(lyrics, SyncedLyrics)
    rows = _SYNCED_ROWS_SKIPPED if synced else _UNTIMED_ROWS_SKIPPED
    return CheckReason(CheckRule.DURATION_UNREADABLE, rows)


def _unlike_backup(embedded: Lyrics, backup: Lyrics | None) -> CheckReason | None:
    """The unlike-backup FLAG; None when the backup held no words to compare."""
    backup_words = normalize_words(backup.plain_text()) if backup is not None else []
    if not backup_words:
        return None
    similarity = word_similarity(normalize_words(embedded.plain_text()), backup_words)
    if similarity >= MIN_SIMILARITY:
        return None
    detail = f"similarity {shown(similarity)} < {MIN_SIMILARITY}"
    return CheckReason(CheckRule.UNLIKE_BACKUP, detail)


#: The placeholder FLAG's detail: what to do about it.
PLACEHOLDER_DETAIL = "run the album again to replace it"


def _placeholder(embedded: Lyrics) -> CheckReason | None:
    """The placeholder FLAG when *embedded* stands in for lyrics; None otherwise."""
    if not is_placeholder_lyrics(embedded):
        return None
    return CheckReason(CheckRule.PLACEHOLDER, PLACEHOLDER_DETAIL)


def check_track(
    embedded: Lyrics | None, backup: Lyrics | None, duration_s: float | None
) -> CheckResult:
    """Judge *embedded* lyrics against the track's *duration_s* and its *backup*.

    *embedded* None is a single `CheckRule.NO_LYRICS` FLAG: the check runs only on
    tracks the run wrote, so lyrics gone since are a problem to surface. *backup* None,
    or one holding no words, leaves the similarity row out. A *duration_s* that is None,
    not finite, or not positive makes the duration rows and the word-rate row one SKIP;
    the order row still runs. Lyrics that are a placeholder are that FLAG first.
    """
    if embedded is None:
        gone = CheckReason(CheckRule.NO_LYRICS, "no lyrics frame")
        return CheckResult((gone,), ())
    usable = _usable_duration(duration_s)
    found: list[CheckReason | None] = [_placeholder(embedded)]
    if isinstance(embedded, SyncedLyrics):
        found += _timing_rows(embedded, usable)
    found += [_too_many_words(embedded, usable), _unlike_backup(embedded, backup)]
    skips = () if usable is not None else (_unreadable_skip(embedded),)
    return CheckResult(tuple(flag for flag in found if flag is not None), skips)
