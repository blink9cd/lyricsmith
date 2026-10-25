# lyricsmith sweep samples

A hand run of the sweep, `sweep/sweep.py` (what `lyricsmith sweep` runs), that needs no
network, no Genius key and no real music. The
library is built from `album`'s own sample album: silent MP3s tagged with an invented
artist and invented titles, two of them with the genre `Stand-up Comedy`, one invented
timed `.lrc` sidecar, one invented untimed `.txt` sidecar and one `.txt` holding only a
placeholder, and two more invented untimed `.txt` sidecars written below. Nothing here is
committed audio, and the run is `--offline`, so `album` asks no provider.

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
| `The Invented Quartet/Second Album` | one synced (its `.lrc`): every track synced, so no run visits it |
| `The Made-Up Trio/Untimed Album` | two untimed (their `.txt`) |

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
albums a run would visit: 2
  The Invented Quartet/Sample Album
  The Made-Up Trio/Untimed Album
note: an album whose lyrics were embedded by other tools may have each track
  saved once by its first run, even where the words stay the same: the frames
  written from a timed sidecar keep blank lines those tools left out. In a
  library kept in a synced cloud folder, that is one upload per track.
```

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
album                                           run  exit  outcome  written  unchanged  not found  no lyrics expected  silent  kept  errors  flagged  refusing
The Invented Quartet/Sample Album        <run-id-1>     0  offline        2          0          3                   1       0     0       0        1         0
The Made-Up Trio/Untimed Album           <run-id-2>     0  offline        2          0          0                   0       0     0       0        0         0
The Invented Quartet/Sample Album  undo: album.py --album '<library>/The Invented Quartet/Sample Album' --mode undo --run-id <run-id-1>
The Made-Up Trio/Untimed Album  undo: album.py --album '<library>/The Made-Up Trio/Untimed Album' --mode undo --run-id <run-id-2>
finished; albums left: 0
```

Exit code `0`. In the first album, the two tracks with a sidecar were served from it and
written, and the other three have no lyrics to find offline: two end not found, and the
stand-up one ends no lyrics expected, in a column of its own. The track whose `.txt` held
only a placeholder found nothing either, so the run removed the placeholder, and it
counts as not found too; the survey counts it untimed, by what its file holds. The
stand-up track served from its `.txt` counts in `flagged`, for holding lyrics on a genre
that expects none. In the untimed album, both tracks were served from their `.txt` and
written. `Second Album` was never visited.

That is the report with no `ffmpeg` on `PATH`. With one, the three tracks nothing answered
are measured, read as silence, and count in `silent` instead: not found 1 (the removed
placeholder, which is never measured), no lyrics expected 0, silent 3.

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
it was: undo is `album`'s own, and the sweep is not told.

## Comparing your output

Three values vary from run to run, and the blocks above show them as placeholders:

| Placeholder | What it stands for |
| --- | --- |
| `<run-id-1>`, `<run-id-2>` | Each album's run id, the UTC time its run started, as `YYYYMMDDTHHMMSSZ`. The two are the same when both runs started within one second. In the report's table the column is as wide as the id, 16 characters. |
| `<library>` | The library's full path, which is inside the folder `mktemp -d` made. |

Put your own values in for the placeholders, then compare line by line: everything else
should match exactly.
