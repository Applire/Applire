# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#271 Task 1 — de-chromed, content-bearing JD excerpt.

Pinned against the run-5 charter fixture (``.run5fixture/jd.txt``, git-
excluded — read at runtime, never copied into this file): the writer/reviewer
JD window used to be ``raw_text[:2000]``. That window landed entirely on
LinkedIn sign-in boilerplate repeated verbatim three times and NEVER reached
the leadership-weighting sentence, the people-management bullet, or the "What
We're Looking For" requirements list — all of which sit past character 2000
in the raw scrape. See ``services.jd_excerpt`` module docstring for the full
ground truth and the budget arithmetic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from applire.services.jd_excerpt import JD_EXCERPT_BUDGET, build_jd_excerpt

_FIXTURE = Path(__file__).parents[4] / ".run5fixture" / "jd.txt"

# Scoped PER FUNCTION, never module-level: half of this file's tests need no
# fixture at all, and a module-scoped `pytestmark` silently skipped those in CI
# too — which is how build_jd_excerpt ended up with zero CI coverage. Mirrors
# the per-function pattern in test_vault_evidence.py.
run5_fixture = pytest.mark.skipif(
    not _FIXTURE.exists(), reason="run-5 charter fixture not present in this checkout"
)


def _load_run5_jd() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


@run5_fixture
def test_run5_excerpt_contains_leadership_weighting_line():
    """The 60/40 leadership-weighting sentence — the exact fact #271 says
    the writer never saw under the old raw_text[:2000] slice — must now be
    inside the excerpt."""
    excerpt = build_jd_excerpt(_load_run5_jd())
    assert "60% technical leadership and 40% hands-on engineering" in excerpt


@run5_fixture
def test_run5_excerpt_contains_management_mentoring_bullet():
    excerpt = build_jd_excerpt(_load_run5_jd())
    assert "Managing, mentoring and developing a growing AI engineering team" in excerpt


@run5_fixture
def test_run5_excerpt_contains_full_requirements_list():
    """Every "What We're Looking For" bullet must survive — a partial
    requirements list would let the writer/reviewer disagree about which
    requirements exist at all."""
    excerpt = build_jd_excerpt(_load_run5_jd())
    for requirement in (
        "Strong experience building and deploying AI-powered products in production",
        "Proven leadership of AI, machine learning or applied AI engineers",
        "Hands-on experience with agentic systems and tool-using LLM applications",
        "Production experience with RAG, embeddings, ranking and retrieval pipelines",
        "Strong knowledge of AI evaluation, monitoring and observability",
        "Experience defining engineering standards and technical best practices",
        "The ability to move between strategic leadership and hands-on delivery",
        "Strong software engineering fundamentals and production ownership",
    ):
        assert requirement in excerpt, f"missing requirement: {requirement!r}"


@run5_fixture
def test_run5_excerpt_still_contains_company_domain_engagement_facts():
    """#255's company & domain engagement depends on these two facts staying
    inside the window — de-chroming must never cost them."""
    excerpt = build_jd_excerpt(_load_run5_jd())
    assert "LegalTech" in excerpt
    assert "hundreds of customers" in excerpt


@run5_fixture
def test_run5_excerpt_drops_repeated_signin_boilerplate():
    """The LinkedIn sign-in block repeats verbatim 3x in the raw scrape.
    Its two constituent sentences that are IDENTICAL across all three
    repeats ("Sign in Sign in with Email or New to LinkedIn?" and "By
    clicking Continue to join or sign in, you agree to LinkedIn's User
    Agreement, Privacy Policy, and Cookie Policy.") must collapse to one
    occurrence each — the segment-level exact-duplicate dedup this module
    performs. (The "...Forgot password?" line is prefixed differently each
    time — "Tailor my resume Sign in to access..." vs "Sign in to evaluate
    your skills..." vs "Sign in to tailor your resume..." — so it is not an
    exact segment duplicate; this module targets whole-segment repeats, not
    sub-segment near-duplicates.)"""
    excerpt = build_jd_excerpt(_load_run5_jd())
    assert excerpt.count("By clicking Continue to join or sign in") <= 1
    assert excerpt.count("Sign in Sign in with Email or New to LinkedIn?") <= 1


@run5_fixture
def test_run5_excerpt_is_bounded_by_budget():
    excerpt = build_jd_excerpt(_load_run5_jd())
    assert len(excerpt) <= JD_EXCERPT_BUDGET


# ---------------------------------------------------------------------------
# Unit-level behaviour (no fixture dependency)
# ---------------------------------------------------------------------------


def test_build_jd_excerpt_none_and_empty_tolerant():
    assert build_jd_excerpt(None) == ""
    assert build_jd_excerpt("") == ""


def test_build_jd_excerpt_short_text_passes_through_unchanged():
    assert build_jd_excerpt("We are hiring a QA Manager.") == "We are hiring a QA Manager."


def test_build_jd_excerpt_collapses_whitespace():
    excerpt = build_jd_excerpt("Line one.\n\n\nLine   two.")
    assert "\n" not in excerpt
    assert "Line one. Line two." == excerpt


def test_build_jd_excerpt_drops_exact_duplicate_sentence():
    text = "We build great products. Sign in now. We build great products. Apply today."
    excerpt = build_jd_excerpt(text)
    assert excerpt.count("We build great products.") == 1
    assert "Apply today." in excerpt


def test_build_jd_excerpt_collapses_repeated_header_phrase():
    """The common LinkedIn-scrape artifact: '<Title> Apply <Title> <n> ago'."""
    text = "Senior Backend Engineer Acme Corp Apply Senior Backend Engineer Acme Corp 2 days ago"
    excerpt = build_jd_excerpt(text)
    assert excerpt.count("Senior Backend Engineer Acme Corp") == 1


def test_build_jd_excerpt_truncates_to_budget():
    text = "word " * 2000  # far exceeds any reasonable budget
    excerpt = build_jd_excerpt(text, budget=100)
    assert len(excerpt) <= 100
