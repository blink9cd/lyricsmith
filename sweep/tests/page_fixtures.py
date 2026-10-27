"""Shared fixtures for the silence page's suites: the check every page must pass.

`assert_well_formed` holds a page to the shared HTML page library's check
(`html_page.page_problems`): every element closed in order, every id once, every link
an anchor on the page, and nothing a browser would fetch or run. The parse and the
check live once, in that library, for every tool's suites.

Not discovered as a suite (no ``test_`` prefix). Suites import it by bare name with
this directory on ``sys.path``, after the entry point's bootstrap has put the library
there.
"""

from __future__ import annotations

import unittest

import html_page


def assert_well_formed(case: unittest.TestCase, page: str) -> html_page.ParsedPage:
    """Check *page* passes the shared check; return its parse."""
    case.assertEqual(html_page.page_problems(page), [])
    return html_page.ParsedPage(page)
