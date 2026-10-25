"""Marked tracks: no lyrics expected on a miss, and flagged whenever they hold lyrics.

A track whose genre tag names a no-lyrics genre (`lyrics_genre`) is asked for as any
other; only its miss reads ``no lyrics expected``, and any end at which it holds lyrics
carries the genre FLAG. Each test drives the entry point through `cli_fakes.CliCase`,
so the path from the config file to the summary is the one an operator gets. Every
title, genre list and lyric here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import run_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import tag_builders  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

STANDUP = ("Stand-up Comedy",)
FLAG = lr.CheckReason(lr.CheckRule.NO_LYRICS_GENRE, "Stand-up Comedy")
#: The run id a second run in one test takes, as the fake run folder keeps one per id.
_SECOND_RUN_ID = "20261014T153013Z"
#: How a marked track's kept row closes.
MARKED = " (FLAG: lyrics on a no-lyrics genre (Stand-up Comedy))"
#: Old untimed frames holding a different song from every answer.
_ORCHARD = (tag_builders.uslt(fixtures.ORCHARD_PLAIN),)
#: Old timed and untimed frames holding the lighthouse song.
_LIGHTHOUSE = (
    tag_builders.uslt(fixtures.LIGHTHOUSE_PLAIN),
    tag_builders.sylt(
        *(
            (1000 + 4000 * index, line)
            for index, line in enumerate(fixtures.LIGHTHOUSE_PLAIN.splitlines())
            if line
        )
    ),
)


def _rules(after: lr.TrackOutcome) -> list[lr.CheckRule]:
    """The rule of each reason *after*'s check flagged, or none without a check."""
    return [] if after.check is None else [flag.rule for flag in after.check.flags]


class MarkedCase(cli_fakes.CliCase):
    """Reads a track's outcome from the newest manifest; reruns under a second id."""

    def _after(self, rel: str) -> lr.TrackOutcome:
        """*rel*'s outcome in the newest manifest."""
        tracks = {t.path: t.after for t in self.folder.manifests[-1].tracks}
        after = tracks[rel]
        assert after is not None
        return after

    def _rerun(self, *extra: str) -> cli_fakes.Result:
        """Run again, under a second run id: the fake folder creates one per run."""
        self.folder.run_id = _SECOND_RUN_ID
        return self.run_album(*extra)


class MarkedRunTest(MarkedCase):
    """Each end of a marked track, against the same end of an unmarked one."""

    def test_a_marked_miss_is_no_lyrics_expected_an_unmarked_one_not_found(
        self,
    ) -> None:
        fixtures.build_album(
            self.album,
            (
                TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP),
                TrackSpec("02 Song.mp3", title="The Song"),
            ),
        )
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIs(
            self._after("01 Bit.mp3").status, lr.OutcomeStatus.NO_LYRICS_EXPECTED
        )
        self.assertIsNone(self._after("01 Bit.mp3").check)
        self.assertIs(self._after("02 Song.mp3").status, lr.OutcomeStatus.NOT_FOUND)
        self.assertIn("not found 1 · no lyrics expected 1", result.stdout)

    def test_a_marked_miss_offline_is_no_lyrics_expected(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Bit.mp3", genres=STANDUP),))
        self.run_album("--offline")
        self.assertIs(
            self._after("01 Bit.mp3").status, lr.OutcomeStatus.NO_LYRICS_EXPECTED
        )

    def test_a_marked_answer_is_written_and_flagged(self) -> None:
        self.providers = run_fakes.table({"The Bit": fixtures.LIGHTHOUSE_LRC})
        fixtures.build_album(
            self.album, (TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP),)
        )
        result = self.run_album()
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.WRITTEN)
        assert after.check is not None
        self.assertIn(FLAG, after.check.flags)
        self.assertIn("flagged 1", result.stdout)

    def test_a_marked_track_served_from_its_sidecar_is_unchanged_and_flagged(
        self,
    ) -> None:
        fixtures.build_album(
            self.album,
            (TrackSpec("01 Bit.mp3", genres=STANDUP, lrc=fixtures.LIGHTHOUSE_LRC),),
        )
        self.run_album("--offline")
        self._rerun("--offline")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.UNCHANGED)
        assert after.check is not None
        self.assertIn(FLAG, after.check.flags)

    def test_a_marked_track_keeping_its_lyrics_is_kept_and_flagged(self) -> None:
        # The first run embeds the sidecar; with it removed, a miss keeps the frames.
        mp3 = fixtures.build_album(
            self.album,
            (TrackSpec("01 Bit.mp3", genres=STANDUP, lrc=fixtures.LIGHTHOUSE_LRC),),
        )[0]
        self.run_album("--offline")
        mp3.with_suffix(".lrc").unlink()
        result = self._rerun("--fresh")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertEqual(after.check, lr.CheckResult((FLAG,), ()))
        self.assertIn(
            "kept existing (FLAG: lyrics on a no-lyrics genre (Stand-up Comedy))",
            result.stdout,
        )
        self.assertIn("flagged 1", result.stdout)

    def test_a_marked_track_every_provider_failed_is_still_the_fetch_error(
        self,
    ) -> None:
        self.providers = run_fakes.failing_table()
        fixtures.build_album(self.album, (TrackSpec("01 Bit.mp3", genres=STANDUP),))
        self.assertEqual(self.run_album().code, album.EXIT_TRACK_ERRORS)
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.ERROR)
        self.assertNotIn(lr.CheckRule.NO_LYRICS_GENRE, _rules(after))

    def test_a_config_list_replaces_the_built_in_one_under_offline(self) -> None:
        config = self.tmp / "c.toml"
        config.write_text(
            '[no_lyrics]\ngenres = ["Audiobook"]\n', encoding="utf-8", newline=""
        )
        fixtures.build_album(
            self.album,
            (
                TrackSpec("01 Book.mp3", genres=("Audiobook",)),
                TrackSpec("02 Bit.mp3", genres=STANDUP),
            ),
        )
        self.run_album("--offline", "--config", str(config))
        self.assertIs(
            self._after("01 Book.mp3").status, lr.OutcomeStatus.NO_LYRICS_EXPECTED
        )
        self.assertIs(self._after("02 Bit.mp3").status, lr.OutcomeStatus.NOT_FOUND)

    def test_an_unusable_config_is_exit_2_under_no_genius_too(self) -> None:
        # The list is read under --offline and --no-genius, so the file is too.
        fixtures.build_album(self.album, (TrackSpec("01 Bit.mp3"),))
        for flag in ("--offline", "--no-genius"):
            with self.subTest(flag=flag):
                result = self.run_album(
                    flag, "--config", str(self.tmp / "missing.toml")
                )
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)

    def test_a_malformed_default_table_warns_and_uses_the_built_in_list(self) -> None:
        default = self.xdg["XDG_CONFIG_HOME"] / "lyricsmith" / "config.toml"
        default.parent.mkdir(parents=True)
        default.write_text('[no_lyrics]\ngenres = "x"\n', encoding="utf-8", newline="")
        fixtures.build_album(self.album, (TrackSpec("01 Bit.mp3", genres=STANDUP),))
        result = self.run_album("--offline")
        self.assertIn("no_lyrics: ", result.stderr)
        self.assertIs(
            self._after("01 Bit.mp3").status, lr.OutcomeStatus.NO_LYRICS_EXPECTED
        )

    def test_a_dry_run_plans_the_marked_fetch(self) -> None:
        fixtures.build_album(
            self.album,
            (
                TrackSpec("01 Bit.mp3", genres=STANDUP),
                TrackSpec("02 Song.mp3"),
            ),
        )
        out = self.run_album("--dry-run").stdout
        self.assertEqual(out.count("fetch (no lyrics expected if none)"), 1)

    def _fresh_plans(self, *tracks: TrackSpec) -> dict[str, str]:
        """Each track's plan in a ``--fresh --dry-run``, by file name; frames on 02."""
        paths = fixtures.build_album(self.album, tracks)
        tag_builders.plant_frames(paths[1], _LIGHTHOUSE)
        out = self.run_album("--fresh", "--dry-run").stdout
        rows = [line.split(maxsplit=3) for line in out.splitlines() if ".mp3" in line]
        return {row[2]: row[3] for row in rows}

    def test_a_fresh_dry_run_plans_the_marked_fetch_for_a_track_holding_none(
        self,
    ) -> None:
        plans = self._fresh_plans(
            TrackSpec("01 Bare.mp3", genres=STANDUP), TrackSpec("02 Framed.mp3")
        )
        self.assertEqual(plans["Bare.mp3"], "fetch (no lyrics expected if none)")

    def test_a_fresh_dry_run_plans_a_plain_fetch_for_a_marked_track_holding_lyrics(
        self,
    ) -> None:
        # A fresh miss keeps whatever a marked track holds, frames or a sidecar, so
        # its plan is the plain fetch an unmarked track's is.
        plans = self._fresh_plans(
            TrackSpec("01 Lrc.mp3", genres=STANDUP, lrc=fixtures.LIGHTHOUSE_LRC),
            TrackSpec("02 Framed.mp3", genres=STANDUP),
            TrackSpec("03 Txt.mp3", genres=STANDUP, txt=fixtures.LIGHTHOUSE_PLAIN),
        )
        for name in ("Lrc.mp3", "Framed.mp3", "Txt.mp3"):
            with self.subTest(name=name):
                self.assertEqual(plans[name], "fetch")


class MarkedEndsTest(MarkedCase):
    """The genre FLAG at the ends a fresh run and a refetch reach, and on its row."""

    def _planted(self, *tracks: TrackSpec, frames: tuple[lm.FrameRecord, ...]) -> None:
        """Build *tracks*, each with *frames* planted."""
        for path in fixtures.build_album(self.album, tracks):
            tag_builders.plant_frames(path, frames)

    @staticmethod
    def _row(stdout: str, name: str) -> str:
        """The summary row naming *name*."""
        (row,) = [line for line in stdout.splitlines() if name in line]
        return row

    def test_a_refetch_kept_marked_row_shows_its_flag(self) -> None:
        self._planted(
            TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP), frames=_ORCHARD
        )
        result = self.run_album("--refetch-untimed")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.KEPT_EXISTING)
        self.assertTrue(after.refetch_untimed)
        assert after.check is not None
        self.assertIn(FLAG, after.check.flags)
        self.assertTrue(
            self._row(result.stdout, "01 Bit").endswith(
                "kept existing: no answer to the refetch" + MARKED
            ),
            result.stdout,
        )
        self.assertIn("flagged 1", result.stdout)

    def test_a_declined_marked_answer_shows_the_reason_then_the_flag(self) -> None:
        self.providers = run_fakes.table({"The Bit": fixtures.LIGHTHOUSE_LRC})
        self._planted(
            TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP), frames=_ORCHARD
        )
        result = self.run_album("--fresh")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.KEPT_EXISTING)
        assert after.check is not None
        self.assertEqual(
            [flag.rule for flag in after.check.flags],
            [lr.CheckRule.UNLIKE_BACKUP, lr.CheckRule.NO_LYRICS_GENRE],
        )
        reason = after.check.flags[0].describe()
        self.assertTrue(
            self._row(result.stdout, "01 Bit").endswith(
                f"kept existing: new answer {reason}{MARKED}"
            ),
            result.stdout,
        )

    def test_only_the_marked_decline_counts_as_flagged(self) -> None:
        self.providers = run_fakes.table(
            {"The Bit": fixtures.LIGHTHOUSE_LRC, "The Song": fixtures.LIGHTHOUSE_LRC}
        )
        self._planted(
            TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP),
            TrackSpec("02 Song.mp3", title="The Song"),
            frames=_ORCHARD,
        )
        result = self.run_album("--fresh")
        song = self._after("02 Song.mp3")
        self.assertIs(song.status, lr.OutcomeStatus.KEPT_EXISTING)
        assert song.check is not None
        self.assertEqual(song.check.verdict, lr.CheckVerdict.FLAG)
        self.assertIn("kept existing 2", result.stdout)
        self.assertIn("flagged 1", result.stdout)

    def test_a_refetch_removed_marked_track_is_not_flagged(self) -> None:
        # Its implausible lyrics are gone, so it ends holding none: no flag, no suffix.
        dense = "la " * 2000
        fixtures.build_album(
            self.album,
            (TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP, txt=dense),),
        )
        result = self.run_album("--refetch-untimed")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.REMOVED)
        self.assertNotIn(lr.CheckRule.NO_LYRICS_GENRE, _rules(after))
        row = self._row(result.stdout, "01 Bit")
        self.assertIn("removed: implausible old lyrics", row)
        self.assertNotIn("FLAG", row)
        self.assertNotIn("flagged 1", result.stdout)

    def test_a_refetch_written_marked_track_carries_the_flag(self) -> None:
        self.providers = run_fakes.table(plain={"The Bit": fixtures.LIGHTHOUSE_PLAIN})
        self._planted(
            TrackSpec("01 Bit.mp3", title="The Bit", genres=STANDUP), frames=_ORCHARD
        )
        self.run_album("--refetch-untimed")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.WRITTEN)
        self.assertTrue(after.refetch_untimed)
        assert after.check is not None
        self.assertIn(FLAG, after.check.flags)

    def test_a_marked_track_its_frames_serve_is_unchanged_and_flagged(self) -> None:
        self._planted(TrackSpec("01 Bit.mp3", genres=STANDUP), frames=_LIGHTHOUSE)
        self.run_album("--offline")
        self._rerun("--offline")
        after = self._after("01 Bit.mp3")
        self.assertIs(after.status, lr.OutcomeStatus.UNCHANGED)
        self.assertIs(after.source, lm.Source.FRAMES)
        assert after.check is not None
        self.assertIn(FLAG, after.check.flags)
