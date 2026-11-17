# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 fix round — two defects of the package's own instruments.

1. The language gate: an English vault whose short noun-phrase units tie in the
   detector (tie → 'de') read as mixed and lost its list (live case dannone,
   other-language share 0.214). A tied unit now votes for neither language; the
   true cross-language pair still gets no list; a withheld list logs one INFO
   line (ids and counts, no profile text).
2. Coordinated terms: "FastAPI / Django" for a person who wrote only Django is
   never offered as one tick (it would write a skill attesting FastAPI). It stays
   stamped open, so it is never a chip either.
Synthetic data only.
"""
from __future__ import annotations

import logging

import pytest

from applire.services import posting_terms as pt

#: 11 long English units with function words, plus the three captured tie units.
_EN_SENTENCES = [
    "Designed and operated the shipment-tracking backend in Python and Django for the platform.",
    "Redesigned the pricing data model and reduced the latency of the quote calculation.",
    "Owned the AWS deployment and the GitLab pipelines for four of the services.",
    "Introduced contract tests between the tracking API and three of the consumer teams.",
    "Mentored two engineers and ran the weekly design review session for the team.",
    "Built REST APIs in Python and Flask for a B2B invoicing product with PostgreSQL.",
    "Implemented an invoice-dunning workflow engine that processed the monthly invoices.",
    "Integrated a Stripe-based checkout for the subscription billing feature of the product.",
    "Participated in the on-call rotation and wrote the incident runbook for the team.",
    "Developed internal tooling in Python and created the SQL reporting for operations.",
    "Worked with the product team on the roadmap and on the delivery of the features.",
]
_TIES = [
    "AWS Certified Solutions Architect – Associate",
    "Keeping approximately 40k logistics users' shipments flowing",
    "Cut pager noise roughly in half through alert routing.",
]


def _vault(lines):
    return {"personal_info": {"name": "Daniel Kovac"},
            "work_experience": [{"id": "w1", "company": "Parcelo GmbH", "role": "Backend Engineer",
                                 "responsibilities": list(lines)}],
            "skills": [{"name": "Django", "status": "confirmed"}, {"name": "Python", "status": "confirmed"}]}


_GERMAN = [
    "Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden im Dreischichtbetrieb.",
    "Einführung von KVP-Routinen und Shopfloor-Management in der gesamten Fertigung.",
    "Steuerung der Instandhaltung und der Materialwirtschaft für beide Werke des Unternehmens.",
    "Aufbau der Kennzahlen für die Produktion und Berichte an die Geschäftsführung.",
]


# ── 1. the language gate ─────────────────────────────────────────────────────

def test_an_english_vault_with_tied_noun_phrases_keeps_its_list():
    """The captured dannone shape: the Oracle's share reads 3/14 = 0.214 German."""
    from applire.services.oracle.matchers.vault import build_vault_index, holds_other_language

    vault = _vault(_EN_SENTENCES + _TIES)
    assert holds_other_language(build_vault_index(vault), "en")      # the Oracle's fact, unchanged
    reading = pt.language_pair_reading(vault, "en")
    assert reading["same"] is True and reading["other_share"] == 0.0
    assert reading["long_units"] >= 14 and reading["decided_units"] == reading["long_units"] - 3


def test_the_true_cross_language_pair_still_gets_no_list():
    reading = pt.language_pair_reading(_vault(_GERMAN * 3), "en")
    assert reading["same"] is False and reading["reason"] == "dominant_language_differs"


def test_a_really_mixed_vault_still_gets_no_list():
    """German sentences with German function words still vote: 4 of 15 ≥ 0.2."""
    reading = pt.language_pair_reading(_vault(_EN_SENTENCES + _GERMAN), "en")
    assert reading["same"] is False and reading["reason"] == "mixed_vault"
    assert reading["other_share"] >= 0.2


def test_a_withheld_list_is_one_info_line_with_ids_and_counts_only(caplog):
    caplog.set_level(logging.INFO, logger="applire.services.posting_terms")
    assert not pt.same_language_pair(_vault(_GERMAN * 3), "en", seam="posting terms", job_id="job-1")
    (rec,) = [r for r in caplog.records if "list withheld" in r.getMessage()]
    msg = rec.getMessage()
    assert rec.levelno == logging.INFO
    assert "job=job-1" in msg and "reason=dominant_language_differs" in msg and "long_units=" in msg
    assert "Fertigung" not in msg and "Parcelo" not in msg                  # no profile text


def test_a_same_language_pair_logs_nothing(caplog):
    caplog.set_level(logging.INFO, logger="applire.services.posting_terms")
    assert pt.same_language_pair(_vault(_EN_SENTENCES), "en", seam="posting terms", job_id="j")
    assert not [r for r in caplog.records if "list withheld" in r.getMessage()]


# ── 2. coordinated terms ─────────────────────────────────────────────────────

@pytest.mark.parametrize("term,parts", [
    ("FastAPI / Django", ["FastAPI", "Django"]),
    ("FastAPI/Django", ["FastAPI", "Django"]),
    ("Kafka, RabbitMQ", ["Kafka", "RabbitMQ"]),
    ("Python oder Go", ["Python", "Go"]),
    ("Kafka or Pulsar", ["Kafka", "Pulsar"]),
    ("Budgetierung & Forecast", ["Budgetierung", "Forecast"]),
    ("CI/CD", []), ("GitLab CI/CD", []), ("SAP CO/FI", []), ("TCP/IP", []), ("UI/UX", []),
    ("KVP/Kaizen", []), ("R&D", []), ("Maschinendaten- und Betriebsdatenerfassung", []),
    ("Ein-/Verkauf", []), ("Kubernetes", []),
])
def test_coordination_parts_split_coordinations_and_keep_names_whole(term, parts):
    assert pt.coordination_parts(term) == parts


def _row(concept):
    return {"concept": concept, "surface_forms": [concept], "status": "direct", "claimable": True,
            "sources": ["required"], "evidence": "x"}


def test_a_coordination_with_a_held_part_is_never_offered_but_stays_stamped():
    vault = _vault(_EN_SENTENCES)
    ledger = [_row("FastAPI / Django"), _row("Event streaming")]
    assert [r["concept"] for r in pt.posting_term_rows(ledger, vault)] == ["FastAPI / Django", "Event streaming"]
    assert [r["concept"] for r in pt.offered_posting_term_rows(ledger, vault)] == ["Event streaming"]
    stamped = pt.stamp_posting_terms_open(ledger, vault, same_language=True)
    assert pt.is_posting_term_open(stamped[0])            # never a chip either
    assert pt.chip_posting_term("FastAPI / Django", stamped) == "FastAPI / Django"


def test_a_coordination_none_of_whose_parts_is_held_is_offered_like_any_term():
    vault = _vault(_EN_SENTENCES)
    ledger = [_row("Kafka / RabbitMQ")]
    assert [r["concept"] for r in pt.offered_posting_term_rows(ledger, vault)] == ["Kafka / RabbitMQ"]


def test_an_acronym_pair_is_one_name_and_is_offered():
    vault = _vault(_EN_SENTENCES)
    assert [r["concept"] for r in pt.offered_posting_term_rows([_row("CI/CD")], vault)] == ["CI/CD"]


def test_the_card_list_leaves_out_the_mixed_coordination():
    from applire.services.posting_term_answer import compute_state

    vault = _vault(_EN_SENTENCES)
    state = compute_state([_row("FastAPI / Django"), _row("Event streaming")], vault, "en", None)
    assert [t["term"] for t in state.terms] == ["Event streaming"]
