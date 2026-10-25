"""The fetch chain: the sidecar first, then the synced steps, the plain ones, a retry.

Every provider here is a fake from fixtures.py, keyed into a table the way the real one
is, so each branch of the chain runs without the network. Every lyric is invented.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_fetch as lf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_placeholder as ph  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_stages as ls  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import step_key  # noqa: E402 - import follows the sys.path bootstrap above

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB, NETEASE = lm.Source.LRCLIB, lm.Source.NETEASE
GENIUS = lm.Source.GENIUS
#: Generous for a fake that answers at once; the hang tests pass their own.
DEADLINE_S = 5.0
#: Short enough that a hang test runs in milliseconds.
HANG_DEADLINE_S = 0.05
QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts (Live) [2020]")
PLAIN_TITLE_QUERY = lm.TrackQuery("Invented Artist", "The Keeper Counts")
NO_SIDECAR = lm.SidecarState(None)

#: The protocol binding: mypy fails the gate if fetch_lyrics drifts from it.
_BOUND: ls.FetchLyrics = lf.fetch_lyrics


def _outcomes(result: lm.FetchResult) -> list[tuple[lm.ProviderKey, lm.AttemptOutcome]]:
    """Each attempt's step and outcome, in order."""
    return [(attempt.key, attempt.outcome) for attempt in result.attempts]


class ChainOrderTest(fixtures.IsolatedCase):
    """The chain's steps and their order are one module constant."""

    def test_three_synced_steps_then_six_plain(self) -> None:
        self.assertEqual(lf.LRC_PROVIDERS, (LRCLIB, NETEASE))
        self.assertEqual(
            lf.CHAIN,
            (
                step_key(LRCLIB, S),
                step_key(NETEASE, S),
                step_key(lm.Source.YOUTUBE_MUSIC, S),
                step_key(LRCLIB, U),
                step_key(NETEASE, U),
                step_key(lm.Source.BANDCAMP, U),
                step_key(lm.Source.YOUTUBE_MUSIC, U),
                step_key(GENIUS, U),
                step_key(lm.Source.TEKSTOWO, U),
            ),
        )

    def test_the_real_table_never_asks_musixmatch(self) -> None:
        table = ltable.build_provider_table(None)
        optional = {GENIUS, lm.Source.BANDCAMP, lm.Source.YOUTUBE_MUSIC}
        optional.add(lm.Source.TEKSTOWO)
        self.assertEqual(set(table), {k for k in lf.CHAIN if k.source not in optional})
        self.assertNotIn(lm.Source.MUSIXMATCH, {key.source for key in table})


class SyncedStepsTest(fixtures.IsolatedCase):
    """Steps 1-2: the first timed answer wins and stops the chain."""

    def test_synced_from_each_provider(self) -> None:
        for source in lf.LRC_PROVIDERS:
            with self.subTest(source=source):
                answer = fixtures.answering(fixtures.LIGHTHOUSE_LRC)
                table = fixtures.chain_table({step_key(source, S): answer})
                result = lf.fetch_lyrics(
                    PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
                )
                self.assertIs(result.source, source)
                self.assertEqual(result.lyrics, lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))
                self.assertEqual(answer.calls, [PLAIN_TITLE_QUERY])
                self.assertEqual(
                    _outcomes(result)[-1],
                    (step_key(source, S), lm.AttemptOutcome.ANSWERED),
                )

    def test_the_first_accepted_answer_stops_the_chain(self) -> None:
        later = fixtures.answering(fixtures.LIGHTHOUSE_LRC)
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
                step_key(NETEASE, S): later,
            }
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertIs(result.source, LRCLIB)
        self.assertEqual(later.calls, [])
        self.assertEqual(len(result.attempts), 1)

    def test_an_untimed_synced_answer_is_demoted_to_the_plain_steps(self) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(fixtures.LIGHTHOUSE_PLAIN),
                step_key(LRCLIB, U): fixtures.answering(fixtures.LIGHTHOUSE_PLAIN),
            }
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertEqual(result.lyrics, lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN))
        self.assertIs(result.source, LRCLIB)
        first = result.attempts[0]
        self.assertIs(first.outcome, lm.AttemptOutcome.EMPTY)
        self.assertEqual(first.detail, lf.DETAIL_UNTIMED)


class PlainStepsTest(fixtures.IsolatedCase):
    """Steps 3-5: plain text, once no provider had it timed."""

    def test_unsynced_falls_through_from_each_provider(self) -> None:
        for source in lf.LRC_PROVIDERS:
            with self.subTest(source=source):
                table = fixtures.chain_table(
                    {step_key(source, U): fixtures.answering(fixtures.ORCHARD_PLAIN)}
                )
                result = lf.fetch_lyrics(
                    PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
                )
                self.assertEqual(result.lyrics, lm.PlainLyrics(fixtures.ORCHARD_PLAIN))
                self.assertIs(result.source, source)

    def test_plain_text_loses_its_surrounding_whitespace(self) -> None:
        answer = "\n\n  " + fixtures.ORCHARD_PLAIN + "  \n"
        table = fixtures.chain_table({step_key(LRCLIB, U): fixtures.answering(answer)})
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertEqual(result.lyrics, lm.PlainLyrics(fixtures.ORCHARD_PLAIN))

    def test_a_timed_answer_at_a_plain_step_is_kept_as_plain_text(self) -> None:
        table = fixtures.chain_table(
            {step_key(NETEASE, U): fixtures.answering(fixtures.LIGHTHOUSE_LRC)}
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        expected = lm.parse_lrc(fixtures.LIGHTHOUSE_LRC).plain_text()
        self.assertEqual(result.lyrics, lm.PlainLyrics(expected))

    def test_an_untimed_answer_with_one_stamped_line_keeps_every_line(self) -> None:
        # One stamped line made the whole answer parse as timed, so only it was kept.
        answer = "The tide turns\n[00:12.00]and waits\n\nby the harbor wall\n"
        table = fixtures.chain_table({step_key(LRCLIB, U): fixtures.answering(answer)})
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        expected = "The tide turns\nand waits\n\nby the harbor wall"
        self.assertEqual(result.lyrics, lm.PlainLyrics(expected))

    def test_a_partly_stamped_answer_drops_its_header_tag_lines(self) -> None:
        answer = "[ar:Invented Artist]\nThe tide turns\n[00:12.00][00:40.00]and waits\n"
        table = fixtures.chain_table({step_key(LRCLIB, U): fixtures.answering(answer)})
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertEqual(result.lyrics, lm.PlainLyrics("The tide turns\nand waits"))

    def test_genius_is_used_before_tekstowo_and_its_text_cleaned(self) -> None:
        raw = (
            "12 ContributorsThe Keeper Counts Lyrics"
            + fixtures.ORCHARD_PLAIN
            + "34Embed"
        )
        genius = fixtures.answering(raw)
        table = fixtures.chain_table({step_key(GENIUS, U): genius})
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertIs(result.source, GENIUS)
        self.assertEqual(result.lyrics, lm.PlainLyrics(fixtures.ORCHARD_PLAIN))
        # Every step up to Genius's is asked; Tekstowo's, after it, is not.
        self.assertEqual(len(result.attempts), len(lf.CHAIN) - 1)

    def test_without_a_genius_entry_the_step_is_skipped(self) -> None:
        table = fixtures.chain_table({})
        del table[step_key(GENIUS, U)]
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertFalse(result.found)
        self.assertEqual(len(result.attempts), len(lf.CHAIN) - 1)
        self.assertNotIn(GENIUS, {attempt.key.source for attempt in result.attempts})

    def test_an_empty_table_finds_nothing_and_is_not_an_error(self) -> None:
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, {}, deadline_s=DEADLINE_S)
        self.assertFalse(result.found)
        self.assertEqual(result.attempts, ())
        self.assertFalse(result.every_provider_failed)
        self.assertEqual(result.query, QUERY)


class _WidenedSlowAnswer(fixtures.FakeProvider):
    """Answers only after outliving the plain deadline, having asked for a wider one."""

    #: Longer than HANG_DEADLINE_S, far shorter than the bound it asks for.
    DELAY_S = 0.2

    def __init__(self, text: str) -> None:
        """Answer *text* once the delay has passed."""
        super().__init__(answer=text)
        self.bounds_asked = 0

    def step_deadline_s(self) -> float:
        """Count the ask; ask for room past the delay."""
        self.bounds_asked += 1
        return DEADLINE_S

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Wait out the delay, then answer."""
        threading.Event().wait(self.DELAY_S)
        return super().__call__(query)


class NotFoundAndErrorsTest(fixtures.IsolatedCase):
    """Every step empty is not found; every step failing is the fetch error."""

    def test_all_empty_is_not_found(self) -> None:
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY,
            NO_SIDECAR,
            fixtures.chain_table({}),
            deadline_s=DEADLINE_S,
        )
        self.assertFalse(result.found)
        self.assertFalse(result.every_provider_failed)
        self.assertEqual(
            {attempt.outcome for attempt in result.attempts}, {lm.AttemptOutcome.EMPTY}
        )

    def test_all_raising_is_the_fetch_error_naming_only_the_error_type(self) -> None:
        table = {key: fixtures.raising(OSError("secret detail")) for key in lf.CHAIN}
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertTrue(result.every_provider_failed)
        self.assertEqual({attempt.detail for attempt in result.attempts}, {"OSError"})

    def test_an_http_status_error_is_recorded_by_its_status(self) -> None:
        error = lp.HttpStatusError(LRCLIB, 429)
        table = {step_key(LRCLIB, S): fixtures.raising(error)}
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertEqual([attempt.detail for attempt in result.attempts], ["HTTP 429"])

    def test_a_provider_error_does_not_stop_the_chain(self) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.raising(RuntimeError("down")),
                step_key(NETEASE, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
            }
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertIs(result.source, NETEASE)
        self.assertEqual(
            _outcomes(result),
            [
                (step_key(LRCLIB, S), lm.AttemptOutcome.ERROR),
                (step_key(NETEASE, S), lm.AttemptOutcome.ANSWERED),
            ],
        )

    def test_an_answer_stamped_with_thousands_of_digits_does_not_end_the_chain(
        self,
    ) -> None:
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.answering(f"[{'9' * 5000}:00]a line"),
                step_key(NETEASE, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
            }
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertIs(result.source, NETEASE)
        self.assertEqual(
            [(a.outcome, a.detail) for a in result.attempts][0],
            (lm.AttemptOutcome.EMPTY, lf.DETAIL_UNTIMED),
        )

    def test_a_hanging_provider_is_abandoned_at_the_deadline(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        table = fixtures.chain_table(
            {
                step_key(LRCLIB, S): fixtures.hanging(release),
                step_key(NETEASE, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC),
            }
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=HANG_DEADLINE_S
        )
        self.assertIs(result.source, NETEASE)
        self.assertIs(result.attempts[0].outcome, lm.AttemptOutcome.TIMEOUT)

    def test_a_provider_that_widens_its_deadline_gets_the_wider_bound(self) -> None:
        slow = _WidenedSlowAnswer(fixtures.ORCHARD_PLAIN)
        table = fixtures.chain_table({step_key(LRCLIB, U): slow})
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=HANG_DEADLINE_S
        )
        self.assertIs(result.source, LRCLIB)
        self.assertEqual(slow.bounds_asked, 1)

    def test_every_step_hanging_is_the_fetch_error(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        table = {key: fixtures.hanging(release) for key in lf.CHAIN}
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=HANG_DEADLINE_S
        )
        self.assertTrue(result.every_provider_failed)

    def test_a_placeholder_answer_is_not_found(self) -> None:
        for placeholder in sorted(ph.INSTRUMENTAL_PLACEHOLDERS):
            with self.subTest(placeholder=placeholder.encode("unicode_escape")):
                table = fixtures.chain_table(
                    {
                        step_key(NETEASE, S): fixtures.answering(
                            f"[00:00.00]{placeholder}\n"
                        ),
                        step_key(NETEASE, U): fixtures.answering(f" {placeholder} \n"),
                    }
                )
                result = lf.fetch_lyrics(
                    PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
                )
                self.assertFalse(result.found)
                details = {attempt.detail for attempt in result.attempts}
                self.assertIn(lf.DETAIL_PLACEHOLDER, details)

    def test_whitespace_only_is_not_found(self) -> None:
        table = fixtures.chain_table(
            {step_key(LRCLIB, U): fixtures.answering(" \n\t\n")}
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S
        )
        self.assertFalse(result.found)


class _AnswersStrippedOnly(fixtures.FakeProvider):
    """A provider that knows the song only by its stripped title."""

    def __call__(self, query: lm.TrackQuery) -> str | None:
        """Record *query*; answer only the stripped one."""
        self.calls.append(query)
        return fixtures.LIGHTHOUSE_LRC if query.stripped else None


class StrippedRetryTest(fixtures.IsolatedCase):
    """The chain runs once more on the stripped title, only when nothing was found."""

    def test_nothing_found_retries_with_the_stripped_title(self) -> None:
        netease = _AnswersStrippedOnly()
        table = fixtures.chain_table({step_key(NETEASE, S): netease})
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertIs(result.source, NETEASE)
        self.assertEqual(result.query.title, "The Keeper Counts")
        self.assertTrue(result.query.stripped)
        self.assertEqual(result.query.markers, (lm.QueryMarker.STRIPPED,))
        self.assertEqual([call.stripped for call in netease.calls], [False, True])
        # The whole first pass, then the stripped pass up to its first answer.
        self.assertEqual(
            [attempt.query_stripped for attempt in result.attempts],
            [False] * len(lf.CHAIN) + [True, True],
        )

    def test_genius_gets_the_stripped_title_too(self) -> None:
        genius = fixtures.empty()
        lf.fetch_lyrics(
            QUERY,
            NO_SIDECAR,
            fixtures.chain_table({step_key(GENIUS, U): genius}),
            deadline_s=DEADLINE_S,
        )
        self.assertEqual(
            [call.title for call in genius.calls], [QUERY.title, "The Keeper Counts"]
        )

    def test_a_found_retry_keeps_the_filename_marker(self) -> None:
        query = lm.TrackQuery("Invented Artist", "Track (Live)", from_filename=True)
        table = fixtures.chain_table({step_key(LRCLIB, S): _AnswersStrippedOnly()})
        result = lf.fetch_lyrics(query, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertEqual(
            result.query.markers, (lm.QueryMarker.FILENAME, lm.QueryMarker.STRIPPED)
        )

    def test_a_retry_that_also_finds_nothing_reports_the_original_query(self) -> None:
        query = lm.TrackQuery("Invented Artist", "Track (Live)", from_filename=True)
        result = lf.fetch_lyrics(
            query, NO_SIDECAR, fixtures.chain_table({}), deadline_s=DEADLINE_S
        )
        self.assertIs(result.query, query)
        self.assertEqual(result.query.markers, (lm.QueryMarker.FILENAME,))
        self.assertIsNone(result.lyrics)
        self.assertEqual(len(result.attempts), 2 * len(lf.CHAIN))

    def test_a_title_that_strips_to_itself_is_not_retried(self) -> None:
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY,
            NO_SIDECAR,
            fixtures.chain_table({}),
            deadline_s=DEADLINE_S,
        )
        self.assertEqual(len(result.attempts), len(lf.CHAIN))
        self.assertIs(result.query, PLAIN_TITLE_QUERY)

    def test_every_provider_failing_is_not_retried(self) -> None:
        table = {key: fixtures.raising(OSError("down")) for key in lf.CHAIN}
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertEqual(len(result.attempts), len(lf.CHAIN))
        self.assertFalse(result.query.stripped)
        self.assertTrue(result.every_provider_failed)

    def test_some_errors_and_some_empties_still_retry(self) -> None:
        table = fixtures.chain_table(
            {step_key(LRCLIB, S): fixtures.raising(OSError("down"))}
        )
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertTrue(result.attempts[-1].query_stripped)
        self.assertEqual(len(result.attempts), 2 * len(lf.CHAIN))

    def test_a_found_answer_is_not_retried(self) -> None:
        table = fixtures.chain_table(
            {step_key(LRCLIB, U): fixtures.answering(fixtures.ORCHARD_PLAIN)}
        )
        result = lf.fetch_lyrics(QUERY, NO_SIDECAR, table, deadline_s=DEADLINE_S)
        self.assertFalse(result.query.stripped)
        self.assertIs(result.source, LRCLIB)


class SidecarTest(fixtures.IsolatedCase):
    """An existing sidecar wins, except that a timed fetch beats a plain .txt."""

    def test_a_synced_sidecar_is_used_with_no_provider_call(self) -> None:
        sidecar = lm.SidecarState(lm.parse_lrc(fixtures.LIGHTHOUSE_LRC))
        table = {key: fixtures.answering(fixtures.LIGHTHOUSE_LRC) for key in lf.CHAIN}
        result = lf.fetch_lyrics(QUERY, sidecar, table, deadline_s=DEADLINE_S)
        self.assertIs(result.source, lm.Source.SIDECAR)
        self.assertEqual(result.lyrics, sidecar.lyrics)
        self.assertEqual(result.attempts, ())
        self.assertTrue(all(not fake.calls for fake in table.values()))

    def test_a_txt_sidecar_still_runs_the_synced_steps_and_a_timed_answer_wins(
        self,
    ) -> None:
        sidecar = lm.SidecarState(lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN))
        table = fixtures.chain_table(
            {step_key(NETEASE, S): fixtures.answering(fixtures.LIGHTHOUSE_LRC)}
        )
        result = lf.fetch_lyrics(
            PLAIN_TITLE_QUERY, sidecar, table, deadline_s=DEADLINE_S
        )
        self.assertIs(result.source, NETEASE)
        self.assertIs(result.lyrics.kind if result.lyrics else None, S)

    def test_a_txt_sidecar_is_used_when_no_timed_answer_and_the_plain_steps_skip(
        self,
    ) -> None:
        sidecar = lm.SidecarState(lm.PlainLyrics(fixtures.LIGHTHOUSE_PLAIN))
        plain_steps = {
            key: fixtures.answering(fixtures.ORCHARD_PLAIN)
            for key in lf.CHAIN
            if key.kind is U
        }
        table = fixtures.chain_table(dict(plain_steps))
        result = lf.fetch_lyrics(QUERY, sidecar, table, deadline_s=DEADLINE_S)
        self.assertIs(result.source, lm.Source.SIDECAR)
        self.assertEqual(result.lyrics, sidecar.lyrics)
        self.assertEqual({a.key.kind for a in result.attempts}, {S})
        self.assertEqual(len(result.attempts), sum(k.kind is S for k in lf.CHAIN))
        self.assertTrue(all(not fake.calls for fake in plain_steps.values()))
        self.assertFalse(result.query.stripped)
