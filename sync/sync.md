# sync.py

Times a track's untimed lyrics against its own audio, offline, into a labeled `.lrc`. Many
tracks end an album run with untimed lyrics only, because several providers give no timing.
This tool takes the words already in the track's `.txt`, separates the vocals with htdemucs,
scores them with torchaudio's English wav2vec2 model, and force-aligns the whole text in one
pass, so each line gets the start of its own first letter. Every `.lrc` it writes opens with
two label lines naming the tool and both models, so a machine-timed file is never mistaken for
a hand-made or provider one, and `--overwrite` re-times only the files the tool wrote itself.

It works on English only: another language comes out low-confidence and gets no file.

## Prerequisites

1. **The sync install**, into the interpreter that runs lyricsmith:
   `pip install -r requirements-sync.txt`, from this folder. It pins PyTorch, torchaudio,
   demucs and everything they pull in, each exactly. It is kept out of lyricsmith's
   `pyproject.toml` extras so the gate's interpreter and CI never install PyTorch.
2. **ffmpeg** on `PATH` (`brew install ffmpeg`), which decodes each track.
3. **The three models, once:** `python sync.py --mode fetch-models`.

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
| `--dry-run` | run | off | print the setup check and each track's planned action; time nothing |
| `--model-dir` | all | lyricsmith's model folder, then `sync` | where `fetch-models` writes and a run reads; made absolute against the working folder |

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
when it exits. The parent writes a request file (the track, its letters, the model folder);
the child writes a reply file, which the parent checks field by field before trusting it. In
the child, which first makes every socket connection, datagram send and name lookup in its
own process fail:

1. **Decode** the track with ffmpeg to 44.1 kHz stereo, named as `file:` plus its absolute
   path with only ffmpeg's file protocol allowed, so no name is read as a network address
   or a pipe, opened only by the audio demuxers listed under *Options*, so no playlist in a
   track's bytes opens another file, and no more than the longest track's length decoded.
2. **Separate the vocals** with htdemucs, loaded from the model folder alone, in memory; no
   stem file is written.
3. **Score** the vocals at 16 kHz with wav2vec2, in 20-second windows, and join the windows'
   letter scores.
4. **Align** the whole text in one CTC forced alignment, torchaudio's `forced_align`. The text
   becomes the model's letters: capitals and the apostrophe, an accented letter read as its
   base letter (`café` as `CAFE`), a typographic apostrophe read as the plain one, one word
   separator between each two words, every other character dropped.

The parent then takes **each line's start from its own first letter**. The separator between
two lines belongs to neither: CTC may place it anywhere in a pause, and counting it as the next
line's start would stamp a line after a pause where the pause began. **A line with nothing to
align**, such as a line of digits or a bracketed section header like `[Chorus]`, keeps its place
in the output and takes the next aligned line's stamp. One after the last sung line, such as
a closing `[Outro]`, is stamped where that line's last letter ends instead: given the sung
line's own start it would tie with it, and a player showing the later of two tied lines would
hide the sung one. The row counts such lines.

**The checks, in this order:**

- **The track is too long.** A track whose tags claim more than an hour, the longest track
  timed, is `skip: track too long`, with its length, before any child starts: the child
  holds the whole decoded track in memory. A track whose length cannot be read is decoded
  up to that hour.
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
| Per-track bound | 60 s plus twice the track's length, the length counted up to the longest track's, and the longest track's when it cannot be read | Timing ran at about 0.2 times the track's length; loading both models takes most of a minute; a tag's length is a claim a crafted header can inflate |
| Album run bound | 30 minutes a folder, under `--embed` | One offline album run backs up, embeds and checks in minutes at most |
| Separation shift seed | 0 | demucs shifts the mix by a random offset before separating, drawn from Python's module random generator; unseeded, the same track's stamps moved between runs. The seed fixes the draw, so a run repeats byte for byte. The draw itself moves a track's timing: on one test track, the lines more than 3 s off ranged from 6 to 9 across five seeds. 0 is a fixed choice, set before any seed was measured, not a tuned one |

None is an option: a new value needs a new measurement on the same reference tracks.

## Output and progress

A run prints one row per track to stdout as each finishes: the outcome, padded to a fixed
column, then the track's path relative to the `--path` folder (or a named file's own folder).
A row's detail lines are indented beneath it. The run ends with a footer: one count line naming
each outcome seen, then, without `--embed`, the album command for each album that gained a
`.lrc` (the folders `--embed` would run it on) and, as `embed it:`, the one-track command for
each written track in no album, then, as `stays a sidecar:`, each written `.lrc` beside a track
that is no MP3, then the first-line fix when a line was flagged, and the note on timing by hand
when a track was low-confidence:

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

`--dry-run` prints `setup: ok`, or `setup: <problem>` for each problem, then each track's planned
action, a refusal's reason indented beneath it. It times nothing and runs no child. A dry run
with a setup problem exits `2`; one with an `ERROR text` or `ERROR audio` row exits `1`. `fetch-models` prints one
row per model: `present`, `downloaded`, or `failed` with its reason indented below.

Setup errors go to stderr, prefixed with the program's name. Every printed line that quotes a
path has its control and direction characters spelled as escapes by the shared
`stdio_utf8.spell_unsafe`, the rule every lyricsmith tool follows, so a hostile file name
can neither steer the terminal nor print as another name.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | every track timed, re-timed, skipped, or left low-confidence for timing by hand; under `fetch-models`, every model present or downloaded |
| `1` | a track's text, alignment or write failed, a found name was no regular file, a folder could not be read, or an `--embed` album run failed, was refused or did not finish, or a written track could not be embedded; under `fetch-models`, any model failed |
| `2` | a setup problem found before any track is touched: a missing sync install (naming `pip install -r` and `requirements-sync.txt`), no ffmpeg, a missing or mismatched model (naming `--mode fetch-models`), a `--path` that does not exist, a missing `--path`, or an unusable `--extensions`, `lrc`, `txt` and any extension with no listed demuxer included |
| `130` | Ctrl-C while timing: the tracks timed so far keep their `.lrc`, the footer names the album command for each, and `--embed` is not run |

A shared library or a sibling tool's module that cannot be located, or that a same-named
module would shadow, also exits `2`, before anything runs: see *Exit codes* in
`../README.md`.

A low-confidence track is a planned outcome, not a failure. One track's failure is that track's
row and never stops the run.

## Tests

`test_sync.py` runs every suite under `tests/` and holds a floor per file, so a suite emptied by
accident fails loudly. Every case derives from the album tool's isolated base class, which
points `HOME` and the XDG folders into a temporary folder and fails any socket use; the
aggregator fails the run when a case does not. `tests/sy_cases.py` holds the shared helpers: a
fake child runner that writes the reply the child would, and a builder of a good reply for a
given text.

The unit suites need no PyTorch: the child module's PyTorch functions are left out of their
coverage, each marked, and `tests/test_sy_live.py` runs them. The black-box suite runs `main()`
with every outside call faked through the `Seams` record in `src/sy_modes.py`: the child runner,
the album runner, the module lookup, the ffmpeg lookup and the package versions. `--embed` is
also tested against a real child process: a stand-in `album.py` that logs its arguments and
exits as told.

`tests/test_sy_live.py` is the live smoke test across the process boundary: the real child and
models time whisper.cpp's bundled `jfk.wav`, public domain and never committed, from its known
text, and each line's start must fall within a second of its pinned value. It skips unless the
interpreter running it has the sync install, ffmpeg is on `PATH`, the models are fetched, and
`jfk.wav` sits beside `whisper-cli`. The parent runs under the same socket tripwire, so a pass
also proves the run made no network call. `samples/README.md` documents the same run by hand.
