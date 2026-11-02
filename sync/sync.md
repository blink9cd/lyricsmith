# sync.py

Times a track's untimed lyrics against its own audio, offline, into a labeled `.lrc`. Many
tracks end an album run with untimed lyrics only, because several providers give no timing.
This tool takes the words already in the track's `.txt`, separates the vocals with htdemucs,
checks that those words are sung there by transcribing the vocals with whisper.cpp, scores
them with torchaudio's English wav2vec2 model, and force-aligns the whole text in one pass,
so each line gets the start of its own first letter. A long instrumental break, found
from the vocal stem's level against the rest of the band, is closed to every letter and the
text aligned again. Every `.lrc` it writes opens with
two label lines naming the tool and both models, so a machine-timed file is never mistaken for
a hand-made or provider one, and `--overwrite` re-times only the files the tool wrote itself.

It works on English only: another language comes out low-confidence and gets no file.

**Why the words are checked first.** Forced alignment scores how well the given text fits
where it placed it, never whether that text is sung at all: over a loud mix it finds a
plausible path for any words, so another song's lyrics, as a provider serving each page
shifted by one track gives, were timed with confidence. So each track's vocal stem is
transcribed with whisper.cpp before anything is aligned. A text the check finds not sung
is `not sung: left untimed` and gets no file; a track with too few word pairs heard to judge
is timed as before, and listed `little heard` (*The sung check* below).

## Prerequisites

1. **The sync install**, into the interpreter that runs lyricsmith:
   `pip install -r requirements-sync.txt`, from this folder. It pins PyTorch, torchaudio,
   demucs and everything they pull in, each exactly. The stack is pinned there rather than
   among lyricsmith's dependencies, so installing the other tools never pulls PyTorch in.
2. **ffmpeg** on `PATH` (`brew install ffmpeg`), which decodes each track.
3. **The three models, once:** `python sync.py --mode fetch-models`.
4. **whisper.cpp's `whisper-cli`** on `PATH` (`brew install whisper.cpp`), found as the
   transcribe tool finds it and made absolute, and **the transcribe tool's two whisper
   models**, fetched once with `lyricsmith transcribe --mode fetch-models`
   (`../transcribe/transcribe.md`) into that tool's model folder, the one this tool's own
   `sync` folder sits in. `--whisper-model-dir` reads them from elsewhere; they are checked
   against the transcribe tool's manifest, by size and digest, before any track is touched.
   That check hashes the 1.6 GB whisper model on every run, which takes a few seconds.

`fetch-models` downloads into a `sync` folder inside lyricsmith's per-user model folder,
derived at run time:

| Platform | Model folder |
| --- | --- |
| macOS | `$HOME/Library/Application Support/lyricsmith/models/sync` |
| Windows | `%LOCALAPPDATA%\lyricsmith\models\sync` |
| Linux and the rest | `${XDG_DATA_HOME:-$HOME/.local/share}/lyricsmith/models/sync` |

`--model-dir` moves it, and `--help` prints the live value. The three files, the wav2vec2
weights from download.pytorch.org and htdemucs's bag file and weights from Hugging Face, are
pinned by name, size and SHA-256 in the manifest in `src/sy_models.py`, the one place those
values are written; read them there. The two Hugging Face addresses name one commit, never a
branch, so they cannot start serving other weights.

The download is the transcribe tool's: each file is streamed into a temporary partial beside
its target, hashed and size-bounded on the way in, and renamed into place only when its digest
matches; a file already present with the right digest is reported `present`. A run never
downloads: it checks every model's size and digest before touching a track, and a missing or
mismatched one is exit `2`, naming `--mode fetch-models`.

## Options

| Option | Modes | Default | Meaning |
| --- | --- | --- | --- |
| `--mode` | all | `run` | `run` or `fetch-models` |
| `--path` | run | required there | one track, or a folder walked recursively; made absolute against the working folder |
| `--extensions` | run | `mp3` | comma-separated, matched case-insensitively; each must be one ffmpeg may open with an audio demuxer (below), so `lrc`, `txt` and any other name are refused |
| `--embed` | run | off | after timing, run `lyricsmith album --offline` on each album that gained or changed a `.lrc` |
| `--overwrite` | run | off | re-time a track whose `.lrc` this tool wrote |
| `--by-hand-list` | run | none | a list file: each low-confidence or not-sung track, each flagged first line and each written track too little heard to check is added as its track ends, and a listed low-confidence or not-sung track is skipped, `--overwrite` or not; remove a track's row to time it again. Made absolute against the working folder; *The by-hand list* below |
| `--heard-dir` | run | none | a folder keeping what `whisper-cli` heard of each track found not sung, so a track with a record whose text has changed, or whose row was removed, is judged again without hearing it again, and album can replace the text. Needs `--by-hand-list`; made absolute against the working folder; a name holding anything but a folder is a setup error. *The transcript store* below |
| `--dry-run` | run | off | print the setup check and each track's planned action; time nothing |
| `--model-dir` | all | lyricsmith's model folder, then `sync` | where `fetch-models` writes and a run reads; made absolute against the working folder |
| `--whisper-model-dir` | run | lyricsmith's model folder, the transcribe tool's | where a run reads the two whisper models that hear each track's vocals; made absolute against the working folder |

The walk is the transcribe tool's: it skips every file and folder whose name starts with `.`,
does not follow a symlinked folder, and takes a `--path` naming one file as named. Each name
it finds is judged as that tool judges it, before any sidecar is read: a symlink the walk
found is `skip: linked`, whatever it points at, as the album tool skips a linked MP3, and
claims no `.lrc`; a FIFO, socket or device, or a name that cannot be read, is `ERROR audio`,
never opened, since ffmpeg would wait on a FIFO for its whole bound. A `--path` naming a link
is the operator's choice, so it is followed. Only a name that is a track decides whether
`--path` is a library for `--embed`, so a link or FIFO planted deep in one album leaves it one
album.

ffmpeg opens a track only with the audio demuxer its extension needs, by
`-format_whitelist`, whatever the track's bytes claim: probed freely, a file holding an
ffconcat or HLS playlist under a track's name would open the files that playlist names. The
extensions `--extensions` takes, and the demuxer each needs, are `DEMUXERS` in
`src/sy_types.py`:

| Extensions | ffmpeg demuxer |
| --- | --- |
| `aac` | `aac` |
| `aif`, `aifc`, `aiff` | `aiff` |
| `flac` | `flac` |
| `m4a`, `m4b` | `mov` |
| `mka` | `matroska` |
| `mp2`, `mp3` | `mp3` |
| `oga`, `ogg`, `opus` | `ogg` |
| `wav` | `wav` |
| `wma` | `asf` |
| `wv` | `wv` |

None of them reads another file: `concat`, `hls`, `image2` and every playlist demuxer stay
off the list, and a track whose bytes need one is `ERROR align`, as any track ffmpeg cannot
decode is, its detail carrying ffmpeg's `Format not on whitelist`.

## What happens to each track

**The plan, which reads no audio.** Each track found gets one action, which the dry run prints:

| The track holds | Action |
| --- | --- |
| a `.txt` and no timed `.lrc` | `time`; `ERROR text` when the `.lrc` name holds a link, which the write would replace |
| a `.lrc` carrying this tool's by-line, under `--overwrite` | `re-time`, the words taken from that `.lrc`; `skip: no untimed text` when they hold nothing to align |
| any other timed `.lrc` | `skip: already timed` |
| an untimed `.lrc` | `skip: untimed .lrc (run album first)` |
| no `.txt`, or one with nothing to align | `skip: no untimed text` |
| a sidecar that cannot be read | `ERROR text` |
| a symlink the walk found | `skip: linked` |
| a `low confidence` row in the `--by-hand-list`, and a plan to time or re-time it | `skip: listed for timing by hand` |
| a `not sung` row in the `--by-hand-list`, and a plan to time or re-time it | `skip: listed as not sung`, unless its text has changed since its record in the `--heard-dir` was kept: then `time` or `re-time`, judged first against that record, so the run may still end it `not sung` (*The transcript store*) |
| no regular file: a FIFO, socket or device | `ERROR audio` |

A re-time takes its words from the `.lrc` because the album tool removes the `.txt` once a
timed `.lrc` is embedded. Sidecars are found by the album tool's own rules
(`../album/album.md`, *How a track's lyrics are found*): the same names, the lowercase
suffixes, a link followed only when it stays inside the track's folder, the same decode and
the same lyrics cap. So the two tools never disagree about what a track holds. A sidecar
that is not UTF-8, is over the cap, or cannot be opened is that track's `ERROR text`, naming
the file.

Two tracks can share one `.lrc`: `01 A.mp3` and `01 A.flac` under `--extensions mp3,flac`.
The first in sorted order keeps its plan, and each later one is `ERROR text` with
`another track already claims 01 A.lrc`. File names in one folder are compared case-folded,
because on a folder that ignores case `A.mp3` and `a.flac` share one too; on one that does
not, the pair costs a row, never a file. Folders are compared exactly, so two folders whose
names differ only by case never refuse each other's tracks. A folder the walk cannot read is
an `ERROR text` row naming that folder, `could not read the folder: <reason>`, never a
silent gap.

**Timing, one track at a time, each in a child process.** PyTorch's calls take no timeout, so
`src/sy_child.py` runs as its own process under the same interpreter, and the parent kills it
at its bound. One track at a time keeps memory to about 3 GB, and the child returns all of it
when it exits. The break check reads every stem htdemucs separates, not only the vocals, but
separation already computed them all, and it keeps each only as a mono copy at 16 kHz: the
stereo stems are freed before wav2vec2 loads, so the four copies held through scoring and
alignment take less memory than the one stereo vocal stem held there before. The parent
writes a request file (the track, its letters, each letter's line, the model folder,
`whisper-cli` and the whisper model folder); the child writes a reply file, which the parent
checks field by field before trusting it. In the child, which first makes every socket
connection, datagram send and name lookup in its own process fail:

1. **Decode** the track with ffmpeg to 44.1 kHz stereo, named as `file:` plus its absolute
   path with only ffmpeg's file protocol allowed, so no name is read as a network address
   or a pipe, opened only by the audio demuxers listed under *Options*, so no playlist in a
   track's bytes opens another file, and no more than the longest track's length decoded.
2. **Separate the vocals** with htdemucs, loaded from the model folder alone, in memory; no
   stem file is written. Every stem is reduced at once to mono at 16 kHz, as the model hears
   the vocals; the other stems are kept so, in memory too, for the break check.
3. **Hear** the vocals, before wav2vec2 loads: the mono vocal stem is written as a 16-bit
   WAV at 16 kHz in a private folder made inside the parent's work folder, and
   transcribed by `whisper-cli` exactly as the transcribe tool calls it
   (`at_engine.build_argv`), in English, with the Genius key withheld from its
   environment, within a minute plus half the track's length. The transcript is judged
   against the text (*The sung check*); a text not sung is replied as such, and nothing
   below runs. A `whisper-cli` that fails, times out or leaves no transcript fails the
   child, so its track is `ERROR align` with `whisper-cli`'s last error lines: a check
   that could not run is never a pass. `whisper-cli` runs as the child's own process, so a
   child the parent kills at its bound leaves it running, orphaned, until it finishes on
   its own: its input is the vocals of one track, at most an hour of them.
4. **Score** the vocals at 16 kHz with wav2vec2, in 20-second windows, and join the windows'
   letter scores. Each window also gives, for each of its model frames, the level (RMS) of
   the vocal stem and of each other stem.
5. **Align** the whole text in one CTC forced alignment, torchaudio's `forced_align`. The text
   becomes the model's letters: capitals and the apostrophe, an accented letter read as its
   base letter (`café` as `CAFE`), a typographic apostrophe read as the plain one, one word
   separator between each two words, every other character dropped.
6. **Close the breaks and align again.** CTC spreads lines across a long instrumental break,
   since nothing in the scores tells it a stretch holds no singing: on a reference track with
   a 48 s break, 6 to 9 lines landed more than 3 s off. So each model frame's vocal level is
   read against the rest of the band's, the other stems combined, in dB, and smoothed by a
   running median. A stretch at least as long as the shortest break, under the break
   threshold throughout, is a break once the break margin is trimmed from each edge. Only a
   stretch starting after the second line's start in the first alignment counts: a quiet
   stretch before it is the intro, which the first-line check covers. Every letter is then
   forbidden in each break, only the blank left, and the text aligned again over those
   scores; the reply carries the second alignment. When no break is found, no letter
   belongs to a line after the first, or the frames left outside the breaks could not hold
   the text, the first alignment stands, exactly as before. The four values, measured on
   the reference tracks and on held-out test tracks, are under *Fixed settings*.

The parent then takes each line's start from its own first letter. The separator between
two lines belongs to neither: CTC may place it anywhere in a pause, and counting it as the next
line's start would stamp a line after a pause where the pause began. A line with nothing to
align, such as a line of digits or a bracketed section header like `[Chorus]`, keeps its place
in the output and takes the next aligned line's stamp. One after the last sung line, such as
a closing `[Outro]`, is stamped where that line's last letter ends instead: given the sung
line's own start it would tie with it, and a player showing the later of two tied lines would
hide the sung one. The row counts such lines.

**Blank lines between verses are kept.** The `.lrc` holds every line of the text, each
stripped, in order: a blank line between two non-blank lines is written as an empty line,
each one of a run, and only the blank lines before the first and after the last line are
dropped. A blank line has no letter, so it takes the next line's stamp as a section header
does, written just before that line: the album tool reads tied stamps in file order, so a
player showing the later of two tied lines shows the sung one, and no sung line's stamp
moves. They are kept because the album tool embeds the untimed lyrics from the `.lrc`'s
lines, so a file without them would lose the breaks between verses. The row does not count
them as lines with nothing to align.

**The checks, in this order:**

- **The track is too long.** A track whose tags claim more than an hour, the longest track
  timed, is `skip: track too long`, with its length, before any child starts: the child
  holds the whole decoded track in memory. A track whose length cannot be read is decoded
  up to that hour.
- **The text is not sung.** Enough of the vocals heard to judge, and too few of the word
  pairs heard are the text's: `not sung: left untimed`, and no file. The row names the
  share and the pairs heard, and the footer says what to check. A planned outcome, like
  low confidence: the run still exits `0`. A track too little heard to judge is timed as
  before, the confidence floor still applying, and its row says `only <n> word pairs
  heard, under 20: its text was not checked against the vocals`.
- **The text does not fit.** More letters than the audio has frames, which CTC cannot place:
  `skip: text longer than the track`, found before the alignment runs, and no file.
- **Low confidence.** The track's median line confidence below the floor:
  `low confidence: time by hand`, and no file. The row names the median, and the footer points
  to a tap-along LRC editor run locally.
- **A stretched first line.** A first line has no gap before it, so it can stretch back over
  the intro. The track is written, and its row says `check the first line: <file> <stamp>`;
  the footer prints the fix.

**The write.** The `.lrc` goes beside the track, written with the album tool's atomic writer: a
hidden temporary file beside it, fsynced, then renamed over. The tool never touches the audio or
the `.txt`. A child that crashes, times out or leaves an unreadable reply leaves no `.lrc`: its
track is `ERROR align`, with the child's exit and the last lines of its error output, and the run
goes on. A write that fails is `ERROR write`, with the reason.

**`--embed`, once every track is done.** The album tool runs once on each album that gained or
changed a `.lrc`, and an album is the unit the sweep tool walks a library by. So when `--path` is
the library root the sweep walks, or one album, the album tool holds the lock the sweep would
take, and the two never write one MP3 at once. When any track found lies two or more folders
below `--path`, `--path` is a library: a written track is embedded through its `Artist/Album`
folder, the two folders under `--path` that hold it, so a multi-disc album's disc folders are
one run. A written track nearer `--path`, at the library root or in an artist folder, lies in no
album of the library: its line is `embed not done (in a library but not in an Artist/Album
folder)`, naming the track, with the album command for that track alone (`--track`, both paths
absolute, so it runs from any folder). A command for its whole folder would run the album tool
over every album below. Otherwise `--path` is one album and is embedded once, as a whole; a
`--path` naming one track embeds that track's folder. The album tool embeds into MP3s only, so
a `.lrc` beside a track of another format stays a sidecar: no album run is made for it, by its
name or with `--embed`, and the footer's `stays a sidecar: <file>` line names it.

For each album, `album.py --album <folder> --mode run --offline` runs as a child process under
the same interpreter, its output on stderr so this tool's summary alone reaches stdout. The
album tool takes its own lock, backs up, embeds and checks as in any offline run. Asking no
provider, it applies its usual sidecar rules to every track in the folder, not only the ones
this tool timed: it may, for example, remove placeholder lyrics from another track, and its
`--mode undo` restores that. This tool never takes that lock: a folder another run holds is
refused by the album tool, and the line says `embed not done (album exit <code>)` with the
command to run.

The album tool saves MP3s in place and lets a save in hand finish before it stops on Ctrl-C, so
it is stopped the way the sweep tool stops it, never killed first. An album run past its
half-hour bound is sent Ctrl-C's own signal and given the sweep tool's grace to stop before it
is killed, and is `embed not done (timed out)`. A Ctrl-C during a run reaches the album tool
too and is given the same grace: that run is `embed not done (interrupted)`, and each album
after it `embed not done (not run: ...)`. A run that cannot start is `embed not done (did not
start)`, and one that would not stop even when killed says so.

**A Ctrl-C while tracks are being timed** reaches the child too, and stops the run there.
The tracks timed so far keep their `.lrc`; the track in hand gets none, its temporary folder
removed, unless the interrupt came after its new `.lrc` was in place, timed or re-timed:
then it is counted and named with the rest, though without its row and without its
first-line check. The summary goes on to an `interrupted:` line naming how many tracks were
left, then the footer with the album command for each album that gained a `.lrc`, as a run
without `--embed` prints it. `--embed` is not run, and the run exits `130`. A second run
skips the tracks already timed, so their album commands are printed only here.

## The sung check

The text and the transcript are read as the alignment model's words, each line by
album's `lyrics_heard.line_words`: capitals and the apostrophe, accents folded, a bracketed
line such as `[Music]` giving none. A word pair is two consecutive words, nothing dropped;
the text's pairs run across its line ends, as its letters do. The verdict is the share of
the transcript's distinct pairs found among the text's. The rule and its cutoffs live
in album's `../album/src/lyrics_heard.py`, so album judges a text by the same ones; sync reads its
token ids back to words for it (`src/sy_heard.py`):

- fewer than 20 distinct pairs heard (`MIN_HEARD_PAIRS`): the check cannot judge, and the
  track is timed as before, with a `little heard` note;
- otherwise a share under 0.25 (`MIN_HEARD_SHARE`): `not sung`, and nothing is aligned;
- otherwise the track is aligned as before.

**The measurement.** On about 190 test-library tracks, each with its own text and with a
deliberately wrong one, the 16 kHz vocal stem transcribed by that same call: a wrong text
scored at most 0.16; a right one at least 0.34 (a song not in English) and 0.55 at the 5th
percentile. Under 20 distinct pairs heard, a wrong text could score 0.5 by chance; about 1
track in 11 fell under it. Two other measures were rejected: single words let a long wrong
text match common words (up to 0.68), and counting every pair heard rather than each
distinct one is inflated by whisper falling into a loop repeating one line.

**The cost.** `whisper-cli` took a median 7.8 s a track, about 0.036 of its length, against
separation's 59 s. Its bound in the child is a minute plus half the track's length, and the
parent's bound on the whole child gains the same.

## The by-hand list

A run over a whole library takes days, so it is run in legs, each resumed where the last
stopped: a timed track is skipped as already timed. A low-confidence track gets no `.lrc`,
so without a record of it each leg would time it again, at minutes a track, and its row
would exist only on standard output. `--by-hand-list <file>` keeps that record.

**What is written, and when.** As each track ends, before its row prints, a
low-confidence or not-sung track adds a row, and a written track adds one for a flagged
first line and one when too little was heard to check its text.
The whole file is then replaced atomically with Ctrl-C held, so an interrupted leg keeps
every row it reached and a reader never sees a torn line. The file is written as
lyricsmith's state files are: `0o600`, any missing folder above it made `0o700`, and
confirmed by a read-back; a link, a FIFO or anything but a regular file under its name is
refused, never written through. Rows for tracks outside this run's `--path` are kept as
they are. A track written again replaces its earlier `first line` and `little heard` rows
with its new ones, or drops each that no longer holds, since it was about the old file. A
low-confidence or not-sung track replaces only its own earlier row of that reason: the old
`.lrc`, if any, is still in place. A track that wrote its `.lrc` also drops its earlier
`not sung` and `low confidence` rows, since the track is timed now, and a track left low
confidence or with a text longer than the track drops its earlier `not sung` row, since
its text was heard sung.
A failed write is said beneath the track's row, `--by-hand-list: could not write
<file>: <reason>`, and the run exits `1`. When rows were added, the footer ends with
`listed for timing by hand: <n> row(s) added to <file>`. A dry run reads the file, so its
plan shows the skips, and never writes it.

**The format.** UTF-8, one row per line, each ended by a linefeed, three tab-separated
fields: the track's absolute path as the walk names it, the reason, and the detail.

| Reason | Detail | What it means |
| --- | --- | --- |
| `low confidence` | the median line confidence, as the row printed it (`0.08`), or `not measured` when the run had none to print | the track got no `.lrc`: time it by hand. The next run skips it |
| `first line` | the flagged first line's stamp (`[00:00.280]`) | the `.lrc` was written: check its first line by ear. Not a skip: the track is already timed |
| `not sung` | the share of the word pairs heard that are the text's, two decimals (`0.04`), or `not measured` | the track got no `.lrc`: its `.txt` may hold another song's lyrics. The next run skips it |
| `little heard` | the distinct word pairs heard (`12 pairs`) | the `.lrc` was written, but too few pairs were heard to check its text against the vocals. Information only, not a skip |

A path the format cannot hold, one holding a tab, linefeed, carriage return or NUL, one
that is not strict UTF-8, or one that is not absolute, is left out, and the track's row
says so (`--by-hand-list: left out, the path <why>`). Blank lines are skipped.

**The read.** The file is read whole at the start, before any track is touched, through
the same reader as the sweep's state files: never through a link, never from a FIFO or a
device, and never past 64 MiB. A missing file is an empty list. One that cannot be read,
or holds a line this tool does not write, is a setup error (exit `2`) naming the file and
the line. A track is matched by its path exactly as the walk names it, so a list kept for
one library root matches only runs whose `--path` names the tracks the same way.

**Timing a listed track again.** Remove its row, or, for a `not sung` row kept with
`--heard-dir`, change its text. `--overwrite` does not override the list. Do not edit
the file while a run is using it: the run rewrites it whole from what it read at the
start, so an edit made meanwhile is lost at the next row.

## The transcript store

A track whose text is not sung was heard by `whisper-cli` for nothing, and hearing it again
costs a separation and a transcription. `--heard-dir <folder>` keeps what was heard, so
that a changed text is judged against it, and so that album can replace a wrong text
without transcribing the track again. Nothing is transcribed twice while its record is
kept: a track with a record is judged against it, listed or not.

**What it holds.** One file a track, named by the SHA-256 of the track's absolute path as
the walk names it, never resolved, holding JSON with the track's `path`, the `fingerprint`
of the text it was judged against (the SHA-256 of its words, read as the sung check reads
them, so a change of case, spacing or punctuation is no change) and the `transcript`. The
store is album's (`../album/src/lyrics_heard_store.py`). Each file is written `0o600`,
atomically, and confirmed by a read-back; a folder the store makes is `0o700`. A transcript
over 64 KB is never stored: the child sends none, the track is still `not sung` with its
row, and under `--heard-dir` the note `--heard-dir: what was heard is over 64 KB and was
not kept` is said beneath it, with no change to the exit code. A child that does send one
over 64 KB is refused as a malformed reply, and the track is an `ERROR align`. These files
are what a library's tracks sing: they are never committed and never sent anywhere.

**When it is written.** A track that ends `not sung` writes its record in the same
Ctrl-C hold as its `not sung` row, so the two are kept or lost together; that is why the
option needs `--by-hand-list`. A track timed, re-timed or left low confidence was heard
sung, and has its record removed in the same hold. A track whose text is longer than the
track keeps its record, so its next text is judged against it. A failed write or removal is said beneath the track's row,
`--heard-dir: could not keep what was heard in <folder>: <reason>` or
`--heard-dir: could not remove the record of <track>: <reason>`, and the run exits `1`.
When records were written, the footer ends with `kept what was heard of <n> track(s) not
sung in <folder>`.

**The rerun rule.** A track listed `not sung` is skipped when its record's fingerprint is
its current text's, or when it has no record (a list from before the option, or a run
without it). Any other track with a record that would be timed, its text changed or its
row removed, is first judged against the record's transcript in the parent, with no
child: still not sung, the track ends `not sung` again with the note `judged against what
was heard before`, and its row and record are written again. Otherwise it is timed, and
the child is handed the transcript: it still separates the vocals, to align against them,
but does not run `whisper-cli`.

**Cleaning itself.** At the start of a run, never a dry run, each record whose track lies
under `--path` and is gone, or now holds a timed `.lrc`, is removed, and the run says
`--heard-dir: removed <n> record(s) of tracks gone or timed from <folder>`. Records of
tracks outside `--path` are left alone, so a run over one folder never empties the store
of a library that is not there to look at. A record path holding a `..` part is never read
as under `--path`, since the store keeps a path as written and `..` could climb out of it,
so no sidecar outside `--path` is ever read for one. A file in the store that is not a
record this tool can read (its path holding a NUL byte among them) is reported, `--heard-dir: <file>: transcript record ignored (<error>)`, and
left as it is. A dry run reads the store, so its plan shows which tracks would be timed,
and never writes or removes anything. Its plan shows `time` for a track whose record it
would judge again, even one the run then finds still not sung, since only the run judges.

**When it may go.** The store serves one whole-library run and the album
fix that follows it. Once that run is done and the fix has been applied, the folder may be
deleted. Each track listed `not sung` stays skipped by its row; once its row is removed,
a later run hears it again.

## The label

```text
[re:lyricsmith sync 0.1.0; demucs 4.1.0 htdemucs; torchaudio 2.11.0 WAV2VEC2_ASR_BASE_960H]
[by:machine timed]
[00:00.421]And so my fellow Americans,
```

`[re:]` names the program and both models, `[by:]` the author, the common LRC convention.
Every version is read at run time: the tool's from `[project].version` in the project's
`pyproject.toml`, through the project's manifest reader (`unknown` when it cannot be read;
`../lyricsmith_project/lyricsmith_project.md`), and
demucs's and torchaudio's from their installed package metadata. A version's control,
direction and separator characters are spelled as escapes (`stdio_utf8.spell_unsafe`), as
the transcribe tool's label spells its values, so a line break in one cannot split the
header.

A file is this tool's by its `[by:machine timed]` line, never by its `[re:]` line: one of the
header lines, before the first timed line, must be exactly that once surrounding whitespace is
stripped, the comparison the album tool makes. Any edit to that line makes the file a person's:
the album tool serves it as an ordinary sidecar, and `--overwrite` never touches it.

## Beside the album tool

The album tool knows this file by the same `[by:machine timed]` line, as the source
`machine timed`. It still asks its timed providers: a provider's synced answer replaces the
machine-timed `.lrc`, written with no label, and the run's backup holds the old bytes; an untimed
answer never replaces it. `--offline`, which `--embed` runs, embeds it at once.

A labeled `.lrc` whose lines match the embedded synced lyrics, only the times different, replaces
them. So a stamp corrected by hand reaches the tags: edit the stamp in the `.lrc`, keep the
`[by:machine timed]` line, and run `lyricsmith album --offline --album <the album folder>`: in
a library, the library's `Artist/Album` folder, and for a track in no album folder, the
track's folder with `--track <the track>` added, so the album tool runs on that track alone. A
second run of this tool does not embed it, since `--embed` covers only the tracks a run
writes. The album run backs up the old frames as any run does, so the album tool's `--mode
undo` puts the earlier timing back. An `--overwrite` re-time reaches the tags the same way.

Undo puts the frames back, but the `.lrc` keeps the new times, and the next run embeds them
again. To revert, also restore the `.lrc` from the album tool's backup, or delete it so the
frames serve. To change the words, remove the `[by:machine timed]` line, which makes the file a
person's: a labeled `.lrc` whose words differ from the synced frames' gives way to them, and the
album tool rewrites it from the frames.

The match is on words alone, whoever wrote the frames. A track holding synced frames another
tool wrote, beside a `.txt` with the same words, is timed from that `.txt` like any other, and
the embed then replaces those frames with this tool's times. The album tool's `--mode undo`
recovers them.

## Fixed settings

Each is a named constant beside the code that uses it, with its reason:

| Setting | Value | Why |
| --- | --- | --- |
| Language | English | The model is English; another language comes out low-confidence and gets no file |
| Device | CPU | Timing ran under a minute a track on the reference tracks, with no GPU path |
| Window | 20 s | wav2vec2's attention memory grows with the square of its input; a whole song in one pass does not fit in 16 GB |
| Confidence floor | 0.15, the track's median line confidence | Tracks that aligned well scored medians of 0.27 to 0.68; one whose vocals the model could not follow scored 0.08 |
| First-line flag | the first line's span over 2 times the track's median line span | Flagged the one bad first line on the reference tracks and one more, and no others |
| Longest track | an hour, `MAX_TRACK_S` | The decoded track is held in memory, about 705 KB a second as 32-bit stereo and twice that while separating |
| Per-track bound | 60 s plus twice the track's length, then the `whisper-cli` bound on top, the length counted up to the longest track's, and the longest track's when it cannot be read | Timing ran at about 0.2 times the track's length; loading both models takes most of a minute; a tag's length is a claim a crafted header can inflate |
| `whisper-cli` bound | 60 s plus half the track's decoded length, `WHISPER_BASE_S` and `WHISPER_PER_SECOND` | It ran a median 7.8 s a track, about 0.036 of its length, model load included |
| Sung share | 0.25, `MIN_HEARD_SHARE` | Wrong texts scored at most 0.16 on about 190 test-library tracks, right ones at least 0.34 |
| Fewest pairs judged | 20 distinct word pairs heard, `MIN_HEARD_PAIRS` | Under 20, a wrong text could score 0.5 by chance; about 1 track in 11 fell under it |
| Hearing language | English, the transcribe tool's fixed call otherwise | The alignment model's one language; a song in another still scored 0.34 against its own text |
| Album run bound | 30 minutes a folder, under `--embed` | One offline album run backs up, embeds and checks in minutes at most |
| Break threshold | -12 dB, the vocal stem's level against the rest of the band's, `BREAK_RATIO_DB` | With the three values below, the lines more than 3 s off on the reference track with a 48 s break fell from between 6 and 9, across five seeds, to 3, and the other reference tracks were unchanged. Of 60 held-out test-library tracks, 9 improved and 5 got worse; the rest were unchanged, and each worse one already held a line 20 s or more off. -10 dB gave the same reference figures; at -15 dB and lower they stayed the built tool's |
| Shortest break | 12 s, `BREAK_MIN_S` | Measured with the threshold above; settings that scored better on the reference tracks (-10 to -12 dB at 8 s) pushed one of them under the confidence floor or made more held-out tracks worse |
| Break margin | 1.5 s trimmed from each edge of a break, `BREAK_MARGIN_S` | The margin the break figures above were measured with |
| Ratio smoothing | a running median over 1 s, `RATIO_SMOOTH_S` | The smoothing the break figures above were measured with |
| Separation shift seed | 0 | demucs shifts the mix by a random offset before separating, drawn from Python's module random generator; unseeded, the same track's stamps moved between runs. The seed fixes the draw, so a run repeats byte for byte. The draw itself moves a track's timing: on one test track, the lines more than 3 s off ranged from 6 to 9 across five seeds. 0 is a fixed choice, set before any seed was measured, not a tuned one |

None is an option: a new value needs a new measurement on the same reference tracks.

## Output and progress

A run prints one row per track to stdout as each finishes: the outcome, padded to a fixed
column, then the track's path relative to the `--path` folder (or a named file's own folder).
A row's detail lines are indented beneath it. The run ends with a footer: one count line naming
each outcome seen, then, without `--embed`, the album command for each album that gained a
`.lrc` (the folders `--embed` would run it on) and, as `embed it:`, the one-track command for
each written track in no album, then, as `stays a sidecar:`, each written `.lrc` beside a track
that is no MP3, then the first-line fix when a line was flagged, the note on timing by hand
when a track was low-confidence, the note on checking the lyrics when a text was not sung,
and, under `--by-hand-list`, the line naming the list when
rows were added to it, then, under `--heard-dir`, the line naming the store when records
were kept in it:

```text
timed                                 jfk.wav
timed 1
embed them: lyricsmith album --offline --album <folder>
```

With `--embed`, each album's line is `embedded: <folder>`, or `embed not done (...)` with the
command to run, and no `embed them:` line is printed.

Each command printed names the tools the way this one was run, as `README.md` describes:
run through the dispatcher, the album tool as `lyricsmith album` and this tool, in the setup
check's model fetch line, as `lyricsmith sync`; run alone, each by its entry file's name,
`album.py` and `sync.py`. The examples here show the dispatcher's form.

`--dry-run` prints `setup: ok`, or `setup: <problem>` for each problem, the whisper ones
among them, then each track's planned action, a refusal's reason indented beneath it. It
times nothing and runs no child. A dry run with a setup problem exits `2`; one with an `ERROR
text` or `ERROR audio` row exits `1`. `fetch-models` prints one row per model: `present`,
`downloaded`, or `failed` with its reason indented below.

Setup errors go to stderr, prefixed with the program's name. Every printed line that quotes a
path has its control and direction characters spelled as escapes by the shared
`stdio_utf8.spell_unsafe`, the rule every lyricsmith tool follows, so a hostile file name
can neither steer the terminal nor print as another name.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | every track timed, re-timed, skipped, left low-confidence for timing by hand, or left untimed as not sung; under `fetch-models`, every model present or downloaded |
| `1` | a track's text, alignment or write failed, a `--by-hand-list` row could not be written, a `--heard-dir` record could not be written or removed, a found name was no regular file, a folder could not be read, or an `--embed` album run failed, was refused or did not finish, or a written track could not be embedded; under `fetch-models`, any model failed |
| `2` | a setup problem found before any track is touched: a missing sync install (naming `pip install -r` and `requirements-sync.txt`), no ffmpeg, a missing or mismatched model (naming `--mode fetch-models`), no `whisper-cli` on `PATH`, a missing or mismatched whisper model (naming `lyricsmith transcribe --mode fetch-models`), a `--path` that does not exist, a missing `--path`, an unusable `--extensions`, `lrc`, `txt` and any extension with no listed demuxer included, a `--by-hand-list` that cannot be read or breaks its format (naming the file and the line), `--by-hand-list` or `--heard-dir` given with `--mode fetch-models`, `--heard-dir` without `--by-hand-list`, or a `--heard-dir` naming something that is not a folder, or a folder that cannot be listed |
| `130` | Ctrl-C while timing: the tracks timed so far keep their `.lrc`, the footer names the album command for each, and `--embed` is not run |

A shared library or a sibling tool's module that cannot be located, or that a same-named
module would shadow, also exits `2`, before anything runs: see *Exit codes* in
`../README.md`.

A low-confidence track is a planned outcome, not a failure, and so is a text not sung. One
track's failure is that track's row and never stops the run.

## Tests

`test_sync.py` runs every suite under `tests/` and holds a floor per file, so a suite emptied by
accident fails loudly. Every case derives from the album tool's isolated base class, which
points `HOME` and the XDG folders into a temporary folder and fails any socket use; the
aggregator fails the run when a case does not. `tests/sy_cases.py` holds the shared helpers: a
fake child runner that writes the reply the child would, and a builder of a good reply for a
given text.

The unit suites need no PyTorch: the child module's PyTorch functions are left out of their
coverage, each marked, and `tests/test_sy_live.py` runs them. The sung check's run,
`src/sy_whisper.py`, is tested with a stand-in `whisper-cli` that reads the WAV it is given
and writes a transcript. The black-box suite runs `main()`
with every outside call faked through the `Seams` record in `src/sy_modes.py`: the child runner,
the album runner, the module lookup, the ffmpeg lookup and the package versions. `--embed` is
also tested against a real child process: a stand-in `album.py` that logs its arguments and
exits as told.

`tests/test_sy_live.py` is the live smoke test across the process boundary: the real child and
models time whisper.cpp's bundled `jfk.wav`, public domain and never committed, from its known
text, and each line's start must fall within a second of its pinned value. The clip's 18
distinct word pairs are under the sung check's floor, so the run also lists it as `little
heard`; and the check's verdict on its vocal stem, read in the test's own process through the
real `whisper-cli`, must hear the clip's own text whole and an invented one under the cutoff.
It skips unless the interpreter running it has the sync install, ffmpeg is on `PATH`, the
models are fetched, sync's and the transcribe tool's, and `jfk.wav` sits beside
`whisper-cli`. The parent runs under the same socket tripwire, so a pass
also proves the run made no network call. `samples/README.md` documents the same run by hand.
