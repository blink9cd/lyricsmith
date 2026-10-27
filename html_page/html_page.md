# html_page.py

The frame of a self-contained HTML report page, shared by every tool that writes one: the
style, the escaped cells, the tables, the summary, the sections and the document around
them. Each tool supplies only its own wording.

## Why it exists

Several tools write a read-only report as a single HTML file that a person opens in a
browser. The page around their findings is the same for all of them, so it lives here once:
the pages read alike, and a fix to the frame reaches every one of them. A tool's own page
module names its title, its header, its sections with their one-sentence notes, and its
read-only line; this library names no tool and no check.

The page is one HTML5 document with inline CSS and nothing else: no script, and nothing a
browser would fetch (no font, image or stylesheet), so it reads the same offline and from a
`file://` path. Colors are custom properties redefined for dark mode, and each table scrolls
sideways inside its own wrapper, so on a phone the page itself never does.

## The escape: `shown`, then `escaped`

Every value from outside the tool (a file or folder name, a tag, a reason quoted from a
child process) is text the tool did not write. `escaped(value)` turns it into page text in
two steps:

1. `shown` spells each C0 control, DEL and C1 control as a visible `\xNN` (lowercase hex, a
   tab as `\x09`, never `\t`), and then each lone surrogate, an undecodable byte in a name,
   as its backslash escape. Every other character, accents, CJK and the direction marks
   included, comes back unchanged.
2. `html.escape` with `quote=True` escapes `<`, `>`, `&` and both quotes, so no name can
   become markup or leave an attribute.

`shown` is public because a tool's plain-text report needs the same escape for its own
lines: a linefeed in a folder name would split one row into two, an escape sequence would
drive the terminal, and a lone surrogate would make a strict UTF-8 stdout raise. One copy
serves both forms: a tool using `shown` for both shows a name the same way in each.

It is deliberately narrower than `stdio_utf8.spell_unsafe`, which also spells the
direction marks and the line and paragraph separators. This escape keeps those characters
as they are, so a report built on one cannot switch to the other without changing its output.

## Usage

Importers put this folder on `sys.path`, the same way they reach any other shared library,
and call the functions:

```python
import html_page
from html_page import Cell

rows = [(Cell(html_page.escaped(name)), Cell(str(size), numeric=True))]
page = html_page.page(
    title="A report",
    header=["<p>What was read, and when.</p>"],
    main=[
        *html_page.summary_section([html_page.summary_row("Found", 1, "found")]),
        *html_page.section(
            "found",
            "Found",
            1,
            "One sentence.",
            html_page.table(("Name", "Size"), rows),
        ),
    ],
    read_only="Nothing was changed.",
)
```

## Public surface

| Name | Meaning |
| --- | --- |
| `shown(text)` | *text* with each control character and lone surrogate as its escape. |
| `escaped(value)` | `str(value)` through `shown`, then HTML-escaped, quotes included. |
| `Cell` | One table cell: its markup, already escaped, and whether it holds a number (`numeric`, which keeps it on one line in tabular figures). |
| `Row` | A tuple of `Cell`. |
| `row_markup(row, css_class)` | One `<tr>`, with *css_class* on it when given. |
| `table(columns, rows, classes)` | A table in its scrolling wrapper; *classes* gives each row its class, in order. |
| `summary_row(heading, count, anchor)` | A summary row: the heading linked to *anchor* when given; a `None` count reads `not run`. |
| `summary_section(rows)` | The summary section: one row per check, with its count. |
| `section(anchor, heading, count, note, body)` | One section: its heading and count, its note, then *body*. |
| `page(...)` | The whole document, keyword-only: `title`, `header`, `main`, `read_only`, `footer`, `extra_css`, and `generator`, which names the tool in a `generator` meta element when given and adds nothing when left out. |
| `CSS` | The shared style; a tool adds its own rules after it through `extra_css`. |
| `NOT_RUN` | The summary's word for a check that did not run. |
| `ParsedPage(page)` | A strict parse of *page*: `ValueError` on an element closed out of order; `stack` (left open), `ids`, `links`, `sources` (each URL-bearing attribute as `name=value`), `handlers`, `styles`, `repeated` (each attribute given twice on one element, as its tag and name), `sheets` (each `style` element's content), `values` (each attribute's value, character references decoded), `markup` and `text`, in page order. Of an attribute given twice, the first value is the one recorded, as a browser keeps it. |
| `page_problems(page)` | Each way *page* fails the check below, as a line of text; empty for a sound page. |
| `css_unescaped(text)` | CSS *text* with each escape decoded, as a browser's CSS parser reads it: `u\72l(` is `url(`. |
| `DOCUMENT_ATTRIBUTES` | The attributes whose value is a whole document a browser renders: `srcdoc`. |
| `VOID_ELEMENTS` | HTML's void elements, which take no end tag, so the parse never waits for one. |
| `URL_ATTRIBUTES` | The attributes, other than `href`, whose value a browser may fetch or send a request to. |
| `FETCHING` | What would make a browser fetch or run something, as it would appear in a page's markup or a decoded attribute value. |

Every function returns markup lines (or the whole page) and does no I/O: writing the file,
atomically and outside any repository, is the calling tool's job.

## The check every page is held to

`page_problems` is what each tool's tests assert of a page it writes, so the parse and
the rules live here once rather than in each suite. A sound page has:

- every element closed, and in order (a misclosed one is reported as the parse's error);
- every `id` once;
- no attribute twice on one element, names compared case ignored, each reported as
  `attribute repeated: tag name`; a browser keeps the first value, so the `id`, link,
  `style` and refresh checks read the first one, while every value is still matched
  for fetches, sources and handlers;
- every link an anchor to an `id` on the page;
- no URL-bearing attribute (any of `URL_ATTRIBUTES`: `src`, `srcset`, `poster`, `data`,
  `action` and the rest, and the `content` of a `refresh` meta element), and no
  `srcdoc`, whatever it holds, since its value is a whole document that can run or
  fetch in more ways than any marker names; each is reported as `source: name=value`;
  no event handler (an `on*` attribute, `ontoggle` or `onclick` say), and no `style`
  attribute;
- none of `FETCHING` in the page's markup, case ignored: each start tag as written, its
  attributes included, each comment, and the content of each `style` and `script`
  element; nor in any attribute's value with its character references decoded, so an
  encoded `&lt;script&gt;` in a `srcdoc` is read as the browser reads it. A scheme is
  matched with its colon (`http:`, `https:`, `javascript:`), so a `http-equiv`
  attribute is no fetch. The markers are a backstop, not the defense: a browser strips
  a tab inside `java&#x09;script:`, which no marker matches, and such a URL is caught
  because every attribute that can run one is already a link or a source. CSS is also
  read as a browser's CSS parser reads it: each `style` element's content and each
  `style` attribute's value is matched again with its CSS escapes decoded
  (`css_unescaped`: a backslash and 1-6 hex digits, with one optional whitespace after,
  a CRLF pair counting as one, is that code point, U+FFFD for a NUL, a surrogate or one
  past Unicode; a backslash before any other character is that character), so
  `u\72l(` is `url(`. A `style` element's content is matched once more with its
  character references decoded first, since inside SVG or MathML a browser decodes
  them there: `&#64;import` is `@import`.
  The page's text is not read for them, since every value in it is escaped, so a name
  that reads like a fetch (`AC//DC`, `http`) is no problem.

A suite asserts the list is empty, so a failure names each problem, and reads the page's
text or ids from `ParsedPage` when it checks content. Both are pure and use only the
standard library's `html.parser`; nothing a page builder calls depends on them.

## Notes

- **Fixed tokens are interpolated unescaped.** An `anchor` or a `css_class` lands in an
  attribute as given, so a caller passes a fixed identifier, never data. Headings, notes,
  column names, the title and the footer lines are escaped here; a `Cell`'s markup is
  escaped by the caller, through `escaped`, because a cell may hold markup of its own (a
  link, a list).
- **Byte-for-byte stability is a contract.** A tool's suite may pin its whole page:
  `library_audit`'s builds three pages and asserts each one's length and SHA-256 digest.
  Any change to the markup or the style here changes every such page, and its pins must
  be re-derived on purpose. A keyword left at its default, `generator` among them,
  changes nothing.
- It imports only the standard library, and ships no `samples/`: it has no CLI, and each
  tool that writes a page ships the sample that shows one.

## Testing it

`test_html_page.py` beside this file pins each piece's markup, checks that a whole page
passes `page_problems`, and pins each problem the check names. Run it directly with Python.
