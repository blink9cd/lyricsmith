# sweep.py

Run `album` over a whole library, one album at a time, so that tracks holding only
untimed lyrics, or none, get another try at synced ones. `album` stays a one-album
tool: the sweep runs it as a child process on each album, with the album's own lock, backup,
manifest and exit code, and never with `--fresh`.

Only the albums with at least one track lacking synced lyrics are visited, found by an offline
survey that writes nothing. A run pauses between albums, records each album in a ledger kept
outside the library, resumes where a stopped run left off, and stops cleanly when the
providers keep refusing instead of answering.

## Prerequisites

`album` beside this tool, under the same project root, with the project's
dependencies, pinned under `[project].dependencies` in `../pyproject.toml`, installed: the
sweep imports `album` and runs it, and its survey reads each track's lyrics frames
with `mutagen`. Every prerequisite of `album` applies to each album a run visits, a
Genius key included when you want Genius asked. The library is a folder of `Artist/Album`
folders.

A run needs a POSIX system (macOS, Linux): it stops `album` with SIGINT, Ctrl-C's own
signal, which Windows does not deliver that way, and it locks the ledger with `flock`. On
Windows `--mode run` is refused with exit `2` before anything runs; the survey still works.

## A first real run

Try it on a handful of albums before the whole library:

1. **Survey.** `sweep.py --mode survey` (add `--library <dir>` when the library is
   not `~/Music/Albums`). It writes nothing and asks no provider, and prints how many tracks
   of each album are synced, untimed or without lyrics, which albums a run would visit, and
   which it would pass over as already done. It reads every MP3, so read "The survey"
   below first when the library lives in a cloud folder.
2. **A limited run.** `sweep.py --mode run --limit 3` visits the first three of
   those albums, pausing between them. Each album's own `album` output scrolls by on
   stderr; the report at the end lists each album visited and its undo command, and the
   `resume:` line continues from the fourth album.
3. **Look, and undo what you do not want.** Check the albums the report names, in your
   player or with `lyricsmith album --mode check`. Any album's undo line puts that album back
   exactly as it was (see "Undo").
4. **Go on.** Run the `resume:` line, with a larger `--limit` or none. A stopped or
   interrupted sweep picks up where it left off, and the albums already done are passed
   over.

`samples/README.md` walks the same steps offline over a small invented library.

## Usage

```text
sweep.py [--library <dir>] [--mode survey|run] [--lang <code>] [--refetch-untimed]
                [--refetch-implausible]
                [--offline] [--no-netease] [--no-bandcamp] [--no-youtube-music]
                [--no-genius] [--no-tekstowo] [--pause-seconds <s>] [--stop-after <n>]
                [--limit <n>] [--restart] [--changed-list <file>]
```

| Option | Default | Description |
| --- | --- | --- |
| `--library` | `~/Music/Albums` | The library: a folder of `Artist/Album` folders. A leading `~` is expanded. |
| `--mode` | `survey` | `survey` counts each album's tracks by the lyrics they hold and writes nothing. `run` runs `album` on each album a survey would visit. |
| `--lang` | `album`'s own, `eng` | The ISO 639-2 code `album` ranks a track's frames by and writes new frames in; the survey ranks frames by it too. Passed to `album` only when given. |
| `--refetch-untimed` | off | The refetch pass (see "Two passes"): a run passes `--refetch-untimed` to `album`, which asks the providers again for each track holding only untimed lyrics, and a survey lists the albums this pass would visit. Refused with `--offline`. |
| `--refetch-implausible` | off | A run passes `--refetch-implausible` to `album`, which asks the providers again for each track serving implausible lyrics and removes them when none answers. A survey or run with it also visits an album whose every track is synced when a track's serving lyrics are implausible, naming each such track (see "Which albums are visited"). It adds no pass: it rides the normal pass, or the refetch pass beside `--refetch-untimed` (see "Two passes"), and the survey's restart hint names it. Refused with `--offline`. |
| `--offline` | off | `run` only: pass `--offline` to `album`, which then asks no provider. For trying the sweep on a library, and for its samples. An offline visit never makes an album done. |
| `--no-netease` | off | `run` only: pass `--no-netease` to `album`, which then skips both NetEase steps. NetEase publishes no terms for the routes asked. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-bandcamp` | off | `run` only: pass `--no-bandcamp` to `album`, which then skips its Bandcamp step. Bandcamp's terms allow personal, non-commercial use. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-youtube-music` | off | `run` only: pass `--no-youtube-music` to `album`, which then skips its YouTube Music steps. YouTube's terms bar automated access. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-genius` | off | `run` only: pass `--no-genius` to `album`, which then skips its Genius step. Genius's terms bar scraping, and its lyrics are read from the song page. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-tekstowo` | off | `run` only: pass `--no-tekstowo` to `album`, which then skips its Tekstowo step. Tekstowo's terms allow home use only. See *Lyrics sources and their terms* in `../README.md`. |
| `--pause-seconds` | `DEFAULT_PAUSE_S`, shown by `--help` | `run` only: the pause between one album and the next. |
| `--stop-after` | `DEFAULT_STOP_AFTER`, shown by `--help` | `run` only: stop after this many consecutive refused albums (see "The stop rule"). |
| `--limit` | every album left | `run` only: visit at most this many albums, for a first real run. An album dropped just before its visit takes no place in the count. |
| `--restart` | off | `run` only: visit the albums the ledger holds as done as well. |
| `--changed-list` | no list | `run` only: write the absolute path of every MP3 the run's albums saved to this file, one per line, for a library app or script that rereads those files' tags; rewritten after each album, and added to when the file already exists, unless `--restart`. See "The changed list". |

The survey refuses `--offline`, the five `--no-*` options, `--limit`, `--restart` and
`--changed-list` with exit `2` rather than ignoring them, and both modes
refuse `--refetch-untimed` and `--refetch-implausible` with `--offline`, which `album` itself refuses, so every
album would only fail to run. The defaults for `--pause-seconds` and `--stop-after` are
provisional and deliberately conservative: the providers' limits at this scale have not
been measured, and both will be tuned from the first limited run.

## Which albums are visited

The albums are the non-hidden `Artist/Album` folders under `--library`, found by the shared walk
(`library_walk.md`): a multi-disc album's disc folders stay
one album, and a folder whose name starts with a dot is passed over. An artist folder that
cannot be listed is named on stderr and passed over. Only a library whose own folder cannot
be listed ends the sweep, with exit `2` and stderr saying so; an album the run cannot read is
that album's alone (`could not run`, see "How a visit ends").

An album reached through a link is dropped, by survey and run alike, and named on stderr: one
whose artist folder or album folder is a symbolic link, or whose real location is not inside
the library. `album` writes into each album it runs on, so a link would take those
writes out of the library. `album` does not refuse a link itself: it resolves the
album folder it is given, so a linked album runs wherever the link leads. The sweep's own
checks are the only guard, for a linked album folder and a linked artist folder alike,
which is why they stay. A run checks each album again just before its visit, since a folder
can be swapped for a link during a long sweep: an album that fails the check then is named
on stderr and in the report, and never run. That leaves only the milliseconds between the
check and `album` opening the folder, a window no check made from outside can close.
An album linked from elsewhere in the library is still visited once, under its own folder.

Each album's tracks are found as `album` finds them, and each track is classified the
way `album` decides whether a track is already synced: **synced** (millisecond-timed
lyrics frames, or a timed `.lrc` sidecar, holding words), **untimed** (words with no usable
timing), or **none**. A run visits an album with at least one untimed or no-lyrics track. A
track that cannot be classified, a sidecar that is not UTF-8 for one, is counted as
**unreadable**, never fatal, and its album is visited too: `album` reports the track
itself when it runs. A folder holding no MP3 is counted apart and never visited.

With `--refetch-implausible`, and only then, a track is also judged by `album`'s own rule
for the lyrics serving it being implausible, the rule a `--refetch-implausible` run picks
its tracks by (`../album/album.md`): synced lines whose latest starts past the audio's end
(the check's `past end` row), or more words a second than any song is sung at (its `too
many words` row), over the track's readable duration. A track serving such lyrics makes its
album one to visit, however synced, since that is the track the option asks again: a
fully synced album holding another song's lines is reached this way. Without the option
such an album is never visited.

## The survey

`--mode survey` prints one row per album with its synced, untimed-only, no-lyrics and
unreadable tracks, a total row, each unreadable track by path with its error's type in
parentheses, each track serving implausible lyrics when `--refetch-implausible` is given
(`implausible: <album>/<track> (<row>: <the row's measure>)`, the reason its album is
visited), the count of folders holding no MP3, and the albums a run would visit. It writes nothing and contacts nothing.

The table and its totals describe the tracks. The list of albums to visit is the run's own: the
survey reads the library's ledger (see "The ledger"), only reading it, in the language and pass
a run would use (`--refetch-untimed` selects the refetch pass, see "Two passes"), and an album
with a track still lacking synced lyrics that the ledger holds as done is left out of it, as a
run passes it over. Such an album, one no provider had lyrics for, say, is listed under its own
line, `already done, passed over by a run: <count>`, followed by a reminder that `--mode run
--restart` visits it as well (`--mode run --refetch-untimed --restart` in the refetch pass,
each with `--refetch-implausible` after the pass's option when it was given);
the line is absent when no album is done. A library with no ledger yet has nothing done, and
the survey makes none: no state folder, no ledger, no lock file. A ledger line that cannot be
read is reported on stderr and skipped, as a run skips it; a ledger file that cannot be read at
all ends the survey with exit `2`, stderr naming the file and the error, and nothing on stdout.

It does read every MP3 of every album, to classify its lyrics frames, and so does a run, which
surveys first. In a library kept in a cloud folder that stores files online only (iCloud
Drive's optimized storage, say), reading a file that is not on the machine downloads it, so a
first survey can download the whole library.

It also says that an album whose lyrics were embedded by other tools may have each track saved
once by its first run, even where the words stay the same: the frames `album` writes
from a timed sidecar keep the blank lines that those tools' frames left out. After that one
save, a later run finds the frames already match and saves nothing. In a library kept in a
synced cloud folder, that first save is one upload per track, which is expected.

## The run

`--mode run` surveys the library first and reads the ledger, passing over each album the
ledger holds as done (unless `--restart`, which writes its mark to the ledger and visits them
too). It says on stderr how many albums it will visit, `<N> of <M> albums to visit`, `M`
counting every album folder surveyed; the albums it visits are the ones a survey just before
would list. Then, for each album to visit in the walk's order, it:

1. pauses `--pause-seconds` when it ran an album since the last pause, so never before
   the first album, and never twice for an album dropped in between;
2. drops the album, unrun, if it is now reached through a link (see "Which albums are
   visited");
3. runs `<interpreter> <path>/album.py --album <album> --mode run`, the interpreter being
   the one running the sweep, adding `--lang`, `--offline`, `--refetch-untimed`,
   `--refetch-implausible` and each
   `--no-*` option when given (last, in the chain's order: `--no-netease`,
   `--no-bandcamp`, `--no-youtube-music`, `--no-genius`, `--no-tekstowo`), and with
   `--changed-list`,
   `--changed-list <per-album file>` (see "The changed list").
   `album.py` is found beside this tool, from this file's own location. Its progress
   and summary go to stderr as it prints them;
4. reads the manifest of the run it made, through `album`'s own reader;
5. appends the album's entry to the ledger;
6. applies the stop rule.

It ends with a table of the albums it visited: each album's run id, `album`'s exit code,
how the visit ended, and its written (upgraded), unchanged, not found, no lyrics expected,
silent, kept, errors, flagged and refusing tracks. A track whose implausible old lyrics a
`--refetch-untimed` or `--refetch-implausible` run removed, or whose stored placeholder
lyrics any run removed,
counts as not found: nothing new was found, and its implausible lyrics are gone. The
`no lyrics expected` column counts the tracks whose genre expects no lyrics (stand-up,
spoken word) and which nothing was found for, apart from not found; the stop rule counts
such a track as answered, as it does a not-found one. The `silent` column counts the tracks
which nothing was found for and which `album` then measured as silence with a local
`ffmpeg`; the stop rule counts such a track as answered too. The sweep passes `album`
no `--config`, so the genre list is the one `album` reads from its default config
file. Then each album's own undo command, `album`'s `--mode undo --run-id <id>` for
that album, the providers that refused, each album dropped just before its visit, and why
the run stopped. When it stopped early it prints a `resume:` line: the same command with the
same options, `--refetch-untimed`, `--refetch-implausible`, every `--no-*` option and
`--changed-list` included, and
never `--restart`, which picks up at the first album not done.

**Names print escaped.** A folder name is text the sweep did not write, so every line the
sweep prints to stdout, the survey, the run's report and the `resume:` line, shows a control
character (an escape sequence such as a color or a hyperlink, a C1 control, a carriage
return) and a direction override or separator as a visible escape (`\x1b`, `\u202e`), as
`album`'s own reports do; a table cell is escaped before its column is measured, so its row
still lines up. A pasted undo or resume command naming such a folder then holds the escapes
as text, the trade-off `album`'s own backup footer makes.

**The providers a sweep asks.** Each album's `album` runs with its own defaults, so
it asks Bandcamp (through MusicBrainz), YouTube Music and, last, Tekstowo as well as
LRCLIB, NetEase and, with a key, Genius (`../album/album.md`, *Bandcamp through
MusicBrainz*, *YouTube Music* and *Tekstowo.pl*). Each `--no-*` option leaves its
source out of every album: `--no-netease` NetEase, `--no-bandcamp` Bandcamp,
`--no-youtube-music` YouTube Music, `--no-genius` Genius and `--no-tekstowo` Tekstowo.
The tool is meant for home use, and five of those sources are used under terms that
limit it, which you must keep (*Lyrics sources and their terms* in `../README.md`). The albums share each source's cool-down file: a refusal during one album stops
that source for that album and starts its 24-hour cool-down, so every later album of the
sweep reads `cooling down` for it and does not ask it, while the other source goes on.
That block was accepted when YouTube Music and Tekstowo were turned on for sweeps: the
rest of the chain goes on as before, and deleting a cool-down file by hand lets the next
album ask that source again.

### Finding the run an album's child made

`album` names a run by its UTC start time to the second, with `-2`, `-3` and so on
appended on a collision, and lists a run once its manifest exists. The sweep notes the run ids
present before the child starts, and afterwards the ids that were not there before. Exactly one
new run is the one the child made, and is read. None means the child made no run; several mean
another `album` ran on the album alongside, which stderr notes, and since which run is
whose cannot be told, none is read. Either way the entry has no run id or counts, and the
outcome is `no record` (for an exit `0` or `1`). Comparing the runs before and after holds whatever the clock did between
them and whatever suffix a collision took, where predicting the id from a clock, or taking the
newest run, would not. When `album` exits `2` it could not start, or failed before any
MP3 changed, so no run is looked for. When it exits `3` it made its run but could not write its
record (the manifest, or its `--changed-list`): the run is looked for and read as above, as far
as its manifest goes, and its undo command is printed when its run was read.

### How a visit ends

| Outcome | When | Done? |
| --- | --- | --- |
| `done` | `album` exited `0` or `1` and its manifest was read, and the album was not refused | yes |
| `refused` | as `done`, but the providers only refused (see "The stop rule") | no |
| `could not run` | `album` exited `2`: the album locked by another run, say; or the sweep could not run `album` on it at all, its run folder (`.lyricsmith`) unreadable before `album` ran or `album` not started, which leaves no exit code, names the error on stderr and in a `could not run:` line of the report, and goes on to the next album | no |
| `interrupted` | Ctrl-C stopped `album`, which exited `130`; or Ctrl-C reached the sweep while it read the run `album` made, whatever `album` exited with (`0`, say) | no |
| `timed out` | the per-album bound stopped `album` | no |
| `no record` | `album` exited `0` or `1`, but no new manifest could be read; among the causes, a run folder (`.lyricsmith`) that can no longer be listed once `album` ran, which stderr names, its exit code kept | no |
| `record not written` | `album` exited `3`: it made its run but could not write its manifest, its `--changed-list` or its report (a closed stdout or stderr); whatever manifest it left is read and tallied, and its undo command printed | no |
| `failed` | `album` exited with a code it never gives | no |
| `offline` | as `done`, but run with `--offline`, so no provider was asked | no |
| `abandoned` | `album` did not stop even when killed, and was left running (see "Interrupting a run") | no |

Only a `done` album is skipped by a resumed run, so a refused, locked, interrupted, timed-out
or unrecorded (`record not written`) album is tried again. So is an album visited `--offline`: no provider was asked, so a later
sweep that may ask them visits it again.

An album is done per language: an album done under one `--lang` is visited again by a sweep
under another, since each language's lyrics frames are their own. It is done per pass too
(see "Two passes").

Undoing an album's run with its printed undo command puts the album back, but leaves the
ledger as it was: the album still reads as done, and a resumed sweep passes it over. To have
the sweep visit it again, run it with `--restart` (and with `--refetch-untimed` when the run
undone was the refetch pass's).

### Two passes

A sweep makes one of two passes. The normal pass runs `album` as it runs by
default, which serves a track holding only untimed lyrics from those lyrics. The refetch
pass, `--refetch-untimed`, passes that option to `album`, which asks the providers
again for each such track instead (`../album/album.md` describes what it does with the
answer). The albums it visits are the same as a normal pass's: those with an untimed,
no-lyrics or unreadable track. The survey counts a track whose only worded `SYLT` frame is
timed in MPEG frames as untimed, so the refetch pass may visit its album, but
`album` counts that frame as synced lyrics and leaves the track as a normal run
would.

Each pass keeps its own done albums in the library's one ledger. An album a normal sweep made
done is visited once by the refetch pass, and an album the refetch pass made done is visited
by a normal sweep; after that each pass passes it over. A `--restart` forgets only its own
pass's done albums.

`--refetch-implausible` is no third pass. It is handed to every album the sweep visits, in
whichever pass it runs, and the ledger records that pass, so the albums it holds as done are
the pass's own: an album a normal sweep made done is passed over by a normal sweep with
`--refetch-implausible` too, until `--restart`. It adds to the albums a pass visits those
whose every track is synced but one serves implausible lyrics (see "Which albums are
visited"); such an album, once the pass made it done, is passed over like any other until
`--restart`.

### The stop rule

An album is **refused** only when no provider answered it: at least one of its tracks met a
provider refusal, and no track shows a provider answering. `album` records a track whose
every provider failed as that track's fetch error, so a track that ended any other way after a
lookup heard from some provider, even beside another provider's refusal. A track shows an
answer when a provider's lyrics were taken for it; when it ended with nothing new found, not
found, no lyrics expected, silent, its old lyrics kept, or its implausible old lyrics removed (the
providers answered, emptily); or when it was served its own untimed lyrics or a machine-labeled `.lrc` (a machine transcript, or lyrics timed by `lyricsmith sync`) and
not every LRC provider's synced step (LRCLIB's and NetEase's) refused (such a track is looked up for synced
lyrics only). Under `--no-netease` each album asks LRCLIB's synced step alone, so LRCLIB's
refusal is every synced step refusing. YouTube Music's synced step is left out of that rule: an album may not ask it at
all (off, cooling down, stopped, or a track with no artist), which records nothing, so its
silence cannot be read as an answer. A track served other synced
lyrics it already held asked no provider, so it says nothing either way, and an album of nothing but
such tracks is not refused. A known limitation: a track for which no provider could be asked
(one with no artist tag and no Genius key: the LRC providers skip it unrecorded) ends not found
(or no lyrics expected) and so counts as answered, and one such track keeps an otherwise
refused album from counting as refused. Every refusal counts alike, whatever its detail: an
HTTP status, a provider's refusal code, or a timeout. Refusals are counted by track and by
provider, never one by one: a single failed call can leave a refusal at both the synced and the
untimed step.

So a provider that keeps refusing while another answers does not stop the sweep: those albums
end `done`. The ledger and the report still name the refusing providers and count the tracks
they refused, and `--restart` revisits the albums once that provider answers again.

A refused album adds one to a run of refused albums; a `done` album ends that run. An album
that could not run, timed out, failed, left no record, could not write its record, ran
`offline`, was `abandoned` or was `interrupted` neither adds to it nor ends it, since it says nothing about the providers. When
the run reaches `--stop-after`, the sweep stops cleanly after that album, prints the resume
command and exits `3`.

### The per-album bound

Each album's run is bounded by `ALBUM_TIMEOUT_S` in `src/sweep_child.py`, which `--help`
states in seconds. It is derived from `album`'s own chain and bounds: one track's worst
case, `TRACK_WORST_S`, asks every provider at its own bound on both the title's lookup and the
stripped-title retry, plus one silence measurement at `album`'s bound (a track is
measured at most once), and the album bound covers `_LONG_ALBUM_TRACKS` tracks at that worst.
The Genius, Bandcamp, YouTube Music and Tekstowo steps are each taken at their own
whole-call bound, which leaves room for what they do inside one call: Genius's
`genius_step_deadline_s` in `album`, for its paced attempts and its retry waits;
Bandcamp's `BANDCAMP_STEP_DEADLINE_S`, for the paced MusicBrainz lookups, the `/music`
page and up to `MAX_TIED_ENTRIES` album pages; YouTube Music's
`YOUTUBE_MUSIC_STEP_DEADLINE_S`, for its three requests spaced by
`YOUTUBE_MUSIC_MIN_INTERVAL_S`; and Tekstowo's `TEKSTOWO_STEP_DEADLINE_S`, for its two
requests spaced by `TEKSTOWO_MIN_INTERVAL_S`. Every other step is taken at `album`'s
`PROVIDER_DEADLINE_S`, within which LRCLIB's and NetEase's own retries end, so they need no
room of their own. LRCLIB, NetEase and YouTube Music each answer both kinds of lyrics in one
call, which their plain step re-reads, so each is counted once per lookup.
Past the bound the child is sent Ctrl-C's own signal, so it stops as an interrupted run does
(below); a child still running `STOP_GRACE_S` later is killed. The album is recorded as
`timed out`, and the sweep goes on.

### Interrupting a run

`album` runs in the same process group and on the same terminal, so Ctrl-C reaches it
too. It stops at once: it lets a write in hand finish, so no file is left half-written, cuts
the track in hand short and records it as interrupted in its manifest, prints its summary and
exits `130`. The sweep waits up to `STOP_GRACE_S` for that, generous because the write may be
to a synced cloud folder that stalls, then records the album as `interrupted` (not done),
prints its report and the resume command, and exits `130`. Ctrl-C during the pause stops before
the next album, the same way. Ctrl-C after `album` ended, while the sweep reads its run,
leaves the run's counts untallied and records the album as `interrupted` with `album`'s
exit code, again not done, then prints the report and the resume command and exits `130`. The
entry keeps the run's id when it was already found, so the report still prints that album's
undo command. Ctrl-C after the run was read, while the album's entry is made or written to the
ledger, does not cut that short: the entry is made as the run says the album ended and written
in full, once, through the same atomic replace, and then the sweep prints the report, with the
album's undo command, and the resume command, and exits `130`. The guard is not airtight: a
Ctrl-C landing in the few instructions between the run's read ending and the entry being made,
or between the entry being made and its write starting, still ends the sweep at once, as below.
Closing that gap would need SIGINT deferred across the whole stretch.

Those are the windows the sweep guards. A Ctrl-C anywhere else, during the survey, say, or
while the ledger is read, a `--restart` mark written or the lock taken, ends the sweep at once:
it prints the resume command, but no report, and exits `130`. No album is in hand then, so none
is left half recorded.

A second Ctrl-C while the sweep waits kills `album` at once. Every wait is bounded, the
one after a kill too: a child still not gone `KILL_WAIT_S` after its kill (one stuck in
uninterruptible I/O, say) is left running, named on stderr by its pid, and the album is
recorded as `abandoned`, with no exit code and no run read. Either way the album is recorded,
the report prints, and the sweep exits `130` after a Ctrl-C, or goes on after a time-out.

The project's bounded child-process helper, `bounded_run`, is deliberately not used
here: it captures the child's output, starts the child in a session of its own, beyond the
terminal's Ctrl-C, and kills the child's whole tree on Ctrl-C. A sweep needs the opposite on
all three counts, and `album` starts no process of its own, so there is no grandchild for
that helper to guard against.

## The changed list

`--changed-list <file>` makes the sweep keep the union of what its albums changed: the
absolute path of every MP3 any album's `album` saved, each once, in visit order. It is
meant for a library app or script that rereads those files' tags, run once the sweep is
over; the sweep never calls one. The file follows the same list contract as `album`'s
own `--changed-list`, and what each album's list holds is `album`'s "The changed list".

- **Per album.** Each `album` child is handed `--changed-list` with a file of its own
  in a private temporary folder, removed when the sweep ends. Once the child has ended, the
  sweep reads that list back under the contract's rules, merges it into the union and
  rewrites its own file atomically, with Ctrl-C held until the write lands. The list is
  written once before the first album too, so a sweep stopped before it still leaves one.
- **A list that already exists is added to.** A resumed sweep is the same pass as the leg
  it resumes, so the union starts from the paths the file holds, in their order, and each
  album's are added after them, each path once. The tracks a stopped leg listed are still
  owed their refresh, so no resume drops them, and the refresh can wait until the pass is
  over. A `--restart` starts a new pass, so it starts an empty list instead and never reads
  the file: run the refresh before a `--restart`, or name another file. A list lives one
  pass, from a `--restart` (or the first sweep) to the pass's end; to start one empty
  without a restart, remove the file or name another.
- **An existing list that cannot be read**, or that the list contract refuses (a last line
  with no linefeed, a relative path), stops the sweep before its first album, with exit `2`
  and stderr naming the file; the file is left as it was, since replacing it would drop
  tracks still owed a refresh. Refresh what it names, then remove it or name another file.
  A `--restart` never reads it, so it replaces such a file.
- **When it is current.** After each album, and once more as the sweep ends, however it
  ends: finished, stopped, or interrupted by Ctrl-C, through the same handler that prints the
  resume command. A sweep killed outright leaves the union of every album finished before the
  kill; only the album in hand is missing from it.
- **A child that left no readable list** (killed at the per-album bound, crashed, or stopped
  before it began, a held album lock among them) changed an unknown set of tracks, so every MP3
  of its album, found as `album` finds them, is listed instead, with one stderr line
  saying so. Those paths are under the album folder as `album` resolves it, each track's
  own path never resolved, as the child would have listed them. Refreshing an unchanged track
  only rereads its tags; missing a changed one is the failure to avoid. A child whose run
  errored but wrote its list is taken at its word.
- **An album whose `album` could not start** (the launch itself failed, so the album is
  `could not run`) is treated the same way, listing every MP3 of the album, but at once, before
  the next album, and its stderr line says that `album` could not start.
- **A path the list cannot hold** is left out and named on stderr, as `album` does,
  and does not change the exit code.
- **An album's list that cannot be removed** once it is merged is named on stderr
  ("could not remove the album tool's list") and changes nothing else: the album is
  already in the union, the exit code stays, and the private folder goes whole as the
  sweep ends.
- **The resume command carries the option**, with the same file, so the next leg adds to
  it.
- **A list that cannot be written** is tried again after each album; when the last write
  failed, the sweep says so on stderr after its report and exits `2`.
- **A private folder that cannot be made** (the temporary directory not writable, say)
  stops the run before its first album, with exit `2` and stderr naming the scratch folder
  and the error; nothing is visited, so no report prints.

## The ledger

The ledger lives under `$XDG_STATE_HOME/lyricsmith/sweep/` when that variable holds an
absolute path, else `~/.local/state/lyricsmith/sweep/`: never in the library. There is one
ledger per library, `ledger-<hash>.jsonl`, named by a prefix of the SHA-256 of the
library's resolved path, so two libraries never share one.

Each line is one JSON object:

- an album's entry, `"kind": "album"`: `album` (relative to the library), `run_id` (null when
  no run was found, none being looked for after a child left running or one that could not
  run, or when its manifest could not be read; kept, if already found, when a Ctrl-C cut the
  read short), `exit_code` (null for a child left running, or for an album the sweep could not run `album` on), `outcome` (the table above), `counts`
  (`written`, `unchanged`, `not_found`, `no_lyrics_expected`, `silent`, `kept`, `errors`,
  `flagged`; null whenever no tally was made; `no_lyrics_expected` and `silent` read as `0`
  from a line written before they were recorded), `refusing_tracks`, `refusing_providers`,
  `lang` (the language `album` ran in), `pass` (`"normal"`, or `"refetch-untimed"` for the refetch pass)
  and `time` (UTC, ISO 8601). An entry from before `lang` was recorded reads as
  `album`'s default language, and one from before `pass` was recorded as `"normal"`;
- a restart mark, `"kind": "restart"`, with its `time` and `pass`, written by `--restart`: the
  entries of that pass before it, in every language, no longer count as done, so a run resumed
  after a restarted one revisits what the restart has not yet redone. A mark without `pass`
  is the normal pass's.

An album is done in a language and pass when its newest entry in that language and pass since
that pass's last restart mark is `done`. Both passes share the one ledger of the library. Each
append rewrites the file atomically, through a temporary file and one replace, keeping every
byte already there. A line that cannot be read, from a hand edit say, is reported on stderr by
its line number and skipped, never fatal, and it stays in the file for a person to see. A
ledger file that cannot be read or written at all (no permission on its folder, say) ends the
sweep: stderr names the file and the error, the report still lists the albums visited so far,
the last one included though its entry was not recorded, and the exit code is `2`.

## One sweep per library at a time

Two sweeps on one library would each read the ledger and then rewrite it whole, so one's
entries would replace the other's. A run therefore holds a lock beside the ledger,
`ledger-<hash>.lock`, from before its survey to its end, and a second run on the same library
is refused at once with exit `2`, naming the lock file. The lock is the operating system's
(`flock`), let go when the sweep ends however it ends, so a crashed sweep leaves nothing to
clean up; the empty lock file stays. A lock file that cannot be made or opened, or a lock
`flock` refuses for any other reason (a file system that keeps no locks, say), ends the run
the same way, with exit `2` naming the lock file. A survey takes no lock. Sweeps on two
different libraries never meet.

## Exit codes

| Code | When |
| --- | --- |
| `0` | The sweep finished, every album it visited `done` (or `offline`) with `album` exiting `0`. A survey exits `0`. |
| `1` | The sweep finished, but an album did not end `done` (or `offline`) with exit `0`: track errors, refused providers, an album locked by another run or one the sweep could not run, a timeout, no record, a record `album` could not write (its exit `3`), an exit code `album` never gives, or a child left running (`abandoned`). |
| `2` | A usage problem, a library that is missing or cannot be listed, a ledger that cannot be read or written (a run still prints its report so far; a survey prints nothing), its lock not taken (another sweep running on the library, or the lock unavailable), a run on Windows, a `--changed-list` file whose last write failed (said on stderr after the report), an existing one it cannot read, or its private folder not made. |
| `3` | Stopped after `--stop-after` consecutive refused albums; the resume command is printed. |
| `130` | Interrupted with Ctrl-C; the album in hand, if any, is recorded (not done, unless its run was already read), and the resume command is printed. |

An album dropped because it is reached through a link, by the survey or just before its
visit, does not change the exit code: a sweep that dropped albums and visited the rest
cleanly exits `0`. Each drop is named on stderr, and one dropped just before its visit is
named in the report too.

## Undo

The sweep has no undo of its own: each album's run is an ordinary `album` run, with
its backup in the album's own `.lyricsmith/<run-id>/`. The report prints, for each album
visited whose run was found, the `album` command that undoes it. Run alone, as
`sweep.py`, the sweep prints it as `album.py`:

```text
<album>  undo: album.py --album '<library>/<album>' --mode undo --run-id <run-id>
```

Run that with the path to `album.py` in front, one album at a time. Run through the
`lyricsmith` command, as `lyricsmith sweep`, it prints the dispatched form, which runs as
printed:

```text
<album>  undo: lyricsmith album --album '<library>/<album>' --mode undo --run-id <run-id>
```

The ledger keeps each album's run id as well, so an undo command can be rebuilt from it
after the report has scrolled away. An undo, in either form, leaves the ledger as it was,
still holding the album as done (see "How a visit ends"), so run the sweep with
`--restart` to have it visit that album again, and with `--refetch-untimed` as well when the run undone was the refetch pass's.
A `--restart` revisits every album done in that pass, not only the one undone.

## Limitations

- **The pace and the stop are provisional.** The defaults of `--pause-seconds` and
  `--stop-after` were not measured against the providers' limits at a library's scale; they
  are conservative, and will be tuned from the first limited runs.
- **The stop rule can miss a refused album** that holds a track no provider could be asked
  about (see "The stop rule").
- **A survey reads every file**, and so downloads a library kept online only, and a first
  run may save each track once even where its words stay the same (see "The survey").
- **A folder swapped for a link** in the milliseconds between the sweep's last check and
  `album` opening it is followed (see "Which albums are visited").
- **`--mode run` needs a POSIX system**; on Windows only the survey runs.
- **Run folders accumulate.** Each album visited gains one `album` run folder per
  sweep, a few kilobytes each, under its own `.lyricsmith` folder. `album.py --mode prune`
  removes, album by album, those nothing needs; the undo command a ledger printed for a pruned run then reports no such
  run, and that run was already undone.

## Samples

`samples/README.md` walks a survey, an offline run and each album's undo over a small
invented library of two artists and three albums, built from `album`'s own sample
album, with the output each prints and how to compare yours. `tests/test_sweep_samples.py`
runs the same steps and fails when the README's blocks stop matching.

## Tests

`test_sweep.py` folds the suites under `tests/` into one run and fails any test that
does not run isolated: HOME and every XDG base directory point into a temporary folder, the
Genius key is unset and the network is a tripwire, so no test reaches a real library, the real
ledger or a provider. Most suites play `album` with a fake that writes each run's
manifest through `album`'s own records and writer; one runs the real `album`
with `--offline` over its own invented sample album and undoes the run from the printed undo
command. The ledger's line parser has property tests, since a person may edit the file.
