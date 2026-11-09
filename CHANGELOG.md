# Changelog

Notable changes to lyricsmith, newest first.

## 0.4.0 - 2026-11-09

### Added

- `album --mode fix-text --hold-for-genius <file>` and `sweep --mode fix-text
  --hold-for-genius <file>` never ask Genius, which now challenges every script, and keep
  instead of removing a wrong text that every provider asked answered without singing:
  the track ends `kept existing`, its removal withheld as `held for Genius`, and its path
  is listed in `<file>` in the `--changed-list` format for a later pass. A provider that
  failed still keeps the track as an error, and a later provider's sung answer still
  replaces the text. The sweep keeps the union of its albums' lists, a resumed sweep
  adding to it, and an album whose open tracks were all held is done, so it never feeds
  the stop rule. Refused beside `--no-genius`, with any other mode, and when it names
  the same file as `--changed-list`, since each list is written whole. The
  `removal withheld:` line now names each reason it counts.
- `sync --mode hear` separates, transcribes, and judges each selected track without
  timing it: it aligns nothing and writes no `.lrc`, frame, or sidecar. `--kinds` picks
  provider-timed texts by default, and machine-timed or untimed ones when asked;
  `--tracks-from` takes a list in the `--changed-list` format. Every verdict goes to
  `--hearing-log <dir>`, one private JSON record a track and model with its transcript,
  and a rerun skips a track already heard with the same text and model. A text not sung
  also gets its `--heard-dir` record and by-hand row, as a timing run writes them. A
  track `sweep --mode draw` listed is found when `--path` reaches the library through a
  link.
- `transcribe --mode fetch-models --whisper-model ggml-large-v3.bin` fetches the full
  `large-v3` model, pinned by hash beside the turbo, for a comparison hearing with
  `sync --mode hear --whisper-model`. The default fetch is unchanged.
- `sweep --mode draw` draws a seeded, stratified sample of a library's provider-timed
  tracks, read from its sidecars, run manifests, and ledger: every track on an album
  where a wrong text was found, and up to 250 from each recorded provenance, writing a
  track list and `strata.tsv`. `--withheld` lists instead the tracks whose removal was
  withheld.
- `sweep --mode hear-report` reports a hearing log over a drawn sample: each group's
  `not sung` rate, a library-wide estimate weighted by each group's size, a shift test of
  each `not sung` transcript against its album's other texts with the misses tallied by
  genre, and, with `--compare-model`, how many tracks rise past the sung share under a
  second model. It prints approximate figures and writes nothing. A strata row may sit in
  a disc folder of its album, as a multi-disc album's `Disc 1/` holds its tracks. A link
  or junction at the track or at a folder between it and its album is refused, and so is
  a row whose path or album is not lexically normal, which a `..` past a link could
  otherwise read through.
- `bounded_run`'s `run_bounded` takes `inherit_stderr=True`, which gives the child the
  caller's own stderr instead of a pipe, so a long run's progress lines appear as they
  are written rather than when it ends. The result's `stderr` is then `""`.

### Changed

- `sweep`'s survey also names a synced track whose timed `.lrc` no machine labeled, as
  `unembedded: <album>/<track> (unlabeled)`, and a run visits its album to embed the
  file, but only while the track's frames hold no timed lyrics. Frames holding timed
  lyrics are left to serve, even where `album` would replace them with the file.
- `album --mode fix-text` also fixes provider-timed texts a hearing found not sung. Their
  candidates are the album's other texts, timed ones by their words alone, then the whole
  chain, its synced steps first. A sibling's timing is never copied: a sibling or an
  untimed answer is written untimed for `sync` to time, while a synced answer keeps its
  stamps. A machine-timed `.lrc` is skipped. `sweep --mode fix-text` visits an all-synced
  album when a track of it holds both a record and a provider's timed text.
- `sync`: a `--heard-dir` record is spent when its track is gone, when `sync` itself timed
  it, or when a provider's timed text differs from the one heard; before, any timed track
  spent it, which would have erased a provider-timed text's record before its fix.

### Fixed

- `sweep` runs on Windows: `--mode run` and `--mode fix-text` no longer refuse a host that
  is not POSIX. Past its bound an `album` child is terminated there, since Windows cannot
  send it Ctrl-C, and the stop is recorded as on POSIX, exit 130 and timed out.
  The ledger's lock is `run_lock`'s, `msvcrt.locking` on Windows, still `0o600` in a
  `0o700` folder. `--mode accept` runs on Windows too, through `album`'s own delete, and
  resuming a run refuses a link at the silent list's name there as on POSIX.
- `album`'s `--mode prune` and `--mode accept` run on Windows. Where `shutil.rmtree` does
  not resist links (every Windows Python), the delete is `album`'s own walk, which never
  enters a symbolic link, junction, or other reparse point and stops when a folder is
  swapped while it walks. A junction is refused wherever a link is: a run folder, the
  `.lyricsmith` folder itself, and an album walk, whose notice now names it. On Windows,
  where an open lock blocks the folder's rename, accept moves the run folder's entries
  one at a time under the lock and removes the emptied folder last, saying when it keeps
  it.
- `album`: a read or write that must not follow a link refuses one on Windows too, where
  `O_NOFOLLOW` does not exist: the name is looked at first, then the opened file is
  checked to be the one looked at. A link target Windows reads back as `\\?\C:\...` is recorded
  as it was made.
- `sync` and `transcribe`: a track's path below the folder walked is printed with `/` on
  every host, as `album`'s manifests name tracks; on Windows a report row read `One\01`.
  A path outside it still prints in the host's form.
- `album`: a Bandcamp page that redirects to an `https` address off Bandcamp, as the
  pages of an artist serving them from its own domain do, is now nothing found (`page
  elsewhere`), not a refusal recorded as `unrecognized`. The step never follows the
  redirect, and the domain was seen to redirect straight back, so the page holds nothing
  it could use. `fix-text` no longer keeps such a track an error on every pass. It is
  passed over among same-titled `/music` entries as a gone page is. A redirect naming no
  target, a third one, and one to a plain `http` address, another port, or a login stay
  Bandcamp's error, as before.
- Two of `bounded_run`'s tests failed on Windows and now run only where their subject
  does. The check that an unkillable `ps` is abandoned runs on POSIX alone, as its only
  caller does, and the late-tracking live test waits for a grandchild process on POSIX
  alone, since on Windows the child is still suspended at that point.

## 0.3.0 - 2026-11-02

### Added

- `album --mode fix-text --heard-dir <dir>` fixes the untimed texts `sync` found are not
  sung, such as another song's words stored on a track. For each track with a record in
  the folder `sync --heard-dir` kept, it tries the album's other untimed texts first, with
  no request, then each provider's untimed step in the chain's order, and writes the first
  text the kept transcript sings, backed up as a run's writes are and undone the same way.
  A text taken from another track names that track in the row and the manifest. When
  nothing is sung, the wrong text is removed, backed up, as `wrong text removed`, but only
  when every provider the run meant to ask answered; a failure, a timeout, an open
  breaker, or a cool-down keeps the text and makes the track an error. On an album that
  looks spoken, where a transcript of speech matches no text, a removal is withheld
  instead: any track tagged `Comedy`, `Spoken Word`, `Stand-up Comedy`, or `Stand-Up` (in
  any case, an ID3v1 numeric genre read by name), or more than half its tracks holding a
  kept `not sung` transcript, keeps its text and reads `kept existing: removal withheld,
  spoken word (<why>)`, counted on a `removal withheld:` line, never an error. A text the
  transcript sings is still written there. `--dry-run` prints each track's plan and asks
  nothing. The fix only reads the folder and keeps every record.
- `sweep --mode fix-text --heard-dir <dir>` runs that fix over a library: it visits only
  the albums holding a record in the folder, with the run's ledger, pace, resume, Ctrl-C
  handling, and changed list, and keeps its own done albums. A fix is never owed; one that
  kept a wrong text ends as track errors and is retried whole.
- `sync --heard-dir <dir>`, beside `--by-hand-list`, keeps what `whisper-cli` heard of each
  track it finds not sung: one private JSON file a track (`0o600` in a `0o700` folder),
  written in the same Ctrl-C hold as its `not sung` row. A listed track whose text has
  changed is judged against that transcript without hearing it again; still not sung, its
  row and record are written again, otherwise it is timed without running `whisper-cli`.
  A timed track's record is removed, and a run's start removes the records of tracks under
  `--path` that are gone or timed. A transcript over 64 KB is never stored.
- `album`: a refusal from Genius records which of its two hosts refused, `api.genius.com`
  (the keyed API) or `genius.com` (the unkeyed site), as the manifest entry's `host`, and
  the summary's `refusals:` line names it. An entry written before the field reads as no
  host.
- `help_formatter`: `UnsetHidingFormatter`, the counterpart of `RawNoneHidingFormatter`
  for a help whose epilog argparse may rewrap, hides a `None` or empty-list default.
- `sync`: each track's text is checked against its vocals before it is timed, so another
  song's lyrics are no longer timed with confidence. Once the vocals are separated, and
  before wav2vec2 loads, the vocal stem is transcribed by `whisper-cli` with the
  transcribe tool's fixed call, in English, and the share of the transcript's distinct
  word pairs found among the text's is the verdict. A share under 0.25, with 20 or more
  pairs heard, is `not sung: left untimed`: no `.lrc`, a planned outcome that exits `0`.
  Under 20 pairs the check cannot judge, and the track is timed as before with a note.
  Measured on about 190 test-library tracks: wrong texts scored at most 0.16, right ones at
  least 0.34. `--by-hand-list` gains two reasons: `not sung` (the share; a rerun plans
  it as `skip: listed as not sung`) and `little heard` (the pair count; information
  only). A run now needs `whisper-cli` on `PATH` and the transcribe tool's two models,
  which the setup check names with `lyricsmith transcribe --mode fetch-models`;
  `--whisper-model-dir` reads them from elsewhere. It adds a median 7.8 s a track,
  against separation's 59 s; a `whisper-cli` that fails is the track's `ERROR align`,
  never a pass.
- `sync`: `--by-hand-list <file>` keeps a list of each track left for a person, so a
  library timed in resumable legs never times a low-confidence track twice. As each track
  ends, a low-confidence track adds a `low confidence` row (its median line confidence)
  and a track written with a flagged first line a `first line` row (the stamp to check),
  each a line of three tab-separated fields: the absolute path, the reason, and the
  detail. The file is replaced atomically with Ctrl-C held, `0o600` in `0o700` folders,
  and never written through a link, so an interrupted leg keeps every row it reached. The
  next run reads it before timing anything, and a listed low-confidence track plans as
  `skip: listed for timing by hand`, `--overwrite` or not; removing the row times it
  again. A list that cannot be read or breaks the format exits `2`, naming the line; a
  row that cannot be written is said beneath the track's row and exits `1`.

### Changed

- `--help` of every tool no longer prints `(default: False)` beside an on/off option, as
  it already printed no default for an option left unset. Such an option left off now
  parses as None rather than False; every value a run keeps is still False.
- The README's install steps now make a virtual environment first, since Homebrew and
  Debian Python refuse a system-wide `pip install`. They give the download sizes of
  `transcribe`'s models (about 1.6 GB) and `sync`'s (about 460 MB), say that `sync`'s
  install pulls in PyTorch and that `sync` needs `transcribe`'s `whisper-cli` and models,
  and say how to get a Genius key.
- `sync/sync.md` and `sync/requirements-sync.txt` no longer say the sync stack is kept out
  of the `pyproject.toml` extras, which hold a `sync` extra naming sync's direct imports;
  they say instead why the whole stack is pinned apart.
- The docs, comments, and help text read more plainly: dashes used as punctuation,
  scattered bold and capitals for emphasis, and pointers to files that do not ship are
  rewritten.
- `pyproject.toml` keeps only the settings a shipped tool or test reads.

### Fixed

- `bounded_run`: the `ps` snapshot a tree kill reads on macOS and Linux could stall for
  minutes under heavy load, and the kill waited for it to be reaped. An overdue `ps` is now
  killed, given one second to be reaped, and then abandoned, so the kill goes on.
- `album`: a Bandcamp page that MusicBrainz links but that answers `404` or `410` is
  now nothing found (`page gone`), not a refusal recorded as `unrecognized`: such links
  outlive the pages they name, so an album whose page is gone is no longer counted as
  refused by Bandcamp or by a run's stop. A gone page among same-titled `/music` entries
  is passed over for the next. A `403`, a `5xx`, and every other error status stay
  Bandcamp's error, as before.
- `sweep`: a track holding a machine-labeled timed `.lrc` (`sync`'s `[by:machine timed]`,
  or a transcript) whose frames have not taken it, holding no timed lyrics or its lines at
  other times, now makes its album one to visit, so `sweep --mode run --offline` embeds
  what `sync` timed. The survey read the sidecar alone and counted such a track as
  synced, so an album whose owed tracks had all just been timed was never visited. The
  survey names each one, `unembedded: <album>/<track> (machine timed)`; the fix pass does
  not visit for it, since a fix embeds nothing, and an album the ledger holds as done
  is passed over for it without `--restart`, as for any other track it owes.
- `album`: LRCLIB's search by title and artist answers a server error,
  `ServerOverloaded`, for a song LRCLIB does not hold, so nearly every such track recorded
  LRCLIB as overloaded. A field search still failing with a server error once retried is
  now followed by one search by `q` (the artist, then the title), unretried, only while
  the call's deadline has room and with its timeout cut to fit what is left, whose hits
  go through the same matcher; its empty list is a real "no match". If it fails too, the
  refusal is recorded exactly as before, as overloaded, so the breaker and the sweep's
  refusal tally still count it. A server error is never read as "not found".
- `album`: under `--refetch-untimed` or `--refetch-implausible`, a lyrics frame over the
  65,536-character cap is judged implausible junk, as a `.txt` too large to read already
  was, instead of ending the track as `ERROR sidecar: a USLT frame is too large`. The
  track is asked again as though it held nothing; a plausible answer replaces every old
  frame, and the check's `unlike backup` row is a `SKIP` naming the backed-up frame. With
  no answer every lyrics frame is removed, its sidecars judged apart, so an oversized
  `.txt` beside it goes too; undo restores both. Without either option the track is its
  error, as before. It is marked `refetch_implausible` under either option. `sweep`'s
  survey under either option names such a track implausible, with that reason, rather
  than unreadable.
- `album`: the backup refuses a lyrics frame whose export or exact copy would pass the
  manifest's 16 MiB cap, as that track's `backup` error, so the track is never written.
  Undo reads each copy under that cap, so the backup once held a frame undo could not
  restore, and a strip or a refetch could remove it for good.
- `album`: enhanced LRC's inline word stamps (`<00:01.50>`) are no longer embedded as
  text. A timed line read from a `.lrc` or a provider's answer kept everything after its
  line stamps, so the stamps reached every `SYLT` entry and the `USLT`. Each is now
  removed, keeping the line's timing and collapsing its spacing; a line stamp behind one
  still times the line, and a partly timed answer's untimed lines lose theirs too. A
  `.lrc` holding them is left as it is on disk, a line with none reads exactly as before,
  and untimed text stays verbatim. The check reads the same pattern, so a malformed stamp
  (seconds past 59) now counts as words there too, and a `SYLT` entry holding one takes
  the backup's exact copy.
- `sync`: machine-timed tracks keep the blank lines between verses. The `.lrc` held only
  the sung lines, and the album tool embeds the untimed lyrics from the `.lrc`, so every
  stanza break was lost. Each blank line between two lines of the text is now written as an
  empty line stamped with the next line's start, just before it; leading and trailing blank
  lines are still dropped, no sung line's stamp moves, a re-time keeps them, and the row does
  not count them as lines with nothing to align.
- `sync`: lines are no longer spread across a long instrumental break. The child reads each
  model frame's level of the vocal stem against the rest of the band, and a stretch of 12 s
  or longer where the vocals sit more than 12 dB under the band (smoothed over 1 s, 1.5 s
  trimmed from each edge) that starts after the second line is closed to every letter, and
  the text aligned again. On a reference track with a 48 s break, the lines more than 3 s
  off fell from between 6 and 9, across five separation seeds, to 3. Of 60 held-out
  tracks, 9 improved and 5 got worse; the rest were unchanged, and each worse one already
  held a line 20 s or more off. A track with no such stretch is timed exactly as before.

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
