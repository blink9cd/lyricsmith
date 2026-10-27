# transcribe.py

Turns audio into a timed `.lrc` and a plain `.txt`, offline, by running whisper.cpp's
`whisper-cli` program on each file. whisper.cpp does the transcription and writes both
formats itself; this tool adds what a one-file command lacks for a music library: a folder
walk, a rule for lyrics already present, a machine-made label, a per-file summary, and a
checked model download. Every `.lrc` it writes opens with two label lines naming the tool, the
engine and the model, so a machine transcript is never mistaken for a hand-made or provider
one, and `--overwrite` replaces only the files the tool wrote itself.

It makes no claim for sung lyrics: Whisper's error rate on sung vocals is far higher than on
speech, and the tool was chosen and checked on spoken audio.

## Prerequisites

1. **whisper.cpp**, MIT-licensed: `brew install whisper.cpp` puts `whisper-cli` on `PATH`.
   Elsewhere, build it from source and pass the program with `--whisper-cli`.
2. **The two models, once:** `python3.12 transcribe.py --mode fetch-models`. Any Python
   3.12 or later runs the tool, which imports the standard library only.

`fetch-models` downloads into the per-user model folder, derived at run time:

| Platform | Model folder |
| --- | --- |
| macOS | `$HOME/Library/Application Support/lyricsmith/models` |
| Windows | `%LOCALAPPDATA%\lyricsmith\models` |
| Linux and the rest | `${XDG_DATA_HOME:-$HOME/.local/share}/lyricsmith/models` |

`--model-dir` moves it, and `--help` prints the live value and both file names. The two
files are pinned by name, size and SHA-256 in the manifest in `src/at_models.py`, the one
place those values are written; read them there.

Each file is streamed into a temporary partial beside its target, hashed and size-bounded on
the way in, and renamed into place only when its digest matches. Every redirect on the way
must stay on HTTPS: urllib would otherwise follow a hop to plain HTTP mid-chain, which a check
of the final URL alone cannot see. Each file's whole download is bounded by a deadline
(`DOWNLOAD_DEADLINE_SECONDS` in `src/at_models.py`, sized generously for the largest model on
a slow line), checked after every read, because the socket timeout bounds only one read and a
server trickling a byte a minute would otherwise never be cut off. A file already present with
the right digest is reported `present` and not downloaded again; one with the wrong digest is
downloaded again, and replaced only once the new one matches. A download that stops short
fails naming how many of the pinned bytes arrived, not as a digest mismatch. A transcribe run never touches
the network: a missing model is exit `2`, naming `--mode fetch-models`.

A transcribe run does not check a model's digest again: only `fetch-models` does. That is an
accepted gap, not an oversight: hashing a 1.6 GB model on every run costs seconds, and a
model swapped after its download needs write access to the user's own data folder, which
already reaches everything the run could protect.

## Options

| Option | Modes | Default | Meaning |
| --- | --- | --- | --- |
| `--mode` | all | `transcribe` | `transcribe` or `fetch-models` |
| `--path` | transcribe | required there | one audio file, or a folder walked recursively |
| `--extensions` | transcribe | `mp3` | comma-separated, matched case-insensitively |
| `--output-dir` | transcribe | beside each file | write into this folder, mirroring the tree under `--path` |
| `--overwrite` | transcribe | off | replace a `.lrc` this tool wrote |
| `--dry-run` | transcribe | off | print the setup check and each file's planned action; run nothing |
| `--language` | transcribe | `en` | a two- or three-letter code, or `auto` |
| `--model` | transcribe | `<model dir>/ggml-large-v3-turbo.bin` | the Whisper model file |
| `--vad-model` | transcribe | `<model dir>/ggml-silero-v6.2.0.bin` | the voice-activity model file |
| `--model-dir` | all | the per-user data folder | where `fetch-models` writes and the model defaults point |
| `--whisper-cli` | transcribe | found on `PATH` | the `whisper-cli` executable |

`fetch-models` neither reads nor validates the transcribe options. `--path`, `--output-dir`,
`--model` and `--vad-model` are made absolute against the working folder (links and `..`
kept), and so is the `whisper-cli` the setup check finds, whether named by `--whisper-cli` or
found on `PATH`: the file checked is the file run, and no path handed to `whisper-cli` can
start with `-` and read as one of its options. A `--path` naming one file is taken as named,
whatever its extension, and followed when it is a link, because naming it is the operator's
choice. It must be a regular file: a FIFO, socket or device is a setup error, since
`whisper-cli` would block on it for its whole bound or read it without end. A `.lrc` or `.txt`
(in any case) is a setup error too: its outputs would be the file itself. `--extensions` filters only a folder walk,
and refuses `lrc` and `txt` (in any case, with or without the dot) for the same reason. The
walk skips every file and folder whose name starts with `.`, and does not follow a symlinked
folder. A linked file it finds is a `skipped (linked)` row and is never transcribed, as
`lyricsmith album` skips a linked MP3; name it with `--path` to transcribe it. A matching name
that is not a regular file (a FIFO, socket or device, judged by `os.stat`) is a `failed` row
and is never opened. A folder below `--path` that it cannot read is a `failed` row naming that
folder, never a silent gap; a `--path` folder that cannot itself be listed is a setup error,
exit `2`.

## What happens to each file

Each audio file's action is judged only at its own output location: beside the audio, or its
mirror under `--output-dir`.

| What is at the output location | Without `--overwrite` | With `--overwrite` |
| --- | --- | --- |
| no `.lrc` and no `.txt` | `transcribe` | `transcribe` |
| our `.lrc`, with or without a `.txt` | `skipped (has sidecar)` | `overwrite (ours)` |
| a `.lrc` that is not ours | `skipped (has sidecar)` | `refused (not ours)` |
| a `.txt` alone | `skipped (has sidecar)` | `refused (not ours)` |

A `.lrc` is **ours** when one of its header lines, the lines before its first timestamped
line, is exactly `[by:machine transcript]` once surrounding whitespace is stripped. A `.lrc`
that cannot be read counts as not ours, and so does one that is not a regular file: a FIFO,
socket or device under the name is opened without waiting on it and never read, even one
swapped in after the name was looked at. Only its first 64 KiB is read (`LRC_READ_CAP_BYTES` in
`src/at_plan.py`), far more than any header block, so a huge or sparse planted sidecar costs no
more; a by-line past that point is not judged ours. A `.txt` carries no label, so a `.txt` alone is never
ours and never replaced; a second run over the same folder skips every file it finished. A
sidecar is present when anything at all carries its name, a dangling symlink included, so a
link is skipped or refused like any sidecar and never silently replaced.

Two audio files can map onto one output pair: `one.mp3` and `one.m4a` under
`--extensions mp3,m4a`, `a.mp3` and `a.MP3` on a case-sensitive disk, or `a.mp3` and
`A.mp3` where the destination ignores case. The whole run is planned first, and each `.lrc`
is claimed once: the first claimant in sorted order keeps its planned action, and every later
one is a `failed` row naming the file that already writes that `.lrc`, in a real run and a dry
run alike. Names differing only by case are one claim only where the destination's
filesystem ignores case, which is probed at run time: the nearest existing folder at or above
the destination whose name has a case, compared with its case-swapped twin by
`os.path.samefile`. A probe that cannot answer, or a path with no cased folder name, counts
the names as one claim, because that can only fail a file, never let one pair replace another.

Each file is transcribed into a temporary dot-folder inside its destination folder, so every
rename stays on one filesystem and is atomic. The labeled `.lrc` is renamed into place first,
then the `.txt`: a failure between the two leaves an ours `.lrc` that `--overwrite` repairs,
never a `.txt` alone. The temporary folder is removed whether the file succeeds or fails, and
a failed file also removes any destination folder it created, an `--output-dir` mirror folder
among them, while that folder is empty.
Neither rename leaves a half-written file, so an interrupted run leaves no partial output; an
interrupt between the two renames is the same as that failure: an ours `.lrc` with no `.txt`,
which `--overwrite` repairs.

A file whose output location cannot be checked (a folder above it that cannot be searched)
is that file's `failed` row, and the run goes on.

## The label

whisper.cpp opens its `.lrc` with `[by:whisper.cpp]`. The tool replaces that header block with
two lines and keeps the timed body as it was:

```text
[re:lyricsmith transcribe 0.1.0; whisper.cpp 1.9.5; ggml-large-v3-turbo]
[by:machine transcript]
[00:00.26] An invented example line.
```

`[re:]` names the program that made the file and `[by:]` its author, the common LRC
convention, so players that know the tags show them and the rest ignore them. Every value is
read at run time: the tool's version from `[project].version` in the project's
`pyproject.toml`, through the project's manifest reader (`unknown` when it cannot be read;
`../lyricsmith_project/lyricsmith_project.md`), the engine's from `whisper-cli --version`,
and the model's name from its file's stem. A value's control, direction and separator
characters are spelled as escapes (`stdio_utf8.spell_unsafe`), as on the terminal, so a line
break in a model's file name cannot split the header. The `.txt` is left as whisper.cpp
wrote it.

A file is ours by its `[by:machine transcript]` line, never by its `[re:]` line.

`lyricsmith album` knows a transcript by the same `[by:machine transcript]` line, so a file
carrying it is a machine transcript to it: it still asks its timed providers, a timed answer
replaces the transcript, and with none the transcript is embedded and its `.lrc` kept byte
for byte (`../album/album.md`, *How a track's lyrics are found*, on machine-made `.lrc`
files).

## Beside the album tool

Where `lyricsmith album` runs, it removes a `.txt` that sits beside a timed `.lrc` and
embeds the same words, without the timestamps, as the untimed lyrics. The vanished `.txt` is
therefore not a fault, and nothing is lost.

## Fixed settings

Every engine call carries `--vad -vp 400 -vsd 600 -vspd 100 -mc 0`, and `-np` for a quiet
log:

```text
whisper-cli -m <model> -vm <vad-model> --vad -vp 400 -vsd 600 -vspd 100 -mc 0
            -l <language> -np -f <audio> -otxt -olrc -of <temp folder>/out
```

With whisper.cpp's defaults, the trial behind the design filled the quiet gaps of a spoken
recording with one invented line, repeated many times. `-mc 0` keeps no earlier text as
context, and `--vad` skips the gaps altogether; together they removed the repeats.

The detector's defaults cut too close on a stand-up recording, though: a short phrase said
into laughter fell between two speech stretches and was lost, and line start times drifted
around the laughter. Padding each stretch by 400 ms (`-vp`), splitting only at a pause of
600 ms or more (`-vsd`), and counting a stretch from 100 ms (`-vspd`) kept the phrase each
time it was said, with no repeated line, and gave the best line times of five settings
compared by ear on that recording. Turning the detector off also kept the phrase, with
slightly worse times.

None of these is an option: a new setting needs a new trial, proven on the same audio.

whisper-cli runs with this tool's own environment less one variable: the album tool's
Genius key, `GENIUS_ACCESS_TOKEN`. The engine decodes audio files of unknown origin, so a
key exported in the shell for `lyricsmith album` never reaches it.

## Output and progress

A transcribe run prints its rows to stdout as each file finishes: the outcome, padded to a
fixed column, then the audio's path relative to the `--path` folder (or a named file's own
folder). A failed file's row is followed by indented lines saying why: for an engine failure
(a non-zero exit, a timeout, or no `.lrc` and `.txt` left behind), what went wrong and the
last five non-blank lines of `whisper-cli`'s error output. The run ends with one count line
naming every outcome:

```text
transcribed            a/one.mp3
failed                 b/two.mp3
    whisper-cli exited 3
    error: failed to read audio
transcribed 1, overwritten 0, skipped 0, refused 0, failed 1, linked 0
```

`--dry-run` prints the setup check (the `whisper-cli` found, and each model present, missing
or unreadable) and then each file's planned action. It runs nothing, not even `whisper-cli
--version`, so it shows no engine version. A folder or output location it cannot read, or an
output pair another file already claims, is shown as a `failed` row and makes the dry run
exit `1`, unless the setup check failed. A folder with no matching file prints `no audio files
found under <path>` and exits `0`. `fetch-models` prints one row per model: `present`,
`downloaded`, or `failed` with its reason indented below.

Setup errors go to stderr, prefixed with the program's name. Every line that quotes a path or
an argument value has its control characters spelled as escapes (`\x1b`), and its direction
marks, embeddings, overrides, isolates and line and paragraph separators too (`\u202e`), by
the shared `stdio_utf8.spell_unsafe` the album tool uses, so a hostile file name can neither
steer the terminal nor print as another name. That includes argparse's own messages, such as
a stray argument's "unrecognized arguments" error, escaped line by line so the usage keeps
its shape.
The usage, help and errors are printed without argparse's color (Python 3.14 and later), whose
codes would otherwise show up as escaped text.

One whisper-cli call is bounded at three hours: the standard library cannot read an MP3's
duration, and a CPU-only machine running the large model can be slower than real time on a
long recording. A call past the bound is that file's failure, and the run goes on.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | every file transcribed, skipped (a linked one among them) or refused; under `fetch-models`, every model present or downloaded |
| `1` | any file failed (an output pair another file already claims, or a name that is not a regular file, among them), or a folder under `--path` could not be read; under `fetch-models`, any model failed |
| `2` | a setup error found before any file is touched: no working `whisper-cli` (missing, unreadable, not executable, or printing no version), a missing or unreadable model, a `--path` that does not exist, cannot be read, is not a folder or a regular file, or names a `.lrc` or `.txt`, or an unusable option value (`--extensions lrc` or `txt` among them) |

A shared library or a sibling tool's module that cannot be located, or that a same-named
module would shadow, also exits `2`, before anything runs: see *Exit codes* in
`../README.md`.

One file's failure is that file's row and never stops the run; so is an unreadable folder's.

## Tests

`test_transcribe.py` runs every suite under `tests/` and holds a floor per file, so a
suite emptied by accident fails loudly. Every suite that touches the filesystem, the
environment or a process derives from `IsolatedCase` in `tests/_isolation.py`: it points
`HOME` and the per-user data variables into a temporary folder, sets `PATH` to a folder holding
only that test's fakes, and makes the socket and urllib entry points raise. No test downloads
a model or reads a real library.

The black-box suites run `main()` against a stand-in `whisper-cli` that `write_fake_whisper`
writes: a script whose shebang is the running interpreter, which logs each call and writes an
invented `.lrc` and `.txt`, steered per test through environment variables to fail, to leave an
untimed `.lrc` or to print another version. Being a shebang script, it runs on POSIX only, so
those suites skip on Windows with that reason; every unit suite runs everywhere. Their shared
fixture is `CliCase` in `tests/_cli.py`. The unreadable-path cases patch `Path.exists` and
`Path.is_file` to raise, as Python 3.12 does under a folder that cannot be searched, because a
newer Python answers `False` there and a real locked folder would not exercise the 3.12 path.

The live smoke test runs the real `whisper-cli` on the `jfk.wav` sample whisper.cpp installs
beside itself, with the real models from the model folder, and skips with a stated reason when
either is absent, so a machine without whisper.cpp still gates. The hand check is in
`samples/README.md`.
