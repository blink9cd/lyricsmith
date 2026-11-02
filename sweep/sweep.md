# sweep.py

Run `album` over a whole library, one album at a time, so that tracks holding only
untimed lyrics, or none, get another try at synced ones. `album` stays a one-album
tool: the sweep runs it as a child process on each album, with the album's own lock, backup,
manifest and exit code, and never with `--fresh`.

Only the albums with at least one track lacking synced lyrics, or holding a machine-timed
`.lrc` its frames have not taken, are visited, found by an offline survey that writes nothing. A run pauses between albums, records each album in a ledger kept
outside the library, resumes where a stopped run left off, and stops cleanly when the
providers keep refusing instead of answering.

`--mode silence` is a separate, read-only pass: it measures every track of the library for
silence, whatever its lyrics, and lists the silent ones with a verdict on each, in a
tab-separated list and an HTML page beside it (see "The silence pass").

`--mode fix-text` drives `album --mode fix-text` over the albums holding a transcript
record `sync --heard-dir` kept, with a run's ledger, pace and resume (see "The fix pass").

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
`--mode accept` locks the ledger the same way, so it too needs a POSIX system.

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
sweep.py [--library <dir>] [--mode survey|run|accept|silence|fix-text] [--apply] [--lang <code>]
                [--refetch-untimed] [--refetch-implausible] [--owe-earlier-runs] [--heard-dir <dir>]
                [--offline] [--no-netease] [--no-bandcamp] [--no-youtube-music]
                [--no-genius] [--no-tekstowo] [--pause-seconds <s>] [--stop-after <n>]
                [--limit <n>] [--restart] [--changed-list <file>] [--resume <file>]
```

| Option | Default | Description |
| --- | --- | --- |
| `--library` | `~/Music/Albums` | The library: a folder of `Artist/Album` folders. A leading `~` is expanded. |
| `--mode` | `survey` | `survey` counts each album's tracks by the lyrics they hold and writes nothing. `run` runs `album` on each album a survey would visit. `accept` reports, by `album`'s accept rules, what dropping each album's runs and backups would remove, and removes them with `--apply` (see "Accept"). `silence` measures every track's peak and lists the silent ones, judged (see "The silence pass"). `fix-text` runs `album --mode fix-text` on each album holding a record in `--heard-dir` (see "The fix pass"). |
| `--apply` | off | `accept` only: record each album's manifests outside the library, then remove its `.lyricsmith` folder, every backup and undo with it. Without it, accept only reports. Refused by `survey` and `run`. |
| `--lang` | `album`'s own, `eng` | The ISO 639-2 code `album` ranks a track's frames by and writes new frames in; the survey ranks frames by it too. Passed to `album` only when given. |
| `--refetch-untimed` | off | The refetch pass (see "Two passes"): a run passes `--refetch-untimed` to `album`, which asks the providers again for each track holding only untimed lyrics, and a survey lists the albums this pass would visit. Refused with `--offline`. |
| `--refetch-implausible` | off | A run passes `--refetch-implausible` to `album`, which asks the providers again for each track serving implausible lyrics and removes them when none answers. A survey or run with it also visits an album whose every track is synced when a track's serving lyrics are implausible, naming each such track (see "Which albums are visited"). It adds no pass: it rides the normal pass, or the refetch pass beside `--refetch-untimed` (see "Two passes"), and the survey's restart hint names it. Refused with `--offline`. |
| `--owe-earlier-runs` | off | A survey or run also counts as owed an album an earlier run left owing: one whose newest ledger line is `done` but was written before the `owed` outcome existed, owing each refusing provider it names and each provider its run left out by a cool-down while that provider could still answer a track (see "An owed album"). A run asks it only those providers; a survey lists it with its debt. Refused with `--restart`, `--offline` and `--mode accept`. |
| `--heard-dir` | none | `fix-text` only, and required there: the transcript folder `sync --heard-dir` wrote, made absolute (a leading `~` expanded) and passed to each album's fix, which only reads it. Every other mode refuses it. |
| `--offline` | off | `run` only: pass `--offline` to `album`, which then asks no provider. For trying the sweep on a library, and for its samples. An offline visit never makes an album done. |
| `--no-netease` | off | `run` and `fix-text` only: pass `--no-netease` to `album`, which then skips both NetEase steps. NetEase publishes no terms for the routes asked. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-bandcamp` | off | `run` and `fix-text` only: pass `--no-bandcamp` to `album`, which then skips its Bandcamp step. Bandcamp's terms allow personal, non-commercial use. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-youtube-music` | off | `run` and `fix-text` only: pass `--no-youtube-music` to `album`, which then skips its YouTube Music steps. YouTube's terms bar automated access. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-genius` | off | `run` and `fix-text` only: pass `--no-genius` to `album`, which then skips its Genius step. Genius's terms bar scraping, and its lyrics are read from the song page. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-tekstowo` | off | `run` and `fix-text` only: pass `--no-tekstowo` to `album`, which then skips its Tekstowo step. Tekstowo's terms allow home use only. See *Lyrics sources and their terms* in `../README.md`. |
| `--pause-seconds` | `DEFAULT_PAUSE_S`, shown by `--help` | `run` and `fix-text` only: the pause between one album and the next. |
| `--stop-after` | `DEFAULT_STOP_AFTER`, shown by `--help` | `run` and `fix-text` only: stop after this many consecutive refused albums (see "The stop rule"). |
| `--limit` | every album left | `run`, `fix-text` and `silence` only: visit at most this many albums, for a first real run or a preview. An album dropped just before its visit takes no place in the count. |
| `--restart` | off | `run` and `fix-text` only: visit the albums the ledger holds as done as well. |
| `--changed-list` | no list | `run` and `fix-text` only: write the absolute path of every MP3 the run's albums saved to this file, one per line, for a library app or script that rereads those files' tags; rewritten after each album, and added to when the file already exists, unless `--restart`. See "The changed list". |
| `--resume` | a new list | `silence` only: append to this silence list, skipping the albums it marks done, after cutting off the lines of an album it did not finish (see "The silence pass"). |

The survey refuses `--offline`, the five `--no-*` options, `--limit`, `--restart` and
`--changed-list` with exit `2` rather than ignoring them, and `--pause-seconds` and
`--stop-after` when given a value other than their default; both modes
refuse `--refetch-untimed` and `--refetch-implausible` with `--offline`, which `album` itself refuses, so every
album would only fail to run. A run refuses `--owe-earlier-runs` with `--restart`, which
already asks every provider of every album, and with `--offline`, which asks none. The defaults for `--pause-seconds` and `--stop-after` are
deliberately conservative: they have run a whole library unchanged, and have not been
tuned. `accept` takes only
`--library`, `--mode` and `--apply`: it refuses every other option with exit `2`, since it
takes every run of every album whatever the language or pass (`--pause-seconds` and
`--stop-after` are refused when given a value other than their default). `silence` takes
only `--library`, `--mode`, `--limit` and `--resume`, and refuses every other option the
same way; every other mode refuses `--resume`. `fix-text` takes a run's options but
`--offline`, `--refetch-untimed` and `--refetch-implausible`, which `album`'s fix itself
refuses, `--owe-earlier-runs`, since a fix is never owed (see "The fix pass"), and
`--apply`; it needs `--heard-dir`.

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
timing), or **none**. A run visits an album with at least one untimed or no-lyrics track.

A synced track whose timed `.lrc` a machine labeled (`sync`'s `[by:machine timed]`, or the
transcribe tool's `[by:machine transcript]`) makes its album one to visit too while its
frames have not taken the file: they hold no timed lyrics, or the file's lines at other
times, as after the file was timed again. `sync` writes the `.lrc` and leaves the frames as
they were, so a run is what embeds it, by `album`'s own rule for a labeled file. The survey
reads the frames for this, not just the sidecar's presence, and names each such track
**unembedded**. A visit settles it, offline or not: `--offline`, `album` embeds the file;
online, the synced providers are asked first and an answer is embedded instead, else the
file. Frames holding the file itself, or other timed lines, owe nothing.

A track that cannot be classified, a sidecar that is not UTF-8 for one, is counted as
**unreadable**, never fatal, and its album is visited too: `album` reports the track
itself when it runs. A folder holding no MP3 is counted apart and never visited.

With `--refetch-implausible`, and only then, a track is also judged by `album`'s own rule
for the lyrics serving it being implausible, the rule a `--refetch-implausible` run picks
its tracks by (`../album/album.md`): synced lines whose latest starts past the audio's end
(the check's `past end` row), or more words a second than any song is sung at (its `too
many words` row), over the track's readable duration. A track serving such lyrics makes its
album one to visit, however synced, since that is the track the option asks again: a
fully synced album holding another song's lines is reached this way. Without the option
such an album is never visited. Under it or `--refetch-untimed`, a track holding a lyrics
frame over `album`'s lyrics cap is named the same way, with `album`'s reason (`a USLT
frame is too large: over 65536 characters`), since either run judges the frame junk and
asks the track again; it is counted by what it holds beside that frame, a sidecar it
cannot read holding nothing. Without either option `album` ends such a track as its
error, so it is counted unreadable.

## The survey

`--mode survey` prints one row per album with its synced, untimed-only, no-lyrics and
unreadable tracks, a total row, each unreadable track by path with its error's type in
parentheses, each track serving implausible lyrics when `--refetch-implausible` is given
(`implausible: <album>/<track> (<row>: <the row's measure>)`, the reason its album is
visited; one holding a lyrics frame over the cap, under either refetch option, names that
reason instead, and its album is
visited), each unembedded track (`unembedded: <album>/<track> (machine timed)`, counted
as synced in the table, and the reason its album is visited, though an album the ledger
holds as done is passed over for it, as for any other, without `--restart`), the count of folders holding no MP3, and the albums a run would visit. It writes nothing and contacts nothing.

The table and its totals describe the tracks. The list of albums to visit is the run's own: the
survey reads the library's ledger (see "The ledger"), only reading it, in the language and pass
a run would use (`--refetch-untimed` selects the refetch pass, see "Two passes"), and an album
with a track still lacking synced lyrics that the ledger holds as done is left out of it, as a
run passes it over. Such an album, one no provider had lyrics for, say, is listed under its own
line, `already done, passed over by a run: <count>`, followed by a reminder that `--mode run
--restart` visits it as well (`--mode run --refetch-untimed --restart` in the refetch pass,
each with `--refetch-implausible` after the pass's option when it was given);
the line is absent when no album is done. The survey reads every provider's hold once,
as a run reads them before each owed album (see "An owed album"): an album whose debt,
an earlier run's included, is held whole is left out of the albums to visit and listed
under `owed, waiting on a cool-down or breaker: <count>`, one line each, as the run's
report lists it, the line absent when none waits. With `--owe-earlier-runs`, each album that
option adds to the visits (see "An owed album") is among those listed to visit, and is
listed again under `owed by earlier runs, visited again: <count>`; one whose debt waits is
listed under the waiting line alone, and is not counted under this one. Each is one line, with what
it owes, as the run's report names an owed album (`<album>  owes: LRCLIB, Tekstowo`), so a
catch-up pass can start with a survey as its dry run. The list to visit is in the walk's
order, except that an owed album whose last visit was challenged by a provider it still
owes comes after every other album to visit (see "An owed album"), in the order a run
visits them. The line is printed whenever the option
is given, `0` included, so a survey that finds no such album says so. To find what each owes,
the survey reads the run manifest of every album needing a visit whose newest ledger line is
such an earlier `done` line, only reading it. A library with no ledger yet has nothing done, and
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
would list, except that `N`, and the `[i/N]` line before each album, also count the owed
albums the run may still pass over (see "An owed album"), which the survey lists apart:
the run reads the holds afresh before each one, so it cannot know them in advance. It
names on stderr, once each, every owed album it puts last for a challenge
(`<album>: visited last, its last visit challenged by Genius`, see "An owed album").
Then, for each album to visit in the walk's order, those put last after the rest, it:

1. passes over the album, unrun, when it is owed and its whole debt waits on a cool-down
   or an open breaker (see "An owed album");
2. pauses `--pause-seconds` when it ran an album since the last pause, so never before
   the first album, and never twice for an album dropped or passed over in between;
3. drops the album, unrun, if it is now reached through a link (see "Which albums are
   visited");
4. runs `<interpreter> <path>/album.py --album <album> --mode run`, the interpreter being
   the one running the sweep, adding `--lang`, `--offline`, `--refetch-untimed`,
   `--refetch-implausible` and each
   `--no-*` option when given (last, in the chain's order: `--no-netease`,
   `--no-bandcamp`, `--no-youtube-music`, `--no-genius`, `--no-tekstowo`), and with
   `--changed-list`,
   `--changed-list <per-album file>` (see "The changed list"), and, revisiting an owed
   album, `--only-providers <name,...>` before the `--no-*` options (see "An owed
   album").
   `album.py` is found beside this tool, from this file's own location. Its progress
   and summary go to stderr as it prints them;
5. reads the manifest of the run it made, through `album`'s own reader;
6. appends the album's entry to the ledger;
7. applies the stop rule.

Every 25 albums visited, it says on stderr how it is going, in one line: the albums
visited, done and owed so far, and the tracks each provider refused, by reason, as
`album`'s `refusals:` line counts them, a lookup a provider's open breaker left unsent
under `breaker open` rather than under the reason that opened it:

```text
progress: 25 albums visited, 21 done, 3 owed; refusals: LRCLIB overloaded 12, unreachable 1; Genius blocked 1, breaker open 20
```

It ends with a table of the albums it visited: each album's run id, `album`'s exit code,
how the visit ended, and its written (upgraded), unchanged, not found, removed, no lyrics
expected, silent, kept, errors, flagged and refusing tracks. The `removed` column counts
the tracks whose implausible old lyrics a `--refetch-untimed` or `--refetch-implausible`
run removed, or whose stored placeholder lyrics any run removed: nothing new was found,
and the implausible lyrics are gone. Below the table come the totals over the albums
visited, each line only when it counts something, in `album`'s own words and order:

```text
written: upgraded 40, new synced 6, embedded 12
removed: placeholder 3, past end 1
refusals: LRCLIB overloaded 12, unreachable 1; Genius blocked 1, breaker open 20
retried: LRCLIB overloaded 30
```

`written:` and `removed:` split the written and removed tracks as `album`'s summary does
(`../album/album.md`, the run summary's `written:` and `removed:` lines), and a fix
pass's `removal withheld: spoken word <n>` counts the kept tracks whose removal a fix
withheld because the album looks spoken (*`--mode fix-text`* there), each counted in the
`kept` column too; `refusals:`
counts the tracks each provider refused, by reason, then the tracks its open breaker left
unsent as `breaker open` (each still owed, as any refusal but `unrecognized` is), and
`retried:` the retries made after a refusal, by provider and reason, from each run's
manifest. Then each owed album, with the providers it owes (`<album>  owes: LRCLIB,
Genius`), then, when any was, each owed album passed over because its whole debt waits
(see "An owed album"):

```text
owed, waiting on a cool-down or breaker: 2
  Artist/Album  waits on: Genius (breaker), Tekstowo (cool-down)
  Artist/Other  waits on: YouTube Music (cool-down)
```

The
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
return) and a direction override or separator as a visible escape (`\x1b`, `\u202e`),
through the shared `stdio_utf8.spell_unsafe`, as `album`'s own reports do; a table cell is escaped before its column is measured, so its row
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
The rest of the chain goes on as before, and deleting a cool-down file by hand lets the
next album ask that source again.

### Finding the run an album's child made

`album` names a run by its UTC start time to the second, with `-2`, `-3` and so on
appended on a collision, and lists a run once its manifest exists. The sweep notes the run ids
present before the child starts, and once it ends the ids that were not there before. Exactly one
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
| `done` | `album` exited `0` or `1` and its manifest was read, and the album was neither refused nor owed | yes |
| `refused` | as `done`, but the providers only refused (see "The stop rule") | no |
| `owed` | as `done`, but a provider refused a track it could still answer, or the run left out by its cool-down, or after its stop, a provider that could still answer a track (see "An owed album"); `refused` comes first | no |
| `could not run` | `album` exited `2`: the album locked by another run, say; or the sweep could not run `album` on it at all, its run folder (`.lyricsmith`) unreadable before `album` ran or `album` not started, which leaves no exit code, names the error on stderr and in a `could not run:` line of the report, and goes on to the next album | no |
| `interrupted` | Ctrl-C stopped `album`, which exited `130`; or Ctrl-C reached the sweep while it read the run `album` made, whatever `album` exited with (`0`, say) | no |
| `timed out` | the per-album bound stopped `album` | no |
| `no record` | `album` exited `0` or `1`, but no new manifest could be read; among the causes, a run folder (`.lyricsmith`) that can no longer be listed once `album` ran, which stderr names, its exit code kept | no |
| `record not written` | `album` exited `3`: it made its run but could not write its manifest, its `--changed-list` or its report (a closed stdout or stderr); whatever manifest it left is read and tallied, and its undo command printed | no |
| `failed` | `album` exited with a code it never gives | no |
| `offline` | as `done`, but run with `--offline`, so no provider was asked | no |
| `abandoned` | `album` did not stop even when killed, and was left running (see "Interrupting a run") | no |
| `track errors` | the fix pass only: `album --mode fix-text` exited `1`, a wrong text kept (see "The fix pass") | no |

Only a `done` album is skipped by a resumed run, so a refused, owed, locked, interrupted,
timed-out or unrecorded (`record not written`) album is tried again. So is an album visited `--offline`: no provider was asked, so a later
sweep that may ask them visits it again.

An album is done per language: an album done under one `--lang` is visited again by a sweep
under another, since each language's lyrics frames are their own. It is done per pass too
(see "Two passes").

Undoing an album's run with its printed undo command puts the album back, but leaves the
ledger as it was: the album still reads as done, and a resumed sweep passes it over. To have
the sweep visit it again, run it with `--restart` (and with `--refetch-untimed` when the run
undone was the refetch pass's).

### An owed album

A track **owes** a provider when that provider refused it for any reason but
`unrecognized` (`../album/album.md`, each refusal's `reason`), and the provider **could
still answer** it. An `unrecognized` refusal is an answer of a shape the tool does not
read, or a client error such as a `400` or `404` that says the one request was bad,
which asking again would not change; the other reasons (rate limited, overloaded,
unreachable, blocked) may pass. A provider with a synced step in the chain (LRCLIB,
NetEase, YouTube Music) could still answer a track that does not now hold synced lyrics.
A provider with only untimed steps (Bandcamp, Genius, Tekstowo) could still answer only a
track that holds no lyrics at all: one that ended `not found` or `removed`, or one that
kept what it held (`kept existing`, or an error at any stage, which left it as it was)
when its held kind is `nothing` (wordless frames) or was not read, taken as none because
a debt forgotten costs the lyrics. An ordinary run looks a track holding untimed lyrics
up with the synced steps only, so such a provider could never improve it. A track a
refetch asked again (its manifest marks it `refetch_untimed` or `refetch_implausible`)
was asked as though it held nothing, and any answer would have replaced its lyrics, so
an untimed-only provider could still answer it too while it holds no synced lyrics. One
test serves every half of the rule: the refusals, the cool-downs and the stops below.
A track holds synced lyrics when it ended `written` or
`unchanged` with synced lyrics, or `kept existing` when its `held` kind (what it held
before the run, which it still holds) is synced. A kept track's own `kind`, when recorded,
is an answer a `--fresh` run declined, so it is never read for this. A kept track whose
`held` is unknown (null: a manifest written before it was recorded, or a sidecar that
could not be read) cannot be known to hold synced lyrics, so it owes: a revisit costs one
more lookup, a debt forgotten costs the lyrics. In a sweep, which never runs `--fresh`, a
kept track held wordless frames or, in the refetch pass, untimed lyrics.

A track owes, too, each provider with a cool-down (Tekstowo and YouTube Music) that its
run left out by that cool-down, when the provider could still answer it. Such a provider's
manifest state is `cooling down` (`../album/album.md`): its cool-down still held when the
run began, so it was asked for no track and recorded nothing, refusal included. By the
test above, YouTube Music is owed by a track that does not hold synced lyrics, and
Tekstowo only by a track holding no lyrics at all or one a refetch asked again: outside
a refetch, a track left with untimed or synced lyrics, written or kept, owes Tekstowo
nothing. A provider with a cool-down that a
refusal stopped mid-run (manifest state `stopped`) was asked up to its stop, and every
track after the stopping one skipped it unrecorded (`../album/album.md`, each provider's
stop rule): the **stopping track**, the last track the run reached recording a refusal by
it, and every reached track after it owe it when it could still answer them, by the same
test, whatever the stopping refusal's reason. A stop on YouTube Music's reply that is not
JSON, recorded `unrecognized`, owes so too: it wrote a cool-down like any stop, so it was
no one bad request. A track before the stop owes it only by its own refusals. A
`stopped` provider no reached track records a refusal by leaves every reached track
owing it that it could still answer: a revisit costs one more lookup, a debt forgotten
costs the lyrics. A provider `off` (offline, or turned off by its option) or `on` is
never owed so. This holds for every run, an ordinary visit as much as a revisit, so a
sweep made while Tekstowo cools ends each album it could still answer `owed`, and the
next sweep asks it again.

An album owes the providers its tracks owe, in the chain's order. Only a provider
`--only-providers` can name is owed: a refusal by one no step asks any more is no debt
anyone can pay. A provider the sweep turns off with its `--no-*` option is never owed: a
run that never asked it records nothing for it, and the sweep holds to that whatever a
manifest says.

An album that finished owing any provider is `owed`, its owed providers listed in the
ledger (`owed_providers`); one whose providers only refused stays `refused`. A cool-down's
debt is no refusal, so an album owing only that is `owed`, never `refused`. The next
sweep of the same pass and language visits it again, as it visits any album not done,
and hands `album` `--only-providers` naming those providers, so the revisit asks
none that answered. The revisit's outcome replaces the last: `done` when it owes nothing
more, still `owed`, naming what it still owes, when it does. In detail:

- **Turned-off providers stay off.** The revisit is given the sweep's own `--no-*`
  options as well, and `album` lets them win: a provider both named and turned off is
  off. An album owed only providers this sweep turns off is asked none of them, and so
  ends `done`; resume without the `--no-*` option to ask them. Each such dropped debt is
  said on stderr before the album's visit, naming the album and the providers dropped
  (`<album>: no longer owed Genius, which this sweep turns off`).
- **An untimed-only provider's debt ends once the track holds lyrics.** A track the
  revisit serves from untimed lyrics it now holds (written by another provider on the
  last visit, say) is looked up with the synced steps only, so a provider that answers
  only untimed lyrics (Genius) is not asked again for it; the track no longer owes it by
  the test above, and the revisit ends `done` when nothing else is owed. In the refetch
  pass such a track is asked again as though it held nothing, so it stays owed until a
  revisit no longer records that provider's refusal for it, or no longer asks it again.
  There a revisit can replace an untimed answer the last visit accepted: the refusing
  provider comes earlier in the chain than the one that answered, so asked again, it may
  answer first.
- **An album whose whole debt is waiting is passed over.** Before visiting an owed album,
  and before the pause owed ahead of it, the sweep reads every provider's hold afresh,
  through `album`'s own readers (`../album/album.md`, the cool-down files and *The breaker
  file*), at the sweep's clock and in the state folder its environment names, which `album`
  inherits: a provider is held by a **cool-down** while its cool-down file is cooling, and
  by a **breaker** while its breaker file is open (its window not yet ended). The album's
  **effective debt** is what it owes less the providers this sweep's `--no-*` options turn
  off. When that is not empty and every provider in it is held, a revisit would ask none of
  them and end `owed` again, so the album is **passed over**: not run, no ledger line, no
  `--limit` place, no pause owed before the next album, no change to the stop rule's run of
  refused albums, and not counted in `albums left`. Its `[i/N] <album>` line is followed on
  stderr by `<album>: passed over, waits on: Genius (breaker), Tekstowo (cool-down)`, and
  the report lists it (see "The run"). It stays owed, so the next sweep reads the holds
  again; the holds are read again before each owed album, so one a refusal starts mid-sweep
  passes over a later album. A cool-down that ends during the pause after the holds were
  read still passes the album over; the next sweep visits it. An album only partly held is
  visited as before, naming every provider it owes, the held one included: `album` asks it
  nothing and keeps its debt. An effective debt that is empty (every provider turned off) is
  visited as before, and an offline sweep never passes an album over, since it asks no
  provider. A state file the reader ignores (unreadable, or holding what `album` would
  refuse) is no hold, as in `album`, and its warning is said on stderr once a sweep.
- **Offline**, the revisit names no provider (`album` refuses `--only-providers` with
  `--offline`, which asks none anyway) and ends `offline`, so the next visit online asks
  every provider.
- **Any outcome but `owed`** in between (refused, interrupted, timed out, no record and
  the rest) leaves no debt in the ledger, so the next visit asks every provider: never
  fewer than the album owes.
- **The same pass.** The revisit is a visit of the pass that left the debt, with its
  options: in the refetch pass it carries `--refetch-untimed`, and each pass keeps its
  own owed albums, as it keeps its own done ones. `--refetch-implausible` is handed on as
  to any visit. With `--changed-list`, the revisit is one more album of the same pass, so
  what it saves is merged into the list like any other album's (see "The changed list").
- **A challenged album is visited last.** A challenge is a lookup sent and refused as
  `blocked`: a Genius `403` after its retries from a challenge page, say, or Tekstowo's
  challenge, which records no status. `album` stops asking that provider at the first
  one: its breaker opens for 60 minutes, or, for a cooling source such as Tekstowo, it
  stops for the run (`../album/album.md`). While it is stopped no lookup of it is sent:
  an open breaker records each skipped lookup as a `blocked` refusal marked `unsent`,
  counted as `breaker open`, and a stopped cooling source records none. Since the
  walk's order never changes, the same owed album would come first in each sweep, draw
  the challenge again, and cost every album after it that provider. So an owed album
  whose newest entry records a challenge (`challenged_providers`) by a provider it still
  owes is visited after every other album to visit, each group in the walk's order, and
  stderr says so before the first visit. The challenge is still sent once a sweep and still stops the
  provider: nothing retries it or gets around it, and a challenge settles no debt. A
  lookup an open breaker kept unsent is no challenge, so an album owing a provider only
  through `breaker open` lookups keeps its place. An earlier entry records no challenge.
- **`--restart` forgets it** with everything else: a restart visits every album asking
  every provider, in the walk's order.
- **Albums swept before the `owed` outcome existed are owed only under
  `--owe-earlier-runs`.** Their ledger lines hold `done`, however their providers refused
  or a cool-down left one out, and no `owed_providers` at all, so a sweep without the
  option passes them over. With it, an album whose newest entry of the pass and language
  is such a line (an *earlier* entry) owes, in the chain's order:
  - each provider its `refusing_providers` names, by label (`LRCLIB`), when
    `--only-providers` can name it: a label no provider step has is dropped, as the owed
    rule drops one;
  - each provider its run's manifest owes by the cool-down and stop rule above, read
    from that manifest exactly as a run's own tally reads it: so an earlier run owes
    Tekstowo only for a track holding no lyrics at all, as above. A run id that is null or no run's
    name, or a manifest that cannot be read, adds no such debt, and stderr says so naming
    the album; its refusals are still owed.

  An album owing anything is visited as an owed album is, everything above applying, and
  an album owing nothing stays `done`. Only an album needing a visit is read so (see
  "Which albums are visited"). A line that holds `owed_providers`, even empty, is never
  read so, so a later `done` that names a refusal but owes nothing stays `done`. The
  revisit's entry is an ordinary new line, so later sweeps read it as any other without the
  option; a resume command carries the option, so a resumed leg still owes what it has not
  revisited.
- An owed album makes the sweep exit `1`, as any album not `done` does, and so does
  one passed over: it is still owed.

### Two passes

A run makes one of two passes; the fix pass, `--mode fix-text`, is a third (see "The fix
pass"). The normal pass runs `album` as it runs by
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
pass's done albums. The fix pass keeps its own done albums the same way, so no fix makes
a run's album done, and no run a fix's.

`--refetch-implausible` is no third pass. It is handed to every album the sweep visits, in
whichever pass it runs, and the ledger records that pass, so the albums it holds as done are
the pass's own: an album a normal sweep made done is passed over by a normal sweep with
`--refetch-implausible` too, until `--restart`. It adds to the albums a pass visits those
whose every track is synced but one serves implausible lyrics (see "Which albums are
visited"); such an album, once the pass made it done, is passed over like any other until
`--restart`.

### The fix pass

`--mode fix-text --heard-dir <dir>` drives `album --mode fix-text` over the library, for
the untimed texts `sync --heard-dir <dir>` found are not sung. It visits only the albums
of the walk, links dropped as for a run, one of whose tracks has a record in the
transcript folder: a record is matched to a track as `album` matches it, by the file it
names however its path was spelled, so the sweep picks exactly the albums whose fix would
find one. Each such album is surveyed as a run surveys it, so one holding no track
without synced lyrics is not visited; an unembedded track does not count here, since a fix
embeds nothing. A file the folder holds that cannot be read as a
record is one notice on stderr, and the rest are still used; records naming no track of
the library's albums are counted in one notice and passed over. A transcript folder that
is not a folder, or cannot be listed, exits `2` before any album is visited. The sweep
only reads the folder: every record is kept, and `sync` deletes each one once its track
is timed (`../album/album.md`, *`--mode fix-text`*).

Each album's child is `album --album <dir> --mode fix-text --heard-dir <dir>` with
`--lang`, the `--no-*` options and the album's `--changed-list` when given, and with no
`--only-providers`, ever. The visit is paced, bounded, recorded in the ledger, resumed
and interrupted exactly as a run's is; only how it ends differs:

| The fix | Outcome |
| --- | --- |
| made a run whose providers only refused, whether it exits `0` or `1` | `refused`, as for a run, toward the stop rule |
| exits `0`, having made a run | `done`: each text it took was written or removed, or its removal withheld on an album that looks spoken, which is no error and never retried |
| exits `0` and made no run (nothing to fix: every recorded track skipped) | `done` |
| exits `1` otherwise | `track errors`: a wrong text kept, never done, so a later fix pass visits it again |

Every other end (a timeout, Ctrl-C, no record, exit `2`, `3` or one `album` never gives)
is the run's (see "How a visit ends"). A fix is never `owed`. An owed album is
revisited naming only the providers it owes, and `album`'s fix treats a provider left
out by `--only-providers` as one it never meant to ask: such a revisit could remove a
text the other providers were never asked about again. So an album whose fix kept a text
is revisited whole, asking every provider. A cool-down or an open breaker that stops a
provider part-way through the sweep is read by each later album's own fix, which then
removes nothing (`ERROR fetch: ... not asked (cooling down): nothing removed`), so that
album ends as `track errors` and is retried. A provider turned off by its `--no-*`
option is left out on purpose, as `album` documents, and blocks no removal.

**When a done album is visited again.** A fix pass passes over an album whose last fix
ended `done` only while its records in the transcript folder are as that fix left them.
Each fix entry that ends `done` holds a `records_digest`: the SHA-256 over the sorted
pairs of path and fingerprint of the album's records, matched as `album` matches them,
read once the fix ended (so it holds what the fix kept and removed). The next fix pass
reads the same digest of each album's records now, and an album whose digest differs is
visited again: a record gained (another track heard not sung), one whose fingerprint
changed (a text replaced and heard not sung again), or one regained after the fix
removed it (an undo, then `sync` keeping the same record again). An entry written before
the digest holds none, so its album is visited again once. The digest reads only the
records, never the tracks' texts: an undo of a written text leaves the record as it was
(`sync` has not yet re-timed the track), so its album stays done until `--restart`.
A `--restart` of the fix pass forgets only the fix pass's done albums, never a run's.

The report is a run's: the table, the `written:` and `removed:` lines (a removal counted
as `wrong text`), the `removal withheld: spoken word <n>` line when a fix withheld any,
an `errors: <n> tracks kept for a later fix` line when a track
errored, each album's undo command, and the `resume:` line, which resumes the fix on the
same `--heard-dir`. The exit code is a run's: `0` only when every album visited ended
`done` with `album` exiting `0`.

### The stop rule

An album is **refused** only when no provider answered it: at least one of its tracks met a
provider refusal, and no track shows a provider answering. `album` records a track whose
every provider failed as that track's fetch error, so a track that ended any other way after
a lookup heard from some provider, even beside another provider's refusal. A track shows an
answer when a provider's lyrics were taken for it; when it ended with nothing new found, not
found, no lyrics expected, silent, its old lyrics kept (a fix's withheld removal among
them), or its implausible old lyrics removed (the providers answered, emptily); or when it was served its own untimed lyrics or a
machine-labeled `.lrc` (a machine transcript, or lyrics timed by `lyricsmith sync`) and not
every LRC provider's synced step (LRCLIB's and NetEase's) that the album asked refused (such
a track is looked up for synced lyrics only). Under `--no-netease`, or on a revisit naming
LRCLIB alone, an album asks LRCLIB's synced step alone, so LRCLIB's refusal is every synced
step refusing; a revisit naming no LRC provider asks no synced step, so such a track shows
no answer. YouTube Music's synced step is left out of that rule: an album may not ask it at
all (off, cooling down, stopped, or a track with no artist), which records nothing, so its
silence cannot be read as an answer. A track served other synced lyrics it already held
asked no provider, so it says nothing either way, and an album of nothing but such tracks is
not refused. A known limitation: a track for which no provider could be asked (one with no
artist tag and no Genius key: the LRC providers skip it unrecorded) ends not found (or no
lyrics expected) and so counts as answered, and one such track keeps an otherwise refused
album from counting as refused. Every refusal counts alike, whatever its detail: an HTTP
status, a provider's refusal code, or a timeout. Refusals are counted by track and by
provider, never one by one: a single failed call can leave a refusal at both the synced and
the untimed step.

So a provider that keeps refusing while another answers does not stop the sweep: those albums
end `owed` (see "An owed album"), and the next sweep asks only the refusing providers again.
Nor does a cool-down: a provider left out by one records no refusal, so an album owing only
that is `owed`, never `refused`, and ends a run of refused albums as a `done` album does.
The ledger and the report name the refusing providers and count the tracks they refused, by
reason. The owed rule, unlike this one, reads the refusal's reason.

A refused album adds one to a run of refused albums; a `done` or `owed` album, which a
provider answered, ends that run. An album
that could not run, timed out, failed, left no record, could not write its record, ran
`offline`, was `abandoned` or was `interrupted` neither adds to it nor ends it, since it says nothing about the providers. When
the run reaches `--stop-after`, the sweep stops cleanly after that album, prints the resume
command and exits `3`.

In the fix pass a `track errors` album, where a provider answered but a text was kept
because another failed, neither adds to the run nor ends it either. That is stricter than
a run, where such an album ends `owed` or `done` and so ends the run: in the fix pass it
does not reset the count, so a provider outage stops the fix pass sooner, before it
spends visits that can only keep their texts.

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

`album` runs in the same process group and on the same terminal, so Ctrl-C reaches it too.
It stops at once: it lets a write in hand finish, so no file is left half-written, cuts the
track in hand short and records it as interrupted in its manifest, prints its summary and
exits `130`. The sweep waits up to `STOP_GRACE_S` for that, generous because the write may
be to a synced cloud folder that stalls, then records the album as `interrupted` (not done),
prints its report and the resume command, and exits `130`. Ctrl-C while the next album's
cool-downs and breakers are read, during the pause, while the next album is checked for a
link, or while its `album` is set up (its runs listed, its command and its `--changed-list`
made) stops before that album, the same way: no entry is written for it, and a
`--changed-list` already made for it lists its every MP3, as for an `album` that could not
start. Ctrl-C after `album` ended, while the sweep reads its run, leaves the run's counts
untallied and records the album as `interrupted` with `album`'s exit code, again not done,
then prints the report and the resume command and exits `130`. The entry keeps the run's id
when it was already found, so the report still prints that album's undo command. Ctrl-C
after the run was read, while the album's entry is made or written to the ledger, does not
cut that short: the entry is made as the run says the album ended and written in full, once,
through the same atomic replace, and then the sweep prints the report, with the album's undo
command, and the resume command, and exits `130`. The guard is not airtight: a Ctrl-C
landing in the few instructions between the run's read ending and the entry being made, or
between the entry being made and its write starting, still ends the sweep at once, as below.
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
  and stderr naming the file. So does anything there but a regular file (a FIFO pipe,
  refused at once rather than waited on) and a file over 64 MiB; a link is followed, since
  you named it. The list is yours, not the sweep's state, so it is written with the
  default permissions. In each case the file is left as it was, since replacing it would
  drop tracks still owed a refresh. Refresh what it names, then remove it or name another
  file. A `--restart` never reads it, so it replaces such a file.
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
  the next album, and its stderr line says that `album` could not start. Ctrl-C while
  the album tool is set up, once its list is handed out, lists every MP3 the same way with
  the same stderr line, but the album gets no entry: the sweep stops before it.
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
library's resolved path, so two libraries never share one. The ledger and its lock file
are readable by you alone (permissions 0o600), and each folder the sweep makes for them
0o700; a folder already there, `~/.local/state` say, keeps its permissions.

Each line is one JSON object:

- an album's entry, `"kind": "album"`: `album` (relative to the library), `run_id` (null when
  no run was found, none being looked for after a child left running or one that could not
  run, or when its manifest could not be read; kept, if already found, when a Ctrl-C cut the
  read short), `exit_code` (null for a child left running, or for an album the sweep could not run `album` on), `outcome` (the table above), `counts`
  (`written`, `unchanged`, `not_found`, `removed`, `no_lyrics_expected`, `silent`, `kept`,
  `errors`, `flagged`, and `written_by` and `removed_by`, the written tracks by category
  and the removed ones by reason, keyed by `album`'s words, such as
  `{"upgraded": 40}`; null whenever no tally was made), `refusing_tracks`,
  `refusing_providers`, `owed_providers` (the providers the album owes, by the manifest's
  values, such as `["Lrclib", "Genius"]`; only an `owed` entry's are asked on a revisit),
  `challenged_providers` (the providers whose challenge the run met, by the manifest's
  values, such as `["Genius"]`; see "An owed album"),
  `refusals` (the tracks each provider refused, by reason, such as
  `{"Lrclib": {"overloaded": 12}}`, with the tracks its open breaker left unsent under
  `"breaker open"`, which a line written before it was counted apart never holds),
  `retried` (the manifest's retries, by provider and reason, in the same shape, never
  `"breaker open"`),
  `lang` (the language `album` ran in), `pass` (`"normal"`, `"refetch-untimed"` for the refetch pass, or `"fix-text"` for the fix pass),
  `records_digest` (a `done` fix entry's alone, a SHA-256 in lower-case hex, left out of
  every other line; see "The fix pass")
  and `time` (UTC, ISO 8601). An entry from before `lang` was recorded reads as
  `album`'s default language, and one from before `pass` was recorded as `"normal"`. A
  field added later reads as its default from a line written before it: `no_lyrics_expected`,
  `silent` and `removed` as `0`, `written_by`, `removed_by`, `refusals` and `retried` as
  empty, `challenged_providers` and `records_digest` as none, `owed_providers` as none (which `--owe-earlier-runs` tells apart from an empty
  list, see "An owed album"). **An older line's `not_found` still includes its
  removals**: a sweep counted a removed track as not found until `removed` was recorded
  apart. Every count must be a whole number of `0` or more, every provider, reason and
  category one the tool knows, and every owed or challenged provider one
  `--only-providers` can name, named once; a line holding anything else is skipped as unreadable;
- a restart mark, `"kind": "restart"`, with its `time` and `pass`, written by `--restart`: the
  entries of that pass before it, in every language, no longer count as done, so a run resumed
  after a restarted one revisits what the restart has not yet redone. A mark without `pass`
  is the normal pass's.

An album is done in a language and pass when its newest entry in that language and pass since
that pass's last restart mark is `done`, and owed, with that entry's providers, when it is
`owed`. Both passes share the one ledger of the library. Each
append rewrites the file atomically, through a temporary file and one replace, keeping every
byte already there. A line that cannot be read, from a hand edit say, is reported on stderr by
its line number and skipped, never fatal, and it stays in the file for a person to see. A
ledger file that cannot be read or written at all (no permission on its folder, say) ends the
sweep, and so does a ledger that is a link, a FIFO pipe or anything else but a regular
file, or one over 256 MiB, each refused at once and never read or written through: stderr
names the file and the error (a link as one), the report still lists the albums visited so
far, the last one included though its entry was not recorded, and the exit code is `2`.

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

## Accept

`--mode accept` keeps a library's albums as they are and drops what their runs kept for
undo: the whole-library form of `album`'s `--mode accept`. The rules for each album are
`album`'s own, applied in process and unchanged: what refuses an album, which tracks are
at risk, where the records go, and the order that leaves the recoverable state are
*`--mode accept`* under "The modes" in `../album/album.md`. The sweep adds no rule.

- **Which albums.** The albums a run considers (see "Which albums are visited"), every one
  of them, whether or not a run would visit it: an album whose every track is synced holds
  runs too. An album reached through a link is dropped and named on stderr, as a run drops
  it. Each album is checked again just before it is accepted, as a run checks it before
  each visit: one swapped for a link since the walk began is skipped, named on stderr and
  counted under `skipped`, and never followed. An album holding no run is counted, and
  prints nothing.
- **One album at a time, each for itself.** An album that accept refuses (an unfinished run,
  an unreadable manifest, a run folder it cannot list, an entry lyricsmith did not write, a
  link) and one it could not start on (its album lock held by a run, a linked `.lyricsmith`
  or lock, an error reading it) is refused alone, with nothing of it touched, and the walk
  goes on; the pass then exits `1`.
- **The report.** Each album holding a run prints its name relative to the library, then
  `album`'s accept report for it: the tracks at risk first, its runs, the records folder
  under `--apply`, its counts. One summary ends the pass:

  ```text
  albums 5 · would clear 2 · refused 3 · holding at-risk tracks 1 · no run 0 · skipped 0 · errors 0
  refused by reason: album locked 1 · unfinished run 2
  report only: nothing was removed; add --apply to remove it
  ```

  `would clear` is `cleared` under `--apply`; it counts the albums holding at-risk tracks
  too, since `--apply` takes them; `holding at-risk tracks` counts albums, each album's own
  report counting its tracks, and takes in an album whose removal or records failed as
  well as one cleared, but never a refused one, which is not judged for risk. A refused album counts once under each reason it was refused
  for. An error raised before the judgment has a reason of its own: `album locked` (a run
  holds the album's lock), `linked run folder` (a linked `.lyricsmith` or lock),
  `unsafe leftover` (a `.lyricsmith.accepting` that is a link or not a folder),
  `unreadable` (an error reading the album), and `could not start` for any other.
  `errors` counts the albums whose removal, or whose records, failed. The last line, a
  report-only pass's alone, is said once, at the end.
- **The ledger.** Accept holds the ledger's lock (see "One sweep per library at a time")
  for the whole pass, so it never runs beside a sweep run on the same library, and a run
  started meanwhile is refused; it reads no ledger and writes no ledger line. An album the
  ledger holds as done stays done: a later run passes over it as before.

Run it without `--apply` first, on any library, and read the at-risk tracks before
removing anything; on a library that still owes a catch-up pass over albums a run could
not finish asking, run that pass first, since its undo lives in the run folders accept
removes.

## The silence pass

`--mode silence` measures every track of every album for silence and writes a list of the
silent ones, each with a verdict on whether it is likely meant. It is read-only: nothing in
the library is written, and the ledger is neither read nor written. It answers a question a
run cannot: a run measures only a track no provider answered, while this pass measures each
track, whatever lyrics it holds, so it also finds a real song that came out silent when it
was ripped.

| Option | Default | Description |
| --- | --- | --- |
| `--library` | `~/Music/Albums` | The library, as for every mode. |
| `--limit` | every album left | Measure only the first this many albums still to do: a preview. |
| `--resume` | a new list | Append to this list, skipping the albums it marks done. |

**It needs `ffmpeg` on `PATH`**, the measurement `album` makes for a track no provider
answered (*Silent tracks* in `../album/album.md`), with its threshold: a whole-track peak at
or below `-60` dBFS is silent. Without `ffmpeg` it exits `2` before anything is read or
made. Each track is decoded locally, and nothing leaves the machine.

**The albums** are those a survey walks (see "Which albums are visited"), in the same order,
an album reached through a link dropped and named on stderr; each album's tracks are every
MP3 `album` would find. An album with a folder that cannot be listed, its own or a disc
folder inside it, is named on stderr and skipped, unmarked, so a resume tries it again: it
counts among the albums left. A linked MP3 or folder inside an album is never followed, and
is named on stderr, `skipped link <path>: a link is never followed`, by its path as the list
writes one. A folder holding no MP3 is marked done with no rows. A track whose tags cannot
be read is named on stderr with its error's type, as the survey names one, and keeps its row
with its title, track number and length empty.

**It reads every MP3 in full**, decoding each one, so in a library kept in a cloud folder
that stores files online only (iCloud Drive's optimized storage, say), it downloads every
file that is not on the machine. Preview it with `--limit` first.

**The list** is a new tab-separated file,
`$XDG_STATE_HOME/lyricsmith/silence/silent-<UTC start>.tsv`, or under
`~/.local/state/lyricsmith/silence/` when `XDG_STATE_HOME` is unset or not an absolute
path: the state directory the ledger uses, never the library. The list and its page are
readable by you alone (permissions 0o600), and each folder the pass makes for them 0o700,
as the ledger's are. Its header names the columns:

| Column | What it holds |
| --- | --- |
| `verdict` | `likely meant`, `likely a ripping mistake`, `unsure`, or `not measured` |
| `reason` | why, in a few words: `gap track: placeholder title, 4.0 s`, or the measurement's own failure (`ffmpeg exit 1`, `timed out`) |
| `peak_dbfs` | the whole-track peak, to one decimal place; empty when not measured |
| `length_s` | the length in seconds, to one decimal place, read from the file; empty when unknown |
| `track_no` | the track number as its tag writes it (`3/12`); empty when untagged |
| `title` | the title tag; empty when untagged |
| `path` | the album relative to the library, `/`, then the track relative to the album |

A tab or line break in a field is written as a space, so one line is one track, and a
title or path that is not UTF-8 writes each undecodable byte as `\udcXX`, as the marker
below does. Only a
silent track and a track that could not be measured get a row. After each album's rows
comes a line `#album-done`, a tab and the album, an album with no silent track included,
so a resume knows it was measured. In that line a backslash, tab, line feed and carriage
return in the album's name are written `\\`, `\t`, `\n` and `\r`, so no two albums share
one, and a name that is not UTF-8 writes each undecodable byte as `\udcXX`. An album's rows
and its marker are written in one write and flushed when the album is measured.

**The page.** However the pass ends (finished, stopped by `--limit`, interrupted by Ctrl-C
once the finished albums are written, or a `--resume` with nothing left), it then writes the
whole list as one HTML page beside it: the list's name with `.html`, `silent-<UTC
start>.html` (a list resumed under another name, `kept.tsv` say, gets `kept.html`; one
already ending `.html`, in any case, gets `.html` added, so the page is never the list). The
page replaces only a page this pass wrote, which it knows by a `generator` meta element in
the page's head naming `lyricsmith sweep --mode silence`: a file already at the page's name
without it (`report.html` beside a list resumed as `report.tsv`, say), one that is the list
itself under a second name (another case of its name on a case-insensitive volume), or
anything there but a regular file (a FIFO pipe, refused at once rather than waited on, or a
link, never read through), is left as it is and named on stderr, as a page that cannot be
written. Two lists sharing a name but their suffix (`a.tsv` and `a.txt`) share one page, the
last pass's. It is read from the list, so a resumed list's page shows every leg's rows, and
it ignores an unfinished album's lines as a resume does. It opens with a count per verdict,
each linked to its section, then a section per verdict in this order: `likely a ripping
mistake`, `unsure`, `not measured`, `likely meant`. Each section's rows are sorted by album,
then by track path, and show the album, the file (the track's path in its album, or the
whole path when the album is the library's own folder or a row was edited to name another),
track number, title, length, peak and reason; an empty section shows its count of 0 and says
no track has that verdict. The header says when the page was written, in UTC, and names the
list by its file name only, so the page holds no folder path. It is laid out by the shared
HTML page library (`html_page`): one self-contained file with inline style, no script, and
nothing a browser would fetch. Every value from the list is escaped as the terminal's lines
escape it (`stdio_utf8.spell_unsafe`), a control character shown as a visible `\xNN` and a
direction mark or line separator as its `\uXXXX`, then made safe as page text. The album is
read from the album's `#album-done` line, its escapes undone; every other field is shown as
the list writes it. The page is replaced atomically, through the album tool's fsynced
temporary file in the same folder, whose name is cut to fit beside a long list's, so a page
open in a browser is never half written. A page that cannot be written is named on stderr,
the list is kept, the resume command that writes it again is printed, and the exit is `1`.

**Rendering an existing list's page.** A `--resume` of a list with no album left measures
nothing and only writes its page again:

```text
sweep.py --mode silence --library <library> --resume <list>
```

It still needs `ffmpeg` on `PATH` and the same library, which it walks to find what is
left: an album added to the library since the list was made is measured into it first.

**The verdicts** judge each silent track from its title, its length, and how many silent
tracks its album holds; the first rule that matches wins:

1. A placeholder title, at most 10 seconds long, in an album with at least 3 silent tracks:
   `likely meant`, a spacer run (the silent tracks some discs carry before a hidden track).
2. A placeholder title, at most 10 seconds long: `likely meant`, a gap track.
3. A real title, over 10 seconds long: `likely a ripping mistake`, a song that came out
   silent.
4. Anything else: `unsure`. A length that cannot be read is neither short nor long, so it
   is always `unsure`.

A placeholder title is a missing or empty one, or one that reads `untitled`, `silence`,
`silent`, or `track` and a number, once its case, spacing, brackets and punctuation are set
aside: `[Untitled]`, `Track 05` and `track05` are placeholders, `Track` alone is not. The
10 seconds (`GAP_MAX_S`) and the 3 tracks (`SPACER_RUN_MIN`) are named constants in
`src/sweep_silence_verdict.py`.

**The output.** Each album's progress line, `[n/M] <album>`, goes to stderr, `M` being the
albums this pass measures. At the end, stdout counts the albums a resumed list held as done,
the albums and tracks measured, the silent tracks by verdict and the tracks not measured,
then why the pass ended, how many albums are left, the list's path and its page's:

```text
already done: 0
albums measured: 4
tracks measured: 52
silent: 5
  likely meant: 4
  likely a ripping mistake: 1
  unsure: 0
not measured: 0
finished; albums left: 0
list: <state>/lyricsmith/silence/silent-<UTC start>.tsv
page: <state>/lyricsmith/silence/silent-<UTC start>.html
```

The `page:` line is left out when the page could not be written.

`albums skipped: <n>` is added when an album could not be read. A skipped album counts among
the albums left, since a resume tries it again, and whenever any album is left, skipped ones
included, or when the page could not be written, the resume command below follows the
list's and the page's paths.

**A preview and a resume.** `sweep.py --mode silence --limit 5` measures the first five
albums and ends `limit reached; albums left: <n>`, then prints the command that goes on into
the same list:

```text
resume: sweep.py --mode silence --library <library> --resume <list>
```

Run it with the tool's path in front, adding a `--limit` for another preview. A resumed pass
reads the list first, and refuses one it cannot read, or one holding a line this pass does
not write, with exit `2` naming it and the line, leaving it untouched. It also refuses, with
exit `2` naming the list and the library and leaving it untouched, a list that marks albums
done when none of them is an album of `--library`: a list another library began. It skips
each album the list marks done. A pass stopped in the middle of writing an album, by a crash
say, can leave that album's rows with no marker after them, or a last line with no line
feed: the resume cuts those off, says on stderr how many lines it dropped, and measures that
album again, so no track is listed twice. A list holding only its header, with no line feed
after it, is written its header again, and stderr says so: `the list's header had no line
ending; it was written again`.

**Ctrl-C** stops the pass at the track in hand. Every album already written stays in the
list; the album in hand is not written. It writes the page of what the list holds, prints
the summary so far, ending `interrupted; albums left: <n>` with the list and page, then the
resume command, and exits `130`, even when the page could not be written. Ctrl-C while the
page is being written stops that write, leaving no temporary file: stderr says the page was
not written, and the summary, with no page line, and the resume command, which writes it,
still print, with exit `130`.

**Exit codes.** `0` when every track of every album was measured and the page written; `1`
when a track could not be measured (listed as `not measured`), an album could not be read,
or the page could not be written; `2` with no
`ffmpeg`, a library that cannot be listed, a `--resume` list that cannot be read or holds a
line this pass does not write, or a list that cannot be made or opened (a `--resume` list
that is a link, a FIFO pipe or anything else but a regular file, or one over 64 MiB, is
refused at once as one that cannot be read); `130` on Ctrl-C.

## Exit codes

| Code | When |
| --- | --- |
| `0` | The sweep finished, every album it visited `done` (or `offline`) with `album` exiting `0`. A survey exits `0`. An accept that finished exits `0`, every album cleared, holding no run, or skipped as a link. A silence pass exits `0` when every track was measured and its page written. |
| `1` | The sweep finished, but an album did not end `done` (or `offline`) with exit `0`: track errors (a fix's `track errors` among them), refused providers, an album still `owed` answers or passed over waiting on a cool-down or breaker, an album locked by another run or one the sweep could not run, a timeout, no record, a record `album` could not write (its exit `3`), an exit code `album` never gives, or a child left running (`abandoned`). For accept: an album refused, or one whose removal, or whose records, failed. For silence: a track not measured, an album that could not be read, or a page that could not be written. |
| `2` | A usage problem, a library that is missing or cannot be listed, a ledger that cannot be read or written (a run still prints its report so far; a survey prints nothing), its lock not taken (another sweep running on the library, or the lock unavailable), a run or a fix on Windows, a `--heard-dir` that is not a folder or cannot be listed, a `--changed-list` file whose last write failed (said on stderr after the report), an existing one it cannot read as a list, or its private folder not made. For accept also a Python whose `shutil.rmtree` can follow a link. For silence: no `ffmpeg`, a `--resume` list that cannot be read, is malformed or was begun on another library, or a list that cannot be made or opened. |
| `3` | Stopped after `--stop-after` consecutive refused albums; the resume command is printed. For accept: a report that could not be written, which ends the pass at that album. |
| `130` | Interrupted with Ctrl-C; the album in hand, if any, is recorded (not done, unless its run was already read), and the resume command is printed. An accept prints no resume command and records nothing: run it again, and an album stopped after its rename has its `.lyricsmith.accepting` finished. A silence pass keeps every album it finished in its list, writes its page, and prints the resume command. |

A shared library or a sibling tool's module that cannot be located, or that a same-named
module would shadow, also exits `2`, before anything runs: see *Exit codes* in
`../README.md`.

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

- **The pace and the stop are untuned.** The defaults of `--pause-seconds` and
  `--stop-after` are conservative: they have run a whole library unchanged, and have not
  been tuned against the providers' limits.
- **The stop rule can miss a refused album** that holds a track no provider could be asked
  about (see "The stop rule").
- **A survey reads every file**, and so downloads a library kept online only, and a first
  run may save each track once even where its words stay the same (see "The survey").
  A silence pass decodes every file in full, and downloads it the same way.
- **Run one silence pass per list at a time.** Nothing locks a silence list, and a resume
  first cuts the list back to its last finished album, so a resume started while another
  pass still writes to that list can cut off the album the other pass is writing.
- **A silence list does not record its library.** Its paths are relative to the library,
  and a resume skips the albums the list names as done, so resume a list only with the same
  `--library` as the pass that began it; the printed resume command carries it. A resume
  is refused only when none of the albums the list marks done is in `--library`: one
  library holding some of another's albums by the same names is not told apart.
- **A file at the page's name is read only for the page's marker.** A file put there
  between that check and the replace is replaced.
- **A folder swapped for a link** in the milliseconds between the sweep's last check and
  `album` opening it is followed (see "Which albums are visited").
- **`--mode run` and `--mode accept` need a POSIX system**; on Windows a run is refused,
  and an accept cannot take the ledger's lock, each with exit `2`.
- **Run folders accumulate.** Each album visited gains one `album` run folder per sweep, a
  few kilobytes each, under its own `.lyricsmith` folder. `album.py --mode prune` removes,
  album by album, those nothing needs; the undo command a sweep printed for a pruned run
  then reports no such run, and that run was already undone. To drop every run of every
  album once their results are kept, use `--mode accept` (see "Accept").

## Samples

`samples/README.md` walks a survey, an offline run, each album's undo, and a silence
preview and its resume over a small invented library of two artists and three albums,
built from `album`'s own sample album, with the output each prints and how to compare
yours. `tests/test_sweep_samples.py`
runs the same steps and fails when the README's blocks stop matching.

## Tests

`test_sweep.py` folds the suites under `tests/` into one run and fails any test that
does not run isolated: HOME and every XDG base directory point into a temporary folder, the
Genius key is unset and the network is a tripwire, so no test reaches a real library, the real
ledger or a provider. Most suites play `album` with a fake that writes each run's
manifest through `album`'s own records and writer; one runs the real `album`
with `--offline` over its own invented sample album and undoes the run from the printed undo
command, and another runs the real `album --mode fix-text` over that album with an invented
transcript record that the fix skips, so it asks no provider. The ledger's line parser has property tests, since a person may edit the file,
and so does the silence list's resume reader. The silence pass's suites play `ffmpeg` through
`measure_level`'s process seam; one live test, skipped without `ffmpeg` on `PATH`, measures
a generated silent MP3 for real.
