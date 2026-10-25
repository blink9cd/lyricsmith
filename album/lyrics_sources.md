# Lyrics sources: used, possible and ruled out

Every lyrics source evaluated for `lyricsmith album`, with what it gives and what using it would
take. Kept beside the tool so the next piece of work can be picked from it, and so a source
already ruled out is not investigated again. Each was probed with famous songs only, never a
track from the library being tagged. Re-check a row only when its reason changes.

The hard rules every row is judged against: no personal data beyond a track's tags sent to a
service not allowed for it, no account session or cookie carried, no request signing
forged, no CAPTCHA or bot challenge worked around.

## Used, or measured

| Source | Gives | Status |
| --- | --- | --- |
| LRCLIB | Timed and untimed | Used: a documented public API with an exact lookup by track, artist, album and length |
| NetEase | Timed and untimed | Used: two undocumented, cookie-free routes; wins many tracks LRCLIB misses |
| Genius | Untimed | Used, through `lyricsgenius`, with the user's own API key. The API returns no lyrics, so `lyricsgenius` reads the song page's HTML, which Genius's terms bar as scraping (re-read in October 2026 from the library's docs and a published quote of the terms, since the site refused the fetch); `--no-genius` leaves it out |
| Bandcamp, found through MusicBrainz | Untimed, as the artist or label entered them | Used, unsynced only: one MusicBrainz lookup and at most two page requests per album (up to nine when several albums tie on the title: the exact title is opened first, and the first album giving the track's lyrics is kept), no cookie. Bandcamp's terms give its content for personal, non-commercial use only and carry no scraping clause (re-read in October 2026); `--no-bandcamp` leaves it out |
| YouTube Music | Timed and untimed | Used: the anonymous route, no cookie, paced, stops at the first refusal and then cools down for 24 hours |
| Tekstowo.pl | Untimed, user-submitted, often with translations | Used, untimed only, last in the chain after Genius: its terms allow home use. A probe in October 2026 found it serves song pages, lyrics included, to the tool's own User-Agent with no cookie and no challenge, so beets' reason for turning it off no longer holds. The site's plain search by artist and title, the original lyrics only and never the translation beside them, no cookie, paced, stops at the first refusal or challenge and then cools down for 24 hours. Posing as a browser stays ruled out |

## Possible: worth a probe

None: the last one, Lyrics.com, returns only links. The sources under "Maybe" each carry
a doubt and are not being pursued, so no source is waiting on a probe.

## Maybe: not pursued for now

Not ruled out, but each has a doubt that a probe or a ruling would have to settle first, and
none looks worth it before the sources above.

| Source | Gives | The doubt |
| --- | --- | --- |
| AZLyrics | Untimed, broad and mostly mainstream | Reading its HTML pages, since it has no API. Its terms forbid use by other lyrics providers, which this tool is not. A few hundred lookups earn an IP ban behind a CAPTCHA, so it would be opt-in, one album at a time, slowly paced, and stop at the first challenge. Overlaps Genius heavily |
| SimpMusic Lyrics API | Timed and untimed | Public reads at 30 a minute, but no terms, and partly filled by crawling other services. A watch item, not yet a source |
| Letras.mus.br | Untimed | HTTP 403 from Akamai to the probe; whether that is a bot block or a one-off refusal is not known |
| SongLyrics.com, LyricsFreak, DarkLyrics, Lyrics Translate | Untimed | Scraping behind Cloudflare, or reported IP bans; mainstream or metal only |
| lyrics.ovh | Untimed | Answered all three probes; reportedly serves from LRCLIB and NetEase, which would add nothing, unconfirmed |
| happi.dev | Untimed | Keyed, mainstream, status unconfirmed |
| Karalyr | Word-timed | Holds a handful of tracks |
| Kaggle "Genius Song Lyrics", Million Song Dataset, DALI, Lyrics1M | Untimed, offline | Unclear license, bag of words only, or gated and small; no album or length to match on |

## Needs an account

Each holds lyrics only for a signed-in listener or a paid plan, and the tool never carries a
listener's session. A free account counts the same as a paid one: it is still the listener's
own session, every request would be tied to that account, and automated use that possibly
breaks the service's terms puts the account itself at risk.

| Source | Gives | Why not |
| --- | --- | --- |
| TIDAL | Timed and untimed | Its official API gives an app-only token no lyrics (probed 2026-10-21) |
| Musixmatch | Timed and untimed | The free API gives about 30% of each song; the desktop route returns canned hits, and the only working route forges its web app's signing |
| Spotify | Timed | Needs a signed-in account's token, free or Premium (free accounts show lyrics again since late 2024); its terms bar scraping |
| Deezer | Timed | Search works, but lyrics need a signed-in session |
| Apple Music | Timed | Needs a listener's media-user-token |
| Amazon Music | Timed in its app | No lyrics in its API |
| LyricsPlus (YouLy+) | Timed | Runs on pooled account cookies, behind proof-of-work |

## Ruled out by the hard rules

| Source | Gives | Why not |
| --- | --- | --- |
| QQ Music | Timed, word by word | The old search answers 500 and the unsigned one code 2001; working clients forge its request signature or pose as its Android app |
| Kuwo | Timed | Needs a key built into its client or a CSRF cookie; little Western indie |
| Lyricsify | Timed | A Cloudflare bot challenge |

## Gone, or adding nothing

| Source | Why not |
| --- | --- |
| Megalobiz | Its search answers HTTP 410 Gone |
| ChartLyrics | The service is gone |
| Kugou | Probed 2026-10-21 with nine famous punk, indie and non-English albums: timed lyrics the matcher accepts for 39 of 106 tracks, mostly each artist's best-known songs, which LRCLIB and NetEase already hold; most other searches found no song at all. Plain HTTP and no license grant besides. A fallback for the same catalog only if NetEase stops answering |
| LyricWiki | Shut down in 2020 |
| lrcmux | An MIT-licensed Go aggregator, hosted or self-hosted, read from its example config in October 2026: Genius, Kugou, LRCLIB, Musixmatch, NetEase and YouTube Music. Every one is already used or judged here, and its Musixmatch rotates a pool of tokens, which the hard rules rule out. The hosted instance would also receive every track's tags |
| LRCLIB's database dump | Measured in October 2026: a timed record the matcher accepts for about 2 in 1,000 tracks the live chain found nothing for, while finding about 96% of a sample the chain had found. What the live API misses is missing from LRCLIB, so no option reads it |
| Genius's internal JSON | The same data as Genius |
| Lyrics.com (STANDS4 API) | Its documentation, read in October 2026: a search returns song, artist and album names with links to Lyrics.com pages, never the text. Reading the pages would be scraping, and adds nothing over Genius |
| Better Lyrics backends | Serve their extension only, with no API or terms |
| SoundCloud | No lyrics product |
| MusicBrainz, Discogs | Hold no lyrics; MusicBrainz is Bandcamp's directory |
