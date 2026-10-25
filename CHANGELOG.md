# Changelog

Notable changes to lyricsmith, newest first.

## 0.1.0 - 2026-10-25

### Added

- Four tools, `album`, `sweep`, `transcribe` and `sync`, each run alone or through
  `python lyricsmith.py COMMAND`.
- `album` finds an album's lyrics, embeds them and checks them, strips them, compares
  them with a second source, and undoes a run. Each run is backed up under
  `<album>/.lyricsmith/<run-id>/` before anything changes, and `--mode prune` removes the
  run folders nothing needs.
- `album`: an LRCLIB, NetEase or MusicBrainz request answered with HTTP 429, 502, 503 or
  504, or whose connection dropped, is tried again up to three times, after 2, 5 and 10
  seconds or the wait a `Retry-After` header asks (at most 10 seconds), always within the call's
  30-second deadline. A refusal a retry got past is not recorded, so a server that is
  briefly overloaded does not send a track on to NetEase or Genius, or to `not found`, and
  a brief overload does not stop `sweep` on an album whose providers only refused. A
  request that still fails is recorded with the retries made, such as `HTTP 503 after 3
  retries`.
- `album`: a Genius challenge's retry wait that would end at or past the call's deadline
  is not begun, so the call ends at once on that challenge, recorded with the retries made
  (such as `HTTP 429 after 1 retry`), instead of sleeping on until it times out.
- `album`: a timed `.lrc` labeled `[by:machine transcript]` is credited to the source
  `machine transcript`, still asks the timed providers, and keeps its file.
- `album`: a timed `.lrc` labeled `[by:machine timed]` is credited to the source
  `machine timed`, the same way.
- `album`: a labeled `.lrc` whose lines match the embedded synced lyrics, only the times
  different, is embedded again, so a stamp corrected by hand or a re-timed file reaches
  the tags; undo puts the earlier times back.
- `album`: a track whose file name takes nearly the 255-byte limit is backed up like any
  other. A backup file whose name would pass the limit keeps the head of the track's name
  and a short hash of the whole, and every temporary file a write makes keeps its name
  within the limit too; undo and check read each file by the name its manifest records.
- `album` judges every provider answer against the track's length, in every run: an
  answer whose timed lines start past the audio's end, or that holds more words a second
  than any song is sung at, is declined as a placeholder is, and the chain moves on, the
  attempt naming the check row that ruled it out. A length that cannot be read declines
  nothing. A timed answer declined with its lines running past one and a half times the
  track's length is another song, so a later untimed answer of the same words, in that
  track's fetch, is declined too.
- `album --refetch-implausible` asks the providers again for each track whose serving
  lyrics are implausible by those two rows; a plausible answer replaces them, and with
  none the implausible side, its sidecars or its frames, is removed, backed up for undo.
  It combines with `--refetch-untimed`, and each track it asked again is marked
  `refetch_implausible` in the manifest. `sweep` passes it to every album it visits,
  within its own pass, and carries it in its restart hint and resume line; with it, a
  sweep also visits an album whose every track is synced when one serves implausible
  lyrics, its survey naming each such track and why.
- `sweep` runs `album` over every album of a library holding a track without synced
  lyrics, one album at a time, recording each in a ledger it resumes from.
- Every lyrics source stays on by default, and each can be left out: `album` gains
  `--no-netease`, which leaves out both NetEase steps, and `sweep` passes on
  `--no-netease`, `--no-bandcamp`, `--no-youtube-music`, `--no-genius` and `--no-tekstowo`,
  refused by a survey and carried by a resumed sweep. Under `--no-netease` a sweep's stop
  rule reads LRCLIB's timed refusal alone as every timed step refusing.
- `sweep --changed-list` adds to a list file that already exists, so a resumed sweep keeps
  the tracks its stopped leg listed until they are refreshed, and the `resume:` line
  carries the option. A `--restart` starts the list empty; an existing list the sweep
  cannot read stops it before the first album, left as it was. `album`'s own
  `--changed-list` still replaces its file.
- `README.md`'s *Lyrics sources and their terms* says the tools are meant for home use
  and write lyrics only into your own files, republishing none, and sets out each source's
  terms with a link: YouTube Music, Tekstowo, NetEase, Genius and Bandcamp limit their use,
  and LRCLIB and MusicBrainz are open. Each `--no-*` option's help, and `album`'s
  `Providers:` help, name the source's terms and point there.
- `transcribe` turns audio into a timed `.lrc` and a plain `.txt` offline with
  whisper.cpp, labeled `[re:lyricsmith transcribe ...]` and `[by:machine transcript]`; a
  file is the tool's own by that by-line.
- `sync` times a track's untimed lyrics against its own audio, offline, into a `.lrc`
  labeled `[re:lyricsmith sync ...]` and `[by:machine timed]`, with demucs to separate
  the vocals and wav2vec2 to align them; the models are fetched once with
  `--mode fetch-models` and checked against pinned digests. `--embed` then runs `album`
  offline on each album that gained a `.lrc`.

### Security

- `album`'s undo keeps every copy it sets aside inside the run folder: a link leading out
  of it is that track's undo error, and nothing is written through it.
- `album` reads every reply, sidecar, backup copy and lyrics frame under a cap, so a huge
  or endlessly inflating file or reply is refused rather than read whole, and an `.lrc`
  time stamp with an absurdly long minutes field reads as no stamp.
- `album`'s Genius session keeps no cookies, sends no netrc login, follows a redirect only
  over `https` to Genius's own hosts and at most twice, and drops the key off the API
  host. NetEase requests carry no `Referer` and no cookie. Tekstowo stops for the run
  less readily: a refused redirect fails only its track, lyrics that merely quote a
  challenge page's words stop nothing, a page it does not recognize fails only its track
  until three search pages, or three song pages, in a row stop it, and an instrumental's
  page, which has no lyrics block, is nothing found.
- `sweep` escapes what it prints from tags and file names, and keeps going past an album
  it cannot read.
- `transcribe` takes regular files only, skips links met while walking a folder, hands
  `whisper-cli` absolute paths and never the Genius key, follows a model download's
  redirects only over `https`, and gives each download a deadline.
- Every tool finds its shared helpers only inside its own repository, or its own project
  folder outside one, so a same-named folder planted higher up is never imported.
