"""The [no_lyrics] table: where the list is read from, and the neither-table exit rule."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "src")]

import fixtures  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_config as lc  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_model as lm  # noqa: E402 - import follows the sys.path bootstrap above
import lyrics_records as lr  # noqa: E402 - import follows the sys.path bootstrap above

FAKE_TOKEN = "fake-token-for-tests"
GENRES_BODY = '[no_lyrics]\ngenres = ["Audiobook", "Stand-Up"]\n'


def _write(path: Path, body: str) -> Path:
    """Write a private config file at *path*; return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="")
    path.chmod(0o600)
    return path


class ExplicitListTest(fixtures.IsolatedCase):
    """--config names the list, and is the only place it is read from."""

    def test_the_table_replaces_the_list(self) -> None:
        path = _write(self.tmp / "c.toml", GENRES_BODY)
        got = lc.resolve_no_lyrics(path, {})
        self.assertEqual(got, lc.GenreListing(("Audiobook", "Stand-Up")))

    def test_an_empty_array_turns_marking_off(self) -> None:
        path = _write(self.tmp / "c.toml", "[no_lyrics]\ngenres = []\n")
        self.assertEqual(lc.resolve_no_lyrics(path, {}).genres, ())

    def test_a_key_only_file_names_no_list(self) -> None:
        path = _write(self.tmp / "c.toml", f'[genius]\naccess_token = "{FAKE_TOKEN}"\n')
        self.assertEqual(lc.resolve_no_lyrics(path, {}), lc.GenreListing(None))

    def test_the_default_file_is_not_read_when_config_is_named(self) -> None:
        environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        _write(lc.default_config_path(environ), GENRES_BODY)
        path = _write(self.tmp / "c.toml", f'[genius]\naccess_token = "{FAKE_TOKEN}"\n')
        self.assertIsNone(lc.resolve_no_lyrics(path, environ).genres)

    def test_each_unusable_explicit_file_is_a_config_error(self) -> None:
        bodies = {
            "missing": None,
            "malformed": "[no_lyrics\n",
            "neither table": "title = 'x'\n",
            "blank key and no list": "[genius]\naccess_token = ' '\n",
            "no genres": "[no_lyrics]\n",
            "genres not an array": '[no_lyrics]\ngenres = "Stand-Up"\n',
            "an entry not a string": "[no_lyrics]\ngenres = [7]\n",
            "table not a table": 'no_lyrics = "x"\n',
        }
        for name, body in bodies.items():
            with self.subTest(case=name):
                path = self.tmp / f"{name}.toml"
                if body is not None:
                    _write(path, body)
                with self.assertRaises(lm.ConfigError) as caught:
                    lc.resolve_no_lyrics(path, {})
                self.assertIn(str(path), str(caught.exception))


class DefaultListTest(fixtures.IsolatedCase):
    """With no --config the default file names the list; the environment never does."""

    def setUp(self) -> None:
        super().setUp()
        self.environ = {
            lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"]),
            lc.GENIUS_TOKEN_ENV: FAKE_TOKEN,
        }
        self.path = lc.default_config_path(self.environ)

    def test_no_default_file_names_no_list(self) -> None:
        self.assertEqual(
            lc.resolve_no_lyrics(None, self.environ), lc.GenreListing(None)
        )

    def test_the_default_file_names_the_list_beside_an_environment_key(self) -> None:
        _write(self.path, GENRES_BODY)
        got = lc.resolve_no_lyrics(None, self.environ)
        self.assertEqual(got.genres, ("Audiobook", "Stand-Up"))

    def test_a_malformed_default_table_is_a_warning_and_no_list(self) -> None:
        _write(self.path, '[no_lyrics]\ngenres = "Stand-Up"\n')
        got = lc.resolve_no_lyrics(None, self.environ)
        self.assertIsNone(got.genres)
        self.assertEqual(len(got.warnings), 1)
        self.assertIn(str(self.path), got.warnings[0])


class NeitherTableRuleTest(fixtures.IsolatedCase):
    """A file is unusable for its key only when it holds neither table, the default included."""

    def test_an_explicit_list_only_file_is_an_absent_key_never_the_environment(
        self,
    ) -> None:
        path = _write(self.tmp / "c.toml", GENRES_BODY)
        got = lc.resolve_genius_key(path, {lc.GENIUS_TOKEN_ENV: FAKE_TOKEN})
        self.assertEqual(got, lr.KeyResolution(None, lr.KeyOrigin.ABSENT, path))

    def test_an_explicit_file_with_neither_table_is_still_a_config_error(self) -> None:
        path = _write(self.tmp / "c.toml", "[genius]\n")
        with self.assertRaises(lm.ConfigError) as caught:
            lc.resolve_genius_key(path, {})
        self.assertIn("none of", str(caught.exception))

    def test_a_list_only_default_file_is_no_key_without_a_warning(self) -> None:
        environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        path = _write(lc.default_config_path(environ), GENRES_BODY)
        got = lc.resolve_genius_key(None, environ)
        self.assertEqual(got, lr.KeyResolution(None, lr.KeyOrigin.ABSENT, path))

    def test_a_default_file_with_neither_table_still_warns(self) -> None:
        environ = {lc.XDG_CONFIG_ENV: str(self.xdg["XDG_CONFIG_HOME"])}
        _write(lc.default_config_path(environ), "[genius]\n")
        self.assertEqual(len(lc.resolve_genius_key(None, environ).warnings), 1)
