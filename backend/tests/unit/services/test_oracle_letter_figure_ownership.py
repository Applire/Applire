# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#243-adjacent (Oracle half) — letter figures check ignores evidence
ownership when a claim is UNANCHORED (#237 ``_find_employer_anchor`` stamps
no ``source_experience_id`` when a sentence names no employer, or two).

Live-reproduced 2026-07-24 (generated_cover_letters 9f387766-...): the
sentence "I also built a deterministic verification layer ... backed by full
LLM exchange observability logging and over 2,600 tests gating CI." names NO
employer at all — it is a bare continuation sentence following two anchored
paragraphs (NordPharm, then Applire). The ONLY vault unit carrying the figure
"2,600" is a project entry owned exclusively by the NordPharm role, while the
letter's surrounding context (and the claim itself, read narratively) is
about Applire's Truthfulness Oracle. Because the claim is unanchored,
``source_experience_id`` is ``None``, and the existing per-figure attribution
check (:func:`applire.services.oracle.matchers.find_foreign_owner`) fails
open on a ``None`` source id BY DESIGN (it must never guess an attribution
for a claim that names no position) — so the figure graded "grounded" from
evidence that in fact belongs to a different, unnamed role.

Rule implemented (see ``extract.letter_named_experience_ids`` +
``audit.verify_claim``'s ``letter_named_ids`` parameter): for an UNANCHORED
letter claim carrying a figure, if EVERY backing unit for that figure is
owned (no role-agnostic unit clears it) AND none of those owners' names are
mentioned ANYWHERE ELSE in the letter, full attribution is genuinely
impossible — downgrade to ``unverifiable`` with an honest note, rather than
letting the deterministic "numbers" grounded verdict launder it. A legitimate
unanchored summary sentence whose figure-owning employer IS named somewhere
else in the letter (or whose backing is role-agnostic) is unaffected.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {
        "en": "Lead AI Engineer with hands-on expertise in production AI systems."
    },
    "work_experience": [
        {
            "id": "w-nordpharm",
            "company": "NordPharm",
            "role": "Automation Lead",
        },
        {
            "id": "w-applire",
            "company": "Applire",
            "role": "Founder",
        },
    ],
    "projects": [
        {
            "id": "p-genai",
            "name": "Agentic GenAI System",
            "associated_experience": "w-nordpharm",
            "achievements": [
                "Backed by full LLM exchange observability logging and "
                "over 2,600 tests gating CI.",
            ],
        }
    ],
}


def _figure_result(report, needle="2,600"):
    hits = [r for r in report.claims if needle in r.claim.text]
    assert hits, f"expected a decomposed claim containing {needle!r}"
    return hits


@pytest.mark.asyncio
async def test_unanchored_figure_never_named_elsewhere_is_not_grounded():
    """The live bug: NordPharm is never named ANYWHERE in the letter, the
    figure's only backing is NordPharm-owned, and the sentence itself is
    unanchored — must NOT verdict grounded."""
    letter = {
        "body": {
            "paragraphs": [
                "I am writing to express my interest in the Lead AI Engineer "
                "position.",
                "I also built a deterministic verification layer auditing "
                "every LLM output against source data, backed by full LLM "
                "exchange observability logging and over 2,600 tests gating CI.",
                "I look forward to the opportunity to discuss this further.",
            ]
        }
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    for r in _figure_result(report):
        assert r.verdict.verdict != "grounded", (r.claim.text, r.verdict)
        assert r.verdict.verdict == "unverifiable"
        assert r.verdict.checker == "attribution"
        assert r.verdict.evidence


@pytest.mark.asyncio
async def test_unanchored_figure_named_elsewhere_stays_grounded():
    """Legitimate case: the SAME unanchored figure sentence, but NordPharm is
    named in an EARLIER paragraph of the same letter — full attribution
    isn't provable at clause level, but the owning employer isn't a stranger
    to the document, so this must stay grounded (no over-firing)."""
    letter = {
        "body": {
            "paragraphs": [
                "At NordPharm, I led automation projects that modernised "
                "validation workflows.",
                "I also built a deterministic verification layer auditing "
                "every LLM output against source data, backed by full LLM "
                "exchange observability logging and over 2,600 tests gating CI.",
            ]
        }
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    for r in _figure_result(report):
        assert r.verdict.verdict == "grounded", (r.claim.text, r.verdict)


@pytest.mark.asyncio
async def test_unanchored_figure_with_role_agnostic_backing_stays_grounded():
    """A figure whose vault backing includes a role-agnostic unit (e.g. the
    professional summary) is never attribution-starved, anchored or not."""
    profile = {
        **PROFILE,
        "professional_summary": {
            "en": (
                "Lead AI Engineer whose Truthfulness Oracle is backed by "
                "over 2,600 tests gating CI."
            )
        },
    }
    letter = {
        "body": {
            "paragraphs": [
                "I also built a deterministic verification layer backed by "
                "over 2,600 tests gating CI.",
            ]
        }
    }
    report = await audit_document("cover_letter", profile, letter_data=letter)
    for r in _figure_result(report):
        assert r.verdict.verdict == "grounded", (r.claim.text, r.verdict)
