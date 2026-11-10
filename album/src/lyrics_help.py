"""The album tool's --help epilog, its prerequisites and exit codes, and the terms notes.

Kept apart from the entry point, which declares every option itself (its companion
doc's consistency check reads them there), so the entry point stays inside the file
length bar; the entry point lays the help out with the shared
`help_formatter.RawNoneHidingFormatter`, which keeps the epilog's line breaks as
written. The epilog's exit codes are `lyrics_exit`'s ``EXIT_*`` constants, and its
companion doc's "Exit codes" table says the same. Nothing here reads a file or prints.
"""

from __future__ import annotations

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
  The dependencies [project].dependencies in its pyproject.toml pins, installed.
  A Genius key is optional; without one the Genius step is skipped. It is read
  from --config when given, else GENIUS_ACCESS_TOKEN, else
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
  sources is used under its own terms, which you must keep
  ({TERMS_POINTER}).
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
  --mode fix-text reads the transcripts sync --heard-dir kept and, for each such
  track, writes the first text its transcript sings, the album's other untimed
  texts tried before the providers'; with none it removes the wrong text,
  backed up, as 'wrong text removed'. Every provider failing removes nothing,
  and nor does an album that looks spoken (a Comedy or Spoken Word genre, or
  most tracks not sung): its tracks read 'removal withheld'. With
  --hold-for-genius it never asks Genius, keeps each text every provider asked
  answered without singing, read 'removal withheld, held for Genius', and lists
  those tracks in that file.
  --mode prune removes the run folders nothing needs: a run whose every track is
  undone, with nothing set aside, and a backup that never finished; --dry-run first
  shows which.
  --mode accept keeps the album as it is and drops every run's backup: by default
  it only reports, the tracks whose only copy would go first; --apply copies each
  run's manifest under $XDG_STATE_HOME/lyricsmith/accepted/, else
  ~/.local/state/lyricsmith/accepted/, then removes <album>/.lyricsmith/, and
  undo with it. Run the report first, on any library.

Exit codes:
  0    every track processed without an error (not found and FLAG, a compare's
       included, are not errors)
  1    at least one track errored: its backup, sidecar, fetch, strip, embed,
       check or undo; or, in a dry run, a track that could not be planned; or,
       in a prune, a run folder whose rename or removal failed; or, in an
       accept, the removal of .lyricsmith or of an earlier accept's leftover;
       or a compare with an ERROR row (a tag or sidecar it cannot read, or every
       provider asked failing); or a fix-text track whose wrong text a guard
       kept (a provider that failed or was cooling down, an unread track)
  2    could not run: bad options, no album or no MP3, the album lock held by
       another run, no run to check or undo, a chosen run whose manifest could
       not be read, a --track outside the album, not one of its tracks or not in
       the run, a --heard-dir that is not a folder, or an unusable --config; an
       accept refused (an unfinished run, an unreadable manifest or run folder,
       an entry lyricsmith did not write, a link), nothing removed, or whose
       records could not be written, nothing removed but an earlier accept's
       leftover; or an album-level step failed with an OS error before any MP3
       changed (the lock, the backup, a manifest read)
  3    the work finished, or stopped part-way, but the run's record (its
       manifest) or the --changed-list file could not be written, or a fix's
       --hold-for-genius file, said after the report: an OS error, or a
       manifest write that did not read back as written (recording a finished
       track, which ends a run or an undo after its report); or, once a run, a
       strip, an undo, a prune or an accept began, its report, as a check's, a
       compare's or a dry run's: a progress line or the summary that a closed
       stdout or stderr refused, which ends it with no summary; it outranks 1
       and 130, since undo needs the record
  4    paste only: the track refused by its guards (no transcript record, its
       text changed since it was heard, its tags or sidecars unreadable),
       nothing written
  5    paste only: the run was made, but its one track errored
  6    paste only: not written, the paste not sung or not lyrics to write
  130  interrupted (Ctrl-C); once the backup is done, the manifest records the
       tracks that finished; an accept stopped after its rename leaves
       .lyricsmith.accepting, which the next accept finishes"""
