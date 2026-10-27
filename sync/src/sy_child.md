# sy_child.py

The process that times one track for `sync.py`, which starts it once a track; nobody runs it
by hand. It is a process of its own so the parent's timeout can bound
PyTorch's calls, which take no timeout, and so the memory it needs, about 3 GB, goes back when
it exits. Before loading a model it makes every socket connection, datagram send and name
lookup in its own process fail. ffmpeg is handed the track as `file:` plus its absolute path,
with only the file protocol allowed, and with `-format_whitelist` naming only the audio
demuxers `DEMUXERS` in `sy_types.py` maps the allowed extensions to, so a track whose bytes
are an ffconcat or HLS playlist is refused rather than opening the files it names. It decodes
no more than the longest track's length. The steps it takes are `../sync.md`, *What happens to each track*.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--request` | required | the request file to read: the track, its letters as the model's label indexes, the model folder |
| `--reply` | required | the reply file to write: the status, the frame count, each frame's seconds, and one span per letter |

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
`Format not on whitelist` there rather than only ffmpeg's exit code.
