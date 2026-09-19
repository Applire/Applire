# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#697 line 24 — an INHERITED paragraph anchor may ground, never accuse.

Ground truth (blind Kaile probe, 2026-09-19; dev DB
``generated_cover_letters`` id ``53ae8076…``, claim at
``body.paragraphs[3][4]``): the delivered letter read

    "HGB, Reporting und Konsolidierung bilden hierfür eine angrenzende
    Grundlage, ohne US-GAAP-Kompetenz zu behaupten."

``sentence_named_ids`` was EMPTY — the sentence names no employer at all —
while ``source_experience_id`` carried the id of the *previous* employer,
INHERITED from sentence ``[3][1]`` through the paragraph anchor carry
(``extract.extract_claims_from_letter`` docstring point 5). The claim's own
vault evidence belonged to the OTHER position, so ``_attribution_red_flag``
fired: verdict ``misattributed``, checker ``attribution``, detail *"Backed
only by evidence from a different position … the claim is rendered under a
role it does not belong to."*

Both halves of that accusation are false for this shape. The reader sees NO
role on this sentence, so the document never rendered the claim under one;
and the evidence the report cites is the RIGHT employer's — the report
accused the letter of a blend the letter did not commit, on the strength of
an id only the report itself can see.

The fix keeps the carry's grounding value and removes only its power to
accuse: ``Claim.anchor_inherited`` marks the carried id, and
``audit.verify_claim`` hands ``None`` to every ``_attribution_red_flag`` call
for such a claim while every grounding path still sees the full
``source_experience_id``. A sentence that names its OWN employer is
completely unaffected — that is the mutation guard below.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document
from applire.services.oracle.extract import extract_claims_from_letter

PROFILE = {
    "personal_info": {"name": "Katrin Hoffmann"},
    "work_experience": [
        {
            "id": "w-brauner",
            "company": "Brauner & Söhne Maschinenbau GmbH",
            "role": "Controllerin",
            "is_current": False,
            "achievements": [
                "Kostenstellenrechnung und Kostenträgerrechnung im "
                "Maschinenbau aufgebaut.",
            ],
        },
        {
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Senior Controllerin",
            "is_current": True,
            "achievements": [
                "Reporting und Konsolidierung nach HGB bilden eine "
                "angrenzende Grundlage der Werksberichterstattung.",
            ],
        },
    ],
}

# Sentence 1 anchors on Brauner; sentence 2 names NOTHING and inherits that
# anchor, while its only vault evidence is Schwarzwald's HGB/Reporting/
# Konsolidierung achievement — the exact persisted shape.
INHERITED_LETTER = {
    "body": {
        "paragraphs": [
            "Bei Brauner & Söhne Maschinenbau habe ich die "
            "Kostenstellenrechnung aufgebaut. "
            "Reporting und Konsolidierung nach HGB bilden hierfür eine "
            "angrenzende Grundlage."
        ]
    }
}

# The mutation guard's twin: the SAME second sentence, but naming its own
# employer. Here the document really does render the claim under a role, and
# the accusation is the correct verdict.
OWN_ANCHOR_LETTER = {
    "body": {
        "paragraphs": [
            "Meine Laufbahn begann im Controlling. "
            "Bei Brauner & Söhne Maschinenbau bilden Reporting und "
            "Konsolidierung nach HGB eine angrenzende Grundlage."
        ]
    }
}

_INHERITED_TEXT = "Reporting und Konsolidierung nach HGB bilden hierfür"
_OWN_TEXT = "bilden Reporting und Konsolidierung nach HGB"


def _claim(report, needle):
    hits = [r for r in report.claims if needle in r.claim.text]
    assert hits, f"expected a claim containing {needle!r}"
    return hits[0]


def test_extraction_marks_the_carried_anchor_as_inherited():
    """The fact is recorded on the claim, not inferred at audit time."""
    claims = extract_claims_from_letter(INHERITED_LETTER, PROFILE)
    first = next(c for c in claims if "Kostenstellenrechnung aufgebaut" in c.text)
    assert first.source_experience_id == "w-brauner"
    assert first.anchor_inherited is False

    carried = next(c for c in claims if _INHERITED_TEXT in c.text)
    # Inherited, not named: the id is carried, the sentence names nobody.
    assert carried.source_experience_id == "w-brauner"
    assert carried.sentence_named_ids == frozenset()
    assert carried.anchor_inherited is True


def test_own_named_anchor_is_never_marked_inherited():
    claims = extract_claims_from_letter(OWN_ANCHOR_LETTER, PROFILE)
    own = next(c for c in claims if _OWN_TEXT in c.text)
    assert own.source_experience_id == "w-brauner"
    assert own.anchor_inherited is False


@pytest.mark.asyncio
async def test_inherited_anchor_with_foreign_evidence_is_not_misattributed():
    """(a) The defect itself: a claim that names no employer must never be
    told it is "rendered under a role it does not belong to"."""
    report = await audit_document("cover_letter", PROFILE, letter_data=INHERITED_LETTER)
    result = _claim(report, _INHERITED_TEXT)
    assert result.verdict.verdict != "misattributed", result.verdict
    assert result.verdict.checker != "attribution", result.verdict
    # The fact is visible to a report reader, so the absent accusation is
    # explainable rather than mysterious.
    assert result.claim.anchor_inherited is True


@pytest.mark.asyncio
async def test_own_named_anchor_with_foreign_evidence_stays_misattributed():
    """(b) THE mutation guard. Invert the fix — drop the ``anchor_inherited``
    blinding in ``verify_claim`` — and this test must stay green while the
    one above fails. A sentence that names Brauner while standing entirely on
    Schwarzwald's evidence IS the #196 blend the verdict exists for."""
    report = await audit_document("cover_letter", PROFILE, letter_data=OWN_ANCHOR_LETTER)
    result = _claim(report, _OWN_TEXT)
    assert result.verdict.verdict == "misattributed", result.verdict
    assert result.verdict.checker == "attribution"
    assert result.claim.anchor_inherited is False


@pytest.mark.asyncio
async def test_inherited_anchor_still_grounds_through_the_carried_role():
    """Over-drop guard: the carry keeps its grounding value. A continuation
    sentence whose content genuinely belongs to the carried role still
    grounds through that role's own evidence — the fix removes the accusation
    only, never the evidence path."""
    letter = {
        "body": {
            "paragraphs": [
                "Bei Brauner & Söhne Maschinenbau habe ich die "
                "Kostenstellenrechnung aufgebaut. "
                "Die Kostenträgerrechnung im Maschinenbau habe ich "
                "aufgebaut."
            ]
        }
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    result = _claim(report, "Kostenträgerrechnung im Maschinenbau habe ich")
    assert result.claim.anchor_inherited is True
    assert result.verdict.verdict == "grounded", result.verdict


@pytest.mark.asyncio
async def test_employer_fact_and_denial_short_circuits_are_untouched():
    """The ``not_applicable`` paths run BEFORE any attribution question and
    must stay exactly where they were."""
    letter = {
        "recipient": {"company": "Rheinwerk Industrie AG"},
        "body": {
            "paragraphs": [
                "Bei Brauner & Söhne Maschinenbau habe ich die "
                "Kostenstellenrechnung aufgebaut.",
                "Rheinwerk Industrie verbindet Präzisionsfertigung mit "
                "einem klaren Qualitätsanspruch.",
                "Bei Brauner & Söhne Maschinenbau habe ich die "
                "Kostenstellenrechnung aufgebaut. "
                "Mit US-GAAP habe ich keine Erfahrung.",
            ]
        },
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    fact = _claim(report, "Rheinwerk Industrie verbindet")
    assert fact.verdict.verdict == "not_applicable"
    denial = _claim(report, "Mit US-GAAP habe ich keine Erfahrung")
    assert denial.verdict.verdict == "not_applicable"
