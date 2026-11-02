"""`--mode fix-text` on an album that looks spoken: a removal withheld, a match still written.

A stand-up or spoken-word track's transcript is speech, so no provider's text is sung
and a fix would remove a text that was right. So a fix withholds the removal when the
album looks spoken: a track of it tagged with a spoken genre (Comedy, Spoken Word), or
most of its tracks, strictly more than half, holding a kept ``not sung`` transcript. The
track keeps its text and its record, ends ``kept existing`` with the removal withheld,
and the summary counts it on its own line, never as an error. A text the transcript does
sing is still written. Each test drives the entry point with its real wiring; every
title, lyric and monologue is invented.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
from fix_fakes import (  # noqa: E402 - import follows the sys.path bootstrap above
    FOUNDRY,
    HARBOR,
    MEADOW,
    QUARRY,
    TITLES,
    FixCase,
    song_track,
    words,
)
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from test_lyrics_run_fresh_unlike import raw  # noqa: E402 - import follows the sys.path bootstrap above

#: Two invented monologues, as whisper-cli would hear a comic on stage: no song's words.
AIRPORT = """So I get to the airport three hours early because my uncle says you have to,
and the lady at the counter looks at me like I brought a sandwich to a wedding,
she asks where I am flying and I tell her I honestly have not decided yet,
and that is when the tall guy in the vest starts walking over very slowly.
"""
LAUNDRY = """My neighbor folds his laundry on the porch every single Sunday morning,
towels first then the socks then the shirts with the little cartoon ducks on them,
and he waves at everybody who drives past like he is running for mayor,
I asked him once why the porch and he said the dryer gets lonely inside.
"""
#: How a withheld track's row opens, and how the summary counts the withheld tracks.
WITHHELD_ROW = "kept existing: removal withheld, spoken word"
WITHHELD_LINE = "removal withheld: spoken word"
#: A stage set's length, in seconds.
LONG_S = 180.0
#: How a removed track's row reads.
WRONG_TEXT = "wrong text removed"


def genre_track(index: int, song: str, txt: str, *genres: str) -> TrackSpec:
    """Track *index* titled for *song*, holding *txt*, tagged with *genres*."""
    return dataclasses.replace(song_track(index, song, txt=txt), genres=genres)


class SpokenGenreTest(FixCase):
    """A spoken genre on the album withholds every removal on it."""

    def assert_withheld(self, paths: list[Path], name: str, why: str) -> None:
        """The fix ran, kept every file of *paths*, and withheld *name*'s removal."""
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        row = cli_fakes.row_cells(result.stdout, name)[-1]
        self.assertEqual(row, f"{WITHHELD_ROW} ({why})")
        self.assertIn(f"{WITHHELD_LINE} 1", result.stdout)
        self.assertNotIn("removed:", result.stdout)
        self.assertEqual(raw(paths), before)
        outcome = self.outcome(name)
        self.assertIs(outcome.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(outcome.withheld, f"spoken word ({why})")
        self.assertIsNone(outcome.error)
        self.assertIsNotNone(self.record_of(paths[0]))

    def test_a_comedy_album_keeps_its_text(self) -> None:
        paths = self.build(
            genre_track(1, HARBOR, QUARRY, "Comedy"),
            genre_track(2, MEADOW, MEADOW, "Comedy"),
            genre_track(3, FOUNDRY, FOUNDRY, "Comedy"),
        )
        self.hear(paths[0], AIRPORT)
        self.assert_withheld(paths, paths[0].name, "genre Comedy")

    def test_the_genre_is_matched_in_any_case(self) -> None:
        paths = self.build(
            genre_track(1, HARBOR, QUARRY, "spoken word"),
            genre_track(2, MEADOW, MEADOW),
            genre_track(3, FOUNDRY, FOUNDRY),
        )
        self.hear(paths[0], AIRPORT)
        self.assert_withheld(paths, paths[0].name, "genre spoken word")

    def test_another_track_s_genre_marks_the_album(self) -> None:
        paths = self.build(
            genre_track(1, HARBOR, QUARRY),
            genre_track(2, MEADOW, MEADOW, "Rock", "Spoken Word"),
            genre_track(3, FOUNDRY, FOUNDRY),
        )
        self.hear(paths[0], AIRPORT)
        self.assert_withheld(paths, paths[0].name, "genre Spoken Word")

    def test_an_id3v1_numeric_comedy_genre_marks_the_album(self) -> None:
        # ID3v1's genre 57 is Comedy; mutagen names the reference when it reads it.
        paths = self.build(
            genre_track(1, HARBOR, QUARRY, "(57)"),
            genre_track(2, MEADOW, MEADOW),
            genre_track(3, FOUNDRY, FOUNDRY),
        )
        self.hear(paths[0], AIRPORT)
        self.assert_withheld(paths, paths[0].name, "genre Comedy")

    def test_a_musical_comedy_genre_is_no_spoken_genre(self) -> None:
        paths = self.build(
            genre_track(1, HARBOR, QUARRY, "Musical Comedy"),
            genre_track(2, MEADOW, MEADOW),
            genre_track(3, FOUNDRY, FOUNDRY),
        )
        self.hear(paths[0], HARBOR)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(
            cli_fakes.row_cells(result.stdout, paths[0].name)[-1], WRONG_TEXT
        )

    def test_a_sung_text_is_still_written_on_a_comedy_album(self) -> None:
        # Long enough that the monologue's words are no implausible rate.
        long_track = dataclasses.replace(
            genre_track(1, HARBOR, QUARRY, "Comedy"), seconds=LONG_S
        )
        paths = self.build(long_track, genre_track(2, MEADOW, MEADOW, "Comedy"))
        self.hear(paths[0], AIRPORT)
        self.answer({TITLES[HARBOR]: AIRPORT})
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.WRITTEN)
        self.assertEqual(self.text_of(paths[0]), words(AIRPORT))
        self.assertNotIn(WITHHELD_LINE, result.stdout)

    def test_failing_providers_on_a_spoken_album_are_an_error_not_withheld(
        self,
    ) -> None:
        # The error guards run before the spoken one: a removal no provider could
        # vouch for is an error to retry, never a withheld removal that looks settled.
        paths = self.build(
            genre_track(1, HARBOR, QUARRY, "Comedy"),
            genre_track(2, MEADOW, FOUNDRY, "Comedy"),
        )
        self.hear(paths[0], AIRPORT)
        self.hear(paths[1], LAUNDRY)
        self.providers = run_fakes.failing_table()
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_TRACK_ERRORS, result.stdout)
        self.assertIn("every provider failed", result.stdout)
        self.assertNotIn(WITHHELD_LINE, result.stdout)
        self.assertEqual(raw(paths), before)
        for path in paths:
            outcome = self.outcome(path.name)
            self.assertIs(outcome.status, lr.OutcomeStatus.ERROR)
            self.assertIsNone(outcome.withheld)
            self.assertIsNotNone(self.record_of(path))


class MostlyNotSungTest(FixCase):
    """Most of the album's tracks heard not sung withholds every removal on it."""

    def test_most_tracks_not_sung_keep_their_texts(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY),
            song_track(2, MEADOW, txt=FOUNDRY),
            song_track(3, FOUNDRY, txt=FOUNDRY),
        )
        self.hear(paths[0], AIRPORT)
        self.hear(paths[1], LAUNDRY)
        before = raw(paths)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        why = "2 of 3 tracks not sung"
        for path in paths[:2]:
            row = cli_fakes.row_cells(result.stdout, path.name)[-1]
            self.assertEqual(row, f"{WITHHELD_ROW} ({why})")
            outcome = self.outcome(path.name)
            self.assertIs(outcome.status, lr.OutcomeStatus.KEPT_EXISTING)
            self.assertEqual(outcome.withheld, f"spoken word ({why})")
            self.assertIsNotNone(self.record_of(path))
        self.assertIn(f"{WITHHELD_LINE} 2", result.stdout)
        self.assertEqual(raw(paths), before)

    def test_the_count_is_the_whole_album_s_under_track(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY),
            song_track(2, MEADOW, txt=FOUNDRY),
            song_track(3, FOUNDRY, txt=FOUNDRY),
        )
        self.hear(paths[0], AIRPORT)
        self.hear(paths[1], LAUNDRY)
        result = self.fix("--track", str(paths[0]))
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        row = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertEqual(row, f"{WITHHELD_ROW} (2 of 3 tracks not sung)")

    def test_half_the_tracks_not_sung_is_not_most(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY),
            song_track(2, MEADOW, txt=FOUNDRY),
            song_track(3, FOUNDRY, txt=FOUNDRY),
            song_track(4, QUARRY, txt=QUARRY),
        )
        self.hear(paths[0], HARBOR)
        self.hear(paths[1], MEADOW)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        for path in paths[:2]:
            self.assertIs(self.outcome(path.name).status, lr.OutcomeStatus.REMOVED)
            self.assertFalse(path.with_suffix(".txt").exists())
        self.assertNotIn(WITHHELD_LINE, result.stdout)

    def test_a_music_album_with_one_track_not_sung_loses_the_wrong_text(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY),
            song_track(2, MEADOW, txt=MEADOW),
            song_track(3, FOUNDRY, txt=FOUNDRY),
        )
        self.hear(paths[0], HARBOR)
        result = self.fix()
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        self.assertEqual(
            cli_fakes.row_cells(result.stdout, paths[0].name)[-1], WRONG_TEXT
        )
        self.assertIs(self.outcome(paths[0].name).status, lr.OutcomeStatus.REMOVED)
        self.assertFalse(paths[0].with_suffix(".txt").exists())

    def test_a_dry_run_says_the_removal_is_withheld(self) -> None:
        paths = self.build(
            song_track(1, HARBOR, txt=QUARRY),
            song_track(2, MEADOW, txt=FOUNDRY),
        )
        self.hear(paths[0], AIRPORT)
        self.hear(paths[1], LAUNDRY)
        result = self.fix("--dry-run")
        self.assertEqual(result.code, album.EXIT_OK, result.stdout + result.stderr)
        plan = cli_fakes.row_cells(result.stdout, paths[0].name)[-1]
        self.assertEqual(
            plan,
            "ask the providers; no removal: spoken word (2 of 2 tracks not sung)",
        )
