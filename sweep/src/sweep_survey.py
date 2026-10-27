"""The survey: every track of every album classified, and the albums a run would visit.

An album is found by the shared library walk (`library_walk.album_dirs`) and its tracks
by the album tool's own discovery (`lyrics_album.discover_tracks`), so the sweep sees
exactly the albums and tracks the album tool would. The walk follows links, so the sweep
drops every album whose Artist or Album folder is a link, or that resolves outside the
library (`contained_albums`): the album tool writes into each album it runs on, and a link
would take those writes out of the library. The album tool does not refuse a link itself:
it resolves the album folder it is given, so a linked album runs wherever the link
leads. These checks are the only guard, for a linked album folder and a linked artist
folder alike, so they must stay. Each album dropped is named. A run makes the same check
again just before each visit (`linked_now`), since a folder can be swapped for a link
during a long sweep. Each track is classified by the album tool's classifier
(`lyrics_inventory.classify_track`): synced, untimed only, or none. For a sweep with
``--refetch-implausible``, each track is also judged by the album tool's own rule for
lyrics serving it that are implausible (`lyrics_refetch.serving_implausible`), and a
track they are, named with the check's FLAG, makes its album one to visit, however
synced: that option exists to ask such a track again. The survey writes
nothing and contacts nothing. `plan_visits` splits the albums that need a visit into
those a run visits and those it passes over as done, for the survey and the run alike,
and puts those it is told to visit last (a challenged owed album,
`sweep_challenged`) after the others.

A track whose classification raises, a sidecar that is not UTF-8 for one, is counted as
unreadable, named with its error's type, rather than ending the survey, and its album is
one to visit: the album tool reports the track itself, by the same error, when it runs.
Any error at all is that track's, as the album tool's own per-track boundary holds it,
so one malformed file in one album never ends a survey of the library. Nothing here
prints.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import sweep_located  # noqa: F401 - puts album and the libraries on sys.path
from library_walk import album_dirs, ignore_skipped
from lyrics_album import AlbumUnusableError, discover_tracks
from lyrics_inventory import TrackLyricsKind, classify_track
from lyrics_refetch import serving_implausible
from lyrics_sidecar import plan_sidecars
from lyrics_tags import read_tags


class LibraryUnlistableError(Exception):
    """The library's own folder could not be listed: the one error a survey ends on.

    Raised from the library walk's root listing alone, so a caller tells it apart from
    any other `PermissionError`, which is an album's or the sweep's own, never the
    library's. *library* is the folder, and the walk's error is its ``__cause__``.
    """

    def __init__(self, library: Path) -> None:
        """Name *library* as the folder that could not be listed."""
        super().__init__(f"the library cannot be listed: {library}")
        self.library = library


@dataclass(frozen=True)
class AlbumSurvey:
    """One album's tracks, counted by the lyrics they hold.

    *unreadable* names each track that could not be classified, by its album-relative
    path and its error's type: ``Disc 2/02.mp3 (SidecarDecodeError)``. *implausible*
    names each track serving implausible lyrics, by its path and the check's FLAG,
    ``01.mp3 (past end: last line ...)``; only a ``--refetch-implausible`` survey judges
    that. *has_mp3* is False for a folder holding no MP3, which is no album to run.
    """

    album: Path
    synced: int = 0
    untimed: int = 0
    none: int = 0
    unreadable: tuple[str, ...] = ()
    has_mp3: bool = True
    implausible: tuple[str, ...] = ()

    @property
    def needs_visit(self) -> bool:
        """True when a track lacks synced lyrics, is implausible, or is unclassified."""
        return bool(self.untimed or self.none or self.unreadable or self.implausible)


def _implausible(mp3: Path, album: Path, lang: str) -> str | None:
    """Why the lyrics serving *mp3* are implausible, as ``<rule>: <measure>``; else None.

    Read as a ``--refetch-implausible`` run reads the track, by the album tool's rule
    (`serving_implausible`); raises what classifying it raises.
    """
    flag = serving_implausible(plan_sidecars(mp3, album), read_tags(mp3), lang)
    return None if flag is None else f"{flag.rule}: {flag.detail}"


def survey_album(
    album: Path, *, lang: str, refetch_implausible: bool = False
) -> AlbumSurvey:
    """Classify every track of *album*; *lang* ranks a track's frames as a run would.

    With *refetch_implausible*, each track serving implausible lyrics is named too.
    """
    try:
        tracks = discover_tracks(album).tracks
    except AlbumUnusableError:
        return AlbumSurvey(album, has_mp3=False)
    kinds: list[TrackLyricsKind] = []
    unreadable: list[str] = []
    implausible: list[str] = []
    for mp3 in tracks:
        name = mp3.relative_to(album).as_posix()
        try:
            kind = classify_track(mp3, album=album, lang=lang)
            why = _implausible(mp3, album, lang) if refetch_implausible else None
        # The per-track boundary: a sidecar or tag the album tool cannot read, a file
        # that cannot be opened, a tag mutagen cannot parse, or anything a malformed
        # file raises that no reader names, is this track's, and the survey moves on.
        except Exception as error:
            unreadable.append(f"{name} ({type(error).__name__})")
            continue
        kinds.append(kind)
        if why is not None:
            implausible.append(f"{name} ({why})")
    return AlbumSurvey(
        album,
        synced=kinds.count(TrackLyricsKind.SYNCED),
        untimed=kinds.count(TrackLyricsKind.UNTIMED),
        none=kinds.count(TrackLyricsKind.NONE),
        unreadable=tuple(unreadable),
        implausible=tuple(implausible),
    )


def _linked(album: Path, root: Path) -> bool:
    """True when *album* or its artist folder is a link, or it resolves outside *root*."""
    return (
        album.parent.is_symlink()
        or album.is_symlink()
        or not album.resolve().is_relative_to(root)
    )


def is_linked(album: Path, library: Path) -> bool:
    """True when *album* is reached through a link, as `contained_albums` drops it.

    For the check a run, or an accept, makes again just before it visits *album*.
    """
    return _linked(album, library.resolve())


def relinked_notice(album: Path, library: Path) -> str:
    """The notice for *album*, found by the walk but reached through a link by now."""
    return (
        f"skipped album {album.relative_to(library).as_posix()}: since the survey, "
        "its artist or album folder became a link, or it moved outside the library; "
        "not run"
    )


def linked_now(library: Path, album: Path, progress: Callable[[str], None]) -> bool:
    """True, said through *progress*, when *album* is now reached through a link.

    The survey dropped every album reached through one, but a folder can be swapped
    for a link while a long sweep runs, and the album tool would follow it: it resolves
    the album folder it is given. So a run makes the check again just before each visit.
    """
    if not is_linked(album, library):
        return False
    progress(relinked_notice(album, library))
    return True


def contained_albums(
    library: Path,
    *,
    on_skip: Callable[[str], None] = ignore_skipped,
    on_linked: Callable[[str], None] = ignore_skipped,
) -> list[Path]:
    """The walk's albums of *library*, those reached through a link dropped.

    *on_skip* hears the name of each artist folder the walk could not list, and
    *on_linked* each album dropped, as ``Artist/Album`` relative to *library*. A root
    that cannot be listed raises `LibraryUnlistableError`: the walk passes over an
    artist folder it cannot list, so its only `PermissionError` is the root's own.
    """
    root = library.resolve()
    try:
        found = album_dirs(library, on_skip)
    except PermissionError as error:
        raise LibraryUnlistableError(library) from error
    albums: list[Path] = []
    for album in found:
        if _linked(album, root):
            on_linked(album.relative_to(library).as_posix())
        else:
            albums.append(album)
    return albums


def survey_library(
    library: Path,
    *,
    lang: str,
    on_skip: Callable[[str], None] = ignore_skipped,
    on_linked: Callable[[str], None] = ignore_skipped,
    refetch_implausible: bool = False,
) -> list[AlbumSurvey]:
    """Survey every album of *library* not reached through a link, in the walk's order.

    *on_skip* and *on_linked* are as `contained_albums` takes them, and a root that
    cannot be listed raises `LibraryUnlistableError` as it does. *refetch_implausible*
    is as `survey_album` takes it.
    """
    albums = contained_albums(library, on_skip=on_skip, on_linked=on_linked)
    return [
        survey_album(album, lang=lang, refetch_implausible=refetch_implausible)
        for album in albums
    ]


@dataclass(frozen=True)
class VisitPlan:
    """The albums a run visits, and those it passes over as done, each in walk order."""

    to_visit: tuple[Path, ...]
    done: tuple[Path, ...]


def plan_visits(
    library: Path,
    surveys: Sequence[AlbumSurvey],
    done: Collection[str],
    *,
    restart: bool,
    last: Collection[str] = (),
) -> VisitPlan:
    """Of the albums of *surveys* that need a visit, those a run visits and passes over.

    *done* names, relative to *library*, the albums the ledger holds as done in the
    run's language and by its pass (`sweep_ledger.done_albums`); a *restart* visits
    them as well. The survey and the run both plan
    with this, so the list a survey prints is the list a run visits. An album whose
    every track is synced needs no visit, done or not, so it is in neither list, unless
    a ``--refetch-implausible`` survey found a track serving implausible lyrics.
    The albums *last* names come after the others to visit, each group in walk order.
    """
    first: list[Path] = []
    after: list[Path] = []
    passed: list[Path] = []
    for survey, name in _named_needing_visit(library, surveys):
        if not restart and name in done:
            passed.append(survey.album)
        else:
            (after if name in last else first).append(survey.album)
    return VisitPlan(tuple(first + after), tuple(passed))


def _named_needing_visit(
    library: Path, surveys: Sequence[AlbumSurvey]
) -> Iterator[tuple[AlbumSurvey, str]]:
    """Each of *surveys* that needs a visit, with its album's name in the ledger."""
    for survey in surveys:
        if survey.needs_visit:
            yield survey, survey.album.relative_to(library).as_posix()


def needing_visit(library: Path, surveys: Sequence[AlbumSurvey]) -> tuple[str, ...]:
    """The albums of *surveys* that need a visit, named as the ledger names them."""
    return tuple(name for _, name in _named_needing_visit(library, surveys))
