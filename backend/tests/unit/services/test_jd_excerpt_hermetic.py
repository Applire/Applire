# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#271 Task 1 — hermetic twin of ``test_jd_excerpt.py``.

``test_jd_excerpt.py`` is gated by a MODULE-LEVEL ``pytestmark =
pytest.mark.skipif(...)`` keyed on ``.run5fixture/`` (git-excluded, absent
from CI) — every test in that module skips there, including its own
fixture-independent unit tests, because the skip mark applies to the whole
module regardless of which individual test needs the fixture. That file is
left exactly as it is (a useful local pin against the real run-5 scrape); THIS
file is a separate module, carries no skip mark, and reproduces the same
de-chroming property against a synthetic job posting so it is actually
exercised in CI.

Synthetic posting shape (mirrors the real run-5 LinkedIn scrape's shape, no
real content): a block of sign-in/cookie-consent chrome repeated verbatim
three times, then the substantive posting — a company/domain sentence early,
and further down a leadership-weighting sentence, a responsibilities list,
and a requirements list. Positioned so that in the RAW text the deep content
sits past the excerpt budget (behind the 3x-repeated chrome), but after
de-chroming collapses the chrome to one occurrence, the SAME budget reaches
all of it — that inversion is ``build_jd_excerpt``'s entire reason to exist
(see ``services.jd_excerpt`` module docstring for the real-fixture ground
truth this generalizes).
"""
from __future__ import annotations

from applire.services.jd_excerpt import build_jd_excerpt

# Two sign-in/cookie-consent style sentences, repeated verbatim 3x — the
# same scrape artifact the run-5 LinkedIn fixture exhibits (see
# ``services.jd_excerpt``'s module docstring), just for a synthetic
# "ExampleJobs" board instead of the real LinkedIn scrape.
_CHROME_BLOCK = (
    "Sign in Sign in with Email or New to ExampleJobs? "
    "By clicking Continue to join or sign in, you agree to ExampleJobs's "
    "User Agreement, Privacy Policy, and Cookie Policy. "
)
_COMPANY_SENTENCE = (
    "ExampleCorp is a Berlin-based logistics-tech company serving "
    "hundreds of customers across Europe."
)
_PADDING_SENTENCE = (
    "ExampleCorp was founded in 2014 and has grown steadily every year since. "
)
_LEADERSHIP_SENTENCE = (
    "This role is weighted approximately 55% technical leadership and "
    "45% hands-on engineering across the team."
)
_RESPONSIBILITIES_LIST = (
    "• Own the platform roadmap end to end. "
    "• Mentor and grow a team of backend engineers. "
    "• Partner with product on technical strategy."
)
_REQUIREMENTS_LIST = (
    "• 5+ years of backend engineering experience. "
    "• Proven experience leading engineering teams. "
    "• Strong communication skills across stakeholders."
)
_TRAILING_NOISE = (
    "Similar jobs You might also like Senior Backend Engineer at OtherCorp "
    "Platform Lead at ThirdCorp Site Reliability Engineer at FourthCorp"
)

_SYNTHETIC_JD = (
    (_CHROME_BLOCK * 3)
    + _COMPANY_SENTENCE + " "
    + _PADDING_SENTENCE + " "
    + _LEADERSHIP_SENTENCE + " "
    + _RESPONSIBILITIES_LIST + " "
    + _REQUIREMENTS_LIST + " "
    + _TRAILING_NOISE
)

# Small enough that the naive raw[:budget] slice lands entirely on chrome +
# the company sentence + padding — never reaching the leadership sentence,
# the responsibilities list, or the requirements list (all past this offset
# in the RAW, un-dechromed text) — yet large enough that once the 3x-
# repeated chrome collapses to one occurrence, all of that deep content
# fits comfortably inside the same budget.
_SYNTHETIC_BUDGET = 730


def test_synthetic_scrape_dedup_makes_deep_content_reachable_within_budget():
    """The whole point of ``build_jd_excerpt``: naive ``raw[:budget]`` never
    reaches the leadership-weighting sentence or the requirements list
    (they sit past the budget in the raw scrape, behind 3x-repeated chrome);
    after de-chroming, the SAME budget comfortably contains them. Both sides
    are asserted — a naive slice that already contained the deep content
    would mean this test has no teeth."""
    naive_slice = _SYNTHETIC_JD[:_SYNTHETIC_BUDGET]
    assert _LEADERSHIP_SENTENCE not in naive_slice
    assert "Own the platform roadmap end to end." not in naive_slice
    assert "5+ years of backend engineering experience." not in naive_slice
    assert "Strong communication skills across stakeholders." not in naive_slice

    excerpt = build_jd_excerpt(_SYNTHETIC_JD, budget=_SYNTHETIC_BUDGET)
    assert _LEADERSHIP_SENTENCE in excerpt
    assert "Own the platform roadmap end to end." in excerpt
    assert "5+ years of backend engineering experience." in excerpt
    assert "Strong communication skills across stakeholders." in excerpt


def test_synthetic_scrape_collapses_repeated_chrome_to_one_occurrence():
    """The 3x-repeated chrome block must collapse to a single occurrence of
    each of its constituent sentences — the segment-level exact-duplicate
    dedup this module performs (mirrors the real LinkedIn sign-in block's
    behaviour)."""
    excerpt = build_jd_excerpt(_SYNTHETIC_JD, budget=_SYNTHETIC_BUDGET)
    assert excerpt.count("Sign in Sign in with Email or New to ExampleJobs?") <= 1
    assert excerpt.count("By clicking Continue to join or sign in") <= 1


def test_synthetic_scrape_excerpt_still_contains_early_company_sentence():
    excerpt = build_jd_excerpt(_SYNTHETIC_JD, budget=_SYNTHETIC_BUDGET)
    assert _COMPANY_SENTENCE in excerpt


def test_synthetic_scrape_excerpt_is_bounded_by_budget():
    excerpt = build_jd_excerpt(_SYNTHETIC_JD, budget=_SYNTHETIC_BUDGET)
    assert len(excerpt) <= _SYNTHETIC_BUDGET


# ---------------------------------------------------------------------------
# None/empty/whitespace-only tolerance — already covered fixture-free in
# test_jd_excerpt.py's own unit-level section, but that ENTIRE module is
# gated by its module-wide skipif (see this file's docstring above), so
# these must be re-asserted here to actually run in CI.
# ---------------------------------------------------------------------------


def test_build_jd_excerpt_none_is_tolerated():
    assert build_jd_excerpt(None) == ""


def test_build_jd_excerpt_empty_string_is_tolerated():
    assert build_jd_excerpt("") == ""


def test_build_jd_excerpt_whitespace_only_is_tolerated():
    assert build_jd_excerpt("   \n\t  ") == ""
