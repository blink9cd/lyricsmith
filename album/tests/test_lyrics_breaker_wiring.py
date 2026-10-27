"""Where a run's breakers come from: the table's four providers, and the run's choices.

The real table gives LRCLIB, NetEase, Genius and Bandcamp each one guard, shared by
their steps; YouTube Music and Tekstowo keep their own 24-hour stop. A run reads each
breaker file of a provider it asks as it starts, and no other: one turned off, or
Genius with no key, has its file left alone, as an offline run and a dry run leave
every file. Every file
is under the isolation's temporary state folder, and no test reaches the network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breaker as lbk  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_breakers as lbs  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_cooldown as lyc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_providers as lp  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_refusal as lrf  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above
from genius_fakes import FakeGeniusClient  # noqa: E402 - follows the sys.path bootstrap
from test_lyrics_breaker_fetch import (  # noqa: E402 - follows the sys.path bootstrap
    EveryKindFake,
)

S, U = lm.LyricsKind.SYNCED, lm.LyricsKind.UNSYNCED
LRCLIB = lm.Source.LRCLIB
#: Bytes no breaker file holds.
BROKEN = b"not a breaker"
#: An invented Genius key, so a run asks Genius; the providers are faked.
INVENTED_KEY = "fake-token-for-tests"


class TableTest(fixtures.IsolatedCase):
    """Each guarded provider's steps share one guard, built from its setup."""

    def setUp(self) -> None:
        """A setup for each guarded provider, every one closed."""
        super().setUp()
        environ = dict(os.environ)
        self.setups = {
            source: lbs.BreakerSetup(lbk.breaker_path(environ, source), lbk.CLOSED)
            for source in lbk.GUARDED_SOURCES
        }

    def test_the_four_providers_each_carry_one_guard(self) -> None:
        state_path = self.tmp / "cool.json"
        settings = ltable.ProviderSettings(
            bandcamp=True,
            breakers=self.setups,
            youtube_music=lyc.CoolingSetup(state_path),
            tekstowo=lyc.CoolingSetup(state_path),
        )
        table = ltable.build_provider_table(
            lr.Secret("not-a-real-key"),
            genius_factory=lambda _key: FakeGeniusClient([]),
            settings=settings,
        )
        guards = lbs.table_guards(table)
        self.assertEqual(set(guards), set(lbk.GUARDED_SOURCES))
        for key, provider in table.items():
            with self.subTest(key=key):
                expected = guards.get(key.source)
                self.assertIs(lbs.guard_of(provider), expected)
        self.assertEqual(guards[LRCLIB].setup, self.setups[LRCLIB])

    def test_no_setups_build_no_guards(self) -> None:
        self.assertEqual(lbs.table_guards(ltable.build_provider_table(None)), {})


class ChoiceTest(cli_fakes.CliCase):
    """A run and a compare read the files; an offline run and a dry run do not."""

    def setUp(self) -> None:
        """A one-track album, and a broken LRCLIB breaker file."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.path = lbk.breaker_path(self.wiring().environ, LRCLIB)
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(BROKEN)

    def expected_setups(
        self, *, genius: bool = False
    ) -> dict[lm.Source, lbs.BreakerSetup]:
        """Each asked guarded provider's setup, closed, on this test's clock.

        Genius's is there only when *genius*, a run holding a key.
        """
        environ, now = self.wiring().environ, self.wiring().now
        return {
            source: lbs.BreakerSetup(lbk.breaker_path(environ, source), lbk.CLOSED, now)
            for source in lbk.GUARDED_SOURCES
            if genius or source is not lm.Source.GENIUS
        }

    def test_a_run_reads_each_file_and_notices_a_broken_one(self) -> None:
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (settings,) = self.provider_settings
        self.assertEqual(dict(settings.breakers), self.expected_setups())
        self.assertIn("lrclib: ", result.stderr)
        self.assertIn("breaker file ignored", result.stderr)

    def test_with_a_genius_key_its_file_is_read_too(self) -> None:
        os.environ[fixtures.GENIUS_TOKEN_ENV] = INVENTED_KEY
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (settings,) = self.provider_settings
        self.assertEqual(dict(settings.breakers), self.expected_setups(genius=True))

    def test_compare_reads_them_too(self) -> None:
        result = self.main("--album", str(self.album), "--mode", "compare")
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (settings,) = self.provider_settings
        self.assertEqual(dict(settings.breakers), self.expected_setups())

    def test_an_offline_run_and_a_dry_run_leave_every_file_alone(self) -> None:
        for flag in ("--offline", "--dry-run"):
            with self.subTest(flag=flag):
                result = self.run_album(flag)
                self.assertEqual(result.code, album.EXIT_OK, result.stderr)
                self.assertNotIn("breaker", result.stderr)
                self.assertEqual(self.path.read_bytes(), BROKEN)
        self.assertEqual(self.provider_settings, [])


class UnaskedFileTest(cli_fakes.CliCase):
    """A provider the run does not ask has its breaker file neither read nor noticed."""

    def broken(self, source: lm.Source) -> Path:
        """*source*'s breaker file, written broken."""
        path = lbk.breaker_path(self.wiring().environ, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BROKEN)
        return path

    def assert_unread(self, source: lm.Source, *flags: str) -> None:
        """A run with *flags* builds no setup for *source* and leaves its file alone.

        Each on an album of its own, as two runs of one album at the fixed clock would
        take the same run id.
        """
        self.album = self.tmp / f"album-{source.name.lower()}"
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.provider_settings.clear()
        path = self.broken(source)
        result = self.run_album(*flags)
        # Read and removed before any check, so the next case starts with no file.
        left = path.read_bytes()
        path.unlink()
        self.assertEqual(result.code, album.EXIT_OK, result.stderr)
        (settings,) = self.provider_settings
        self.assertNotIn(source, settings.breakers)
        self.assertNotIn("breaker file ignored", result.stderr)
        self.assertEqual(left, BROKEN)

    def test_a_provider_turned_off_keeps_its_file_unread(self) -> None:
        # A key, so Genius would be asked but for its own option.
        os.environ[fixtures.GENIUS_TOKEN_ENV] = INVENTED_KEY
        cases = (
            ("--no-netease", lm.Source.NETEASE),
            ("--no-bandcamp", lm.Source.BANDCAMP),
            ("--no-genius", lm.Source.GENIUS),
        )
        for flag, source in cases:
            with self.subTest(flag=flag):
                self.assert_unread(source, flag)

    def test_genius_with_no_key_keeps_its_file_unread(self) -> None:
        self.assert_unread(lm.Source.GENIUS)


class GuardedRunCase(cli_fakes.CliCase):
    """A run whose LRCLIB steps carry the guard built from the run's own setups."""

    def setUp(self) -> None:
        """A one-track album, and a table built from the run's own breaker setups."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        self.fakes: list[EveryKindFake] = []

    def build_providers(
        self,
        key: lr.Secret | None,
        deadline_s: float,
        settings: ltable.ProviderSettings = ltable.ProviderSettings(),
    ) -> lm.ProviderTable:
        """LRCLIB's two steps, one fake refusing with a 403 under the run's guard."""
        super().build_providers(key, deadline_s, settings)
        guard = lbs.BreakerGuard(settings.breakers[LRCLIB])
        fake = EveryKindFake(guard, [lp.HttpStatusError(LRCLIB, 403)])
        self.fakes.append(fake)
        return {lm.ProviderKey(LRCLIB, S): fake, lm.ProviderKey(LRCLIB, U): fake}

    def path(self) -> Path:
        """LRCLIB's breaker file under this test's state folder."""
        return lbk.breaker_path(self.wiring().environ, LRCLIB)


class RunWritesTest(GuardedRunCase):
    """A run's refusals reach the file, which the next run starts from."""

    def test_a_block_is_written_and_the_next_run_skips_the_provider(self) -> None:
        first = self.run_album()
        self.assertIn(first.code, (album.EXIT_OK, album.EXIT_TRACK_ERRORS))
        read = lbk.read_breaker(self.path(), self.fixed_now())
        self.assertEqual(read.state.reason, lrf.Refusal.BLOCKED)
        self.assertEqual(read.state.open_until, self.fixed_now() + lbk.MAX_WINDOW)
        self.folder.run_id = "20261014T153013Z"  # a second run, so a second folder
        self.run_album()
        self.assertEqual([fake.calls for fake in self.fakes], [1, 0])


class WriteFailsTest(GuardedRunCase):
    """A breaker file that cannot be written is a notice, and the run goes on."""

    def test_the_failed_write_is_noticed(self) -> None:
        self.path().mkdir(parents=True)
        result = self.run_album()
        self.assertIn(result.code, (album.EXIT_OK, album.EXIT_TRACK_ERRORS))
        self.assertIn("lrclib: could not write the breaker file: ", result.stderr)
