# lyricsmith samples

The dispatcher turns no input into output of its own: each command is its tool, so each
tool's samples are the hand run. Each runs the tool's entry file directly, and the same
steps run through the dispatcher by putting `python lyricsmith.py COMMAND` in place of the
entry file, from this project's root folder. The printed program name then reads
`lyricsmith album` (or `lyricsmith sweep`, `lyricsmith transcribe`, `lyricsmith sync`)
where the samples show the entry file's name; nothing else in the output changes. The
paths a sample names relative to its tool's folder gain that folder from the root:
`transcribe`'s `diff` reads `transcribe/samples/jfk.lrc` and `transcribe/samples/jfk.txt`
there.

| Command | Its samples |
| --- | --- |
| `album` | `album/samples/README.md`: an invented sample album, built locally, run offline, checked and undone |
| `sweep` | `sweep/samples/README.md`: a survey, an offline run and each album's undo over a small invented library |
| `transcribe` | `transcribe/samples/README.md`: a public-domain clip transcribed, with the `.lrc` and `.txt` it gives |
| `sync` | `sync/samples/README.md`: the public-domain clip timed from its known text, with the `.lrc` it gives |

A check that the dispatcher reaches each tool, which changes nothing. Run it from this
project's root folder with Python 3.12 or later and the project's dependencies installed
(`README.md`, "Install"):

```bash
python lyricsmith.py --version
python lyricsmith.py album --help | head -n 1
```

The first prints `lyricsmith.py` and the project version; the second prints the album tool's
usage line, starting `usage: lyricsmith album`.
