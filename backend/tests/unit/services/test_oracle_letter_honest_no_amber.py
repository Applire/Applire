# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial-pass BLOCKER residual (2026-07-23).

Live-reproduced on the real-LLM adversarial pass: an ENTIRELY HONEST
generated cover letter still audited unverifiable-dominated ({grounded: 8,
unverifiable: 11, ...}) — the amber "most claims couldn't be checked"
warning (``TruthfulnessPanel.tsx``'s ``isUnverifiableDominant``) fires on a
truthful letter. Two verified causes, both fixed deterministically:

(a) courtesy boilerplate ("I am writing to express my interest…", "Thank
    you for your time and consideration.") counted as unverifiable claims.
    RENEGOTIATED 2026-08-08 (ADR-068's sentence-triage amendment, #309 +
    #373): the deterministic formula filter that used to DROP these is
    retired — a phrase list cannot answer "does this assert anything about
    the candidate?" (ADR-062 clause 1/3). The courtesy sentences are now
    extracted and answered by the ``sentence_triage`` seam with a VISIBLE,
    quoted ``not_applicable`` verdict, which leaves the denominator exactly
    as the drop did — but is shown to the user instead of vanishing. This
    fixture therefore audits WITH the seam wired (a targeted stub, per
    ADR-062 clause 7 a mock can only pin wiring); with the seam DOWN the
    same letter is audited sentence by sentence and may read amber again —
    the degradation cost the amendment names explicitly, self-identified via
    ``judgement_unavailable``.
(b) truthful multi-skill enumeration clauses ("My experience includes
    designing and implementing RESTful APIs with Python, FastAPI") failed
    single-unit grounding — see ``grounding.py``'s skill-union fallback.

This fixture reproduces the adversarial run's shape (courtesy opener/closer
+ several skill-enumeration clauses + a role-anchored achievement clause)
and pins that the report audits grounded-dominated: the frontend's
``counts.unverifiable > counts.grounded`` amber condition must not hold.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document
from tests.unit.services.oracle_triage_stub import TriageStubProvider

PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {"en": "Backend engineer focused on reliable automation."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "achievements": [
                "Reduced deployment time by 40% through CI automation.",
            ],
        }
    ],
    "skills": [
        {"name": "Python"}, {"name": "FastAPI"}, {"name": "REST APIs"},
        {"name": "PostgreSQL"}, {"name": "SQLAlchemy"}, {"name": "Docker"},
        {"name": "Git"}, {"name": "GitHub Actions"},
    ],
}

LETTER = {
    "header": {"name": "Anna Bauer"},
    "body": {
        "paragraphs": [
            # Pure courtesy opener — extracted, then triaged out visibly.
            "I am writing to express my interest in the Senior Backend "
            "Engineer position at your company.",
            # A truthful, figure-carrying, employer-anchored achievement.
            "At Acme GmbH, I reduced deployment time by 40% through CI "
            "automation.",
            # Truthful multi-skill enumeration clauses (#237 follow-up).
            "My experience includes designing and implementing RESTful "
            "APIs with Python, FastAPI.",
            "I have also worked with PostgreSQL, SQLAlchemy, and Docker.",
            "I automated CI/CD workflows using Git and GitHub Actions.",
            # Pure courtesy closer — extracted, then triaged out visibly.
            "Thank you for your time and consideration.",
        ]
    },
    "signature": {"closing": "Mit freundlichen Grüßen"},
}


def _seam() -> TriageStubProvider:
    """The triage seam, available and classifying this fixture's courtesy
    opener/closer as ``epistolary-form``. Marker-driven test wiring, never a
    classifier: correctness is charter-run evidence (ADR-062 clause 7)."""
    return TriageStubProvider(
        epistolary=("express my interest", "thank you for your time")
    )


@pytest.mark.asyncio
async def test_honest_letter_audits_grounded_dominated_no_amber():
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_seam()
    )

    # (1) the frontend amber trigger (counts.unverifiable > counts.grounded)
    # must not fire on this entirely truthful letter.
    assert report.counts["grounded"] >= report.counts["unverifiable"], report.counts
    assert report.counts["misattributed"] == 0
    assert report.counts["inflated"] == 0
    assert report.counts["unbacked"] == 0

    # (2) the courtesy opener/closer carry a VISIBLE, quoted verdict now —
    # extracted, never silently dropped, and out of the denominator.
    courtesy = [
        r
        for r in report.claims
        if "express my interest" in r.claim.text.lower()
        or "thank you for your time" in r.claim.text.lower()
    ]
    assert len(courtesy) >= 2, [r.claim.text for r in report.claims]
    for r in courtesy:
        assert r.verdict.verdict == "not_applicable", (r.claim.text, r.verdict)
        assert r.verdict.checker == "sentence_triage", r.verdict
        assert r.claim.text in (r.verdict.detail or "")

    # (3) the enumeration clauses ground via the skill union.
    by_text = {r.claim.text: r.verdict for r in report.claims}
    enumeration_texts = [
        t
        for t in by_text
        if "restful apis" in t.lower()
        or "postgresql" in t.lower()
        or "github actions" in t.lower()
    ]
    assert enumeration_texts, "expected at least one enumeration clause claim"
    assert all(by_text[t].verdict == "grounded" for t in enumeration_texts), {
        t: by_text[t].verdict for t in enumeration_texts
    }

    # (4) the figure-carrying achievement clause still grounds as before.
    figure_texts = [t for t in by_text if "40%" in t]
    assert figure_texts
    assert all(by_text[t].verdict == "grounded" for t in figure_texts)
