# lyricsmith album samples

A hand run of `lyricsmith album` that needs no network, no Genius key and no real music.
`build_sample_album.py` builds a six-track album of silent MP3s, tagged with an
invented artist and invented titles, and puts one invented timed `.lrc` sidecar beside
the first track. The fourth and fifth tracks are tagged with the genre `Stand-up
Comedy`, and the fifth has an invented untimed `.txt` sidecar beside it. The sixth has a
`.txt` holding only `[au:instrumental]`, an LRC header tag: the placeholder a provider
may send in place of lyrics. Every lyric line in them was written for this sample.

The album is built when you run the builder, never committed: no audio file lives in
this folder.

## Build the album

The builder needs the project's dependencies (`[project].dependencies`), as the tool
does. It takes one option, `--out`, the album folder to build, which must be missing or
empty. Build it somewhere outside the repository, so nothing the run writes lands in the
tree:

```bash
ALBUM_DIR="$PWD"  # run from the album folder
cd "$(mktemp -d)" || return
python "$ALBUM_DIR/samples/build_sample_album.py" --out sample_album
```

`sample_album/` now holds:

```text
01 The Keeper Counts.lrc
01 The Keeper Counts.mp3
02 Orchard Wall.mp3
03 The Mill Bell.mp3
04 The Long Bit.mp3
05 The Short Bit.mp3
05 The Short Bit.txt
06 The Quiet Reel.mp3
06 The Quiet Reel.txt
```

Each MP3 plays 30 seconds of silence. The timed sidecar's last line starts at 25.75 seconds,
so it passes every timing row of the check.

## 1. A dry run

```bash
python "$ALBUM_DIR/album.py" --album sample_album --dry-run --offline
```

```text
album dry run: 6 tracks under sample_album

 #  track                     plan
 1  01 The Keeper Counts.mp3  sidecar (synced)
 2  02 Orchard Wall.mp3       nothing (offline, no sidecar or frames)
 3  03 The Mill Bell.mp3      nothing (offline, no sidecar or frames)
 4  04 The Long Bit.mp3       nothing (offline, no sidecar or frames)
 5  05 The Short Bit.mp3      sidecar (unsynced)
 6  06 The Quiet Reel.mp3     fetch, else remove (implausible: placeholder lyrics)
errors 0 · genius: offline
dry run: nothing was written
```

Exit code `0`. Nothing is written: there is no `sample_album/.lyricsmith/` yet.

## 2. An offline run, with ffmpeg

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode run --offline
```

```text
album run 20261016T034745Z: 6 tracks under sample_album

 #  track                     source   kind      check
 1  01 The Keeper Counts.mp3  sidecar  synced    PASS
 2  02 Orchard Wall.mp3       -        -         silent (peak -91.0 dBFS)
 3  03 The Mill Bell.mp3      -        -         silent (peak -91.0 dBFS)
 4  04 The Long Bit.mp3       -        -         silent (peak -91.0 dBFS)
 5  05 The Short Bit.mp3      sidecar  unsynced  FLAG: lyrics on a no-lyrics genre (Stand-up Comedy)
 6  06 The Quiet Reel.mp3     -        -         removed: implausible old lyrics (placeholder lyrics)
synced 1 · unsynced 1 · unchanged 0 · not found 0 · no lyrics expected 0 · silent 3 · kept existing 0 · removed 1 · errors 0 · flagged 1 · genius: offline
written: embedded 2
removed: placeholder 1
bandcamp: off (offline)
youtube music: off (offline)
tekstowo: off (offline)
silence check: on
providers: LRCLIB - · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -
backup: .lyricsmith/20261016T034745Z/   undo: album.py --album <album> --mode undo
```

Exit code `0`; a track with no lyrics found is not an error. This run found `ffmpeg` on
`PATH` (an optional program, `brew install ffmpeg`; the section after this step shows the
run without it), so it measured every track no provider answered and found the sound
level of each below the threshold: the second, third and fourth tracks are `silent`
rather than `not found`, the peak shown in dBFS. Silence is judged before the genre, so
the fourth track, tagged `Stand-up Comedy`, ends `silent` too; on a track that sounds
it would end `no lyrics expected`. The sample's MP3s are digital silence, which is why
all three read so low. The fifth is served from its `.txt` and embedded as usual,
then flagged `lyrics on a no-lyrics genre`, which counts in `flagged` but removes
nothing. The sixth track's `.txt` holds a placeholder, which reads as no lyrics held:
the run asks the providers as for a track with none, and offline nothing answers, so
the placeholder is removed, `removed: implausible old lyrics (placeholder lyrics)`. The
backup holds it, so the undo below puts it back. Under the counts, `written: embedded 2`
says both written tracks embedded their own sidecars, and `removed: placeholder 1` why
the sixth track's lyrics went. The `bandcamp:`, `youtube music:` and
`tekstowo:` lines say each of those steps was off, and why, and `silence check: on` says
the measurement ran. The `providers:` line shows `-` for every provider, since an offline
run asks none; a run that asks them shows each one's tracks won over tracks asked, and how
many refused, in brackets, when any did.
Stderr shows the progress:

```text
backing up 6 tracks
[1/6] 01 The Keeper Counts.mp3: synced (sidecar)
[2/6] 02 Orchard Wall.mp3: silent (peak -91.0 dBFS)
[3/6] 03 The Mill Bell.mp3: silent (peak -91.0 dBFS)
[4/6] 04 The Long Bit.mp3: silent (peak -91.0 dBFS)
[5/6] 05 The Short Bit.mp3: unsynced (sidecar)
[6/6] 06 The Quiet Reel.mp3: removed: implausible old lyrics (placeholder lyrics)
```

### Without ffmpeg

With no `ffmpeg` on `PATH` the run never measures sound, and nothing else changes: the
same command prints this instead: the second and third tracks are `not found`, the fourth
(by its genre) is `no lyrics expected`, the `silent` count is `0`, and the `silence check:`
line says why it is off. The exit code is `0` as before.

```text
album run 20261016T034745Z: 6 tracks under sample_album

 #  track                     source   kind      check
 1  01 The Keeper Counts.mp3  sidecar  synced    PASS
 2  02 Orchard Wall.mp3       -        -         not found
 3  03 The Mill Bell.mp3      -        -         not found
 4  04 The Long Bit.mp3       -        -         no lyrics expected
 5  05 The Short Bit.mp3      sidecar  unsynced  FLAG: lyrics on a no-lyrics genre (Stand-up Comedy)
 6  06 The Quiet Reel.mp3     -        -         removed: implausible old lyrics (placeholder lyrics)
synced 1 · unsynced 1 · unchanged 0 · not found 2 · no lyrics expected 1 · silent 0 · kept existing 0 · removed 1 · errors 0 · flagged 1 · genius: offline
written: embedded 2
removed: placeholder 1
bandcamp: off (offline)
youtube music: off (offline)
tekstowo: off (offline)
silence check: off (no ffmpeg)
providers: LRCLIB - · NetEase - · YouTube Music - · Bandcamp - · Genius - · Tekstowo -
backup: .lyricsmith/20261016T034745Z/   undo: album.py --album <album> --mode undo
```

Stderr shows the progress:

```text
backing up 6 tracks
[1/6] 01 The Keeper Counts.mp3: synced (sidecar)
[2/6] 02 Orchard Wall.mp3: not found
[3/6] 03 The Mill Bell.mp3: not found
[4/6] 04 The Long Bit.mp3: no lyrics expected
[5/6] 05 The Short Bit.mp3: unsynced (sidecar)
[6/6] 06 The Quiet Reel.mp3: removed: implausible old lyrics (placeholder lyrics)
```

**Two parts of this report differ on every run.** The run id, `20261016T034745Z` above,
is the run's start time in UTC, so it is different each time; it appears in the first
line and the last. The last line's `<album>` stands for the album's full path, resolved
once at the start of the run, which depends on where you built it; it is the one edit
made to the report as captured.

The first track now holds a `SYLT` and a `USLT` frame with the sidecar's words, and its
`.lrc` is left as it was; the fifth holds a `USLT` frame with its `.txt`'s words. The run
folder, `sample_album/.lyricsmith/<run-id>/`, holds `manifest.json` and, under
`sidecars/`, the copies of the three sidecars taken before anything changed. The sixth
track's `.txt` is gone.

## 3. The check

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode check
```

```text
album check 20261016T034745Z: 6 tracks under sample_album

 #  track                     check
 1  01 The Keeper Counts.mp3  PASS
 2  02 Orchard Wall.mp3       -
 3  03 The Mill Bell.mp3      -
 4  04 The Long Bit.mp3       -
 5  05 The Short Bit.mp3      PASS
 6  06 The Quiet Reel.mp3     -
passed 2 · flagged 0 · skipped 0 · not checked 4 · errors 0
```

Exit code `0`. Only the tracks the run wrote are checked; the run id in the first line
is the newest run's. The check judges the embedded lyrics against the backup and never
reads the genre, so the fifth track passes here with no genre flag: that flag is the
run's, kept in its manifest. The sixth track was removed, not written, so it is not
checked.

## 4. Undo

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode undo
```

```text
album undo 20261016T034745Z: 6 tracks under sample_album

 #  track                     undo
 1  01 The Keeper Counts.mp3  undone
 2  02 Orchard Wall.mp3       undone
 3  03 The Mill Bell.mp3      undone
 4  04 The Long Bit.mp3       undone
 5  05 The Short Bit.mp3      undone
 6  06 The Quiet Reel.mp3     undone
undone 6 · already undone 0 · errors 0
```

Exit code `0`. No `displaced:` note appears: the run wrote no sidecar, so undo had none
to set aside.

## What you should see after the undo

The album is back in its built state: the same nine files, each with the same bytes
as before the run. Hash them before step 2 and
again after step 4 to see it:

```bash
find sample_album -name .lyricsmith -prune -o -type f -exec shasum -a 256 {} + | sort
```

The two listings match. The hidden `sample_album/.lyricsmith/` folder, holding the run
folder and the lock, stays after an undo. Once you no longer need its runs, `--mode accept`
removes it: run it first without `--apply` to read what would go, then again with
`--apply`. It copies each run's manifest to a records folder under your state folder, so
for a sample run point `XDG_STATE_HOME` at a scratch folder first:

```bash
XDG_STATE_HOME="$(mktemp -d)" python "$ALBUM_DIR/album.py" --album sample_album --mode accept
```

## 5. Strip

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode strip
```

```text
album strip 20261016T034748Z: 6 tracks under sample_album

 #  track                     removed
 1  01 The Keeper Counts.mp3  .lrc
 2  02 Orchard Wall.mp3       nothing to remove
 3  03 The Mill Bell.mp3      nothing to remove
 4  04 The Long Bit.mp3       nothing to remove
 5  05 The Short Bit.mp3      .txt
 6  06 The Quiet Reel.mp3     .txt
stripped 6 · errors 0
backup: .lyricsmith/20261016T034748Z/   undo: album.py --album <album> --mode undo
```

Exit code `0`. Stderr shows the progress:

```text
backing up 6 tracks
[1/6] 01 The Keeper Counts.mp3: stripped (.lrc)
[2/6] 02 Orchard Wall.mp3: stripped (nothing to remove)
[3/6] 03 The Mill Bell.mp3: stripped (nothing to remove)
[4/6] 04 The Long Bit.mp3: stripped (nothing to remove)
[5/6] 05 The Short Bit.mp3: stripped (.txt)
[6/6] 06 The Quiet Reel.mp3: stripped (.txt)
```

A strip is a run of its own, with its own run id and backup, taken the same way before
anything is removed. The undo in step 4 already took the frames away, so only the first
track's `.lrc` and the fifth's and sixth's `.txt` are left to remove; on a track that
still had lyrics frames the row would name `frames` as well. The album now holds the six
MP3s and no sidecar.

## 6. Undo the strip

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode undo
```

```text
album undo 20261016T034748Z: 6 tracks under sample_album

 #  track                     undo
 1  01 The Keeper Counts.mp3  undone
 2  02 Orchard Wall.mp3       undone
 3  03 The Mill Bell.mp3      undone
 4  04 The Long Bit.mp3       undone
 5  05 The Short Bit.mp3      undone
 6  06 The Quiet Reel.mp3     undone
undone 6 · already undone 0 · errors 0
```

Exit code `0`. Undo takes the newest run, the strip, and puts the `.lrc` and both `.txt`
files back from its backup, so the album is in its built state again and the hash listing above
matches once more.

## 7. One track: strip it, plan it again, undo it

`--track` narrows a strip or a run to one MP3 of the album, the way to replace one track's
lyrics after `--mode compare` flags them. The path is resolved against the working
directory, as a shell path is, so it names the album folder too. Strip the fifth track
alone:

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode strip \
  --track "sample_album/05 The Short Bit.mp3"
```

```text
album strip 20261021T151338Z: 1 track under sample_album

 #  track                 removed
 1  05 The Short Bit.mp3  .txt
stripped 1 · errors 0
backup: .lyricsmith/20261021T151338Z/   undo: album.py --album <album> --mode undo
```

Exit code `0`. The strip is a run of its own, as in step 5, but it backs up, records and
strips the one track: the report has one row, the counts cover that track alone, and the
first track's `.lrc` is still there. A run with the same `--track` would then fetch that
one track again; offline, a dry run shows what it would find:

```bash
python "$ALBUM_DIR/album.py" --album sample_album --dry-run --offline \
  --track "sample_album/05 The Short Bit.mp3"
```

```text
album dry run: 1 track under sample_album

 #  track                 plan
 1  05 The Short Bit.mp3  nothing (offline, no sidecar or frames)
errors 0 · genius: offline
dry run: nothing was written
```

Exit code `0`, and nothing is written, so the strip is still the newest run. Undo it:

```bash
python "$ALBUM_DIR/album.py" --album sample_album --mode undo
```

```text
album undo 20261021T151338Z: 1 track under sample_album

 #  track                 undo
 1  05 The Short Bit.mp3  undone
undone 1 · already undone 0 · errors 0
```

Exit code `0`. The `.txt` is back, and the hash listing above matches again.

## Not shown: compare

`--mode compare` asks each provider in `COMPARED`, each on its own, for every
untimed-only track, and reports whether an answer from a provider other than the lyrics'
source agrees; it writes nothing. It needs the network, and the sample holds no real song for an answer to agree
with, so this walkthrough does not run it: its hand check is the acceptance run,
made by hand on a real album:

```bash
python "$ALBUM_DIR/album.py" --album <album> --mode compare
```

A FLAG row is followed by the one-track strip command for its track, ready to copy; that
strip, then `--mode run --track` on the same file, `--mode check`, and `--mode undo` when
the new answer is no better, is the loop whose strip and undo step 7 walks offline.

## Not shown: a fresh run

`--mode run --fresh` asks the providers for every track, even one a timed sidecar would
serve, and replaces a track's old frames and sidecars only once an answer is accepted; a
track with no accepted answer keeps what it had, unless that was only a placeholder, as
in step 2, and an answer unlike the old lyrics is
declined, so it keeps them too. It needs the network, so this offline
walkthrough does not run it, and `--fresh` is refused together with `--offline`.
