# build_sample_album.py

Builds the sample album that `README.md` beside this file walks through: six silent
MP3s, one timed `.lrc` sidecar and two untimed `.txt` sidecars, one of them holding only
a placeholder, every tag and every lyric line invented. It writes
the album through the same builders the tool's tests use, so the sample and the tests
cannot drift apart on what a silent track is.

| Track | Tags | Sidecar |
| --- | --- | --- |
| `01 The Keeper Counts.mp3` | The Invented Quartet, The Keeper Counts | `01 The Keeper Counts.lrc`, timed |
| `02 Orchard Wall.mp3` | The Invented Quartet, Orchard Wall | none |
| `03 The Mill Bell.mp3` | The Invented Quartet, The Mill Bell | none |
| `04 The Long Bit.mp3` | The Invented Quartet, The Long Bit, genre Stand-up Comedy | none |
| `05 The Short Bit.mp3` | The Invented Quartet, The Short Bit, genre Stand-up Comedy | `05 The Short Bit.txt`, untimed |
| `06 The Quiet Reel.mp3` | The Invented Quartet, The Quiet Reel | `06 The Quiet Reel.txt`, the placeholder `[au:instrumental]` alone |

Each track is 30 seconds of silent MPEG-1 Layer III frames at 128 kbps, tagged ID3v2.4.

## Usage

```text
build_sample_album.py --out <dir>
```

| Option | Default | Description |
| --- | --- | --- |
| `--out` | required | The album folder to build. It must be missing or empty, so a run cannot mix the sample with other files. |

## Exit codes

| Code | When |
| --- | --- |
| `0` | The album was built. |
| `2` | `--out` exists and is not an empty folder (a file, or a folder with anything in it), or an option was missing or malformed. |
