"""The ``[musicbrainz] contact``: read from the config file, used in MusicBrainz's User-Agent only.

Every config file here is written under the test's own directory or its redirected HOME,
so no test reaches the real config directory. The contact is an invented URL, never an
email address.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent), str(_HERE.parent / "src")]

import album  # noqa: E402 - import follows the sys.path bootstrap above
import cli_fakes  # noqa: E402 - import follows the sys.path bootstrap above
import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_config as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_table as ltable  # noqa: E402 - import follows the sys.path bootstrap above
from fixtures import TrackSpec  # noqa: E402 - import follows the sys.path bootstrap above

CONTACT = "https://example.com/lyricsmith-contact"
#: The refusal of an unusable contact, which never quotes the value.
_REFUSAL = "[musicbrainz] contact is not 1-200 printable characters without parentheses"
#: Unusable contacts, as TOML values.
_UNUSABLE = {
    "a number": "5",
    "empty": '""',
    "201 characters": '"' + "x" * 201 + '"',
    "a parenthesis": '"https://example.com/(contact"',
    "a tab": '"https://example.com/\\tcontact"',
}


def _write(path: Path, body: str) -> Path:
    """Write a config file holding *body* at *path*; return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="")
    path.chmod(0o600)
    return path


def _contact_body(value: str = f'"{CONTACT}"') -> str:
    """A config file body holding only ``[musicbrainz] contact = <value>``."""
    return f"[musicbrainz]\ncontact = {value}\n"


class NamedFileTest(fixtures.IsolatedCase):
    """A ``--config`` file: its contact, or a refusal that stops the run."""

    def _named(self, body: str) -> Path:
        """A ``--config`` file holding *body*."""
        return _write(self.tmp / "named.toml", body)

    def test_its_contact_is_given(self) -> None:
        listing = lc.resolve_musicbrainz_contact(self._named(_contact_body()), {})
        self.assertEqual(listing, lc.ContactListing(CONTACT))

    def test_a_contact_is_trimmed(self) -> None:
        named = self._named(_contact_body(f'"  {CONTACT} "'))
        self.assertEqual(lc.resolve_musicbrainz_contact(named, {}).contact, CONTACT)

    def test_an_unusable_contact_is_refused_without_its_value(self) -> None:
        for name, value in _UNUSABLE.items():
            with self.subTest(name):
                named = self._named(_contact_body(value))
                with self.assertRaises(lm.ConfigError) as caught:
                    lc.resolve_musicbrainz_contact(named, {})
                self.assertEqual(str(caught.exception), f"{named}: {_REFUSAL}")

    def test_a_musicbrainz_entry_that_is_not_a_table_is_refused(self) -> None:
        named = self._named('musicbrainz = "a string"\n')
        with self.assertRaises(lm.ConfigError) as caught:
            lc.resolve_musicbrainz_contact(named, {})
        self.assertEqual(
            str(caught.exception), f"{named}: [musicbrainz] is not a table"
        )

    def test_a_table_without_a_contact_is_no_contact(self) -> None:
        named = self._named("[musicbrainz]\n")
        self.assertEqual(
            lc.resolve_musicbrainz_contact(named, {}), lc.ContactListing(None)
        )

    def test_a_file_without_the_table_is_no_contact(self) -> None:
        named = self._named('[no_lyrics]\ngenres = ["Spoken Word"]\n')
        self.assertIsNone(lc.resolve_musicbrainz_contact(named, {}).contact)

    def test_a_file_holding_only_musicbrainz_is_accepted(self) -> None:
        named = self._named(_contact_body())
        resolution = lc.resolve_genius_key(named, {})
        self.assertIsNone(resolution.secret)
        self.assertIsNone(lc.resolve_no_lyrics(named, {}).genres)

    def test_a_file_holding_none_of_the_three_names_them_all(self) -> None:
        named = self._named('[other]\nvalue = "x"\n')
        for resolve in (
            lc.resolve_genius_key,
            lc.resolve_no_lyrics,
            lc.resolve_musicbrainz_contact,
        ):
            with self.subTest(resolve.__name__):
                with self.assertRaises(lm.ConfigError) as caught:
                    resolve(named, {})
                message = str(caught.exception)
                for table in ("[genius] access_token", "[no_lyrics]", "[musicbrainz]"):
                    self.assertIn(table, message)


class DefaultFileTest(fixtures.IsolatedCase):
    """The default file: its contact, or a warning and no contact."""

    def setUp(self) -> None:
        """The environment naming the redirected XDG config directory."""
        super().setUp()
        self.environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        self.default = lc.default_config_path(self.environ)

    def test_no_default_file_is_no_contact(self) -> None:
        listing = lc.resolve_musicbrainz_contact(None, self.environ)
        self.assertEqual(listing, lc.ContactListing(None))

    def test_its_contact_is_given(self) -> None:
        _write(self.default, _contact_body())
        listing = lc.resolve_musicbrainz_contact(None, self.environ)
        self.assertEqual(listing, lc.ContactListing(CONTACT))

    def test_an_unusable_contact_is_a_warning_and_no_contact(self) -> None:
        for name, value in _UNUSABLE.items():
            with self.subTest(name):
                _write(self.default, _contact_body(value))
                listing = lc.resolve_musicbrainz_contact(None, self.environ)
                warning = f"{self.default}: {_REFUSAL}"
                self.assertEqual(listing, lc.ContactListing(None, (warning,)))


class ContactRunTest(cli_fakes.CliCase):
    """The entry point builds the table with the contact, and writes it nowhere else."""

    def setUp(self) -> None:
        """A one-track album."""
        super().setUp()
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))

    def _config(self, body: str = _contact_body()) -> str:
        """A ``--config`` file holding *body*, as the option's value."""
        return str(_write(self.tmp / "named.toml", body))

    def test_the_run_turns_bandcamp_on_with_the_contact(self) -> None:
        result = self.run_album("--config", self._config())
        self.assertEqual(result.code, album.EXIT_OK)
        expected = ltable.ProviderSettings(
            bandcamp=True,
            musicbrainz_contact=CONTACT,
            youtube_music=self.youtube_setup(),
            tekstowo=self.tekstowo_setup(),
        )
        self.assertEqual(self.provider_settings, [expected])

    def test_without_a_contact_bandcamp_is_on_with_none(self) -> None:
        self.run_album()
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    bandcamp=True,
                    youtube_music=self.youtube_setup(),
                    tekstowo=self.tekstowo_setup(),
                )
            ],
        )

    def test_an_unusable_default_contact_is_a_notice_and_the_run_goes_on(self) -> None:
        default = lc.default_config_path(
            {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        )
        _write(default, _contact_body(_UNUSABLE["a tab"]))
        result = self.run_album()
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertIn(f"musicbrainz: {default}: {_REFUSAL}", result.stderr)
        self.assertEqual(
            self.provider_settings,
            [
                ltable.ProviderSettings(
                    bandcamp=True,
                    youtube_music=self.youtube_setup(),
                    tekstowo=self.tekstowo_setup(),
                )
            ],
        )

    def test_compare_passes_the_settings_too(self) -> None:
        self.main(
            "--mode", "compare", "--album", str(self.album), "--config", self._config()
        )
        expected = ltable.ProviderSettings(
            bandcamp=True,
            musicbrainz_contact=CONTACT,
            youtube_music=self.youtube_setup(),
            tekstowo=self.tekstowo_setup(),
        )
        self.assertEqual(self.provider_settings, [expected])

    def test_an_unusable_named_contact_cannot_run_and_writes_nothing(self) -> None:
        named = self._config(_contact_body(_UNUSABLE["a parenthesis"]))
        for extra in ((), ("--dry-run",), ("--offline",), ("--mode", "compare")):
            with self.subTest(extra=extra):
                if extra[:1] == ("--mode",):
                    result = self.main(
                        *extra, "--album", str(self.album), "--config", named
                    )
                else:
                    result = self.run_album("--config", named, *extra)
                self.assertEqual(result.code, album.EXIT_CANNOT_RUN)
                self.assertIn(_REFUSAL, result.stderr)
                self.assertNotIn("(contact", result.stderr + result.stdout)
        self.assertEqual((self.folder.manifests, self.provider_calls), ([], []))


class ContactNowhereTest(cli_fakes.RealWiringCliCase):
    """The contact is in no output and no file the run writes."""

    def test_the_contact_is_in_no_stream_and_no_file(self) -> None:
        fixtures.build_album(self.album, (TrackSpec("01 Song.mp3"),))
        named = _write(self.tmp / "named.toml", _contact_body())
        result = self.run_album("--config", str(named))
        self.assertEqual(result.code, album.EXIT_OK)
        self.assertNotIn(CONTACT, result.stdout + result.stderr)
        written = [p for p in self.album.rglob("*") if p.is_file()]
        self.assertTrue(any(p.suffix == ".json" for p in written))
        for path in written:
            self.assertNotIn(CONTACT.encode(), path.read_bytes(), path.name)
