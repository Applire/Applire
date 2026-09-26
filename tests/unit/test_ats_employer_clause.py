# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 amended 2026-09-26 (WP-R, founder ruling R-2, option A): the clause that
describes the employer's business is not a candidate claim.

The clause starts at a mention of the employer's name (as analysed, without its
legal form, or its first word when that word has at least four letters, as a
whole word) and ends at the first first-person word, the sentence end or a
salutation. It is anchored on the name ONLY: "Sie/Ihr/your" is no anchor, because
on the captured letters that anchor masked the candidate's own intent sentences.
Sentences below are the captured synthetic delivery-run letters (Rheinwerk,
NovaPay, Arnold), normalised the way the audit normalises the PDF text.
"""
from __future__ import annotations

import pytest

from applire.services.ats_audit import (
    _CORPUS_FRAGMENT_BOUNDARY,
    _keyword_coverage,
    _norm,
    mask_non_claim_spans,
    non_claim_names,
)

_B = _CORPUS_FRAGMENT_BOUNDARY
_RHEINWERK = non_claim_names("Leiter Operations (m/w/d)", ["Rheinwerk Verpackungen GmbH"])
_NOVAPAY = non_claim_names("Senior Backend Engineer — Payments Platform (m/f/d)", ["NovaPay GmbH"])
_ARNOLD = non_claim_names("Senior Controller (m/w/d)", ["Arnold Antriebstechnik GmbH"])


def _gap(concept: str) -> dict:
    return {
        "concept": concept, "surface_forms": [concept], "claimable": False,
        "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": "",
    }


def _masked(text: str, names) -> str:
    return mask_non_claim_spans(_norm(text), names)


# ── what the clause covers ──────────────────────────────────────────────────


def test_clause_from_the_short_name_to_the_sentence_end():
    t = ("Die Position als Leiter Operations bei der Rheinwerk Verpackungen GmbH spricht mich an, "
         "weil Rheinwerk hochwertige Kunststoff- und Verbundverpackungen für Konsumgüter- und "
         "Lebensmittelkunden fertigt. Meine Erfahrung in diskreter Fertigung zählt.")
    m = _masked(t, _RHEINWERK)
    for word in ("verbundverpackungen", "konsumgüter", "lebensmittelkunden"):
        assert word not in m
    assert "meine erfahrung in diskreter fertigung" in m


def test_clause_stops_at_the_first_person_word():
    t = ("NovaPay’s platform for European e-commerce merchants across checkout, settlement and "
         "payout rails interests me, building on my B2B invoicing, subscription billing and "
         "Stripe-based checkout experience.")
    m = _masked(t, _NOVAPAY)
    assert "settlement" not in m and "european e commerce" not in m
    assert "building on my b2b invoicing, subscription billing" in m


def test_clause_stops_at_a_salutation_so_the_opening_claim_survives():
    """The PDF text has no sentence break between the recipient block and the
    salutation — without this stop the opening claim was masked on 4 of 16
    captured letters."""
    t = ("Rheinwerk Verpackungen GmbH Sehr geehrte Damen und Herren, mit 14 Jahren Erfahrung in "
         "der diskreten Fertigung und als Produktionsleiter bewerbe ich mich.")
    m = _masked(t, _RHEINWERK)
    assert "mit 14 jahren erfahrung in der diskreten fertigung und als produktionsleiter" in m


@pytest.mark.parametrize("pronoun", ["i", "me", "my", "ich", "mich", "mir", "meine", "meinem"])
def test_every_first_person_word_ends_the_clause(pronoun):
    t = f"Arnold fertigt Antriebe {pronoun} Lean Controlling."
    m = _masked(t, _ARNOLD)
    assert "antriebe" not in m
    assert f"{pronoun} lean controlling" in m


def test_the_short_name_anchors_only_as_a_whole_word():
    names = non_claim_names(None, ["Rhein Klinikum Bonn"])
    m = _masked("Im Rheinland habe ich Intensivpflege geleitet.", names)
    assert "rheinland habe ich intensivpflege" in m
    assert _B not in m


def test_the_clause_never_crosses_a_sentence_end():
    names = non_claim_names(None, ["SAP SE"])
    m = _masked("SAP hat Payroll. Danach kenne ich Payroll.", names)
    assert "hat payroll" not in m
    assert "danach kenne ich payroll" in m


def test_a_first_word_shorter_than_four_letters_is_no_anchor():
    names = non_claim_names(None, ["Ace Logistik GmbH"])
    m = _masked("Ace ist gut. Logistik habe ich geleitet.", names)
    assert "ace ist gut" in m


def test_you_and_sie_are_no_anchor():
    t = ("Sie fertigen hochwertige Kunststoff- und Verbundverpackungen. Ihre Lean-Produktion "
         "konsequent weiterzuentwickeln reizt mich.")
    m = _masked(t, _RHEINWERK)
    assert "verbundverpackungen" in m and "lean produktion" in m


# ── the decision on the captured rows ───────────────────────────────────────


def test_employer_business_keyword_is_present_but_not_unsupported():
    t = _norm("NovaPay’s platform for European e-commerce merchants across checkout, settlement "
              "and payout rails interests me.")
    cov = _keyword_coverage(t, ["Settlement"], [_gap("Settlement")], non_claim=_NOVAPAY)
    assert cov.present == ["Settlement"] and cov.present_unsupported == []


def test_the_same_word_claimed_after_the_first_person_word_still_flags():
    t = _norm("NovaPay runs settlement rails, and I have run settlement systems for five years.")
    cov = _keyword_coverage(t, ["Settlement"], [_gap("Settlement")], non_claim=_NOVAPAY)
    assert cov.present_unsupported == ["Settlement"]


def test_a_candidate_claim_in_a_sentence_without_the_employer_still_flags():
    t = _norm("Damit bringe ich Erfahrung mit der Digitalisierung der Fertigung mit.")
    cov = _keyword_coverage(
        t, ["Digitalisierung der Fertigung"], [_gap("Digitalisierung der Fertigung")],
        non_claim=_RHEINWERK,
    )
    assert cov.present_unsupported == ["Digitalisierung der Fertigung"]
