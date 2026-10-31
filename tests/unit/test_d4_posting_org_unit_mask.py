# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-4 (Hazelnut build 5, founder replay): a letter sentence about the posting's
own team ("The Platform Reliability team's work … across CSD") names neither the
employer nor the title, so neither mask reached it and the team's name ("Platform
Reliability", a JD keyword) became a false "Is this true?" card. The posting's
organisation units — introduced by "our"/"unser…"/"join" or followed by an
acronym — are masked in the LETTER audit like the employer's name, with the
clause they open. Synthetic data, shaped like the founder letter."""

from __future__ import annotations

from types import SimpleNamespace

from applire.services.ats_audit import (
    _keyword_coverage,
    _norm,
    non_claim_names,
    non_claim_names_for_job,
    posting_org_units,
)

POSTING = (
    "We're looking for a Lead Engineer to join our Platform Reliability team and provide "
    "technical leadership for the Cloud Services Division's (CSD) automation efforts. "
    "Required: Platform Reliability, Python."
)


def _gap(concept: str) -> dict:
    return {"concept": concept, "surface_forms": [concept], "claimable": False,
            "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""}


def test_the_posting_names_its_own_units():
    assert posting_org_units(POSTING) == ("platform reliability team", "cloud services division", "csd")


def test_a_ward_in_a_title_or_a_duty_is_no_unit_of_the_employer():
    assert posting_org_units("Registered Nurse — Intensive Care Unit (m/f/d)") == ()
    assert posting_org_units("- Fachliche Führung eines kleinen Controlling-Teams (2 Personen)") == ()
    assert posting_org_units(None) == ()


def _job():
    return SimpleNamespace(role_title="Lead Engineer", company_name="Example Systems Inc.", raw_text=POSTING)


def test_the_letter_sentence_about_the_team_is_no_claim():
    letter = ("Dear Hiring Manager, I am applying for the Lead Engineer role at Example Systems. "
              "The Platform Reliability team’s work to bring automation into the tools used across "
              "CSD is a compelling focus for me. I built release pipelines for six years.")
    nc = non_claim_names_for_job(_job()).plus_employers("Example Systems Inc.").with_employer_clause()
    cov = _keyword_coverage(_norm(letter), ["Platform Reliability"], [_gap("Platform Reliability")], non_claim=nc)
    assert cov.present == ["Platform Reliability"] and cov.present_unsupported == []


def test_the_candidates_own_claim_of_the_keyword_is_still_flagged():
    letter = "I am applying for the Lead Engineer role. I led platform reliability for six years."
    nc = non_claim_names_for_job(_job()).with_employer_clause()
    cov = _keyword_coverage(_norm(letter), ["Platform Reliability"], [_gap("Platform Reliability")], non_claim=nc)
    assert cov.present_unsupported == ["Platform Reliability"]


def test_without_the_unit_mask_the_team_sentence_was_flagged():
    """The baseline the mask rescues: same letter, the posting's names only."""
    letter = ("The Platform Reliability team’s work to bring automation into the tools used across "
              "CSD is a compelling focus for me.")
    nc = non_claim_names("Lead Engineer", ["Example Systems Inc."]).with_employer_clause()
    cov = _keyword_coverage(_norm(letter), ["Platform Reliability"], [_gap("Platform Reliability")], non_claim=nc)
    assert cov.present_unsupported == ["Platform Reliability"]


def test_a_cv_never_reads_the_unit_mask():
    """The CV audit carries no employer clause: a bullet naming a team is the
    candidate's own (their former team), so the unit is not masked there."""
    nc = non_claim_names_for_job(_job())
    assert nc.org_units and not nc.employer_clause
    cov = _keyword_coverage(_norm("Led the Platform Reliability team."), ["Platform Reliability"],
                            [_gap("Platform Reliability")], non_claim=nc)
    assert cov.present_unsupported == ["Platform Reliability"]
