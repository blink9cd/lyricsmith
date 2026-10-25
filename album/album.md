# album.py

Finds lyrics for every track of one album, embeds them in the MP3s, checks what it wrote, and
can put the album back exactly as it was. It can also strip an album's lyrics, and every
strip is undone the same way. Timed lyrics are preferred: a track given new lyrics gets an
ID3 `SYLT` frame and a `USLT` frame with the same words, or a `USLT` frame alone when only
untimed lyrics exist, plus a matching `.lrc` or `.txt` sidecar beside the MP3. A track
served by its own lyrics frames keeps them as they are and gains only the sidecar it
lacked, and a track that already holds exactly what a run would write is not written at
all: it is reported `unchanged`.

It runs once per album, after the album is tagged and its gain set (MP3Gain, then Picard):
it searches by each track's artist, title and album tags and its length.

## Prerequisites

- The project's dependencies installed: `mutagen`, `requests`, `urllib3` and
  `lyricsgenius`, each pinned under `[project].dependencies` in this project's
  `pyproject.toml`. LRCLIB, NetEase,
  MusicBrainz, Bandcamp, YouTube Music and Tekstowo are asked through `requests` directly;
  `lyricsgenius` asks Genius and reads its song page.
  `urllib3` is `requests`' own transport, pinned because the reply-size cap relies on its
  inflating a compressed reply no faster than it is read, which it does from 2.6.
- A Genius key is optional. Without one the Genius step is skipped with one notice on stderr,
  and the run goes on with the other providers.
- `ffmpeg` on `PATH` is optional, installed with `brew install ffmpeg`. With it a track
  no provider answered is measured for silence (*Silent tracks*); without it the run
  measures nothing and says so. The measurement decodes the local file and nothing leaves
  the machine.
- Network access for a normal run and for `--mode compare`. `--offline` needs none, and
  neither do `--mode check`, `--mode undo`, `--mode strip` and `--mode prune`.

## Usage

```text
album.py --album <dir> [--mode run|check|undo|strip|compare|prune] [--track <file>] [--run-id <id>]
                [--lang <code>] [--config <file>] [--dry-run] [--offline] [--no-genius]
                [--no-netease] [--no-bandcamp] [--no-youtube-music] [--no-tekstowo]
                [--fresh | [--refetch-untimed] [--refetch-implausible]]
                [--changed-list <file>]
```

| Option | Default | Description |
| --- | --- | --- |
| `--album` | required | The album folder. Every MP3 under it, in any subfolder, is a track. |
| `--mode` | `run` | `run` backs up, fetches, embeds and checks. `check` re-checks a finished run. `undo` puts a run's backup back. `strip` backs up, then removes every track's lyrics frames and sidecars. `compare` asks each provider for every untimed-only track and reports whether a provider other than its lyrics' source agrees; it writes nothing. `prune` removes the run folders nothing needs. |
| `--track` | every track | `run`, `strip`, `check`, `undo` and `compare`: one MP3 under `--album`, relative to the working directory. |
| `--run-id` | the newest run | `check` and `undo` only: which run to read. |
| `--changed-list` | no list | `run`, `strip` and `undo`: write the absolute path of every MP3 this saved to this file, one per line, for a library app or script that rereads those files' tags. See [The changed list](#the-changed-list). |
| `--lang` | `eng` | `run` and `compare` only: the language whose frames rank first when a track's own frames serve it, and rank first among the frames `compare` may judge; `run` also writes this ISO 639-2 code into new lyrics frames. |
| `--config` | the environment, then the default config paths | `run` and `compare` only: a config file holding the Genius key, the `[no_lyrics]` genres and the `[musicbrainz]` contact; when given, the only place any is read. |
| `--dry-run` | off | `run` and `prune` only: report what each track would use, or which run folders would go, and change no file. |
| `--offline` | off | `run` only: no network; only what the tracks already hold, their sidecars and lyrics frames, is used, and the local silence measurement (*Silent tracks*) still runs. |
| `--no-genius` | off | `run` and `compare` only: skip the Genius step. Genius's terms bar scraping, and its lyrics are read from the song page. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-netease` | off | `run` and `compare` only: skip both NetEase steps, timed and untimed. NetEase publishes no terms for the routes asked. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-bandcamp` | off | `run` and `compare` only: skip the Bandcamp step. Bandcamp's terms allow personal, non-commercial use. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-youtube-music` | off | `run` and `compare` only: skip the YouTube Music steps. YouTube's terms bar automated access. See *Lyrics sources and their terms* in `../README.md`. |
| `--no-tekstowo` | off | `run` and `compare` only: skip the Tekstowo step. Tekstowo's terms allow home use only. See *Lyrics sources and their terms* in `../README.md`. |
| `--fresh` | off | `run` only: ask the providers for every track, even one a sidecar or its own frames would serve; an accepted answer replaces the track's old frames and sidecars, and a track with no accepted answer keeps them. An answer unlike the old lyrics frames is not accepted, nor an untimed one for a track whose lyrics are synced. Refused with `--offline`, `--refetch-untimed` and `--refetch-implausible`. |
| `--refetch-untimed` | off | `run` only: ask the providers again for each track whose lyrics are untimed only, as though it held none; any accepted answer replaces its old frames and sidecars, however unlike them, and a track with no answer keeps them exactly as they were, except that its sidecar and its frames, each judged alone, are removed when implausible (more words a second than any song is sung at, a `.txt` too large to read, or placeholder lyrics). Synced tracks and tracks with no lyrics run as without it; a track holding only placeholder lyrics is asked again in any run (*Placeholder lyrics*). Refused with `--fresh` and `--offline`. |
| `--refetch-implausible` | off | `run` only: ask the providers again for each track whose serving lyrics are implausible, synced lines starting past the audio's end or more words a second than any song is sung at, as though it held none; a plausible answer replaces them, and a track with none has the implausible side, its sidecars or its frames, removed, backed up for undo. Plausible tracks run as without it. Combines with `--refetch-untimed`, asking the union of the two; refused with `--fresh` and `--offline`. |

An option a mode has no use for is refused with exit `2` rather than silently ignored: `run`
refuses `--run-id`; `check` and `undo` refuse every option the table marks `run` only,
`run` and `compare` only, or `run` and `prune` only; and `strip`, a new run of every track
or the one `--track` names that asks no provider, refuses `--run-id` and every option the
table marks `run` only, `run` and `compare` only, or `run` and `prune` only, `--dry-run`,
`--lang`, `--fresh`, `--refetch-untimed` and `--refetch-implausible` among them. `compare` reads the album as it is now, asks the providers and writes nothing, so it
refuses `--run-id`, `--dry-run`, `--offline`, `--fresh`, `--refetch-untimed`,
`--refetch-implausible` and `--changed-list`, and takes `--lang`, `--config`, `--no-genius`, `--no-netease`, `--no-bandcamp`,
`--no-youtube-music`, `--no-tekstowo` and `--track`. `prune` judges every run folder by
its rules, asks no provider and saves no MP3, so it takes `--album` and `--dry-run`
alone and refuses `--run-id`, `--track`, `--changed-list`, `--lang`, `--config`,
`--offline`, `--no-genius`, `--no-netease`, `--no-bandcamp`, `--no-youtube-music`,
`--no-tekstowo`, `--fresh`, `--refetch-untimed` and `--refetch-implausible`. `--fresh`,
`--refetch-untimed` and `--refetch-implausible` are each refused together with
`--offline`, since an offline run asks no provider (and would remove implausible lyrics
unasked), and `--fresh` with either refetch, since it declines the very answers a
refetch is there to accept. The two refetches combine: a run with both asks again every
track either would.
`--changed-list` is refused by `check` and with `--dry-run`: neither changes anything, so
its list would always be empty.

## The modes

**`--mode run`** takes the album lock, backs up every track under
`<album>/.lyricsmith/<run-id>/`, and only then changes anything beside an MP3. Each track
then goes through its sidecars and lyrics frames, the provider chain, the embed and the
check, inside its own error boundary: a failure is recorded against that track, at the
stage it happened in, and the run moves on. The manifest in the run folder is rewritten as
each track finishes, so a run that stops part-way still records what it changed. No file a
run would leave as it was is written (*What a write changes*), so a second run over an
album the first one finished changes no track it serves from what the first run left,
and each such track reports `unchanged`. An online run still asks the timed steps for a
track holding untimed lyrics, and asks again for one the first run did not find, so
either can still be written. The chain holds both NetEase steps, the Bandcamp step,
both YouTube Music steps and the Tekstowo step by default: `--no-netease` leaves out
both NetEase steps, `--no-bandcamp` the Bandcamp one,
`--no-youtube-music` the next two and `--no-tekstowo` the last, and YouTube Music and
Tekstowo are each left out as well while its own cool-down holds (*Bandcamp through
MusicBrainz*, *YouTube Music* and *Tekstowo.pl*, below).

**`--track` runs one track** of the album instead of every one, and every run option
works with it: `--dry-run`, `--offline`, `--fresh`, `--refetch-untimed` and
`--refetch-implausible`. Nothing else
changes: the lock, the run id, the backup, the per-track error boundary, the manifest, the
summary, the undo line and `--changed-list` are a whole run's, covering that track alone.
Its manifest lists the one track, so `--mode check` and `--mode undo` read it as they read
any run.

**`--mode run --fresh`** starts each track from its providers instead of what it already
holds. Its lyrics frames are not used for the lookup, and its sidecars are neither used nor
brought to one per track, so the providers are always asked, even for a track a timed
`.lrc` or synced frames would have served, and nothing beside the MP3 changes before an
answer is accepted. With an accepted answer, the track's old lyrics frames, in any
language, and both its sidecars are removed, then the answer is embedded and its sidecar
written, all as one write that Ctrl-C waits for, so a track is never left stripped with
nothing new in place by an interrupt. An accepted answer the track's frames already hold,
exactly as the embed would write them, strips nothing: the frames are not saved, a
sidecar already holding the answer's bytes is not rewritten, only a sidecar of the other
kind is removed, and a track for which nothing changed reports `unchanged`. A folder, a
pipe or a device under a sidecar's name is still that track's `ERROR strip`, as the strip
would refuse it. An answer the embed cannot
encode (a time stamp past the SYLT range, a character no Unicode encoding writes, a NUL),
a tag version it cannot write, and an MP3 it cannot open for writing (a read-only file)
are that track's `ERROR embed` before the strip, so the track keeps its old frames and
sidecars. Those are the only failures checked for. Any other failure after the strip
leaves the track stripped: a full disk during the embed (`ERROR embed`), or a sidecar the
strip cannot remove, such as one in a folder that cannot be written or one marked
immutable (`ERROR strip`, after the frames are gone). `--mode undo` puts the old frames
and sidecars back in every such case. On a network file system that refuses opening a file
for update, the check can refuse an MP3 the embed could have written; the track keeps its
lyrics, as `ERROR embed`. **A fresh run keeps the old lyrics when nothing is found:** a
track with no accepted answer keeps its frames and sidecars exactly as they were and
reports `kept existing` when it had lyrics frames, in any language, or a sidecar the
backup recorded, a link included; a track with neither reports `not found` (`no lyrics
expected` for a marked track, *Stand-up and spoken word*; `silent` when `ffmpeg` measured
it silent, *Silent tracks*). The one exception is a track whose only lyrics are
placeholders, which are removed (*Placeholder lyrics*), and a placeholder backup never
declines an answer. **A fresh run also declines
an answer unlike the old lyrics:** before anything of the track changes, the check judges
the answer against the lyrics frames the backup holds, and an answer its `unlike backup`
row would flag (*The check and its thresholds*) is not accepted. The track keeps its
frames and sidecars exactly as they were and reports `kept existing: new answer unlike
backup (similarity <s> < <threshold>)`, the threshold being the one *The check and its
thresholds* holds, the same rule as nothing found, and its source and kind columns name
the answer it declined. A track whose only lyrics frames are in another language is judged
against those, as the check always reads them, so an answer unlike them is declined too
and the old lyrics stay. A track whose backup holds no lyrics frames, a sidecar alone
included, has nothing to be unlike, so its answer is accepted; the check of the written
track then compares it with that backed-up sidecar (*The check and its thresholds*), so an
answer unlike it is written and flagged. An answer the embed cannot encode is still that
track's `ERROR embed`, decided first. A run without `--fresh` writes such an answer and
flags it; a track whose old lyrics were wrong gets the new answer through a strip followed
by a run. **A fresh run never trades synced lyrics for untimed ones:** an untimed answer
for a track whose lyrics were already synced is declined the same way, reported as `kept
existing: new answer untimed (existing lyrics synced)`. The track's lyrics were synced
when its backup holds a SYLT frame with a worded entry, in any language and whatever its
time units, that is no placeholder (*Placeholder lyrics*; one of time stamps alone holds
no lyrics to keep, as a stamps-only `.lrc` does not), or when a sidecar the backup
recorded is one the sidecar stage would serve as timed lyrics (*How a track's lyrics are
found*) and no placeholder: a `.lrc` with a time stamp and a worded line, read only for
this, and never through a link leading out of the album. A sidecar that must
be read for it and cannot be (not UTF-8, or past the size cap) is that track's `ERROR
check`, with nothing changed. An answer that is both unlike and untimed over synced lyrics
names both reasons. A synced answer, and an untimed one for a track with no synced lyrics,
are judged as above; a run without `--fresh` never asks for an untimed answer over synced
frames, since the frames serve the track (*How a track's lyrics are found*). When every
provider fails, the track is `ERROR fetch` and is left untouched. The backup and manifest
are the run's usual ones, so `--mode undo` puts back the old frames and sidecars. With
`--dry-run`, every track that can be read plans `fetch`, and its sidecars are read only to
tell a track holding nothing but placeholder lyrics, which plans `fetch, else remove
(implausible: placeholder lyrics)` (*Placeholder lyrics*): a
marked track holding no lyrics frames and no sidecar plans `fetch (no lyrics expected if
none)` instead (*Stand-up and spoken word*). Only a folder, a pipe or a device under a
sidecar's name, which the strip would fail on, shows as that track's `ERROR strip`, and a
tag or an MP3 the embed cannot write as its `ERROR embed`. A track with both shows its
`ERROR embed`: the run refuses an embed it cannot save before it strips, so that is the
error it would stop on.

**`--mode run --refetch-untimed`** asks the providers again for each track whose lyrics
are untimed only, the way out for a track holding wrong untimed lyrics, which a run serves
from forever and `--fresh` declines to replace. A track is untimed only when it holds no
`SYLT` frame with a worded entry other than a placeholder (*Placeholder lyrics*), in any
language and whatever its time units, and no sidecar the sidecar stage would serve as
timed lyrics, but holds untimed words: a `USLT` frame, a `.txt`, or a `.lrc` with no time
stamp; or a `.txt` the run cannot read (not UTF-8, or past the size cap). Such a track is
looked up as though it held nothing: its sidecars and frames are left out of the lookup
and its sidecars are not brought to one per track, so the timed steps are asked first,
then the untimed ones and Genius, with the stripped-title retry, and nothing beside the
MP3 changes before an answer comes. **The guards are the reverse of `--fresh`'s:**
`--fresh` declines an answer unlike the old lyrics, or untimed over synced ones, and keeps
what the track had; a refetch accepts any answer, however unlike the old lyrics, since it
exists to replace lyrics that are wrong (only an answer the track's duration rules out is
declined, as in every run: *How a track's lyrics are found*), and it never touches a synced track, so no answer
can be untimed over synced lyrics. An answer is written as under `--fresh`: the old frames
and both sidecars are stripped and the answer embedded with its sidecar, all as one write,
and a synced answer upgrades the track as any run's does. The check's `unlike backup` row
is how a replaced track shows it differs from its old lyrics, `FLAG: unlike backup
(similarity <s> < <threshold>)`: a sign that one of the two was wrong, worth a look. A
track whose old lyrics were in a sidecar alone is compared with the backed-up sidecar
instead, as in any run (*The check and its thresholds*); a backed-up copy that cannot be
read, not UTF-8 or past the size cap, makes that row a `SKIP` naming why. A `.txt` past
the backup's byte cap (*How a track's lyrics are found*) is never asked again: the backup
refuses it unread, so it is that track's `ERROR backup` and the track is not written, and
a dry run shows its `ERROR sidecar`. **A track with no answer keeps its lyrics when they
are plausible:** its frames and sidecars stay exactly as they were, byte for byte, an
untimed `.lrc` unrenamed, and it reports `kept existing: no answer to the refetch`. A
`.txt` it could not read because it is not UTF-8 is named in that same row (`kept
existing: no answer to the refetch; <name> is not UTF-8: ...`) rather than as an `ERROR
sidecar`, since the file is left alone; with an answer the strip removes it, and the
backup holds it. **Implausible old lyrics are removed instead.** The sidecars and the
frames are judged apart, each by the lyrics on that side that would serve the track: the
sidecar's (the `.txt`, or an untimed `.lrc`), and the frames' best-ranked worded untimed
frame, the one in `--lang` first (*How a track's lyrics are found*), passing over a
placeholder frame when a real one is beside it, so only frames that are all placeholders
are judged as one. Those lyrics are implausible when they are a `.txt` too large to read
(past the size cap), when they are placeholder lyrics (*Placeholder lyrics*), or when they
hold more words a second over the track's duration than the check's `too many words` row
passes (*The check and its thresholds*), counted as that row counts them: a saved web
page, say. A side judged implausible goes whole, by the same strip a replacing run makes
before its embed: implausible sidecar lyrics take both sidecar names, the `.txt` and the
`.lrc`, a stamps-only one included; an implausible serving frame takes every lyrics frame,
after the same check that the MP3 can be saved, a `USLT` in another language and a
wordless `SYLT` included, however plausible they are. The other side, when its own serving
lyrics are plausible, stays as it was. The track reports `removed: implausible old lyrics
(<why>)`, the why being the row's own measure (`66.692 words per second: 2000 words in
29.989 s`), the size refusal (`<name> is too large: over 65536 characters`) or
`placeholder lyrics`, each joined by `;` when both sides went for different reasons. A
`.txt` that is not UTF-8, left beside removed frames, is named after it as a kept track
names it (`removed: implausible old lyrics (<why>); <name> is not UTF-8: ...`). When
neither side is implausible, nothing is removed and the track keeps its lyrics as above.
The backup holds everything removed, and `--mode undo` restores it byte for byte.
The check does not judge a removed track, as it does not judge a kept one, so its `no
lyrics embedded` row never flags the removal as a fault: `--mode check` shows it `-`, not
checked, and the run's `flagged` count leaves it out.
A `.txt` that is not UTF-8 is no sign either way, and neither is a duration that cannot
be read, so both keep. An MP3 the strip cannot save is that track's `ERROR strip`, with
nothing removed. Removal never comes before an answer: any answer the fetch accepts
replaces the old lyrics as above. A `.lrc` it cannot read might be timed, so that track runs as without
the option, and is its `ERROR sidecar`. When every provider fails the track is `ERROR
fetch`, untouched, implausible or not. If some providers fail and the rest answer
nothing, implausible lyrics are still removed, and `--mode undo` brings them back. A
synced track, and a track with no lyrics at all, runs exactly as in a run without the
option. The backup and manifest are the run's usual ones, each track asked again marked
`refetch_untimed` (*`manifest.json`*), so `--mode check` judges the written tracks and
`--mode undo` puts back the old frames and sidecars, an unreadable `.txt` byte for byte.
With `--dry-run`, each untimed-only track plans `fetch, else keep existing`, with an
unreadable `.txt` named after it, or `fetch, else remove (implausible: <why>)` when its
old lyrics are implausible, unless the backup would refuse its `.txt`, and previews the
strip and embed errors a fresh dry run does; every other track plans as without the
option.

**`--mode run --refetch-implausible`** asks the providers again for each track whose
serving lyrics are implausible, the way out for a track a run took another song's lyrics
for before answers were judged against the track's length: a full song's timed lines on a
short interlude, say. The serving lyrics are those the fetch would serve the track from
(*How a track's lyrics are found*): a timed `.lrc`, unless labeled as machine-made, then
the best-ranked worded synced frame, the one in `--lang` first, then a `.txt` (or a
labeled or untimed `.lrc`), then the best-ranked worded untimed frame, a placeholder never
serving (*Placeholder lyrics*). They are implausible by the check's `past end` row, for
synced lyrics only (the latest line, not the last stored, starting past the track's end
plus `PAST_END_TOLERANCE_S`), or its `too many words` row, over the track's readable
duration (*The check and its thresholds*); `ends short` is no reason, since a long outro
sings nothing. A duration that cannot be read judges nothing implausible, and a sidecar
that cannot be read is no evidence, so either track runs as without the option. Such a
track is asked as `--refetch-untimed` asks an untimed-only one: as though it held
nothing, the timed steps first, then the untimed ones and Genius. Every run declines an
answer by the same two rows, so the same wrong lyrics cannot come back. A plausible answer
replaces the old lyrics, written as under `--refetch-untimed`, and the check's `unlike
backup` row shows the two differ. **With no answer, the implausible side is removed**, as
`--refetch-untimed` removes implausible untimed lyrics: the sidecar side and the frame side
are judged apart, each by its own serving lyrics, so a late `.lrc` beside plausible synced
frames takes both sidecar names and leaves the frames, and late synced frames beside a
plausible `.txt` take every lyrics frame and leave the `.txt`. A side holding placeholder
lyrics goes with an implausible one. The track reports `removed: implausible old lyrics
(<why>)`, the why being the row's own measure (`last line 60.000 s, track 29.989 s`, or
the word rate), and `--mode undo` restores what went. When every provider fails the track
is `ERROR fetch`, untouched. A track whose serving lyrics are plausible runs exactly as in
a run without the option, never asked again. With `--refetch-untimed` as well, a run asks
again every track either option picks, an untimed-only track as that option asks it. Each
track asked again is marked `refetch_implausible` in the manifest (*`manifest.json`*), so
`--mode check` and `--mode undo` read the run as any. With `--dry-run`, each such track
plans `fetch, else remove (implausible: <why>)`, previewing the strip and embed errors a
fresh dry run does; every other track plans as without the option.

**Placeholder lyrics.** Some providers answer a track with a stand-in rather than nothing,
and a track may already hold one, written by an earlier run or another tool. Lyrics are
a placeholder when their text, whitespace aside, is NetEase's instrumental notice or
nothing but music-note glyphs; when every non-blank line is an LRC header tag, a
bracketed `name:value` whose name opens with a letter (so never a time stamp) with
nothing outside the brackets, such as LRCLIB's `[au:comedy]` or `[au:instrumental]`; or
when they are timed lines with no words, stamps alone. Blank text is not one: it is
simply nothing. A placeholder answer is nothing found (*How a track's lyrics are found*).
**A placeholder a track stores reads as no lyrics held**, in a `.txt`, a `.lrc`, a `USLT`
or a `SYLT` frame: it never serves the track. Beside worded lyrics on the other side, those
serve as usual, and the run's own writes replace the placeholder (a sidecar written from
the frames, or frames embedded from the sidecar). A track whose only lyrics are
placeholders, in its sidecars, its worded frames in any language, or both, is asked in
every run as though it held none, as `--refetch-untimed` asks an untimed-only track: the
timed steps, then the untimed ones and Genius, and an answer replaces the placeholder by
the same strip and embed, backed up as any change is. Under `--fresh` an answer is never
declined as untimed over a placeholder `.lrc` or a placeholder synced frame, which are no
synced lyrics. A placeholder is never the backup an answer is compared with, in a fresh
run's decline, the check after a run or a later `--mode check`: placeholder frames are
set aside, so a real frame beside one is the backup, and a placeholder alone, or a
placeholder sidecar the backup copied, leaves nothing to compare.
**When nothing answers, the placeholder is removed**, as implausible old lyrics are: the
sidecars holding it, both names, and the frames holding it, every lyrics frame, each side
only when it holds one. The track reports `removed: implausible old lyrics (placeholder
lyrics)` and counts in `removed`, and `--mode undo` restores what went byte for byte. When
every provider fails the track is `ERROR fetch` and keeps its placeholder. A blank frame or
sidecar, a stamps-only `.lrc` or a wordless `SYLT` among them, already holds no lyrics, as
*How a track's lyrics are found* says, so it is neither asked about nor removed for this. A
dry run plans such a track `fetch, else remove (implausible: placeholder lyrics)`, offline
too, since offline nothing answers. A `.txt` or `.lrc` that cannot be read is no evidence
either way, so that track runs as in any run. `--mode check` flags a written track whose
lyrics frames hold a placeholder, `placeholder lyrics` (*The check and its thresholds*), so
a track an earlier run wrote one into is found, and a run of the album replaces it.

**`--mode check`** re-runs the check on the tracks a run wrote or left `unchanged`, the
newest run unless `--run-id` names another. It is read only: it writes nothing and takes no lock, so it can run
beside another run of the same album. A track whose check cannot run (the file deleted or
unreadable since the run, or, for a track served from its frames, its sidecar unreadable)
is that track's `ERROR` row, and the exit code is `1`.

**`--mode undo`** takes the album lock and puts a run's backed-up lyrics frames and sidecars
back, every track of the run or the one `--track` names. Undoing a run that is not the newest
is allowed. Once it is done, each newer run's manifest is read, and a newer run that holds a
track this undo restored, or began restoring, is warned of on stderr, one line per such
run, naming the tracks they share:

```text
warning: run <newer> changed <path>, <path> after run <id>; this undo overwrote those changes
```

A newer run that shares no restored track gives no warning, so undoing a whole-album run
after a one-track run of another of its tracks is silent; nor does a shared track that the
newer run's own undo already put back, since that run's change to it is gone. A newer run
that cannot be listed, or a newer manifest that cannot be read, gives the general warning
instead, `warning: run <id> is not the newest; a newer run's
changes to the same tracks were overwritten`, so the warning never fails open. The manifests
are read after the lock is released, so a run started meanwhile can add a false alarm but
never hide a warning; an undo of a newer run made meanwhile can drop that run's line, since
its change to the track is then gone.

Undo never deletes or overwrites a sidecar it cannot prove is the run's. A sidecar it must
remove or replace is first copied into the run folder's own `undo` folder, mirroring the
album, unless its bytes still match the hash the run recorded when it wrote that file; so an
ordinary run is undone with nothing copied, while a sidecar edited since the run, or one left
by a run killed before it recorded what it wrote, is kept there. A sidecar that already
matches its backup is left alone. Each copy is named in the track's row of the undo report,
`displaced: <path>`, an errored row's too, and recorded in the run's manifest. A sidecar the
backup found as a symbolic link is put back as that link, to the same target; a link
already pointing there is left alone, and a file now in its place is set aside under the
same rule first. Any other symbolic link under a sidecar's name is removed or replaced
itself, never the file it points at, and is not copied; a folder, a pipe or a device there
is that track's error, refused before any frame is restored, and is left as it is.
A track undo cannot restore is that track's `ERROR undo: <message>` row, the shape a run's
and a check's errors take, and the exit code is `1`.

**`--mode strip`** removes every track's lyrics, or with `--track` one track's: its `SYLT`
and `USLT` frames, in any language, and its `.lrc` and `.txt` sidecars. It runs as a run
does, with its own run id: it takes the album lock, backs up every track, or the one
`--track` names, under `<album>/.lyricsmith/<run-id>/` before anything changes, then
strips each track inside its own error boundary and rewrites the manifest as each one
finishes, so `--mode undo` puts back what a strip removed. A track with nothing to remove is
not written at all. The summary has one row per track naming what was removed, `frames` and
each sidecar's suffix joined by commas (`frames, .lrc`), or `nothing to remove`, or its
`ERROR strip: <message>`. A counts line follows, `stripped <n> · errors <m>`, where a track
with nothing to remove counts as stripped, then the backup and undo line a run prints. A
strip stopped part-way shows every track it never reached as `not reached`. The ID3 version
and the ID3v1 tail stay, as on every write. With `--track`, the strip backs up, strips and
records that track alone: its report has one row and its counts cover that track, and
`--mode undo` puts back that track. It is how one track's wrong lyrics are cleared without
the whole album's (*Comparing with the providers*).

Strip treats a sidecar's name as undo does. A symbolic link there is removed itself,
never the file it points at. The backup records a link by its target and copies nothing,
so an undo puts the link itself back, whether it pointed at a file, at a folder or at
nothing. A folder, a pipe or a device under a sidecar's name fails
that track, `ERROR strip`, before its frames or its other sidecar change, and is left as
it is. `--mode check` on a strip gives each track no verdict: a strip wrote no lyrics to
check.

**`--mode compare`** asks whether a track's untimed lyrics are its own song's, which the
check cannot tell: its `unlike backup` row compares with what the track held before a run,
so lyrics wrong from the start are their own baseline. It reads the album as it is now,
every track or the one `--track` names, and judges each untimed-only track, by the test
`--refetch-untimed` uses: no worded `SYLT` other than a placeholder and no timed `.lrc`,
but untimed words in a `USLT` frame, a `.txt` or a `.lrc` with no time stamp. The text
judged is the one a run would serve the track from: its sidecar's when that holds words,
else its best-ranked worded untimed frame, the one in `--lang` first, passing over a
placeholder frame when a real one is beside it, as a refetch judges it. Each judged track
gets a run's query, from its tags or, when a tag is missing, its file name, and LRCLIB
(its untimed step: plain text, else its timed text without the stamps), NetEase, Bandcamp
(without `--no-bandcamp`), YouTube Music (its untimed step, without `--no-youtube-music`
and not while its cool-down holds), Genius (only with a key and without `--no-genius`) and
Tekstowo (without `--no-tekstowo` and not while its cool-down holds) are each asked on its
own, in that order, not as a chain that stops at the first answer.
Every answer passes a run's matcher and network rules unchanged, and a provider that found
nothing is asked once more with the title's trailing bracketed groups stripped, as a run
retries. A query by file name has no artist, so only Genius is asked for it, as in a run.
A refusal from YouTube Music or Tekstowo during a compare still writes that source's
cool-down: the file describes the site's state, not the album's. The provider a track's
lyrics came from is not asked: it would only repeat them, so its answer is set aside, and
the row reads `own` for it (*Comparing with the providers*). Each answer is scored against
the judged text, and the row says whether a provider other than the lyrics' source agrees,
with weak hints beside a track that does not (*Comparing with the providers*). A synced
track, and one holding no lyrics, is a `-` row counted as `not judged`. A sidecar that
cannot be read (not UTF-8, or past the lyrics cap), `.lrc` or `.txt`, is that track's
`ERROR sidecar`, as in a run. Compare writes nothing into the album, and nothing at all
but that cool-down: it takes no album lock, makes no run folder and needs no earlier run,
so it can run beside a run of the same album; a track that run is writing may then read as
an error or as its earlier state, because tags are saved in place.

**`--mode prune`** removes the run folders nothing needs, for the one `--album`. It takes the
album lock for the whole pass, as undo does, and refuses a linked `.lyricsmith` or `lock`
as run, strip, undo and check do. It asks no provider, and it touches nothing but the
run-id folders under `.lyricsmith`: no MP3 and no sidecar. The `lock` file and the run
folder itself are never removed, since deleting the lock would race another run opening it. Only
entries named as run ids are looked at, and a folder an earlier prune left half-removed (a run
id with `.pruning` after it); any other entry is left alone and not listed. An album with
no run folder prints that it has no run folders and exits `0`, and nothing is created;
one whose run folder holds no run-id entry prints the same.

Each listed entry gets one outcome, tried in this order:

| The entry | Outcome |
| --- | --- |
| A link, or not a directory | `kept: not a run folder` |
| A folder named for an earlier prune | `removed: left by an earlier prune` |
| A folder with no `manifest.json`: a backup that never finished | `removed: unfinished backup` |
| A manifest that cannot be read (malformed, over the 16 MiB cap, linked, or the folder cannot be searched) | `kept: unreadable manifest`, with the reason; never removed unread, and never taken for an unfinished backup |
| A manifest whose state is not `undone` (`backed_up` or `complete`) | `kept: not undone` |
| State `undone`, but its `undo` folder holds an entry or cannot be listed, or a track lists a displaced sidecar | `kept: holds displaced sidecars` |
| State `undone`, nothing set aside | `removed: undone` |

A run that is not fully undone holds the only copy of the lyrics it replaced, and an undone
one can hold the only copy of a sidecar edited after the run, which undo set aside in its
`undo` folder; both are therefore kept.

**The newest run is held to one more rule.** A plain `--mode undo` or `--mode check` acts on
the newest run, and a plain undo repeated on an undone newest run does nothing. Removing that
run would point the same command at an older run and really undo it. So when the newest run
would be removed but any run left behind is not `undone`, it is kept instead, as
`kept: newest, another run is not undone`; every older removable run still goes. A kept run with
displaced sidecars counts as left behind, but it is `undone`, so it does not hold the newest
back. An unreadable one counts as not undone, and an unreadable run newer than the newest
readable one holds that run back too, since a plain undo can pass over an unreadable run (a
linked manifest) for the run below it. With no runs left at all, a plain undo reports that
there is no run, as on an album never run.

**Rename, then delete.** Each folder is first renamed, inside the run folder, to
its run id with `.pruning` after it, a name no mode takes for a run, and only then deleted. A
prune stopped part-way therefore never leaves a half-deleted folder that still reads as a run
whose backup is missing, and a later prune removes what it finds so named. Deletion is
`shutil.rmtree`, which removes a link inside the folder and never what it points to only where
`shutil.rmtree.avoids_symlink_attacks` is true; where it is not, the mode refuses to start. A
rename or delete that fails is that folder's error, its folder kept or left for the next prune
to finish, and the pass goes on.

With `--dry-run` the same table is printed with `would remove`, in its rows and its counts
line, and no run folder is changed.
The dry run takes the album lock too, so an album never run under the lock may gain the
`.lyricsmith` folder and its empty `lock` file, as an undo creates them; no other file
changes.

The report has one row per listed entry, oldest first, then a counts line. The names below are
invented:

```text
album prune: 4 run folders under <album>

 #  run                 state      outcome
 1  20261019T161809Z    complete   kept: not undone
 2  20261019T163633Z    undone     removed: undone
 3  20261022T031008Z    undone     kept: holds displaced sidecars
 4  20261022T032325Z    undone     removed: undone
removed 2 · kept 2 · errors 0
```

A row whose rename or removal failed shows `ERROR: <reason>` in its outcome column, counted
under `errors`. A dry run ends with `dry run: nothing was removed` after the counts line.
Exit `0` means the pass finished, whatever it kept; `1`, that at least one removal failed;
`2`, that it could not start (an unusable album, a refused link, the album lock held, or a
`shutil.rmtree` without protection against links); `3`, that the report could not be
written.

## How a track's lyrics are found

**Sidecars come first.** Before any provider is asked, the sidecars beside the MP3 are
brought to one per track, named after it: `.lrc` for timed lyrics, `.txt` for untimed.
The one exception is `--fresh`, which skips this step and serves no track from a sidecar,
so every track is asked of the providers; it reads a sidecar only to tell whether an
untimed answer would replace synced lyrics (*The modes* above). `--refetch-untimed` skips
it too, for an untimed-only track alone, which it reads the sidecars only to recognize.

- A `.lrc` with a time stamp and a worded line is used as timed lyrics, and a `.txt`
  beside it is removed.
- A `.lrc` whose every line is stamps alone gives way to a `.txt` holding words, which is
  used as untimed lyrics; with no such `.txt` it is kept and holds no lyrics.
- A `.lrc` with no time stamp was never timed: it is renamed over any `.txt`, and used as
  untimed lyrics. A stamp's seconds run 0-59, so a line opened by `[01:75.00]` is not
  timed.
- A `.txt` alone is used as untimed lyrics.
- A symbolic link under a sidecar name is read through only when it leads, every link
  followed, to a file inside the album, so a shared file elsewhere in the album still
  serves the track. One leading out of the album (an absolute target, or one climbing
  with `..`), nowhere, round a loop or to a folder holds no lyrics, and the track is
  fetched; the dry run and the summary note an outside link it ignored, `sidecar link
  outside the album ignored: <name>`. Paths are compared as written, so on a
  case-insensitive volume an absolute target that spells the album's path in another
  letter case reads as outside: it is ignored, never read. A rename, a removal or a
  write acts on the link itself, never on the file it points at. A folder, a pipe or a
  device under a sidecar name is not a sidecar here: it is never read, moved or removed.
- Only the lowercase suffixes `.lrc` and `.txt` count. On a case-insensitive volume, the
  default on macOS, they also find `.LRC` and `.TXT`; on a case-sensitive one an uppercase
  sidecar is not seen.
- A sidecar that does not decode as UTF-8, or holds more than the lyrics cap (65,536
  characters, the cap a provider's answer has too), is that track's `ERROR sidecar`,
  with nothing beside it changed. An oversized file is refused by its size, never read
  whole. The backup copies each sidecar before this step and refuses an oversized one
  the same way, so in a run, a fresh run, a run with `--refetch-untimed` or a strip it
  is that track's `ERROR backup` and the track is not written; a dry run without
  `--fresh` shows the `ERROR sidecar`.

**Then the track's own lyrics frames, which count like a sidecar of the same kind.** A
`SYLT` frame timed in milliseconds with at least one worded entry is synced lyrics; a
worded `USLT`, else a worded `SYLT` timed in MPEG frames (which cannot become a `.lrc`
without the audio's frame rate), is untimed lyrics. Frames holding no word count for
nothing, as frames or sidecars holding placeholder lyrics do (*Placeholder lyrics*).
Among several worded frames of one kind the one in `--lang` with an empty description
wins, unless a sidecar holds another one's lyrics (below). The check of a
track served from its frames judges the frame that served it (*The check and its
thresholds*). A frame over the lyrics cap, a `USLT`'s text or a `SYLT`'s entries
counting a line break each, is reported as a sidecar over it is, as that track's
`ERROR sidecar` (`a USLT frame is too large: over 65536 characters`), in every run,
fresh or not: no lyrics are taken from it and no sidecar is written from it. It is still
backed up as it is, and `--mode strip` removes it.

**The order, in a run without `--fresh`, for every track but one holding only
placeholder lyrics (*Placeholder lyrics*), an untimed-only one under
`--refetch-untimed`, and one serving implausible lyrics under `--refetch-implausible`:**

1. A timed `.lrc` sidecar: it wins outright, and no provider is asked, unless it holds
   the synced frames' own lyrics (below) or is labeled as machine-made (below).
2. Else synced frames: they serve the track with no provider asked, source `frames`.
3. Else the timed steps of the provider chain below.
4. Else a `.txt` sidecar.
5. Else untimed frames, source `frames`.
6. Else the untimed steps and Genius.

So beside a `.txt` sidecar or untimed frames the timed steps still run, since timed lines
win, and with no timed answer the track keeps what it had; an untimed answer is used only
for a track that held no lyrics at all, and a run never trades the lyrics a track holds for
weaker ones. Under `--refetch-untimed` an untimed-only track skips steps 4 and 5: it is
asked the timed steps, then the untimed steps and Genius, as a track holding nothing is. A
track served from its frames is left as it is, apart from the sidecar it lacked, written
from them: a `.lrc` from synced frames, replacing a `.txt`, and a `.txt` from untimed
frames. Its MP3 is not written at all, so its frames keep their language, their time unit
(a `SYLT` timed in MPEG frames stays one) and any other lyrics frames beside them, an
ID3v2.2 tag is no error, and its `frames_written` is false. The `.lrc` holds each entry's
text stripped of spaces and line breaks at either end, since many taggers open each entry
with a line break. A `.lrc` is not written when one of the frame's lines would not come
back from it whole (an entry holding a line break inside its text, or opening with what
reads as a time stamp): the frames then stay the only copy, and the next run serves the
track from them again. Its row reads `written`, source `frames` with its kind, when its
sidecar was written, and `unchanged` when nothing was: no sidecar could be written, or the
one there already held its bytes. Either way the check still reads its frames back and
judges them. `--offline` follows the same order with no provider in it, so a track with
lyrics frames is served from them.

**Machine-made `.lrc` files.** A timed `.lrc` whose header, the lines before its first
timed line, holds one of two exact lines (whitespace at either end ignored) was made by a
machine: `[by:machine transcript]`, a transcript `lyricsmith transcribe` made, source
`machine transcript`; or `[by:machine timed]`, untimed lyrics `lyricsmith sync` timed
against the track, source `machine timed`. Either is taken as a `.txt` is: synced frames
holding other lines serve, else the timed steps are asked, and a timed answer replaces
it; with none, the file serves, and its `.lrc` is kept byte for byte, both header lines
included. An untimed answer never replaces it. Frames holding its lines count as the
file, whatever their times: a second run credits it the same way, and once a stamp is
corrected by hand, or the file re-timed, the next run embeds the new times, while undo
puts the earlier ones back. Undo leaves the `.lrc` holding the new times, which the next
run embeds again: to revert, also restore the `.lrc` from the run's backup, or delete it
so the frames serve. Those frames may be another tool's: a `.lrc` timed from a `.txt`
holding their words replaces them, and undo recovers them. A by-line edited in any way
makes the file an ordinary sidecar, which is how to change its words: a labeled file
whose lines differ from synced frames gives way to them and is rewritten from them.

**An unlabeled sidecar holding the frames' own lyrics counts as the frames.** A timed
`.lrc` with no machine label whose lines are a synced frame's lines, at the same times and with each line's text stripped
as a `.lrc` holds it, or a `.txt` whose text is an untimed frame's text once written and
read back (every line ending a newline, a leading byte-order mark dropped), does not
displace them. Every worded frame of the sidecar's kind is compared, in any language and
with any description, not only the one `--lang` ranks first: the track is served from the
frame the sidecar holds, source `frames`, its MP3 is not written, and the sidecar stays
exactly as it is. So the sidecar a run wrote from a track's frames keeps them serving on
every later run, under any `--lang`, and a run they serve reports the track `unchanged`:
an ID3v2.2 tag stays no error, a `SYLT` timed in MPEG frames keeps its timing, and
frames in another language, with a description, or beside other lyrics frames are not
saved again. A `.lrc` written under `--lang fra` from a track holding a French and a
German `SYLT` therefore leaves both on a later run under `--lang deu`. Only lyrics of
one kind are compared: a `.txt` beside synced frames still gives way to a `.lrc`
written from them, and a timed `.lrc` beside untimed frames still serves and is
embedded, since timed lines win. A sidecar whose lyrics differ
from the frames', one edited by hand say, serves and is embedded as any sidecar is.

**A dry run plans each track by the same order**, reading its sidecars and frames and
writing nothing, an unlabeled sidecar of the frames' own lyrics counted as the frames, by
the same `--lang`: `sidecar (synced)` for a timed `.lrc`, `machine transcript (synced),
providers asked` for a machine transcript (`machine transcript (synced)` with
`--offline`), and the same for a `.lrc` labeled `machine timed`, `frames (synced)` for synced frames, `fetch synced, else sidecar (unsynced)` beside a `.txt`, `fetch synced, else
frames (unsynced)` for untimed frames, and `fetch` for a track holding no lyrics. With
`--offline` the untimed ones read `sidecar (unsynced)` and `frames (unsynced)`, and a track
holding no lyrics `nothing (offline, no sidecar or frames)`.

**Then the provider chain, in this order:**

| Step | Provider and kind | Held |
| --- | --- | --- |
| 1-2 | LRCLIB timed, NetEase timed | always, but under `--offline`; NetEase's not under `--no-netease` |
| 3 | YouTube Music timed | without `--no-youtube-music`, and not while its cool-down holds |
| 4-5 | LRCLIB untimed, NetEase untimed | as steps 1-2 |
| 6 | Bandcamp untimed, found through MusicBrainz | without `--no-bandcamp` |
| 7 | YouTube Music untimed, from step 3's answer | as step 3 |
| 8 | Genius untimed | when a key is set, without `--no-genius` |
| 9 | Tekstowo untimed | without `--no-tekstowo`, and not while its cool-down holds |

`--offline` holds none of them. The summary's source column names the provider whose
answer was taken, `LRCLIB`, `NetEase`, `YouTube Music`, `Bandcamp`, `Genius` or
`Tekstowo`, or for a track served from its own files `sidecar`, `frames`, `machine
transcript` or `machine timed`. The
first accepted answer wins and the chain stops. An answer over the lyrics cap is refused
before any cleanup or parse, recorded as too large, and the chain moves on. A timed step
accepts only text with a time stamp; an untimed step keeps the words of a timed answer
and drops the stamps. An answer only some of whose lines are timed keeps every line's
words in their own order, stamps and LRC header tag lines dropped. A blank answer, or placeholder lyrics (*Placeholder lyrics*):
NetEase's "pure music" notice, a lone run of music-note glyphs, nothing but LRC header tags
such as LRCLIB's `[au:comedy]`, or timed lines with no words, is treated as nothing found,
its attempt recorded as a `placeholder`. **An answer is judged against the track's
length**, in every run: one whose latest timed line starts past the track's end, beyond
the check's `past end` tolerance, or that holds more words a second than its `too many
words` row passes (*The check and its thresholds*), is the lyrics of another recording,
so it is declined as a placeholder is and the chain moves on, the stripped-title retry
included, its attempt recorded as `implausible: <row> (<the row's measure>)`. A duration
that cannot be read declines nothing, and what a track already holds is not judged so;
`--refetch-implausible` asks again a track that holds such lyrics. **A timed answer far
past the end takes its song's plain text with it:** when one is declined as `past end` with
its latest line beyond one and a half times the track's length, it is a whole other song,
so a later untimed answer in the same track's fetch, from any provider and the
stripped-title retry included, whose words are that song's (the check's similarity at or
above its `unlike backup` minimum) is declined too, its attempt recorded as `implausible:
same song as a declined timed answer (<similarity>)`. A timed answer only a little past
the end may be a longer version of the right song, so its words decline nothing. Genius's text loses its
`<n> Contributors ... Lyrics` header, through the first line's last `Lyrics` (a title may
hold the word), and its `Embed` trailer; section headers such as
`[Chorus]` stay, as part of the published text.

**Musixmatch and Megalobiz are not in the chain.** Musixmatch's desktop API answers any
query with the same unrelated hits, so it never yields lyrics; the only known working
route forges its request signatures with a secret taken from its web app, which bypasses
its API authentication and was refused. Megalobiz answers HTTP 410 Gone.

**The stripped-title retry.** When no step's answer was accepted and at least one step
answered with nothing, the chain runs once more on the title with its trailing bracketed
groups removed, so `Song (feat. X) [Live]` is asked as `Song`. A track found that way is marked `query: stripped` in the
summary. The retry does not run when every step failed, or when the title has no trailing
group to remove.

**Each LRC provider is asked once per query.** An LRCLIB or NetEase reply holds a song's
timed and plain text together, so within one track's fetch the untimed step of each reads
the answer its timed step got and sends no request. A provider that failed for the query,
with an error status, a 429 or a timeout, is not asked again either: its untimed step
records the same error. LRCLIB's call sends only the requests its timed step needs.
When its search answers an error status or a reply in another shape, or its request
fails, after the exact lookup already found untimed text, the untimed step still takes
that text. A search that runs past the step's deadline, or fails in any other way, fails
the whole call: the untimed step records the same timeout or error, and the lookup's
text goes with it. The stripped-title retry is another query, so it asks each provider
afresh. A track found nowhere thus costs each LRC provider one round of
requests per query, not two. YouTube Music is asked the same way: its untimed step reads
the answer, or the failure, its timed step got, and sends nothing.

**How LRCLIB is asked.** Through its documented public API at `lrclib.net/api`. First the
exact lookup, `GET /api/get`, with the track's title, artist, album and length in whole
seconds, all from its tags; LRCLIB matches the length within about two seconds itself, and
answers 404 when no record matches, which is "nothing found", not an error. A server error
(5xx) from the exact lookup is treated the same way, because the lookup has been seen
overloaded (503) while the search still answered, so the search is its other try and the
lookup's server error is not retried; a server error from the search itself is an error,
once its retries (*Network discipline*) run out. When the lookup finds no matching record with text of the step's kind, or the
album tag or a usable length is missing, the search, `GET /api/search`, asks by title and
artist, and the matcher below picks among its records. The exact lookup's record goes
through the matcher too. A record LRCLIB marks instrumental is nothing found, and no
search follows it. A timed step takes a record's timed text only; an untimed step takes
its plain text, else its timed text.

**How NetEase is asked.** NetEase publishes no API documentation, so the tool uses the two
endpoints open-source lyrics clients have long used: `music.163.com/api/search/get` for
ten song hits on "title artist", then `music.163.com/api/song/lyric` for the chosen song's
lyric. Both answer anonymous requests, so neither sends a `Referer` or a cookie, and the
session refuses any cookie the search sets, so the lyric request cannot send it back. A
probe on 2026-10-23 got the same lyric with and without the search's `Referer` and cookie,
so neither is needed. NetEase publishes no terms for these routes, so it is one of the
terms-limited sources (*Lyrics sources and their terms* in `../README.md`), and
`--no-netease` leaves both of its steps out. The sibling search `api/search/pc` refuses an anonymous request with code
-462 (risk control); that refusal, or any other `code` but 200, is NetEase's error, never
"nothing found", so it shows in the run's tally. The matcher below picks the hit; with
none accepted, no lyric is asked for. NetEase answers one LRC text, timed or not, so both
steps get the same text and a timed step accepts it only with a time stamp. A song NetEase
marks as having no lyric is nothing found.

**Matching a search hit.** A wrong match puts another song's words into the file, and
nothing later in the run can tell them from the right ones, so a hit is accepted only
when every rule holds:

| Rule | A hit is refused when |
| --- | --- |
| Title | Its title and the track's are under 90% alike, compared with case, accents, punctuation, bracketed groups such as `(Remastered 2011)` or `(feat. X)`, a ` - ` qualifier and a featuring credit removed (a credit is cut only where written as one, `feat.` or `ft.` with the period or the word `featuring`, so `A Feat of Clay` keeps its whole title while `Up - 2011 Remaster` is `Up`), and `&` read as "and"; or the two hold different unbracketed digit runs, so `Song 2` is never `Song 3`. Digits inside brackets go with their group and Roman numerals are not digits, so `Song (Part 2)` against `Song (Part 3)`, or `Song II` against `Song III`, is told apart only by the length check |
| Artist | The track's artist is under 90% alike both to the hit's first credited artist and to all its credits together, compared the same way with a leading "the" dropped |
| Version | Its title names a version the track's does not: live, remix, acoustic and the like, read as Latin words even beside Chinese text (`Live版`); or a Chinese marker such as 伴奏 (backing track), anywhere in the title. `VERSION_WORDS` and `VERSION_MARKERS` in `src/lyrics_match.py` are the full lists |
| Length | Both lengths are known and differ by more than 3 seconds |

Among the hits accepted, the closest names win, then the closer length. Titles that
differ only inside brackets compare equal unless the length or a version word tells them
apart: that is the price of matching `Song` to `Song (2011 Remaster)`.

**Genius's terms.** Genius's API returns no lyrics, so `lyricsgenius` reads the song
page's HTML, which Genius's terms bar as scraping: Genius is one of the terms-limited
sources (*Lyrics sources and their terms* in `../README.md`), and `--no-genius`
leaves it out.

**Genius is matched too.** Its search answers with one song, its own best guess, which
can be another song, a live take or a remix. That song goes through the title, artist
and version rules above; Genius gives no length, so the length rule never applies. A
song refused is nothing found, not an error. A track missing its artist tag or its
title tag is asked by its file name instead, with no artist, so none of LRCLIB,
NetEase, YouTube Music, Bandcamp and Tekstowo is asked, since no hit could be confirmed for it
(Bandcamp also needs an album tag). Genius still is, with the
file name whole as the title, and its song is accepted when any reading of that file
name matches it. The readings are the name whole, the name without a leading track
number (`03 Song`, `03 - Song`, `03. Song` and `1-03 Song` all read as `Song`), each
matched by the title and version rules alone; and, where what remains splits at spaced
dashes, the last part as the title with each earlier part that is not a bare number as
the artist (`03 - Artist - Song`), matched by the artist rule too, so the song's artist
must agree. A title that starts with a number, such as `99 Lanterns`, still matches by
the name whole. Genius's credits are the song's primary artists in order, so a joint
credit matches by its first artist or all of them together, as a NetEase hit does.
(How the `providers:` tally counts such a track is under *What goes to stdout and
stderr*.) So for such a track Genius is the only provider asked, and when it fails the
track is `ERROR fetch`, not `not found`. Without a Genius key no provider is asked, and
the track is `not found` (`no lyrics expected` for a marked track, *Stand-up and spoken
word*; `silent` when `ffmpeg` measured it silent, *Silent tracks*).

**Network discipline.** Each provider call has a 30-second deadline, after which it is
abandoned as that step's timeout and the chain moves on; a Genius call, which retries
inside itself, gets that much per attempt, plus its pacing and its retry waits. A
Bandcamp call has `BANDCAMP_STEP_DEADLINE_S`, a YouTube Music call
`YOUTUBE_MUSIC_STEP_DEADLINE_S` and a Tekstowo call `TEKSTOWO_STEP_DEADLINE_S`, room for
their pacing (*Bandcamp through MusicBrainz*, *YouTube Music* and *Tekstowo.pl*, below,
which also say what each sends). Every
LRCLIB and NetEase request also has its own timeout, 5 seconds to connect and 10 between
bytes of the reply, and each call opens a fresh connection, so a call that was abandoned
shares nothing with the next. Every request, to every provider, names the tool and
its version in its `User-Agent`, as LRCLIB asks of its clients, with no address or
personal detail in it (MusicBrainz's alone adds the contact, when the config file sets
one: *The config file*): the tool's folder name and the project's version, each kept to
ASCII letters, digits, `.`, `_` and `-`, so neither can break the header, and a half
left empty reads `lyrics-tool` or `unknown`. Genius requests go through a session of the
tool's own in place of lyricsgenius's, so neither the library's header naming the
operating system and the Python release nor its `application` header naming the library
is sent. A reply from any provider, Genius included, is read only up to 2 MiB, far
above any real one, and a larger reply is that step's error before any of it is
decoded, a compressed one included, since it is counted as it inflates; with the lyrics
cap on each answer, that bounds what a run holds in memory. Neither LRCLIB's nor
NetEase's redirects are followed. Genius's are, as lyricsgenius follows them, but only
to `https` on `genius.com` or `api.genius.com` at the default port, and at most two in a
row: a redirect anywhere else, or naming no target, is that step's error before it is
asked, and a third is too. No request sends a login kept in a netrc file, on a Genius
redirect either, though the proxy and certificate settings from the environment still
apply. The Genius key goes only as lyricsgenius sends it, in the authorization header of
each request to `api.genius.com`, and is dropped on a redirect to another host. The
Genius session keeps no cookie a reply sets, so it never sends one back, not even on a
redirect's next hop.

An LRCLIB or NetEase request refused in a way that may pass, an HTTP 429, 502, 503 or 504
or a connection that dropped or was refused, is tried again up to three times, after 2, 5
and then 10 seconds. A reply's `Retry-After` header in whole seconds replaces that wait, up
to 10 seconds; its date form is ignored. The retries stay inside the call's 30-second
deadline: a wait that would end past it is not begun, and the step's error is the last
refusal, so a call is never abandoned in the middle of a wait. Every other failure is that
step's error at once: another 4xx status, a redirect, a request that timed out (it has
already spent much of the call), a certificate failure, and a NetEase body refusing the
request with its own code. A refusal a retry got past leaves no trace. Genius calls are at
least 3 seconds apart; a pace wait that would end past the call's deadline is not begun,
and the call times out. A Genius 403 or 429 challenge is retried after 15, 45 and 90
seconds, then is that step's error (`HTTP 429 after 3 retries`). A retry wait that would end
at or past the call's deadline is not begun either: the last challenge is then the step's error,
counting the retries made (`HTTP 429 after 1 retry`), or as it came when none was made.
Any other HTTP error status from Genius is that step's error at once.

**An error status is that step's error, never nothing found.** Every reply's HTTP status
is read: any status of 400 or above from LRCLIB or NetEase, a 429 rate limit included,
is an error for that step, once any retry it is owed has been made, except two answers from LRCLIB's exact lookup, its 404 (no
record matched) and a server error (5xx), both of which fall through to its search. A
redirect (a status from 300 to 399) from either is an error too, since it is never
followed. So is a NetEase reply whose body refuses the request with a code other than
200, and a reply from either in a shape the tool does not know. The chain still moves
on to the next provider, and the track is an `ERROR` only when every provider failed. A
request that fails outright, with no connection or past its deadline, is that step's
error too. A provider that answers every request but wins no track shows in the summary's
`providers:` line as `0/<n>`.

**Each refusal is recorded on its track.** Every step that ended in an error or a
timeout is kept in the track's manifest entry as a refusal, in the order asked, naming
the provider, the step's kind and a short detail, whatever the track's end: `not found`,
`no lyrics expected`, written, `unchanged`, `kept existing` or `ERROR`. So a later reader
can tell "the providers refused" from "nothing was found": a refusal beside another
provider's empty answer still ends `not found` (or `no lyrics expected`, or `silent`,
*Silent tracks*), and a track every provider failed for is still its `ERROR fetch`, its
refusals recorded too. An answer
that is empty, or one the chain does not accept, is no refusal. A track its sidecar or its
frames served with no provider asked has none; one whose untimed lyrics kept their place
still asked the timed steps, so it keeps any refusal they met. The detail is one of:

| Detail | When |
| --- | --- |
| `HTTP <status>` | An error status, or an unfollowed redirect, from any provider |
| `code <n>`, `code out of range` | NetEase answered 200 with another `code` in its body, such as `code -462`; one of ten or more digits is not quoted |
| `HTTP <status> after <n> retries` | A Genius challenge (403 or 429) still answered after `<n>` retries (`after 1 retry` for one): 3, or fewer when the call's deadline left no room for the next wait. With no room for even the first, the challenge is recorded as `HTTP <status>` |
| `<detail> after <n> retries` | An LRCLIB, NetEase or MusicBrainz request (MusicBrainz's recorded as Bandcamp's) still failing after `<n>` retries (`after 1 retry` for one), `<detail>` being how it failed last: `HTTP 503 after 3 retries`, `ConnectionError after 2 retries`. Fewer than 3 when the call's deadline left no room for the next wait |
| `timeout` | The call ran past its deadline and was abandoned |
| `unexpected reply` | A reply in a shape the tool does not know, from any provider but Genius; MusicBrainz's is recorded as Bandcamp's |
| `reply not JSON`, `reply too large` | A reply that is not JSON from any provider but Genius, or over 2 MiB from any provider; from YouTube Music, a reply not JSON stops it for the run |
| `redirect refused`, `TooManyRedirects` | A Genius redirect off `https` on Genius's own hosts, or a third in a row |
| `redirect refused`, `too many redirects` | A Bandcamp or Tekstowo redirect off that provider's own hosts, or a third one (*Bandcamp through MusicBrainz*, *Tekstowo.pl*); from Tekstowo, a third one stops it for the run, a refused one only fails the track |
| `URL refused` | A Bandcamp or Tekstowo address off that provider's own hosts before anything is sent; it stops nothing for the run |
| `reply not text`, `no track list` | A Bandcamp page that is not UTF-8, or holds no album track list; a Tekstowo page that is not UTF-8 |
| `challenge page`, `search page not recognized`, `song page not recognized` | A Tekstowo challenge, which stops it for the run, or a page not shaped as expected, which fails only its track until a run of one kind stops it (*Tekstowo.pl*) |
| `redirect to consent`, `redirect to sorry` | A YouTube Music challenge, which stops it for the run (*YouTube Music*) |
| the error's type | Any other failure, such as `ConnectionError` or `ReadTimeout` |

A detail never holds a reply's body or a request's URL, so no track title reaches it.
A Genius challenge, or an LRCLIB or NetEase refusal, that a retry got past is no refusal:
only a step's final failure counts. An LRC provider's untimed step that re-records how its timed step's call failed
(*Each LRC provider is asked once per query*) is a refusal of its own, one per step.

**Known limit: a Genius challenge page answered with status 200 reads as no lyrics.** Genius
challenges a burst of requests, and a challenge answered with a 403 or 429 status is retried
on the schedule above, the lyrics page included. A challenge page served with a 200 status
cannot be told from a song page with no lyrics on it, so that Genius step reads as finding
nothing.

## Bandcamp through MusicBrainz

Many independent albums are sold on Bandcamp, whose album pages carry each track's lyrics,
where the artist entered them, as structured data. Bandcamp's robots file disallows its
own search, so the tool never asks it: MusicBrainz, which links many albums and artists
to their Bandcamp pages, is how an album's page is found. The Bandcamp step comes after
every LRC step (step 6 of the chain), so it is asked only for a track they all missed,
and only for a track with both an album tag and an artist tag; any other is skipped
unrecorded and not counted as asked. `--no-bandcamp` leaves the step out. Bandcamp's terms
give its content for personal, non-commercial use only, so Bandcamp is one of the
terms-limited sources (*Lyrics sources and their terms* in `../README.md`);
MusicBrainz asks for one request a second and a contact in the User-Agent, both kept.

**The route.** The first track of the album that reaches the step starts the lookup:

| Step | Request | Detail |
| --- | --- | --- |
| 1 | MusicBrainz release search | By the album and artist tags, up to 10 releases |
| 2 | Up to 3 release lookups, `inc=url-rels` | The releases passing the album match (below), best first; the first holding a `free streaming` or `purchase for download` link to a Bandcamp `/album/` page wins |
| 3 | One artist lookup, `inc=url-rels` | Only when no release linked a page: the best release's first credited artist, for a `bandcamp` link to their Bandcamp site |
| 4 | The artist site's `/music` page | Only on the artist route: the album is the `/album/` entry whose title best passes the album match; a `/music` page that redirects to an album page is that page, kept when its album name passes |
| 5 | The album page | Its `MusicAlbum` JSON-LD track list serves every track of the album; on the artist route, entries tying on the best title are opened, the exact title first, up to `MAX_TIED_ENTRIES` (8), until one gives the asked track's lyrics |

**Same-titled albums.** A band can hold several albums of one name, and the album match
lets version words pass (below), so `Name (Deluxe Edition)`, `Name (Live)` and `Name`
all tie for a tag of `Name`. Their pages are opened in this order, at most eight: first
the entries whose whole title, version words included, is the tag's (letters and digits
compared, case and accents folded, `&` read as "and"), then the rest, each group in the
order the `/music` page lists them. The album is the first whose best match for the
asked track (the track match below) has lyrics; when none does, the first whose track
list holds the track at all; when none holds it, the first one opened. So a tag naming
an edition is answered from that edition, a plain tag from the plain album, and a
self-titled run told apart only by suffixes is searched down to its eighth. A single
best entry is one page. The choice is made with the album's first track to reach the
step and kept for the rest (*The cache*), which is right when the tag names one real
album. No further page is asked once the call's deadline has passed: that call ran
out of time, and nothing is kept.

A release search answers no URL relations, so each release needs its own lookup, and
one album is often many releases (pressings, countries, reissues), the link on whichever
an editor attached it to. On the album page, each track is matched by the matcher every
provider's hits go through (*Matching a search hit*): title, version words, artist and
length. The best track's lyrics are the answer; no matching track, or one without
lyrics, is nothing found. No release matching is nothing found too (`no album match`),
and so is no link on either route (`no link`): the album was looked for.

**The album match is strict, but lets version words pass.** A release, or a `/music`
entry, matches when its title passes the title rule against the album tag (90% alike,
the same digit runs) and, for a release, its artist credit passes the artist rule against
the first credit or all of them. The version rule is not applied to the album: a deluxe or
remastered edition's tracks are usually the same songs, and each track is judged again,
version words included, when it is picked from the page. A compilation credited to
"Various Artists" fails the artist rule against a track's own artist, so it reads as
nothing found.

**The cache.** One lookup serves the whole album: its outcome (the page's tracks, no
link, no album match, or the error) is kept for the rest of the run, so the album's later
tracks and the stripped-title retry send nothing. It is kept per artist and per album
tag, the tag's whole title compared with its version words, so tracks tagged `Name` and
tracks tagged `Name (Deluxe Edition)` are each their own lookup, and each tag picks its
own edition among same-titled albums. An album-level failure (MusicBrainz, a
refused redirect, a page that cannot be read) is recorded once on each track of the album
that reaches the step, with no new request. A call that ran out of time is not kept, so
the album's next track looks it up again; a slow MusicBrainz is asked again for the next
track, still at its one-request-a-second pace. Nothing is kept between runs.

**Pacing and the worst case.** MusicBrainz is asked at most once a second, its own
documented limit, and a `429` or `503` from it is retried as an LRCLIB request is
(*Network discipline*), within the call's deadline. Bandcamp gets at most two page
requests per album, or among same-titled entries the `/music` page and up to eight album
pages, never retried: a retry would break that cap, so a refused page is the album's
error. The worst case for an album is five MusicBrainz requests, about five seconds, and
nine Bandcamp requests, paid once per album tag; a Bandcamp call's deadline is
`BANDCAMP_STEP_DEADLINE_S`.

**Redirects.** MusicBrainz's are never followed. A Bandcamp request follows at most two,
each only to an `https` URL whose host is `bandcamp.com` or ends in `.bandcamp.com`, with
no login and no port but 443; a redirect to any other target, or a third redirect, is the
album's error (`redirect refused`, `too many redirects`). The address asked first is held
to the same rule before anything is sent: one off it is `URL refused`, not `redirect
refused`. A link MusicBrainz holds as `http` is asked as `https`, by the same host rule,
and one whose host is not Bandcamp is not taken as a link at all.

**What is sent, and what never is.** To MusicBrainz: the album and artist tags in the
search, and the ids its own replies gave in each lookup's path (only in MusicBrainz's id
form, so a reply cannot steer a request), with the tool's `User-Agent` and, when the
config file sets one, the contact appended to it in parentheses, as MusicBrainz asks of
its clients (*The config file*). To Bandcamp: the page requests, with the plain
`User-Agent`. Never a cookie, to either: every request goes through a session whose
cookie jar refuses every cookie a reply sets, so nothing is stored to be sent back. Never
the contact to Bandcamp, never Bandcamp's search, and never a file name, a path or a
lyric to anyone.

## YouTube Music

YouTube Music had the widest coverage of the lyrics sources evaluated
(`lyrics_sources.md`), timed lyrics included, through the anonymous route its own web
player uses. **It is on by default**, sweeps included, rather than an option off by
default, accepting that YouTube may block it; `--no-youtube-music` leaves it out.
YouTube's terms bar access by automated means; that was accepted, for
the lyrics of albums already held and for personal use, as a deliberate exception, made
only within the hard rules below: no cookie, no
account, no request signing, no challenge worked around, paced slowly, and backing off at
the first refusal.

**Two steps, one set of requests.** Its timed step comes after NetEase's (step 3 of the
chain), and its untimed step after Bandcamp's (step 7), which reads the answer the timed
step got and sends nothing. A track already holding untimed lyrics runs only the timed
steps, so it can still gain YouTube Music's timed lyrics. A track with no artist tag is
skipped unrecorded, since the search needs one.

**The requests.** Three POSTs to `https://music.youtube.com/youtubei/v1`, each with a
JSON body:

| Step | Endpoint | Gives |
| --- | --- | --- |
| 1 | `search`, filtered to songs, for the title and artist | Song results: title, artists, album, length, video id |
| 2 | `next`, for the matched song | Its lyrics tab, or none |
| 3 | `browse`, for that tab | Timed lines, or plain text, and a source credit line |

Only a result that is the song's own audio track counts, never a music video or an
upload, and it is matched as any provider's hit is (*Matching a search hit*). No match is
nothing found after the search alone, no lyrics tab after two requests, and empty lyrics
after all three.
Timed lines become millisecond `[mm:ss.mmm]` lines from their start times, and the answer
is timed only when every line has a time, else it is plain text. The source credit line is
not part of the lyrics. The stripped-title retry searches again: three more requests.

**What is sent, and what never is.** Each request sends the tool's own `User-Agent`, a
JSON content type, `Origin: https://music.youtube.com`, and in its body the client name
and version YouTube's web API expects and the search words or ids. Never a cookie: each
request goes through a fresh session whose cookie jar refuses every cookie, no `Cookie`
header is written, and the home page, which hands one out, is never asked. Never a
visitor id: no `X-Goog-Visitor-Id` header and no `visitorData` in a body. Never an
account, a signature or the MusicBrainz contact. No redirect is followed, and nothing is
retried: the HTTP layer's retries of `429` and `503` are off for it, since a retry after
a refusal is the knocking the rules forbid.

**The pace.** At least `YOUTUBE_MUSIC_MIN_INTERVAL_S` between any two YouTube Music requests, under
one lock, so three requests a track take about three of those; a YouTube Music call's
deadline is `YOUTUBE_MUSIC_STEP_DEADLINE_S`.

**The stop rule.** Any reply but `200`, any redirect included, a `Location` naming a host
that starts with `consent.` or a path that starts with `/sorry`, or a `200` whose body is
not JSON, turns YouTube Music off for the rest of the run and writes the cool-down below.
That track records the refusal (`HTTP 429`, `redirect to consent`, `reply not JSON`), and
every later track skips both steps unrecorded, so none counts as asked. **Not a stop**,
only that track's error, with no cool-down: a `200` of an unexpected shape (`unexpected
reply`, which is how a change to YouTube's JSON shows and needs a code fix, not a back-off),
a reply over 2 MiB, a connection error and a request timeout.

**The cool-down file** is `$XDG_STATE_HOME/lyricsmith/youtube_music.json` when
`XDG_STATE_HOME` holds an absolute path, else
`$HOME/.local/state/lyricsmith/youtube_music.json`: state, not settings, so it lives apart
from the config file and from every album. It holds three fields, each time in UTC as
`2026-10-22T09:30:00Z`: `refused_at`, when the refusal came; `what`, what it was
(`HTTP 429`, `redirect to consent`); and `until`, exactly 24 hours later. It is written
atomically and read back, since a sweep's next album reads it at once.

- Every run or compare that would ask YouTube Music reads it first, and until `until`
  leaves YouTube Music out (`cooling down until <time>` in the summary). `--offline` and
  `--no-youtube-music` never read it, and neither does a dry run.
- A file that cannot be read, or does not hold exactly those fields, is a `youtube
  music:` notice on stderr and is ignored, as is one whose refusal lies in the future or
  whose `until` is not 24 hours after it, so an edited or broken file cannot switch
  YouTube Music off for good; the next refusal writes a fresh one.
- A cool-down that cannot be written still stops YouTube Music for the run, with a
  notice saying so.
- **To clear it early, delete the file by hand.** No option clears it.

## Tekstowo.pl

Tekstowo.pl holds user-submitted lyrics for a wide catalog, Polish and international.
**It is on by default**, with `--no-tekstowo` to leave it out. Its terms offer the lyrics
for home use only and forbid any gainful use; lyrics of albums already held, kept in a
personal library, are that home use. It is asked only within the hard rules below: no
cookie, no browser pose, no script run, no challenge worked around, paced slowly, and
stopping at the first refusal.

**Last in the chain** (step 9, after Genius), and untimed only. It is asked only for a
track every other step missed, so the fewest requests reach a small site, and its
user-submitted text ranks below every source before it. A track with no artist tag is
skipped unrecorded, since the search and the artist rule both need one.

**The requests.** Two GETs, each through a fresh session whose cookie jar refuses every
cookie:

| Step | Request | Gives |
| --- | --- | --- |
| 1 | `https://www.tekstowo.pl/szukaj/<artist> <title>`, path-encoded | The listed songs, each `Artist - Title` and its link |
| 2 | The best-matched song's own link, on the same host | The page's own title and artist, and its lyrics |

No song address is built from the tags: the site gives a second artist of the same name a
numeric suffix, so a built address can answer `200` with another artist's song. The listed
songs are matched as any provider's hits are (*Matching a search hit*), with no length, so
the length rule never applies; no match is nothing found after the search alone. The
matched page's own title and artist must pass the same rules, or it is nothing found. Only
the page's original lyrics are read, never its caption or the translation beside them. A
blank text, or placeholder lyrics, is nothing found. The stripped-title retry searches
again: two more requests.

**What is sent, and what never is.** Only the track's artist and title tags, joined by a
space, a `/` in either made a space, in the search's path; the tool's own `User-Agent`
and nothing that poses as a browser. Never the album, a file name, a path, the audio, a
cookie or the MusicBrainz contact. Its replies are read as text, so no script on a page is
ever run. A redirect is followed only to an `https` address on `www.tekstowo.pl` with no
login and no port but 443, at most two of them, by the same check as Bandcamp's.
Nothing is retried, since a retry after a refusal is the knocking the rules forbid.

**The pace.** At least `TEKSTOWO_MIN_INTERVAL_S` between any two of its lookups' requests,
under one lock, so a track takes about two of those; a same-site redirect's hop (at most
two) follows its lookup's request at once, unpaced. A Tekstowo call's deadline is
`TEKSTOWO_STEP_DEADLINE_S`.

**The stop rule.** HTTP `403`, `429` or `503`; a third redirect; a challenge page; or
`UNRECOGNIZED_STOP_RUN` search pages in a row without the site's search form, or as
many song pages in a row without their lyrics block, turns Tekstowo off for the rest of
the run and writes the cool-down below. That track records the refusal (`HTTP 403`,
`challenge page`, `song page not recognized`), and every later track skips the step
unrecorded, so none counts as asked. A page that holds a challenge page's marker is a
block, and asking on would only knock. One page not shaped as expected is weak evidence
of a block or a change to the site, and may be a real song entry the reader does not
know, so it is only that track's error (`search page not recognized`, `song page not
recognized`), with no cool-down; only a run of them stops. A page of the same kind read
as expected starts its count again. The two kinds are counted apart, since every track's
search page comes before its song page: a change to the song pages alone still stops it
within that many tracks. A page that is shaped as expected is read whatever its words, so
lyrics quoting a marker stop nothing. **Not a stop**, only that track's error, with no
cool-down and no count: any other error status, such as `404` or `500`, a redirect
refused (`redirect refused`), since one odd `Location` is no refusal and must not turn
the source off for a day, a page that is not UTF-8, a reply over 2 MiB, a connection
error and a request timeout.

**An instrumental's page is nothing found.** A song entry the site marks instrumental has
no lyrics block; a box whose text opens with "Utwór instrumentalny" stands in its place
(probed 2026-10-24). Such a page is read as a song page with no lyrics: nothing found, no
error, and no count.

**The cool-down file** is `$XDG_STATE_HOME/lyricsmith/tekstowo.json` when `XDG_STATE_HOME`
holds an absolute path, else `$HOME/.local/state/lyricsmith/tekstowo.json`, beside YouTube
Music's, and it follows the same rules (*YouTube Music*): the same three fields, written atomically, read before a run or a
compare that would ask, never read under `--offline`, `--no-tekstowo` or a dry run, ignored
with a `tekstowo:` notice on stderr when unusable, and 24 hours long. A refusal during a
compare writes it too. One source's cool-down never turns the other off. **To clear it
early, delete the file by hand.** No option clears it.

## Stand-up and spoken word

A track whose genre expects no lyrics is **marked**, so that a miss on it is not counted
with the real misses. The genre is read from the track's `TCON` frame, and a frame holding
several genres marks the track when any one of them matches. The built-in list is
`Stand-up Comedy`, `Stand-Up` and `Spoken Word`. Each entry is compared with a whole
tagged genre, after surrounding whitespace is trimmed and case is folded, and never as a
substring: `Comedy` alone is not `Stand-up Comedy`, so a musical comedy track, which has
lyrics, is not marked. An ID3v2.3 frame saved with several genres holds them as one
`/`-joined value, such as `Rock/Stand-up Comedy`, which is matched whole as well, so that
track is not marked.

A `[no_lyrics]` table in the config file replaces the built-in list, and an empty
`genres` array turns marking off (*The config file* says where the list is read from):

```toml
[no_lyrics]
genres = ["Stand-up Comedy", "Stand-Up", "Spoken Word"]
```

**The providers are still asked** for a marked track, every step in the usual order, and
an answer is taken as for any track. Only where the run would end the track `not found`
does a marked track end **`no lyrics expected`** instead, under `--offline` too, counted
apart from `not found` in the summary's counts line. A dry run plans a marked track's
lookup as `fetch (no lyrics expected if none)` where it would plan a plain `fetch`. With
`--fresh` that holds only for a marked track with no lyrics frames and no sidecar: a
fresh miss keeps what the track holds, so a track holding lyrics plans a plain `fetch`.
A marked track that keeps its old lyrics still reads `kept existing`, and one whose every
provider failed still reads its `ERROR fetch`.

**A marked track that ends a run holding lyrics is flagged** `lyrics on a no-lyrics
genre`, with the matching genre as tagged in brackets: a track written from a provider's
answer or its own sidecar, a track left `unchanged`, and a track that kept its old
lyrics. A written track shows the flag in its check column with any other FLAG; the other
two show it last on their row:

```text
FLAG: lyrics on a no-lyrics genre (<genre>)
unchanged (FLAG: lyrics on a no-lyrics genre (<genre>))
kept existing (FLAG: lyrics on a no-lyrics genre (<genre>))
kept existing: new answer <reasons> (FLAG: lyrics on a no-lyrics genre (<genre>))
kept existing: no answer to the refetch[; <why>] (FLAG: lyrics on a no-lyrics genre (<genre>))
```

The fourth row is a `--fresh` run that declined an answer, the fifth a
`--refetch-untimed` run that heard no answer. Every such track counts in the summary's
`flagged`, a kept one included, and the manifest records the flag in the track's `check`.
The flag only points at the track: nothing is removed or declined because of it, and a
`--fresh` run still declines an answer for `unlike backup` or `untimed` alone. Clearing a
wrong track's lyrics is a strip's job, of that one track with `--track` (*Comparing with
the providers*). `--mode check` judges the embedded lyrics against the
backup and never reads the genre, so it does not raise this flag again; the run's manifest
keeps it.

## Silent tracks

A track that is silent, such as a gap of digital silence between two songs, has no lyrics
to find, and the providers cannot answer for it. When `ffmpeg` is on `PATH`, a track the
whole provider chain missed is **measured**, and one whose whole-track peak is at or below
`-60` dBFS (`SILENT_PEAK_DBFS`) ends **`silent`** instead of `not found`. The threshold
sits about 30 dB under the quietest music and above the dither and hiss a silent gap
carries; it is a named constant, and there is no option for it or to turn the check off.

`ffmpeg` is looked up once per run with `shutil.which`. It decodes the track's first audio
stream to a null sink through its `volumedetect` filter and reads the `max_volume` line
of its answer; nothing is written and nothing leaves the machine. Each call is bounded
by `SILENCE_TIMEOUT_S`, 60 seconds.

**The providers are still asked** first, in the usual order, and an answer is taken as
for any track; only a miss is measured. A track that was served, kept or errored is never
measured, nor is one whose placeholder lyrics a run removed (*Placeholder lyrics*): it ends
`removed`. The ends of a missed track are tried in this order, and the first that holds
wins:

1. `ERROR fetch`, when every provider failed.
2. `kept existing`, when the track holds lyrics frames or a kept sidecar.
3. `silent`, when `ffmpeg` measured a peak at or below the threshold.
4. `no lyrics expected`, for a marked track (*Stand-up and spoken word*).
5. `not found`.

So silence is judged ahead of the genre: a silent track tagged `Stand-up Comedy` is
`silent`. A dry run measures nothing and plans a lookup as it always did. `--offline`
still measures, since the measurement is local.

A silent track's row reads `silent (peak -91.0 dBFS)`, the peak to one decimal place; an
all-zero signal reads `-91.0` for a 16-bit file, and `-inf` is held at `-120.0`. It
counts as `silent` in the counts line, apart from `not found` and `no lyrics expected`,
and its exit code is `0`.

**A measurement that fails** never fails the run and never changes the end: the track
ends as it would without the check, and its row says why in brackets, such as
`not found (silence check failed: timed out)`. The reasons are `timed out`,
`ffmpeg did not start`, `ffmpeg exit <n>` and `no level in its answer`. The exit code is
unchanged.

**Without `ffmpeg`** the check is off for the run: no track is measured, the `silent`
count is `0`, and the summary says `silence check: off (no ffmpeg)`. A run with it says
`silence check: on` (*What goes to stdout and stderr*). The manifest records which as its
`silence_check`, and a silent track's peak as `peak_dbfs` (*The backup and its manifest*).

## What a write changes

Every write, an embed, a strip or an undo's restore, changes only the `SYLT` and `USLT`
frames, except that a file with no ID3v2 header also gains its ID3v1 fields as v2 frames:

- **The ID3 version stays.** A v2.3 tag is saved as v2.3 and a v2.4 tag as v2.4, with no
  other frame converted. A v2.2 tag cannot be written, so a run's embed of that track
  is `ERROR embed` with the MP3 untouched (a track its own frames serve is never
  embedded, so it gets its sidecar and no error), though a run without `--fresh` may
  already have brought its sidecars to one, which undo puts back. That holds even when
  its frames already hold the answer: the tag is refused before it is compared. A fresh run refuses the tag
  before its strip, as it does an MP3 it cannot open for writing, so the track keeps its
  frames and sidecars too (*The modes* lists the failures that can still leave a fresh
  run's track stripped, for `--mode undo` to restore). `--mode strip` on a v2.2 tag
  holding lyrics frames is `ERROR strip` before anything changes; one holding none has
  nothing to remove from the tag, so only its sidecars go. A file with no ID3v2 header
  gains a v2.4 tag, holding its ID3v1 fields beside the lyrics frames.
- **The ID3v1 tail stays byte-identical**, on every write. It is found with mutagen's
  private `find_id3v1`, which has no deprecation contract, so any mutagen version bump
  must re-run the ID3v1 tail tests (`test_lyrics_tags.py`) before it lands. A hostile
  or misread tail fails closed: the read-back compares the file's last bytes with the
  tail put back, so a wrong find is that track's error, never a silent change.
- **Tags are written in place**, the same file rather than a new one renamed over it.
  An MP3 that a library shares with another folder by a hard link, a torrent client's
  seeding folder say, is one file under two names, so a write through either changes
  both.
- **The frames are built and encoded in memory first.** An answer the embed cannot encode
  (a time stamp past the SYLT range, a character no Unicode encoding writes, a NUL) is
  that track's `ERROR embed` with the MP3 untouched, in every mode.
- **A frame mutagen cannot parse is lost on any save.** mutagen skips a frame whose
  fields do not read (a lyrics frame whose language is not ASCII, say) and saves the tag
  without it, so the tool never sees it, the backup never records it, and undo cannot
  put it back. A well-formed tag loses nothing.
- **Each write is read back** and its lyrics frames compared with what was meant, so a
  save that did not take is that track's error, not a silent success.
- **A write that would change nothing is not made.** Before saving, the embed compares
  the lyrics frames it would leave with those the track holds, as the read-back compares
  them: the kind, language, description, `SYLT` time format and content type, and the
  text or the entries with their times, in any order (a save orders frames by size, so
  no stored order survives one) and never the encoding byte, since the words are what
  is written. The one exception is a frame stored as UTF-8 in a v2.3 tag, which another
  tool may have written though v2.3 does not allow it: that tag is saved, which
  re-encodes the frame as UTF-16. When they are the same, the MP3 is not saved: its
  bytes and modification time stay, and its `frames_written` is false. Nothing a
  skipped save would have done is the run's to do: the version and the ID3v1 tail stay
  either way, and a file with no ID3v2 header holds no lyrics frame, so it is always
  saved. A skipped save also leaves what a save would have dropped: a frame mutagen
  cannot parse, and an earlier frame sharing its kind, language and description with
  the one read. Likewise a sidecar file already holding exactly the bytes to be
  written is not rewritten, so its modification time stays and the run records no
  hash for it (a link is still replaced by a file), and a fresh run does not strip a
  track whose frames already hold its answer. A track for which nothing at all changed, no frames saved,
  no sidecar written, renamed or removed, reports `unchanged`, and undo of it changes
  nothing.

## What goes to stdout and stderr

The end summary goes to stdout and nothing else does, so `> summary.txt` captures exactly the
report. Progress lines, notices and errors go to stderr. A track whose search fell back to its
file name, because a tag was missing, is marked `query: filename` in the summary, one found
by the stripped-title retry `query: stripped`, one whose sidecar link led out of the album
`sidecar link outside the album ignored: <name>`, and an undo report marks each sidecar
copy it set aside with `displaced:` on that track's row.

A run's summary has one row per track, its source, kind and check columns, then a counts
line:

```text
synced 3 · unsynced 1 · unchanged 4 · not found 1 · no lyrics expected 0 · silent 0 · kept existing 0 · removed 0 · errors 0 · flagged 1 · genius: absent
```

Each track is counted once among the statuses: `synced` and `unsynced` count the tracks
written, by kind, and a track left as it was counts as `unchanged` alone, whatever its
kind; `removed` counts the tracks a run found nothing for and whose implausible old
lyrics it removed, a `--refetch-untimed` run's or a stored placeholder's in any run
(*Placeholder lyrics*), apart from `kept existing`, the tracks that kept
theirs; its row still names its source and kind, and its check column reads `unchanged`
with its verdict. `no lyrics expected` counts the marked tracks nothing was found for
(*Stand-up and spoken word*), apart from `not found`. `silent` counts the tracks nothing
was found for whose audio `ffmpeg` measured as silent (*Silent tracks*), also apart from
`not found`. `flagged` is counted apart, over
the written and unchanged tracks whose check flagged them, and every track flagged
`lyrics on a no-lyrics genre`, a `kept existing` one included. A progress line on stderr
names an unchanged track's kind and source after `unchanged` (`unchanged synced
(sidecar)`).

After the counts line, a line each says whether the run asked Bandcamp, YouTube Music
and Tekstowo, and whether it measured silence:

```text
bandcamp: on
youtube music: on
tekstowo: on
silence check: on
```

`bandcamp:` reads `on`, `off (--no-bandcamp)` or `off (offline)`. `youtube music:` reads
`on`, `off (--no-youtube-music)`, `off (offline)`, `cooling down until <time>` when a
cool-down held as the run began, or `stopped after <refusal>; cooling down until <time>`
when a refusal stopped it during the run (*YouTube Music*), `<time>` in UTC as
`2026-10-22T09:30:00Z`. `tekstowo:` reads the same, its option `--no-tekstowo`, its
cool-down and its stops its own (*Tekstowo.pl*). A compare prints the same provider lines
after its counts line; a dry run, a strip, a check and an undo ask no provider and print
none of them.

`silence check:` reads `on` when `ffmpeg` was found and `off (no ffmpeg)` when it was not
(*Silent tracks*). Only a run prints it; a compare, a dry run, a strip, a check and an undo
measure nothing and print no such line.

A run's summary follows those lines with one line tallying each provider in chain
order: the tracks whose answer the run kept, over the tracks it was asked for, and, for
a provider that refused or failed for any track, how many in brackets.

```text
providers: LRCLIB 5/12 · NetEase 4/7 (3 refused) · YouTube Music 2/3 · Bandcamp 0/2 · Genius - · Tekstowo 0/1
```

A provider counts once per track, its synced and plain steps together, and so does a
track the stripped-title retry asked it for twice. Every step the fetch records counts as
asked. The chain skips LRCLIB, NetEase, YouTube Music, Bandcamp and Tekstowo without
recording an attempt for a track asked by its file name, with no artist, so none of them
counts as asked for that track; it skips Bandcamp for a track with no album tag, and
YouTube Music and Tekstowo each for every track after a refusal stopped it, the same way.
A `0/<n>` still means `n` tracks the provider was asked for, though a Bandcamp track whose
album's lookup an earlier track made sends no request of its own. An answer counts as kept
when the track took it, an embed that then failed included, and so does a fresh run's
answer whose decline check itself failed, an `ERROR check` before anything was written; it
does not count when a fresh run declined it.
A track a timed sidecar or synced frames served asks no provider and is not counted; a
sidecar's win shows in the source column as `sidecar`, the frames' as `frames`. A machine-labeled
`.lrc` is served from the track's own files too, as `machine transcript` or `machine
timed`, but its track was asked of the timed steps, so each provider they asked counts it as asked, and
none as kept. An
`unchanged` track's answer counts as kept, as a written one's does. `-` marks a provider
the run did not hold: Genius without a key or under `--no-genius`, Bandcamp under
`--no-bandcamp`, YouTube Music under `--no-youtube-music` or while its cool-down held,
Tekstowo under `--no-tekstowo` or while its cool-down held, and every provider under
`--offline`. A provider that answers every request with plausible but useless data wins
nothing and shows `0/<n>`, where the counts alone would never show it. A strip and a dry
run ask no provider and print no such line. A run cut short tallies the tracks whose fetch
finished. A track counts once as refused however many of the provider's steps refused for
it (*Each refusal is recorded on its track*); a provider that refused no track shows no
bracket.

A compare's summary (*Comparing with the providers*) has one row per track, its verdict and
scores columns, then, for each FLAG row, that track's one-track strip command, ready to copy:

```text
strip #2: album.py --album /srv/music/invented_album --mode strip --track '/srv/music/invented_album/02 The Long Bit.mp3'
```

then its counts line and the same `providers:` tally line, counting answers that agreed:

```text
agrees 1 · flagged 1 · no second source 1 · errors 0 · not judged 1 · source unrecorded 0 · genius: configured
```

Its hints are notes at a row's end, on stdout with the rest. It prints no progress lines;
the notices go to stderr as a run's do.

**Notices on stderr** are prefixed by what they concern: `genius:` (the key: no key
found, a key file readable by others, an unusable default file), `no_lyrics:` (the
default file's genre list), `musicbrainz:` (the default file's `[musicbrainz] contact`
unusable, never quoting it, so no contact is sent), and `youtube music:` and `tekstowo:`
(that source's cool-down file that cannot be used and is ignored, or one that could not be
written after a refusal).

Every printed name, path and message shows its control characters (C0, tab and newline
included, DEL and C1) as visible `\xNN` escapes, and its direction marks, embeddings,
overrides and isolates (U+200E, U+200F, U+202A to U+202E, U+2066 to U+2069) and line and
paragraph separators (U+2028, U+2029) as `\uXXXX`, so a name cannot drive the terminal or
read as another. Every other character prints as it is. The manifest keeps the true
names.

## Exit codes

| Code | When |
| --- | --- |
| `0` | Every track was processed with no error. A track with no lyrics found, or one the check flagged, is not an error, and neither is a compare FLAG nor a hint, whatever the count. |
| `1` | At least one track errored: its backup, sidecar, fetch, strip, embed or check, or its undo. A prune exits `1` when a run folder's rename or removal failed. A compare exits `1` when any row is an `ERROR`: a tag or sidecar it cannot read, or every provider asked failing. A dry run exits `1` when it cannot plan a track: its tag or sidecar unreadable, or, with `--fresh`, or for an untimed-only track with `--refetch-untimed`, a sidecar name its strip, or an MP3 its embed, would fail on. |
| `2` | The run could not start: a bad or misplaced option, no album or no MP3 under it, the album lock held by another run, no run to check or undo, a prune on a Python whose `shutil.rmtree` does not protect against links, a `--track` outside the album or the run or not one of its tracks (*`--track` paths*), or an unusable `--config` (unreadable, holding none of the three tables, a malformed `[no_lyrics]` table, or an unusable `[musicbrainz] contact`). Also an album-level error before any MP3 changed, as below. |
| `3` | The work finished, or stopped part-way, but its record could not be written: the run's manifest after a track finished (an OS error, or a write that does not read back as written), or the `--changed-list` file. Each is said on stderr after the report. So is a prune's report that could not be written, and so is a report that could not be written once any mode's work began, a dry run's, a check's and a compare's included: a progress line or the summary refused by a closed stderr or stdout (below). It outranks `1` and `130`, since undo depends on the record: a run with track errors, or one a Ctrl-C stopped, whose record also failed exits `3`, its errors still in the summary. |
| `130` | Interrupted with Ctrl-C. Once the backup is done, the manifest records the tracks that finished; before it, the run stops with no summary and no track changed. |

**An album-level error is exit `2`, with one line on stderr naming it and no traceback:** an OS error, or a manifest write that does not read back as written.
The album-level steps are taking the lock (a read-only album cannot hold one), the backup,
creating the run folder, and reading a run's manifest in `check` or `undo`. Each comes before
any MP3 changes. Writing the manifest after a track finished is the one that can come later,
in a run or an undo, and it is exit `3`, not `2`: when it fails, with an OS error or a write
that does not read back as written, no further track may change once its record cannot be
kept. A run stops there as an interrupted one does, with the summary on stdout and the
unprocessed tracks as `not reached`; an undo stops with its report of the tracks it reached,
the last of them already restored. Either then prints the reason on stderr, one line with no
traceback, and exits `3`, so a caller can tell a run that was made from one that never
started.
A run folder removed after its run was chosen, before undo takes the lock or before check
reads it, is no run to check or undo: exit `2`, `no run '<id>' under <album>`.
A report that cannot be written once the work began is exit `3`, not `2`, since the run
was made and its manifest holds what it changed; a dry run, a check and a compare change
nothing, and take the same code so that a lost report always reads the same way. A
progress line that cannot be written to stderr (a closed pipe) ends a run or a strip once
the track in hand is recorded, and no summary is printed; a summary, a warning or a
closing notice that cannot be written, in a run, a strip or an undo, ends it the same way,
as does a prune's report, a dry run's plan, a check's report, or a compare's report or its
cool-down notice. One line on stderr names it, `could not write the report: <why>`, while
stderr can still take it; with stderr gone, the exit code says it alone. Both streams are
flushed before the exit, since a pipe holds the summary in its buffer, and a stream that
still cannot take what it holds is pointed at the null device: otherwise the interpreter's
own flush at exit fails and makes the code `120`. A line that cannot be written before the
backup is still exit `2`, stderr closed for good included: no MP3 has changed.

## Interrupting a run

A Ctrl-C while a track's MP3 or sidecar is being written, or its outcome recorded in the
manifest, waits for that write to return, so none is left half-written; if the write itself
failed, the track's row shows that error instead of `interrupted`. Either way the run stops
there: the summary is printed with every track it never reached as `not reached`, a notice on
stderr says it was interrupted, and the exit code is `130`. Ctrl-C during a fetch or a check
stops the run at once, with the same summary and code; that track's row records what had
already been done to it, which is what undo needs. Ctrl-C before the backup is complete stops
the run with no summary and exit `130`: no track has changed yet. A shell that runs the tool
with Ctrl-C ignored gets no interrupt at all.

Undo holds Ctrl-C the same way: a Ctrl-C while a track's frames or sidecars are being
restored, or its undo recorded, waits for that track and its record. Undo then stops: its
report is printed with every selected track it never began as `not reached`, a notice on
stderr says it was interrupted, and the exit code is `130`. A record that failed behind the
held Ctrl-C is still reported, and the exit code is then `3`.

A run or an undo killed outright, rather than interrupted, while a sidecar is being written,
or a sidecar link put back, can leave a temporary `.<name>.<random>.tmp` file beside the MP3 (`<name>` cut short
when the whole would pass 255 bytes), and one killed while the
manifest is being written can leave one of the same form in the run folder, beside
`manifest.json`; either is safe to delete by hand.

## Symbolic links are skipped

Discovery never follows a link. A linked MP3, or a linked folder under the album, is skipped
with one stderr notice each, so a track missing from the summary is explained. Hidden files
and folders, any name starting with `.`, are skipped without a notice, which keeps the run
folder and the macOS `._` files out. The album
path itself is resolved once, so `--album` may be a link to the album.

## Hard links are the file itself

A hard-linked MP3 or sidecar is treated as the file itself, since it is: every name of a
hard-linked file is equally its own, and there is no way to tell which name is the
original. A synced folder cannot plant one pointing outside the album, since a sync
carries a file's contents, not its links. An archive mostly cannot either, though not by
every unpacker. Measured: bsdtar 3.5.3 on libarchive 3.7.4 and Python's `tarfile`
under its `data` filter, the default from Python 3.14, refuse a hard-link entry naming a
path outside the folder they unpack into; `tarfile` under its `tar` filter, the default
before 3.14, creates it; a zip archive has no hard-link entries. GNU tar and macOS's
Archive Utility were not measured. Even then the link is the file itself, so the
behavior here is unchanged: the album holds that file, and writing its tags writes the
one file. Otherwise only someone already able to write on the volume can link a name in
the album to a file elsewhere. (A symbolic link, which can point anywhere, is
skipped, above.) Tags are written in place (*What a write changes*), so a library
sharing its files with a torrent client's seeding folder by hard links changes both
copies, since they are one file.

## `--track` paths

`run`, `strip`, `check`, `undo` and `compare` take `--track`. It is resolved against the
working directory, as a shell path is, and must then lie under `--album`. A track outside the
album is exit `2`, and so is one the chosen run does not list. Under `run`, `strip` and
`compare`, which read the album rather than a run, it must also be one of the tracks they
find there (an MP3, outside any hidden folder): any other path, a missing file or another
kind of file included, is exit `2`, with `--track <path> is not a track of <album>` on
stderr, the path as a manifest names it, relative to the album.

## The album lock

One run or one undo changes an album at a time. Each takes a kernel lock, through the shared
`run_lock` library (`flock` on POSIX), on `<album>/.lyricsmith/lock`, creating the folder
and the file when absent. A second run of the same album while the first holds it is exit `2` before anything is
backed up. The kernel drops the lock when its holder exits, however it exits, so a run
killed outright leaves a lock file that holds nothing, and the next run simply takes it. The
file itself stays between runs: deleting it would race a second run opening it, and the two
could then each hold a lock.

`.lyricsmith` and its `lock` must be real: a symbolic link at either name would put the
run folder or the lock outside the album, so run, strip, undo, check and prune refuse it
with exit `2` before anything is created or read. A run-id folder under the run folder
that is a link is not a run: it is never listed, never chosen
as the newest, and `--run-id` naming it finds no run.

A kernel lock on a network-mounted library may be less dependable than on a local disk. Some
network file systems emulate it, or do not enforce it between machines, so two machines
running the tool on one shared album at the same time are not kept apart as well as two runs
on one machine are.

## The config file

A Genius key is looked for in three places, and the first one that holds a key wins:

1. **`--config <file>`**, when given. It is then the only place a key is read: a file
   that is missing, unreadable, not valid TOML, holding none of the three tables (a
   `[genius]` key, a `[no_lyrics]` table and a `[musicbrainz]` table), or holding a
   malformed `[no_lyrics]` table or an unusable `[musicbrainz] contact` is exit `2`,
   never a fall back to some other key nobody named. A file holding only `[no_lyrics]`
   or `[musicbrainz]` names no key, so Genius is `absent` and the environment's key is
   not used.
2. **The `GENIUS_ACCESS_TOKEN` environment variable**, the Genius library's own
   convention. A blank value counts as unset.
3. **The default file**: `$XDG_CONFIG_HOME/lyricsmith/config.toml` when `XDG_CONFIG_HOME`
   holds an absolute path, else `$HOME/.config/lyricsmith/config.toml`. A relative
   `XDG_CONFIG_HOME` is ignored, as the XDG
   convention says, so the answer never depends on the working directory. A default file
   that exists but cannot be used is treated as no key, with the reason on stderr: nobody
   named it, so it does not stop the run. A default file holding only `[no_lyrics]` is no
   key with no warning; the notice that no key was found still prints.

So the environment variable overrides the default file, and `--config` overrides both.

The file is TOML holding any of a `[genius]` table, a `[no_lyrics]` table (*Stand-up and
spoken word*) and a `[musicbrainz]` table (below). The key is the `access_token` string of the `[genius]` table:

```toml
[genius]
access_token = "<your Genius client access token>"
```

Whitespace around the key is trimmed, and a blank string is no key. A file that does not
parse is reported by its path and the error's type alone, never the line, which could
hold the key.

**The genre list** comes from `--config` when given, else from the default file, and
never from the environment: `GENIUS_ACCESS_TOKEN` carries the key alone. It is read under
`--offline` and `--no-genius` as well, where no key is looked for, so an unusable
`--config` is exit `2` under them too. A malformed `[no_lyrics]` table in the default
file draws a warning on stderr, prefixed `no_lyrics:`, and the built-in list is used.

**The MusicBrainz contact** is optional. MusicBrainz asks each client to name a way to
reach its operator in its `User-Agent`; without one, the Bandcamp step's MusicBrainz
requests send the tool's name and version alone, as every other request does. To add one:

```toml
[musicbrainz]
contact = "https://example.com/lyricsmith-contact"
```

It is trimmed, and must then be 1 to 200 printable ASCII characters with no `(` or `)`,
since it is written inside the `User-Agent`'s parentheses. It comes from `--config` when
given, else from the default file, never from the environment, and is read on every run,
a dry run and `--offline` included, and by compare, so an unusable one in a `--config`
file is exit `2` there too. An unusable one in the default file draws a warning on
stderr, prefixed `musicbrainz:`, never quoting it, and no contact is sent. It goes only
into MusicBrainz's `User-Agent`: never to Bandcamp, YouTube Music or Tekstowo, and never
into the manifest, the summary, a notice or an error message.

**A default file that cannot be used is read twice, once for each.** One that is
unreadable or not valid TOML draws its warning from both readers: prefixed `genius:` when
the key is looked for and no environment key is set, and prefixed `no_lyrics:` on every
run. With `GENIUS_ACCESS_TOKEN` set, the key is never looked for in the default file but
the list still is, so a broken default file draws the `no_lyrics:` warning where it used
to draw none.

The tool never writes the file and never creates its folder. Create both yourself, then
make the file readable by you alone:

```bash
mkdir -p "$HOME/.config/lyricsmith"
# write config.toml there with an editor, then:
chmod 600 "$HOME/.config/lyricsmith/config.toml"
```

**Permissions.** On a POSIX system, a key file readable by its group or by others draws
one warning on stderr, naming the file's mode and the `chmod 600` that fixes it. The key
is still used. This holds for a `--config` file and the default file alike.

**When no key resolves,** the Genius step is skipped with one notice on stderr naming
the default file it looked for, and the summary's counts line ends `genius: absent`. The
other values there are `configured`, `skipped` under `--no-genius` and `offline` under
`--offline`; with either option no key is looked for at all.

The key is never printed, logged or written. It is held in a wrapper whose text form
hides it, only the Genius client's constructor receives the raw value, and the manifest
records only whether the run had one: `"genius": "configured"` or `"absent"`.

### Checking a Genius key by hand

No automated test reaches Genius, since it would need a key. This one line checks a key
from the config file against one song, and prints `True` when Genius accepts the key and
finds the song:

```bash
python -c 'import sys, tomllib, lyricsgenius; key = tomllib.load(open(sys.argv[1], "rb"))["genius"]["access_token"]; print(lyricsgenius.Genius(key).search_song(sys.argv[2], sys.argv[3]) is not None)' "$HOME/.config/lyricsmith/config.toml" "<title>" "<artist>"
```

Run it with a Python 3.12 or later that has the project's dependencies
(`[project].dependencies`) installed, named `python` here because the system `python3` on
some machines is older and has no `tomllib`. The key is read inside the process from the
config file, never passed on the command line.

`False` means the key was accepted but no lyrics came back: either no song matched, or
the song's lyrics page yielded no text; try a well-known song. A refused key raises an
`AssertionError` naming the status (`Unexpected response status code: 401`) rather than
an HTTP error. The line prints neither the key nor any lyrics.

## The backup and its manifest

Every run keeps its backup in a hidden folder inside the album:

```text
<album>/.lyricsmith/
  lock                                        the album lock; stays between runs
  <run-id>/                                   one folder per run
    manifest.json                             the index check and undo read
    frames/<track-rel>.sylt.<k>.lrc           each SYLT frame, as LRC with millisecond stamps
    frames/<track-rel>.uslt.<k>.txt           each USLT frame's text, as stored
    frames/<track-rel>.<kind>.<k>.frames.json a frame whose LRC or text export cannot carry it
    sidecars/<track-rel>.lrc                  a copy of each sidecar file beside the MP3 before the run
    sidecars/<track-rel>.txt                  (a link is recorded in the manifest, not copied)
    undo/<track-rel-dir>/<name>               sidecars an undo set aside rather than delete
```

- **The run id** is the run's start time in UTC, `YYYYMMDDTHHMMSSZ`, with `-2`, `-3` and
  so on when a run in the same second already took that folder. The folder is created
  exclusively, so two runs never share one, and runs sort by start time, then by that
  suffix as a number. A folder with no manifest is a backup that never finished, which
  changed nothing, so check and undo do not count it as a run.
- **The backup mirrors the album.** `<track-rel>` is the track's path inside the album
  with `.mp3` removed, so `CD1/01 Song.mp3` backs up to `frames/CD1/01 Song.sylt.1.lrc`
  and a person can find a track's lyrics by eye. `<kind>` is `sylt` or `uslt`, and
  `<k>` numbers a track's frames of one kind from `1`, in stored order. Two tracks
  whose names differ only by suffix case (`Song.mp3` and `Song.MP3`) would share these
  names, so the second one's backup fails, as that track's error.
- **Every name fits in 255 bytes.** A file name may take 255 bytes of UTF-8, and a track
  may be named in nearly all of them. When a track's name without `.mp3` would leave
  less than 32 bytes for the endings above, the backup keeps as much of its head as
  fits, then `~` and the first 8 hex digits of the whole name's SHA-256, so
  `frames/<head>~<hash>.uslt.1.txt`; two long names sharing a head still back up apart.
  Undo and check find every file by the name the manifest records, so runs written
  before this rule read as they always did.
- **Each export is proven.** After writing a frame's `.lrc` or `.txt`, the backup reads
  it back and compares it with the frame. An LRC cannot carry every SYLT frame: one
  timed in MPEG frames rather than milliseconds, entries stored out of time order, an
  entry holding a line break or leading or trailing spaces, or one opening with a
  bracket that would read back as a stamp. A USLT text holding what UTF-8 cannot encode
  fails the same way. Wherever the two differ, the frame is also written verbatim as
  `frames/<track-rel>.<kind>.<k>.frames.json`, and the manifest names it as that frame's
  `exact` copy. A stamp past `99:59.999` needs no exact copy: the LRC reader takes
  minutes of any width. So the common case stays readable, the edge case stays lossless,
  and nothing is ever restored from an export that was not proven.
- **Every file is written whole.** Each backup file and each manifest is written to a
  temporary file beside it, synced, renamed into place and read back, so a reader sees
  the old file or the new one, never half of one.
- **The backup comes first.** Every track is backed up and the manifest written, with
  `state` `backed_up`, before any MP3 or sidecar changes. A track whose backup fails (an
  unreadable tag, an I/O error) is recorded as that track's `backup` error with no
  `before` block, and the run never writes it: continue-on-error never reaches a track
  with nothing to undo from. The other tracks go on.
- **A linked sidecar is backed up as a link.** A sidecar name holding a symbolic link,
  whether it leads to a file, to a folder or nowhere, is recorded by the link's target
  and nothing is copied; undo recreates the link. The file it points at is never read
  for the backup and never changed. A folder, a pipe or a device under a sidecar name
  is not recorded at all.

### `manifest.json`

The manifest holds only paths inside the album, and `album` is always `"."`, so moving
or renaming the album folder does not break undo. Its top level records the `schema`
(`1`; any other value is refused, exit `2`), the `run_id`, the `lang` written into new
frames, the run's `state`, `genius` (`configured` or `absent`), `bandcamp`, `youtube_music`,
`tekstowo`, `silence_check` and the `tracks`. `state` moves from `backed_up` to `complete`
when the run finishes, and to `undone` once every track of the run is undone. A run killed
part-way stays `backed_up`, which undo honors.

`bandcamp` is `on` or `off`, whether the run asked Bandcamp. `youtube_music` is `on`,
`off`, `cooling down` (a cool-down held as the run began) or `stopped` (a refusal stopped
it during the run); each manifest write reads it afresh, so the write after the track
that stopped it already says `stopped`. `tekstowo` reads the same four ways, for
Tekstowo (*Tekstowo.pl*). A strip, which asks no provider, records all three `off`. All
three are optional when read: a manifest without them, as every run made before they
existed has, or with `null`, reads `off`, which is true of those runs, so the schema
stays `1` and every older run still checks and undoes.

`silence_check` is `on` or `off`, whether the run found `ffmpeg` and so measured the tracks
it missed (*Silent tracks*). It is optional when read in the same way: a manifest without
it reads `off`, which is true of every run made before it existed.

Each track records:

| Field | What it holds |
| --- | --- |
| `path` | The MP3's path inside the album. |
| `id3_version` | The ID3v2 version the file had before the run, as `[2, 4]`, or `null` when it had no ID3v2 header. |
| `before` | What undo restores: `frames`, each frame's kind, language, description, encoding, SYLT format and type, its export `file` and any `exact` copy; and `sidecars`, each sidecar's `name` and either its copy's `file` or, for a symbolic link, its `link` target, the other `null` (either left out reads as `null`, and an entry with neither is refused); an entry with no `link` field, as a manifest written before links were recorded has, is a file copy. `null` when the track's backup failed. |
| `after` | What the run did, filled in as the track finishes: `status` (`written`, `unchanged`, `not found`, `no lyrics expected`, `silent`, `kept existing`, `removed`, `stripped` or `error`; a manifest written before `unchanged` existed reads as it always did), `source` (a provider's name, `sidecar`, `frames`, `machine transcript` or `machine timed`), `kind` (`synced` or `unsynced`), `query`, `sidecar_written`, `sidecars_removed`, `sidecars_renamed`, `sidecar_hashes`, `frames_written`, `check`, `error` and `sidecar_links_ignored`, each sidecar link left unread because it leads out of the album (left out, as a manifest written before it existed has, it reads as none), and `refusals`, each provider call that refused or failed instead of answering, in the order asked, as its `provider`, its `step` (`synced` or `unsynced`, the kind it was asked for) and a short `detail` such as `HTTP 503` or `timeout`, one line of at most 80 characters, never a reply body (left out, it reads as none), then `refetch_untimed`, `sidecar_unreadable`, `implausible` and `refetch_implausible` (below), and the silence check's `peak_dbfs`, a number set on a `silent` track alone, and `silence_failed`, a string naming why a missed track's measurement failed (both left out, they read as none). `null` until then. |
| `undone` | Whether an undo has put the track back. |
| `displaced` | Every copy an undo of this track set aside in `undo/`, by its path inside the album, an undo that then failed included. |

A manifest naming the source `machine transcript` or `machine timed` cannot be read by a release from before
it; undo and check a run with the release that made it.

A strip records `stripped`, with `frames_written` true when it removed lyrics frames, since
saving the track without them is a write of its frames, and every sidecar name it removed
in `sidecars_removed`. A fresh run lists the sidecars its strip removed there too, beside
any its write replaced. An `error` records the stage it happened in, as the summary's
`ERROR <stage>` row names it: `strip` for a strip that failed, in a strip or a fresh run.
A fresh run that declined an answer records `kept existing` with the declined answer's
`source` and `kind`, `frames_written` false, no sidecar written or removed, and as its
`check` each FLAG that declined it, `unlike backup`, `untimed` or both; only a `written`
or `unchanged` track's `check` judges what the track holds. A track served from its own
frames records `written` with source `frames` and `frames_written` false, since its MP3
was not saved. A track for which nothing changed records `unchanged`, with its `source`,
`kind` and `check`, `frames_written` false, and no sidecar written, removed, renamed or
hashed. No field is added for any of them. A refetch adds three `after` fields:
`refetch_untimed`, true on each track it asked again as untimed only, whatever its
outcome; `sidecar_unreadable`, why that track's old `.txt` could not be read, or `null`;
and `implausible`, why the old lyrics it would remove had nothing answered were too
implausible to keep, its sidecar, its frames or both, each judged alone, whether or not
something did, or `null`. Any run sets `implausible` to `placeholder lyrics` on a
track whose only lyrics are placeholders, with `refetch_untimed` left false
(*Placeholder lyrics*). A `--refetch-implausible` run adds a fourth,
`refetch_implausible`, true on each track it asked again because its serving lyrics were
implausible, whatever its outcome, with `implausible` saying why and `refetch_untimed`
false; a track the `--refetch-untimed` side of a run with both asked is marked that way
instead. A track the
refetch kept records `kept existing` with no source or kind, and one whose implausible
lyrics a run removed records `removed`, with `frames_written` true when it removed lyrics
frames and every sidecar name it removed in `sidecars_removed`, as a strip records them,
so undo restores them as it restores a strip's. A manifest written before the four
existed lacks them, and reads as asking no track again and judging no lyrics
implausible, so the schema stays `1`.

`sidecar_hashes` pairs each sidecar the run wrote or renamed into place with the SHA-256
of its bytes, in lowercase hex. It is undo's proof that a sidecar is still the run's own.

### How undo uses it

Undo decides what a track should hold from `before` alone, so a run killed before it
recorded `after` is undone the same way. It reads `after` only for `sidecar_hashes`, to
tell a sidecar the run wrote from one edited since.

- **Frames.** Every SYLT and USLT frame is removed, and each `before` frame is rebuilt
  from its `exact` copy when it has one, else from its export, with every recorded
  field, then saved at the recorded ID3 version and read back to confirm. A track whose
  frames already match the backup is not saved at all. A track with no `before` frame
  ends with none.
- **A track that had no ID3v2 header** before the run keeps the v2.4 tag the run's write
  gave it: undo removes its lyrics frames and leaves the tag, with the ID3v1 artist and
  title the write carried into it and any frame another tool has added since. Returning
  the file to header-less would mean deleting frames undo cannot prove the run wrote.
  The ID3v1 tail stays byte-identical.
- **Sidecars, by name.** Each `before` sidecar is put back under its own name, a file
  copy as its bytes and a link as that link, and a file or link under either of the
  track's two sidecar names that the backup did not find is removed, each under the
  displacement rule in *The modes* above. `sidecar_hashes` only decides whether a file
  goes without a copy. A link is put back the way a file is written: made under a
  temporary name beside the sidecar, renamed over it, then read back.
- **A version that has changed since** is that track's error, with the file untouched:
  a tag rewritten at another ID3 version than the one recorded is not restored over.
  A track deleted or renamed since the run is that track's error too; the other tracks
  are still undone.
- **Every read stays inside the run folder.** Each file undo reads back from it, the
  manifest included, must lie inside it once every link is followed: a linked frames or
  sidecars folder, a linked file or a link loop is that track's undo error, and
  such a manifest is refused as malformed. A manifest over 16 MiB is refused unread.
- **Every read is capped.** A sidecar beside a track that undo would copy or check, and a
  sidecar copy in the run folder, are read under the lyrics cap a run reads a sidecar
  under; a frame's export or exact copy, under the manifest's 16 MiB. A file past its cap
  is that track's undo error (`too large`), read no further than the cap and never copied
  or written back; the other tracks are still undone.
- **Every displaced copy stays inside the run folder.** Its `undo/` folder, and each
  track's folder under it, must lie inside it once every link is followed, checked before
  the folder is made and again before a copy is written. One linked out of it, a dangling
  link included, is the error of each track needing a copy there, its sidecar left in
  place and no file written outside; the other tracks are still undone. One race is
  accepted: a link put in the run folder while undo runs, which needs write access to it
  during the run, can let undo make empty folders outside before the second check
  refuses the track, and a link put there between that check and the copy's write would
  receive the copy. The second check narrows the window to that write; it does not close
  it.
- **The run folder is kept** after an undo, its manifest marked, and so are the sidecars
  an undo displaced into its `undo/` folder. `--mode prune` removes a run folder once nothing
  needs it; a run not fully undone, or holding a sidecar undo set aside, is kept.

## The check and its thresholds

After each track's lyrics are accepted, the check reads the lyrics frames back from the
file, the authoritative state rather than what was meant to be written, and compares them
with the track's duration and with the lyrics frames the backup holds. When no backed-up
frame holds a word, the old lyrics were in a sidecar alone, and the check compares with
the backup's copy of it instead, chosen in the sidecar stage's order: the `.lrc` copy
when it holds words and no time stamp, nothing when it is timed or blank, and the `.txt`
copy only beside no `.lrc` copy or one of time stamps alone; never a sidecar recorded as
a link. The copy is read as the sidecar stage reads a sidecar, so one that is not UTF-8
or holds more than the lyrics cap makes the `unlike backup` row a `SKIP` naming why
(`SKIP: unlike backup (backed-up <name> is not UTF-8: ...)`), never a FLAG. A track a
run served from its own sidecar is compared with that same file, so it passes. A fresh
run's own guard still compares with the frames alone (*The modes*). It changes nothing,
and it runs only on tracks whose lyrics this run accepted: those it embedded, those
served from their own frames, and those it left `unchanged`, which hold what the run
would have written or frames kept because they serve the track. For a track it embedded, the check
judges the frame `--lang` ranks first, which is the one the run wrote. For a track
served from its own frames, it judges the frame that served, against that same frame
in the backup, even when `--lang` ranks another first: a track holding a French and a
German `SYLT`, served under `--lang deu` from the French one because its `.lrc` holds
it, is judged by the French frame, so a flaw in the German one is no FLAG, and one in
the French one is. Only when no frame holds those lyrics any more, changed since they
were read, is the `--lang` frame judged instead. `--mode check` does not know which
frame served, so for a track the run served from its frames it asks the run's question
again: the frame a run would serve the track from, given the sidecar beside it now.
Usually the sidecar the run wrote or found holds that frame's lyrics, so the re-check
judges the frame the run did. Not always: a run writes no `.lrc` that could not hold the
frame's lines whole, and a `.txt` of untimed lyrics beside synced frames names no synced
frame; in both cases the run and the re-check agree only because both fall back to the
`--lang` ranking. A sidecar removed or edited since the run makes `--mode check` judge
the frame a run would serve now. In the run summary an `unchanged` track's check column
reads `unchanged (<verdict>)`, such as `unchanged (PASS)`, and any other track it did not
write shows its status (`not found`, `no lyrics expected`, `kept existing`, `removed:
...`) or its `ERROR <stage>: <message>`; a marked `unchanged` or `kept existing` row closes
with `(FLAG: lyrics on a no-lyrics genre (<genre>))` (*Stand-up and spoken word*). In
`--mode check`, a track the run neither wrote nor left unchanged, or one undone since,
shows `-` and counts as not checked, and one whose check could not run shows its `ERROR`
row. Under `--fresh` the check also judges each answer before it is accepted, and an
`unlike backup` FLAG there declines it, as does an untimed answer for a track whose lyrics
were synced, recorded as an `untimed` FLAG that is no row of the table below (*The
modes*); the run's `flagged` count holds written and unchanged tracks, and beyond them
only a track carrying the `lyrics on a no-lyrics genre` flag.

| Row | Applies to | FLAG when |
| --- | --- | --- |
| `past end` | Timed lyrics | The latest line starts more than `PAST_END_TOLERANCE_S` (2.0 s) after the audio ends. Every run declines a provider's timed answer this row would flag, and `--refetch-implausible` asks again a track whose serving synced lyrics it would flag (*The modes*). |
| `ends short` | Timed lyrics | The latest line starts before `SHORT_FRACTION` (0.5) of the track **and** more than `SHORT_MIN_GAP_S` (60.0 s) before its end. |
| `out of order` | Timed lyrics | A line starts earlier than the one stored before it. |
| `too many words` | Timed and untimed lyrics | The words over the track's duration exceed `MAX_WORDS_PER_SECOND` (6.0) a second. Every run declines a provider's answer this row would flag. A `--refetch-untimed` run judges a track's old untimed lyrics by this row too, when no provider answers it, and removes them when it would flag; `--refetch-implausible` asks again a track whose serving lyrics it would flag (*The modes*). |
| `unlike backup` | Any track whose backup held lyrics: its frames, or, with no worded frame, its untimed `.lrc`, else its `.txt` | The word similarity to the backed-up lyrics is below `MIN_SIMILARITY` (0.6). |
| `no lyrics embedded` | Any track the run wrote | The file holds no lyrics frame any more. |
| `placeholder lyrics` | Timed and untimed lyrics | The judged frame holds placeholder lyrics, not lyrics (*Placeholder lyrics*): `FLAG: placeholder lyrics (run the album again to replace it)`. A run no longer accepts one, so this finds a track an earlier run wrote one into; running the album again asks the providers afresh and replaces it, or removes it. It comes first among the FLAGs. |
| `lyrics on a no-lyrics genre` | Any marked track that ends holding lyrics | Its genre equals an entry of the list; recorded by the run, not judged by `--mode check` (*Stand-up and spoken word*). |

Why these numbers:

- **`PAST_END_TOLERANCE_S = 2.0`** absorbs encoder padding and the duration's rounding,
  while nothing legitimate is sung after the audio stops.
- **`SHORT_FRACTION = 0.5` and `SHORT_MIN_GAP_S = 60.0`** must both hold, so a long
  instrumental outro (the last line at 60 to 70 percent of a seven-minute track) is not
  flagged, while the lyrics of a different, shorter song are.
- **`MIN_SIMILARITY = 0.6`** sits between two transcriptions of one song, which differ
  in punctuation, contractions and repeat-marking and score well above it, and two
  different songs, which share only common words and score below it.
- **`MAX_WORDS_PER_SECOND = 6.0`** sits above any sung song: a fast rap verse sustains
  about 4 to 5 words a second, and a whole song averages 1 to 2, while a web page
  saved as lyrics, or another album's lyrics in one file, runs to dozens.
- **`FAR_PAST_END_FACTOR = 1.5`** is the fetch's, not a row's: a timed answer declined as
  `past end` whose latest line starts beyond one and a half times the track's length is a
  whole other song on a short track, so its plain text is declined too (*How a track's
  lyrics are found*), while one only a little past the end may be a longer take of the
  right song, whose words still fit.

All of these are initial estimates, to be re-tuned against real runs; the summary prints the
measured values beside every FLAG, so re-tuning has data. "Latest line" means the latest
time stamp, not the last one stored, so a disordered frame cannot hide a line past the end
behind an earlier one. The timed rows read a SYLT frame in milliseconds; lyrics with only a
USLT frame get the word-rate and similarity rows alone.

**How similarity is measured.** Both texts are casefolded, time stamps, tags and bracketed
section headers such as `[Chorus]` are removed, apostrophes are dropped so a contraction
matches its spelling without one, other punctuation becomes a space, and the words are
compared in order with the standard library's `difflib.SequenceMatcher`, its junk
heuristic off, since a chorus repeats exactly the words that make two transcriptions
alike. Only the opening 2,000 words of each text are compared (`MAX_COMPARED_WORDS`),
far more than a song holds: with the heuristic off, the comparison's cost grows with the
square of a text's repeated words, so two texts differing only past their first 2,000
words read as alike. The FLAG shows the similarity truncated to three places, never rounded up to the
threshold. A backup with no words is not compared. The `too many words` row counts the
words the same way, over the duration's seconds, and its FLAG shows the words per
second truncated to three places, with the word count and the duration beside it:
`too many words (14.205 words per second: 2557 words in 180.000 s)`.

**A duration that cannot be read** makes the two duration rows and the `too many words`
row one `SKIP`, never a FLAG: `duration unreadable (timing and too-many-words rows not
checked)` for timed lyrics, `duration unreadable (too-many-words row not checked)` for
untimed ones. The order row still runs. Each track's check is `PASS`,
`FLAG: <rows with values>` or `SKIP: <reason>`. The manifest's `after.check` records
the findings rather than that text: `{"flags": [...], "skips": [...]}`, each entry a
`rule` and its `detail`, so a `PASS` is two empty lists. A FLAG or SKIP never changes
the exit code; a check that cannot run is that track's `ERROR` row, and the exit code is
`1`. `--mode check` re-runs exactly this on a finished run, so lyrics edited by hand
can be checked again.

## Comparing with the providers

`--mode compare` (*The modes*) prints one row per track: its file, its verdict, each asked
provider's score, and, in the notes at the row's end, a `source unrecorded` mark and the
hints. Here on an invented album:

```text
album compare: 5 tracks under invented_album

 #  track                  verdict                              scores
 1  01 Paper Lanterns.mp3  agrees                               LRCLIB own · NetEase - · Bandcamp - · YouTube Music 0.905 · Genius 0.881 · Tekstowo -
 2  02 The Long Bit.mp3    FLAG: no source agrees (best 0.214)  LRCLIB 0.214 · NetEase - · Bandcamp - · YouTube Music 0.201 · Genius 0.188 · Tekstowo -  source unrecorded; few words (0.040 words per second: 24 words in 600.000 s); no title word
 3  03 Harbor Song.mp3     no second source                     LRCLIB own · NetEase ERROR · Bandcamp - · YouTube Music - · Genius - · Tekstowo -
 4  04 Morning Train.mp3   -                                    -
 5  05 Old Quay.mp3        agrees                               LRCLIB 1.000 · NetEase - · Bandcamp - · YouTube Music - · Genius 0.041 · Tekstowo -  source unrecorded
strip #2: album.py --album /srv/music/invented_album --mode strip --track '/srv/music/invented_album/02 The Long Bit.mp3'
agrees 2 · flagged 1 · no second source 1 · errors 0 · not judged 1 · source unrecorded 2 · genius: configured
bandcamp: on
youtube music: on
tekstowo: on
providers: LRCLIB 1/2 (2 own) · NetEase 0/4 (1 refused) · Bandcamp 0/4 · YouTube Music 1/4 · Genius 1/4 · Tekstowo 0/4
```

Each answer is scored against the judged text with the check's own word similarity
(*How similarity is measured*, above), so one number, `MIN_SIMILARITY` (0.6), means
"another song" in the check and here alike, and re-tuning it moves both.

| Verdict | When |
| --- | --- |
| `agrees` | Any one provider other than the track's own source scores at or above `MIN_SIMILARITY`. |
| `FLAG: no source agrees (best <s>)` | Some provider other than the track's own source answered, and every such answer scores below it; `<s>` is the best of them. |
| `no second source` | No provider other than the track's own source answered, and at least one was asked without failing. Not a FLAG: nothing contradicts the lyrics. |
| `no second source (no provider asked)` | No provider could be asked: a track searched by its file name is asked of Genius alone, and Genius was not held; or the only provider held is the track's own source. |
| `ERROR fetch: every provider failed (<counts>)` | Every provider asked failed, by an error or a timeout, each way counted. |
| `-` | Not judged: the track is synced, or holds no lyrics. |

**The track's own source.** Which provider wrote a track's lyrics is learned from the
album's run manifests, not asked of the provider. The runs are read newest first, and for
each track the first entry that decides it wins:

| Entry for the track | Decides |
| --- | --- |
| Absent from the manifest, undone, or with no outcome | Nothing: the next older run is read. |
| `written` or `unchanged`, from LRCLIB, NetEase, Bandcamp, YouTube Music, Genius, Tekstowo or the legacy Musixmatch | The source is that provider. |
| `stripped` or `removed` | The source is unrecorded: whatever the track holds now came later, from outside the tool. |
| Anything else: kept existing, not found, no lyrics expected, an error, or a write served from the track's own sidecar, frames or a machine-labeled `.lrc` | Nothing: the run left the lyrics it found, so the next older run is read. |

A manifest that cannot be read (malformed, an unknown schema, oversized, reached through a
link) is passed over like a run that never named the track. A track no run decides is
`source unrecorded`: its lyrics predate the tool or came from outside it.

A source among the providers compare asks is left out of the questions and its answer is
set aside: it neither agrees nor flags, because asked again it returns the very lyrics it
wrote. A track agrees only with a different provider, and reads `no second source` when no
other answered. A source that is the legacy Musixmatch sets nothing aside: every provider
is a second source, and the row carries no mark. A track whose source is unrecorded is
judged with every provider counting, as before, and its row carries `source unrecorded`,
agreeing ones included.

The scores column names each provider in the order of the chain's untimed steps: its
similarity, truncated to three places as the check's are and never rounded up to the
threshold; `-` when it answered nothing; `ERROR` when its call failed; `own` for the
track's own source, which was not asked (`LRCLIB own`). A provider not asked for the track
(Genius without a key or under `--no-genius`, NetEase under `--no-netease`, Bandcamp
under `--no-bandcamp`, YouTube
Music under `--no-youtube-music` or while its cool-down holds, Tekstowo under
`--no-tekstowo` or while its cool-down holds, and LRCLIB, NetEase, Bandcamp, YouTube
Music and Tekstowo for a query by file name) is left out, and a track no provider was
asked for shows `-`. The source's `own` cell is the exception: it shows even when that
provider is not held (no Genius key, `--no-genius`, `--no-netease`, `--no-bandcamp`,
`--no-youtube-music`, `--no-tekstowo`)
or would not be asked for the query, because it records where the lyrics came from; its
tally then reads `-` with no own count. In the notes, `source unrecorded` comes first and
then the hints, joined by a semicolon and a space.

**The hints.** A track that does not agree, flagged or with no second source, gets two weak
hints in its notes, never a FLAG on their own; an agreeing row and an error row get none.
The `source unrecorded` mark is not a hint, since it qualifies the verdict and not the text:
it prints on every judged row with no recorded source, whatever the verdict.

- `few words (<w> words per second: <n> words in <d> s)` when the judged text holds fewer
  words a second over the track's duration than `MIN_WORDS_PER_SECOND` (0.1). The words are
  counted as the check's `too many words` row counts them, the rate is truncated to three
  places and the duration shown to three. A duration that cannot be read gives no hint.
- `no title word` when no word of the track's title, of three letters or more, is among the
  judged text's words. Both are normalized as the check normalizes words, and the title
  loses its trailing bracketed groups first, as the stripped-title retry's does. Three
  letters means a word such as `the` counts, while `a`, `of` and `me` do not; a title with
  no such word gives no hint.

**Why `MIN_WORDS_PER_SECOND = 0.1`.** A whole sung song averages 1 to 2 words a second
(*The check and its thresholds*), so a tenth of a word a second is far below any of them: a
three-minute song under 18 words, or a short song's lyrics on a long stand-up track. A
sparse song can still fall under it, which is why it is a hint and not a verdict.

**After the table, for each FLAG row**, comes that track's one-track strip command,
numbered by its row, ready to copy:
`strip #<n>: <prog> --album <album> --mode strip --track <path>`. `<prog>` is the name the
tool was run by, `<album>` the album's path as resolved, and `<path>` the track's full path;
each is shell-quoted where it needs to be, so a name with a space or a quote pastes intact.
Then a counts line, `agrees <a> · flagged <f> · no second source <n> · errors <e> · not
judged <j> · source unrecorded <u> · genius: <state>`, `<u>` being the judged tracks whose
source is unrecorded and the state `configured`, `absent` or `skipped` as in a run, the
`bandcamp:`, `youtube music:` and `tekstowo:` lines a run prints (*What goes to stdout and
stderr*), and the `providers:` tally line. Here a provider's count is the tracks whose
answer agreed, over the tracks it was asked for, with the tracks set aside as its own and
the calls that failed in brackets (`LRCLIB 1/3 (2 own, 1 refused)`); a track set aside is
in neither number, and an error row is never counted as set aside. `-` marks a provider
compare did not hold.

**The intended loop**, for a track whose lyrics are wrong:

1. `--mode compare` flags the track and prints its strip line.
2. That line, `--mode strip --track <file>`, clears the one track, backed up as any strip.
3. `--mode run --track <file>` fetches it again; a marked stand-up track with nothing found
   then reads `no lyrics expected` (*Stand-up and spoken word*).
4. `--mode check` judges the new answer: the one-track run is the newest.
5. `--mode undo` takes back the run, and `--mode undo --run-id <strip-id>` the strip, with
   no warning: the run's change to that track is already undone (*The modes*).

## After a run

**Music.app does not re-read embedded lyrics until the track is refreshed.** It keeps its
own copy of each track's tags, so lyrics a run embeds, or an undo removes, may not show
there until the track is refreshed in Music.app. The tool does not do that step: it can name
the tracks to refresh, with `--changed-list`, for a tool that does.

## The changed list

`--changed-list <file>` writes the absolute path of every MP3 whose file a run, a strip or an
undo saved, each once, in the order the tracks were processed. It is meant for a library
app or script that rereads those files' tags, so that a music player shows the lyrics a
run wrote; the tool itself never calls one, so it runs the same on any machine. The
format is UTF-8, one absolute path per line, each line ended by a linefeed, so a file
whose writer stopped part-way is told apart from a whole one; the tool writes only lines
that format can hold (below).

- **What is listed.** A track whose lyrics frames were written or removed, a `--fresh` or
  `--refetch-untimed` strip among them, and, for an undo, a track whose frames were restored
  or removed. A track whose sidecar alone changed is not: a player rereads tags, not
  sidecars. A track whose embed, strip or undo failed after its MP3 may have been saved (a
  save whose read-back then failed, say) is listed too: refreshing an unchanged track only
  rereads its tags, and missing a changed one would leave the player showing old lyrics.
  Each path is under the album folder as the tool resolved it (a linked album folder
  is followed once, as every mode does); the track's own path is never resolved.
- **When it is written.** Once the tracks are done, before the report is printed, and the same
  way when Ctrl-C stops a run or an undo part-way, so it names every track saved before the
  interrupt. It replaces the file atomically, with Ctrl-C held until it has; a Ctrl-C while the
  list is being chosen or written still leaves it written and the report printed, and the exit
  code is then `130`. A run that saved nothing writes an empty file. A run that could not start
  (exit `2` before its report: the lock held, no MP3, a bad option) writes none, and leaves an
  existing file as it was.
- **A path the list cannot hold.** A path holding a linefeed or a NUL, one ending in a
  carriage return, or one that is not UTF-8 (a name decoded from other bytes) cannot be read
  back from the format, so it is left out, and one stderr line names it, escaped:
  `--changed-list: left out '<path>': <why>`. That does not change the exit code: the lyrics
  work succeeded. Refresh such a track by hand.
- **A list that cannot be written** is reported on stderr after the report, and the exit code
  is `3`, as for a record that could not be written: the lyrics work was done.

## Samples

`samples/build_sample_album.py` builds an album of silent MP3s with invented tags, some
of them stand-up, one invented timed `.lrc` sidecar and one invented untimed `.txt`
sidecar, and `samples/README.md` walks through a dry run, an offline run, the check and
the undo on it, then a strip and its undo, then a one-track strip, a one-track dry run and
that strip's undo, with each report verbatim. The run needs no network and no key. It is
shown first with `ffmpeg` found: the timed sidecar's track embeds synced lyrics and
passes the check, the tracks nothing answered report `silent` (the stand-up one among
them, since silence is judged before the genre), and the stand-up track with a `.txt` is
embedded and flagged `lyrics on a no-lyrics genre`. A *Without ffmpeg* section then shows
the same run with no measurement: those tracks report `not found`, and the stand-up one
`no lyrics expected`. Each undo returns the album to its built state, byte for byte.
`--fresh` and `--mode compare` need the providers, so the walkthrough names them without
running them; it gives compare's command for the acceptance run, by hand on a real album.
