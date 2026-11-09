"""Genius key resolution: --config, else the environment, else the config file.

Every config file here is written under the test's redirected HOME, with the obvious fake
value below, so no test can reach the real config directory or a real key.
"""

from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_config as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above

FAKE_TOKEN = "fake-token-for-tests"
OTHER_FAKE = "another-fake-token-for-tests"
PRIVATE, SHARED = 0o600, 0o644


def _write_config(path: Path, body: str, mode: int = PRIVATE) -> Path:
    """Write a config file at *path* with *mode*; return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="")
    path.chmod(mode)
    return path


def _token_body(token: str) -> str:
    """A config file body naming *token*."""
    return f'[genius]\naccess_token = "{token}"\n'


class DefaultPathTest(fixtures.IsolatedCase):
    """XDG_CONFIG_HOME when set and absolute, else ~/.config."""

    def test_xdg_config_home_wins(self) -> None:
        xdg = self.tmp / "xdg"
        self.assertEqual(
            lc.default_config_path({lc.XDG_CONFIG_ENV: str(xdg)}),
            xdg / "lyricsmith" / "config.toml",
        )

    def test_without_xdg_it_is_under_home(self) -> None:
        self.assertEqual(
            lc.default_config_path({}),
            self.home / ".config" / "lyricsmith" / "config.toml",
        )

    def test_home_is_read_from_the_given_environment(self) -> None:
        other_home = self.tmp / "other-home"
        self.assertEqual(
            lc.default_config_path({"HOME": str(other_home)}),
            other_home / ".config" / "lyricsmith" / "config.toml",
        )

    def test_a_relative_or_empty_xdg_is_ignored(self) -> None:
        for value in ("", "relative/dir"):
            with self.subTest(value=value):
                self.assertEqual(
                    lc.default_config_path({lc.XDG_CONFIG_ENV: value}),
                    self.home / ".config" / "lyricsmith" / "config.toml",
                )


class PrecedenceTest(fixtures.IsolatedCase):
    """First hit wins: --config, then the environment, then the default file."""

    def setUp(self) -> None:
        """A default config file under the redirected XDG directory, holding OTHER_FAKE."""
        super().setUp()
        self.environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        self.default = lc.default_config_path(self.environ)
        _write_config(self.default, _token_body(OTHER_FAKE))

    def test_an_explicit_config_beats_the_environment_and_the_file(self) -> None:
        given = _write_config(self.tmp / "given.toml", _token_body(FAKE_TOKEN))
        environ = {**self.environ, lc.GENIUS_TOKEN_ENV: OTHER_FAKE}
        got = lc.resolve_genius_key(given, environ)
        self.assertIs(got.origin, lr.KeyOrigin.ARGUMENT)
        self.assertEqual(got.secret.reveal() if got.secret else None, FAKE_TOKEN)
        self.assertEqual(got.searched, given)
        self.assertEqual(got.warnings, ())

    def test_the_environment_beats_the_file(self) -> None:
        environ = {**self.environ, lc.GENIUS_TOKEN_ENV: f"  {FAKE_TOKEN}\n"}
        got = lc.resolve_genius_key(None, environ)
        self.assertIs(got.origin, lr.KeyOrigin.ENVIRONMENT)
        self.assertEqual(got.secret.reveal() if got.secret else None, FAKE_TOKEN)
        self.assertIsNone(got.searched)

    def test_a_blank_environment_value_falls_through_to_the_file(self) -> None:
        environ = {**self.environ, lc.GENIUS_TOKEN_ENV: "  "}
        got = lc.resolve_genius_key(None, environ)
        self.assertIs(got.origin, lr.KeyOrigin.CONFIG_FILE)
        self.assertEqual(got.secret.reveal() if got.secret else None, OTHER_FAKE)
        self.assertEqual(got.searched, self.default)

    def test_nothing_anywhere_is_absent_naming_the_path_looked_for(self) -> None:
        self.default.unlink()
        got = lc.resolve_genius_key(None, self.environ)
        self.assertIs(got.origin, lr.KeyOrigin.ABSENT)
        self.assertIsNone(got.secret)
        self.assertEqual(got.searched, self.default)
        self.assertEqual(got.warnings, ())


class ExplicitConfigErrorsTest(fixtures.IsolatedCase):
    """A named config is the only source: anything wrong with it is a ConfigError."""

    def test_each_unusable_file_is_a_config_error_never_a_fallback(self) -> None:
        bodies = {
            "malformed": "[genius\n",
            "no table": f'access_token = "{FAKE_TOKEN}"\n',
            "no key": "[genius]\n",
            "blank key": '[genius]\naccess_token = "  "\n',
            "not a string": "[genius]\naccess_token = 7\n",
            "table not a table": 'genius = "x"\n',
        }
        environ = {lc.GENIUS_TOKEN_ENV: FAKE_TOKEN}
        for name, body in bodies.items():
            with self.subTest(case=name):
                path = _write_config(self.tmp / f"{name}.toml", body)
                with self.assertRaises(lm.ConfigError) as caught:
                    lc.resolve_genius_key(path, environ)
                self.assertIn(str(path), str(caught.exception))

    def test_a_missing_file_is_a_config_error(self) -> None:
        with self.assertRaises(lm.ConfigError):
            lc.resolve_genius_key(self.tmp / "absent.toml", {})

    def test_a_file_that_is_not_utf8_is_a_config_error(self) -> None:
        path = self.tmp / "latin.toml"
        path.write_bytes(b'[genius]\naccess_token = "caf\xe9"\n')
        with self.assertRaises(lm.ConfigError):
            lc.resolve_genius_key(path, {})

    def test_the_error_never_carries_the_value(self) -> None:
        path = _write_config(
            self.tmp / "bad.toml", f'[genius]\naccess_token = "{FAKE_TOKEN}\n'
        )
        with self.assertRaises(lm.ConfigError) as caught:
            lc.resolve_genius_key(path, {})
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))


class DefaultFileProblemsTest(fixtures.IsolatedCase):
    """An unusable default file is absent with a warning, not an exit."""

    def test_an_unusable_default_file_is_absent_with_a_warning(self) -> None:
        environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        path = _write_config(lc.default_config_path(environ), "[genius]\n")
        got = lc.resolve_genius_key(None, environ)
        self.assertIs(got.origin, lr.KeyOrigin.ABSENT)
        self.assertEqual(got.searched, path)
        self.assertEqual(len(got.warnings), 1)
        self.assertIn(str(path), got.warnings[0])


class PermissionWarningTest(fixtures.IsolatedCase):
    """A key file readable by group or others draws one warning, and still works."""

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_a_shared_file_warns_once_and_still_resolves(self) -> None:
        for mode in (0o640, 0o604, SHARED):
            with self.subTest(mode=oct(mode)):
                path = _write_config(
                    self.tmp / "shared.toml", _token_body(FAKE_TOKEN), mode
                )
                got = lc.resolve_genius_key(path, {})
                self.assertEqual(
                    got.secret.reveal() if got.secret else None, FAKE_TOKEN
                )
                self.assertEqual(len(got.warnings), 1)
                self.assertIn(str(path), got.warnings[0])
                self.assertIn(oct(mode)[2:], got.warnings[0])
                self.assertNotIn(FAKE_TOKEN, got.warnings[0])

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_the_default_file_warns_too(self) -> None:
        environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        _write_config(lc.default_config_path(environ), _token_body(FAKE_TOKEN), SHARED)
        got = lc.resolve_genius_key(None, environ)
        self.assertIs(got.origin, lr.KeyOrigin.CONFIG_FILE)
        self.assertEqual(len(got.warnings), 1)

    def test_off_posix_the_bits_are_not_read(self) -> None:
        path = _write_config(self.tmp / "shared.toml", _token_body(FAKE_TOKEN), SHARED)
        self.assertEqual(lc.permission_warnings(path, posix=False), ())

    @unittest.skipIf(os.name == "nt", fixtures.NO_POSIX_MODES)
    def test_a_private_file_draws_no_warning(self) -> None:
        path = _write_config(self.tmp / "private.toml", _token_body(FAKE_TOKEN))
        self.assertEqual(lc.permission_warnings(path, posix=True), ())

    def test_on_posix_only_the_shared_read_bits_warn(self) -> None:
        # The bits a POSIX stat would report, handed in, so both answers are reached on
        # every host: NTFS reports every file as 0o666 and would only ever warn.
        path = _write_config(self.tmp / "key.toml", _token_body(FAKE_TOKEN))
        for mode, warnings in ((PRIVATE, 0), (SHARED, 1)):
            with self.subTest(mode=oct(mode)):
                reported = os.stat_result((stat.S_IFREG | mode,) + (0,) * 9)
                with mock.patch.object(Path, "stat", return_value=reported):
                    got = lc.permission_warnings(path, posix=True)
                self.assertEqual([oct(mode)[2:] in w for w in got], [True] * warnings)


class SecretTest(fixtures.IsolatedCase):
    """The resolved key never shows its value in a repr, a str or a format."""

    def test_the_resolution_never_shows_the_value(self) -> None:
        got = lc.resolve_genius_key(None, {lc.GENIUS_TOKEN_ENV: FAKE_TOKEN})
        for shown in (repr(got), str(got), repr(got.secret), f"{got.secret}"):
            self.assertNotIn(FAKE_TOKEN, shown)
        self.assertIn("Secret(****)", repr(got))
