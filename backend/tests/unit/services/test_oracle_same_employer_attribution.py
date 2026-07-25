# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 round-3 (live MCP probe residual, 2026-07-24) — same-company
multi-role FALSE misattribution.

Live-reproduced: "Additionally, I mentored engineers transitioning into
software roles and introduced CI/CD and automated testing across multiple
services" verdicted ``misattributed``, detail "Backed only by evidence from a
different position (work_experience[1].responsibilities[1])". The letter
paragraph anchors "At Alpha Systems GmbH" (paragraph-continuation inherits
it), the current-role tie-break resolved the anchor to the Principal role
(work_experience[0]), but the mentoring evidence is owned by the Team Lead
role (work_experience[1]) — the SAME employer, a different internal tenure.
A company-level anchor must own the UNION of that company's roles: sibling
roles of the same employer are never "foreign" for attribution purposes.

Cross-EMPLOYER blends must still flag (the BioNTech/Applire shape,
``test_oracle_letter_audit.py`` / ``test_oracle_misattribution.py``) — this
module pins the SAME test shape once more, explicitly under the new
same-employer union rule, to prove it did not over-relax.
"""
from __future__ import annotations

import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle import audit_document, verify_claim

PROFILE = {
    "personal_info": {"name": "Max Prober"},
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
    ],
}

LETTER = {
    "body": {
        "paragraphs": [
            "At Alpha Systems GmbH, I built an LLM-assisted document "
            "classification service in Python (FastAPI, PostgreSQL, "
            "Docker) and owned end-to-end production operations for the "
            "platform. Additionally, I mentored engineers transitioning "
            "into software roles and introduced CI/CD and automated "
            "testing across multiple services.",
        ]
    }
}


@pytest.mark.asyncio
async def test_same_employer_sibling_role_evidence_is_not_misattributed():
    """The exact probe shape: anchored to the CURRENT role (tie-break), but
    the evidence lives on the PAST role at the SAME company."""
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    mentoring = [r for r in report.claims if "mentored engineers" in r.claim.text]
    assert mentoring, "expected a decomposed claim about mentoring"
    for r in mentoring:
        assert r.verdict.verdict != "misattributed", (r.claim.text, r.verdict)


@pytest.mark.asyncio
async def test_same_employer_sibling_role_claim_grounds_directly():
    """Narrow regression pin, independent of the full-document fixture."""
    verdict = await verify_claim(
        Claim(
            text=(
                "I mentored engineers transitioning into software roles "
                "and introduced CI/CD and automated testing across "
                "multiple services."
            ),
            location="body.paragraphs[0][1]",
            kind="sentence",
            source_experience_id="w-principal",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded", verdict
    assert verdict.checker in ("grounding", "numbers")


@pytest.mark.asyncio
async def test_cross_employer_blend_still_misattributes_under_union_rule():
    """Over-relax guard: a DIFFERENT employer's evidence must still flag —
    the same-employer union must never widen to cover a genuinely foreign
    company just because the claim's own company has multiple roles."""
    profile = {
        **PROFILE,
        "work_experience": [
            *PROFILE["work_experience"],
            {
                "id": "w-other-co",
                "company": "Beta Insurance AG",
                "role": "Backend Engineer",
                "is_current": False,
                "responsibilities": [
                    "Migrated the claims ledger to a new event-sourced "
                    "architecture.",
                ],
            },
        ],
    }
    verdict = await verify_claim(
        Claim(
            text="I migrated the claims ledger to a new event-sourced architecture.",
            location="body.paragraphs[1][0]",
            kind="sentence",
            source_experience_id="w-principal",
        ),
        profile,
    )
    assert verdict.verdict == "misattributed", verdict
    assert verdict.checker == "attribution"
