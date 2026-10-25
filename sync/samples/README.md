# Samples

The input is whisper.cpp's own bundled `jfk.wav` (public domain), read from the installed
whisper.cpp and never committed here, with its known words in `jfk.txt`.

Run the hand check from this tool's folder, because the `diff` path is relative, with the
interpreter that has the sync install and lyricsmith's own dependencies, and the models
fetched once (`sync.md`, *Prerequisites*):

```bash
SAMPLE="$(dirname "$(dirname "$(realpath "$(command -v whisper-cli)")")")/share/whisper.cpp/jfk.wav"
OUT="$(mktemp -d)"
cp "$SAMPLE" samples/jfk.txt "$OUT"
python sync.py --path "$OUT" --extensions wav
diff "$OUT/jfk.lrc" samples/jfk.lrc
```

| File | What it is |
| --- | --- |
| `jfk.txt` | The input: the clip's three lines, untimed |
| `jfk.lrc` | The expected output: the two label lines, then each line at its start |

The `.lrc`'s `[re:]` line names the tool's, demucs's and torchaudio's versions, so the `diff`
reports that line whenever an installed version differs. A stamp may also move by a few
milliseconds across machines.
