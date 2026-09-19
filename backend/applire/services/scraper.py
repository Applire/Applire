# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""
Tiered job-description scraper.

Tier 1: httpx (plain HTTP fetch) + BeautifulSoup extraction.
Tier 2: Playwright headless Chromium for JS-rendered pages (StepStone, Indeed DACH).
Fallback: raises ScraperError with human-readable instructions.

What is returned is the POSTING, not the page: every consumer of the stored
``job_analyses.raw_text`` reads it as the employer's own words — the keyword
ledger's ``jd_qualifying_phrase`` quotes it verbatim into the writer's input
view, the letter's recipient extraction reads the company out of it, and
``raw_text_hash`` is what repost detection compares. A page wrapper that also
carries a sign-in modal and ten other employers' postings corrupts all four
(#722).

Public API:
    scrape_job_url(url: str) -> str
    ScraperError
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

_MIN_TEXT_LENGTH = 200

# #722: a job page's posting sits inside a container that the page wrapper shares
# with navigation, a sign-in modal and a "similar jobs" rail. Two mechanisms find
# the posting, in this order.
#
# (1) The container is usually NAMED. `_DESCRIPTION_HINTS` are id/class substrings
#     that name a posting body; the DENSEST match wins, not the first in document
#     order — LinkedIn nests `show-more-less-html__markup` inside
#     `description__text`, and the outer node is the complete posting.
# (2) When nothing is named, the wrapper is trimmed structurally: a `<main>` whose
#     text is more than `_WRAPPER_TEXT_RATIO` times its densest block-level
#     descendant is chrome AROUND a posting, not a posting. Measured on the
#     captured LinkedIn guest page of 2026-09-16 (#722): `<main>` 11,667 chars vs
#     the posting block 5,773 = 2.02x, the block carrying 49.5% of the wrapper's
#     text. `_MIN_BLOCK_SHARE` is what keeps the trim from picking ONE of several
#     sibling sections that together ARE the posting — split three ways each
#     sibling holds ~33% and the wrapper is returned whole.
_WRAPPER_TEXT_RATIO = 1.5
_MIN_BLOCK_SHARE = 0.40

# Chrome that is never part of a posting. `form` and `dialog` carry sign-in and
# application modals; `aside` carries the recommendation rail on the boards that
# use it (the captured LinkedIn page uses a plain `section`, which is why (2)
# exists — stripping `aside` changes that page by 0 chars).
_CHROME_TAGS = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "dialog",
    "form",
    "noscript",
)

_DESCRIPTION_HINTS = (
    "jobdescription",
    "job-description",
    "job_description",
    "description__text",            # LinkedIn guest page: the posting container
    "show-more-less-html__markup",  # LinkedIn guest page: the posting body
    "stellenbeschreibung",
    "vacancy-description",
    "job-ad",
    "jobad",
)

_WRAPPER_TAGS = ("article", "main", "body")

# Tier 2 waits for the same containers tier 1 extracts from — otherwise a
# JS-rendered board renders the posting after we have already read the page.
_TIER2_WAIT_SELECTOR = (
    "article, main, [id*=jobDescription], [class*=job-description], "
    "[class*=description__text], [class*=show-more-less-html__markup]"
)

# ADR-001 amended 2026-09-18: a job board's GUEST posting page — served to
# anyone without signing in — is fetched by tier 1 like any other board. Only a
# page actually behind a login falls back to manual paste. linkedin.com is
# therefore deliberately absent from this set (it is not JS-only) and is not
# blocked anywhere; `_DESCRIPTION_HINTS` names its two posting containers.
_JS_HOSTS: frozenset[str] = frozenset(
    {
        "www.stepstone.de",
        "www.stepstone.at",
        "www.stepstone.ch",
        "de.indeed.com",
        "at.indeed.com",
        "ch.indeed.com",
    }
)


class ScraperError(Exception):
    """Raised when all tiers fail to extract job text."""

    def __init__(self, url: str, reason: str, code: str = "jd_fetch_failed") -> None:
        self.url = url
        self.reason = reason
        self.code = code
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> None:
    """Raise ValueError if *url* is not a safe http(s) URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Only http and https URLs are supported, got scheme: {parsed.scheme!r}"
        )
    if not parsed.netloc:
        raise ValueError(f"Not a valid URL: {url!r}")


def _requires_js(url: str) -> bool:
    """Return True if *url* belongs to a known JS-rendered job board."""
    host = urlparse(url).hostname or ""
    return host in _JS_HOSTS


def _node_text(node) -> str:
    """The visible text of *node*, whitespace-collapsed."""
    return node.get_text(separator=" ", strip=True)


def _names_a_description(node) -> bool:
    """True if *node*'s own id or class names a job-description container."""
    haystack = " ".join(
        [node.get("id") or "", *(node.get("class") or [])]
    ).lower()
    if not haystack.strip():
        return False
    return any(hint in haystack for hint in _DESCRIPTION_HINTS)


def _description_blocks(soup) -> list:
    """Every description-named node, densest (most text) first.

    Densest, not first-in-document: LinkedIn's guest page nests
    `show-more-less-html__markup` (5,527 chars) inside `description__text`
    (5,547) and the outer node is the complete posting. Document order would
    return whichever the parser reached first.
    """
    nodes = [n for n in soup.find_all(True) if _names_a_description(n)]
    return sorted(nodes, key=lambda n: len(_node_text(n)), reverse=True)


def _trim_wrapper(wrapper, wrapper_len: int):
    """The posting inside a page *wrapper*, or None if the wrapper IS the posting.

    Fires only when both bounds hold (see the module constants): the wrapper
    carries more than `_WRAPPER_TEXT_RATIO` times the block's text, AND the
    block still carries at least `_MIN_BLOCK_SHARE` of the wrapper's. The
    second bound is the guard against trimming a posting that is split THREE OR
    MORE ways (each sibling under the share floor, so nothing qualifies and the
    wrapper is returned whole).
    It is not a guard against a TWO-way split where both siblings individually
    clear the floor (adversarial pass, 2026-09-19): a 50/50 layout has each half
    at 50% > 40%, so both are legitimate candidates and picking "the densest"
    silently ships one half and drops the other — the same corruption #722
    fixed, one shape further. So candidates are checked for disjointness first:
    when two qualifying nodes are siblings (neither contains the other), which
    is over the age of one HTML page, which one is "the posting" is genuinely
    ambiguous, and the safe answer is the same as the three-way case — return
    None and let the caller ship the wrapper whole. Only when every candidate
    is one continuous nested chain (the LinkedIn shape: a container inside a
    container inside a container, same text at every depth) does "densest
    wins" pick a real single posting.
    """
    upper = wrapper_len / _WRAPPER_TEXT_RATIO
    lower = max(_MIN_TEXT_LENGTH, wrapper_len * _MIN_BLOCK_SHARE)
    if lower > upper:
        return None
    candidates = []
    for node in wrapper.find_all(["div", "section", "article"]):
        length = len(_node_text(node))
        if lower <= length <= upper:
            candidates.append((length, node))
    if not candidates:
        return None
    for i, (_, a) in enumerate(candidates):
        for _, b in candidates[i + 1:]:
            if a in b.parents or b in a.parents:
                continue
            return None  # disjoint candidates: which half is "the posting"?
    best_len, best = max(candidates, key=lambda pair: pair[0])
    return best


def _extract_text(html: str) -> str | None:
    """
    Extract the job posting from *html*.

    Strips chrome (`_CHROME_TAGS`), then prefers a description-named container
    (densest match) over the page wrapper, and trims a wrapper that is chrome
    around a posting. Returns None if nothing reaches _MIN_TEXT_LENGTH.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(list(_CHROME_TAGS)):
        tag.decompose()

    for node in _description_blocks(soup):
        text = _node_text(node)
        if len(text) >= _MIN_TEXT_LENGTH:
            return text

    for name in _WRAPPER_TAGS:
        node = soup.find(name)
        if node is None:
            continue
        text = _node_text(node)
        if len(text) < _MIN_TEXT_LENGTH:
            continue
        inner = _trim_wrapper(node, len(text))
        if inner is not None:
            return _node_text(inner)
        return text

    return None


async def _fetch_tier1(url: str) -> str | None:
    """Fetch *url* with httpx and extract text. Returns None on failure or thin content."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Applire/1.0)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
        return _extract_text(response.text)
    except Exception:
        return None


async def _fetch_tier2(url: str) -> str | None:
    """Render *url* with Playwright Chromium and extract text."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_selector(
                _TIER2_WAIT_SELECTOR,
                timeout=10_000,
            )
        except Exception:
            pass  # proceed with whatever is rendered
        html = await page.content()
        await browser.close()

    return _extract_text(html)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scrape_job_url(url: str) -> str:
    """
    Return extracted job-description text for *url*.

    Raises:
        ValueError: if *url* is not a valid http(s) URL.
        ScraperError: if all tiers fail to extract usable text.
    """
    _validate_url(url)

    if not _requires_js(url):
        text = await _fetch_tier1(url)
        if text:
            return text

    try:
        text = await _fetch_tier2(url)
        if text:
            return text
    except ScraperError:
        raise
    except Exception as exc:
        raise ScraperError(url, f"JS render failed: {exc}") from exc

    raise ScraperError(
        url,
        "Could not extract job text from this page. "
        "Please paste the job description manually.",
    )
