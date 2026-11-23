# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet invite gate WP-L — a first-person statement inside an employer run is
read as done at that employer, so its inherited anchor may accuse.

Recorded turn (delivery-q10, 2026-11-18, Daniel's delivered letter): the Finleap
paragraph opened "At Finleap Build GmbH, I integrated Stripe Checkout …" and went on
"I also built Prometheus and Grafana monitoring with RED-metrics dashboards, defined
SLOs with error budgets, owned GitLab CI/CD pipelines, introduced contract tests …" —
every one of those facts is Cargonaut's. The Oracle graded the sentence
``unverifiable`` (``noCloseEvidence``): its anchor was INHERITED, and #697 line 24
let an inherited anchor ground but never accuse. Both hiring managers read the work
as Finleap's. The #697 shape (a sentence with no subject pronoun) keeps its rule;
see ``test_oracle_inherited_anchor_never_accuses.py``, which must stay green.

Synthetic names stand in for the recorded persona.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document
from applire.services.oracle.extract import extract_claims_from_letter

PROFILE = {
    "personal_info": {"name": "Dario Beispiel"},
    "work_experience": [
        {
            "id": "w-nordwind",
            "company": "Nordwind Logistik GmbH",
            "role": "Senior Backend Engineer",
            "start_date": "2021-03",
            "is_current": True,
            "responsibilities": [
                "Own the GitLab CI/CD pipelines for four services.",
                "Introduced contract tests between the tracking API and three consumer teams.",
                "Built the Prometheus and Grafana setup, including RED-metrics dashboards for all four services.",
                "Defined SLOs with error budgets for the tracking API.",
            ],
        },
        {
            "id": "w-brueckenbau",
            "company": "Brückenbau Fintech GmbH",
            "role": "Backend Engineer",
            "start_date": "2017-06",
            "end_date": "2021-02",
            "responsibilities": [
                "Integrated Stripe checkout with webhooks and reconciled payouts against invoices.",
                "Wrote the team's incident runbook.",
            ],
        },
    ],
}

RUN = (
    "At Brückenbau Fintech GmbH, I integrated Stripe checkout with webhooks and "
    "reconciled payouts against invoices. "
)
FOREIGN = (
    "I also built Prometheus and Grafana monitoring with RED-metrics dashboards "
    "and owned GitLab CI/CD pipelines."
)
OWN = "I also wrote the team's incident runbook."
NO_SUBJECT = "Prometheus and Grafana monitoring with RED-metrics dashboards completes this foundation."


def _letter(*sentences: str) -> dict:
    return {"body": {"paragraphs": ["Dear Sir or Madam,", RUN + " ".join(sentences)]}}


def _claim(report, needle):
    hits = [r for r in report.claims if needle in r.claim.text]
    assert hits, f"no claim containing {needle!r}: {[r.claim.text for r in report.claims]}"
    return hits[0]


def test_extraction_marks_a_first_person_run_statement():
    claims = extract_claims_from_letter(_letter(FOREIGN, NO_SUBJECT), PROFILE)
    stmt = next(c for c in claims if "Prometheus and Grafana monitoring" in c.text and c.text.startswith("I"))
    assert stmt.anchor_inherited is True and stmt.run_statement is True
    plain = next(c for c in claims if "completes this foundation" in c.text)
    assert plain.anchor_inherited is True and plain.run_statement is False
    first = next(c for c in claims if "Stripe" in c.text)
    assert first.anchor_inherited is False and first.run_statement is False


@pytest.mark.asyncio
async def test_first_person_statement_with_foreign_evidence_is_misattributed():
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN))
    result = _claim(report, "I also built Prometheus")
    assert result.verdict.verdict == "misattributed", result.verdict
    assert result.verdict.checker == "attribution"
    assert any("work_experience[0]" in e.ref for e in result.verdict.evidence), result.verdict.evidence


@pytest.mark.asyncio
async def test_first_person_statement_of_the_runs_own_employer_still_grounds():
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(OWN))
    result = _claim(report, "incident runbook")
    assert result.verdict.verdict == "grounded", result.verdict


@pytest.mark.asyncio
async def test_a_sentence_without_a_subject_pronoun_keeps_697_line_24():
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(NO_SUBJECT))
    result = _claim(report, "completes this foundation")
    assert result.verdict.verdict != "misattributed", result.verdict
    assert result.claim.run_statement is False


@pytest.mark.asyncio
async def test_a_possessive_is_not_a_subject():
    """"My experience also includes …" names no act at the employer."""
    sentence = "My experience also includes Prometheus and Grafana monitoring with RED-metrics dashboards."
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(sentence))
    result = _claim(report, "Prometheus and Grafana monitoring")
    assert result.claim.run_statement is False
    assert result.verdict.verdict != "misattributed", result.verdict


@pytest.mark.asyncio
async def test_german_ich_counts():
    letter = {"body": {"paragraphs": [
        "Bei Brückenbau Fintech GmbH habe ich Stripe Checkout mit Webhooks integriert. "
        "Ich habe außerdem Prometheus und Grafana mit RED-metrics dashboards aufgebaut."
    ]}}
    claims = extract_claims_from_letter(letter, PROFILE)
    stmt = next(c for c in claims if "Prometheus" in c.text)
    assert stmt.run_statement is True
