# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 cl. 2 (``KeywordMatch`` / ``keyword_matches``) and cl. 4 (the
matched-form grounding widening on ``present_unsupported``).

``keyword_present`` is, by construction, ``bool(keyword_matches(...))`` — the
recorded matches ARE the decision, never a second search (module docstring of
``services/ats_audit.py``). This file pins that equivalence over a table of
shapes, the F4 ownership rule the search set must preserve, the two new
``ATSKeywordCoverage`` match dicts, and the ADR-090 clause 4 widening built on
the founder's own "IT Data & AI Governance" case (#672-adjacent).
"""
from __future__ import annotations

import pytest

from applire.schemas.ats import ATSKeywordCoverage, KeywordMatch
from applire.services.ats_audit import (
    _grounded_through_matched_forms,
    _keyword_coverage,
    _keyword_search_forms,
    _norm,
    grounding_vault_index,
    keyword_matches,
    keyword_present,
)

# ---------------------------------------------------------------------------
# keyword_present == bool(keyword_matches) over a table of cases
# ---------------------------------------------------------------------------

_LEDGER_ORCH = [
    {"concept": "container orchestration",
     "surface_forms": ["container orchestration", "Kubernetes", "K8s"],
     "claimable": True, "status": "direct", "sources": ["required"],
     "fit_weight": 1.0, "evidence": "led Kubernetes migration"},
]

_LEDGER_GXP = [
    {"concept": "Good Manufacturing Practice", "surface_forms": ["GxP", "GMP"],
     "claimable": True, "status": "direct", "sources": ["keyword"],
     "fit_weight": 0.0, "evidence": "implemented GMP standards"},
]

_CASES = [
    # (case name, keyword, text, ledger, expected present, expected match forms+stem)
    pytest.param(
        "literal", "Python", "Anna Bauer writes Python daily", None,
        True, [("Python", False)], id="literal",
    ),
    pytest.param(
        "ledger surface form", "container orchestration",
        "Anna Bauer led the Kubernetes migration", _LEDGER_ORCH,
        True, [("Kubernetes", False)], id="ledger-surface-form",
    ),
    pytest.param(
        "concept fallback", "GxP",
        "Anna Bauer implemented Good Manufacturing Practice standards across three plants",
        _LEDGER_GXP,
        True, [("Good Manufacturing Practice", False)], id="concept-fallback",
    ),
    pytest.param(
        "plural fold", "Reviews",
        "Anna Bauer ran a weekly review session", None,
        True, [("Reviews", False)], id="plural-fold",
    ),
    pytest.param(
        "stem-only verb form", "Mentoring",
        "Anna Bauer mentored three junior engineers", None,
        True, [("Mentoring", True)], id="stem-only-verb-form",
    ),
    pytest.param(
        "no match", "Blockchain",
        "Anna Bauer writes Python daily", None,
        False, [], id="no-match",
    ),
]


@pytest.mark.parametrize("name,keyword,text,ledger,expected_present,expected_forms", _CASES)
def test_keyword_present_equals_bool_keyword_matches(
    name, keyword, text, ledger, expected_present, expected_forms
):
    text_norm = _norm(text)
    matches = keyword_matches(keyword, text_norm, ledger)
    assert keyword_present(keyword, text_norm, ledger) is expected_present
    # keyword_present is bool(keyword_matches) BY CONSTRUCTION — not just the
    # same verdict as this table's own expectation, but the same verdict as
    # whatever keyword_matches just computed.
    assert keyword_present(keyword, text_norm, ledger) is bool(matches)
    assert matches == [KeywordMatch(form=f, stem=s) for f, s in expected_forms]


def test_no_match_is_empty_list_not_none():
    matches = keyword_matches("Blockchain", _norm("nothing relevant here"), None)
    assert matches == []
    assert isinstance(matches, list)


# ---------------------------------------------------------------------------
# F4 ownership preserved in the search set: a gap owner beats a claimable
# owner that ALSO happens to carry the same keyword as one of its forms.
# ---------------------------------------------------------------------------

_LEDGER_F4 = [
    {"concept": "Cloud environment qualification (AWS, Azure)", "status": "partial",
     "surface_forms": ["Cloud environment qualification", "Azure"], "claimable": True,
     "sources": ["required"], "fit_weight": 1.0, "evidence": "Qualified GxP cloud environment."},
    {"concept": "Azure", "status": "gap", "surface_forms": ["Azure"], "claimable": False,
     "sources": ["keyword"], "fit_weight": 0.0, "evidence": ""},
]


def test_f4_gap_owner_beats_claimable_owner_in_search_set():
    """The gap-owning row's own forms are the ONLY widening — the claimable
    sibling's 'Cloud environment qualification' must never appear."""
    forms = _keyword_search_forms("Azure", _LEDGER_F4)
    assert forms == ["Azure"]
    assert "Cloud environment qualification" not in forms


def test_f4_gap_owner_matched_forms_exclude_foreign_claimable_wording():
    """keyword_matches must not count the keyword present via the claimable
    sibling's own wording — only the gap owner's forms decide."""
    text_norm = _norm("Anna Bauer performed cloud environment qualification work")
    matches = keyword_matches("Azure", text_norm, _LEDGER_F4)
    assert matches == []
    assert keyword_present("Azure", text_norm, _LEDGER_F4) is False


# ---------------------------------------------------------------------------
# ATSKeywordCoverage.present_unsupported_matches / present_denied_matches
# ---------------------------------------------------------------------------

def test_default_coverage_has_none_match_dicts():
    """A report predates the field → both dicts are None, never {}."""
    cov = ATSKeywordCoverage()
    assert cov.present_unsupported_matches is None
    assert cov.present_denied_matches is None


_LEDGER_UNSUPPORTED_AND_DENIED = [
    {"concept": "SaaS", "surface_forms": ["SaaS", "software as a service"],
     "claimable": False, "status": "gap", "sources": ["keyword"],
     "fit_weight": 0.0, "evidence": ""},
    {"concept": "AWS", "surface_forms": ["AWS", "Amazon Web Services"],
     "claimable": False, "status": "denied", "sources": ["keyword"],
     "fit_weight": 0.0, "evidence": ""},
]


def test_keyword_coverage_populates_matches_only_for_their_own_bucket():
    text_norm = _norm(
        "We sell software as a service. We run everything on Amazon Web Services."
    )
    cov = _keyword_coverage(
        text_norm, ["SaaS", "AWS"], _LEDGER_UNSUPPORTED_AND_DENIED
    )
    assert cov.present == ["SaaS", "AWS"]
    assert cov.present_unsupported == ["SaaS"]
    assert cov.present_denied == ["AWS"]

    saas_matches = keyword_matches("SaaS", text_norm, _LEDGER_UNSUPPORTED_AND_DENIED)
    aws_matches = keyword_matches("AWS", text_norm, _LEDGER_UNSUPPORTED_AND_DENIED)
    assert cov.present_unsupported_matches == {"SaaS": saas_matches}
    assert cov.present_denied_matches == {"AWS": aws_matches}
    # Each dict is keyed ONLY by the keywords in its own list.
    assert "AWS" not in cov.present_unsupported_matches
    assert "SaaS" not in cov.present_denied_matches


# ---------------------------------------------------------------------------
# ADR-090 clause 4 — the founder's "IT Data & AI Governance" case.
# ---------------------------------------------------------------------------

_JD_KEYWORD = "IT Data & AI Governance"
# The keyword's own literal form is a surface form too (mirrors a real
# `build_keyword_ledger` row, whose `surface_forms` always carries the JD term
# itself) — otherwise `_norm(keyword)` never lands in
# `unsupported_claim_surface_forms`'s flattened set at all, and the whole
# quadrant is inapplicable regardless of grounding. Empirically verified against
# the real `_keyword_coverage` before writing this fixture.
_LEDGER_AI_GOV = [
    {"concept": "IT Data & AI Governance",
     "surface_forms": ["IT Data & AI Governance", "AI governance"],
     "claimable": False, "status": "gap", "sources": ["required"],
     "fit_weight": 1.0, "evidence": ""},
]
_DOC_TEXT = "Anna Bauer led AI governance reviews and set the AI governance roadmap."


def _profile(skill_name: str | None, status: str = "confirmed") -> dict:
    profile = {"personal_info": {"name": "Anna Bauer"}, "skills": []}
    if skill_name is not None:
        profile["skills"] = [{"name": skill_name, "status": status}]
    return profile


def test_widening_a_vault_without_the_wording_keeps_the_finding():
    """Mutation guard: an unrelated vault must not accidentally ground the
    keyword — the finding stays open."""
    index = grounding_vault_index(_profile("Data Analysis"))
    assert index is not None
    cov = _keyword_coverage(_norm(_DOC_TEXT), [_JD_KEYWORD], _LEDGER_AI_GOV, vault_index=index)
    assert cov.present_unsupported == [_JD_KEYWORD]


def test_widening_b_vault_with_the_wording_drops_only_with_index():
    index = grounding_vault_index(_profile("AI Governance"))
    assert index is not None

    with_index = _keyword_coverage(
        _norm(_DOC_TEXT), [_JD_KEYWORD], _LEDGER_AI_GOV, vault_index=index
    )
    assert with_index.present_unsupported == []

    without_index = _keyword_coverage(
        _norm(_DOC_TEXT), [_JD_KEYWORD], _LEDGER_AI_GOV, vault_index=None
    )
    assert without_index.present_unsupported == [_JD_KEYWORD]


_LEDGER_AI_GOV_TWO_FORMS = [
    {"concept": "IT Data & AI Governance",
     "surface_forms": ["IT Data & AI Governance", "AI governance", "Data Governance"],
     "claimable": False, "status": "gap", "sources": ["required"],
     "fit_weight": 1.0, "evidence": ""},
]
_DOC_TEXT_TWO_FORMS = "Anna Bauer led AI governance and Data Governance initiatives."


def test_widening_c_one_of_two_matched_forms_ungrounded_keeps_the_finding():
    """Every matched form must ground — the vault backs only 'AI governance',
    not 'Data Governance', so the keyword must stay open."""
    index = grounding_vault_index(_profile("AI Governance"))
    assert index is not None
    text_norm = _norm(_DOC_TEXT_TWO_FORMS)
    matches = keyword_matches(_JD_KEYWORD, text_norm, _LEDGER_AI_GOV_TWO_FORMS)
    assert {m.form for m in matches} == {"AI governance", "Data Governance"}
    assert _grounded_through_matched_forms(matches, index) is False

    cov = _keyword_coverage(
        text_norm, [_JD_KEYWORD], _LEDGER_AI_GOV_TWO_FORMS, vault_index=index
    )
    assert cov.present_unsupported == [_JD_KEYWORD]


@pytest.mark.parametrize("status", ["unconfirmed", "denied"])
def test_widening_d_unconfirmed_or_denied_vault_skill_does_not_ground(status):
    """exclude_unconfirmed strips the entry before the index is even built —
    an unconfirmed/denied skill must not ground the matched-form claim."""
    index = grounding_vault_index(_profile("AI Governance", status=status))
    assert index is not None
    cov = _keyword_coverage(_norm(_DOC_TEXT), [_JD_KEYWORD], _LEDGER_AI_GOV, vault_index=index)
    assert cov.present_unsupported == [_JD_KEYWORD]


def test_no_profile_grounding_vault_index_is_none():
    assert grounding_vault_index(None) is None
    assert grounding_vault_index({}) is None
