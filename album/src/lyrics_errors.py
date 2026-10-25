"""The errors one album module raises and another acts on.

A leaf: it imports nothing of the tool's, so `lyrics_model` re-exports every class here
and each importer keeps reading them from there. Pure: no I/O, no printing.
"""

from __future__ import annotations


class LyricsError(Exception):
    """Base of the errors one module raises and another acts on."""


class TagVersionError(LyricsError):
    """The file's ID3 version cannot be written back as it was (ID3v2.2)."""


class ReadBackError(LyricsError):
    """A save did not leave the frames that were written."""


class SidecarDecodeError(LyricsError):
    """A sidecar file is not UTF-8; it is never read with replacement characters."""


class FrameTooLargeError(LyricsError):
    """A track's lyrics frame is over the lyrics cap; it is reported, never used."""


class ManifestSchemaError(LyricsError):
    """A run manifest declares a schema this code does not know."""


class ConfigError(LyricsError):
    """An explicitly named config file is missing, unreadable or malformed."""


class AlbumUnusableError(LyricsError):
    """The album cannot be run: not a directory, no MP3 under it, or a path outside it."""


class NoRunError(LyricsError):
    """The album holds no run folder, or none by the requested run id.

    Undo and check have nothing to read, so the entry point exits 2.
    """


class TrackNotInRunError(LyricsError):
    """A requested track is not one the chosen run lists; the entry point exits 2."""
