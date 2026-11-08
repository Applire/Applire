# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UX audit 4 (Kaile on Sonnet 5.5, CV 51f85ef8 + letter 9dc1082b): the ledger
form "Konsolidierung" of the posting term "Konzernkonsolidierung" was found as
the TAIL of "Kapitalkonsolidierung" and in "konsolidierten Quartalszahlen", both
the profile's own wording, and the honest documents got ``stop_and_fix: true``.

ADR-062 cl. 1: a word boundary is a fact. The unsupported-claim accusation reads
only occurrences that name the term in a word of their own which the profile
does not already carry. Presence and coverage keep the substring predicate.
Synthetic texts; no persona string.
"""
from __future__ import annotations

import pytest

from applire.services.ats_audit import _keyword_coverage, _norm

LEDGER = [{
    "concept": "Konsolidierung",
    "surface_forms": ["Konzernkonsolidierung", "Konsolidierung"],
    "sources": ["keyword"], "status": "gap", "evidence": "", "claimable": False,
}]
KW = ["Konzernkonsolidierung"]
VAULT = _norm("Leitete das Teilprojekt Kapitalkonsolidierung. Erstellt die konsolidierten Monatszahlen mit.")


def _cov(doc: str, vault: str | None = VAULT):
    return _keyword_coverage(_norm(doc), KW, LEDGER, vault, None, None)


@pytest.mark.parametrize("vault", [VAULT, _norm("Werkscontrolling, SAP CO.")])
def test_a_form_inside_a_different_compound_is_no_claim_but_stays_present(vault):
    """Both rules on their own: with or without the profile carrying the compound."""
    cov = _cov("Ich verantwortete das Teilprojekt Kapitalkonsolidierung in der Gruppe.", vault=vault)
    assert cov.present_unsupported == []
    assert cov.present == ["Konzernkonsolidierung"]  # coverage/score unchanged


def test_the_profiles_own_word_is_no_claim():
    assert _cov("Ich erstelle die konsolidierten Monatszahlen mit.").present_unsupported == []


def test_the_same_word_without_the_profile_carrying_it_is_still_flagged():
    cov = _cov("Ich erstelle die konsolidierten Monatszahlen mit.", vault=_norm("Werkscontrolling, SAP CO."))
    assert cov.present_unsupported == ["Konzernkonsolidierung"]


@pytest.mark.parametrize("doc", [
    "Ich verantworte die Konzernkonsolidierung nach HGB.",
    "Erfahrung in der Konsolidierung von zwölf Gesellschaften.",
    "Die Konsolidierungen im Quartal habe ich geleitet.",
    "Ich leitete die Konzern-Konsolidierung.",
])
def test_a_real_claim_of_the_term_is_still_flagged(doc):
    cov = _cov(doc)
    assert cov.present_unsupported == ["Konzernkonsolidierung"], doc
    assert cov.present_unsupported_matches["Konzernkonsolidierung"]


def test_the_form_as_a_modifier_still_claims():
    """Head position names the thing ("Konsolidierungsprozesse" = processes OF
    consolidation): unchanged substring reach."""
    assert _cov("Ich optimierte die Konsolidierungsprozesse.").present_unsupported == ["Konzernkonsolidierung"]


def test_a_bare_term_keeps_its_reach_into_compounds():
    """The sibling rule needs the TERM to be the compound. "Führung" in
    "Mitarbeiterführung" is still a claim of Führung (D-4c pins the same for
    "Lebensmittel" in "Lebensmittelproduktion")."""
    ledger = [{"concept": "Führung", "surface_forms": ["Führung"], "sources": ["keyword"],
               "status": "gap", "evidence": "", "claimable": False}]
    cov = _keyword_coverage(_norm("Ich verantwortete die Mitarbeiterführung von 40 Personen."),
                            ["Führung"], ledger, _norm("SAP CO."), None, None)
    assert cov.present_unsupported == ["Führung"]


def test_only_the_claiming_sentence_is_located_and_recorded():
    cov = _cov("Ich leitete die Kapitalkonsolidierung. Danach verantwortete ich die Konzernkonsolidierung.")
    assert cov.present_unsupported == ["Konzernkonsolidierung"]
    # both forms name the term's own word "Konzernkonsolidierung" here
    assert {m.form for m in cov.present_unsupported_matches["Konzernkonsolidierung"]} == {
        "Konzernkonsolidierung", "Konsolidierung",
    }
    (sent,) = cov.present_unsupported_sentences["Konzernkonsolidierung"]
    assert "kapitalkonsolidierung" not in sent
