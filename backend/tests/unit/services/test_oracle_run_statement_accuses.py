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
                "Mentor two mid-level engineers.",
                "Run the team's weekly design-review session.",
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


@pytest.mark.asyncio
async def test_a_negated_first_person_clause_never_accuses():
    """Corpus re-audit (delivery-1 Katrin): "Disziplinarische Führung habe ich noch
    nicht ausgeübt" in the earlier employer's paragraph is a stated limit, not an act
    done there — the one false accusation the first cut of the rule raised."""
    letter = {"body": {"paragraphs": [
        "Bei Brückenbau Fintech GmbH habe ich Stripe Checkout mit Webhooks integriert. "
        "Prometheus und Grafana mit RED-metrics dashboards habe ich dort nicht aufgebaut; "
        "diese Grundlage bilden meine RED-metrics dashboards."
    ]}}
    claims = extract_claims_from_letter(letter, PROFILE)
    neg = next(c for c in claims if "nicht aufgebaut" in c.text)
    assert neg.anchor_inherited is True and neg.run_statement is False
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    assert all(r.verdict.verdict != "misattributed" for r in report.claims), [
        (r.claim.text, r.verdict.verdict) for r in report.claims
    ]


def test_the_subject_is_read_on_the_clause_not_the_whole_sentence():
    letter = _letter(
        "I integrated the invoice webhooks; Prometheus and Grafana monitoring with "
        "RED-metrics dashboards completes this foundation."
    )
    claims = extract_claims_from_letter(letter, PROFILE)
    tail = next(c for c in claims if "completes this foundation" in c.text)
    assert tail.run_statement is False, tail


def test_a_coordinated_predicate_borrows_the_subject():
    """delivery-2b cross A: "…, and moderated 5S and Kaizen workshops" in the
    current employer's paragraph — the elided "I" is the run's."""
    letter = _letter(
        "I also wrote the team's incident runbook, and built Prometheus and Grafana "
        "monitoring with RED-metrics dashboards."
    )
    claims = extract_claims_from_letter(letter, PROFILE)
    tail = next(c for c in claims if c.text.startswith("built Prometheus"))
    assert tail.anchor_inherited is True and tail.run_statement is True


def test_a_coordinated_clause_with_its_own_subject_does_not_borrow():
    letter = _letter(
        "I also wrote the team's incident runbook, and the Prometheus and Grafana "
        "monitoring with RED-metrics dashboards grew out of it."
    )
    claims = extract_claims_from_letter(letter, PROFILE)
    tail = next(c for c in claims if "grew out of it" in c.text)
    assert tail.run_statement is False


def test_a_semicolon_clause_does_not_borrow():
    letter = _letter(
        "I also wrote the team's incident runbook; Prometheus and Grafana monitoring "
        "with RED-metrics dashboards rounds this off."
    )
    claims = extract_claims_from_letter(letter, PROFILE)
    tail = next(c for c in claims if "rounds this off" in c.text)
    assert tail.run_statement is False


@pytest.mark.asyncio
async def test_a_multi_fact_statement_backed_only_by_another_employers_union_is_misattributed():
    """delivery-1b / delivery-2b Daniel: "I also mentor two mid-level engineers and
    run weekly design reviews." after the earlier employer's sentence — no single
    bullet backs it, so it sat at ``unverifiable``; the other employer's union does."""
    # "team's" also occurs in the run employer's own runbook bullet, so the best
    # single units are NOT exclusively foreign and the pre-union flag stays silent —
    # the recorded shape (Finleap's "team's incident runbook").
    sentence = "I also mentor two mid-level engineers and run the team's weekly design reviews."
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(sentence))
    result = _claim(report, "mentor two mid-level engineers")
    assert result.verdict.verdict == "misattributed", result.verdict
    assert "union" in (result.verdict.detail or "")
    refs = {e.ref for e in result.verdict.evidence}
    assert any(r.startswith("work_experience[0].responsibilities") for r in refs), refs


@pytest.mark.asyncio
async def test_the_same_statement_under_its_own_employer_is_not_accused():
    letter = {"body": {"paragraphs": [
        "At Nordwind Logistik GmbH, I own the GitLab CI/CD pipelines for four services. "
        "I also mentor two mid-level engineers and run weekly design reviews."
    ]}}
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    result = _claim(report, "mentor two mid-level engineers")
    assert result.verdict.verdict != "misattributed", result.verdict


# ── the carry after an ambiguous employer sentence (WP-L delivery run, case 5) ──

TWO_ROLE_PROFILE = {
    "personal_info": {"name": "Sven Beispiel"},
    "work_experience": [
        {"id": "w-kunststoff", "company": "Lindenau Kunststoff GmbH", "role": "Produktionsleiter",
         "start_date": "2017-04", "is_current": True,
         "responsibilities": ["Created real-time OEE transparency and raised OEE from 61% to 73% in 18 months."]},
        {"id": "w-blech-meister", "company": "Talwerk Umformtechnik GmbH", "role": "Schichtleiter",
         "start_date": "2011-08", "end_date": "2017-03",
         "responsibilities": ["Led a shift of 14 employees in sheet-metal forming."]},
        {"id": "w-blech-mech", "company": "Talwerk Umformtechnik GmbH", "role": "Industriemechaniker",
         "start_date": "2004-08", "end_date": "2011-07",
         "responsibilities": ["Maintained presses."]},
    ],
}


def _two_role_letter():
    return {"body": {"paragraphs": [
        "At Lindenau Kunststoff GmbH, I hold budget responsibility for personnel. "
        "At Talwerk Umformtechnik GmbH, I led a shift of 14 employees. "
        "I also created real-time OEE transparency and raised OEE from 61% to 73% in 18 months."
    ]}}


def test_an_ambiguous_employer_sentence_moves_the_carry_to_that_employer():
    claims = extract_claims_from_letter(_two_role_letter(), TWO_ROLE_PROFILE)
    oee = next(c for c in claims if "OEE transparency" in c.text)
    assert oee.anchor_inherited is True
    assert oee.source_experience_id in {"w-blech-meister", "w-blech-mech"}, oee


@pytest.mark.asyncio
async def test_the_earlier_employers_work_after_it_is_misattributed():
    report = await audit_document("cover_letter", TWO_ROLE_PROFILE, letter_data=_two_role_letter())
    result = _claim(report, "OEE transparency")
    assert result.verdict.verdict == "misattributed", result.verdict


def test_a_sentence_naming_two_employers_ends_the_carry():
    letter = {"body": {"paragraphs": [
        "At Lindenau Kunststoff GmbH, I hold budget responsibility for personnel. "
        "Both Talwerk Umformtechnik and Lindenau Kunststoff taught me lean methods. "
        "I also created real-time OEE transparency."
    ]}}
    claims = extract_claims_from_letter(letter, TWO_ROLE_PROFILE)
    oee = next(c for c in claims if "OEE transparency" in c.text)
    assert oee.source_experience_id is None, oee
