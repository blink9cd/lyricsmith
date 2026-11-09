# lyricsmith sweep samples

A hand run of the sweep, `sweep/sweep.py` (what `lyricsmith sweep` runs), that needs no
network, no Genius key and no real music. The
library is built from `album`'s own sample album: silent MP3s tagged with an invented
artist and invented titles, two of them with the genre `Stand-up Comedy`, one invented
timed `.lrc` sidecar, one invented untimed `.txt` sidecar and one `.txt` holding only a
placeholder, and two more invented untimed `.txt` sidecars written below. Nothing here is
committed audio, and the run is `--offline`, so `album` asks no provider. The silence pass
in step 4 needs `ffmpeg` on `PATH`, which only decodes the local files.

The suite `tests/test_sweep_samples.py` runs the build block below in `bash`, then these
same commands, and fails when a command or what it prints stops matching the blocks below,
placeholders filled in.

## Build the library

The builder and the tool need the project's dependencies (`[project].dependencies`). Build
the library somewhere outside the repository:

```bash
TOOLS="$PWD"  # run from this project's root folder
cd "$(mktemp -d)" || return
python "$TOOLS/album/samples/build_sample_album.py" \
  --out "library/The Invented Quartet/Sample Album"
mkdir -p "library/The Invented Quartet/Second Album" \
  "library/The Made-Up Trio/Untimed Album"
cp "library/The Invented Quartet/Sample Album/01 The Keeper Counts."* \
  "library/The Invented Quartet/Second Album/"
python "$TOOLS/album/album.py" \
  --album "library/The Invented Quartet/Second Album" --offline
rm -r "library/The Invented Quartet/Second Album/.lyricsmith"
cp "library/The Invented Quartet/Sample Album/02 Orchard Wall.mp3" \
  "library/The Invented Quartet/Sample Album/03 The Mill Bell.mp3" \
  "library/The Made-Up Trio/Untimed Album/"
printf 'Over the orchard wall\nthe ladders lean and wait\n' \
  > "library/The Made-Up Trio/Untimed Album/02 Orchard Wall.txt"
printf 'The mill bell rings at noon\nand nobody comes home\n' \
  > "library/The Made-Up Trio/Untimed Album/03 The Mill Bell.txt"
```

Two artists, three albums:

| Album | Tracks |
| --- | --- |
| `The Invented Quartet/Sample Album` | one synced (its `.lrc`), one untimed stand-up track (its `.txt`), one whose `.txt` holds only the placeholder `[au:instrumental]`, three with no lyrics, one of them stand-up |
| `The Invented Quartet/Second Album` | one synced (its `.lrc`, already embedded in its frames): every track synced, so no run visits it |
| `The Made-Up Trio/Untimed Album` | two untimed (their `.txt`) |

The build runs `album` offline on `Second Album` once, so the track's frames hold its
`.lrc`, then removes that run's `.lyricsmith` folder: the album looks like one
embedded long ago. Without the embed, a timed `.lrc` beside frames holding no timed
lyrics owes one, and the survey would visit the album for it.

Each `sweep` command below sets `XDG_STATE_HOME` for itself alone, to a `state`
folder beside the library, so the run's ledger stays there rather than in your own state
directory, and a real sweep later run from the same shell still keeps its ledger in the
usual place.

## 1. The survey

```bash
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \
  --library library --mode survey
```

```text
album                              synced  untimed  none  unreadable
The Invented Quartet/Sample Album       1        2     3           0
The Invented Quartet/Second Album       1        0     0           0
The Made-Up Trio/Untimed Album          0        2     0           0
total (3 albums)                        2        4     3           0
unembedded: The Invented Quartet/Sample Album/01 The Keeper Counts.mp3 (unlabeled)
albums a run would visit: 2
  The Invented Quartet/Sample Album
  The Made-Up Trio/Untimed Album
note: an album whose lyrics were embedded by other tools may have each track
  saved once by its first run, even where the words stay the same: the frames
  written from a timed sidecar keep blank lines those tools left out. In a
  library kept in a synced cloud folder, that is one upload per track.
```

The `unembedded:` line names the synced track whose timed `.lrc` its frames do not hold
yet: no machine labeled the file, so it shows as `unlabeled`, and a run embeds it. Its
album is visited for its other tracks anyway.

Exit code `0`. It wrote nothing: there is no `.lyricsmith/` folder in any album, and no
`state/` folder beside the library yet.

## 2. An offline run

```bash
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \
  --library library --mode run --offline --pause-seconds 0
```

`album`'s own progress and summary, and the sweep's `[1/2]` progress lines, appear on
stderr as it runs. The report on stdout:

```text
already done: 0
album                                           run  exit  outcome  written  unchanged  not found  removed  no lyrics expected  silent  kept  errors  flagged  refusing
The Invented Quartet/Sample Album        <run-id-1>     0  offline        2          0          2        1                   1       0     0       0        1         0
The Made-Up Trio/Untimed Album           <run-id-2>     0  offline        2          0          0        0                   0       0     0       0        0         0
written: embedded 4
removed: placeholder 1
The Invented Quartet/Sample Album  undo: album.py --album '<library>/The Invented Quartet/Sample Album' --mode undo --run-id <run-id-1>
The Made-Up Trio/Untimed Album  undo: album.py --album '<library>/The Made-Up Trio/Untimed Album' --mode undo --run-id <run-id-2>
finished; albums left: 0
```

Exit code `0`. In the first album, the two tracks with a sidecar were served from it and
written, and the other three have no lyrics to find offline: two end not found, and the
stand-up one ends no lyrics expected, in a column of its own. The track whose `.txt` held
only a placeholder found nothing either, so the run removed the placeholder, and it
counts in `removed`; the survey counts it untimed, by what its file holds. The
stand-up track served from its `.txt` counts in `flagged`, for holding lyrics on a genre
that expects none. In the untimed album, both tracks were served from their `.txt` and
written. `Second Album` was never visited. Below the table, `written:` says all four
written tracks embedded their own sidecars, and `removed:` says why the one removed went,
in the album summary's words.

That is the report with no `ffmpeg` on `PATH`. With one, the three tracks nothing answered
are measured, read as silence, and count in `silent` instead: not found 0, no lyrics
expected 0, silent 3, and removed still 1 (the placeholder, which is never measured).

The ledger, `state/lyricsmith/sweep/ledger-<hash>.jsonl`, holds one line for each album visited.
Its outcome is `offline`, not `done`: no provider was asked, so running the same command
again visits both albums again, and so would a sweep without `--offline`.

## 3. Undo

Each undo line is `album`'s own command for that album's run. Run it with the tool's
path in front, putting each album's run id from the report in place of its placeholder:

```bash
python "$TOOLS/album/album.py" \
  --album "library/The Invented Quartet/Sample Album" --mode undo --run-id "<run-id-1>"
python "$TOOLS/album/album.py" \
  --album "library/The Made-Up Trio/Untimed Album" --mode undo --run-id "<run-id-2>"
```

```text
album undo <run-id-1>: 6 tracks under Sample Album

 #  track                     undo
 1  01 The Keeper Counts.mp3  undone
 2  02 Orchard Wall.mp3       undone
 3  03 The Mill Bell.mp3      undone
 4  04 The Long Bit.mp3       undone
 5  05 The Short Bit.mp3      undone
 6  06 The Quiet Reel.mp3     undone
undone 6 · already undone 0 · errors 0
```

```text
album undo <run-id-2>: 2 tracks under Untimed Album

 #  track                 undo
 1  02 Orchard Wall.mp3   undone
 2  03 The Mill Bell.mp3  undone
undone 2 · already undone 0 · errors 0
```

Each exits `0`, and the albums hold exactly the files they were built with, byte for byte,
beside the run folders `album` keeps under `.lyricsmith/`. The ledger is left as
it was: undo is `album`'s own, and the sweep is not told. Once the runs are no longer
wanted, `--mode accept` over the same library reports what would go from each album, and
with `--apply` removes every album's `.lyricsmith/`. Give it the same
`XDG_STATE_HOME="$PWD/state"` as the commands above, so it copies each manifest into
`state/lyricsmith/accepted/` first and not into your own state folder.

## 4. The silence pass

`--mode silence` measures every track of every album with `ffmpeg`, whatever its lyrics,
and lists the silent ones; it needs `ffmpeg` on `PATH`. Start with a preview of one album:

```bash
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \
  --library library --mode silence --limit 1
```

The `[1/1]` progress line appears on stderr. On stdout:

```text
already done: 0
albums measured: 1
tracks measured: 6
silent: 6
  likely meant: 0
  likely a ripping mistake: 6
  unsure: 0
not measured: 0
limit reached; albums left: 2
list: <list>
page: <page>
resume: sweep.py --mode silence --library <library> --resume <list>
```

Exit code `0`. Every track of the sample is silent: the sample stands silent MP3s in for
songs. Each has a real title and lasts 30 seconds, so each reads as a likely ripping
mistake, a song that came out silent. The resume line measures the other two albums
into the same list. Run it with the tool's path in front, placeholders filled in:

```bash
XDG_STATE_HOME="$PWD/state" python "$TOOLS/sweep/sweep.py" \
  --mode silence --library "<library>" --resume "<list>"
```

```text
already done: 1
albums measured: 2
tracks measured: 3
silent: 3
  likely meant: 0
  likely a ripping mistake: 3
  unsure: 0
not measured: 0
finished; albums left: 0
list: <list>
page: <page>
```

Exit code `0`. The list, a tab-separated file under `state/lyricsmith/silence/`, now holds
its header, then nine rows reading `likely a ripping mistake`, `real title, 30.0 s`, a
peak of `-91.0` dBFS and a length of `30.0`, with each track's title and path, and after
each album's rows a line `#album-done` naming the album. The page beside it, `<page>`,
shows the same nine tracks under "Likely a ripping mistake", both legs included, and the
other three verdicts with a count of 0; open it in a browser. Running the resume command
again measures nothing and only writes the page again. The library is unchanged.

## Comparing your output

Five values vary from run to run, and the blocks above show them as placeholders:

| Placeholder | What it stands for |
| --- | --- |
| `<run-id-1>`, `<run-id-2>` | Each album's run id, the UTC time its run started, as `YYYYMMDDTHHMMSSZ`. The two are the same when both runs started within one second. In the report's table the column is as wide as the id, 16 characters. |
| `<library>` | The library's full path, which is inside the folder `mktemp -d` made. |
| `<list>` | The silence list's full path: `state/lyricsmith/silence/silent-<stamp>.tsv` beside the library, named by the UTC time the preview started, as `YYYYMMDDTHHMMSSZ`. |
| `<page>` | The silence list's HTML page: the list's full path with `.html` in place of `.tsv`. |

Put your own values in for the placeholders, then compare line by line: everything else
should match exactly.
