# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 (founder-acceptance F14) — the letter path must discriminate.

Before this fix, a cover letter scored {grounded: 1, unverifiable: 8} on the
same profile the CV path scored 39/42 grounded on — near-zero discriminating
power — and the one real writer error (a NordPharm achievement blended with
unrelated interview evidence) filed as merely "unverifiable" instead of
"misattributed". This is the realistic multi-paragraph reproduction: a
truthful letter should ground substantially at clause granularity, and the
one blended sentence must flag loud.
"""
from __future__ import annotations

import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle import audit_document, verify_claim

PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {
        "en": "Automation engineer with a track record across regulated industries."
    },
    "work_experience": [
        {
            "id": "w-acme",
            "company": "Acme GmbH",
            "role": "Support Engineer",
            "start_date": "2018-01",
            "end_date": "2021-12",
            "achievements": [
                "Mentored junior engineers.",
                "Improved onboarding documentation.",
            ],
        },
        {
            "id": "w-nordpharm",
            "company": "NordPharm",
            "role": "Automation Lead",
            "start_date": "2022-01",
            "end_date": None,
            "achievements": [
                "Led AI automation projects that reduced manual QA effort by 40%.",
            ],
        },
    ],
    # #237 (F14): a signature story surfaced during the candidate's Applire
    # interview, anchored to the OLD role (w-acme) — the exact shape of the
    # bug: real practice-area evidence that belongs to a different position
    # than the one the letter blends it into.
    "signature_stories": [
        {
            "title": "Building a testing culture",
            "challenge": "Flaky releases were undermining stakeholder trust.",
            "mechanism": "Introduced automated regression suites and dashboards.",
            "outcome": (
                "Established comprehensive testing, observability, and "
                "reliability practices."
            ),
            "experience_refs": ["w-acme"],
        }
    ],
}

LETTER = {
    "header": {"name": "Anna Bauer"},
    "body": {
        "paragraphs": [
            "I am excited to apply for the Senior Automation Engineer role "
            "at your company.",
            # The F14 blend: a real NordPharm achievement, bolted onto
            # unrelated (foreign-owned) practice-area evidence.
            "At NordPharm, I led AI automation projects that reduced manual "
            "QA effort by 40%, with comprehensive testing, observability, "
            "and reliability practices.",
            # A truthful, clause-checkable paragraph about the OTHER role.
            "At Acme GmbH; I mentored junior engineers, and I improved "
            "onboarding documentation.",
            "I look forward to hearing from you.",
        ]
    },
    "signature": {"closing": "Mit freundlichen Grüßen"},
}


@pytest.mark.asyncio
async def test_letter_audit_discriminates_and_flags_the_blend():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    by_loc = {r.claim.location: r for r in report.claims}

    # (1) the NordPharm-anchored clause whose evidence lives in a foreign-owned
    # story verdicts misattributed, not unverifiable.
    blend_results = [
        r
        for loc, r in by_loc.items()
        if loc.startswith("body.paragraphs[1]")
        and "observability" in r.claim.text.lower()
    ]
    assert blend_results, "expected a decomposed clause naming observability"
    assert all(r.verdict.verdict == "misattributed" for r in blend_results)
    assert all(r.verdict.checker == "attribution" for r in blend_results)

    # (2) truthful clauses that match vault evidence at clause granularity
    # verdict grounded — the letter path discriminates instead of scoring
    # 1/9 grounded on an honest letter.
    acme_results = [
        r for loc, r in by_loc.items() if loc.startswith("body.paragraphs[2]")
    ]
    assert acme_results
    assert all(r.verdict.verdict == "grounded" for r in acme_results), [
        (r.claim.text, r.verdict.verdict) for r in acme_results
    ]

    nordpharm_figure_results = [
        r
        for loc, r in by_loc.items()
        if loc.startswith("body.paragraphs[1]") and "40%" in r.claim.text
    ]
    assert nordpharm_figure_results
    assert all(r.verdict.verdict == "grounded" for r in nordpharm_figure_results)

    # (3) formulaic sentences stay unverifiable but do not dominate the report.
    formulaic = [
        r
        for loc, r in by_loc.items()
        if "look forward to hearing" in r.claim.text.lower()
    ]
    assert formulaic
    assert all(r.verdict.verdict == "unverifiable" for r in formulaic)

    assert report.counts["grounded"] >= report.counts["unverifiable"], report.counts
    assert report.counts["misattributed"] >= 1


@pytest.mark.asyncio
async def test_letter_blend_clause_misattributed_in_isolation():
    """Narrow regression pin for the exact F14 sentence, independent of the
    full-document fixture above."""
    verdict = await verify_claim(
        Claim(
            text=(
                "Established comprehensive testing, observability, and "
                "reliability practices."
            ),
            location="body.paragraphs[1][0].clauses[1]",
            kind="clause",
            source_experience_id="w-nordpharm",
        ),
        PROFILE,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"
