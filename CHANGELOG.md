# Changelog

Notable changes to lyricsmith, newest first.

## 0.2.0 - 2026-10-27

### Added

- `sweep`: an owed album whose last visit was challenged by a provider it still owes (a
  lookup sent and refused as `blocked`, such as Genius's `403` from a challenge page) is
  visited after every other album to visit, in the run and in the survey's list alike,
  and the run names each such album on stderr before the first visit
  (`<album>: visited last, its last visit challenged by Genius`). The challenge is still
  sent once a sweep and still stops the provider, but only after every other album has
  asked it. A lookup an open breaker kept unsent, counted as `breaker open`, is no
  challenge. Each ledger entry records the providers that challenged it in
  `challenged_providers`; a line without it reads as none. `--restart` visits in the
  walk's order.
- `sweep`: a run passes over an owed album whose whole debt waits: every provider it
  owes, less those its `--no-*` options turn off, still cooling down or behind an open
  breaker, read afresh before each owed album through the album tool's own readers. Such
  an album is not run, writes no ledger line, takes no `--limit` place and no pause, and
  leaves the stop rule's count as it was; stderr says `<album>: passed over, waits on:
  Tekstowo (cool-down)`, the report lists it under `owed, waiting on a cool-down or
  breaker: <count>`, and the sweep exits at least 1, since it is still owed. An album
  only partly held is visited as before. `--mode survey` lists such albums apart from
  those a run would visit. A state file the reader ignores is no hold.
- `sweep`: `--mode silence` also writes the whole list as an HTML page beside it,
  `silent-<stamp>.html`, after every run: finished, stopped by `--limit`, interrupted by
  Ctrl-C, or a `--resume` of a list with nothing left, which measures nothing and only
  writes the page again. The page counts each verdict, then lists each verdict's tracks
  (album, file, track number, title, length, peak and reason), likely ripping mistakes first
  and likely meant last, every leg of a resumed list included. It is one self-contained
  file laid out by the shared HTML page library (`html_page`), naming the list
  by its file name only, and it is replaced atomically. It replaces only a page the pass
  itself wrote, marked in its head: a file of that name the pass did not write, a page
  from before that mark included, or one that is the list itself, is refused and kept,
  and the page is not written. A FIFO or a symbolic link there is refused at once,
  neither waited on nor read through. The summary names it in a
  `page:` line under `list:`; a page that cannot be written is named on stderr, the list
  is kept, the resume command that writes it again is printed, and the exit is 1. Ctrl-C
  during the page's write still prints the summary and the resume command, and exits 130.
- `sweep`: `--mode silence` measures every track of a library with `ffmpeg`, whatever
  its lyrics, and lists the silent ones in a tab-separated file under
  `$XDG_STATE_HOME/lyricsmith/silence/` (else `~/.local/state/lyricsmith/silence/`), one
  row each with a verdict and its reason: `likely meant` for a short placeholder-titled
  gap or spacer-run track, `likely a ripping mistake` for a real title over 10 seconds,
  `unsure` otherwise, and `not measured` for a track `ffmpeg` could not read. It writes
  nothing in the library. Each album's rows end with a done marker, written in one write,
  so `--resume <list>` goes on in the same list, cutting off an album left unfinished;
  `--limit` previews the first albums, and the end prints the counts by verdict and the
  command that resumes. It takes only `--library`, `--limit` and `--resume`, and every
  other mode refuses `--resume`. Without `ffmpeg` it exits 2; a track not measured, or
  an album that cannot be read, makes its exit 1. A `--resume` list none of whose albums
  is in this library was begun on another one, and exits 2 untouched. A track or folder
  reached through a link is named on stderr and not measured; a disc folder that cannot
  be read leaves its album unfinished and counted as left. Every name it prints, on the
  terminal and on the page alike, is escaped the same way.
- `sweep`: `--owe-earlier-runs` catches up the albums swept before the `owed` outcome
  existed. An album whose newest ledger line reads `done` but holds no `owed_providers`
  owes each refusing provider the line names, and what its run's manifest owes by the
  owed rule's cool-down half; it is revisited asking only those, as an owed album is,
  and one owing nothing stays done. `--mode survey` takes it too and lists each album it
  adds with what it owes (`<album>  owes: LRCLIB, Tekstowo`), so the pass starts with a
  dry run. A resume command carries it; it is refused with `--restart`, `--offline` and
  `--mode accept`.
- `album`: `--mode accept` keeps an album as it is and drops every run's backup. By
  default it only reports: the tracks whose only copy would go (lyrics a run removed,
  stripped or replaced, a sidecar undo set aside, a track whose check flags), then each
  run, then the counts. `--apply` copies each run's manifest to
  `$XDG_STATE_HOME/lyricsmith/accepted/` (else `~/.local/state/lyricsmith/accepted/`),
  reads each copy back, and only then removes the album's `.lyricsmith` folder, undo
  with it. An unfinished run, an unreadable manifest, a run folder it cannot list or an
  entry lyricsmith did not write refuses the album with nothing touched. Afterward undo
  and check say the album has no run, and compare treats its tracks' lyrics as
  unrecorded. Its `at risk` count
  counts tracks; a records failure exits 2 naming any partial records folder. It needs
  a POSIX system: on Windows it is refused before anything is read. `sweep --mode accept`
  does the same for every album of a library, each refused only for itself, under the
  ledger's lock and writing no ledger line, and ends with one summary counting the
  refusals by reason in plain words (`album locked`, `unfinished run`). Each album is
  checked again just before it is accepted, and one swapped for a link since the walk
  began is skipped, never followed. A refused or failing album makes its exit 1.
- `album`: each refusal in the manifest keeps its class and facts beside its detail:
  `reason` (`rate limited`, `overloaded`, `unreachable`, `blocked` or `unrecognized`),
  the refusing `status`, the `retries` made, the `retry_after_s` the reply asked for before
  its cap, and the reply's `server` header. No reply body is kept. A refusing client
  error other than 401, 403, 407, 429 and 451 (a 400, 404, 410 or 422, say) is that
  track's own error, `unrecognized`: it neither counts toward a provider's breaker nor
  opens it, and the track does not owe the provider. YouTube Music stops for the run on
  any reply but 200, so its stop on such a client error is `blocked` and the album stays
  owed to it. Any server error (5xx) is `overloaded` and counts toward the breaker, and a
  connection dropped while a reply's body is read is `unreachable`; it is not retried. A
  manifest written before them reads its `reason` from each refusal's detail, by these
  same classes; the schema stays 1.
- `album`: LRCLIB, NetEase, Genius and Bandcamp (with MusicBrainz) each have a breaker.
  Three overloaded, unreachable or rate-limited refusals in a row stop asking that
  provider for 5 minutes, then one track tries again: an answer closes it, a refusal
  doubles the wait, up to 60 minutes. One blocked refusal stops it for 60 minutes at once.
  A breaker left waiting for that one track more than 60 minutes past its wait reads as
  closed.
  A track it skips records a refusal, `breaker open until HH:MM:SS UTC`, marked `unsent`
  in the manifest (a refusal without the mark reads as sent). Each breaker's state is
  kept in `<provider>_breaker.json` beside the cool-down files, so it carries across a
  sweep's albums and a resume; `--offline` and `--dry-run` never touch it, and a run that
  does not ask the provider (its `--no-*` option, `--only-providers`, or Genius with no
  key) neither reads nor writes its file.
- `album`: the retries made after a refusal, Genius's challenge retries included, are
  counted by provider and reason, kept in the manifest's optional `retried` map and
  shown on the `providers:` line, such as `LRCLIB 40/50 (3 refused, 7 retried)`. A new
  `refusals:` line counts the tracks each provider refused, by reason: `refusals: LRCLIB
  overloaded 12, unreachable 1`. A lookup an open breaker skipped counts under `breaker
  open`, after every reason, not as one more refusal of the class that opened it: one
  Genius challenge and twenty skipped lookups read `Genius blocked 1, breaker open 20`.
  The `providers:` line counts a track whose only refusals from a provider were skipped
  this way as `breaker open` too, `Genius 0/21 (1 refused, 20 breaker open)`.
- `album`: the run summary splits its written tracks by what each held and got, `written:
  upgraded 3, new synced 1, embedded 2`, and its removed tracks by why their old lyrics
  went, `removed: placeholder 1, past end 1`, each line only when it counts a track. The
  `providers:` line splits each provider's kept answers by kind: `LRCLIB 30/50 (24 synced,
  6 untimed; 3 refused, 7 retried)`. What each track held before the run is recorded in
  the manifest's optional `held` (`nothing`, `untimed` or `synced`), read from the files
  before anything changes, and the categories are decided by it.
- `album --only-providers <name>[,<name>...]` asks only the named providers (`lrclib`,
  `netease`, `youtube-music`, `bandcamp`, `genius`, `tekstowo`), in a run and a compare.
  A provider left out is not asked, records nothing, has its breaker and cool-down files
  left alone, and shows as not asked in the summary; one also turned off by its `--no-*`
  option stays off. An unknown, empty or repeated name is a usage error, and the option
  is refused with `--offline` and by the modes that ask no provider. The sweep's revisit
  of an album still owed answers is what it is for.
- `sweep`: an album a provider refused, for any reason but `unrecognized`, on a track that
  still holds no synced lyrics now ends `owed`, not `done`, its owed providers listed in
  the ledger. A resumed sweep visits it again with `--only-providers` naming only those,
  until it owes nothing more; an owed album never adds to `--stop-after`'s run of refused
  albums, makes the sweep exit `1`, and is forgotten by `--restart`. An album is owed,
  too, a provider its run left out by a cool-down while that provider could still answer
  a track: YouTube Music for a track not holding synced lyrics, Tekstowo only for one
  holding no lyrics at all, which ended `not found` or `removed` or kept wordless frames
  or a held kind not read. That holds in every run, so a revisit made while the
  cool-down still holds stays `owed` rather than losing the debt. A provider with a
  cool-down that a refusal stopped part-way (YouTube Music, Tekstowo) is owed by the
  stopping track and every later track it left unasked that it could still answer,
  whatever the stop's reason, YouTube Music's `reply not JSON`, recorded
  `unrecognized`, included. An untimed-only provider (Bandcamp, Genius, Tekstowo) that
  refused a track already holding untimed lyrics is not owed for it: an ordinary run
  looks such a track up with the synced steps only, so that provider could never improve
  it. A track a refetch asked again (`refetch_untimed` or `refetch_implausible` in its
  manifest) counts as holding nothing for such a provider, so a refetch run's debt to it
  is kept, by a refusal, a cool-down or a stop alike; and a track that ended in an error,
  at any stage, which left it as it was, is judged by what it held. A provider the sweep
  turns off is never owed, and a revisit that drops a debt for that reason says so on
  stderr. A debt to Genius on a track the revisit serves from its own untimed lyrics is
  not asked again, so that revisit ends `done`. An album swept by 0.1.0 keeps its `done`
  line; `--owe-earlier-runs` catches such albums up, and reads their debt by the same
  rule. Whether a revisit was refused is judged by the synced providers it actually
  asked, so a revisit asking LRCLIB alone that LRCLIB refuses counts as refused.
- `sweep`: the ledger's lines gain optional `owed_providers`, `refusals` (tracks by
  provider and reason, with the `breaker open` count beside the reasons), `retried`, and
  in `counts` `removed`, `written_by` and `removed_by`; older lines still read. A
  removed track is now counted in `removed`, not in `not_found`, though an older line's
  `not_found` still includes its removals. The final table gains a `removed` column, and
  below it `written:`, `removed:`, `refusals:` and `retried:` totals in `album`'s words
  and the providers each owed album owes. Every 25 albums visited, a progress line on
  stderr gives the albums visited, done and owed, and the refusals by provider and
  reason so far. The progress and total `refusals:` lines count a lookup an open breaker
  skipped under `breaker open`, as `album`'s does, and the sweep still owes the provider
  for it.

### Changed

- Every tool takes its shared libraries from the folders the project's `pyproject.toml`
  names under `[tool.lyricsmith] shared_libraries`, each relative to that file, read by
  one manifest reader in `lyricsmith_project/`. Nothing searches the folders above the
  project any more, and a named folder that is missing fails at once, naming the folder
  and the key.
- `sync` and its per-track child process no longer search upward without a bound for
  their shared libraries: they read the same list as every other tool.
- The project's version, for the dispatcher's `--version`, the album tool's User-Agent
  and the labels `transcribe` and `sync` write, is read by that one reader.
- The `--help` formatter that hides a `None` default and the escape for the characters a
  terminal acts on each have one copy, in the shared libraries
  (`help_formatter.RawNoneHidingFormatter`, `stdio_utf8.spell_unsafe`); each tool's own
  copy is gone. `--help` and every escaped name print as before.
- `album`: a request to LRCLIB's search, NetEase or MusicBrainz is tried again after any
  server error (5xx), not only 502, 503 and 504.
- `album --mode compare` names each run whose manifest it cannot read, and so passes
  over, on stderr before any provider is asked, newest first:
  `passed over the run .lyricsmith/<id>: its manifest cannot be read (<error type>)`.
- `album`: on a file system that refuses opening a file for update, a network share
  through FUSE, say, no track can be embedded, stripped or undone: each is that track's
  error before anything of it changes, where an embed used to fall back to rewriting
  the file whole.
- `sweep --mode survey`, `accept` and `silence` refuse `--pause-seconds` and
  `--stop-after` off their defaults, as they refuse the run's other options.
- `sync`: its printed commands name the tools as it was run, `album.py` and `sync.py`
  run alone or `lyricsmith album` and `lyricsmith sync` through the dispatcher, where
  they always said `lyricsmith`. `--extensions` refuses `lrc` and `txt`, as
  `transcribe`'s does. A written `.lrc` beside a track that is not an MP3 is named in
  the footer as staying a sidecar, and `--embed` embeds MP3s only. Accented letters
  fold to their base letter before the words are aligned.
- The dispatcher's `--help` names its prerequisites.

### Fixed

- `album`: a run whose `manifest.json` is a dangling symbolic link is a run to undo and
  check, as it is to prune, so a plain `--mode undo` or `--mode check` refuses it (exit
  2, naming the run, why its manifest could not be read, and `--run-id` as the way to
  another run) instead of passing over it to an older run, and an undo of an older run
  warns that a newer run's changes to the same tracks were overwritten. A run folder
  that cannot be searched is now refused the same way rather than passed over, and so is
  one whose `manifest.json` is a FIFO pipe or other non-regular file, refused at once and
  never waited on. A folder named with another script's digits in place of a run id's
  ASCII ones is no longer read as a run.
- Nothing waits on a FIFO pipe or device swapped in for a file a tool reads. In
  `album`, a frame export, exact copy or sidecar copy in a run folder, a sidecar beside
  a track, or the track's MP3, at its read or its save, is that track's error at once; a
  cool-down or breaker file that is not a regular file is ignored with a warning
  (`NotRegularFileError`). `transcribe` reads such a `.lrc` as present and not its own,
  and `sync` reads such a track's length as unknown.
- `album`: an embed no longer re-creates a track deleted while it was written, as a file
  holding only the ID3v1 tail. The read-back after a write refuses a symbolic link
  swapped in for what was just written, rather than reading through it.
- `album`: a Genius challenge retry whose turn runs out of time ends on the challenge,
  `blocked`, rather than as a timeout.
- `sweep`: a Ctrl-C while the next album's holds or link are read, or while its visit is
  set up, still prints the report so far and the resume command, and exits 130. That
  album gets no entry, and a `--changed-list` already handed to it lists every MP3 of it.
- `sync`: a Ctrl-C while timing stops there, prints the footer with the album commands
  for every track that got its `.lrc`, the one in hand included once its new `.lrc` is
  in place, skips `--embed` and exits 130. A trailing line with nothing to align is
  stamped at the last sung line's end, not its start. A re-time whose words hold
  nothing to align is skipped. `--dry-run` prints a track's detail on its own line. The
  `[re:]` label's versions are escaped.
- The escape for characters a terminal acts on also covers U+061C, the Arabic letter
  mark.

### Security

- Each tool loads every shared library and sibling tool only from the folder the
  project's manifest names, checked before any is imported. A same-named module earlier
  on `sys.path`, from `PYTHONPATH` or `site-packages` say, or a planted `album.py`, is
  refused rather than run, and the manifest reader is moved first on the path. Such a
  refusal, from an entry file or through the dispatcher, exits 2 with one line naming
  the error, where it was a traceback and exit 1. A library module file that is itself
  a link still loads from its named folder.
- State files are readable by their user only: the sweep's ledger, lock, silence list and
  page, the album's cool-down and breaker files, and accept's records are written 0o600,
  and a state folder the tools create is made 0o700 at its creation. A folder already
  there keeps its mode. The `--changed-list` file and files beside the library keep the
  default mode.
- A state file or an operator-named list is read bounded and without blocking. A
  `--resume` list or the ledger that is a link, a FIFO pipe, a device, or larger than
  its cap (64 MiB for a list, 256 MiB for the ledger) is refused at once with exit 2, a
  link named as one. A ledger that is a FIFO pipe or a folder is now an error where it
  read as empty. An existing `--changed-list` that is no regular file, or over 64 MiB,
  is refused with exit 2; a link there is still followed. Cool-down and breaker files
  are read no further than one byte past their 1 KiB cap. A resumed silence list is cut
  and appended to only through a descriptor checked to be a regular file, never through
  a link, and is written with LF line endings on Windows too.
- `album`: a breaker file whose `window_s` is out of range is a notice and no breaker,
  where it ended every online run, and every sweep album, with a traceback at startup.
- `album --mode accept --apply` reads each manifest again for its copy through the run
  folder's checks, so a link out of the run folder, a link swapped in since the
  judgment, or a manifest over 16 MiB is a records failure (exit 2) and nothing is
  deleted. Accept lists an `undo/` folder however deeply it nests, where a deep one
  ended it with a traceback.
- `album`: the backup's proof of a frame export or exact copy refuses a link found under
  the name, so that track's backup fails rather than being taken as proven.
- `sync` judges each walked name as `transcribe` does: a link is `skip: linked`, and a
  FIFO pipe, a device or an unreadable name is `ERROR audio`, where a FIFO could hang the
  run. `--embed` decides its library depth from walked tracks only, ignoring links and
  FIFO pipes.
- `sync` makes `--path` and `--model-dir` absolute and hands ffmpeg a `file:` path with
  only the file protocol and the track's own audio demuxer allowed, so a track whose bytes
  are a playlist reads no other file and is `ERROR align`, its detail carrying ffmpeg's
  own last error lines. `--extensions` accepts only an extension with a known demuxer and
  refuses any other with exit 2; `aifc`, `m4b` and `mp2` are added.
- `sync` caps a track at an hour: a track tagged longer is `skip: track too long` and no
  child runs, ffmpeg decodes no more than an hour, and the child's time bound is capped
  with it.
- `sync`'s first-time TIME refuses a link at the `.lrc` name, as RETIME already did.
- `sync`'s offline guard also refuses `connect_ex`, `sendto` and `sendmsg`.

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
