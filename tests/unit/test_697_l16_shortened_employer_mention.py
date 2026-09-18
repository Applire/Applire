# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Oracle collector #697 line 16 — the report named the wrong employer.

Ground truth (Nougat build-2 delivery run, 2026-09-11, persisted report
``32-cl-truthfulness-report.json``): the letter claim

    "Bei Rasselstein führte ich als Fertigungsmeister / Schichtleiter eine
    Schicht mit 14 Mitarbeitenden und moderierte 5S- und Kaizen-Workshops."

carried ``source_experience_id`` = the **Weberit** id and verdicted
``grounded``, while its own evidence array cited ``work_experience[1]`` —
Rasselstein. The build-1 twin ("Bei Rasselstein moderierte ich …") verdicted
``misattributed`` on the same false premise: there the backing units happened
to be foreign-owned without a role-agnostic unit riding along, so the
attribution matcher fired — a control firing on a defect of its own input.

The mechanism is a seam, not a model failure. ``_find_employer_anchor``
resolves through three passes (exact name, legal-form-stripped, #372
distinctive leading token). The two ambiguity-TOLERANT signals built beside it
— ``Claim.sentence_named_ids`` and the "this sentence names no employer of its
own" test that gates the paragraph anchor carry (#237 run-4) — only ever ran
the first two. A shortened mention of a company the vault holds TWICE
therefore matched nothing anywhere: the anchor's own third pass drops a token
shared by two candidates (guard (b) — a RESOLUTION guard), and the sentence
read as naming nobody, so it INHERITED the previous sentence's employer.

``extract.sentence_mentioned_ids`` asks the tolerant question with the
anchor's full reach. Every assertion below fails when it is reverted to the
bare ``_match_ids(sentence, loose_candidates)`` call it replaced.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.oracle.audit import audit_document  # noqa: E402
from applire.services.oracle.extract import (  # noqa: E402
    extract_claims_from_letter,
    sentence_mentioned_ids,
    _employer_anchor_candidates,
)

# Two roles at ONE company (the shape that defeats the distinctive-token
# guard: the token names two candidates) plus a second, single-role employer.
PROFILE = {
    "personal_info": {"name": "Katrin Vogel"},
    "work_experience": [
        {
            "id": "w-weberit",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiterin",
            "is_current": True,
            "responsibilities": [
                "Verantwortet zwei Fertigungsbereiche im Dreischichtbetrieb.",
            ],
            "achievements": [
                "Führte Shopfloor-Management und KVP ein.",
            ],
        },
        {
            "id": "w-rassel-meisterin",
            "company": "Rasselstein Umformtechnik GmbH",
            "role": "Fertigungsmeisterin",
            "is_current": False,
            "responsibilities": [
                "Führte eine Schicht mit 14 Mitarbeitenden.",
            ],
            "achievements": [
                "Moderierte 5S- und Kaizen-Workshops als Lean-Multiplikatorin.",
            ],
        },
        {
            "id": "w-rassel-mechanikerin",
            "company": "Rasselstein Umformtechnik GmbH",
            "role": "Industriemechanikerin",
            "is_current": False,
            "responsibilities": ["Rüstete Umformpressen."],
        },
    ],
}

#: The delivered shape: an employer run, then a SHORTENED mention of the other
#: company — the second sentence must not inherit the first one's id.
LETTER = {
    "recipient": {"company": "Arnsberg Kunststoff AG"},
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Bei Weberit verantwortete ich zwei Fertigungsbereiche im "
            "Dreischichtbetrieb. Bei Rasselstein moderierte ich als "
            "Lean-Multiplikatorin 5S- und Kaizen-Workshops.",
        ]
    },
}


def _claims(letter=LETTER, profile=PROFILE):
    return {c.text: c for c in extract_claims_from_letter(letter, profile)}


def test_shortened_mention_of_a_two_role_employer_is_a_mention() -> None:
    """The tolerant signal sees what the strict anchor cannot resolve."""
    loose = _employer_anchor_candidates(PROFILE, loose=True)
    found = sentence_mentioned_ids("Bei Rasselstein moderierte ich Workshops.", loose)
    assert found == {"w-rassel-meisterin", "w-rassel-mechanikerin"}


def test_a_sentence_naming_another_employer_does_not_inherit_the_carry() -> None:
    """#697 L16: the defect itself — the wrong id on the delivered claim."""
    claims = _claims()
    rassel = next(c for c in claims.values() if "Rasselstein" in c.text)
    assert rassel.source_experience_id is None, (
        "a sentence naming Rasselstein must never carry the Weberit id"
    )
    assert rassel.sentence_named_ids == frozenset(
        {"w-rassel-meisterin", "w-rassel-mechanikerin"}
    )


def test_the_paragraph_carry_still_works_for_a_sentence_naming_nobody() -> None:
    """#237 run-4 is preserved: the widening only touches sentences that DO
    name an employer of their own."""
    letter = {
        "recipient": {"company": "Arnsberg Kunststoff AG"},
        "body": {
            "paragraphs": [
                "Bei Weberit verantwortete ich zwei Fertigungsbereiche. "
                "Dort führte ich Shopfloor-Management und KVP ein.",
            ]
        },
    }
    claims = _claims(letter=letter)
    carried = next(c for c in claims.values() if c.text.startswith("Dort"))
    assert carried.source_experience_id == "w-weberit"


def test_a_shortened_mention_of_a_single_role_employer_still_anchors() -> None:
    """#372's distinctive-token anchor is untouched: one candidate resolves."""
    claims = _claims()
    weberit = next(c for c in claims.values() if c.text.startswith("Bei Weberit"))
    assert weberit.source_experience_id == "w-weberit"


def test_the_delivered_verdict_no_longer_accuses_the_honest_sentence() -> None:
    """The build-1 shape at the DELIVERY point: the claim's evidence is owned
    by the Rasselstein role its own sentence names, so the report must not
    call it misattributed — nor stamp it with the other employer's id."""
    report = asyncio.run(
        audit_document(
            "cover_letter", profile=PROFILE, letter_data=LETTER, provider=None
        )
    )
    result = next(r for r in report.claims if "Rasselstein" in r.claim.text)
    assert result.verdict.verdict != "misattributed", result.verdict.detail
    assert result.claim.source_experience_id is None
    assert report.counts["misattributed"] == 0
