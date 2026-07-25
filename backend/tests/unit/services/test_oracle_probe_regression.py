# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 round-3 regression — a live real-LLM MCP probe on a FRESH profile
shape (a different candidate/company/role shape than the run-4 fixture)
exposed four gaps the run-4 fix left standing.

Ground truth (2026-07-24, live MCP probe against a fresh profile+letter,
dumped from the probe DB): {unverifiable: 7, grounded: 1, misattributed: 1},
dominated=True — and the ONE misattribution was FALSE.

Four gaps, confirmed against the live data (fixture below sanitized: real
name/contact/address stripped, company/role/achievement shapes kept
near-verbatim per the coordinator's explicit allowance):

1. EMPLOYER FACTS ARE NOT CANDIDATE CLAIMS — "ClaimFlow is a fast-growing
   InsurTech company." / "Its AI platform automates insurance claims
   processing..." are JD-sourced facts about the TARGET COMPANY (the
   ADR-021 reviewer validates these against JD text elsewhere); the
   deterministic vault audit can never ground them and must not mislabel
   them as a failed candidate claim. Fixed: ``extract.py``'s employer-fact
   classification -> ``Claim.is_employer_fact`` -> ``not_applicable``
   verdict (schemas/oracle.py), excluded from the domination denominator.

2. SAME-COMPANY MULTI-ROLE FALSE MISATTRIBUTION — a sentence anchored (via
   the current-role tie-break) to the Principal role, whose mentoring
   evidence actually lives on a PAST role at the SAME employer, verdicted
   misattributed. Fixed: ``VaultIndex.same_employer_ids`` +
   ``find_foreign_owner``/``ground_via_role_union`` treat same-employer
   sibling roles as never-foreign (see also
   ``test_oracle_same_employer_attribution.py``).

3. NEAR-VERBATIM PARAPHRASE STILL UNVERIFIABLE — an em-dash-delimited
   parenthetical aside's OWN internal Oxford-comma enumeration was
   mid-fragmented by the general clause-boundary rule before the aside
   itself was ever isolated. Fixed: ``extract.split_clauses``'s
   paired-em-dash rule (exactly two em-dashes -> isolate the aside as one
   clause, never re-split it further).

4. COURTESY CLOSER WITH CONTENT — "I would welcome the opportunity to
   discuss how my background in X, Y, and Z aligns with your needs."
   carries a real, checkable competence list buried in a courtesy
   preamble. Fixed: ``extract._strip_formula_prefix`` trims the recognized
   courtesy PREFIX from the stored claim text (never drops the clause
   outright — that stays ``_is_pure_formula_clause``'s call).

This fixture pins the combined effect: the report must not be
unverifiable-dominated, the one genuine misattribution risk must not
recur, and the two headline paraphrase/enumeration fixes must ground.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

# Sanitized: real name/email/phone/address removed. Company, role, and
# responsibility text kept near-verbatim (coordinator-approved) — this is
# what actually exposed the bugs; a paraphrased fixture would risk
# re-hiding them.
PROFILE = {
    "personal_info": {"name": "Test Candidate"},
    "work_experience": [
        {
            "id": "w-principal",
            "company": "Alpha Systems GmbH",
            "role": "Principal Platform Engineer",
            "is_current": True,
            "start_date": "2023",
            "end_date": None,
            "responsibilities": [
                "Lead a team of five platform engineers running the core "
                "data platform.",
                "Built an internal LLM-assisted document classification "
                "service in Python (FastAPI, PostgreSQL, Docker), "
                "targeting a 60% reduction in manual processing time.",
                "Own production operations end-to-end for the platform "
                "(on-call, SLOs).",
            ],
        },
        {
            "id": "w-teamlead",
            "company": "Alpha Systems GmbH",
            "role": "Team Lead Data Services",
            "is_current": False,
            "start_date": "2020",
            "end_date": "2023",
            "responsibilities": [
                "Mentored engineers moving from operations roles into "
                "software roles; built the onboarding curriculum.",
                "Introduced CI/CD and automated testing across four "
                "services.",
            ],
        },
        {
            "id": "w-swe",
            "company": "Alpha Systems GmbH",
            "role": "Software Engineer",
            "is_current": False,
            "start_date": "2018",
            "end_date": "2020",
            "responsibilities": [
                "Backend development in Python for regulated "
                "pharma-adjacent customers (GxP documentation workflows, "
                "audit trails, validation reports).",
            ],
        },
    ],
    "skills": [
        {"name": "Python"},
        {"name": "FastAPI"},
        {"name": "PostgreSQL"},
        {"name": "Docker"},
        {"name": "CI/CD"},
        {"name": "LLM integration"},
        {"name": "mentoring"},
    ],
}

# The letter's actual paragraph/sentence shapes (sentence boundaries, the
# paired-em-dash aside, and the Oxford-comma competence list all matter for
# reproducing the four mechanisms above).
LETTER = {
    "body": {
        "paragraphs": [
            "I am writing to express my interest in the Senior AI "
            "Engineer (m/f/d) position at ClaimFlow GmbH. ClaimFlow is a "
            "fast-growing InsurTech company. Its AI platform automates "
            "insurance claims processing for European insurers.",
            "With years of Python expertise, I have led teams and "
            "delivered solutions in compliance-critical environments. At "
            "Alpha Systems GmbH, I built an LLM-assisted document "
            "classification service in Python (FastAPI, PostgreSQL, "
            "Docker) and owned end-to-end production operations for the "
            "platform. Additionally, I mentored engineers transitioning "
            "into software roles and introduced CI/CD and automated "
            "testing across multiple services.",
            "While I have not worked in InsurTech or insurance claims "
            "processing, my regulated-industry background—building "
            "GxP documentation workflows, audit trails, and validation "
            "reports for pharma-adjacent customers—gives me "
            "hands-on experience with compliance-critical, "
            "precision-first document processing that transfers "
            "directly to your domain.",
            "I would welcome the opportunity to discuss how my "
            "background in backend engineering, production LLM "
            "applications, and mentoring aligns with your needs. Thank "
            "you for your time and consideration.",
        ]
    },
    "recipient": {"company": "ClaimFlow GmbH"},
}


@pytest.mark.asyncio
async def test_probe_letter_report_is_not_unverifiable_dominated():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    assert report.unverifiable_dominated is False, report.counts


@pytest.mark.asyncio
async def test_probe_employer_facts_excluded_from_domination_denominator():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    employer_facts = [r for r in report.claims if r.claim.is_employer_fact]
    assert len(employer_facts) == 2
    assert all(r.verdict.verdict == "not_applicable" for r in employer_facts)


@pytest.mark.asyncio
async def test_probe_no_false_misattribution_on_same_employer_mentoring_claim():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    mentoring = [r for r in report.claims if "mentored engineers" in r.claim.text]
    assert mentoring
    for r in mentoring:
        assert r.verdict.verdict != "misattributed", (r.claim.text, r.verdict)


@pytest.mark.asyncio
async def test_probe_no_claim_in_the_whole_report_is_misattributed():
    """The live bug's exact symptom: ZERO false misattributions anywhere in
    the report (not just the mentoring claim) — this is a fresh profile
    where every claim genuinely traces to one employer's own history."""
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    assert report.counts["misattributed"] == 0, [
        (r.claim.text, r.verdict) for r in report.claims if r.verdict.verdict == "misattributed"
    ]


@pytest.mark.asyncio
async def test_probe_gxp_transfer_enumeration_grounds():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    hits = [r for r in report.claims if "GxP documentation workflows" in r.claim.text]
    assert hits
    assert all(r.verdict.verdict == "grounded" for r in hits), [
        (r.claim.text, r.verdict.verdict) for r in hits
    ]


@pytest.mark.asyncio
async def test_probe_courtesy_closer_competence_list_grounds():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    hits = [r for r in report.claims if "backend engineering" in r.claim.text]
    assert hits
    assert all(r.verdict.verdict == "grounded" for r in hits), [
        (r.claim.text, r.verdict.verdict) for r in hits
    ]
