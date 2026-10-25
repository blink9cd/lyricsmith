"""Resolve the Genius key, the ``[no_lyrics]`` genres and the MusicBrainz contact from config.

The key's sources, where the first hit wins:

1. ``--config <file>``, when given, and then it is the ONLY source: a file that is
   missing, unreadable, malformed or holds none of a key, a ``[no_lyrics]`` table and a
   ``[musicbrainz]`` table raises `ConfigError` rather than falling back to another key
   the operator did not name.
2. The ``GENIUS_ACCESS_TOKEN`` environment variable, the Genius library's own convention.
3. ``$XDG_CONFIG_HOME/lyricsmith/config.toml``, else ``~/.config/lyricsmith/config.toml``
   (`default_config_path`). A default file that cannot be used resolves as absent with
   a warning: nobody named it, so it does not stop the run. One holding no ``[genius]``
   table names no key, and resolves as absent with no warning.

The file is TOML holding any of ``[genius] access_token``, ``[no_lyrics] genres`` (an array
of strings) and ``[musicbrainz] contact``; a named file holding none is a `ConfigError`, and
one without a key leaves the key absent. The genres and the contact are read from
``--config`` when given, else from the default file, never from the environment. The
contact goes inside the parentheses of MusicBrainz's User-Agent and nowhere else, so it
must be 1-`MAX_CONTACT_CHARS` printable ASCII characters without parentheses. The key comes
back inside a `Secret`, whose repr never shows it, and no message here ever quotes it. Warnings
(a key file readable by group or others, an unusable default file) are returned for the
entry point to print; this module never prints, and never writes or creates the file.
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lyrics_model import ConfigError
from lyrics_records import KeyOrigin, KeyResolution, Secret

#: The Genius library's own variable for its client access token.
GENIUS_TOKEN_ENV = "GENIUS_ACCESS_TOKEN"
#: The XDG base directory for configuration; relative or empty values are ignored.
XDG_CONFIG_ENV = "XDG_CONFIG_HOME"
#: The default file's place under the configuration base directory.
CONFIG_RELPATH = Path("lyricsmith") / "config.toml"
#: The user's home directory, read from the given environment like the XDG base.
HOME_ENV = "HOME"
#: The configuration base directory under HOME when XDG_CONFIG_HOME does not name one.
_HOME_CONFIG_DIR = ".config"
#: The permission bits that let someone other than the file's user read it.
_SHARED_READ_BITS = stat.S_IRGRP | stat.S_IROTH
#: Whether this platform's permission bits mean what POSIX says they mean.
_IS_POSIX = os.name == "posix"
#: The config table naming the genres whose tracks expect no lyrics.
NO_LYRICS_TABLE = "no_lyrics"
#: The config table naming the contact MusicBrainz asks a client to give.
MUSICBRAINZ_TABLE = "musicbrainz"
#: The longest contact accepted, after trimming.
MAX_CONTACT_CHARS = 200
#: The characters a contact may hold: printable ASCII, but for the User-Agent's parentheses.
_CONTACT_CHARS = frozenset(map(chr, range(ord(" "), ord("~") + 1))) - frozenset("()")


@dataclass(frozen=True)
class GenreListing:
    """The ``[no_lyrics] genres`` a config file names, and why a default one was unusable.

    *genres* is None when no file names a list, so the built-in list applies.
    """

    genres: tuple[str, ...] | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContactListing:
    """The ``[musicbrainz] contact`` a config file names, if any, and why a default was unusable."""

    contact: str | None
    warnings: tuple[str, ...] = ()


def _config_base(environ: Mapping[str, str]) -> Path:
    """The configuration base directory: XDG_CONFIG_HOME when absolute, else ~/.config.

    The XDG convention ignores a relative value, so a stray relative setting cannot
    make the lookup depend on the working directory. HOME is read from *environ* too,
    so the whole answer follows the environment given; the process's home directory
    stands in only when *environ* has no HOME.
    """
    xdg = environ.get(XDG_CONFIG_ENV, "")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg)
    home = environ.get(HOME_ENV)
    base = Path(home) if home else Path.home()
    return base / _HOME_CONFIG_DIR


def default_config_path(environ: Mapping[str, str]) -> Path:
    """The default config file, ``lyricsmith/config.toml`` under the base directory."""
    return _config_base(environ) / CONFIG_RELPATH


def _token_in(document: Mapping[str, object]) -> str | None:
    """The non-blank ``[genius] access_token`` string in a parsed file, else None."""
    table = document.get("genius")
    if not isinstance(table, Mapping):
        return None
    token = table.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return None
    return token.strip()


def _read_document(path: Path) -> Mapping[str, object]:
    """The parsed file at *path*; `ConfigError` naming the path and the error's type alone."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: cannot be read ({type(error).__name__})") from None


def _require_a_table(document: Mapping[str, object], path: Path) -> None:
    """Refuse a file holding none of a Genius key, ``[no_lyrics]`` and ``[musicbrainz]``."""
    tables = (NO_LYRICS_TABLE, MUSICBRAINZ_TABLE)
    if _token_in(document) is None and not any(t in document for t in tables):
        raise ConfigError(
            f"{path}: holds none of a [genius] access_token string, "
            f"a [{NO_LYRICS_TABLE}] table and a [{MUSICBRAINZ_TABLE}] table"
        )


def _contact_in(document: Mapping[str, object], path: Path) -> str | None:
    """The trimmed ``[musicbrainz] contact``, or None; an unusable one is refused, unquoted."""
    table = document.get(MUSICBRAINZ_TABLE, {})
    if not isinstance(table, Mapping):
        raise ConfigError(f"{path}: [{MUSICBRAINZ_TABLE}] is not a table")
    contact = table.get("contact")  # TOML has no null: None is an absent contact
    if contact is None:
        return None
    text = contact.strip() if isinstance(contact, str) else ""
    if not 0 < len(text) <= MAX_CONTACT_CHARS or not set(text) <= _CONTACT_CHARS:
        raise ConfigError(
            f"{path}: [{MUSICBRAINZ_TABLE}] contact is not 1-{MAX_CONTACT_CHARS} "
            "printable characters without parentheses"
        )
    return text


def _genres_in(document: Mapping[str, object], path: Path) -> tuple[str, ...] | None:
    """The ``[no_lyrics] genres`` array in a parsed file; None when it has no such table."""
    if NO_LYRICS_TABLE not in document:
        return None
    table = document[NO_LYRICS_TABLE]
    genres = table.get("genres") if isinstance(table, Mapping) else None
    if not isinstance(genres, list) or not all(isinstance(g, str) for g in genres):
        raise ConfigError(
            f"{path}: [{NO_LYRICS_TABLE}] genres is not an array of strings"
        )
    return tuple(genres)


def load_token(path: Path) -> Secret | None:
    """The key in the config file at *path*, or None when it holds only ``[no_lyrics]``.

    Raise `ConfigError` naming the path and what was wrong, never the value: a parse
    error is reported by its type alone, since its message may quote the line.
    """
    document = _read_document(path)
    _require_a_table(document, path)
    token = _token_in(document)
    return Secret(token) if token is not None else None


def permission_warnings(path: Path, *, posix: bool = _IS_POSIX) -> tuple[str, ...]:
    """One warning when the key file at *path* is readable by group or others.

    Off POSIX the bits do not describe who can read the file, so nothing is said.
    """
    if not posix:
        return ()
    mode = stat.S_IMODE(path.stat().st_mode)
    if not mode & _SHARED_READ_BITS:
        return ()
    return (
        f"{path} is readable by other users (mode {mode:o}); "
        f"restrict it with: chmod 600 {path}",
    )


def _resolved(secret: Secret | None, origin: KeyOrigin, path: Path) -> KeyResolution:
    """*secret* from the file at *path* as a resolution.

    It is absent with no warning when *secret* is None, else carrying the file's
    permission warnings.
    """
    if secret is None:
        return KeyResolution(None, KeyOrigin.ABSENT, path)
    return KeyResolution(secret, origin, path, permission_warnings(path))


def _from_explicit(config: Path) -> KeyResolution:
    """Step 1: the named file, the only source once named."""
    return _resolved(load_token(config), KeyOrigin.ARGUMENT, config)


def _from_default_file(path: Path) -> KeyResolution:
    """Step 3: the default file; an unusable one is absent, with the reason as a warning."""
    if not path.exists():
        return KeyResolution(None, KeyOrigin.ABSENT, path)
    try:
        secret = load_token(path)
    except ConfigError as error:
        return KeyResolution(None, KeyOrigin.ABSENT, path, (str(error),))
    return _resolved(secret, KeyOrigin.CONFIG_FILE, path)


class ResolveGeniusKey(Protocol):
    """Resolve the Genius key: *config* when given, else the environment, else the file.

    Not a run stage: the entry point resolves the key once, before any mode runs.
    """

    def __call__(
        self, config: Path | None, environ: Mapping[str, str]
    ) -> KeyResolution:
        """Raise `ConfigError` when an explicit *config* is unreadable or holds no table."""


def resolve_genius_key(
    config: Path | None, environ: Mapping[str, str]
) -> KeyResolution:
    """Resolve the Genius key from *config*, else *environ*, else the default file.

    Raise `ConfigError` when an explicit *config* cannot be read or holds none of the
    three tables; a named file holding no key resolves as absent. A blank environment
    value counts as unset.
    """
    if config is not None:
        return _from_explicit(config)
    token = environ.get(GENIUS_TOKEN_ENV, "").strip()
    if token:
        return KeyResolution(Secret(token), KeyOrigin.ENVIRONMENT, None)
    return _from_default_file(default_config_path(environ))


def _from_files[T](
    config: Path | None,
    environ: Mapping[str, str],
    read: Callable[[Mapping[str, object], Path], T | None],
) -> tuple[T | None, tuple[str, ...]]:
    """*read* of *config* when given, its errors raised; else of the default file.

    A named file holding no table is refused; the default file's problems are warnings.
    """
    if config is not None:
        document = _read_document(config)
        _require_a_table(document, config)
        return read(document, config), ()
    path = default_config_path(environ)
    if not path.exists():
        return None, ()
    try:
        return read(_read_document(path), path), ()
    except ConfigError as error:
        return None, (str(error),)


def resolve_no_lyrics(config: Path | None, environ: Mapping[str, str]) -> GenreListing:
    """The ``[no_lyrics]`` genres from *config* when given, else from the default file.

    A malformed ``[no_lyrics]`` table is a `ConfigError` in a named file (`_from_files`).
    """
    return GenreListing(*_from_files(config, environ, _genres_in))


def resolve_musicbrainz_contact(
    config: Path | None, environ: Mapping[str, str]
) -> ContactListing:
    """The ``[musicbrainz] contact`` from *config* when given, else from the default file.

    An unusable contact is a `ConfigError` in a named file (`_from_files`).
    """
    return ContactListing(*_from_files(config, environ, _contact_in))


#: Bound to its protocol, so the type checker holds the signature.
BOUND: ResolveGeniusKey = resolve_genius_key
