"""An album's run, counted from its manifest, and the rule for a refused album.

An album is refused only when no provider answered it: at least one track met a
refusal, and no track shows a provider answering. A track whose every provider failed
ends as the album tool's fetch error; any other end after a lookup means some provider
answered, even beside another's refusal, but for a track served its own file (untimed
lyrics, a transcript, a machine-timed ``.lrc``) after every LRC provider's synced step
refused. Refusals are counted by track and by provider, never entry by entry, since one
failed call can leave a refusal at each step that re-reads it.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

import sweep  # noqa: E402, F401 - its bootstrap puts src/ and the album tool on the path
import lyrics_categories as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal_tally as lrt  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_fixtures as sf  # noqa: E402 - import follows the sys.path bootstrap above
import sweep_tally  # noqa: E402 - import follows the sys.path bootstrap above


_FLAG = lr.CheckResult(flags=(lr.CheckReason(lr.CheckRule.PAST_END, "late"),), skips=())


class CountsTest(sf.IsolatedCase):
    """Each status lands in its own count; a flagged check is counted too."""

    def test_every_status_is_counted(self) -> None:
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(
                sf.written(),
                sf.status_outcome(lr.OutcomeStatus.WRITTEN, check=_FLAG),
                sf.status_outcome(lr.OutcomeStatus.UNCHANGED, source=lm.Source.SIDECAR),
                sf.not_found(),
                sf.status_outcome(lr.OutcomeStatus.KEPT_EXISTING),
                sf.fetch_error(),
                None,
            )
        )
        self.assertEqual(
            tally.counts,
            sweep_tally.Counts(
                written=2,
                unchanged=1,
                not_found=1,
                kept=1,
                errors=1,
                flagged=1,
                # The flagged one records no kind, so what it got is unknown.
                written_by={
                    lc.WrittenCategory.NEW_SYNCED: 1,
                    lc.WrittenCategory.BEFORE_UNKNOWN: 1,
                },
            ),
        )

    def test_a_removed_track_counts_apart_from_not_found(self) -> None:
        # A refetch that found nothing and removed implausible old lyrics: counted
        # removed, by its reason, never among the not found.
        removed = sf.status_outcome(lr.OutcomeStatus.REMOVED)
        tally = sweep_tally.tally_manifest(sf.manifest_of(removed, sf.not_found()))
        self.assertEqual(
            tally.counts,
            sweep_tally.Counts(
                not_found=1, removed=1, removed_by={lc.RemovedReason.OTHER: 1}
            ),
        )

    def test_written_tracks_are_counted_by_category(self) -> None:
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(sf.written(), sf.written(lm.Source.SIDECAR))
        )
        self.assertEqual(
            tally.counts.written_by,
            {lc.WrittenCategory.NEW_SYNCED: 1, lc.WrittenCategory.EMBEDDED: 1},
        )

    def test_the_counts_and_the_tally_cannot_be_changed_once_made(self) -> None:
        tally = sweep_tally.tally_manifest(sf.manifest_of(sf.written()))
        with self.assertRaises(FrozenInstanceError):
            tally.refused = True  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            tally.counts.written = 0  # type: ignore[misc]

    def test_no_lyrics_expected_is_counted_apart_from_not_found(self) -> None:
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(
                sf.status_outcome(lr.OutcomeStatus.NO_LYRICS_EXPECTED), sf.not_found()
            )
        )
        self.assertEqual(
            tally.counts, sweep_tally.Counts(not_found=1, no_lyrics_expected=1)
        )

    def test_refusals_are_counted_by_track_and_by_provider(self) -> None:
        # One failed call can leave a refusal at the synced step and another at the
        # untimed step: still one refusing track, and the provider named once.
        twice = (
            sf.refusal(lm.Source.LRCLIB),
            lr.ProviderRefusal(
                lm.Source.LRCLIB,
                lm.LyricsKind.UNSYNCED,
                "HTTP 429",
                lrf.Refusal.RATE_LIMITED,
            ),
        )
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(
                sf.not_found(*twice),
                sf.fetch_error(sf.refusal(lm.Source.NETEASE)),
                sf.written(),
            )
        )
        self.assertEqual(tally.refusing_tracks, 2)
        self.assertEqual(
            tally.refusing_providers, (lm.Source.LRCLIB.label, lm.Source.NETEASE.label)
        )


class UnsentTallyTest(sf.IsolatedCase):
    """A lookup an open breaker left unsent counts apart, and changes no verdict."""

    def _unsent(self) -> lr.ProviderRefusal:
        """Genius's step skipped by its open breaker, recorded under its last reason."""
        blocked = sf.refusal(lm.Source.GENIUS, "HTTP 403", lrf.Refusal.BLOCKED)
        return replace(blocked, detail="breaker open until 09:35:00 UTC", unsent=True)

    def test_the_album_s_refusals_line_counts_them_apart(self) -> None:
        blocked = sf.refusal(lm.Source.GENIUS, "HTTP 403", lrf.Refusal.BLOCKED)
        unsent = self._unsent()
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(sf.fetch_error(blocked), *[sf.fetch_error(unsent)] * 2)
        )
        self.assertEqual(
            lrt.format_refusals(tally.refusals),
            "refusals: Genius blocked 1, breaker open 2",
        )

    def test_an_album_only_left_unsent_is_still_refused(self) -> None:
        unsent = self._unsent()
        tally = sweep_tally.tally_manifest(
            sf.manifest_of(sf.fetch_error(unsent), sf.fetch_error(unsent))
        )
        self.assertEqual((tally.refused, tally.refusing_tracks), (True, 2))


#: The end states no run records: a strip's alone, which the sweep never runs.
_NOT_FROM_A_RUN = frozenset({lr.OutcomeStatus.STRIPPED})


class EveryStatusTest(sf.IsolatedCase):
    """Each end state a run records lands in exactly one count; none is dropped."""

    def test_each_run_status_lands_in_exactly_one_count(self) -> None:
        # A status the album tool adds later fails here until it is counted, rather
        # than vanishing from every total.
        for status in set(lr.OutcomeStatus) - _NOT_FROM_A_RUN:
            with self.subTest(status=status):
                counts = sweep_tally.tally_manifest(
                    sf.manifest_of(sf.status_outcome(status))
                ).counts
                # The categories split the written and removed counts by kind and
                # reason: a breakdown of a count, never an end state of their own.
                landed = {
                    name: value
                    for name, value in vars(counts).items()
                    if value and name not in {"flagged", "written_by", "removed_by"}
                }
                self.assertEqual(list(landed.values()), [1])

    def test_a_strip_status_is_the_one_counted_nowhere(self) -> None:
        self.assertEqual(_NOT_FROM_A_RUN, {lr.OutcomeStatus.STRIPPED})
        counts = sweep_tally.tally_manifest(
            sf.manifest_of(sf.status_outcome(lr.OutcomeStatus.STRIPPED))
        ).counts
        self.assertEqual(counts, sweep_tally.Counts())


class RefusedRuleTest(sf.IsolatedCase):
    """When an album counts as refused."""

    def _refused(self, *outcomes: lr.TrackOutcome | None) -> bool:
        return sweep_tally.tally_manifest(sf.manifest_of(*outcomes)).refused

    def test_only_refusals_and_failures_is_refused(self) -> None:
        self.assertTrue(self._refused(*sf.REFUSED))

    def test_a_track_a_provider_answered_means_not_refused(self) -> None:
        self.assertFalse(self._refused(*sf.REFUSED, sf.written(lm.Source.NETEASE)))

    def test_a_provider_answer_beside_its_own_refusal_still_counts(self) -> None:
        answered = lr.TrackOutcome(
            lr.OutcomeStatus.UNCHANGED,
            lm.Source.NETEASE,
            lm.LyricsKind.SYNCED,
            refusals=(sf.refusal(lm.Source.LRCLIB),),
        )
        self.assertFalse(self._refused(sf.fetch_error(sf.refusal()), answered))

    def test_a_track_not_found_without_refusals_means_not_refused(self) -> None:
        # The providers answered, emptily: that is no refusal.
        self.assertFalse(self._refused(*sf.REFUSED, sf.not_found()))

    def test_no_lyrics_expected_beside_a_refusal_is_an_answer(self) -> None:
        # Like not found: some provider answered, emptily, or every one failed and the
        # track would be the fetch error instead.
        marked = sf.status_outcome(
            lr.OutcomeStatus.NO_LYRICS_EXPECTED, refusals=(sf.refusal(),)
        )
        self.assertFalse(self._refused(*sf.REFUSED, marked))

    def test_a_track_kept_without_refusals_means_not_refused(self) -> None:
        kept = sf.status_outcome(lr.OutcomeStatus.KEPT_EXISTING)
        self.assertFalse(self._refused(*sf.REFUSED, kept))

    def test_untimed_lyrics_kept_after_an_empty_timed_lookup_mean_not_refused(
        self,
    ) -> None:
        # A track served its own untimed sidecar was first looked up for synced
        # lyrics; with no refusal recorded, the providers answered that lookup.
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.SIDECAR,
            kind=lm.LyricsKind.UNSYNCED,
        )
        self.assertFalse(self._refused(*sf.REFUSED, served))

    def test_synced_lyrics_served_without_asking_leave_the_verdict_alone(
        self,
    ) -> None:
        # A synced sidecar or frame serves the track with no provider asked, so it
        # says nothing about the providers either way.
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.FRAMES,
            kind=lm.LyricsKind.SYNCED,
        )
        self.assertTrue(self._refused(*sf.REFUSED, served))

    def test_untimed_lyrics_served_beside_one_refusal_are_an_answer(self) -> None:
        # Only the synced steps are asked for a track holding untimed lyrics: one of
        # them refusing leaves the other, which answered, emptily.
        served = sf.status_outcome(
            lr.OutcomeStatus.WRITTEN,
            source=lm.Source.SIDECAR,
            kind=lm.LyricsKind.UNSYNCED,
            refusals=(sf.refusal(),),
        )
        self.assertFalse(self._refused(served))

    def test_untimed_lyrics_served_after_every_synced_step_refused_are_not(
        self,
    ) -> None:
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.SIDECAR,
            kind=lm.LyricsKind.UNSYNCED,
            refusals=(sf.refusal(lm.Source.LRCLIB), sf.refusal(lm.Source.NETEASE)),
        )
        self.assertTrue(self._refused(served))

    def test_untimed_lyrics_served_after_every_asked_synced_step_refused_are_not(
        self,
    ) -> None:
        # A run that left NetEase out asked LRCLIB's synced step alone, so its refusal
        # is every synced step the run asked refusing.
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.SIDECAR,
            kind=lm.LyricsKind.UNSYNCED,
            refusals=(sf.refusal(lm.Source.LRCLIB),),
        )
        manifest = sf.manifest_of(served)
        self.assertFalse(sweep_tally.tally_manifest(manifest).refused)
        lrclib = frozenset({lm.Source.LRCLIB})
        self.assertTrue(sweep_tally.tally_manifest(manifest, synced=lrclib).refused)

    def test_a_transcript_after_every_synced_step_refused_is_no_answer(self) -> None:
        # A transcript is an own file, looked up for synced lyrics only, so it
        # shows no answer when every LRC provider's synced step refused.
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.MACHINE_TRANSCRIPT,
            kind=lm.LyricsKind.SYNCED,
            refusals=(sf.refusal(lm.Source.LRCLIB), sf.refusal(lm.Source.NETEASE)),
        )
        self.assertTrue(self._refused(served))

    def test_a_transcript_beside_one_refusal_is_an_answer(self) -> None:
        # Its other synced step did not refuse, so it answered, emptily.
        served = sf.status_outcome(
            lr.OutcomeStatus.WRITTEN,
            source=lm.Source.MACHINE_TRANSCRIPT,
            kind=lm.LyricsKind.SYNCED,
            refusals=(sf.refusal(),),
        )
        self.assertFalse(self._refused(served))

    def test_a_timed_lrc_after_every_synced_step_refused_is_no_answer(self) -> None:
        # The sync tool's .lrc is an own file looked up for synced lyrics only, as a
        # transcript is.
        served = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.MACHINE_TIMED,
            kind=lm.LyricsKind.SYNCED,
            refusals=(sf.refusal(lm.Source.LRCLIB), sf.refusal(lm.Source.NETEASE)),
        )
        self.assertTrue(self._refused(served))

    def test_a_timed_lrc_beside_one_refusal_is_an_answer(self) -> None:
        served = sf.status_outcome(
            lr.OutcomeStatus.WRITTEN,
            source=lm.Source.MACHINE_TIMED,
            kind=lm.LyricsKind.SYNCED,
            refusals=(sf.refusal(),),
        )
        self.assertFalse(self._refused(served))

    def test_not_found_beside_a_refusal_is_an_answer(self) -> None:
        # Had every provider failed, the album tool would have recorded a fetch error:
        # a track ending not found heard from some provider, emptily.
        self.assertFalse(self._refused(*sf.REFUSED, sf.not_found(sf.refusal())))

    def test_kept_beside_a_refusal_is_an_answer(self) -> None:
        kept = sf.status_outcome(
            lr.OutcomeStatus.KEPT_EXISTING, refusals=(sf.refusal(),)
        )
        self.assertFalse(self._refused(*sf.REFUSED, kept))

    def test_removed_beside_a_refusal_is_an_answer(self) -> None:
        # A refetch removes implausible old lyrics only when some provider answered,
        # emptily; had every one failed, the track would be the fetch error.
        removed = sf.status_outcome(lr.OutcomeStatus.REMOVED, refusals=(sf.refusal(),))
        self.assertFalse(self._refused(*sf.REFUSED, removed))

    def test_one_provider_refusing_while_another_answers_emptily_is_done(
        self,
    ) -> None:
        tracks = [sf.not_found(sf.refusal(lm.Source.LRCLIB)) for _ in range(3)]
        tally = sweep_tally.tally_manifest(sf.manifest_of(*tracks))
        self.assertFalse(tally.refused)
        # The refusing provider is still on record.
        self.assertEqual(tally.refusing_providers, (lm.Source.LRCLIB.label,))
        self.assertEqual(tally.refusing_tracks, 3)

    def test_a_provider_answer_that_failed_later_is_still_an_answer(self) -> None:
        error = lr.StageError(lr.ErrorStage.EMBED, "OSError", "disk full")
        failed = lr.TrackOutcome(
            lr.OutcomeStatus.ERROR,
            lm.Source.NETEASE,
            lm.LyricsKind.SYNCED,
            error=error,
            refusals=(sf.refusal(lm.Source.LRCLIB),),
        )
        self.assertFalse(self._refused(*sf.REFUSED, failed))

    def test_every_track_served_from_its_own_files_is_not_refused(self) -> None:
        served = [
            sf.status_outcome(
                lr.OutcomeStatus.UNCHANGED, source=src, kind=lm.LyricsKind.SYNCED
            )
            for src in (lm.Source.SIDECAR, lm.Source.FRAMES)
        ]
        self.assertFalse(self._refused(*served))

    def test_every_provider_failing_on_every_track_is_refused(self) -> None:
        both = (sf.refusal(lm.Source.LRCLIB), sf.refusal(lm.Source.NETEASE))
        self.assertTrue(self._refused(*(sf.fetch_error(*both) for _ in range(4))))

    def test_a_mixed_album_with_one_answer_is_not_refused(self) -> None:
        own = sf.status_outcome(
            lr.OutcomeStatus.UNCHANGED,
            source=lm.Source.FRAMES,
            kind=lm.LyricsKind.SYNCED,
        )
        self.assertFalse(
            self._refused(own, *sf.REFUSED, sf.not_found(sf.refusal()), None)
        )
        self.assertTrue(self._refused(own, *sf.REFUSED, None))

    def test_no_refusal_at_all_is_not_refused(self) -> None:
        self.assertFalse(self._refused(sf.fetch_error(), None))

    def test_an_empty_run_is_not_refused(self) -> None:
        self.assertFalse(self._refused())
