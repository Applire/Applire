# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — the posting-term reading (rulings Q7-2, Q10-1…Q10-6; ADR-048
amended 2026-11-17).

A claimable ``direct`` ledger row whose CONCEPT the person never used is a
posting term: they are asked once whether it is theirs, and until they tick it
it is never printed as their skill. The fixtures restate the synthetic personas
of delivery 2a/2b (Katrin, Stefan, Daniel); names are fixtures.
"""
from __future__ import annotations

import copy

from applire.services import posting_terms as pt


def _row(concept, forms=(), status="direct", **kw):
    return {"concept": concept, "surface_forms": list(forms), "status": status,
            "claimable": True, "sources": ["required"], "evidence": "x", **kw}


KATRIN = {
    "professional_summary": {"de": "Controllerin mit 9 Jahren Erfahrung im industriellen "
                                   "Mittelstand. Zuhause in Monats- und Jahresabschluss, Planung "
                                   "und Werkscontrolling.", "en": ""},
    "work_experience": [{
        "id": "w1", "company": "Muster GmbH", "role": "Controllerin",
        "responsibilities": [
            "Verantwortlich für Monatsabschluss und Reporting (HGB) der GmbH.",
            "Einführung der HGB-konformen Percentage-of-Completion-Bewertung im Sondermaschinenbereich.",
            "Pflege der Kostenstellen- und Kostenträgerrechnung.",
        ],
    }],
    "skills": [{"name": "Budgetierung & Forecast", "status": "confirmed"},
               {"name": "Herstellkostenrechnung", "status": "confirmed"},
               {"name": "Percentage of Completion", "status": "confirmed"}],
    "metadata": {"denied_concepts": [{
        "concept": "POC-Projektcontrolling",
        "statement": "Bei mir steht POC für Percentage of Completion, also die Bewertung "
                     "langfristiger Aufträge nach Fertigstellungsgrad.",
    }]},
}
STEFAN = {
    "projects": [{"id": "p1", "name": "MES", "responsibilities": [
        "Auswahl, Pilotlinie und Rollout eines MES-Systems zur Maschinendaten- und "
        "Betriebsdatenerfassung an 14 Spritzgussmaschinen."]}],
    "work_experience": [{"id": "w1", "company": "Weberit", "role": "Produktionsleiter",
                         "responsibilities": ["Direkte Führung von drei Schichtleitern."]}],
    "skills": [{"name": "Lean Management", "status": "confirmed"},
               {"name": "Mitarbeiterführung", "status": "confirmed"}],
}
DANIEL = {
    "work_experience": [{"id": "w1", "company": "Parcelo", "role": "Backend Engineer",
                         "responsibilities": [
                             "Designed and ran the shipment order-event pipeline on Apache Kafka.",
                             "Built Prometheus and Grafana setup, including alert routing.",
                             "Helm charts for all 12 services in Git, rolled out by ArgoCD GitOps."]}],
    "skills": [{"name": "Kafka", "status": "confirmed"}, {"name": "Django", "status": "confirmed"},
               {"name": "SQL performance tuning", "status": "confirmed"}],
    "metadata": {"denied_concepts": [{
        "concept": "cluster provisioning",
        "statement": "Helm charts for all 12 services, rolled out by ArgoCD. That is the "
                     "infrastructure as code I did there; I did not write the cluster "
                     "provisioning itself.",
    }, {
        "concept": "Security certification",
        "statement": "I have no formal security engineering certification.",
    }]},
}


def _concepts(rows):
    return [r["concept"] for r in rows]


# ── the three false reds of the Q7 prototype (next-package-notes) ─────────────

def test_poc_is_the_persons_word_when_she_defined_the_acronym():
    rows = pt.posting_term_rows([_row("POC-Bewertung")], KATRIN)
    assert rows == []


def test_a_suspended_hyphen_in_the_vault_is_expanded():
    rows = pt.posting_term_rows([_row("Maschinendatenerfassung")], STEFAN)
    assert rows == []


def test_a_term_written_with_a_suspended_hyphen_is_read_word_by_word():
    rows = pt.posting_term_rows([_row("Monats- und Jahresabschluss-Reporting")], KATRIN)
    assert rows == []


# ── the borderline pairs belong on the list (notes) ───────────────────────────

def test_the_borderline_pairs_are_posting_terms():
    rows = pt.posting_term_rows(
        [_row("Budgetprozess"), _row("Kostenrechnung")], KATRIN)
    assert _concepts(rows) == ["Budgetprozess", "Kostenrechnung"]
    rows = pt.posting_term_rows(
        [_row("Lean-Produktion"), _row("Führungserfahrung")], STEFAN)
    assert _concepts(rows) == ["Lean-Produktion", "Führungserfahrung"]


def test_the_near_duplicate_instrument_does_not_join_the_borderline_pairs():
    from applire.services.ats_audit import skills_near_dupe

    for a, b in (("Budgetprozess", "Budgetierung & Forecast"),
                 ("Kostenrechnung", "Herstellkostenrechnung"),
                 ("Lean-Produktion", "Lean Management"),
                 ("Führungserfahrung", "Mitarbeiterführung")):
        assert not skills_near_dupe(a, b), (a, b)


# ── what counts as the person's word ──────────────────────────────────────────

def test_an_affirmative_clause_of_a_denial_statement_is_the_persons_word():
    rows = pt.posting_term_rows([_row("Infrastructure as code")], DANIEL)
    assert rows == []


def test_a_negated_clause_of_a_denial_statement_is_not_the_persons_word():
    rows = pt.posting_term_rows([_row("Security engineering")], DANIEL)
    assert _concepts(rows) == ["Security engineering"]


def test_a_sibling_surface_form_in_the_persons_words_does_not_hold_the_concept():
    rows = pt.posting_term_rows(
        [_row("Observability", ["monitoring", "alerting"])], DANIEL)
    assert _concepts(rows) == ["Observability"]


def test_a_coordinated_concept_is_held_only_when_every_part_is():
    assert pt.posting_term_rows([_row("Kafka / Django")], DANIEL) == []
    assert _concepts(pt.posting_term_rows([_row("FastAPI / Django")], DANIEL)) == ["FastAPI / Django"]


def test_a_near_duplicate_of_a_held_skill_is_the_persons_word():
    assert pt.posting_term_rows([_row("SQL performance-tuning")], DANIEL) == []


def test_a_skill_alias_is_the_persons_word():
    vault = copy.deepcopy(KATRIN)
    vault["skills"][0]["aliases"] = ["Budgetprozess"]
    assert pt.posting_term_rows([_row("Budgetprozess")], vault) == []


def test_only_claimable_direct_rows_that_are_not_keyword_only_are_read():
    ledger = [
        _row("Budgetprozess", status="partial"),
        {**_row("Budgetprozess"), "claimable": False},
        {**_row("Budgetprozess"), "sources": ["keyword"]},
        {**_row("Budgetprozess"), "bar": {"figure": 3}},  # a scope row
    ]
    assert pt.posting_term_rows(ledger, KATRIN) == []


def test_unheld_forms_are_the_concept_plus_the_forms_the_person_never_used():
    row = _row("Security engineering", ["application security", "least-privilege IAM"])
    vault = copy.deepcopy(DANIEL)
    vault["work_experience"][0]["responsibilities"].append("Least-privilege IAM on AWS.")
    assert pt.unheld_forms(row, vault) == ["Security engineering", "application security"]


def test_their_words_are_held_forms_and_the_backing_quote():
    row = _row("Observability", ["monitoring", "alerting"],
               backing={"quote": "Built Prometheus and Grafana setup, including alert routing."})
    words = pt.their_words(row, DANIEL)
    assert words == ["alerting", "Built Prometheus and Grafana setup, including alert routing."]


# ── the stamp ─────────────────────────────────────────────────────────────────

def test_the_stamp_marks_posting_terms_with_their_unheld_forms_and_drops_a_stale_one():
    ledger = [_row("Budgetprozess"), {**_row("Monatsabschluss"), pt.POSTING_TERM_OPEN_KEY: True}]
    before = copy.deepcopy(ledger)
    out = pt.stamp_posting_terms_open(ledger, KATRIN, same_language=True)
    assert ledger == before  # pure
    assert out[0][pt.POSTING_TERM_OPEN_KEY] is True
    assert out[0][pt.UNHELD_FORMS_KEY] == ["Budgetprozess"]
    assert pt.POSTING_TERM_OPEN_KEY not in out[1] and pt.UNHELD_FORMS_KEY not in out[1]
    assert pt.is_posting_term_open(out[0]) and not pt.is_posting_term_open(out[1])


def test_across_languages_nothing_is_stamped():
    ledger = [_row("Budgetprozess"), {**_row("Kostenrechnung"), pt.POSTING_TERM_OPEN_KEY: True}]
    out = pt.stamp_posting_terms_open(ledger, KATRIN, same_language=False)
    assert not any(pt.is_posting_term_open(e) for e in out)


def test_a_ticked_term_is_no_longer_a_posting_term():
    vault = copy.deepcopy(KATRIN)
    vault["skills"].append({"name": "Budgetprozess", "status": "confirmed"})
    out = pt.stamp_posting_terms_open([_row("Budgetprozess")], vault, same_language=True)
    assert not pt.is_posting_term_open(out[0])


def test_an_unconfirmed_or_denied_skill_does_not_hold_the_word():
    vault = copy.deepcopy(KATRIN)
    vault["skills"].append({"name": "Budgetprozess", "status": "denied"})
    assert _concepts(pt.posting_term_rows([_row("Budgetprozess")], vault)) == ["Budgetprozess"]
