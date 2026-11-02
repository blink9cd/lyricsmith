# sy_child.py

The process that times one track for `sync.py`, which starts it once a track; nobody runs it
by hand. It is a process of its own so the parent's timeout can bound
PyTorch's calls, which take no timeout, and so the memory it needs, about 3 GB, goes back when
it exits. Before loading a model it makes every socket connection, datagram send and name
lookup in its own process fail. ffmpeg is handed the track as `file:` plus its absolute path,
with only the file protocol allowed, and with `-format_whitelist` naming only the audio
demuxers `DEMUXERS` in `sy_types.py` maps the allowed extensions to, so a track whose bytes
are an ffconcat or HLS playlist is refused rather than opening the files it names. It decodes
no more than the longest track's length. Before scoring, it runs `whisper-cli` on the vocal
stem, through `sy_whisper.py`, to check that the text is sung. The steps it takes are
`../sync.md`, *What happens to each track*.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--request` | required | the request file to read: the track, its letters as the model's label indexes, the model folder, the line each letter belongs to, `whisper-cli`'s absolute path and the whisper model folder |
| `--reply` | required | the reply file to write: what `whisper-cli` heard (the distinct word pairs and the share of them in the text), then the status, the frame count, each frame's seconds, and one span per letter; for a text not sung, the status and what was heard alone |

Both files are JSON, written and checked by `sy_request.py`; the parent checks every field of
the reply before trusting it.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | the reply was written |
| `2` | a missing or unknown option |

Any other failure, such as a decode error, a model that will not load or a crash in PyTorch,
ends the process with Python's own non-zero exit and a traceback on stderr; the parent reports
that track as `ERROR align` with the exit and the last lines of that output. A decode error
ends that output with ffmpeg's own last error lines, so a whitelist refusal reads
`Format not on whitelist` there rather than only ffmpeg's exit code. A `whisper-cli` that
exits non-zero, times out, cannot start or leaves no transcript ends it the same way, with
`whisper-cli`'s own last error lines: the sung check never passes a track it could not run.
A child the parent kills at its bound while `whisper-cli` runs leaves `whisper-cli` running,
orphaned, until it finishes its one track on its own; nothing waits for it, and the work
folder it writes into is the parent's, removed with the rest.
