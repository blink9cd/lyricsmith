# Samples

The input is whisper.cpp's own bundled `jfk.wav`, read from the installed whisper.cpp and
never committed here. It is in the public domain as a US government recording: an excerpt of
President Kennedy's 1961 inaugural address, a work of the federal government made in the
course of official duties.

Run the hand check from the project folder, because the `diff` paths are relative, with any
Python 3.12 or later (`python3.12` below; macOS's own `python3` is older and lacks `tomllib`):

```bash
SAMPLE="$(dirname "$(dirname "$(realpath "$(command -v whisper-cli)")")")/share/whisper.cpp/jfk.wav"
OUT="$(mktemp -d)"
python3.12 transcribe.py --path "$SAMPLE" --output-dir "$OUT"
diff "$OUT/jfk.lrc" samples/jfk.lrc && diff "$OUT/jfk.txt" samples/jfk.txt
```

| File | What it is |
| --- | --- |
| `jfk.lrc` | The expected timed transcript: the two label lines, then whisper.cpp's timestamped lines |
| `jfk.txt` | The expected plain transcript, left exactly as whisper.cpp wrote it |

The `.lrc`'s `[re:]` line names the whisper.cpp version the sample was made with, so the first
`diff` reports that line whenever the installed version differs.
