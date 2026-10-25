# lyricsmith

Four tools for an album's lyrics, run through one command: `lyricsmith album` finds an
album's lyrics, embeds them and checks them, `lyricsmith sweep` runs `album` over a whole
library, `lyricsmith transcribe` transcribes audio offline with whisper.cpp, and
`lyricsmith sync` times a track's untimed lyrics against its own audio, offline.

`lyricsmith.py` is a thin dispatcher. Its own parser reads only the command name and
`--version`; everything after the command goes untouched to that tool's `main()`, whose
parser and companion doc stay the authority on its options. Each tool still runs alone as
`<command>/<command>.py`, and only the chosen tool's code is loaded.

Run through the dispatcher, a tool names itself by its command: its help reads
`usage: lyricsmith album ...`, its report header `lyricsmith album dry run: ...`, its
notices `lyricsmith album: ...`. The commands it prints for pasting back name it the same
way, as separate shell words: an album run's or strip's undo line and a compare's strip
lines start `lyricsmith album`, a sweep's undo lines `lyricsmith album` and its resume
line `lyricsmith sweep`. lyricsmith installs no `lyricsmith` command yet (see "Installing"),
so the `lyricsmith album` and `lyricsmith sweep` in those notices and hints stand for the
`python lyricsmith.py album` and `python lyricsmith.py sweep` invocations. Run alone,
each tool names its entry file, as one shell word whatever its name holds.

## Commands

| Command | Runs | What it does | Its options and behavior |
| --- | --- | --- | --- |
| `album` | `album/album.py` | Finds an album's lyrics, embeds them and checks them; strips them; undoes a run | `album/album.md` |
| `sweep` | `sweep/sweep.py` | Runs `album` over every album of a library holding a track without synced lyrics | `sweep/sweep.md` |
| `transcribe` | `transcribe/transcribe.py` | Transcribes audio offline into a timed `.lrc` and a plain `.txt` with whisper.cpp | `transcribe/transcribe.md` |
| `sync` | `sync/sync.py` | Times a track's untimed lyrics against its own audio, offline, into a labeled `.lrc` | `sync/sync.md` |

`python lyricsmith.py COMMAND --help` prints that tool's own help: the same text its entry file
prints, apart from the program name.

## Installing

lyricsmith needs Python 3.12 or later. From a clone of this repository:

```bash
pip install .
```

installs the pinned dependencies `album` and `sweep` need, listed under
`[project].dependencies` in `pyproject.toml`. To run the test suites as well, install the
`test` extra, which adds the one test-only library:

```bash
pip install ".[test]"
```

Either way, installing puts no code anywhere and installs no `lyricsmith` command: run
`python lyricsmith.py COMMAND` from the clone's root folder, with the interpreter you
installed into.

- **`transcribe`** needs none of those dependencies, but it needs whisper.cpp's
  `whisper-cli` program and its two models, fetched once with
  `python transcribe/transcribe.py --mode fetch-models`, as `transcribe/transcribe.md`
  ("Prerequisites") describes.
- **`sync`** needs its PyTorch stack, pinned in `sync/requirements-sync.txt` and kept out of
  the extras so that installing the other tools never pulls PyTorch in:
  `pip install -r sync/requirements-sync.txt`. It needs ffmpeg on `PATH` too, and its three
  models, fetched once with `python sync/sync.py --mode fetch-models`, as `sync/sync.md`
  ("Prerequisites") describes.

The tools import six shared libraries, each a folder at the top of this repository:
`stdio_utf8`, `help_formatter`, `run_lock`, `bounded_run`, `library_walk`, and
`live_pid_guard`, which `bounded_run`'s tests use. Each tool puts each library on
`sys.path` from one fixed place: the folder of that name directly under the repository
root, derived from the tool's own file. Nothing searches the folders above the clone, so a
same-named folder there, in a shared temporary folder or the home folder, is never
imported, and a tree missing its copy of a library fails at once with Python's
`No module named '<name>'`. So a copy of this project made to run elsewhere must carry
those folders beside the tools, as this repository does. The `stdio_utf8` library's own
doc says why, under "How this project's tools import it".

Prefer a Python patch release that includes the fix for CVE-2025-6069 (gh-135462): without
it, `html.parser` takes quadratic time on some malformed pages, and the Bandcamp and
Tekstowo steps parse pages a server sends with it.

## Usage

```text
lyricsmith.py [--version] COMMAND [the command's own options]
```

| Option | Default | Description |
| --- | --- | --- |
| `--version` | off | Print the dispatcher's own name, its entry file's, and the project version from `pyproject.toml` (`unknown` when it cannot be read), then exit `0`. |
| `--help` | off | Print the commands, then exit `0`. After a command, `--help` is that command's own. |

Only the first argument is the dispatcher's. An option before the command, even one the
command itself takes, is refused rather than handed on.

## Exit codes

| Code | When |
| --- | --- |
| `0` | `--help` or `--version`. |
| `2` | No command, an unknown one (the message lists the commands), or an option before the command. |
| any other | The tool's own exit code, passed back unchanged: see each tool's `Exit codes` section. |

## Lyrics sources and their terms

lyricsmith is meant for home use: for the lyrics of a music library you own, on your own
computer. It writes the lyrics it finds only into your own files, the MP3s and the
sidecars beside them, and republishes none of them. Every source below is asked by
default. Five of them limit how they may be used, and keeping each one's terms is up to
you. Each has a `--no-*` option on `album` and `sweep` that leaves it out.

| Source | Terms | Option that leaves it out |
| --- | --- | --- |
| YouTube Music | Terms-limited: [YouTube's terms](https://www.youtube.com/t/terms) bar automated access | `--no-youtube-music` |
| Tekstowo.pl | Terms-limited: [its terms](https://www.tekstowo.pl/info/regulamin) allow home use only | `--no-tekstowo` |
| NetEase | Terms-limited: it publishes no terms for the undocumented routes the tool asks; [its user agreement](https://st.music.163.com/official-terms/service) is the nearest | `--no-netease` |
| Genius | Terms-limited: its API gives no lyrics, so the song page is read, which [Genius's terms](https://genius.com/static/terms) bar as scraping. A key of your own is needed | `--no-genius` |
| Bandcamp | Terms-limited: [its terms](https://bandcamp.com/terms_of_use) allow personal, non-commercial use | `--no-bandcamp` |
| LRCLIB | Open: [LRCLIB](https://lrclib.net/) states no terms and no data license | none |
| MusicBrainz | Open: [it asks](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting) for one request a second and a User-Agent naming a contact; the tool paces itself to that and adds the `[musicbrainz]` contact from the config file when set | `--no-bandcamp`, the only step that asks it |

Which sources were ruled out, and why, are in `album/lyrics_sources.md`.

## Samples

`samples/README.md` points to each tool's own hand run.

## Tests

Each suite runs bare from the clone's root, after `pip install ".[test]"`:

```bash
python test_lyricsmith.py
python album/test_album.py
python sweep/test_sweep.py
python transcribe/test_transcribe.py
python sync/test_sync.py
```

and each shared library's suite the same way, as `python <library>/test_<library>.py`.
Album's live provider tests ask the real services; set `LYRICSMITH_OFFLINE=1` to skip
them. The live whisper.cpp, ffmpeg and sync tests skip themselves when the program, the
models or the sync install they need is absent.

`test_lyricsmith.py` runs the dispatcher with a fake tool, so no tool runs, and checks
that each command's `--help` is its tool's own: it runs each entry file with `--help`
alone and through the dispatcher, and compares the two with the program name replaced
and whitespace collapsed, since argparse wraps the usage line by the name's length. Each
tool's own suites check that the name it is given reaches its help, its notices and the
commands it prints. The library-path cases copy the project, its shared libraries left
out, into a scratch tree with a decoy of every library planted above it, then run each entry
file's `--help`: once loose, and once inside a repository whose own `utilities/` folder
holds real copies. Each run must fail naming a missing library and never load a decoy or
the copy elsewhere, then succeed once the libraries are back at the project root.

## License

lyricsmith is free software, licensed under the GNU Affero General Public License,
version 3 or (at your option) any later version (`AGPL-3.0-or-later`). The full text is
in `LICENSE`.
