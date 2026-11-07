# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial pass, Hazelnut build 7 — #803 reopened: the second-person clause
("Your …" / "Ihre …", up to the first first-person word / comma) is read as
ABOUT THE EMPLOYER even when the candidate's own experience claim sits inside it.
A term moved to `present_unsupported_employer_sentence` loses its add-to-profile
exit and its headline count. Synthetic data.
"""
from __future__ import annotations

from applire.services.ats_audit import _keyword_coverage, _norm, _norm_cased, non_claim_names

_NOVAPAY = non_claim_names(
    "Senior Backend Engineer — Payments Platform (m/f/d)", ["NovaPay GmbH"]
).with_employer_clause()
_RHEINWERK = non_claim_names("Leiter Operations (m/w/d)", ["Rheinwerk Verpackungen GmbH"]).with_employer_clause()


def _gap(concept: str) -> dict:
    return {
        "concept": concept, "surface_forms": [concept], "claimable": False,
        "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": "",
    }


def _cov(text: str, term: str, names):
    return _keyword_coverage(_norm(text), [term], [_gap(term)], non_claim=names, text_cased=_norm_cased(text))


def test_candidate_experience_inside_ihrer_clause_stays_a_candidate_claim():
    # "… with ten years of Verbundverpackungen experience" is the CANDIDATE's claim.
    t = "Gern unterstütze ich Sie beim Aufbau Ihrer Plattform mit Verbundverpackungen-Erfahrung aus zehn Jahren."
    cov = _cov(t, "Verbundverpackungen", _RHEINWERK)
    assert cov.present_unsupported == ["Verbundverpackungen"]
    assert cov.present_unsupported_employer_sentence == [], "own 10-year claim demoted to employer row"


def test_candidate_claim_after_a_dash_inside_your_clause_stays_a_candidate_claim():
    # dashes are folded to spaces by _norm, so the clause runs to "I"
    t = "Your platform needs Kafka depth — six years of running Kafka clusters is what I bring."
    cov = _cov(t, "Kafka", _NOVAPAY)
    assert cov.present_unsupported == ["Kafka"]
    assert cov.present_unsupported_employer_sentence == [], "own 6-year claim demoted to employer row"
