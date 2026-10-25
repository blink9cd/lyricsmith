"""The album tool's --help: its formatter and its epilog, the prerequisites and exit codes.

Kept apart from the entry point, which declares every option itself (its companion
doc's consistency check reads them there), so the entry point stays inside the file
length bar. The epilog's exit codes are `lyrics_exit`'s ``EXIT_*`` constants, and its
companion doc's "Exit codes" table says the same. Nothing here reads a file or prints.
"""

from __future__ import annotations

import argparse

import help_formatter


class HelpFormatter(
    help_formatter.ReqAwareDefaultsFormatter, argparse.RawDescriptionHelpFormatter
):
    """Every real default shown, the epilog's breaks kept.

    The two bases override different methods, so they compose. The project's floor is
    below the Python release that stopped argparse stamping a default on a required
    option, which is what the shared base is for. A default of None is no value a
    person could type, so it is not shown: each such option's help says instead what
    leaving it out means.
    """

    def _get_help_string(self, action: argparse.Action) -> str | None:
        """*action*'s help, with its default appended unless that default is None."""
        if action.default is None:
            return action.help
        return super()._get_help_string(action)


#: The project doc's section that sets out each source's terms, and the words each
#: `--no-*` option's help, and the epilog, point to it with.
TERMS_SECTION = "Lyrics sources and their terms"
TERMS_POINTER = f"terms: README.md, {TERMS_SECTION}"
#: Each `--no-*` option's source's terms, in a few words: the album tool's help and the
#: sweep's, which passes each option on, both say them.
SOURCE_TERMS = {
    "--no-genius": "Genius's terms bar scraping, and its lyrics are read from the "
    "song page",
    "--no-netease": "NetEase publishes no terms for the routes asked",
    "--no-bandcamp": "Bandcamp's terms allow personal, non-commercial use",
    "--no-youtube-music": "YouTube's terms bar automated access",
    "--no-tekstowo": "Tekstowo's terms allow home use only",
}


def terms_note(flag: str) -> str:
    """The sentence a `--no-*` option's help ends with: its source's terms, and where."""
    return f"{SOURCE_TERMS[flag]} ({TERMS_POINTER})."


EPILOG = f"""\
Prerequisites:
  The project's dependencies installed (mutagen, requests, urllib3, lyricsgenius,
  pinned under [project].dependencies in its pyproject.toml). A Genius key is
  optional; without one the Genius step is skipped. It is read from --config when
  given, else GENIUS_ACCESS_TOKEN, else
  $XDG_CONFIG_HOME/lyricsmith/config.toml or ~/.config/lyricsmith/config.toml,
  whose [genius] table holds it as access_token. The same file's [no_lyrics]
  genres array replaces the built-in no-lyrics genre list. Its [musicbrainz]
  contact, when set, is added to MusicBrainz's User-Agent and sent nowhere else.
  Run it once the album is tagged and its gain set (MP3Gain, then Picard): it
  searches by the artist, title and album tags and the track's length.
  ffmpeg on PATH is optional (brew install ffmpeg). With it a track no provider
  answered is measured, locally, and ends silent when it holds no sound
  (album.md, Silent tracks); without it nothing is measured and the
  summary says 'silence check: off (no ffmpeg)'.

Providers:
  The chain asks LRCLIB and NetEase, then YouTube Music, for timed lyrics; then
  LRCLIB and NetEase, Bandcamp (its album page found through MusicBrainz), YouTube
  Music, Genius and last Tekstowo.pl for untimed lyrics. --no-netease,
  --no-bandcamp, --no-youtube-music and --no-tekstowo leave those steps out, as
  --no-genius does Genius's. The tool is meant for home use: each of those five
  sources is used under its own terms, which you must keep ({TERMS_POINTER}).
  A YouTube Music or Tekstowo refusal stops that source for the run and
  for 24 hours after, recorded in its own cool-down file under
  $XDG_STATE_HOME/lyricsmith/, else ~/.local/state/lyricsmith/:
  youtube_music.json and tekstowo.json; delete that file by hand to clear its
  cool-down early.

Output:
  The summary goes to stdout alone; progress and notices go to stderr. A run or a
  strip backs up every track, or the one --track names, under
  <album>/.lyricsmith/<run-id>/ before it changes anything, and --mode undo
  puts the album back from there. --mode compare writes nothing into the album
  and takes no lock; after its table it prints one one-track strip command per
  FLAG row, numbered by that row.
  --mode prune removes the run folders nothing needs: a run whose every track is
  undone, with nothing set aside, and a backup that never finished; --dry-run first
  shows which.

Exit codes:
  0    every track processed without an error (not found and FLAG, a compare's
       included, are not errors)
  1    at least one track errored: its backup, sidecar, fetch, strip, embed,
       check or undo; or, in a dry run, a track that could not be planned; or,
       in a prune, a run folder whose rename or removal failed
  2    could not run: bad options, no album or no MP3, the album lock held by
       another run, no run to check or undo, a --track outside the album, not one
       of its tracks or not in the run, an unusable --config, or a prune on a
       Python whose shutil.rmtree does not protect against links; or an
       album-level step failed with an OS error before any MP3 changed (the lock,
       the backup, a manifest read)
  3    the work finished, or stopped part-way, but the run's record (its
       manifest) or the --changed-list file could not be written, said after the
       report: an OS error, or a manifest write that did not read back as written
       (recording a finished track, which ends a run or an undo after its
       report); or, once a run, a strip, an undo or a prune began, its report,
       as a check's, a compare's or a dry run's: a progress line or the summary
       that a closed stdout or stderr refused, which ends it with no summary; it
       outranks 1 and 130, since undo needs the record
  130  interrupted (Ctrl-C); once the backup is done, the manifest records the
       tracks that finished"""
