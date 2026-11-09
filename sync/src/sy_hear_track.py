"""One track heard, and its hearing kept: the child's reply, the log, the store and the list.

`Hearer.hear` runs the child on one planned track, asking it not to align, and turns
its reply into the track's outcome. `Recorder.record` keeps what was heard: every
hearing in the hearing log, and a text not sung, as a timing run keeps it
(`sy_heard_dir.Kept`), also in the ``--heard-dir`` store and on the ``--by-hand-list``.
A transcript longer than the store keeps is logged as none, its verdict kept, and said
beneath the row. A record that cannot be written is said beneath the row too, and the
run then exits 1. Its caller holds Ctrl-C around `Recorder.record`, so a track's log
record, its store record and its row are kept or lost as one. Nothing here prints.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import sy_located  # noqa: F401 - puts the other tools and libraries on sys.path
import at_engine
import sy_engine
import sy_heard_dir
from lyrics_heard import shown_share, text_fingerprint
from lyrics_hearing_log import (
    MAX_TRANSCRIPT_BYTES,
    STORE_ERRORS,
    HeardKind,
    HeardVerdict,
    HearingRecord,
    write_hearing,
)
from sy_hear_plan import LOG_OPTION, HearPlan
from sy_hear_rows import OF_VERDICT, HearOutcome, HearResult, heard_detail
from sy_request import HeardOnly, Request
from sy_text import tokenize
from sy_types import (
    MAX_TRACK_S,
    TEMP_PREFIX,
    Action,
    Outcome,
    Planned,
    TrackResult,
    Whisper,
)

#: The note beneath a hearing whose transcript is longer than the log keeps.
UNLOGGED: Final = (
    f"{LOG_OPTION}: what was heard is over {MAX_TRANSCRIPT_BYTES // 1024} KB"
    " and was not kept; its verdict was logged"
)


def _kind_of(plan: HearPlan) -> HeardKind:
    """The kind of text *plan* hears: a plan to hear always carries it."""
    assert plan.kind is not None, plan
    return plan.kind


@dataclass(frozen=True)
class Heard:
    """A track heard: its outcome and row, and the child's reply when there was one."""

    result: HearResult
    reply: HeardOnly | None = None


@dataclass(frozen=True)
class Hearer:
    """What hears each track: the sync models, whisper-cli and the model, the runner."""

    model_dir: Path
    whisper: Whisper
    model: str
    run: at_engine.Runner

    def hear(self, plan: HearPlan) -> Heard:
        """*plan*'s track heard by the child, or why it was not.

        A track whose tags claim more than `MAX_TRACK_S` seconds is skipped with no
        child, as a timing run skips it.
        """
        seconds = sy_engine.track_seconds(plan.audio)
        if seconds is not None and seconds > MAX_TRACK_S:
            detail = f"{seconds:.0f} s, over the {MAX_TRACK_S} s cap"
            skipped = HearResult(plan.audio, HearOutcome.SKIP_LONG_TRACK, (detail,))
            return Heard(skipped)
        tokens = tokenize(plan.lines)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as work:
            request = Request(
                plan.audio,
                tokens.ids,
                self.model_dir,
                tokens.line_of,
                self.whisper.cli,
                self.whisper.model_dir,
                align=False,
                whisper_model=self.model,
            )
            back = sy_engine.hear(request, Path(work), self.run, seconds)
        reply = back.reply
        if reply is None:
            return Heard(HearResult(plan.audio, HearOutcome.ERROR_HEAR, back.detail))
        detail = heard_detail(_kind_of(plan), reply.heard, reply.verdict)
        outcome = OF_VERDICT[reply.verdict]
        return Heard(HearResult(plan.audio, outcome, (detail,)), reply)


class Recorder:
    """The hearing log and the timing run's keeping, as this run writes them."""

    def __init__(self, folder: Path, model: str, kept: sy_heard_dir.Kept) -> None:
        """Log in *folder* each hearing by *model*; keep a text not sung by *kept*."""
        self.folder = folder
        self.model = model
        self.kept = kept
        #: How many hearings this run logged, and whether any log write failed.
        self.added = 0
        self.failed = False

    def record(self, plan: HearPlan, heard: Heard) -> HearResult:
        """Keep *heard*, what was heard of *plan*'s track; its result, any note added."""
        reply = heard.reply
        if reply is None:
            return heard.result
        notes = self._log(plan, reply)
        if reply.verdict is HeardVerdict.NOT_SUNG:
            notes += self._keep(plan, reply)
        result = heard.result
        return replace(result, detail=(*result.detail, *notes))

    def _log(self, plan: HearPlan, reply: HeardOnly) -> tuple[str, ...]:
        """Write *reply* to the log; the notes for its row."""
        record = HearingRecord(
            plan.log_key,
            text_fingerprint(plan.lines),
            _kind_of(plan),
            self.model,
            reply.heard.pairs,
            reply.heard.share,
            reply.verdict,
            reply.transcript or "",
        )
        unkept = () if reply.transcript is not None else (UNLOGGED,)
        try:
            write_hearing(self.folder, record)
        except STORE_ERRORS as error:
            self.failed = True
            said = f"{LOG_OPTION}: could not keep the hearing in {self.folder}: {error}"
            return (said,)
        self.added += 1
        return unkept

    def _keep(self, plan: HearPlan, reply: HeardOnly) -> tuple[str, ...]:
        """Keep a text not sung as a timing run keeps it; the notes for its row.

        The --heard-dir record is of the text heard, so the plan given it is a timing
        plan of the same lines.
        """
        timing = Planned(plan.audio, Action.TIME, lines=plan.lines)
        unsung = TrackResult(
            plan.audio,
            Outcome.NOT_SUNG,
            share=shown_share(reply.heard),
            transcript=reply.transcript,
        )
        return self.kept.record(timing, unsung).detail

    @property
    def any_failed(self) -> bool:
        """Whether any record of this run, in the log, the store or the list, failed."""
        kept = self.kept
        return self.failed or kept.recorder.failed or kept.keeper.failed
