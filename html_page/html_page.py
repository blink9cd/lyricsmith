#!/usr/bin/env python3
r"""Lay out a self-contained HTML report page: the frame every tool's page shares. Pure.

Why this exists
---------------
Several tools write a read-only report as one HTML file a person opens in a browser. The
page around their findings is the same for all of them: the style (light and dark),
escaped cells, a table in its scrolling wrapper, the summary table, a section, and the
whole document. Kept here once, every tool's page reads alike, and a fix to the frame
reaches all of them.

It names no tool and no check: each tool's own page module supplies its title, header,
sections and read-only line. The page holds inline CSS and nothing else: no script, and
nothing a browser would fetch (no font, image or stylesheet), so it reads the same
offline.

Every value from outside goes through `escaped`: first `shown` (a control character as a
visible ``\xNN``, a lone surrogate as its ``\uXXXX``), then HTML escaping, quotes
included, so no name can become markup. `shown` is public because a tool's plain-text
report needs the same escape for its own lines.

`ParsedPage` and `page_problems` are the check every tool's tests hold a page to: a
strict parse, each element closed in order, each id once, no attribute twice on one
element, each link an anchor on the page, no URL-bearing attribute (a ``srcdoc`` among
them), event handler or style attribute, and nothing in the markup or in an attribute's
decoded value that would fetch or run. The markup is the tags, their attributes,
comments and the style and script elements' content; CSS is also read with its escapes
decoded. The page's text is left out, so an escaped name that reads like a fetch
(``AC//DC``) is not one. One copy serves every tool's suite.

Entry point assumptions
-----------------------
A library with no CLI and no I/O: importers put this folder on ``sys.path`` (see
``html_page.md``) and call its functions. It imports only the standard library.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from itertools import chain, repeat

#: The summary's word for a check that did not run.
NOT_RUN = "not run"

#: The page's whole shared style. Colors are custom properties, redefined for dark mode;
#: tables scroll sideways inside their own wrapper, so the page never does. A tool adds
#: rules of its own after these, through `page`'s ``extra_css``.
CSS = """\
:root {
  color-scheme: light dark;
  --bg: #fbfaf7;
  --fg: #1d1d1f;
  --muted: #5f5f66;
  --line: #e3e1dc;
  --head: #f1efe9;
  --accent: #a8401f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17171a;
    --fg: #ececee;
    --muted: #a0a0a8;
    --line: #2c2c31;
    --head: #212126;
    --accent: #e2876b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
}
header, main, footer { max-width: 1100px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 24px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 32px 0 4px; }
.library, .note, footer { color: var(--muted); }
.library { overflow-wrap: anywhere; margin: 0; }
.tallies {
  list-style: none; margin: 8px 0 0; padding: 0;
  display: flex; flex-wrap: wrap; gap: 4px 16px;
}
.note { margin: 0 0 10px; }
.wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td {
  text-align: left; vertical-align: top; padding: 7px 10px;
  border-bottom: 1px solid var(--line);
}
th { background: var(--head); font-weight: 600; white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.count {
  display: inline-block; min-width: 2em; padding: 0 8px; border-radius: 10px;
  background: var(--head); color: var(--accent); text-align: center;
}
a { color: var(--accent); }
ol, ul.differs { margin: 0; padding-left: 18px; }
footer { padding-top: 24px; padding-bottom: 48px; font-size: 14px; }
"""


# --- Text from outside -------------------------------------------------------------

#: C0 controls, DEL and C1 controls, each shown as its ``\\xNN`` escape.
_CONTROLS = {code: f"\\x{code:02x}" for code in (*range(0x20), *range(0x7F, 0xA0))}


def shown(text: str) -> str:
    r"""*text* with each control character, and each lone surrogate, as an escape.

    A linefeed in a folder name would otherwise split one row into two, and an escape
    sequence would drive the terminal. A lone surrogate (an undecodable byte in a name
    given on the command line) would make the strict UTF-8 stdout raise.
    """
    return text.translate(_CONTROLS).encode("utf-8", "backslashreplace").decode("utf-8")


# --- Cells ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One table cell: its markup, already escaped, and whether it holds a number."""

    markup: str
    numeric: bool = False


Row = tuple[Cell, ...]


def escaped(value: object) -> str:
    """*value* as page text: control characters shown, then every markup char escaped."""
    return html.escape(shown(str(value)), quote=True)


# --- Tables --------------------------------------------------------------------------


def row_markup(row: Row, css_class: str | None = None) -> str:
    """One table row, with *css_class* on it when given.

    *css_class* is interpolated unescaped, so the caller must pass a fixed identifier
    token, never data.
    """
    cells = (
        f'<td class="num">{c.markup}</td>' if c.numeric else f"<td>{c.markup}</td>"
        for c in row
    )
    opening = "<tr>" if css_class is None else f'<tr class="{css_class}">'
    return f"{opening}{''.join(cells)}</tr>"


def table(
    columns: Sequence[str],
    rows: Sequence[Row],
    classes: Sequence[str | None] = (),
) -> list[str]:
    """A table in its own scrolling wrapper.

    *classes* gives each row its class, in order; rows past its end take none.
    """
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    row_classes = chain(classes, repeat(None))
    return [
        '<div class="wrap"><table>',
        f"<thead><tr>{head}</tr></thead>",
        "<tbody>",
        *map(row_markup, rows, row_classes),
        "</tbody>",
        "</table></div>",
    ]


# --- The summary and the sections ----------------------------------------------------


def summary_row(heading: str, count: int | None, anchor: str | None) -> Row:
    """A summary row: *heading* linked to *anchor* when given; None counts as not run.

    *anchor* is interpolated unescaped, so the caller must pass a fixed identifier
    token, never data.
    """
    shown_heading = html.escape(heading)
    if count is None:
        return (Cell(shown_heading), Cell(NOT_RUN))
    if anchor is not None:
        shown_heading = f'<a href="#{anchor}">{shown_heading}</a>'
    return (Cell(shown_heading), Cell(str(count), numeric=True))


def summary_section(rows: Sequence[Row]) -> list[str]:
    """The summary section: one row per check, each with its count."""
    return [
        '<section id="summary">',
        "<h2>Summary</h2>",
        *table(("Check", "Found"), rows),
        "</section>",
    ]


def section(
    anchor: str, heading: str, count: int, note: str, body: Sequence[str]
) -> list[str]:
    """One section: its heading and count, its one-sentence note, then *body*.

    *anchor* is interpolated unescaped, so the caller must pass a fixed identifier
    token, never data.
    """
    return [
        f'<section id="{anchor}">',
        f'<h2>{html.escape(heading)} <span class="count">{count}</span></h2>',
        f'<p class="note">{html.escape(note, quote=False)}</p>',
        *body,
        "</section>",
    ]


# --- The page ------------------------------------------------------------------------


def page(
    *,
    title: str,
    header: Sequence[str],
    main: Sequence[str],
    read_only: str,
    footer: Sequence[str] = (),
    extra_css: str = "",
    generator: str | None = None,
) -> str:
    """The whole document.

    *title* heads the page and names it; *header* follows the title inside the page
    header, *main* is the summary and the sections, and the footer holds *read_only*,
    then each line of *footer*, each a paragraph of plain text. *extra_css* is the
    tool's own rules, after the shared ones. *generator*, when given, names the tool
    in a ``generator`` meta element, a fixed line the tool can know its own page by;
    left out, the page is byte for byte the page without it.
    """
    shown_title = html.escape(title)
    named = (
        []
        if generator is None
        else [f'<meta name="generator" content="{html.escape(generator)}">']
    )
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        *named,
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{shown_title}</title>",
        f"<style>\n{CSS}{extra_css}</style>",
        "</head>",
        "<body>",
        "<header>",
        f"<h1>{shown_title}</h1>",
        *header,
        "</header>",
        "<main>",
        *main,
        "</main>",
        "<footer>",
        *(f"<p>{html.escape(line, quote=False)}</p>" for line in (read_only, *footer)),
        "</footer>",
        "</body>",
        "</html>",
    ]
    return "".join(f"{line}\n" for line in lines)


# --- The check -----------------------------------------------------------------------

#: The elements that take no end tag, so the parse never waits for one: HTML's void
#: elements, ``param`` (obsolete, but still void to a browser) among them.
VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
#: What would make a browser fetch or run something, as it would appear in a page,
#: matched lowercased against the page's markup (`ParsedPage.markup`) and against each
#: attribute's decoded value (`ParsedPage.values`), never its text. A scheme is matched
#: with its colon, so an attribute named ``http-equiv`` is no fetch.
FETCHING = (
    "<script",
    "javascript:",
    "http:",
    "https:",
    "//",
    "url(",
    "image-set(",
    "@import",
    "<link",
    " src=",
)
#: The attributes whose value a browser may fetch, or send a request to, other than
#: ``href``, which is checked as a link. ``xlink:href`` is SVG's older link.
URL_ATTRIBUTES = frozenset(
    "action background cite data formaction longdesc manifest ping poster src srcset"
    " xlink:href".split()
)
#: The attributes whose value is a whole document a browser renders: reported whatever
#: they hold, since a document can run or fetch in more ways than any marker names.
DOCUMENT_ATTRIBUTES = frozenset({"srcdoc"})
#: A meta element's ``http-equiv`` value that makes its ``content`` a URL to load.
_REFRESH = "refresh"
#: The element whose content is CSS.
_STYLE = "style"
#: The elements whose content is code, not text: their content counts as markup.
_CODE_ELEMENTS = frozenset({_STYLE, "script"})
#: What starts an event handler's attribute name.
_HANDLER_PREFIX = "on"
#: A CSS escape: a backslash and 1-6 hex digits with one optional whitespace after (a CRLF
#: pair is one), or a backslash before any other character (CSS Syntax).
_CSS_ESCAPE = re.compile(
    r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\n\r\f])?|(.))", re.DOTALL
)
#: Past the last code point, or a surrogate or NUL, a hex escape reads as U+FFFD.
_MAX_CODE_POINT = 0x10FFFF
_SURROGATES = range(0xD800, 0xE000)


def _css_code_point(match: re.Match[str]) -> str:
    """The character the CSS escape *match* stands for."""
    digits, other = match.groups()
    if digits is None:
        return str(other)
    code = int(digits, 16)
    bad = code == 0 or code > _MAX_CODE_POINT or code in _SURROGATES
    return "�" if bad else chr(code)


def css_unescaped(text: str) -> str:
    r"""CSS *text* with each escape decoded, as a browser's CSS parser reads it.

    ``u\72l(`` is ``url(`` and ``\@import`` is ``@import``.
    """
    return _CSS_ESCAPE.sub(_css_code_point, text)


def _repeated_names(attrs: list[tuple[str, str | None]]) -> list[str]:
    """Each attribute name given more than once among *attrs*, once, in page order."""
    names = [name for name, _ in attrs]
    return list(dict.fromkeys(name for name in names if names.count(name) > 1))


def _first_of_each(attrs: list[tuple[str, str | None]]) -> dict[str, str | None]:
    """Each attribute name with its first value, the one a browser keeps."""
    named: dict[str, str | None] = {}
    for name, value in attrs:
        named.setdefault(name, value)
    return named


def _is_source(tag: str, name: str, named: dict[str, str | None]) -> bool:
    """Whether attribute *name* of *tag*, among *named*, holds a URL a browser loads."""
    if name in URL_ATTRIBUTES or name in DOCUMENT_ATTRIBUTES:
        return True
    # A refresh meta element loads the URL in its content, after its delay.
    refresh = (named.get("http-equiv") or "").lower() == _REFRESH
    return tag == "meta" and name == "content" and refresh


class ParsedPage(HTMLParser):
    """A page parsed whole, raising ValueError on an element closed out of order.

    ``stack`` is what stays open at the end; ``ids``, ``links`` (each ``href``),
    ``sources`` (each URL-bearing attribute, as ``name=value``), ``handlers`` (each
    ``on*`` attribute, as its tag and name), ``styles`` (each ``style`` attribute),
    ``repeated`` (each attribute given twice on one element, as its tag and name),
    ``sheets`` (each style element's content), ``values``, ``markup`` and ``text`` are
    recorded in page order. Of an attribute given twice, its first value is the one
    recorded, as a browser keeps it. ``values`` is each attribute's value with its
    character references decoded; ``markup`` is each start tag as written, each
    comment, and the content of each style and script element; ``text`` is every
    element's content.
    """

    def __init__(self, page: str) -> None:
        """Parse *page* whole."""
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.ids: list[str] = []
        self.links: list[str] = []
        self.sources: list[str] = []
        self.handlers: list[str] = []
        self.styles: list[str] = []
        self.repeated: list[str] = []
        self.sheets: list[str] = []
        self.values: list[str] = []
        self.markup: list[str] = []
        self.text: list[str] = []
        self.feed(page)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open *tag* unless it is void; record it and what its attributes hold."""
        self.markup.append(self.get_starttag_text() or "")
        named = _first_of_each(attrs)
        # The parser lowercases attribute names, so TITLE repeats title.
        self.repeated += (f"{tag} {name}" for name in _repeated_names(attrs))
        for key, into in (
            ("id", self.ids),
            ("href", self.links),
            ("style", self.styles),
        ):
            if named.get(key) is not None:
                into.append(str(named[key]))
        # The parser decodes character references in a value, so an encoded marker
        # (``&lt;script&gt;`` in a srcdoc) is matched as the browser would read it.
        self.values += (value for _, value in attrs if value is not None)
        self.sources += (
            f"{name}={value}"
            for name, value in attrs
            if value is not None and _is_source(tag, name, named)
        )
        # The parser lowercases attribute names, so ONCLICK is caught as onclick.
        self.handlers += (
            f"{tag} {name}" for name, _ in attrs if name.startswith(_HANDLER_PREFIX)
        )
        if tag == _STYLE:
            self.sheets.append("")
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        """Close *tag*, which must be the innermost open element."""
        if not self.stack or self.stack[-1] != tag:
            raise ValueError(f"</{tag}> closes {self.stack[-1:]}")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        """Keep the text; inside a style or script element, keep it as markup too."""
        self.text.append(data)
        if self.stack and self.stack[-1] in _CODE_ELEMENTS:
            self.markup.append(data)
        if self.stack and self.stack[-1] == _STYLE:
            self.sheets[-1] += data

    def handle_comment(self, data: str) -> None:
        """Keep a comment as markup: what it holds is no text a reader sees."""
        self.markup.append(f"<!--{data}-->")


def _read_pieces(parsed: ParsedPage) -> list[str]:
    """Each piece of *parsed* that `FETCHING` is matched against, lowercased.

    The markup whole; each decoded attribute value on its own, so no marker spans two;
    and each style attribute and style element with its CSS escapes decoded, a style
    element's content also with its character references decoded first, as a browser
    does inside SVG or MathML.
    """
    css = chain(parsed.styles, parsed.sheets, map(html.unescape, parsed.sheets))
    pieces = chain(["".join(parsed.markup)], parsed.values, map(css_unescaped, css))
    return [piece.lower() for piece in pieces]


def page_problems(page: str) -> list[str]:
    """Each way *page* fails the check, in a fixed order; empty for a sound page."""
    try:
        parsed = ParsedPage(page)
    except ValueError as error:
        return [str(error)]
    found = [f"left open: {parsed.stack}"] if parsed.stack else []
    seen: set[str] = set()
    for anchor in parsed.ids:
        if anchor in seen:
            found.append(f"id repeated: {anchor}")
        seen.add(anchor)
    found.extend(f"attribute repeated: {repeat}" for repeat in parsed.repeated)
    on_page = {f"#{anchor}" for anchor in seen}
    found.extend(
        f"link not on the page: {link}" for link in parsed.links if link not in on_page
    )
    found.extend(f"source: {source}" for source in parsed.sources)
    found.extend(f"handler: {handler}" for handler in parsed.handlers)
    found.extend(f"style attribute: {style}" for style in parsed.styles)
    lowered = _read_pieces(parsed)
    found.extend(
        f"fetches or runs: {marker}"
        for marker in FETCHING
        if any(marker in piece for piece in lowered)
    )
    return found
