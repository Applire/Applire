# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-4b (Hazelnut build 5, delivery run 4): both letters' own denial sentences
got a keyword card about the term they deny — "I have not built CI/CD pipelines
or other cloud or DevOps capabilities." („DevOps", remove-only) and "I have no
experience with AI programming assistants …" („AI copilots", "Stimmt das?";
the recorded denial is German, so no cover). A term whose every matched form
stands in a negation's scope is the candidate's limit, not a claim (class S).
Synthetic data only."""

from __future__ import annotations

from applire.services.ats_audit import _keyword_coverage, _norm, grounding_vault_index, non_claim_names
from applire.services.oracle.extract import form_in_denial_segment


def _vault(denials) -> dict:
    return {
        "personal_info": {"name": "Jana Lehmann"},
        "work_experience": [{"id": "w1", "company": "Nordpharm SE", "role": "Associate Director",
                             "start_date": "2024-12", "responsibilities": ["Lead an agentic GenAI system."]}],
        "skills": [],
        "metadata": {"denied_concepts": [
            {"concept": c, "date": "2026-10-29", "source": "interview", "statement": "Nein.", "denial_level": "direct"}
            for c in denials
        ]},
    }


_LEDGER = [
    {"concept": c, "status": "gap", "claimable": False, "surface_forms": forms, "sources": ["keyword"],
     "fit_weight": 1.0, "evidence": ""}
    for c, forms in (("DevOps", ["DevOps"]), ("AI copilots", ["AI copilots", "AI programming assistants"]))
]
_NC = non_claim_names("Lead, AI Engineering", ["Agilent Technologies"]).with_employer_clause()


def _cov(letter: str, denials=("Cloud- oder DevOps-Erfahrung", "KI-Programmierassistenten")):
    return _keyword_coverage(_norm(letter), ["DevOps", "AI copilots"], _LEDGER, None,
                             grounding_vault_index(_vault(list(denials))), _NC, document_language=None)


def test_the_own_denial_sentence_gets_no_card():
    cov = _cov("At Nordpharm SE, I lead an agentic GenAI system. "
               "I have not built CI/CD pipelines or other cloud or DevOps capabilities. "
               "I have no experience with AI programming assistants that I can substantiate; "
               "my related work is this agentic system.")
    assert cov.present_unsupported == []
    assert sorted(cov.present_unsupported_in_denial) == ["AI copilots", "DevOps"]


def test_negative_control_a_real_claim_keeps_its_card():
    cov = _cov("At Nordpharm SE, I led the DevOps team for five years.")
    assert cov.present_unsupported == ["DevOps"]
    assert cov.present_unsupported_in_denial == []


def test_a_claim_after_the_denial_in_the_same_sentence_keeps_its_card():
    cov = _cov("I have not built CI/CD pipelines, but I led DevOps at Nordpharm SE.")
    assert cov.present_unsupported == ["DevOps"]


def test_without_any_recorded_limit_nothing_moves():
    cov = _cov("I have not built CI/CD pipelines or other cloud or DevOps capabilities.", denials=())
    assert cov.present_unsupported == ["DevOps"]


def test_the_scope_fact():
    assert form_in_denial_segment("i have not built ci/cd pipelines or other devops capabilities.", "devops")
    assert form_in_denial_segment("mit devops habe ich keine erfahrung.", "devops")
    assert not form_in_denial_segment("not only devops but cloud.", "devops")
    assert not form_in_denial_segment("i led devops.", "devops")
